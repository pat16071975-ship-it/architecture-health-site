#!/usr/bin/env python3
"""Controlled PR258 release: two existing runtime files; NO DB migration.

Use the established AZ-BAZE immutable Git blob, preflight, backup and
rollback procedure. Only the administrator module and template may change.
"""
import argparse
import ast
import base64
import binascii
import hashlib
import json
import os
import shutil
import socket
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
BASE_SHA = "9bf6a042f771af063ac280ac95186d2ccd784fe4"
AUDITED_HEAD = "40835befe9c9ff5be655adb1ba40eea55ae69941"
MERGED_SHA = "0e8c5015e7442167941e68f410ba3029c1f8969e"
ROOT = Path("/opt/az-baze-auth")
DB = Path("/var/lib/az-baze/auth.db")
BACKUPS = Path("/var/lib/az-baze/backups")
VENV = ROOT / "venv/bin/python"
SERVICE = "az-baze-auth"
LOCAL = "http://127.0.0.1:8001"
FILES = {
    "administrators.py": ("0b4a25b28a20b42965bf7f66e3f042b819b49ccd", "00fcf878c682c431ac64c303232f939b2173e3fb"),
    "templates/administrators.html": ("a7f694052c7940774150638ab24b93ad48a8f877", "8ded2a68012759e999704e46325a2f9aefaeb803"),
}
TABLES = {
    "az_admin_staff": ("id","full_name","created_month","inactive_month","created_at","updated_at"),
    "az_admin_rates": ("staff_id","valid_from","kopecks"),
    "az_admin_days": ("staff_id","work_date","minutes"),
    "az_admin_bonuses": ("staff_id","month","standards","conversion","category","plan","extras"),
    "az_admin_months": ("month","closed","version","snapshot","closed_by","closed_at"),
    "az_admin_events": ("id","actor_id","month","staff_id","action","before_json","after_json","created_at"),
}
INDEXES = {
    "ix_az_admin_days_date": "az_admin_days",
    "ix_az_admin_events_month": "az_admin_events",
}


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

def http_status(path, method="GET"):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, url):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    req = urllib.request.Request(LOCAL + path, method=method,
                                 data=b"" if method == "POST" else None)
    try:
        with opener.open(req, timeout=4) as response:
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

def smoke_routes(new_version=False):
    for route in ("/", "/administrators/", "/administrators/?tab=payroll",
                  "/administrators/?tab=settings"):
        status, _ = http_status(route)
        if status not in (302, 401, 403):
            fail(f"ROUTE_UNPROTECTED_OR_MISSING: {route} status={status}")
    # Empty, anonymous POST; login_required rejects it before any DB operation.
    if new_version:
        status, _ = http_status("/administrators/hours/batch", "POST")
        if status not in (302, 401, 403):
            fail("NEW_BATCH_ROUTE_UNPROTECTED_OR_MISSING: " + str(status))
    print("ADMINISTRATORS_ROUTES=PASS; ANONYMOUS_PROTECTED", flush=True)


def check_blob(path, expected, label):
    if path.is_symlink() or not path.is_file():
        fail(f"{label}_NOT_REGULAR_FILE: {path}")
    actual = git_blob(path.read_bytes())
    if actual != expected:
        fail(f"{label}_BLOB_MISMATCH: {path}; actual={actual}; expected={expected}")

def check_live_files():
    for directory in (ROOT, ROOT / "templates"):
        if directory.is_symlink() or not directory.is_dir():
            fail("AZ_BAZE_ROOT_MISSING_OR_SYMLINK")
    for name, (old, _new) in FILES.items():
        check_blob(ROOT / name, old, "LIVE_BASE")
    print(f"LIVE_BASE=PASS; FILES=2; SOURCE={BASE_SHA}", flush=True)


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

