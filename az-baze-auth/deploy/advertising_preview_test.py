#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

EXPECTED_HOST = "az-server"
LIVE_SERVICE = "az-baze-auth"
PREVIEW_SERVICE = "az-advertising-preview"
PREVIEW_PORT = 8003
PREVIEW_ROOT = Path("/opt/az-advertising-preview")
PREVIEW_UNIT = Path("/etc/systemd/system/az-advertising-preview.service")
SOURCE_HEAD = "664c1a17fe71c9d801d587eb1ba6b8ab89916aad"
APPLY_TOKEN = "ADVERTISING-PREVIEW-B2527918"
UPDATE_TOKEN = "ADVERTISING-PREVIEW-UI3-664C1A17"
HTML_BLOB = "9256fe0f8dcb6a190f5b8f85839eca5766d32c1e"
JS_BLOB = "e7d4ba225c0c194d3126fa9c2d5a5f75c906abc7"
RAW_BASE = "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/" + SOURCE_HEAD + "/reports/"
HTML_PATH = PREVIEW_ROOT / "advertising.html"
JS_PATH = PREVIEW_ROOT / "advertising.js"
SELF_PATH = PREVIEW_ROOT / "advertising_preview_test.py"

def git_blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()

def run(cmd, check=True):
    p = subprocess.run(cmd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError("command failed: " + " ".join(cmd) + "\n" + p.stdout + "\n" + p.stderr)
    return p

def service_active(name):
    return run(["systemctl", "is-active", "--quiet", name], check=False).returncode == 0

def port_free():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", PREVIEW_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()

def preflight(require_clean=False):
    if socket.gethostname().split(".")[0] != EXPECTED_HOST:
        raise RuntimeError("STOP: WRONG_HOST")
    if os.geteuid() != 0:
        raise RuntimeError("STOP: ROOT_REQUIRED")
    if not service_active(LIVE_SERVICE):
        raise RuntimeError("STOP: LIVE_SERVICE_NOT_ACTIVE")
    if service_active(PREVIEW_SERVICE):
        raise RuntimeError("STOP: PREVIEW_SERVICE_ALREADY_ACTIVE")
    if not port_free():
        raise RuntimeError("STOP: PREVIEW_PORT_BUSY")
    existing = [str(x) for x in (PREVIEW_ROOT, PREVIEW_UNIT) if x.exists()]
    if require_clean and existing:
        raise RuntimeError("STOP: PREVIEW_PATHS_EXIST=" + ",".join(existing))
    print("ADVERTISING_PREVIEW_PREFLIGHT=PASS")
    print("SOURCE_HEAD=" + SOURCE_HEAD)
    print("LIVE_SERVICE=active")
    print("PREVIEW_BIND=127.0.0.1:%d" % PREVIEW_PORT)
    print("PRODUCTION_DB_WRITES=NONE; PRODUCTION_FILES=NONE; NGINX=NONE; DNS=NONE")
    return True

def fetch_exact(name, expected_blob):
    url = RAW_BASE + name
    with urllib.request.urlopen(url, timeout=30) as r:
        raw = r.read()
    actual = git_blob(raw)
    if actual != expected_blob:
        raise RuntimeError("STOP: SOURCE_BLOB_MISMATCH " + name + " " + actual)
    return raw

def render_unit():
    return f"""[Unit]
Description=AZ advertising report private preview
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory={PREVIEW_ROOT}
ExecStart=/usr/bin/python3 {SELF_PATH} --serve
Restart=on-failure
RestartSec=2
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
IPAddressDeny=any
IPAddressAllow=localhost
ReadOnlyPaths={PREVIEW_ROOT}

[Install]
WantedBy=multi-user.target
"""

def cleanup():
    run(["systemctl", "disable", "--now", PREVIEW_SERVICE], check=False)
    if PREVIEW_UNIT.exists():
        PREVIEW_UNIT.unlink(missing_ok=True)
        run(["systemctl", "daemon-reload"], check=False)
    if PREVIEW_ROOT.exists():
        shutil.rmtree(PREVIEW_ROOT)

def apply():
    preflight(require_clean=True)
    html = fetch_exact("advertising.html", HTML_BLOB)
    js = fetch_exact("advertising.js", JS_BLOB)
    try:
        PREVIEW_ROOT.mkdir(parents=True, mode=0o755)
        HTML_PATH.write_bytes(html)
        JS_PATH.write_bytes(js)
        shutil.copy2(Path(__file__).resolve(), SELF_PATH)
        for p in (HTML_PATH, JS_PATH, SELF_PATH):
            os.chmod(p, 0o644)
        PREVIEW_UNIT.write_text(render_unit(), encoding="utf-8")
        os.chmod(PREVIEW_UNIT, 0o644)
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "--now", PREVIEW_SERVICE])
        ok = False
        for _ in range(30):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/health" % PREVIEW_PORT, timeout=2) as r:
                    ok = r.status == 200 and r.read().decode().strip() == "ok"
            except Exception:
                ok = False
            if ok:
                break
            time.sleep(0.25)
        if not ok:
            raise RuntimeError("preview health failed")
        print("ADVERTISING_PREVIEW_APPLY=PASS")
        print("SOURCE_HEAD=" + SOURCE_HEAD)
        print("HTML_BLOB=" + HTML_BLOB)
        print("JS_BLOB=" + JS_BLOB)
        print("SERVICE=" + PREVIEW_SERVICE + "; ACTIVE=YES")
        print("PREVIEW_URL_VIA_TUNNEL=http://127.0.0.1:18003/reports/advertising/")
        print("LOCAL_TUNNEL=ssh -N -L 18003:127.0.0.1:8003 root@62.113.98.64")
        print("DATA=SYNTHETIC_ONLY; PRODUCTION_DB_WRITES=NONE; PRODUCTION_FILES=NONE; NGINX=NONE; DNS=NONE")
    except Exception:
        cleanup()
        raise


