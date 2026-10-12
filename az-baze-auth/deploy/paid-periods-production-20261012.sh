#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Owner-approved support for a selected period in the existing paid MIS loader.
# Exactly six runtime files; no checkout, release marker edit, or live migration.
# Live SQLite connections are read-only. Rollback NEVER restores the database.
REPO="pat16071975-ship-it/architecture-health-site"
TARGET_COMMIT="1853eb6e074a524b0904396ad7a0519dbac6c709"
PAID_TARGET="e1f8cf945161c753775af9a39d1df47c1639df0e"
UPLOAD_TARGET="83abc5cb422887ba14f9d6d90755a25c8f6a9427"
STORAGE_TARGET="367592919431997255862ed179a5c6a4f93703ad"
UPLOADS_TARGET="d3aaaa2b5a9cc7e4b0a6ccc656384d5c9f2aae94"
INDEX_TARGET="a6febb1769603e02e1433fac1402d9796e4b9632"
PERIOD_TARGET="042d6574edc26fecc5c42893684175e858f0d59b"
BASE_TEMPLATE_BLOB="c19bbc4660519f3c47a9dd15ca15488247df4d2b"
IDENT_BLOB="e9950b781b0f4ebdfe044860b948813938a8089d"
TOKEN="PAID-PERIODS-ONLY-20261012"
APP_ROOT="/opt/az-baze-auth"
REPORT_ROOT="/var/www/az-baze.ru/reports"
DB="/var/lib/az-baze/auth.db"
BACKUP_ROOT="/var/lib/az-baze/backups"
LOCK_FILE="/run/lock/az-baze-paid-periods.lock"
SERVICE="az-baze-auth"
HEALTH_URL="http://127.0.0.1:8001/health"
HEALTH_WAIT_SECONDS=35
SERVICE_TIMEOUT_SECONDS=45
PYTHON="$APP_ROOT/venv/bin/python"
MODE="${1:-}"

case "$MODE" in
  --preflight) [ "$#" -eq 1 ] || { echo "STOP: INVALID_ARGUMENTS"; exit 2; } ;;
  --apply)
    [ "$#" -eq 2 ] && [ "$2" = "$TOKEN" ] || { echo "STOP: CONFIRM_TOKEN_REQUIRED"; exit 2; } ;;
  *) echo "Usage: $0 --preflight | --apply $TOKEN"; exit 2 ;;
esac
[ "$(id -u)" -eq 0 ] || { echo "STOP: ROOT_REQUIRED"; exit 1; }
[ "$(hostname -s)" = "az-server" ] || { echo "STOP: WRONG_HOST"; exit 1; }
for dependency in curl git systemctl python3 timeout flock sha256sum stat readlink; do
  command -v "$dependency" >/dev/null
done
for pinned in "$TARGET_COMMIT" "$PAID_TARGET" "$UPLOAD_TARGET" "$STORAGE_TARGET" \
              "$UPLOADS_TARGET" "$INDEX_TARGET" "$PERIOD_TARGET"; do
  [[ "$pinned" =~ ^[0-9a-f]{40}$ ]] || { echo "STOP: UNPINNED_DRAFT"; exit 1; }
done
[ -x "$PYTHON" ] || { echo "STOP: PRODUCTION_PYTHON_MISSING"; exit 1; }
if [ "$MODE" = "--apply" ]; then
  exec 9>"$LOCK_FILE"
  flock -n 9 || { echo "STOP: DEPLOY_ALREADY_RUNNING"; exit 1; }
fi

STAGE="$(mktemp -d /tmp/az-paid-periods.XXXXXX)"
MANIFEST="$STAGE/runtime-manifest.txt"
BACKUP=""
INSTALL_STARTED=0
SERVICE_STOP_ATTEMPTED=0
SUCCESS=0
cat > "$MANIFEST" <<EOF
app|paid_services.py|9d64a3a379ba1a117263b8d7ef445fdbc33b325f|$PAID_TARGET
app|paid_services_upload.py|26ed2c138ef55b84c8f36a147ebe9f4c7813087a|$UPLOAD_TARGET
app|report_storage.py|e14756f3eb7a311100e08d21a6e2f781231f7fd4|$STORAGE_TARGET
app|templates/uploads.html|53d0ee65ad795a97ed7a7140341864630a194069|$UPLOADS_TARGET
report|period-view.js|0f59b99d5c75651be2ef9aff36d0a1862c4838e3|$PERIOD_TARGET
report|index.html|9f4ecc4ecbbb2c2d031978b75449eb7332f0da47|$INDEX_TARGET
EOF

