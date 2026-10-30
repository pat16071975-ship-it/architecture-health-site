#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_COMMIT='8062b65adb406acddfcacb91a638f005a00255ec'
CONFIRM_TOKEN='ADVERTISING-TERMINOLOGY-8062B65A'
SERVICE='az-baze-auth'
LIVE='/opt/az-baze-auth/terminology.py'
DB='/var/lib/az-baze/auth.db'
BACKUP_ROOT='/var/lib/az-baze/backups'
HEALTH='http://127.0.0.1:8001/health'
BASE_BLOB='defc0929c083d494bebced292756de68d56811e8'
TARGET_BLOB='1b690a53638cbd204732f50f91a325ddac1102ae'

MODE="${1:-}"
TOKEN="${2:-}"
TMP=''
BACKUP=''
INSTALLED=0

cleanup(){ [[ -n "${TMP:-}" && -f "$TMP" ]] && rm -f -- "$TMP"; }
trap cleanup EXIT
fail(){ echo "STOP: $*" >&2; exit 1; }
health(){ curl -fsS --connect-timeout 2 --max-time 5 "$HEALTH" >/dev/null; }
wait_health(){ local i; for i in $(seq 1 30); do health && return 0; sleep 1; done; return 1; }

db_fingerprint(){
  python3 -B - "$DB" <<'PY'
import hashlib, sqlite3, sys
c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro', uri=True, timeout=10)
try:
    c.execute('PRAGMA query_only=ON')
    c.execute('BEGIN')
    h=hashlib.sha256()
    for line in c.iterdump():
        h.update(line.encode()); h.update(b'\n')
    print(h.hexdigest())
finally:
    c.close()
PY
}

verify_live_base(){
  [[ -f "$LIVE" && ! -L "$LIVE" ]] || fail 'live terminology path invalid'
  local actual
  actual="$(git hash-object "$LIVE")"
  [[ "$actual" == "$BASE_BLOB" ]] || fail "live base mismatch: $actual"
  echo 'LIVE_BASE_GUARD=PASS'
}

verify_live_target(){
  local actual
  actual="$(git hash-object "$LIVE")"
  [[ "$actual" == "$TARGET_BLOB" ]] || fail "live target mismatch: $actual"
  echo 'LIVE_TARGET_BLOB=PASS'
}

download_target(){
  TMP="$(mktemp /tmp/az-ad-terminology-XXXXXX.py)"
  curl -fsSL --retry 2 --connect-timeout 5 --max-time 30     "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$TARGET_COMMIT/az-baze-auth/terminology.py"     -o "$TMP"
  [[ "$(git hash-object "$TMP")" == "$TARGET_BLOB" ]] || fail 'target blob mismatch'
  python3 -m py_compile "$TMP"
  python3 -B - "$TMP" <<'PY'
import importlib.util, sys
path=sys.argv[1]
spec=importlib.util.spec_from_file_location('terminology_target', path)
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
for value in (
    '/reports/advertising/',
    '/reports/advertising.html',
    '/reports/advertising.js',
    '/api/reports/advertising',
    '/api/reports/advertising/metrika/import',
):
    assert m.skip_runtime_rename(value), value
for value in (
    '/reports/management/',
    '/reports/forecast/',
    '/api/reports/management',
):
    assert not m.skip_runtime_rename(value), value
print('TARGET_CONTRACT=PASS')
PY
  echo 'TARGET_BLOB_AND_SYNTAX=PASS'
}

make_backup(){
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP="$BACKUP_ROOT/advertising-terminology-$stamp"
  mkdir -p "$BACKUP"
  cp -a -- "$LIVE" "$BACKUP/terminology.py"
  [[ "$(git hash-object "$BACKUP/terminology.py")" == "$BASE_BLOB" ]] || fail 'file backup verify failed'
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
  echo "BACKUP=$BACKUP"
  echo 'BACKUP_VERIFY=PASS'
}

restore_all(){
  echo 'ROLLBACK=START'
  systemctl stop "$SERVICE" || true
  if [[ -n "$BACKUP" && -f "$BACKUP/terminology.py" ]]; then
    cp -a -- "$BACKUP/terminology.py" "$LIVE"
  fi
  if [[ -n "$BACKUP" && -f "$BACKUP/auth.db" ]]; then
    cp -a -- "$BACKUP/auth.db" "$DB"
  fi
  systemctl start "$SERVICE" || true
  wait_health || true
  echo 'ROLLBACK=COMPLETED'
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
  echo 'ADVERTISING_TERMINOLOGY_PREFLIGHT=PASS; LIVE_CHANGES=NONE'
}

on_error(){
  local code="$1"
  trap - ERR
  if [[ "$INSTALLED" == '1' ]]; then restore_all; fi
  exit "$code"
}
trap 'on_error $?' ERR

case "$MODE" in
  --preflight)
    preflight
    ;;
  --apply)
    [[ "$TOKEN" == "$CONFIRM_TOKEN" ]] || fail 'wrong confirm token'
    preflight
    DB_BEFORE="$(db_fingerprint)"
    make_backup

    systemctl stop "$SERVICE"
    echo 'SERVICE_STOPPED=PASS'

    uid="$(stat -c %u "$LIVE")"
    gid="$(stat -c %g "$LIVE")"
    mode="$(stat -c %a "$LIVE")"
    install -o "$uid" -g "$gid" -m "$mode" "$TMP" "$LIVE"
    INSTALLED=1
    verify_live_target

    [[ "$(db_fingerprint)" == "$DB_BEFORE" ]] || fail 'database changed before service start'
    echo 'DB_EXACT_UNCHANGED_BEFORE_START=PASS'

    systemctl start "$SERVICE"
    wait_health || fail 'post-health failed'
    echo 'SERVICE_HEALTH_POST=PASS'
    verify_live_target

    DB_AFTER="$(db_fingerprint)"
    [[ "$DB_AFTER" == "$DB_BEFORE" ]] || fail 'database changed during service restart'
    echo 'DB_EXACT_UNCHANGED_POST_START=PASS'

    INSTALLED=0
    echo 'ADVERTISING_TERMINOLOGY_PRODUCTION_DEPLOY=PASS'
    echo "TARGET_COMMIT=$TARGET_COMMIT"
    echo "BACKUP=$BACKUP"
    echo 'CHANGED_FILES=1; DATABASE_WRITES=NONE'
    ;;
  *)
    fail 'use --preflight or --apply <token>'
    ;;
esac
