#!/usr/bin/env python3
"""AZ-BAZE PR256 controlled installer. Default --preflight does not modify Production.

Deploy only six source-authenticated runtime files and create exactly two empty
incoming-document tables plus two indexes. Never edits existing medical
supplies items, movement, finance, history, users, or document records.
"""
import argparse
import ast
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = "pat16071975-ship-it/architecture-health-site"
BASE_SHA = "ffd069d35c9a79f40e3035d1746e2dbd0c9be508"
AUDITED_HEAD = "35f08a9d034945fab613e35fd65149e96b011211"
MERGED_SHA = "9bf6a042f771af063ac280ac95186d2ccd784fe4"
ROOT = Path("/opt/az-baze-auth")
DB = Path("/var/lib/az-baze/auth.db")
BACKUPS = Path("/var/lib/az-baze/backups")
VENV = ROOT / "venv/bin/python"
SERVICE = "az-baze-auth"
LOCAL = "http://127.0.0.1:8001"
FILES = {
    "medical_supplies.py": ("0c8866461ab5a75561154fd977665673822b783f", "226437cd0ccce5ceef6fe84f3e9f43b7e355aed0"),
    "medical_supplies_documents.py": (null, "f9bcf89dd2f3cf54df9d8392d5ce269318923713"),
    "templates/medical_supplies.html": ("fe43bd742106e8af608a1965ffe1ceeeab80413e", "0314a1a4608db48ba96574fb9bbcf8551b3af8be"),
    "templates/medical_supplies_daily.html": ("e1a22d6b9941b3cf24bbffda0808728a7a3328c1", "5eccbce7093256b95412e9744cdc8902bc2434f6"),
    "templates/medical_supplies_stock.html": ("a122a2c8eca0a2855caebca84e12380c045749d4", "97e66d537759e3731ef9056e9ce7934fd162bbd0"),
    "templates/medical_supplies_documents.html": (null, "4333551d4939ed6632651dd014836c9ca4a57402"),
}
NEW_TABLES = frozenset(("med_supply_documents", "med_supply_document_events"))
NEW_INDEXES = frozenset(("ix_med_supply_documents_status",
                         "ix_med_supply_document_events_document"))
NEW_OBJECTS = NEW_TABLES | NEW_INDEXES
ANCHORS = frozenset(("users", "permissions", "audit_log", "med_supply_items",
                     "med_supply_stock_imports", "med_supply_days"))
ROUTES = (
    "/accounting/medical-supplies/",
    "/accounting/medical-supplies/incoming/",
    "/accounting/medical-supplies/incoming/1/download",
    "/accounting/medical-supplies/incoming/1/view",
)


def fail(message):
    raise RuntimeError(message)


def git_blob(data):
    return hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()


def run(*args, check=True):
    result = subprocess.run(
        args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    if check and result.returncode != 0:
        fail("COMMAND_FAILED: " + args[0] + ": "
             + (result.stderr or result.stdout)[-600:])
    return result


def active():
    return run("systemctl", "is-active", "--quiet", SERVICE, check=False).returncode == 0


def http_status(path):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, url):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(LOCAL + path, timeout=4) as response:
            return response.status, response.read(120)
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def health():
    if not active():
        fail("SERVICE_NOT_ACTIVE")
    status, payload = http_status("/health")
    if (status, payload.strip()) != (200, b"ok"):
        fail("SERVICE_HEALTH_BAD: " + str(status))
    print("SERVICE_HEALTH=PASS", flush=True)


def smoke_routes():
    for route in ROUTES:
        status, _payload = http_status(route)
        if status not in (302, 401, 403):
            fail(f"ROUTE_UNPROTECTED_OR_MISSING: {route} status={status}")
    print("MEDICAL_INCOMING_ROUTES=PASS; ANONYMOUS_PROTECTED", flush=True)


def check_blob(path, expected, label):
    if path.is_symlink() or not path.is_file():
        fail(f"{label}_NOT_REGULAR_FILE: {path}")
    actual = git_blob(path.read_bytes())
    if actual != expected:
        fail(f"{label}_BLOB_MISMATCH: {path}; actual={actual}; expected={expected}")


def check_live_files():
    if not ROOT.is_dir() or not (ROOT / "templates").is_dir():
        fail("AZ_BAZE_ROOT_MISSING")
    for name, (old, _new) in FILES.items():
        file = ROOT / name
        if old is None:
            if file.is_symlink() or file.exists():
                fail(f"NEW_RUNTIME_FILE_ALREADY_EXISTS: {name}")
        else:
            check_blob(file, old, "LIVE_BASE")
    print(f"LIVE_BASE=PASS; FILES=6; SOURCE={BASE_SHA}", flush=True)