blob() { git hash-object "$1"; }
metadata() { stat -c '%u:%g:%a' "$1"; }
live_path() {
  if [ "$1" = "app" ]; then printf '%s/%s\n' "$APP_ROOT" "$2"; else printf '%s/%s\n' "$REPORT_ROOT" "$2"; fi
}
repo_path() {
  if [ "$1" = "app" ]; then printf 'az-baze-auth/%s\n' "$2"; else printf 'reports/%s\n' "$2"; fi
}
regular_path() {
  [ -f "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]
}
protected_ok() {
  regular_path "$APP_ROOT/templates/base.html" &&
    regular_path "$APP_ROOT/ident_import.py" &&
    [ "$(blob "$APP_ROOT/templates/base.html")" = "$BASE_TEMPLATE_BLOB" ] &&
    [ "$(blob "$APP_ROOT/ident_import.py")" = "$IDENT_BLOB" ]
}
live_matches() {
  local want="$1" scope rel base target live expected
  while IFS='|' read -r scope rel base target; do
    live="$(live_path "$scope" "$rel")"
    regular_path "$live" || return 1
    if [ "$want" = "base" ]; then expected="$base"; else expected="$target"; fi
    [ "$(blob "$live")" = "$expected" ] || return 1
  done < "$MANIFEST"
}
health_ok() {
  systemctl is-active --quiet "$SERVICE" &&
    [ "$(curl -fsS --connect-timeout 2 --max-time 3 "$HEALTH_URL" 2>/dev/null)" = "ok" ]
}
wait_health() {
  local deadline=$((SECONDS + HEALTH_WAIT_SECONDS))
  while (( SECONDS < deadline )); do
    if health_ok; then return 0; fi
    sleep 1
  done
  return 1
}
service_stopped() { ! systemctl is-active --quiet "$SERVICE"; }

atomic_copy() {
  python3 -B - "$1" "$2" <<'PY'
from pathlib import Path
import os, shutil, sys, tempfile
source, target = map(Path, sys.argv[1:])
assert source.is_file() and not source.is_symlink()
assert target.is_file() and not target.is_symlink()
meta = source.stat()
fd, name = tempfile.mkstemp(prefix='.paid-periods-', dir=target.parent)
try:
    with os.fdopen(fd, 'wb') as handle, source.open('rb') as original:
        shutil.copyfileobj(original, handle)
        handle.flush()
        os.fchown(handle.fileno(), meta.st_uid, meta.st_gid)
        shutil.copystat(source, name, follow_symlinks=False)
        os.fsync(handle.fileno())
    os.replace(name, target)
    directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if os.path.exists(name):
        os.unlink(name)
PY
}
on_exit() {
  local rc=$? rollback_ok=1 stopped=0 scope rel base target live saved
  trap - EXIT
  trap '' INT TERM
  set +e
  if [ "$rc" -ne 0 ] && [ "$SUCCESS" -ne 1 ]; then
    if [ "$INSTALL_STARTED" -eq 1 ]; then
      echo "PAID_PERIODS_ROLLBACK=START"
      if timeout "${SERVICE_TIMEOUT_SECONDS}s" systemctl stop "$SERVICE" && service_stopped; then
        stopped=1
        while IFS='|' read -r scope rel base target; do
          live="$(live_path "$scope" "$rel")"
          saved="$BACKUP/files/$scope/$rel"
          if regular_path "$saved" && [ "$(blob "$saved")" = "$base" ] &&
             atomic_copy "$saved" "$live" &&
             [ "$(blob "$live")" = "$base" ] &&
             [ "$(metadata "$live")" = "$(metadata "$saved")" ]; then
            echo "RESTORED[$scope/$rel]=PASS"
          else
            rollback_ok=0
            echo "RESTORED[$scope/$rel]=FAIL"
          fi
        done < "$MANIFEST"
        protected_ok || rollback_ok=0
        live_matches base || rollback_ok=0
      else
        rollback_ok=0
        echo "ROLLBACK_SERVICE_STOP=FAIL"
      fi
      if [ "$stopped" -eq 1 ] && [ "$rollback_ok" -eq 1 ] &&
         timeout "${SERVICE_TIMEOUT_SECONDS}s" systemctl start "$SERVICE" && wait_health; then
        echo "PAID_PERIODS_ROLLBACK=PASS"
        echo "SERVICE_HEALTH_ROLLBACK=PASS"
      else
        echo "PAID_PERIODS_ROLLBACK=FAIL"
      fi
    elif [ "$SERVICE_STOP_ATTEMPTED" -eq 1 ]; then
      if live_matches base && protected_ok &&
         timeout "${SERVICE_TIMEOUT_SECONDS}s" systemctl start "$SERVICE" && wait_health; then
        echo "SERVICE_RECOVERY_WITHOUT_FILE_CHANGES=PASS"
      else
        echo "SERVICE_RECOVERY_WITHOUT_FILE_CHANGES=FAIL"
      fi
    fi
    [ -z "$BACKUP" ] || echo "BACKUP=$BACKUP"
    echo "DB_RESTORE=NOT_PERFORMED"
    if [ "$MODE" = "--apply" ]; then echo "PAID_PERIODS_PRODUCTION_DEPLOY=FAIL"; else echo "PAID_PERIODS_PREFLIGHT=FAIL"; fi
  fi
  rm -rf -- "$STAGE"
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cat > "$STAGE/db_ops.py" <<'PY'
"""Read-only live checks; all CREATE/INSERT operations target an isolated copy."""
import ast
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

PERIOD_OBJECTS = {
    'service_payment_periods', 'service_payment_period_versions',
    'idx_service_payment_periods_end', 'idx_service_payment_period_versions_range',
}

def read_only(path):
    conn = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True, timeout=10)
    conn.execute('PRAGMA query_only=ON')
    assert conn.execute('PRAGMA query_only').fetchone() == (1,)
    conn.execute('BEGIN')
    return conn

def schema(conn):
    return [list(row) for row in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    )]

