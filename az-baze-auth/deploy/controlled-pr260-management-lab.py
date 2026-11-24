#!/usr/bin/env python3
"""Pinned PR260 controlled release. Six allowlisted files, NO DB migration.

Install the audited merged backend and management report assets together.
Use existing local deployment paths; never replace the database.
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
BASE_SHA = "0e8c5015e7442167941e68f410ba3029c1f8969e"
AUDITED_HEAD = "11cfe9fbbfacf3fe8c6b1b06392160f2c16e49b2"
MERGED_SHA = "5f7fc09e25954034946a9a334f38d331ddc16284"
ROOT = Path("/opt/az-baze-auth")
REPORTS = Path("/var/www/az-baze.ru/reports")
DB = Path("/var/lib/az-baze/auth.db")
BACKUPS = Path("/var/lib/az-baze/backups")
VENV = ROOT / "venv/bin/python"
SERVICE = "az-baze-auth"
LOCAL = "http://127.0.0.1:8001"
# Publish new dependencies first, then their consumers; HTML is last.
FILES = {
    "az-baze-auth/management_lab.py": (None, "0cee9dfa4e7fb473df22833ed99b672a52cfc096"),
    "az-baze-auth/paid_services.py": ("7731d49098494276e40442d06443a0729fc7fcf3", "1bf1009439b5ca5cbd6b70fa2601a146e4e5cd86"),
    "az-baze-auth/report_storage.py": ("15cc66be731742f34618ff3602c13d227dcf570c", "46b4b6203df33ec48c5bcc448e61fb82cab8ad7d"),
    "reports/management-lab.js": (None, "f63fda25920f8ff6c6267616b73a4f8edb3d9bc1"),
    "reports/period-view.js": ("3e89a095cb58dcb1b08fd02e25a216642b8a6c3e", "b88587ac112cf7ff4dddf6779eab1ecfb3c93f04"),
    "reports/index.html": ("18b6971fad34f7525c8909769e969b64c944c1a8", "a51b51e43e14d021bc9f58e95adee28d18fc6d46"),
}
EXEMPLARS = {
    "az-baze-auth/management_lab.py": "az-baze-auth/paid_services.py",
    "reports/management-lab.js": "reports/period-view.js",
}
TABLES = {
    "users": {"id"},
    "permissions": {"user_id", "section"},
    "audit_log": {"id"},
    "report_data": {"date","payload","updated_by","updated_at"},
    "report_blobs": {"key","payload"},
    "daily_uploads": {"data_date","normalized_json","services_sha256"},
    "service_payment_snapshots": {"as_of_date","month","payload_json","source_sha256","imported_at"},
    "service_payment_periods": {"period_start","period_end","payload_json","source_sha256","imported_at"},
    "service_payment_versions": {"id","as_of_date","payload_json"},
    "service_payment_period_versions": {"id","period_start","period_end","payload_json"},
    "cash_receipts_daily": {"data_date","payload_json"},
}
SCHEMA_KEYS = {
    "az-baze-auth/paid_services.py": "SCHEMA",
    "az-baze-auth/report_storage.py": "REPORT_SCHEMA",
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

def check_blob(path, expected, label):
    if path.is_symlink() or not path.is_file():
        fail(f"{label}_NOT_REGULAR_FILE: {path}")
    actual = git_blob(path.read_bytes())
    if actual != expected:
        fail(f"{label}_BLOB_MISMATCH: {path}; actual={actual}; expected={expected}")

def destination(name):
    if name not in FILES:
        fail("FILE_NOT_ALLOWLISTED: " + name)
    prefix, filename = name.split("/", 1)
    if "/" in filename or filename in (".", ".."):
        fail("UNSAFE_RUNTIME_FILE")
    return (ROOT if prefix == "az-baze-auth" else REPORTS) / filename


def check_expected(path, expected, label):
    if expected is None:
        if path.exists() or path.is_symlink():
            fail(f"{label}_NEW_FILE_ALREADY_EXISTS: {path}")
    else:
        check_blob(path, expected, label)


def check_live_files():
    for directory in (ROOT, REPORTS):
        if directory.resolve() != directory or not directory.is_dir():
            fail("RUNTIME_DIRECTORY_MISSING_OR_SYMLINK: " + str(directory))
    for name, (old, _new) in FILES.items():
        check_expected(destination(name), old, "LIVE_BASE")
    print(f"LIVE_BASE=PASS; EXISTING=4; NEW_ABSENT=2; SOURCE={BASE_SHA}", flush=True)


def smoke_routes(new_version=False):
    routes = ["/", "/reports/management/", "/api/reports/data",
              "/api/reports/data?view=dashboard", "/reports/period-view.js",
              "/administrators/"]
    if new_version:
        routes.append("/reports/management-lab.js?v=20261124-2")
    for route in routes:
        status, _ = http_status(route)
        if status not in (302, 401, 403):
            fail(f"ROUTE_UNPROTECTED_OR_MISSING: {route} status={status}")
    print("MANAGEMENT_ROUTES=PASS; ANONYMOUS_PROTECTED", flush=True)


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
        for name, columns in TABLES.items():
            if name not in objects or objects[name][0] != "table":
                fail("REQUIRED_TABLE_MISSING: " + name)
            actual = {row[1] for row in conn.execute('PRAGMA table_info("' + name + '")')}
            if not columns <= actual:
                fail("REQUIRED_COLUMNS_MISSING: " + name)
        print(f"DATABASE=READ_ONLY_PASS; OBJECTS={len(objects)}", flush=True)
        return objects
    finally:
        conn.close()


def fetch_exact_blob(expected):
    """Fetch immutable Git blob, tolerating GitHub's wrapped base64 response."""
    url = f"https://api.github.com/repos/{REPO}/git/blobs/{expected}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "AZ-BAZE-PR260-Immutable-Release",
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

