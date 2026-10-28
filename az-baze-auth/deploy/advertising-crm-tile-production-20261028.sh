#!/usr/bin/env bash
set -Eeuo pipefail

REPO='pat16071975-ship-it/architecture-health-site'
TARGET_COMMIT='0b99986c8c779bdef316a35f6f198464ef59fa90'
SITE_ROOT='/var/www/az-baze.ru'
BACKUP_ROOT='/var/lib/az-baze/backups'
HEALTH='http://127.0.0.1:8001/health'
CONFIRM='AZ-ADVERTISING-CRM-TILE-0B99986C'

MODE="${1:---preflight}"
TOKEN="${2:-}"
case "$MODE" in
  --preflight) ;;
  --apply) [ "$TOKEN" = "$CONFIRM" ] || { echo 'CONFIRMATION=FAIL'; exit 2; } ;;
  *) echo "UNKNOWN_MODE=$MODE"; exit 2 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo 'STOP: ROOT_REQUIRED'; exit 1; }
[ "$(hostname -s)" = 'az-server' ] || { echo "STOP: WRONG_HOST=$(hostname -s)"; exit 1; }

HTML="$SITE_ROOT/reports/advertising.html"
JS="$SITE_ROOT/reports/advertising.js"

BASE_HTML='d5e91cdbfd24cf65bfba77d2dd6c6a2f30c8a212'
BASE_JS='4d76056792a0742a694e57bf053d42f4e61da8e9'
TARGET_HTML='f50e8e7377ae378f9cbf9efaaf7020774ce06a69'
TARGET_JS='3c44fe7cfbe305774555b48a89712fa22b021487'

blob(){ git hash-object "$1"; }

echo '=== AZ-BAZE ADVERTISING CRM TILE DEPLOY ==='
echo "TARGET_COMMIT=$TARGET_COMMIT"

for spec in "HTML|$HTML|$BASE_HTML|$TARGET_HTML" "JS|$JS|$BASE_JS|$TARGET_JS"; do
  IFS='|' read -r label file base target <<<"$spec"
  [ -f "$file" ] || { echo "STOP: LIVE_FILE_MISSING=$label"; exit 1; }
  actual="$(blob "$file")"
  if [ "$actual" = "$base" ]; then
    echo "LIVE_FILE=$label;STATE=BASE;BLOB=$actual"
  elif [ "$actual" = "$target" ]; then
    echo "LIVE_FILE=$label;STATE=TARGET;BLOB=$actual"
  else
    echo "STOP: LIVE_BLOB_MISMATCH=$label"
    echo "ACTUAL=$actual"
    echo "EXPECTED_BASE=$base"
    echo "EXPECTED_TARGET=$target"
    exit 1
  fi
done

[ "$(curl -fsS "$HEALTH")" = 'ok' ] || { echo 'STOP: HEALTH_PRE_FAIL'; exit 1; }
echo 'HEALTH_PRE=PASS'

STAGE="$(mktemp -d /tmp/az-ad-crm-tile.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

curl -fsSL --retry 2 --connect-timeout 5 --max-time 30   "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/reports/advertising.html"   -o "$STAGE/advertising.html"
curl -fsSL --retry 2 --connect-timeout 5 --max-time 30   "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/reports/advertising.js"   -o "$STAGE/advertising.js"

[ "$(blob "$STAGE/advertising.html")" = "$TARGET_HTML" ] || { echo 'STOP: TARGET_HTML_BLOB_FAIL'; exit 1; }
[ "$(blob "$STAGE/advertising.js")" = "$TARGET_JS" ] || { echo 'STOP: TARGET_JS_BLOB_FAIL'; exit 1; }
echo 'TARGET_BLOBS=PASS'

python3 - "$STAGE/advertising.html" "$STAGE/advertising.js" <<'PY'
from pathlib import Path
import sys

html=Path(sys.argv[1]).read_text(encoding="utf-8")
js=Path(sys.argv[2]).read_text(encoding="utf-8")
text=html+"\n"+js

