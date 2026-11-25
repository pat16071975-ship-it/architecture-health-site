#!/usr/bin/env python3
"""Exact audited PR267 runtime release; include PR262 only if wholly absent.

No private bundle required, no historical selection or data reimport.
Both allowed physical baselines preserve already installed PR264 OCR.
"""
import argparse
import ast
import base64
import binascii
import hashlib
import json
import os
import pwd
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
BASE_SHA = "93a8ee21866dd6abc3c70c323f76adc75e077508"
AUDITED_HEAD = "180b448c4c4d7b3b5664aa06c3b62edd0fab26a8"
MERGED_SHA = "f76986d9a8e13dd54f44139b64d3c2c7eafe936b"
ARCHIVE_BASE = "5f7fc09e25954034946a9a334f38d331ddc16284"
ARCHIVE_HEAD = "9c3d013d4d70264b2f5b7d5562e88430b5039e3c"
ARCHIVE_MERGE = "559a297ec013997497d9ef125326bc695f3be53e"
ROOT = Path("/opt/az-baze-auth")
REPORTS = Path("/var/www/az-baze.ru/reports")
DB = Path("/var/lib/az-baze/auth.db")
BACKUPS = Path("/var/lib/az-baze/backups")
VENV = ROOT / "venv/bin/python"
SERVICE = "az-baze-auth"
LOCAL = "http://127.0.0.1:8001"
ARCHIVE_FILES = {
    "az-baze-auth/mis_source_archive.py": (None, "2de27d966207f4b1be48a345c3e85e58052c62c3"),
    "az-baze-auth/mis_archive_bundle.py": (None, "09685ca673aa153a37e1a7358a2d85e295914dc9"),
    "az-baze-auth/upload_reconcile.py": ("6945643c50da4054ad18ca44885cd4ce59c422c9", "a356ceed40d24264123bcb6cb50d8d802d45f050"),
    "az-baze-auth/ident_import.py": ("e9950b781b0f4ebdfe044860b948813938a8089d", "86b5ab1828e416e31938ae21a227b512d02d951d"),
    "az-baze-auth/cash_upload.py": ("58a3958be43db73666d84ab7010e00950af515e4", "796e4cb187cd1783adf3d66ebf8da75c46182db1"),
    "az-baze-auth/completed_upload.py": ("4fe926761082cd88544d58a1037c412d05cc2520", "403baa320347f7833241ca5b718f39697ad19806"),
    "az-baze-auth/daily_upload.py": ("5a67f8fc9bbdb4fa036edb997280d1d969326693", "24bfb292192505830b077323b40bc99e594c30b6"),
    "az-baze-auth/paid_services_upload.py": ("28da8edbdf7cae3bb0306c0de28b38dd585fb8ae", "7decd0cde73116bd8855b80e144e32a4d9ac9da6"),
    "az-baze-auth/services_upload.py": ("a1ae01e029be4585ca1a55fee6b3df9565882c9d", "0ce260c7822963f043a1d7946e66f3e597f15301"),
}
LAB_FILES = {
    "az-baze-auth/management_lab.py": ("0cee9dfa4e7fb473df22833ed99b672a52cfc096", "3b3981a252f84bc6ae36be4f4bc6eb2e9033205d"),
    "az-baze-auth/management_lab_recovery_parser.py": (None, "6faf919cd3295f46f9c900eff1513849ba792f56"),
    "az-baze-auth/management_lab_recovery.py": (None, "374aed0a21133e7666d1bde68a84e67d6beaefb8"),
    "az-baze-auth/report_storage.py": ("46b4b6203df33ec48c5bcc448e61fb82cab8ad7d", "4b8af6f4cdce1d2fc48a5a3bd74f23c573216859"),
    "reports/management-lab.js": ("f63fda25920f8ff6c6267616b73a4f8edb3d9bc1", "0ed181a90da7912cd982d85216d7c93466e2abc3"),
    "reports/period-view.js": ("b88587ac112cf7ff4dddf6779eab1ecfb3c93f04", "acee0ef9098e910930ed1683b44002d02a75d3d9"),
    "reports/index.html": ("a51b51e43e14d021bc9f58e95adee28d18fc6d46", "21f41a1399dc0187b94de538c52252dbf8dbe56e"),
}
FILES = {**ARCHIVE_FILES, **LAB_FILES}
EXEMPLARS = {
    "az-baze-auth/mis_source_archive.py":"az-baze-auth/paid_services_upload.py",
    "az-baze-auth/mis_archive_bundle.py":"az-baze-auth/paid_services_upload.py",
    "az-baze-auth/management_lab_recovery.py":"az-baze-auth/management_lab.py",
    "az-baze-auth/management_lab_recovery_parser.py":"az-baze-auth/management_lab.py",
}
PROTECTED_OCR = {
    "medical_supplies_documents.py":"0e269427d75989a455a7a347346b933df665c776",
    "medical_supplies_ocr.py":"84a1609c1d1e44d5ba89f7edc2c6fef79848d3ef",
    "templates/medical_supplies_documents.html":"c5683f0fae0fd3f1cf0070108043387dc1ee63f1",
    "templates/medical_supplies_ocr.html":"0908320c1ee8909885d1b767d8170f0b0659d75e",
}
PROTECTED_DEPENDENCIES = {
    "paid_services.py":"1bf1009439b5ca5cbd6b70fa2601a146e4e5cd86",
}
TABLES = {
    "users":{"id"},
    "permissions":{"user_id","section"},
    "audit_log":{"id"},
    "report_data":{"date","payload","updated_by","updated_at"},
    "report_blobs":{"key","payload"},
    "daily_uploads":{"data_date","normalized_json","services_sha256"},
    "cash_receipts_daily":{"data_date","payload_json"},
    "service_payment_snapshots":{"as_of_date","month","payload_json","source_sha256","imported_at"},
    "service_payment_periods":{"period_start","period_end","payload_json","source_sha256","imported_at"},
    "service_payment_versions":{"id","as_of_date","payload_json"},
    "service_payment_period_versions":{"id","period_start","period_end","payload_json"},
    "med_supply_documents":{"id","original_bytes","fields_json"},
    "med_supply_document_events":{"id","document_id"},
}
ARCHIVE_OBJECTS=frozenset(("mis_source_objects","mis_source_events",
    "ix_mis_source_events_kind_range","ix_mis_source_events_sha"))