def ro_conn(path):
    if path.is_symlink() or not path.is_file():
        fail("DB_MISSING_OR_SYMLINK: " + str(path))
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                           timeout=30)
    conn.execute("PRAGMA query_only=ON")
    return conn


def schema_map(conn):
    return {row[1]: (row[0], row[2], row[3])
            for row in conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%'"
            )}


def db_health(conn):
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        fail("DB_INTEGRITY_CHECK_FAILED")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        fail("DB_FOREIGN_KEY_CHECK_FAILED")


def check_db(before_release=True, path=None):
    conn = ro_conn(path or DB)
    try:
        db_health(conn)
        objects = schema_map(conn)
        missing = ANCHORS - set(objects)
        if missing:
            fail("NOT_AZ_BAZE_DB: missing=" + ",".join(sorted(missing)))
        if before_release and set(objects) & NEW_OBJECTS:
            fail("INCOMING_DOCUMENT_SCHEMA_ALREADY_PRESENT_OR_PARTIAL")
        if not before_release and not NEW_OBJECTS <= set(objects):
            fail("INCOMING_DOCUMENT_SCHEMA_INCOMPLETE")
        print(f"DATABASE=READ_ONLY_PASS; OBJECTS={len(objects)}", flush=True)
        return objects
    finally:
        conn.close()


def fetch_exact_blob(expected):
    """Fetch immutable Git blob, tolerating GitHub's wrapped base64 response."""
    url = f"https://api.github.com/repos/{REPO}/git/blobs/{expected}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "AZ-BAZE-PR256-Immutable-Release",
        "Accept": "application/vnd.github+json",
    })
    last_error = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=40) as response:
                if response.status != 200:
                    fail("GIT_BLOB_HTTP_BAD")
                obj = json.loads(response.read(2 * 1024 * 1024 + 1))
            if obj.get("encoding") != "base64" or not isinstance(obj.get("content"), str):
                fail("GIT_BLOB_ENCODING_BAD")
            encoded = "".join(obj["content"].split())
            try:
                data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                fail("GIT_BLOB_BASE64_BAD: " + type(exc).__name__)
            if len(data) > 512 * 1024 or git_blob(data) != expected:
                fail("GIT_BLOB_HASH_BAD: " + expected)
            return data
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
    fail("GIT_BLOB_UNAVAILABLE: " + type(last_error).__name__)


def prepare(stage):
    for name, (_old, new) in FILES.items():
        raw = fetch_exact_blob(new)
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        if name.endswith(".py"):
            compile(raw.decode("utf-8"), str(target), "exec")
    for name in FILES:
        if name.endswith(".html"):
            result = run(str(VENV), "-c",
                         "import pathlib,jinja2,sys;"
                         "jinja2.Environment().parse("
                         "pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))",
                         str(stage / name))
            if result.returncode:
                fail("INVALID_TEMPLATE: " + name)
    schema_statements(stage)
    print(f"IMMUTABLE_TARGET=PASS; FILES=6; SOURCE={MERGED_SHA}", flush=True)


def schema_statements(stage):
    script = stage / "medical_supplies_documents.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    declarations = [
        n for n in tree.body if isinstance(n, ast.Assign)
        and any(isinstance(x, ast.Name) and x.id == "SCHEMA" for x in n.targets)
    ]
    if len(declarations) != 1:
        fail("SCHEMA_NOT_UNIQUE")
    value = ast.literal_eval(declarations[0].value)
    statements = [x.strip() for x in value.split(";") if x.strip()]
    if len(statements) != 4:
        fail("SCHEMA_STATEMENT_COUNT_UNEXPECTED")
    tables, indexes = set(), set()
    for statement in statements:
        mt = re.match(r"^CREATE TABLE IF NOT EXISTS (med_supply_[a-z_]+)\s*\(",
                      statement)
        mi = re.match(r"^CREATE INDEX IF NOT EXISTS (ix_med_supply_[a-z_]+)\s+ON\s+",
                      statement)
        if mt:
            tables.add(mt.group(1))
        elif mi:
            indexes.add(mi.group(1))
        else:
            fail("SCHEMA_STATEMENT_NOT_ALLOWLISTED")
        if len(re.findall(r"\bCREATE\b", statement, flags=re.IGNORECASE)) != 1:
            fail("SCHEMA_NESTED_CREATE")
        if re.search(r"\b(DROP|ALTER|INSERT|UPDATE|DELETE|REPLACE|ATTACH|DETACH)\b",
                     statement, re.IGNORECASE):
            fail("SCHEMA_MUTATING_SQL_FORBIDDEN")
    if tables != NEW_TABLES or indexes != NEW_INDEXES:
        fail("SCHEMA_DELTA_UNEXPECTED")
    scratch = sqlite3.connect(":memory:")
    try:
        scratch.execute("PRAGMA foreign_keys=ON")
        scratch.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
        for statement in statements:
            scratch.execute(statement)
        db_health(scratch)
        actual = set(schema_map(scratch))
        if actual != NEW_OBJECTS | {"users"}:
            fail("SCHEMA_IN_MEMORY_MISMATCH")
    finally:
        scratch.close()
    print("ADDITIVE_SCHEMA=PASS; TABLES=2; INDEXES=2", flush=True)
    return statements


