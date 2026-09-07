import json
import sqlite3

OLD = "Клиника"
NEW = "Отделение структуры"


REPORT_UI_GUARD = r'''<script id="az-report-terminology-guard">
(() => {
  const exactOld='\u041a\u043b\u0438\u043d\u0438\u043a\u0430';
  const exactNew='\u041e\u0442\u0434\u0435\u043b\u0435\u043d\u0438\u0435 \u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u044b';
  const genOld='\u043a\u043b\u0438\u043d\u0438\u043a\u0438';
  const genNew='\u043e\u0442\u0434\u0435\u043b\u0435\u043d\u0438\u044f \u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u044b';
  const genCapOld='\u041a\u043b\u0438\u043d\u0438\u043a\u0438';
  const genCapNew='\u041e\u0442\u0434\u0435\u043b\u0435\u043d\u0438\u044f \u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u044b';
  const quotedOld='\u00ab'+exactOld+'\u00bb';
  const quotedNew='\u00ab'+exactNew+'\u00bb';

  function patchText(node){
    if(!node || !node.nodeValue) return;
    const parent=node.parentElement;
    if(parent && /^(SCRIPT|STYLE|NOSCRIPT|TEXTAREA)$/.test(parent.tagName)) return;
    let value=node.nodeValue;
    const trimmed=value.trim();
    if(trimmed===exactOld) value=value.replace(exactOld,exactNew);
    if(value.includes(quotedOld)) value=value.split(quotedOld).join(quotedNew);
    if(value.includes(genCapOld)) value=value.split(genCapOld).join(genCapNew);
    if(value.includes(genOld)) value=value.split(genOld).join(genNew);
    if(value!==node.nodeValue) node.nodeValue=value;
  }

  function patch(root){
    if(!root) return;
    if(root.nodeType===Node.TEXT_NODE){patchText(root);return;}
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
    let node;
    while((node=walker.nextNode())) patchText(node);
  }

  function start(){
    patch(document.body);
    const observer=new MutationObserver(mutations=>{
      for(const mutation of mutations){
        if(mutation.type==='characterData') patchText(mutation.target);
        for(const node of mutation.addedNodes) patch(node);
      }
    });
    observer.observe(document.body,{subtree:true,childList:true,characterData:true});
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start,{once:true});
  else start();
})();
</script>'''


def _replace_text(value):
    return value.replace(OLD, NEW) if isinstance(value, str) else value


def _replace_json_object(value):
    if isinstance(value, dict):
        return {
            (_replace_text(key) if isinstance(key, str) else key): _replace_json_object(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_json_object(item) for item in value]
    if isinstance(value, str):
        return _replace_text(value)
    return value


def _normalize_json_text(value):
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return _replace_text(value)
    return json.dumps(_replace_json_object(parsed), ensure_ascii=False, separators=(",", ":"))


def migrate_report_storage(db_path):
    conn = sqlite3.connect(db_path)
    try:
        for table in ("report_data", "report_blobs"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                continue
            rows = conn.execute(f"SELECT rowid, payload FROM {table}").fetchall()
            for rowid, payload in rows:
                normalized = _normalize_json_text(payload)
                if normalized != payload:
                    conn.execute(
                        f"UPDATE {table} SET payload=? WHERE rowid=?",
                        (normalized, rowid),
                    )
        conn.commit()
    finally:
        conn.close()


def install(app, report_storage):
    migrate_report_storage(report_storage.DB_PATH)

    original_validate = report_storage._validate_blob_payload
    original_normalize = report_storage._normalize_record
    original_management_rows = report_storage._management_rows_from_backup

    def validate_blob_payload(key, payload):
        try:
            normalized = original_validate(key, payload)
        except Exception:
            if isinstance(payload, str) and NEW in payload:
                normalized = original_validate(key, payload.replace(NEW, OLD))
            else:
                raise
        return _normalize_json_text(normalized)

    def normalize_record(date, record):
        return _replace_json_object(original_normalize(date, record))

    def management_rows_from_backup(raw_value):
        rows = original_management_rows(raw_value)
        return [
            (date, _normalize_json_text(payload), updated_by, updated_at)
            for date, payload, updated_by, updated_at in rows
        ]

    report_storage._validate_blob_payload = validate_blob_payload
    report_storage._normalize_record = normalize_record
    report_storage._management_rows_from_backup = management_rows_from_backup

    @app.after_request
    def rename_report_direction(response):
        path = getattr(__import__('flask').request, 'path', '')
        if not (path.startswith('/reports/') or path.startswith('/api/reports/')):
            return response
        content_type = response.headers.get('Content-Type', '')
        if not any(kind in content_type for kind in ('text/', 'javascript', 'json')):
            return response
        try:
            data = response.get_data()
            old = OLD.encode('utf-8')
            if old in data:
                response.set_data(data.replace(old, NEW.encode('utf-8')))

            if 'text/html' in content_type:
                html = response.get_data(as_text=True)
                if 'az-report-terminology-guard' not in html:
                    if '</body>' in html:
                        html = html.replace('</body>', REPORT_UI_GUARD + '\n</body>', 1)
                    else:
                        html += REPORT_UI_GUARD
                    response.set_data(html)
        except Exception:
            pass
        return response
