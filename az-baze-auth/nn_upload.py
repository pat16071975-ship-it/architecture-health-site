import hashlib
import json
import os
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from flask import g, render_template, request

from app import DB_PATH, csrf_token, db, is_owner, iso_now, require_csrf


UPLOAD_ROOT = Path(os.environ.get("AZBAZE_NN_UPLOAD_ROOT", "/var/lib/az-baze/nn-uploads"))

DEFAULT_NN_CLINICS = (
    "Клиника 1 (Толи Бе)",
    "Клиника 2 (Шевчеко)",
)

SOURCE_SLOTS = (
    ("appointments_registry", "Реестр приемов", ".xls", "01_appointments_registry.xls"),
    ("medical_records", "Отчет по медицинским записям", ".xls", "02_medical_records.xls"),
    ("services_detailed", "Отчет по услугам подробно", ".pdf", "03_services_detailed.pdf"),
    ("patients_general", "Общий отчет по пациентам", ".xls", "04_patients_general.xls"),
    ("deleted_appointments", "Отчет по удаленным приемам", ".pdf", "05_deleted_appointments.pdf"),
)

OPTIONAL_SOURCE_SLOTS = (
    ("doctor_services_payments", "Оказанные врачами услуги", ".pdf", "06_doctor_services_payments.pdf"),
)

