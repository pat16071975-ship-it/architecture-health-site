#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_COMMIT='9f6511864d0af476979916d4e1f886e43424e84b'
CONFIRM_TOKEN='PAID-CASH-DAILY-9F651186'
SERVICE='az-baze-auth'
DB='/var/lib/az-baze/auth.db'
CONFIG='/var/lib/az-baze/paid-period-confirmations-v1.json'
BACKUP_ROOT='/var/lib/az-baze/backups'
HEALTH='http://127.0.0.1:8001/health'

REL=(
  'az-baze-auth/cash_payments.py'
  'az-baze-auth/cash_upload.py'
  'az-baze-auth/upload_reconcile.py'
  'az-baze-auth/report_storage.py'
  'reports/index.html'
  'reports/period-view.js'
)
LIVE=(
  '/opt/az-baze-auth/cash_payments.py'
  '/opt/az-baze-auth/cash_upload.py'
  '/opt/az-baze-auth/upload_reconcile.py'
  '/opt/az-baze-auth/report_storage.py'
  '/var/www/az-baze.ru/reports/index.html'
  '/var/www/az-baze.ru/reports/period-view.js'
)
BASE_BLOB=(
  '416c219b7fd8bd14c2a28cb823e0ff68e2b4df91'
  'f7b041ec8dd367fb26d1f06e0bb4b33615b34f72'
  '5cad7baa20e2f8c77d7ab389d8137f7d5452db75'
  '45da0bd7939240d5c2e109fea2ddde3519746b31'
  '078c94300e0d8cc2f5aefca469bb573eb02b187c'
  '7692475d3385d3828d589f3a489139e23d9c9056'
)
TARGET_BLOB=(
  '557fab9cf2bb8173d752cb1c226fde4a0fc2bdb9'
  'ae6499ae68de25272758bcbb06b1a6bae90fd027'
  'a0aa7ba33a6850cb8b4f06f19c50fdc200a890f1'
  'a0db6ef5f1c69b19f56ae1ad525afb66aa2b3f6f'
  '3e43b1124cc7f526ece4a5bc6ceaef8ad19f06b7'
  '153b1bba79fe506494ae941dc09254a8cc698988'
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
import hashlib, sqlite3, sys
path=sys.argv[1]
c=sqlite3.connect('file:'+path+'?mode=ro', uri=True, timeout=10)
try:
    c.execute('PRAGMA query_only=ON')
    c.execute('BEGIN')
    assert c.execute('PRAGMA query_only').fetchone()[0] == 1
    h=hashlib.sha256()
    for line in c.iterdump():
        h.update(line.encode('utf-8')); h.update(b'\n')
    print(h.hexdigest())
finally:
    c.close()
PY
}

verify_config(){
  python3 -B - "$CONFIG" <<'PY'
import json, pathlib, sys
p=pathlib.Path(sys.argv[1])
v=json.loads(p.read_text(encoding='utf-8'))
assert v.get('version')==1 and isinstance(v.get('confirmations'),list)
jan=[x for x in v['confirmations'] if isinstance(x,dict) and x.get('reportMonth')=='2026-01']
assert len(jan)==1
x=jan[0]
assert (x.get('kind'),x.get('sourceFrom'),x.get('sourceTo'),x.get('importedAt')) == (
    'period','2026-01-05','2026-01-31','2026-10-29T07:17:46.091831+00:00')
print('JAN_CURRENT_CONFIRMATION=PASS')
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
  STAGE="$(mktemp -d /tmp/az-paid-cash-daily-stage-XXXXXX)"
  local i rel dst url actual
  for i in "${!REL[@]}"; do
    rel="${REL[$i]}"
    dst="$STAGE/$rel"
    mkdir -p -- "$(dirname "$dst")"
    url="https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$TARGET_COMMIT/$rel"
    curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 "$url" -o "$dst"
    actual="$(git_blob "$dst")"
    [[ "$actual" == "${TARGET_BLOB[$i]}" ]] || fail "target blob mismatch $rel: $actual"
  done

  python3 -m py_compile     "$STAGE/az-baze-auth/cash_payments.py"     "$STAGE/az-baze-auth/cash_upload.py"     "$STAGE/az-baze-auth/upload_reconcile.py"     "$STAGE/az-baze-auth/report_storage.py"

  python3 -B - "$STAGE" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
cash=(root/'az-baze-auth/cash_payments.py').read_text(encoding='utf-8')
upload=(root/'az-baze-auth/cash_upload.py').read_text(encoding='utf-8')
storage=(root/'az-baze-auth/report_storage.py').read_text(encoding='utf-8')
index=(root/'reports/index.html').read_text(encoding='utf-8')
view=(root/'reports/period-view.js').read_text(encoding='utf-8')

assert 'PAYER_EVIDENCE_KEYS' in cash
assert 'PATIENT_HEADERS = ("Пациент/Компания", "Пациент / Компания")' in cash
assert 'if legal is None or movement is None or movement <= 0:' in cash
assert 'financial_payload' in cash and 'payer_evidence' in cash
assert 'payer_evidence_backfill' in upload
assert '_backfill_payer_evidence' in upload
assert '_cash_paid_patients_by_date' in storage
assert 'payer_by_date = _management_paid_patients_by_date' in storage
assert 'period-view.js?v=20261029-paid-cash-daily-1' in index
assert '<option value="compare" selected>Сравнение</option><option value="single">На дату</option>' in view
assert '<option value="monthly"' not in view
assert 'cashDirectionAmount' in view
print('TARGET_CONTRACT=PASS')
PY
  echo 'TARGET_BLOBS_AND_SYNTAX=PASS'
}

