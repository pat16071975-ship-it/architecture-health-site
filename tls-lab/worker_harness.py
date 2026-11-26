"""Isolated Windows SQL TLS fixture. No production endpoints or credentials."""
import argparse, base64, ctypes as ct, hashlib, json, msvcrt, os, socket, ssl, struct, subprocess, threading, time
from ctypes import wintypes as wt
from pathlib import Path

HOST, PORT = "127.0.0.1", 15433
QUERY = "277cc67cb4c0369f7f3461ae5d8c50ac0443c3342cb44a81625cf701c52e443f"
EXE_SHA = "cd30e0fd3bd852b51c6f62ccb003f7dcf67df61709f12251690ebec7899c8c91"


def exact(sock, n):
    data = bytearray()
    while len(data) < n:
        part = sock.recv(n-len(data))
        if not part:
            raise RuntimeError("TDS_EOF")
        data.extend(part)
    return bytes(data)


def send_tds(sock, data):
    for i, offset in enumerate(range(0, len(data), 4088)):
        part = data[offset:offset+4088]
        end = offset + len(part) == len(data)
        sock.sendall(struct.pack("!BBHHBB", 0x12, int(end), len(part)+8, 0, (i+1)&255, 0)+part)


def recv_tds(sock):
    out = bytearray()
    while True:
        kind, flags, size, _, _, _ = struct.unpack("!BBHHBB", exact(sock, 8))
        if kind not in (0x04, 0x12) or not 8 <= size <= 65535:
            raise RuntimeError("TDS_HEADER")
        out.extend(exact(sock, size-8))
        if len(out) > 1048576:
            raise RuntimeError("TDS_LIMIT")
        if flags & 1:
            return bytes(out)


def capture_lab_certificate(path):
    # Only the loopback fixture controlled by this job. No LOGIN7 is sent.
    # This is observation, not enrollment or trust in any external host.
    with socket.create_connection((HOST, PORT), timeout=10) as sock:
        sock.settimeout(10)
        request = struct.pack("!BHH", 0, 11, 6)+struct.pack("!BHH", 1, 17, 1)+b"\xff"+b"\0"*6+b"\x01"
        send_tds(sock, request)
        response = recv_tds(sock)
        pos, encryption = 0, None
        while pos < len(response) and response[pos] != 255:
            token, offset, size = struct.unpack_from("!BHH", response, pos)
            if offset+size > len(response):
                raise RuntimeError("PRELOGIN_RESPONSE")
            if token == 1 and size == 1:
                encryption = response[offset]
            pos += 5
        if encryption not in (1, 3):
            raise RuntimeError("PRELOGIN_ENCRYPTION_NOT_NEGOTIATED")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        tls = ctx.wrap_bio(incoming, outgoing, server_side=False, server_hostname="localhost")
        for _ in range(30):
            done = False
            try:
                tls.do_handshake()
                done = True
            except ssl.SSLWantReadError:
                pass
            except ssl.SSLWantWriteError:
                pass
            while outgoing.pending:
                send_tds(sock, outgoing.read())
            if done:
                data = tls.getpeercert(binary_form=True)
                if not data:
                    raise RuntimeError("NO_CERTIFICATE")
                path.write_bytes(data)
                print("LAB_CERT_OBSERVED=PASS; TLS="+str(tls.version())+"; SHA256="+hashlib.sha256(data).hexdigest(), flush=True)
                return
            incoming.write(recv_tds(sock))
        raise RuntimeError("HANDSHAKE_LIMIT")


class CharUnion(ct.Union):
    _fields_ = [("UnicodeChar", wt.WCHAR), ("AsciiChar", ct.c_char)]


class Key(ct.Structure):
    _fields_ = [("bKeyDown", wt.BOOL), ("wRepeatCount", wt.WORD),
                ("wVirtualKeyCode", wt.WORD), ("wVirtualScanCode", wt.WORD),
                ("uChar", CharUnion), ("dwControlKeyState", wt.DWORD)]


class Event(ct.Union):
    _fields_ = [("KeyEvent", Key), ("padding", ct.c_byte*16)]


class Record(ct.Structure):
    _fields_ = [("EventType", wt.WORD), ("Event", Event)]


