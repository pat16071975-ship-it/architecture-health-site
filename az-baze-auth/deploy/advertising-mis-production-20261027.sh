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

db_holders(){
  "$APP_ROOT/venv/bin/python" - "$DB" <<'PY'
import os,sys
db=os.path.realpath(sys.argv[1])
targets={db,db+"-wal",db+"-shm",db+"-journal"}
hits={}
for pid in os.listdir("/proc"):
    if not pid.isdigit(): continue
    fdroot=f"/proc/{pid}/fd"
    try: fds=os.listdir(fdroot)
    except (FileNotFoundError,PermissionError): continue
    for fd in fds:
        try: target=os.path.realpath(f"{fdroot}/{fd}")
        except OSError: continue
        if target in targets:
            hits.setdefault(pid,{"fds":[],"cmd":""})
            hits[pid]["fds"].append((fd,target))
for pid in sorted(hits,key=lambda x:int(x)):
    try:
        raw=open(f"/proc/{pid}/cmdline","rb").read().replace(b"\0",b" ").decode("utf-8","replace").strip()
    except Exception:
        raw=""
    hits[pid]["cmd"]=raw
for pid,item in hits.items():
    print(f"DB_HOLDER_PID={pid} CMD={item['cmd']} FDS={item['fds']}")
if hits:
    raise SystemExit(3)
PY
}

wait_db_quiescent(){
  local attempt=0
  local diag="/tmp/az-ad-mis-db-holders.$$"
  while [ "$attempt" -lt 30 ]; do
    if db_holders >"$diag" 2>&1; then
      rm -f "$diag"
      echo "DB_QUIESCENCE=PASS"
      return 0
    fi
    attempt=$((attempt+1))
    sleep 1
  done
  echo "STOP: DB_NOT_QUIESCENT_AFTER_30S"
  cat "$diag" || true
  rm -f "$diag"
  return 1
}

WORKER_UNITS=()

validate_known_db_worker_units(){
  local match_file="/tmp/az-ad-mis-worker-units.$"
  "$APP_ROOT/venv/bin/python" - "$APP_ROOT" >"$match_file" <<'PY'
import os,sys
root=sys.argv[1]
allowed={
    f"{root}/venv/bin/python {root}/knowledge_video_compat.py --worker",
    f"{root}/venv/bin/python3 {root}/knowledge_video_compat.py --worker",
}
for pid in sorted((p for p in os.listdir("/proc") if p.isdigit()),key=int):
    try:
        cmd=open(f"/proc/{pid}/cmdline","rb").read().replace(b"\0",b" ").decode("utf-8","replace").strip()
    except Exception:
        continue
    if cmd not in allowed:
        continue
    try:
        cgroup=open(f"/proc/{pid}/cgroup","r",encoding="utf-8",errors="replace").read().strip().replace("\n",";")
    except Exception:
        cgroup=""
    print(f"{pid}|{cmd}|{cgroup}")
PY

  if [ ! -s "$match_file" ]; then
    rm -f "$match_file"
    echo "KNOWN_DB_WORKER_UNIT=NONE"
    return 0
  fi

  while IFS='|' read -r pid cmd cgroup; do
    unit="$(printf '%s\n' "$cgroup" | tr ';' '\n' | sed -n 's#^0::/system.slice/\([^/]*\.service\)$#\1#p' | head -n1)"
    [ -n "$unit" ] || {
      echo "STOP: KNOWN_WORKER_WITHOUT_SYSTEMD_UNIT_PID=$pid"
      echo "CMD=$cmd"
      echo "CGROUP=$cgroup"
      rm -f "$match_file"
      return 1
    }
    systemctl is-active --quiet "$unit" || {
      echo "STOP: WORKER_UNIT_NOT_ACTIVE=$unit PID=$pid"
      rm -f "$match_file"
      return 1
    }
    echo "KNOWN_DB_WORKER_UNIT=$unit PID=$pid"
  done < "$match_file"
  rm -f "$match_file"
  echo "KNOWN_DB_WORKER_PREFLIGHT=PASS"
}

