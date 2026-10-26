#!/usr/bin/env python3
import argparse
import ast
import fcntl
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
import urllib.request
from pathlib import Path

EXPECTED_HOST = "az-server"
SERVICE = "az-baze-auth"
HEALTH_URL = "http://127.0.0.1:8001/health"
APP_ROOT = Path("/opt/az-baze-auth")
REPORT_ROOT = Path("/var/www/az-baze.ru/reports")
DB_PATH = Path("/var/lib/az-baze/auth.db")
BACKUP_ROOT = Path("/var/lib/az-baze/backups")
LOCK_PATH = Path("/run/lock/az-advertising-production.lock")

BASE_COMMIT = "16d4dd615b2b1ae3d830ca15910d737cf6e87ee2"
TARGET_COMMIT = "9127255f0e6287e086a18cf6812dc3dc1114c526"
TOKEN = "ADVERTISING-PRODUCTION-9127255F"
REPO = "pat16071975-ship-it/architecture-health-site"

AD_TABLES = (
    "advertising_traffic_daily",
    "advertising_clinic_daily",
    "advertising_cohort_daily",
    "advertising_report_imports",
)

FILES = (
    {
        "repo": "az-baze-auth/advertising_report.py",
        "live": APP_ROOT / "advertising_report.py",
        "base": None,
        "target": "e003a9ab4cdbd245897d23f2dbd9be471aebcaca",
        "template": APP_ROOT / "report_storage.py",
    },
    {
        "repo": "reports/advertising.js",
        "live": REPORT_ROOT / "advertising.js",
        "base": None,
        "target": "083e3e43abb65d41abc7e01c236bbe3271103613",
        "template": REPORT_ROOT / "menu.html",
    },
    {
        "repo": "reports/advertising.html",
        "live": REPORT_ROOT / "advertising.html",
        "base": None,
        "target": "9256fe0f8dcb6a190f5b8f85839eca5766d32c1e",
        "template": REPORT_ROOT / "menu.html",
    },
    {
        "repo": "az-baze-auth/report_storage.py",
        "live": APP_ROOT / "report_storage.py",
        "base": "367592919431997255862ed179a5c6a4f93703ad",
        "target": "0e50ed2827b0257107caffe8b8a4d71999b76a17",
        "template": None,
    },
    {
        "repo": "reports/menu.html",
        "live": REPORT_ROOT / "menu.html",
        "base": "33a7c389cd564312f9690c7115597fa9a080341c",
        "target": "af6df9c05a76feced44998cd72d6dba1cbcf7635",
        "template": None,
    },
)

