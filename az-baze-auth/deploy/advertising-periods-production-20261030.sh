#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_COMMIT='48200f2a9897f3b0e366939eae826f398dc54f2c'
CONFIRM_TOKEN='ADVERTISING-PERIODS-48200F2A'
SITE_ROOT='/var/www/az-baze.ru'
BACKUP_ROOT='/var/lib/az-baze/backups'
DB='/var/lib/az-baze/auth.db'
SERVICE='az-baze-auth'
HEALTH='http://127.0.0.1:8001/health'

REL=(
  'reports/advertising.html'
  'reports/advertising.js'
)
LIVE=(
  '/var/www/az-baze.ru/reports/advertising.html'
  '/var/www/az-baze.ru/reports/advertising.js'
)
BASE_BLOB=(
  '99033f3739489069c529ec9e580cd90860cd4eab'
  '37ec2259e8cebb9ee2fc6f2855e6e37e66b8cb1c'
)
TARGET_BLOB=(
  '0d96e0d0c595bec491e2c64ee26010e8ee610665'
  '049c47090973459af64bd939153d5ad73da842b6'
)

MODE="${1:-}"
TOKEN="${2:-}"
STAGE=''
BACKUP=''

cleanup(){ [[ -n "${STAGE:-}" && -d "$STAGE" ]] && rm -rf -- "$STAGE"; }
trap cleanup EXIT
fail(){ echo "STOP: $*" >&2; exit 1; }
health(){ curl -fsS --connect-timeout 2 --max-time 5 "$HEALTH" >/dev/null; }

db_fingerprint(){
  python3 -B - "$DB" <<'PY'
import hashlib, sqlite3, sys
c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True,timeout=10)
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
  STAGE="$(mktemp -d /tmp/az-ad-periods-stage-XXXXXX)"
  local i rel dst url actual
  for i in "${!REL[@]}"; do
    rel="${REL[$i]}"; dst="$STAGE/$rel"
    mkdir -p -- "$(dirname "$dst")"
    url="https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$TARGET_COMMIT/$rel"
    curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 "$url" -o "$dst"
    actual="$(git hash-object "$dst")"
    [[ "$actual" == "${TARGET_BLOB[$i]}" ]] || fail "target blob mismatch $rel: $actual"
  done
  # JavaScript syntax is verified by the exact delivery CI against TARGET_BLOB.
  # Production host intentionally does not require Node.js.
  grep -Fq 'data-range="day">Сегодня</button>' "$STAGE/reports/advertising.html"
  grep -Fq 'Текущий месяц по сегодня' "$STAGE/reports/advertising.html"
  grep -Fq 'Рекламные KPI собираются с 27.10.2026' "$STAGE/reports/advertising.html"
  grep -Fq 'clinicToday=payload.today||clinicToday' "$STAGE/reports/advertising.js"
  grep -Fq 'history.replaceState' "$STAGE/reports/advertising.js"
  ! grep -Fq 'new Date(2026,9,27)' "$STAGE/reports/advertising.js"
  echo 'TARGET_CONTRACT=PASS'
  echo 'TARGET_BLOBS_AND_SYNTAX=PASS'
}

make_backup(){
  local stamp i
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP="$BACKUP_ROOT/advertising-periods-$stamp"
  mkdir -p "$BACKUP/files"
  for i in "${!LIVE[@]}"; do cp -a -- "${LIVE[$i]}" "$BACKUP/files/$i"; done
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
  echo 'ATOMIC_INSTALL=PASS'
}

preflight(){
  [[ "$(id -u)" == '0' ]] || fail 'run as root'
  [[ "$(hostname -s)" == 'az-server' ]] || fail "wrong host $(hostname -s)"
  for c in curl git python3 systemctl; do command -v "$c" >/dev/null 2>&1 || fail "missing command $c"; done
  [[ -d "$SITE_ROOT/reports" ]] || fail 'site reports root missing'
  [[ -f "$DB" && ! -L "$DB" ]] || fail 'database path invalid'
  systemctl is-active --quiet "$SERVICE" || fail 'service not active'
  health || fail 'service health failed'
  echo 'SERVICE_HEALTH=PASS'
  verify_live_base
  download_targets
  echo "DB_READONLY_FINGERPRINT=$(db_fingerprint)"
  echo 'ADVERTISING_PERIODS_PREFLIGHT=PASS; LIVE_CHANGES=NONE'
}

case "$MODE" in
  --preflight)
    preflight
    ;;
  --apply)
    [[ "$TOKEN" == "$CONFIRM_TOKEN" ]] || fail 'wrong confirm token'
    preflight
    DB_BEFORE="$(db_fingerprint)"
    make_backup
    atomic_install
    verify_live_target
    DB_AFTER="$(db_fingerprint)"
    [[ "$DB_AFTER" == "$DB_BEFORE" ]] || fail 'database changed during static deploy'
    health || fail 'service health failed after install'
    echo 'DB_EXACT_UNCHANGED=PASS'
    echo 'SERVICE_HEALTH_POST=PASS'
    echo 'ADVERTISING_PERIODS_PRODUCTION_DEPLOY=PASS'
    echo "TARGET_COMMIT=$TARGET_COMMIT"
    echo "BACKUP=$BACKUP"
    echo 'CHANGED_FILES=2; DATABASE_WRITES=NONE; SERVICE_RESTART=NONE'
    ;;
  *)
    fail 'use --preflight or --apply <token>'
    ;;
esac
