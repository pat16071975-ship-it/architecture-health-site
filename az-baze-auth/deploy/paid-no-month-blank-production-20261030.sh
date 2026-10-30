#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_COMMIT='83e0b51dff396a3915acf8b6a03a7e780fe57957'
CONFIRM_TOKEN='PAID-NO-MONTH-BLANK-83E0B51D'
SERVICE='az-baze-auth'
DB='/var/lib/az-baze/auth.db'
BACKUP_ROOT='/var/lib/az-baze/backups'
HEALTH='http://127.0.0.1:8001/health'

REL='az-baze-auth/report_storage.py'
LIVE='/opt/az-baze-auth/report_storage.py'
BASE_BLOB='6c87c6038959f430970786b08e830aa27d7e6419'
TARGET_BLOB='d409d76721e80dd1fbb2ce5d4fc243d2e5bc9841'

MODE="${1:-}"
TOKEN="${2:-}"
STAGE=''
BACKUP=''
INSTALLED=0

cleanup(){ [[ -n "${STAGE:-}" && -d "$STAGE" ]] && rm -rf -- "$STAGE"; }
trap cleanup EXIT
fail(){ echo "STOP: $*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || fail "missing command $1"; }
health(){ curl -fsS --connect-timeout 2 --max-time 5 "$HEALTH" >/dev/null; }
wait_health(){
  local i
  for i in $(seq 1 30); do
    if health; then return 0; fi
    sleep 1
  done
  return 1
}

db_fingerprint(){
  python3 -B - "$DB" <<'PY'
import hashlib,sqlite3,sys
c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True,timeout=10)
try:
    c.execute('PRAGMA query_only=ON')
    c.execute('BEGIN')
    assert c.execute('PRAGMA query_only').fetchone()[0]==1
    h=hashlib.sha256()
    for line in c.iterdump():
        h.update(line.encode()); h.update(b'\n')
    print(h.hexdigest())
finally:
    c.close()
PY
}

verify_live_base(){
  [[ -f "$LIVE" && ! -L "$LIVE" ]] || fail "live path invalid $LIVE"
  local actual
  actual="$(git hash-object "$LIVE")"
  [[ "$actual" == "$BASE_BLOB" ]] || fail "live base mismatch $actual"
  echo 'LIVE_BASE_GUARD=PASS'
}

verify_live_target(){
  local actual
  actual="$(git hash-object "$LIVE")"
  [[ "$actual" == "$TARGET_BLOB" ]] || fail "live target mismatch $actual"
  echo 'LIVE_TARGET_BLOB=PASS'
}

download_target(){
  STAGE="$(mktemp -d /tmp/az-paid-no-blank-stage-XXXXXX)"
  local dst="$STAGE/report_storage.py"
  curl -fsSL --retry 2 --connect-timeout 5 --max-time 30     "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$TARGET_COMMIT/$REL"     -o "$dst"
  [[ "$(git hash-object "$dst")" == "$TARGET_BLOB" ]] || fail 'target blob mismatch'
  python3 -m py_compile "$dst"
  python3 -B - "$dst" <<'PY'
from pathlib import Path
import sys
text=Path(sys.argv[1]).read_text(encoding='utf-8')
assert 'if day is None or not month_start <= day <= as_of:' in text
assert 'day = as_of' in text
assert 'the whole month' in text
assert 'cumulative["medicine"] != mis["medicine"]' in text
print('TARGET_CONTRACT=PASS')
PY
  echo 'TARGET_BLOB_AND_SYNTAX=PASS'
}

make_backup(){
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP="$BACKUP_ROOT/paid-no-month-blank-$stamp"
  mkdir -p "$BACKUP/files"
  cp -a -- "$LIVE" "$BACKUP/files/report_storage.py"
  python3 -B - "$DB" "$BACKUP/auth.db" <<'PY'
import sqlite3,sys
src=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True,timeout=10)
dst=sqlite3.connect(sys.argv[2])
try:
    src.execute('PRAGMA query_only=ON')
    src.backup(dst)
    assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
finally:
    dst.close(); src.close()
PY
  printf 'TARGET_COMMIT=%s\n' "$TARGET_COMMIT" > "$BACKUP/metadata.txt"
  echo "BACKUP=$BACKUP"
  echo 'BACKUP_VERIFY=PASS'
}