def check_db(path=None):
    conn = ro_conn(path or DB)
    try:
        db_health(conn)
        objects = schema_map(conn)
        for name in ("users", "permissions", "audit_log", *TABLES):
            if name not in objects or objects[name][0] != "table":
                fail("REQUIRED_TABLE_MISSING: " + name)
        for name, columns in TABLES.items():
            actual = tuple(row[1] for row in conn.execute(
                'PRAGMA table_info("' + name + '")'))
            if actual != columns:
                fail("ADMIN_SCHEMA_COLUMNS_MISMATCH: " + name)
        for name, table in INDEXES.items():
            if name not in objects or objects[name][:2] != ("index", table):
                fail("ADMIN_INDEX_MISSING: " + name)
        print(f"DATABASE=READ_ONLY_PASS; OBJECTS={len(objects)}", flush=True)
        return objects
    finally:
        conn.close()


def fetch_exact_blob(expected):
    """Fetch immutable Git blob, tolerating GitHub's wrapped base64 response."""
    url = f"https://api.github.com/repos/{REPO}/git/blobs/{expected}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "AZ-BAZE-PR258-Immutable-Release",
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

def extract_schema(source):
    tree = ast.parse(source)
    matches = [n for n in tree.body if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "SCHEMA"
                       for t in n.targets)]
    if len(matches) != 1:
        fail("SCHEMA_DECLARATION_NOT_UNIQUE")
    value = ast.literal_eval(matches[0].value)
    if not isinstance(value, str):
        fail("SCHEMA_DECLARATION_BAD")
    return value


def prepare(stage):
    for name, (_old, new) in FILES.items():
        data = fetch_exact_blob(new)
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        check_blob(target, new, "STAGED")
        if name.endswith(".py"):
            compile(data, str(target), "exec")
        elif name.endswith(".html"):
            run(str(VENV), "-c",
                "import pathlib,jinja2,sys;"
                "jinja2.Environment().parse("
                "pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))",
                str(target))
    if extract_schema((ROOT / "administrators.py").read_text(encoding="utf-8")) != \
       extract_schema((stage / "administrators.py").read_text(encoding="utf-8")):
        fail("SCHEMA_CHANGED_MIGRATION_FORBIDDEN")
    print(f"IMMUTABLE_TARGET=PASS; FILES=2; SOURCE={MERGED_SHA}", flush=True)
    print("SCHEMA_SOURCE=UNCHANGED; NO_MIGRATION", flush=True)


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
        destination = backup / "files" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
        check_blob(destination, old, "BACKUP")
    print("RUNTIME_BACKUP=PASS; FILES=2", flush=True)


def atomic_file_replace(source, destination):
    if destination.is_symlink() or not destination.is_file():
        fail("RUNTIME_TARGET_NOT_REGULAR")
    metadata = destination.stat()
    fd, temporary = tempfile.mkstemp(prefix=".pr258-", dir=destination.parent)
    path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(path, metadata.st_uid, metadata.st_gid)
        os.chmod(path, metadata.st_mode & 0o7777)
        os.replace(path, destination)
    finally:
        if path.exists():
            path.unlink()


def install(stage):
    # Validate the complete set again before changing either file.
    for name, (old, target) in FILES.items():
        check_blob(ROOT / name, old, "INSTALL_BASE")
        check_blob(stage / name, target, "INSTALL_STAGE")
    for name, (_old, target) in FILES.items():
        atomic_file_replace((stage / name).read_bytes(), ROOT / name)
        check_blob(ROOT / name, target, "INSTALLED")
    print("RUNTIME_INSTALL=PASS; FILES=2", flush=True)


def restore(backup):
    for name, (old, new) in FILES.items():
        check_blob(backup / "files" / name, old, "ROLLBACK_BACKUP")
        destination = ROOT / name
        if destination.is_symlink() or not destination.is_file():
            fail("ROLLBACK_TARGET_NOT_REGULAR")
        if git_blob(destination.read_bytes()) not in (old, new):
            fail("ROLLBACK_UNEXPECTED_THIRD_PARTY_CHANGE: " + name)
    for name, (old, _new) in FILES.items():
        atomic_file_replace((backup / "files" / name).read_bytes(), ROOT / name)
        check_blob(ROOT / name, old, "ROLLBACK_RESTORED")
    print("RUNTIME_ROLLBACK=PASS; DB_RESTORE_NOT_AUTOMATIC", flush=True)


