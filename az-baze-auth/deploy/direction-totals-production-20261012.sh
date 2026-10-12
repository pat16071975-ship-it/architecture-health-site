#!/usr/bin/env bash
set -Eeuo pipefail

# Owner-approved direction totals: install only the two report frontend files.
# No database access, service restart, Git checkout, or release metadata update.
REPO="pat16071975-ship-it/architecture-health-site"
TARGET_COMMIT="4873d5130ac720d8b9a89cc7d3c243e844c3a673"
INDEX_BASE="23554bf780063538c81fd1b9bbce508e054ac268"
INDEX_TARGET="9f4ecc4ecbbb2c2d031978b75449eb7332f0da47"
PERIOD_BASE="b534186f91805e7d60d571807d94e2b6c1a7ce44"
PERIOD_TARGET="0f59b99d5c75651be2ef9aff36d0a1862c4838e3"
REPORT_STORAGE_BLOB="e14756f3eb7a311100e08d21a6e2f781231f7fd4"
BASE_TEMPLATE_BLOB="c19bbc4660519f3c47a9dd15ca15488247df4d2b"
TOKEN="DIRECTION-TOTALS-ONLY-20261012"
REPORT_ROOT="/var/www/az-baze.ru/reports"
APP_ROOT="/opt/az-baze-auth"
BACKUP_ROOT="/var/lib/az-baze/backups"
LOCK_FILE="/run/lock/az-baze-direction-totals.lock"
SERVICE="az-baze-auth"
HEALTH_URL="http://127.0.0.1:8001/health"
MODE="${1:-}"
case "$MODE" in
  --preflight) [ "$#" -eq 1 ] || { echo "STOP: INVALID_ARGUMENTS"; exit 2; } ;;
  --apply)
    [ "$#" -eq 2 ] && [ "$2" = "$TOKEN" ] || { echo "STOP: CONFIRM_TOKEN_REQUIRED"; exit 2; } ;;
  *) echo "Usage: $0 --preflight | --apply $TOKEN"; exit 2 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo "STOP: ROOT_REQUIRED"; exit 1; }
[ "$(hostname -s)" = "az-server" ] || { echo "STOP: WRONG_HOST"; exit 1; }
for dependency in curl git systemctl python3 flock stat readlink; do
  command -v "$dependency" >/dev/null
done
for pinned in "$TARGET_COMMIT" "$INDEX_TARGET" "$PERIOD_TARGET"; do
  [[ "$pinned" =~ ^[0-9a-f]{40}$ ]] || { echo "STOP: UNPINNED_DRAFT"; exit 1; }
done
if [ "$MODE" = "--apply" ]; then
  exec 9>"$LOCK_FILE"
  flock -n 9 || { echo "STOP: DEPLOY_ALREADY_RUNNING"; exit 1; }
fi

