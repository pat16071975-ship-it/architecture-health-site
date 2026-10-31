#!/usr/bin/env bash
set -Eeuo pipefail

REPO='pat16071975-ship-it/architecture-health-site'
BASE_COMMIT='4f5553834e15d37554c13d4218d6932045becdd4'
TARGET_COMMIT='91356c4fd597fbcfd3e5af76f57ff18fe82c5c56'
CONFIRM_TOKEN='METRIKA-ONE-CLICK-PRODUCTION-91356C4F'
EXPECTED_HOST='az-server'
SERVICE='az-baze-auth'
APP_ROOT='/opt/az-baze-auth'
SITE_ROOT='/var/www/az-baze.ru'
ENV_FILE='/etc/az-baze/auth.env'
DB='/var/lib/az-baze/auth.db'
BACKUP_ROOT='/var/lib/az-baze/backups'
HEALTH='http://127.0.0.1:8001/health'
LIVE_JS='/var/www/az-baze.ru/reports/advertising.js'
EXPECTED_JS_BLOB='58c7b1ccc84ca0d0848adf5e81440d7233a1d4d8'

MODE="${1:-}"
CONFIRM="${2:-}"
STAGE=''
BACKUP=''
INSTALLED=0

cleanup(){ [[ -n "${STAGE:-}" && -d "$STAGE" ]] && rm -rf -- "$STAGE"; }
trap cleanup EXIT
fail(){ echo "STOP: $*" >&2; exit 1; }
health(){ curl -fsS --connect-timeout 2 --max-time 5 "$HEALTH" >/dev/null; }
wait_health(){ local i; for i in $(seq 1 30); do health && return 0; sleep 1; done; return 1; }
blob(){ git hash-object "$1"; }

cat_manifest(){ cat <<'MANIFEST'
advertising_report.py|7deb8d73fdb633c4644e7b0b6924de4b87bcbff7|d9def18c1a227e388586ddde55f24d1f3ce57e79
metrika_auto.py|-|9967320615c4f9bd6ebdf5062545da3d8a7b3d6c
templates/uploads.html|c36b7393a90f72626425e92fac8b56b54c335cf6|1effc5ae1b123816bde3ff792efaa2e649a6a90d
MANIFEST
}

schema_fingerprint(){
  python3 -B - "$DB" <<'PY'
import hashlib, sqlite3, sys
c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True,timeout=10)
try:
    c.execute('PRAGMA query_only=ON')
    rows=c.execute("SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master ORDER BY type,name,tbl_name").fetchall()
    h=hashlib.sha256()
    for row in rows:
        h.update(repr(tuple(row)).encode()); h.update(b'\n')
    print(h.hexdigest())
finally:
    c.close()
PY
}

db_integrity(){
  python3 -B - "$DB" <<'PY'
import sqlite3,sys
c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True,timeout=10)
try:
    c.execute('PRAGMA query_only=ON')
    print(c.execute('PRAGMA integrity_check').fetchone()[0])
finally:c.close()
PY
}

env_sha(){ sha256sum "$ENV_FILE" | awk '{print $1}'; }

live_path(){ printf '%s/%s\n' "$APP_ROOT" "$1"; }

verify_live_base(){
  local rel base target live actual
  while IFS='|' read -r rel base target; do
    live="$(live_path "$rel")"
    if [[ "$base" == '-' ]]; then
      [[ ! -e "$live" ]] || fail "new target already exists: $live"
    else
      [[ -f "$live" && ! -L "$live" ]] || fail "live path invalid: $live"
      actual="$(blob "$live")"
      [[ "$actual" == "$base" ]] || fail "live base mismatch $live expected=$base actual=$actual"
    fi
  done < <(cat_manifest)
  [[ -f "$LIVE_JS" && ! -L "$LIVE_JS" ]] || fail 'advertising.js path invalid'
  [[ "$(blob "$LIVE_JS")" == "$EXPECTED_JS_BLOB" ]] || fail 'advertising.js drift'
  echo 'LIVE_BASE_GUARD=PASS'
}

