import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app import DB_PATH


DIRECTIONS = {"pending", "dent", "structure", "lab", "ignore"}
CLINICAL_DIRECTIONS = {"dent", "structure", "lab"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_registry (
    source_name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    direction TEXT NOT NULL
        CHECK (direction IN ('pending','dent','structure','lab','ignore')),
    source_filename TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    confirmed_by INTEGER,
    confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_provider_registry_direction
    ON provider_registry(direction);

CREATE TABLE IF NOT EXISTS daily_upload_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data_date TEXT NOT NULL,
    revision INTEGER NOT NULL,
    completed_filename TEXT NOT NULL,
    services_filename TEXT NOT NULL,
    completed_sha256 TEXT NOT NULL,
    services_sha256 TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    uploaded_by INTEGER,
    uploaded_at TEXT NOT NULL,
    archived_by INTEGER,
    archived_at TEXT NOT NULL,
    decision TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_daily_upload_versions_date
    ON daily_upload_versions(data_date, revision);

CREATE TABLE IF NOT EXISTS cash_receipt_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data_date TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    imported_by INTEGER,
    imported_at TEXT NOT NULL,
    archived_by INTEGER,
    archived_at TEXT NOT NULL,
    decision TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cash_receipt_versions_date
    ON cash_receipt_versions(data_date);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_schema(conn):
    conn.executescript(SCHEMA)


def _seed_mapping(conn, mapping, direction):
    now = now_iso()
    for source_name, display_name in (mapping or {}).items():
        conn.execute(
            """
            INSERT INTO provider_registry(
                source_name,display_name,direction,source_filename,
                first_seen_at,last_seen_at,confirmed_by,confirmed_at
            ) VALUES(?,?,?,NULL,?,?,NULL,?)
            ON CONFLICT(source_name) DO NOTHING
            """,
            (str(source_name), str(display_name), direction, now, now, now),
        )


def seed_defaults(conn, ident_import):
    init_schema(conn)
    _seed_mapping(conn, ident_import.DENTISTS, "dent")
    _seed_mapping(conn, ident_import.STRUCTURE_DOCTORS, "structure")
    _seed_mapping(conn, ident_import.LAB_DOCTORS, "lab")


def refresh_runtime(conn, ident_import, cash_payments=None):
    """Apply persistent provider decisions to the in-process IDENT maps."""
    init_schema(conn)
    rows = conn.execute(
        """
        SELECT source_name,display_name,direction
        FROM provider_registry
        WHERE direction<>'pending'
        ORDER BY source_name
        """
    ).fetchall()

    for row in rows:
        source = str(row["source_name"] if hasattr(row, "keys") else row[0])
        display = str(row["display_name"] if hasattr(row, "keys") else row[1])
        direction = str(row["direction"] if hasattr(row, "keys") else row[2])

        ident_import.DENTISTS.pop(source, None)
        ident_import.STRUCTURE_DOCTORS.pop(source, None)
        ident_import.LAB_DOCTORS.pop(source, None)
        ident_import.KNOWN_STAFF.add(source)

        if direction == "dent":
            ident_import.DENTISTS[source] = display
        elif direction == "structure":
            ident_import.STRUCTURE_DOCTORS[source] = display
        elif direction == "lab":
            ident_import.LAB_DOCTORS[source] = display

    if cash_payments is not None:
        cash_payments.configure_providers(
            ident_import.DENTISTS,
            ident_import.STRUCTURE_DOCTORS,
            ident_import.LAB_DOCTORS,
        )



def bootstrap_runtime(ident_import, cash_payments=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        seed_defaults(conn, ident_import)
        refresh_runtime(conn, ident_import, cash_payments)
        conn.commit()
    finally:
        conn.close()


def pending_provider_rows(conn):
    init_schema(conn)
    rows = conn.execute(
        """
        SELECT source_name,display_name,source_filename,first_seen_at,last_seen_at
        FROM provider_registry
        WHERE direction='pending'
        ORDER BY first_seen_at,source_name
        """
    ).fetchall()
    return [
        {
            "source_name": str(row["source_name"] if hasattr(row, "keys") else row[0]),
            "display_name": str(row["display_name"] if hasattr(row, "keys") else row[1]),
            "source_filename": (
                str(row["source_filename"] if hasattr(row, "keys") else row[2])
                if (row["source_filename"] if hasattr(row, "keys") else row[2])
                else ""
            ),
            "first_seen_at": str(row["first_seen_at"] if hasattr(row, "keys") else row[3]),
            "last_seen_at": str(row["last_seen_at"] if hasattr(row, "keys") else row[4]),
        }
        for row in rows
    ]

def resolved_provider_names(conn):
    init_schema(conn)
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT source_name FROM provider_registry WHERE direction<>'pending'"
        ).fetchall()
    }


def pending_provider_names(conn):
    init_schema(conn)
    return [
        str(row[0])
        for row in conn.execute(
            """
            SELECT source_name
            FROM provider_registry
            WHERE direction='pending'
            ORDER BY source_name
            """
        ).fetchall()
    ]


def detect_unknown_providers(period, ident_import, conn=None):
    known = (
        set(ident_import.DENTISTS)
        | set(ident_import.STRUCTURE_DOCTORS)
        | set(ident_import.LAB_DOCTORS)
        | set(ident_import.KNOWN_STAFF)
    )
    if conn is not None:
        known |= resolved_provider_names(conn)

    seen = set()
    for row in period or []:
        normalized = row.get("normalized") or {}
        for item in normalized.get("items") or []:
            staff = str(item.get("staff") or "").strip()
            if staff:
                seen.add(staff)
        doctors = normalized.get("doctors") or {}
        for day in doctors.values():
            if not isinstance(day, dict):
                continue
            for kind in day.values():
                if isinstance(kind, dict):
                    seen.update(str(name).strip() for name in kind if str(name).strip())

        paid_snapshot = normalized.get("paid_snapshot") or {}
        providers = paid_snapshot.get("providers") if isinstance(paid_snapshot, dict) else {}
        if isinstance(providers, dict):
            # A provider can appear in the expanded report only because an old
            # opening debt exists. Do not force classification for dormant
            # historical rows. Snapshot-only classification is required when
            # the provider actually received an allocated payment in the
            # selected period; current service rows are already covered above.
            for name, financials in providers.items():
                if not str(name).strip() or not isinstance(financials, dict):
                    continue
                try:
                    paid = float(financials.get("paid") or 0)
                except (TypeError, ValueError):
                    paid = 0.0
                if abs(paid) > 0.004:
                    seen.add(str(name).strip())

    return sorted(name for name in seen if name not in known)


def record_pending_providers(conn, names, source_filename, actor_id=None):
    init_schema(conn)
    now = now_iso()
    for name in sorted(set(names or [])):
        conn.execute(
            """
            INSERT INTO provider_registry(
                source_name,display_name,direction,source_filename,
                first_seen_at,last_seen_at,confirmed_by,confirmed_at
            ) VALUES(?,?,'pending',?,?,?,NULL,NULL)
            ON CONFLICT(source_name) DO UPDATE SET
                last_seen_at=excluded.last_seen_at,
                source_filename=COALESCE(excluded.source_filename, provider_registry.source_filename)
            WHERE provider_registry.direction='pending'
            """,
            (name, name, source_filename, now, now),
        )


def resolve_providers(conn, decisions, actor_id):
    init_schema(conn)
    now = now_iso()
    clean = {}
    for source_name, raw in (decisions or {}).items():
        source = str(source_name or "").strip()
        if not source:
            continue
        if isinstance(raw, dict):
            direction = str(raw.get("direction") or "").strip()
            display = str(raw.get("display_name") or source).strip() or source
        else:
            direction = str(raw or "").strip()
            display = source
        if direction not in {"dent", "structure", "lab", "ignore"}:
            raise ValueError(f"Для врача «{source}» не выбрано допустимое направление.")
        clean[source] = (direction, display)

    if not clean:
        return

    for source, (direction, display) in clean.items():
        row = conn.execute(
            "SELECT source_name FROM provider_registry WHERE source_name=?",
            (source,),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE provider_registry
                SET display_name=?,direction=?,last_seen_at=?,
                    confirmed_by=?,confirmed_at=?
                WHERE source_name=?
                """,
                (display, direction, now, actor_id, now, source),
            )
        else:
            conn.execute(
                """
                INSERT INTO provider_registry(
                    source_name,display_name,direction,source_filename,
                    first_seen_at,last_seen_at,confirmed_by,confirmed_at
                ) VALUES(?,?,?,NULL,?,?,?,?)
                """,
                (source, display, direction, now, now, actor_id, now),
            )


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compare_clinical(conn, period, service_through):
    result = {
        "new": [],
        "identical": [],
        "conflict": [],
        "historical": [],
        "removed": [],
    }
    incoming_dates = sorted(str(row["data_date"]) for row in period)
    incoming_set = set(incoming_dates)
    for row in period:
        data_date = str(row["data_date"])
        existing = conn.execute(
            "SELECT normalized_json FROM daily_uploads WHERE data_date=?",
            (data_date,),
        ).fetchone()
        if existing:
            try:
                old = json.loads(existing["normalized_json"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Нельзя безопасно проверить сохранённые данные за {data_date}: версия повреждена."
                ) from exc
            bucket = "identical" if canonical(old) == canonical(row["normalized"]) else "conflict"
        elif service_through and data_date <= str(service_through):
            bucket = "historical"
        else:
            bucket = "new"
        result[bucket].append(data_date)

    if incoming_dates:
        old_rows = conn.execute(
            """
            SELECT data_date
            FROM daily_uploads
            WHERE data_date>=? AND data_date<=?
            ORDER BY data_date
            """,
            (incoming_dates[0], incoming_dates[-1]),
        ).fetchall()
        for old_row in old_rows:
            old_date = str(old_row["data_date"] if hasattr(old_row, "keys") else old_row[0])
            if old_date not in incoming_set:
                result["removed"].append(old_date)
    return result


def compare_cash(conn, daily):
    init_schema(conn)
    result = {"new": [], "identical": [], "conflict": [], "removed": []}
    incoming_dates = sorted(daily)
    incoming_set = set(incoming_dates)
    for data_date in incoming_dates:
        row = conn.execute(
            "SELECT payload_json FROM cash_receipts_daily WHERE data_date=?",
            (data_date,),
        ).fetchone()
        if not row:
            result["new"].append(data_date)
            continue
        try:
            old = json.loads(row["payload_json"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Нельзя безопасно проверить денежные данные за {data_date}: сохранённая версия повреждена."
            ) from exc
        target = "identical" if canonical(old) == canonical(daily[data_date]) else "conflict"
        result[target].append(data_date)

    if incoming_dates:
        old_rows = conn.execute(
            """
            SELECT data_date
            FROM cash_receipts_daily
            WHERE data_date>=? AND data_date<=?
            ORDER BY data_date
            """,
            (incoming_dates[0], incoming_dates[-1]),
        ).fetchall()
        for old_row in old_rows:
            old_date = str(old_row["data_date"] if hasattr(old_row, "keys") else old_row[0])
            if old_date not in incoming_set:
                result["removed"].append(old_date)
    return result


def archive_daily_row(conn, existing, actor_id, decision):
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO daily_upload_versions(
            data_date,revision,completed_filename,services_filename,
            completed_sha256,services_sha256,normalized_json,
            uploaded_by,uploaded_at,archived_by,archived_at,decision
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            existing["data_date"],
            int(existing["revision"] or 1),
            existing["completed_filename"],
            existing["services_filename"],
            existing["completed_sha256"],
            existing["services_sha256"],
            existing["normalized_json"],
            existing["uploaded_by"],
            existing["uploaded_at"],
            actor_id,
            now_iso(),
            decision,
        ),
    )


def archive_cash_row(conn, existing, actor_id, decision):
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO cash_receipt_versions(
            data_date,payload_json,source_filename,source_sha256,
            imported_by,imported_at,archived_by,archived_at,decision
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            existing["data_date"],
            existing["payload_json"],
            existing["source_filename"],
            existing["source_sha256"],
            existing["imported_by"],
            existing["imported_at"],
            actor_id,
            now_iso(),
            decision,
        ),
    )


def create_db_backup(label):
    source = Path(DB_PATH)
    if not source.is_file():
        raise RuntimeError(f"DB not found: {source}")
    root = source.parent / "reconcile-backups"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / f"{label}-{stamp}.db"

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()

    check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        status = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if status != "ok":
        target.unlink(missing_ok=True)
        raise RuntimeError("Backup integrity_check failed.")
    os.chmod(target, 0o600)
    return str(target)


def cash_reconciliation(payload):
    total = float((payload or {}).get("cashTotal") or 0)
    dent = float((payload or {}).get("dentCashOOO") or 0) + float((payload or {}).get("dentCashIP") or 0)
    structure = float((payload or {}).get("clinicCashOOO") or 0) + float((payload or {}).get("clinicCashIP") or 0)
    lab = float((payload or {}).get("labCashOOO") or 0) + float((payload or {}).get("labCashIP") or 0)
    unallocated = float((payload or {}).get("cashUnallocated") or 0)
    delta = round(total - dent - structure - lab - unallocated, 2)
    return {
        "total": round(total, 2),
        "dent": round(dent, 2),
        "structure": round(structure, 2),
        "lab": round(lab, 2),
        "unallocated": round(unallocated, 2),
        "delta": delta,
    }


def assert_cash_reconciliation(payload):
    check = cash_reconciliation(payload)
    if abs(check["delta"]) > 0.01:
        raise ValueError(
            "Нарушен баланс денежных данных: общий Факт не равен "
            "Стоматология + Отделение структуры + Лаборатория + Нераспределённые ДС."
        )
    return check