def update_preflight():
    if socket.gethostname().split(".")[0] != EXPECTED_HOST:
        raise RuntimeError("STOP: WRONG_HOST")
    if os.geteuid() != 0:
        raise RuntimeError("STOP: ROOT_REQUIRED")
    if not service_active(LIVE_SERVICE):
        raise RuntimeError("STOP: LIVE_SERVICE_NOT_ACTIVE")
    if not service_active(PREVIEW_SERVICE):
        raise RuntimeError("STOP: PREVIEW_SERVICE_NOT_ACTIVE")
    for path in (PREVIEW_ROOT, PREVIEW_UNIT, HTML_PATH, JS_PATH, SELF_PATH):
        if not path.exists():
            raise RuntimeError("STOP: PREVIEW_PATH_MISSING=" + str(path))
    print("ADVERTISING_PREVIEW_UPDATE_PREFLIGHT=PASS")
    print("SOURCE_HEAD=" + SOURCE_HEAD)
    print("LIVE_SERVICE=active")
    print("PREVIEW_SERVICE=active")
    print("PRODUCTION_DB_WRITES=NONE; PRODUCTION_FILES=NONE; NGINX=NONE; DNS=NONE")

def update():
    update_preflight()
    html = fetch_exact("advertising.html", HTML_BLOB)
    js = fetch_exact("advertising.js", JS_BLOB)
    backups = {
        HTML_PATH: HTML_PATH.read_bytes(),
        JS_PATH: JS_PATH.read_bytes(),
        SELF_PATH: SELF_PATH.read_bytes(),
        PREVIEW_UNIT: PREVIEW_UNIT.read_bytes(),
    }
    try:
        run(["systemctl", "stop", PREVIEW_SERVICE])
        HTML_PATH.write_bytes(html)
        JS_PATH.write_bytes(js)
        shutil.copy2(Path(__file__).resolve(), SELF_PATH)
        PREVIEW_UNIT.write_text(render_unit(), encoding="utf-8")
        for path in (HTML_PATH, JS_PATH, SELF_PATH):
            os.chmod(path, 0o644)
        os.chmod(PREVIEW_UNIT, 0o644)
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "start", PREVIEW_SERVICE])
        ok = False
        for _ in range(30):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/health" % PREVIEW_PORT, timeout=2) as r:
                    ok = r.status == 200 and r.read().decode().strip() == "ok"
            except Exception:
                ok = False
            if ok:
                break
            time.sleep(0.25)
        if not ok:
            raise RuntimeError("preview update health failed")
        print("ADVERTISING_PREVIEW_UPDATE=PASS")
        print("SOURCE_HEAD=" + SOURCE_HEAD)
        print("HTML_BLOB=" + HTML_BLOB)
        print("JS_BLOB=" + JS_BLOB)
        print("SERVICE=" + PREVIEW_SERVICE + "; ACTIVE=YES")
        print("DATA=SYNTHETIC_ONLY; PRODUCTION_DB_WRITES=NONE; PRODUCTION_FILES=NONE; NGINX=NONE; DNS=NONE")
    except Exception:
        run(["systemctl", "stop", PREVIEW_SERVICE], check=False)
        for path, raw in backups.items():
            path.write_bytes(raw)
        os.chmod(PREVIEW_UNIT, 0o644)
        for path in (HTML_PATH, JS_PATH, SELF_PATH):
            os.chmod(path, 0o644)
        run(["systemctl", "daemon-reload"], check=False)
        run(["systemctl", "start", PREVIEW_SERVICE], check=False)
        raise