RECOVERY_TABLE="management_lab_recovery_runs"
COMMIT_ROUTES=(
    "/api/uploads/clinical/commit",
    "/api/uploads/services/commit",
    "/api/uploads/completed/commit",
    "/api/uploads/cash/commit",
    "/api/uploads/paid-services/commit",
    "/api/reports/import-ident",
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

def check_expected(path, expected, label):
    if expected is None:
        if path.exists() or path.is_symlink():
            fail(f"{label}_NEW_FILE_ALREADY_EXISTS: {path}")
    else:
        check_blob(path, expected, label)

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
        "User-Agent": "AZ-BAZE-PR267-Immutable-Release",
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

def destination(name):
    if name not in FILES:
        fail("FILE_NOT_ALLOWLISTED")
    prefix,filename=name.split("/",1)
    if prefix not in ("az-baze-auth","reports") or Path(filename).name!=filename:
        fail("UNSAFE_RUNTIME_PATH")
    return (ROOT if prefix=="az-baze-auth" else REPORTS)/filename


def check_protected():
    for name,sha in PROTECTED_OCR.items():
        check_blob(ROOT/name,sha,"PR264_PROTECTED")
    for name,sha in PROTECTED_DEPENDENCIES.items():
        check_blob(ROOT/name,sha,"DEPENDENCY_PROTECTED")


def current_blob(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        fail("RUNTIME_TARGET_NOT_REGULAR: "+str(path))
    return git_blob(path.read_bytes()) if path.exists() else None


def check_live_files(expected_mode=None):
    for directory in (ROOT,ROOT/"templates",REPORTS):
        if directory.resolve()!=directory or not directory.is_dir():
            fail("RUNTIME_DIRECTORY_MISSING_OR_SYMLINK")
    for name,(old,_new) in LAB_FILES.items():
        check_expected(destination(name),old,"LIVE_BASE")
    observed={name:current_blob(destination(name)) for name in ARCHIVE_FILES}
    if all(observed[name]==old for name,(old,_new) in ARCHIVE_FILES.items()):
        mode="INSTALL_PR262"
    elif all(observed[name]==new for name,(_old,new) in ARCHIVE_FILES.items()):
        mode="KEEP_PR262"
    else:
        fail("PR262_MIXED_OR_UNKNOWN_RUNTIME")
    if expected_mode is not None and mode!=expected_mode:
        fail("PR262_MODE_CHANGED_DURING_RELEASE")
    check_protected()
    print("LIVE_BASE=PASS; ARCHIVE_MODE="+mode+"; PR264=PROTECTED",flush=True)
    return mode


def plan(mode):
    if mode=="INSTALL_PR262":
        return FILES
    if mode=="KEEP_PR262":
        return LAB_FILES
    fail("UNKNOWN_RELEASE_MODE")


def smoke_routes(new_version=False):
    routes=("/", "/uploads/", "/reports/management/", "/api/reports/data",
            "/api/reports/data?view=dashboard", "/reports/management-lab.js",
            "/reports/period-view.js", "/administrators/",
            "/accounting/medical-supplies/incoming/1/ocr")
    for route in routes:
        status,_=http_status(route)
        if status not in (302,401,403):
            fail("ROUTE_UNPROTECTED_OR_MISSING: "+route+" status="+str(status))
    if new_version:
        for route in COMMIT_ROUTES:
            status,_=http_status(route,"POST")
            if status not in (302,401,403):
                fail("IMPORT_ROUTE_UNPROTECTED_OR_MISSING: "+route+" status="+str(status))
        for route in ("/reports/management-lab.js?v=20261125-recovery-1",
                      "/reports/period-view.js?v=20261125-recovery-1"):
            status,_=http_status(route)
            if status not in (302,401,403):
                fail("VERSIONED_ASSET_UNPROTECTED_OR_MISSING")
    print("MANAGEMENT_MIS_OCR_ROUTES=PASS; ANONYMOUS_PROTECTED",flush=True)


def check_archive_environment():
    username=run("systemctl","show","-p","User","--value",SERVICE).stdout.strip()
    if not username or username=="root":
        fail("ARCHIVE_REQUIRES_NONROOT_SERVICE")
    account=pwd.getpwnam(username)
    if DB.resolve()!=DB or not DB.is_file() or DB.stat().st_uid!=account.pw_uid:
        fail("DATABASE_OWNER_MISMATCH")
    for path in (DB,Path(str(DB)+"-wal"),Path(str(DB)+"-shm"),Path(str(DB)+"-journal")):
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_mode&0o007)):
            fail("DATABASE_OR_SIDECAR_NOT_PRIVATE")
    run("runuser","-u",username,"--","test","-w",str(DB))
    run("runuser","-u",username,"--","test","-w",str(DB.parent))
    print("ARCHIVE_ENVIRONMENT=PASS; PRIVATE_DATABASE=YES; NONROOT=YES",flush=True)

