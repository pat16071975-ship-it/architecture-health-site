#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO="pat16071975-ship-it/architecture-health-site"
TARGET_COMMIT="30bc7d8c6314cd950c4cfd20bcd48c409aef47c1"
TOKEN="PAID-PERIOD-AUTO-OVERLAP-20261013"
APP_ROOT="/opt/az-baze-auth"
DB="/var/lib/az-baze/auth.db"
BACKUP_ROOT="/var/lib/az-baze/backups"
SERVICE="az-baze-auth"
HEALTH_URL="http://127.0.0.1:8001/health"
LOCK_FILE="/run/lock/az-baze-paid-period-auto-overlap.lock"
MODE="${1:-}"

case "$MODE" in
  --preflight) [ "$#" -eq 1 ] || { echo "STOP: INVALID_ARGUMENTS"; exit 2; } ;;
  --apply) [ "$#" -eq 2 ] && [ "$2" = "$TOKEN" ] || { echo "STOP: CONFIRM_TOKEN_REQUIRED"; exit 2; } ;;
  *) echo "Usage: $0 --preflight | --apply $TOKEN"; exit 2 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo "STOP: ROOT_REQUIRED"; exit 1; }
[ "$(hostname -s)" = "az-server" ] || { echo "STOP: WRONG_HOST"; exit 1; }
for dep in curl git systemctl python3 flock stat readlink sha256sum install; do command -v "$dep" >/dev/null; done

if [ "$MODE" = "--apply" ]; then
  exec 9>"$LOCK_FILE"
  flock -n 9 || { echo "STOP: DEPLOY_ALREADY_RUNNING"; exit 1; }
fi

STAGE="$(mktemp -d /tmp/az-paid-period-auto-overlap.XXXXXX)"
MANIFEST="$STAGE/manifest.txt"
BACKUP=""
INSTALL_STARTED=0
SERVICE_STOPPED=0
SUCCESS=0

cat >"$MANIFEST" <<'EOF'
paid_services.py|e1f8cf945161c753775af9a39d1df47c1639df0e|1613f6946976efe980e1bf95390d2b5118665caa
paid_services_upload.py|83abc5cb422887ba14f9d6d90755a25c8f6a9427|d2e682540f831d5adafd9b19e753a01d53749349
templates/uploads.html|d3aaaa2b5a9cc7e4b0a6ccc656384d5c9f2aae94|be36446bbeaa82fc9c27769a8cabb24978ec0d36
EOF

