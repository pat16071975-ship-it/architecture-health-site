#!/usr/bin/env bash
set -Eeuo pipefail

# Owner-approved one-line fix. Installs only templates/base.html.
# No Git checkout, database mutation, migration, or business-data import.
SOURCE_COMMIT="4306c3bd1988fd284b3e8af1c19cf05667c0d1d6"
BASE_BLOB="3501883c68f1afa7ff32df267bce4a0d00fd9d96"
TARGET_BLOB="c19bbc4660519f3c47a9dd15ca15488247df4d2b"
TARGET_SHA256="1ca1aeada7ec56cdc2292818427e38ce359ee221faca102937fbcd177c763a1e"
UPLOADS_BLOB="53d0ee65ad795a97ed7a7140341864630a194069"
TOKEN="PAID-UPLOAD-OVERLAY-C19BBC46"
APP_ROOT="/opt/az-baze-auth"
DB="/var/lib/az-baze/auth.db"
BACKUP_ROOT="/var/lib/az-baze/backups"
LOCK_FILE="/run/lock/az-baze-paid-overlay.lock"
SERVICE="az-baze-auth"
HEALTH_URL="http://127.0.0.1:8001/health"
LOGIN_URL="http://127.0.0.1:8001/login?next=/uploads/"
HEALTH_WAIT_SECONDS=35
RESTART_TIMEOUT_SECONDS=45
MODE="${1:-}"
case "$MODE" in
  --preflight) if [ "$#" -ne 1 ]; then echo "STOP: INVALID_ARGUMENTS"; exit 2; fi ;;
  --apply)
    if [ "$#" -ne 2 ] || [ "$2" != "$TOKEN" ]; then
      echo "STOP: CONFIRM_TOKEN_REQUIRED"; exit 2
    fi ;;
  *) echo "Usage: $0 --preflight | --apply $TOKEN"; exit 2 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo "STOP: ROOT_REQUIRED"; exit 1; }
[ "$(hostname -s)" = "az-server" ] || { echo "STOP: WRONG_HOST"; exit 1; }
for dependency in curl git systemctl python3 timeout flock sha256sum; do
  command -v "$dependency" >/dev/null
done
LIVE="$APP_ROOT/templates/base.html"
UPLOADS="$APP_ROOT/templates/uploads.html"
PYTHON="$APP_ROOT/venv/bin/python"
if [ ! -f "$LIVE" ] || [ -L "$LIVE" ] || [ ! -x "$PYTHON" ] || [ ! -f "$DB" ]; then
  echo "STOP: PRODUCTION_PATHS_MISSING_OR_UNEXPECTED"; exit 1
fi
[ "$(readlink -f "$LIVE")" = "$LIVE" ]
[ "$(git hash-object "$UPLOADS")" = "$UPLOADS_BLOB" ] || { echo "STOP: UPLOADS_BASE_MISMATCH"; exit 1; }

STAGE="$(mktemp -d /tmp/az-paid-overlay.XXXXXX)"
BACKUP=""
INSTALL_STARTED=0
trap 'rm -rf -- "$STAGE"' EXIT

health_ok() {
  [ "$(curl -fsS --connect-timeout 3 --max-time 4 "$HEALTH_URL")" = "ok" ]
}
wait_health() {
  local deadline=$((SECONDS + HEALTH_WAIT_SECONDS))
  while (( SECONDS < deadline )); do
    if systemctl is-active --quiet "$SERVICE" && health_ok; then return 0; fi
    sleep 1
  done
  return 1
}
rendered_target_ok() {
  curl -fsS --connect-timeout 3 --max-time 8 -H 'Host: az-baze.ru' "$LOGIN_URL" -o "$STAGE/login.html"
  python3 - "$STAGE/login.html" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text(encoding='utf-8')
needle = "if (form.matches('[data-az-upload-form],[data-az-cash-upload-form],[data-az-paid-upload-form]')) return;"
assert needle in text, 'Rendered login template does not contain the approved fix'
PY
}
show_months() {
  python3 - "$DB" <<'PY'
import json, sqlite3, sys
with sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True, timeout=5) as conn:
    conn.execute('PRAGMA query_only=ON')
    rows = conn.execute('SELECT month, COUNT(*) FROM service_payment_snapshots GROUP BY month ORDER BY month').fetchall()
    print('MIS_SNAPSHOTS_READONLY=' + json.dumps(rows, ensure_ascii=False))