discover_and_stop_known_db_workers(){
  local holder_file="/tmp/az-ad-mis-known-holders.$$"
  "$APP_ROOT/venv/bin/python" - "$DB" >"$holder_file" <<'PY'
import os,sys
db=os.path.realpath(sys.argv[1])
targets={db,db+"-wal",db+"-shm",db+"-journal"}
hits=set()
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    fdroot=f"/proc/{pid}/fd"
    try:
        fds=os.listdir(fdroot)
    except (FileNotFoundError,PermissionError):
        continue
    for fd in fds:
        try:
            target=os.path.realpath(f"{fdroot}/{fd}")
        except OSError:
            continue
        if target in targets:
            hits.add(pid)
            break
for pid in sorted(hits,key=lambda x:int(x)):
    try:
        cmd=open(f"/proc/{pid}/cmdline","rb").read().replace(b"\0",b" ").decode("utf-8","replace").strip()
    except Exception:
        cmd=""
    try:
        cgroup=open(f"/proc/{pid}/cgroup","r",encoding="utf-8",errors="replace").read().strip().replace("\n",";")
    except Exception:
        cgroup=""
    print(f"{pid}|{cmd}|{cgroup}")
PY

  if [ ! -s "$holder_file" ]; then
    rm -f "$holder_file"
    echo "KNOWN_DB_WORKERS=NONE"
    return 0
  fi

  while IFS='|' read -r pid cmd cgroup; do
    [ -n "$pid" ] || continue
    case "$cmd" in
      "$APP_ROOT/venv/bin/python $APP_ROOT/knowledge_video_compat.py --worker"|\
      "$APP_ROOT/venv/bin/python3 $APP_ROOT/knowledge_video_compat.py --worker")
        ;;
      *)
        echo "STOP: UNKNOWN_DB_HOLDER_PID=$pid CMD=$cmd"
        echo "CGROUP=$cgroup"
        rm -f "$holder_file"
        return 1
        ;;
    esac

    unit="$(printf '%s\n' "$cgroup" | tr ';' '\n' | sed -n 's#^0::/system.slice/\([^/]*\.service\)$#\1#p' | head -n1)"
    [ -n "$unit" ] || {
      echo "STOP: KNOWN_WORKER_WITHOUT_SYSTEMD_UNIT_PID=$pid"
      echo "CMD=$cmd"
      echo "CGROUP=$cgroup"
      rm -f "$holder_file"
      return 1
    }

    systemctl is-active --quiet "$unit" || {
      echo "STOP: WORKER_UNIT_NOT_ACTIVE=$unit PID=$pid"
      rm -f "$holder_file"
      return 1
    }

    case " ${WORKER_UNITS[*]-} " in
      *" $unit "*) ;;
      *) WORKER_UNITS+=("$unit") ;;
    esac
  done < "$holder_file"
  rm -f "$holder_file"

  for unit in "${WORKER_UNITS[@]}"; do
    echo "STOPPING_DB_WORKER_UNIT=$unit"
    systemctl stop "$unit"
    if systemctl is-active --quiet "$unit"; then
      echo "STOP: WORKER_UNIT_DID_NOT_STOP=$unit"
      return 1
    fi
    echo "DB_WORKER_UNIT_STOPPED=$unit"
  done
}

start_known_db_workers(){
  for unit in "${WORKER_UNITS[@]}"; do
    echo "STARTING_DB_WORKER_UNIT=$unit"
    systemctl start "$unit"
    sleep 1
    systemctl is-active --quiet "$unit" || {
      echo "STOP: WORKER_UNIT_DID_NOT_START=$unit"
      return 1
    }
    echo "DB_WORKER_UNIT_STARTED=$unit"
  done
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
validate_known_db_worker_units

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
  for unit in "${WORKER_UNITS[@]}"; do systemctl stop "$unit" || true; done
  wait_db_quiescent
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
    start_known_db_workers || true
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
discover_and_stop_known_db_workers
wait_db_quiescent

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
start_known_db_workers
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
echo "DB_WORKER_UNITS_RESTARTED=${#WORKER_UNITS[@]}"
echo "CHANGED_RUNTIME_FILES=5"
echo "LIVE_DB_SCHEMA_CHANGED=NO"
echo "TRACKING_START=2026-10-27"
