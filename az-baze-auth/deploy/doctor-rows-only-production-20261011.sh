#!/usr/bin/env bash
set -euo pipefail

# Doctor rows only, existing AZ-BAZE management report. Exact PR74 production baseline.
# PREPARED ONLY. --preflight is read-only. --apply requires exact token.

REPO="pat16071975-ship-it/architecture-health-site"
LIVE_BASE_COMMIT="e2c4507653a78fab382c74a5d34100f15674de4a"
SOURCE_HEAD="7af9c3838642bb9a26170f4db3c5ce568affb353"
TARGET_COMMIT="7af9c3838642bb9a26170f4db3c5ce568affb353"
CONFIRM_TOKEN="DOCTOR-ROWS-ONLY-7AF9C383"

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
CONFIRM="${2:-}"

case "$MODE" in
  --preflight) ;;
  --apply)
    [ "$CONFIRM" = "$CONFIRM_TOKEN" ] || { echo "CONFIRMATION FAIL"; exit 2; }
    ;;
  *) echo "UNKNOWN MODE: $MODE"; exit 2 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo "ROOT REQUIRED"; exit 1; }
[ "$(hostname -s)" = "$EXPECTED_HOST" ] || { echo "HOST FAIL: $(hostname -s)"; exit 1; }
[ -f "$ENV_FILE" ] || { echo "ENV FILE MISSING"; exit 1; }

set -a
source "$ENV_FILE"
set +a

DB="${AZBAZE_DB:-/var/lib/az-baze/auth.db}"
SITE_ROOT="${AZBAZE_SITE_ROOT:-/var/www/az-baze.ru}"

[ "$DB" = "/var/lib/az-baze/auth.db" ] || { echo "DB PATH FAIL: $DB"; exit 1; }
[ "$SITE_ROOT" = "/var/www/az-baze.ru" ] || { echo "SITE ROOT FAIL: $SITE_ROOT"; exit 1; }
[ "$REPORT_ROOT" = "$SITE_ROOT/reports" ] || { echo "REPORT ROOT FAIL"; exit 1; }
[ -x "$APP_ROOT/venv/bin/python" ] || { echo "VENV PYTHON MISSING"; exit 1; }
[ -f "$DB" ] || { echo "DB MISSING"; exit 1; }

command -v curl >/dev/null
command -v git >/dev/null
command -v systemctl >/dev/null
command -v sha256sum >/dev/null

STAGE="$(mktemp -d /tmp/az-doctors-only.XXXXXX)"
MANIFEST_FILE="$STAGE/manifest.txt"
BASELINE_JSON="$STAGE/db-baseline.json"
trap 'rm -rf "$STAGE"' EXIT

cat > "$MANIFEST_FILE" <<'EOF'
app|report_storage.py|32eb59ee63eda1d71a5535a5d33b6e265a59ee72|e14756f3eb7a311100e08d21a6e2f781231f7fd4
report|index.html|08cc848eb6d5c39fb156330254309f2a1c03b3ed|23554bf780063538c81fd1b9bbce508e054ac268
report|period-view.js|1feaaa68cf3939cf2113db245691ae477d2838c8|b534186f91805e7d60d571807d94e2b6c1a7ce44
EOF

live_path() {
  if [ "$1" = "app" ]; then printf '%s/%s\n' "$APP_ROOT" "$2"; else printf '%s/%s\n' "$REPORT_ROOT" "$2"; fi
}
repo_path() {
  if [ "$1" = "app" ]; then printf 'az-baze-auth/%s\n' "$2"; else printf 'reports/%s\n' "$2"; fi
}
blob() { git hash-object "$1"; }

verify_live_base() {
  while IFS='|' read -r scope rel base target; do
    live="$(live_path "$scope" "$rel")"
    [ -f "$live" ] || { echo "BASE FILE MISSING: $live"; return 1; }
    actual="$(blob "$live")"
    [ "$actual" = "$base" ] || {
      echo "BASE BLOB FAIL: $live"
      echo "expected=$base"
      echo "actual=$actual"
      return 1
    }
  done < "$MANIFEST_FILE"
  echo "LIVE_BASE_GUARD=PASS"
}

download_targets() {
  while IFS='|' read -r scope rel base target; do
    rp="$(repo_path "$scope" "$rel")"
    out="$STAGE/$scope/$rel"
    mkdir -p "$(dirname "$out")"
    curl -fsSL "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/$rp" -o "$out"
    actual="$(blob "$out")"
    [ "$actual" = "$target" ] || {
      echo "TARGET BLOB FAIL: $rp expected=$target actual=$actual"
      return 1
    }
  done < "$MANIFEST_FILE"
  echo "TARGET_BLOBS=PASS"
}

