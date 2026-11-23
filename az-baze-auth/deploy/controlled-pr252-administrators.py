#!/usr/bin/env python3
"""AZ BAZE PR252 controlled production installer. Default --preflight is read-only.

Installs ONLY the audited eight runtime files from the immutable merged commit.
Creates six new administrators tables and two indexes transactionally; does not
alter existing finance modules, reports, users, roles, or payroll records.
"""
import argparse
import ast
import base64
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
AUDITED_HEAD = "46f4d9b1c133818c8f0ac1cfdde8bc6640b53a27"
MERGED_SHA = "83405d50bd1a508d4a90641907c8a5586f541147"
BASE_SHA = "5d7115050a86adca281af681da25f6b22b09e53d"
AUDITED_SERVER_BLOB = "b0934c61c722ab006a405d1aaefebf4cd4a86fbb"
LIVE_SERVER_BLOB = "8c9a1b45390e273a1e8f1bc8a12f331cf0fbdd47"
PRESERVED_SERVER_BLOB = "99dbe7ac9bd35bcc131d0c321b0d269716c4ceb9"
ROOT = Path("/opt/az-baze-auth")
DB = Path("/var/lib/az-baze/auth.db")
BACKUPS = Path("/var/lib/az-baze/backups")
SERVICE = "az-baze-auth"
VENV_PY = ROOT / "venv/bin/python"
LOCAL = "http://127.0.0.1:8001"

# Relative installed path: (exact expected live Git blob, exact audited target blob).
# None means the target must NOT yet exist. No wildcard file replacement.
FILES = {
    "app.py": (
        "7472be60508a497aa04ad969dac932877bf5d9bf",
        "4ebd5c7bf688fe929bef6b60a47d511a3aa504a2",
    ),
    "administrators.py": (
        None,
        "0b4a25b28a20b42965bf7f66e3f042b819b49ccd",
    ),
    "menu_order.py": (
        "433178e688cc29cd78ff60770994ec897fe3bc10",
        "50e0e628710898a98ff58850a722c1edb2bef1cd",
    ),
    "section_navigation.py": (
        "b404a4727e92958d2be9ac0ddbafd659614b9c1f",
        "89b9cd8a3e842c14646a3bf33c2fa6db188d7195",
    ),
    "server.py": (
        LIVE_SERVER_BLOB,
        PRESERVED_SERVER_BLOB,
    ),
    "templates/admin.html": (
        "b775de7450582e3e0bf17cf5557b82b47451377f",
        "19dc5c72e06918633280a4857ad4d4914d9fea68",
    ),
    "templates/administrators.html": (
        None,
        "a7f694052c7940774150638ab24b93ad48a8f877",
    ),
    "templates/user_form.html": (
        "a68ff6daa28b1b122661e7a6670bc6376d70ae52",
        "5a8445fcf298e323cc27875ed936bc28c5c1eb7c",
    ),
}
NEW_TABLES = frozenset({
    "az_admin_staff", "az_admin_rates", "az_admin_days",
    "az_admin_bonuses", "az_admin_months", "az_admin_events",
})
NEW_INDEXES = frozenset({"ix_az_admin_days_date", "ix_az_admin_events_month"})
NEW_OBJECTS = NEW_TABLES | NEW_INDEXES
ANCHORS = {"users", "permissions", "audit_log"}


def fail(message):
    raise RuntimeError(message)


def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def preserve_knowledge_catalog(data, expected_source_blob):
    """Carry over exactly two already-live knowledge_catalog registrations.

    Only valid against the approved Git source blob. The resulting merged server
    must exactly match the precomputed combined Git blob, never an arbitrary
    live-file patch. No knowledge_catalog module is downloaded or replaced.
    """
    if git_blob(data) != expected_source_blob:
        fail("KNOWLEDGE_MERGE_UNEXPECTED_SOURCE_BLOB")
    insertions = (
        (b"import upload_reconcile\n",
         b"import upload_reconcile\nimport knowledge_catalog\n"),
        (b"clinic_structure_settings.register(app)\n",
         b"clinic_structure_settings.register(app)\nknowledge_catalog.register(app)\n"),
    )
    if b"import knowledge_catalog" in data or b"knowledge_catalog.register" in data:
        fail("KNOWLEDGE_MERGE_DUPLICATE_REGISTRATION")
    for original, replacement in insertions:
        if data.count(original) != 1:
            fail("KNOWLEDGE_MERGE_ANCHOR_MISMATCH")
        data = data.replace(original, replacement, 1)
    return data


