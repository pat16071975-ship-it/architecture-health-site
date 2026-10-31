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



def auto_metrika_request(path, params):
    if path == "/management/v1/counter/113776005":
        return {"counter":{"id":113776005,"time_zone_name":"Asia/Novosibirsk","permission":"own"}}
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
            elif metric=="ym:s:bounceRate": values.append(0.0)
            elif metric=="ym:s:avgVisitDurationSeconds": values.append(30.0)
            elif metric=="ym:s:goal674318079reaches": values.append(float(booking[0]))
            elif metric=="ym:s:goal674318079visits": values.append(float(booking[1]))
            elif metric=="ym:s:goal674318216reaches": values.append(float(medflex[0]))
            elif metric=="ym:s:goal674318216visits": values.append(float(medflex[1]))
            elif metric.startswith("ym:s:goal") and (
                metric.endswith("reaches") or metric.endswith("visits")
            ): values.append(0.0)
            else: raise AssertionError(metric)
        return values

    if counter_id in ma.MEDFLEX_COUNTERS:
        return {
            "query":{"ids":[int(counter_id)],"dimensions":dimensions,"metrics":metrics,"date1":params["date1"],"date2":params["date2"]},
            "data":[],
            "total_rows":0,
            "total_rows_rounded":False,
            "sampled":False,
            "sample_share":1.0,
            "totals":[0.0 for _ in metrics],
        }

    if dimensions == [ma.CAMPAIGN_DIMENSION]:
        data=[
            {"dimensions":[{"id":"715560815","name":"АЗ | Ортодонт и гнатолог | Поиск | Новосибирск"}],
             "metrics":metric_values(5,booking=(2,2),medflex=(1,1))},
            {"dimensions":[{"id":"715444498","name":"АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск"}],
             "metrics":metric_values(1)},
            {"dimensions":[{"id":"999999999","name":"Новая неизвестная кампания"}],
             "metrics":metric_values(2)},
            {"dimensions":[{"id":"other","name":"Чужие кампании"}],
             "metrics":metric_values(1)},
        ]
        totals=metric_values(9,booking=(2,2),medflex=(1,1))
    elif tuple(dimensions)==ma.TRAFFIC_DIMENSIONS:
        day=str(params["date1"])
        def dims(source_id,source_name,detail_id=None,detail=None,utm_source=None,utm_medium=None,utm_campaign=None,utm_content=None,utm_term=None):
            return [
                {"name":day},
                {"id":source_id,"name":source_name},
                {"id":detail_id,"name":detail},
                {"name":utm_source},
                {"name":utm_medium},
                {"name":utm_campaign},
                {"name":utm_content},
                {"name":utm_term},
            ]
        data=[
            {"dimensions":dims("ad","Переходы по рекламе","ad.Яндекс: Директ","Яндекс: Директ"),
             "metrics":metric_values(9,booking=(2,2),medflex=(1,1))},
            {"dimensions":dims("social","Переходы из социальных сетей","social.instagram","instagram.com","instagram","social","starostenko","profile"),
             "metrics":metric_values(3,booking=(1,1))},
            {"dimensions":dims("messenger","Переходы из мессенджеров","messenger.telegram","Telegram","telegram","social","metrika_test"),
             "metrics":metric_values(2)},
        ]
        totals=metric_values(14,booking=(3,3),medflex=(1,1))
    else:
        data=[{
            "dimensions":[{"name":str(params["date1"]) if dimension.endswith(":date") else "Тест"} for dimension in dimensions],
            "metrics":metric_values(1),
        }]
        totals=metric_values(1)

    return {
        "query":{"ids":[113776005],"dimensions":dimensions,"metrics":metrics,"date1":params["date1"],"date2":params["date2"]},
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
    if path == "/management/v1/counter/113776005":
        return {"counter":{"id":113776005,"time_zone_name":"Asia/Novosibirsk","permission":"own"}}
    if path == "/management/v1/counter/113776005/goals":
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


class AdvertisingReportTests(unittest.TestCase):
    def test_auto_metrika_missing_days_starts_after_latest_and_stops_at_yesterday(self):
        c=conn()
        try:
            c.execute(
                "INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
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
                "INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
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
            direct={
                str(row.get("campaign_id") or ""):row
                for row in az_payload["direct_campaign_day"]
            }
            self.assertEqual(direct["715560815"]["visits"],5)
            self.assertEqual(direct["715444498"]["visits"],1)
            self.assertEqual(direct["999999999"]["campaign_name"],"Новая неизвестная кампания")
            self.assertEqual(direct["999999999"]["visits"],2)
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
        self.assertIn(
            'if _metrika_sync_has_changes(completed_updated, current_result):\n            audit(',
            source,
        )

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
        self.assertEqual(calls[0][1]["limit"],100000)

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
                    "INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
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
                        "INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
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
                        "INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
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
                ("715444498","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",6),
                ("999999999","АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",4),
            ],
        )
        self.assertEqual(sum(row[2] for row in yandex),10)

    def test_group_breakdown_keeps_two_distinct_nonempty_ids_with_same_name(self):
        c=conn()
        try:
            name="АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск"
            for campaign_id,visits in (("715444498",5),("999999999",4)):
                c.execute(
                    "INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    "INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
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
            self.assertEqual(by_id["999999999"]["campaign_name"],"Не определено")
            self.assertEqual(by_id["888888888"]["campaign_name"],"Не определено")
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
                self.assertEqual(by_id["999999999"]["campaign_name"],"Не определено")
                self.assertEqual(by_id["999999999"]["visits"],4)
                self.assertEqual(sum(row["visits"] for row in rows),10)
            finally:
                c.close(); m.METRIKA_ARCHIVE_ROOT=old

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
        self.assertIn(
            (
                "999999999",
                "АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",
                4,
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

    def test_yandex_campaign_breakdown_keeps_named_and_undefined(self):
        c=conn()
        try:
            c.execute(
                "INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("metrika","2026-10-29","yandex_ads","Яндекс-реклама","Платная реклама",
                 "101","Ортодонтия",None,None,20,"t","named"),
            )
            c.execute(
                "INSERT INTO advertising_traffic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
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
        self.assertIn("['Отделение структуры',directions.structure]", js)
        self.assertNotIn("['Клиника',directions.structure]", js)
        terminology_source=(Path(__file__).parent/"terminology.py").read_text(encoding="utf-8")
        self.assertIn("skip_runtime_rename(path)", terminology_source)
        self.assertIn("all('.chip').forEach", js)
        self.assertNotIn("$('.chip').forEach", js)
        self.assertIn("function campaignRowsForSource(sourceKey)", js)
        self.assertIn("sourceKey==='yandex_ads'||sourceKey==='instagram'", js)
        self.assertIn("class=\"source-expand\"", js)
        self.assertIn("class=\"child-row source-campaign-row\"", js)
        self.assertIn("button.textContent=expanded?'+':'−'", js)
        self.assertIn("return String(row.campaign_name||'').trim()||'Не определено'", js)
        self.assertIn(".source-expand{", html)

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
