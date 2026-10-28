#!/usr/bin/env bash
set -Eeuo pipefail

REPO='pat16071975-ship-it/architecture-health-site'
TARGET_COMMIT='5b41b25bf9d9c6ac6bb4aea623e397699da548cc'
EXPECTED_HOST='az-server'
APP_ROOT='/opt/az-baze-auth'
SERVICE='az-baze-auth'
HEALTH_URL='http://127.0.0.1:8001/health'
BACKUP_ROOT='/var/lib/az-baze/backups'
CONFIRM_TOKEN='CLINICAL-UNRESOLVED-NONBLOCKING-5B41B25B'

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

declare -A REL BASE TARGET
REL[daily_upload.py]='daily_upload.py'
BASE[daily_upload.py]='c6291e649c0d40fbae457572349cd2f2967f14bc'
TARGET[daily_upload.py]='01b3f8abfcf4f7e55309893bc2bf1e2ea6319c07'

REL[uploads.html]='templates/uploads.html'
BASE[uploads.html]='335f2dd2cf6da4e38cdfc4a4c0af9e1455d47352'
TARGET[uploads.html]='1cccae398c664082294080e26cc0cca929877e26'

FILES=(daily_upload.py uploads.html)

blob(){ git hash-object "$1"; }

BASE_COUNT=0
TARGET_COUNT=0
for key in "${FILES[@]}"; do
  live="$APP_ROOT/${REL[$key]}"
  [ -f "$live" ] || { echo "STOP: LIVE_FILE_MISSING=$key"; exit 1; }
  actual="$(blob "$live")"
  if [ "$actual" = "${BASE[$key]}" ]; then
    state='BASE'
    BASE_COUNT=$((BASE_COUNT+1))
  elif [ "$actual" = "${TARGET[$key]}" ]; then
    state='TARGET'
    TARGET_COUNT=$((TARGET_COUNT+1))
  else
    echo "STOP: LIVE_BLOB_MISMATCH=$key"
    echo "ACTUAL=$actual"
    echo "EXPECTED_BASE=${BASE[$key]}"
    echo "EXPECTED_TARGET=${TARGET[$key]}"
    exit 1
  fi
  echo "LIVE_FILE=$key;STATE=$state;BLOB=$actual"
done

if [ "$BASE_COUNT" -eq "${#FILES[@]}" ]; then
  LIVE_STATE='BASE'
elif [ "$TARGET_COUNT" -eq "${#FILES[@]}" ]; then
  LIVE_STATE='TARGET'
else
  echo "STOP: MIXED_LIVE_STATE;BASE=$BASE_COUNT;TARGET=$TARGET_COUNT"
  exit 1
fi
echo "LIVE_STATE=$LIVE_STATE"

systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_NOT_ACTIVE'; exit 1; }
[ "$(curl -fsS "$HEALTH_URL")" = 'ok' ] || { echo 'STOP: HEALTH_PRE_FAIL'; exit 1; }
echo 'SERVICE_HEALTH_PRE=PASS'

STAGE="$(mktemp -d /tmp/az-clinical-unresolved-nonblocking.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

for key in "${FILES[@]}"; do
  rel="${REL[$key]}"
  target="$STAGE/$key"
  curl -fsSL --retry 2 --connect-timeout 5 --max-time 30     "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/az-baze-auth/$rel"     -o "$target"
  [ "$(blob "$target")" = "${TARGET[$key]}" ] || { echo "STOP: TARGET_BLOB_FAIL=$key"; exit 1; }
  echo "TARGET_FILE=$key;BLOB=PASS"
done

"$APP_ROOT/venv/bin/python" -m py_compile "$STAGE/daily_upload.py"
echo 'PYTHON_SYNTAX=PASS'

"$APP_ROOT/venv/bin/python" - "$STAGE/daily_upload.py" "$STAGE/uploads.html" <<'PY'
from pathlib import Path
from jinja2 import Environment
import sys

daily=Path(sys.argv[1]).read_text(encoding="utf-8")
html=Path(sys.argv[2]).read_text(encoding="utf-8")
Environment().parse(html)

required_daily=[
    "_visit_direction_rows",
    "_apply_visit_direction_rows",
    "unresolved_visits, resolved_visit_directions",
    "Нераспределённое направление не блокирует обычную клиническую загрузку",
]
for marker in required_daily:
    if marker not in daily:
        raise SystemExit("STOP: DAILY_CONTRACT_MISSING="+marker)

forbidden_daily=[
    "Для каждой строки выберите «Стоматология», «Клиника»",
]
for marker in forbidden_daily:
    if marker in daily:
        raise SystemExit("STOP: BLOCKING_GATE_STILL_PRESENT="+marker)

required_html=[
    "Можно продолжить загрузку без выбора",
    "continueBtn.disabled=unknown.length>0&&!payload.can_replace",
    "if(select.value)result[select.dataset.visitDirection]=select.value",
    "data-visit-direction",
    "Оставить нераспределённым",
    'id="reconcileCloseText"',
    "height:100dvh;max-height:100dvh",
]
for marker in required_html:
    if marker not in html:
        raise SystemExit("STOP: UI_CONTRACT_MISSING="+marker)

