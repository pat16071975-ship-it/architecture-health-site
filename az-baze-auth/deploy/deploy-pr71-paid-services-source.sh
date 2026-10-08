#!/usr/bin/env bash
set -euo pipefail

REPO="pat16071975-ship-it/architecture-health-site"
BASE_COMMIT="742d4b68bf471be32c5a072e5d646f79eeab5e8d"
TARGET_COMMIT="49bdf49628d252e8eccb3f5a55c3810e650e84c1"
SOURCE_HEAD="186598f723a096ac63cb61ee04e79d85866e73db"
CONFIRM_TOKEN="PR71-DEPLOY-49BDF496"

APP_ROOT="/opt/az-baze-auth"
REPORT_ROOT="/var/www/az-baze.ru/reports"
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
CONFIRM=""
if [ "$#" -ge 2 ]; then CONFIRM="$2"; fi
case "$MODE" in
  --preflight) ;;
  --apply)
    [ "$CONFIRM" = "$CONFIRM_TOKEN" ] || { echo "CONFIRMATION FAIL"; exit 2; }
    ;;
  *) echo "UNKNOWN MODE: $MODE"; exit 2 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo "ROOT REQUIRED"; exit 1; }
[ "$(hostname -s)" = "$EXPECTED_HOST" ] || { echo "HOST FAIL"; exit 1; }
[ -f "$ENV_FILE" ] || { echo "ENV FILE MISSING"; exit 1; }

set -a
source "$ENV_FILE"
set +a

DB="$(printenv AZBAZE_DB || true)"
SITE_ROOT="$(printenv AZBAZE_SITE_ROOT || true)"
[ -n "$DB" ] || DB="/var/lib/az-baze/auth.db"
[ -n "$SITE_ROOT" ] || SITE_ROOT="/var/www/az-baze.ru"

[ "$DB" = "/var/lib/az-baze/auth.db" ] || { echo "DB PATH FAIL: $DB"; exit 1; }
[ "$SITE_ROOT" = "/var/www/az-baze.ru" ] || { echo "SITE ROOT FAIL: $SITE_ROOT"; exit 1; }
[ "$REPORT_ROOT" = "$SITE_ROOT/reports" ] || { echo "REPORT ROOT FAIL"; exit 1; }
[ -x "$APP_ROOT/venv/bin/python" ] || { echo "VENV PYTHON MISSING"; exit 1; }
[ -f "$DB" ] || { echo "DB MISSING"; exit 1; }

command -v curl >/dev/null
command -v git >/dev/null
command -v systemctl >/dev/null
command -v sha256sum >/dev/null

STAGE="$(mktemp -d /tmp/az-pr71-stage.XXXXXX)"
MANIFEST_FILE="$STAGE/manifest.txt"
BASELINE_JSON="$STAGE/db-baseline.json"
trap 'rm -rf "$STAGE"' EXIT

