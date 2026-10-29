import json
import math
import re
import secrets
import sqlite3
from datetime import date as calendar_date, timedelta
from flask import Response, abort, g, jsonify, request, session

from app import DB_PATH, SITE_ROOT, admin_required, audit, csrf_token, db, iso_now, permission_required, user_permissions
import cash_payments
import ident_import
import paid_services
import upload_reconcile
import advertising_report
import paid_period_confirmation

MANAGEMENT_KEY = "az-management-report-v1"
REPORT_BLOB_KEYS = {
    "az-service-analytics-v1",
    "az-patient-transitions-v1",
    "az-clinic-primary-cost-v1",
    "az-service-extra-payments-v1",
    "az-service-salary-v1",
    "az-management-monthly-plan-v1",
}
WRITABLE_BLOB_KEYS = set(REPORT_BLOB_KEYS)

REPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS report_data (
    date TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS report_blobs (
    key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);
"""

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _ensure_schema():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(REPORT_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _require_api_csrf():
    sent = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")
    if not expected or not sent or not secrets.compare_digest(sent, expected):
        abort(400)


def _normalize_record(date, record):
    if not DATE_RE.fullmatch(date or ""):
        abort(400)
    if not isinstance(record, dict):
        abort(400)
    normalized = dict(record)
    normalized["date"] = date
    return normalized


CASH_PRESERVED_KEYS = (
    "billedMedicine", "billedLab", "billedDentists", "billedClinicDocs",
    "billedLabRevenue", "billedTotal", "cashOOO", "cashIP", "cashTotal",
    "cashUnallocated", "factMedicine", "factLab", "labRevenue", "dentists",
    "clinicDocs", "dentistsLegal", "clinicDocsLegal", "labLegal",
    "dentCashOOO", "dentCashIP", "clinicCashOOO", "clinicCashIP",
    "labCashOOO", "labCashIP", "_cash_source", "_cash_rule",
)

PAID_PRESERVED_KEYS = (
    "paidDataComplete", "paidAsOf", "paidDentists", "paidClinicDocs",
    "paidLabDocs", "paidDentistry", "paidStructure", "paidLab",
    "paidServicesTotal", "paidClassifiedTotal", "paidUnclassified",
    "serviceDebtOpening", "serviceBilled", "serviceDebtClosing",
    "paidVisitAttribution", "paidVisitMatched", "paidVisitUnmatched",
)


def _has_cash_state(record):
    return isinstance(record, dict) and (
        record.get("_cash_rule") == "positive-receipts-only-v1"
        or record.get("cashTotal") not in (None, "")
    )


def _preserve_cash_state(existing, incoming):
    result = dict(incoming)
    if _has_cash_state(existing):
        for key in CASH_PRESERVED_KEYS:
            if key in existing:
                result[key] = existing[key]
    if isinstance(existing, dict) and existing.get("paidDataComplete") is True:
        for key in PAID_PRESERVED_KEYS:
            if key in existing:
                result[key] = existing[key]
    return result


def _cash_months(conn):
    try:
        rows = conn.execute(
            "SELECT DISTINCT substr(data_date,1,7) AS month "
            "FROM cash_receipts_daily ORDER BY month"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    result = []
    for row in rows:
        try:
            value = row["month"]
        except (TypeError, IndexError):
            value = row[0]
        if value:
            result.append(str(value))
    return result


def _reapply_cash_after_restore(conn, updated_by, updated_at):
    changed = 0
    for month in _cash_months(conn):
        changed += cash_payments.overlay_stored_month(conn, month, updated_by, updated_at)
    return changed


def _reapply_paid_after_restore(conn, updated_by, updated_at):
    maps = upload_reconcile.provider_maps(conn)
    changed = 0
    for month in paid_services.snapshot_months(conn):
        changed += paid_services.overlay_stored_month(
            conn,
            month,
            updated_by,
            updated_at,
            maps["dent"],
            maps["structure"],
            maps["lab"],
            ignored=maps["ignore"],
        )
    return changed



def _management_doctor_provider_mappings(conn):
    """Use the same confirmed provider decisions for every paid period."""
    mappings = {
        "dent": dict(ident_import.DENTISTS),
        "structure": dict(ident_import.STRUCTURE_DOCTORS),
        "lab": dict(ident_import.LAB_DOCTORS),
    }
    ignored = set(ident_import.KNOWN_STAFF)
    for providers in mappings.values():
        ignored.difference_update(providers)

    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='provider_registry'"
    ).fetchone():
        for row in conn.execute(
            "SELECT source_name,display_name,direction "
            "FROM provider_registry WHERE direction<>'pending' "
            "ORDER BY source_name"
        ):
            source = str(row["source_name"])
            for providers in mappings.values():
                providers.pop(source, None)
            ignored.discard(source)
            direction = str(row["direction"])
            if direction in mappings:
                mappings[direction][source] = str(row["display_name"])
            elif direction == "ignore":
                ignored.add(source)

    return mappings, ignored


def _paid_patient_interval(snapshot, start, end, mappings, ignored):
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("paid_patient_evidence_complete") is not True
        or not isinstance(snapshot.get("paid_patients"), dict)
    ):
        return None

    sets = {
        "medicine": set(),
        "dent": set(),
        "structure": set(),
        "dentists": {},
        "clinicDocs": {},
    }
    unknown = set()
    for source_name, raw_tokens in snapshot.get("paid_patients", {}).items():
        source = str(source_name or "")
        tokens = {
            str(token)
            for token in (raw_tokens or [])
            if str(token or "").strip()
        }
        if not tokens:
            continue
        if source in mappings["dent"]:
            display = mappings["dent"][source]
            sets["dent"].update(tokens)
            sets["medicine"].update(tokens)
            sets["dentists"].setdefault(display, set()).update(tokens)
        elif source in mappings["structure"]:
            display = mappings["structure"][source]
            sets["structure"].update(tokens)
            sets["medicine"].update(tokens)
            sets["clinicDocs"].setdefault(display, set()).update(tokens)
        elif source in mappings["lab"] or source in ignored:
            continue
        else:
            unknown.add(source)

    if unknown:
        return None
    return {"from": start, "to": end, "sets": sets}


def _paid_report_sources(conn):
    return paid_period_confirmation.read_sources(
        conn, paid_period_confirmation.load_config(DB_PATH)
    )


def _paid_patient_intervals(conn):
    mappings, ignored = _management_doctor_provider_mappings(conn)
    unique = {}
    # Snapshot sources first, explicit period sources second. An exact period
    # takes precedence over a legacy snapshot; never add overlapping copies.
    for source in _paid_report_sources(conn):
        interval = _paid_patient_interval(
            source["payload"], source["from"], source["to"], mappings, ignored
        )
        if interval is not None:
            unique[(interval["from"], interval["to"])] = interval
    return sorted(unique.values(), key=lambda item: (item["from"], item["to"]))


def _cover_paid_patient_range(intervals, start, end):
    exact = next(
        (item for item in intervals if item["from"] == start and item["to"] == end),
        None,
    )
    if exact:
        return [exact]

    by_start = {}
    for item in intervals:
        if item["from"] < start or item["to"] > end:
            continue
        by_start.setdefault(item["from"], []).append(item)
    for items in by_start.values():
        items.sort(key=lambda item: item["to"], reverse=True)

    memo = {}
    def cover(cursor):
        if cursor > end:
            return []
        if cursor in memo:
            return memo[cursor]
        result = None
        for item in by_start.get(cursor, []):
            next_day = (calendar_date.fromisoformat(item["to"]) + timedelta(days=1)).isoformat()
            tail = cover(next_day)
            if tail is not None:
                result = [item, *tail]
                break
        memo[cursor] = result
        return result

    return cover(start)


def _combine_paid_patient_intervals(parts):
    if not parts:
        return None
    combined = {
        "medicine": set(),
        "dent": set(),
        "structure": set(),
        "dentists": {},
        "clinicDocs": {},
    }
    for part in parts:
        sets = part["sets"]
        for key in ("medicine", "dent", "structure"):
            combined[key].update(sets[key])
        for key in ("dentists", "clinicDocs"):
            for name, tokens in sets[key].items():
                combined[key].setdefault(name, set()).update(tokens)
    return {
        "medicine": len(combined["medicine"]),
        "dent": len(combined["dent"]),
        "structure": len(combined["structure"]),
        "dentists": {name: len(tokens) for name, tokens in sorted(combined["dentists"].items())},
        "clinicDocs": {name: len(tokens) for name, tokens in sorted(combined["clinicDocs"].items())},
    }


def _paid_patient_counts(conn, start, end, intervals=None):
    if not DATE_RE.fullmatch(str(start or "")) or not DATE_RE.fullmatch(str(end or "")) or start > end:
        return None
    intervals = _paid_patient_intervals(conn) if intervals is None else intervals
    parts = _cover_paid_patient_range(intervals, start, end)
    return _combine_paid_patient_intervals(parts)


def _management_paid_patients_by_date(conn, intervals=None):
    """Every provable MTD endpoint, not just the latest endpoint per month.

    A completed monthly export cannot prove an earlier cutoff. Source intervals
    and patient tokens stay private; only aggregate counts leave this function.
    """
    intervals = _paid_patient_intervals(conn) if intervals is None else intervals
    result = {}
    for end in sorted({part["to"] for part in intervals}):
        start = end[:7] + "-01"
        counts = _paid_patient_counts(conn, start, end, intervals)
        if counts is not None:
            result[end] = {"from": start, "to": end, "asOf": end, **counts}
    return result


def _management_paid_patients_monthly(conn, intervals=None):
    # A late incremental slice can extend an earlier MTD source. It need not
    # itself begin on day 1; the complete covering is proven above.
    result = {}
    for end, row in _management_paid_patients_by_date(conn, intervals).items():
        result[end[:7]] = {key: value for key, value in row.items()
                          if key not in ("from", "to")}
    return result


def _management_doctor_paid_snapshots(conn):
    """Read the original amounts using the same reporting period as payers."""
    mappings, ignored = _management_doctor_provider_mappings(conn)
    result = {}
    for source in _paid_report_sources(conn):
        if source["kind"] != "snapshot":
            continue
        snapshot = source["payload"]
        try:
            summary = paid_services.direction_summary(
                snapshot, mappings["dent"], mappings["structure"], mappings["lab"],
                ignored=ignored,
            )
        except (TypeError, ValueError, AttributeError):
            continue
        if summary["unknownProviders"] or any(
            not math.isfinite(value)
            for key in ("dentists", "clinicDocs", "labDocs")
            for value in summary[key].values()
        ):
            continue
        result.setdefault(source["from"][:7], []).append({
            "asOf": source["to"],
            "dentists": summary["dentists"], "clinicDocs": summary["clinicDocs"],
        })
    for rows in result.values():
        rows.sort(key=lambda row: row["asOf"])
    return result


def _management_doctor_paid_periods(conn):
    """Whole source amounts; an approved month does not invent payment days."""
    mappings, ignored = _management_doctor_provider_mappings(conn)
    result = []
    for source in _paid_report_sources(conn):
        if source["kind"] != "period":
            continue
        try:
            summary = paid_services.direction_summary(
                source["payload"], mappings["dent"], mappings["structure"], mappings["lab"],
                ignored=ignored,
            )
        except (TypeError, ValueError, AttributeError):
            continue
        if summary["unknownProviders"] or any(
            not math.isfinite(value)
            for key in ("dentists", "clinicDocs", "labDocs")
            for value in summary[key].values()
        ):
            continue
        result.append({
            "from": source["from"], "to": source["to"],
            "dentists": summary["dentists"], "clinicDocs": summary["clinicDocs"],
        })
    return result


def _read_report_file(name):
    path = SITE_ROOT / "reports" / name
    if not path.exists():
        abort(404)
    return path.read_text(encoding="utf-8")


def _render_reports_menu():
    return _read_report_file("menu.html")


def _storage_map():
    rows = db().execute(
        "SELECT key, payload FROM report_blobs WHERE key IN ({}) ORDER BY key".format(
            ",".join("?" for _ in REPORT_BLOB_KEYS)
        ),
        tuple(sorted(REPORT_BLOB_KEYS)),
    ).fetchall()
    return {row["key"]: row["payload"] for row in rows}


def management_monthly_plans():
    row = db().execute(
        "SELECT payload FROM report_blobs WHERE key=?",
        ("az-management-monthly-plan-v1",),
    ).fetchone()
    if not row:
        return {}
    try:
        parsed = json.loads(row["payload"])
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict) or parsed.get("version") != 1:
        return {}
    months = parsed.get("months")
    if not isinstance(months, dict):
        return {}
    result = {}
    for month, raw in months.items():
        month = str(month)
        if not MONTH_RE.fullmatch(month) or isinstance(raw, bool):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        result[month] = int(value) if value.is_integer() else value
    return result


def _validate_blob_payload(key, payload):
    if key not in REPORT_BLOB_KEYS or not isinstance(payload, str):
        abort(400)
    if len(payload.encode("utf-8")) > 1_500_000:
        abort(400)
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError):
        abort(400)
    if not isinstance(parsed, dict):
        abort(400)

    if key == "az-service-analytics-v1":
        if not isinstance(parsed.get("directions"), dict) or not isinstance(parsed.get("months"), list):
            abort(400)
    elif key == "az-patient-transitions-v1":
        if not isinstance(parsed.get("levels"), dict) or not parsed.get("dataEnd"):
            abort(400)
    elif key == "az-clinic-primary-cost-v1":
        if not isinstance(parsed.get("months"), list) or not isinstance(parsed.get("primaryPatients"), list):
            abort(400)
    elif key == "az-management-monthly-plan-v1":
        if parsed.get("version") != 1 or not isinstance(parsed.get("months"), dict):
            abort(400)
        months = {}
        for month, raw in parsed["months"].items():
            month = str(month)
            if not MONTH_RE.fullmatch(month) or isinstance(raw, bool):
                abort(400)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                abort(400)
            if not math.isfinite(value) or value < 0 or value > 1_000_000_000_000:
                abort(400)
            months[month] = int(value) if value.is_integer() else value
        parsed = {"version": 1, "months": months}
    elif key in {"az-service-extra-payments-v1", "az-service-salary-v1"}:
        if not any(name in parsed for name in ("Стоматология", "Клиника", "Лаборатория")):
            abort(400)

    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def _script_json(value):
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _server_bootstrap_script():
    storage_json = _script_json(_storage_map())
    sync_keys_json = _script_json(sorted(WRITABLE_BLOB_KEYS))
    csrf_json = _script_json(csrf_token())
    return f"""<script>
(() => {{
  const serverStorage={storage_json};
  const syncKeys=new Set({sync_keys_json});
  const csrf={csrf_json};
  const nativeSet=Storage.prototype.setItem;
  const nativeRemove=Storage.prototype.removeItem;

  Object.entries(serverStorage).forEach(([key,value]) => {{
    try {{ nativeSet.call(localStorage,key,String(value)); }} catch (error) {{ console.error(error); }}
  }});
  try {{ sessionStorage.setItem('az-management-auth-v1','1'); }} catch (error) {{}}

  Storage.prototype.setItem=function(key,value) {{
    nativeSet.call(this,key,value);
    let isLocal=false;
    try {{ isLocal=this===window.localStorage; }} catch (error) {{}}
    if(!isLocal || !syncKeys.has(String(key))) return;
    fetch('/api/reports/storage/'+encodeURIComponent(String(key)), {{
      method:'PUT',
      credentials:'same-origin',
      headers:{{'Content-Type':'application/json','X-CSRF-Token':csrf}},
      body:JSON.stringify({{payload:String(value)}})
    }}).catch(error => console.error('AZ report server sync failed',error));
  }};

  Storage.prototype.removeItem=function(key) {{
    nativeRemove.call(this,key);
    let isLocal=false;
    try {{ isLocal=this===window.localStorage; }} catch (error) {{}}
    if(!isLocal || !syncKeys.has(String(key))) return;
    fetch('/api/reports/storage/'+encodeURIComponent(String(key)), {{
      method:'DELETE',
      credentials:'same-origin',
      headers:{{'X-CSRF-Token':csrf}}
    }}).catch(error => console.error('AZ report server sync failed',error));
  }};
}})();
</script>"""


def _inject_bootstrap(html):
    return html.replace("<head>", "<head>\n" + _server_bootstrap_script(), 1)


def _render_services():
    return _inject_bootstrap(_read_report_file("services.html"))


def _render_services_base():
    html = _inject_bootstrap(_read_report_file("services-base.html"))
    html = html.replace(
        '<button id="logoutBtn" class="btn">Выйти</button>',
        '<a class="btn" href="/reports/">К отчётам</a>'
        '<a class="btn" href="/">На главную</a>'
        '<button id="logoutBtn" class="btn hidden" style="display:none">Выйти</button>',
    )
    return html


def _render_transitions():
    html = _inject_bootstrap(_read_report_file("transitions.html"))
    html = html.replace('<div id="loginView" class="login">', '<div id="loginView" class="login hidden">')
    html = html.replace('<div id="appView" class="app hidden">', '<div id="appView" class="app">')
    html = html.replace(
        '<a class="btn" href="./">Управленческий отчёт</a>\n      <button id="logoutBtn" class="btn">Выйти</button>',
        '<a class="btn" href="/reports/">К отчётам</a>\n'
        '      <a class="btn" href="/">На главную</a>\n'
        '      <button id="logoutBtn" class="btn hidden" style="display:none">Выйти</button>',
    )
    html = html.replace(
        '<script src="./transitions-page.js?v=20260904-1"></script>',
        '<script>sessionStorage.setItem("az-management-auth-v1","1");</script>\n'
        '<script src="./transitions-page.js?v=20260904-1"></script>',
    )
    return html


def _render_server_reports():
    html = _read_report_file("index.html")
    html = html.replace("<head>", '<head>\n<base href="/reports/">', 1)

    html = html.replace('<div id="loginView" class="login">', '<div id="loginView" class="login hidden">')
    html = html.replace('<div id="appView" class="app hidden">', '<div id="appView" class="app">')
    html = html.replace(
        '<div><div class="title">Управленческий отчёт</div><div class="muted">Ручной режим ввода данных</div></div>',
        '<div><div class="title">Управленческий отчёт</div><div class="muted">Данные хранятся на сервере AZ-BAZE</div></div>'
    )
    html = html.replace(
        '<button id="logoutBtn" class="btn">Выйти</button>',
        '<button id="logoutBtn" class="btn hidden" style="display:none">Выйти</button>',
    )
    html = html.replace(
        '<button id="deleteBtn" class="btn warn">Удалить запись</button>',
        '<button id="deleteBtn" class="btn warn hidden" style="display:none">Удалить запись</button>',
    )
    html = html.replace(
        '<button class="btn" disabled title="Добавим на следующем этапе">Импорт из МИС</button>',
        '',
    )
    html = html.replace(
        '<div class="notice">Временный режим: данные сохраняются только в этом браузере на этом устройстве. Для реальной многопользовательской работы следующим этапом нужен закрытый серверный контур.</div>',
        '<div class="notice">Данные отчёта хранятся централизованно на сервере AZ-BAZE и доступны после авторизации.</div>'
    )
    html = html.replace(
        '<div class="danger-note">Логин на этой временной статической странице защищает интерфейс только от случайного доступа. Перед хранением реальных управленческих данных в интернете авторизацию и данные нужно перенести на сервер.</div>',
        '<div class="danger-note">Резервную копию можно скачать в любой момент. Восстановление копии полностью заменяет серверные данные управленческого отчёта.</div>'
    )

    old_store = """function loadStore(){try{return JSON.parse(localStorage.getItem(STORAGE_KEY)||'{}')}catch{return {}}}
function saveStore(v){localStorage.setItem(STORAGE_KEY,JSON.stringify(v))}"""
    new_store = """let SERVER_STORE={},REPORT_CSRF='',DOCTOR_PAID_SNAPSHOTS={},DOCTOR_PAID_PERIODS=[],PAID_PATIENTS_MONTHLY={},PAID_PATIENTS_BY_DATE={};
function loadStore(){return SERVER_STORE}
function saveStore(v){SERVER_STORE=v}
async function reportApi(url,options={}){
  const opts={credentials:'same-origin',...options};
  const method=(opts.method||'GET').toUpperCase();
  opts.headers={...(opts.headers||{})};
  if(opts.body&&!opts.headers['Content-Type'])opts.headers['Content-Type']='application/json';
  if(method!=='GET'&&method!=='HEAD')opts.headers['X-CSRF-Token']=REPORT_CSRF;
  const response=await fetch(url,opts);
  if(response.status===401){location.href='/login?next='+encodeURIComponent(location.pathname);throw new Error('auth required')}
  if(!response.ok){const text=await response.text();throw new Error(text||('HTTP '+response.status))}
  return response.status===204?null:response.json();
}
async function loadServerStore(){
  const payload=await reportApi('/api/reports/data');
  SERVER_STORE=payload.data||{};
  DOCTOR_PAID_SNAPSHOTS=payload.doctorPaidSnapshots||{};
  DOCTOR_PAID_PERIODS=Array.isArray(payload.doctorPaidPeriods)?payload.doctorPaidPeriods:[];
  PAID_PATIENTS_MONTHLY=payload.paidPatientsMonthly||{};
  PAID_PATIENTS_BY_DATE=payload.paidPatientsByDate||{};
  REPORT_CSRF=payload.csrf||'';
  if(typeof syncProviderNames==='function'){
    syncProviderNames(SERVER_STORE);
    Object.values(DOCTOR_PAID_SNAPSHOTS).forEach(rows=>
      rows.forEach(r=>syncProviderNames({[r.asOf]:{dentists:r.dentists,clinicDocs:r.clinicDocs}})));
    DOCTOR_PAID_PERIODS.forEach(r=>
      syncProviderNames({[r.to]:{dentists:r.dentists,clinicDocs:r.clinicDocs}}));
  }
  if(typeof renderStructure==='function')renderStructure();
}"""
    html = html.replace(old_store, new_store)

    old_save = """function saveDate(){const r=collectRecord();if(!r.date)return;const store=loadStore();store[r.date]=r;saveStore(store);fillRecord(r);$('#status').textContent='Сохранено в этом браузере: '+new Date(r.date+'T12:00:00').toLocaleString('ru-RU');}"""
    new_save = """async function saveDate(){const r=collectRecord();if(!r.date)return;try{const result=await reportApi('/api/reports/data/'+encodeURIComponent(r.date),{method:'PUT',body:JSON.stringify(r)});const saved=result?.record||r;SERVER_STORE[r.date]=saved;fillRecord(saved);$('#status').textContent='Сохранено на сервере: '+new Date(r.date+'T12:00:00').toLocaleString('ru-RU')}catch(error){console.error(error);$('#status').textContent='Не удалось сохранить данные на сервере';alert('Не удалось сохранить данные.')}}"""
    html = html.replace(old_save, new_save)

    old_delete = """function deleteDate(){const date=getSelectedDate();if(!date||!confirm('Удалить сохранённую запись за выбранную дату?'))return;const s=loadStore();delete s[date];saveStore(s);loadDate();}"""
    new_delete = """async function deleteDate(){const date=getSelectedDate();if(!date||!confirm('Удалить сохранённую запись за выбранную дату?'))return;try{await reportApi('/api/reports/data/'+encodeURIComponent(date),{method:'DELETE'});delete SERVER_STORE[date];loadDate()}catch(error){console.error(error);alert('Не удалось удалить запись.')}}"""
    html = html.replace(old_delete, new_delete)

    old_import = """function importBackup(file){const fr=new FileReader();fr.onload=()=>{try{const j=JSON.parse(fr.result);if(!j.data||typeof j.data!=='object')throw 0;if(!confirm('Заменить локальные данные данными из резервной копии?'))return;saveStore(j.data);loadDate();alert('Резервная копия восстановлена')}catch{alert('Не удалось прочитать резервную копию')}};fr.readAsText(file)}"""
    new_import = """async function importBackup(file){try{const j=JSON.parse(await file.text());if(!j.data||typeof j.data!=='object'||Array.isArray(j.data))throw new Error('invalid backup');const entries=Object.entries(j.data).filter(([date,record])=>/^\\d{4}-\\d{2}-\\d{2}$/.test(date)&&record&&typeof record==='object'&&!Array.isArray(record));if(!entries.length)throw new Error('empty backup');if(!confirm(`Заменить серверные данные резервной копией? Будет загружено записей: ${entries.length}.`))return;const result=await reportApi('/api/reports/import',{method:'POST',body:JSON.stringify(j)});await loadServerStore();const dates=Object.keys(SERVER_STORE).sort();const latest=dates[dates.length-1];$('#reportDate').value=latest;loadDate();$('#status').textContent=`На сервер импортировано записей: ${result.imported}. Открыта дата ${new Date(latest+'T12:00:00').toLocaleDateString('ru-RU')}.`;alert(`Резервная копия перенесена на сервер. Записей: ${result.imported}.`)}catch(error){console.error(error);alert('Не удалось перенести резервную копию на сервер.')}}"""
    html = html.replace(old_import, new_import)

    html = re.sub(r'<script src="\./seed-data\.js[^"]*"></script>', "", html)
    html = re.sub(r'<script src="\./secure-import\.js[^"]*"></script>', "", html)

    init_pattern = re.compile(r"function init\(\)\{.*?\}\ninit\(\);", re.S)
    new_init = """async function init(){renderStructure();const ys=$('#year');const cy=new Date().getFullYear();for(let y=2025;y<=cy+3;y++){const o=document.createElement('option');o.value=y;o.textContent=y;if(y===cy)o.selected=true;ys.appendChild(o)}const today=new Date();$('#reportDate').value=[today.getFullYear(),String(today.getMonth()+1).padStart(2,'0'),String(today.getDate()).padStart(2,'0')].join('-');document.addEventListener('input',e=>{if(e.target.matches('[data-key]:not([readonly]),[data-doctor]'))refreshAutos()});$('#reportDate').addEventListener('change',loadDate);$('#mode').addEventListener('change',switchMode);$('#quarter').addEventListener('change',renderPeriod);$('#half').addEventListener('change',renderPeriod);$('#year').addEventListener('change',()=>$('#mode').value==='date'?null:renderPeriod());$('#saveBtn').addEventListener('click',saveDate);$('#deleteBtn').addEventListener('click',deleteDate);$('#backupBtn').addEventListener('click',exportBackup);$('#restoreInput').addEventListener('change',e=>e.target.files[0]&&importBackup(e.target.files[0]));$('#logoutBtn').addEventListener('click',()=>location.href='/reports/');try{await loadServerStore();const dates=Object.keys(SERVER_STORE).sort();if(dates.length)$('#reportDate').value=dates[dates.length-1];$('#reportDate').dispatchEvent(new Event('change'))}catch(error){console.error(error);$('#status').textContent='Не удалось загрузить серверные данные. Обновите страницу.'}}
init();"""
    html, count = init_pattern.subn(new_init, html, count=1)
    if count != 1:
        raise RuntimeError("report init patch failed")

    return html


def _management_rows_from_backup(raw_value):
    if not isinstance(raw_value, str):
        abort(400)
    try:
        incoming = json.loads(raw_value)
    except (TypeError, ValueError):
        abort(400)
    if not isinstance(incoming, dict) or not incoming:
        abort(400)

    rows = []
    now = iso_now()
    for date, record in incoming.items():
        if not DATE_RE.fullmatch(str(date)) or not isinstance(record, dict):
            abort(400)
        normalized = dict(record)
        normalized["date"] = date
        rows.append(
            (
                date,
                json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
                g.user["id"],
                now,
            )
        )
    if len(rows) > 4000:
        abort(400)
    return rows


def register_report_storage(app):
    _ensure_schema()
    advertising_report.register_advertising_report(app)

    @app.before_request
    def serve_report_pages():
        if request.method != "GET":
            return None
        paths = {
            "/reports/",
            "/reports/management/",
            "/reports/services.html",
            "/reports/services-base.html",
            "/reports/transitions.html",
        }
        if request.path not in paths:
            return None
        if not getattr(g, "user", None):
            return None
        if "reports" not in user_permissions(g.user):
            return None
        if request.path == "/reports/":
            return Response(_render_reports_menu(), mimetype="text/html")
        if request.path == "/reports/management/":
            return Response(_render_server_reports(), mimetype="text/html")
        if request.path == "/reports/services.html":
            return Response(_render_services(), mimetype="text/html")
        if request.path == "/reports/services-base.html":
            return Response(_render_services_base(), mimetype="text/html")
        return Response(_render_transitions(), mimetype="text/html")

    @app.get("/reports/import-all/")
    @admin_required
    def report_import_all_page():
        return Response(_read_report_file("import-all.html"), mimetype="text/html")

    @app.get("/api/reports/data")
    @permission_required("reports")
    def report_data_all():
        rows = db().execute("SELECT date, payload FROM report_data ORDER BY date").fetchall()
        monthly_plans = management_monthly_plans()
        data = {}
        payer_intervals = _paid_patient_intervals(db())
        for row in rows:
            try:
                record = json.loads(row["payload"])
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            month = str(row["date"] or "")[:7]
            if month in monthly_plans:
                record["plan"] = monthly_plans[month]
            record_date = str(row["date"])
            month_start = record_date[:7] + "-01"
            payer_counts = _paid_patient_counts(db(), month_start, record_date, payer_intervals)
            # These are derived read-view fields, never a fallback stored count.
            for key in ("paidPatientEvidenceComplete", "paidPatients", "paidDentPatients",
                        "paidClinicPatients", "paidDentPatientsByDoctor",
                        "paidClinicPatientsByDoctor", "paidPatientPeriodFrom", "paidPatientPeriodTo"):
                record.pop(key, None)
            if payer_counts is not None:
                record["paidPatientPeriodFrom"] = month_start
                record["paidPatientPeriodTo"] = record_date
                record["paidPatientEvidenceComplete"] = True
                record["paidPatients"] = payer_counts["medicine"]
                record["paidDentPatients"] = payer_counts["dent"]
                record["paidClinicPatients"] = payer_counts["structure"]
                record["paidDentPatientsByDoctor"] = payer_counts["dentists"]
                record["paidClinicPatientsByDoctor"] = payer_counts["clinicDocs"]
            data[row["date"]] = record
        return jsonify(
            data=data,
            doctorPaidSnapshots=_management_doctor_paid_snapshots(db()),
            doctorPaidPeriods=_management_doctor_paid_periods(db()),
            paidPatientsMonthly=_management_paid_patients_monthly(db(), payer_intervals),
            paidPatientsByDate=_management_paid_patients_by_date(db(), payer_intervals),
            csrf=csrf_token(),
        )

    @app.put("/api/reports/data/<date>")
    @permission_required("reports")
    def report_data_put(date):
        _require_api_csrf()
        record = _normalize_record(date, request.get_json(silent=True))
        existing_row = db().execute(
            "SELECT payload FROM report_data WHERE date=?",
            (date,),
        ).fetchone()
        if existing_row:
            try:
                existing = json.loads(existing_row["payload"])
            except (TypeError, ValueError):
                existing = {}
            record = _preserve_cash_state(existing, record)
        db().execute(
            """
            INSERT INTO report_data(date, payload, updated_by, updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET
              payload=excluded.payload,
              updated_by=excluded.updated_by,
              updated_at=excluded.updated_at
            """,
            (date, json.dumps(record, ensure_ascii=False, separators=(",", ":")), g.user["id"], iso_now()),
        )
        db().commit()
        audit("report_saved", target_user_id=g.user["id"], details=f"date={date}; cash_preserved={_has_cash_state(record)}")
        return jsonify(ok=True, record=record)

    @app.delete("/api/reports/data/<date>")
    @permission_required("reports")
    def report_data_delete(date):
        _require_api_csrf()
        if not DATE_RE.fullmatch(date or ""):
            abort(400)
        existing_row = db().execute(
            "SELECT payload FROM report_data WHERE date=?",
            (date,),
        ).fetchone()
        if existing_row:
            try:
                existing = json.loads(existing_row["payload"])
            except (TypeError, ValueError):
                existing = {}
            if _has_cash_state(existing):
                abort(409)
        db().execute("DELETE FROM report_data WHERE date=?", (date,))
        db().commit()
        audit("report_deleted", target_user_id=g.user["id"], details=f"date={date}")
        return jsonify(ok=True)

    @app.get("/api/reports/storage")
    @permission_required("reports")
    def report_storage_all():
        return jsonify(storage=_storage_map(), csrf=csrf_token())

    @app.put("/api/reports/storage/<path:key>")
    @permission_required("reports")
    def report_storage_put(key):
        _require_api_csrf()
        if key not in WRITABLE_BLOB_KEYS:
            abort(404)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        payload = _validate_blob_payload(key, body.get("payload"))
        db().execute(
            """
            INSERT INTO report_blobs(key, payload, updated_by, updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET
              payload=excluded.payload,
              updated_by=excluded.updated_by,
              updated_at=excluded.updated_at
            """,
            (key, payload, g.user["id"], iso_now()),
        )
        db().commit()
        audit("report_blob_saved", target_user_id=g.user["id"], details=f"key={key}")
        return jsonify(ok=True)

    @app.delete("/api/reports/storage/<path:key>")
    @permission_required("reports")
    def report_storage_delete(key):
        _require_api_csrf()
        if key not in WRITABLE_BLOB_KEYS:
            abort(404)
        db().execute("DELETE FROM report_blobs WHERE key=?", (key,))
        db().commit()
        audit("report_blob_deleted", target_user_id=g.user["id"], details=f"key={key}")
        return jsonify(ok=True)

    @app.post("/api/reports/import")
    @admin_required
    def report_data_import():
        _require_api_csrf()
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            abort(400)
        incoming = payload["data"]
        rows = []
        for date, record in incoming.items():
            if not DATE_RE.fullmatch(str(date)) or not isinstance(record, dict):
                abort(400)
            normalized = _normalize_record(date, record)
            rows.append(
                (
                    date,
                    json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
                    g.user["id"],
                    iso_now(),
                )
            )
        if not rows or len(rows) > 4000:
            abort(400)
        conn = db()
        cash_payments.init_schema(conn)
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM report_data")
            conn.executemany(
                "INSERT INTO report_data(date, payload, updated_by, updated_at) VALUES(?,?,?,?)",
                rows,
            )
            restored_at = iso_now()
            _reapply_cash_after_restore(conn, g.user["id"], restored_at)
            _reapply_paid_after_restore(conn, g.user["id"], restored_at)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        audit("reports_backup_imported", target_user_id=g.user["id"], details=f"records={len(rows)}")
        return jsonify(ok=True, imported=len(rows))

    @app.post("/api/reports/import-all")
    @admin_required
    def report_import_all():
        _require_api_csrf()
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            abort(400)
        storage = payload.get("storage")
        if not isinstance(storage, dict) or MANAGEMENT_KEY not in storage:
            abort(400)

        management_rows = _management_rows_from_backup(storage[MANAGEMENT_KEY])
        blob_rows = []
        now = iso_now()
        for key in sorted(REPORT_BLOB_KEYS):
            if key not in storage:
                continue
            normalized = _validate_blob_payload(key, storage[key])
            blob_rows.append((key, normalized, g.user["id"], now))

        if not blob_rows:
            abort(400)

        conn = db()
        cash_payments.init_schema(conn)
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM report_data")
            conn.executemany(
                "INSERT INTO report_data(date, payload, updated_by, updated_at) VALUES(?,?,?,?)",
                management_rows,
            )
            for key, value, uid, updated_at in blob_rows:
                conn.execute(
                    """
                    INSERT INTO report_blobs(key, payload, updated_by, updated_at)
                    VALUES(?,?,?,?)
                    ON CONFLICT(key) DO UPDATE SET
                      payload=excluded.payload,
                      updated_by=excluded.updated_by,
                      updated_at=excluded.updated_at
                    """,
                    (key, value, uid, updated_at),
                )
            _reapply_cash_after_restore(conn, g.user["id"], now)
            _reapply_paid_after_restore(conn, g.user["id"], now)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        audit(
            "reports_full_backup_imported",
            target_user_id=g.user["id"],
            details=f"records={len(management_rows)}; blobs={','.join(key for key, *_ in blob_rows)}",
        )
        return jsonify(
            ok=True,
            managementRecords=len(management_rows),
            datasets=[key for key, *_ in blob_rows],
        )
