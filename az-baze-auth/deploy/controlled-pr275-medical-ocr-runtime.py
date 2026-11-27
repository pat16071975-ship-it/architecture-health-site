#!/usr/bin/env python3
"""Install one exact, audited PR275 file after owner authorization.

The owner authorized merge and installation of this one file on 2026-11-27.
The actual merge tree and every target blob match the audited source.
No dependency installation, OCR execution, migrations, stock or IDENT imports.
Rollback restores code only; the live database is never restored.
"""
import argparse
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

REPO = "pat16071975-ship-it/architecture-health-site"
BASE = "d5add09bc13807613d986b9f74db915ba443a871"
AUDITED = "91367b39b6ca56fe280f5ac70d06ed97f6df8aa8"
TREE = "f4d3fc3c869aa064089801c53926f21d7304e6cd"
MERGED = "349ed644996815d5bef11f499d95661434cbeb90"
RELEASE_AUTHORIZED = True
ROOT = Path("/opt/az-baze-auth")
DB = Path("/var/lib/az-baze/auth.db")
BACKUPS = Path("/var/lib/az-baze/backups")
LOCK = Path("/run/lock/az-baze-pr275.lock")
SERVICE = "az-baze-auth"
LOCAL = "http://127.0.0.1:8001"
FILES = {
    "medical_supplies_ocr.py": (
        "2b9ae9103df06a077f8476d71c368d3dbc03e83d",
        "1b473fb5634b762dc8ea602f682f4777a884d4e4"),
}
DEPENDENT_FILES = {
    "medical_supplies_ocr_image.py": "0fb65eaf55ec9f6d69e13cbd8d527c5a51abcadc",
    "medical_supplies_ocr_enrichment.py": "7304d9db500725951fe9dc75a221e66e729fc53c",
    "medical_supplies_ocr_quality.py": "c8d0b7b2df97f974d28b408be7c0dc43d1f95d22",
    "requirements.txt": "8607792ae81956fb5d22cbd01b1addb32abe9648",
}
PROTECTED = (
    "app.py", "server.py", *DEPENDENT_FILES,
    "mis_source_archive.py", "mis_archive_bundle.py", "upload_reconcile.py",
    "ident_import.py", "cash_upload.py", "completed_upload.py", "daily_upload.py",
    "paid_services_upload.py", "services_upload.py", "management_lab.py",
)


def require(condition, marker):
    if not condition:
        raise RuntimeError(marker)


def release_gate():
    require(RELEASE_AUTHORIZED is True and isinstance(MERGED, str)
            and re.fullmatch(r"[0-9a-f]{40}", MERGED) and MERGED not in (BASE, AUDITED),
            "RELEASE_NOT_AUTHORIZED")


def source_commit():
    # Unreleased preflight may inspect the audited head. It is never a
    # substitute for a real, independently verified merge commit in apply.
    if MERGED is None:
        return AUDITED
    require(isinstance(MERGED, str) and re.fullmatch(r"[0-9a-f]{40}", MERGED), "INVALID_MERGED_PIN")
    return MERGED


def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def regular(path):
    require(not path.is_symlink() and path.is_file(), "NOT_REGULAR: " + str(path))


def digest(path):
    regular(path)
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def check_blob(path, expected, marker):
    regular(path)
    require(blob(path.read_bytes()) == expected, marker + ": " + str(path))


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=45, check=False)
    require(result.returncode == 0, "COMMAND_FAILED: " + args[0])
    return result.stdout.strip()


def active():
    return subprocess.run(["systemctl", "is-active", "--quiet", SERVICE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False).returncode == 0


def stop():
    command("systemctl", "stop", SERVICE)
    require(not active(), "SERVICE_NOT_STOPPED")


def http_get(path):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, url):
            return None
    # No proxy, authentication, cookie jar, redirects or mutating requests.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(LOCAL + path, timeout=3) as response:
            return response.status, response.read(128), response.headers.get("Location", "")
    except urllib.error.HTTPError as error:
        return error.code, b"", error.headers.get("Location", "")


def smoke(target=False):
    require(active(), "SERVICE_NOT_ACTIVE")
    status, body, _ = http_get("/health")
    require(status == 200 and body.strip() == b"ok", "SERVICE_HEALTH_FAILED")
    base = "/accounting/medical-supplies/incoming/"
    for route in (base, base + "1/ocr"):
        status, _, location = http_get(route)
        require(status == 302 and location.startswith("/login"), "OCR_AUTH_GET_FAILED")
    for route in (base + "1/ocr/page", base + "1/ocr/save"):
        require(http_get(route)[0] == 405, "OCR_POST_ONLY_ROUTE_FAILED")
    if target:
        require(http_get(base + "1/ocr/automatic/apply")[0] == 405, "AUTOMATIC_APPLY_POST_ONLY_ROUTE_FAILED")
    print("SERVICE_HEALTH=PASS; MEDICAL_GET_ROUTES=PASS", flush=True)