def scale(v, factor):
    if v is None:
        return None
    if isinstance(v, int):
        return int(round(v * factor))
    return round(v * factor, 2)

def comparison_label(mode):
    return {
        "previous_period": "Предыдущий сопоставимый период",
        "previous_quarter_avg": "Среднее предыдущего квартала",
        "year_avg": "Среднее текущего года",
        "last_year": "Тот же период прошлого года",
        "none": "Без сравнения",
    }.get(mode, "Предыдущий сопоставимый период")

def pct_delta(a, b):
    if a is None or b in (None, 0):
        return None
    return round((a - b) * 100.0 / b, 1)

BASE = {
    "spend": 412750,
    "clicks": 3860,
    "visits": 8420,
    "target_leads": 142,
    "leads_with_primary": 92,
    "primary_visits": 108,
    "primary_paid": 2848460,
    "primary_services": 3021000,
    "repeat_visits": 681,
    "repeat_paid": 2763840,
    "new_patients": 84,
    "new_patient_30d_paid": 4170000,
}
PREV = {
    "spend": 503620,
    "clicks": 3446,
    "visits": 7725,
    "target_leads": 117,
    "leads_with_primary": 78,
    "primary_visits": 96,
    "primary_paid": 2499000,
    "primary_services": 2620000,
    "repeat_visits": 620,
    "repeat_paid": 2450000,
    "new_patients": 76,
    "new_patient_30d_paid": 3780000,
}

SOURCE_ROWS = [
    ("yandex_ads", "Реклама Яндекса", "Платная реклама", 412750, 3860, 3100, 76, 48, 55, 1350000, 1450000, 180, 750000, 42, 1820000),
    ("yandex_search", "Яндекс Поиск", "Поиск", None, None, 1700, 20, 14, 16, 440000, 465000, 110, 470000, 12, 570000),
    ("google_search", "Google Поиск", "Поиск", None, None, 700, 8, 5, 6, 180000, 190000, 50, 220000, 5, 250000),
    ("yandex_maps", "Яндекс Карты", "Карты", None, None, 850, 11, 8, 9, 260000, 280000, 75, 320000, 7, 390000),
    ("2gis", "2ГИС", "Карты", None, None, 650, 9, 6, 7, 180000, 190000, 56, 270000, 6, 320000),
    ("direct", "Прямые заходы", "Прямые заходы", None, None, 820, 10, 6, 7, 198460, 210000, 90, 390000, 5, 350000),
    ("recommendations", "Рекомендации", "Другие источники", None, None, 360, 5, 3, 3, 150000, 158000, 70, 205000, 4, 270000),
    ("referrals", "Переходы с других сайтов", "Другие источники", None, None, 240, 3, 2, 2, 90000, 92000, 50, 138840, 3, 200000),
]
CAMPAIGNS = [
    ("1", "Ортодонтия", 118000, 1180, 28, 18, 21, 510000, 780000),
    ("2", "Стоматология", 96000, 860, 21, 14, 16, 420000, 570000),
    ("3", "Миотерапия", 74000, 650, 13, 8, 9, 270000, 360000),
    ("4", "Общая медицина", 68000, 610, 9, 5, 6, 110000, 220000),
    ("5", "Брендовая", 56750, 560, 5, 3, 3, 40000, 170000),
]