def backup_sqlite(path):
    source = ro_conn(DB)
    target = sqlite3.connect(path)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    connection = ro_conn(path)
    try:
        db_health(connection)
    finally:
        connection.close()
    result = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"DATABASE_BACKUP=PASS; SHA256={result}", flush=True)
    return result


def backup_files(backup):
    for name, (old, _new) in FILES.items():
        if old is None:
            continue
        destination = backup / "files" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
        check_blob(destination, old, "BACKUP")
    print("RUNTIME_BACKUP=PASS; FILES=4", flush=True)


def atomic_file_replace(source, destination, template=False):
    if destination.is_symlink():
        fail("RUNTIME_SYMLINK_FORBIDDEN")
    exists = destination.exists()
    if exists and not destination.is_file():
        fail("RUNTIME_TARGET_NOT_FILE")
    if not exists and destination.exists():
        fail("RUNTIME_TARGET_RACE")
    exemplar = ROOT / ("templates/medical_supplies.html" if template
                        else "medical_supplies.py")
    if exemplar.is_symlink() or not exemplar.is_file():
        fail("RUNTIME_EXEMPLAR_MISSING")
    metadata = (destination if exists else exemplar).stat()
    fd, temporary = tempfile.mkstemp(prefix=".pr256-", dir=destination.parent)
    path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, metadata.st_mode & 0o777)
        os.chown(path, metadata.st_uid, metadata.st_gid)
        os.replace(path, destination)
    finally:
        if path.exists():
            path.unlink()


def install(stage):
    for name, (old, target) in FILES.items():
        destination = ROOT / name
        if old is not None:
            check_blob(destination, old, "INSTALL_BASE")
        elif destination.exists() or destination.is_symlink():
            fail("INSTALL_NEW_FILE_ALREADY_EXISTS")
        atomic_file_replace((stage / name).read_bytes(), destination,
                            template=name.startswith("templates/"))
        check_blob(destination, target, "INSTALLED")
    print("RUNTIME_INSTALL=PASS; FILES=6", flush=True)


def migrate(statements, baseline):
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("BEGIN IMMEDIATE")
        original = schema_map(conn)
        if original != baseline:
            fail("DB_SCHEMA_CHANGED_BEFORE_MIGRATION")
        if set(original) & NEW_OBJECTS:
            fail("DB_SCHEMA_MIGRATION_ALREADY_PRESENT")
        tables = [name for name, (typ, _tbl, _sql) in original.items()
                  if typ == "table"]
        before_counts = {
            name: conn.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
            for name in tables
        }
        for statement in statements:
            conn.execute(statement)
        after = schema_map(conn)
        if any(after.get(k) != definition for k, definition in baseline.items()):
            fail("DB_EXISTING_SCHEMA_CHANGED")
        if set(after) - set(baseline) != NEW_OBJECTS:
            fail("DB_SCHEMA_UNEXPECTED_OBJECT_DELTA")
        for name in tables:
            count = conn.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
            if count != before_counts[name]:
                fail("DB_OLD_TABLE_COUNT_CHANGED: " + name)
        for name in NEW_TABLES:
            if conn.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0] != 0:
                fail("NEW_TABLE_NOT_EMPTY: " + name)
        db_health(conn)
        conn.commit()
        print(f"DATABASE_MIGRATION=PASS; ADD_ONLY=2_TABLES+2_INDEXES; EXISTING_ROWS=UNCHANGED",
              flush=True)
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def restore(backup):
    for name, (old, _target) in FILES.items():
        destination = ROOT / name
        if old is None:
            if destination.exists():
                if destination.is_symlink() or not destination.is_file():
                    fail("ROLLBACK_NEW_FILE_NOT_REGULAR")
                destination.unlink()
        else:
            previous = backup / "files" / name
            check_blob(previous, old, "ROLLBACK_BACKUP")
            atomic_file_replace(previous.read_bytes(), destination,
                                template=name.startswith("templates/"))
            check_blob(destination, old, "ROLLBACK_RESTORED")
    print("RUNTIME_ROLLBACK=PASS; DB_SCHEMA_NOT_ROLLED_BACK_AUTOMATICALLY",
          flush=True)


