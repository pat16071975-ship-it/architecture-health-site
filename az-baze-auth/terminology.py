import json
import sqlite3

OLD = "Клиника"
NEW = "Отделение структуры"


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
        except Exception:
            pass
        return response