def extract_schema(source, key):
    tree = ast.parse(source)
    matches = [n for n in tree.body if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == key
                       for t in n.targets)]
    if len(matches) != 1:
        fail("SCHEMA_DECLARATION_NOT_UNIQUE: " + key)
    value = ast.literal_eval(matches[0].value)
    if not isinstance(value, str):
        fail("SCHEMA_DECLARATION_BAD: " + key)
    return value


def validate_stage(stage):
    for name, (_old, new) in FILES.items():
        target = stage / name
        check_blob(target, new, "STAGED")
        if name.endswith(".py"):
            compile(target.read_bytes(), str(target), "exec")
    for name, key in SCHEMA_KEYS.items():
        if extract_schema(destination(name).read_text(encoding="utf-8"), key) != \
           extract_schema((stage / name).read_text(encoding="utf-8"), key):
            fail("SCHEMA_CHANGED_MIGRATION_FORBIDDEN: " + name)
    html = (stage / "reports/index.html").read_text(encoding="utf-8")
    tags = ('<script src="./management-lab.js?v=20261124-2"></script>',
            '<script src="./period-view.js?v=20261124-lab-2"></script>')
    if any(html.count(tag) != 1 for tag in tags) or html.index(tags[0]) >= html.index(tags[1]):
        fail("MANAGEMENT_ASSET_CONTRACT_BAD")
    node = shutil.which("node")
    if node:
        for name in ("reports/management-lab.js", "reports/period-view.js"):
            run(node, "--check", str(stage / name))
        print("JS_SYNTAX=PASS", flush=True)
    else:
        print("JS_SYNTAX=VERIFIED_IN_CI; NODE_NOT_REQUIRED_ON_SERVER", flush=True)
    print(f"IMMUTABLE_TARGET=PASS; FILES=6; SOURCE={MERGED_SHA}", flush=True)
    print("SCHEMA_SOURCE=UNCHANGED; NO_MIGRATION", flush=True)


def prepare(stage):
    for name, (_old, new) in FILES.items():
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fetch_exact_blob(new))
    validate_stage(stage)


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
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    result = digest.hexdigest()
    print(f"DATABASE_BACKUP=PASS; SHA256={result}", flush=True)
    return result

def backup_files(backup):
    for name, (old, _new) in FILES.items():
        if old is None:
            continue
        path = backup / "files" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destination(name), path)
        check_blob(path, old, "BACKUP")
    print("RUNTIME_BACKUP=PASS; FILES=4; NEW_FILES=2", flush=True)


