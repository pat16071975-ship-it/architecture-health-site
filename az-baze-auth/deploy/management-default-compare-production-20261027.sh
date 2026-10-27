#!/usr/bin/env bash
set -Eeuo pipefail

REPO='pat16071975-ship-it/architecture-health-site'
TARGET_COMMIT='d6568484353d10e703b6fb90ef354c27fb4c5f45'
CONFIRM_TOKEN='MANAGEMENT-DEFAULT-COMPARE-D6568484'
EXPECTED_HOST='az-server'
SITE_ROOT='/var/www/az-baze.ru'
BACKUP_ROOT='/var/lib/az-baze/backups'

INDEX_REL='reports/index.html'
VIEW_REL='reports/period-view.js'
INDEX_BASE_BLOB='ebf7ae4d4e5e121f20cff2302206e9a5fc25e350'
VIEW_BASE_BLOB='a3075abb77f562e8eddbde11364049c4362261e2'
INDEX_TARGET_BLOB='ea68923b8eaf5ac1354c807290fd97ab1a589b55'
VIEW_TARGET_BLOB='68cd5606f0d4a82d378d66d470eb16ea76fbca42'

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
command -v node >/dev/null

INDEX_LIVE="$SITE_ROOT/$INDEX_REL"
VIEW_LIVE="$SITE_ROOT/$VIEW_REL"
[ -f "$INDEX_LIVE" ] || { echo "STOP: LIVE_MISSING=$INDEX_LIVE"; exit 1; }
[ -f "$VIEW_LIVE" ] || { echo "STOP: LIVE_MISSING=$VIEW_LIVE"; exit 1; }

blob(){ git hash-object "$1"; }

INDEX_ACTUAL="$(blob "$INDEX_LIVE")"
VIEW_ACTUAL="$(blob "$VIEW_LIVE")"

if [ "$INDEX_ACTUAL" = "$INDEX_TARGET_BLOB" ] && [ "$VIEW_ACTUAL" = "$VIEW_TARGET_BLOB" ]; then
  LIVE_STATE='TARGET_ALREADY'
elif [ "$INDEX_ACTUAL" = "$INDEX_BASE_BLOB" ] && [ "$VIEW_ACTUAL" = "$VIEW_BASE_BLOB" ]; then
  LIVE_STATE='BASE'
else
  echo 'STOP: LIVE_BLOB_MISMATCH'
  echo "INDEX_ACTUAL=$INDEX_ACTUAL"
  echo "INDEX_EXPECTED_BASE=$INDEX_BASE_BLOB"
  echo "INDEX_EXPECTED_TARGET=$INDEX_TARGET_BLOB"
  echo "VIEW_ACTUAL=$VIEW_ACTUAL"
  echo "VIEW_EXPECTED_BASE=$VIEW_BASE_BLOB"
  echo "VIEW_EXPECTED_TARGET=$VIEW_TARGET_BLOB"
  exit 1
fi
echo "LIVE_STATE=$LIVE_STATE"

STAGE="$(mktemp -d /tmp/az-management-default-compare.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/reports"

curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 \
  "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/$INDEX_REL" \
  -o "$STAGE/$INDEX_REL"
curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 \
  "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/$VIEW_REL" \
  -o "$STAGE/$VIEW_REL"

[ "$(blob "$STAGE/$INDEX_REL")" = "$INDEX_TARGET_BLOB" ] || { echo 'STOP: INDEX_TARGET_BLOB_FAIL'; exit 1; }
[ "$(blob "$STAGE/$VIEW_REL")" = "$VIEW_TARGET_BLOB" ] || { echo 'STOP: VIEW_TARGET_BLOB_FAIL'; exit 1; }
echo 'TARGET_BLOBS=PASS'

node --check "$STAGE/$VIEW_REL"

python3 - "$STAGE/$INDEX_REL" "$STAGE/$VIEW_REL" <<'PY'
from pathlib import Path
import sys
index=Path(sys.argv[1]).read_text(encoding='utf-8')
view=Path(sys.argv[2]).read_text(encoding='utf-8')
assert './period-view.js?v=20261027-default-compare-1' in index
needle='<select id="dateViewMode"><option value="single">Одна дата</option><option value="compare" selected>На эту дату по месяцам</option></select>'
assert needle in view
assert "document.getElementById('dateViewMode').addEventListener('change',syncDateUx)" in view
assert "compare=document.getElementById('dateViewMode').value==='compare'" in view
print('DEFAULT_COMPARE_CONTRACT=PASS')
PY