def validate_stage(stage,mode):
    for name,(_old,new) in FILES.items():
        target=stage/name
        check_blob(target,new,"STAGED")
        if name.endswith(".py"):
            compile(target.read_bytes(),str(target),"exec")
    for name,key in (("az-baze-auth/upload_reconcile.py","SCHEMA"),
                     ("az-baze-auth/report_storage.py","REPORT_SCHEMA")):
        if extract_schema(destination(name).read_text(encoding="utf-8"),key) != \
           extract_schema((stage/name).read_text(encoding="utf-8"),key):
            fail("EXISTING_SCHEMA_CHANGED: "+name)
    html=(stage/"reports/index.html").read_text(encoding="utf-8")
    tags=('<script src="./management-lab.js?v=20261125-recovery-1"></script>',
          '<script src="./period-view.js?v=20261125-recovery-1"></script>')
    if any(html.count(t)!=1 for t in tags) or html.index(tags[0])>=html.index(tags[1]):
        fail("MANAGEMENT_ASSET_CONTRACT_BAD")
    node=shutil.which("node")
    if node:
        for name in ("reports/management-lab.js","reports/period-view.js"):
            run(node,"--check",str(stage/name))
    print("IMMUTABLE_TARGET=PASS; INSTALL_FILES="+str(len(plan(mode)))+"; SOURCE="+MERGED_SHA,flush=True)
    print("EXISTING_SCHEMA_SOURCE=UNCHANGED; PYTHON_SYNTAX=PASS; JS_CI=PASS",flush=True)


