"""Lossless IDENT originals, private SQLite, no derived-report writes.

Raw files are immutable content-addressed BLOBs. XLSX is already ZIP.
No source is made active by archiving it. The existing import decisions
remain authoritative; archive receipts describe what the importer did.
"""
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import PurePath

MAX_SOURCE_BYTES = 25 * 1024 * 1024
KINDS = frozenset(("services", "paid_services", "cash", "completed", "catalog"))
HEX = re.compile(r"[0-9a-f]{64}\Z")
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS mis_source_objects(
        sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64),
        size_bytes INTEGER NOT NULL CHECK(size_bytes>0),
        raw BLOB NOT NULL CHECK(length(raw)=size_bytes),
        first_saved_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS mis_source_events(
        event_key TEXT PRIMARY KEY CHECK(length(event_key)=64),
        sha256 TEXT NOT NULL REFERENCES mis_source_objects(sha256),
        source_kind TEXT NOT NULL CHECK(source_kind IN ('services','paid_services','cash','completed','catalog')),
        original_filename TEXT NOT NULL,
        source_from TEXT, source_to TEXT,
        origin TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK(outcome IN ('applied','duplicate','kept_old','archive_only')),
        decision TEXT NOT NULL,
        observed_dates_json TEXT,
        applied_dates_json TEXT,
        removed_dates_json TEXT,
        actor_id INTEGER,
        saved_at TEXT NOT NULL,
        context_json TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS ix_mis_source_events_kind_range
       ON mis_source_events(source_kind,source_from,source_to)""",
    """CREATE INDEX IF NOT EXISTS ix_mis_source_events_sha
       ON mis_source_events(sha256)""",
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Invalid archive date")
    return value


def _dates(values):
    return None if values is None else sorted({_day(v) for v in values})


def _filename(value):
    name = PurePath(str(value or "").replace("\\", "/")).name.strip()
    name = "".join(ch for ch in name if ord(ch) >= 32 and ord(ch) != 127)
    return name[:255] or "source"


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def init_schema(conn):
    # No executescript/commit: DDL rolls back with the caller's business writes.
    if not conn.in_transaction:
        raise RuntimeError("Archive schema requires an explicit transaction")
    for statement in SCHEMA:
        conn.execute(statement)


def record(conn, kind, raw, filename, *, source_from=None, source_to=None,
           observed=None, applied=None, removed=None, decision="append",
           outcome="applied", origin="ident_upload", actor_id=None,
           saved_at=None, context=None, expected_sha=None):
    """Must run INSIDE the caller's transaction, before its successful commit.

    No implicit commit, no writes to business tables. One copy per SHA256;
    even an identical hash is checked against the stored raw bytes. A receipt
    records observed/selected dates, never infers a calendar zero or activates
    a historic version. Explicit period bounds are distinct from observations.
    """
    if not conn.in_transaction:
        raise RuntimeError("Archive record requires an explicit transaction")
    if kind not in KINDS or outcome not in ("applied", "duplicate", "kept_old", "archive_only"):
        raise ValueError("Invalid archive source classification")
    if not isinstance(raw, bytes) or not (0 < len(raw) <= MAX_SOURCE_BYTES):
        raise ValueError("Invalid archive source size")
    start = _day(source_from) if source_from is not None else None
    end = _day(source_to) if source_to is not None else None
    if (start is None) != (end is None) or (start is not None and start > end):
        raise ValueError("Invalid archive interval")
    observed, applied, removed = _dates(observed), _dates(applied), _dates(removed)
    if observed is not None and start is not None and any(not start <= d <= end for d in observed):
        raise ValueError("Observation outside archive source interval")
    if outcome != "applied" and (applied or removed):
        raise ValueError("Archive-only receipt cannot apply business dates")
    if not isinstance(origin, str) or not origin or len(origin) > 80:
        raise ValueError("Invalid archive origin")
    if actor_id is not None and (isinstance(actor_id, bool) or not isinstance(actor_id, int)):
        raise ValueError("Invalid archive actor")
    sha = hashlib.sha256(raw).hexdigest()
    if expected_sha is not None and sha != expected_sha:
        raise ValueError("Archive source SHA256 mismatch")
    now = saved_at or _timestamp()
    name = _filename(filename)
    metadata = {
        "sha256": sha, "kind": kind, "name": name,
        "from": start, "to": end, "origin": origin,
        "outcome": outcome, "decision": str(decision or "append"),
        "observed": observed, "applied": applied, "removed": removed,
        "context": context or {},
    }
    # Historical and no-op replays are idempotent. Successful applications
    # keep the occurrence time/actor (a previously replaced version may return).
    event = dict(metadata)
    if outcome == "applied":
        event.update(actor_id=actor_id, saved_at=now)
    key = hashlib.sha256(_json(event).encode("utf-8")).hexdigest()

    init_schema(conn)
    conn.execute(
        "INSERT INTO mis_source_objects(sha256,size_bytes,raw,first_saved_at) "
        "VALUES(?,?,?,?) ON CONFLICT(sha256) DO NOTHING",
        (sha, len(raw), raw, now),
    )
    existing = conn.execute("SELECT size_bytes,raw FROM mis_source_objects WHERE sha256=?",
                            (sha,)).fetchone()
    if existing is None or existing[0] != len(raw) or existing[1] != raw:
        raise ValueError("Stored archive source integrity mismatch")
    conn.execute(
        """INSERT INTO mis_source_events(
            event_key,sha256,source_kind,original_filename,source_from,source_to,
            origin,outcome,decision,observed_dates_json,applied_dates_json,
            removed_dates_json,actor_id,saved_at,context_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key) DO NOTHING""",
        (key,sha,kind,name,start,end,origin,outcome,metadata["decision"],
         None if observed is None else _json(observed),
         None if applied is None else _json(applied),
         None if removed is None else _json(removed),
         actor_id,now,_json(metadata["context"])),
    )
    return sha


def record_noop(conn, sources):
    """Archive an already validated no-op without changing business data.

    Do not commit a caller's pending transaction. All source objects and
    receipts (including a two-file clinical upload) succeed or roll back.
    """
    if conn.in_transaction:
        raise RuntimeError("No-op archive requires a clean transaction boundary")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for args, kwargs in sources:
            record(conn, *args, **kwargs)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def read_original(conn, sha):
    """Private read helper, not an HTTP endpoint. Check every recovered byte."""
    if not isinstance(sha, str) or not HEX.fullmatch(sha):
        raise ValueError("Invalid archive SHA256")
    row = conn.execute("SELECT size_bytes,raw FROM mis_source_objects WHERE sha256=?",
                       (sha,)).fetchone()
    if row is None:
        raise KeyError("Archive source not found")
    if len(row[1]) != row[0] or hashlib.sha256(row[1]).hexdigest() != sha:
        raise ValueError("Archive source integrity mismatch")
    return row[1]


def specs(kind, raw, filename, dates, *, applied=None, removed=None,
          decision=None, actor_id=None, saved_at=None, origin="ident_upload",
          outcome=None, explicit_bounds=None):
    """Build source metadata using observed dates, not filename month guesses."""
    days = _dates(dates)
    bounds = explicit_bounds or ((days[0], days[-1]) if days else (None, None))
    return ((kind, raw, filename), dict(
        source_from=bounds[0], source_to=bounds[1], observed=days,
        applied=applied, removed=removed, decision=decision or "append",
        actor_id=actor_id, saved_at=saved_at, origin=origin,
        outcome=outcome or ("applied" if applied or removed else
                            "kept_old" if decision == "keep_old" else "duplicate"),
        context={"interval_basis": "explicit_source" if explicit_bounds else "observed_dates"},
    ))