def verify_knowledge_catalog_available():
    module = ROOT / "knowledge_catalog.py"
    package = ROOT / "knowledge_catalog" / "__init__.py"
    if not ((module.is_file() and not module.is_symlink())
            or (package.is_file() and not package.is_symlink())):
        fail("LIVE_KNOWLEDGE_CATALOG_MODULE_NOT_FOUND")
    print("LIVE_KNOWLEDGE_CATALOG=PRESENT; WILL_NOT_REPLACE")


def run(*args, check=True):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if check and result.returncode:
        fail(f"COMMAND_FAIL: {args[0]} {args[1] if len(args)>1 else ''}: "
             + (result.stderr or result.stdout)[-650:])
    return result


def is_active():
    return run("systemctl", "is-active", "--quiet", SERVICE, check=False).returncode == 0


def blob_check(path, expected, label):
    if path.is_symlink():
        fail(f"{label}_SYMLINK_FORBIDDEN: {path}")
    if expected is None:
        if path.exists():
            fail(f"{label}_SHOULD_BE_ABSENT: {path}")
        return
    if not path.is_file():
        fail(f"{label}_MISSING: {path}")
    actual = git_blob(path.read_bytes())
    if actual != expected:
        fail(f"{label}_BLOB_MISMATCH: {path.name}; expected={expected}; actual={actual}")


def check_live_files():
    if not ROOT.is_dir() or not (ROOT / "templates").is_dir():
        fail("LIVE_APP_ROOT_MISSING")
    for name, (before, _target) in FILES.items():
        blob_check(ROOT / name, before, "LIVE_BASE")
    print(f"RUNTIME_BASE=PASS; FILES={len(FILES)}; BASE={BASE_SHA}")


def fetch_audited_files(stage):
    for name, (_before, expected) in FILES.items():
        url = f"https://raw.githubusercontent.com/{REPO}/{MERGED_SHA}/az-baze-auth/{name}"
        data = None
        source_sha = AUDITED_SERVER_BLOB if name == "server.py" else expected
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "az-pr252-deployer"})
                with urllib.request.urlopen(req, timeout=30) as response:
                    if response.status != 200:
                        fail(f"FETCH_STATUS_BAD: {name}")
                    data = response.read(512 * 1024 + 1)
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(1)
        if data is None:
            # Fallback is pinned to the same immutable source SHA.
            api_url = f"https://api.github.com/repos/{REPO}/git/blobs/{source_sha}"
            req = urllib.request.Request(api_url, headers={
                "User-Agent": "az-pr252-deployer", "Accept": "application/vnd.github+json"
            })
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status != 200:
                    fail(f"GITHUB_API_STATUS_BAD: {name}")
                item = json.loads(response.read(1024 * 1024 + 1))
            if item.get("encoding") != "base64":
                fail(f"GITHUB_API_ENCODING_BAD: {name}")
            data = base64.b64decode(item["content"])
            print(f"FETCH_FALLBACK=GITHUB_API; FILE={name}")
        if data is None or len(data) > 512 * 1024:
            fail(f"SOURCE_TOO_LARGE_OR_MISSING: {name}")
        if git_blob(data) != source_sha:
            fail(f"IMMUTABLE_SOURCE_BLOB_MISMATCH: {name}")
        if name == "server.py":
            data = preserve_knowledge_catalog(data, AUDITED_SERVER_BLOB)
            if git_blob(data) != PRESERVED_SERVER_BLOB:
                fail("KNOWLEDGE_MERGE_TARGET_BLOB_MISMATCH")
            print("KNOWLEDGE_CATALOG=EXACTLY_PRESERVED_IN_SERVER")
        actual = git_blob(data)
        if actual != expected:
            fail(f"TARGET_BLOB_MISMATCH: {name}; expected={expected}; actual={actual}")
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if name.endswith(".py"):
            compile(data.decode("utf-8"), str(target), "exec")
    for name in ("templates/admin.html", "templates/administrators.html",
                 "templates/user_form.html"):
        result = run(str(VENV_PY), "-c",
                     "import sys,jinja2; jinja2.Environment().parse("
                     "open(sys.argv[1],encoding='utf-8').read())",
                     str(stage / name))
        if result.returncode:
            fail(f"JINJA_CHECK_FAIL: {name}")
    print(f"TARGET_BLOBS=PASS; SHA={MERGED_SHA}; AUDITED_HEAD={AUDITED_HEAD}")