def expected_archive_schema(stage):
    """Evaluate only the literal audited schema in isolated in-memory SQLite."""
    path=stage/"az-baze-auth/mis_source_archive.py"
    check_blob(path,FILES["az-baze-auth/mis_source_archive.py"][1],"ARCHIVE_SCHEMA_SOURCE")
    tree=ast.parse(path.read_text(encoding="utf-8"))
    nodes=[n for n in tree.body if isinstance(n,ast.Assign) and
           any(isinstance(t,ast.Name) and t.id=="SCHEMA" for t in n.targets)]
    if len(nodes)!=1:
        fail("ARCHIVE_SCHEMA_NOT_UNIQUE")
    statements=ast.literal_eval(nodes[0].value)
    if not isinstance(statements,tuple) or len(statements)!=4 or not all(isinstance(s,str) for s in statements):
        fail("ARCHIVE_SCHEMA_BAD")
    conn=sqlite3.connect(":memory:")
    try:
        for statement in statements:
            conn.execute(statement)
        schema=schema_map(conn)
        if set(schema)!=ARCHIVE_OBJECTS:
            fail("ARCHIVE_SCHEMA_DELTA_BAD")
        return schema
    finally:
        conn.close()

def check_baseline_schema(baseline,archive,mode):
    if RECOVERY_TABLE in baseline:
        fail("HISTORICAL_SELECTION_ALREADY_PRESENT_STOP")
    present=ARCHIVE_OBJECTS.intersection(baseline)
    if present:
        if mode!="KEEP_PR262" or present!=ARCHIVE_OBJECTS or any(
            baseline.get(k)!=v for k,v in archive.items()
        ):
            fail("UNEXPECTED_ARCHIVE_SCHEMA")


def check_post_schema(baseline,current,archive):
    # A legitimate upload may occur after service start. Its exact archive
    # schema is permitted; no other schema drift is.
    if current!=baseline and current!={**baseline,**archive}:
        fail("POST_RELEASE_UNEXPECTED_SCHEMA")

def prepare(stage,mode):
    for name,(_old,new) in FILES.items():
        target=stage/name
        target.parent.mkdir(parents=True,exist_ok=True)
        if name in ARCHIVE_FILES and mode=="KEEP_PR262":
            # The already installed exact dependency is not reinstalled.
            data=destination(name).read_bytes()
        else:
            data=fetch_exact_blob(new)
        target.write_bytes(data)
        check_blob(target,new,"STAGED")
    validate_stage(stage,mode)


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

