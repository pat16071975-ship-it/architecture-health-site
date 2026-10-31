#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_COMMIT='5eabd3c2a3855533c9898606296e8fef9137bc5b'
CONFIRM_TOKEN='ADVERTISING-CROSS-DAY-5EABD3C2'
SERVICE='az-baze-auth'
DB='/var/lib/az-baze/auth.db'
BACKUP_ROOT='/var/lib/az-baze/backups'
HEALTH='http://127.0.0.1:8001/health'

REL='az-baze-auth/advertising_report.py'
LIVE='/opt/az-baze-auth/advertising_report.py'
LIVE_JS='/var/www/az-baze.ru/reports/advertising.js'
BASE_REPORT_BLOB='2b269edc407e86668876f497ea525cb6f1037679'
TARGET_REPORT_BLOB='7deb8d73fdb633c4644e7b0b6924de4b87bcbff7'
EXPECTED_JS_BLOB='58c7b1ccc84ca0d0848adf5e81440d7233a1d4d8'

MODE="${1:-}"
TOKEN="${2:-}"
STAGE=''
BACKUP=''
INSTALLED=0

cleanup(){ [[ -n "${STAGE:-}" && -d "$STAGE" ]] && rm -rf -- "$STAGE"; }
trap cleanup EXIT
fail(){ echo "STOP: $*" >&2; exit 1; }
health(){ curl -fsS --connect-timeout 2 --max-time 5 "$HEALTH" >/dev/null; }
wait_health(){ local i; for i in $(seq 1 30); do health && return 0; sleep 1; done; return 1; }

db_fingerprint(){
  python3 -B - "$DB" <<'PY'
import hashlib,sqlite3,sys
c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True,timeout=10)
try:
    c.execute('PRAGMA query_only=ON'); c.execute('BEGIN')
    h=hashlib.sha256()
    for line in c.iterdump():
        h.update(line.encode()); h.update(b'\n')
    print(h.hexdigest())
finally:c.close()
PY
}

verify_live_base(){
  local report_blob js_blob
  [[ -f "$LIVE" && ! -L "$LIVE" ]] || fail "live report path invalid $LIVE"
  [[ -f "$LIVE_JS" && ! -L "$LIVE_JS" ]] || fail "live JS path invalid $LIVE_JS"
  report_blob="$(git hash-object "$LIVE")"
  js_blob="$(git hash-object "$LIVE_JS")"
  [[ "$report_blob" == "$BASE_REPORT_BLOB" ]] || fail "live report base mismatch: $report_blob"
  [[ "$js_blob" == "$EXPECTED_JS_BLOB" ]] || fail "live JS mismatch: $js_blob"
  echo 'LIVE_BASE_GUARD=PASS'
}

verify_live_target(){
  local report_blob js_blob
  report_blob="$(git hash-object "$LIVE")"
  js_blob="$(git hash-object "$LIVE_JS")"
  [[ "$report_blob" == "$TARGET_REPORT_BLOB" ]] || fail "live report target mismatch: $report_blob"
  [[ "$js_blob" == "$EXPECTED_JS_BLOB" ]] || fail "live JS drift: $js_blob"
  echo 'LIVE_TARGET_GUARD=PASS'
}

download_target(){
  STAGE="$(mktemp -d /tmp/az-ad-cross-day-stage-XXXXXX)"
  local dst url actual
  dst="$STAGE/$REL"
  mkdir -p -- "$(dirname "$dst")"
  url="https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$TARGET_COMMIT/$REL"
  curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 "$url" -o "$dst"
  actual="$(git hash-object "$dst")"
  [[ "$actual" == "$TARGET_REPORT_BLOB" ]] || fail "target blob mismatch: $actual"
  python3 -m py_compile "$dst"
  grep -Fq 'YANDEX_CANONICAL_CAMPAIGNS' "$dst"
  grep -Fq 'LEGACY_INSTAGRAM_SOURCE_KEYS' "$dst"
  grep -Fq 'Missing display name must not erase a factual non-empty campaign ID.' "$dst"
  grep -Fq 'A non-empty campaign ID is authoritative evidence.' "$dst"
  grep -Fq 'structured traffic already contains an unknown non-empty ID' "$dst"
  echo 'TARGET_CONTRACT=PASS'
  echo 'TARGET_BLOB_AND_SYNTAX=PASS'
}

