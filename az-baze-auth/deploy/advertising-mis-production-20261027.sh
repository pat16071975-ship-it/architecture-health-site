#!/usr/bin/env bash
set -Eeuo pipefail

REPO="pat16071975-ship-it/architecture-health-site"
BASE_COMMIT="9127255f0e6287e086a18cf6812dc3dc1114c526"
TARGET_COMMIT="496cbb94e2ff453c057a4cf1cd999f31d9ba5496"
SOURCE_HEAD="70b13aa2288554e55bae10f75cbd09a269184b8c"
CONFIRM_TOKEN="ADVERTISING-MIS-PRODUCTION-496CBB94"

APP_ROOT="/opt/az-baze-auth"
ENV_FILE="/etc/az-baze/auth.env"
SERVICE="az-baze-auth"
HEALTH_URL="http://127.0.0.1:8001/health"
BACKUP_ROOT="/var/lib/az-baze/backups"
EXPECTED_HOST="az-server"

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 --preflight"
  echo "   or: $0 --apply $CONFIRM_TOKEN"
  exit 2
fi
MODE="$1"
CONFIRM="${2:-}"
case "$MODE" in
  --preflight) ;;
  --apply)
    [ "$CONFIRM" = "$CONFIRM_TOKEN" ] || { echo "CONFIRMATION=FAIL"; exit 2; }
    ;;
  *) echo "UNKNOWN_MODE=$MODE"; exit 2 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo "STOP: ROOT_REQUIRED"; exit 1; }
[ "$(hostname -s)" = "$EXPECTED_HOST" ] || { echo "STOP: WRONG_HOST"; exit 1; }
[ -f "$ENV_FILE" ] || { echo "STOP: ENV_FILE_MISSING"; exit 1; }
command -v curl >/dev/null
command -v git >/dev/null
command -v systemctl >/dev/null
command -v sha256sum >/dev/null

set -a
source "$ENV_FILE"
set +a

DB="${AZBAZE_DB:-/var/lib/az-baze/auth.db}"
SITE_ROOT="${AZBAZE_SITE_ROOT:-/var/www/az-baze.ru}"
[ "$DB" = "/var/lib/az-baze/auth.db" ] || { echo "STOP: DB_PATH_MISMATCH=$DB"; exit 1; }
[ "$SITE_ROOT" = "/var/www/az-baze.ru" ] || { echo "STOP: SITE_ROOT_MISMATCH=$SITE_ROOT"; exit 1; }
[ -x "$APP_ROOT/venv/bin/python" ] || { echo "STOP: VENV_PYTHON_MISSING"; exit 1; }
[ -f "$DB" ] || { echo "STOP: DB_MISSING"; exit 1; }

STAGE="$(mktemp -d /tmp/az-ad-mis-stage.XXXXXX)"
MANIFEST="$STAGE/manifest.txt"
BASELINE="$STAGE/db-baseline.json"
trap 'rm -rf "$STAGE"' EXIT

cat > "$MANIFEST" <<'EOF'
app|advertising_mis_bridge.py|-|688d4a54083adc999ae2994af53f5efab971904c
app|paid_services_upload.py|d2e682540f831d5adafd9b19e753a01d53749349|28da8edbdf7cae3bb0306c0de28b38dd585fb8ae
app|daily_upload.py|b767890804e1f61a247476f13f73c01e20628f39|0ceb17ceb4b0f3f1feec20e711a22a692f2a3d06
app|completed_upload.py|71baa099276b4e697de2382aac8e9b6fa8e5d4ae|d7a7b395f08cb5d8a253058cfc4fd324b47a4ccc
app|templates/uploads.html|fc7913c0ab812c6709a778aa706d969e438d3c93|d0ecaec2d569c61bec113e30fde63de90ec3b6fe
EOF

live_path(){ printf '%s/%s\n' "$APP_ROOT" "$2"; }
repo_path(){ printf 'az-baze-auth/%s\n' "$2"; }
blob(){ git hash-object "$1"; }

