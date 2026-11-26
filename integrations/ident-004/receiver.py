"""IDENT-004 offline reference receiver. No network, no live-system writes.

A dedicated private SQLite file is initialized for exactly one approved source
and stream. The CLI is a local test tool, NOT an authenticated HTTP endpoint.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent
DATASET = "payments_daily_v1"
SCOPE = "whole_source_database"
MAX_BYTES = 512 * 1024
MAX_INT = 2**53 - 1
APP_ID = 0x49423034  # IB04
STORE_VERSION = 1
ZERO = Decimal(0)
ROOT_KEYS = set("contract_version dataset scope source_id stream_id batch_id sequence source_timezone date_from date_to_exclusive query_started_at observed_at coverage query_sha256 schema rows".split())
ROW_KEYS = set("date records_count paying_patients positive_amount negative_amount net_amount".split())
MONEY_FIELDS = ("positive_amount", "negative_amount", "net_amount")
MONEY_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")


class Rejected(ValueError):
    """Safe, non-sensitive rejection reason."""


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise Rejected(reason)


def query_sha256() -> str:
    sql = (ROOT / "sql/payments_daily_v1.sql").read_text(encoding="utf-8-sig")
    return sha256(sql.encode("utf-8")).hexdigest()


def uuid_value(value: object) -> str:
    require(isinstance(value, str), "INVALID_UUID")
    try:
        result = UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise Rejected("INVALID_UUID") from exc
    require(result.int != 0 and str(result) == value, "NONCANONICAL_UUID")
    return value


def integer(value: object, minimum=0, maximum=MAX_INT) -> int:
    require(type(value) is int and minimum <= value <= maximum, "INVALID_INTEGER")
    return value


def date_value(value: object) -> date:
    require(isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)), "INVALID_DATE")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise Rejected("INVALID_DATE") from exc


def utc_value(value: object) -> datetime:
    require(isinstance(value, str) and bool(UTC_RE.fullmatch(value)), "INVALID_UTC_TIMESTAMP")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise Rejected("INVALID_UTC_TIMESTAMP") from exc


def tz_value(value: object) -> ZoneInfo:
    require(isinstance(value, str) and 0 < len(value) <= 100, "INVALID_SOURCE_TIMEZONE")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise Rejected("INVALID_SOURCE_TIMEZONE") from exc


def money(value: object, scale: int) -> Decimal:
    require(isinstance(value, str) and len(value) <= 80 and bool(MONEY_RE.fullmatch(value)), "INVALID_DECIMAL_STRING")
    whole, _, fractional = value.lstrip("-").partition(".")
    require(len(fractional) <= scale and len(whole.lstrip("0")) <= 38 - scale,
            "DECIMAL_PRECISION_OR_SCALE_OVERFLOW")
    result = Decimal(value)
    return result


def exact_keys(value: object, keys: set[str], reason: str) -> None:
    require(isinstance(value, dict) and set(value) == keys, reason)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def reject_constant(_):
    raise Rejected("INVALID_JSON_CONSTANT")


@dataclass(frozen=True)
class Identity:
    source_id: str
    stream_id: str
    source_timezone: str

    def __post_init__(self):
        uuid_value(self.source_id)
        uuid_value(self.stream_id)
        tz_value(self.source_timezone)


def validate(raw: bytes, expected: Identity) -> dict:
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_BYTES, "PAYLOAD_SIZE")
    try:
        p = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    except Rejected:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise Rejected("INVALID_JSON") from exc
    exact_keys(p, ROOT_KEYS, "INVALID_ENVELOPE_FIELDS")
    require(type(p["contract_version"]) is int and p["contract_version"] == 1, "UNKNOWN_CONTRACT")
    require(p["dataset"] == DATASET and p["scope"] == SCOPE, "UNKNOWN_DATASET_OR_SCOPE")
    require(uuid_value(p["source_id"]) == expected.source_id and uuid_value(p["stream_id"]) == expected.stream_id,
            "SOURCE_OR_STREAM_MISMATCH")
    uuid_value(p["batch_id"])
    integer(p["sequence"], 1)
    require(p["source_timezone"] == expected.source_timezone, "SOURCE_TIMEZONE_MISMATCH")
    tz = tz_value(p["source_timezone"])
    start, end = date_value(p["date_from"]), date_value(p["date_to_exclusive"])
    days = (end - start).days
    require(1 <= days <= 31, "INVALID_DATE_WINDOW")
    started, finished = utc_value(p["query_started_at"]), utc_value(p["observed_at"])
    require(started <= finished, "INVALID_OBSERVATION_ORDER")
    require(end.toordinal() <= finished.astimezone(tz).date().toordinal() + 1, "FUTURE_DATE_WINDOW")
    require(p["coverage"] == "full_query_window_as_observed", "UNSUPPORTED_COVERAGE")
    require(p["query_sha256"] == query_sha256(), "UNKNOWN_QUERY_FINGERPRINT")
    exact_keys(p["schema"], {"payment_precision", "payment_scale"}, "INVALID_SCHEMA_FIELDS")
    precision = integer(p["schema"]["payment_precision"], 1, 38)
    scale = integer(p["schema"]["payment_scale"], 0, precision)
    rows = p["rows"]
    require(isinstance(rows, list) and len(rows) == days, "INCOMPLETE_DATE_COVERAGE")
    for i, row in enumerate(rows):
        exact_keys(row, ROW_KEYS, "INVALID_ROW_FIELDS")
        require(date_value(row["date"]) == start + timedelta(days=i), "UNORDERED_OR_DUPLICATE_DAY")
        count, patients = integer(row["records_count"]), integer(row["paying_patients"])
        require(patients <= count, "INVALID_PATIENT_COUNT")
        positive, negative, net = (money(row[k], scale) for k in MONEY_FIELDS)
        with localcontext() as ctx:
            ctx.prec = 80
            require(positive >= ZERO and negative <= ZERO and positive + negative == net, "MONEY_BALANCE_MISMATCH")
        require((patients > 0) == (positive > ZERO), "PATIENT_PAYMENT_MISMATCH")
        require(count > 0 or (patients == 0 and positive == negative == net == ZERO), "INVALID_ZERO_DAY")
    return p


DDL = (
    """CREATE TABLE IF NOT EXISTS source_identity (
         singleton INTEGER PRIMARY KEY CHECK(singleton=1), source_id TEXT NOT NULL,
         stream_id TEXT NOT NULL, source_timezone TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS batches (
         sequence INTEGER PRIMARY KEY, batch_id TEXT NOT NULL UNIQUE,
         payload_sha256 TEXT NOT NULL, payload BLOB NOT NULL,
         date_from TEXT NOT NULL, date_to_exclusive TEXT NOT NULL,
         observed_at TEXT NOT NULL, accepted_at TEXT NOT NULL,
         payment_precision INTEGER NOT NULL, payment_scale INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS daily (
         date TEXT PRIMARY KEY, sequence INTEGER NOT NULL REFERENCES batches(sequence),
         records_count INTEGER NOT NULL, paying_patients INTEGER NOT NULL,
         positive_amount TEXT NOT NULL, negative_amount TEXT NOT NULL, net_amount TEXT NOT NULL)""",
)


def connect(path: Path, readonly=False) -> sqlite3.Connection:
    require(not path.is_symlink(), "SYMLINK_STORE_FORBIDDEN")
    uri = path.resolve().as_uri() + ("?mode=ro" if readonly else "?mode=rw")
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def verify_store(conn: sqlite3.Connection) -> None:
    require(conn.execute("PRAGMA application_id").fetchone()[0] == APP_ID and
            conn.execute("PRAGMA user_version").fetchone()[0] == STORE_VERSION,
            "NOT_IDENT004_REFERENCE_STORE")


def init_store(path: Path, expected: Identity) -> str:
    wanted = (expected.source_id, expected.stream_id, expected.source_timezone)

    def verify_existing() -> str:
        with closing(connect(path, readonly=True)) as conn:
            verify_store(conn)
            current = conn.execute("SELECT source_id,stream_id,source_timezone FROM source_identity WHERE singleton=1").fetchone()
            require(current == wanted, "STORE_ALREADY_BOUND")
        return "STORE_READY"

    # Never initialize at the final path. An interrupted candidate is not a store.
    if path.exists() or path.is_symlink():
        return verify_existing()
    fd, name = tempfile.mkstemp(prefix=".ident004-init-", suffix=".sqlite3", dir=path.parent)
    os.close(fd)
    candidate = Path(name)
    try:
        with closing(connect(candidate)) as conn:
            require(conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete", "INIT_JOURNAL_MODE")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(f"PRAGMA application_id={APP_ID}")
                conn.execute(f"PRAGMA user_version={STORE_VERSION}")
                for ddl in DDL:
                    conn.execute(ddl)
                conn.execute("INSERT INTO source_identity VALUES (1,?,?,?)", wanted)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            verify_store(conn)
        with candidate.open("r+b") as handle:
            os.fsync(handle.fileno())
        # Atomic no-clobber publication on the same filesystem. Never replace an
        # existing store, even if another initializer won or it is unrelated.
        try:
            os.link(candidate, path)
        except FileExistsError:
            return verify_existing()
        if os.name == "posix":
            dirfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        return verify_existing()
    finally:
        candidate.unlink(missing_ok=True)


def ingest(path: Path, raw: bytes) -> str:
    with closing(connect(path)) as conn:
        verify_store(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            identity = conn.execute("SELECT source_id,stream_id,source_timezone FROM source_identity WHERE singleton=1").fetchone()
            require(identity is not None, "UNINITIALIZED_STORE")
            p = validate(raw, Identity(*identity))
            seq = p["sequence"]
            digest = sha256(raw).hexdigest()
            old = conn.execute("SELECT batch_id,payload_sha256,payload FROM batches WHERE sequence=?", (seq,)).fetchone()
            if old is not None:
                require(old == (p["batch_id"], digest, raw), "SEQUENCE_CONTENT_CONFLICT")
                conn.commit()
                return "ALREADY_ACCEPTED"
            require(conn.execute("SELECT 1 FROM batches WHERE batch_id=?", (p["batch_id"],)).fetchone() is None,
                    "BATCH_ID_CONFLICT")
            newer = conn.execute("SELECT 1 FROM daily WHERE date>=? AND date<? AND sequence>? LIMIT 1",
                                 (p["date_from"], p["date_to_exclusive"], seq)).fetchone()
            require(newer is None, "STALE_OVERLAPPING_WINDOW")
            schema = (p["schema"]["payment_precision"], p["schema"]["payment_scale"])
            pinned = conn.execute("SELECT payment_precision,payment_scale FROM batches ORDER BY sequence LIMIT 1").fetchone()
            require(pinned is None or pinned == schema, "SOURCE_SCHEMA_CHANGED")
            conn.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?,?)", (seq, p["batch_id"], digest, raw,
                         p["date_from"], p["date_to_exclusive"], p["observed_at"], datetime.now(timezone.utc).isoformat(), *schema))
            for row in p["rows"]:
                conn.execute("""INSERT INTO daily VALUES (?,?,?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET
                    sequence=excluded.sequence,records_count=excluded.records_count,paying_patients=excluded.paying_patients,
                    positive_amount=excluded.positive_amount,negative_amount=excluded.negative_amount,net_amount=excluded.net_amount""",
                    (row["date"],seq,row["records_count"],row["paying_patients"],row["positive_amount"],row["negative_amount"],row["net_amount"]))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return "ACCEPTED"


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    init = subs.add_parser("init")
    init.add_argument("db", type=Path)
    init.add_argument("--source-id", required=True)
    init.add_argument("--stream-id", required=True)
    init.add_argument("--source-timezone", required=True)
    accept = subs.add_parser("ingest")
    accept.add_argument("db", type=Path)
    accept.add_argument("payload", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "init":
            result = init_store(args.db, Identity(args.source_id, args.stream_id, args.source_timezone))
        else:
            with args.payload.open("rb") as f:
                raw = f.read(MAX_BYTES+1)
            result = ingest(args.db, raw)
        print(result)
        return 0
    except Rejected as exc:
        print(f"STOP={exc}")
    except (OSError, sqlite3.Error, OverflowError, ValueError) as exc:
        print(f"STOP={type(exc).__name__}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