make_backup(){
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP="$BACKUP_ROOT/advertising-cross-day-$stamp"
  mkdir -p "$BACKUP/files"
  cp -a -- "$LIVE" "$BACKUP/files/advertising_report.py"
  cp -a -- "$LIVE_JS" "$BACKUP/files/advertising.js"
  python3 -B - "$DB" "$BACKUP/auth.db" <<'PY'
import sqlite3,sys
src=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True,timeout=10)
dst=sqlite3.connect(sys.argv[2])
try:
    src.execute('PRAGMA query_only=ON'); src.backup(dst)
    assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
finally:dst.close();src.close()
PY
  echo "BACKUP=$BACKUP"
  echo 'BACKUP_VERIFY=PASS'
}

atomic_install(){
  python3 -B - "$STAGE/$REL" "$LIVE" <<'PY'
import os,pathlib,sys,tempfile
src=pathlib.Path(sys.argv[1]); target=pathlib.Path(sys.argv[2]); st=target.stat()
fd,tmp=tempfile.mkstemp(prefix='.'+target.name+'.',dir=str(target.parent))
try:
    with os.fdopen(fd,'wb') as out:
        out.write(src.read_bytes()); out.flush(); os.fsync(out.fileno())
    os.chown(tmp,st.st_uid,st.st_gid); os.chmod(tmp,st.st_mode & 0o7777)
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

restore_file(){
  echo 'ROLLBACK_FILE=START'
  systemctl stop "$SERVICE" || true
  [[ -f "$BACKUP/files/advertising_report.py" ]] && cp -a -- "$BACKUP/files/advertising_report.py" "$LIVE"
  systemctl start "$SERVICE" || true
  wait_health || true
  echo 'ROLLBACK_FILE=COMPLETED; DB_RESTORE=NOT_PERFORMED'
}

preflight(){
  [[ "$(id -u)" == '0' ]] || fail 'run as root'
  [[ "$(hostname -s)" == 'az-server' ]] || fail "wrong host $(hostname -s)"
  for c in curl git python3 systemctl; do command -v "$c" >/dev/null 2>&1 || fail "missing command $c"; done
  [[ -f "$DB" && ! -L "$DB" ]] || fail 'database path invalid'
  systemctl is-active --quiet "$SERVICE" || fail 'service not active'
  health || fail 'pre-health failed'
  echo 'SERVICE_HEALTH_PRE=PASS'
  verify_live_base
  download_target
  echo "DB_READONLY_FINGERPRINT=$(db_fingerprint)"
  echo 'ADVERTISING_CROSS_DAY_PREFLIGHT=PASS; LIVE_CHANGES=NONE'
}

on_error(){ local code="$1"; trap - ERR; if [[ "$INSTALLED" == '1' && -n "$BACKUP" ]]; then restore_file; fi; exit "$code"; }
trap 'on_error $?' ERR

case "$MODE" in
  --preflight)
    preflight
    ;;
  --apply)
    [[ "$TOKEN" == "$CONFIRM_TOKEN" ]] || fail 'wrong confirm token'
    preflight
    make_backup
    DB_BEFORE="$(db_fingerprint)"

    systemctl stop "$SERVICE"
    echo 'SERVICE_STOPPED=PASS'

    atomic_install
    [[ "$(db_fingerprint)" == "$DB_BEFORE" ]] || fail 'database changed before service start'
    echo 'DB_EXACT_UNCHANGED_BEFORE_START=PASS'
    verify_live_target

    systemctl start "$SERVICE"
    wait_health || fail 'post-health failed'
    echo 'SERVICE_HEALTH_POST=PASS'
    verify_live_target

    [[ "$(db_fingerprint)" == "$DB_BEFORE" ]] || fail 'database changed after service start'
    echo 'DB_EXACT_UNCHANGED_POST_START=PASS'

    INSTALLED=0
    echo 'ADVERTISING_CROSS_DAY_PRODUCTION_DEPLOY=PASS'
    echo "TARGET_COMMIT=$TARGET_COMMIT"
    echo "BACKUP=$BACKUP"
    echo 'CHANGED_RUNTIME_FILES=1; DATABASE_WRITES_BY_INSTALLER=NONE'
    ;;
  *)
    fail 'use --preflight or --apply <token>'
    ;;
esac
