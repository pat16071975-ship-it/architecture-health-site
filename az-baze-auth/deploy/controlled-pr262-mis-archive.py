#!/usr/bin/env python3
"""Controlled PR262 release; archive-only historical data, no business reimport."""
import argparse
import ast
import base64
import binascii
import hashlib
import importlib
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
BASE_SHA = "5f7fc09e25954034946a9a334f38d331ddc16284"
AUDITED_HEAD = "9c3d013d4d70264b2f5b7d5562e88430b5039e3c"
MERGED_SHA = "559a297ec013997497d9ef125326bc695f3be53e"
ROOT = Path("/opt/az-baze-auth")
DB = Path("/var/lib/az-baze/auth.db")
BACKUPS = Path("/var/lib/az-baze/backups")
VENV = ROOT / "venv/bin/python"
SERVICE = "az-baze-auth"
LOCAL = "http://127.0.0.1:8001"
BUNDLE_SHA = "82b8bea75da87af488f064da6719cea4a1137db2545ad2baa6f95f2c4d29cbdc"
BUNDLE_SIZE = 5351450
BUNDLE_INFO = {"originals":60,"occurrences":68,"raw_bytes":5324783,
               "bundle_sha256":BUNDLE_SHA}
PRIVATE_BUNDLE = Path("/root/AZ_IDENT_2026-01_10_verified_sources.zip")
FILES = {
    "mis_source_archive.py": (None,"2de27d966207f4b1be48a345c3e85e58052c62c3"),
    "mis_archive_bundle.py": (None,"09685ca673aa153a37e1a7358a2d85e295914dc9"),
    "upload_reconcile.py": ("6945643c50da4054ad18ca44885cd4ce59c422c9","a356ceed40d24264123bcb6cb50d8d802d45f050"),
    "ident_import.py": ("e9950b781b0f4ebdfe044860b948813938a8089d","86b5ab1828e416e31938ae21a227b512d02d951d"),
    "cash_upload.py": ("58a3958be43db73666d84ab7010e00950af515e4","796e4cb187cd1783adf3d66ebf8da75c46182db1"),
    "completed_upload.py": ("4fe926761082cd88544d58a1037c412d05cc2520","403baa320347f7833241ca5b718f39697ad19806"),
    "daily_upload.py": ("5a67f8fc9bbdb4fa036edb997280d1d969326693","24bfb292192505830b077323b40bc99e594c30b6"),
    "paid_services_upload.py": ("28da8edbdf7cae3bb0306c0de28b38dd585fb8ae","7decd0cde73116bd8855b80e144e32a4d9ac9da6"),
    "services_upload.py": ("a1ae01e029be4585ca1a55fee6b3df9565882c9d","0ce260c7822963f043a1d7946e66f3e597f15301"),
}
EXEMPLARS = {"mis_source_archive.py":"paid_services_upload.py",
             "mis_archive_bundle.py":"paid_services_upload.py"}
TABLES = {
    "users": {"id"},
    "permissions": {"user_id","section"},
    "report_data": {"date","payload"},
    "report_blobs": {"key","payload"},
    "daily_uploads": {"data_date","normalized_json"},
    "cash_receipts_daily": {"data_date","payload_json"},
    "service_payment_snapshots": {"as_of_date","payload_json"},
    "service_payment_periods": {"period_start","period_end","payload_json"},
}
ARCHIVE_TABLES = frozenset(("mis_source_objects","mis_source_events"))
ARCHIVE_OBJECTS = ARCHIVE_TABLES | {"ix_mis_source_events_kind_range","ix_mis_source_events_sha"}


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
        "User-Agent": "AZ-BAZE-PR262-Immutable-Release",
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
    if name not in FILES or Path(name).name != name:
        fail("FILE_NOT_ALLOWLISTED")
    return ROOT / name


def check_live_files():
    if ROOT.resolve() != ROOT or not ROOT.is_dir():
        fail("RUNTIME_DIRECTORY_MISSING_OR_SYMLINK")
    for name,(old,_new) in FILES.items():
        check_expected(destination(name),old,"LIVE_BASE")
    print("LIVE_BASE=PASS; EXISTING=7; NEW_ABSENT=2; SOURCE="+BASE_SHA,flush=True)


def smoke_routes(new_version=False):
    for route in ("/","/uploads/","/reports/management/","/api/reports/data",
                  "/reports/import-ident/","/administrators/"):
        status,_ = http_status(route)
        if status not in (302,401,403):
            fail("ROUTE_UNPROTECTED_OR_MISSING: "+route+" status="+str(status))
    print("MIS_ROUTES=PASS; ANONYMOUS_PROTECTED",flush=True)