def start(target=False):
    command("systemctl", "start", SERVICE)
    for _ in range(20):
        try:
            status, body, _ = http_get("/health")
            if active() and status == 200 and body.strip() == b"ok":
                smoke(target=target)
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError("RESTART_HEALTH_FAILED")


def ro_conn(path):
    regular(path)
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA query_only=ON")
    return conn


def check_db(path):
    with closing(ro_conn(path)) as conn:
        require(conn.execute("PRAGMA quick_check").fetchone()[0] == "ok", "DATABASE_CHECK_FAILED")
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        require({"med_supply_documents", "med_supply_document_events", "med_supply_items",
                 "med_supply_folders", "med_supply_daily_items"} <= names, "MEDICAL_SCHEMA_MISSING")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(med_supply_documents)")}
        require({"original_bytes", "sha256", "fields_json", "version"} <= columns, "DOCUMENT_COLUMNS_MISSING")


def database_bytes():
    # Main DB and WAL hold data. SHM lock metadata is deliberately excluded.
    # Compare only while the service is stopped, never after normal traffic resumes.
    result = {DB.name: digest(DB)}
    wal = Path(str(DB) + "-wal")
    if wal.exists() or wal.is_symlink():
        result[wal.name] = digest(wal)
    else:
        result[wal.name] = None
    return result


def backup_database(path):
    with closing(ro_conn(DB)) as source, closing(sqlite3.connect(path)) as target:
        source.backup(target)
        target.commit()
    os.chmod(path, 0o600)
    check_db(path)
    return digest(path)


def protected_files():
    result = {}
    for name in PROTECTED:
        path = ROOT / name
        result[name] = digest(path) if path.exists() or path.is_symlink() else None
    return result


def check_live():
    for directory in (ROOT, ROOT / "templates", DB.parent, BACKUPS):
        require(directory.is_dir() and directory.resolve() == directory, "DIRECTORY_NOT_CANONICAL")
    for name, (old, _) in FILES.items():
        check_blob(ROOT / name, old, "LIVE_BASE_MISMATCH")
    for name, expected in DEPENDENT_FILES.items():
        check_blob(ROOT / name, expected, "DEPENDENT_BASE_MISMATCH")


def validate_stage(stage):
    import jinja2
    for name, (_, target) in FILES.items():
        path = stage / name
        check_blob(path, target, "STAGED_TARGET_MISMATCH")
        if name.endswith(".py"):
            compile(path.read_bytes(), name, "exec")
        else:
            jinja2.Environment().parse(path.read_text(encoding="utf-8"))


def prepare(stage):
    for name, (_, expected) in FILES.items():
        url = f"https://raw.githubusercontent.com/{REPO}/{source_commit()}/az-baze-auth/{name}"
        request = urllib.request.Request(url, headers={"User-Agent": "AZ-BAZE-PR275-Preflight"})
        data = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    require(response.status == 200, "DOWNLOAD_STATUS_FAILED")
                    data = response.read(512 * 1024 + 1)
                break
            except (OSError, urllib.error.URLError):
                if attempt:
                    raise RuntimeError("SOURCE_DOWNLOAD_FAILED") from None
                time.sleep(1)
        require(data is not None and len(data) <= 512 * 1024 and blob(data) == expected,
                "EXACT_SOURCE_MISMATCH")
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    validate_stage(stage)
    print("EXACT_TARGET=PASS; PYTHON_JINJA=PASS; FILES=1", flush=True)