print("NONBLOCKING_VISIT_CONTRACT=PASS")
print("MOBILE_REVIEW_CONTROLS_PRESERVED=PASS")
PY

if [ "$MODE" = '--preflight' ]; then
  echo '=== FINAL ==='
  echo 'CLINICAL_UNRESOLVED_NONBLOCKING_PREFLIGHT=PASS'
  echo 'LIVE_CHANGES=NONE'
  echo 'DATABASE_CONNECTIONS=NONE'
  echo 'DATABASE_CHANGES=NONE'
  exit 0
fi

if [ "$LIVE_STATE" = 'TARGET' ]; then
  echo '=== FINAL ==='
  echo 'CLINICAL_UNRESOLVED_NONBLOCKING_DEPLOY=PASS'
  echo 'REUSED=TRUE'
  echo 'CHANGED_RUNTIME_FILES=0'
  echo 'DATABASE_CONNECTIONS=NONE'
  echo 'DATABASE_CHANGES=NONE'
  echo 'SERVICE=active'
  echo 'HEALTH=ok'
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_ROOT/clinical-unresolved-nonblocking-$STAMP"
mkdir -p "$BACKUP/files/templates"
chmod 700 "$BACKUP"

declare -A FILE_UID FILE_GID FILE_MODE
for key in "${FILES[@]}"; do
  live="$APP_ROOT/${REL[$key]}"
  cp -a "$live" "$BACKUP/files/${REL[$key]}"
  [ "$(blob "$BACKUP/files/${REL[$key]}")" = "${BASE[$key]}" ] || { echo "STOP: FILE_BACKUP_FAIL=$key"; exit 1; }
  FILE_UID[$key]="$(stat -c %u "$live")"
  FILE_GID[$key]="$(stat -c %g "$live")"
  FILE_MODE[$key]="$(stat -c %a "$live")"
done
echo 'FILE_BACKUP=PASS'
echo "BACKUP=$BACKUP"

rollback(){
  set +e
  echo 'ROLLBACK=START'
  systemctl stop "$SERVICE" >/dev/null 2>&1
  for key in "${FILES[@]}"; do
    live="$APP_ROOT/${REL[$key]}"
    install -D -o "${FILE_UID[$key]}" -g "${FILE_GID[$key]}" -m "${FILE_MODE[$key]}"       "$BACKUP/files/${REL[$key]}" "$live"
  done
  systemctl start "$SERVICE" >/dev/null 2>&1
  sleep 2
  good=1
  for key in "${FILES[@]}"; do
    [ "$(blob "$APP_ROOT/${REL[$key]}" 2>/dev/null)" = "${BASE[$key]}" ] || good=0
  done
  systemctl is-active --quiet "$SERVICE" || good=0
  [ "$(curl -fsS "$HEALTH_URL" 2>/dev/null)" = 'ok' ] || good=0
  if [ "$good" -eq 1 ]; then echo 'ROLLBACK=PASS'; else echo 'ROLLBACK=FAIL'; fi
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then rollback; fi; rm -rf "$STAGE"; exit "$rc"' EXIT

systemctl stop "$SERVICE"
systemctl is-active --quiet "$SERVICE" && { echo 'STOP: SERVICE_DID_NOT_STOP'; exit 1; } || true
echo 'SERVICE_STOP_FOR_CONTROLLED_APPLY=PASS'

for key in "${FILES[@]}"; do
  live="$APP_ROOT/${REL[$key]}"
  install -D -o "${FILE_UID[$key]}" -g "${FILE_GID[$key]}" -m "${FILE_MODE[$key]}"     "$STAGE/$key" "$live"
  [ "$(blob "$live")" = "${TARGET[$key]}" ] || { echo "STOP: LIVE_INSTALL_FAIL=$key"; exit 1; }
done
echo 'RUNTIME_FILES_INSTALL=PASS'

systemctl start "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_RESTART_FAIL'; exit 1; }
[ "$(curl -fsS "$HEALTH_URL")" = 'ok' ] || { echo 'STOP: HEALTH_POST_FAIL'; exit 1; }

for key in "${FILES[@]}"; do
  [ "$(blob "$APP_ROOT/${REL[$key]}")" = "${TARGET[$key]}" ] || { echo "STOP: POST_LIVE_BLOB_FAIL=$key"; exit 1; }
done

trap - EXIT
rm -rf "$STAGE"

echo '=== FINAL ==='
echo 'CLINICAL_UNRESOLVED_NONBLOCKING_DEPLOY=PASS'
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "BACKUP=$BACKUP"
echo 'CHANGED_RUNTIME_FILES=2'
echo 'DATABASE_CONNECTIONS=NONE'
echo 'DATABASE_CHANGES=NONE'
echo 'SERVICE=active'
echo 'HEALTH=ok'
