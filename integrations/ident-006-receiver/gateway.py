"""IDENT-006A: isolated authenticated shadow receiver, Python 3.10+ / POSIX.

Importing this module changes no files. Explicit init precedes HTTP startup.
The unchanged IDENT-004 validator is the sole financial payload contract.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
from typing import Callable

from core import receiver as contract

APP_ID = 0x49423641  # IB6A, deliberately different from IDENT-004 offline store
VERSION = 1
BATCH_ROUTE = "/api/ident-bridge/v1/batches"
STATUS_ROUTE = "/api/ident-bridge/v1/status"
HEX = re.compile(r"[0-9a-f]{64}\Z")
CONFIG_KEYS = set("organization_id source_id stream_id source_timezone token_sha256 max_store_bytes min_free_bytes".split())
CONFLICTS = {"SEQUENCE_CONTENT_CONFLICT", "BATCH_ID_CONFLICT", "STALE_OVERLAPPING_WINDOW", "SOURCE_SCHEMA_CHANGED"}
MAX_CONFIG = 4096
MIB = 1024 * 1024
STORE_NAME = "ident-shadow.sqlite3"


class Stop(RuntimeError):
    pass


class Unavailable(Stop):
    pass


def check(ok, code):
    if not ok:
        raise Stop(code)


def utc_now():
    return datetime.now(timezone.utc)


def utc_text(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def assert_absolute_trusted(path: Path):
    check(path.is_absolute() and path.resolve(strict=True) == path, "UNSAFE_PATH")
    uid = os.geteuid()
    # Root-owned sticky temporary directories are safe ancestors of our owned
    # private directory; all other ancestors must not be writable by outsiders.
    for parent in path.parents:
        info = parent.lstat()
        check(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, uid), "UNSAFE_ANCESTOR")
        writable = bool(info.st_mode & 0o022)
        sticky = bool(info.st_mode & stat.S_ISVTX) and info.st_uid == 0
        check(not writable or sticky, "UNSAFE_ANCESTOR")


def assert_private_dir(path: Path):
    assert_absolute_trusted(path)
    info = path.lstat()
    check(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
          and stat.S_IMODE(info.st_mode) == 0o700, "PRIVATE_STATE_REQUIRED")


def assert_private_file(path: Path):
    info = path.lstat()
    check(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
          and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
          "PRIVATE_STORE_REQUIRED")
    return info


def read_config(path: Path) -> dict:
    assert_absolute_trusted(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        # A root-provisioned 0640 file can be read via the dedicated service
        # group. No write access for groups or other users.
        check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
              and info.st_uid in (0, os.geteuid())
              and stat.S_IMODE(info.st_mode) in (0o600, 0o640), "PRIVATE_CONFIG_REQUIRED")
        if info.st_uid != 0:
            check(stat.S_IMODE(info.st_mode) == 0o600, "PRIVATE_CONFIG_REQUIRED")
        raw = f.read(MAX_CONFIG + 1)
    check(0 < len(raw) <= MAX_CONFIG, "CONFIG_SIZE")
    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=contract.unique_pairs,
                         parse_constant=contract.reject_constant)
    except (UnicodeError, ValueError, RecursionError) as e:
        raise Stop("INVALID_CONFIG") from e
    check(type(obj) is dict and set(obj) == CONFIG_KEYS, "INVALID_CONFIG_FIELDS")
    return obj


@dataclass(frozen=True)
class Settings:
    organization_id: str
    identity: contract.Identity
    token_sha256: str
    state: Path
    max_store_bytes: int
    min_free_bytes: int

    @property
    def db(self):
        return self.state / STORE_NAME

    @property
    def binding(self):
        return (self.organization_id, self.identity.source_id, self.identity.stream_id,
                self.identity.source_timezone)

    @classmethod
    def load(cls, config: Path, state: Path):
        check(os.name == "posix" and os.geteuid() != 0, "NONROOT_POSIX_REQUIRED")
        assert_private_dir(state)
        check(config != state and state not in config.parents, "SEPARATE_CONFIG_REQUIRED")
        obj = read_config(config)
        org = contract.uuid_value(obj["organization_id"])
        identity = contract.Identity(obj["source_id"], obj["stream_id"], obj["source_timezone"])
        digest = obj["token_sha256"]
        check(isinstance(digest, str) and bool(HEX.fullmatch(digest)), "INVALID_TOKEN_VERIFIER")
        maximum = contract.integer(obj["max_store_bytes"], 16*MIB, 8*1024*MIB)
        reserve = contract.integer(obj["min_free_bytes"], 16*MIB, 8*1024*MIB)
        return cls(org, identity, digest, state, maximum, reserve)

    @classmethod
    def from_env(cls):
        config, state = os.environ.get("IDENT_RECEIVER_CONFIG"), os.environ.get("IDENT_RECEIVER_STATE_DIR")
        check(bool(config and state), "EXPLICIT_CONFIG_AND_STATE_REQUIRED")
        return cls.load(Path(config), Path(state))


DDL = (
    """CREATE TABLE source_identity (
       singleton INTEGER PRIMARY KEY CHECK(singleton=1),
       organization_id TEXT NOT NULL, source_id TEXT NOT NULL,
       stream_id TEXT NOT NULL, source_timezone TEXT NOT NULL)""",
    *contract.DDL[1:],
)


def connect(path: Path, readonly=False):
    assert_private_file(path)
    conn = sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
                           uri=True, isolation_level=None, timeout=3)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        check(conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete", "UNEXPECTED_JOURNAL_MODE")
        conn.execute("PRAGMA trusted_schema=OFF")
        if readonly:
            conn.execute("PRAGMA query_only=ON")
        else:
            conn.execute("PRAGMA synchronous=FULL")
            check(conn.execute("PRAGMA synchronous").fetchone()[0] >= 2, "DURABILITY_REQUIRED")
        return conn
    except BaseException:
        conn.close()
        raise


def verify_binding(conn, settings):
    check(conn.execute("PRAGMA application_id").fetchone()[0] == APP_ID
          and conn.execute("PRAGMA user_version").fetchone()[0] == VERSION,
          "NOT_IDENT006_SHADOW_STORE")
    row = conn.execute("""SELECT organization_id,source_id,stream_id,source_timezone
                          FROM source_identity WHERE singleton=1""").fetchone()
    check(row == settings.binding, "STORE_BINDING_MISMATCH")


def assert_known_store_file(path):
    assert_private_file(path)
    # Header checks precede any write-capable opening of an existing store.
    # An unrelated SQLite or an arbitrary file must not be modified/recovered.
    with path.open("rb") as f:
        header = f.read(100)
    check(len(header) == 100 and header[:16] == b"SQLite format 3\x00"
          and int.from_bytes(header[68:72],"big") == APP_ID
          and int.from_bytes(header[60:64],"big") == VERSION,
          "NOT_IDENT006_SHADOW_STORE")
    for suffix in ("-wal","-shm"):
        side = Path(str(path)+suffix)
        check(not side.exists() and not side.is_symlink(),"UNEXPECTED_SQLITE_SIDECAR")
    journal = Path(str(path)+"-journal")
    try:
        assert_private_file(journal)
    except FileNotFoundError:
        # Another legitimate writer can finish/delete the journal between
        # requests. SQLite itself serializes writers and journal recovery.
        pass


def verify_store(settings):
    assert_private_dir(settings.state)
    assert_known_store_file(settings.db)
    # Opening this recognized, dedicated store RW permits SQLite to recover a
    # hot rollback journal after abrupt power/process loss. No new schema or
    # application data is written here.
    with closing(connect(settings.db)) as conn:
        verify_binding(conn, settings)


@contextmanager
def initialization_lock(state: Path):
    path = state / ".initialize.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        check(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
              and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
              "PRIVATE_INIT_LOCK_REQUIRED")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def initialize(settings: Settings):
    """Only the explicit operator CLI calls this. Never replace an existing file."""
    assert_private_dir(settings.state)
    with initialization_lock(settings.state):
        if settings.db.exists() or settings.db.is_symlink():
            verify_store(settings)
            return "STORE_READY"
        fd, name = tempfile.mkstemp(prefix=".ident006-init-", dir=settings.state)
        os.close(fd)
        candidate = Path(name)
        try:
            with closing(connect(candidate)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute(f"PRAGMA application_id={APP_ID}")
                    conn.execute(f"PRAGMA user_version={VERSION}")
                    for ddl in DDL:
                        conn.execute(ddl)
                    conn.execute("INSERT INTO source_identity VALUES (1,?,?,?,?)", settings.binding)
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                verify_binding(conn, settings)
            with candidate.open("r+b") as f:
                os.fsync(f.fileno())
            # Publish the whole schema at once without clobbering any file.
            os.link(candidate, settings.db)
            candidate.unlink()
            fsync_dir(settings.state)
            verify_store(settings)
            return "STORE_CREATED"
        finally:
            candidate.unlink(missing_ok=True)


def receipt(p, digest, accepted_at, status):
    return {"contract_version": 1, "dataset": contract.DATASET,
            "source_id": p["source_id"], "stream_id": p["stream_id"],
            "batch_id": p["batch_id"], "sequence": p["sequence"],
            "payload_sha256": digest, "accepted_at": accepted_at, "status": status}


def ingest(settings: Settings, raw: bytes, now: datetime):
    """Validate before mutation, then serialize the durable ACK with the write.

    The original reference contract is unchanged. This is a distinct storage
    implementation with explicit FULL sync, organization binding and disk cap.
    """
    p = contract.validate(raw, settings.identity)
    contract.require(contract.utc_value(p["observed_at"]) <= now + timedelta(minutes=5),
                     "OBSERVATION_IN_FUTURE")
    digest = sha256(raw).hexdigest()
    seq = p["sequence"]
    assert_private_dir(settings.state)
    assert_known_store_file(settings.db)
    with closing(connect(settings.db)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            verify_binding(conn, settings)
            old = conn.execute("""SELECT batch_id,payload_sha256,payload,accepted_at
                                  FROM batches WHERE sequence=?""", (seq,)).fetchone()
            if old is not None:
                contract.require(old[:3] == (p["batch_id"], digest, raw), "SEQUENCE_CONTENT_CONFLICT")
                conn.commit()
                return receipt(p, digest, old[3], "already_accepted")
            contract.require(conn.execute("SELECT 1 FROM batches WHERE batch_id=?",
                                          (p["batch_id"],)).fetchone() is None, "BATCH_ID_CONFLICT")
            newer = conn.execute("""SELECT 1 FROM daily WHERE date>=? AND date<?
                                    AND sequence>? LIMIT 1""",
                                 (p["date_from"], p["date_to_exclusive"], seq)).fetchone()
            contract.require(newer is None, "STALE_OVERLAPPING_WINDOW")
            schema = (p["schema"]["payment_precision"], p["schema"]["payment_scale"])
            pinned = conn.execute("SELECT payment_precision,payment_scale FROM batches ORDER BY sequence LIMIT 1").fetchone()
            contract.require(pinned is None or pinned == schema, "SOURCE_SCHEMA_CHANGED")
            # Capacity is checked only for new writes; an already committed
            # exact retry must still get its ACK when the store is full.
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            pages = conn.execute("PRAGMA page_count").fetchone()[0]
            maximum = settings.max_store_bytes // page_size
            if pages >= maximum or shutil.disk_usage(settings.state).free < settings.min_free_bytes + 4*contract.MAX_BYTES:
                raise Unavailable("STORE_CAPACITY")
            actual = conn.execute(f"PRAGMA max_page_count={maximum}").fetchone()[0]
            if actual != maximum:
                raise Unavailable("STORE_CAPACITY")
            accepted_at = utc_text(now)
            conn.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (seq,p["batch_id"],digest,raw,p["date_from"],p["date_to_exclusive"],
                          p["observed_at"],accepted_at,*schema))
            for row in p["rows"]:
                conn.execute("""INSERT INTO daily VALUES (?,?,?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET
                    sequence=excluded.sequence, records_count=excluded.records_count,
                    paying_patients=excluded.paying_patients, positive_amount=excluded.positive_amount,
                    negative_amount=excluded.negative_amount, net_amount=excluded.net_amount""",
                    (row["date"],seq,row["records_count"],row["paying_patients"],row["positive_amount"],
                     row["negative_amount"],row["net_amount"]))
            conn.commit()  # FULL synchronous; no success response before this returns
            return receipt(p, digest, accepted_at, "accepted")
        except BaseException:
            conn.rollback()
            raise


def read_status(settings):
    assert_private_dir(settings.state)
    assert_known_store_file(settings.db)
    with closing(connect(settings.db, readonly=True)) as conn:
        conn.execute("BEGIN")
        verify_binding(conn, settings)
        count, max_seq, last = conn.execute("SELECT count(*),max(sequence),max(accepted_at) FROM batches").fetchone()
        first, last_day = conn.execute("SELECT min(date),max(date) FROM daily").fetchone()
        conn.commit()
    return {"status":"ready", "dataset":contract.DATASET, "source_id":settings.identity.source_id,
            "stream_id":settings.identity.stream_id, "accepted_batches":count, "max_sequence":max_seq,
            "last_accepted_at":last, "first_observed_day":first, "last_observed_day":last_day,
            "mode":"shadow_only"}


def authorized(environ, settings):
    header = environ.get("HTTP_AUTHORIZATION", "")
    if not isinstance(header, str) or not header.startswith("Bearer "):
        return False
    token = header[7:]
    return bool(HEX.fullmatch(token)) and hmac.compare_digest(
        sha256(token.encode("ascii")).hexdigest(), settings.token_sha256)


def response(start_response, status, obj, extra=()):
    body = json.dumps(obj, ensure_ascii=True, separators=(",",":")).encode("utf-8")
    start_response(status, [("Content-Type","application/json; charset=utf-8"),
                            ("Content-Length",str(len(body))), ("Cache-Control","no-store"),
                            ("X-Content-Type-Options","nosniff"), *extra])
    return [body]


def make_app(settings: Settings, clock: Callable = utc_now):
    verify_store(settings)

    def app(environ, start_response):
        def error(status, code, extra=()):
            return response(start_response,status,{"error":code},extra)

        path = environ.get("PATH_INFO", "")
        if path not in (BATCH_ROUTE, STATUS_ROUTE) or environ.get("QUERY_STRING", ""):
            return error("404 Not Found","NOT_FOUND")
        # A private Unix socket and Nginx replacing this header are required.
        # This header is NOT authentication on an exposed HTTP listener.
        if environ.get("wsgi.url_scheme") != "https" or environ.get("HTTP_X_FORWARDED_PROTO") != "https":
            return error("400 Bad Request","HTTPS_REQUIRED")
        if not authorized(environ, settings):
            return error("401 Unauthorized","UNAUTHORIZED",(("WWW-Authenticate",'Bearer realm="ident-bridge"'),))
        method = environ.get("REQUEST_METHOD")
        wanted = "POST" if path == BATCH_ROUTE else "GET"
        if method != wanted:
            return error("405 Method Not Allowed","METHOD_NOT_ALLOWED",(("Allow",wanted),))
        try:
            if path == STATUS_ROUTE:
                if environ.get("CONTENT_LENGTH") not in (None,"","0") or environ.get("HTTP_TRANSFER_ENCODING"):
                    return error("400 Bad Request","UNEXPECTED_BODY")
                return response(start_response,"200 OK",read_status(settings))
            if environ.get("HTTP_TRANSFER_ENCODING") or environ.get("HTTP_CONTENT_ENCODING"):
                return error("400 Bad Request","UNSUPPORTED_BODY_ENCODING")
            ctype = environ.get("CONTENT_TYPE","").lower()
            if not re.fullmatch(r'application/json(?:\s*;\s*charset\s*=\s*"?utf-8"?)?',ctype):
                return error("415 Unsupported Media Type","JSON_REQUIRED")
            length = environ.get("CONTENT_LENGTH","")
            if not isinstance(length,str) or not re.fullmatch(r"[0-9]{1,10}",length):
                return error("411 Length Required","CONTENT_LENGTH_REQUIRED")
            size = int(length)
            if size > contract.MAX_BYTES:
                return error("413 Payload Too Large","PAYLOAD_SIZE")
            if size <= 0:
                return error("400 Bad Request","EMPTY_BODY")
            raw = environ["wsgi.input"].read(size)
            if not isinstance(raw,bytes) or len(raw) != size:
                return error("400 Bad Request","INCOMPLETE_BODY")
            ack = ingest(settings,raw,clock())
            return response(start_response,"200 OK",ack)
        except contract.Rejected as e:
            code = str(e)
            if not re.fullmatch(r"[A-Z_]+",code):
                code = "INVALID_PAYLOAD"
            return error("409 Conflict" if code in CONFLICTS else "422 Unprocessable Entity",code)
        except Unavailable as e:
            return error("503 Service Unavailable",str(e),(("Retry-After","60"),))
        except (Stop, OSError, sqlite3.Error, ValueError, OverflowError, KeyError, TypeError):
            # Deliberately no exception text, raw body, credential or financial
            # amounts in responses/logs. A failed request never gets an ACK.
            return error("503 Service Unavailable","STORE_OR_REQUEST_UNAVAILABLE",(("Retry-After","30"),))
    return app


def create_app():
    os.umask(0o077)
    return make_app(Settings.from_env())


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command",choices=("init","status"))
    args = p.parse_args()
    try:
        settings = Settings.from_env()
        if args.command == "init":
            print(initialize(settings))
        else:
            print(json.dumps(read_status(settings),ensure_ascii=True,separators=(",",":")))
        return 0
    except (Stop, contract.Rejected, OSError, sqlite3.Error, ValueError):
        print("STOP=CONFIG_OR_STORE_UNAVAILABLE")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
