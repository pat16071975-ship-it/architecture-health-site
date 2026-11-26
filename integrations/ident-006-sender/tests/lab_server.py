"""Disposable localhost HTTPS fixture using the unchanged IDENT-004 validator
and SQLite reference receiver. No live endpoints. Intentionally lose first ACK."""
import argparse
import hashlib
import hmac
import http.server
import json
from pathlib import Path
import socket
import ssl
import sys
from datetime import datetime,timezone

sys.path.insert(0,str(Path(__file__).resolve().parent/"reference"))
import receiver as ref

STATUS="/api/ident-bridge/v1/status"
BATCH="/api/ident-bridge/v1/batches"


def utc(s):
    return datetime.fromisoformat(s).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def main(lab):
    config=json.loads((lab/"config.json").read_bytes())
    expected=(lab/"seed.json").read_bytes()
    token=(lab/"token.txt").read_text().strip()
    identity=ref.Identity(config["source_id"],config["stream_id"],config["source_timezone"])
    ref.validate(expected,identity)
    store=lab/"lab.sqlite3"
    ref.init_store(store,identity)
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version="HTTP/1.1"
        def log_message(self,*args): pass
        def send_json(self,status,obj):
            raw=json.dumps(obj,separators=(",",":")).encode()
            self.send_response(status)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def auth(self):
            if not hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+token):
                self.send_json(401,{"error":"UNAUTHORIZED"})
                return False
            return True
        def do_GET(self):
            if self.path!=STATUS:
                self.send_json(404,{"error":"NOT_FOUND"});return
            if not self.auth():return
            with ref.closing(ref.connect(store,readonly=True)) as db:
                n,seq,accepted=db.execute("SELECT count(*),max(sequence),max(accepted_at) FROM batches").fetchone()
                first,last=db.execute("SELECT min(date),max(date) FROM daily").fetchone()
            self.send_json(200,{"status":"ready","dataset":ref.DATASET,"source_id":identity.source_id,
                "stream_id":identity.stream_id,"accepted_batches":n,"max_sequence":seq,
                "last_accepted_at":utc(accepted) if accepted else None,"first_observed_day":first,
                "last_observed_day":last,"mode":"shadow_only"})
        def do_POST(self):
            if self.path!=BATCH:
                self.send_json(404,{"error":"NOT_FOUND"});return
            if not self.auth():return
            try:
                length=int(self.headers["Content-Length"])
                if not 0<length<=ref.MAX_BYTES:raise ValueError
                raw=self.rfile.read(length)
                if raw!=expected:raise ValueError("UNEXPECTED_SYNTHETIC_BYTES")
                p=ref.validate(raw,identity)
                result=ref.ingest(store,raw)
                with ref.closing(ref.connect(store,readonly=True)) as db:
                    accepted=db.execute("SELECT accepted_at FROM batches WHERE sequence=?",(p["sequence"],)).fetchone()[0]
            except Exception:
                self.send_json(422,{"error":"SYNTHETIC_VALIDATION"});return
            if result=="ACCEPTED":
                (lab/"first-accepted").write_text("1")
                self.close_connection=True
                try:self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                return
            (lab/"exact-duplicate").write_text("1")
            self.send_json(200,{"contract_version":1,"dataset":ref.DATASET,"source_id":p["source_id"],
                "stream_id":p["stream_id"],"batch_id":p["batch_id"],"sequence":p["sequence"],
                "payload_sha256":hashlib.sha256(raw).hexdigest(),"accepted_at":utc(accepted),
                "status":"already_accepted"})
    httpd=http.server.HTTPServer(("127.0.0.1",443),Handler)
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version=ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(lab/"leaf.pem",lab/"leaf.key")
    httpd.socket=ctx.wrap_socket(httpd.socket,server_side=True)
    (lab/"https-ready").write_text("1")
    httpd.serve_forever()


if __name__=="__main__":
    import os
    if os.environ.get("GITHUB_ACTIONS")!="true" or os.environ.get("RUNNER_OS")!="Windows" or os.environ.get("GITHUB_REF")!="refs/heads/test/ident-006c-sender-20261126":
        raise SystemExit("DISPOSABLE_RUNNER_REQUIRED")
    p=argparse.ArgumentParser();p.add_argument("lab",type=Path);main(p.parse_args().lab)
