#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_COMMIT='37973940903a798e0061c9c35f1b4559fc10920b'
CONFIRM_TOKEN='PAYER-EVIDENCE-V2-37973940'
SERVICE='az-baze-auth'
DB='/var/lib/az-baze/auth.db'
BACKUP_ROOT='/var/lib/az-baze/backups'
HEALTH='http://127.0.0.1:8001/health'

REL=(
  'az-baze-auth/cash_payments.py'
  'az-baze-auth/upload_reconcile.py'
  'az-baze-auth/cash_upload.py'
)
LIVE=(
  '/opt/az-baze-auth/cash_payments.py'
  '/opt/az-baze-auth/upload_reconcile.py'
  '/opt/az-baze-auth/cash_upload.py'
)
BASE_BLOB=(
  'd7c952f525e4fde687b9ad58a6933b1ccb3024a3'
  'f521694ea986bf8d3fb61718538e4b6d39aa9d8c'
  '34f40d075ccc21c7f650830a8aeb8dc7f7493ed0'
)
TARGET_BLOB=(
  '8e90ce33b8d36f51a46f3eaf13d55fce6ac8bea9'
  '6945643c50da4054ad18ca44885cd4ce59c422c9'
  '58a3958be43db73666d84ab7010e00950af515e4'
)

MODE="${1:-}"
TOKEN="${2:-}"
STAGE=''
BACKUP=''
INSTALLED=0

cleanup(){ [[ -n "${STAGE:-}" && -d "$STAGE" ]] && rm -rf -- "$STAGE"; }
trap cleanup EXIT
fail(){ echo "STOP: $*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || fail "missing command $1"; }
git_blob(){ git hash-object "$1"; }
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
        h.update(line.encode());h.update(b'\n')
    print(h.hexdigest())
finally:c.close()
PY
}

verify_live_base(){
  local i actual
  for i in "${!LIVE[@]}"; do
    [[ -f "${LIVE[$i]}" && ! -L "${LIVE[$i]}" ]] || fail "live path invalid ${LIVE[$i]}"
    actual="$(git_blob "${LIVE[$i]}")"
    [[ "$actual" == "${BASE_BLOB[$i]}" ]] || fail "live base mismatch ${LIVE[$i]}: $actual"
  done
  echo 'LIVE_BASE_GUARD=PASS'
}

verify_live_target(){
  local i actual
  for i in "${!LIVE[@]}"; do
    actual="$(git_blob "${LIVE[$i]}")"
    [[ "$actual" == "${TARGET_BLOB[$i]}" ]] || fail "live target mismatch ${LIVE[$i]}: $actual"
  done
  echo 'LIVE_TARGET_BLOBS=PASS'
}

download_targets(){
  STAGE="$(mktemp -d /tmp/az-payer-v2-stage-XXXXXX)"
  local i rel dst url actual
  for i in "${!REL[@]}"; do
    rel="${REL[$i]}"; dst="$STAGE/$rel"
    mkdir -p -- "$(dirname "$dst")"
    url="https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$TARGET_COMMIT/$rel"
    curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 "$url" -o "$dst"
    actual="$(git_blob "$dst")"
    [[ "$actual" == "${TARGET_BLOB[$i]}" ]] || fail "target blob mismatch $rel: $actual"
  done

  python3 -m py_compile     "$STAGE/az-baze-auth/cash_payments.py"     "$STAGE/az-baze-auth/upload_reconcile.py"     "$STAGE/az-baze-auth/cash_upload.py"

  python3 -B - "$STAGE" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
cash=(root/'az-baze-auth/cash_payments.py').read_text(encoding='utf-8')
reconcile=(root/'az-baze-auth/upload_reconcile.py').read_text(encoding='utf-8')
upload=(root/'az-baze-auth/cash_upload.py').read_text(encoding='utf-8')
assert 'PAYER_EVIDENCE_VERSION = 2' in cash
assert 'version not in {1, 2}' in cash
assert 'old_evidence["version"] == 1' in reconcile
assert 'new_evidence["version"] == 2' in reconcile
assert '.issubset(new_evidence[key])' in reconcile
assert 'cash_payments.cash_core_payload(old)' in reconcile
assert 'v1_to_v2_superset' in upload
assert 'Payer-evidence изменился небезопасно' in upload
print('TARGET_CONTRACT=PASS')
PY
  echo 'TARGET_BLOBS_AND_SYNTAX=PASS'
}

