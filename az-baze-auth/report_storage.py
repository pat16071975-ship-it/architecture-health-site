import json
import re
import secrets
import sqlite3
from flask import Response, abort, g, jsonify, request, session

from app import DB_PATH, SITE_ROOT, admin_required, audit, csrf_token, db, iso_now, permission_required, user_permissions

MANAGEMENT_KEY = "az-management-report-v1"
REPORT_BLOB_KEYS = {
    "az-service-analytics-v1",
    "az-patient-transitions-v1",
    "az-clinic-primary-cost-v1",
    "az-service-extra-payments-v1",
    "az-service-salary-v1",
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
    html = html.replace('<button id="logoutBtn" class="btn">Выйти</button>', '<button id="logoutBtn" class="btn">К отчётам</button>')
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
    new_store = """let SERVER_STORE={},REPORT_CSRF='';
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
  REPORT_CSRF=payload.csrf||'';
}"""
    html = html.replace(old_store, new_store)

    old_save = """function saveDate(){const r=collectRecord();if(!r.date)return;const store=loadStore();store[r.date]=r;saveStore(store);fillRecord(r);$('#status').textContent='Сохранено в этом браузере: '+new Date(r.date+'T12:00:00').toLocaleString('ru-RU');}"""
    new_save = """async function saveDate(){const r=collectRecord();if(!r.date)return;try{await reportApi('/api/reports/data/'+encodeURIComponent(r.date),{method:'PUT',body:JSON.stringify(r)});SERVER_STORE[r.date]=r;fillRecord(r);$('#status').textContent='Сохранено на сервере: '+new Date(r.date+'T12:00:00').toLocaleString('ru-RU')}catch(error){console.error(error);$('#status').textContent='Не удалось сохранить данные на сервере';alert('Не удалось сохранить данные.')}}"""
    html = html.replace(old_save, new_save)

    old_delete = """function deleteDate(){const date=getSelectedDate();if(!date||!confirm('Удалить сохранённую запись за выбранную дату?'))return;const s=loadStore();delete s[date];saveStore(s);loadDate();}"""
    new_delete = """async function deleteDate(){const date=getSelectedDate();if(!date||!confirm('Удалить сохранённую запись за выбранную дату?'))return;try{await reportApi('/api/reports/data/'+encodeURIComponent(date),{method:'DELETE'});delete SERVER_STORE[date];loadDate()}catch(error){console.error(error);alert('Не удалось удалить запись.')}}"""
    html = html.replace(old_delete, new_delete)

    old_import = """function importBackup(file){const fr=new FileReader();fr.onload=()=>{try{const j=JSON.parse(fr.result);if(!j.data||typeof j.data!=='object')throw 0;if(!confirm('Заменить локальные данные данными из резервной копии?'))return;saveStore(j.data);loadDate();alert('Резервная копия восстановлена')}catch{alert('Не удалось прочитать резервную копию')}};fr.readAsText(file)}"""
    new_import = """async function importBackup(file){try{const j=JSON.parse(await file.text());if(!j.data||typeof j.data!=='object'||Array.isArray(j.data))throw new Error('invalid backup');const entries=Object.entries(j.data).filter(([date,record])=>/^\\d{4}-\\d{2}-\\d{2}$/.test(date)&&record&&typeof record==='object'&&!Array.isArray(record));if(!entries.length)throw new Error('empty backup');if(!confirm(`Заменить серверные данные резервной копией? Будет загружено записей: ${entries.length}.`))return;const result=await reportApi('/api/reports/import',{method:'POST',body:JSON.stringify(j)});SERVER_STORE=j.data;const dates=entries.map(([date])=>date).sort();const latest=dates[dates.length-1];$('#reportDate').value=latest;loadDate();$('#status').textContent=`На сервер импортировано записей: ${result.imported}. Открыта дата ${new Date(latest+'T12:00:00').toLocaleDateString('ru-RU')}.`;alert(`Резервная копия перенесена на сервер. Записей: ${result.imported}.`)}catch(error){console.error(error);alert('Не удалось перенести резервную копию на сервер.')}}"""
    html = html.replace(old_import, new_import)

    html = re.sub(r'<script src="\./seed-data\.js[^"]*"></script>', "", html)
    html = re.sub(r'<script src="\./secure-import\.js[^"]*"></script>', "", html)

    init_pattern = re.compile(r"function init\(\)\{.*?\}\ninit\(\);", re.S)
    new_init = """async function init(){renderStructure();const ys=$('#year');const cy=new Date().getFullYear();for(let y=2025;y<=cy+3;y++){const o=document.createElement('option');o.value=y;o.textContent=y;if(y===cy)o.selected=true;ys.appendChild(o)}const today=new Date();$('#reportDate').value=[today.getFullYear(),String(today.getMonth()+1).padStart(2,'0'),String(today.getDate()).padStart(2,'0')].join('-');document.addEventListener('input',e=>{if(e.target.matches('[data-key]:not([readonly]),[data-doctor]'))refreshAutos()});$('#reportDate').addEventListener('change',loadDate);$('#mode').addEventListener('change',switchMode);$('#quarter').addEventListener('change',renderPeriod);$('#half').addEventListener('change',renderPeriod);$('#year').addEventListener('change',()=>$('#mode').value==='date'?null:renderPeriod());$('#saveBtn').addEventListener('click',saveDate);$('#deleteBtn').addEventListener('click',deleteDate);$('#backupBtn').addEventListener('click',exportBackup);$('#restoreInput').addEventListener('change',e=>e.target.files[0]&&importBackup(e.target.files[0]));$('#logoutBtn').addEventListener('click',()=>location.href='/reports/');try{await loadServerStore();const dates=Object.keys(SERVER_STORE).sort();if(dates.length)$('#reportDate').value=dates[dates.length-1];loadDate()}catch(error){console.error(error);$('#status').textContent='Не удалось загрузить серверные данные. Обновите страницу.'}}
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
        data = {}
        for row in rows:
            try:
                data[row["date"]] = json.loads(row["payload"])
            except (TypeError, ValueError):
                continue
        return jsonify(data=data, csrf=csrf_token())

    @app.put("/api/reports/data/<date>")
    @permission_required("reports")
    def report_data_put(date):
        _require_api_csrf()
        record = _normalize_record(date, request.get_json(silent=True))
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
        audit("report_saved", target_user_id=g.user["id"], details=f"date={date}")
        return jsonify(ok=True)

    @app.delete("/api/reports/data/<date>")
    @permission_required("reports")
    def report_data_delete(date):
        _require_api_csrf()
        if not DATE_RE.fullmatch(date or ""):
            abort(400)
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
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM report_data")
            conn.executemany(
                "INSERT INTO report_data(date, payload, updated_by, updated_at) VALUES(?,?,?,?)",
                rows,
            )
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