def run(cmd, check=True):
    p = subprocess.run(cmd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(
            "command failed: " + " ".join(map(str, cmd)) +
            "\nstdout=" + p.stdout + "\nstderr=" + p.stderr
        )
    return p

def service_active():
    return run(["systemctl", "is-active", "--quiet", SERVICE], check=False).returncode == 0

def health_ok():
    if not service_active():
        return False
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as response:
            return response.status == 200 and response.read().decode("utf-8").strip() == "ok"
    except Exception:
        return False

def git_blob_bytes(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()

def git_blob_file(path):
    return git_blob_bytes(path.read_bytes())

def regular_file(path):
    return path.is_file() and not path.is_symlink() and path.resolve() == path

def file_state():
    base_ok = True
    target_ok = True
    rows = []
    for item in FILES:
        path = item["live"]
        exists = path.exists()
        actual = git_blob_file(path) if exists and regular_file(path) else None
        if item["base"] is None:
            base_match = not exists
        else:
            base_match = exists and regular_file(path) and actual == item["base"]
        target_match = exists and regular_file(path) and actual == item["target"]
        base_ok = base_ok and base_match
        target_ok = target_ok and target_match
        rows.append((str(path), actual or "ABSENT", base_match, target_match))
    return base_ok, target_ok, rows

def db_connect_ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)

def db_integrity(path):
    conn = db_connect_ro(path)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()

def ad_schema_state(path):
    conn = db_connect_ro(path)
    try:
        found = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ({})".format(
                    ",".join("?" for _ in AD_TABLES)
                ),
                AD_TABLES,
            )
        }
        counts = {}
        for table in sorted(found):
            counts[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        return found, counts
    finally:
        conn.close()

def validate_environment():
    if os.geteuid() != 0:
        raise RuntimeError("STOP: ROOT_REQUIRED")
    if socket.gethostname().split(".")[0] != EXPECTED_HOST:
        raise RuntimeError("STOP: WRONG_HOST")
    for command in ("systemctl", "python3"):
        if not shutil.which(command):
            raise RuntimeError("STOP: MISSING_TOOL=" + command)
    if not regular_file(DB_PATH):
        raise RuntimeError("STOP: LIVE_DB_MISSING_OR_UNSAFE")
    if not APP_ROOT.is_dir() or not REPORT_ROOT.is_dir():
        raise RuntimeError("STOP: LIVE_ROOT_MISSING")
    if not health_ok():
        raise RuntimeError("STOP: LIVE_HEALTH_NOT_OK")
    if db_integrity(DB_PATH) != "ok":
        raise RuntimeError("STOP: LIVE_DB_INTEGRITY_FAIL")

def preflight_state():
    validate_environment()
    base_ok, target_ok, rows = file_state()
    found, counts = ad_schema_state(DB_PATH)
    print("=== ADVERTISING REPORT PRODUCTION PREFLIGHT ===")
    print("BASE_COMMIT=" + BASE_COMMIT)
    print("TARGET_COMMIT=" + TARGET_COMMIT)
    for path, actual, base_match, target_match in rows:
        print(f"FILE={path}; BLOB={actual}; BASE={'YES' if base_match else 'NO'}; TARGET={'YES' if target_match else 'NO'}")
    print("AD_TABLES_PRESENT=" + ",".join(sorted(found)) if found else "AD_TABLES_PRESENT=NONE")
    if counts:
        print("AD_TABLE_COUNTS=" + json.dumps(counts, ensure_ascii=False, sort_keys=True))
    if base_ok:
        if found:
            raise RuntimeError("STOP: BASE_FILES_BUT_AD_SCHEMA_PRESENT")
        state = "BASE"
    elif target_ok:
        if found != set(AD_TABLES):
            raise RuntimeError("STOP: TARGET_FILES_BUT_SCHEMA_INCOMPLETE")
        state = "TARGET"
    else:
        raise RuntimeError("STOP: LIVE_BASE_OR_MIXED_STATE_MISMATCH")
    print("LIVE_STATE=" + state)
    print("SERVICE_HEALTH_PRE=PASS")
    print("DB_INTEGRITY_PRE=PASS")
    return state

def download_targets(stage):
    for item in FILES:
        target = stage / Path(item["repo"]).name
        url = f"https://raw.githubusercontent.com/{REPO}/{TARGET_COMMIT}/{item['repo']}"
        with urllib.request.urlopen(url, timeout=30) as response:
            raw = response.read()
        actual = git_blob_bytes(raw)
        if actual != item["target"]:
            raise RuntimeError(f"STOP: TARGET_BLOB_MISMATCH={item['repo']}:{actual}")
        target.write_bytes(raw)
        item["stage"] = target
    print("TARGET_BLOBS=PASS")

def syntax_and_schema_rehearsal(stage, backup_db):
    py_targets = [item for item in FILES if item["repo"].endswith(".py")]
    for item in py_targets:
        source = item["stage"].read_text(encoding="utf-8")
        compile(source, item["repo"], "exec")

    ad_source = next(item["stage"] for item in FILES if item["repo"].endswith("advertising_report.py"))
    tree = ast.parse(ad_source.read_text(encoding="utf-8"))
    schema = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SCHEMA" for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                schema = node.value.value
                break
    if not schema:
        raise RuntimeError("STOP: SCHEMA_CONSTANT_NOT_FOUND")

    rehearsal = stage / "rehearsal.db"
    shutil.copy2(backup_db, rehearsal)
    conn = sqlite3.connect(rehearsal)
    try:
        conn.executescript(schema)
        conn.commit()
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("STOP: REHEARSAL_INTEGRITY_FAIL")
        found = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ({})".format(
                    ",".join("?" for _ in AD_TABLES)
                ),
                AD_TABLES,
            )
        }
        if found != set(AD_TABLES):
            raise RuntimeError("STOP: REHEARSAL_SCHEMA_INCOMPLETE")
        for table in AD_TABLES:
            if conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] != 0:
                raise RuntimeError("STOP: REHEARSAL_TABLE_NOT_EMPTY=" + table)
    finally:
        conn.close()
    print("ISOLATED_SCHEMA_REHEARSAL=PASS")