if [ "$MODE" = '--preflight' ]; then
  echo '=== FINAL ==='
  echo 'MANAGEMENT_DEFAULT_COMPARE_PREFLIGHT=PASS'
  echo 'LIVE_CHANGES=NONE'
  exit 0
fi

if [ "$LIVE_STATE" = 'TARGET_ALREADY' ]; then
  echo '=== FINAL ==='
  echo 'MANAGEMENT_DEFAULT_COMPARE_DEPLOY=PASS'
  echo 'REUSED=TRUE'
  echo 'CHANGED_RUNTIME_FILES=0'
  echo 'DB_CHANGES=NONE'
  echo 'SERVICE_CHANGES=NONE'
  exit 0
fi

BACKUP="$BACKUP_ROOT/management-default-compare-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP/reports"
chmod 700 "$BACKUP"
cp -a "$INDEX_LIVE" "$BACKUP/$INDEX_REL"
cp -a "$VIEW_LIVE" "$BACKUP/$VIEW_REL"
[ "$(blob "$BACKUP/$INDEX_REL")" = "$INDEX_BASE_BLOB" ] || { echo 'STOP: INDEX_BACKUP_BLOB_FAIL'; exit 1; }
[ "$(blob "$BACKUP/$VIEW_REL")" = "$VIEW_BASE_BLOB" ] || { echo 'STOP: VIEW_BACKUP_BLOB_FAIL'; exit 1; }
echo 'FILE_BACKUP=PASS'
echo "BACKUP=$BACKUP"

INDEX_UID="$(stat -c %u "$INDEX_LIVE")"; INDEX_GID="$(stat -c %g "$INDEX_LIVE")"; INDEX_MODE="$(stat -c %a "$INDEX_LIVE")"
VIEW_UID="$(stat -c %u "$VIEW_LIVE")"; VIEW_GID="$(stat -c %g "$VIEW_LIVE")"; VIEW_MODE="$(stat -c %a "$VIEW_LIVE")"

rollback(){
  set +e
  install -o "$INDEX_UID" -g "$INDEX_GID" -m "$INDEX_MODE" "$BACKUP/$INDEX_REL" "$INDEX_LIVE"
  install -o "$VIEW_UID" -g "$VIEW_GID" -m "$VIEW_MODE" "$BACKUP/$VIEW_REL" "$VIEW_LIVE"
  if [ "$(blob "$INDEX_LIVE" 2>/dev/null)" = "$INDEX_BASE_BLOB" ] \
     && [ "$(blob "$VIEW_LIVE" 2>/dev/null)" = "$VIEW_BASE_BLOB" ]; then
    echo 'ROLLBACK=PASS'
  else
    echo 'ROLLBACK=FAIL'
  fi
}

trap 'rc=$?; if [ "$rc" -ne 0 ]; then rollback; fi; rm -rf "$STAGE"; exit "$rc"' EXIT

install -o "$INDEX_UID" -g "$INDEX_GID" -m "$INDEX_MODE" "$STAGE/$INDEX_REL" "$INDEX_LIVE"
install -o "$VIEW_UID" -g "$VIEW_GID" -m "$VIEW_MODE" "$STAGE/$VIEW_REL" "$VIEW_LIVE"

[ "$(blob "$INDEX_LIVE")" = "$INDEX_TARGET_BLOB" ] || { echo 'STOP: INDEX_LIVE_TARGET_FAIL'; exit 1; }
[ "$(blob "$VIEW_LIVE")" = "$VIEW_TARGET_BLOB" ] || { echo 'STOP: VIEW_LIVE_TARGET_FAIL'; exit 1; }

trap - EXIT
rm -rf "$STAGE"

echo '=== FINAL ==='
echo 'MANAGEMENT_DEFAULT_COMPARE_DEPLOY=PASS'
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "BACKUP=$BACKUP"
echo 'CHANGED_RUNTIME_FILES=2'
echo 'DEFAULT_VIEW=COMPARE'
echo 'SINGLE_DATE_MODE=AVAILABLE'
echo 'DB_CHANGES=NONE'
echo 'SERVICE_CHANGES=NONE'
echo 'QNAP_WRITES=NONE'