verify_live_target(){
  local rel base target live actual
  while IFS='|' read -r rel base target; do
    live="$(live_path "$rel")"
    [[ -f "$live" && ! -L "$live" ]] || fail "live target missing: $live"
    actual="$(blob "$live")"
    [[ "$actual" == "$target" ]] || fail "live target mismatch $live expected=$target actual=$actual"
  done < <(cat_manifest)
  [[ "$(blob "$LIVE_JS")" == "$EXPECTED_JS_BLOB" ]] || fail 'advertising.js drift'
  echo 'LIVE_TARGET_GUARD=PASS'
}

download_targets(){
  STAGE="$(mktemp -d /tmp/az-metrika-one-click-stage.XXXXXX)"
  local rel base target out actual
  while IFS='|' read -r rel base target; do
    out="$STAGE/$rel"
    mkdir -p -- "$(dirname "$out")"
    curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 \
      "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/az-baze-auth/$rel" -o "$out"
    actual="$(blob "$out")"
    [[ "$actual" == "$target" ]] || fail "target blob mismatch $rel expected=$target actual=$actual"
  done < <(cat_manifest)

  "$APP_ROOT/venv/bin/python" -m py_compile \
    "$STAGE/advertising_report.py" "$STAGE/metrika_auto.py"
  "$APP_ROOT/venv/bin/python" - "$STAGE/templates/uploads.html" <<'PY'
from pathlib import Path
import sys
from jinja2 import Environment
text=Path(sys.argv[1]).read_text(encoding='utf-8')
Environment().parse(text)
assert 'id="metrikaAutoLoad"' in text
assert 'Загрузить данные Яндекс Метрики' in text
assert 'Ручная загрузка ZIP — резервный вариант' in text
assert '/api/reports/advertising/metrika/sync' in text
print('JINJA_METRIKA_UI_CONTRACT=PASS')
PY
  grep -Fq 'AZBAZE_YANDEX_METRIKA_TOKEN' "$STAGE/metrika_auto.py"
  grep -Fq 'metrika-live-' "$STAGE/advertising_report.py"
  grep -Fq 'MetrikaSyncLock' "$STAGE/metrika_auto.py"
  grep -Fq 'TRACKING_START = date(2026, 10, 27)' "$STAGE/metrika_auto.py"
  grep -Fq '@app.post("/api/reports/advertising/metrika/sync")' "$STAGE/advertising_report.py"
  echo 'TARGET_CONTRACT=PASS'
  echo 'TARGET_BLOBS_AND_SYNTAX=PASS'
}

make_backup(){
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP="$BACKUP_ROOT/metrika-one-click-$stamp"
  mkdir -p -- "$BACKUP/files/templates"
  chmod 0700 "$BACKUP"
  cp -a -- "$APP_ROOT/advertising_report.py" "$BACKUP/files/advertising_report.py"
  cp -a -- "$APP_ROOT/templates/uploads.html" "$BACKUP/files/templates/uploads.html"
  cp -a -- "$ENV_FILE" "$BACKUP/auth.env"
  python3 -B - "$DB" "$BACKUP/auth.db" <<'PY'
import sqlite3,sys
src=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True,timeout=30)
dst=sqlite3.connect(sys.argv[2])
try:
    src.execute('PRAGMA query_only=ON'); src.backup(dst)
    assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
finally:
    dst.close(); src.close()
PY
  echo "BACKUP=$BACKUP"
  echo 'BACKUP_VERIFY=PASS'
}

atomic_install_one(){
  local src="$1" target="$2" reference="$3"
  python3 -B - "$src" "$target" "$reference" <<'PY'
import os,pathlib,sys,tempfile
src=pathlib.Path(sys.argv[1]); target=pathlib.Path(sys.argv[2]); ref=pathlib.Path(sys.argv[3])
st=ref.stat()
target.parent.mkdir(parents=True,exist_ok=True)
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
}

install_targets(){
  atomic_install_one "$STAGE/advertising_report.py" "$APP_ROOT/advertising_report.py" "$APP_ROOT/advertising_report.py"
  atomic_install_one "$STAGE/metrika_auto.py" "$APP_ROOT/metrika_auto.py" "$APP_ROOT/advertising_report.py"
  atomic_install_one "$STAGE/templates/uploads.html" "$APP_ROOT/templates/uploads.html" "$APP_ROOT/templates/uploads.html"
  INSTALLED=1
  echo 'ATOMIC_INSTALL=PASS'
}