verify_target_contract() {
  "$APP_ROOT/venv/bin/python" -m py_compile "$STAGE/app/report_storage.py"
  grep -Fq 'def _management_doctor_paid_snapshots(conn):' "$STAGE/app/report_storage.py"
  grep -Fq 'doctorPaidSnapshots=_management_doctor_paid_snapshots(db())' "$STAGE/app/report_storage.py"
  grep -Fq 'const dentRev=Object.values(r.dentists||{}).reduce' "$STAGE/report/index.html"
  grep -Fq 'const clinicRev=Object.values(r.clinicDocs||{}).reduce' "$STAGE/report/index.html"
  grep -Fq 'doctorPeriodPaid(rows' "$STAGE/report/period-view.js"
  grep -Fq 'window.azDoctorHasPaidForDate' "$STAGE/report/period-view.js"
  grep -Fq 'period-view.js?v=20261011-doctors-1' "$STAGE/report/index.html"
  grep -Fq "addMetric(tbody,'Факт'" "$STAGE/report/period-view.js"
  echo "DOCTOR_ONLY_TARGET_CONTRACT=PASS"
}

paid_source_precheck() {
  "$APP_ROOT/venv/bin/python" - "$DB" <<'PY'
import sqlite3,sys
c=sqlite3.connect(f"file:{sys.argv[1]}?mode=ro",uri=True)
try:
    c.execute("PRAGMA query_only=ON")
    source=c.execute(
        "SELECT COUNT(*),COUNT(DISTINCT month) FROM service_payment_snapshots"
    ).fetchone()
    count,months=source
    print(f"CONFIRMED_MIS_SNAPSHOTS={count} | months={months}")
    if count==0:
        raise SystemExit("STOP: новый МИС «Выручка по направлениям» ещё не загружен на рабочий сервер; рабочие файлы НЕ изменены")
    dates=[row[0] for row in c.execute(
        "SELECT DISTINCT month FROM service_payment_snapshots ORDER BY month"
    )]
    print("MIS_MONTHS="+",".join(dates))
finally:
    c.close()
PY
}

wait_service_health() {
  local tries=0
  while [ "$tries" -lt 60 ]; do
    if systemctl is-active --quiet "$SERVICE" && [ "$(curl --max-time 3 -fsS "$HEALTH_URL" 2>/dev/null)" = "ok" ]; then
      echo "SERVICE_HEALTH_READY=PASS"
      return 0
    fi
    tries=$((tries+1))
    sleep 1
  done
  echo "SERVICE_HEALTH_READY=FAIL"
  return 1
}

db_readonly_precheck() {
  "$APP_ROOT/venv/bin/python" - "$DB" <<'PY'
import sqlite3, sys
db=sys.argv[1]
c=sqlite3.connect(f"file:{db}?mode=ro", uri=True)
try:
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required={"service_payment_snapshots","service_payment_versions"}
    assert required <= names, f"missing paid snapshot tables: {sorted(required-names)}"
finally:
    c.close()
print("DB_READONLY_PRECHECK=PASS")
PY
}

capture_db_baseline() {
  "$APP_ROOT/venv/bin/python" - "$DB" "$BASELINE_JSON" <<'PY'
import hashlib, json, sqlite3, sys
db,out=sys.argv[1:3]
c=sqlite3.connect(db)
try:
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    schema=[tuple(r) for r in c.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    )]
    dump_hash=hashlib.sha256("\n".join(c.iterdump()).encode("utf-8")).hexdigest()
    json.dump({"schema":schema,"dump_sha256":dump_hash},open(out,"w",encoding="utf-8"),
              ensure_ascii=False,sort_keys=True,indent=2)
finally:
    c.close()
print("DB_BASELINE_CAPTURE=PASS")
PY
}

verify_db_exact() {
  "$APP_ROOT/venv/bin/python" - "$DB" "$BASELINE_JSON" <<'PY'
import hashlib, json, sqlite3, sys
db,baseline=sys.argv[1:3]
b=json.load(open(baseline,encoding="utf-8"))
c=sqlite3.connect(db)
try:
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    schema=[list(r) for r in c.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    )]
    assert schema == b["schema"], "schema changed during file-only deploy"
    dump_hash=hashlib.sha256("\n".join(c.iterdump()).encode("utf-8")).hexdigest()
    assert dump_hash == b["dump_sha256"], "DB content changed during file-only deploy"
