"""Synthetic HTTPS checks for the disposable Linux CI host."""
import hashlib
import http.client
import json
from pathlib import Path
import socket
import ssl
import sys
import time
from datetime import datetime,timedelta,timezone
from uuid import uuid4

ROOT=Path("/root/ident006b-ci")
HOST="ident-lab.test"
ALIAS="ident-alias.test"
STATUS="/api/ident-bridge/v1/status"
BATCH="/api/ident-bridge/v1/batches"


class HTTPS(http.client.HTTPSConnection):
    def connect(self):
        sock=socket.create_connection(("127.0.0.1",443),self.timeout)
        self.sock=self._context.wrap_socket(sock,server_hostname=self.host)


def request(method,path,body=None,headers=None,host=HOST):
    # Verified certificate and hostname, no insecure TLS option.
    for _ in range(10):
        conn=HTTPS(host,timeout=8,context=ssl.create_default_context())
        try:
            conn.request(method,path,body=body,headers=headers or {})
            r=conn.getresponse()
            status,data=r.status,r.read(1024*1024)
        finally:
            conn.close()
        if status!=429:
            return status,data
        time.sleep(2)
    raise RuntimeError("RATE_LIMIT_RETRY_EXHAUSTED")


def check():
    token=(ROOT/"token").read_text().strip()
    config=json.loads((ROOT/"config.json").read_text())
    auth={"Authorization":"Bearer "+token}
    assert request("GET","/")[0]==200
    assert request("GET","/api/other")== (200,b"existing-other")
    assert request("GET",STATUS)==(401,b'{"error":"UNAUTHORIZED"}')
    assert request("GET",STATUS,headers={"Authorization":"Bearer "+"0"*64})[0]==401
    # A browser cookie or spoofed forwarding header cannot authorize requests.
    assert request("GET",STATUS,headers={"Cookie":"session=synthetic","X-Forwarded-Proto":"https"})[0]==401
    assert request("GET",STATUS,headers=auth,host=ALIAS)[0]==404
    assert request("GET",STATUS+"?x=1",headers=auth)[0]==404
    assert request("GET",BATCH,headers=auth)[0]==405
    assert request("POST",STATUS,b"",auth)[0]==405
    assert request("GET",STATUS,headers={**auth,"X-Forwarded-Proto":"http","X-Forwarded-SSL":"on"})[0]==200
    code,data=request("GET",STATUS,headers=auth)
    assert code==200 and json.loads(data)["accepted_batches"]==0
    assert request("POST",BATCH,b"x"*(512*1024+1),{**auth,"Content-Type":"application/json"})[0]==413
    # Plain HTTP is the untouched fixture site, never the receiver.
    conn=http.client.HTTPConnection("127.0.0.1",80,timeout=5)
    try:
        conn.request("GET",STATUS,headers={"Host":HOST})
        r=conn.getresponse()
        assert r.status==404 and r.read()==b"existing-http"
    finally:
        conn.close()
    now=datetime.now(timezone.utc)
    day=(now-timedelta(days=2)).date()
    p={"contract_version":1,"dataset":"payments_daily_v1","scope":"whole_source_database",
       "source_id":config["source_id"],"stream_id":config["stream_id"],"batch_id":str(uuid4()),
       "sequence":1,"source_timezone":config["source_timezone"],"date_from":day.isoformat(),
       "date_to_exclusive":(day+timedelta(days=1)).isoformat(),
       "query_started_at":now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
       "observed_at":now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
       "coverage":"full_query_window_as_observed",
       "query_sha256":"277cc67cb4c0369f7f3461ae5d8c50ac0443c3342cb44a81625cf701c52e443f",
       "schema":{"payment_precision":18,"payment_scale":2},
       "rows":[{"date":day.isoformat(),"records_count":1,"paying_patients":1,
                "positive_amount":"12.34","negative_amount":"0","net_amount":"12.34"}]}
    raw=json.dumps(p,separators=(",",":")).encode()
    headers={**auth,"Content-Type":"application/json"}
    assert request("POST",BATCH,raw,headers={**headers,"Content-Encoding":"gzip"})[0]==400
    code,data=request("POST",BATCH,raw,headers)
    ack=json.loads(data)
    assert code==200 and ack["status"]=="accepted" and ack["payload_sha256"]==hashlib.sha256(raw).hexdigest()
    assert ack["batch_id"]==p["batch_id"]
    (ROOT/"packet.json").write_bytes(raw)
    (ROOT/"ack.json").write_text(json.dumps(ack))
    code,data=request("POST",BATCH,raw,headers)
    assert code==200 and json.loads(data)=={**ack,"status":"already_accepted"}
    print("HTTPS_REAL=PASS; CERTIFICATE_VALIDATION=PASS; HOST_SCOPE=PASS; AUTH=PASS; BODY_LIMIT=PASS; EXACT_ACK=PASS; DUPLICATE=PASS")


def after_restart():
    token=(ROOT/"token").read_text().strip()
    raw=(ROOT/"packet.json").read_bytes()
    original=json.loads((ROOT/"ack.json").read_text())
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"}
    code,data=request("POST",BATCH,raw,headers)
    assert code==200 and json.loads(data)=={**original,"status":"already_accepted"}
    code,data=request("GET",STATUS,headers=headers)
    assert code==200 and json.loads(data)["accepted_batches"]==1
    print("AUTOMATIC_RESTART=PASS; DURABLE_ACK=PASS; NO_DUPLICATE=PASS")


if __name__=="__main__":
    {"check":check,"after-restart":after_restart}[sys.argv[1]]()
