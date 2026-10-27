#!/usr/bin/env bash
set -Eeuo pipefail

REPO='pat16071975-ship-it/architecture-health-site'
SOURCE_HEAD='5ee3a69293512c4a7b4465f84732040cae6caed8'
BASE_BLOB='1211888359da609778dc0a479399ccd88157ea3b'
TARGET_BLOB='ebd8310c480e20de9a9c60f7c309786a28151ffc'
CONFIRM_TOKEN='KNOWLEDGE-VIDEO-ROTATE-5EE3A692'
APP_ROOT='/opt/az-baze-auth'
LIVE="$APP_ROOT/templates/knowledge_view.html"
SERVICE='az-baze-auth'
HEALTH_URL='http://127.0.0.1:8001/health'
EXPECTED_HOST='az-server'
BACKUP_ROOT='/var/lib/az-baze/backups'

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
[ -f "$LIVE" ] || { echo 'STOP: LIVE_TEMPLATE_MISSING'; exit 1; }

STAGE="$(mktemp -d /tmp/az-video-rotate.XXXXXX)"
TARGET="$STAGE/knowledge_view.html"
trap 'rm -rf "$STAGE"' EXIT

live_blob="$(git hash-object "$LIVE")"
if [ "$live_blob" = "$TARGET_BLOB" ]; then
  echo 'LIVE_STATE=TARGET_ALREADY'
elif [ "$live_blob" = "$BASE_BLOB" ]; then
  echo 'LIVE_STATE=BASE'
else
  echo 'STOP: LIVE_TEMPLATE_BLOB_MISMATCH'
  echo "EXPECTED_BASE=$BASE_BLOB"
  echo "EXPECTED_TARGET=$TARGET_BLOB"
  echo "ACTUAL=$live_blob"
  exit 1
fi

systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_NOT_ACTIVE'; exit 1; }
[ "$(curl -fsS "$HEALTH_URL")" = 'ok' ] || { echo 'STOP: HEALTH_PRE_FAIL'; exit 1; }
echo 'SERVICE_HEALTH_PRE=PASS'

curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 \
  "https://raw.githubusercontent.com/$REPO/$SOURCE_HEAD/az-baze-auth/templates/knowledge_view.html" \
  -o "$TARGET"
[ "$(git hash-object "$TARGET")" = "$TARGET_BLOB" ] || { echo 'STOP: TARGET_BLOB_MISMATCH'; exit 1; }
echo 'TARGET_BLOB=PASS'

"$APP_ROOT/venv/bin/python" - "$TARGET" <<'PY'
from pathlib import Path
import sys
from jinja2 import Environment
p=Path(sys.argv[1])
text=p.read_text(encoding='utf-8')
Environment().parse(text)
assert text.count('id="videoRotateButton"') == 1
assert 'rotation = (rotation + 90) % 360' in text
assert "video.style.transform = 'rotate(' + rotation + 'deg)'" in text
assert 'const fitVideo = () =>' in text
assert "video.playbackRate = rate" in text
assert 'video.currentTime =' not in text
assert 'video.load()' not in text
print('ROTATION_CONTRACT=PASS')
PY

if [ "$MODE" = '--preflight' ]; then
  echo '=== FINAL ==='
  echo 'KNOWLEDGE_VIDEO_ROTATE_PREFLIGHT=PASS'
  echo 'LIVE_CHANGES=NONE'
  exit 0
fi

if [ "$live_blob" = "$TARGET_BLOB" ]; then
  echo '=== FINAL ==='
  echo 'KNOWLEDGE_VIDEO_ROTATE_DEPLOY=PASS'
  echo 'REUSED=TRUE'
  echo 'CHANGED_RUNTIME_FILES=0'
  echo 'DB_CHANGES=NONE'
  echo 'QNAP_WRITES=NONE'
  echo 'WORKER_CHANGES=NONE'
  exit 0
fi

BACKUP="$BACKUP_ROOT/knowledge-video-rotate-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"
cp -a "$LIVE" "$BACKUP/knowledge_view.html"
[ "$(git hash-object "$BACKUP/knowledge_view.html")" = "$BASE_BLOB" ] || { echo 'STOP: BACKUP_BLOB_FAIL'; exit 1; }
echo 'FILE_BACKUP=PASS'
echo "BACKUP=$BACKUP"

uid="$(stat -c %u "$LIVE")"
gid="$(stat -c %g "$LIVE")"
mode="$(stat -c %a "$LIVE")"

rollback(){
  set +e
  install -o "$uid" -g "$gid" -m "$mode" "$BACKUP/knowledge_view.html" "$LIVE"
  systemctl restart "$SERVICE"
  sleep 2
  if [ "$(git hash-object "$LIVE" 2>/dev/null)" = "$BASE_BLOB" ] \
     && systemctl is-active --quiet "$SERVICE" \
     && [ "$(curl -fsS "$HEALTH_URL" 2>/dev/null)" = 'ok' ]; then
    echo 'ROLLBACK=PASS'
  else
    echo 'ROLLBACK=FAIL'
  fi
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then rollback; fi; rm -rf "$STAGE"; exit "$rc"' EXIT

install -o "$uid" -g "$gid" -m "$mode" "$TARGET" "$LIVE"
[ "$(git hash-object "$LIVE")" = "$TARGET_BLOB" ] || { echo 'STOP: LIVE_TARGET_BLOB_FAIL'; exit 1; }
echo 'ATOMIC_FILE_INSTALL=PASS'

systemctl restart "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_RESTART_FAIL'; exit 1; }
[ "$(curl -fsS "$HEALTH_URL")" = 'ok' ] || { echo 'STOP: HEALTH_POST_FAIL'; exit 1; }
[ "$(git hash-object "$LIVE")" = "$TARGET_BLOB" ] || { echo 'STOP: LIVE_TARGET_POST_FAIL'; exit 1; }

trap - EXIT
rm -rf "$STAGE"
echo '=== FINAL ==='
echo 'KNOWLEDGE_VIDEO_ROTATE_DEPLOY=PASS'
echo "SOURCE_HEAD=$SOURCE_HEAD"
echo "TARGET_BLOB=$TARGET_BLOB"
echo "BACKUP=$BACKUP"
echo 'SERVICE=active'
echo 'HEALTH=ok'
echo 'CHANGED_RUNTIME_FILES=1'
echo 'DB_CHANGES=NONE'
echo 'QNAP_WRITES=NONE'
echo 'WORKER_CHANGES=NONE'