cat > "$MANIFEST_FILE" <<'EOF'
app|attention.py|23e2504c5bf081dba4c2be112291847906e46e81|099cb6164ae0612515436c2e0e17ddef6923eeeb
app|cash_upload.py|e23da4cf1cf59e252acf4284e8c576859792b74d|f7b041ec8dd367fb26d1f06e0bb4b33615b34f72
app|completed_upload.py|-|71baa099276b4e697de2382aac8e9b6fa8e5d4ae
app|daily_upload.py|fb3fe7e817a6dca9cb0dd7add5e19281fc49d3a5|fc589d860cecf18bea693d55c3aeeca1dfc4d7f9
app|daily_upload_core.py|cc44ba04a732ca2429c8f938a1daa357f6c6a223|c0c2faee2474d8b8a2f678898bbe0ec7053cfa37
app|economics_control.py|2c352a6e3daee9fdd56bb6608f02ec79262811f1|d48ad4d9d68747504a40e017426196023ca9c3a9
app|finrez.py|189087a3372d06bb3f39626a1b7afe5f403c93b5|479c2c5a10059cbca7efb4e27e0bebbef29d106e
app|paid_services.py|-|9d64a3a379ba1a117263b8d7ef445fdbc33b325f
app|paid_services_upload.py|-|e7d13cc01e62cddd59811aa8533896939fa08251
app|report_storage.py|bf56cac6a120c8cbe584074a1010bac1401da16f|32eb59ee63eda1d71a5535a5d33b6e265a59ee72
app|server.py|afcfc720023290253a00a6d188a334aa20969eae|ef52f1d4c6054efa85a5c9a017ce3313c46e67ac
app|templates/attention.html|6ec306ba17bc27e8063bdbe30721a5fc9cc99361|a6f6155869524c47fc8624014ead6e7fa702da12
app|templates/uploads.html|76e6437ca477654e373fbe5e3eb3887144900ded|e9099204ace220482b344c4979fb4711c6f62394
app|upload_reconcile.py|ba628d34e630ca61a12c4cee5b49623cd94126db|bce8384dbb8d4b6eacf98ad5d4d099705bf39914
report|dashboard.html|92ffb6c20bafbb2f4cc7e915ff2a121cdd2cb58f|5c7c9c1a7a972d34ce7a580c59a39b927b37b8f2
report|finrez-economics-integrated.js|2d045ad01bd66645f740cc049da7407f51a74c21|5b564fda4351a31923f785943ef3be47736b3390
report|finrez.html|08f104f4be2971e3c4211009a57ff656c105d385|297fd09527c49a616de72c5d916e30b2127bb1dc
report|forecast.html|bee46ab1f543efe0f8a37bb20e5475ef8ae23e88|bf8b60d642ae6ece21aa25ac4ed23978440d27da
report|index.html|f28dfc486412651af62d63669d2ebeefc26d5d79|38481ba3e8a19df9cd9479d20a0290fc91f770a2
report|period-view.js|a2da47dcbab94662e0f103129e8b9dc3a4064d12|ccf7ccd6eeb82e3ed3801bc0e7e00be13f295e66
EOF

live_path(){ if [ "$1" = "app" ]; then printf '%s/%s\n' "$APP_ROOT" "$2"; else printf '%s/%s\n' "$REPORT_ROOT" "$2"; fi; }
repo_path(){ if [ "$1" = "app" ]; then printf 'az-baze-auth/%s\n' "$2"; else printf 'reports/%s\n' "$2"; fi; }
blob(){ git hash-object "$1"; }

verify_live_base(){
  while IFS='|' read -r scope rel base target; do
    live="$(live_path "$scope" "$rel")"
    if [ "$base" = "-" ]; then
      [ ! -e "$live" ] || { echo "BASE FAIL expected absent: $live"; return 1; }
    else
      [ -f "$live" ] || { echo "BASE FILE MISSING: $live"; return 1; }
      actual="$(blob "$live")"
      [ "$actual" = "$base" ] || { echo "BASE BLOB FAIL: $live expected=$base actual=$actual"; return 1; }
    fi
  done < "$MANIFEST_FILE"
  echo "LIVE_BASE_GUARD=PASS"
}

download_targets(){
  while IFS='|' read -r scope rel base target; do
    rp="$(repo_path "$scope" "$rel")"
    out="$STAGE/$scope/$rel"
    mkdir -p "$(dirname "$out")"
    curl -fsSL "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/$rp" -o "$out"
    actual="$(blob "$out")"
    [ "$actual" = "$target" ] || { echo "TARGET BLOB FAIL: $rp"; return 1; }
  done < "$MANIFEST_FILE"
  echo "TARGET_BLOBS=PASS"
}

verify_target_contract(){
  "$APP_ROOT/venv/bin/python" -m py_compile \
    "$STAGE/app/attention.py" "$STAGE/app/cash_upload.py" "$STAGE/app/completed_upload.py" \
    "$STAGE/app/daily_upload.py" "$STAGE/app/daily_upload_core.py" "$STAGE/app/economics_control.py" \
    "$STAGE/app/finrez.py" "$STAGE/app/paid_services.py" "$STAGE/app/paid_services_upload.py" \
    "$STAGE/app/report_storage.py" "$STAGE/app/server.py" "$STAGE/app/upload_reconcile.py"

  "$APP_ROOT/venv/bin/python" - "$STAGE/app/templates/attention.html" "$STAGE/app/templates/uploads.html" <<'PY'
from pathlib import Path
import sys
from jinja2 import Environment
env=Environment()
for value in sys.argv[1:]:
    env.parse(Path(value).read_text(encoding="utf-8"))
print("JINJA=PASS")
PY

  grep -Fq 'completed_upload.register_completed_upload(app)' "$STAGE/app/server.py"
  grep -Fq 'paid_services_upload.register_paid_services_upload(app)' "$STAGE/app/server.py"
  grep -Fq '/api/uploads/paid-services/preview' "$STAGE/app/templates/uploads.html"
  grep -Fq '/api/uploads/completed/preview' "$STAGE/app/templates/uploads.html"
  grep -Fq 'План месяца:' "$STAGE/report/dashboard.html"
  grep -Fq 'Оплачено по направлениям' "$STAGE/report/finrez.html"
  grep -Fq 'Факт клиники и «Оплачено» по направлениям' "$STAGE/report/forecast.html"
  if grep -Fq 'Нераспределённые ДС' "$STAGE/report/index.html"; then echo "USER-FACING UNALLOCATED LABEL PRESENT"; return 1; fi
  echo "TARGET_CONTRACT=PASS"
}