UPLOAD_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS nn_clinics (
    clinic_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nn_upload_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clinic_id INTEGER NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploaded'
        CHECK (status IN ('uploaded','processing','ready','error')),
    uploaded_by INTEGER,
    uploaded_at TEXT NOT NULL,
    UNIQUE (clinic_id, bundle_sha256),
    FOREIGN KEY (clinic_id) REFERENCES nn_clinics(clinic_id) ON DELETE RESTRICT,
    FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS nn_upload_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    source_key TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    mime_type TEXT NOT NULL DEFAULT '',
    UNIQUE (batch_id, source_key),
    FOREIGN KEY (batch_id) REFERENCES nn_upload_batches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_nn_upload_batches_clinic_time
    ON nn_upload_batches(clinic_id, uploaded_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_nn_upload_files_batch
    ON nn_upload_files(batch_id, source_key);
"""


def _table_exists(conn, table):
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def _column_names(conn, table):
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_legacy_nn_schema_if_empty(conn):
    columns = _column_names(conn, "nn_clinics")
    if not columns or "name" in columns:
        return

    tracked = (
        "nn_normalized_batches",
        "nn_upload_files",
        "nn_upload_batches",
        "nn_clinics",
    )
    nonempty = []
    for table in tracked:
        if _table_exists(conn, table):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count:
                nonempty.append(f"{table}={count}")
    if nonempty:
        raise RuntimeError(
            "Нельзя автоматически отделить НН-клиники от общего справочника: "
            "в старой НН-схеме уже есть данные (" + ", ".join(nonempty) + ")."
        )

    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in tracked:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _init_schema():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        _migrate_legacy_nn_schema_if_empty(conn)
        conn.executescript(UPLOAD_SCHEMA)
        now = iso_now()
        conn.execute(
            """
            UPDATE nn_clinics
            SET name=?,updated_at=?
            WHERE clinic_id=2 AND name=?
            """,
            ("Клиника 2 (Шевчеко)", now, "Клиника 2 (другая)"),
        )
        if conn.execute("SELECT COUNT(*) FROM nn_clinics").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO nn_clinics(name,active,created_at,updated_at) VALUES(?,1,?,?)",
                [(name, now, now) for name in DEFAULT_NN_CLINICS],
            )
        conn.commit()
    finally:
        conn.close()


def _clinic_rows(conn):
    if not _table_exists(conn, "nn_clinics"):
        return []
    return conn.execute(
        """
        SELECT clinic_id AS id,name,'' AS address
        FROM nn_clinics
        WHERE active=1
        ORDER BY clinic_id
        """
    ).fetchall()


def _active_clinic(conn, clinic_id):
    if not _table_exists(conn, "nn_clinics"):
        return None
    return conn.execute(
        """
        SELECT clinic_id AS id,name,'' AS address
        FROM nn_clinics
        WHERE clinic_id=? AND active=1
        """,
        (clinic_id,),
    ).fetchone()


def _clean_original_name(value):
    normalized = str(value or "").replace("\\", "/")
    name = Path(normalized).name.strip().replace("\x00", "")
    return name[:255]


def _private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _read_slot(file_storage, label, extension):
    if not file_storage or not file_storage.filename:
        raise ValueError(f"Выберите файл «{label}».")
    original = _clean_original_name(file_storage.filename)
    if not original.lower().endswith(extension):
        raise ValueError(f"Для «{label}» требуется файл {extension}.")
    raw = file_storage.read()
    if not raw:
        raise ValueError(f"Файл «{label}» пуст.")
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError(f"Файл «{label}» превышает 25 МБ.")
    if extension == ".pdf" and not raw.startswith(b"%PDF-"):
        raise ValueError(f"Файл «{label}» не распознан как PDF.")
    return {
        "original_filename": original,
        "raw": raw,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
        "mime_type": str(file_storage.mimetype or ""),
    }


def _bundle_hash(items):
    digest = hashlib.sha256()
    for source_key, _label, _ext, _stored in SOURCE_SLOTS + OPTIONAL_SOURCE_SLOTS:
        if source_key not in items:
            continue
        digest.update(source_key.encode("utf-8"))
        digest.update(b":")
        digest.update(items[source_key]["sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _latest_batch(conn, clinic_id):
    row = conn.execute(
        """
        SELECT id,clinic_id,bundle_sha256,status,uploaded_by,uploaded_at
        FROM nn_upload_batches
        WHERE clinic_id=?
        ORDER BY uploaded_at DESC,id DESC
        LIMIT 1
        """,
        (clinic_id,),
    ).fetchone()
    if not row:
        return None, {}
    files = conn.execute(
        """
        SELECT source_key,original_filename,sha256,size_bytes,mime_type
        FROM nn_upload_files
        WHERE batch_id=?
        ORDER BY id
        """,
        (row["id"],),
    ).fetchall()
    return row, {item["source_key"]: item for item in files}


def _store_bundle(conn, clinic_id, user_id, items):
    bundle_sha256 = _bundle_hash(items)
    duplicate = conn.execute(
        """
        SELECT id,status,uploaded_at
        FROM nn_upload_batches
        WHERE clinic_id=? AND bundle_sha256=?
        """,
        (clinic_id, bundle_sha256),
    ).fetchone()
    if duplicate:
        return {
            "status": "duplicate",
            "batch_id": duplicate["id"],
            "existing_status": duplicate["status"],
            "message": "Этот набор файлов для выбранной клиники уже загружен. Данные не изменены.",
        }

    now = iso_now()
    batch_dir = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            INSERT INTO nn_upload_batches(
                clinic_id,bundle_sha256,status,uploaded_by,uploaded_at
            ) VALUES(?,?,'uploaded',?,?)
            """,
            (clinic_id, bundle_sha256, user_id, now),
        )
        batch_id = cur.lastrowid
        _private_dir(UPLOAD_ROOT)
        clinic_dir = _private_dir(UPLOAD_ROOT / str(clinic_id))
        batch_dir = clinic_dir / str(batch_id)
        batch_dir.mkdir(exist_ok=False, mode=0o700)
        os.chmod(batch_dir, 0o700)

        for source_key, _label, _ext, stored_filename in SOURCE_SLOTS + OPTIONAL_SOURCE_SLOTS:
            if source_key not in items:
                continue
            item = items[source_key]
            target = batch_dir / stored_filename
            with target.open("xb") as handle:
                handle.write(item["raw"])
            os.chmod(target, 0o600)
            conn.execute(
                """
                INSERT INTO nn_upload_files(
                    batch_id,source_key,original_filename,stored_filename,
                    sha256,size_bytes,mime_type
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    batch_id,
                    source_key,
                    item["original_filename"],
                    stored_filename,
                    item["sha256"],
                    item["size_bytes"],
                    item["mime_type"],
                ),
            )
        conn.commit()
        optional_count = sum(1 for key, *_rest in OPTIONAL_SOURCE_SLOTS if key in items)
        return {
            "status": "uploaded",
            "batch_id": batch_id,
            "message": (
                "Пять обязательных файлов сохранены для выбранной клиники."
                + (" Дополнительный BI-файл также сохранён." if optional_count else "")
            ),
        }
    except Exception:
        conn.rollback()
        if batch_dir and batch_dir.exists():
            shutil.rmtree(batch_dir, ignore_errors=True)
        raise



_OVERLAP_EVENT_SOURCES = (
    ("visits", "date"),
    ("medical_records", "date"),
    ("deleted_appointments", "appointment_date"),
)
_OVERLAP_IGNORED_FIELDS = {"patient_key", "service_class", "class"}


def _date_range(start, end):
    if not start or not end:
        return []
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if last < first:
        return []
    result = []
    current = first
    while current <= last:
        result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def _compress_dates(values):
    days = sorted({date.fromisoformat(value) for value in values if value})
    if not days:
        return []
    ranges = []
    start = previous = days[0]
    for current in days[1:]:
        if current == previous + timedelta(days=1):
            previous = current
            continue
        ranges.append({"start": start.isoformat(), "end": previous.isoformat()})
        start = previous = current
    ranges.append({"start": start.isoformat(), "end": previous.isoformat()})
    return ranges


def _display_date(value):
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return str(value or "—")


def _display_ranges(values):
    labels = []
    for item in _compress_dates(values):
        start = _display_date(item["start"])
        end = _display_date(item["end"])
        labels.append(start if item["start"] == item["end"] else f"{start}–{end}")
    return ", ".join(labels) if labels else "—"


def _stable_value(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    if isinstance(value, dict):
        return {key: _stable_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_stable_value(item) for item in value]
    return str(value).strip()


def _patient_refs(patient):
    refs = set()
    for value in patient.get("charts", []) or []:
        value = str(value or "").strip()
        if value:
            refs.add("chart:" + value)
    chart = str(patient.get("chart") or "").strip()
    if chart:
        refs.add("chart:" + chart)

    iin = str(patient.get("iin") or "").strip()
    if iin:
        refs.add("iin:" + iin)

    for value in patient.get("phones", []) or []:
        value = str(value or "").strip()
        if value:
            refs.add("phone:" + value)
    phone = str(patient.get("phone") or "").strip()
    if phone:
        refs.add("phone:" + phone)

    name = " ".join(str(patient.get("name") or "").lower().replace("ё", "е").split())
    dob = str(patient.get("dob") or "").strip()
    if name and dob:
        refs.add(f"name_dob:{name}|{dob}")
    return refs


def _patient_profile(patient):
    return {
        key: _stable_value(patient.get(key))
        for key in ("name", "dob", "gender", "source", "iin", "chart", "phone", "note")
    }


def _patient_index(payload):
    patients = list(payload.get("patients", []))
    by_key = {
        row.get("key"): row for row in patients if row.get("key")
    }
    by_ref = {}
    for row in patients:
        for ref in _patient_refs(row):
            by_ref.setdefault(ref, []).append(row)
    return by_key, by_ref


def _find_patient(by_ref, refs):
    matches = []
    seen = set()
    for ref in refs:
        for row in by_ref.get(ref, []):
            marker = id(row)
            if marker not in seen:
                seen.add(marker)
                matches.append(row)
    if len(matches) == 1:
        return matches[0]
    return None


def _patient_conflicts_for_day(existing, candidate, day):
    candidate_by_key, _candidate_by_ref = _patient_index(candidate)
    _existing_by_key, existing_by_ref = _patient_index(existing)
    keys = {
        row.get("patient_key")
        for source, date_field in _OVERLAP_EVENT_SOURCES[:2]
        for row in _rows_for_day(candidate, source, date_field, day)
        if row.get("patient_key")
    }
    conflicts = set()
    for key in keys:
        candidate_patient = candidate_by_key.get(key)
        if not candidate_patient:
            continue
        candidate_refs = _patient_refs(candidate_patient)
        existing_patient = _find_patient(existing_by_ref, candidate_refs)
        if existing_patient is None:
            conflicts.update(candidate_refs)
            continue
        if _patient_profile(existing_patient) != _patient_profile(candidate_patient):
            conflicts.update(candidate_refs)
            conflicts.update(_patient_refs(existing_patient))
    return conflicts


def _rows_for_day(payload, source, date_field, day):
    return [
        row for row in payload.get(source, [])
        if row.get(date_field) == day
    ]


def _rows_equal_for_day(existing, candidate, source, date_field, day):
    old_rows = _rows_for_day(existing, source, date_field, day)
    new_rows = _rows_for_day(candidate, source, date_field, day)
    if len(old_rows) != len(new_rows):
        return False
    if not old_rows:
        return True

    old_keys = {key for row in old_rows for key in row}
    new_keys = {key for row in new_rows for key in row}
    shared_keys = sorted((old_keys & new_keys) - _OVERLAP_IGNORED_FIELDS)

    def signature(row):
        body = {key: _stable_value(row.get(key)) for key in shared_keys}
        return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    return sorted(signature(row) for row in old_rows) == sorted(signature(row) for row in new_rows)


def _day_comparison(existing, candidate, day):
    rows_match = all(
        _rows_equal_for_day(existing, candidate, source, date_field, day)
        for source, date_field in _OVERLAP_EVENT_SOURCES
    )
    patient_conflicts = _patient_conflicts_for_day(existing, candidate, day)
    return rows_match and not patient_conflicts, patient_conflicts


def _ready_periods(conn, clinic_id, exclude_batch_id=None):
    params = [clinic_id]
    extra = ""
    if exclude_batch_id is not None:
        extra = " AND n.batch_id<>?"
        params.append(exclude_batch_id)
    rows = conn.execute(
        """
        SELECT n.period_start,n.period_end
        FROM nn_normalized_batches n
        JOIN nn_upload_batches b ON b.id=n.batch_id
        WHERE n.clinic_id=? AND b.status='ready'
        """ + extra + """
        ORDER BY b.uploaded_at,n.batch_id
        """,
        tuple(params),
    ).fetchall()
    return [
        (row["period_start"], row["period_end"])
        for row in rows
        if row["period_start"] and row["period_end"]
    ]


def _analyze_overlap(conn, clinic_id, payload, batch_id=None):
    period = payload.get("period") or {}
    candidate_dates = _date_range(period.get("start"), period.get("end"))
    if not candidate_dates:
        raise ValueError("Не удалось определить период загружаемых данных.")

    periods = _ready_periods(conn, clinic_id, exclude_batch_id=batch_id)
    loaded_dates = [
        day for day in candidate_dates
        if any(start <= day <= end for start, end in periods)
    ]
    new_dates = [day for day in candidate_dates if day not in set(loaded_dates)]

    if not loaded_dates:
        return {
            "matching_dates": [],
            "conflict_dates": [],
            "new_dates": new_dates,
            "conflict_patient_refs": [],
        }

    import nn_reports
    existing = nn_reports.effective_payload(conn, clinic_id) or {}
    matching_dates = []
    conflict_dates = []
    conflict_patient_refs = set()
    for day in loaded_dates:
        matches, patient_conflicts = _day_comparison(existing, payload, day)
        if matches:
            matching_dates.append(day)
        else:
            conflict_dates.append(day)
            conflict_patient_refs.update(patient_conflicts)

    return {
        "matching_dates": matching_dates,
        "conflict_dates": conflict_dates,
        "new_dates": new_dates,
        "conflict_patient_refs": sorted(conflict_patient_refs),
    }


def _load_normalized_payload(conn, batch_id):
    row = conn.execute(
        "SELECT payload_json FROM nn_normalized_batches WHERE batch_id=?",
        (batch_id,),
    ).fetchone()
    if not row:
        raise ValueError("Нормализованный набор не найден.")
    return json.loads(row["payload_json"])


def _store_merge_control(conn, batch_id, payload, control, status):
    payload["merge_control"] = control
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE nn_normalized_batches SET payload_json=? WHERE batch_id=?",
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), batch_id),
        )
        conn.execute(
            "UPDATE nn_upload_batches SET status=? WHERE id=?",
            (status, batch_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _finalize_overlap(conn, batch_id, analysis, decision):
    payload = _load_normalized_payload(conn, batch_id)
    matching = sorted(set(analysis.get("matching_dates", [])))
    conflicts = sorted(set(analysis.get("conflict_dates", [])))
    new_dates = sorted(set(analysis.get("new_dates", [])))
    conflict_patient_refs = sorted(set(analysis.get("conflict_patient_refs", [])))

    if decision == "keep_old":
        ignore_dates = sorted(set(matching + conflicts))
        replace_dates = []
        ignore_patient_refs = conflict_patient_refs
        replace_patient_refs = []
        active = bool(new_dates)
        use_controls = False
    elif decision == "use_new":
        ignore_dates = matching
        replace_dates = conflicts
        ignore_patient_refs = []
        replace_patient_refs = conflict_patient_refs
        active = bool(new_dates or conflicts)
        use_controls = active
    elif decision == "automatic":
        if conflicts:
            raise ValueError("Конфликт нельзя завершить автоматически.")
        ignore_dates = matching
        replace_dates = []
        ignore_patient_refs = []
        replace_patient_refs = []
        active = bool(new_dates)
        use_controls = active
    else:
        raise ValueError("Неизвестный вариант разрешения конфликта.")

    control = {
        "version": 1,
        "state": "ready",
        "decision": decision,
        "active": active,
        "use_controls": use_controls,
        "matching_dates": matching,
        "conflict_dates": conflicts,
        "new_dates": new_dates,
        "ignore_dates": ignore_dates,
        "replace_dates": replace_dates,
        "ignore_patient_refs": ignore_patient_refs,
        "replace_patient_refs": replace_patient_refs,
    }
    _store_merge_control(conn, batch_id, payload, control, "ready")
    return control


def _mark_overlap_conflict(conn, batch_id, analysis):
    payload = _load_normalized_payload(conn, batch_id)
    control = {
        "version": 1,
        "state": "conflict",
        "decision": None,
        "active": False,
        "use_controls": False,
        "matching_dates": sorted(set(analysis.get("matching_dates", []))),
        "conflict_dates": sorted(set(analysis.get("conflict_dates", []))),
        "new_dates": sorted(set(analysis.get("new_dates", []))),
        "ignore_dates": [],
        "replace_dates": [],
        "conflict_patient_refs": sorted(set(analysis.get("conflict_patient_refs", []))),
        "ignore_patient_refs": [],
        "replace_patient_refs": [],
    }
    _store_merge_control(conn, batch_id, payload, control, "processing")
    return control


def _overlap_message(control):
    matching = control.get("matching_dates", [])
    conflicts = control.get("conflict_dates", [])
    new_dates = control.get("new_dates", [])
    decision = control.get("decision")

    parts = []
    if matching:
        parts.append(
            f"Данные за {_display_ranges(matching)} уже были загружены и совпадают."
        )
    if decision == "keep_old" and conflicts:
        parts.append(
            f"По отличающимся данным за {_display_ranges(conflicts)} оставлены ранее загруженные данные."
        )
    elif decision == "use_new" and conflicts:
        parts.append(
            f"Данные за {_display_ranges(conflicts)} заменены новыми по вашему выбору."
        )
    if new_dates:
        parts.append(f"Добавлены новые данные за {_display_ranges(new_dates)}.")
    if matching and not conflicts and not new_dates:
        parts.append("Новые данные не добавлялись.")
    elif not parts:
        parts.append("Новые данные не добавлялись.")
    return " ".join(parts)


def _pending_conflicts(conn, clinic_id):
    if not _table_exists(conn, "nn_normalized_batches"):
        return []
    rows = conn.execute(
        """
        SELECT n.batch_id,n.payload_json,b.uploaded_at
        FROM nn_normalized_batches n
        JOIN nn_upload_batches b ON b.id=n.batch_id
        WHERE n.clinic_id=? AND b.status='processing'
        ORDER BY b.uploaded_at,n.batch_id
        """,
        (clinic_id,),
    ).fetchall()
    result = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except Exception:
            continue
        control = payload.get("merge_control") or {}
        if control.get("version") != 1 or control.get("state") != "conflict":
            continue
        result.append({
            "batch_id": row["batch_id"],
            "uploaded_at": row["uploaded_at"],
            "conflict_label": _display_ranges(control.get("conflict_dates", [])),
            "matching_label": _display_ranges(control.get("matching_dates", [])),
            "new_label": _display_ranges(control.get("new_dates", [])),
        })
    return result


def _resolve_overlap(conn, clinic_id, batch_id, choice):
    row = conn.execute(
        """
        SELECT b.id,b.status,n.payload_json
        FROM nn_upload_batches b
        JOIN nn_normalized_batches n ON n.batch_id=b.id
        WHERE b.id=? AND b.clinic_id=?
        """,
        (batch_id, clinic_id),
    ).fetchone()
    if not row or row["status"] != "processing":
        raise ValueError("Конфликтный набор не найден или уже обработан.")

    payload = json.loads(row["payload_json"])
    control = payload.get("merge_control") or {}
    if control.get("version") != 1 or control.get("state") != "conflict":
        raise ValueError("Для этого набора нет ожидающего решения по наложению.")

    decision = "keep_old" if choice == "keep_old" else "use_new" if choice == "use_new" else None
    if not decision:
        raise ValueError("Выберите, какие данные оставить.")

    analysis = {
        "matching_dates": control.get("matching_dates", []),
        "conflict_dates": control.get("conflict_dates", []),
        "new_dates": control.get("new_dates", []),
        "conflict_patient_refs": control.get("conflict_patient_refs", []),
    }
    return _finalize_overlap(conn, batch_id, analysis, decision)

def handle_uploads_page():
    _init_schema()
    conn = db()
    result = None
    error = None

    clinics = _clinic_rows(conn)

    raw_clinic_id = request.form.get("clinic_id") if request.method == "POST" else request.args.get("clinic_id")
    try:
        selected_clinic_id = int(raw_clinic_id) if raw_clinic_id else (int(clinics[0]["id"]) if clinics else None)
    except (TypeError, ValueError):
        selected_clinic_id = None

    selected_clinic = _active_clinic(conn, selected_clinic_id) if selected_clinic_id else None
    if selected_clinic_id and not selected_clinic:
        error = "Выбранная клиника не найдена или отключена."

    if request.method == "POST":
        require_csrf()
        if not selected_clinic:
            error = error or "Сначала выберите клинику."
        else:
            action = request.form.get("action", "upload")
            try:
                if action == "resolve_overlap":
                    try:
                        batch_id = int(request.form.get("batch_id", ""))
                    except (TypeError, ValueError):
                        raise ValueError("Не указан конфликтный набор.")
                    control = _resolve_overlap(
                        conn,
                        int(selected_clinic["id"]),
                        batch_id,
                        request.form.get("choice", ""),
                    )
                    result = {
                        "status": "ready",
                        "batch_id": batch_id,
                        "message": _overlap_message(control),
                    }
                elif action == "upload":
                    items = {}
                    for source_key, label, extension, _stored in SOURCE_SLOTS:
                        items[source_key] = _read_slot(
                            request.files.get(source_key),
                            label,
                            extension,
                        )
                    for source_key, label, extension, _stored in OPTIONAL_SOURCE_SLOTS:
                        uploaded = request.files.get(source_key)
                        if uploaded and uploaded.filename:
                            items[source_key] = _read_slot(uploaded, label, extension)

                    result = _store_bundle(
                        conn,
                        int(selected_clinic["id"]),
                        int(g.user["id"]),
                        items,
                    )
                    should_normalize = (
                        result["status"] == "uploaded"
                        or result.get("existing_status") in {"uploaded", "error"}
                    )
                    if should_normalize:
                        import nn_normalize
                        batch_id = int(result["batch_id"])
                        try:
                            payload = nn_normalize.normalize_batch(
                                conn,
                                batch_id,
                                UPLOAD_ROOT,
                                mark_ready=False,
                            )
                            analysis = _analyze_overlap(
                                conn,
                                int(selected_clinic["id"]),
                                payload,
                                batch_id=batch_id,
                            )
                            if analysis["conflict_dates"]:
                                control = _mark_overlap_conflict(conn, batch_id, analysis)
                                message = (
                                    f"Обнаружены отличия в ранее загруженных данных за "
                                    f"{_display_ranges(control['conflict_dates'])}. "
                                    "До вашего выбора этот набор не влияет на отчёты и BI."
                                )
                                if control["matching_dates"]:
                                    message += (
                                        f" Совпадающие данные за "
                                        f"{_display_ranges(control['matching_dates'])} повторно не загружаются."
                                    )
                                if control["new_dates"]:
                                    message += (
                                        f" Новые данные за {_display_ranges(control['new_dates'])} "
                                        "будут добавлены после разрешения конфликта."
                                    )
                                result = {
                                    "status": "conflict",
                                    "batch_id": batch_id,
                                    "message": message,
                                }
                            else:
                                control = _finalize_overlap(
                                    conn,
                                    batch_id,
                                    analysis,
                                    "automatic",
                                )
                                result = {
                                    "status": "ready",
                                    "batch_id": batch_id,
                                    "message": _overlap_message(control),
                                }
                            if "doctor_services_payments" in items:
                                result["message"] += " Дополнительный BI-файл сохранён."
                        except Exception:
                            conn.execute(
                                "UPDATE nn_upload_batches SET status='error' WHERE id=?",
                                (batch_id,),
                            )
                            conn.commit()
                            error = (
                                "Файлы сохранены, но автоматическая обработка не завершена. "
                                "Набор помечен как ошибка обработки."
                            )
                else:
                    raise ValueError("Неизвестное действие загрузки.")
            except ValueError as exc:
                error = str(exc)
            except Exception:
                error = "Не удалось обработать набор файлов. Рабочие данные не изменены."

    latest = None
    latest_files = {}
    pending_conflicts = []
    if selected_clinic:
        clinic_id = int(selected_clinic["id"])
        latest, latest_files = _latest_batch(conn, clinic_id)
        pending_conflicts = _pending_conflicts(conn, clinic_id)

    return render_template(
        "nn_uploads.html",
        clinics=clinics,
        selected_clinic=selected_clinic,
        source_slots=SOURCE_SLOTS,
        optional_source_slots=OPTIONAL_SOURCE_SLOTS,
        latest=latest,
        latest_files=latest_files,
        pending_conflicts=pending_conflicts,
        result=result,
        error=error,
        csrf=csrf_token(),
    )