blob(){ git hash-object "$1"; }
regular(){ [ -f "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
health(){
  systemctl is-active --quiet "$SERVICE" &&
  [ "$(curl -fsS --connect-timeout 2 --max-time 3 "$HEALTH_URL" 2>/dev/null)" = "ok" ]
}
wait_health(){
  local i
  for i in $(seq 1 35); do health && return 0; sleep 1; done
  return 1
}
live_matches(){
  local which="$1" rel base target want
  while IFS='|' read -r rel base target; do
    regular "$APP_ROOT/$rel" || return 1
    [ "$which" = "base" ] && want="$base" || want="$target"
    [ "$(blob "$APP_ROOT/$rel")" = "$want" ] || return 1
  done <"$MANIFEST"
}
protected_ok(){
  regular "$APP_ROOT/report_storage.py" &&
  [ "$(blob "$APP_ROOT/report_storage.py")" = "367592919431997255862ed179a5c6a4f93703ad" ] &&
  regular "$APP_ROOT/templates/base.html" &&
  [ "$(blob "$APP_ROOT/templates/base.html")" = "c19bbc4660519f3c47a9dd15ca15488247df4d2b" ]
}
restore_files(){
  local rel base target saved
  while IFS='|' read -r rel base target; do
    saved="$BACKUP/files/$rel"
    [ -f "$saved" ] || return 1
    cp -a -- "$saved" "$APP_ROOT/$rel"
    [ "$(blob "$APP_ROOT/$rel")" = "$base" ] || return 1
  done <"$MANIFEST"
}
cleanup(){
  local rc=$?
  trap - EXIT
  set +e
  if [ "$rc" -ne 0 ] && [ "$SUCCESS" -ne 1 ]; then
    if [ "$INSTALL_STARTED" -eq 1 ] && [ -n "$BACKUP" ]; then
      systemctl stop "$SERVICE" >/dev/null 2>&1 || true
      if restore_files; then
        systemctl start "$SERVICE" >/dev/null 2>&1 || true
        wait_health && echo "PAID_PERIOD_AUTO_OVERLAP_ROLLBACK=PASS" || echo "PAID_PERIOD_AUTO_OVERLAP_ROLLBACK=FAIL"
      else
        echo "PAID_PERIOD_AUTO_OVERLAP_ROLLBACK=FAIL"
      fi
    elif [ "$SERVICE_STOPPED" -eq 1 ]; then
      systemctl start "$SERVICE" >/dev/null 2>&1 || true
      wait_health && echo "SERVICE_RECOVERY=PASS" || echo "SERVICE_RECOVERY=FAIL"
    fi
    echo "PAID_PERIOD_AUTO_OVERLAP_PRODUCTION_DEPLOY=FAIL"
  fi
  rm -rf -- "$STAGE"
  exit "$rc"
}
trap cleanup EXIT

echo "=== AZ-BAZE PAID PERIOD AUTO/OVERLAP PREFLIGHT ==="
echo "TARGET_COMMIT=$TARGET_COMMIT"
regular "$DB" || { echo "STOP: DB_PATH"; exit 1; }
protected_ok || { echo "STOP: PROTECTED_RUNTIME_MISMATCH"; exit 1; }
health || { echo "STOP: SERVICE_HEALTH_PRE"; exit 1; }
echo "SERVICE_HEALTH_PRE=PASS"

if live_matches target; then
  echo "ALREADY_INSTALLED=PASS"
  echo "PAID_PERIOD_AUTO_OVERLAP_PRODUCTION_DEPLOY=PASS"
  SUCCESS=1
  exit 0
fi

live_matches base || { echo "STOP: LIVE_BASE_OR_MIXED_STATE_MISMATCH"; exit 1; }
echo "LIVE_BASE_GUARD=PASS"

while IFS='|' read -r rel base target; do
  out="$STAGE/$rel"
  mkdir -p "$(dirname "$out")"
  curl -fsSL --retry 2 --connect-timeout 5 --max-time 30     "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/az-baze-auth/$rel" -o "$out"
  [ "$(blob "$out")" = "$target" ] || { echo "STOP: TARGET_BLOB_MISMATCH[$rel]"; exit 1; }
done <"$MANIFEST"
echo "TARGET_BLOBS=PASS"

"$APP_ROOT/venv/bin/python" -B - "$STAGE" <<'PY'
from pathlib import Path
from jinja2 import Environment
import sys
root=Path(sys.argv[1])
for name in ("paid_services.py","paid_services_upload.py"):
    p=root/name
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
Environment().parse((root/"templates/uploads.html").read_text(encoding="utf-8"))
PY
echo "TARGET_SYNTAX=PASS"

if [ "$MODE" = "--preflight" ]; then
  echo "PAID_PERIOD_AUTO_OVERLAP_PREFLIGHT=PASS"
  echo "LIVE_CHANGES=NONE"
  SUCCESS=1
  exit 0
fi

BACKUP="$(mktemp -d "$BACKUP_ROOT/paid-period-auto-overlap-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
mkdir -p "$BACKUP/files" "$BACKUP/db"
while IFS='|' read -r rel base target; do
  mkdir -p "$BACKUP/files/$(dirname "$rel")"
  cp -a -- "$APP_ROOT/$rel" "$BACKUP/files/$rel"
  [ "$(blob "$BACKUP/files/$rel")" = "$base" ]
done <"$MANIFEST"
echo "BACKUP=$BACKUP"
echo "FILE_BACKUP=PASS"

systemctl stop "$SERVICE"
SERVICE_STOPPED=1
! systemctl is-active --quiet "$SERVICE"

"$APP_ROOT/venv/bin/python" -B - "$DB" "$BACKUP/db/auth.db" <<'PY'
import sqlite3, sys
src=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro", uri=True)
dst=sqlite3.connect(sys.argv[2])
src.backup(dst)
assert src.execute("PRAGMA integrity_check").fetchone()==("ok",)
src.close(); dst.close()
PY
sha256sum "$BACKUP/db/auth.db" > "$BACKUP/db/auth.db.sha256"
echo "DB_BACKUP_VERIFY=PASS"

INSTALL_STARTED=1
while IFS='|' read -r rel base target; do
  live="$APP_ROOT/$rel"
  uid="$(stat -c %u "$live")"
  gid="$(stat -c %g "$live")"
  mode="$(stat -c %a "$live")"
  tmp="$(mktemp "$(dirname "$live")/.paid-period-auto-overlap.XXXXXX")"
  install -o "$uid" -g "$gid" -m "$mode" "$STAGE/$rel" "$tmp"
  mv -f -- "$tmp" "$live"
  [ "$(blob "$live")" = "$target" ]
  [ "$(stat -c '%u:%g:%a' "$live")" = "$uid:$gid:$mode" ]
done <"$MANIFEST"
echo "ATOMIC_INSTALL=PASS"
live_matches target
protected_ok
echo "LIVE_TARGET_BLOBS=PASS"

systemctl start "$SERVICE"
SERVICE_STOPPED=0
wait_health
echo "SERVICE_HEALTH_POST=PASS"
live_matches target
protected_ok

SUCCESS=1
INSTALL_STARTED=0
echo "=== FINAL ==="
echo "PAID_PERIOD_AUTO_OVERLAP_PRODUCTION_DEPLOY=PASS"
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "BACKUP=$BACKUP"
echo "SERVICE=active"
echo "HEALTH=ok"
echo "CHANGED_FILES=ONLY_PAID_SERVICES_PAID_SERVICES_UPLOAD_UPLOADS_HTML"
echo "DB_WRITES_BY_INSTALLER=NONE"
echo "SERVICE_RESTART=STOP_START_COMPLETED"