verify_live_base(){
  while IFS='|' read -r scope rel base target; do
    live="$(live_path "$scope" "$rel")"
    if [ "$base" = "-" ]; then
      [ ! -e "$live" ] || { echo "STOP: LIVE_MIXED_NEW_FILE=$live"; return 1; }
    else
      [ -f "$live" ] || { echo "STOP: LIVE_FILE_MISSING=$live"; return 1; }
      actual="$(blob "$live")"
      [ "$actual" = "$base" ] || {
        echo "STOP: LIVE_BASE_BLOB_MISMATCH=$live"
        echo "EXPECTED=$base"
        echo "ACTUAL=$actual"
        return 1
      }
    fi
  done < "$MANIFEST"
  echo "LIVE_BASE_GUARD=PASS"
}

download_targets(){
  while IFS='|' read -r scope rel base target; do
    rp="$(repo_path "$scope" "$rel")"
    out="$STAGE/app/$rel"
    mkdir -p "$(dirname "$out")"
    curl -fsSL --retry 2 --connect-timeout 5 --max-time 30       "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/$rp" -o "$out"
    actual="$(blob "$out")"
    [ "$actual" = "$target" ] || {
      echo "STOP: TARGET_BLOB_MISMATCH=$rp"
      echo "EXPECTED=$target"
      echo "ACTUAL=$actual"
      return 1
    }
  done < "$MANIFEST"
  echo "TARGET_BLOBS=PASS"
}

verify_target_contract(){
  "$APP_ROOT/venv/bin/python" -m py_compile     "$STAGE/app/advertising_mis_bridge.py"     "$STAGE/app/paid_services_upload.py"     "$STAGE/app/daily_upload.py"     "$STAGE/app/completed_upload.py"

  "$APP_ROOT/venv/bin/python" - "$STAGE/app/templates/uploads.html" <<'PY'
from pathlib import Path
import sys
from jinja2 import Environment
p=Path(sys.argv[1])
text=p.read_text(encoding="utf-8")
Environment().parse(text)
assert 'id="reconcileClose"' in text
assert 'aria-label="Закрыть">×</button>' in text
assert ".reconcile-close{width:44px;height:44px" in text
assert "closeBtn?.addEventListener('click',closePreview)" in text
print("JINJA_AND_POPUP_CONTRACT=PASS")
PY

  "$APP_ROOT/venv/bin/python" -     "$STAGE/app/advertising_mis_bridge.py"     "$STAGE/app/paid_services_upload.py"     "$STAGE/app/daily_upload.py" <<'PY'
from pathlib import Path
import sys
bridge=Path(sys.argv[1]).read_text(encoding="utf-8")
paid=Path(sys.argv[2]).read_text(encoding="utf-8")
daily=Path(sys.argv[3]).read_text(encoding="utf-8")
assert 'TRACKING_START = "2026-10-27"' in bridge
assert 'CREATE TABLE IF NOT EXISTS advertising_mis_paid_ingests' in bridge
assert 'CREATE TABLE IF NOT EXISTS advertising_mis_paid_lines' in bridge
assert 'DELETE FROM advertising_clinic_daily WHERE provider=?' in bridge
assert 'DELETE FROM advertising_cohort_daily WHERE provider=?' in bridge
assert 'advertising_mis_bridge.capture_paid_report' in paid
assert paid.index('status = paid_services.store_snapshot') < paid.index('advertising_mis_bridge.capture_paid_report')
assert 'advertising_mis_bridge.rebuild' in daily
print("MIS_BRIDGE_SOURCE_CONTRACT=PASS")
PY
  echo "TARGET_CONTRACT=PASS"
}

db_readonly_precheck(){
  "$APP_ROOT/venv/bin/python" - "$DB" <<'PY'
import sqlite3,sys
db=sys.argv[1]
c=sqlite3.connect(f"file:{db}?mode=ro",uri=True,timeout=5)
try:
    c.execute("PRAGMA query_only=ON")
    assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
    names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required={"advertising_clinic_daily","advertising_cohort_daily","advertising_report_imports"}
    missing=required-names
    assert not missing,f"missing advertising base tables: {sorted(missing)}"
    forbidden={"advertising_mis_paid_ingests","advertising_mis_paid_lines"} & names
    assert not forbidden,f"MIS bridge tables already exist: {sorted(forbidden)}"
finally:
    c.close()
print("DB_READONLY_PRECHECK=PASS")
PY
}