finally:
    c.close()
print("DB_EXACT_UNCHANGED=PASS")
PY
}

verify_db_schema_only() {
  "$APP_ROOT/venv/bin/python" - "$DB" "$BASELINE_JSON" <<'PY'
import json, sqlite3, sys
db,baseline=sys.argv[1:3]
b=json.load(open(baseline,encoding="utf-8"))
c=sqlite3.connect(db)
try:
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    schema=[list(r) for r in c.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    )]
    assert schema == b["schema"], "schema changed after service restart"
finally:
    c.close()
print("DB_SCHEMA_UNCHANGED=PASS")
PY
}

assert_db_quiescent() {
  "$APP_ROOT/venv/bin/python" - "$DB" <<'PY'
import os, sys
db=os.path.realpath(sys.argv[1])
targets={db,db+"-wal",db+"-shm",db+"-journal"}
hits=[]
for pid in os.listdir("/proc"):
    if not pid.isdigit(): continue
    root=f"/proc/{pid}/fd"
    try: fds=os.listdir(root)
    except (FileNotFoundError,PermissionError): continue
    for fd in fds:
        try: target=os.path.realpath(f"{root}/{fd}")
        except OSError: continue
        if target in targets: hits.append((pid,fd,target))
assert not hits, f"DB NOT QUIESCENT: {hits[:20]}"
print("DB_QUIESCENCE=PASS")
PY
}

verify_backup_db() {
  local backup_db="$1"
  "$APP_ROOT/venv/bin/python" - "$backup_db" "$BASELINE_JSON" <<'PY'
import hashlib, json, sqlite3, sys
db,baseline=sys.argv[1:3]
b=json.load(open(baseline,encoding="utf-8"))
c=sqlite3.connect(db)
try:
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    schema=[list(r) for r in c.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    )]
    assert schema == b["schema"]
    dump_hash=hashlib.sha256("\n".join(c.iterdump()).encode("utf-8")).hexdigest()
    assert dump_hash == b["dump_sha256"]
finally:
    c.close()
print("DB_BACKUP_VERIFY=PASS")
PY
}

verify_live_target() {
  while IFS='|' read -r scope rel base target; do
    live="$(live_path "$scope" "$rel")"
    [ -f "$live" ] || { echo "TARGET LIVE MISSING: $live"; return 1; }
    actual="$(blob "$live")"
    [ "$actual" = "$target" ] || {
      echo "TARGET LIVE BLOB FAIL: $live expected=$target actual=$actual"
      return 1
    }
  done < "$MANIFEST_FILE"
  echo "LIVE_TARGET_BLOBS=PASS"
}

echo "=== AZ-BAZE DOCTOR-ROWS-ONLY PRODUCTION PREFLIGHT ==="
echo "LIVE_BASE_COMMIT=$LIVE_BASE_COMMIT"
echo "SOURCE_HEAD=$SOURCE_HEAD"
echo "TARGET_COMMIT=$TARGET_COMMIT"
systemctl is-active --quiet "$SERVICE"
[ "$(curl -fsS "$HEALTH_URL")" = "ok" ]
echo "SERVICE_HEALTH_PRE=PASS"
verify_live_base
download_targets
verify_target_contract
db_readonly_precheck
paid_source_precheck

if [ "$MODE" = "--preflight" ]; then
  echo "=== FINAL ==="
  echo "DOCTOR-ONLY PRODUCTION PREFLIGHT: PASS"
  echo "LIVE_CHANGES=NONE"
  exit 0
fi

BACKUP="$BACKUP_ROOT/doctor-rows-only-$(date -u +%Y%m%dT%H%M%SZ)"
CHANGES_STARTED=0
START_ATTEMPTED=0
SERVICE_STOP_ATTEMPTED=0
SUCCESS=0

restore_files() {
  echo "ROLLBACK_FILES: START"
  while IFS='|' read -r scope rel base target; do
    live="$(live_path "$scope" "$rel")"
    src="$BACKUP/files/$scope/$rel"
    [ -f "$src" ] || { echo "ROLLBACK FILE MISSING: $src"; return 1; }
    cp -a "$src" "$live"
    [ "$(blob "$live")" = "$base" ] || { echo "ROLLBACK BLOB FAIL: $live"; return 1; }
  done < "$MANIFEST_FILE"
  echo "ROLLBACK_FILES=PASS"
}