def sqlite_backup(source, destination):
    src = db_connect_ro(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    if db_integrity(destination) != "ok":
        raise RuntimeError("STOP: BACKUP_DB_INTEGRITY_FAIL")

def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def atomic_write(raw, target, meta_source):
    if target.exists() and not regular_file(target):
        raise RuntimeError("STOP: UNSAFE_TARGET=" + str(target))
    st = meta_source.stat()
    fd, name = tempfile.mkstemp(prefix=".advertising-deploy-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchown(handle.fileno(), st.st_uid, st.st_gid)
            os.fchmod(handle.fileno(), st.st_mode & 0o7777)
        os.replace(name, target)
        fsync_dir(target.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def restore_db(backup_db, meta):
    fd, name = tempfile.mkstemp(prefix=".advertising-db-restore-", dir=DB_PATH.parent)
    os.close(fd)
    tmp = Path(name)
    try:
        shutil.copyfile(backup_db, tmp)
        os.chown(tmp, meta["uid"], meta["gid"])
        os.chmod(tmp, meta["mode"])
        with tmp.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, DB_PATH)
        fsync_dir(DB_PATH.parent)
    finally:
        tmp.unlink(missing_ok=True)

def apply():
    state = preflight_state()
    if state == "TARGET":
        print("ALREADY_INSTALLED=PASS")
        print("ADVERTISING_REPORT_PRODUCTION_DEPLOY=PASS")
        return

    lock_file = LOCK_PATH.open("w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("STOP: DEPLOY_ALREADY_RUNNING")

    stage = Path(tempfile.mkdtemp(prefix="az-advertising-production-", dir="/tmp"))
    backup = None
    service_stopped = False
    files_changed = False
    db_backed_up = False
    original_meta = {}
    db_meta = None
    try:
        download_targets(stage)

        # Recheck immediately before downtime.
        state2 = preflight_state()
        if state2 != "BASE":
            raise RuntimeError("STOP: LIVE_STATE_CHANGED_BEFORE_APPLY")

        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="advertising-report-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-", dir=BACKUP_ROOT))
        (backup / "files").mkdir()

        for item in FILES:
            path = item["live"]
            if path.exists():
                st = path.stat()
                original_meta[str(path)] = {"uid": st.st_uid, "gid": st.st_gid, "mode": st.st_mode & 0o7777}
                shutil.copy2(path, backup / "files" / path.name)

        db_st = DB_PATH.stat()
        db_meta = {"uid": db_st.st_uid, "gid": db_st.st_gid, "mode": db_st.st_mode & 0o7777}

        run(["systemctl", "stop", SERVICE])
        service_stopped = True
        if service_active():
            raise RuntimeError("STOP: SERVICE_DID_NOT_STOP")
        print("SERVICE_STOP=PASS")

        backup_db = backup / "auth-before-advertising.db"
        sqlite_backup(DB_PATH, backup_db)
        db_backed_up = True
        print("DB_BACKUP=PASS")
        print("BACKUP=" + str(backup))

        syntax_and_schema_rehearsal(stage, backup_db)

        manifest = {
            "base_commit": BASE_COMMIT,
            "target_commit": TARGET_COMMIT,
            "files": [
                {
                    "repo": item["repo"],
                    "live": str(item["live"]),
                    "base": item["base"],
                    "target": item["target"],
                }
                for item in FILES
            ],
            "db_backup": str(backup_db),
        }
        (backup / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        # Install new files first, integration files last.
        for item in FILES:
            live = item["live"]
            meta_source = live if live.exists() else item["template"]
            if not regular_file(meta_source):
                raise RuntimeError("STOP: METADATA_TEMPLATE_UNSAFE=" + str(meta_source))
            atomic_write(item["stage"].read_bytes(), live, meta_source)
            if git_blob_file(live) != item["target"]:
                raise RuntimeError("STOP: LIVE_TARGET_BLOB_FAIL=" + str(live))
        files_changed = True
        print("ATOMIC_FILE_INSTALL=PASS")

        run(["systemctl", "start", SERVICE])
        service_stopped = False
        ok = False
        for _ in range(40):
            if health_ok():
                ok = True
                break
            time.sleep(0.5)
        if not ok:
            raise RuntimeError("STOP: POST_DEPLOY_HEALTH_FAIL")
        print("SERVICE_HEALTH_POST=PASS")

        base_ok, target_ok, rows = file_state()
        if not target_ok:
            raise RuntimeError("STOP: POST_DEPLOY_FILE_STATE_FAIL")
        found, counts = ad_schema_state(DB_PATH)
        if found != set(AD_TABLES):
            raise RuntimeError("STOP: POST_DEPLOY_SCHEMA_INCOMPLETE")
        if any(counts.get(table, -1) != 0 for table in AD_TABLES):
            raise RuntimeError("STOP: AD_TABLE_NOT_EMPTY_AFTER_FIRST_DEPLOY")
        if db_integrity(DB_PATH) != "ok":
            raise RuntimeError("STOP: POST_DEPLOY_DB_INTEGRITY_FAIL")

        print("LIVE_TARGET_BLOBS=PASS")
        print("ADVERTISING_SCHEMA=PASS")
        print("ADVERTISING_TABLES_EMPTY=PASS")
        print("DB_INTEGRITY_POST=PASS")
        print("=== FINAL ===")
        print("ADVERTISING_REPORT_PRODUCTION_DEPLOY=PASS")
        print("TARGET_COMMIT=" + TARGET_COMMIT)
        print("BACKUP=" + str(backup))
        print("SERVICE=active")
        print("HEALTH=ok")
        print("CHANGED_RUNTIME_FILES=5")
        print("ADVERTISING_DATA_ROWS=0")
    except Exception:
        exc = sys.exc_info()[1]
        print("DEPLOY_EXCEPTION=" + repr(exc))
        rollback_ok = True
        try:
            run(["systemctl", "stop", SERVICE], check=False)
            service_stopped = True

            if files_changed and backup is not None:
                for item in reversed(FILES):
                    live = item["live"]
                    saved = backup / "files" / live.name
                    if item["base"] is None:
                        if live.exists():
                            live.unlink()
                            fsync_dir(live.parent)
                    else:
                        meta = original_meta[str(live)]
                        atomic_write(saved.read_bytes(), live, saved)
                        os.chown(live, meta["uid"], meta["gid"])
                        os.chmod(live, meta["mode"])
                        if git_blob_file(live) != item["base"]:
                            rollback_ok = False

            if db_backed_up and backup is not None and db_meta is not None:
                restore_db(backup / "auth-before-advertising.db", db_meta)
                if db_integrity(DB_PATH) != "ok":
                    rollback_ok = False

            run(["systemctl", "start", SERVICE], check=False)
            service_stopped = False
            if not health_ok():
                rollback_ok = False
        except Exception as rollback_exc:
            rollback_ok = False
            print("ROLLBACK_EXCEPTION=" + repr(rollback_exc))

        print("ADVERTISING_REPORT_ROLLBACK=" + ("PASS" if rollback_ok else "FAIL"))
        if backup is not None:
            print("BACKUP=" + str(backup))
        print("ADVERTISING_REPORT_PRODUCTION_DEPLOY=FAIL")
        raise
    finally:
        if service_stopped:
            run(["systemctl", "start", SERVICE], check=False)
        shutil.rmtree(stage, ignore_errors=True)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()

def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--apply", default="")
    args = parser.parse_args()

    if args.preflight:
        state = preflight_state()
        print("ADVERTISING_REPORT_PREFLIGHT=PASS")
        print("LIVE_CHANGES=NONE")
        return 0

    if args.apply != TOKEN:
        raise RuntimeError("STOP: CONFIRM_TOKEN_MISMATCH")
    apply()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