def schema_statements(stage):
    tree = ast.parse((stage / "administrators.py").read_text(encoding="utf-8"))
    assignments = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "SCHEMA" for t in node.targets)
    ]
    if len(assignments) != 1:
        fail("SCHEMA_DECLARATION_UNEXPECTED")
    ddl = ast.literal_eval(assignments[0].value)
    stmts = [x.strip() for x in ddl.split(";") if x.strip()]
    if len(stmts) != 8:
        fail("SCHEMA_STATEMENT_COUNT_UNEXPECTED")
    found_tables, found_indexes = set(), set()
    for statement in stmts:
        match_table = re.match(r"^CREATE TABLE IF NOT EXISTS (az_admin_[a-z_]+)\s*\(", statement)
        match_index = re.match(r"^CREATE INDEX IF NOT EXISTS ([a-z_]+)\s+ON\s+", statement)
        if match_table:
            found_tables.add(match_table.group(1))
        elif match_index:
            found_indexes.add(match_index.group(1))
        else:
            fail("SCHEMA_CONTAINS_UNAPPROVED_STATEMENT")
        # Each DDL statement must have exactly one CREATE token and no mutating SQL verbs.
        if len(re.findall(r"\bCREATE\b", statement, re.I)) != 1:
            fail("SCHEMA_UNEXPECTED_NESTED_CREATE")
        if re.search(r"\b(DROP|ALTER|INSERT|UPDATE|REPLACE|ATTACH|DETACH)\b", statement, re.I):
            fail("SCHEMA_FORBIDDEN_OPERATION")
    if found_tables != NEW_TABLES or found_indexes != NEW_INDEXES:
        fail("SCHEMA_OBJECT_ALLOWLIST_MISMATCH")
    mem = sqlite3.connect(":memory:")
    try:
        for statement in stmts:
            mem.execute(statement)
        actual = {row[0] for row in mem.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )}
        if actual != NEW_OBJECTS:
            fail(f"SCHEMA_IN_MEMORY_DELTA_BAD: {sorted(actual ^ NEW_OBJECTS)}")
    finally:
        mem.close()
    print("ADDITIVE_SCHEMA=PASS; NEW_TABLES=6; NEW_INDEXES=2")
    return stmts


def db_connect_readonly(path):
    if not path.is_file() or path.is_symlink():
        fail("DATABASE_MISSING_OR_SYMLINK")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA query_only=ON")
    return conn


def schema_map(conn):
    return {row[1]: (row[0], row[2], row[3]) for row in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    )}


def table_counts(conn, names):
    counts = {}
    for table in names:
        safe = table.replace('"', '""')
        counts[table] = conn.execute(f'SELECT count(*) FROM "{safe}"').fetchone()[0]
    return counts


def db_health(conn):
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        fail("DATABASE_INTEGRITY_CHECK_FAIL")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        fail("DATABASE_FOREIGN_KEY_CHECK_FAIL")


def check_live_database():
    conn = db_connect_readonly(DB)
    try:
        current = schema_map(conn)
        if not ANCHORS <= set(current):
            fail(f"NOT_AZ_BAZE_DB: missing={sorted(ANCHORS - set(current))}")
        if set(current) & NEW_OBJECTS:
            fail("ADMINISTRATORS_SCHEMA_ALREADY_PRESENT_OR_PARTIAL")
        db_health(conn)
        print(f"DB_PREFLIGHT=PASS; LEGACY_SCHEMA_OBJECTS={len(current)}")
    finally:
        conn.close()