capture_db_baseline(){
  "$APP_ROOT/venv/bin/python" - "$DB" "$BASELINE" <<'PY'
import json,sqlite3,sys
db,out=sys.argv[1:3]
c=sqlite3.connect(f"file:{db}?mode=ro",uri=True,timeout=5)
try:
    c.execute("PRAGMA query_only=ON")
    assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
    objs=[{"type":r[0],"name":r[1],"tbl_name":r[2],"sql":r[3]}
          for r in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
    counts={}
    for o in objs:
        if o["type"]=="table":
            q='"'+o["name"].replace('"','""')+'"'
            counts[o["name"]]=c.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
    json.dump({"objects":objs,"counts":counts},open(out,"w",encoding="utf-8"),ensure_ascii=False,sort_keys=True,indent=2)
finally:
    c.close()
print("DB_BASELINE_CAPTURE=PASS")
PY
}

verify_db_unchanged(){
  "$APP_ROOT/venv/bin/python" - "$DB" "$BASELINE" <<'PY'
import json,sqlite3,sys
db,base=sys.argv[1:3]
b=json.load(open(base,encoding="utf-8"))
c=sqlite3.connect(f"file:{db}?mode=ro",uri=True,timeout=5)
try:
    c.execute("PRAGMA query_only=ON")
    assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
    objs=[{"type":r[0],"name":r[1],"tbl_name":r[2],"sql":r[3]}
          for r in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
    assert objs==b["objects"],"live DB schema changed during file-only deploy"
    for name,count in b["counts"].items():
        q='"'+name.replace('"','""')+'"'
        actual=c.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
        assert actual==count,f"live DB row count changed during deploy: {name}: {count}->{actual}"
finally:
    c.close()
print("DB_UNCHANGED=PASS")
PY
}

isolated_schema_rehearsal(){
  local lab="$STAGE/lab.db"
  "$APP_ROOT/venv/bin/python" - "$DB" "$lab" "$BASELINE" "$STAGE/app/advertising_mis_bridge.py" <<'PY'
import importlib.util,json,sqlite3,sys
src,lab,base,module_path=sys.argv[1:5]
a=sqlite3.connect(f"file:{src}?mode=ro",uri=True)
b=sqlite3.connect(lab)
try:
    a.backup(b)
finally:
    a.close(); b.close()

baseline=json.load(open(base,encoding="utf-8"))
spec=importlib.util.spec_from_file_location("advertising_mis_bridge_stage",module_path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
c=sqlite3.connect(lab)
try:
    before_counts=dict(baseline["counts"])
    m.init_schema(c); c.commit()
    assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
    names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"advertising_mis_paid_ingests","advertising_mis_paid_lines"} <= names
    assert c.execute("SELECT COUNT(*) FROM advertising_mis_paid_ingests").fetchone()[0]==0
    assert c.execute("SELECT COUNT(*) FROM advertising_mis_paid_lines").fetchone()[0]==0
    for name,count in before_counts.items():
        q='"'+name.replace('"','""')+'"'
        assert c.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]==count,name
finally:
    c.close()
print("ISOLATED_SCHEMA_REHEARSAL=PASS")
PY
}

assert_db_quiescent(){
  "$APP_ROOT/venv/bin/python" - "$DB" <<'PY'
import os,sys
db=os.path.realpath(sys.argv[1])
targets={db,db+"-wal",db+"-shm",db+"-journal"}
hits=[]
for pid in os.listdir("/proc"):
    if not pid.isdigit(): continue
    fdroot=f"/proc/{pid}/fd"
    try: fds=os.listdir(fdroot)
    except (FileNotFoundError,PermissionError): continue
    for fd in fds:
        try: target=os.path.realpath(f"{fdroot}/{fd}")
        except OSError: continue
        if target in targets: hits.append((pid,fd,target))
assert not hits,f"DB NOT QUIESCENT: {hits[:20]}"
print("DB_QUIESCENCE=PASS")
PY
}

verify_live_target(){
  while IFS='|' read -r scope rel base target; do
    live="$(live_path "$scope" "$rel")"
    [ -f "$live" ] || { echo "STOP: TARGET_LIVE_MISSING=$live"; return 1; }
    actual="$(blob "$live")"
    [ "$actual" = "$target" ] || {
      echo "STOP: TARGET_LIVE_BLOB_MISMATCH=$live"
      echo "EXPECTED=$target"
      echo "ACTUAL=$actual"
      return 1
    }
  done < "$MANIFEST"
  echo "LIVE_TARGET_BLOBS=PASS"
}

echo "=== ADVERTISING MIS PRODUCTION PREFLIGHT ==="
echo "BASE_COMMIT=$BASE_COMMIT"
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "SOURCE_HEAD=$SOURCE_HEAD"
systemctl is-active --quiet "$SERVICE"
[ "$(curl -fsS "$HEALTH_URL")" = "ok" ]
echo "SERVICE_HEALTH_PRE=PASS"
verify_live_base
download_targets
verify_target_contract
db_readonly_precheck
capture_db_baseline
isolated_schema_rehearsal

if [ "$MODE" = "--preflight" ]; then
  echo "=== FINAL ==="
  echo "ADVERTISING_MIS_PRODUCTION_PREFLIGHT=PASS"
  echo "LIVE_CHANGES=NONE"
  exit 0
fi

# Recheck just before the maintenance window.
verify_live_base
verify_db_unchanged

BACKUP="$BACKUP_ROOT/advertising-mis-$(date -u +%Y%m%dT%H%M%SZ)"
SERVICE_STOPPED=0
CHANGES_STARTED=0
START_ATTEMPTED=0
SUCCESS=0
DB_UID="$(stat -c %u "$DB")"
DB_GID="$(stat -c %g "$DB")"
DB_MODE="$(stat -c %a "$DB")"

restore_files(){
  echo "ROLLBACK_FILES: START"
  while IFS='|' read -r scope rel base target; do
    live="$(live_path "$scope" "$rel")"
    if [ "$base" = "-" ]; then
      rm -f "$live"
    else
      src="$BACKUP/files/$rel"
      [ -f "$src" ] || { echo "ROLLBACK_FILE_MISSING=$src"; return 1; }
      cp -a "$src" "$live"
      [ "$(blob "$live")" = "$base" ] || { echo "ROLLBACK_BLOB_FAIL=$live"; return 1; }
    fi
  done < "$MANIFEST"
  echo "ROLLBACK_FILES=PASS"
}

restore_db_prestart(){
  systemctl stop "$SERVICE" || true
  assert_db_quiescent
  rm -f "$DB-wal" "$DB-shm" "$DB-journal"
  install -o "$DB_UID" -g "$DB_GID" -m "$DB_MODE" "$BACKUP/db/auth.db" "$DB"
  echo "ROLLBACK_DB=PASS"
}

on_exit(){
  rc=$?
  trap - EXIT
  set +e
  if [ "$rc" -ne 0 ] && [ "$SUCCESS" -ne 1 ]; then
    echo "DEPLOY_FAILURE_RC=$rc"
    if [ "$CHANGES_STARTED" -eq 1 ]; then
      if [ "$START_ATTEMPTED" -eq 0 ]; then
        restore_files
        restore_db_prestart
      else
        systemctl stop "$SERVICE" || true
        restore_files
        echo "ROLLBACK_DB=SKIPPED_AFTER_START_ATTEMPT"
      fi
    fi
    systemctl start "$SERVICE" || true
    sleep 2
    if systemctl is-active --quiet "$SERVICE" && [ "$(curl -fsS "$HEALTH_URL" 2>/dev/null)" = "ok" ]; then
      echo "ROLLBACK_SERVICE_HEALTH=PASS"
    else
      echo "ROLLBACK_SERVICE_HEALTH=FAIL"
    fi
    echo "BACKUP=$BACKUP"
  fi
  rm -rf "$STAGE"
  exit "$rc"
}
trap on_exit EXIT

systemctl stop "$SERVICE"
SERVICE_STOPPED=1
if systemctl is-active --quiet "$SERVICE"; then echo "STOP: SERVICE_DID_NOT_STOP"; exit 1; fi
assert_db_quiescent

mkdir -p "$BACKUP/files" "$BACKUP/db"
chmod 700 "$BACKUP"
cp -a "$BASELINE" "$BACKUP/db/baseline.json"
cp -a "$MANIFEST" "$BACKUP/runtime-manifest.txt"
cat > "$BACKUP/deploy-meta.txt" <<EOF
BASE_COMMIT=$BASE_COMMIT
TARGET_COMMIT=$TARGET_COMMIT
SOURCE_HEAD=$SOURCE_HEAD
DEPLOY_SCRIPT_BLOB=$(git hash-object "$0")
UTC_STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
chmod 600 "$BACKUP/db/baseline.json" "$BACKUP/runtime-manifest.txt" "$BACKUP/deploy-meta.txt"

while IFS='|' read -r scope rel base target; do
  [ "$base" = "-" ] && continue
  live="$(live_path "$scope" "$rel")"
  out="$BACKUP/files/$rel"
  mkdir -p "$(dirname "$out")"
  cp -a "$live" "$out"
  [ "$(blob "$out")" = "$base" ] || { echo "STOP: FILE_BACKUP_BLOB_FAIL=$rel"; exit 1; }
done < "$MANIFEST"
echo "FILE_BACKUP=PASS"

"$APP_ROOT/venv/bin/python" - "$DB" "$BACKUP/db/auth.db" <<'PY'
import sqlite3,sys
src,dst=sys.argv[1:3]
a=sqlite3.connect(src);b=sqlite3.connect(dst)
try:a.backup(b)
finally:b.close();a.close()
PY
chmod 600 "$BACKUP/db/auth.db"
sha256sum "$BACKUP/db/auth.db" > "$BACKUP/db/auth.db.sha256"
"$APP_ROOT/venv/bin/python" - "$BACKUP/db/auth.db" "$BASELINE" <<'PY'
import json,sqlite3,sys
db,base=sys.argv[1:3]
baseline=json.load(open(base,encoding="utf-8"))
c=sqlite3.connect(db)
try:
    assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
    for name,count in baseline["counts"].items():
        q='"'+name.replace('"','""')+'"'
        assert c.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]==count,name