PY
}
atomic_copy() {
  python3 - "$1" "$LIVE" <<'PY'
from pathlib import Path
import os, shutil, stat, sys, tempfile
source, target = map(Path, sys.argv[1:])
assert target.is_file() and not target.is_symlink()
metadata = source.stat()
fd, name = tempfile.mkstemp(prefix='.base-paid-overlay-', dir=target.parent)
try:
    with os.fdopen(fd, 'wb') as handle, source.open('rb') as original:
        shutil.copyfileobj(original, handle)
        handle.flush()
        os.fchown(handle.fileno(), metadata.st_uid, metadata.st_gid)
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
rollback() {
  local code=$?
  (( code != 0 )) || code=1
  trap - ERR INT TERM
  if [ "$INSTALL_STARTED" -eq 1 ]; then
    echo "LOADER_OVERLAY_ROLLBACK=START"
    set +e
    atomic_copy "$BACKUP/base.html"
    local restore_code=$?
    local restored_blob
    restored_blob="$(git hash-object "$LIVE")"
    if [ "$restore_code" -eq 0 ] && [ "$restored_blob" = "$BASE_BLOB" ] && \
       timeout "${RESTART_TIMEOUT_SECONDS}s" systemctl restart "$SERVICE" && wait_health; then
      echo "LOADER_OVERLAY_ROLLBACK=PASS"
    else
      echo "LOADER_OVERLAY_ROLLBACK=FAIL"
      echo "BACKUP=$BACKUP"
    fi
  fi
  echo "LOADER_OVERLAY_PRODUCTION_DEPLOY=FAIL"
  exit "$code"
}

echo "=== AZ-BAZE PAID-UPLOAD OVERLAY ONE-FILE PREFLIGHT ==="
echo "SOURCE_COMMIT=$SOURCE_COMMIT"
systemctl is-active --quiet "$SERVICE"
health_ok
echo "SERVICE_HEALTH_PRE=PASS"
CURRENT_BLOB="$(git hash-object "$LIVE")"
if [ "$CURRENT_BLOB" = "$TARGET_BLOB" ]; then
  rendered_target_ok
  show_months
  echo "ALREADY_INSTALLED=PASS"
  echo "LOADER_OVERLAY_PRODUCTION_DEPLOY=PASS"
  exit 0
fi
[ "$CURRENT_BLOB" = "$BASE_BLOB" ] || { echo "STOP: LIVE_BASE_MISMATCH $CURRENT_BLOB"; exit 1; }
echo "LIVE_BASE_GUARD=PASS"

curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 \
  "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$SOURCE_COMMIT/az-baze-auth/templates/base.html" -o "$STAGE/base.html"
[ "$(git hash-object "$STAGE/base.html")" = "$TARGET_BLOB" ]
[ "$(sha256sum "$STAGE/base.html" | cut -d ' ' -f 1)" = "$TARGET_SHA256" ]
"$PYTHON" - "$LIVE" "$STAGE/base.html" <<'PY'
from pathlib import Path
from jinja2 import Environment
import sys
before, after = [Path(path).read_text(encoding='utf-8') for path in sys.argv[1:]]
old = "if (form.matches('[data-az-upload-form],[data-az-cash-upload-form]')) return;"
new = "if (form.matches('[data-az-upload-form],[data-az-cash-upload-form],[data-az-paid-upload-form]')) return;"
assert before.count(old) == 1
assert after == before.replace(old, new, 1), 'Only the approved one-line delta is allowed'
Environment().parse(after)
PY
echo "TARGET_BLOB_AND_ONE_LINE_CONTRACT=PASS"
show_months
echo "LOADER_OVERLAY_PREFLIGHT=PASS"
[ "$MODE" = "--apply" ] || exit 0

exec 9>"$LOCK_FILE"
flock -n 9 || { echo "STOP: DEPLOY_ALREADY_RUNNING"; exit 1; }
[ "$(git hash-object "$LIVE")" = "$BASE_BLOB" ]
systemctl is-active --quiet "$SERVICE"
health_ok
mkdir -p "$BACKUP_ROOT"
BACKUP="$(mktemp -d "$BACKUP_ROOT/paid-upload-overlay-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
cp -a -- "$LIVE" "$BACKUP/base.html"
[ "$(git hash-object "$BACKUP/base.html")" = "$BASE_BLOB" ]
printf 'SOURCE_COMMIT=%s\nBASE_BLOB=%s\nTARGET_BLOB=%s\n' "$SOURCE_COMMIT" "$BASE_BLOB" "$TARGET_BLOB" >"$BACKUP/manifest.txt"
echo "BACKUP=$BACKUP"
echo "BACKUP_VERIFY=PASS"

# Preserve live metadata on the staged replacement without replacing its bytes.
cp --attributes-only --preserve=all -- "$LIVE" "$STAGE/base.html"
[ "$(git hash-object "$STAGE/base.html")" = "$TARGET_BLOB" ]
trap rollback ERR INT TERM
INSTALL_STARTED=1
atomic_copy "$STAGE/base.html"
[ "$(git hash-object "$LIVE")" = "$TARGET_BLOB" ]
echo "ATOMIC_INSTALL=PASS"
timeout "${RESTART_TIMEOUT_SECONDS}s" systemctl restart "$SERVICE"
wait_health
echo "SERVICE_HEALTH_POST=PASS"
rendered_target_ok
echo "RENDERED_TEMPLATE_FIX=PASS"
[ "$(git hash-object "$LIVE")" = "$TARGET_BLOB" ]
[ "$(git hash-object "$UPLOADS")" = "$UPLOADS_BLOB" ]
show_months
trap - ERR INT TERM
INSTALL_STARTED=0
echo "LOADER_OVERLAY_PRODUCTION_DEPLOY=PASS"