def atomic_replace(data, path, metadata, expected_blobs=None):
    regular(path)
    require(path.parent.resolve() == path.parent, "TARGET_PARENT_NOT_CANONICAL")
    fd, filename = tempfile.mkstemp(prefix=".pr275-", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(data)
            target.flush()
            os.fchown(target.fileno(), metadata["uid"], metadata["gid"])
            os.fchmod(target.fileno(), metadata["mode"])
            os.fsync(target.fileno())
        regular(path)
        if expected_blobs is not None:
            require(blob(path.read_bytes()) in expected_blobs, "REPLACE_THIRD_PARTY_CHANGE")
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def restore(backup, metadata):
    # Validate the entire rollback set before replacing a single file.
    for name, (old, target) in FILES.items():
        check_blob(backup / "files" / name, old, "BACKUP_HASH_FAILED")
        regular(ROOT / name)
        require(blob((ROOT / name).read_bytes()) in (old, target), "ROLLBACK_THIRD_PARTY_CHANGE")
    for name in reversed(tuple(FILES)):
        atomic_replace((backup / "files" / name).read_bytes(), ROOT / name, metadata[name], FILES[name])
        check_blob(ROOT / name, FILES[name][0], "ROLLBACK_VERIFY_FAILED")


def sync_backup(backup):
    for path in [*(backup / "files" / name for name in FILES),
                 backup / "auth-pre-pr275.sqlite", backup / "release-manifest.json"]:
        regular(path)
        with path.open("rb") as saved:
            os.fsync(saved.fileno())
    for path in (backup / "files", backup, BACKUPS):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def apply(stage):
    release_gate()
    validate_stage(stage)
    check_live()
    backup = BACKUPS / ("pr275-medical-reread-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    backup.mkdir(mode=0o700, exist_ok=False)
    stop_attempted = touched = False
    metadata = {}
    try:
        stop_attempted = True
        stop()
        check_live()
        check_db(DB)
        before = database_bytes()
        protected = protected_files()
        for name, (old, _) in FILES.items():
            path = ROOT / name
            info = path.stat()
            metadata[name] = {"uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode)}
            saved = backup / "files" / name
            saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(path, saved)
            check_blob(saved, old, "BACKUP_HASH_FAILED")
        db_digest = backup_database(backup / "auth-pre-pr275.sqlite")
        require(database_bytes() == before, "DATABASE_CHANGED_DURING_BACKUP")
        manifest = {"release": "PR275", "base": BASE, "audited_head": AUDITED,
                    "merged_sha": MERGED, "tree": TREE, "files": FILES, "metadata": metadata,
                    "dependent_files": DEPENDENT_FILES,
                    "target_sha256": {name: digest(stage / name) for name in FILES},
                    "database_backup_sha256": db_digest, "database_before": before,
                    "protected_files": protected, "auto_db_restore": False,
                    "created_utc": datetime.now(timezone.utc).isoformat()}
        (backup / "release-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        sync_backup(backup)
        print("BACKUP=PASS " + str(backup), flush=True)
        validate_stage(stage)
        check_live()
        touched = True
        for name, (old, target) in FILES.items():
            check_blob(ROOT / name, old, "INSTALL_BASE_MISMATCH")
            atomic_replace((stage / name).read_bytes(), ROOT / name, metadata[name], (old,))
            check_blob(ROOT / name, target, "INSTALL_TARGET_MISMATCH")
        require(database_bytes() == before, "DATABASE_CHANGED_BEFORE_START")
        require(protected_files() == protected, "PROTECTED_RUNTIME_CHANGED")
        print("DB_UNCHANGED_BEFORE_START=PASS; DATABASE_WRITES_BY_INSTALLER=NONE", flush=True)
        start(target=True)
        for name, (_, target) in FILES.items():
            check_blob(ROOT / name, target, "POST_RELEASE_HASH_MISMATCH")
        require(protected_files() == protected, "PROTECTED_RUNTIME_CHANGED")
        print("PR275_RELEASE=PASS; RUNTIME_FILES=1; DEPENDENCIES=UNCHANGED", flush=True)
        print("MERGED_SHA=" + MERGED, flush=True)
        print("REAL_DOCUMENT_ACCEPTANCE=PENDING; PR270=DEFERRED", flush=True)
        return backup
    except BaseException:
        print("PR275_RELEASE=FAIL; BACKUP=" + str(backup), flush=True)
        if stop_attempted:
            try:
                if touched:
                    stop()
                    restore(backup, metadata)
                start()
                print("CODE_ROLLBACK=PASS; DATABASE_NOT_RESTORED", flush=True)
            except BaseException:
                print("CODE_ROLLBACK=FAILED; DATABASE_NOT_RESTORED; BACKUP=" + str(backup), flush=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        release_gate()
    require(os.geteuid() == 0 and socket.gethostname().split(".")[0] == "vdska", "RUN_IN_ROOT_VDSKA")
    os.umask(0o077)
    fd = None
    try:
        if args.apply:
            fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077,
                    "UNSAFE_RELEASE_LOCK")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_live()
        smoke()
        check_db(DB)
        user = command("systemctl", "show", "-p", "User", "--value", SERVICE)
        require(bool(user) and pwd.getpwnam(user).pw_uid != 0, "SERVICE_MUST_BE_NONROOT")
        wal = Path(str(DB) + "-wal")
        needed = DB.stat().st_size + (wal.stat().st_size if wal.is_file() else 0) + 64 * 1024 * 1024
        require(shutil.disk_usage(BACKUPS).free >= needed, "BACKUP_SPACE_LOW")
        with tempfile.TemporaryDirectory(prefix="pr275-stage-") as directory:
            stage = Path(directory)
            prepare(stage)
            print("PR275_PREFLIGHT=PASS; LIVE_BASE=PASS; RUNTIME_FILES=1; EXPECTED_BASE=" + BASE,
                  flush=True)
            if args.apply:
                apply(stage)
            elif not RELEASE_AUTHORIZED or MERGED is None:
                print("NOT_RELEASED; APPLY=DISABLED; SOURCE_COMMIT=" + source_commit(), flush=True)
    finally:
        if fd is not None:
            os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        # Fixed operational markers only; never print HTTP bodies, SQL rows or credentials.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print("PR275_STOP=" + message[:250], file=sys.stderr, flush=True)
        sys.exit(1)
