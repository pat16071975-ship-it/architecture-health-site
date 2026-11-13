import importlib.util
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import types
import unittest
import terminology
import metrika_auto as ma
import zipfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
fake_app.csrf_token=lambda:"csrf"
fake_app.db=lambda:None
fake_app.iso_now=lambda:"2026-10-26T00:00:00+00:00"
fake_app.permission_required=lambda _key:(lambda fn:fn)
fake_app.require_csrf=lambda:None
fake_app.user_permissions=lambda _user:{"reports","upload_services"}
sys.modules["app"]=fake_app

spec=importlib.util.spec_from_file_location("advertising_report",Path(__file__).with_name("advertising_report.py"))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def conn():
    c=sqlite3.connect(":memory:"); c.row_factory=sqlite3.Row; c.executescript(m.SCHEMA); return c

def seed(c):
    c.execute("INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",("direct","2026-10-01","yandex_ads","Реклама Яндекса","Платная реклама","10","Ортодонтия",1000,100,None,"x","a"))
    c.execute("INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",("metrika","2026-10-01","yandex_ads","Реклама Яндекса","Платная реклама","10","Ортодонтия",None,None,80,"x","b"))
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


def metrika_campaign_breakdown_zip():
    with zipfile.ZipFile(io.BytesIO(metrika_zip())) as source:
        payload = json.loads(source.read("az_analytics.json").decode("utf-8"))

    payload["metrica_traffic_day"].extend([
        {"row_id":"yandex36","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"ad","traffic_source_name":"Переходы по рекламе","source_detail":"Яндекс: Директ","source_detail_id":"ad.Яндекс: Директ","visits":36,"is_test_tag":False},
        {"row_id":"brux1","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"ad","traffic_source_name":"Переходы по рекламе","source_detail":"Яндекс: Директ","source_detail_id":"ad.Яндекс: Директ","utm_source":"yandex","utm_medium":"cpc","utm_campaign":"bruksizm_search_nsk","visits":1,"is_test_tag":False},
        {"row_id":"igzub3","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"social","traffic_source_name":"Переходы из социальных сетей","source_detail":"instagram.com","source_detail_id":"social.instagram","utm_source":"instagram","utm_medium":"social","utm_campaign":"zubachev","utm_content":"profile","visits":3,"is_test_tag":False},
        {"row_id":"igclinic4","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"social","traffic_source_name":"Переходы из социальных сетей","source_detail":"instagram.com","source_detail_id":"social.instagram","utm_source":"instagram","utm_medium":"social","utm_campaign":"clinic","utm_content":"profile","visits":4,"is_test_tag":False},
        {"row_id":"igstory2","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"social","traffic_source_name":"Переходы из социальных сетей","source_detail":"instagram.com","source_detail_id":"social.instagram","utm_source":"instagram","utm_medium":"social","utm_campaign":"story_autumn","visits":2,"is_test_tag":False},
        {"row_id":"igunknown1","date":"2026-10-27","day_status":"completed_calendar_day_revisable","traffic_source_id":"social","traffic_source_name":"Переходы из социальных сетей","source_detail":"instagram.com","source_detail_id":"social.instagram","utm_source":"instagram","utm_medium":"social","visits":1,"is_test_tag":False},
    ])
    payload["metrica_goal_day"].extend([
        {"traffic_row_id":"yandex36","date":"2026-10-27","goal_name":"Открытие окна записи","goal_visits":2,"reaches":2},
        {"traffic_row_id":"yandex36","date":"2026-10-27","goal_name":"Переход к записи в Medflex","goal_visits":1,"reaches":1},
        {"traffic_row_id":"igzub3","date":"2026-10-27","goal_name":"Открытие окна записи","goal_visits":1,"reaches":1},
    ])
    payload["goal_registry"] = [
        {"goal_id":"674318079","goal_name":"Открытие окна записи"},
        {"goal_id":"674318216","goal_name":"Переход к записи в Medflex"},
    ]

    report = """campaigns_clean
Метрики: ym:s:visits, ym:s:goal674318079reaches, ym:s:goal674318079visits, ym:s:goal674318216reaches, ym:s:goal674318216visits
Итого: [60.0, 2.0, 2.0, 1.0, 1.0]
АЗ | Ортодонт и гнатолог | Поиск | Новосибирск | 34.0, 2.0, 2.0, 1.0, 1.0
Чужие кампании | 25.0, 0.0, 0.0, 0.0, 0.0
АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск | 1.0, 0.0, 0.0, 0.0, 0.0

"""

    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("az_analytics.json", json.dumps(payload, ensure_ascii=False))
        z.writestr("REPORT-2026-10-27.txt", report)
    return raw.getvalue()


def legacy_alias_zip():
    payload = {
        "metadata": {
            "schema_version": "az_analytics_export_v1",
            "fetched_at_utc": "2026-10-28T23:00:00+00:00",
            "period_from": "2026-10-28",
            "period_to": "2026-10-28",
            "source_timezone": "Asia/Novosibirsk",
            "counter_id": "113776005",
            "sample_share": 1.0,
            "attribution": "cross_device_last_significant",
        },
        "metrica_traffic_day": [
            {"row_id":"brux_native","date":"2026-10-28","day_status":"completed_calendar_day_revisable",
             "traffic_source_id":"ad","traffic_source_name":"Переходы по рекламе","source_detail":"Яндекс: Директ",
             "utm_source":"yandex","utm_medium":"cpc","utm_campaign":"bruksizm_search_nsk",
             "direct_campaign_id":"715444498","direct_campaign_name":"АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",
             "visits":5,"is_test_tag":False},
            {"row_id":"brux_utm","date":"2026-10-28","day_status":"completed_calendar_day_revisable",
             "traffic_source_id":"ad","traffic_source_name":"Переходы по рекламе","source_detail":"Яндекс Директ: Не определено",
             "utm_source":"yandex","utm_medium":"cpc","utm_campaign":"bruksizm_search_nsk",
             "visits":1,"is_test_tag":False},
            {"row_id":"ig_star","date":"2026-10-28","day_status":"completed_calendar_day_revisable",
             "traffic_source_id":"social","traffic_source_name":"Переходы из социальных сетей","source_detail":"instagram.com",
             "utm_source":"instagram","utm_medium":"social","utm_campaign":"starostenko","utm_content":"profile",
             "visits":2,"is_test_tag":False},
            {"row_id":"ig_bio","date":"2026-10-28","day_status":"completed_calendar_day_revisable",
             "traffic_source_id":"social","traffic_source_name":"Переходы из социальных сетей","source_detail":"instagram.com",
             "utm_source":"ig","utm_medium":"social","utm_content":"link_in_bio",
             "visits":1,"is_test_tag":False},
        ],
        "metrica_goal_day": [
            {"traffic_row_id":"ig_star","date":"2026-10-28","goal_name":"Переход к записи в Medflex","goal_visits":1,"reaches":1},
        ],
        "goal_registry": [],
        "direct_campaign_day": None,
        "daily_summary": [],
    }
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("az_analytics.json", json.dumps(payload, ensure_ascii=False))
    return raw.getvalue()


def unknown_id_conflict_zip():
    with zipfile.ZipFile(io.BytesIO(legacy_alias_zip())) as source:
        payload=json.loads(source.read("az_analytics.json").decode("utf-8"))
    payload["metrica_traffic_day"].append(
        {
            "row_id":"brux_unknown_id",
            "date":"2026-10-28",
            "day_status":"completed_calendar_day_revisable",
            "traffic_source_id":"ad",
            "traffic_source_name":"Переходы по рекламе",
            "source_detail":"Яндекс: Директ",
            "utm_source":"yandex",
            "utm_medium":"cpc",
            "utm_campaign":"bruksizm_search_nsk",
            "direct_campaign_id":"999999999",
            "direct_campaign_name":"АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",
            "visits":4,
            "is_test_tag":False,
        }
    )
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("az_analytics.json",json.dumps(payload,ensure_ascii=False))
    return raw.getvalue()


def unknown_id_conflict_with_campaign_report_zip():
    with zipfile.ZipFile(io.BytesIO(metrika_campaign_breakdown_zip())) as source:
        payload=json.loads(source.read("az_analytics.json").decode("utf-8"))
        report=source.read("REPORT-2026-10-27.txt")
    payload["metrica_traffic_day"].append(
        {
            "row_id":"unknown_report_conflict",
            "date":"2026-10-27",
            "day_status":"completed_calendar_day_revisable",
            "traffic_source_id":"ad",
            "traffic_source_name":"Переходы по рекламе",
            "source_detail":"Яндекс: Директ",
            "utm_source":"yandex",
            "utm_medium":"cpc",
            "utm_campaign":"bruksizm_search_nsk",
            "direct_campaign_id":"999999999",
            "direct_campaign_name":"АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",
            "visits":4,
            "is_test_tag":False,
        }
    )
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("az_analytics.json",json.dumps(payload,ensure_ascii=False))
        z.writestr("REPORT-2026-10-27.txt",report)
    return raw.getvalue()


def unknown_empty_id_with_campaign_report_zip():
    with zipfile.ZipFile(io.BytesIO(legacy_alias_zip())) as source:
        payload=json.loads(source.read("az_analytics.json").decode("utf-8"))
    payload["metrica_traffic_day"].append(
        {
            "row_id":"unknown_empty_name",
            "date":"2026-10-28",
            "day_status":"completed_calendar_day_revisable",
            "traffic_source_id":"ad",
            "traffic_source_name":"Переходы по рекламе",
            "source_detail":"Яндекс: Директ",
            "direct_campaign_id":"999999999",
            "direct_campaign_name":"",
            "visits":4,
            "is_test_tag":False,
        }
    )
    report="""campaigns_clean
Метрики: ym:s:visits
Итого: [10.0]
АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск | 6.0
Не определено | 4.0

"""
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("az_analytics.json",json.dumps(payload,ensure_ascii=False))
        z.writestr("REPORT-2026-10-28.txt",report)
    return raw.getvalue()




def new_api_test_only_native_campaign_zip():
    day="2026-10-31"
    payload={
        "metadata":{
            "schema_version":"az_analytics_export_v1",
            "fetched_at_utc":"2026-11-01T01:00:00+00:00",
            "period_from":day,
            "period_to":day,
            "day_status":"completed_calendar_day_revisable",
            "source_timezone":"Asia/Novosibirsk",
            "counter_id":"113776005",
            "sample_share":1.0,
            "attribution":"cross_device_last_significant",
            "campaign_identity_source":"traffic_direct_click_order",
        },
        "metrica_traffic_day":[
            {
                "row_id":"test-native-5",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"ad",
                "traffic_source_name":"Переходы по рекламе",
                "source_detail":"Яндекс: Директ",
                "source_detail_id":"ad.Яндекс: Директ",
                "utm_campaign":"metrika_test",
                "direct_campaign_id":"999999999",
                "direct_campaign_name":"Тестовая кампания",
                "visits":5,
                "is_test_tag":True,
            },
            {
                "row_id":"work-undefined-7",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"ad",
                "traffic_source_name":"Переходы по рекламе",
                "source_detail":"Яндекс: Директ",
                "source_detail_id":"ad.Яндекс: Директ",
                "utm_source":"yandex",
                "utm_medium":"cpc",
                "utm_campaign":"bruksizm_search_nsk",
                "visits":7,
                "is_test_tag":False,
            },
        ],
        "metrica_goal_day":[],
        "goal_registry":[],
        "direct_campaign_day":None,
        "daily_summary":{day:{"visits":12,"test_tag_visits":5}},
    }
    report="""campaigns_clean
Метрики: ym:s:visits
Итого: [5.0]
Тестовая кампания | 5.0

"""
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("az_analytics.json",json.dumps(payload,ensure_ascii=False))
        z.writestr(f"REPORT-{day}.txt",report)
    return raw.getvalue()


def universal_dynamic_identity_zip():
    day="2026-11-07"
    payload={
        "metadata":{
            "schema_version":"az_analytics_export_v1",
            "fetched_at_utc":"2026-11-08T01:00:00+00:00",
            "period_from":day,
            "period_to":day,
            "day_status":"completed_calendar_day_revisable",
            "source_timezone":"Asia/Novosibirsk",
            "counter_id":"113776005",
            "sample_share":1.0,
            "attribution":"cross_device_last_significant",
            "campaign_identity_source":"traffic_direct_click_order",
        },
        "metrica_traffic_day":[
            {
                "row_id":"future-real-id",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"ad",
                "traffic_source_name":"Переходы по рекламе",
                "source_detail":"Яндекс: Директ",
                "source_detail_id":"ad.Яндекс: Директ",
                "utm_source":"yandex",
                "utm_medium":"cpc",
                "utm_campaign":"future_named_from_utm",
                "direct_campaign_id":"991234567",
                "direct_campaign_name":"",
                "visits":4,
                "is_test_tag":False,
            },
            {
                "row_id":"future-other-alpha",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"ad",
                "traffic_source_name":"Переходы по рекламе",
                "source_detail":"Яндекс: Директ",
                "source_detail_id":"ad.Яндекс: Директ",
                "utm_source":"yandex",
                "utm_medium":"cpc",
                "utm_campaign":"future_service_alpha",
                "direct_campaign_id":"other",
                "direct_campaign_name":"Чужие кампании",
                "visits":17,
                "is_test_tag":False,
            },
            {
                "row_id":"future-blank-beta",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"ad",
                "traffic_source_name":"Переходы по рекламе",
                "source_detail":"Яндекс: Директ",
                "source_detail_id":"ad.Яндекс: Директ",
                "utm_source":"yandex",
                "utm_medium":"cpc",
                "utm_campaign":"future_service_beta",
                "direct_campaign_id":"",
                "direct_campaign_name":"",
                "visits":12,
                "is_test_tag":False,
            },
            {
                "row_id":"future-truly-unknown",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"ad",
                "traffic_source_name":"Переходы по рекламе",
                "source_detail":"Яндекс: Директ",
                "source_detail_id":"ad.Яндекс: Директ",
                "visits":3,
                "is_test_tag":False,
            },
            {
                "row_id":"future-paid-source",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"ad",
                "traffic_source_name":"Переходы по рекламе",
                "source_detail":"Future Ads Network",
                "utm_source":"future_paid_network",
                "utm_medium":"cpc",
                "utm_campaign":"launch_alpha",
                "visits":5,
                "is_test_tag":False,
            },
            {
                "row_id":"future-paid-direct-word-source",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"ad",
                "traffic_source_name":"Переходы по рекламе",
                "source_detail":"Future Ads Network Two",
                "utm_source":"future_direct_mail_network",
                "utm_medium":"cpc",
                "utm_campaign":"mail_launch",
                "visits":8,
                "is_test_tag":False,
            },
            {
                "row_id":"future-social-source",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"social",
                "traffic_source_name":"Переходы из социальных сетей",
                "source_detail":"future.social",
                "utm_source":"future_social_network",
                "utm_campaign":"creator_launch",
                "visits":6,
                "is_test_tag":False,
            },
            {
                "row_id":"future-referral-source",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"referral",
                "traffic_source_name":"Переходы по ссылкам на сайтах",
                "utm_source":"future_partner",
                "utm_campaign":"clinic_profile",
                "visits":7,
                "is_test_tag":False,
            },
        ],
        "metrica_goal_day":[
            {
                "traffic_row_id":"future-other-alpha",
                "date":day,
                "goal_name":"Открытие окна записи",
                "goal_visits":2,
                "reaches":2,
            },
            {
                "traffic_row_id":"future-paid-source",
                "date":day,
                "goal_name":"Переход к записи в Medflex",
                "goal_visits":1,
                "reaches":1,
            },
        ],
        "goal_registry":[],
        "direct_campaign_day":None,
        "daily_summary":{day:{"visits":62,"test_tag_visits":0}},
    }
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("az_analytics.json",json.dumps(payload,ensure_ascii=False))
    return raw.getvalue()


def az186_blocker_identity_zip():
    day="2026-11-07"
    payload={
        "metadata":{
            "schema_version":"az_analytics_export_v1",
            "fetched_at_utc":"2026-11-08T02:00:00+00:00",
            "period_from":day,
            "period_to":day,
            "day_status":"completed_calendar_day_revisable",
            "source_timezone":"Asia/Novosibirsk",
            "counter_id":"113776005",
            "sample_share":1.0,
            "attribution":"cross_device_last_significant",
            "campaign_identity_source":"traffic_direct_click_order",
        },
        "metrica_traffic_day":[
            {
                "row_id":"az186-01-gamma",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"ad",
                "traffic_source_name":"Переходы по рекламе",
                "source_detail":"Яндекс: Директ",
                "source_detail_id":"ad.Яндекс: Директ",
                "utm_source":"yandex",
                "utm_medium":"cpc",
                "utm_campaign":"future_service_gamma",
                "direct_campaign_id":"other",
                "direct_campaign_name":"Другие кампании",
                "visits":7,
                "is_test_tag":False,
            },
            {
                "row_id":"az186-02-paid-direct-word",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"ad",
                "traffic_source_name":"Переходы по рекламе",
                "source_detail":"Future Direct Mail Network",
                "source_detail_id":"ad.Future Direct Mail Network",
                "utm_source":"future_direct_mail_network",
                "utm_medium":"cpc",
                "utm_campaign":"mail_launch",
                "visits":9,
                "is_test_tag":False,
            },
            {
                "row_id":"az186-02-organic-google-word",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"organic",
                "traffic_source_name":"Переходы из поисковых систем",
                "source_detail":"NotGoogleSearch",
                "source_detail_id":"organic.NotGoogleSearch",
                "visits":4,
                "is_test_tag":False,
            },
            {
                "row_id":"az186-02-referral-2gis-word",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"referral",
                "traffic_source_name":"Переходы по ссылкам на сайтах",
                "utm_source":"future_2gis_partner",
                "utm_campaign":"partner_launch",
                "visits":3,
                "is_test_tag":False,
            },
            {
                "row_id":"az186-03-dot",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"referral",
                "traffic_source_name":"Переходы по ссылкам на сайтах",
                "utm_source":"future.network",
                "utm_campaign":"dot_campaign",
                "visits":5,
                "is_test_tag":False,
            },
            {
                "row_id":"az186-03-dash",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"referral",
                "traffic_source_name":"Переходы по ссылкам на сайтах",
                "utm_source":"future-network",
                "utm_campaign":"dash_campaign",
                "visits":6,
                "is_test_tag":False,
            },
        ],
        "metrica_goal_day":[
            {
                "traffic_row_id":"az186-02-paid-direct-word",
                "date":day,
                "goal_name":"Открытие окна записи",
                "goal_visits":4,
                "reaches":4,
            },
            {
                "traffic_row_id":"az186-03-dot",
                "date":day,
                "goal_name":"Открытие окна записи",
                "goal_visits":2,
                "reaches":2,
            },
            {
                "traffic_row_id":"az186-03-dash",
                "date":day,
                "goal_name":"Открытие окна записи",
                "goal_visits":3,
                "reaches":3,
            },
        ],
        "goal_registry":[],
        "direct_campaign_day":None,
        "daily_summary":{day:{"visits":34,"test_tag_visits":0}},
    }
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("az_analytics.json",json.dumps(payload,ensure_ascii=False))
    return raw.getvalue()


def az186_long_source_cost_zip(raw_source):
    day="2026-11-07"
    payload={
        "metadata":{
            "schema_version":"az_analytics_export_v1",
            "fetched_at_utc":"2026-11-08T03:00:00+00:00",
            "period_from":day,
            "period_to":day,
            "day_status":"completed_calendar_day_revisable",
            "source_timezone":"Asia/Novosibirsk",
            "counter_id":"113776005",
            "sample_share":1.0,
            "attribution":"cross_device_last_significant",
            "campaign_identity_source":"traffic_direct_click_order",
        },
        "metrica_traffic_day":[
            {
                "row_id":"az186-04-long-source",
                "date":day,
                "day_status":"completed_calendar_day_revisable",
                "traffic_source_id":"ad",
                "traffic_source_name":"Переходы по рекламе",
                "source_detail":"Future Ads Network",
                "utm_source":raw_source,
                "utm_medium":"cpc",
                "utm_campaign":"long_source_launch",
                "visits":3,
                "is_test_tag":False,
            },
        ],
        "metrica_goal_day":[],
        "goal_registry":[],
        "direct_campaign_day":None,
        "daily_summary":{day:{"visits":3,"test_tag_visits":0}},
    }
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("az_analytics.json",json.dumps(payload,ensure_ascii=False))
    return raw.getvalue()


def campaign_evidence_zip(
    day,
    utm_campaign,
    campaign_id,
    campaign_name,
    visits=3,
):
    payload={
        "metadata":{
            "schema_version":"az_analytics_export_v1",
            "fetched_at_utc":day+"T03:00:00+00:00",
            "period_from":day,
            "period_to":day,
            "day_status":"completed_calendar_day_revisable",
            "source_timezone":"Asia/Novosibirsk",
            "counter_id":"113776005",
            "sample_share":1.0,
            "attribution":"cross_device_last_significant",
            "campaign_identity_source":"traffic_direct_click_order",
        },
        "metrica_traffic_day":[{
            "row_id":"evidence-"+day+"-"+str(campaign_id or "fallback"),
            "date":day,
            "day_status":"completed_calendar_day_revisable",
            "traffic_source_id":"ad",
            "traffic_source_name":"Переходы по рекламе",
            "source_detail":"Яндекс: Директ",
            "source_detail_id":"ad.Яндекс: Директ",
            "utm_source":"yandex",
            "utm_medium":"cpc",
            "utm_campaign":utm_campaign,
            "direct_campaign_id":campaign_id,
            "direct_campaign_name":campaign_name,
            "visits":visits,
            "is_test_tag":False,
        }],
        "metrica_goal_day":[],
        "goal_registry":[],
        "direct_campaign_day":None,
        "daily_summary":{day:{"visits":visits,"test_tag_visits":0}},
    }
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("az_analytics.json",json.dumps(payload,ensure_ascii=False))
    return raw.getvalue()


def auto_metrika_request(path, params):
    if path == "/management/v1/counter/113776005":
        return {
            "counter":{
                "id":113776005,
                "site":"azmednsk.ru",
                "status":"Active",
                "time_zone_name":"Asia/Novosibirsk",
                "permission":"own",
            }
        }
    if path == "/management/v1/counter/113776005/goals":
        return {
            "goals":[
                {"id":674318079,"name":"Открытие окна записи","status":"Active"},
                {"id":674318216,"name":"Переход к записи в Medflex","status":"Active"},
                {"id":674318251,"name":"Нажатие на телефон","status":"Active"},
                {"id":674371065,"name":"Автоцель: клик по номеру телефона","status":"Active"},
                {"id":674572012,"name":"Автоцель: переход в мессенджер","status":"Active"},
                {"id":675346316,"name":"Автоцель: переход в соц.сеть","status":"Active"},
                {"id":675772127,"name":"Автоцель: скачивание файла","status":"Active"},
            ]
        }
    for counter_id in ma.MEDFLEX_COUNTERS:
        if path == f"/management/v1/counter/{counter_id}":
            return {
                "counter":{
                    "id":int(counter_id),
                    "site":"medflex.example",
                    "status":"Active",
                    "time_zone_name":"Asia/Novosibirsk",
                    "permission":"view",
                }
            }
        if path == f"/management/v1/counter/{counter_id}/goals":
            return {
                "goals":[
                    {"id":int(counter_id)+1,"name":"Medflex test goal","status":"Active"}
                ]
            }
    if path != "/stat/v1/data":
        raise AssertionError(path)

    dimensions=str(params.get("dimensions") or "").split(",")
    metrics=str(params.get("metrics") or "").split(",")
    counter_id=str(params.get("ids") or "")

    def metric_values(visits, booking=(0,0), medflex=(0,0)):
        values=[]
        for metric in metrics:
            if metric in {"ym:s:visits","ym:s:users","ym:s:pageviews","ym:pv:pageviews","ym:pv:users"}:
                values.append(float(visits))
            elif metric=="ym:s:bounceRate":
                values.append(0.0)
            elif metric=="ym:s:avgVisitDurationSeconds":
                values.append(30.0)
            elif metric=="ym:s:goal674318079reaches":
                values.append(float(booking[0]))
            elif metric=="ym:s:goal674318079visits":
                values.append(float(booking[1]))
            elif metric=="ym:s:goal674318216reaches":
                values.append(float(medflex[0]))
            elif metric=="ym:s:goal674318216visits":
                values.append(float(medflex[1]))
            elif metric.startswith("ym:s:goal") and (
                metric.endswith("reaches") or metric.endswith("visits")
            ):
                values.append(0.0)
            else:
                raise AssertionError(metric)
        return values

    if counter_id in ma.MEDFLEX_COUNTERS:
        return {
            "query":{
                "ids":[int(counter_id)],
                "dimensions":dimensions,
                "metrics":metrics,
                "date1":params["date1"],
                "date2":params["date2"],
            },
            "data":[],
            "total_rows":0,
            "total_rows_rounded":False,
            "sampled":False,
            "sample_share":1.0,
            "totals":[0.0 for _ in metrics],
        }

    if tuple(dimensions)==ma.TRAFFIC_DIMENSIONS:
        day=str(params["date1"])
        def dims(
            source_id,
            source_name,
            detail_id=None,
            detail=None,
            utm_source=None,
            utm_medium=None,
            utm_campaign=None,
            utm_content=None,
            utm_term=None,
            campaign_id=None,
            campaign_name=None,
        ):
            return [
                {"name":day},
                {"id":source_id,"name":source_name},
                {"id":detail_id,"name":detail},
                {"name":utm_source},
                {"name":utm_medium},
                {"name":utm_campaign},
                {"name":utm_content},
                {"name":utm_term},
                {"id":campaign_id,"name":campaign_name},
            ]
        data=[
            {
                "dimensions":dims(
                    "ad","Переходы по рекламе","ad.Яндекс: Директ","Яндекс: Директ",
                    campaign_id="715560815",
                    campaign_name="АЗ | Ортодонт и гнатолог | Поиск | Новосибирск",
                ),
                "metrics":metric_values(5,booking=(2,2),medflex=(1,1)),
            },
            {
                "dimensions":dims(
                    "ad","Переходы по рекламе","ad.Яндекс: Директ","Яндекс: Директ",
                    campaign_id="715444498",
                    campaign_name="АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",
                ),
                "metrics":metric_values(1),
            },
            {
                "dimensions":dims(
                    "ad","Переходы по рекламе","ad.Яндекс: Директ","Яндекс: Директ",
                    campaign_id="999999999",
                    campaign_name="Новая неизвестная кампания",
                ),
                "metrics":metric_values(2),
            },
            {
                "dimensions":dims(
                    "ad","Переходы по рекламе","ad.Яндекс: Директ","Яндекс: Директ"
                ),
                "metrics":metric_values(1),
            },
            {
                "dimensions":dims(
                    "social","Переходы из социальных сетей","social.instagram","instagram.com",
                    "instagram","social","starostenko","profile"
                ),
                "metrics":metric_values(3,booking=(1,1)),
            },
            {
                "dimensions":dims(
                    "messenger","Переходы из мессенджеров","messenger.telegram","Telegram",
                    "telegram","social","metrika_test"
                ),
                "metrics":metric_values(2),
            },
        ]
        totals=metric_values(14,booking=(3,3),medflex=(1,1))
    elif dimensions == ["ym:s:date"]:
        without_test="metrika_test" in str(params.get("filters") or "")
        visits=12 if without_test else 14
        data=[{
            "dimensions":[{"name":str(params["date1"])}],
            "metrics":metric_values(visits,booking=(3,3),medflex=(1,1)),
        }]
        totals=metric_values(visits,booking=(3,3),medflex=(1,1))
    else:
        data=[{
            "dimensions":[
                {"name":str(params["date1"]) if dimension.endswith(":date") else "Тест"}
                for dimension in dimensions
            ],
            "metrics":metric_values(1),
        }]
        totals=metric_values(1)

    return {
        "query":{
            "ids":[113776005],
            "dimensions":dimensions,
            "metrics":metrics,
            "date1":params["date1"],
            "date2":params["date2"],
        },
        "data":data,
        "total_rows":len(data),
        "total_rows_rounded":False,
        "sampled":False,
        "sample_share":1.0,
        "totals":totals,
    }


def provisional_metrika_zip(day="2026-10-31", visits=5, status="provisional_current_day"):
    payload={
        "metadata":{
            "schema_version":"az_analytics_export_v1",
            "fetched_at_utc":"2026-10-31T08:00:00+00:00",
            "period_from":day,
            "period_to":day,
            "day_status":status,
            "source_timezone":"Asia/Novosibirsk",
            "counter_id":"113776005",
            "sample_share":1.0,
            "attribution":"cross_device_last_significant",
        },
        "metrica_traffic_day":[
            {
                "row_id":"live1",
                "date":day,
                "day_status":status,
                "traffic_source_id":"direct",
                "traffic_source_name":"Прямые заходы",
                "visits":visits,
                "is_test_tag":False,
            }
        ],
        "metrica_goal_day":[],
        "goal_registry":[],
        "direct_campaign_day":None,
        "daily_summary":{},
    }
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("az_analytics.json",json.dumps(payload,ensure_ascii=False))
    return raw.getvalue()


def empty_metrika_zip(day="2026-10-31", status="provisional_current_day"):
    payload={
        "metadata":{
            "schema_version":"az_analytics_export_v1",
            "fetched_at_utc":"2026-10-31T08:00:00+00:00",
            "period_from":day,
            "period_to":day,
            "day_status":status,
            "source_timezone":"Asia/Novosibirsk",
            "counter_id":"113776005",
            "sample_share":1.0,
            "attribution":"cross_device_last_significant",
        },
        "metrica_traffic_day":[],
        "metrica_goal_day":[],
        "goal_registry":[],
        "direct_campaign_day":[],
        "daily_summary":{day:{"visits":0,"test_tag_visits":0}},
    }
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("az_analytics.json",json.dumps(payload,ensure_ascii=False))
    return raw.getvalue()


def zero_auto_metrika_request(path, params):
    if path in {
        "/management/v1/counter/113776005",
        "/management/v1/counter/113776005/goals",
    }:
        return auto_metrika_request(path,params)
    if path != "/stat/v1/data":
        raise AssertionError(path)
    metrics=str(params.get("metrics") or "").split(",")
    dimensions=str(params.get("dimensions") or "").split(",")
    return {
        "query":{"ids":[113776005],"dimensions":dimensions,"metrics":metrics,"date1":params["date1"],"date2":params["date2"]},
        "data":[],
        "total_rows":0,
        "total_rows_rounded":False,
        "sampled":False,
        "sample_share":1.0,
        "totals":[0.0 for _ in metrics],
    }


def metrika_geoadv_search_yabs_zip():
    payload = {
        "metadata": {
            "schema_version": "az_analytics_export_v1",
            "fetched_at_utc": "2026-11-09T23:10:00+00:00",
            "period_from": "2026-11-09",
            "period_to": "2026-11-09",
            "source_timezone": "Asia/Novosibirsk",
            "counter_id": "113776005",
            "sample_share": 1.0,
            "attribution": "cross_device_last_significant",
            "campaign_identity_source": "traffic_direct_click_order",
        },
        "metrica_traffic_day": [
            {
                "row_id": "yb11",
                "date": "2026-11-09",
                "day_status": "completed_calendar_day_final",
                "traffic_source_id": "ad",
                "traffic_source_name": "Переходы по рекламе",
                "source_detail": "Яндекс Директ: Не определено",
                "source_detail_id": "ad.Яндекс Директ: Не определено",
                "utm_source": "geoadv_search_yabs",
                "direct_campaign_id": "44320327528",
                "direct_campaign_name": "Чужие кампании",
                "visits": 11,
                "is_test_tag": False,
            },
            {
                "row_id": "yd5",
                "date": "2026-11-09",
                "day_status": "completed_calendar_day_final",
                "traffic_source_id": "ad",
                "traffic_source_name": "Переходы по рекламе",
                "source_detail": "Яндекс: Директ",
                "source_detail_id": "ad.Яндекс: Директ",
                "utm_source": "yandex",
                "direct_campaign_id": "715560815",
                "direct_campaign_name": "АЗ | Ортодонт и гнатолог | Поиск | Новосибирск",
                "visits": 5,
                "is_test_tag": False,
            },
            {
                "row_id": "other3",
                "date": "2026-11-09",
                "day_status": "completed_calendar_day_final",
                "traffic_source_id": "ad",
                "traffic_source_name": "Переходы по рекламе",
                "utm_source": "future_paid_network",
                "visits": 3,
                "is_test_tag": False,
            },
        ],
        "metrica_goal_day": [
            {
                "traffic_row_id": "yb11",
                "date": "2026-11-09",
                "goal_name": "Открытие окна записи",
                "goal_visits": 2,
                "reaches": 3,
            },
            {
                "traffic_row_id": "yb11",
                "date": "2026-11-09",
                "goal_name": "Переход к записи в Medflex",
                "goal_visits": 1,
                "reaches": 1,
            },
            {
                "traffic_row_id": "yd5",
                "date": "2026-11-09",
                "goal_name": "Открытие окна записи",
                "goal_visits": 1,
                "reaches": 1,
            },
        ],
    }
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "az_analytics.json",
            json.dumps(payload, ensure_ascii=False),
        )
    return raw.getvalue()


class AdvertisingReportTests(unittest.TestCase):
    def test_auto_metrika_missing_days_starts_after_latest_and_stops_at_yesterday(self):
        c=conn()
        try:
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-10-30","direct","Прямые заходы","Прямые заходы","","",None,None,1,"t","m"),
            )
            c.commit()
            days=ma.missing_completed_days(
                c,
                datetime(2026,11,2,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
            )
            self.assertEqual(
                [day.isoformat() for day in days],
                ["2026-10-27","2026-10-28","2026-10-29","2026-10-31","2026-11-01"],
            )
        finally:
            c.close()

    def test_auto_metrika_no_missing_days_when_yesterday_already_loaded(self):
        c=conn()
        try:
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-10-30","direct","Прямые заходы","Прямые заходы","","",None,None,1,"t","m"),
            )
            c.commit()
            days=ma.missing_completed_days(
                c,
                datetime(2026,10,31,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
            )
            self.assertEqual(
                [day.isoformat() for day in days],
                ["2026-10-27","2026-10-28","2026-10-29"],
            )
        finally:
            c.close()


    def test_auto_metrika_core_snapshot_uses_only_two_report_requests(self):
        calls=[]
        def request_json(path,params):
            calls.append((path,dict(params)))
            return auto_metrika_request(path,params)

        client=ma.MetrikaClient("secret-not-logged",request_json=request_json)
        raw,filename=client.fetch_day_archive(
            date(2026,10,31),
            completed=False,
            diagnostics=False,
        )
        stat_calls=[item for item in calls if item[0]=="/stat/v1/data"]
        self.assertEqual(len(stat_calls),2)
        self.assertEqual(
            stat_calls[0][1]["dimensions"],
            ",".join(ma.TRAFFIC_DIMENSIONS),
        )
        self.assertEqual(stat_calls[1][1]["dimensions"],"ym:s:date")
        self.assertEqual(stat_calls[0][1]["accuracy"],"full")
        self.assertEqual(stat_calls[1][1]["accuracy"],"full")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"az_analytics.json","REPORT-2026-10-31.txt"},
            )
        parsed=m._parse_metrika_zip(raw,filename,allow_provisional=True)
        self.assertEqual(parsed["completed_days"],[])
        self.assertEqual(parsed["provisional_days"],["2026-10-31"])

    def test_auto_metrika_api_archive_uses_existing_parser_and_campaign_split(self):
        client=ma.MetrikaClient("secret-not-logged",request_json=auto_metrika_request)
        raw,filename=client.fetch_day_archive(date(2026,10,31))
        self.assertEqual(filename,"AZ-BAZE-auto-metrika-2026-10-31.zip")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names=set(archive.namelist())
            self.assertEqual(len(names),39)
            self.assertIn("az_analytics.json",names)
            self.assertIn("REPORT-2026-10-31.txt",names)
            self.assertIn("metrica_api_raw.json",names)
            self.assertIn("metrica_traffic_day.csv",names)
            self.assertIn("metrica_goal_day.csv",names)
            self.assertIn("goal_registry.csv",names)
            self.assertIn("medflex_daily_raw.json",names)
            self.assertNotIn("campaigns_raw.json",names)
            az_payload=json.loads(archive.read("az_analytics.json").decode("utf-8"))
            self.assertIsNone(az_payload["direct_campaign_day"])
            self.assertEqual(
                az_payload["metadata"]["campaign_identity_source"],
                "traffic_direct_click_order",
            )
            traffic=az_payload["metrica_traffic_day"]
            native={
                str(row.get("direct_campaign_id") or ""):row
                for row in traffic
                if str(row.get("direct_campaign_id") or "")
            }
            self.assertEqual(native["715560815"]["visits"],5)
            self.assertEqual(native["715444498"]["visits"],1)
            self.assertEqual(native["999999999"]["direct_campaign_name"],"Новая неизвестная кампания")
            self.assertEqual(native["999999999"]["visits"],2)
            medflex=json.loads(archive.read("medflex_daily_raw.json").decode("utf-8"))
            self.assertEqual(
                set(medflex["counters"]),
                set(ma.MEDFLEX_COUNTERS),
            )
            for counter_id in ma.MEDFLEX_COUNTERS:
                self.assertIn("goals_raw",medflex["counters"][counter_id])
            hashes=json.loads(archive.read("SHA256.json").decode("utf-8"))
            self.assertEqual(len(hashes),38)
            for name,expected in hashes.items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(),expected)
            self.assertIsNone(archive.testzip())

        parsed=m._parse_metrika_zip(
            raw,
            filename,
            now=datetime(2026,11,1,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )
        self.assertEqual(parsed["completed_days"],["2026-10-31"])
        self.assertEqual(
            sum(values["visits"] for values in parsed["traffic_rows"].values()),
            12,
        )
        yandex={
            (key[4],key[5]):values["visits"]
            for key,values in parsed["traffic_rows"].items()
            if key[1]=="yandex_ads"
        }
        self.assertEqual(
            yandex,
            {
                ("715560815","АЗ | Ортодонт и гнатолог | Поиск | Новосибирск"):5,
                ("715444498","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск"):1,
                ("999999999","Новая неизвестная кампания"):2,
                ("",""):1,
            },
        )
        instagram={
            key[5]:values["visits"]
            for key,values in parsed["traffic_rows"].items()
            if key[1]=="instagram"
        }
        self.assertEqual(instagram,{"Старостенко":3})


    def test_auto_metrika_sync_plan_refreshes_last_three_completed_and_current_day(self):
        c=conn()
        try:
            for day in ("2026-10-27","2026-10-28","2026-10-29","2026-10-30"):
                c.execute(
                    "INSERT INTO advertising_metrika_imports VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "sha"+day,day+".zip",day,day,json.dumps([day]),"t","t",1,1,1,"/tmp/"+day
                    ),
                )
            c.commit()
            now=datetime(2026,10,31,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk"))
            self.assertEqual(
                [d.isoformat() for d in ma.completed_sync_days(c,now)],
                ["2026-10-28","2026-10-29","2026-10-30"],
            )
            self.assertEqual(ma.current_local_day(now).isoformat(),"2026-10-31")
        finally:
            c.close()

    def test_provisional_current_day_replaces_snapshot_without_import_history(self):
        c=conn()
        try:
            first=m._parse_metrika_zip(
                provisional_metrika_zip(visits=5),
                "live.zip",
                allow_provisional=True,
            )
            self.assertEqual(first["completed_days"],[])
            self.assertEqual(first["provisional_days"],["2026-10-31"])

            r1=m._store_metrika_provisional(c,first,1); c.commit()
            self.assertTrue(r1["updated"])
            self.assertFalse(r1["reused"])
            self.assertEqual(
                c.execute(
                    "SELECT SUM(visits) FROM advertising_traffic_daily "
                    "WHERE provider='metrika' AND metric_date='2026-10-31'"
                ).fetchone()[0],
                5,
            )
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM advertising_metrika_imports").fetchone()[0],
                0,
            )

            before=c.total_changes
            same=m._store_metrika_provisional(c,first,1); c.commit()
            self.assertTrue(same["reused"])
            self.assertFalse(same["updated"])
            self.assertEqual(c.total_changes,before)

            second=m._parse_metrika_zip(
                provisional_metrika_zip(visits=8),
                "live2.zip",
                allow_provisional=True,
            )
            r2=m._store_metrika_provisional(c,second,1); c.commit()
            self.assertTrue(r2["updated"])
            self.assertEqual(
                c.execute(
                    "SELECT SUM(visits) FROM advertising_traffic_daily "
                    "WHERE provider='metrika' AND metric_date='2026-10-31'"
                ).fetchone()[0],
                8,
            )
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM advertising_traffic_daily "
                    "WHERE provider='metrika' AND metric_date='2026-10-31'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM advertising_metrika_imports").fetchone()[0],
                0,
            )
        finally:
            c.close()

    def test_completed_day_replaces_provisional_and_blocks_later_provisional_overwrite(self):
        c=conn()
        with tempfile.TemporaryDirectory() as tmp:
            old=m.METRIKA_ARCHIVE_ROOT
            m.METRIKA_ARCHIVE_ROOT=Path(tmp)
            try:
                live=m._parse_metrika_zip(
                    provisional_metrika_zip(visits=5),
                    "live.zip",
                    allow_provisional=True,
                )
                m._store_metrika_provisional(c,live,1); c.commit()

                completed=m._parse_metrika_zip(
                    provisional_metrika_zip(
                        visits=7,
                        status="completed_calendar_day_revisable",
                    ),
                    "completed.zip",
                    now=datetime(2026,11,1,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
                )
                m._store_metrika(c,completed,1); c.commit()
                self.assertEqual(
                    c.execute(
                        "SELECT SUM(visits) FROM advertising_traffic_daily "
                        "WHERE provider='metrika' AND metric_date='2026-10-31'"
                    ).fetchone()[0],
                    7,
                )
                self.assertEqual(
                    c.execute("SELECT COUNT(*) FROM advertising_metrika_imports").fetchone()[0],
                    1,
                )
                with self.assertRaisesRegex(ValueError,"уже сохранён как завершённый"):
                    m._store_metrika_provisional(c,live,1)
            finally:
                c.close(); m.METRIKA_ARCHIVE_ROOT=old

    def test_metrika_sync_lock_rejects_parallel_second_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"sync.lock"
            with ma.MetrikaSyncLock(path):
                with self.assertRaisesRegex(ma.MetrikaBusyError,"уже выполняется"):
                    with ma.MetrikaSyncLock(path):
                        pass

    def test_auto_archive_can_mark_current_day_provisional(self):
        client=ma.MetrikaClient("secret-not-logged",request_json=auto_metrika_request)
        raw,filename=client.fetch_day_archive(date(2026,10,31),completed=False)
        parsed=m._parse_metrika_zip(raw,filename,allow_provisional=True)
        self.assertEqual(parsed["completed_days"],[])
        self.assertEqual(parsed["provisional_days"],["2026-10-31"])
        with self.assertRaisesRegex(ValueError,"нет завершённых"):
            m._parse_metrika_zip(raw,filename)


    def test_identical_provisional_must_still_finalize_as_completed(self):
        c=conn()
        with tempfile.TemporaryDirectory() as tmp:
            old=m.METRIKA_ARCHIVE_ROOT
            m.METRIKA_ARCHIVE_ROOT=Path(tmp)
            try:
                live=m._parse_metrika_zip(
                    provisional_metrika_zip(visits=5),
                    "live.zip",
                    allow_provisional=True,
                )
                m._store_metrika_provisional(c,live,1); c.commit()

                completed=m._parse_metrika_zip(
                    provisional_metrika_zip(
                        visits=5,
                        status="completed_calendar_day_revisable",
                    ),
                    "completed-identical.zip",
                    now=datetime(2026,11,1,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
                )
                self.assertTrue(m._stored_metrika_matches(c,completed))
                self.assertFalse(m._completed_metrika_matches_registered(c,completed))

                result=m._store_metrika(c,completed,1); c.commit()
                self.assertFalse(result["reused"])
                self.assertFalse(result["reclassified"])
                self.assertTrue(m._completed_metrika_matches_registered(c,completed))
                self.assertEqual(
                    c.execute("SELECT COUNT(*) FROM advertising_metrika_imports").fetchone()[0],
                    1,
                )
                import_ids={
                    str(row[0])
                    for row in c.execute(
                        "SELECT DISTINCT import_id FROM advertising_traffic_daily "
                        "WHERE provider='metrika' AND metric_date='2026-10-31'"
                    ).fetchall()
                }
                self.assertEqual(len(import_ids),1)
                self.assertNotIn("metrika-live-2026-10-31",import_ids)
            finally:
                c.close(); m.METRIKA_ARCHIVE_ROOT=old

    def test_zero_visit_current_and_completed_days_are_parseable_and_finalizable(self):
        current=m._parse_metrika_zip(
            empty_metrika_zip(),
            "zero-current.zip",
            allow_provisional=True,
        )
        self.assertEqual(current["completed_days"],[])
        self.assertEqual(current["provisional_days"],["2026-10-31"])
        self.assertEqual(current["traffic_rows"],{})
        self.assertEqual(current["goal_rows"],{})

        completed=m._parse_metrika_zip(
            empty_metrika_zip(status="completed_calendar_day_revisable"),
            "zero-completed.zip",
            now=datetime(2026,11,1,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )
        self.assertEqual(completed["completed_days"],["2026-10-31"])
        self.assertEqual(completed["traffic_rows"],{})

        with tempfile.TemporaryDirectory() as tmp:
            old=m.METRIKA_ARCHIVE_ROOT
            m.METRIKA_ARCHIVE_ROOT=Path(tmp)
            c=conn()
            try:
                result=m._store_metrika(c,completed,1); c.commit()
                self.assertFalse(result["reused"])
                self.assertEqual(
                    c.execute("SELECT COUNT(*) FROM advertising_metrika_imports").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    c.execute(
                        "SELECT COUNT(*) FROM advertising_traffic_daily "
                        "WHERE provider='metrika' AND metric_date='2026-10-31'"
                    ).fetchone()[0],
                    0,
                )
            finally:
                c.close(); m.METRIKA_ARCHIVE_ROOT=old

    def test_zero_visit_api_core_snapshot_roundtrips(self):
        client=ma.MetrikaClient("secret-not-logged",request_json=zero_auto_metrika_request)
        raw,filename=client.fetch_day_archive(
            date(2026,10,31),
            completed=False,
            diagnostics=False,
        )
        parsed=m._parse_metrika_zip(
            raw,
            filename,
            allow_provisional=True,
        )
        self.assertEqual(parsed["provisional_days"],["2026-10-31"])
        self.assertEqual(parsed["traffic_rows"],{})

    def test_manual_zip_cannot_mark_current_calendar_day_completed(self):
        raw=provisional_metrika_zip(
            visits=3,
            status="completed_calendar_day_revisable",
        )
        with self.assertRaisesRegex(ValueError,"Незавершённый календарный день"):
            m._parse_metrika_zip(
                raw,
                "mislabeled-today.zip",
                now=datetime(2026,10,31,21,16,tzinfo=ZoneInfo("Asia/Novosibirsk")),
            )

    def test_noop_sync_has_no_audit_write_contract(self):
        self.assertFalse(
            m._metrika_sync_has_changes(
                [],
                {"updated":False,"reused":True},
            )
        )
        self.assertTrue(
            m._metrika_sync_has_changes(
                ["2026-10-30"],
                {"updated":False,"reused":True},
            )
        )
        self.assertTrue(
            m._metrika_sync_has_changes(
                [],
                {"updated":True,"reused":False},
            )
        )
        source=Path(__file__).with_name("advertising_report.py").read_text(encoding="utf-8")
        # No-op audit preservation now spans the shared button/hourly core
        # and the unchanged CSRF-protected route's audit gate.
        self.assertIn(
            '"changed": _metrika_sync_has_changes(completed_updated, current_result)',
            source,
        )
        self.assertIn('or not direct_result["reused"]', source)
        self.assertIn('if result["changed"]:\n            audit(', source)

    def test_empty_history_scans_all_gaps_from_tracking_start(self):
        c=conn()
        try:
            days=ma.missing_completed_days(
                c,
                datetime(2026,11,1,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
            )
            self.assertEqual(
                [day.isoformat() for day in days],
                ["2026-10-27","2026-10-28","2026-10-29","2026-10-30","2026-10-31"],
            )
        finally:
            c.close()

    def test_auto_metrika_requires_server_token(self):
        with self.assertRaises(ma.MetrikaApiError):
            ma.MetrikaClient("")

    def test_old_provisional_is_still_missing_when_completed_history_is_empty(self):
        c=conn()
        try:
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "metrika","2026-10-27","direct","Прямые заходы","Прямые заходы",
                    "","",None,None,5,"t","metrika-live-2026-10-27",
                ),
            )
            c.commit()
            now=datetime(2026,11,10,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk"))
            missing=ma.missing_completed_days(c,now)
            targets=ma.completed_sync_days(c,now)
            self.assertIn(date(2026,10,27),missing)
            self.assertIn(date(2026,10,27),targets)
        finally:
            c.close()

    def test_legacy_completed_row_stays_completed_after_first_history_record_appears(self):
        c=conn()
        with tempfile.TemporaryDirectory() as tmp:
            old=m.METRIKA_ARCHIVE_ROOT
            m.METRIKA_ARCHIVE_ROOT=Path(tmp)
            try:
                c.execute(
                    "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "metrika","2026-10-28","direct","Прямые заходы","Прямые заходы",
                        "","",None,None,3,"t","metrika-aabbccddeeff0011",
                    ),
                )
                provisional=m._parse_metrika_zip(
                    provisional_metrika_zip(day="2026-10-27",visits=5),
                    "live-27.zip",
                    allow_provisional=True,
                )
                m._store_metrika_provisional(c,provisional,1)
                c.commit()

                now=datetime(2026,11,10,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk"))
                before=ma.missing_completed_days(c,now)
                self.assertIn(date(2026,10,27),before)
                self.assertNotIn(date(2026,10,28),before)

                completed=m._parse_metrika_zip(
                    provisional_metrika_zip(
                        day="2026-10-27",
                        visits=5,
                        status="completed_calendar_day_revisable",
                    ),
                    "completed-27.zip",
                    now=datetime(2026,10,28,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
                )
                m._store_metrika(c,completed,1)
                c.commit()

                after=ma.missing_completed_days(c,now)
                targets=ma.completed_sync_days(c,now)
                self.assertNotIn(date(2026,10,27),after)
                self.assertNotIn(date(2026,10,28),after)
                self.assertNotIn(date(2026,10,28),targets)

                legacy=c.execute(
                    "SELECT import_id,visits FROM advertising_traffic_daily "
                    "WHERE provider='metrika' AND metric_date='2026-10-28'"
                ).fetchone()
                self.assertEqual(legacy["import_id"],"metrika-aabbccddeeff0011")
                self.assertEqual(legacy["visits"],3)
            finally:
                c.close(); m.METRIKA_ARCHIVE_ROOT=old

    def test_auto_metrika_finds_gap_before_earliest_import_from_tracking_start(self):
        c=conn()
        try:
            for day in ("2026-10-28","2026-10-29","2026-10-30"):
                c.execute(
                    "INSERT INTO advertising_metrika_imports VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("sha"+day,day+".zip",day,day,json.dumps([day]),"t","t",1,1,1,"/tmp/"+day),
                )
            c.commit()
            days=ma.missing_completed_days(
                c,
                datetime(2026,10,31,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
            )
            self.assertEqual([day.isoformat() for day in days],["2026-10-27"])
        finally:
            c.close()

    def test_auto_metrika_finds_gap_inside_existing_history(self):
        c=conn()
        try:
            c.executemany(
                "INSERT INTO advertising_metrika_imports VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [
                    ("sha27","a.zip","2026-10-27","2026-10-27",json.dumps(["2026-10-27"]),"t","t",1,1,1,"/tmp/a"),
                    ("sha29","b.zip","2026-10-29","2026-10-29",json.dumps(["2026-10-29"]),"t","t",1,1,1,"/tmp/b"),
                ],
            )
            c.commit()
            days=ma.missing_completed_days(
                c,
                datetime(2026,10,31,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
            )
            self.assertEqual(
                [day.isoformat() for day in days],
                ["2026-10-28","2026-10-30"],
            )
        finally:
            c.close()

    def test_auto_metrika_requires_all_approved_goals(self):
        def missing_goal(path,params):
            if path.endswith("/goals"):
                return {"goals":[{"id":674318079,"name":"Открытие окна записи","status":"Active"}]}
            raise AssertionError(path)
        client=ma.MetrikaClient("server-secret",request_json=missing_goal)
        with self.assertRaisesRegex(ma.MetrikaApiError,"отсутствуют используемые цели"):
            client.goals()

    def test_auto_metrika_reporting_query_has_attribution_and_full_accuracy_guard(self):
        calls=[]
        def request_json(path,params):
            calls.append((path,dict(params)))
            return {
                "query":dict(params),
                "data":[],
                "total_rows":0,
                "total_rows_rounded":False,
                "sampled":False,
                "sample_share":1.0,
                "totals":[0.0],
            }
        client=ma.MetrikaClient("server-secret",request_json=request_json)
        client.table(
            day=date(2026,10,31),
            dimensions=("ym:s:date",),
            metrics=("ym:s:visits",),
        )
        self.assertEqual(calls[0][1]["attribution"],ma.ATTRIBUTION)
        self.assertEqual(calls[0][1]["filters"],ma.ROBOT_FILTER)
        self.assertEqual(calls[0][1]["accuracy"],"full")
        self.assertEqual(calls[0][1]["limit"],100000)
        self.assertIn(ma.CAMPAIGN_DIMENSION,ma.TRAFFIC_DIMENSIONS)

        def sampled(path,params):
            return {
                "data":[],
                "total_rows":0,
                "total_rows_rounded":False,
                "sampled":True,
                "sample_share":0.5,
            }
        with self.assertRaisesRegex(ma.MetrikaApiError,"семплированные"):
            ma.MetrikaClient("server-secret",request_json=sampled).table(
                day=date(2026,10,31),
                dimensions=("ym:s:date",),
                metrics=("ym:s:visits",),
            )

    def test_metrika_routes_require_upload_services_permission(self):
        source=Path(__file__).with_name("advertising_report.py").read_text(encoding="utf-8")
        self.assertIn(
            '@app.post("/api/reports/advertising/metrika/sync")\n'
            '    @permission_required("upload_services")',
            source,
        )
        self.assertIn(
            '@app.post("/api/reports/advertising/metrika/import")\n'
            '    @permission_required("upload_services")',
            source,
        )
        self.assertNotIn("AZBAZE_YANDEX_METRIKA_TOKEN=",source)
        template=(Path(__file__).with_name("templates")/"uploads.html").read_text(encoding="utf-8")
        self.assertNotIn("AZBAZE_YANDEX_METRIKA_TOKEN",template)


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
            self.assertEqual((s["spend"],s["clicks"],s["visits"]),(None,None,80))
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
        self.assertEqual(by_name["Instagram"], 8)
        self.assertNotIn("Instagram — Старостенко", by_name)
        self.assertNotIn("Instagram — ссылка в bio, профиль не определён", by_name)
        self.assertNotIn("Яндекс-реклама", by_name)

        goals = {}
        for key, values in parsed["goal_rows"].items():
            source_name, goal_key = key[2], key[6]
            goals[(source_name, goal_key)] = goals.get((source_name, goal_key), 0) + values["goal_visits"]
        self.assertEqual(goals[("Instagram", "booking_open")], 2)
        self.assertEqual(goals[("Instagram", "medflex")], 2)
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
                self.assertEqual(sources["Instagram"]["visits"], 8)
                self.assertEqual(sources["Instagram"]["booking_open_visits"], 2)
                self.assertEqual(sources["Instagram"]["medflex_visits"], 2)

                campaigns = {
                    row["campaign_name"]: row
                    for row in m._group_breakdown(c, date(2026,10,27), date(2026,10,27), "campaign")
                    if row["source_key"] == "instagram"
                }
                self.assertEqual(campaigns["Старостенко"]["visits"], 6)
                self.assertEqual(campaigns["Старостенко"]["booking_open_visits"], 2)
                self.assertEqual(campaigns["Старостенко"]["medflex_visits"], 2)
                self.assertEqual(campaigns["link_in_bio"]["visits"], 2)

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
                    "UPDATE advertising_metrika_goal_daily SET source_key='instagram_other',source_name='Instagram — профиль не определён' "
                    "WHERE provider='metrika' AND source_key='instagram'"
                )
                c.execute(
                    "UPDATE advertising_traffic_daily SET source_key='instagram_other',source_name='Instagram — профиль не определён' "
                    "WHERE provider='metrika' AND source_key='instagram'"
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
                self.assertEqual(values["Instagram"], 8)
                self.assertNotIn("Instagram — профиль не определён", values)
                self.assertNotIn("Яндекс-реклама", values)
            finally:
                c.close(); m.METRIKA_ARCHIVE_ROOT = old



    def test_campaign_report_splits_yandex_and_instagram_by_real_labels(self):
        parsed = m._parse_metrika_zip(metrika_campaign_breakdown_zip(), "campaigns.zip")

        yandex = {
            key[5]: values["visits"]
            for key, values in parsed["traffic_rows"].items()
            if key[1] == "yandex_ads"
        }
        self.assertEqual(yandex["АЗ | Ортодонт и гнатолог | Поиск | Новосибирск"], 34)
        self.assertEqual(yandex["АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск"], 1)
        self.assertEqual(yandex[""], 2)
        self.assertEqual(sum(yandex.values()), 37)

        business_visits = sum(
            values["visits"]
            for key, values in parsed["traffic_rows"].items()
            if key[1] == "yandex_business"
        )
        self.assertEqual(business_visits, 25)

        instagram = {
            key[5]: values["visits"]
            for key, values in parsed["traffic_rows"].items()
            if key[1] == "instagram"
        }
        self.assertEqual(instagram["Старостенко"], 6)
        self.assertEqual(instagram["link_in_bio"], 2)
        self.assertEqual(instagram["Зубачев"], 3)
        self.assertEqual(instagram["Клиника"], 4)
        self.assertEqual(instagram["story_autumn"], 2)
        self.assertEqual(instagram[""], 1)

        with tempfile.TemporaryDirectory() as tmp:
            old = m.METRIKA_ARCHIVE_ROOT
            m.METRIKA_ARCHIVE_ROOT = Path(tmp)
            c = conn()
            try:
                m._store_metrika(c, parsed, 1); c.commit()
                sources = {
                    row["source_key"]: row
                    for row in m._group_breakdown(c, date(2026,10,27), date(2026,10,27), "source")
                    if row["visits"] is not None
                }
                self.assertEqual(sources["yandex_ads"]["visits"], 37)
                self.assertEqual(sources["yandex_business"]["visits"], 25)
                self.assertEqual(sources["instagram"]["visits"], 18)

                campaigns = m._group_breakdown(c, date(2026,10,27), date(2026,10,27), "campaign")
                yandex_campaigns = {
                    row["campaign_name"]: row for row in campaigns if row["source_key"] == "yandex_ads"
                }
                instagram_campaigns = {
                    row["campaign_name"]: row for row in campaigns if row["source_key"] == "instagram"
                }
                self.assertEqual(yandex_campaigns["АЗ | Ортодонт и гнатолог | Поиск | Новосибирск"]["booking_open_visits"], 2)
                self.assertEqual(yandex_campaigns["АЗ | Ортодонт и гнатолог | Поиск | Новосибирск"]["medflex_visits"], 1)
                self.assertEqual(yandex_campaigns["АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск"]["booking_open_visits"], 0)
                self.assertEqual(yandex_campaigns["АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск"]["medflex_visits"], 0)
                self.assertEqual(yandex_campaigns["Не определено"]["visits"], 2)
                self.assertEqual(yandex_campaigns["Не определено"]["booking_open_visits"], 0)
                self.assertEqual(yandex_campaigns["Не определено"]["medflex_visits"], 0)
                self.assertEqual(instagram_campaigns["Зубачев"]["booking_open_visits"], 1)
                self.assertEqual(instagram_campaigns["Не определено"]["visits"], 1)
            finally:
                c.close(); m.METRIKA_ARCHIVE_ROOT = old


    def test_cross_day_breakdown_canonicalizes_legacy_yandex_and_instagram_without_double_count(self):
        c=conn()
        try:
            traffic_rows = [
                # 28-29 legacy Yandex: same factual Bruksizm campaign stored under native ID and UTM-only alias.
                ("2026-10-28","yandex_ads","Яндекс-реклама","Платная реклама","715444498","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",5),
                ("2026-10-28","yandex_ads","Яндекс-реклама","Платная реклама","","bruksizm_search_nsk",1),
                ("2026-10-29","yandex_ads","Яндекс-реклама","Платная реклама","715444498","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",2),
                ("2026-10-29","yandex_ads","Яндекс-реклама","Платная реклама","","bruksizm_search_nsk",2),
                # 30.10 PR178 representation: same campaign by full name, plus Orthodontics and factual residual.
                ("2026-10-30","yandex_ads","Яндекс-реклама","Платная реклама","","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",1),
                ("2026-10-30","yandex_ads","Яндекс-реклама","Платная реклама","","АЗ | Ортодонт и гнатолог | Поиск | Новосибирск",34),
                ("2026-10-30","yandex_ads","Яндекс-реклама","Платная реклама","","",2),
                # Legacy "other" campaign remains separate and is not guessed.
                ("2026-10-28","yandex_ads","Яндекс-реклама","Платная реклама","other","Чужие кампании",1),
                ("2026-10-29","yandex_ads","Яндекс-реклама","Платная реклама","other","Чужие кампании",17),

                # Instagram 27-29 legacy source keys + 30.10 new parent representation.
                ("2026-10-27","instagram_starostenko","Instagram — Старостенко","Социальные сети","","starostenko",6),
                ("2026-10-27","instagram_bio_unknown","Instagram — ссылка в bio, профиль не определён","Социальные сети","","",2),
                ("2026-10-28","instagram_starostenko","Instagram — Старостенко","Социальные сети","","starostenko",2),
                ("2026-10-29","instagram_starostenko","Instagram — Старостенко","Социальные сети","","starostenko",1),
                ("2026-10-29","instagram_bio_unknown","Instagram — ссылка в bio, профиль не определён","Социальные сети","","",1),
                ("2026-10-30","instagram","Instagram","Социальные сети","","Старостенко",1),
            ]
            for day,source_key,source_name,channel,campaign_id,campaign_name,visits in traffic_rows:
                c.execute(
                    "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("metrika",day,source_key,source_name,channel,campaign_id,campaign_name,
                     None,None,visits,"t","metrika-legacy"),
                )

            goal_rows = [
                # Physical 30.10 values.
                ("2026-10-30","yandex_ads","Яндекс-реклама","Платная реклама","","АЗ | Ортодонт и гнатолог | Поиск | Новосибирск","booking_open","Открытие окна записи",2,2),
                ("2026-10-30","yandex_ads","Яндекс-реклама","Платная реклама","","АЗ | Ортодонт и гнатолог | Поиск | Новосибирск","medflex","Переход к записи в Medflex",1,1),
                ("2026-10-30","yandex_ads","Яндекс-реклама","Платная реклама","","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск","booking_open","Открытие окна записи",0,0),
                ("2026-10-30","yandex_ads","Яндекс-реклама","Платная реклама","","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск","medflex","Переход к записи в Medflex",0,0),
                ("2026-10-30","yandex_ads","Яндекс-реклама","Платная реклама","","","booking_open","Открытие окна записи",0,0),
                ("2026-10-30","yandex_ads","Яндекс-реклама","Платная реклама","","","medflex","Переход к записи в Medflex",0,0),

                # Legacy Instagram goals from the physical mixed-period presentation.
                ("2026-10-27","instagram_starostenko","Instagram — Старостенко","Социальные сети","","starostenko","booking_open","Открытие окна записи",1,1),
                ("2026-10-27","instagram_starostenko","Instagram — Старостенко","Социальные сети","","starostenko","medflex","Переход к записи в Medflex",1,1),
                ("2026-10-27","instagram_bio_unknown","Instagram — ссылка в bio, профиль не определён","Социальные сети","","","booking_open","Открытие окна записи",1,1),
                ("2026-10-27","instagram_bio_unknown","Instagram — ссылка в bio, профиль не определён","Социальные сети","","","medflex","Переход к записи в Medflex",1,1),
                ("2026-10-28","instagram_starostenko","Instagram — Старостенко","Социальные сети","","starostenko","medflex","Переход к записи в Medflex",1,1),
                ("2026-10-29","instagram_starostenko","Instagram — Старостенко","Социальные сети","","starostenko","medflex","Переход к записи в Medflex",1,1),
            ]
            for row in goal_rows:
                c.execute(
                    "INSERT INTO advertising_metrika_goal_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("metrika",)+row+("t","metrika-legacy"),
                )
            c.commit()

            sources = {
                row["source_key"]: row
                for row in m._group_breakdown(c,date(2026,10,27),date(2026,10,30),"source")
                if row["visits"] is not None
            }
            self.assertEqual(sources["yandex_ads"]["visits"],65)
            self.assertEqual(sources["yandex_ads"]["booking_open_visits"],2)
            self.assertEqual(sources["yandex_ads"]["medflex_visits"],1)
            self.assertEqual(sources["instagram"]["visits"],13)
            self.assertEqual(sources["instagram"]["booking_open_visits"],2)
            self.assertEqual(sources["instagram"]["medflex_visits"],4)
            self.assertNotIn("instagram_starostenko",sources)
            self.assertNotIn("instagram_bio_unknown",sources)

            campaigns=m._group_breakdown(c,date(2026,10,27),date(2026,10,30),"campaign")
            yandex={row["campaign_name"]:row for row in campaigns if row["source_key"]=="yandex_ads"}
            instagram={row["campaign_name"]:row for row in campaigns if row["source_key"]=="instagram"}

            self.assertEqual(
                set(yandex),
                {
                    "АЗ | Ортодонт и гнатолог | Поиск | Новосибирск",
                    "АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",
                    "Чужие кампании",
                    "Не определено",
                },
            )
            self.assertEqual(yandex["АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск"]["campaign_id"],"715444498")
            self.assertEqual(yandex["АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск"]["visits"],11)
            self.assertEqual(yandex["АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск"]["booking_open_visits"],0)
            self.assertEqual(yandex["АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск"]["medflex_visits"],0)
            self.assertEqual(yandex["АЗ | Ортодонт и гнатолог | Поиск | Новосибирск"]["campaign_id"],"715560815")
            self.assertEqual(yandex["АЗ | Ортодонт и гнатолог | Поиск | Новосибирск"]["visits"],34)
            self.assertEqual(yandex["Не определено"]["visits"],2)
            self.assertEqual(yandex["Чужие кампании"]["visits"],18)
            self.assertNotIn("bruksizm_search_nsk",yandex)

            self.assertEqual(instagram["Старостенко"]["visits"],10)
            self.assertEqual(instagram["Старостенко"]["booking_open_visits"],1)
            self.assertEqual(instagram["Старостенко"]["medflex_visits"],3)
            self.assertEqual(instagram["link_in_bio"]["visits"],3)
            self.assertEqual(instagram["link_in_bio"]["booking_open_visits"],1)
            self.assertEqual(instagram["link_in_bio"]["medflex_visits"],1)

            # Canonicalization changes grouping only; raw stored total remains exact.
            self.assertEqual(
                c.execute("SELECT SUM(visits) FROM advertising_traffic_daily WHERE provider='metrika'").fetchone()[0],
                78,
            )
            self.assertEqual(sum(row["visits"] for row in sources.values()),78)
        finally:
            c.close()

    def test_canonical_identity_does_not_guess_unknown_campaigns(self):
        self.assertEqual(
            m._canonical_metrika_identity(
                "yandex_ads","Яндекс-реклама","Платная реклама","","new_story_campaign"
            ),
            ("yandex_ads","Яндекс-реклама","Платная реклама","","new_story_campaign"),
        )
        self.assertEqual(
            m._canonical_metrika_identity(
                "instagram_other","Instagram — профиль не определён","Социальные сети","","story_123"
            ),
            ("instagram","Instagram","Социальные сети","","story_123"),
        )
        self.assertEqual(
            m._canonical_metrika_identity(
                "instagram_bio_unknown","Instagram — ссылка в bio, профиль не определён","Социальные сети","",""
            ),
            ("instagram","Instagram","Социальные сети","","link_in_bio"),
        )


    def test_parser_merges_native_and_utm_aliases_into_one_canonical_campaign(self):
        parsed=m._parse_metrika_zip(legacy_alias_zip(),"legacy.zip")
        yandex=[
            (key,values) for key,values in parsed["traffic_rows"].items()
            if key[1]=="yandex_ads"
        ]
        self.assertEqual(len(yandex),1)
        key,values=yandex[0]
        self.assertEqual(key[4],"715444498")
        self.assertEqual(key[5],"АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск")
        self.assertEqual(values["visits"],6)

        instagram={
            key[5]:values["visits"]
            for key,values in parsed["traffic_rows"].items()
            if key[1]=="instagram"
        }
        self.assertEqual(instagram,{"Старостенко":2,"link_in_bio":1})

    def test_same_archived_zip_reclassifies_legacy_rows_without_changing_totals_or_history(self):
        raw=legacy_alias_zip()
        parsed=m._parse_metrika_zip(raw,"legacy.zip")
        sha=parsed["payload_sha256"]
        with tempfile.TemporaryDirectory() as tmp:
            old=m.METRIKA_ARCHIVE_ROOT
            m.METRIKA_ARCHIVE_ROOT=Path(tmp)
            c=conn()
            try:
                archive=m._archive_metrika(raw,sha)
                c.execute(
                    "INSERT INTO advertising_metrika_imports VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (sha,"legacy.zip","2026-10-28","2026-10-28",json.dumps(["2026-10-28"]),
                     "2026-10-28T23:00:00+00:00","old",1,4,1,archive),
                )
                c.execute(
                    "INSERT INTO advertising_report_imports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sha,"metrika","2026-10-28","2026-10-28","2026-10-28T23:00:00+00:00",
                     "Asia/Novosibirsk","RUB","","cross_device_last_significant","old",1,4),
                )
                legacy_import="metrika-"+sha[:16]
                legacy_traffic=[
                    ("yandex_ads","Яндекс-реклама","Платная реклама","715444498","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",5),
                    ("yandex_ads","Яндекс-реклама","Платная реклама","","bruksizm_search_nsk",1),
                    ("instagram_starostenko","Instagram — Старостенко","Социальные сети","","starostenko",2),
                    ("instagram_bio_unknown","Instagram — ссылка в bio, профиль не определён","Социальные сети","","",1),
                ]
                for source_key,source_name,channel,campaign_id,campaign_name,visits in legacy_traffic:
                    c.execute(
                        "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("metrika","2026-10-28",source_key,source_name,channel,campaign_id,campaign_name,
                         None,None,visits,"old",legacy_import),
                    )
                c.execute(
                    "INSERT INTO advertising_metrika_goal_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("metrika","2026-10-28","instagram_starostenko","Instagram — Старостенко","Социальные сети",
                     "","starostenko","medflex","Переход к записи в Medflex",1,1,"old",legacy_import),
                )
                c.commit()

                before_visits=c.execute(
                    "SELECT SUM(visits) FROM advertising_traffic_daily WHERE provider='metrika' AND metric_date='2026-10-28'"
                ).fetchone()[0]
                before_goal=c.execute(
                    "SELECT SUM(goal_visits),SUM(reaches) FROM advertising_metrika_goal_daily "
                    "WHERE provider='metrika' AND metric_date='2026-10-28'"
                ).fetchone()
                before_imports=c.execute("SELECT COUNT(*) FROM advertising_metrika_imports").fetchone()[0]
                before_schema=c.execute("PRAGMA schema_version").fetchone()[0]

                self.assertFalse(m._stored_metrika_matches(c,parsed))
                result=m._store_metrika(c,parsed,1); c.commit()
                self.assertFalse(result["reused"])
                self.assertTrue(result["reclassified"])
                self.assertTrue(m._stored_metrika_matches(c,parsed))

                after_visits=c.execute(
                    "SELECT SUM(visits) FROM advertising_traffic_daily WHERE provider='metrika' AND metric_date='2026-10-28'"
                ).fetchone()[0]
                after_goal=c.execute(
                    "SELECT SUM(goal_visits),SUM(reaches) FROM advertising_metrika_goal_daily "
                    "WHERE provider='metrika' AND metric_date='2026-10-28'"
                ).fetchone()
                self.assertEqual(before_visits,after_visits)
                self.assertEqual(tuple(before_goal),tuple(after_goal))
                self.assertEqual(c.execute("SELECT COUNT(*) FROM advertising_metrika_imports").fetchone()[0],before_imports)
                self.assertEqual(c.execute("PRAGMA schema_version").fetchone()[0],before_schema)

                rows=c.execute(
                    "SELECT source_key,campaign_id,campaign_name,visits FROM advertising_traffic_daily "
                    "WHERE provider='metrika' AND metric_date='2026-10-28' ORDER BY source_key,campaign_name"
                ).fetchall()
                rendered=[tuple(row) for row in rows]
                self.assertIn(("yandex_ads","715444498","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",6),rendered)
                self.assertIn(("instagram","","Старостенко",2),rendered)
                self.assertIn(("instagram","","link_in_bio",1),rendered)
                self.assertNotIn(("yandex_ads","","bruksizm_search_nsk",1),rendered)

                second=m._store_metrika(c,parsed,1); c.commit()
                self.assertTrue(second["reused"])
                self.assertFalse(second["reclassified"])
            finally:
                c.close(); m.METRIKA_ARCHIVE_ROOT=old


    def test_history_reclass_preflight_is_read_only_and_resolves_current_archive(self):
        tool_path=Path(__file__).with_name("tools")/"propose-metrika-history-reclass-20261031.py"
        source=tool_path.read_text(encoding="utf-8")
        self.assertIn("?mode=ro",source)
        self.assertIn('PRAGMA query_only=ON',source)
        for forbidden in ("UPDATE advertising_","DELETE FROM advertising_","INSERT INTO advertising_","_store_metrika(","conn.commit("):
            self.assertNotIn(forbidden,source)

        raw=legacy_alias_zip()
        parsed=m._parse_metrika_zip(raw,"legacy.zip")
        sha=parsed["payload_sha256"]
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp)/(sha+".zip")
            archive.write_bytes(raw)
            c=conn()
            try:
                legacy_import="metrika-"+sha[:16]
                c.execute(
                    "INSERT INTO advertising_metrika_imports VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (sha,"legacy.zip","2026-10-28","2026-10-28",json.dumps(["2026-10-28"]),
                     "2026-10-28T23:00:00+00:00","old",1,4,1,str(archive)),
                )
                legacy_traffic=[
                    ("yandex_ads","Яндекс-реклама","Платная реклама","715444498","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",5),
                    ("yandex_ads","Яндекс-реклама","Платная реклама","","bruksizm_search_nsk",1),
                    ("instagram_starostenko","Instagram — Старостенко","Социальные сети","","starostenko",2),
                    ("instagram_bio_unknown","Instagram — ссылка в bio, профиль не определён","Социальные сети","","",1),
                ]
                for source_key,source_name,channel,campaign_id,campaign_name,visits in legacy_traffic:
                    c.execute(
                        "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("metrika","2026-10-28",source_key,source_name,channel,campaign_id,campaign_name,
                         None,None,visits,"old",legacy_import),
                    )
                c.execute(
                    "INSERT INTO advertising_metrika_goal_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("metrika","2026-10-28","instagram_starostenko","Instagram — Старостенко","Социальные сети",
                     "","starostenko","medflex","Переход к записи в Medflex",1,1,"old",legacy_import),
                )
                c.commit()

                previous=sys.modules.get("advertising_report")
                sys.modules["advertising_report"]=m
                try:
                    tool_spec=importlib.util.spec_from_file_location("metrika_history_reclass_proposal",tool_path)
                    tool=importlib.util.module_from_spec(tool_spec); tool_spec.loader.exec_module(tool)
                    tool.TARGET_DAYS=("2026-10-28",)
                    plan=tool.build_plan(c)
                finally:
                    if previous is None:
                        sys.modules.pop("advertising_report",None)
                    else:
                        sys.modules["advertising_report"]=previous

                self.assertEqual(len(plan),1)
                self.assertEqual(plan[0]["day"],"2026-10-28")
                self.assertEqual(plan[0]["payload_sha256"],sha)
                self.assertEqual(plan[0]["visits"],9)
                self.assertTrue(plan[0]["reclassification_required"])
                self.assertEqual(plan[0]["current_traffic_rows"],4)
                self.assertEqual(plan[0]["canonical_traffic_rows"],3)
            finally:
                c.close()


    def test_unknown_nonempty_yandex_id_is_not_replaced_by_matching_known_name(self):
        canonical=m._canonical_metrika_identity(
            "yandex_ads",
            "Яндекс-реклама",
            "Платная реклама",
            "999999999",
            "АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",
        )
        self.assertEqual(
            canonical,
            (
                "yandex_ads",
                "Яндекс-реклама",
                "Платная реклама",
                "999999999",
                "АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",
            ),
        )

    def test_parser_preserves_unknown_id_even_when_name_matches_known_campaign(self):
        parsed=m._parse_metrika_zip(unknown_id_conflict_zip(),"unknown-id.zip")
        yandex=sorted(
            [
                (key[4],key[5],values["visits"])
                for key,values in parsed["traffic_rows"].items()
                if key[1]=="yandex_ads"
            ]
        )
        self.assertEqual(
            yandex,
            [
                ("","bruksizm_search_nsk",1),
                ("715444498","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",5),
                ("999999999","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",4),
            ],
        )
        # Two factual IDs for the same UTM make that UTM ambiguous. The
        # fallback visit must remain separate rather than being assigned by
        # the historical alias dictionary.
        self.assertEqual(sum(row[2] for row in yandex),10)

    def test_group_breakdown_keeps_two_distinct_nonempty_ids_with_same_name(self):
        c=conn()
        try:
            name="АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск"
            for campaign_id,visits in (("715444498",5),("999999999",4)):
                c.execute(
                    "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "metrika","2026-10-28","yandex_ads","Яндекс-реклама","Платная реклама",
                        campaign_id,name,None,None,visits,"t","conflict",
                    ),
                )
            c.commit()
            rows=[
                row for row in m._group_breakdown(
                    c,date(2026,10,28),date(2026,10,28),"campaign"
                )
                if row["source_key"]=="yandex_ads"
            ]
            self.assertEqual(len(rows),2)
            by_id={row["campaign_id"]:row for row in rows}
            self.assertEqual(set(by_id),{"715444498","999999999"})
            self.assertEqual(by_id["715444498"]["visits"],5)
            self.assertEqual(by_id["999999999"]["visits"],4)
            self.assertEqual(sum(row["visits"] for row in rows),9)
        finally:
            c.close()



    def test_group_breakdown_preserves_distinct_unknown_ids_when_names_are_empty(self):
        c=conn()
        try:
            for campaign_id,visits in (("999999999",4),("888888888",3)):
                c.execute(
                    "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "metrika","2026-10-28","yandex_ads","Яндекс-реклама","Платная реклама",
                        campaign_id,"",None,None,visits,"t","empty-name",
                    ),
                )
            c.commit()
            rows=[
                row for row in m._group_breakdown(
                    c,date(2026,10,28),date(2026,10,28),"campaign"
                )
                if row["source_key"]=="yandex_ads"
            ]
            self.assertEqual(len(rows),2)
            by_id={row["campaign_id"]:row for row in rows}
            self.assertEqual(set(by_id),{"999999999","888888888"})
            self.assertEqual(by_id["999999999"]["campaign_name"],"Кампания 999999999")
            self.assertEqual(by_id["888888888"]["campaign_name"],"Кампания 888888888")
            self.assertEqual(by_id["999999999"]["visits"],4)
            self.assertEqual(by_id["888888888"]["visits"],3)
            self.assertEqual(sum(row["visits"] for row in rows),7)
        finally:
            c.close()

    def test_campaign_report_keeps_known_and_unknown_empty_name_ids_separate(self):
        parsed=m._parse_metrika_zip(
            unknown_empty_id_with_campaign_report_zip(),
            "unknown-empty-id-with-report.zip",
        )
        yandex=sorted(
            [
                (key[4],key[5],values["visits"])
                for key,values in parsed["traffic_rows"].items()
                if key[1]=="yandex_ads"
            ]
        )
        self.assertEqual(
            yandex,
            [
                ("715444498","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",6),
                ("999999999","",4),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            old=m.METRIKA_ARCHIVE_ROOT
            m.METRIKA_ARCHIVE_ROOT=Path(tmp)
            c=conn()
            try:
                m._store_metrika(c,parsed,1); c.commit()
                rows=[
                    row for row in m._group_breakdown(
                        c,date(2026,10,28),date(2026,10,28),"campaign"
                    )
                    if row["source_key"]=="yandex_ads"
                ]
                by_id={row["campaign_id"]:row for row in rows}
                self.assertEqual(set(by_id),{"715444498","999999999"})
                self.assertEqual(by_id["715444498"]["visits"],6)
                self.assertEqual(by_id["999999999"]["campaign_name"],"Кампания 999999999")
                self.assertEqual(by_id["999999999"]["visits"],4)
                self.assertEqual(sum(row["visits"] for row in rows),10)
            finally:
                c.close(); m.METRIKA_ARCHIVE_ROOT=old

    def test_new_api_format_never_applies_legacy_campaigns_clean_when_native_id_is_test_only(self):
        parsed=m._parse_metrika_zip(
            new_api_test_only_native_campaign_zip(),
            "new-api-test-only-native.zip",
            now=datetime(2026,11,1,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )
        yandex={
            (key[4],key[5]):values["visits"]
            for key,values in parsed["traffic_rows"].items()
            if key[1]=="yandex_ads"
        }
        self.assertEqual(
            yandex,
            {
                ("","bruksizm_search_nsk"):7,
            },
        )
        self.assertNotIn(("999999999","Тестовая кампания"),yandex)
        self.assertEqual(sum(yandex.values()),7)

    def test_universal_identity_uses_utm_fallback_and_discovers_new_sources_without_registries(self):
        parsed=m._parse_metrika_zip(
            universal_dynamic_identity_zip(),
            "universal-dynamic.zip",
            now=datetime(2026,11,8,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )

        yandex={
            (key[4],key[5]):values["visits"]
            for key,values in parsed["traffic_rows"].items()
            if key[1]=="yandex_ads"
        }
        self.assertEqual(
            yandex,
            {
                ("991234567","future_named_from_utm"):4,
                ("","future_service_alpha"):17,
                ("","future_service_beta"):12,
                ("",""):3,
            },
        )

        sources={
            key[2]:(key[1],values["visits"])
            for key,values in parsed["traffic_rows"].items()
            if key[1]!="yandex_ads"
        }
        self.assertEqual(sources["future_paid_network"][1],5)
        self.assertEqual(sources["future_direct_mail_network"][1],8)
        self.assertEqual(sources["future_social_network"][1],6)
        self.assertEqual(sources["future_partner"][1],7)
        self.assertEqual(len({value[0] for value in sources.values()}),4)

        goals={
            (key[2],key[5],key[6]):values["goal_visits"]
            for key,values in parsed["goal_rows"].items()
        }
        self.assertEqual(
            goals[("Яндекс-реклама","future_service_alpha","booking_open")],
            2,
        )
        self.assertEqual(
            goals[("future_paid_network","launch_alpha","medflex")],
            1,
        )

        with tempfile.TemporaryDirectory() as tmp:
            old=m.METRIKA_ARCHIVE_ROOT
            m.METRIKA_ARCHIVE_ROOT=Path(tmp)
            c=conn()
            try:
                m._store_metrika(c,parsed,1); c.commit()
                source_rows={
                    row["source_name"]:row
                    for row in m._group_breakdown(
                        c,date(2026,11,7),date(2026,11,7),"source"
                    )
                    if row["visits"] is not None
                }
                self.assertEqual(source_rows["future_paid_network"]["visits"],5)
                self.assertEqual(source_rows["future_direct_mail_network"]["visits"],8)
                self.assertEqual(source_rows["future_social_network"]["visits"],6)
                self.assertEqual(source_rows["future_partner"]["visits"],7)
                self.assertEqual(
                    len({
                        source_rows[name]["source_key"]
                        for name in (
                            "future_paid_network",
                            "future_direct_mail_network",
                            "future_social_network",
                            "future_partner",
                        )
                    }),
                    4,
                )

                all_campaigns=m._group_breakdown(
                    c,date(2026,11,7),date(2026,11,7),"campaign"
                )
                dynamic_paid={
                    row["campaign_name"]:row
                    for row in all_campaigns
                    if row["source_name"]=="future_paid_network"
                }
                dynamic_social={
                    row["campaign_name"]:row
                    for row in all_campaigns
                    if row["source_name"]=="future_social_network"
                }
                dynamic_referral={
                    row["campaign_name"]:row
                    for row in all_campaigns
                    if row["source_name"]=="future_partner"
                }
                self.assertEqual(dynamic_paid["launch_alpha"]["visits"],5)
                self.assertEqual(dynamic_social["creator_launch"]["visits"],6)
                # clinic_profile is a technical placement UTM, not a child
                # marketing campaign. The source keeps all 7 visits above,
                # but no campaign row is rendered for this marker.
                self.assertEqual(dynamic_referral,{})

                campaigns=[
                    row for row in m._group_breakdown(
                        c,date(2026,11,7),date(2026,11,7),"campaign"
                    )
                    if row["source_key"]=="yandex_ads"
                ]
                by_name={row["campaign_name"]:row for row in campaigns}
                self.assertEqual(by_name["future_service_alpha"]["visits"],17)
                self.assertEqual(by_name["future_service_beta"]["visits"],12)
                self.assertEqual(by_name["future_named_from_utm"]["campaign_id"],"991234567")
                self.assertEqual(by_name["Не определено"]["visits"],3)
            finally:
                c.close(); m.METRIKA_ARCHIVE_ROOT=old

    def test_clinic_profile_is_technical_utm_not_child_campaign(self):
        parsed=m._parse_metrika_zip(
            metrika_zip(),
            "technical-clinic-profile.zip",
            now=datetime(2026,10,28,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )

        nap=[
            (key,values)
            for key,values in parsed["traffic_rows"].items()
            if key[1]=="napopravku"
        ]
        pro=[
            (key,values)
            for key,values in parsed["traffic_rows"].items()
            if key[1]=="prodoctorov"
        ]
        self.assertEqual(len(nap),1)
        self.assertEqual(len(pro),1)
        self.assertEqual(nap[0][0][5],"")
        self.assertEqual(pro[0][0][5],"")
        self.assertEqual(nap[0][1]["visits"],2)
        self.assertEqual(pro[0][1]["visits"],1)

        c=conn()
        try:
            historical=[
                ("direct","Прямые заходы","Прямые заходы",2),
                ("yandex_organic","Яндекс органика","Поиск",4),
                ("2gis","2ГИС","Карты",14),
                ("napopravku","НаПоправку","Другие источники",2),
                ("prodoctorov","ПроДокторов","Другие источники",1),
            ]
            for source_key,source_name,channel,visits in historical:
                c.execute(
                    "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "metrika","2026-11-08",source_key,source_name,channel,
                        "","clinic_profile",None,None,visits,"t","legacy-profile",
                    ),
                )
            c.commit()

            sources={
                row["source_key"]:row
                for row in m._group_breakdown(
                    c,date(2026,11,8),date(2026,11,8),"source"
                )
            }
            self.assertEqual(sources["direct"]["visits"],2)
            self.assertEqual(sources["yandex_organic"]["visits"],4)
            self.assertEqual(sources["2gis"]["visits"],14)
            self.assertEqual(sources["napopravku"]["visits"],2)
            self.assertEqual(sources["prodoctorov"]["visits"],1)

            campaigns=m._group_breakdown(
                c,date(2026,11,8),date(2026,11,8),"campaign"
            )
            self.assertFalse(
                any(row["campaign_name"]=="clinic_profile" for row in campaigns)
            )
        finally:
            c.close()

    def test_future_arbitrary_utm_is_not_promoted_on_non_campaign_sources(self):
        cases=[
            (
                {
                    "traffic_source_id":"direct",
                    "traffic_source_name":"Прямые заходы",
                    "utm_source":"future_source",
                    "utm_campaign":"future_technical_marker",
                },
                "direct",
            ),
            (
                {
                    "traffic_source_id":"organic",
                    "traffic_source_name":"Переходы из поисковых систем",
                    "source_detail":"Яндекс",
                    "source_detail_id":"organic.yandex",
                    "utm_source":"future_source",
                    "utm_campaign":"future_technical_marker",
                },
                "yandex_organic",
            ),
            (
                {
                    "traffic_source_id":"referral",
                    "traffic_source_name":"Переходы по ссылкам на сайтах",
                    "source_detail":"link.2gis.ru",
                    "source_detail_id":"referral.link.2gis.ru",
                    "utm_source":"future_source",
                    "utm_campaign":"future_technical_marker",
                },
                "2gis",
            ),
            (
                {
                    "traffic_source_id":"internal",
                    "traffic_source_name":"Внутренние переходы",
                    "utm_campaign":"future_technical_marker",
                },
                "internal",
            ),
        ]
        for row,expected_source in cases:
            source_key,source_name,channel=m._metrika_source(row)
            identity=m._metrika_campaign_identity(
                row,source_key,source_name,channel,{}
            )
            self.assertEqual(identity[0],expected_source)
            self.assertEqual(identity[4],"")

        # Paid and social traffic remain campaign-bearing. This prevents the
        # future-proof guard from flattening real campaign structures.
        paid={
            "traffic_source_id":"ad",
            "traffic_source_name":"Переходы по рекламе",
            "utm_source":"future_paid_network",
            "utm_campaign":"future_real_campaign",
        }
        source_key,source_name,channel=m._metrika_source(paid)
        identity=m._metrika_campaign_identity(
            paid,source_key,source_name,channel,{}
        )
        self.assertEqual(identity[4],"future_real_campaign")

        social={
            "traffic_source_id":"social",
            "traffic_source_name":"Переходы из социальных сетей",
            "source_detail":"future.social",
            "utm_source":"future_social_network",
            "utm_campaign":"future_social_campaign",
        }
        source_key,source_name,channel=m._metrika_source(social)
        identity=m._metrika_campaign_identity(
            social,source_key,source_name,channel,{}
        )
        self.assertEqual(identity[4],"future_social_campaign")

    def test_future_historical_utm_marker_is_hidden_by_source_semantics_not_name(self):
        c=conn()
        try:
            rows=[
                ("direct","Прямые заходы","Прямые заходы",2),
                ("yandex_organic","Яндекс органика","Поиск",4),
                ("2gis","2ГИС","Карты",14),
                ("napopravku","НаПоправку","Другие источники",2),
                ("prodoctorov","ПроДокторов","Другие источники",1),
            ]
            for source_key,source_name,channel,visits in rows:
                c.execute(
                    "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "metrika","2026-11-08",source_key,source_name,channel,
                        "","future_unknown_placement_tag",None,None,visits,
                        "t","historical-future-marker",
                    ),
                )
            c.commit()

            source_rows={
                row["source_key"]:row
                for row in m._group_breakdown(
                    c,date(2026,11,8),date(2026,11,8),"source"
                )
            }
            self.assertEqual(source_rows["direct"]["visits"],2)
            self.assertEqual(source_rows["yandex_organic"]["visits"],4)
            self.assertEqual(source_rows["2gis"]["visits"],14)
            self.assertEqual(source_rows["napopravku"]["visits"],2)
            self.assertEqual(source_rows["prodoctorov"]["visits"],1)

            campaigns=m._group_breakdown(
                c,date(2026,11,8),date(2026,11,8),"campaign"
            )
            self.assertFalse(
                any(
                    row["campaign_name"]=="future_unknown_placement_tag"
                    for row in campaigns
                )
            )
        finally:
            c.close()

    def test_factual_campaign_id_named_clinic_profile_is_not_hidden(self):
        identity=m._canonical_metrika_identity(
            "yandex_ads",
            "Яндекс-реклама",
            "Платная реклама",
            "123456789",
            "clinic_profile",
            allow_legacy_aliases=False,
        )
        self.assertEqual(
            identity,
            (
                "yandex_ads",
                "Яндекс-реклама",
                "Платная реклама",
                "123456789",
                "clinic_profile",
            ),
        )

    def test_az186_01_sentinel_direct_name_cannot_outrank_factual_utm(self):
        parsed=m._parse_metrika_zip(
            az186_blocker_identity_zip(),
            "az186-blockers.zip",
            now=datetime(2026,11,8,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )
        yandex={
            (key[4],key[5]):values["visits"]
            for key,values in parsed["traffic_rows"].items()
            if key[1]=="yandex_ads"
        }
        self.assertEqual(yandex,{("","future_service_gamma"):7})
        self.assertNotIn(("","Другие кампании"),yandex)

    def test_az186_02_known_platforms_require_exact_trusted_evidence_not_substrings(self):
        parsed=m._parse_metrika_zip(
            az186_blocker_identity_zip(),
            "az186-blockers.zip",
            now=datetime(2026,11,8,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )
        by_name={
            key[2]:(key[1],values["visits"])
            for key,values in parsed["traffic_rows"].items()
        }

        paid_key,paid_visits=by_name["future_direct_mail_network"]
        self.assertEqual(paid_visits,9)
        self.assertNotEqual(paid_key,"yandex_ads")

        organic_key,organic_visits=by_name["NotGoogleSearch"]
        self.assertEqual(organic_visits,4)
        self.assertNotEqual(organic_key,"google_organic")

        referral_key,referral_visits=by_name["future_2gis_partner"]
        self.assertEqual(referral_visits,3)
        self.assertNotEqual(referral_key,"2gis")

        # Exact existing provider evidence must still canonicalize.
        self.assertEqual(
            m._metrika_source({
                "traffic_source_id":"organic",
                "source_detail":"Google",
                "source_detail_id":"organic.google",
            })[0],
            "google_organic",
        )
        self.assertEqual(
            m._metrika_source({
                "traffic_source_id":"referral",
                "source_detail":"link.2gis.ru",
                "source_detail_id":"referral.link.2gis.ru",
            })[0],
            "2gis",
        )

    def test_az186_03_distinct_factual_source_values_have_collision_safe_keys(self):
        parsed=m._parse_metrika_zip(
            az186_blocker_identity_zip(),
            "az186-blockers.zip",
            now=datetime(2026,11,8,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )

        parsed_sources={
            key[2]:(key[1],values["visits"])
            for key,values in parsed["traffic_rows"].items()
            if key[2] in {"future.network","future-network"}
        }
        self.assertEqual(parsed_sources["future.network"][1],5)
        self.assertEqual(parsed_sources["future-network"][1],6)
        self.assertNotEqual(
            parsed_sources["future.network"][0],
            parsed_sources["future-network"][0],
        )

        with tempfile.TemporaryDirectory() as tmp:
            old=m.METRIKA_ARCHIVE_ROOT
            m.METRIKA_ARCHIVE_ROOT=Path(tmp)
            c=conn()
            try:
                m._store_metrika(c,parsed,1); c.commit()
                rows={
                    row["source_name"]:row
                    for row in m._group_breakdown(
                        c,date(2026,11,7),date(2026,11,7),"source"
                    )
                    if row["source_name"] in {"future.network","future-network"}
                }
                self.assertEqual(set(rows),{"future.network","future-network"})
                self.assertEqual(rows["future.network"]["visits"],5)
                self.assertEqual(rows["future.network"]["booking_open_visits"],2)
                self.assertEqual(rows["future-network"]["visits"],6)
                self.assertEqual(rows["future-network"]["booking_open_visits"],3)
                self.assertNotEqual(
                    rows["future.network"]["source_key"],
                    rows["future-network"]["source_key"],
                )
                self.assertEqual(
                    rows["future.network"]["visits"] + rows["future-network"]["visits"],
                    11,
                )
            finally:
                c.close(); m.METRIKA_ARCHIVE_ROOT=old

    def test_az186_04_long_dynamic_source_key_saves_through_costs_get_post(self):
        raw_source="future_network_" + ("a" * 62)
        self.assertEqual(len(raw_source),77)
        parsed=m._parse_metrika_zip(
            az186_long_source_cost_zip(raw_source),
            "az186-long-source.zip",
            now=datetime(2026,11,8,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )

        traffic=[
            (key,values)
            for key,values in parsed["traffic_rows"].items()
            if key[2]==raw_source
        ]
        self.assertEqual(len(traffic),1)
        source_key=traffic[0][0][1]
        self.assertLessEqual(len(source_key),120)
        self.assertEqual(traffic[0][1]["visits"],3)

        same=m._dynamic_source_identity(
            "paid",raw_source,"Платная реклама","Платная реклама"
        )[0]
        other=m._dynamic_source_identity(
            "paid",raw_source[:-1]+"b","Платная реклама","Платная реклама"
        )[0]
        self.assertEqual(source_key,same)
        self.assertNotEqual(source_key,other)
        self.assertLessEqual(len(other),120)

        with tempfile.TemporaryDirectory() as tmp:
            old_archive=m.METRIKA_ARCHIVE_ROOT
            old_db=m.db
            old_ensure=m._ensure_schema
            old_request=m.request
            m.METRIKA_ARCHIVE_ROOT=Path(tmp)
            c=conn()
            try:
                c.execute(
                    "CREATE TABLE report_blobs("
                    "key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
                )
                m._store_metrika(c,parsed,1)
                c.commit()

                class FakeApp:
                    def __init__(self):
                        self.routes={}
                    def get(self,path):
                        return self._route("GET",path)
                    def post(self,path):
                        return self._route("POST",path)
                    def _route(self,method,path):
                        def decorator(fn):
                            self.routes[(method,path)]=fn
                            return fn
                        return decorator

                app=FakeApp()
                m.db=lambda:c
                m._ensure_schema=lambda path=m.DB_PATH:None
                m.register_advertising_report(app)

                m.request=types.SimpleNamespace(
                    args={"from":"2026-11-07","to":"2026-11-07"},
                    form={},
                    get_json=lambda silent=True:{},
                )
                payload=app.routes[("GET","/api/reports/advertising/costs")]()
                long_source=next(
                    row for row in payload["sources"]
                    if row["source_name"]==raw_source
                )
                self.assertEqual(long_source["source_key"],source_key)
                self.assertLessEqual(len(long_source["source_key"]),120)

                m.request=types.SimpleNamespace(
                    args={},
                    form={
                        "entity_type":"source",
                        "entity_key":long_source["source_key"],
                        "source_name":long_source["source_name"],
                        "channel_name":long_source["channel_name"],
                        "from":"2026-11-07",
                        "to":"2026-11-07",
                        "advertising_payment":"100",
                        "contractors_payment":"",
                        "total_spend":"100",
                    },
                    get_json=lambda silent=True:{},
                )
                response=app.routes[("POST","/api/reports/advertising/costs")]()
                self.assertEqual(response["status"],"ok")
                self.assertEqual(response["version"],2)

                stored=json.loads(
                    c.execute(
                        "SELECT payload FROM report_blobs WHERE key=?",
                        (m.ADVERTISING_COSTS_KEY,),
                    ).fetchone()[0]
                )
                self.assertEqual(stored["version"],2)
                period=stored["periods"]["sources"][source_key][0]
                self.assertEqual(period["advertising_payment"],100)
                self.assertIsNone(period["contractors_payment"])
                self.assertEqual(period["total_spend"],100)
            finally:
                m.METRIKA_ARCHIVE_ROOT=old_archive
                m.db=old_db
                m._ensure_schema=old_ensure
                m.request=old_request
                c.close()

    def test_hierarchical_costs_v1_migration_preserves_unknown_contractor(self):
        c=conn()
        try:
            c.execute(
                "CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
            )
            legacy={
                "version":1,
                "sources":{
                    "yandex_ads":{
                        "name":"Яндекс-реклама",
                        "channel_name":"Платная реклама",
                        "manual":False,
                    }
                },
                "periods":[{
                    "from":"2026-11-01",
                    "to":"2026-11-08",
                    "advertising_payment":1000,
                    "contractors_payment":200,
                    "total_spend":1200,
                    "source_spend":{"yandex_ads":900},
                }],
            }
            c.execute(
                "INSERT INTO report_blobs VALUES(?,?,?,?)",
                (m.ADVERTISING_COSTS_KEY,json.dumps(legacy,ensure_ascii=False),1,"t"),
            )
            c.commit()
            store=m._load_cost_store(c)
            self.assertEqual(store["version"],2)
            global_summary=m._cost_entity_summary(
                store,"global","",date(2026,11,1),date(2026,11,8)
            )
            source_summary=m._cost_entity_summary(
                store,"source","yandex_ads",date(2026,11,1),date(2026,11,8)
            )
            self.assertEqual(global_summary["total_spend"],1200)
            self.assertEqual(source_summary["advertising_payment"],900)
            self.assertIsNone(source_summary["contractors_payment"])
            self.assertIsNone(source_summary["total_spend"])
            self.assertTrue(source_summary["legacy_partial"])
        finally:
            c.close()

    def test_hierarchical_costs_global_source_campaign_are_detail_not_double_count(self):
        c=conn()
        old_db=m.db
        old_ensure=m._ensure_schema
        old_request=m.request
        try:
            c.execute(
                "CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-11-08","paid_test","Тестовый рекламный источник","Платная реклама",
                 "777","АЗ | Тестовая РК | Поиск | Новосибирск",None,None,10,"t","m"),
            )
            c.commit()

            class FakeApp:
                def __init__(self): self.routes={}
                def get(self,path): return self._route("GET",path)
                def post(self,path): return self._route("POST",path)
                def _route(self,method,path):
                    def decorator(fn):
                        self.routes[(method,path)]=fn
                        return fn
                    return decorator

            app=FakeApp();m.db=lambda:c;m._ensure_schema=lambda path=m.DB_PATH:None
            m.register_advertising_report(app)

            m.request=types.SimpleNamespace(
                args={"from":"2026-11-08","to":"2026-11-08"},form={},
                get_json=lambda silent=True:{},
            )
            hierarchy=app.routes[("GET","/api/reports/advertising/costs")]()
            source=next(row for row in hierarchy["sources"] if row["source_key"]=="paid_test")
            campaign=source["campaigns"][0]

            def save(level,key,ad,contractor,total,extra=None):
                form={
                    "entity_type":level,"entity_key":key,
                    "from":"2026-11-08","to":"2026-11-08",
                    "advertising_payment":str(ad),
                    "contractors_payment":"" if contractor is None else str(contractor),
                    "total_spend":str(total),
                }
                form.update(extra or {})
                m.request=types.SimpleNamespace(args={},form=form,get_json=lambda silent=True:{})
                response=app.routes[("POST","/api/reports/advertising/costs")]()
                self.assertEqual(response["status"],"ok")

            save(
                "source","paid_test",100,None,100,
                {"source_name":"Тестовый рекламный источник","channel_name":"Платная реклама"},
            )
            save("campaign",campaign["campaign_key"],80,None,80)

            store=m._load_cost_store(c)
            global_summary=m._cost_entity_summary(store,"global","",date(2026,11,8),date(2026,11,8))
            source_summary=m._cost_entity_summary(store,"source","paid_test",date(2026,11,8),date(2026,11,8))
            campaign_summary=m._cost_entity_summary(store,"campaign",campaign["campaign_key"],date(2026,11,8),date(2026,11,8))
            self.assertIsNone(global_summary["total_spend"])
            self.assertEqual(source_summary["total_spend"],100)
            self.assertIsNone(source_summary["contractors_payment"])
            self.assertEqual(campaign_summary["total_spend"],80)
            self.assertIsNone(campaign_summary["contractors_payment"])

            economics=m._source_economics(c,date(2026,11,8),date(2026,11,8),store)
            self.assertEqual(economics["source_total_spend"],100)
            self.assertEqual(economics["source_reconciliation_delta"],0)
            self.assertEqual(
                economics["sources"]["paid_test"]["campaign_distributed_total"],80
            )
            self.assertEqual(
                economics["sources"]["paid_test"]["campaign_reconciliation_delta"],20
            )
            # Child 80 is detail only: it is never added to source/global 100.
            self.assertEqual(economics["budget"]["total_spend"],100)
            self.assertEqual(economics["budget"]["contractors_payment"],0)
            self.assertEqual(m._summary(c,date(2026,11,8),date(2026,11,8))["spend"],100)
        finally:
            m.db=old_db;m._ensure_schema=old_ensure;m.request=old_request;c.close()

    def test_yandex_api_campaign_cost_excludes_child_contractors_and_preserves_shared_source(self):
        c=conn()
        try:
            start=end=date(2026,11,8)
            normalized=m._normalize_package({
                "schema_version":m.SCHEMA_VERSION,
                "generated_at":"2026-11-09T00:00:00+07:00",
                "timezone":"Asia/Novosibirsk",
                "currency":"RUB",
                "vat_mode":"DIRECT_INCLUDE_VAT_YES",
                "attribution_model":"YANDEX_DIRECT_AGGREGATE_CONVERSIONS",
                "datasets":{"direct":{
                    "period_start":"2026-11-08",
                    "period_end":"2026-11-08",
                    "rows":[{
                        "date":"2026-11-08","source_key":"yandex_ads",
                        "source_name":"Яндекс-реклама","channel_name":"Платная реклама",
                        "campaign_id":"777","campaign_name":"API кампания",
                        "spend":110,"clicks":10,"conversions":2.5
                    }]
                }}
            })
            m._apply_import(c,normalized,1)
            c.commit()
            key=m._campaign_cost_key("yandex_ads","777","API кампания")
            store=m._empty_hierarchical_cost_store()
            store["sources"]["yandex_ads"]={
                "name":"Яндекс-реклама","channel_name":"Платная реклама","manual":False
            }
            store["campaigns"][key]={
                "source_key":"yandex_ads","campaign_id":"777","campaign_name":"API кампания"
            }
            store["periods"]["sources"]["yandex_ads"]=[{
                "from":"2026-11-08","to":"2026-11-08",
                "advertising_payment":100,"contractors_payment":20,
                "total_spend":120,
            }]
            store["periods"]["campaigns"][key]=[{
                "from":"2026-11-08","to":"2026-11-08",
                "advertising_payment":80,"contractors_payment":10,
                "total_spend":90,
            }]
            economics=m._source_economics(c,start,end,store)
            campaign=economics["campaigns"][key]
            source=economics["sources"]["yandex_ads"]
            self.assertEqual(campaign["advertising_payment"],110)
            self.assertIsNone(campaign["contractors_payment"])
            self.assertEqual(campaign["total_spend"],110)
            self.assertEqual(campaign["conversions"],2.5)
            self.assertEqual(source["advertising_payment"],110)
            self.assertEqual(source["contractors_payment"],20)
            self.assertEqual(source["total_spend"],130)
            self.assertEqual(source["campaign_distributed_total"],110)
            self.assertEqual(source["campaign_reconciliation_delta"],0)
        finally:
            c.close()

    def test_az202_01_single_contractor_and_derived_global_reject_second_truth(self):
        # Reproduce the independent audit blocker: legacy global 1039 versus
        # factual Yandex 100 + one contractor 20 + another source ad 30.
        c=conn()
        old_db=m.db
        old_ensure=m._ensure_schema
        old_request=m.request
        try:
            c.execute(
                "CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
            )
            normalized=m._normalize_package({
                "schema_version":m.SCHEMA_VERSION,
                "generated_at":"2026-11-09T00:00:00+07:00",
                "timezone":"Asia/Novosibirsk",
                "currency":"RUB",
                "vat_mode":"DIRECT_INCLUDE_VAT_YES",
                "attribution_model":"YANDEX_DIRECT_AGGREGATE_CONVERSIONS",
                "datasets":{"direct":{
                    "period_start":"2026-11-08",
                    "period_end":"2026-11-08",
                    "rows":[{
                        "date":"2026-11-08","source_key":"yandex_ads",
                        "source_name":"Яндекс-реклама","channel_name":"Платная реклама",
                        "campaign_id":"777","campaign_name":"Кампания Direct",
                        "spend":100,"clicks":10,"conversions":2
                    }]
                }}
            })
            m._apply_import(c,normalized,1)
            key=m._campaign_cost_key("yandex_ads","777","Кампания Direct")
            period=lambda a,contractor,total:{
                "from":"2026-11-08","to":"2026-11-08",
                "advertising_payment":a,
                "contractors_payment":contractor,
                "total_spend":total,
            }
            legacy={
                "version":2,
                "sources":{
                    "yandex_ads":{
                        "name":"Яндекс-реклама","channel_name":"Платная реклама","manual":False
                    },
                    "other_paid":{
                        "name":"Другой платный источник","channel_name":"Платная реклама","manual":True
                    },
                },
                "campaigns":{
                    key:{
                        "source_key":"yandex_ads","campaign_id":"777",
                        "campaign_name":"Кампания Direct",
                    }
                },
                "periods":{
                    "global":[period(999,40,1039)],
                    "sources":{
                        "yandex_ads":[period(100,20,120)],
                        "other_paid":[period(30,7,37)],
                    },
                    "campaigns":{
                        key:[period(80,10,90)],
                    },
                },
            }
            original=json.dumps(legacy,ensure_ascii=False,separators=(",",":"))
            c.execute(
                "INSERT INTO report_blobs VALUES(?,?,?,?)",
                (m.ADVERTISING_COSTS_KEY,original,1,"t"),
            )
            c.commit()

            start=end=date(2026,11,8)
            report=m._summary(c,start,end)
            self.assertEqual(report["spend"],150)
            self.assertEqual(report["advertising_payment"],130)
            self.assertEqual(report["contractors_payment"],20)
            economics=m._source_economics(c,start,end)
            self.assertEqual(economics["sources"]["yandex_ads"]["total_spend"],120)
            self.assertEqual(economics["sources"]["other_paid"]["total_spend"],30)
            self.assertIsNone(economics["sources"]["other_paid"]["contractors_payment"])
            self.assertEqual(economics["campaigns"][key]["total_spend"],100)
            self.assertIsNone(economics["campaigns"][key]["contractors_payment"])
            self.assertEqual(economics["source_reconciliation_delta"],0)
            hierarchy=m._cost_hierarchy_payload(c,start,end)
            self.assertIsNone(hierarchy["global"]["exact"]["contractors_payment"])
            self.assertEqual(hierarchy["global"]["effective"]["total_spend"],150)
            other=next(row for row in hierarchy["sources"] if row["source_key"]=="other_paid")
            self.assertIsNone(other["exact"]["contractors_payment"])
            self.assertEqual(other["exact"]["total_spend"],30)
            self.assertEqual(
                c.execute(
                    "SELECT payload FROM report_blobs WHERE key=?",
                    (m.ADVERTISING_COSTS_KEY,),
                ).fetchone()[0],original,
            )

            class FakeApp:
                def __init__(self): self.routes={}
                def get(self,path): return self._route("GET",path)
                def post(self,path): return self._route("POST",path)
                def _route(self,method,path):
                    def decorator(fn):
                        self.routes[(method,path)]=fn
                        return fn
                    return decorator
            app=FakeApp()
            m.db=lambda:c
            m._ensure_schema=lambda path=m.DB_PATH:None
            m.register_advertising_report(app)

            def submit(level,key,ad,contractor,total):
                m.request=types.SimpleNamespace(
                    args={},
                    form={
                        "entity_type":level,
                        "entity_key":key,
                        "source_name":"Яндекс-реклама" if key=="yandex_ads" else "Другой платный источник",
                        "channel_name":"Платная реклама",
                        "from":"2026-11-08",
                        "to":"2026-11-08",
                        "advertising_payment":ad,
                        "contractors_payment":contractor,
                        "total_spend":total,
                    },
                    get_json=lambda silent=True:{},
                )
                return app.routes[("POST","/api/reports/advertising/costs")]()

            self.assertEqual(submit("global","", "999","40","1039")[1],403)
            self.assertEqual(submit("source","other_paid","30","7","37")[1],403)
            self.assertEqual(submit("campaign",key,"80","10","90")[1],403)
            self.assertEqual(
                c.execute(
                    "SELECT payload FROM report_blobs WHERE key=?",
                    (m.ADVERTISING_COSTS_KEY,),
                ).fetchone()[0],original,
            )
            self.assertEqual(submit("source","yandex_ads","100","30","130")["status"],"ok")
            self.assertEqual(submit("source","other_paid","40","","40")["status"],"ok")
            after=m._summary(c,start,end)
            self.assertEqual(after["spend"],170)
            self.assertEqual(after["advertising_payment"],140)
            self.assertEqual(after["contractors_payment"],30)
            stored=m._load_cost_store(c)
            self.assertEqual(stored["periods"]["global"][0]["total_spend"],1039)
            self.assertEqual(
                stored["periods"]["sources"]["other_paid"][0]["contractors_payment"],None
            )
            self.assertEqual(
                stored["periods"]["sources"]["yandex_ads"][0]["contractors_payment"],30
            )
        finally:
            m.db=old_db
            m._ensure_schema=old_ensure
            m.request=old_request
            c.close()

    def test_campaign_cost_key_is_stable_when_factual_id_name_changes(self):
        a=m._campaign_cost_key("yandex_ads","777","Старое название")
        b=m._campaign_cost_key("yandex_ads","777","Новое название")
        self.assertEqual(a,b)
        self.assertLessEqual(len(a),120)
        self.assertNotEqual(
            a,
            m._campaign_cost_key("yandex_ads","778","Старое название"),
        )

    def test_factual_utm_direct_evidence_learns_cross_day_and_ambiguity_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=m.METRIKA_ARCHIVE_ROOT;m.METRIKA_ARCHIVE_ROOT=Path(tmp)
            c=conn()
            try:
                first=m._parse_metrika_zip(
                    campaign_evidence_zip(
                        "2026-11-06","gastro_search_nsk","777",
                        "АЗ | Гастроэнтеролог | Поиск | Новосибирск",5,
                    ),
                    "evidence-first.zip",
                    now=datetime(2026,11,9,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
                )
                m._store_metrika(c,first,1);c.commit()
                evidence=m._campaign_evidence_map(c)
                self.assertEqual(
                    evidence[("yandex_ads","gastro_search_nsk")]["campaign_id"],
                    "777",
                )

                fallback=m._parse_metrika_zip(
                    campaign_evidence_zip(
                        "2026-11-07","gastro_search_nsk","other","Чужие кампании",3,
                    ),
                    "fallback.zip",
                    now=datetime(2026,11,9,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
                    campaign_evidence=evidence,
                )
                identity=[
                    (key[4],key[5],value["visits"])
                    for key,value in fallback["traffic_rows"].items()
                    if key[1]=="yandex_ads"
                ]
                self.assertEqual(
                    identity,
                    [("777","АЗ | Гастроэнтеролог | Поиск | Новосибирск",3)],
                )

                conflicting=m._parse_metrika_zip(
                    campaign_evidence_zip(
                        "2026-11-07","gastro_search_nsk","888",
                        "Другая factual РК",1,
                    ),
                    "conflict.zip",
                    now=datetime(2026,11,9,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
                )
                m._store_campaign_evidence(c,conflicting["campaign_evidence"]);c.commit()
                ambiguous=m._campaign_evidence_map(c)
                self.assertIsNone(ambiguous[("yandex_ads","gastro_search_nsk")])

                after_conflict=m._parse_metrika_zip(
                    campaign_evidence_zip(
                        "2026-11-08","gastro_search_nsk","other","Чужие кампании",2,
                    ),
                    "ambiguous-fallback.zip",
                    now=datetime(2026,11,9,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
                    campaign_evidence=ambiguous,
                )
                identity=[
                    (key[4],key[5],value["visits"])
                    for key,value in after_conflict["traffic_rows"].items()
                    if key[1]=="yandex_ads"
                ]
                self.assertEqual(identity,[("","gastro_search_nsk",2)])
            finally:
                c.close();m.METRIKA_ARCHIVE_ROOT=old

    def test_group_breakdown_merges_same_factual_campaign_id_across_labels(self):
        c=conn()
        try:
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-11-08","yandex_ads","Яндекс-реклама","Платная реклама",
                 "777","Короткое имя",None,None,3,"t","a"),
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-11-08","yandex_ads","Яндекс-реклама","Платная реклама",
                 "777","АЗ | Полное имя кампании | Поиск | Новосибирск",None,None,4,"t","b"),
            )
            c.commit()
            rows=m._group_breakdown(c,date(2026,11,8),date(2026,11,8),"campaign")
            factual=[row for row in rows if row["campaign_id"]=="777"]
            self.assertEqual(len(factual),1)
            self.assertEqual(factual[0]["visits"],7)
            self.assertEqual(
                factual[0]["campaign_name"],
                "АЗ | Полное имя кампании | Поиск | Новосибирск",
            )
        finally:
            c.close()

    def test_az188_01_native_api_fallback_never_uses_legacy_alias_without_evidence(self):
        parsed=m._parse_metrika_zip(
            campaign_evidence_zip(
                "2026-11-07",
                "bruksizm_search_nsk",
                "other",
                "Чужие кампании",
                8,
            ),
            "native-fallback-no-evidence.zip",
            now=datetime(2026,11,9,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )
        yandex=[
            (key[4],key[5],values["visits"])
            for key,values in parsed["traffic_rows"].items()
            if key[1]=="yandex_ads"
        ]
        self.assertEqual(yandex,[("","bruksizm_search_nsk",8)])
        self.assertEqual(parsed["campaign_evidence"],[])

        with tempfile.TemporaryDirectory() as tmp:
            old=m.METRIKA_ARCHIVE_ROOT;m.METRIKA_ARCHIVE_ROOT=Path(tmp)
            c=conn()
            try:
                m._store_metrika(c,parsed,1);c.commit()
                marker=c.execute(
                    "SELECT campaign_identity_source FROM advertising_metrika_import_formats"
                ).fetchone()[0]
                self.assertEqual(marker,"traffic_direct_click_order")
                rows=m._group_breakdown(
                    c,date(2026,11,7),date(2026,11,7),"campaign"
                )
                yandex=[
                    row for row in rows if row["source_key"]=="yandex_ads"
                ]
                self.assertEqual(len(yandex),1)
                self.assertEqual(yandex[0]["campaign_id"],"")
                self.assertEqual(yandex[0]["campaign_name"],"bruksizm_search_nsk")
                self.assertEqual(yandex[0]["visits"],8)
            finally:
                c.close();m.METRIKA_ARCHIVE_ROOT=old

    def test_az188_02_late_factual_evidence_merges_already_stored_fallback_at_read_time(self):
        fallback=m._parse_metrika_zip(
            campaign_evidence_zip(
                "2026-11-07","future_delayed_evidence",
                "other","Чужие кампании",8,
            ),
            "delayed-fallback.zip",
            now=datetime(2026,11,9,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )
        factual=m._parse_metrika_zip(
            campaign_evidence_zip(
                "2026-11-08","future_delayed_evidence",
                "918222","АЗ | Позднее доказательство | Поиск | Новосибирск",3,
            ),
            "delayed-factual.zip",
            now=datetime(2026,11,9,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )

        with tempfile.TemporaryDirectory() as tmp:
            old=m.METRIKA_ARCHIVE_ROOT;m.METRIKA_ARCHIVE_ROOT=Path(tmp)
            c=conn()
            try:
                m._store_metrika(c,fallback,1);c.commit()
                before=m._group_breakdown(
                    c,date(2026,11,7),date(2026,11,7),"campaign"
                )
                self.assertEqual(
                    [
                        (row["campaign_id"],row["campaign_name"],row["visits"])
                        for row in before if row["source_key"]=="yandex_ads"
                    ],
                    [("","future_delayed_evidence",8)],
                )

                m._store_metrika(c,factual,1);c.commit()
                evidence=m._campaign_evidence_map(c)
                self.assertEqual(
                    evidence[("yandex_ads","future_delayed_evidence")]["campaign_id"],
                    "918222",
                )

                combined=[
                    row for row in m._group_breakdown(
                        c,date(2026,11,7),date(2026,11,8),"campaign"
                    )
                    if row["source_key"]=="yandex_ads"
                ]
                self.assertEqual(len(combined),1)
                self.assertEqual(combined[0]["campaign_id"],"918222")
                self.assertEqual(
                    combined[0]["campaign_name"],
                    "АЗ | Позднее доказательство | Поиск | Новосибирск",
                )
                self.assertEqual(combined[0]["visits"],11)

                # The old day itself is also resolved at read time; no archive
                # replay is required merely to make the report canonical.
                old_day=[
                    row for row in m._group_breakdown(
                        c,date(2026,11,7),date(2026,11,7),"campaign"
                    )
                    if row["source_key"]=="yandex_ads"
                ]
                self.assertEqual(len(old_day),1)
                self.assertEqual(old_day[0]["campaign_id"],"918222")
                self.assertEqual(old_day[0]["visits"],8)
            finally:
                c.close();m.METRIKA_ARCHIVE_ROOT=old

    def test_az190_02_cost_only_name_metadata_cannot_create_campaign_under_non_campaign_source(self):
        c=conn()
        try:
            c.execute(
                "CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
            )
            hidden_key=m._campaign_cost_key(
                "napopravku","","future_unknown_placement_tag"
            )
            factual_key=m._campaign_cost_key(
                "napopravku","123456789","future_unknown_placement_tag"
            )
            payload={
                "version":2,
                "sources":{
                    "napopravku":{
                        "name":"НаПоправку",
                        "channel_name":"Другие источники",
                        "manual":False,
                    },
                },
                "campaigns":{
                    hidden_key:{
                        "source_key":"napopravku",
                        "campaign_id":"",
                        "campaign_name":"future_unknown_placement_tag",
                    },
                    factual_key:{
                        "source_key":"napopravku",
                        "campaign_id":"123456789",
                        "campaign_name":"future_unknown_placement_tag",
                    },
                },
                "periods":{
                    "global":[],
                    "sources":{},
                    "campaigns":{
                        hidden_key:[{
                            "from":"2026-11-08",
                            "to":"2026-11-08",
                            "advertising_payment":50,
                            "contractors_payment":10,
                            "total_spend":60,
                            "legacy_partial":False,
                        }],
                        factual_key:[{
                            "from":"2026-11-08",
                            "to":"2026-11-08",
                            "advertising_payment":70,
                            "contractors_payment":10,
                            "total_spend":80,
                            "legacy_partial":False,
                        }],
                    },
                },
            }
            original=json.dumps(payload,ensure_ascii=False,sort_keys=True)
            c.execute(
                "INSERT INTO report_blobs VALUES(?,?,?,?)",
                (
                    m.ADVERTISING_COSTS_KEY,
                    json.dumps(payload,ensure_ascii=False),
                    1,
                    "t",
                ),
            )
            c.commit()

            store=m._load_cost_store(c)
            self.assertIn(hidden_key,store["campaigns"])
            self.assertEqual(
                store["periods"]["campaigns"][hidden_key][0]["total_spend"],
                60,
            )

            registry=m._campaign_registry(c,store)
            self.assertFalse(
                any(item["campaign_key"]==hidden_key for item in registry)
            )
            self.assertTrue(
                any(item["campaign_key"]==factual_key for item in registry)
            )

            rows=m._group_breakdown(
                c,date(2026,11,8),date(2026,11,8),"campaign"
            )
            self.assertFalse(
                any(item["campaign_key"]==hidden_key for item in rows)
            )
            factual=next(
                item for item in rows
                if item["campaign_key"]==factual_key
            )
            self.assertEqual(factual["campaign_id"],"123456789")
            self.assertEqual(factual["total_spend"],70)
            self.assertIsNone(factual["visits"])

            hierarchy=m._cost_hierarchy_payload(
                c,date(2026,11,8),date(2026,11,8)
            )
            source=next(
                item for item in hierarchy["sources"]
                if item["source_key"]=="napopravku"
            )
            self.assertFalse(
                any(
                    item["campaign_key"]==hidden_key
                    for item in source["campaigns"]
                )
            )
            self.assertTrue(
                any(
                    item["campaign_key"]==factual_key
                    for item in source["campaigns"]
                )
            )

            # The fix is read-only with respect to persisted cost metadata.
            row=c.execute(
                "SELECT payload FROM report_blobs WHERE key=?",
                (m.ADVERTISING_COSTS_KEY,),
            ).fetchone()
            self.assertEqual(
                json.dumps(
                    json.loads(row["payload"]),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                original,
            )
        finally:
            c.close()

    def test_az188_03_campaign_cost_row_is_visible_without_selected_period_visits(self):
        c=conn()
        old_db=m.db
        old_ensure=m._ensure_schema
        old_request=m.request
        try:
            c.execute(
                "CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "metrika","2026-11-07","paid_test","Тестовый рекламный источник","Платная реклама",
                    "701","АЗ | РК без визитов сегодня | Поиск | Новосибирск",
                    None,None,5,"t","metrika-history",
                ),
            )
            c.commit()

            class FakeApp:
                def __init__(self): self.routes={}
                def get(self,path): return self._route("GET",path)
                def post(self,path): return self._route("POST",path)
                def _route(self,method,path):
                    def decorator(fn):
                        self.routes[(method,path)]=fn
                        return fn
                    return decorator

            app=FakeApp()
            m.db=lambda:c
            m._ensure_schema=lambda path=m.DB_PATH:None
            m.register_advertising_report(app)

            m.request=types.SimpleNamespace(
                args={"from":"2026-11-08","to":"2026-11-08"},
                form={},
                get_json=lambda silent=True:{},
            )
            hierarchy=app.routes[("GET","/api/reports/advertising/costs")]()
            source=next(
                row for row in hierarchy["sources"]
                if row["source_key"]=="paid_test"
            )
            campaign=next(
                row for row in source["campaigns"]
                if row["campaign_id"]=="701"
            )

            m.request=types.SimpleNamespace(
                args={},
                form={
                    "entity_type":"campaign",
                    "entity_key":campaign["campaign_key"],
                    "from":"2026-11-08",
                    "to":"2026-11-08",
                    "advertising_payment":"70",
                    "contractors_payment":"",
                    "total_spend":"70",
                },
                get_json=lambda silent=True:{},
            )
            response=app.routes[("POST","/api/reports/advertising/costs")]()
            self.assertEqual(response["status"],"ok")

            rows=[
                row for row in m._group_breakdown(
                    c,date(2026,11,8),date(2026,11,8),"campaign"
                )
                if row["source_key"]=="paid_test"
            ]
            self.assertEqual(len(rows),1)
            row=rows[0]
            self.assertEqual(row["campaign_id"],"701")
            self.assertEqual(row["advertising_payment"],70)
            self.assertIsNone(row["contractors_payment"])
            self.assertEqual(row["total_spend"],70)
            self.assertEqual(row["spend"],70)
            self.assertIsNone(row["visits"])
            self.assertTrue(row["campaign_key"])

            payload=m._report_payload(
                c,date(2026,11,8),date(2026,11,8),"none","visits"
            )
            report_campaign=next(
                item for item in payload["campaigns"]
                if item["campaign_id"]=="701"
            )
            self.assertEqual(report_campaign["total_spend"],70)
        finally:
            m.db=old_db
            m._ensure_schema=old_ensure
            m.request=old_request
            c.close()

    def test_native_direct_click_order_ids_override_legacy_campaign_report_fallback(self):
        raw=unknown_empty_id_with_campaign_report_zip()
        parsed=m._parse_metrika_zip(raw,"native-id-with-report.zip")
        yandex={
            (key[4],key[5]):values["visits"]
            for key,values in parsed["traffic_rows"].items()
            if key[1]=="yandex_ads"
        }
        self.assertEqual(
            yandex,
            {
                ("715444498","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск"):6,
                ("999999999",""):4,
            },
        )

    def test_campaign_report_split_fails_closed_when_unknown_nonempty_id_exists(self):
        parsed=m._parse_metrika_zip(
            unknown_id_conflict_with_campaign_report_zip(),
            "unknown-id-with-report.zip",
        )
        yandex=[
            (key[4],key[5],values["visits"])
            for key,values in parsed["traffic_rows"].items()
            if key[1]=="yandex_ads"
        ]
        # The same ZIP contains factual same-row evidence that
        # bruksizm_search_nsk belongs to campaign 999999999. The one
        # fallback UTM visit is therefore evidence-merged into that ID.
        self.assertIn(
            (
                "999999999",
                "АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",
                5,
            ),
            yandex,
        )
        self.assertEqual(sum(row[2] for row in yandex),41)
        self.assertTrue(
            any(campaign_id=="" and visits==36 for campaign_id,_name,visits in yandex)
        )

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

    def test_actual_received_money_changes_with_selected_day_and_range(self):
        c=conn()
        try:
            c.execute(
                "CREATE TABLE cash_receipts_daily("
                "data_date TEXT PRIMARY KEY,payload_json TEXT,source_filename TEXT,"
                "source_sha256 TEXT,imported_by INTEGER,imported_at TEXT)"
            )
            for day,total,ooo,ip in (
                ("2026-10-27",100000,40000,60000),
                ("2026-10-28",250000,50000,200000),
                ("2026-10-29",70000,20000,50000),
            ):
                c.execute(
                    "INSERT INTO cash_receipts_daily VALUES(?,?,?,?,?,?)",
                    (
                        day,
                        json.dumps({
                            "cashTotal":total,
                            "cashOOO":ooo,
                            "cashIP":ip,
                            "cashUnallocated":0,
                        }),
                        "cash.xlsx","sha",1,"t",
                    ),
                )
            c.commit()

            day27=m._revenue_summary(c,date(2026,10,27),date(2026,10,27))
            day28=m._revenue_summary(c,date(2026,10,28),date(2026,10,28))
            range2728=m._revenue_summary(c,date(2026,10,27),date(2026,10,28))

            self.assertEqual(day27["actual_received_total"],100000)
            self.assertEqual(day28["actual_received_total"],250000)
            self.assertEqual(range2728["actual_received_total"],350000)
            self.assertEqual(range2728["cash_ooo"],90000)
            self.assertEqual(range2728["cash_ip"],260000)
            self.assertNotEqual(day27["actual_received_total"],day28["actual_received_total"])
        finally:
            c.close()

    def test_selected_period_coverage_matches_source_evidence(self):
        c=conn()
        try:
            for day,visits in (("2026-10-27",75),("2026-10-28",42)):
                c.execute(
                    "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
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

    def test_yandex_campaign_breakdown_keeps_named_and_undefined(self):
        c=conn()
        try:
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-10-29","yandex_ads","Яндекс-реклама","Платная реклама",
                 "101","Ортодонтия",None,None,20,"t","named"),
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-10-29","yandex_ads","Яндекс-реклама","Платная реклама",
                 "","",None,None,8,"t","unknown"),
            )
            c.execute(
                "INSERT INTO advertising_metrika_goal_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-10-29","yandex_ads","Яндекс-реклама","Платная реклама",
                 "101","Ортодонтия","booking_open","Открытие окна записи",2,2,"t","named"),
            )
            c.execute(
                "INSERT INTO advertising_metrika_goal_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-10-29","yandex_ads","Яндекс-реклама","Платная реклама",
                 "","","medflex","Переход к записи в Medflex",3,3,"t","unknown"),
            )
            c.commit()

            rows=m._group_breakdown(c,date(2026,10,29),date(2026,10,29),"campaign")
            yandex=[row for row in rows if row["source_key"]=="yandex_ads"]
            by_name={row["campaign_name"]:row for row in yandex}
            self.assertEqual(set(by_name),{"Ортодонтия","Не определено"})
            self.assertEqual(by_name["Ортодонтия"]["visits"],20)
            self.assertEqual(by_name["Ортодонтия"]["booking_open_visits"],2)
            self.assertEqual(by_name["Не определено"]["visits"],8)
            self.assertEqual(by_name["Не определено"]["medflex_visits"],3)
        finally:
            c.close()

    def test_advertising_routes_are_exempt_from_global_structure_terminology(self):
        for path in (
            "/reports/advertising/",
            "/reports/advertising.html",
            "/reports/advertising.js",
            "/api/reports/advertising",
            "/api/reports/advertising/metrika/import",
        ):
            self.assertTrue(terminology.skip_runtime_rename(path), path)

        for path in (
            "/reports/management/",
            "/reports/forecast/",
            "/api/reports/management",
        ):
            self.assertFalse(terminology.skip_runtime_rename(path), path)

    def test_ui_contract(self):
        root=Path(__file__).parents[1]/"reports"
        html=(root/"advertising.html").read_text(encoding="utf-8")
        js=(root/"advertising.js").read_text(encoding="utf-8")
        uploads=(Path(__file__).parent/"templates"/"uploads.html").read_text(encoding="utf-8")
        cost_fragment=(Path(__file__).parent/"templates"/"advertising_costs_fragment.html").read_text(encoding="utf-8")
        daily=(Path(__file__).parent/"daily_upload.py").read_text(encoding="utf-8")
        text=html+"\n"+js
        for label in (
            "Рекламный отчёт","Главное за период","Расходы на рекламу",
            "Клики по рекламе","Посещения сайта","Целевые лиды",
            "Состоявшиеся первичные приёмы","Выручка первичных приёмов",
            "Выручка новых пациентов за 30 дней","Работа клиники за период",
            "Динамика","Источники","Не хватает данных CRM для построения отчёта",
            "Стоимость рекламного первичного","ROAS первичных",
        ):
            self.assertIn(label,text)
        for forbidden in (
            "Потратили и получили","Первично записались",
            "Источники сайта",'id="providerRevenueRows"',
        ):
            self.assertNotIn(forbidden,text)
        self.assertIn("s.lead_to_primary_pct", js)
        self.assertIn("Стоимость клика не считается, пока охват расходов и кликов не совпадает.", js)
        self.assertIn("Стоимость рекламного первичного: <b>нет данных CRM</b>", js)
        self.assertIn("ROAS первичных: <b>нет данных CRM</b>", js)
        self.assertIn("function renderSources()", js)
        self.assertIn("const expandable=campaigns.length>0;", js)
        self.assertNotIn("report-cost-editor", js)
        self.assertNotIn("data-cost-save", js)
        self.assertNotIn("data-cost-field", js)
        self.assertNotIn("/api/reports/advertising/costs", js)
        self.assertNotIn("sourceKey==='yandex_ads'||sourceKey==='instagram'", js)
        self.assertIn("class=\"source-expand\"", js)
        self.assertIn("class=\"child-row source-campaign-row\"", js)
        self.assertIn("(expanded?'−':'+')", js)
        self.assertIn("spend_provenance==='direct_api'", js)
        self.assertIn("spend_provenance==='manual'", js)
        self.assertIn('data-range="day">Сегодня</button>', html)
        self.assertIn('data-range="month">Текущий месяц по сегодня</button>', html)
        self.assertIn('value="primary_paid">Выручка первичных</option>', html)
        self.assertIn('value="actual_received_total">Фактически получено денег</option>', html)
        self.assertNotIn('<option value="spend">Расходы</option>', html)
        self.assertIn('id="coverage"', html)
        self.assertIn("Покрытие выбранного периода", js)
        self.assertIn("history.replaceState", js)
        self.assertIn("load({serverDefaults:true})", js)
        self.assertIn("5. Яндекс Метрика", uploads)
        self.assertNotIn("6. Расходы на рекламу", uploads)
        self.assertIn("6. Расходы на рекламу", cost_fragment)
        self.assertIn("Оплата рекламы", cost_fragment)
        self.assertIn("Подрядчики", cost_fragment)
        self.assertIn("Всего расходов на рекламу", cost_fragment)
        self.assertIn("Добавить источник", cost_fragment)
        self.assertIn("/api/reports/advertising/costs", cost_fragment)
        self.assertIn("Подрядчик вводится только в строке «Яндекс-реклама»", cost_fragment)
        self.assertIn("ad-cost-row", cost_fragment)
        self.assertIn("data-field=\"contractors_payment\"", cost_fragment)
        self.assertIn("data-save", cost_fragment)
        self.assertNotIn("report-cost-editor", js)
        self.assertNotIn("data-cost-save", js)
        self.assertNotIn("data-cost-field", js)
        self.assertIn("contractors_payment", js)
        self.assertIn("total_spend", js)
        self.assertIn('render_template(\n                "advertising_costs_fragment.html"', daily)
        self.assertIn('perms=perms,', daily)
        self.assertIn('if "upload_services" in perms:', daily)

    def test_manual_cost_store_requires_exact_coverage_without_proration(self):
        c=conn()
        try:
            c.execute(
                "CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
            )
            payload={
                "version":1,
                "sources":{"yandex_ads":{"name":"Яндекс-реклама","channel_name":"Платная реклама","manual":False}},
                "periods":[{
                    "from":"2026-10-01","to":"2026-10-31",
                    "advertising_payment":100000,"contractors_payment":20000,"total_spend":120000,
                    "source_spend":{"yandex_ads":100000},
                }],
            }
            c.execute(
                "INSERT INTO report_blobs VALUES(?,?,?,?)",
                (m.ADVERTISING_COSTS_KEY,json.dumps(payload,ensure_ascii=False),1,"t"),
            )
            exact=m._summary(c,date(2026,10,1),date(2026,10,31))
            partial=m._summary(c,date(2026,10,15),date(2026,10,20))
            # Old global entry survives in the persisted v1 blob, but no
            # longer acts as an independent financial source of truth.
            self.assertIsNone(exact["spend"])
            self.assertEqual(exact["advertising_payment"],100000)
            self.assertIsNone(exact["contractors_payment"])
            legacy=m._load_cost_store(c)
            self.assertEqual(legacy["periods"]["global"][0]["total_spend"],120000)
            self.assertIsNone(partial["spend"])
            self.assertIsNone(partial["advertising_payment"])
        finally:
            c.close()

    def test_manual_source_spend_enriches_existing_source_and_cost_per_visit(self):
        c=conn()
        try:
            c.execute(
                "CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-10-01","yandex_ads","Яндекс-реклама","Платная реклама","","",None,None,80,"t","m"),
            )
            payload={
                "version":1,
                "sources":{"yandex_ads":{"name":"Яндекс-реклама","channel_name":"Платная реклама","manual":False}},
                "periods":[{
                    "from":"2026-10-01","to":"2026-10-01",
                    "advertising_payment":1000,"contractors_payment":200,"total_spend":1200,
                    "source_spend":{"yandex_ads":1000},
                }],
            }
            c.execute(
                "INSERT INTO report_blobs VALUES(?,?,?,?)",
                (m.ADVERTISING_COSTS_KEY,json.dumps(payload,ensure_ascii=False),1,"t"),
            )
            c.commit()
            rows=m._group_breakdown(c,date(2026,10,1),date(2026,10,1),"source")
            yandex=next(row for row in rows if row["source_key"]=="yandex_ads")
            self.assertEqual(yandex["spend"],1000)
            self.assertEqual(yandex["spend_provenance"],"manual")
            self.assertEqual(yandex["visits"],80)
            self.assertEqual(yandex["cost_per_visit"],12.5)
            self.assertIsNone(yandex["cost_per_click"])
        finally:
            c.close()

    def test_direct_api_overrides_manual_source_spend_only_with_complete_period(self):
        c=conn()
        try:
            c.execute(
                "CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
            )
            payload={
                "version":1,
                "sources":{"yandex_ads":{"name":"Яндекс-реклама","channel_name":"Платная реклама","manual":False}},
                "periods":[{
                    "from":"2026-10-01","to":"2026-10-02",
                    "advertising_payment":1000,"contractors_payment":200,"total_spend":1200,
                    "source_spend":{"yandex_ads":1000},
                }],
            }
            c.execute(
                "INSERT INTO report_blobs VALUES(?,?,?,?)",
                (m.ADVERTISING_COSTS_KEY,json.dumps(payload,ensure_ascii=False),1,"t"),
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("direct","2026-10-01","yandex_ads","Яндекс-реклама","Платная реклама","","",400,20,None,"t","d1"),
            )
            c.commit()
            partial=m._source_economics(c,date(2026,10,1),date(2026,10,2))
            self.assertEqual(partial["sources"]["yandex_ads"]["spend"],1000)
            self.assertEqual(partial["sources"]["yandex_ads"]["spend_provenance"],"manual")
            self.assertIsNone(partial["sources"]["yandex_ads"]["clicks"])

            for day in ("2026-10-01","2026-10-02"):
                c.execute(
                    "INSERT INTO advertising_report_imports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("sha"+day,"direct",day,day,"t","Asia/Novosibirsk","RUB","","","t",1,1),
                )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("direct","2026-10-02","yandex_ads","Яндекс-реклама","Платная реклама","","",500,25,None,"t","d2"),
            )
            c.commit()
            complete=m._source_economics(c,date(2026,10,1),date(2026,10,2))
            self.assertEqual(complete["sources"]["yandex_ads"]["spend"],900)
            self.assertEqual(complete["sources"]["yandex_ads"]["spend_provenance"],"direct_api")
            self.assertEqual(complete["sources"]["yandex_ads"]["clicks"],45)
            self.assertEqual(complete["source_spend_total"],900)
            # New policy: old global manual 1200 does not substitute for
            # the source's unknown shared contractor (v1 source is partial).
            summary=m._summary(c,date(2026,10,1),date(2026,10,2))
            self.assertIsNone(summary["spend"])
            self.assertEqual(summary["advertising_payment"],900)
            self.assertEqual(summary["clicks"],45)
            self.assertEqual(summary["cost_per_click"],20)
        finally:
            c.close()


    def test_az185_01_total_spend_must_include_contractors(self):
        payload={
            "version":1,
            "sources":{"yandex_ads":{"name":"Яндекс-реклама","channel_name":"Платная реклама","manual":False}},
            "periods":[{
                "from":"2026-10-01","to":"2026-10-01",
                "advertising_payment":100000,
                "contractors_payment":20000,
                "total_spend":100000,
                "source_spend":{"yandex_ads":100000},
            }],
        }
        with self.assertRaisesRegex(ValueError,"total spend must equal"):
            m._normalize_cost_store(payload)

        payload["periods"][0]["total_spend"]=120000
        normalized=m._normalize_cost_store(payload)
        self.assertEqual(normalized["periods"][0]["total_spend"],120000)

    def test_az185_03_cpc_is_unknown_when_paid_source_spend_is_unknown(self):
        c=conn()
        try:
            c.execute(
                "CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
            )
            payload={
                "version":1,
                "sources":{
                    "yandex_ads":{"name":"Яндекс-реклама","channel_name":"Платная реклама","manual":False},
                    "other_paid":{"name":"Другой платный источник","channel_name":"Платная реклама","manual":True},
                },
                "periods":[{
                    "from":"2026-10-01","to":"2026-10-01",
                    "advertising_payment":100,
                    "contractors_payment":20,
                    "total_spend":120,
                    "source_spend":{"yandex_ads":100,"other_paid":None},
                }],
            }
            c.execute(
                "INSERT INTO report_blobs VALUES(?,?,?,?)",
                (m.ADVERTISING_COSTS_KEY,json.dumps(payload,ensure_ascii=False),1,"t"),
            )
            c.execute(
                "INSERT INTO advertising_report_imports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("sha","direct","2026-10-01","2026-10-01","t","Asia/Novosibirsk","RUB","","","t",1,2),
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("direct","2026-10-01","yandex_ads","Яндекс-реклама","Платная реклама","","",100,10,None,"t","d1"),
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("direct","2026-10-01","other_paid","Другой платный источник","Платная реклама","","",None,5,None,"t","d2"),
            )
            c.commit()
            economics=m._source_economics(c,date(2026,10,1),date(2026,10,1))
            self.assertFalse(economics["click_scope_complete"])
            self.assertIsNone(economics["cost_per_click"])
        finally:
            c.close()

    def test_az185_07_manual_added_other_source_with_unknown_spend_blocks_general_cpc(self):
        c=conn()
        old_db=m.db
        old_ensure=m._ensure_schema
        old_request=m.request
        try:
            c.execute(
                "CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-10-27","yandex_ads","Яндекс-реклама","Платная реклама","","",None,None,10,"t","m"),
            )
            c.commit()

            class FakeApp:
                def __init__(self): self.routes={}
                def get(self,path): return self._route("GET",path)
                def post(self,path): return self._route("POST",path)
                def _route(self,method,path):
                    def decorator(fn):
                        self.routes[(method,path)]=fn
                        return fn
                    return decorator

            app=FakeApp()
            m.db=lambda:c
            m._ensure_schema=lambda path=m.DB_PATH:None
            m.register_advertising_report(app)

            def post(form):
                m.request=types.SimpleNamespace(args={},form=form,get_json=lambda silent=True:{})
                response=app.routes[("POST","/api/reports/advertising/costs")]()
                self.assertEqual(response["status"],"ok")
                return response

            post({
                "entity_type":"source","entity_key":"",
                "source_name":"Ручной новый рекламный источник",
                "channel_name":"Другие источники",
                "from":"2026-10-27","to":"2026-10-27",
                "advertising_payment":"","contractors_payment":"","total_spend":"",
            })

            stored=json.loads(
                c.execute(
                    "SELECT payload FROM report_blobs WHERE key=?",
                    (m.ADVERTISING_COSTS_KEY,),
                ).fetchone()[0]
            )
            self.assertEqual(stored["version"],2)
            manual=[
                (key,value)
                for key,value in stored["sources"].items()
                if value.get("manual")
            ]
            self.assertEqual(len(manual),1)
            manual_key,manual_meta=manual[0]
            self.assertEqual(manual_meta["channel_name"],"Другие источники")
            self.assertNotIn(manual_key,stored["periods"]["sources"])

            c.execute(
                "INSERT INTO advertising_report_imports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("sha-direct","direct","2026-10-27","2026-10-27","t","Asia/Novosibirsk","RUB","","","t",1,1),
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("direct","2026-10-27","yandex_ads","Яндекс-реклама","Платная реклама","","",100,10,None,"t","d"),
            )
            c.commit()

            economics=m._source_economics(c,date(2026,10,27),date(2026,10,27))
            self.assertFalse(economics["click_scope_complete"])
            self.assertIsNone(economics["cost_per_click"])
            self.assertIsNone(economics["budget"]["total_spend"])
            summary=m._summary(c,date(2026,10,27),date(2026,10,27))
            self.assertIsNone(summary["cost_per_click"])
            self.assertIsNone(summary["spend"])
        finally:
            m.db=old_db
            m._ensure_schema=old_ensure
            m.request=old_request
            c.close()

    def test_az185_05_direct_null_spend_day_cannot_override_manual_period_spend(self):
        c=conn()
        try:
            c.execute(
                "CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
            )
            payload={
                "version":1,
                "sources":{"yandex_ads":{"name":"Яндекс-реклама","channel_name":"Платная реклама","manual":False}},
                "periods":[{
                    "from":"2026-10-01","to":"2026-10-02",
                    "advertising_payment":1000,
                    "contractors_payment":200,
                    "total_spend":1200,
                    "source_spend":{"yandex_ads":1000},
                }],
            }
            c.execute(
                "INSERT INTO report_blobs VALUES(?,?,?,?)",
                (m.ADVERTISING_COSTS_KEY,json.dumps(payload,ensure_ascii=False),1,"t"),
            )
            for day in ("2026-10-01","2026-10-02"):
                c.execute(
                    "INSERT INTO advertising_report_imports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("sha"+day,"direct",day,day,"t","Asia/Novosibirsk","RUB","","","t",1,1),
                )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("direct","2026-10-01","yandex_ads","Яндекс-реклама","Платная реклама","","",400,20,None,"t","d1"),
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("direct","2026-10-02","yandex_ads","Яндекс-реклама","Платная реклама","","",None,25,None,"t","d2"),
            )
            c.commit()
            economics=m._source_economics(c,date(2026,10,1),date(2026,10,2))
            yandex=economics["sources"]["yandex_ads"]
            self.assertEqual(yandex["spend"],1000)
            self.assertEqual(yandex["spend_provenance"],"manual")
            self.assertIsNone(yandex["direct_spend"])
        finally:
            c.close()

    def test_az185_06_primary_paid_trend_matches_summary_with_crm_and_mis_rows(self):
        c=conn()
        try:
            c.execute(
                "CREATE TABLE daily_uploads(data_date TEXT PRIMARY KEY, normalized_json TEXT NOT NULL, revision INTEGER NOT NULL)"
            )
            patient="Иванов И. И."
            staff="Доктор А. А."
            c.execute(
                "INSERT INTO daily_uploads VALUES(?,?,1)",
                ("2026-10-27",json.dumps({"visits":[{"date":"2026-10-27","patient":patient,"kind":"Первичные"}]},ensure_ascii=False)),
            )
            report={
                "period_start":"2026-10-27",
                "period_end":"2026-10-27",
                "totals":{"paid":1000},
                "items":[{
                    "patient":patient,"staff":staff,"invoice":"1","date":"2026-10-27",
                    "group":"Приёмы","service":"Консультация","qty":1,
                    "amount":1000,"paid_amount":1000,"closing_debt":0,
                }],
                "patients":[{
                    "patient_token":m.advertising_mis_bridge.patient_token(patient),
                    "staff":staff,"paid":1000,
                }],
            }
            m.advertising_mis_bridge.capture_paid_report(c,report,"27.xlsx","sha","t")
            m.advertising_mis_bridge.rebuild(c,"t")
            c.execute(
                "INSERT INTO advertising_clinic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("crm","2026-10-27","crm","CRM","CRM","","",None,None,1,1000,None,0,0,"t","crm"),
            )
            c.commit()
            summary=m._summary(c,date(2026,10,27),date(2026,10,27))
            trend=m._metric_daily(c,date(2026,10,27),date(2026,10,27),"primary_paid")
            self.assertEqual(summary["primary_paid"],1000)
            self.assertEqual(trend["2026-10-27"],1000)
        finally:
            c.close()

    def test_yandex_business_geoadv_prefix_is_classified_without_guessing_other_paid_utm(self):
        for value in (
            "geoadv",
            "geoadv_direct",
            "geoadv_search_yabs",
            "geoadv_maps",
            "GEOADV_SEARCH_ORGANIZATION_LIST",
        ):
            row = {
                "traffic_source_id": "ad",
                "traffic_source_name": "Переходы по рекламе",
                "source_detail": "Яндекс Директ: Не определено",
                "source_detail_id": "ad.Яндекс Директ: Не определено",
                "utm_source": value,
            }
            self.assertEqual(
                m._metrika_source(row),
                ("yandex_business", "Яндекс Бизнес", "Платная реклама"),
            )

        for value in ("geoadvert_search_yabs", "my_geoadv_search_yabs", "future_paid_network"):
            row = {
                "traffic_source_id": "ad",
                "traffic_source_name": "Переходы по рекламе",
                "utm_source": value,
            }
            self.assertNotEqual(m._metrika_source(row)[0], "yandex_business")

    def test_geoadv_legacy_classifier_does_not_recurse_for_non_paid_rows(self):
        module_name = "reclassify_yandex_business_geoadv_test"
        old_module = sys.modules.get("advertising_report")
        sys.modules["advertising_report"] = m
        try:
            spec = importlib.util.spec_from_file_location(
                module_name,
                Path(__file__).with_name("reclassify_yandex_business_geoadv.py"),
            )
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
        finally:
            if old_module is None:
                sys.modules.pop("advertising_report", None)
            else:
                sys.modules["advertising_report"] = old_module

        original = m._metrika_source
        m._metrika_source = helper._legacy_metrika_source
        try:
            row = {
                "traffic_source_id": "organic",
                "traffic_source_name": "Переходы из поисковых систем",
                "source_detail": "Яндекс",
                "source_detail_id": "organic.Яндекс",
                "utm_source": "",
            }
            self.assertEqual(helper._legacy_metrika_source(row), original(row))
        finally:
            m._metrika_source = original

    def test_geoadv_search_yabs_zip_moves_visits_and_goals_without_changing_totals(self):
        parsed = m._parse_metrika_zip(
            metrika_geoadv_search_yabs_zip(),
            "geoadv-search-yabs.zip",
            now=datetime(2026,11,10,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )

        traffic = {
            key: values["visits"]
            for key, values in parsed["traffic_rows"].items()
        }
        goals = parsed["goal_rows"]

        business_rows = [
            (key, visits)
            for key, visits in traffic.items()
            if key[1] == "yandex_business"
        ]
        self.assertEqual(len(business_rows), 1)
        business_key, business_visits = business_rows[0]
        self.assertEqual(business_visits, 11)
        self.assertEqual(business_key[4], "44320327528")
        self.assertEqual(business_key[5], "")

        self.assertEqual(
            sum(traffic.values()),
            19,
        )
        self.assertEqual(
            sum(values["goal_visits"] for values in goals.values()),
            4,
        )
        self.assertEqual(
            sum(values["reaches"] for values in goals.values()),
            5,
        )

        business_goals = {
            key[6]: values
            for key, values in goals.items()
            if key[1] == "yandex_business"
        }
        self.assertEqual(
            business_goals["booking_open"],
            {"goal_visits": 2, "reaches": 3},
        )
        self.assertEqual(
            business_goals["medflex"],
            {"goal_visits": 1, "reaches": 1},
        )

        direct_visits = sum(
            visits for key, visits in traffic.items()
            if key[1] == "yandex_ads"
        )
        self.assertEqual(direct_visits, 5)
        self.assertFalse(
            any(
                key[1] == "yandex_ads" and key[4] == "44320327528"
                for key in traffic
            )
        )

    def test_geoadv_historical_reclassification_is_idempotent_and_preserves_totals(self):
        raw = metrika_geoadv_search_yabs_zip()
        parsed = m._parse_metrika_zip(
            raw,
            "geoadv-search-yabs.zip",
            now=datetime(2026,11,10,12,0,tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )
        sha = parsed["payload_sha256"]

        with tempfile.TemporaryDirectory() as tmp:
            old_root = m.METRIKA_ARCHIVE_ROOT
            m.METRIKA_ARCHIVE_ROOT = Path(tmp)
            c = conn()
            try:
                imported_at = "2026-11-09T23:15:00+00:00"
                c.execute(
                    """
                    INSERT INTO advertising_metrika_imports(
                        payload_sha256,original_filename,period_start,period_end,
                        completed_days_json,generated_at,imported_at,actor_user_id,
                        traffic_rows,goal_rows,archive_path
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        sha, "geoadv-search-yabs.zip", "2026-11-09", "2026-11-09",
                        json.dumps(["2026-11-09"]), "2026-11-09T23:10:00+00:00",
                        imported_at, 1, 3, 3, str(Path(tmp) / (sha + ".zip")),
                    ),
                )
                old_rows = [
                    ("yandex_ads", "Яндекс-реклама", "Платная реклама", "44320327528", "", 11),
                    ("yandex_ads", "Яндекс-реклама", "Платная реклама", "715560815",
                     "АЗ | Ортодонт и гнатолог | Поиск | Новосибирск", 5),
                    (
                        m._dynamic_source_identity(
                            "paid", "future_paid_network", "Платная реклама", "Платная реклама"
                        )[0],
                        "future_paid_network", "Платная реклама", "", "", 3,
                    ),
                ]
                for source_key, source_name, channel, campaign_id, campaign_name, visits in old_rows:
                    c.execute(
                        """
                        INSERT INTO advertising_traffic_daily(
                            provider,metric_date,source_key,source_name,channel_name,
                            campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id
                        ) VALUES('metrika','2026-11-09',?,?,?,?,?,NULL,NULL,?,?,?)
                        """,
                        (
                            source_key, source_name, channel, campaign_id, campaign_name,
                            visits, imported_at, "metrika-" + sha[:16],
                        ),
                    )
                old_goals = [
                    ("yandex_ads", "Яндекс-реклама", "44320327528", "", "booking_open",
                     "Открытие окна записи", 2, 3),
                    ("yandex_ads", "Яндекс-реклама", "44320327528", "", "medflex",
                     "Переход к записи в Medflex", 1, 1),
                    ("yandex_ads", "Яндекс-реклама", "715560815",
                     "АЗ | Ортодонт и гнатолог | Поиск | Новосибирск", "booking_open",
                     "Открытие окна записи", 1, 1),
                ]
                for source_key, source_name, campaign_id, campaign_name, goal_key, goal_name, goal_visits, reaches in old_goals:
                    c.execute(
                        """
                        INSERT INTO advertising_metrika_goal_daily(
                            provider,metric_date,source_key,source_name,channel_name,
                            campaign_id,campaign_name,goal_key,goal_name,goal_visits,reaches,
                            updated_at,import_id
                        ) VALUES('metrika','2026-11-09',?,?,'Платная реклама',?,?,?,?,?,?,?,?)
                        """,
                        (
                            source_key, source_name, campaign_id, campaign_name,
                            goal_key, goal_name, goal_visits, reaches, imported_at,
                            "metrika-" + sha[:16],
                        ),
                    )
                c.commit()

                before_visits = c.execute(
                    "SELECT SUM(visits) FROM advertising_traffic_daily WHERE provider='metrika' AND metric_date='2026-11-09'"
                ).fetchone()[0]
                before_goals = {
                    row["goal_key"]: (row["goal_visits"], row["reaches"])
                    for row in c.execute(
                        """
                        SELECT goal_key,SUM(goal_visits) AS goal_visits,SUM(reaches) AS reaches
                        FROM advertising_metrika_goal_daily
                        WHERE provider='metrika' AND metric_date='2026-11-09'
                        GROUP BY goal_key ORDER BY goal_key
                        """
                    ).fetchall()
                }

                first = m._store_metrika(c, parsed, 1)
                c.commit()
                self.assertTrue(first["reclassified"])
                self.assertFalse(first["reused"])

                after_visits = c.execute(
                    "SELECT SUM(visits) FROM advertising_traffic_daily WHERE provider='metrika' AND metric_date='2026-11-09'"
                ).fetchone()[0]
                after_goals = {
                    row["goal_key"]: (row["goal_visits"], row["reaches"])
                    for row in c.execute(
                        """
                        SELECT goal_key,SUM(goal_visits) AS goal_visits,SUM(reaches) AS reaches
                        FROM advertising_metrika_goal_daily
                        WHERE provider='metrika' AND metric_date='2026-11-09'
                        GROUP BY goal_key ORDER BY goal_key
                        """
                    ).fetchall()
                }
                self.assertEqual(before_visits, after_visits)
                self.assertEqual(before_goals, after_goals)

                business = c.execute(
                    """
                    SELECT SUM(visits) AS visits
                    FROM advertising_traffic_daily
                    WHERE provider='metrika' AND metric_date='2026-11-09'
                      AND source_key='yandex_business'
                    """
                ).fetchone()
                self.assertEqual(business["visits"], 11)

                second = m._store_metrika(c, parsed, 1)
                c.commit()
                self.assertTrue(second["reused"])
                self.assertFalse(second["reclassified"])
                self.assertEqual(
                    c.execute(
                        "SELECT SUM(visits) FROM advertising_traffic_daily WHERE provider='metrika' AND metric_date='2026-11-09'"
                    ).fetchone()[0],
                    after_visits,
                )
            finally:
                c.close()
                m.METRIKA_ARCHIVE_ROOT = old_root

    def test_arbitrary_multi_day_range_sums_only_available_days(self):
        c=conn()
        try:
            seed(c)
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-10-03","direct","Прямые заходы","Прямые заходы","","",None,None,20,"x","m3"),
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
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