def database_state(path):
    """Read-only complete row fingerprints for every existing SQLite table.
    No actual field values, document contents or patient data are logged.
    """
    conn=ro_conn(path)
    try:
        conn.execute("BEGIN")
        names=sorted(name for name,info in schema_map(conn).items()
                     if info[0]=="table")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
            names.append("sqlite_sequence")
        result={}
        for name in names:
            table='"'+name.replace('"','""')+'"'
            digests=[]
            for row in conn.execute("SELECT * FROM "+table):
                digest=hashlib.sha256()
                for field in row:
                    if isinstance(field,bytes):
                        data=field
                        kind=b"B"
                    else:
                        data=repr(field).encode("utf-8")
                        kind=b"V"
                    digest.update(kind)
                    digest.update(str(len(data)).encode("ascii")+b":")
                    digest.update(data)
                digests.append(digest.digest())
            h=hashlib.sha256()
            for digest in sorted(digests):
                h.update(digest)
            result[name]=(len(digests),h.hexdigest())
        return result
    finally:
        conn.close()

def backup_files(backup,mode):
    files=plan(mode)
    for name,(old,_new) in files.items():
        if old is None:
            continue
        target=backup/"files"/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(destination(name),target)
        check_blob(target,old,"BACKUP")
    print("RUNTIME_BACKUP=PASS; OLD_FILES="+str(sum(old is not None for old,new in files.values())),flush=True)


def atomic_file_replace(data, path, exemplar=None):
    # For a new file use a sibling's existing permissions, and create it
    # without replacing anything that appeared since the initial check.
    metadata_source = exemplar or path
    if metadata_source.is_symlink() or not metadata_source.is_file():
        fail("RUNTIME_METADATA_NOT_REGULAR")
    metadata = metadata_source.stat()
    if exemplar is not None:
        check_expected(path, None, "NEW_TARGET")
    fd, temporary = tempfile.mkstemp(prefix=".pr267-", dir=path.parent)
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

def install(stage,mode):
    files=plan(mode)
    check_live_files(mode)
    for name,(old,new) in files.items():
        check_expected(destination(name),old,"INSTALL_BASE")
        check_blob(stage/name,new,"INSTALL_STAGE")
    for name,(old,new) in files.items():
        path=destination(name)
        check_expected(path,old,"INSTALL_BASE")
        exemplar=destination(EXEMPLARS[name]) if old is None else None
        atomic_file_replace((stage/name).read_bytes(),path,exemplar)
        check_blob(path,new,"INSTALLED")
    check_target()
    print("RUNTIME_INSTALL=PASS; FILES="+str(len(files)),flush=True)


def check_target():
    for name,(_old,new) in FILES.items():
        check_blob(destination(name),new,"POST_RELEASE")
    check_protected()


def restore(backup,mode):
    files=plan(mode)
    # Validate the whole rollback set before changing anything. Never destroy
    # a third-party edit or remove a new path with an unexpected hash.
    for name, (old, new) in files.items():
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
    for name, (old, _new) in reversed(tuple(files.items())):
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

