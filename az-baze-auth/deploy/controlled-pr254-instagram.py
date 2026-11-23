#!/usr/bin/env python3
"""Controlled AZ BAZE PR254 runtime release; exact files, immutable blobs, no DB writes.

Default is read-only --preflight. --apply is limited to three approved runtime
files. Refuses unknown live versions before touching service or database.
"""
import argparse
import base64
import hashlib
import json
import os
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
BASE_SHA = "83405d50bd1a508d4a90641907c8a5586f541147"
MERGED_SHA = "ffd069d35c9a79f40e3035d1746e2dbd0c9be508"
ROOT = Path("/opt/az-baze-auth")
DB = Path("/var/lib/az-baze/auth.db")
BACKUPS = Path("/var/lib/az-baze/backups")
VENV = ROOT / "venv/bin/python"
SERVICE = "az-baze-auth"
LOCAL = "http://127.0.0.1:8001"
FILES = {
    "advertising_report.py": ("04a355efe10d4068405e3d34e6ec41329b913eee", "1696a6a0daf4b2982adf7d80c217e015ff4f9352"),
    "advertising_monthly_costs.py": ("6dbe35e272181161571e1c454bf51e2928e40a10", "ddfe031c730ba98ee12fd9368aead07d97b567bc"),
    "templates/advertising_costs_fragment.html": ("e0a9be80d3b54ffd6576bba8ebe8e710bb04cc30", "2c4a3402055619ec440f5c23ca356ee98c49a708"),
}
ROUTES = (
    "/reports/advertising/",
    "/api/reports/advertising?from=2026-11-01&to=2026-11-23",
    "/api/reports/advertising/costs/monthly?month=2026-11",
)


def fail(message):
    raise RuntimeError(message)


def git_blob(data):
    return hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()


def blob_check(path, expected, label):
    if path.is_symlink() or not path.is_file():
        fail(f"{label}_NOT_REGULAR_FILE: {path}")
    actual = git_blob(path.read_bytes())
    if actual != expected:
        fail(f"{label}_BLOB_MISMATCH: {path.name}; expected={expected}; actual={actual}")


def run(*args, check=True):
    result = subprocess.run(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        check=False,
    )
    if check and result.returncode != 0:
        fail(f"COMMAND_FAIL: {args[0]}: " + (result.stderr or result.stdout)[-400:])
    return result


def service_active():
    return run("systemctl", "is-active", "--quiet", SERVICE, check=False).returncode == 0


def http_status(path):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, url):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(LOCAL + path, timeout=4) as response:
            return response.status, response.read(128)
    except urllib.error.HTTPError as error:
        return error.code, b""


def health():
    if not service_active():
        fail("SERVICE_INACTIVE")
    code, body = http_status("/health")
    if (code, body.strip()) != (200, b"ok"):
        fail(f"HEALTH_NOT_READY: {code}")
    print("SERVICE_HEALTH=PASS")


def route_smoke():
    for path in ROUTES:
        code, _ = http_status(path)
        if code not in (302, 401, 403):
            fail(f"PROTECTED_ROUTE_SMOKE_FAIL: code={code}; path={path}")
    print("ADVERTISING_ROUTES=PASS; ANONYMOUS_PROTECTED")


def check_live_files():
    if not ROOT.is_dir() or not (ROOT / "templates").is_dir():
        fail("RUNTIME_DIRECTORY_MISSING")
    for name, (old, _new) in FILES.items():
        blob_check(ROOT / name, old, "LIVE_BASE")
    print(f"LIVE_BASE=PASS; FILES=3; SOURCE={BASE_SHA}")


def read_db(path):
    if path.is_symlink() or not path.is_file():
        fail("DATABASE_MISSING_OR_SYMLINK")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA query_only=ON")
    return conn


def check_db(path=None):
    path = path or DB
    conn = read_db(path)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            fail("DB_INTEGRITY_FAILED")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            fail("DB_FK_FAILED")
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        required = {"report_blobs", "users", "audit_log"}
        if not required <= names:
            fail(f"NOT_AZ_BAZE_DATABASE: {sorted(required - names)}")
        print(f"DATABASE=READ_ONLY_PASS; TABLES={len(names)}")
    finally:
        conn.close()


def fetch_blob(expected):
    url = f"https://api.github.com/repos/{REPO}/git/blobs/{expected}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "AZ-BAZE-PR254-Immutable-Release",
        "Accept": "application/vnd.github+json",
    })
    last_error = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=40) as response:
                if response.status != 200:
                    fail(f"GITHUB_BLOB_STATUS_BAD: {expected}")
                obj = json.loads(response.read(2 * 1024 * 1024))
            if obj.get("encoding") != "base64":
                fail("GITHUB_BLOB_ENCODING_INVALID")
            # GitHub Git Blob API wraps base64 at line boundaries. Remove
            # whitespace, but keep strict validation of all other characters
            # and verify the exact immutable Git blob before using bytes.
            encoded = obj.get("content")
            if not isinstance(encoded, str):
                fail("GITHUB_BLOB_CONTENT_INVALID")
            try:
                data = base64.b64decode("".join(encoded.split()), validate=True)
            except (ValueError, base64.binascii.Error) as exc:
                fail(f"GITHUB_BLOB_BASE64_INVALID: {type(exc).__name__}")
            if len(data) > 1024 * 1024 or git_blob(data) != expected:
                fail(f"IMMUTABLE_GIT_BLOB_INVALID: {expected}")
            return data
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
    fail(f"IMMUTABLE_GIT_BLOB_UNAVAILABLE: {type(last_error).__name__}")