finally:c.close()
print("DB_BACKUP_VERIFY=PASS")
PY
echo "DB_BACKUP_SHA256=$(cut -d' ' -f1 "$BACKUP/db/auth.db.sha256")"

CHANGES_STARTED=1
DEFAULT_UID="$(stat -c %u "$APP_ROOT/server.py")"
DEFAULT_GID="$(stat -c %g "$APP_ROOT/server.py")"
DEFAULT_MODE="$(stat -c %a "$APP_ROOT/server.py")"

while IFS='|' read -r scope rel base target; do
  live="$(live_path "$scope" "$rel")"
  src="$STAGE/app/$rel"
  mkdir -p "$(dirname "$live")"
  if [ -e "$live" ]; then
    uid="$(stat -c %u "$live")"; gid="$(stat -c %g "$live")"; mode="$(stat -c %a "$live")"
  else
    uid="$DEFAULT_UID"; gid="$DEFAULT_GID"; mode="$DEFAULT_MODE"
  fi
  install -o "$uid" -g "$gid" -m "$mode" "$src" "$live"
done < "$MANIFEST"
echo "ATOMIC_FILE_INSTALL=PASS"
verify_live_target
verify_db_unchanged

START_ATTEMPTED=1
systemctl start "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE"
SERVICE_STOPPED=0
[ "$(curl -fsS "$HEALTH_URL")" = "ok" ]
echo "SERVICE_HEALTH_POST=PASS"
verify_live_target
verify_db_unchanged

SUCCESS=1
trap - EXIT
rm -rf "$STAGE"
echo "=== FINAL ==="
echo "ADVERTISING_MIS_PRODUCTION_DEPLOY=PASS"
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "SOURCE_HEAD=$SOURCE_HEAD"
echo "BACKUP=$BACKUP"
echo "SERVICE=active"
echo "HEALTH=ok"
echo "CHANGED_RUNTIME_FILES=5"
echo "LIVE_DB_SCHEMA_CHANGED=NO"
echo "TRACKING_START=2026-10-27"