def make_summary(src, factor):
    out = {k: scale(v, factor) for k, v in src.items()}
    out["total_visits"] = (out["primary_visits"] or 0) + (out["repeat_visits"] or 0)
    out["total_paid"] = (out["primary_paid"] or 0) + (out["repeat_paid"] or 0)
    out["lead_to_primary_pct"] = round(out["leads_with_primary"] * 100.0 / out["target_leads"], 1) if out["target_leads"] else None
    return out

def make_sources(factor):
    rows = []
    for x in SOURCE_ROWS:
        key,name,channel,spend,clicks,visits,leads,converted,primary,ppaid,pservices,repeat,rpaid,newp,cpaid=x
        item = {
            "source_key": key, "source_name": name, "channel_name": channel,
            "campaign_id": "", "campaign_name": "",
            "spend": scale(spend,factor), "clicks": scale(clicks,factor), "visits": scale(visits,factor),
            "target_leads": scale(leads,factor), "leads_with_primary": scale(converted,factor),
            "primary_visits": scale(primary,factor), "primary_paid": scale(ppaid,factor),
            "primary_services": scale(pservices,factor), "repeat_visits": scale(repeat,factor),
            "repeat_paid": scale(rpaid,factor), "new_patients": scale(newp,factor),
            "new_patient_30d_paid": scale(cpaid,factor),
        }
        item["total_visits"]=(item["primary_visits"] or 0)+(item["repeat_visits"] or 0)
        item["total_paid"]=(item["primary_paid"] or 0)+(item["repeat_paid"] or 0)
        item["lead_to_primary_pct"]=round(item["leads_with_primary"]*100/item["target_leads"],1) if item["target_leads"] else None
        rows.append(item)
    return rows

def make_campaigns(factor):
    rows=[]
    for cid,name,spend,clicks,leads,converted,primary,ppaid,total in CAMPAIGNS:
        rows.append({
            "source_key":"yandex_ads","source_name":"Реклама Яндекса","channel_name":"Платная реклама",
            "campaign_id":cid,"campaign_name":name,"spend":scale(spend,factor),"clicks":scale(clicks,factor),
            "visits":None,"target_leads":scale(leads,factor),"leads_with_primary":scale(converted,factor),
            "primary_visits":scale(primary,factor),"primary_paid":scale(ppaid,factor),"primary_services":None,
            "repeat_visits":None,"repeat_paid":None,"new_patients":None,"new_patient_30d_paid":None,
            "total_visits":None,"total_paid":scale(total,factor),
            "lead_to_primary_pct":round(converted*100/leads,1) if leads else None,
        })
    return rows

def trend(total, days, multiplier=1.0):
    result=[]
    avg=(total or 0)/max(days,1)
    for i in range(days):
        wave=1+0.18*math.sin((i+1)*0.8)+0.07*math.cos((i+1)*0.37)
        result.append(max(0,avg*wave*multiplier))
    return result