def state(conn):
    assert conn.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    digest = hashlib.sha256()
    for statement in conn.iterdump():
        digest.update(statement.encode('utf-8') + b'\n')
    return {'schema': schema(conn), 'dump_sha256': digest.hexdigest(),
            'user_version': conn.execute('PRAGMA user_version').fetchone()[0],
            'application_id': conn.execute('PRAGMA application_id').fetchone()[0]}

def snapshot(source, target, state_path):
    assert not Path(target).exists(), 'BACKUP_ALREADY_EXISTS'
    src = read_only(source)
    try:
        before = state(src)
        dst = sqlite3.connect(target)
        deadline = time.monotonic() + 45
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise TimeoutError('SQLITE_BACKUP_TIMEOUT')
        try:
            src.backup(dst, pages=256, progress=progress, sleep=0.02)
        finally:
            dst.close()
    finally:
        src.close()
    os.chmod(target, 0o600)
    check = read_only(target)
    try:
        assert state(check) == before, 'SQLITE_BACKUP_MISMATCH'
    finally:
        check.close()
    Path(state_path).write_text(json.dumps(before, sort_keys=True, indent=2) + '\n')
    os.chmod(state_path, 0o600)

def check_exact(source, state_path):
    expected = json.loads(Path(state_path).read_text())
    conn = read_only(source)
    try:
        assert state(conn) == expected, 'DB_CHANGED_BEFORE_SERVICE_START'
    finally:
        conn.close()

def check_post(source, state_path):
    expected = json.loads(Path(state_path).read_text())
    conn = read_only(source)
    try:
        assert conn.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        after = {row[1]: row for row in schema(conn)}
        for row in expected['schema']:
            assert after.get(row[1]) == row, 'EXISTING_DB_SCHEMA_CHANGED'
        extras = set(after) - {row[1] for row in expected['schema']}
        assert extras <= PERIOD_OBJECTS, 'UNEXPECTED_DB_SCHEMA_ADDITION'
        assert conn.execute('PRAGMA user_version').fetchone()[0] == expected['user_version']
        assert conn.execute('PRAGMA application_id').fetchone()[0] == expected['application_id']
    finally:
        conn.close()

def table_data(conn, names):
    result = {}
    for name in names:
        escaped = name.replace('"', '""')
        digest = hashlib.sha256()
        for row in conn.execute('SELECT * FROM "' + escaped + '"'):
            digest.update(repr(tuple(row)).encode('utf-8') + b'\n')
        result[name] = digest.hexdigest()
    return result