def http_status(path):
    class DoNotFollowRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, url):
            return None
    opener = urllib.request.build_opener(DoNotFollowRedirect())
    try:
        with opener.open(LOCAL + path, timeout=3) as response:
            return response.status, response.read(120)
    except urllib.error.HTTPError as error:
        return error.code, b""


def health_check():
    if not is_active():
        fail("SERVICE_NOT_ACTIVE")
    code, payload = http_status("/health")
    if code != 200 or payload.strip() != b"ok":
        fail(f"LOCAL_HEALTH_BAD: {code}")
    print("SERVICE_HEALTH=PASS")


def preflight(stage):
    if os.geteuid() != 0:
        fail("ROOT_REQUIRED: run in the AZ BAZE server terminal as root")
    if not VENV_PY.is_file():
        fail("AZ_BAZE_VENV_MISSING")
    if not Path("/etc/az-baze/auth.env").is_file():
        fail("AZ_BAZE_SERVICE_ENV_MISSING")
    health_check()
    check_live_files()
    verify_knowledge_catalog_available()
    check_live_database()
    fetch_audited_files(stage)
    statements = schema_statements(stage)
    print("PR252_PREFLIGHT=PASS; LIVE_NOT_MODIFIED")
    return statements


def copy_database_backup(destination):
    src = db_connect_readonly(DB)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    check = db_connect_readonly(destination)
    try:
        db_health(check)
    finally:
        check.close()
    sha = hashlib.sha256(destination.read_bytes()).hexdigest()
    print(f"BACKUP_DB=PASS; SHA256={sha}")
    return sha


def backup_runtime(directory):
    for name, (prior, _new) in FILES.items():
        if prior is None:
            continue
        source = ROOT / name
        target = directory / "files" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        blob_check(target, prior, "BACKUP")


def put_file_atomically(src, destination, template=False):
    reference = ROOT / ("templates/admin.html" if template else "server.py")
    meta = reference.stat()
    temp = destination.parent / (".pr252-" + destination.name + ".new")
    if temp.exists():
        fail(f"STALE_INSTALL_TEMP_EXISTS: {temp}")
    with temp.open("xb") as output:
        output.write(src.read_bytes())
        output.flush()
        os.fsync(output.fileno())
    os.chown(temp, meta.st_uid, meta.st_gid)
    os.chmod(temp, meta.st_mode & 0o777)
    os.replace(temp, destination)


def install_runtime(stage):
    for name, (_before, target_sha) in FILES.items():
        dest = ROOT / name
        put_file_atomically(stage / name, dest, name.startswith("templates/"))
        blob_check(dest, target_sha, "DEPLOYED")
    print("RUNTIME_INSTALL=PASS; 8_EXACT_FILES")