def build_payload(query):
    today=date(2026,10,26)
    try:
        start=date.fromisoformat(query.get("from",["2026-10-01"])[0])
        end=date.fromisoformat(query.get("to",[today.isoformat()])[0])
    except Exception:
        start=date(2026,10,1); end=today
    if end < start:
        start,end=end,start
    days=(end-start).days+1
    factor=max(0.08,days/26.0)
    compare_mode=query.get("compare",["previous_period"])[0]
    metric=query.get("trend",["target_leads"])[0]
    current=make_summary(BASE,factor)
    previous=make_summary(PREV,factor)
    delta={k:pct_delta(current.get(k),previous.get(k)) for k in current if k!="new_patient_30d_paid"}
    metric_key={"spend":"spend","visits":"visits","target_leads":"target_leads","primary_visits":"primary_visits","total_paid":"total_paid"}.get(metric,"target_leads")
    cur_series=trend(current.get(metric_key) or 0,days,1.0)
    prev_series=trend(previous.get(metric_key) or 0,days,0.97)
    trend_rows={"metric":metric,"current":[],"compare":[]}
    for i in range(days):
        d=start+timedelta(days=i)
        trend_rows["current"].append({"point":i+1,"date":d.isoformat(),"value":round(cur_series[i],2)})
        trend_rows["compare"].append({"point":i+1,"date":"","value":round(prev_series[i],2)})
    return {
        "period":{"from":start.isoformat(),"to":end.isoformat()},
        "compare":{"mode":compare_mode,"label":comparison_label(compare_mode),"ranges":[],"summary":previous,"delta_pct":delta},
        "summary":current,
        "activity":{"primary_visits":current["primary_visits"],"primary_paid":current["primary_paid"],"repeat_visits":current["repeat_visits"],"repeat_paid":current["repeat_paid"],"total_visits":current["total_visits"],"total_paid":current["total_paid"]},
        "sources":make_sources(factor),"campaigns":make_campaigns(factor),"trend":trend_rows,
        "freshness":[
            {"dataset":"direct","label":"Реклама","generated_at":"2026-10-26T18:30:00+07:00","imported_at":"2026-10-26T18:31:00+07:00"},
            {"dataset":"metrika","label":"Посещения сайта","generated_at":"2026-10-26T18:32:00+07:00","imported_at":"2026-10-26T18:33:00+07:00"},
            {"dataset":"clinic","label":"CRM и клиника","generated_at":"2026-10-26T18:35:00+07:00","imported_at":"2026-10-26T18:36:00+07:00"},
        ],
        "today":today.isoformat(),
        "availableComparisons":["previous_period","previous_quarter_avg","year_avg","last_year"],
        "preview":True,
    }

class Handler(BaseHTTPRequestHandler):
    server_version="AZAdvertisingPreview/1.0"
    def log_message(self, fmt, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))
    def send_bytes(self, code, raw, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-AZ-BAZE-Contour","advertising-preview")
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        parsed=urllib.parse.urlparse(self.path)
        if parsed.path=="/health":
            return self.send_bytes(200,b"ok\n","text/plain; charset=utf-8")
        if parsed.path in ("/","/reports","/reports/"):
            self.send_response(302); self.send_header("Location","/reports/advertising/"); self.end_headers(); return
        if parsed.path=="/reports/advertising/":
            html=HTML_PATH.read_text(encoding="utf-8")
            banner='<div style="position:sticky;top:0;z-index:99999;background:#44634f;color:white;border-bottom:3px solid #b59662;padding:8px;text-align:center;font:700 11px Arial">ПРИВАТНЫЙ PREVIEW — только вымышленные данные, Production не затронут</div>'
            html=html.replace("<body>","<body>"+banner,1)
            return self.send_bytes(200,html.encode("utf-8"),"text/html; charset=utf-8")
        if parsed.path=="/reports/advertising.js":
            return self.send_bytes(200,JS_PATH.read_bytes(),"application/javascript; charset=utf-8")
        if parsed.path=="/api/reports/advertising":
            data=build_payload(urllib.parse.parse_qs(parsed.query))
            return self.send_bytes(200,json.dumps(data,ensure_ascii=False,separators=(",",":")).encode("utf-8"),"application/json; charset=utf-8")
        return self.send_bytes(404,b"not found\n","text/plain; charset=utf-8")

def serve():
    if Path(__file__).resolve()!=SELF_PATH.resolve():
        pass
    httpd=ThreadingHTTPServer(("127.0.0.1",PREVIEW_PORT),Handler)
    httpd.serve_forever()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--preflight",action="store_true")
    p.add_argument("--apply",default="")
    p.add_argument("--serve",action="store_true")
    p.add_argument("--update",default="")
    args=p.parse_args()
    if args.serve:
        serve(); return 0
    if args.preflight:
        preflight(False); return 0
    if args.apply:
        if args.apply!=APPLY_TOKEN:
            raise RuntimeError("STOP: CONFIRM_TOKEN_MISMATCH")
        apply(); return 0
    if args.update:
        if args.update!=UPDATE_TOKEN:
            raise RuntimeError("STOP: UPDATE_TOKEN_MISMATCH")
        update(); return 0
    p.error("choose --preflight, --apply TOKEN, --update TOKEN or --serve")

if __name__=="__main__":
    raise SystemExit(main())
