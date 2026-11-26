"""Real Gunicorn HTTP on a private Unix socket (not an external TLS test)."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import gateway as g
from test_gateway import packet, encode, ORG, SOURCE, STREAM


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self,path):
        super().__init__("localhost",timeout=8)
        self.path=path
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.path))


def call(path,method,route,body,token,proto="https"):
    conn=UnixHTTP(path)
    try:
        headers={"X-Forwarded-Proto":proto,"Authorization":"Bearer "+token}
        if body is not None:
            headers["Content-Type"]="application/json"
        conn.request(method,route,body=body,headers=headers)
        r=conn.getresponse()
        return r.status,json.loads(r.read())
    finally:
        conn.close()


def main():
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="ident006-http-") as tmp:
        root=Path(tmp)
        state=root/"state"
        state.mkdir(mode=0o700)
        token=secrets.token_hex(32)
        config=root/"config.json"
        config.write_text(json.dumps({"organization_id":ORG,"source_id":SOURCE,"stream_id":STREAM,
            "source_timezone":"Asia/Novosibirsk","token_sha256":hashlib.sha256(token.encode()).hexdigest(),
            "max_store_bytes":32*g.MIB,"min_free_bytes":16*g.MIB}),encoding="utf-8")
        config.chmod(0o600)
        env=dict(os.environ,IDENT_RECEIVER_CONFIG=str(config),IDENT_RECEIVER_STATE_DIR=str(state))
        subprocess.run([sys.executable,"gateway.py","init"],env=env,check=True)
        sock=root/"receiver.sock"
        log=root/"gunicorn.log"
        with log.open("wb") as f:
            p=subprocess.Popen([sys.executable,"-m","gunicorn","gateway:create_app()",
                "--bind","unix:"+str(sock),"--workers","1","--threads","2","--timeout","20",
                "--umask","0077","--access-logfile","/dev/null"],env=env,stdout=f,stderr=f)
            try:
                deadline=time.monotonic()+15
                while True:
                    if p.poll() is not None or time.monotonic()>=deadline:
                        raise RuntimeError("GUNICORN_START_FAILED")
                    if sock.exists():
                        try:
                            code,status=call(sock,"GET",g.STATUS_ROUTE,None,token)
                            if code==200:
                                break
                        except (OSError,http.client.HTTPException):
                            pass
                    time.sleep(.1)
                assert status["accepted_batches"]==0
                assert call(sock,"GET",g.STATUS_ROUTE,None,"0"*64)[0]==401
                assert call(sock,"GET",g.STATUS_ROUTE,None,token,"http")[0]==400
                raw=encode(packet())
                code,first=call(sock,"POST",g.BATCH_ROUTE,raw,token)
                assert code==200 and first["status"]=="accepted"
                assert first["payload_sha256"]==hashlib.sha256(raw).hexdigest()
                code,again=call(sock,"POST",g.BATCH_ROUTE,raw,token)
                assert code==200 and again=={**first,"status":"already_accepted"}
                assert call(sock,"GET",g.STATUS_ROUTE,None,token)[1]["accepted_batches"]==1
                print("GUNICORN_UNIX_HTTP=PASS; AUTH=PASS; FORWARDED_HTTPS=PASS; EXACT_ACK=PASS; DUPLICATE=PASS")
            finally:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
                print(log.read_text(encoding="utf-8",errors="replace"))


if __name__=="__main__":
    main()