def restart_and_smoke(rolled_back=False):
    run("systemctl", "start", SERVICE)
    for _ in range(25):
        try:
            if active():
                status, payload = http_status("/health")
                if (status, payload.strip()) == (200, b"ok"):
                    break
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    else:
        fail("RESTART_HEALTH_FAILED")
    if rolled_back:
        print("PREVIOUS_SERVICE_RESTORED=PASS", flush=True)
    else:
        smoke_routes()
        print("SERVICE_SMOKE=PASS", flush=True)


def apply(stage):
    backup = BACKUPS / ("pr256-incoming-" +
                         datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    if backup.exists():
        fail("BACKUP_PATH_ALREADY_EXISTS")
    backup.mkdir(parents=True, mode=0o700)
    service_stopped = False
    have_files = False
    touched = False
    migration_committed = False
    try:
        run("systemctl", "stop", SERVICE)
        service_stopped = True
        if active():
            fail("SERVICE_NOT_STOPPED")
        check_live_files()
        baseline = check_db()
        backup_files(backup)
        have_files = True
        db_digest = backup_sqlite(backup / "auth-pre-pr256.sqlite")
        manifest = {
            "release": "PR256-incoming-documents",
            "base": BASE_SHA, "audited_head": AUDITED_HEAD,
            "merged_sha": MERGED_SHA, "files": FILES,
            "backup_sha256": db_digest, "db_delta": sorted(NEW_OBJECTS),
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "auto_db_restore": False,
        }
        (backup / "release-manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        os.chmod(backup / "release-manifest.json", 0o600)
        touched = True
        install(stage)
        migrate(schema_statements(stage), baseline)
        migration_committed = True
        restart_and_smoke()
        for name, (_old, target) in FILES.items():
            check_blob(ROOT / name, target, "POST_RELEASE")
        check_db(before_release=False)
        print(f"PR256_RELEASE=PASS; MERGED_SHA={MERGED_SHA}; BACKUP={backup}",
              flush=True)
        print("DOCUMENTS_INSERTED=0; ORIGINALS_UPLOAD_PENDING", flush=True)
        print("BROWSER_ACCEPTANCE=PENDING", flush=True)
    except BaseException:
        print("PR256_RELEASE=FAIL; BEGIN_CODE_ROLLBACK", file=sys.stderr,
              flush=True)
        try:
            if service_stopped:
                run("systemctl", "stop", SERVICE, check=False)
                if touched and have_files:
                    restore(backup)
                restart_and_smoke(rolled_back=True)
        except BaseException as rollback_error:
            print("CODE_ROLLBACK_FAILED=" + str(rollback_error),
                  file=sys.stderr, flush=True)
        print("BACKUP_PATH=" + str(backup) + "; DB_RESTORE_NOT_AUTOMATIC",
              file=sys.stderr, flush=True)
        if migration_committed:
            print("SCHEMA_ADDITIVE_OBJECTS_MAY_REMAIN=YES",
                  file=sys.stderr, flush=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--preflight", action="store_true")
    choice.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if os.geteuid() != 0:
        fail("ROOT_REQUIRED")
    if not VENV.is_file() or not Path("/etc/az-baze/auth.env").is_file():
        fail("AZ_BAZE_RUNTIME_OR_SERVICE_ENV_MISSING")
    with tempfile.TemporaryDirectory(prefix="pr256-stage-") as temporary:
        stage = Path(temporary)
        print(f"RELEASE_SOURCE={MERGED_SHA}; EXPECTED_BASE={BASE_SHA}",
              flush=True)
        health()
        smoke_routes_preflight()
        check_live_files()
        check_db()
        prepare(stage)
        print("PR256_PREFLIGHT=PASS; PRODUCTION_NOT_MODIFIED", flush=True)
        if args.apply:
            apply(stage)


def smoke_routes_preflight():
    # New inbox routes MUST NOT yet be available in old runtime.
    old_route = "/accounting/medical-supplies/"
    status, _ = http_status(old_route)
    if status not in (302, 401, 403):
        fail("EXISTING_MEDICAL_SUPPLIES_ROUTE_NOT_PROTECTED")
    print("MEDICAL_SUPPLIES_BASE_ROUTE=PASS; ANONYMOUS_PROTECTED", flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print("PR256_CONTROLLED_STOP=" + type(exc).__name__ + ": " + str(exc),
              file=sys.stderr, flush=True)
        sys.exit(1)
