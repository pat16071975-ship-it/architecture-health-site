"""Isolated Windows SQL TLS fixture. No production endpoints or credentials."""
import hashlib, os, socket, ssl, struct, sys
from pathlib import Path

HOST, PORT = "127.0.0.1", 15433
QUERY = "277cc67cb4c0369f7f3461ae5d8c50ac0443c3342cb44a81625cf701c52e443f"


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


if __name__ == "__main__":
    if (os.environ.get("GITHUB_ACTIONS")!="true" or os.environ.get("RUNNER_ENVIRONMENT")!="github-hosted"
        or os.environ.get("RUNNER_OS")!="Windows" or os.environ.get("GITHUB_REF")!="refs/heads/test/ident-006c-sender-20261126"):
        raise SystemExit("DISPOSABLE_RUNNER_REQUIRED")
    p=Path(sys.argv[1]).resolve()
    root=Path(os.environ["IDENT_LAB_DIR"]).resolve()
    if p != root/"good"/"server.cer" or p.exists():
        raise SystemExit("LAB_PIN_PATH")
    capture_lab_certificate(p)