def atomic_file_replace(data, path, exemplar=None):
    # For a new file use a sibling's existing permissions, and create it
    # without replacing anything that appeared since the initial check.
    metadata_source = exemplar or path
    if metadata_source.is_symlink() or not metadata_source.is_file():
        fail("RUNTIME_METADATA_NOT_REGULAR")
    metadata = metadata_source.stat()
    if exemplar is not None:
        check_expected(path, None, "NEW_TARGET")
    fd, temporary = tempfile.mkstemp(prefix=".pr260-", dir=path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temp, metadata.st_uid, metadata.st_gid)
        os.chmod(temp, metadata.st_mode & 0o777)
        if exemplar is not None:
            os.link(temp, path)
        else:
            if path.is_symlink() or not path.is_file():
                fail("RUNTIME_TARGET_NOT_REGULAR")
            os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def install(stage):
    for name, (old, new) in FILES.items():
        check_expected(destination(name), old, "INSTALL_BASE")
        check_blob(stage / name, new, "INSTALL_STAGE")
    for name, (old, new) in FILES.items():
        path = destination(name)
        check_expected(path, old, "INSTALL_BASE")
        exemplar = destination(EXEMPLARS[name]) if old is None else None
        atomic_file_replace((stage / name).read_bytes(), path, exemplar)
        check_blob(path, new, "INSTALLED")
    print("RUNTIME_INSTALL=PASS; BACKEND=3; REPORT_ASSETS=3", flush=True)


def restore(backup):
    # Validate the whole rollback set before changing anything. Never destroy
    # a third-party edit or remove a new path with an unexpected hash.
    for name, (old, new) in FILES.items():
        path = destination(name)
        if old is not None:
            check_blob(backup / "files" / name, old, "ROLLBACK_BACKUP")
        if old is None and not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file():
            fail("ROLLBACK_TARGET_NOT_REGULAR: " + name)
        if git_blob(path.read_bytes()) not in (old, new):
            fail("ROLLBACK_UNEXPECTED_THIRD_PARTY_CHANGE: " + name)
    # Restore old consumers before removing newly introduced dependencies.
    for name, (old, _new) in reversed(tuple(FILES.items())):
        path = destination(name)
        if old is None:
            if path.exists():
                path.unlink()
            check_expected(path, None, "ROLLBACK_REMOVED")
        else:
            atomic_file_replace((backup / "files" / name).read_bytes(), path)
            check_blob(path, old, "ROLLBACK_RESTORED")
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
    backup = BACKUPS / ("pr260-management-lab-" +
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
        db_archive = backup / "auth-pre-pr260.sqlite"
        digest = backup_sqlite(db_archive)
        if check_db(db_archive) != baseline:
            fail("DATABASE_BACKUP_SCHEMA_MISMATCH")
        manifest = {
            "release": "PR260-management-lab", "base": BASE_SHA,
            "audited_head": AUDITED_HEAD, "merged_sha": MERGED_SHA,
            "files": FILES, "backup_sha256": digest, "db_delta": [],
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
            check_blob(destination(name), target, "POST_RELEASE")
        if check_db() != baseline:
            fail("POST_RELEASE_SCHEMA_CHANGED")
        print(f"PR260_RELEASE=PASS; MERGED_SHA={MERGED_SHA}; BACKUP={backup}",
              flush=True)
        print("HISTORICAL_REIMPORT=NONE; BROWSER_ACCEPTANCE=PENDING", flush=True)
    except BaseException:
        print("PR260_RELEASE=FAIL; BEGIN_CODE_ROLLBACK", file=sys.stderr, flush=True)
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
    with tempfile.TemporaryDirectory(prefix="pr260-stage-") as temporary:
        stage = Path(temporary)
        print(f"RELEASE_SOURCE={MERGED_SHA}; EXPECTED_BASE={BASE_SHA}; "
              f"AUDITED_HEAD={AUDITED_HEAD}", flush=True)
        health()
        smoke_routes()
        check_live_files()
        check_db()
        prepare(stage)
        print("PR260_PREFLIGHT=PASS; PRODUCTION_NOT_MODIFIED", flush=True)
        if args.apply:
            apply(stage)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print("PR260_CONTROLLED_STOP=" + type(exc).__name__ + ": " + str(exc),
              file=sys.stderr, flush=True)
        sys.exit(1)
