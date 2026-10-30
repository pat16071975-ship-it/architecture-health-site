import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import types
import unittest
import zipfile
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

def metrika_zip():
    payload = {
        "metadata": {
            "schema_version": "az_analytics_export_v1",
            "fetched_at_utc": "2026-10-28T02:48:32+00:00",
            "period_from": "2026-10-27",
            "period_to": "2026-10-28",
            "source_timezone": "Asia/Novosibirsk",
            "counter_id": "113776005",
            "sample_share": 1.0,
            "attribution": "cross_device_last_significant",
        },
        "metrica_traffic_day": [
            {"row_id":"geo20","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"ad","traffic_source_name":"Переходы по рекламе","source_detail":"Яндекс Директ: Не определено","source_detail_id":"ad.Яндекс Директ: Не определено","utm_source":"geoadv_direct","utm_content":"1921754454131403552","visits":20,"is_test_tag":False},
            {"row_id":"direct14","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"direct","traffic_source_name":"Прямые заходы","visits":14,"is_test_tag":False},
            {"row_id":"google8","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"organic","traffic_source_name":"Переходы из поисковых систем","source_detail":"Google","source_detail_id":"organic.google","visits":8,"is_test_tag":False},
            {"row_id":"igstar6","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"social","traffic_source_name":"Переходы из социальных сетей","source_detail":"instagram.com","source_detail_id":"social.instagram","utm_source":"instagram","utm_medium":"social","utm_campaign":"starostenko","utm_content":"profile","visits":6,"is_test_tag":False},
            {"row_id":"2gis6","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"referral","traffic_source_name":"Переходы по ссылкам на сайтах","source_detail":"link.2gis.ru","source_detail_id":"referral.link.2gis.ru","visits":6,"is_test_tag":False},
            {"row_id":"tap4","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"referral","traffic_source_name":"Переходы по ссылкам на сайтах","source_detail":"taplink.cc","source_detail_id":"referral.taplink.cc","visits":4,"is_test_tag":False},
            {"row_id":"yandex4","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"organic","traffic_source_name":"Переходы из поисковых систем","source_detail":"Яндекс","source_detail_id":"organic.yandex","visits":4,"is_test_tag":False},
            {"row_id":"internal3","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"internal","traffic_source_name":"Внутренние переходы","visits":3,"is_test_tag":False},
            {"row_id":"geo3","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"ad","traffic_source_name":"Переходы по рекламе","source_detail":"Яндекс Директ: Не определено","source_detail_id":"ad.Яндекс Директ: Не определено","utm_source":"geoadv_direct","utm_content":"1923074738892725053","visits":3,"is_test_tag":False},
            {"row_id":"nap2","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"referral","traffic_source_name":"Переходы по ссылкам на сайтах","utm_source":"napopravku","utm_medium":"referral","utm_campaign":"clinic_profile","visits":2,"is_test_tag":False},
            {"row_id":"igbio2","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"social","traffic_source_name":"Переходы из социальных сетей","source_detail":"instagram.com","source_detail_id":"social.instagram","utm_source":"ig","utm_medium":"social","utm_content":"link_in_bio","visits":2,"is_test_tag":False},
            {"row_id":"pro1","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"referral","traffic_source_name":"Переходы по ссылкам на сайтах","source_detail":"prodoctorov.ru","source_detail_id":"referral.prodoctorov.ru","utm_source":"prodoctorov","utm_medium":"referral","utm_campaign":"clinic_profile","visits":1,"is_test_tag":False},
            {"row_id":"geo1a","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"ad","traffic_source_name":"Переходы по рекламе","source_detail":"Яндекс Директ: Не определено","source_detail_id":"ad.Яндекс Директ: Не определено","utm_source":"geoadv_direct","utm_content":"1923074738892725033","visits":1,"is_test_tag":False},
            {"row_id":"geo1b","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"ad","traffic_source_name":"Переходы по рекламе","source_detail":"Яндекс Директ: Не определено","source_detail_id":"ad.Яндекс Директ: Не определено","utm_source":"geoadv_direct","utm_content":"1923074738892725037","visits":1,"is_test_tag":False},
            {"row_id":"test5","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"messenger","traffic_source_name":"Telegram","utm_source":"telegram","utm_campaign":"metrika_test","visits":5,"is_test_tag":True},
            {"row_id":"partial9","date":"2026-10-28","day_status":"partial","traffic_source_id":"direct","traffic_source_name":"Прямые заходы","visits":9,"is_test_tag":False},
        ],
        "metrica_goal_day": [
            {"traffic_row_id":"igstar6","date":"2026-10-27","goal_name":"Открытие окна записи","goal_visits":2,"reaches":5},
            {"traffic_row_id":"tap4","date":"2026-10-27","goal_name":"Открытие окна записи","goal_visits":1,"reaches":1},
            {"traffic_row_id":"igstar6","date":"2026-10-27","goal_name":"Переход к записи в Medflex","goal_visits":2,"reaches":5},
            {"traffic_row_id":"2gis6","date":"2026-10-27","goal_name":"Переход к записи в Medflex","goal_visits":1,"reaches":1},
            {"traffic_row_id":"direct14","date":"2026-10-27","goal_name":"Переход к записи в Medflex","goal_visits":1,"reaches":1},
            {"traffic_row_id":"tap4","date":"2026-10-27","goal_name":"Переход к записи в Medflex","goal_visits":1,"reaches":1},
            {"traffic_row_id":"test5","date":"2026-10-27","goal_name":"Переход к записи в Medflex","goal_visits":5,"reaches":10},
            {"traffic_row_id":"partial9","date":"2026-10-28","goal_name":"Автоцель: переход в мессенджер","goal_visits":2,"reaches":2},
        ],
    }
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("az_analytics.json", json.dumps(payload, ensure_ascii=False))
    return raw.getvalue()


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

    def test_metrika_zip_preserves_significant_utm_sources_and_goals(self):
        parsed = m._parse_metrika_zip(metrika_zip(), "batch.zip")
        self.assertEqual(parsed["completed_days"], ["2026-10-27"])
        self.assertEqual(sum(v["visits"] for v in parsed["traffic_rows"].values()), 75)

        by_name = {}
        for key, values in parsed["traffic_rows"].items():
            by_name[key[2]] = by_name.get(key[2], 0) + values["visits"]
        self.assertEqual(by_name["Яндекс Бизнес"], 25)
        self.assertEqual(by_name["Instagram — Старостенко"], 6)
        self.assertEqual(by_name["Instagram — ссылка в bio, профиль не определён"], 2)
        self.assertNotIn("Яндекс-реклама", by_name)

        goals = {}
        for key, values in parsed["goal_rows"].items():
            source_name, goal_key = key[2], key[6]
            goals[(source_name, goal_key)] = goals.get((source_name, goal_key), 0) + values["goal_visits"]
        self.assertEqual(goals[("Instagram — Старостенко", "booking_open")], 2)
        self.assertEqual(goals[("Instagram — Старостенко", "medflex")], 2)
        self.assertEqual(goals[("Taplink", "booking_open")], 1)
        self.assertEqual(goals[("Taplink", "medflex")], 1)
        self.assertEqual(goals[("2ГИС", "medflex")], 1)
        self.assertEqual(goals[("Прямые заходы", "medflex")], 1)

        with tempfile.TemporaryDirectory() as tmp:
            old = m.METRIKA_ARCHIVE_ROOT
            m.METRIKA_ARCHIVE_ROOT = Path(tmp)
            c = conn()
            try:
                stored = m._store_metrika(c, parsed, 1); c.commit()
                self.assertFalse(stored["reused"])
                self.assertFalse(stored["reclassified"])

                summary = m._summary(c, date(2026,10,27), date(2026,10,27))
                self.assertEqual(summary["visits"], 75)
                self.assertEqual(summary["booking_open_visits"], 3)
                self.assertEqual(summary["booking_open_reaches"], 6)
                self.assertEqual(summary["medflex_visits"], 5)
                self.assertEqual(summary["medflex_reaches"], 8)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM advertising_traffic_daily WHERE metric_date='2026-10-28'").fetchone()[0], 0)

                sources = {
                    row["source_name"]: row
                    for row in m._group_breakdown(c, date(2026,10,27), date(2026,10,27), "source")
                    if row["visits"] is not None
                }
                self.assertEqual(sum(row["visits"] for row in sources.values()), 75)
                self.assertEqual(sources["Яндекс Бизнес"]["visits"], 25)
                self.assertEqual(sources["Instagram — Старостенко"]["visits"], 6)
                self.assertEqual(sources["Instagram — Старостенко"]["booking_open_visits"], 2)
                self.assertEqual(sources["Instagram — Старостенко"]["medflex_visits"], 2)
                self.assertEqual(sources["Instagram — ссылка в bio, профиль не определён"]["visits"], 2)

                second = m._store_metrika(c, parsed, 1); c.commit()
                self.assertTrue(second["reused"])
                self.assertFalse(second["reclassified"])
            finally:
                c.close(); m.METRIKA_ARCHIVE_ROOT = old

    def test_existing_same_zip_is_reclassified_when_normalized_rows_changed(self):
        parsed = m._parse_metrika_zip(metrika_zip(), "batch.zip")
        with tempfile.TemporaryDirectory() as tmp:
            old = m.METRIKA_ARCHIVE_ROOT
            m.METRIKA_ARCHIVE_ROOT = Path(tmp)
            c = conn()
            try:
                m._store_metrika(c, parsed, 1); c.commit()
                c.execute(
                    "UPDATE advertising_traffic_daily SET source_key='yandex_ads',source_name='Яндекс-реклама' "
                    "WHERE provider='metrika' AND source_key='yandex_business'"
                )
                c.execute(
                    "UPDATE advertising_metrika_goal_daily SET source_key='instagram',source_name='Instagram' "
                    "WHERE provider='metrika' AND source_key='instagram_starostenko'"
                )
                c.execute(
                    "UPDATE advertising_traffic_daily SET source_key='instagram',source_name='Instagram' "
                    "WHERE provider='metrika' AND source_key IN ('instagram_starostenko','instagram_bio_unknown')"
                )
                c.commit()
                self.assertFalse(m._stored_metrika_matches(c, parsed))

                rebuilt = m._store_metrika(c, parsed, 1); c.commit()
                self.assertFalse(rebuilt["reused"])
                self.assertTrue(rebuilt["reclassified"])
                self.assertTrue(m._stored_metrika_matches(c, parsed))

                rows = c.execute(
                    "SELECT source_name,SUM(visits) AS visits FROM advertising_traffic_daily "
                    "WHERE provider='metrika' AND metric_date='2026-10-27' "
                    "GROUP BY source_name ORDER BY source_name"
                ).fetchall()
                values = {row["source_name"]: row["visits"] for row in rows}
                self.assertEqual(values["Яндекс Бизнес"], 25)
                self.assertEqual(values["Instagram — Старостенко"], 6)
                self.assertEqual(values["Instagram — ссылка в bio, профиль не определён"], 2)
                self.assertNotIn("Яндекс-реклама", values)
            finally:
                c.close(); m.METRIKA_ARCHIVE_ROOT = old


    def test_revenue_summary_puts_total_paid_first_and_reconciles_every_ruble_by_provider(self):
        c=conn()
        try:
            c.execute("CREATE TABLE advertising_mis_paid_ingests(period_start TEXT,period_end TEXT,source_filename TEXT,source_sha256 TEXT,imported_at TEXT,paid_total REAL,detailed_paid_total REAL,line_count INTEGER,PRIMARY KEY(period_start,period_end))")
            c.execute("CREATE TABLE cash_receipts_daily(data_date TEXT PRIMARY KEY,payload_json TEXT,source_filename TEXT,source_sha256 TEXT,imported_by INTEGER,imported_at TEXT)")
            c.execute("CREATE TABLE service_payment_periods(period_start TEXT,period_end TEXT,payload_json TEXT,source_filename TEXT,source_sha256 TEXT,imported_by INTEGER,imported_at TEXT,PRIMARY KEY(period_start,period_end))")
            c.execute("CREATE TABLE provider_registry(source_name TEXT PRIMARY KEY,display_name TEXT,direction TEXT)")
            c.executemany(
                "INSERT INTO provider_registry VALUES(?,?,?)",
                [
                    ("Dent A","Dent A","dent"),
                    ("Structure A","Structure A","structure"),
                    ("Lab A","Lab A","lab"),
                ],
            )
            provider_payload=json.dumps({
                "version":3,
                "period_start":"2026-10-27",
                "period_end":"2026-10-27",
                "totals":{"paid":800900},
                "providers":{
                    "Dent A":{"paid":655540},
                    "Structure A":{"paid":118260},
                    "Lab A":{"paid":27100},
                },
                "provider_residual":{"paid":0},
            },ensure_ascii=False)
            c.execute("INSERT INTO service_payment_periods VALUES(?,?,?,?,?,?,?)",("2026-10-27","2026-10-27",provider_payload,"paid.xlsx","sha",1,"t"))
            c.execute("INSERT INTO advertising_mis_paid_ingests VALUES(?,?,?,?,?,?,?,?)",("2026-10-27","2026-10-27","paid.xlsx","sha","t",800900,535680,71))
            c.execute("INSERT INTO advertising_clinic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("mis","2026-10-27","unknown","Неизвестно","Неизвестно","","",None,None,13,87100,90100,32,421480,"t","mis"))
            cash=json.dumps({"cashTotal":784400,"cashOOO":34600,"cashIP":749800,"cashUnallocated":245000})
            c.execute("INSERT INTO cash_receipts_daily VALUES(?,?,?,?,?,?)",("2026-10-27",cash,"cash.xlsx","sha",1,"t"))
            r=m._revenue_summary(c,date(2026,10,27),date(2026,10,27))
            self.assertEqual(r["clinic_paid_total"],800900)
            self.assertEqual(r["provider_paid_total"],800900)
            self.assertEqual(r["provider_residual"],0)
            self.assertEqual(r["provider_balance_diff"],0)
            self.assertEqual(r["direction_paid"],{"dent":655540.0,"structure":118260.0,"lab":27100.0,"other":0.0})
            self.assertEqual(sum(row["paid"] for row in r["providers"]),800900)
            self.assertEqual(r["detailed_paid_total"],535680)
            self.assertEqual(r["provider_known_without_patient_detail"],265220)
            self.assertEqual(r["unclassified_detailed"],27100)
            self.assertEqual(r["cash_total"],784400)
        finally:c.close()

    def test_selected_period_coverage_matches_source_evidence(self):
        c=conn()
        try:
            for day,visits in (("2026-10-27",75),("2026-10-28",42)):
                c.execute(
                    "INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("metrika",day,"direct","Прямые заходы","Прямые заходы","","",None,None,visits,"t","m"),
                )
            for day,primary,repeat in (
                ("2026-10-27",13,32),
                ("2026-10-28",3,17),
                ("2026-10-29",2,11),
            ):
                c.execute(
                    "INSERT INTO advertising_clinic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("mis",day,"unknown","Неизвестно","Неизвестно","","",None,None,primary,None,None,repeat,None,"t","mis"),
                )
            c.execute(
                "CREATE TABLE advertising_mis_paid_ingests("
                "period_start TEXT,period_end TEXT,source_filename TEXT,source_sha256 TEXT,"
                "imported_at TEXT,paid_total REAL,detailed_paid_total REAL,line_count INTEGER,"
                "PRIMARY KEY(period_start,period_end))"
            )
            c.execute(
                "INSERT INTO advertising_mis_paid_ingests VALUES(?,?,?,?,?,?,?,?)",
                ("2026-10-27","2026-10-27","paid.xlsx","sha","t",800900,535680,71),
            )
            c.execute(
                "CREATE TABLE cash_receipts_daily("
                "data_date TEXT PRIMARY KEY,payload_json TEXT,source_filename TEXT,"
                "source_sha256 TEXT,imported_by INTEGER,imported_at TEXT)"
            )
            for day,total in (
                ("2026-10-27",784400),
                ("2026-10-28",203100),
                ("2026-10-29",163490),
            ):
                c.execute(
                    "INSERT INTO cash_receipts_daily VALUES(?,?,?,?,?,?)",
                    (day,json.dumps({"cashTotal":total}),"cash.xlsx","sha",1,"t"),
                )
            c.commit()

            coverage=m._coverage_summary(c,date(2026,10,27),date(2026,10,30))
            self.assertEqual(
                (coverage["metrika"]["days"],coverage["metrika"]["from"],coverage["metrika"]["to"],coverage["metrika"]["complete"]),
                (2,"2026-10-27","2026-10-28",False),
            )
            self.assertEqual(
                (coverage["clinic"]["days"],coverage["clinic"]["to"],coverage["clinic"]["complete"]),
                (3,"2026-10-29",False),
            )
            self.assertEqual(
                (coverage["misPaid"]["days"],coverage["misPaid"]["from"],coverage["misPaid"]["complete"]),
                (1,"2026-10-27",False),
            )
            self.assertEqual(
                (coverage["cash"]["days"],coverage["cash"]["from"],coverage["cash"]["to"],coverage["cash"]["complete"]),
                (3,"2026-10-27","2026-10-29",None),
            )
            self.assertEqual(coverage["trackingStart"],"2026-10-27")
        finally:
            c.close()

    def test_ui_contract(self):
        root=Path(__file__).parents[1]/"reports"
        html=(root/"advertising.html").read_text(encoding="utf-8")
        js=(root/"advertising.js").read_text(encoding="utf-8")
        uploads=(Path(__file__).parent/"templates"/"uploads.html").read_text(encoding="utf-8")
        text=html+"\n"+js
        for label in ("Рекламный отчёт","Посещения сайта","Визиты с открытием записи","Визиты с переходом в Medflex","Целевые лиды","Работа клиники за период","Выручка клиники","Вся сумма «Оплачено» распределена по врачам/исполнителям","Источники сайта","Не хватает данных CRM для построения отчёта"):
            self.assertIn(label,text)
        for forbidden in ("Выручка новых пациентов за 30 дней","Новых пациентов, впервые пришедших","75 посещений → 13 первичных","Без построчной детализации в выгрузке МИС","Первично записались",'id="crmNote"',"crm-note","Выручка по врачам / исполнителям",'id="providerRevenueRows"'):
            self.assertNotIn(forbidden,text)
        self.assertIn('id="targetLeads"', html)
        self.assertIn("crm.message||'Не хватает данных CRM для построения отчёта'", js)
        self.assertIn("s.lead_to_primary_pct", js)
        self.assertNotIn("providerRevenueRows", js)
        self.assertIn("function siteSourceRows()", js)
        self.assertIn("body.innerHTML=sourceHtml+totalHtml", js)
        self.assertIn('<tr class="group-row"><td>Всего</td>', js)
        self.assertNotIn("function sourceGroups()", js)
        self.assertNotIn("group-btn", js)
        self.assertIn("5. Яндекс Метрика", uploads)
        self.assertIn('name="metrika_zip"', uploads)
        self.assertIn("/api/reports/advertising/metrika/import", uploads)
        self.assertIn('data-range="day">Сегодня</button>', html)
        self.assertIn('data-range="month">Текущий месяц по сегодня</button>', html)
        self.assertIn("Рекламные KPI собираются с 27.10.2026", html)
        self.assertNotIn('data-range="day">27 октября</button>', html)
        self.assertNotIn("function setDefaultDates()", js)
        self.assertNotIn("new Date(2026,9,27)", js)
        self.assertIn("clinicToday=payload.today||clinicToday", js)
        self.assertIn("load({serverDefaults:true})", js)
        self.assertIn("history.replaceState", js)
        self.assertIn("q.set('from',$('#fromDate').value)", js)
        self.assertIn("q.set('to',$('#toDate').value)", js)
        self.assertIn("new Date(Date.UTC(y,m,0)).getUTCDate()", js)
        self.assertIn('id="coverage"', html)
        self.assertIn("Покрытие выбранного периода", js)
        self.assertIn("coveragePart('Метрика',c.metrika)", js)
        self.assertIn("coveragePart('«Оплачено» MIS',c.misPaid)", js)

    def test_arbitrary_multi_day_range_sums_only_available_days(self):
        c=conn()
        try:
            seed(c)
            c.execute(
                "INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-10-03","direct","Прямые заходы","Прямые заходы","","",None,None,20,"x","m3"),
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-10-05","direct","Прямые заходы","Прямые заходы","","",None,None,30,"x","m5"),
            )
            c.commit()
            summary=m._summary(c,date(2026,10,1),date(2026,10,5))
            self.assertEqual(summary["visits"],130)
            empty=m._summary(c,date(2026,10,2),date(2026,10,2))
            self.assertIsNone(empty["visits"])
        finally:
            c.close()


if __name__=="__main__":
    unittest.main()