def schema_ready(isolated, staged_source):
    # Extract only the constant and initializer: never import the application.
    tree = ast.parse(Path(staged_source).read_text(encoding='utf-8'))
    constants = [node for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'SCHEMA' for t in node.targets)]
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == 'init_schema']
    assert len(constants) == len(functions) == 1, 'SCHEMA_INITIALIZER_MISSING'
    namespace = {'__builtins__': {}}
    selected = ast.Module(body=constants + functions, type_ignores=[])
    exec(compile(ast.fix_missing_locations(selected), staged_source, 'exec'), namespace)
    conn = sqlite3.connect(isolated)
    try:
        before_schema = schema(conn)
        names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before_data = table_data(conn, names)
        conn.execute('BEGIN')
        namespace['init_schema'](conn)
        assert conn.in_transaction, 'INITIALIZER_COMMITTED_CALLER_TRANSACTION'
        after_schema = {row[1]: row for row in schema(conn)}
        for row in before_schema:
            assert after_schema.get(row[1]) == row, 'INITIALIZER_CHANGED_EXISTING_SCHEMA'
        assert PERIOD_OBJECTS <= set(after_schema), 'PERIOD_SCHEMA_MISSING'
        added = set(after_schema) - {row[1] for row in before_schema}
        assert added <= PERIOD_OBJECTS, 'UNEXPECTED_INITIALIZER_SCHEMA_ADDITION'
        assert table_data(conn, names) == before_data, 'INITIALIZER_CHANGED_EXISTING_DATA'
        assert conn.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        conn.rollback()
    finally:
        conn.close()

mode, *args = sys.argv[1:]
if mode == 'snapshot': snapshot(*args)
elif mode == 'exact': check_exact(*args)
elif mode == 'post': check_post(*args)
elif mode == 'schema-ready': schema_ready(*args)
else: raise SystemExit('UNKNOWN_DB_OPERATION')
PY

assert_db_quiescent() {
  "$PYTHON" -B - "$DB" <<'PY'
import os, sys
db = os.path.realpath(sys.argv[1])
targets = {db, db + '-wal', db + '-shm', db + '-journal'}
hits = []
for pid in os.listdir('/proc'):
    if not pid.isdigit():
        continue
    root = '/proc/' + pid + '/fd'
    try:
        fds = os.listdir(root)
    except (FileNotFoundError, PermissionError):
        continue
    for fd in fds:
        try:
            target = os.path.realpath(root + '/' + fd)
        except OSError:
            continue
        if target in targets:
            hits.append((pid, fd))
assert not hits, 'DB_NOT_QUIESCENT: ' + repr(hits[:20])
print('DB_QUIESCENCE=PASS')
PY
}

echo "=== AZ-BAZE PAID PERIODS SIX-FILE PREFLIGHT ==="
echo "TARGET_COMMIT=$TARGET_COMMIT"
regular_path "$DB" || { echo "STOP: UNEXPECTED_DB_PATH"; exit 1; }
protected_ok || { echo "STOP: PROTECTED_BASE_OR_IDENT_MISMATCH"; exit 1; }
echo "PROTECTED_BASE_AND_IDENT=PASS"
health_ok
echo "SERVICE_HEALTH_PRE=PASS"
if live_matches target; then
  echo "ALREADY_INSTALLED=PASS"
  echo "CHANGED_FILES=NONE"
  if [ "$MODE" = "--apply" ]; then echo "PAID_PERIODS_PRODUCTION_DEPLOY=PASS"; else echo "PAID_PERIODS_PREFLIGHT=PASS"; fi
  SUCCESS=1
  exit 0
fi
live_matches base || { echo "STOP: LIVE_BASE_OR_MIXED_STATE_MISMATCH"; exit 1; }
echo "LIVE_BASE_GUARD=PASS"
while IFS='|' read -r scope rel base target; do
  out="$STAGE/$scope/$rel"
  mkdir -p "$(dirname "$out")"
  curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 \
    "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/$(repo_path "$scope" "$rel")" -o "$out"
  [ "$(blob "$out")" = "$target" ] || { echo "STOP: TARGET_BLOB_MISMATCH[$scope/$rel]"; exit 1; }
done < "$MANIFEST"
echo "TARGET_BLOBS=PASS"
"$PYTHON" -B - "$STAGE" <<'PY'
from pathlib import Path
from jinja2 import Environment
import sys
root = Path(sys.argv[1])
for name in ['paid_services.py', 'paid_services_upload.py', 'report_storage.py']:
    source = root / 'app' / name
    compile(source.read_text(encoding='utf-8'), str(source), 'exec')
Environment().parse((root / 'app/templates/uploads.html').read_text(encoding='utf-8'))
PY
echo "TARGET_PYTHON_AND_TEMPLATE_SYNTAX=PASS"
"$PYTHON" -B "$STAGE/db_ops.py" snapshot "$DB" "$STAGE/schema-check.db" "$STAGE/preflight-db-state.json"
"$PYTHON" -B "$STAGE/db_ops.py" schema-ready "$STAGE/schema-check.db" "$STAGE/app/paid_services.py"
echo "ISOLATED_SCHEMA_READY=PASS"
echo "LIVE_SCHEMA_EXECUTION_BY_INSTALLER=NONE"
echo "PAID_PERIODS_PREFLIGHT=PASS"
if [ "$MODE" = "--preflight" ]; then
  echo "LIVE_CHANGES=NONE"
  SUCCESS=1
  exit 0