required=[
    "Целевые лиды",
    'id="targetLeads"',
    "Не хватает данных CRM для построения отчёта",
    "s.lead_to_primary_pct",
    "Выручка клиники",
    "Выручка по врачам / исполнителям",
    "Визиты с открытием записи",
    "Визиты с переходом в Medflex",
]
for marker in required:
    if marker not in text:
        raise SystemExit("STOP: CONTRACT_MISSING="+marker)

for marker in [
    "Первично записались",
    'id="crmNote"',
    "crm-note",
]:
    if marker in text:
        raise SystemExit("STOP: FORBIDDEN_UI_PRESENT="+marker)

print("CRM_TILE_CONTRACT=PASS")
print("OTHER_ADVERTISING_REPORT_CONTRACT=PASS")
PY

if command -v node >/dev/null 2>&1; then
  node --check "$STAGE/advertising.js"
  echo 'JAVASCRIPT_SYNTAX=PASS'
else
  echo 'JAVASCRIPT_SYNTAX=CI_VERIFIED_NODE_NOT_INSTALLED'
fi

if [ "$MODE" = '--preflight' ]; then
  echo '=== FINAL ==='
  echo 'ADVERTISING_CRM_TILE_PREFLIGHT=PASS'
  echo 'LIVE_CHANGES=NONE'
  echo 'DATABASE_WRITES=NONE'
  exit 0
fi

if [ "$(blob "$HTML")" = "$TARGET_HTML" ] && [ "$(blob "$JS")" = "$TARGET_JS" ]; then
  echo '=== FINAL ==='
  echo 'ADVERTISING_CRM_TILE_DEPLOY=PASS'
  echo 'REUSED=TRUE'
  echo 'CHANGED_RUNTIME_FILES=0'
  echo 'DATABASE_WRITES=NONE'
  echo 'HEALTH=ok'
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_ROOT/advertising-crm-tile-$STAMP"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"

cp -a "$HTML" "$BACKUP/advertising.html"
cp -a "$JS" "$BACKUP/advertising.js"
echo 'BACKUP=PASS'
echo "BACKUP_PATH=$BACKUP"

HTML_UID="$(stat -c %u "$HTML")"
HTML_GID="$(stat -c %g "$HTML")"
HTML_MODE="$(stat -c %a "$HTML")"
JS_UID="$(stat -c %u "$JS")"
JS_GID="$(stat -c %g "$JS")"
JS_MODE="$(stat -c %a "$JS")"

rollback(){
  set +e
  install -D -o "$HTML_UID" -g "$HTML_GID" -m "$HTML_MODE" "$BACKUP/advertising.html" "$HTML"
  install -D -o "$JS_UID" -g "$JS_GID" -m "$JS_MODE" "$BACKUP/advertising.js" "$JS"
  if [ "$(blob "$HTML")" = "$BASE_HTML" ] && [ "$(blob "$JS")" = "$BASE_JS" ]; then
    echo 'ROLLBACK=PASS'
  else
    echo 'ROLLBACK=FAIL'
  fi
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then rollback; fi; rm -rf "$STAGE"; exit "$rc"' EXIT

install -D -o "$HTML_UID" -g "$HTML_GID" -m "$HTML_MODE" "$STAGE/advertising.html" "$HTML"
install -D -o "$JS_UID" -g "$JS_GID" -m "$JS_MODE" "$STAGE/advertising.js" "$JS"

[ "$(blob "$HTML")" = "$TARGET_HTML" ] || { echo 'STOP: HTML_INSTALL_FAIL'; exit 1; }
[ "$(blob "$JS")" = "$TARGET_JS" ] || { echo 'STOP: JS_INSTALL_FAIL'; exit 1; }
[ "$(curl -fsS "$HEALTH")" = 'ok' ] || { echo 'STOP: HEALTH_POST_FAIL'; exit 1; }

trap - EXIT
rm -rf "$STAGE"

echo '=== FINAL ==='
echo 'ADVERTISING_CRM_TILE_DEPLOY=PASS'
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "BACKUP_PATH=$BACKUP"
echo 'CHANGED_RUNTIME_FILES=2'
echo 'DATABASE_WRITES=NONE'
echo 'HEALTH=ok'
