import importlib.util
import sqlite3
import sys
import types
import unittest
from datetime import date
from pathlib import Path

fake_flask=types.ModuleType("flask")
fake_flask.Response=object
fake_flask.abort=lambda *a,**k: (_ for _ in ()).throw(RuntimeError("abort"))
fake_flask.g=types.SimpleNamespace(user={"id":1})
fake_flask.jsonify=lambda **k:k
fake_flask.request=types.SimpleNamespace(args={},get_json=lambda silent=True:{})
sys.modules["flask"]=fake_flask

fake_app=types.ModuleType("app")
fake_app.DB_PATH=":memory:"
fake_app.SITE_ROOT=Path("/tmp")
fake_app.admin_required=lambda fn:fn
fake_app.audit=lambda *a,**k:None
fake_app.db=lambda:None
fake_app.iso_now=lambda:"2026-10-26T00:00:00+00:00"
fake_app.permission_required=lambda _key:(lambda fn:fn)
fake_app.require_csrf=lambda:None
sys.modules["app"]=fake_app

spec=importlib.util.spec_from_file_location("advertising_report",Path(__file__).with_name("advertising_report.py"))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def conn():
    c=sqlite3.connect(":memory:"); c.row_factory=sqlite3.Row; c.executescript(m.SCHEMA); return c

def seed(c):
    c.execute("INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",("direct","2026-10-01","yandex_ads","Реклама Яндекса","Платная реклама","10","Ортодонтия",1000,100,None,"x","a"))
    c.execute("INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",("metrika","2026-10-01","yandex_ads","Реклама Яндекса","Платная реклама","10","Ортодонтия",None,None,80,"x","b"))
    c.execute("INSERT INTO advertising_clinic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("clinic","2026-10-01","yandex_ads","Реклама Яндекса","Платная реклама","10","Ортодонтия",20,12,15,45000,50000,9,30000,"x","c"))
    c.execute("INSERT INTO advertising_cohort_daily VALUES(?,?,?,?,?,?,?,?,?,?,?)",("cohort","2026-10-01","yandex_ads","Реклама Яндекса","Платная реклама","10","Ортодонтия",11,90000,"x","d"))
    c.commit()

def sample():
    return {"schema_version":1,"generated_at":"2026-10-26T10:00:00+07:00","timezone":"Asia/Novosibirsk","currency":"RUB","vat_mode":"included","attribution_model":"last_significant","datasets":{
        "direct":{"period_start":"2026-10-01","period_end":"2026-10-01","rows":[{"date":"2026-10-01","source_key":"yandex_ads","source_name":"Реклама Яндекса","channel_name":"Платная реклама","campaign_id":"10","campaign_name":"Ортодонтия","spend":1000,"clicks":100}]},
        "clinic":{"period_start":"2026-10-01","period_end":"2026-10-01","rows":[{"date":"2026-10-01","source_key":"yandex_ads","source_name":"Реклама Яндекса","target_leads":20,"leads_with_primary":12,"primary_visits":15,"primary_paid":45000,"primary_services":50000,"repeat_visits":9,"repeat_paid":30000}]},
        "cohort":{"period_start":"2026-10-01","period_end":"2026-10-01","rows":[{"cohort_date":"2026-10-01","source_key":"yandex_ads","source_name":"Реклама Яндекса","new_patients":11,"new_patient_30d_paid":90000}]}
    }}

class AdvertisingReportTests(unittest.TestCase):
    def test_partial_month_comparison(self):
        self.assertEqual(m._comparison_ranges("previous_period",date(2026,10,1),date(2026,10,25)),[(date(2026,9,1),date(2026,9,25))])

    def test_previous_quarter_average_same_day_window(self):
        self.assertEqual(m._comparison_ranges("previous_quarter_avg",date(2026,10,1),date(2026,10,25)),[(date(2026,7,1),date(2026,7,25)),(date(2026,8,1),date(2026,8,25)),(date(2026,9,1),date(2026,9,25))])

    def test_last_year_leap_day(self):
        self.assertEqual(m._comparison_ranges("last_year",date(2024,2,29),date(2024,2,29)),[(date(2023,2,28),date(2023,2,28))])

    def test_summary_separates_30d_cohort_from_period_revenue(self):
        c=conn()
        try:
            seed(c); s=m._summary(c,date(2026,10,1),date(2026,10,1))
            self.assertEqual((s["spend"],s["clicks"],s["visits"]),(1000,100,80))
            self.assertEqual((s["target_leads"],s["leads_with_primary"],s["primary_visits"]),(20,12,15))
            self.assertEqual((s["primary_paid"],s["repeat_paid"],s["total_paid"]),(45000,30000,75000))
            self.assertEqual(s["new_patient_30d_paid"],90000)
            self.assertEqual(s["lead_to_primary_pct"],60)
        finally:c.close()

    def test_missing_is_none_not_zero(self):
        c=conn()
        try:
            s=m._summary(c,date(2026,10,1),date(2026,10,1))
            self.assertIsNone(s["spend"]); self.assertIsNone(s["target_leads"]); self.assertIsNone(s["total_paid"])
        finally:c.close()

    def test_import_preserves_russian_source_and_guards_conversion(self):
        p=sample(); n=m._normalize_package(p)
        self.assertEqual(n["datasets"]["direct"]["rows"][0]["source_name"],"Реклама Яндекса")
        p["datasets"]["clinic"]["rows"][0]["leads_with_primary"]=21
        with self.assertRaises(ValueError):m._normalize_package(p)

    def test_reimport_idempotent_and_range_replacement(self):
        c=conn()
        try:
            p=sample(); n=m._normalize_package(p); first=m._apply_import(c,n,1); c.commit(); second=m._apply_import(c,n,1); c.commit()
            self.assertFalse(first["reused"]); self.assertTrue(second["reused"])
            p["datasets"]["direct"]["rows"][0]["spend"]=1200
            m._apply_import(c,m._normalize_package(p),1); c.commit()
            self.assertEqual(c.execute("SELECT COUNT(*) FROM advertising_traffic_daily WHERE provider='direct'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT spend FROM advertising_traffic_daily WHERE provider='direct'").fetchone()[0],1200)
        finally:c.close()

    def test_ui_contract(self):
        root=Path(__file__).parents[1]/"reports"
        html=(root/"advertising.html").read_text(encoding="utf-8")
        js=(root/"advertising.js").read_text(encoding="utf-8")
        text=html+"\n"+js
        for label in ("Рекламный отчёт","Расходы на рекламу","Клики по рекламе","Посещения сайта","Целевые лиды","Состоявшиеся первичные приёмы","Выручка первичных приёмов","Выручка новых пациентов за 30 дней","Первичные","Повторные","Всего"):
            self.assertIn(label,text)
        for forbidden in ("Устройства","Глубина просмотра","Отказы"):
            self.assertNotIn(forbidden,text)

if __name__=="__main__":
    unittest.main()