restore_file(){
  echo 'ROLLBACK_FILE=START'
  systemctl stop "$SERVICE" || true
  [[ -f "$BACKUP/files/report_storage.py" ]] && cp -a -- "$BACKUP/files/report_storage.py" "$LIVE"
  systemctl start "$SERVICE" || true
  wait_health || true
  echo 'ROLLBACK_FILE=COMPLETED; DB_RESTORE=NOT_PERFORMED'
}

atomic_install(){
  python3 -B - "$STAGE/report_storage.py" "$LIVE" <<'PY'
import os,pathlib,sys,tempfile
src=pathlib.Path(sys.argv[1]); target=pathlib.Path(sys.argv[2]); st=target.stat()
fd,tmp=tempfile.mkstemp(prefix='.'+target.name+'.',dir=str(target.parent))
try:
    with os.fdopen(fd,'wb') as out:
        out.write(src.read_bytes()); out.flush(); os.fsync(out.fileno())
    os.chown(tmp,st.st_uid,st.st_gid)
    os.chmod(tmp,st.st_mode & 0o7777)
    os.replace(tmp,target)
    dfd=os.open(target.parent,os.O_DIRECTORY)
    try: os.fsync(dfd)
    finally: os.close(dfd)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
PY
  INSTALLED=1
  echo 'ATOMIC_INSTALL=PASS'
}

preflight(){
  [[ "$(id -u)" == '0' ]] || fail 'run as root'
  [[ "$(hostname -s)" == 'az-server' ]] || fail "wrong host $(hostname -s)"
  for c in curl git python3 systemctl; do need "$c"; done
  [[ -f "$DB" && ! -L "$DB" ]] || fail 'database path invalid'
  systemctl is-active --quiet "$SERVICE" || fail 'service not active'
  health || fail 'pre-health failed'
  echo 'SERVICE_HEALTH_PRE=PASS'
  verify_live_base
  download_target
  local fp="$(db_fingerprint)"
  [[ "$fp" =~ ^[0-9a-f]{64}$ ]] || fail 'db fingerprint invalid'
  echo "DB_READONLY_FINGERPRINT=$fp"
  echo 'PAID_NO_MONTH_BLANK_PREFLIGHT=PASS; LIVE_CHANGES=NONE'
}

on_error(){
  local code="$1"; trap - ERR
  if [[ "$INSTALLED" == '1' && -n "$BACKUP" ]]; then restore_file; fi
  exit "$code"
}
trap 'on_error $?' ERR

case "$MODE" in
  --preflight) preflight ;;
  --apply)
    [[ "$TOKEN" == "$CONFIRM_TOKEN" ]] || fail 'wrong confirm token'
    preflight
    make_backup
    DB_BEFORE="$(db_fingerprint)"
    systemctl stop "$SERVICE"
    systemctl is-active --quiet "$SERVICE" && fail 'service still active after stop' || true
    echo 'SERVICE_STOPPED=PASS'
    atomic_install
    DB_AFTER_INSTALL="$(db_fingerprint)"
    [[ "$DB_AFTER_INSTALL" == "$DB_BEFORE" ]] || fail 'database changed before service start'
    echo 'DB_EXACT_UNCHANGED_BEFORE_START=PASS'
    verify_live_target
    systemctl start "$SERVICE"
    wait_health || fail 'post-health failed'
    echo 'SERVICE_HEALTH_POST=PASS'
    verify_live_target
    DB_AFTER_START="$(db_fingerprint)"
    [[ "$DB_AFTER_START" == "$DB_BEFORE" ]] || fail 'database changed after service start'
    echo 'DB_EXACT_UNCHANGED_POST_START=PASS'
    INSTALLED=0
    echo 'PAID_NO_MONTH_BLANK_PRODUCTION_DEPLOY=PASS'
    echo "TARGET_COMMIT=$TARGET_COMMIT"
    echo "BACKUP=$BACKUP"
    echo 'SERVICE=active; HEALTH=ok'
    echo 'CHANGED_FILES=1; DATABASE_WRITES_BY_INSTALLER=NONE; BUSINESS_DATA_BACKFILL=NOT_REQUIRED'
    ;;
  *) fail 'use --preflight or --apply <token>' ;;
esac