make_backup(){
  local stamp i
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP="$BACKUP_ROOT/paid-cash-daily-$stamp"
  mkdir -p "$BACKUP/files"
  for i in "${!LIVE[@]}"; do
    cp -a -- "${LIVE[$i]}" "$BACKUP/files/$i"
  done
  python3 -B - "$DB" "$BACKUP/auth.db" <<'PY'
import sqlite3, sys
src=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro', uri=True, timeout=10)
dst=sqlite3.connect(sys.argv[2])
try:
    src.execute('PRAGMA query_only=ON')
    src.backup(dst)
    assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
finally:
    dst.close(); src.close()
PY
  cp -a -- "$CONFIG" "$BACKUP/paid-period-confirmations-v1.json"
  printf 'TARGET_COMMIT=%s\n' "$TARGET_COMMIT" > "$BACKUP/metadata.txt"
  echo "BACKUP=$BACKUP"
  echo 'BACKUP_VERIFY=PASS'
}

restore_files(){
  local i
  echo 'ROLLBACK_FILES=START'
  systemctl stop "$SERVICE" || true
  for i in "${!LIVE[@]}"; do
    [[ -f "$BACKUP/files/$i" ]] || continue
    cp -a -- "$BACKUP/files/$i" "${LIVE[$i]}"
  done
  systemctl start "$SERVICE" || true
  wait_health || true
  echo 'ROLLBACK_FILES=COMPLETED; DB_RESTORE=NOT_PERFORMED'
}

atomic_install(){
  python3 -B - "$STAGE" "${LIVE[@]}" -- "${REL[@]}" <<'PY'
import os, pathlib, sys, tempfile
stage=pathlib.Path(sys.argv[1])
sep=sys.argv.index('--')
live=[pathlib.Path(x) for x in sys.argv[2:sep]]
rel=sys.argv[sep+1:]
assert len(live)==len(rel)
for target,name in zip(live,rel):
    src=stage/name
    st=target.stat()
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
  [[ -f "$CONFIG" && ! -L "$CONFIG" ]] || fail 'confirmation config path invalid'
  systemctl is-active --quiet "$SERVICE" || fail 'service not active'
  health || fail 'pre-health failed'
  echo 'SERVICE_HEALTH_PRE=PASS'
  verify_live_base
  verify_config
  download_targets
  local fp
  fp="$(db_fingerprint)"
  [[ "$fp" =~ ^[0-9a-f]{64}$ ]] || fail 'db fingerprint invalid'
  echo "DB_READONLY_FINGERPRINT=$fp"
  echo 'PAID_CASH_DAILY_PREFLIGHT=PASS; LIVE_CHANGES=NONE'
}

on_error(){
  local code="$1"
  trap - ERR
  if [[ "$INSTALLED" == '1' && -n "$BACKUP" ]]; then restore_files; fi
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
    echo 'PAID_CASH_DAILY_PRODUCTION_DEPLOY=PASS'
    echo "TARGET_COMMIT=$TARGET_COMMIT"
    echo "BACKUP=$BACKUP"
    echo 'SERVICE=active; HEALTH=ok'
    echo 'CHANGED_FILES=6; DATABASE_WRITES_BY_INSTALLER=NONE; BUSINESS_DATA_BACKFILL=NOT_PERFORMED'
    ;;
  *)
    fail 'use --preflight or --apply <token>'
    ;;
esac
