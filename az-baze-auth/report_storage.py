import json
import re
import secrets
import sqlite3
from flask import Response, abort, g, jsonify, request, session

from app import DB_PATH, SITE_ROOT, admin_required, audit, csrf_token, db, iso_now, permission_required, user_permissions

REPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS report_data (
    date TEXT PRIMARY KEY,
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


def _render_server_reports():
    path = SITE_ROOT / "reports" / "index.html"
    if not path.exists():
        abort(404)
    html = path.read_text(encoding="utf-8")

    html = html.replace('<div id="loginView" class="login">', '<div id="loginView" class="login hidden">')
    html = html.replace('<div id="appView" class="app hidden">', '<div id="appView" class="app">')
    html = html.replace(
        '<div><div class="title">Управленческий отчёт</div><div class="muted">Ручной режим ввода данных</div></div>',
        '<div><div class="title">Управленческий отчёт</div><div class="muted">Данные хранятся на сервере AZ-BAZE</div></div>'
    )
    html = html.replace('<button id="logoutBtn" class="btn">Выйти</button>', '<button id="logoutBtn" class="btn">На главную</button>')
    html = html.replace(
        '<div class="notice">Временный режим: данные сохраняются только в этом браузере на этом устройстве. Для реальной многопользовательской работы следующим этапом нужен закрытый серверный контур.</div>',
        '<div class="notice">Данные отчёта хранятся централизованно на сервере AZ-BAZE и доступны после авторизации с разрешённых устройств.</div>'
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

    init_pattern = re.compile(r"function init\(\)\{.*?\}\ninit\(\);", re.S)
    new_init = """async function init(){renderStructure();const ys=$('#year');const cy=new Date().getFullYear();for(let y=2025;y<=cy+3;y++){const o=document.createElement('option');o.value=y;o.textContent=y;if(y===cy)o.selected=true;ys.appendChild(o)}const today=new Date();$('#reportDate').value=[today.getFullYear(),String(today.getMonth()+1).padStart(2,'0'),String(today.getDate()).padStart(2,'0')].join('-');document.addEventListener('input',e=>{if(e.target.matches('[data-key]:not([readonly]),[data-doctor]'))refreshAutos()});$('#reportDate').addEventListener('change',loadDate);$('#mode').addEventListener('change',switchMode);$('#quarter').addEventListener('change',renderPeriod);$('#half').addEventListener('change',renderPeriod);$('#year').addEventListener('change',()=>$('#mode').value==='date'?null:renderPeriod());$('#saveBtn').addEventListener('click',saveDate);$('#deleteBtn').addEventListener('click',deleteDate);$('#backupBtn').addEventListener('click',exportBackup);$('#restoreInput').addEventListener('change',e=>e.target.files[0]&&importBackup(e.target.files[0]));$('#logoutBtn').addEventListener('click',()=>location.href='/');try{await loadServerStore();loadDate()}catch(error){console.error(error);$('#status').textContent='Не удалось загрузить серверные данные. Обновите страницу.'}}
init();"""
    html, count = init_pattern.subn(new_init, html, count=1)
    if count != 1:
        raise RuntimeError("report init patch failed")

    return html


def register_report_storage(app):
    _ensure_schema()

    @app.before_request
    def serve_server_report_index():
        if request.path != "/reports/" or request.method != "GET":
            return None
        if not getattr(g, "user", None):
            return None
        if "reports" not in user_permissions(g.user):
            return None
        return Response(_render_server_reports(), mimetype="text/html")

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