STAGE="$(mktemp -d /tmp/az-direction-totals.XXXXXX)"
BACKUP=""
INSTALL_STARTED=0
SUCCESS=0
blob() { git hash-object "$1"; }
metadata() { stat -c '%u:%g:%a' "$1"; }
health_ok() {
  systemctl is-active --quiet "$SERVICE" &&
    [ "$(curl -fsS --connect-timeout 3 --max-time 5 "$HEALTH_URL")" = "ok" ]
}
regular_path() {
  [ -f "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]
}
protected_ok() {
  regular_path "$APP_ROOT/report_storage.py" &&
    regular_path "$APP_ROOT/templates/base.html" &&
    [ "$(blob "$APP_ROOT/report_storage.py")" = "$REPORT_STORAGE_BLOB" ] &&
    [ "$(blob "$APP_ROOT/templates/base.html")" = "$BASE_TEMPLATE_BLOB" ]
}
reports_match() {
  [ "$(blob "$REPORT_ROOT/index.html")" = "$1" ] &&
    [ "$(blob "$REPORT_ROOT/period-view.js")" = "$2" ]
}
atomic_copy() {
  python3 -B - "$1" "$2" <<'PY'
from pathlib import Path
import os, shutil, sys, tempfile
source, target = map(Path, sys.argv[1:])
assert source.is_file() and not source.is_symlink()
assert target.is_file() and not target.is_symlink()
meta = source.stat()
fd, name = tempfile.mkstemp(prefix='.direction-totals-', dir=target.parent)
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
  local rc=$? rollback_ok=1 rel
  trap - EXIT
  trap '' INT TERM
  set +e
  if [ "$rc" -ne 0 ] && [ "$SUCCESS" -ne 1 ]; then
    if [ "$INSTALL_STARTED" -eq 1 ]; then
      echo "DIRECTION_TOTALS_ROLLBACK=START"
      for rel in index.html period-view.js; do
        if atomic_copy "$BACKUP/$rel" "$REPORT_ROOT/$rel" &&
           [ "$(blob "$REPORT_ROOT/$rel")" = "$(blob "$BACKUP/$rel")" ] &&
           [ "$(metadata "$REPORT_ROOT/$rel")" = "$(metadata "$BACKUP/$rel")" ]; then
          echo "RESTORED[$rel]=PASS"
        else
          rollback_ok=0
          echo "RESTORED[$rel]=FAIL"
        fi
      done
      if [ "$rollback_ok" -eq 1 ]; then
        echo "DIRECTION_TOTALS_ROLLBACK=PASS"
      else
        echo "DIRECTION_TOTALS_ROLLBACK=FAIL"
      fi
      if health_ok; then echo "SERVICE_HEALTH_ROLLBACK=PASS"; else echo "SERVICE_HEALTH_ROLLBACK=FAIL"; fi
      echo "BACKUP=$BACKUP"
    fi
    if [ "$MODE" = "--apply" ]; then
      echo "DIRECTION_TOTALS_PRODUCTION_DEPLOY=FAIL"
    else
      echo "DIRECTION_TOTALS_PREFLIGHT=FAIL"
    fi
  fi
  rm -rf -- "$STAGE"
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "=== AZ-BAZE DIRECTION TOTALS TWO-FILE PREFLIGHT ==="
echo "TARGET_COMMIT=$TARGET_COMMIT"
regular_path "$REPORT_ROOT/index.html"
regular_path "$REPORT_ROOT/period-view.js"
protected_ok
echo "REPORT_BACKEND_AND_LOADER=PASS"
health_ok
echo "SERVICE_HEALTH_PRE=PASS"
if reports_match "$INDEX_TARGET" "$PERIOD_TARGET"; then
  echo "ALREADY_INSTALLED=PASS"
  echo "CHANGED_FILES=NONE"
  if [ "$MODE" = "--apply" ]; then
    echo "DIRECTION_TOTALS_PRODUCTION_DEPLOY=PASS"
  else
    echo "DIRECTION_TOTALS_PREFLIGHT=PASS"
  fi
  SUCCESS=1
  exit 0
fi
reports_match "$INDEX_BASE" "$PERIOD_BASE" || { echo "STOP: LIVE_BASE_OR_MIXED_STATE_MISMATCH"; exit 1; }
echo "LIVE_BASE_GUARD=PASS"
for rel in index.html period-view.js; do
  curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 \
    "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/reports/$rel" -o "$STAGE/$rel"
done
[ "$(blob "$STAGE/index.html")" = "$INDEX_TARGET" ]
[ "$(blob "$STAGE/period-view.js")" = "$PERIOD_TARGET" ]
echo "TARGET_BLOBS=PASS"
echo "DIRECTION_TOTALS_PREFLIGHT=PASS"
if [ "$MODE" = "--preflight" ]; then
  echo "LIVE_CHANGES=NONE"
  SUCCESS=1
  exit 0
fi

# Recheck after download; prepare and verify both backups before any live write.
protected_ok
reports_match "$INDEX_BASE" "$PERIOD_BASE"
health_ok
mkdir -p "$BACKUP_ROOT"
BACKUP="$(mktemp -d "$BACKUP_ROOT/direction-totals-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
printf 'TARGET_COMMIT=%s\nSCRIPT_BLOB=%s\n' "$TARGET_COMMIT" "$(blob "$0")" >"$BACKUP/manifest.txt"
for rel in index.html period-view.js; do
  cp -a -- "$REPORT_ROOT/$rel" "$BACKUP/$rel"
  [ "$(blob "$BACKUP/$rel")" = "$(blob "$REPORT_ROOT/$rel")" ]
  [ "$(metadata "$BACKUP/$rel")" = "$(metadata "$REPORT_ROOT/$rel")" ]
  printf '%s|base=%s|target=%s|uid:gid:mode=%s\n' "$rel" "$(blob "$BACKUP/$rel")" \
    "$(blob "$STAGE/$rel")" "$(metadata "$BACKUP/$rel")" >>"$BACKUP/manifest.txt"
  # Preserve owner, group, mode and ACL; keep the freshly downloaded modification time.
  cp --attributes-only --preserve=mode,ownership -- "$REPORT_ROOT/$rel" "$STAGE/$rel"
done
[ "$(blob "$BACKUP/index.html")" = "$INDEX_BASE" ]
[ "$(blob "$BACKUP/period-view.js")" = "$PERIOD_BASE" ]
[ "$(blob "$STAGE/index.html")" = "$INDEX_TARGET" ]
[ "$(blob "$STAGE/period-view.js")" = "$PERIOD_TARGET" ]
echo "BACKUP=$BACKUP"
echo "BACKUP_BLOBS_AND_METADATA=PASS"
protected_ok
reports_match "$INDEX_BASE" "$PERIOD_BASE"

INSTALL_STARTED=1
# The new JS is backwards compatible with the previous HTML. Publish HTML last.
for rel in period-view.js index.html; do
  atomic_copy "$STAGE/$rel" "$REPORT_ROOT/$rel"
  [ "$(blob "$REPORT_ROOT/$rel")" = "$(blob "$STAGE/$rel")" ]
  [ "$(metadata "$REPORT_ROOT/$rel")" = "$(metadata "$BACKUP/$rel")" ]
done
echo "ATOMIC_INSTALL_AND_METADATA=PASS"
reports_match "$INDEX_TARGET" "$PERIOD_TARGET"
protected_ok
echo "LIVE_TARGET_BLOBS=PASS"
echo "REPORT_BACKEND_AND_LOADER=PASS"
health_ok
echo "SERVICE_HEALTH_POST=PASS"
SUCCESS=1
INSTALL_STARTED=0
echo "=== FINAL ==="
echo "DIRECTION_TOTALS_PRODUCTION_DEPLOY=PASS"
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "BACKUP=$BACKUP"
echo "SERVICE=active"
echo "HEALTH=ok"
echo "CHANGED_FILES=ONLY_INDEX_HTML_AND_PERIOD_VIEW_JS"
echo "DB_ACCESS_BY_DEPLOY=NONE"
echo "SERVICE_RESTART=NONE"
