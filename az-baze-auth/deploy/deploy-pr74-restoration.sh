#!/usr/bin/env bash
set -euo pipefail

# PR74 controlled file-only deploy.
# PREPARED ONLY. --preflight is read-only. --apply requires exact token.

REPO="pat16071975-ship-it/architecture-health-site"
LIVE_BASE_COMMIT="49bdf49628d252e8eccb3f5a55c3810e650e84c1"
SOURCE_HEAD="e71a52e84870234a43871571c8ecb41d22cef472"
TARGET_COMMIT="e2c4507653a78fab382c74a5d34100f15674de4a"
CONFIRM_TOKEN="PR74-RESTORE-E2C45076"

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

STAGE="$(mktemp -d /tmp/az-pr74-stage.XXXXXX)"
MANIFEST_FILE="$STAGE/manifest.txt"
BASELINE_JSON="$STAGE/db-baseline.json"
trap 'rm -rf "$STAGE"' EXIT

cat > "$MANIFEST_FILE" <<'EOF'
app|attention.py|099cb6164ae0612515436c2e0e17ddef6923eeeb|cf09764ea256811697a2bcaf11d66a7d7018da81
app|daily_upload.py|fc589d860cecf18bea693d55c3aeeca1dfc4d7f9|b767890804e1f61a247476f13f73c01e20628f39
app|finrez.py|479c2c5a10059cbca7efb4e27e0bebbef29d106e|4b5d74a937afc8c83052bb6bf7e99e922b338544
app|paid_services_upload.py|e7d13cc01e62cddd59811aa8533896939fa08251|26ed2c138ef55b84c8f36a147ebe9f4c7813087a
app|server.py|ef52f1d4c6054efa85a5c9a017ce3313c46e67ac|1dbc5b51238efd8488680f3322d6e5d48402fdce
app|templates/attention.html|a6f6155869524c47fc8624014ead6e7fa702da12|6e0f0e26f57280a884578881ec875a65911e4636
app|templates/uploads.html|e9099204ace220482b344c4979fb4711c6f62394|53d0ee65ad795a97ed7a7140341864630a194069
report|dashboard.html|5c7c9c1a7a972d34ce7a580c59a39b927b37b8f2|deec8d5174a1205b405a674c668e571334f8a85c
report|finrez.html|297fd09527c49a616de72c5d916e30b2127bb1dc|67839a1d77de32b5929d2bc0f5548b5ba37bf8cd
report|forecast.html|bf8b60d642ae6ece21aa25ac4ed23978440d27da|93fb996b02e00e9f5e5b5d118a00858c009c5094
report|index.html|38481ba3e8a19df9cd9479d20a0290fc91f770a2|08cc848eb6d5c39fb156330254309f2a1c03b3ed
report|period-view.js|ccf7ccd6eeb82e3ed3801bc0e7e00be13f295e66|1feaaa68cf3939cf2113db245691ae477d2838c8
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
  "$APP_ROOT/venv/bin/python" -m py_compile \
    "$STAGE/app/attention.py" \
    "$STAGE/app/daily_upload.py" \
    "$STAGE/app/finrez.py" \
    "$STAGE/app/paid_services_upload.py" \
    "$STAGE/app/server.py"

  "$APP_ROOT/venv/bin/python" - "$STAGE/app/templates/attention.html" "$STAGE/app/templates/uploads.html" <<'PY'
from pathlib import Path
import sys
from jinja2 import Environment
env = Environment()
for value in sys.argv[1:]:
    env.parse(Path(value).read_text(encoding="utf-8"))
