#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_COMMIT='a534f86834957f3acebd5321c0d2a4cb29c2d246'
CONFIRM_TOKEN='ADVERTISING-YANDEX-CAMPAIGNS-A534F868'
SERVICE='az-baze-auth'
DB='/var/lib/az-baze/auth.db'
BACKUP_ROOT='/var/lib/az-baze/backups'
HEALTH='http://127.0.0.1:8001/health'

REL=(
  'az-baze-auth/advertising_report.py'
  'reports/advertising.html'
  'reports/advertising.js'
)
LIVE=(
  '/opt/az-baze-auth/advertising_report.py'
  '/var/www/az-baze.ru/reports/advertising.html'
  '/var/www/az-baze.ru/reports/advertising.js'
)
BASE_BLOB=(
  '911870573d9b73aa06ace28945923b601e2727de'
  '03b1297892f5b97d12f872d1d29141483305378c'
  '0bc09dc6c1a0013d18877bff8cd110fb94cb0216'
)
TARGET_BLOB=(
  '88a4866cd28003b0a9a2f463cba4df3b072a23ec'
  '5ef1cf91a1822abb987cd9f3d406b8c85e185945'
  '64c1d83104e1ca6de6aa8cc7d5b92195441f997a'
)

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
  local i actual
  for i in "${!LIVE[@]}"; do
    [[ -f "${LIVE[$i]}" && ! -L "${LIVE[$i]}" ]] || fail "live path invalid ${LIVE[$i]}"
    actual="$(git hash-object "${LIVE[$i]}")"
    [[ "$actual" == "${BASE_BLOB[$i]}" ]] || fail "live base mismatch ${LIVE[$i]}: $actual"
  done
  echo 'LIVE_BASE_GUARD=PASS'
}

verify_live_target(){
  local i actual
  for i in "${!LIVE[@]}"; do
    actual="$(git hash-object "${LIVE[$i]}")"
    [[ "$actual" == "${TARGET_BLOB[$i]}" ]] || fail "live target mismatch ${LIVE[$i]}: $actual"
  done
  echo 'LIVE_TARGET_BLOBS=PASS'
}

download_targets(){
  STAGE="$(mktemp -d /tmp/az-ad-yandex-campaigns-stage-XXXXXX)"
  local i rel dst url actual
  for i in "${!REL[@]}"; do
    rel="${REL[$i]}"; dst="$STAGE/$rel"
    mkdir -p -- "$(dirname "$dst")"
    url="https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$TARGET_COMMIT/$rel"
    curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 "$url" -o "$dst"
    actual="$(git hash-object "$dst")"
    [[ "$actual" == "${TARGET_BLOB[$i]}" ]] || fail "target blob mismatch $rel: $actual"
  done

  python3 -m py_compile "$STAGE/az-baze-auth/advertising_report.py"
  grep -Fq 'if source_key == "yandex_ads" and not campaign_name:' "$STAGE/az-baze-auth/advertising_report.py"
  grep -Fq 'return "", "Не определено"' "$STAGE/az-baze-auth/advertising_report.py"
  grep -Fq 'function yandexCampaignRows()' "$STAGE/reports/advertising.js"
  grep -Fq "row.source_key==='yandex_ads'" "$STAGE/reports/advertising.js"
  grep -Fq 'class="source-expand"' "$STAGE/reports/advertising.js"
  grep -Fq 'class="child-row yandex-campaign-row"' "$STAGE/reports/advertising.js"
  grep -Fq "button.textContent=expanded?'+':'−'" "$STAGE/reports/advertising.js"
  grep -Fq '.source-expand{' "$STAGE/reports/advertising.html"
  echo 'TARGET_CONTRACT=PASS'
  echo 'TARGET_BLOBS_AND_SYNTAX=PASS'
}

make_backup(){
  local stamp i
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP="$BACKUP_ROOT/advertising-yandex-campaigns-$stamp"
  mkdir -p "$BACKUP/files"
  for i in "${!LIVE[@]}"; do cp -a -- "${LIVE[$i]}" "$BACKUP/files/$i"; done
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
  python3 -B - "$STAGE" "${LIVE[@]}" -- "${REL[@]}" <<'PY'
import os,pathlib,sys,tempfile
stage=pathlib.Path(sys.argv[1]); sep=sys.argv.index('--')
live=[pathlib.Path(x) for x in sys.argv[2:sep]]; rel=sys.argv[sep+1:]
assert len(live)==len(rel)
for target,name in zip(live,rel):
    src=stage/name; st=target.stat()
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

preflight(){
  [[ "$(id -u)" == '0' ]] || fail 'run as root'
  [[ "$(hostname -s)" == 'az-server' ]] || fail "wrong host $(hostname -s)"
  for c in curl git python3 systemctl; do command -v "$c" >/dev/null 2>&1 || fail "missing command $c"; done
  [[ -f "$DB" && ! -L "$DB" ]] || fail 'database path invalid'
  systemctl is-active --quiet "$SERVICE" || fail 'service not active'
  health || fail 'pre-health failed'
  echo 'SERVICE_HEALTH_PRE=PASS'
  verify_live_base
  download_targets
  echo "DB_READONLY_FINGERPRINT=$(db_fingerprint)"
  echo 'ADVERTISING_YANDEX_CAMPAIGNS_PREFLIGHT=PASS; LIVE_CHANGES=NONE'
}

on_error(){ local code="$1"; trap - ERR; if [[ "$INSTALLED" == '1' && -n "$BACKUP" ]]; then restore_files; fi; exit "$code"; }
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
    echo 'ADVERTISING_YANDEX_CAMPAIGNS_PRODUCTION_DEPLOY=PASS'
    echo "TARGET_COMMIT=$TARGET_COMMIT"
    echo "BACKUP=$BACKUP"
    echo 'CHANGED_FILES=3; DATABASE_WRITES_BY_INSTALLER=NONE'
    ;;
  *)
    fail 'use --preflight or --apply <token>'
    ;;
esac