on_exit() {
  rc=$?
  trap - EXIT
  set +e
  if [ "$rc" -ne 0 ] && [ "$SUCCESS" -ne 1 ]; then
    echo "DEPLOY FAILURE rc=$rc"
    if [ "$CHANGES_STARTED" -eq 1 ]; then
      systemctl stop "$SERVICE" || true
      restore_files
    fi
    if [ "$SERVICE_STOP_ATTEMPTED" -eq 1 ]; then
      systemctl start "$SERVICE" || true
      if wait_service_health; then
        echo "ROLLBACK_SERVICE_HEALTH=PASS"
      else
        echo "ROLLBACK_SERVICE_HEALTH=FAIL"
      fi
    fi
    echo "DB_RESTORE=NOT_PERFORMED_FILE_ONLY_DEPLOY"
    echo "VERIFIED_BACKUP=$BACKUP"
  fi
  rm -rf "$STAGE"
  exit "$rc"
}
trap on_exit EXIT

SERVICE_STOP_ATTEMPTED=1
systemctl stop "$SERVICE"
if systemctl is-active --quiet "$SERVICE"; then echo "SERVICE STOP FAIL"; exit 1; fi
assert_db_quiescent
capture_db_baseline

mkdir -p "$BACKUP/files" "$BACKUP/db"
chmod 700 "$BACKUP"
cp -a "$BASELINE_JSON" "$BACKUP/db/baseline.json"
cp -a "$MANIFEST_FILE" "$BACKUP/runtime-manifest.txt"

while IFS='|' read -r scope rel base target; do
  live="$(live_path "$scope" "$rel")"
  out="$BACKUP/files/$scope/$rel"
  mkdir -p "$(dirname "$out")"
  cp -a "$live" "$out"
  [ "$(blob "$out")" = "$base" ] || { echo "FILE BACKUP VERIFY FAIL: $rel"; exit 1; }
done < "$MANIFEST_FILE"
echo "FILE_BACKUP_VERIFY=PASS"

"$APP_ROOT/venv/bin/python" - "$DB" "$BACKUP/db/auth.db" <<'PY'
import sqlite3,sys
a=sqlite3.connect(sys.argv[1]); b=sqlite3.connect(sys.argv[2])
try: a.backup(b)
finally:
    b.close()
    a.close()
PY
chmod 600 "$BACKUP/db/auth.db"
verify_backup_db "$BACKUP/db/auth.db"
sha256sum "$BACKUP/db/auth.db" > "$BACKUP/db/auth.db.sha256"

cat > "$BACKUP/deploy-meta.txt" <<EOF
LIVE_BASE_COMMIT=$LIVE_BASE_COMMIT
SOURCE_HEAD=$SOURCE_HEAD
TARGET_COMMIT=$TARGET_COMMIT
DEPLOY_SCRIPT_BLOB=$(git hash-object "$0")
UTC_STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
chmod 600 "$BACKUP/db/baseline.json" "$BACKUP/runtime-manifest.txt" "$BACKUP/deploy-meta.txt" "$BACKUP/db/auth.db.sha256"
echo "BACKUP_METADATA=PASS"
echo "DB_BACKUP_SHA256=$(cut -d' ' -f1 "$BACKUP/db/auth.db.sha256")"

CHANGES_STARTED=1
while IFS='|' read -r scope rel base target; do
  live="$(live_path "$scope" "$rel")"
  src="$STAGE/$scope/$rel"
  uid="$(stat -c %u "$live")"
  gid="$(stat -c %g "$live")"
  mode="$(stat -c %a "$live")"
  install -o "$uid" -g "$gid" -m "$mode" "$src" "$live"
done < "$MANIFEST_FILE"

verify_live_target
verify_db_exact
assert_db_quiescent

START_ATTEMPTED=1
systemctl start "$SERVICE"
wait_service_health
echo "SERVICE_HEALTH_POST=PASS"

verify_live_target
verify_db_schema_only

SUCCESS=1
trap - EXIT
rm -rf "$STAGE"

echo "=== FINAL ==="
echo "DOCTOR_ONLY_PRODUCTION_DEPLOY=PASS"
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "SOURCE_HEAD=$SOURCE_HEAD"
echo "BACKUP=$BACKUP"
echo "SERVICE=active"
echo "HEALTH=ok"
echo "DB_MUTATION_BY_DEPLOY=NONE"
echo "CHANGED_FILES=ONLY_3_REPORT_RUNTIME_FILES"
echo "PAID_SOURCE_WAS_READ_ONLY=PASS"
