#!/usr/bin/env bash
set -Eeuo pipefail

REPO='pat16071975-ship-it/architecture-health-site'
TARGET_COMMIT='3f52cd6c290f316e902496b30571a0235bebb734'
CONFIRM_TOKEN='MANAGEMENT-DATE-SYNC-3F52CD6C'
EXPECTED_HOST='az-server'
APP_ROOT='/opt/az-baze-auth'
LIVE="$APP_ROOT/report_storage.py"
SERVICE='az-baze-auth'
HEALTH_URL='http://127.0.0.1:8001/health'
BACKUP_ROOT='/var/lib/az-baze/backups'
BASE_BLOB='0e50ed2827b0257107caffe8b8a4d71999b76a17'
TARGET_BLOB='a6edef274755a18fb9e17b2841bb124647bb159b'

MODE="${1:---preflight}"
CONFIRM="${2:-}"
case "$MODE" in
  --preflight) ;;
  --apply) [ "$CONFIRM" = "$CONFIRM_TOKEN" ] || { echo 'CONFIRMATION=FAIL'; exit 2; } ;;
  *) echo "UNKNOWN_MODE=$MODE"; exit 2 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo 'STOP: ROOT_REQUIRED'; exit 1; }
[ "$(hostname -s)" = "$EXPECTED_HOST" ] || { echo "STOP: WRONG_HOST=$(hostname -s)"; exit 1; }
command -v curl >/dev/null
command -v git >/dev/null
command -v systemctl >/dev/null
[ -x "$APP_ROOT/venv/bin/python" ] || { echo 'STOP: VENV_PYTHON_MISSING'; exit 1; }
[ -f "$LIVE" ] || { echo 'STOP: LIVE_FILE_MISSING'; exit 1; }

blob(){ git hash-object "$1"; }

ACTUAL="$(blob "$LIVE")"
if [ "$ACTUAL" = "$TARGET_BLOB" ]; then
  LIVE_STATE='TARGET_ALREADY'
elif [ "$ACTUAL" = "$BASE_BLOB" ]; then
  LIVE_STATE='BASE'
else
  echo 'STOP: LIVE_BLOB_MISMATCH'
  echo "EXPECTED_BASE=$BASE_BLOB"
  echo "EXPECTED_TARGET=$TARGET_BLOB"
  echo "ACTUAL=$ACTUAL"
  exit 1
fi
echo "LIVE_STATE=$LIVE_STATE"

systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_NOT_ACTIVE'; exit 1; }
[ "$(curl -fsS "$HEALTH_URL")" = 'ok' ] || { echo 'STOP: HEALTH_PRE_FAIL'; exit 1; }
echo 'SERVICE_HEALTH_PRE=PASS'

STAGE="$(mktemp -d /tmp/az-management-date-sync.XXXXXX)"
TARGET="$STAGE/report_storage.py"
trap 'rm -rf "$STAGE"' EXIT

curl -fsSL --retry 2 --connect-timeout 5 --max-time 30   "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/az-baze-auth/report_storage.py"   -o "$TARGET"

[ "$(blob "$TARGET")" = "$TARGET_BLOB" ] || { echo 'STOP: TARGET_BLOB_FAIL'; exit 1; }
echo 'TARGET_BLOB=PASS'

"$APP_ROOT/venv/bin/python" -m py_compile "$TARGET"
echo 'PYTHON_SYNTAX=PASS'

"$APP_ROOT/venv/bin/python" - "$TARGET" <<'PY'
from pathlib import Path
import sys
text=Path(sys.argv[1]).read_text(encoding='utf-8')
needle="if(dates.length)$('#reportDate').value=dates[dates.length-1];$('#reportDate').dispatchEvent(new Event('change'))}catch(error)"
assert text.count(needle)==1
assert "SELECT date, payload FROM report_data ORDER BY date" in text
assert 'record["plan"] = monthly_plans[month]' in text
print('DATE_SYNC_CONTRACT=PASS')
PY

if [ "$MODE" = '--preflight' ]; then
  echo '=== FINAL ==='
  echo 'MANAGEMENT_DATE_SYNC_PREFLIGHT=PASS'
  echo 'LIVE_CHANGES=NONE'
  exit 0
fi

if [ "$LIVE_STATE" = 'TARGET_ALREADY' ]; then
  echo '=== FINAL ==='
  echo 'MANAGEMENT_DATE_SYNC_DEPLOY=PASS'
  echo 'REUSED=TRUE'
  echo 'CHANGED_RUNTIME_FILES=0'
  echo 'DB_WRITES=NONE_BY_DEPLOY'
  echo 'QNAP_WRITES=NONE'
  echo 'WORKER_CHANGES=NONE'
  exit 0
fi

BACKUP="$BACKUP_ROOT/management-date-sync-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"
cp -a "$LIVE" "$BACKUP/report_storage.py"
[ "$(blob "$BACKUP/report_storage.py")" = "$BASE_BLOB" ] || { echo 'STOP: BACKUP_BLOB_FAIL'; exit 1; }
echo 'FILE_BACKUP=PASS'
echo "BACKUP=$BACKUP"

uid="$(stat -c %u "$LIVE")"
gid="$(stat -c %g "$LIVE")"
mode="$(stat -c %a "$LIVE")"

rollback(){
  set +e
  install -o "$uid" -g "$gid" -m "$mode" "$BACKUP/report_storage.py" "$LIVE"
  systemctl restart "$SERVICE"
  sleep 2
  if [ "$(blob "$LIVE" 2>/dev/null)" = "$BASE_BLOB" ]      && systemctl is-active --quiet "$SERVICE"      && [ "$(curl -fsS "$HEALTH_URL" 2>/dev/null)" = 'ok' ]; then
    echo 'ROLLBACK=PASS'
  else
    echo 'ROLLBACK=FAIL'
  fi
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then rollback; fi; rm -rf "$STAGE"; exit "$rc"' EXIT

install -o "$uid" -g "$gid" -m "$mode" "$TARGET" "$LIVE"
[ "$(blob "$LIVE")" = "$TARGET_BLOB" ] || { echo 'STOP: LIVE_TARGET_BLOB_FAIL'; exit 1; }
echo 'ATOMIC_FILE_INSTALL=PASS'

systemctl restart "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_RESTART_FAIL'; exit 1; }
[ "$(curl -fsS "$HEALTH_URL")" = 'ok' ] || { echo 'STOP: HEALTH_POST_FAIL'; exit 1; }
[ "$(blob "$LIVE")" = "$TARGET_BLOB" ] || { echo 'STOP: LIVE_TARGET_POST_FAIL'; exit 1; }

trap - EXIT
rm -rf "$STAGE"

echo '=== FINAL ==='
echo 'MANAGEMENT_DATE_SYNC_DEPLOY=PASS'
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "TARGET_BLOB=$TARGET_BLOB"
echo "BACKUP=$BACKUP"
echo 'SERVICE=active'
echo 'HEALTH=ok'
echo 'CHANGED_RUNTIME_FILES=1'
echo 'DB_WRITES=NONE_BY_DEPLOY'
echo 'QNAP_WRITES=NONE'
echo 'WORKER_CHANGES=NONE'