def prepare(stage):
    for name, (_old, new) in FILES.items():
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = fetch_blob(new)
        target.write_bytes(data)
        if name.endswith(".py"):
            compile(data.decode("utf-8"), str(target), "exec")
    fragment = stage / "templates/advertising_costs_fragment.html"
    test = run(str(VENV), "-c",
               "import pathlib,sys,jinja2; jinja2.Environment().parse("
               "pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))",
               str(fragment))
    if test.returncode:
        fail("TEMPLATE_JINJA_SYNTAX_BAD")
    print(f"IMMUTABLE_TARGET=PASS; FILES=3; MERGED_SHA={MERGED_SHA}")


def preflight(stage):
    if os.geteuid() != 0:
        fail("ROOT_REQUIRED")
    if not VENV.is_file() or not Path("/etc/az-baze/auth.env").is_file():
        fail("AZ_BAZE_SERVICE_ENV_OR_VENV_MISSING")
    health()
    route_smoke()
    check_live_files()
    check_db()
    prepare(stage)
    print("PR254_PREFLIGHT=PASS; PRODUCTION_NOT_MODIFIED")


def backup_db(destination):
    source = read_db(DB)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    check_db(destination)
    sha = hashlib.sha256(destination.read_bytes()).hexdigest()
    print(f"DATABASE_BACKUP=PASS; SHA256={sha}")
    return sha


def backup_files(root):
    for name, (old, _new) in FILES.items():
        target = root / "files" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
        blob_check(target, old, "BACKUP")


def atomic_replace(data, destination):
    if destination.is_symlink() or not destination.is_file():
        fail(f"UNSAFE_RUNTIME_PATH: {destination}")
    meta = destination.stat()
    fd, temp_name = tempfile.mkstemp(prefix=".az-pr254-", dir=destination.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chown(temp, meta.st_uid, meta.st_gid)
        os.chmod(temp, meta.st_mode & 0o777)
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()


def install(stage):
    for name, (_old, new) in FILES.items():
        dest = ROOT / name
        atomic_replace((stage / name).read_bytes(), dest)
        blob_check(dest, new, "INSTALLED")
    print("RUNTIME_INSTALL=PASS; THREE_EXACT_FILES")


def rollback(backup):
    for name, (old, _new) in FILES.items():
        prior = backup / "files" / name
        blob_check(prior, old, "ROLLBACK_BACKUP")
        atomic_replace(prior.read_bytes(), ROOT / name)
        blob_check(ROOT / name, old, "RESTORED")
    print("RUNTIME_ROLLBACK=PASS; DATABASE_NOT_CHANGED")


def restart_and_smoke():
    run("systemctl", "start", SERVICE)
    for _ in range(20):
        try:
            if service_active():
                code, body = http_status("/health")
                if (code, body.strip()) == (200, b"ok"):
                    break
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    else:
        fail("SERVICE_RESTART_HEALTH_FAILED")
    route_smoke()
    print("SERVICE_SMOKE=PASS")


def apply(stage):
    label = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUPS / f"pr254-instagram-{label}"
    if backup.exists():
        fail("BACKUP_DIRECTORY_ALREADY_EXISTS")
    backup.mkdir(parents=True, mode=0o700)
    service_stopped = False
    have_backup = False
    touched = False
    try:
        service_stopped = True
        run("systemctl", "stop", SERVICE)
        if service_active():
            fail("SERVICE_DID_NOT_STOP")
        check_live_files()
        backup_files(backup)
        sha = backup_db(backup / "auth-pre-pr254.sqlite")
        have_backup = True
        manifest = {
            "release": "PR254-instagram-campaigns",
            "merged_sha": MERGED_SHA, "baseline_sha": BASE_SHA,
            "files": FILES, "db_backup_sha256": sha,
            "db_migration": False, "created_utc": label,
        }
        (backup / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.chmod(backup / "manifest.json", 0o600)
        touched = True
        install(stage)
        restart_and_smoke()
        for name, (_old, target) in FILES.items():
            blob_check(ROOT / name, target, "POST_RELEASE")
        check_db()
        print(f"PR254_RELEASE=PASS; MERGED_SHA={MERGED_SHA}; BACKUP={backup}")
        print("REAL_BROWSER_ACCEPTANCE=NOT_YET_PERFORMED")
        print("DATA_MIGRATIONS=NONE; PAYMENT_RECORDS=NOT_MODIFIED_BY_INSTALLER")
    except BaseException:
        print("PR254_RELEASE=FAIL; SAFE_CODE_ROLLBACK", file=sys.stderr)
        try:
            if service_stopped:
                run("systemctl", "stop", SERVICE, check=False)
                if touched and have_backup:
                    rollback(backup)
                restart_and_smoke()
                print("PREVIOUS_SERVICE_RESTORED=PASS", file=sys.stderr)
        except BaseException as error:
            print(f"ROLLBACK_FAILED: {error}", file=sys.stderr)
        print(f"BACKUP_LOCATION={backup}; DATABASE_NOT_RESTORED_AUTOMATICALLY",
              file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="az-pr254-stage-") as work:
        stage = Path(work)
        print(f"RELEASE_SOURCE={MERGED_SHA}; EXPECTED_BASE={BASE_SHA}")
        preflight(stage)
        if args.apply:
            apply(stage)


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        print(f"PR254_CONTROLLED_STOP={type(error).__name__}: {error}",
              file=sys.stderr)
        sys.exit(1)