def restart_and_smoke(rolled_back=False):
    run("systemctl", "start", SERVICE)
    for _ in range(25):
        try:
            if active() and http_status("/health") == (200, b"ok"):
                break
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    else:
        fail("RESTART_HEALTH_FAILED")
    smoke_routes(new_version=not rolled_back)
    print("PREVIOUS_SERVICE_RESTORED=PASS" if rolled_back else "SERVICE_SMOKE=PASS",
          flush=True)


def apply(stage):
    backup = BACKUPS / ("pr258-administrators-" +
                        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    backup.mkdir(parents=True, mode=0o700, exist_ok=False)
    stop_attempted = False
    touched = False
    try:
        stop_attempted = True
        run("systemctl", "stop", SERVICE)
        if active():
            fail("SERVICE_NOT_STOPPED")
        check_live_files()
        baseline = check_db()
        backup_files(backup)
        db_archive = backup / "auth-pre-pr258.sqlite"
        db_digest = backup_sqlite(db_archive)
        if check_db(db_archive) != baseline:
            fail("DATABASE_BACKUP_SCHEMA_MISMATCH")
        manifest = {
            "release": "PR258-administrators", "base": BASE_SHA,
            "audited_head": AUDITED_HEAD, "merged_sha": MERGED_SHA,
            "files": FILES, "backup_sha256": db_digest, "db_delta": [],
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "auto_db_restore": False,
        }
        path = backup / "release-manifest.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.chmod(path, 0o600)
        touched = True
        install(stage)
        if check_db() != baseline:
            fail("DATABASE_SCHEMA_CHANGED")
        print("DATABASE_MIGRATION=NONE; DATABASE_WRITES_BY_INSTALLER=NONE",
              flush=True)
        restart_and_smoke()
        for name, (_old, target) in FILES.items():
            check_blob(ROOT / name, target, "POST_RELEASE")
        if check_db() != baseline:
            fail("POST_RELEASE_SCHEMA_CHANGED")
        print(f"PR258_RELEASE=PASS; MERGED_SHA={MERGED_SHA}; BACKUP={backup}",
              flush=True)
        print("BROWSER_ACCEPTANCE=PENDING", flush=True)
    except BaseException:
        print("PR258_RELEASE=FAIL; BEGIN_CODE_ROLLBACK", file=sys.stderr, flush=True)
        try:
            if stop_attempted:
                if touched:
                    run("systemctl", "stop", SERVICE)
                    if active():
                        fail("ROLLBACK_SERVICE_NOT_STOPPED")
                    restore(backup)
                restart_and_smoke(rolled_back=True)
        except BaseException as rollback_error:
            print("CODE_ROLLBACK_FAILED=" + str(rollback_error),
                  file=sys.stderr, flush=True)
        print("BACKUP_PATH=" + str(backup) + "; DB_RESTORE_NOT_AUTOMATIC",
              file=sys.stderr, flush=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if os.geteuid() != 0 or socket.gethostname().split(".")[0] != "vdska":
        fail("RUN_IN_ROOT_VDSKA")
    if not VENV.is_file() or not Path("/etc/az-baze/auth.env").is_file():
        fail("AZ_BAZE_RUNTIME_OR_SERVICE_ENV_MISSING")
    with tempfile.TemporaryDirectory(prefix="pr258-stage-") as temporary:
        stage = Path(temporary)
        print(f"RELEASE_SOURCE={MERGED_SHA}; EXPECTED_BASE={BASE_SHA}; "
              f"AUDITED_HEAD={AUDITED_HEAD}", flush=True)
        health()
        smoke_routes()
        check_live_files()
        check_db()
        prepare(stage)
        print("PR258_PREFLIGHT=PASS; PRODUCTION_NOT_MODIFIED", flush=True)
        if args.apply:
            apply(stage)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print("PR258_CONTROLLED_STOP=" + type(exc).__name__ + ": " + str(exc),
              file=sys.stderr, flush=True)
        sys.exit(1)