def run_worker(exe, cert, pin_hash, result_path, check_only):
    if hashlib.sha256(exe.read_bytes()).hexdigest() != EXE_SHA:
        raise RuntimeError("WORKER_HASH")
    password = os.environ.pop("IDENT_LAB_PASSWORD")
    config = dict(ServerName=HOST, Port=PORT, Database="IdentTlsLab", SqlUser="ident_lab_reader",
                  CertificatePath=str(cert), CertificateSha256=pin_hash, DateFrom="2026-01-01",
                  DateToExclusive="2026-01-03", QuerySha256=QUERY, CheckOnly=check_only)
    arg = base64.b64encode(json.dumps(config,separators=(",",":")).encode()).decode()
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    kernel.FreeConsole()
    if not kernel.AllocConsole():
        raise ct.WinError(ct.get_last_error())
    kernel.CreateFileW.argtypes = [wt.LPCWSTR,wt.DWORD,wt.DWORD,ct.c_void_p,wt.DWORD,wt.DWORD,wt.HANDLE]
    kernel.CreateFileW.restype = wt.HANDLE
    kernel.WriteConsoleInputW.argtypes = [wt.HANDLE,ct.POINTER(Record),wt.DWORD,ct.POINTER(wt.DWORD)]
    kernel.WriteConsoleInputW.restype = wt.BOOL
    kernel.GetConsoleMode.argtypes = [wt.HANDLE,ct.POINTER(wt.DWORD)]
    kernel.FlushConsoleInputBuffer.argtypes = [wt.HANDLE]
    handle = kernel.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
    if handle in (None, ct.c_void_p(-1).value):
        raise ct.WinError(ct.get_last_error())
    mode = wt.DWORD()
    if not kernel.GetConsoleMode(handle,ct.byref(mode)) or not kernel.FlushConsoleInputBuffer(handle):
        raise RuntimeError("CONSOLE_INPUT_REQUIRED")
    fd = msvcrt.open_osfhandle(handle,os.O_RDONLY)
    input_file = os.fdopen(fd,"rb",buffering=0)
    result = {"exit_code":None,"prompt_seen":False,"stdout":"","stderr":"","harness_error":None}
    p = None
    errs = bytearray()
    out = bytearray()
    thread_error = []
    try:
        cache = result_path.parent / "native-cache"
        cache.mkdir(exist_ok=True)
        env = dict(os.environ)
        env["DOTNET_BUNDLE_EXTRACT_BASE_DIR"] = str(cache)
        p = subprocess.Popen([str(exe),arg],stdin=input_file,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
        def read_err():
            try:
                while True:
                    piece = p.stderr.read(1)
                    if not piece:
                        break
                    errs.extend(piece)
                    if b"SQL password: " in errs and not result["prompt_seen"]:
                        result["prompt_seen"] = True
                        records = (Record*(len(password)+1))()
                        for record,ch in zip(records,password+"\r"):
                            record.EventType = 1
                            record.Event.KeyEvent = Key(True,1,13 if ch=="\r" else 0,0,CharUnion(UnicodeChar=ch),0)
                        written = wt.DWORD()
                        if not kernel.WriteConsoleInputW(handle,records,len(records),ct.byref(written)) or written.value != len(records):
                            raise RuntimeError("CONSOLE_WRITE_INPUT")
            except BaseException as e:
                thread_error.append(type(e).__name__+":"+str(e))
        def read_out():
            out.extend(p.stdout.read())
        a,b = threading.Thread(target=read_err,daemon=True),threading.Thread(target=read_out,daemon=True)
        a.start(); b.start()
        try:
            result["exit_code"] = p.wait(timeout=100)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait()
            raise RuntimeError("WORKER_TIMEOUT")
        a.join(10); b.join(10)
        if a.is_alive() or b.is_alive() or thread_error:
            raise RuntimeError("CONSOLE_HARNESS:"+str(thread_error))
        result["stdout"] = out.decode("utf-8","strict")
        result["stderr"] = errs.decode("utf-8","replace")
        if password in result["stdout"] or password in result["stderr"]:
            raise RuntimeError("SYNTHETIC_SECRET_LEAK")
    except BaseException as e:
        result["harness_error"] = type(e).__name__+":"+str(e)
        result["stdout"] = ""; result["stderr"] = ""
    finally:
        if p is not None and p.poll() is None:
            p.kill(); p.wait()
        input_file.close()
        kernel.FreeConsole()
        result_path.write_text(json.dumps(result,ensure_ascii=True),encoding="utf-8")
    return 0 if result["harness_error"] is None else 2


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode",required=True)
    a = sub.add_parser("capture")
    a.add_argument("certificate",type=Path)
    a = sub.add_parser("worker")
    a.add_argument("exe",type=Path); a.add_argument("certificate",type=Path)
    a.add_argument("hash"); a.add_argument("result",type=Path)
    a.add_argument("check_only",choices=["true","false"])
    args = parser.parse_args()
    if args.mode == "capture":
        capture_lab_certificate(args.certificate); return 0
    return run_worker(args.exe,args.certificate,args.hash,args.result,args.check_only=="true")


if __name__ == "__main__":
    raise SystemExit(main())
