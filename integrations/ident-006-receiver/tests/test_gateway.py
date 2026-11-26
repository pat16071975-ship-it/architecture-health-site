"""Synthetic receiver tests. No clinic or external connection."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator

import gateway as g
from core import receiver as c

ORG = "11111111-1111-4111-8111-111111111111"
SOURCE = "22222222-2222-4222-8222-222222222222"
STREAM = "33333333-3333-4333-8333-333333333333"
TOKEN = "42" * 32
NOW = datetime(2026, 1, 20, 12, tzinfo=timezone.utc)
QUERY = "277cc67cb4c0369f7f3461ae5d8c50ac0443c3342cb44a81625cf701c52e443f"


def packet(seq=1, start="2026-01-10", end="2026-01-11", amount="12.34", scale=2):
    from datetime import date, timedelta
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    rows = []
    for i in range((last-first).days):
        rows.append({"date":str(first+timedelta(days=i)), "records_count":1,
                     "paying_patients":1,"positive_amount":amount,
                     "negative_amount":"0","net_amount":amount})
    return {"contract_version":1, "dataset":"payments_daily_v1", "scope":"whole_source_database",
            "source_id":SOURCE,"stream_id":STREAM,"source_timezone":"Asia/Novosibirsk",
            "batch_id":str(uuid4()),"sequence":seq,"date_from":start,"date_to_exclusive":end,
            "query_started_at":"2026-01-20T09:00:00.000000Z","observed_at":"2026-01-20T09:00:01.000000Z",
            "coverage":"full_query_window_as_observed","query_sha256":QUERY,
            "schema":{"payment_precision":18,"payment_scale":scale},"rows":rows}


def encode(p):
    return json.dumps(p, ensure_ascii=False,separators=(",",":")).encode()


class NoRead(io.BytesIO):
    def read(self,*_):
        raise AssertionError("unauthorized or invalid request read body")


class Case(unittest.TestCase):
    def setUp(self):
        self.old_umask = os.umask(0o077)
        self.tmp = tempfile.TemporaryDirectory(prefix="ident006-test-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.umask,self.old_umask)
        self.root = Path(self.tmp.name)
        self.state = self.root/"state"
        self.state.mkdir(mode=0o700)
        self.config = self.root/"config.json"
        self.obj = {"organization_id":ORG,"source_id":SOURCE,"stream_id":STREAM,
                    "source_timezone":"Asia/Novosibirsk",
                    "token_sha256":sha256(TOKEN.encode()).hexdigest(),
                    "max_store_bytes":16*g.MIB,"min_free_bytes":16*g.MIB}
        self.write_config()
        self.s = g.Settings.load(self.config,self.state)

    def write_config(self):
        self.config.write_bytes(encode(self.obj))
        self.config.chmod(0o600)

    def ready(self):
        g.initialize(self.s)
        return g.make_app(self.s,lambda: NOW)

    def http(self, app, raw=b"", route=g.BATCH_ROUTE, method="POST", token=TOKEN, **override):
        env = {}
        setup_testing_defaults(env)
        env.update({"PATH_INFO":route,"REQUEST_METHOD":method,
                    "HTTP_X_FORWARDED_PROTO":"https","wsgi.url_scheme":"https",
                    "CONTENT_TYPE":"application/json","CONTENT_LENGTH":str(len(raw)),
                    "HTTP_AUTHORIZATION":"Bearer "+token,"wsgi.input":io.BytesIO(raw)})
        env.update(override)
        captured = []
        def start(status, headers, exc_info=None):
            captured.append((status,dict(headers)))
            return lambda _: None
        # Directly invoke the WSGI application for deliberately malformed
        # framing: the stdlib validator rejects those environ values itself.
        env.setdefault("QUERY_STRING","")
        result = app(env,start)
        try:
            body = b"".join(result)
        finally:
            if hasattr(result,"close"):
                result.close()
        status,headers = captured[0]
        self.assertEqual(headers["Cache-Control"],"no-store")
        self.assertEqual(headers["Content-Length"],str(len(body)))
        self.assertNotIn(TOKEN,body.decode())
        return int(status.split()[0]),json.loads(body),headers

    def snapshot(self):
        with sqlite3.connect(self.s.db) as db:
            return (db.execute("SELECT sequence,batch_id,payload_sha256,payload FROM batches ORDER BY sequence").fetchall(),
                    db.execute("SELECT * FROM daily ORDER BY date").fetchall())

    def test_explicit_init_no_implicit_create(self):
        self.assertFalse(self.s.db.exists())
        with self.assertRaises(FileNotFoundError):
            g.make_app(self.s)
        self.assertFalse(self.s.db.exists())
        self.assertEqual(g.initialize(self.s),"STORE_CREATED")
        self.assertEqual(g.initialize(self.s),"STORE_READY")
        self.assertEqual(self.s.db.stat().st_mode & 0o777,0o600)
        self.assertEqual(g.read_status(self.s)["accepted_batches"],0)

    def test_reject_other_database_unchanged(self):
        with sqlite3.connect(self.s.db) as conn:
            conn.execute("CREATE TABLE unrelated(x)")
            conn.execute("INSERT INTO unrelated VALUES (7)")
        self.s.db.chmod(0o600)
        before=self.s.db.read_bytes()
        with self.assertRaisesRegex(g.Stop,"NOT_IDENT006_SHADOW_STORE"):
            g.initialize(self.s)
        self.assertEqual(self.s.db.read_bytes(),before)

    def test_reject_rebind_organization(self):
        self.ready()
        self.obj["organization_id"]=str(uuid4())
        self.write_config()
        other=g.Settings.load(self.config,self.state)
        with self.assertRaisesRegex(g.Stop,"STORE_BINDING_MISMATCH"):
            g.initialize(other)
        self.assertEqual(g.read_status(self.s)["accepted_batches"],0)

    def test_reject_rebind_stream(self):
        self.ready()
        self.obj["stream_id"]=str(uuid4())
        self.write_config()
        with self.assertRaisesRegex(g.Stop,"STORE_BINDING_MISMATCH"):
            g.make_app(g.Settings.load(self.config,self.state))

    def test_config_schema_no_plaintext_token(self):
        self.obj["token"]=TOKEN
        self.write_config()
        with self.assertRaisesRegex(g.Stop,"INVALID_CONFIG_FIELDS"):
            g.Settings.load(self.config,self.state)

    def test_config_duplicate_key(self):
        raw=self.config.read_bytes()
        self.config.write_bytes(raw[:-1]+b',"organization_id":"11111111-1111-4111-8111-111111111111"}')
        with self.assertRaisesRegex(g.Stop,"INVALID_CONFIG"):
            g.Settings.load(self.config,self.state)

    def test_config_mode_and_state_mode(self):
        self.config.chmod(0o644)
        with self.assertRaisesRegex(g.Stop,"PRIVATE_CONFIG_REQUIRED"):
            g.Settings.load(self.config,self.state)
        self.config.chmod(0o600)
        self.state.chmod(0o755)
        with self.assertRaisesRegex(g.Stop,"PRIVATE_STATE_REQUIRED"):
            g.Settings.load(self.config,self.state)

    def test_config_symlink_and_hardlink(self):
        alias=self.root/"link"
        alias.symlink_to(self.config)
        with self.assertRaisesRegex(g.Stop,"UNSAFE_PATH"):
            g.Settings.load(alias,self.state)
        alias.unlink()
        os.link(self.config,alias)
        with self.assertRaisesRegex(g.Stop,"PRIVATE_CONFIG_REQUIRED"):
            g.Settings.load(self.config,self.state)

    def test_store_symlink_and_hardlink(self):
        self.ready()
        outside=self.root/"original"
        self.s.db.rename(outside)
        self.s.db.symlink_to(outside)
        with self.assertRaisesRegex(g.Stop,"PRIVATE_STORE_REQUIRED"):
            g.verify_store(self.s)
        self.s.db.unlink()
        os.link(outside,self.s.db)
        with self.assertRaisesRegex(g.Stop,"PRIVATE_STORE_REQUIRED"):
            g.verify_store(self.s)

    def test_no_exposure_without_machine_token(self):
        app=self.ready()
        for route in (g.BATCH_ROUTE,g.STATUS_ROUTE):
            status,obj,_=self.http(app,route=route,token="bad",**{"wsgi.input":NoRead(b"x")})
            self.assertEqual((status,obj),(401,{"error":"UNAUTHORIZED"}))
        self.assertEqual(self.snapshot(),([],[]))

    def test_no_cookie_or_browser_session_auth(self):
        app=self.ready()
        status,obj,_=self.http(app,token="",HTTP_COOKIE="session=example",**{"wsgi.input":NoRead(b"x")})
        self.assertEqual(status,401)

    def test_https_required(self):
        app=self.ready()
        for fields in ({"wsgi.url_scheme":"http"},{"HTTP_X_FORWARDED_PROTO":"http"},{"HTTP_X_FORWARDED_PROTO":"https,http"}):
            status,obj,_=self.http(app,**fields)
            self.assertEqual((status,obj),(400,{"error":"HTTPS_REQUIRED"}))

    def test_strict_path_method_and_query(self):
        app=self.ready()
        self.assertEqual(self.http(app,route="/",method="GET")[0],404)
        self.assertEqual(self.http(app,QUERY_STRING="x=1")[0],404)
        self.assertEqual(self.http(app,method="GET")[0],405)
        self.assertEqual(self.http(app,route=g.STATUS_ROUTE,method="POST")[0],405)

    def test_content_length_and_content_type_before_read(self):
        app=self.ready()
        for headers,wanted in [
                ({"CONTENT_TYPE":"text/plain"},415),({"CONTENT_LENGTH":""},411),
                ({"CONTENT_LENGTH":"-1"},411),({"CONTENT_LENGTH":"0"},400),
                ({"CONTENT_LENGTH":str(c.MAX_BYTES+1)},413),
                ({"HTTP_CONTENT_ENCODING":"gzip"},400),({"HTTP_TRANSFER_ENCODING":"chunked"},400)]:
            headers["wsgi.input"]=NoRead(b"x")
            self.assertEqual(self.http(app,**headers)[0],wanted)
        self.assertEqual(self.snapshot(),([],[]))

    def test_truncated_body(self):
        app=self.ready()
        self.assertEqual(self.http(app,b"{}",CONTENT_LENGTH="3")[0],400)

    def test_wsgi_conformance(self):
        app=self.ready()
        env={}
        setup_testing_defaults(env)
        env.update({"REQUEST_METHOD":"GET","PATH_INFO":g.STATUS_ROUTE,
                    "QUERY_STRING":"","wsgi.url_scheme":"https",
                    "HTTP_X_FORWARDED_PROTO":"https","HTTP_AUTHORIZATION":"Bearer "+TOKEN})
        captured=[]
        def start(status,headers,exc_info=None):
            captured.append(status)
            return lambda _: None
        result=validator(app)(env,start)
        try:
            self.assertEqual(json.loads(b"".join(result))["status"],"ready")
        finally:
            result.close()
        self.assertEqual(captured,["200 OK"])

    def test_valid_batch_raw_archive_exact_ack(self):
        app=self.ready()
        p=packet()
        raw=encode(p)
        status,ack,_=self.http(app,raw)
        self.assertEqual(status,200)
        self.assertEqual(ack["status"],"accepted")
        self.assertEqual(ack["payload_sha256"],sha256(raw).hexdigest())
        self.assertEqual(ack["batch_id"],p["batch_id"])
        self.assertEqual(ack["source_id"],SOURCE)
        self.assertEqual(ack["stream_id"],STREAM)
        self.assertEqual(ack["sequence"],1)
        self.assertEqual(ack["accepted_at"],g.utc_text(NOW))
        archived,daily=self.snapshot()
        self.assertEqual(archived,[(1,p["batch_id"],sha256(raw).hexdigest(),raw)])
        self.assertEqual(daily,[("2026-01-10",1,1,1,"12.34","0","12.34")])

    def test_exact_retry_no_duplicates_even_after_restart(self):
        app=self.ready()
        raw=encode(packet())
        first=self.http(app,raw)[1]
        before=self.snapshot()
        later=g.make_app(self.s,lambda:NOW.replace(hour=13))
        second=self.http(later,raw)[1]
        self.assertEqual(second,{**first,"status":"already_accepted"})
        self.assertEqual(self.snapshot(),before)

    def test_changed_bytes_same_sequence_conflict(self):
        app=self.ready()
        p=packet()
        self.assertEqual(self.http(app,encode(p))[0],200)
        before=self.snapshot()
        status,obj,_=self.http(app,json.dumps(p).encode())
        self.assertEqual((status,obj),(409,{"error":"SEQUENCE_CONTENT_CONFLICT"}))
        self.assertEqual(self.snapshot(),before)

    def test_batch_id_reuse_conflict(self):
        app=self.ready()
        p=packet()
        self.http(app,encode(p))
        before=self.snapshot()
        p["sequence"]=2
        self.assertEqual(self.http(app,encode(p))[:2],(409,{"error":"BATCH_ID_CONFLICT"}))
        self.assertEqual(self.snapshot(),before)

    def test_new_observation_replaces_not_adds(self):
        app=self.ready()
        self.http(app,encode(packet()))
        self.http(app,encode(packet(2,amount="5.00")))
        archived,daily=self.snapshot()
        self.assertEqual(len(archived),2)
        self.assertEqual(daily,[("2026-01-10",2,1,1,"5.00","0","5.00")])

    def test_stale_overlap_reject_entire_window(self):
        app=self.ready()
        self.http(app,encode(packet(3)))
        before=self.snapshot()
        p=packet(2,start="2026-01-09",end="2026-01-11")
        self.assertEqual(self.http(app,encode(p))[:2],(409,{"error":"STALE_OVERLAPPING_WINDOW"}))
        self.assertEqual(self.snapshot(),before)

    def test_old_nonoverlap_accepted(self):
        app=self.ready()
        self.http(app,encode(packet(3)))
        self.assertEqual(self.http(app,encode(packet(1,start="2026-01-09",end="2026-01-10")))[0],200)
        self.assertEqual(len(self.snapshot()[1]),2)

    def test_schema_change_conflict(self):
        app=self.ready()
        self.http(app,encode(packet()))
        before=self.snapshot()
        self.assertEqual(self.http(app,encode(packet(2,scale=3)))[:2],(409,{"error":"SOURCE_SCHEMA_CHANGED"}))
        self.assertEqual(self.snapshot(),before)

    def test_exact_decimal_scale_ten(self):
        app=self.ready()
        raw=encode(packet(amount="12345678.1234567890",scale=10))
        self.assertEqual(self.http(app,raw)[0],200)
        self.assertEqual(self.snapshot()[1][0][-1],"12345678.1234567890")

    def test_invalid_contracts_never_write(self):
        app=self.ready()
        mutations=[
            ("foreign source",lambda p:p.update(source_id=str(uuid4()))),
            ("foreign stream",lambda p:p.update(stream_id=str(uuid4()))),
            ("extra organization",lambda p:p.update(organization_id=ORG)),
            ("wrong dataset",lambda p:p.update(dataset="services")),
            ("unknown query",lambda p:p.update(query_sha256="0"*64)),
            ("missing day",lambda p:p.update(rows=[])),
            ("wrong money",lambda p:p["rows"][0].update(net_amount="999.00")),
            ("number instead of decimal string",lambda p:p["rows"][0].update(net_amount=12.34)),
            ("too many patients",lambda p:p["rows"][0].update(paying_patients=2)),
            ("wrong timezone",lambda p:p.update(source_timezone="UTC")),
            ("future observation",lambda p:p.update(observed_at="2026-02-01T00:00:00Z")),
            ("wrong coverage",lambda p:p.update(coverage="guess")),
            ("reversed time",lambda p:p.update(query_started_at="2026-01-20T11:00:00Z")),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                p=packet()
                mutate(p)
                self.assertEqual(self.http(app,encode(p))[0],422)
        for raw in (b"{",b"\xff",b'{"x":1,"x":2}',b"[]",b'{"x":NaN}'):
            self.assertEqual(self.http(app,raw)[0],422)
        self.assertEqual(self.snapshot(),([],[]))

    def test_zero_day_is_successful_coverage_not_missing(self):
        app=self.ready()
        p=packet()
        p["rows"][0].update(records_count=0,paying_patients=0,positive_amount="0",negative_amount="0",net_amount="0")
        self.assertEqual(self.http(app,encode(p))[0],200)
        self.assertEqual(self.snapshot()[1],[("2026-01-10",1,0,0,"0","0","0")])

    def test_atomic_rollback_if_projection_fails(self):
        app=self.ready()
        self.http(app,encode(packet()))
        before=self.snapshot()
        with sqlite3.connect(self.s.db) as conn:
            conn.execute("CREATE TRIGGER fail_daily BEFORE INSERT ON daily BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        status,obj,_=self.http(app,encode(packet(2)))
        self.assertEqual(status,503)
        self.assertNotIn("synthetic",json.dumps(obj))
        self.assertEqual(self.snapshot(),before)

    def test_capacity_retry_exact_duplicate_still_acknowledged(self):
        app=self.ready()
        raw=encode(packet())
        self.http(app,raw)
        before=self.snapshot()
        with patch("gateway.shutil.disk_usage",return_value=type("Disk",(),{"free":0})()):
            self.assertEqual(self.http(app,encode(packet(2)))[:2],(503,{"error":"STORE_CAPACITY"}))
            self.assertEqual(self.http(app,raw)[1]["status"],"already_accepted")
        self.assertEqual(self.snapshot(),before)

    def test_concurrent_duplicate_one_acceptance(self):
        app=self.ready()
        raw=encode(packet())
        with ThreadPoolExecutor(max_workers=4) as pool:
            answers=list(pool.map(lambda _:self.http(app,raw),range(4)))
        self.assertEqual(sorted(x[1]["status"] for x in answers),["accepted"]+["already_accepted"]*3)
        self.assertTrue(all(x[0]==200 for x in answers))
        self.assertEqual(len(self.snapshot()[0]),1)

    def test_concurrent_conflicting_payloads_no_double_write(self):
        app=self.ready()
        raws=[encode(packet(amount="1.00")),encode(packet(amount="2.00"))]
        with ThreadPoolExecutor(max_workers=2) as pool:
            answers=list(pool.map(lambda x:self.http(app,x),raws))
        self.assertEqual(sorted(x[0] for x in answers),[200,409])
        self.assertEqual(len(self.snapshot()[0]),1)
        self.assertEqual(len(self.snapshot()[1]),1)

    def test_status_no_financial_values(self):
        app=self.ready()
        self.http(app,encode(packet()))
        status,obj,_=self.http(app,route=g.STATUS_ROUTE,method="GET")
        self.assertEqual(status,200)
        self.assertEqual(obj["accepted_batches"],1)
        self.assertEqual(obj["max_sequence"],1)
        self.assertEqual(obj["last_observed_day"],"2026-01-10")
        self.assertEqual(obj["mode"],"shadow_only")
        self.assertNotIn("12.34",json.dumps(obj))

    def test_hot_journal_recovery_no_partial_batch(self):
        app=self.ready()
        self.http(app,encode(packet()))
        before=self.snapshot()
        script="""import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1],isolation_level=None)
db.execute('PRAGMA synchronous=FULL')
db.execute('PRAGMA cache_size=1')
db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE daily SET net_amount='999.00'")
db.execute("UPDATE batches SET payload=zeroblob(1048576)")
os._exit(91)
"""
        p=subprocess.run([sys.executable,"-c",script,str(self.s.db)],check=False)
        self.assertEqual(p.returncode,91)
        self.assertTrue(Path(str(self.s.db)+"-journal").exists())
        # The next startup must perform rollback recovery in the dedicated store.
        app2=g.make_app(self.s,lambda:NOW)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.http(app2,encode(packet(2)))[0],200)


if __name__ == "__main__":
    unittest.main()