db_readonly_precheck(){
  "$APP_ROOT/venv/bin/python" - "$DB" <<'PY'
import sqlite3,sys
db=sys.argv[1]
c=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
try:
    assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
    names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    forbidden={"service_payment_snapshots","service_payment_versions"} & names
    assert not forbidden,f"paid-services tables already exist: {sorted(forbidden)}"
finally:c.close()
print("DB_READONLY_PRECHECK=PASS")
PY
}

capture_db_baseline(){
  "$APP_ROOT/venv/bin/python" - "$DB" "$BASELINE_JSON" <<'PY'
import json,sqlite3,sys
db,out=sys.argv[1:3]
c=sqlite3.connect(db)
try:
    assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
    objs=[{"type":r[0],"name":r[1],"tbl_name":r[2],"sql":r[3]} for r in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
    counts={}
    for o in objs:
        if o["type"]=="table":
            q='"'+o["name"].replace('"','""')+'"'
            counts[o["name"]]=c.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
    json.dump({"objects":objs,"counts":counts},open(out,"w",encoding="utf-8"),ensure_ascii=False,sort_keys=True,indent=2)
finally:c.close()
print("DB_BASELINE_CAPTURE=PASS")
PY
}

assert_db_quiescent(){
  "$APP_ROOT/venv/bin/python" - "$DB" <<'PY'
import os,sys
db=os.path.realpath(sys.argv[1])
targets={db,db+"-wal",db+"-shm",db+"-journal"}
hits=[]
for pid in os.listdir("/proc"):
    if not pid.isdigit():continue
    root=f"/proc/{pid}/fd"
    try:fds=os.listdir(root)
    except (FileNotFoundError,PermissionError):continue
    for fd in fds:
        try:target=os.path.realpath(f"{root}/{fd}")
        except OSError:continue
        if target in targets:hits.append((pid,fd,target))
assert not hits,f"DB NOT QUIESCENT: {hits[:20]}"
print("DB_QUIESCENCE=PASS")
PY
}

verify_db_backup(){
  "$APP_ROOT/venv/bin/python" - "$1" "$BASELINE_JSON" <<'PY'
import json,sqlite3,sys
db,base=sys.argv[1:3]
b=json.load(open(base,encoding="utf-8"))
c=sqlite3.connect(db)
try:
    assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
    objs=[{"type":r[0],"name":r[1],"tbl_name":r[2],"sql":r[3]} for r in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
    assert objs==b["objects"]
    for name,count in b["counts"].items():
        q='"'+name.replace('"','""')+'"'
        assert c.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]==count,name
finally:c.close()
print("DB_BACKUP_VERIFY=PASS")
PY
}

verify_db_migration(){
  "$APP_ROOT/venv/bin/python" - "$DB" "$BASELINE_JSON" <<'PY'
import json,sqlite3,sys
db,base=sys.argv[1:3]
b=json.load(open(base,encoding="utf-8"))
expected={("table","service_payment_snapshots"),("table","service_payment_versions"),("index","idx_service_payment_snapshots_month"),("index","idx_service_payment_versions_date")}
c=sqlite3.connect(db)
try:
    assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
    objs=[{"type":r[0],"name":r[1],"tbl_name":r[2],"sql":r[3]} for r in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
    before={(o["type"],o["name"]):o for o in b["objects"]}
    now={(o["type"],o["name"]):o for o in objs}
    for key,obj in before.items():assert now.get(key)==obj,f"legacy schema changed: {key}"
    assert set(now)-set(before)==expected,f"unexpected schema delta: {sorted(set(now)-set(before))}"
    for name,count in b["counts"].items():
        q='"'+name.replace('"','""')+'"'
        assert c.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]==count,f"legacy count changed: {name}"
    assert c.execute("SELECT COUNT(*) FROM service_payment_snapshots").fetchone()[0]==0
    assert c.execute("SELECT COUNT(*) FROM service_payment_versions").fetchone()[0]==0
finally:c.close()
print("DB_SCHEMA_DELTA=PASS")
PY
}

verify_live_target(){
  while IFS='|' read -r scope rel base target; do
    live="$(live_path "$scope" "$rel")"
    [ -f "$live" ] || { echo "TARGET LIVE MISSING: $live"; return 1; }
    actual="$(blob "$live")"
    [ "$actual" = "$target" ] || { echo "TARGET LIVE BLOB FAIL: $live"; return 1; }
  done < "$MANIFEST_FILE"
  echo "LIVE_TARGET_BLOBS=PASS"
}

echo "=== PR71 CONTROLLED DEPLOY PRECHECK ==="
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "SOURCE_HEAD=$SOURCE_HEAD"
systemctl is-active --quiet "$SERVICE"
[ "$(curl -fsS "$HEALTH_URL")" = "ok" ]
echo "SERVICE_HEALTH_PRE=PASS"
verify_live_base
download_targets
verify_target_contract
db_readonly_precheck

if [ "$MODE" = "--preflight" ]; then
  echo "=== FINAL ==="
  echo "PR71 CONTROLLED DEPLOY PREFLIGHT: PASS"
  echo "LIVE_CHANGES=NONE"
  exit 0
fi

BACKUP="$BACKUP_ROOT/pr71-paid-services-$(date -u +%Y%m%dT%H%M%SZ)"
SERVICE_STOPPED=0
CHANGES_STARTED=0
START_ATTEMPTED=0
SUCCESS=0
DB_UID="$(stat -c %u "$DB")"
DB_GID="$(stat -c %g "$DB")"
DB_MODE="$(stat -c %a "$DB")"

restore_files_only(){
  echo "ROLLBACK_FILES: START"
  while IFS='|' read -r scope rel base target; do
    live="$(live_path "$scope" "$rel")"
    if [ "$base" = "-" ]; then rm -f "$live"; else
      src="$BACKUP/files/$scope/$rel"
      [ -f "$src" ] || { echo "ROLLBACK FILE MISSING: $src"; return 1; }
      cp -a "$src" "$live"
      [ "$(blob "$live")" = "$base" ] || { echo "ROLLBACK BLOB FAIL: $live"; return 1; }
    fi
  done < "$MANIFEST_FILE"
  echo "ROLLBACK_FILES=PASS"
}

restore_db_prestart(){
  systemctl stop "$SERVICE" || true
  assert_db_quiescent
  rm -f "$DB-wal" "$DB-shm" "$DB-journal"
  install -o "$DB_UID" -g "$DB_GID" -m "$DB_MODE" "$BACKUP/db/auth.db" "$DB"
  verify_db_backup "$DB"
  echo "ROLLBACK_DB=PASS"
}

on_exit(){
  rc=$?
  trap - EXIT
  set +e
  if [ "$rc" -ne 0 ] && [ "$SUCCESS" -ne 1 ]; then
    echo "DEPLOY FAILURE rc=$rc"
    if [ "$CHANGES_STARTED" -eq 1 ]; then
      if [ "$START_ATTEMPTED" -eq 0 ]; then
        echo "ROLLBACK_MODE=FILES_AND_DB_PRESTART"
        restore_files_only
        restore_db_prestart
      else
        echo "ROLLBACK_MODE=FILES_ONLY_POSTSTART"
        systemctl stop "$SERVICE" || true
        restore_files_only
        echo "DB RESTORE SKIPPED AFTER START ATTEMPT TO AVOID DATA LOSS"
      fi
    fi
    if [ "$SERVICE_STOPPED" -eq 1 ] || [ "$START_ATTEMPTED" -eq 1 ]; then
      systemctl start "$SERVICE" || true
      sleep 2
      if systemctl is-active --quiet "$SERVICE" && [ "$(curl -fsS "$HEALTH_URL" 2>/dev/null)" = "ok" ]; then echo "ROLLBACK_SERVICE_HEALTH=PASS"; else echo "ROLLBACK_SERVICE_HEALTH=FAIL"; fi
    fi
    echo "VERIFIED_BACKUP=$BACKUP"
  fi
  rm -rf "$STAGE"
  exit "$rc"
}
trap on_exit EXIT

systemctl stop "$SERVICE"
SERVICE_STOPPED=1
if systemctl is-active --quiet "$SERVICE"; then echo "SERVICE STOP FAIL"; exit 1; fi
assert_db_quiescent

mkdir -p "$BACKUP/files" "$BACKUP/db"
chmod 700 "$BACKUP"
capture_db_baseline
cp -a "$BASELINE_JSON" "$BACKUP/db/baseline.json"
cp -a "$MANIFEST_FILE" "$BACKUP/runtime-manifest.txt"
cat > "$BACKUP/deploy-meta.txt" <<EOF
BASE_COMMIT=$BASE_COMMIT
TARGET_COMMIT=$TARGET_COMMIT
SOURCE_HEAD=$SOURCE_HEAD
DEPLOY_SCRIPT_BLOB_PENDING_RUNTIME_VERIFICATION=1
UTC_STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
chmod 600 "$BACKUP/db/baseline.json" "$BACKUP/runtime-manifest.txt" "$BACKUP/deploy-meta.txt"
echo "BACKUP_METADATA=PASS"

while IFS='|' read -r scope rel base target; do
  [ "$base" = "-" ] && continue
  live="$(live_path "$scope" "$rel")"
  out="$BACKUP/files/$scope/$rel"
  mkdir -p "$(dirname "$out")"
  cp -a "$live" "$out"
  [ "$(blob "$out")" = "$base" ] || { echo "FILE BACKUP VERIFY FAIL: $rel"; exit 1; }
done < "$MANIFEST_FILE"
echo "FILE_BACKUP_VERIFY=PASS"

"$APP_ROOT/venv/bin/python" - "$DB" "$BACKUP/db/auth.db" <<'PY'
import sqlite3,sys
a=sqlite3.connect(sys.argv[1]);b=sqlite3.connect(sys.argv[2])
try:a.backup(b)
finally:b.close();a.close()
PY
chmod 600 "$BACKUP/db/auth.db"
verify_db_backup "$BACKUP/db/auth.db"
sha256sum "$BACKUP/db/auth.db" > "$BACKUP/db/auth.db.sha256"
echo "DB_BACKUP_SHA256=$(cut -d' ' -f1 "$BACKUP/db/auth.db.sha256")"

CHANGES_STARTED=1
NEW_UID="$(stat -c %u "$APP_ROOT/server.py")"
NEW_GID="$(stat -c %g "$APP_ROOT/server.py")"
NEW_MODE="$(stat -c %a "$APP_ROOT/server.py")"

while IFS='|' read -r scope rel base target; do
  live="$(live_path "$scope" "$rel")"
  src="$STAGE/$scope/$rel"
  mkdir -p "$(dirname "$live")"
  if [ -e "$live" ]; then uid="$(stat -c %u "$live")";gid="$(stat -c %g "$live")";mode="$(stat -c %a "$live")"; else uid="$NEW_UID";gid="$NEW_GID";mode="$NEW_MODE"; fi
  install -o "$uid" -g "$gid" -m "$mode" "$src" "$live"
done < "$MANIFEST_FILE"
verify_live_target

cd "$APP_ROOT"
AZBAZE_DB="$DB" AZBAZE_SITE_ROOT="$SITE_ROOT" "$APP_ROOT/venv/bin/python" - <<'PY'
import re,sqlite3
import paid_services
assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|REPLACE)\b",paid_services.SCHEMA,re.I)
c=sqlite3.connect("/var/lib/az-baze/auth.db")
try:paid_services.init_schema(c);c.commit()
finally:c.close()
print("PAID_SERVICES_SCHEMA_APPLY=PASS")
PY
verify_db_migration
assert_db_quiescent

START_ATTEMPTED=1
systemctl start "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE"
SERVICE_STOPPED=0
[ "$(curl -fsS "$HEALTH_URL")" = "ok" ]
echo "SERVICE_HEALTH_POST=PASS"
verify_live_target
verify_db_migration

SUCCESS=1
trap - EXIT
rm -rf "$STAGE"
echo "=== FINAL ==="
echo "PR71 CONTROLLED DEPLOY: PASS"
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "SOURCE_HEAD=$SOURCE_HEAD"
echo "BACKUP=$BACKUP"
echo "SERVICE=active"
echo "HEALTH=ok"
echo "DB_SCHEMA=paid-services-additive-v2"
