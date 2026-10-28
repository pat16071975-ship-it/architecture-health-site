#!/usr/bin/env bash
set -Eeuo pipefail

REPO='pat16071975-ship-it/architecture-health-site'
TARGET_COMMIT='6e36e29e2dff276e42f26f679964de2384727f9b'
EXPECTED_HOST='az-server'
APP_ROOT='/opt/az-baze-auth'
SERVICE='az-baze-auth'
HEALTH_URL='http://127.0.0.1:8001/health'
BACKUP_ROOT='/var/lib/az-baze/backups'
REL='app.py'
BASE_BLOB='40cab9120d48993b8c0b774289756f376e13b6ea'
TARGET_BLOB='ae5053a008ffb106f7286b73fc9218c7afdf7cde'
CONFIRM_TOKEN='HIDE-MOBILE-LOGOUT-6E36E29E'

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

LIVE="$APP_ROOT/$REL"
[ -f "$LIVE" ] || { echo 'STOP: LIVE_APP_MISSING'; exit 1; }

blob(){ git hash-object "$1"; }

LIVE_BLOB="$(blob "$LIVE")"
if [ "$LIVE_BLOB" = "$BASE_BLOB" ]; then
  LIVE_STATE='BASE'
elif [ "$LIVE_BLOB" = "$TARGET_BLOB" ]; then
  LIVE_STATE='TARGET'
else
  echo 'STOP: LIVE_APP_BLOB_MISMATCH'
  echo "ACTUAL=$LIVE_BLOB"
  echo "EXPECTED_BASE=$BASE_BLOB"
  echo "EXPECTED_TARGET=$TARGET_BLOB"
  exit 1
fi
echo "LIVE_APP_STATE=$LIVE_STATE"
echo "LIVE_APP_BLOB=$LIVE_BLOB"

systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_NOT_ACTIVE'; exit 1; }
[ "$(curl -fsS "$HEALTH_URL")" = 'ok' ] || { echo 'STOP: HEALTH_PRE_FAIL'; exit 1; }
echo 'SERVICE_HEALTH_PRE=PASS'

STAGE="$(mktemp -d /tmp/az-hide-mobile-logout.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
TARGET="$STAGE/app.py"

curl -fsSL --retry 2 --connect-timeout 5 --max-time 30   "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/az-baze-auth/$REL"   -o "$TARGET"

[ "$(blob "$TARGET")" = "$TARGET_BLOB" ] || { echo 'STOP: TARGET_BLOB_FAIL'; exit 1; }
echo 'TARGET_APP_BLOB=PASS'

"$APP_ROOT/venv/bin/python" -m py_compile "$TARGET"
echo 'PYTHON_SYNTAX=PASS'

"$APP_ROOT/venv/bin/python" - "$TARGET" <<'PY'
from pathlib import Path
import sys

source=Path(sys.argv[1]).read_text(encoding="utf-8")
required=[
    'class="az-home-logout"',
    '@media(max-width:700px){.az-home-logout{display:none!important}}',
    'style="position:fixed;left:18px;bottom:18px;z-index:50"',
    '>Выйти</button></form>',
    '@app.post("/logout")',
]
for marker in required:
    if marker not in source:
        raise SystemExit("STOP: MOBILE_LOGOUT_CONTRACT_MISSING="+marker)

if source.count('class="az-home-logout"') != 1:
    raise SystemExit("STOP: MOBILE_LOGOUT_CLASS_COUNT")
if source.count('@media(max-width:700px){.az-home-logout{display:none!important}}') != 1:
    raise SystemExit("STOP: MOBILE_LOGOUT_CSS_COUNT")
print("MOBILE_LOGOUT_CONTRACT=PASS")
print("DESKTOP_LOGOUT_PRESERVED=PASS")
PY

if [ "$MODE" = '--preflight' ]; then
  echo '=== FINAL ==='
  echo 'HIDE_MOBILE_LOGOUT_PREFLIGHT=PASS'
  echo 'LIVE_CHANGES=NONE'
  echo 'DATABASE_CONNECTIONS=NONE'
  echo 'DATABASE_CHANGES=NONE'
  exit 0
fi

if [ "$LIVE_STATE" = 'TARGET' ]; then
  echo '=== FINAL ==='
  echo 'HIDE_MOBILE_LOGOUT_DEPLOY=PASS'
  echo 'REUSED=TRUE'
  echo 'CHANGED_RUNTIME_FILES=0'
  echo 'DATABASE_CONNECTIONS=NONE'
  echo 'DATABASE_CHANGES=NONE'
  echo 'SERVICE=active'
  echo 'HEALTH=ok'
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_ROOT/hide-mobile-logout-$STAMP"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"

cp -a "$LIVE" "$BACKUP/app.py"
[ "$(blob "$BACKUP/app.py")" = "$BASE_BLOB" ] || { echo 'STOP: FILE_BACKUP_FAIL'; exit 1; }
echo 'FILE_BACKUP=PASS'
echo "BACKUP=$BACKUP"

LIVE_UID="$(stat -c %u "$LIVE")"
LIVE_GID="$(stat -c %g "$LIVE")"
LIVE_MODE="$(stat -c %a "$LIVE")"

rollback(){
  set +e
  echo 'ROLLBACK=START'
  systemctl stop "$SERVICE" >/dev/null 2>&1
  install -o "$LIVE_UID" -g "$LIVE_GID" -m "$LIVE_MODE" "$BACKUP/app.py" "$LIVE"
  systemctl start "$SERVICE" >/dev/null 2>&1
  sleep 2
  good=1
  [ "$(blob "$LIVE" 2>/dev/null)" = "$BASE_BLOB" ] || good=0
  systemctl is-active --quiet "$SERVICE" || good=0
  [ "$(curl -fsS "$HEALTH_URL" 2>/dev/null)" = 'ok' ] || good=0
  if [ "$good" -eq 1 ]; then echo 'ROLLBACK=PASS'; else echo 'ROLLBACK=FAIL'; fi
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then rollback; fi; rm -rf "$STAGE"; exit "$rc"' EXIT

systemctl stop "$SERVICE"
systemctl is-active --quiet "$SERVICE" && { echo 'STOP: SERVICE_DID_NOT_STOP'; exit 1; } || true
echo 'SERVICE_STOP_FOR_CONTROLLED_APPLY=PASS'

install -o "$LIVE_UID" -g "$LIVE_GID" -m "$LIVE_MODE" "$TARGET" "$LIVE"
[ "$(blob "$LIVE")" = "$TARGET_BLOB" ] || { echo 'STOP: LIVE_INSTALL_FAIL'; exit 1; }
echo 'RUNTIME_FILE_INSTALL=PASS'

systemctl start "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_RESTART_FAIL'; exit 1; }
[ "$(curl -fsS "$HEALTH_URL")" = 'ok' ] || { echo 'STOP: HEALTH_POST_FAIL'; exit 1; }
[ "$(blob "$LIVE")" = "$TARGET_BLOB" ] || { echo 'STOP: POST_LIVE_BLOB_FAIL'; exit 1; }

trap - EXIT
rm -rf "$STAGE"

echo '=== FINAL ==='
echo 'HIDE_MOBILE_LOGOUT_DEPLOY=PASS'
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "TARGET_BLOB=$TARGET_BLOB"
echo "BACKUP=$BACKUP"
echo 'CHANGED_RUNTIME_FILES=1'
echo 'DATABASE_CONNECTIONS=NONE'
echo 'DATABASE_CHANGES=NONE'
echo 'SERVICE=active'
echo 'HEALTH=ok'