fi

protected_ok
live_matches base
health_ok
mkdir -p "$BACKUP_ROOT"
BACKUP="$(mktemp -d "$BACKUP_ROOT/paid-periods-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
mkdir -p "$BACKUP/files" "$BACKUP/db"
cp -- "$MANIFEST" "$BACKUP/runtime-manifest.txt"
printf 'TARGET_COMMIT=%s\nSCRIPT_BLOB=%s\nUTC_STARTED=%s\n' \
  "$TARGET_COMMIT" "$(blob "$0")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$BACKUP/deploy-meta.txt"
while IFS='|' read -r scope rel base target; do
  live="$(live_path "$scope" "$rel")"
  saved="$BACKUP/files/$scope/$rel"
  mkdir -p "$(dirname "$saved")"
  cp -a -- "$live" "$saved"
  [ "$(blob "$saved")" = "$base" ]
  [ "$(metadata "$saved")" = "$(metadata "$live")" ]
  printf '%s/%s|uid:gid:mode=%s\n' "$scope" "$rel" "$(metadata "$saved")" >> "$BACKUP/deploy-meta.txt"
  # Keep ownership, mode and ACL/xattrs, but retain new timestamps for Python cache invalidation.
  cp --attributes-only --preserve=mode,ownership,xattr -- "$live" "$STAGE/$scope/$rel"
  [ "$(blob "$STAGE/$scope/$rel")" = "$target" ]
done < "$MANIFEST"
echo "BACKUP=$BACKUP"
echo "FILE_BACKUP_AND_METADATA=PASS"

SERVICE_STOP_ATTEMPTED=1
timeout "${SERVICE_TIMEOUT_SECONDS}s" systemctl stop "$SERVICE"
service_stopped
assert_db_quiescent
protected_ok
live_matches base
"$PYTHON" -B "$STAGE/db_ops.py" snapshot "$DB" "$BACKUP/db/auth.db" "$BACKUP/db/baseline.json"
sha256sum "$BACKUP/db/auth.db" > "$BACKUP/db/auth.db.sha256"
echo "DB_BACKUP_VERIFY=PASS"
echo "DB_BACKUP_SHA256=$(cut -d' ' -f1 "$BACKUP/db/auth.db.sha256")"
echo "BACKUP_METADATA=PASS"

INSTALL_STARTED=1
while IFS='|' read -r scope rel base target; do
  live="$(live_path "$scope" "$rel")"
  atomic_copy "$STAGE/$scope/$rel" "$live"
  [ "$(blob "$live")" = "$target" ]
  [ "$(metadata "$live")" = "$(metadata "$BACKUP/files/$scope/$rel")" ]
done < "$MANIFEST"
echo "ATOMIC_INSTALL_AND_METADATA=PASS"
live_matches target
protected_ok
echo "LIVE_TARGET_BLOBS=PASS"
echo "PROTECTED_BASE_AND_IDENT=PASS"
"$PYTHON" -B "$STAGE/db_ops.py" exact "$DB" "$BACKUP/db/baseline.json"
echo "DB_EXACT_UNCHANGED_BEFORE_START=PASS"
assert_db_quiescent
timeout "${SERVICE_TIMEOUT_SECONDS}s" systemctl start "$SERVICE"
wait_health
echo "SERVICE_HEALTH_POST=PASS"
live_matches target
protected_ok
"$PYTHON" -B "$STAGE/db_ops.py" post "$DB" "$BACKUP/db/baseline.json"
echo "LIVE_TARGET_BLOBS_POST=PASS"
echo "DB_EXISTING_SCHEMA_POST=PASS"

SUCCESS=1
INSTALL_STARTED=0
echo "=== FINAL ==="
echo "PAID_PERIODS_PRODUCTION_DEPLOY=PASS"
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "BACKUP=$BACKUP"
echo "SERVICE=active"
echo "HEALTH=ok"
echo "CHANGED_FILES=ONLY_6_PAID_PERIOD_RUNTIME_FILES"
echo "DB_WRITES_BY_INSTALLER=NONE"
echo "LIVE_SCHEMA_EXECUTION_BY_INSTALLER=NONE"
echo "DB_RESTORE=NOT_PERFORMED"
echo "SERVICE_RESTART=STOP_START_COMPLETED"