def apply(stage,mode):
    backup=BACKUPS/("pr267-lab-recovery-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    backup.mkdir(parents=True,mode=0o700,exist_ok=False)
    stop_attempted=False
    touched=False
    try:
        stop_attempted=True
        run("systemctl","stop",SERVICE)
        if active():
            fail("SERVICE_NOT_STOPPED")
        check_live_files(mode)
        baseline=check_db()
        archive_schema=expected_archive_schema(stage)
        check_baseline_schema(baseline,archive_schema,mode)
        protected=database_state(DB)
        backup_files(backup,mode)
        db_archive=backup/"auth-pre-pr267.sqlite"
        digest=backup_sqlite(db_archive)
        if check_db(db_archive)!=baseline or database_state(db_archive)!=protected:
            fail("DATABASE_BACKUP_CONTENT_MISMATCH")
        manifest={
            "release":"PR267-lab-recovery-runtime","base":BASE_SHA,
            "audited_head":AUDITED_HEAD,"merged_sha":MERGED_SHA,
            "archive_mode":mode,"archive_merge":ARCHIVE_MERGE,
            "files":plan(mode),"backup_sha256":digest,"db_delta":[],
            "history_archive_applied":False,"ztl_recovery_selection_applied":False,
            "pr264_protected":True,"created_utc":datetime.now(timezone.utc).isoformat(),
            "auto_db_restore":False,
        }
        (backup/"release-manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
        touched=True
        install(stage,mode)
        if check_db()!=baseline or database_state(DB)!=protected:
            fail("DATABASE_OR_BUSINESS_ROWS_CHANGED")
        print("DATABASE_MIGRATION=NONE; DATABASE_WRITES_BY_INSTALLER=NONE; BUSINESS_ROWS_UNCHANGED=PASS",flush=True)
        restart_and_smoke()
        check_target()
        check_post_schema(baseline,check_db(),archive_schema)
        print("PR264_RUNTIME_UNCHANGED=PASS; PR262_RUNTIME=EXACT_PASS",flush=True)
        print(f"PR267_RELEASE=PASS; MERGED_SHA={MERGED_SHA}; ARCHIVE_MODE={mode}; BACKUP={backup}",flush=True)
        print("ARCHIVE_ON_ACCEPTED_UPLOADS=ENABLED; HISTORICAL_ARCHIVE=PENDING; ZTL_HISTORICAL_SELECTION=PENDING; BUSINESS_REIMPORT=NONE",flush=True)
        print("AUTHENTICATED_REPORT_ACCEPTANCE=PENDING",flush=True)
    except BaseException:
        print("PR267_RELEASE=FAIL; BEGIN_CODE_ROLLBACK",file=sys.stderr,flush=True)
        try:
            if stop_attempted:
                if touched:
                    run("systemctl","stop",SERVICE)
                    if active():
                        fail("ROLLBACK_SERVICE_NOT_STOPPED")
                    restore(backup,mode)
                    check_live_files(mode)
                restart_and_smoke(rolled_back=True)
        except BaseException as exc:
            print("CODE_ROLLBACK_FAILED="+type(exc).__name__+": "+str(exc)[:200],file=sys.stderr,flush=True)
        print("BACKUP_PATH="+str(backup)+"; DB_RESTORE_NOT_AUTOMATIC",file=sys.stderr,flush=True)
        raise


def main():
    parser=argparse.ArgumentParser()
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight",action="store_true")
    mode.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    os.umask(0o077)
    if os.geteuid()!=0 or socket.gethostname().split(".")[0]!="vdska":
        fail("RUN_IN_ROOT_VDSKA")
    if not VENV.is_file() or not Path("/etc/az-baze/auth.env").is_file():
        fail("AZ_BAZE_RUNTIME_OR_SERVICE_ENV_MISSING")
    health()
    smoke_routes()
    chosen=check_live_files()
    baseline=check_db()
    check_archive_environment()
    run(str(VENV),"-B","-c","import openpyxl,sqlite3; c=sqlite3.connect(':memory:'); assert c.execute(\"SELECT json_extract('{\\\"x\\\":1}','$.x')\").fetchone()==(1,)")
    if not BACKUPS.is_dir() or BACKUPS.is_symlink() or BACKUPS.resolve()!=BACKUPS or shutil.disk_usage(BACKUPS).free < DB.stat().st_size+64*1024*1024:
        fail("BACKUP_DIRECTORY_OR_SPACE_UNAVAILABLE")
    with tempfile.TemporaryDirectory(prefix="pr267-stage-") as directory:
        stage=Path(directory)
        print("RELEASE_SOURCE="+MERGED_SHA+"; AUDITED_HEAD="+AUDITED_HEAD,flush=True)
        prepare(stage,chosen)
        check_baseline_schema(baseline,expected_archive_schema(stage),chosen)
        print("PR267_PREFLIGHT=PASS; PRODUCTION_NOT_MODIFIED; HISTORICAL_BUNDLE_NOT_REQUIRED",flush=True)
        if args.apply:
            apply(stage,chosen)


if __name__=="__main__":
    try:
        main()
    except BaseException as exc:
        print("PR267_CONTROLLED_STOP="+type(exc).__name__+": "+str(exc)[:500],file=sys.stderr,flush=True)
        sys.exit(1)
