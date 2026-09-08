import base64
import hashlib
import hmac
import json
import os
import zlib
from pathlib import Path

from flask import Response, jsonify

from app import SITE_ROOT, csrf_token, db, permission_required

FINREZ_DATA_PATH = Path(os.environ.get("AZ_FINREZ_DATA_PATH", "/var/lib/az-baze/finrez-data.enc"))
FINREZ_KEY_PATH = Path(os.environ.get("AZ_FINREZ_KEY_PATH", "/var/lib/az-baze/finrez.key"))


def _xor_hmac_stream(data, key, nonce):
    out = bytearray(len(data))
    for block_index in range((len(data) + 31) // 32):
        stream = hmac.new(
            key,
            nonce + block_index.to_bytes(8, "big"),
            hashlib.sha256,
        ).digest()
        start = block_index * 32
        block = data[start : start + 32]
        for index, value in enumerate(block):
            out[start + index] = value ^ stream[index]
    return bytes(out)


def _load_private_data():
    if not FINREZ_DATA_PATH.exists() or not FINREZ_KEY_PATH.exists():
        return {
            "version": 1,
            "source": "",
            "period": {},
            "meta": {},
            "months": {},
            "available": False,
        }

    master = base64.urlsafe_b64decode(FINREZ_KEY_PATH.read_text(encoding="utf-8").strip())
    text = FINREZ_DATA_PATH.read_text(encoding="utf-8").strip()
    if not text.startswith("AZFIN1."):
        raise ValueError("invalid finrez data format")
    packed = base64.urlsafe_b64decode(text.split(".", 1)[1])
    if len(packed) < 48:
        raise ValueError("invalid finrez data")

    nonce = packed[:16]
    tag = packed[-32:]
    cipher = packed[16:-32]
    enc_key = hmac.new(master, b"az-finrez-enc-v1", hashlib.sha256).digest()
    mac_key = hmac.new(master, b"az-finrez-mac-v1", hashlib.sha256).digest()
    expected = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("finrez data authentication failed")

    compressed = _xor_hmac_stream(cipher, enc_key, nonce)
    payload = json.loads(zlib.decompress(compressed).decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("invalid finrez payload")
    payload["available"] = True
    return payload


def _management_months():
    rows = db().execute("SELECT date, payload FROM report_data ORDER BY date").fetchall()
    latest = {}
    for row in rows:
        date = str(row["date"] or "")
        if len(date) < 7:
            continue
        month = date[:7]
        try:
            record = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        current = latest.get(month)
        if current is None or date > current["date"]:
            latest[month] = {"date": date, "record": record}

    result = {}
    for month, entry in latest.items():
        record = entry["record"]
        result[month] = {
            "date": entry["date"],
            "plan": record.get("plan", ""),
            "factMedicine": record.get("factMedicine", 0),
            "factLab": record.get("factLab", record.get("labRevenue", 0)),
            "dentPrimary": record.get("dentPrimary", 0),
            "dentRepeat": record.get("dentRepeat", 0),
            "clinicPrimary": record.get("clinicPrimary", 0),
            "clinicRepeat": record.get("clinicRepeat", 0),
            "dentists": record.get("dentists") if isinstance(record.get("dentists"), dict) else {},
            "structureDoctors": record.get("clinicDocs") if isinstance(record.get("clinicDocs"), dict) else {},
        }
    return result


def _inject_script(html, filename, version):
    marker = f'src="/reports/{filename}'
    if marker in html:
        return html
    return html.replace(
        "</body>",
        f'<script src="/reports/{filename}?v={version}"></script>\n</body>',
        1,
    )


def register_finrez(app):
    @app.get("/reports/finrez/")
    @permission_required("reports")
    def finrez_page():
        path = SITE_ROOT / "reports" / "finrez.html"
        html = path.read_text(encoding="utf-8")
        html = html.replace(
            "MAIN_ORDER.map((name,index)=>expenseCategoryNode(year,name,index)).filter(n=>hasAny(n.values))",
            "MAIN_ORDER.map((name,index)=>expenseCategoryNode(year,name,index))",
        )
        html = _inject_script(html, "finrez-economics-integrated.js", "20260908-3")
        return Response(html, mimetype="text/html")

    @app.get("/reports/forecast/")
    @permission_required("reports")
    def forecast_page():
        path = SITE_ROOT / "reports" / "forecast.html"
        html = path.read_text(encoding="utf-8")
        html = _inject_script(html, "forecast-audit-sync.js", "20260908-2")
        return Response(html, mimetype="text/html")

    @app.get("/api/reports/finrez")
    @permission_required("reports")
    def finrez_data():
        try:
            expenses = _load_private_data()
            private_error = ""
        except Exception as error:
            expenses = {
                "version": 1,
                "source": "",
                "period": {},
                "meta": {},
                "months": {},
                "available": False,
            }
            private_error = str(error)
        return jsonify(
            expenses=expenses,
            management=_management_months(),
            privateError=private_error,
            csrf=csrf_token(),
        )