rollback_files(){
  echo 'ROLLBACK_FILES=START'
  systemctl stop "$SERVICE" || true
  cp -a -- "$BACKUP/files/advertising_report.py" "$APP_ROOT/advertising_report.py"
  cp -a -- "$BACKUP/files/templates/uploads.html" "$APP_ROOT/templates/uploads.html"
  rm -f -- "$APP_ROOT/metrika_auto.py"
  systemctl start "$SERVICE" || true
  wait_health || true
  echo 'ROLLBACK_FILES=COMPLETED; DB_RESTORE=NOT_PERFORMED; ENV_RESTORE=NOT_REQUIRED'
}

preflight(){
  [[ "$(id -u)" == '0' ]] || fail 'run as root'
  [[ "$(hostname -s)" == "$EXPECTED_HOST" ]] || fail "wrong host $(hostname -s)"
  for c in curl git python3 systemctl sha256sum; do command -v "$c" >/dev/null 2>&1 || fail "missing command $c"; done
  [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail 'environment file invalid'
  [[ -f "$DB" && ! -L "$DB" ]] || fail 'database path invalid'
  [[ -x "$APP_ROOT/venv/bin/python" ]] || fail 'venv python missing'
  systemctl is-active --quiet "$SERVICE" || fail 'service not active'
  health || fail 'pre-health failed'
  echo 'SERVICE_HEALTH_PRE=PASS'
  verify_live_base
  download_targets
  [[ "$(db_integrity)" == 'ok' ]] || fail 'database integrity failed'
  echo "DB_SCHEMA_FINGERPRINT=$(schema_fingerprint)"
  env_sha >/dev/null
  echo 'ENV_FILE_FINGERPRINT_CAPTURED=PASS'
  echo 'METRIKA_ONE_CLICK_PRODUCTION_PREFLIGHT=PASS; LIVE_CHANGES=NONE'
}

on_error(){
  local code="$1"
  trap - ERR
  if [[ "$INSTALLED" == '1' && -n "$BACKUP" ]]; then rollback_files; fi
  exit "$code"
}
trap 'on_error $?' ERR

case "$MODE" in
  --preflight)
    preflight
    ;;
  --apply)
    [[ "$CONFIRM" == "$CONFIRM_TOKEN" ]] || fail 'wrong confirm token'
    preflight
    make_backup
    SCHEMA_BEFORE="$(schema_fingerprint)"
    ENV_BEFORE="$(env_sha)"

    systemctl stop "$SERVICE"
    echo 'SERVICE_STOPPED=PASS'

    install_targets
    [[ "$(schema_fingerprint)" == "$SCHEMA_BEFORE" ]] || fail 'database schema changed during install'
    [[ "$(env_sha)" == "$ENV_BEFORE" ]] || fail 'environment file changed during product deploy'
    echo 'DB_SCHEMA_UNCHANGED_BEFORE_START=PASS'
    echo 'ENV_FILE_UNCHANGED_BEFORE_START=PASS'
    verify_live_target

    systemctl start "$SERVICE"
    wait_health || fail 'post-health failed'
    echo 'SERVICE_HEALTH_POST=PASS'
    verify_live_target
    [[ "$(schema_fingerprint)" == "$SCHEMA_BEFORE" ]] || fail 'database schema changed after restart'
    [[ "$(env_sha)" == "$ENV_BEFORE" ]] || fail 'environment file changed after restart'
    [[ "$(db_integrity)" == 'ok' ]] || fail 'database integrity failed after restart'
    echo 'DB_SCHEMA_UNCHANGED_POST_START=PASS'
    echo 'ENV_FILE_UNCHANGED_POST_START=PASS'

    INSTALLED=0
    echo 'METRIKA_ONE_CLICK_PRODUCTION_DEPLOY=PASS'
    echo "TARGET_COMMIT=$TARGET_COMMIT"
    echo "BACKUP=$BACKUP"
    echo 'CHANGED_RUNTIME_FILES=3; DB_SCHEMA_CHANGED=NO; SECRET_CHANGES=NONE'
    ;;
  *)
    fail "use --preflight or --apply $CONFIRM_TOKEN"
    ;;
esac
