"""Synthetic HTTPS receiver; exact IDENT-004 validator/store. Loopback only."""
import os, sys, ssl, json, hmac, hashlib, socket, time, threading
import http.server
from pathlib import Path
from datetime import date,timedelta,datetime,timezone
from decimal import Decimal
sys.path.insert(0,str(Path(__file__).resolve().parent/"reference"))
import receiver as ref

if (os.environ.get("GITHUB_ACTIONS")!="true" or os.environ.get("RUNNER_ENVIRONMENT")!="github-hosted"
    or os.environ.get("RUNNER_OS")!="Windows" or os.environ.get("GITHUB_REF")!="refs/heads/test/ident-006c-sender-20261126"):
    raise SystemExit("DISPOSABLE_RUNNER_REQUIRED")
lab=Path(sys.argv[1]).resolve(strict=True)
assert lab==(Path(os.environ["RUNNER_TEMP"])/"ident006c-lab").resolve()
cfg=json.loads((lab/"config.json").read_bytes())
expected_seed=(lab/"seed.json").read_bytes()
token=(lab/"token.txt").read_text().strip()
identity=ref.Identity(cfg["source_id"],cfg["stream_id"],cfg["source_timezone"])
first=date.fromisoformat((lab/"first-day.txt").read_text())
ref.validate(expected_seed,identity)
store=lab/"lab.sqlite3"
ref.init_store(store,identity)
assert not (lab/"accepted-2.json").exists()
lock=threading.Lock()

def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def verify_packet(p,raw):
    seq=p["sequence"]
    if seq==1:
        assert raw==expected_seed
    else:
        assert seq==2 and p["date_from"]==first.isoformat()
        assert p["date_to_exclusive"]==(first+timedelta(days=4)).isoformat()
        assert p["schema"]=={"payment_precision":18,"payment_scale":2}
        expected=[
            (2,1,Decimal("120.05"),Decimal("-10.05"),Decimal("110.00")),
            (1,1,Decimal("25"),Decimal("0"),Decimal("25")),
            (0,0,Decimal("0"),Decimal("0"),Decimal("0")),
            (2,1,Decimal("13"),Decimal("0"),Decimal("13"))
        ]
        assert len(p["rows"])==4
        for i,row in enumerate(p["rows"]):
            assert row["date"]==(first+timedelta(days=i)).isoformat()
            actual=(row["records_count"],row["paying_patients"],*(Decimal(row[k]) for k in ("positive_amount","negative_amount","net_amount")))
            assert actual==expected[i]

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
            self.send_json(401,{"error":"UNAUTHORIZED"});return False
        return True
    def do_GET(self):
        if self.path!="/api/ident-bridge/v1/status":
            self.send_json(404,{"error":"NOT_FOUND"});return
        if not self.auth():return
        with ref.closing(ref.connect(store,readonly=True)) as db:
            n,seq,accepted=db.execute("SELECT count(*),max(sequence),max(accepted_at) FROM batches").fetchone()
            start,end=db.execute("SELECT min(date),max(date) FROM daily").fetchone()
        self.send_json(200,{"status":"ready","dataset":ref.DATASET,"source_id":identity.source_id,
            "stream_id":identity.stream_id,"accepted_batches":n,"max_sequence":seq,
            "last_accepted_at":utc(accepted) if accepted else None,"first_observed_day":start,
            "last_observed_day":end,"mode":"shadow_only"})
    def do_POST(self):
        if self.path!="/api/ident-bridge/v1/batches":
            self.send_json(404,{"error":"NOT_FOUND"});return
        if not self.auth():return
        try:
            length=int(self.headers["Content-Length"])
            assert 0<length<=ref.MAX_BYTES
            raw=self.rfile.read(length)
            p=ref.validate(raw,identity)
            verify_packet(p,raw)
            seq=p["sequence"]
            with lock:
                if seq==2 and not (lab/"allow-2").exists():
                    digest=hashlib.sha256(raw).hexdigest()
                    path=lab/"attempt-2.sha256"
                    if path.exists():assert path.read_text()==digest
                    else:path.write_text(digest)
                    (lab/"receiver-503").write_text("1")
                    self.send_json(503,{"error":"SYNTHETIC_UNAVAILABLE"})
                    return
                if seq==2:
                    assert (lab/"attempt-2.sha256").read_text()==hashlib.sha256(raw).hexdigest()
                result=ref.ingest(store,raw)
                with ref.closing(ref.connect(store,readonly=True)) as db:
                    accepted=db.execute("SELECT accepted_at FROM batches WHERE sequence=?",(seq,)).fetchone()[0]
                if seq==2 and result=="ACCEPTED":
                    (lab/"accepted-2.json").write_bytes(raw)
                    (lab/"commit-2").write_text("1")
                if seq==2 and result=="ALREADY_ACCEPTED":
                    assert (lab/"accepted-2.json").read_bytes()==raw
                    (lab/"duplicate-2").write_text("1")
        except Exception as e:
            (lab/"receiver-error").write_text(type(e).__name__)
            self.send_json(422,{"error":"SYNTHETIC_VALIDATION"});return
        if seq==2 and result=="ACCEPTED":
            # Real commit, deliberately no response; controller kills sender.
            time.sleep(15)
            self.close_connection=True
            try:self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            return
        self.send_json(200,{"contract_version":1,"dataset":ref.DATASET,"source_id":p["source_id"],
            "stream_id":p["stream_id"],"batch_id":p["batch_id"],"sequence":seq,
            "payload_sha256":hashlib.sha256(raw).hexdigest(),"accepted_at":utc(accepted),
            "status":"accepted" if result=="ACCEPTED" else "already_accepted"})

httpd=http.server.ThreadingHTTPServer(("127.0.0.1",443),Handler)
ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.minimum_version=ssl.TLSVersion.TLSv1_2
ctx.load_cert_chain(lab/"leaf.pem",lab/"leaf.key")
httpd.socket=ctx.wrap_socket(httpd.socket,server_side=True)
(lab/"https-ready").write_text("1")
httpd.serve_forever()
