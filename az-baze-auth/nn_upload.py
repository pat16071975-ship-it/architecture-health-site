import hashlib
import os
import shutil
import sqlite3
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
            try:
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
                        payload = nn_normalize.normalize_batch(conn, batch_id, UPLOAD_ROOT)
                        result = {
                            "status": "ready",
                            "batch_id": batch_id,
                            "message": (
                                "Пять обязательных файлов сохранены и обработаны. "
                                + ("Дополнительный BI-файл подключён. " if "doctor_services_payments" in items else "")
                                + f"Период: {payload['period']['start'] or '—'} — {payload['period']['end'] or '—'}."
                            ),
                        }
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
            except ValueError as exc:
                error = str(exc)
            except Exception:
                error = "Не удалось сохранить набор файлов. Данные не изменены."

    latest = None
    latest_files = {}
    if selected_clinic:
        latest, latest_files = _latest_batch(conn, int(selected_clinic["id"]))

    return render_template(
        "nn_uploads.html",
        clinics=clinics,
        selected_clinic=selected_clinic,
        source_slots=SOURCE_SLOTS,
        optional_source_slots=OPTIONAL_SOURCE_SLOTS,
        latest=latest,
        latest_files=latest_files,
        result=result,
        error=error,
        csrf=csrf_token(),
    )
