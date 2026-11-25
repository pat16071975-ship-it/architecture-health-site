#!/usr/bin/env python3
"""PR264: deploy only the audited local OCR review module and templates.

No schema migration, no OCR execution, no stock or financial writes.
PR262 is deliberately deferred; its runtime is not included here.
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
BASE_SHA = "559a297ec013997497d9ef125326bc695f3be53e"
AUDITED_HEAD = "deb6bd5a4dd270a992c535b09e7ce60ae7c6517b"
MERGED_SHA = "93a8ee21866dd6abc3c70c323f76adc75e077508"
ROOT = Path("/opt/az-baze-auth")
DB = Path("/var/lib/az-baze/auth.db")
BACKUPS = Path("/var/lib/az-baze/backups")
VENV = ROOT / "venv/bin/python"
SERVICE = "az-baze-auth"
LOCAL = "http://127.0.0.1:8001"
# New dependencies precede their consumers.
FILES = {
    "az-baze-auth/medical_supplies_ocr.py": (None,"84a1609c1d1e44d5ba89f7edc2c6fef79848d3ef"),
    "az-baze-auth/templates/medical_supplies_ocr.html": (None,"0908320c1ee8909885d1b767d8170f0b0659d75e"),
    "az-baze-auth/medical_supplies_documents.py": ("f9bcf89dd2f3cf54df9d8392d5ce269318923713","0e269427d75989a455a7a347346b933df665c776"),
    "az-baze-auth/templates/medical_supplies_documents.html": ("4333551d4939ed6632651dd014836c9ca4a57402","c5683f0fae0fd3f1cf0070108043387dc1ee63f1"),
}
EXEMPLARS = {
    "az-baze-auth/medical_supplies_ocr.py": "az-baze-auth/medical_supplies_documents.py",
    "az-baze-auth/templates/medical_supplies_ocr.html": "az-baze-auth/templates/medical_supplies_documents.html",
}
TABLES = {
    "users": {"id"},
    "permissions": {"user_id","section"},
    "med_supply_documents": {"id","sha256","size_bytes","original_bytes","content_type","version","status","fields_json"},
    "med_supply_document_events": {"id","document_id","actor_id","action","before_json","after_json"},
    "med_supply_items": {"id","name","unit"},
    "med_supply_daily_items": {"item_id","folder_id","opening_date"},
    "med_supply_folders": {"id","parent_id","name"},
    "med_supply_days": {"item_id","day","qty_in","value_in","qty_out","version"},
    "med_supply_periods": {"item_id","month","qty_in","qty_out"},
    "med_supply_history": {"id","item_id","action"},
}
# This list is only read, never installed. Verify the deferred runtime stays
# unchanged, whatever exact sanctioned baseline is present on the VPS.
DEFERRED_IDENT = (
    "mis_source_archive.py","mis_archive_bundle.py","upload_reconcile.py",
    "ident_import.py","cash_upload.py","completed_upload.py","daily_upload.py",
    "paid_services_upload.py","services_upload.py",
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
        "User-Agent": "AZ-BAZE-PR264-Immutable-Release",
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
    if prefix!="az-baze-auth":
        fail("UNSAFE_RUNTIME_PREFIX")
    target=ROOT/filename
    if target.parent not in (ROOT,ROOT/"templates"):
        fail("UNSAFE_RUNTIME_PATH")
    return target


def check_live_files():
    for directory in (ROOT,ROOT/"templates"):
        if directory.resolve()!=directory or not directory.is_dir():
            fail("RUNTIME_DIRECTORY_MISSING_OR_SYMLINK")
    for name,(old,_new) in FILES.items():
        check_expected(destination(name),old,"LIVE_BASE")
    print("LIVE_BASE=PASS; EXISTING=2; NEW_ABSENT=2; SOURCE="+BASE_SHA,flush=True)


def deferred_state():
    state={}
    for name in DEFERRED_IDENT:
        path=ROOT/name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            fail("DEFERRED_IDENT_NOT_REGULAR")
        state[name]=git_blob(path.read_bytes()) if path.exists() else None
    return state


def smoke_routes(new_version=False):
    base="/accounting/medical-supplies/incoming/"
    for route in ("/","/accounting/medical-supplies/",base,base+"1/download",base+"1/view","/reports/management/"):
        status,_=http_status(route)
        if status not in (302,401,403):
            fail("ROUTE_UNPROTECTED_OR_MISSING: "+route+" status="+str(status))
    if new_version:
        for suffix,method in (("1/ocr","GET"),("1/ocr/page","POST"),("1/ocr/save","POST")):
            status,_=http_status(base+suffix,method)
            if status not in (302,401,403):
                fail("OCR_ROUTE_UNPROTECTED_OR_MISSING: "+suffix+" status="+str(status))
    print("MEDICAL_ROUTES=PASS; ANONYMOUS_PROTECTED; OCR_ROUTES="+("3" if new_version else "BASE"),flush=True)


def check_ocr_environment():
    username=run("systemctl","show","-p","User","--value",SERVICE).stdout.strip()
    if not username or username=="root" or pwd.getpwnam(username).pw_uid==0:
        fail("OCR_REQUIRES_NONROOT_SERVICE")
    # Verify prerequisites as the real service account. No file creation,
    # package installation or actual document recognition in this release.
    for name in ("prlimit","pdfinfo","pdftoppm","tesseract"):
        binary=shutil.which(name,path="/usr/bin:/bin")
        if not binary:
            fail("OCR_TOOL_MISSING: "+name)
        run("runuser","-u",username,"--","test","-x",binary)
    langs=run("runuser","-u",username,"--","/usr/bin/tesseract","--list-langs").stdout.splitlines()
    if not {"rus","eng"}<=set(s.strip() for s in langs):
        fail("OCR_LANGUAGE_MISSING")
    run("runuser","-u",username,"--","test","-r",str(DB))
    run("runuser","-u",username,"--","test","-w",str(DB.parent))
    print("OCR_ENVIRONMENT=PASS; RUS_ENG=YES; NONROOT=YES",flush=True)


def validate_stage(stage):
    for name,(_old,new) in FILES.items():
        target=stage/name
        check_blob(target,new,"STAGED")
        if name.endswith(".py"):
            compile(target.read_bytes(),str(target),"exec")
        else:
            run(str(VENV),"-c",
                "import pathlib,jinja2,sys;"
                "jinja2.Environment().parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))",
                str(target))
    name="az-baze-auth/medical_supplies_documents.py"
    if extract_schema(destination(name).read_text(encoding="utf-8"),"SCHEMA") != \
       extract_schema((stage/name).read_text(encoding="utf-8"),"SCHEMA"):
        fail("SCHEMA_CHANGED_MIGRATION_FORBIDDEN")
    print("IMMUTABLE_TARGET=PASS; FILES=4; SOURCE="+MERGED_SHA,flush=True)
    print("SCHEMA_SOURCE=UNCHANGED; NO_MIGRATION; PYTHON_JINJA=PASS",flush=True)


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

def medical_state(path):
    """Read-only complete row fingerprints for the existing medical tables.
    No actual field values, document contents or patient data are logged.
    """
    conn=ro_conn(path)
    try:
        conn.execute("BEGIN")
        names=sorted(name for name,info in schema_map(conn).items()
                     if name.startswith("med_supply_") and info[0]=="table")
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


def backup_files(backup):
    for name,(old,_new) in FILES.items():
        if old is None:
            continue
        target=backup/"files"/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(destination(name),target)
        check_blob(target,old,"BACKUP")
    print("RUNTIME_BACKUP=PASS; FILES=2; NEW_FILES=2",flush=True)


def atomic_file_replace(data, path, exemplar=None):
    # For a new file use a sibling's existing permissions, and create it
    # without replacing anything that appeared since the initial check.
    metadata_source = exemplar or path
    if metadata_source.is_symlink() or not metadata_source.is_file():
        fail("RUNTIME_METADATA_NOT_REGULAR")
    metadata = metadata_source.stat()
    if exemplar is not None:
        check_expected(path, None, "NEW_TARGET")
    fd, temporary = tempfile.mkstemp(prefix=".pr264-", dir=path.parent)
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
    print("RUNTIME_INSTALL=PASS; BACKEND=2; TEMPLATES=2", flush=True)

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
    backup=BACKUPS/("pr264-medical-ocr-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    backup.mkdir(parents=True,mode=0o700,exist_ok=False)
    stop_attempted=False
    touched=False
    try:
        stop_attempted=True
        run("systemctl","stop",SERVICE)
        if active():
            fail("SERVICE_NOT_STOPPED")
        check_live_files()
        baseline=check_db()
        protected=medical_state(DB)
        deferred=deferred_state()
        backup_files(backup)
        db_archive=backup/"auth-pre-pr264.sqlite"
        digest=backup_sqlite(db_archive)
        if check_db(db_archive)!=baseline or medical_state(db_archive)!=protected:
            fail("DATABASE_BACKUP_CONTENT_MISMATCH")
        manifest={
            "release":"PR264-medical-ocr","base":BASE_SHA,
            "audited_head":AUDITED_HEAD,"merged_sha":MERGED_SHA,
            "files":FILES,"backup_sha256":digest,"db_delta":[],
            "deferred_ident_untouched":True,
            "created_utc":datetime.now(timezone.utc).isoformat(),
            "auto_db_restore":False,
        }
        (backup/"release-manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
        touched=True
        install(stage)
        if check_db()!=baseline or medical_state(DB)!=protected:
            fail("DATABASE_OR_MEDICAL_ROWS_CHANGED")
        if deferred_state()!=deferred:
            fail("DEFERRED_IDENT_CHANGED")
        print("DATABASE_MIGRATION=NONE; DATABASE_WRITES_BY_INSTALLER=NONE; MEDICAL_ROWS_UNCHANGED=PASS",flush=True)
        restart_and_smoke()
        for name,(_old,new) in FILES.items():
            check_blob(destination(name),new,"POST_RELEASE")
        if check_db()!=baseline:
            fail("POST_RELEASE_SCHEMA_CHANGED")
        if deferred_state()!=deferred:
            fail("DEFERRED_IDENT_CHANGED")
        print("PR262_RUNTIME_UNCHANGED=PASS",flush=True)
        print(f"PR264_RELEASE=PASS; MERGED_SHA={MERGED_SHA}; BACKUP={backup}",flush=True)
        print("REAL_DOCUMENT_OCR_ACCEPTANCE=PENDING; STOCK_POSTING=NONE",flush=True)
    except BaseException:
        print("PR264_RELEASE=FAIL; BEGIN_CODE_ROLLBACK",file=sys.stderr,flush=True)
        try:
            if stop_attempted:
                if touched:
                    run("systemctl","stop",SERVICE)
                    if active():
                        fail("ROLLBACK_SERVICE_NOT_STOPPED")
                    restore(backup)
                restart_and_smoke(rolled_back=True)
        except BaseException as exc:
            print("CODE_ROLLBACK_FAILED="+type(exc).__name__,file=sys.stderr,flush=True)
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
    check_live_files()
    check_db()
    check_ocr_environment()
    if not BACKUPS.is_dir() or BACKUPS.is_symlink() or shutil.disk_usage(BACKUPS).free < DB.stat().st_size+64*1024*1024:
        fail("BACKUP_DIRECTORY_OR_SPACE_UNAVAILABLE")
    with tempfile.TemporaryDirectory(prefix="pr264-stage-") as directory:
        stage=Path(directory)
        print(f"RELEASE_SOURCE={MERGED_SHA}; EXPECTED_BASE={BASE_SHA}; AUDITED_HEAD={AUDITED_HEAD}",flush=True)
        prepare(stage)
        print("PR264_PREFLIGHT=PASS; PRODUCTION_NOT_MODIFIED",flush=True)
        if args.apply:
            apply(stage)


if __name__=="__main__":
    try:
        main()
    except BaseException as exc:
        print("PR264_CONTROLLED_STOP="+type(exc).__name__+": "+str(exc)[:500],file=sys.stderr,flush=True)
        sys.exit(1)