def migrate_additive(statements):
    conn = sqlite3.connect(DB, timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        before = schema_map(conn)
        if set(before) & NEW_OBJECTS:
            fail("SCHEMA_DELTA_NOT_EMPTY")
        legacy_tables = [name for name, (typ, _tbl, _sql) in before.items() if typ == "table"]
        before_counts = table_counts(conn, legacy_tables)
        for stmt in statements:
            conn.execute(stmt)
        after = schema_map(conn)
        if any(after.get(name) != definition for name, definition in before.items()):
            fail("LEGACY_SCHEMA_CHANGED")
        extra = set(after) - set(before)
        if extra != NEW_OBJECTS:
            fail(f"SCHEMA_DELTA_UNEXPECTED: {sorted(extra ^ NEW_OBJECTS)}")
        if table_counts(conn, legacy_tables) != before_counts:
            fail("LEGACY_ROW_COUNTS_CHANGED")
        if any(table_counts(conn, [table])[table] for table in NEW_TABLES):
            fail("ADMINISTRATORS_TABLES_NOT_EMPTY")
        db_health(conn)
        conn.commit()
        print("DATABASE_MIGRATION=PASS; ADD_ONLY=6_TABLES+2_INDEXES; LEGACY_ROWS=UNCHANGED")
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def restore_runtime(directory):
    for name, (before, _after) in FILES.items():
        path = ROOT / name
        if before is None:
            if path.exists():
                path.unlink()
        else:
            previous = directory / "files" / name
            if not previous.is_file():
                fail(f"ROLLBACK_BACKUP_MISSING: {name}")
            temp = path.parent / (".pr252-restore-" + path.name)
            shutil.copy2(previous, temp)
            os.replace(temp, path)
            blob_check(path, before, "ROLLBACK")
    print("RUNTIME_ROLLBACK=PASS; DATABASE_NOT_RESTORED_AUTOMATICALLY")


def restart_and_smoke():
    run("systemctl", "start", SERVICE)
    for _ in range(20):
        try:
            if is_active() and http_status("/health") == (200, b"ok"):
                break
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    else:
        fail("RESTART_LOCAL_HEALTH_FAILED")
    for path in ("/administrators/", "/administrators/?tab=payroll", "/"):
        code, _body = http_status(path)
        if code != 302:
            fail(f"ROUTE_SMOKE_FAILED: path={path} code={code}")
    print("SERVICE_SMOKE=PASS; ANONYMOUS_ADMINISTRATORS=302; HOME=302")


def apply(stage, statements):
    label = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUPS / f"pr252-administrators-{label}"
    if backup.exists():
        fail("BACKUP_DIRECTORY_EXISTS")
    backup.mkdir(mode=0o700, parents=True)
    touched = False
    service_stopped = False
    try:
        run("systemctl", "stop", SERVICE)
        service_stopped = True
        if is_active():
            fail("SERVICE_DID_NOT_STOP")
        check_live_files()
        verify_knowledge_catalog_available()
        check_live_database()
        backup_runtime(backup)
        dbsha = copy_database_backup(backup / "auth-pre-pr252.sqlite")
        record = {
            "release": "PR252-administrators",
            "audited_head": AUDITED_HEAD, "merged_sha": MERGED_SHA,
            "base_sha": BASE_SHA, "db_backup_sha256": dbsha,
            "old_and_new_blobs": FILES,
            "created_utc": label,
            "database_auto_restore": False,
        }
        (backup / "release-manifest.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.chmod(backup / "release-manifest.json", 0o600)
        touched = True
        install_runtime(stage)
        migrate_additive(statements)
        restart_and_smoke()
        for name, (_old, new) in FILES.items():
            blob_check(ROOT / name, new, "POST_RELEASE")
        check = db_connect_readonly(DB)
        try:
            db_health(check)
            if not NEW_TABLES <= set(schema_map(check)):
                fail("POST_RELEASE_SCHEMA_MISSING")
        finally:
            check.close()
        print(f"PR252_RELEASE=PASS; MERGED_SHA={MERGED_SHA}; BACKUP={backup}")
        print("PHYSICAL_BROWSER_ACCEPTANCE=NOT_YET_PERFORMED")
        print("OTHER_FINANCIAL_REPORTS=UNCHANGED; EXISTING_USERS=UNCHANGED")
    except BaseException:
        print("PR252_RELEASE=FAIL; BEGINNING_SAFE_CODE_ROLLBACK", file=sys.stderr)
        try:
            if service_stopped:
                run("systemctl", "stop", SERVICE, check=False)
                if touched:
                    restore_runtime(backup)
                restart_and_smoke_rollback()
        except BaseException as secondary:
            print(f"ROLLBACK_ERROR={secondary}", file=sys.stderr)
        print(f"BACKUP_LOCATION={backup}; DATABASE_NOT_RESTORED_TO_AVOID_LOST_WRITES",
              file=sys.stderr)
        raise


def restart_and_smoke_rollback():
    run("systemctl", "start", SERVICE)
    for _ in range(20):
        try:
            if is_active() and http_status("/health") == (200, b"ok"):
                print("OLD_SERVICE_RESTORED=PASS")
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    fail("OLD_SERVICE_ROLLBACK_HEALTH_FAILED")


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="az-pr252-stage-") as work:
        stage = Path(work)
        print(f"RELEASE_SOURCE={MERGED_SHA}; AUDITED_SOURCE={AUDITED_HEAD}")
        statements = preflight(stage)
        if args.preflight:
            return
        apply(stage, statements)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print(f"PR252_CONTROLLED_STOP={type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