make_backup(){
  local stamp i
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP="$BACKUP_ROOT/payer-evidence-v2-$stamp"
  mkdir -p "$BACKUP/files"
  for i in "${!LIVE[@]}"; do cp -a -- "${LIVE[$i]}" "$BACKUP/files/$i"; done
  python3 -B - "$DB" "$BACKUP/auth.db" <<'PY'
import sqlite3,sys
src=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True,timeout=10)
dst=sqlite3.connect(sys.argv[2])
try:
    src.execute('PRAGMA query_only=ON');src.backup(dst)
    assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
finally:dst.close();src.close()
PY
  printf 'TARGET_COMMIT=%s\n' "$TARGET_COMMIT" > "$BACKUP/metadata.txt"
  echo "BACKUP=$BACKUP"
  echo 'BACKUP_VERIFY=PASS'
}

restore_files(){
  local i
  echo 'ROLLBACK_FILES=START'
  systemctl stop "$SERVICE" || true
  for i in "${!LIVE[@]}"; do
    [[ -f "$BACKUP/files/$i" ]] && cp -a -- "$BACKUP/files/$i" "${LIVE[$i]}"
  done
  systemctl start "$SERVICE" || true
  wait_health || true
  echo 'ROLLBACK_FILES=COMPLETED; DB_RESTORE=NOT_PERFORMED'
}

atomic_install(){
  python3 -B - "$STAGE" "${LIVE[@]}" -- "${REL[@]}" <<'PY'
import os,pathlib,sys,tempfile
stage=pathlib.Path(sys.argv[1]);sep=sys.argv.index('--')
live=[pathlib.Path(x) for x in sys.argv[2:sep]];rel=sys.argv[sep+1:]
assert len(live)==len(rel)
for target,name in zip(live,rel):
    src=stage/name;st=target.stat()
    fd,tmp=tempfile.mkstemp(prefix='.'+target.name+'.',dir=str(target.parent))
    try:
        with os.fdopen(fd,'wb') as out:
            out.write(src.read_bytes());out.flush();os.fsync(out.fileno())
        os.chown(tmp,st.st_uid,st.st_gid);os.chmod(tmp,st.st_mode & 0o7777)
        os.replace(tmp,target)
        dfd=os.open(target.parent,os.O_DIRECTORY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
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
  download_targets
  local fp="$(db_fingerprint)"
  [[ "$fp" =~ ^[0-9a-f]{64}$ ]] || fail 'db fingerprint invalid'
  echo "DB_READONLY_FINGERPRINT=$fp"
  echo 'PAYER_EVIDENCE_V2_PREFLIGHT=PASS; LIVE_CHANGES=NONE'
}

on_error(){
  local code="$1";trap - ERR
  if [[ "$INSTALLED" == '1' && -n "$BACKUP" ]]; then restore_files; fi
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
    echo 'PAYER_EVIDENCE_V2_PRODUCTION_DEPLOY=PASS'
    echo "TARGET_COMMIT=$TARGET_COMMIT"
    echo "BACKUP=$BACKUP"
    echo 'SERVICE=active; HEALTH=ok'
    echo 'CHANGED_FILES=3; DATABASE_WRITES_BY_INSTALLER=NONE; BUSINESS_DATA_BACKFILL=NOT_PERFORMED'
    ;;
  *) fail 'use --preflight or --apply <token>' ;;
esac