def validate_stage(stage):
    for name,(_old,new) in FILES.items():
        check_blob(stage/name,new,"STAGED")
        compile((stage/name).read_bytes(),str(stage/name),"exec")
    name="upload_reconcile.py"
    if extract_schema(destination(name).read_text(encoding="utf-8"),"SCHEMA") != \
       extract_schema((stage/name).read_text(encoding="utf-8"),"SCHEMA"):
        fail("EXISTING_SCHEMA_CHANGED")
    print("IMMUTABLE_TARGET=PASS; FILES=9; SOURCE="+MERGED_SHA,flush=True)


def prepare(stage):
    for name,(_old,new) in FILES.items():
        (stage/name).write_bytes(fetch_exact_blob(new))
    validate_stage(stage)


def file_digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def verify_private_bundle(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != BUNDLE_SIZE:
        fail("PRIVATE_BUNDLE_MISSING_OR_SIZE_MISMATCH")
    if file_digest(path) != BUNDLE_SHA:
        fail("PRIVATE_BUNDLE_SHA256_MISMATCH")


def load_archive_modules(stage):
    for name in ("mis_source_archive.py","mis_archive_bundle.py"):
        check_blob(stage/name,FILES[name][1],"ARCHIVE_MODULE")
    # Import the exact staged, audited modules, without app/server startup.
    sys.path.insert(0,str(stage))
    try:
        for name in ("mis_source_archive","mis_archive_bundle"):
            if name in sys.modules:
                if Path(sys.modules[name].__file__).resolve() != (stage/(name+".py")).resolve():
                    fail("ARCHIVE_MODULE_ALREADY_LOADED_FROM_OTHER_PATH")
        archive=importlib.import_module("mis_source_archive")
        bundle=importlib.import_module("mis_archive_bundle")
        return archive,bundle
    finally:
        sys.path.pop(0)


def verified_pack(stage,path):
    verify_private_bundle(path)
    archive,bundle=load_archive_modules(stage)
    pack=bundle.read_bundle(path,BUNDLE_SHA)
    if pack[2] != BUNDLE_INFO:
        fail("HISTORICAL_CATALOG_MISMATCH")
    print("MIS_ARCHIVE_BUNDLE=PASS; ORIGINALS=60; ALIASES=68; RAW_BYTES=5324783",flush=True)
    return archive,bundle,pack


def quoted(name):
    return '"' + name.replace('"','""') + '"'


def business_state(conn):
    schema=schema_map(conn)
    # The schema and complete multiset of rows in every prior table,
    # including sqlite_sequence, are hashed without exposing values.
    old_schema={k:v for k,v in schema.items() if k not in ARCHIVE_OBJECTS}
    names=sorted(k for k,v in old_schema.items() if v[0]=="table")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
        names.append("sqlite_sequence")
    rows={}
    for name in names:
        hashes=[]
        for row in conn.execute("SELECT * FROM "+quoted(name)):
            hashes.append(hashlib.sha256(repr(tuple(row)).encode("utf-8")).digest())
        h=hashlib.sha256()
        for digest in sorted(hashes):
            h.update(digest)
        rows[name]=(len(hashes),h.hexdigest())
    return old_schema,rows


def read_business(path):
    conn=ro_conn(path)
    try:
        conn.execute("BEGIN")
        return business_state(conn)
    finally:
        conn.close()


def archive_schema(archive):
    conn=sqlite3.connect(":memory:")
    try:
        conn.execute("BEGIN")
        archive.init_schema(conn)
        result=schema_map(conn)
        if set(result)!=ARCHIVE_OBJECTS:
            fail("UNEXPECTED_ARCHIVE_SCHEMA")
        return result
    finally:
        conn.close()


def check_archive_rows(conn,archive,pack):
    manifest,sources,info=pack
    catalog_sha=hashlib.sha256(manifest).hexdigest()
    originals={sha:raw for _kind,raw,sha,*_rest in sources}
    expected_objects={**originals,catalog_sha:manifest}
    rows=conn.execute("SELECT sha256,size_bytes FROM mis_source_objects").fetchall()
    if dict(rows)!={sha:len(raw) for sha,raw in expected_objects.items()}:
        fail("ARCHIVE_OBJECT_SET_MISMATCH")
    for sha,raw in expected_objects.items():
        if archive.read_original(conn,sha)!=raw:
            fail("ARCHIVE_READBACK_MISMATCH")
    events=conn.execute("""SELECT sha256,source_kind,outcome,origin,decision,
        applied_dates_json,removed_dates_json,context_json
        FROM mis_source_events""").fetchall()
    if len(events)!=info["occurrences"]+1:
        fail("ARCHIVE_RECEIPT_COUNT_MISMATCH")
    expected={}
    for kind,_raw,sha,_start,_end,aliases,_selection in sources:
        for alias in aliases:
            key=(sha,kind,alias["archive"],archive._filename(alias["filename"]))
            expected[key]=expected.get(key,0)+1
    actual={}
    catalog_events=0
    detailed=conn.execute("""SELECT sha256,source_kind,original_filename,
        outcome,origin,decision,applied_dates_json,removed_dates_json,context_json,
        source_from,source_to FROM mis_source_events""")
    bounds={sha:(start,end) for _kind,_raw,sha,start,end,*_rest in sources}
    for sha,kind,name,outcome,origin,decision,applied,removed,context,start,end in detailed:
        if (outcome,origin,decision,applied,removed)!=("archive_only","verified_historical_pack","archive_only",None,None):
            fail("ARCHIVE_RECEIPT_NOT_ARCHIVE_ONLY")
        meta=json.loads(context)
        if meta.get("bundle_sha256")!=info["bundle_sha256"]:
            fail("ARCHIVE_RECEIPT_BUNDLE_MISMATCH")
        if kind=="catalog":
            if sha!=catalog_sha or name!="manifest.json" or start is not None or end is not None:
                fail("ARCHIVE_CATALOG_RECEIPT_MISMATCH")
            catalog_events+=1
        else:
            if bounds.get(sha)!=(start,end):
                fail("ARCHIVE_RECEIPT_BOUNDS_MISMATCH")
            key=(sha,kind,meta.get("original_archive"),name)
            actual[key]=actual.get(key,0)+1
    if catalog_events!=1 or actual!=expected:
        fail("ARCHIVE_RECEIPT_SET_MISMATCH")


def archive_transaction(path,archive,bundle,pack):
    """Guard the audited archive-only commit itself, not just its aftermath."""
    path=Path(path)
    class CheckedConnection(sqlite3.Connection):
        def commit(self):
            if not self.in_transaction:
                fail("ARCHIVE_COMMIT_WITHOUT_TRANSACTION")
            if schema_map(self)!=expected_schema:
                fail("ARCHIVE_SCHEMA_DELTA_MISMATCH")
            if business_state(self)!=baseline:
                fail("EXISTING_BUSINESS_DATA_CHANGED")
            check_archive_rows(self,archive,pack)
            db_health(self)
            super().commit()
    conn=sqlite3.connect(path.resolve().as_uri()+"?mode=rw",uri=True,timeout=30,
                         factory=CheckedConnection)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        db_health(conn)
        original=schema_map(conn)
        if ARCHIVE_OBJECTS & original.keys():
            fail("ARCHIVE_ALREADY_PRESENT_STOP")
        baseline=business_state(conn)
        expected_schema={**original,**archive_schema(archive)}
        bundle.archive_bundle(conn,pack)
        if conn.in_transaction or schema_map(conn)!=expected_schema or business_state(conn)!=baseline:
            fail("ARCHIVE_POST_COMMIT_MISMATCH")
        check_archive_rows(conn,archive,pack)
        print("ARCHIVE_TRANSACTION=PASS; BUSINESS_ROWS_UNCHANGED=PASS; ARCHIVE_ONLY=YES",flush=True)
        print("ARCHIVE_READBACK=PASS; ORIGINALS="+str(pack[2]["originals"])+"; RECEIPTS="+str(pack[2]["occurrences"]+1),flush=True)
    finally:
        conn.close()


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

def atomic_file_replace(data, path, exemplar=None):
    # For a new file use a sibling's existing permissions, and create it
    # without replacing anything that appeared since the initial check.
    metadata_source = exemplar or path
    if metadata_source.is_symlink() or not metadata_source.is_file():
        fail("RUNTIME_METADATA_NOT_REGULAR")
    metadata = metadata_source.stat()
    if exemplar is not None:
        check_expected(path, None, "NEW_TARGET")
    fd, temporary = tempfile.mkstemp(prefix=".pr262-", dir=path.parent)
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

def backup_files(backup):
    for name,(old,_new) in FILES.items():
        if old is None:
            continue
        path=backup/"files"/name
        path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(destination(name),path)
        check_blob(path,old,"BACKUP")
    print("RUNTIME_BACKUP=PASS; FILES=7; NEW_FILES=2",flush=True)


def install(stage):
    for name,(old,new) in FILES.items():
        check_expected(destination(name),old,"INSTALL_BASE")
        check_blob(stage/name,new,"INSTALL_STAGE")
    for name,(old,new) in FILES.items():
        path=destination(name)
        check_expected(path,old,"INSTALL_BASE")
        exemplar=destination(EXEMPLARS[name]) if old is None else None
        atomic_file_replace((stage/name).read_bytes(),path,exemplar)
        check_blob(path,new,"INSTALLED")
    print("RUNTIME_INSTALL=PASS; FILES=9",flush=True)


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

def service_account():
    username=run("systemctl","show","-p","User","--value",SERVICE).stdout.strip()
    if not username or username=="root":
        fail("UNEXPECTED_SERVICE_ACCOUNT")
    account=pwd.getpwnam(username)
    if account.pw_uid==0 or DB.stat().st_uid!=account.pw_uid:
        fail("DATABASE_SERVICE_OWNER_MISMATCH")
    if DB.resolve()!=DB or DB.stat().st_mode & 0o007:
        fail("DATABASE_NOT_PRIVATE")
    run("runuser","-u",username,"--","test","-w",str(DB))
    run("runuser","-u",username,"--","test","-w",str(DB.parent))
    print("DATABASE_ACCESS=PASS; SERVICE_ACCOUNT_NONROOT; PRIVATE",flush=True)
    return account


def prepare_worker(stage,account,bundle_path):
    # Root owns every script; the service account can read, not modify them.
    worker=stage/"release_worker.py"
    worker.write_bytes(Path(__file__).read_bytes())
    bundle=stage/"historical.zip"
    shutil.copyfile(bundle_path,bundle)
    verify_private_bundle(bundle)
    for path in (stage,*[stage/name for name in FILES],worker,bundle):
        os.chown(path,0,account.pw_gid)
        os.chmod(path,0o750 if path==stage else 0o640)
    # Only the service UID itself reads the original bundle.
    os.chown(bundle,account.pw_uid,account.pw_gid)
    os.chmod(bundle,0o600)
    return worker,bundle


def run_archive_worker(stage,worker,account):
    result=subprocess.run(
        ["runuser","-u",account.pw_name,"--",str(VENV),"-I","-B",str(worker),"--archive-worker"],
        text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,
    )
    # No raw rows, exception strings or private paths emitted from the worker.
    for line in result.stdout.splitlines():
        if line.startswith(("MIS_ARCHIVE_BUNDLE=PASS;","ARCHIVE_TRANSACTION=PASS;",
                            "ARCHIVE_READBACK=PASS;","ARCHIVE_WORKER_STOP=")):
            print(line[:300],flush=True)
    if result.returncode or "ARCHIVE_TRANSACTION=PASS;" not in result.stdout:
        fail("ARCHIVE_WORKER_FAILED")


def worker_main():
    stage=Path(__file__).resolve().parent
    if os.geteuid()==0 or DB.stat().st_uid!=os.geteuid() or DB.resolve()!=DB:
        fail("ARCHIVE_WORKER_ACCOUNT_MISMATCH")
    if socket.gethostname().split(".")[0]!="vdska":
        fail("RUN_ON_VDSKA")
    os.umask(0o077)
    archive,bundle,pack=verified_pack(stage,stage/"historical.zip")
    archive_transaction(DB,archive,bundle,pack)


def apply(stage,worker,account,archive,pack):
    backup=BACKUPS/("pr262-mis-archive-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    backup.mkdir(parents=True,mode=0o700,exist_ok=False)
    stopped=False
    touched=False
    archive_committed=False
    try:
        stopped=True
        run("systemctl","stop",SERVICE)
        if active():
            fail("SERVICE_NOT_STOPPED")
        check_live_files()
        baseline=check_db()
        if ARCHIVE_OBJECTS & baseline.keys():
            fail("ARCHIVE_ALREADY_PRESENT_STOP")
        expected_schema={**baseline,**archive_schema(archive)}
        old_business=read_business(DB)
        backup_files(backup)
        dbcopy=backup/"auth-pre-pr262.sqlite"
        digest=backup_sqlite(dbcopy)
        if check_db(dbcopy)!=baseline or read_business(dbcopy)!=old_business:
            fail("DATABASE_BACKUP_CONTENT_MISMATCH")
        manifest={
            "release":"PR262-mis-archive","base":BASE_SHA,"audited_head":AUDITED_HEAD,
            "merged_sha":MERGED_SHA,"files":FILES,"db_backup_sha256":digest,
            "archive_sha256":BUNDLE_SHA,"archive_schema_delta":sorted(ARCHIVE_OBJECTS),
            "business_reimport":False,"auto_db_restore":False,
        }
        (backup/"release-manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
        touched=True
        install(stage)
        if check_db()!=baseline or read_business(DB)!=old_business:
            fail("PRE_ARCHIVE_BUSINESS_DRIFT")
        run_archive_worker(stage,worker,account)
        archive_committed=True
        if check_db()!=expected_schema or read_business(DB)!=old_business:
            fail("POST_ARCHIVE_BUSINESS_DRIFT")
        conn=ro_conn(DB)
        try:
            check_archive_rows(conn,archive,pack)
        finally:
            conn.close()
        print("DATABASE_SCHEMA_DELTA=PASS; TABLES=2; INDEXES=2; BUSINESS_ROWS_UNCHANGED=PASS",flush=True)
        restart_and_smoke()
        for name,(_old,new) in FILES.items():
            check_blob(destination(name),new,"POST_RELEASE")
        if check_db()!=expected_schema:
            fail("POST_RELEASE_SCHEMA_CHANGED")
        print("PR262_RELEASE=PASS; MERGED_SHA="+MERGED_SHA+"; BACKUP="+str(backup),flush=True)
        print("MIS_HISTORY_ARCHIVE=PASS; ORIGINALS=60; OBJECTS=61; RECEIPTS=69; HISTORICAL_REIMPORT=NONE",flush=True)
    except BaseException:
        print("PR262_RELEASE=FAIL; BEGIN_CODE_ROLLBACK",file=sys.stderr,flush=True)
        try:
            if stopped:
                if touched:
                    run("systemctl","stop",SERVICE)
                    if active():
                        fail("ROLLBACK_SERVICE_NOT_STOPPED")
                    restore(backup)
                restart_and_smoke(rolled_back=True)
        except BaseException as exc:
            print("CODE_ROLLBACK_FAILED="+type(exc).__name__,file=sys.stderr,flush=True)
        # Never undo a committed archive or restore the live database automatically.
        # A worker can commit successfully and then fail before acknowledgement.
        # Query actual schema rather than claiming a failed worker wrote nothing.
        archive_status = "CONFIRMED" if archive_committed else "UNKNOWN"
        try:
            conn = ro_conn(DB)
            try:
                present = ARCHIVE_OBJECTS & schema_map(conn).keys()
                if not present:
                    archive_status = "NONE"
                elif present == ARCHIVE_OBJECTS:
                    check_archive_rows(conn,archive,pack)
                    archive_status = "CONFIRMED"
            finally:
                conn.close()
        except BaseException:
            archive_status = "UNKNOWN"
        print("ARCHIVE_COMMITTED="+archive_status+"; BACKUP_PATH="+str(backup)
              +"; DB_RESTORE_NOT_AUTOMATIC",file=sys.stderr,flush=True)
        raise


def main():
    parser=argparse.ArgumentParser()
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight",action="store_true")
    mode.add_argument("--apply",action="store_true")
    mode.add_argument("--archive-worker",action="store_true",help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.archive_worker:
        try:
            worker_main()
        except BaseException as exc:
            print("ARCHIVE_WORKER_STOP="+type(exc).__name__,flush=True)
            sys.exit(1)
        return
    os.umask(0o077)
    if os.geteuid()!=0 or socket.gethostname().split(".")[0]!="vdska":
        fail("RUN_IN_ROOT_VDSKA")
    if not VENV.is_file() or not Path("/etc/az-baze/auth.env").is_file():
        fail("AZ_BAZE_RUNTIME_OR_SERVICE_ENV_MISSING")
    verify_private_bundle(PRIVATE_BUNDLE)
    health()
    smoke_routes()
    check_live_files()
    before=check_db()
    if ARCHIVE_OBJECTS & before.keys():
        fail("ARCHIVE_ALREADY_PRESENT_STOP")
    account=service_account()
    if shutil.disk_usage(BACKUPS).free < DB.stat().st_size+64*1024*1024:
        fail("INSUFFICIENT_BACKUP_SPACE")
    with tempfile.TemporaryDirectory(prefix="pr262-stage-") as directory:
        stage=Path(directory)
        prepare(stage)
        archive,bundle,pack=verified_pack(stage,PRIVATE_BUNDLE)
        print("PR262_PREFLIGHT=PASS; PRODUCTION_NOT_MODIFIED",flush=True)
        if args.apply:
            worker,_=prepare_worker(stage,account,PRIVATE_BUNDLE)
            apply(stage,worker,account,archive,pack)


if __name__=="__main__":
    try:
        main()
    except BaseException as exc:
        print("PR262_CONTROLLED_STOP="+type(exc).__name__+": "+str(exc)[:400],
              file=sys.stderr,flush=True)
        sys.exit(1)
