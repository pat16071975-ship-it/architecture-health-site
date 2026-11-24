"""Real Flask routes, real parsers and isolated SQLite for all six IDENT paths.

Only user/session setup, audit delivery and downstream advertising sync are
isolated. Do not mock source parsing, business writes or archive storage.
"""
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AZBAZE_SECRET_KEY", "synthetic-archive-route-tests")
os.environ.setdefault("AZBAZE_DB", "/tmp/az-archive-route-tests-bootstrap.db")
os.environ.setdefault("AZBAZE_SITE_ROOT", "/tmp/az-archive-route-tests-site")

from flask import Flask, g
from openpyxl import Workbook
import app as app_core
import daily_upload
import daily_upload_core as core
import completed_upload
import services_upload
import cash_upload
import cash_payments
import paid_services
import paid_services_upload
import ident_import
import report_storage
import upload_reconcile
import upload_integrity
import advertising_mis_bridge
import mis_source_archive as archive
from test_paid_services_periods import report_bytes

FIRST="2026-11-01"
SECOND="2026-11-02"
THIRD="2026-11-03"
STAFF="Dent D."


def display(day):
    return ".".join(reversed(day.split("-")))


def xlsx(rows,title):
    book=Workbook()
    book.active.title=title
    for row in rows:
        book.active.append(row)
    out=io.BytesIO()
    book.save(out)
    book.close()
    return out.getvalue()


def text_rows(rows):
    return "\n".join("\t".join("" if x is None else str(x) for x in row)
                     for row in rows).encode("utf-8")


def service_rows(days,amount=100):
    rows=[["Header"],["Header two"],[STAFF]]
    for number,day in enumerate(days,1):
        rows.extend([
            ["Пациент С."],
            [f"Счет №{number} от {display(day)} 12:00:00"],
            ["Терапия",display(day),"Лечение",1,amount,0,amount],
        ])
    return rows


def completed_rows(days):
    rows=[["Header"],["Header two"],["Header three"],["Header four"]]
    for day in days:
        rows.extend([
            [display(day),None,"1 (1)"],
            ["Первичные",None,"1 (1)"],
            [STAFF,None,"1 (1)"],
            [display(day),"Пациент С."],
        ])
    return rows


class ArchiveRouteTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dbpath=Path(temp.name)/"auth.db"
        self.conn=sqlite3.connect(self.dbpath)
        self.addCleanup(self.conn.close)
        self.conn.row_factory=sqlite3.Row
        self.conn.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY); INSERT INTO users VALUES(7);")
        self.conn.executescript(report_storage.REPORT_SCHEMA)
        self.conn.executescript(core.UPLOAD_SCHEMA)
        upload_reconcile.init_schema(self.conn)
        cash_payments.init_schema(self.conn)
        paid_services.init_schema(self.conn)
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        def set_attr(module,name,value):
            self.stack.enter_context(patch.object(module,name,value))
        self.perms={"section5","upload_completed","upload_services","upload_replace"}
        self.is_admin=True
        for module in (app_core,core,completed_upload,services_upload,cash_upload,
                       paid_services_upload,ident_import,report_storage):
            if hasattr(module,"db"):
                set_attr(module,"db",lambda:self.conn)
            if hasattr(module,"audit"):
                set_attr(module,"audit",lambda *a,**k:None)
        for module in (app_core,core,daily_upload,completed_upload,services_upload,
                       cash_upload,paid_services_upload):
            if hasattr(module,"user_permissions"):
                set_attr(module,"user_permissions",lambda user:set(self.perms))
        set_attr(core,"_init_schema",lambda:None)
        set_attr(upload_reconcile,"DB_PATH",self.dbpath)
        set_attr(ident_import,"DENTISTS",{STAFF:"Synthetic Dentist"})
        set_attr(ident_import,"STRUCTURE_DOCTORS",{})
        set_attr(ident_import,"LAB_DOCTORS",{})
        set_attr(ident_import,"KNOWN_STAFF",{STAFF})
        set_attr(cash_payments,"_PROVIDER_MAPS",{"dent":{},"structure":{},"lab":{}})
        set_attr(advertising_mis_bridge,"rebuild",lambda *a,**k:{})
        set_attr(advertising_mis_bridge,"capture_paid_report",lambda *a,**k:{})
        # Match server.py's existing integration while restoring global
        # functions for other test modules in the same interpreter.
        for module,name in ((core,"_daily_delta"),(core,"_rebuild_management"),
                            (ident_import,"_parse_revenue"),(ident_import,"_build_management")):
            set_attr(module,name,getattr(module,name))
        self.stack.enter_context(patch.object(core,"_az_upload_integrity_installed",
            getattr(core,"_az_upload_integrity_installed",False),create=True))
        upload_integrity.install(core)
        service_data=services_upload._empty_service_data(2026)
        services_upload._ensure_months_through(service_data,2026,10)
        service_data["id"]="az-services-2026-through-2026-10-31-v1"
        self.conn.execute("INSERT INTO report_blobs VALUES(?,?,?,?)",
            ("az-service-analytics-v1",json.dumps(service_data),7,"seed"))
        self.conn.commit()
        self.app=Flask(__name__)
        self.app.config.update(TESTING=True,SECRET_KEY="synthetic-archive")
        @self.app.before_request
        def user():
            g.user={"id":7,"is_admin":self.is_admin}
        for register in (daily_upload.register_daily_upload,
                         completed_upload.register_completed_upload,
                         services_upload.register_services_upload,
                         cash_upload.register_cash_upload,
                         paid_services_upload.register_paid_services_upload,
                         ident_import.register_ident_import):
            register(self.app)
        self.client=self.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf"]="csrf"

    def post(self,path,files,decision="append",**fields):
        data={"csrf":"csrf","decision":decision,**fields}
        for key,(raw,name) in files.items():
            data[key]=(io.BytesIO(raw),name)
        return self.client.post(path,data=data,headers={"X-CSRF-Token":"csrf"})

    def receipt(self,raw,kind):
        sha=hashlib.sha256(raw).hexdigest()
        self.assertEqual(archive.read_original(self.conn,sha),raw)
        rows=self.conn.execute("""SELECT * FROM mis_source_events
            WHERE sha256=? AND source_kind=? AND outcome='applied'
            ORDER BY saved_at,event_key""",(sha,kind)).fetchall()
        self.assertTrue(rows,"No applied receipt for "+kind)
        return rows[-1]

    def assert_days(self,row,observed,applied):
        self.assertEqual(json.loads(row["observed_dates_json"]),observed)
        self.assertEqual(json.loads(row["applied_dates_json"]),applied)
        self.assertEqual((row["source_from"],row["source_to"]),
                         (min(observed),max(observed)))

    def service_file(self,days,amount=100):
        return xlsx(service_rows(days,amount),"Выручка по направлениям")

    def completed_file(self,days):
        return xlsx(completed_rows(days),"Завершенные приемы")

    def cash_file(self,days,amount=100):
        rows=[["Дата и время","Пациент/Компания","Операция","Сумма к оплате (₽)",
               "Движение ДС (₽)","Касса","Дата и время чека","ККМ",
               "РН ККТ","ФН","№ ФД","ФПД"]]
        for i,day in enumerate(days,1):
            rows.append([f"{int(day[-2:])} ноя 2026\n12:00","Синтетический пациент",
                         f"№{i} {STAFF}",amount,amount,"Основная","",
                         cash_payments.OOO_KKM,"","","",""])
        return xlsx(rows,"Счета и оплаты")

    def assert_ok(self,response):
        self.assertEqual(response.status_code,200,str(response.get_json())[:500])

    def assert_pair(self,completed,services,cdates,sdates,origin,removed=()):
        c=self.receipt(completed,"completed")
        s=self.receipt(services,"services")
        self.assert_days(c,cdates,cdates)
        self.assert_days(s,sdates,sdates)
        contexts=[json.loads(r["context_json"]) for r in (c,s)]
        op=contexts[0]["paired_operation"]
        self.assertEqual(op,contexts[1]["paired_operation"])
        self.assertEqual(op["origin"],origin)
        self.assertEqual(op["written_dates"],sorted(set(cdates+sdates)))
        self.assertEqual(op["removed_dates"],list(removed))
        self.assertEqual(op["removal_reason"],"absent_from_replacement" if removed else None)
        self.assertEqual(contexts[0]["unobserved_layer_dates"],sorted(set(sdates)-set(cdates)))
        self.assertEqual(contexts[1]["unobserved_layer_dates"],sorted(set(cdates)-set(sdates)))
        self.assertEqual(json.loads(c["removed_dates_json"]),[])
        self.assertEqual(json.loads(s["removed_dates_json"]),[])
        self.assertEqual({x["sha256"] for x in op["sources"]},
                         {hashlib.sha256(completed).hexdigest(),hashlib.sha256(services).hexdigest()})

    def test_services_http_receipt_and_replay(self):
        raw=self.service_file([FIRST,THIRD])
        files={"services":(raw,"s.xlsx")}
        self.assert_ok(self.post("/api/uploads/services/commit",files))
        self.assert_days(self.receipt(raw,"services"),[FIRST,THIRD],[FIRST,THIRD])
        first=self.conn.execute("SELECT count(*) FROM daily_uploads").fetchone()[0]
        for _ in range(2):
            self.assert_ok(self.post("/api/uploads/services/commit",files))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM daily_uploads").fetchone()[0],first)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM mis_source_objects").fetchone()[0],1)

    def test_completed_http_receipt(self):
        raw=self.completed_file([FIRST,THIRD])
        self.assert_ok(self.post("/api/uploads/completed/commit",{"completed":(raw,"c.xlsx")}))
        self.assert_days(self.receipt(raw,"completed"),[FIRST,THIRD],[FIRST,THIRD])
        self.assertEqual([x[0] for x in self.conn.execute("SELECT data_date FROM daily_uploads ORDER BY 1")],
                         [FIRST,THIRD])

    def test_cash_http_receipt_and_replacement_rollback(self):
        raw=self.cash_file([FIRST,SECOND])
        self.assert_ok(self.post("/api/uploads/cash/commit",{"payments":(raw,"cash.xlsx")}))
        self.assert_days(self.receipt(raw,"cash"),[FIRST,SECOND],[FIRST,SECOND])
        before=list(self.conn.iterdump())
        changed=self.cash_file([FIRST,SECOND],amount=200)
        original=archive.record
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise sqlite3.IntegrityError("archive fault")
        with patch.object(archive,"record",side_effect=fail):
            with self.assertRaisesRegex(sqlite3.IntegrityError,"archive fault"):
                self.post("/api/uploads/cash/commit",{"payments":(changed,"new.xlsx")},"use_new")
        self.assertEqual(list(self.conn.iterdump()),before)
        self.assertFalse(self.conn.in_transaction)

    def test_paid_http_receipt_exact_period(self):
        raw=report_bytes(("01.11.2026","03.11.2026"),provider=STAFF,
                         period=("01.11.2026","03.11.2026"))
        self.assert_ok(self.post("/api/uploads/paid-services/commit",
                                {"paid_services":(raw,"paid.xlsx")},expected_revision="new"))
        row=self.receipt(raw,"paid_services")
        self.assertEqual((row["source_from"],row["source_to"]),(FIRST,THIRD))
        self.assertIsNone(row["observed_dates_json"])
        self.assertIsNone(row["applied_dates_json"])

    def test_clinical_pair_http_disjoint_and_explicit_removal(self):
        oldc=self.completed_file([FIRST,SECOND])
        olds=self.service_file([SECOND,THIRD])
        self.assert_ok(self.post("/api/uploads/clinical/commit",
                                {"completed":(oldc,"c0.xlsx"),"services":(olds,"s0.xlsx")}))
        c=self.completed_file([FIRST])
        s=self.service_file([THIRD],amount=200)
        self.assert_ok(self.post("/api/uploads/clinical/commit",
                                {"completed":(c,"c.xlsx"),"services":(s,"s.xlsx")},"use_new"))
        self.assert_pair(c,s,[FIRST],[THIRD],"ident_clinical_pair",[SECOND])
        self.assertEqual([x[0] for x in self.conn.execute("SELECT data_date FROM daily_uploads ORDER BY 1")],
                         [FIRST,THIRD])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM mis_source_objects").fetchone()[0],4)

    def test_clinical_pair_http_rollback_second_source(self):
        c=self.completed_file([FIRST])
        s=self.service_file([THIRD])
        before=list(self.conn.iterdump())
        original=archive.record
        def fail_second(conn,kind,*args,**kwargs):
            original(conn,kind,*args,**kwargs)
            if kind=="services":
                raise sqlite3.IntegrityError("second source fault")
        with patch.object(archive,"record",side_effect=fail_second):
            with self.assertRaisesRegex(sqlite3.IntegrityError,"second source fault"):
                self.post("/api/uploads/clinical/commit",
                          {"completed":(c,"c.xlsx"),"services":(s,"s.xlsx")})
        self.assertEqual(list(self.conn.iterdump()),before)
        self.assertFalse(self.conn.in_transaction)

    def test_legacy_pair_http_disjoint_and_rollback(self):
        c=text_rows(completed_rows([FIRST]))
        s=text_rows(service_rows([THIRD]))
        files={"completed":(c,"c.tsv"),"revenue":(s,"s.tsv")}
        before=list(self.conn.iterdump())
        original=archive.record
        def fail_second(conn,kind,*args,**kwargs):
            original(conn,kind,*args,**kwargs)
            if kind=="completed":
                raise sqlite3.IntegrityError("second source fault")
        with patch.object(archive,"record",side_effect=fail_second):
            with self.assertRaisesRegex(sqlite3.IntegrityError,"second source fault"):
                self.post("/api/reports/import-ident",files)
        self.assertEqual(list(self.conn.iterdump()),before)
        self.assert_ok(self.post("/api/reports/import-ident",files))
        self.assert_pair(c,s,[FIRST],[THIRD],"ident_legacy_pair")
        self.assertEqual([x[0] for x in self.conn.execute("SELECT date FROM report_data ORDER BY 1")],
                         [FIRST,THIRD])

    def test_preview_and_permission_do_not_archive(self):
        raw=self.service_file([FIRST])
        files={"services":(raw,"s.xlsx")}
        self.assert_ok(self.post("/api/uploads/services/preview",files))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='mis_source_objects'").fetchone())
        self.perms={"section5"}
        self.assertEqual(self.post("/api/uploads/services/commit",files).status_code,403)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='mis_source_objects'").fetchone())


if __name__ == "__main__":
    unittest.main()