print("JINJA=PASS")
PY

  grep -Fq 'data-az-upload-form' "$STAGE/app/templates/uploads.html"
  grep -Fq 'name="completed"' "$STAGE/app/templates/uploads.html"
  grep -Fq 'name="services"' "$STAGE/app/templates/uploads.html"
  grep -Fq '2. Выручка по направлениям' "$STAGE/app/templates/uploads.html"
  grep -Fq '3. Счета и оплаты' "$STAGE/app/templates/uploads.html"
  grep -Fq '4. Новый отчёт МИС «Выручка по направлениям»' "$STAGE/app/templates/uploads.html"
  grep -Fq '/api/uploads/clinical/preview' "$STAGE/app/templates/uploads.html"
  grep -Fq '/api/uploads/clinical/commit' "$STAGE/app/templates/uploads.html"
  grep -Fq '/api/uploads/paid-services/preview' "$STAGE/app/templates/uploads.html"
  ! grep -Fq 'data-az-completed-upload-form' "$STAGE/app/templates/uploads.html"
  ! grep -Fq 'completed_upload.register_completed_upload(app)' "$STAGE/app/server.py"

  grep -Fq 'paid_services.store_snapshot(' "$STAGE/app/paid_services_upload.py"
  ! grep -Fq 'core.replace_service_month(' "$STAGE/app/paid_services_upload.py"
  ! grep -Fq 'paid_services.overlay_stored_month(' "$STAGE/app/paid_services_upload.py"
  ! grep -Fq 'core._save_blob(' "$STAGE/app/paid_services_upload.py"
  ! grep -Fq 'paid_services.overlay_record_map(' "$STAGE/app/daily_upload.py"
  ! grep -Fq 'paid_services.overlay_stored_month(' "$STAGE/app/daily_upload.py"

  grep -Fq 'Выручка стоматологии' "$STAGE/report/index.html"
  grep -Fq 'Выручка по врачам' "$STAGE/report/period-view.js"
  ! grep -Fq 'Нераспределённые ДС' "$STAGE/report/index.html"
  ! grep -Fq 'Нераспределённые ДС' "$STAGE/report/period-view.js"
  ! grep -Fq 'Нераспределённые ДС' "$STAGE/report/dashboard.html"
  grep -Fq 'data.flatMap(x=>[x.plan,x.factTotal])' "$STAGE/report/dashboard.html"
  grep -Fq 'FULL_MONTHS=ALL_MONTHS.filter(fullMonth)' "$STAGE/report/forecast.html"
  ! grep -Fq 'fullMonth(m)&&rec(m).paidDataComplete' "$STAGE/report/forecast.html"
  grep -Fq 'Структура фактически полученных денег' "$STAGE/report/finrez.html"
  ! grep -Fq 'Прочая / не распределённая сумма' "$STAGE/report/finrez.html"
  grep -Fq 'Выручка по направлениям — с начала месяца' "$STAGE/app/templates/attention.html"

  echo "TARGET_CONTRACT=PASS"
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

echo "=== PR74 CONTROLLED RESTORATION PRECHECK ==="
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

if [ "$MODE" = "--preflight" ]; then
  echo "=== FINAL ==="
  echo "PR74 CONTROLLED DEPLOY PREFLIGHT: PASS"
  echo "LIVE_CHANGES=NONE"
  exit 0
fi

BACKUP="$BACKUP_ROOT/pr74-restoration-$(date -u +%Y%m%dT%H%M%SZ)"
CHANGES_STARTED=0
START_ATTEMPTED=0
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
    systemctl start "$SERVICE" || true
    sleep 2
    if systemctl is-active --quiet "$SERVICE" && [ "$(curl -fsS "$HEALTH_URL" 2>/dev/null)" = "ok" ]; then
      echo "ROLLBACK_SERVICE_HEALTH=PASS"
    else
      echo "ROLLBACK_SERVICE_HEALTH=FAIL"
    fi
    echo "DB_RESTORE=NOT_PERFORMED_FILE_ONLY_DEPLOY"
    echo "VERIFIED_BACKUP=$BACKUP"
  fi
  rm -rf "$STAGE"
  exit "$rc"
}
trap on_exit EXIT

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
sleep 2
systemctl is-active --quiet "$SERVICE"
[ "$(curl -fsS "$HEALTH_URL")" = "ok" ]
echo "SERVICE_HEALTH_POST=PASS"

verify_live_target
verify_db_schema_only

SUCCESS=1
trap - EXIT
rm -rf "$STAGE"

echo "=== FINAL ==="
echo "PR74 CONTROLLED DEPLOY: PASS"
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "SOURCE_HEAD=$SOURCE_HEAD"
echo "BACKUP=$BACKUP"
echo "SERVICE=active"
echo "HEALTH=ok"
echo "DB_MUTATION_BY_DEPLOY=NONE"
