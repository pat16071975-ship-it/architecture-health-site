#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_COMMIT='1c55ee8d4eef13c0e410e29efbcfcbec821e524d'
REPO='pat16071975-ship-it/architecture-health-site'
SERVICE='az-baze-auth'
APP_ROOT='/opt/az-baze-auth'
SITE_ROOT='/var/www/az-baze.ru'
DB='/var/lib/az-baze/auth.db'
ARCHIVE_DIR='/var/lib/az-baze/advertising-metrika-archive'
BACKUP_ROOT='/var/lib/az-baze/backups'
HEALTH='http://127.0.0.1:8001/health'
CONFIRM='AZ-ADVERTISING-FINAL-V2-1C55EE8D'

MODE="${1:---preflight}"
TOKEN="${2:-}"
case "$MODE" in
  --preflight) ;;
  --apply) [ "$TOKEN" = "$CONFIRM" ] || { echo 'CONFIRMATION=FAIL'; exit 2; } ;;
  *) echo "UNKNOWN_MODE=$MODE"; exit 2 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo 'STOP: ROOT_REQUIRED'; exit 1; }
[ "$(hostname -s)" = 'az-server' ] || { echo "STOP: WRONG_HOST=$(hostname -s)"; exit 1; }
command -v git >/dev/null
command -v curl >/dev/null
command -v systemctl >/dev/null
[ -x "$APP_ROOT/venv/bin/python" ] || { echo 'STOP: VENV_PYTHON_MISSING'; exit 1; }
[ -f "$DB" ] || { echo 'STOP: DB_MISSING'; exit 1; }

LIVE_AD="$APP_ROOT/advertising_report.py"
LIVE_UPLOADS="$APP_ROOT/templates/uploads.html"
LIVE_BASE="$APP_ROOT/templates/base.html"
LIVE_HTML="$SITE_ROOT/reports/advertising.html"
LIVE_JS="$SITE_ROOT/reports/advertising.js"

BASE_AD='e003a9ab4cdbd245897d23f2dbd9be471aebcaca'
BASE_UPLOADS='25983e1a40436910db0f7df91369629db75981cd'
BASE_BASE_LIVE='c19bbc4660519f3c47a9dd15ca15488247df4d2b'
BASE_HTML='9256fe0f8dcb6a190f5b8f85839eca5766d32c1e'
BASE_JS='083e3e43abb65d41abc7e01c236bbe3271103613'

TARGET_AD='a2c49523e9ebc90702209ef94cfa1d0e21b6b64a'
TARGET_UPLOADS='c36b7393a90f72626425e92fac8b56b54c335cf6'
TARGET_HTML='d5e91cdbfd24cf65bfba77d2dd6c6a2f30c8a212'
TARGET_JS='4d76056792a0742a694e57bf053d42f4e61da8e9'

blob(){ git hash-object "$1"; }

echo '=== AZ-BAZE ADVERTISING FINAL DEPLOY V2 ==='
echo "TARGET_COMMIT=$TARGET_COMMIT"

check_exact_or_target(){
  local label="$1" file="$2" base="$3" target="$4"
  local actual
  actual="$(blob "$file")"
  if [ "$actual" = "$base" ]; then
    echo "LIVE_FILE=$label;STATE=BASE;BLOB=$actual"
    return 0
  fi
  if [ "$actual" = "$target" ]; then
    echo "LIVE_FILE=$label;STATE=TARGET;BLOB=$actual"
    return 10
  fi
  echo "STOP: LIVE_BLOB_MISMATCH=$label"
  echo "ACTUAL=$actual"
  echo "EXPECTED_BASE=$base"
  echo "EXPECTED_TARGET=$target"
  return 1
}

ALL_TARGET=1
for spec in   "advertising_report|$LIVE_AD|$BASE_AD|$TARGET_AD"   "uploads|$LIVE_UPLOADS|$BASE_UPLOADS|$TARGET_UPLOADS"   "advertising_html|$LIVE_HTML|$BASE_HTML|$TARGET_HTML"   "advertising_js|$LIVE_JS|$BASE_JS|$TARGET_JS"
do
  IFS='|' read -r label file base target <<<"$spec"
  set +e
  check_exact_or_target "$label" "$file" "$base" "$target"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then ALL_TARGET=0
  elif [ "$rc" -eq 10 ]; then :
  else exit "$rc"
  fi
done

LIVE_BASE_BLOB="$(blob "$LIVE_BASE")"
echo "LIVE_FILE=base;BLOB=$LIVE_BASE_BLOB"
if [ "$LIVE_BASE_BLOB" != "$BASE_BASE_LIVE" ]; then
  echo 'STOP: LIVE_BLOB_MISMATCH=base'
  echo "ACTUAL=$LIVE_BASE_BLOB"
  echo "EXPECTED_LIVE_BASE=$BASE_BASE_LIVE"
  exit 1
fi

systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_NOT_ACTIVE'; exit 1; }
[ "$(curl -fsS "$HEALTH")" = 'ok' ] || { echo 'STOP: HEALTH_PRE_FAIL'; exit 1; }
echo 'SERVICE_HEALTH_PRE=PASS'

STAGE="$(mktemp -d /tmp/az-advertising-final-v2.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

fetch_target(){
  local repo_path="$1" output="$2" expected="$3"
  curl -fsSL --retry 2 --connect-timeout 5 --max-time 30     "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/$repo_path"     -o "$output"
  [ "$(blob "$output")" = "$expected" ] || {
    echo "STOP: TARGET_BLOB_FAIL=$repo_path"
    exit 1
  }
}

fetch_target 'az-baze-auth/advertising_report.py' "$STAGE/advertising_report.py" "$TARGET_AD"
fetch_target 'az-baze-auth/templates/uploads.html' "$STAGE/uploads.html" "$TARGET_UPLOADS"
fetch_target 'reports/advertising.html' "$STAGE/advertising.html" "$TARGET_HTML"
fetch_target 'reports/advertising.js' "$STAGE/advertising.js" "$TARGET_JS"
echo 'EXACT_TARGET_FILES=PASS'

cp -a "$LIVE_BASE" "$STAGE/base.html"
"$APP_ROOT/venv/bin/python" - "$STAGE/base.html" <<'PY'
from pathlib import Path
import difflib, sys

path = Path(sys.argv[1])
before = path.read_text(encoding="utf-8")
old = "[data-az-upload-form],[data-az-cash-upload-form]"
new = old + ",[data-az-metrika-form]"

if new in before:
    print("STOP: METRIKA_GUARD_ALREADY_PRESENT_IN_LIVE_BASE")
    raise SystemExit(1)
if before.count(old) != 1:
    print("STOP: BASE_PATCH_ANCHOR_COUNT="+str(before.count(old)))
    for line in before.splitlines():
        if "form.matches(" in line or "data-az-cash-upload-form" in line:
            print("BASE_RELEVANT_LINE="+line.strip())
    raise SystemExit(1)

after = before.replace(old, new, 1)
if after.count("[data-az-metrika-form]") != 1:
    raise SystemExit("STOP: BASE_PATCH_RESULT_INVALID")

diff = list(difflib.unified_diff(
    before.splitlines(), after.splitlines(),
    fromfile="live-base.html", tofile="patched-base.html", lineterm=""
))
removed = [x for x in diff if x.startswith("-") and not x.startswith("---")]
added = [x for x in diff if x.startswith("+") and not x.startswith("+++")]
if len(removed) != 1 or len(added) != 1:
    print("\n".join(diff))
    raise SystemExit("STOP: BASE_PATCH_NOT_ONE_LINE")

print("BASE_PATCH_DIFF_BEGIN")
print("\n".join(diff))
print("BASE_PATCH_DIFF_END")
path.write_text(after, encoding="utf-8")
print("BASE_PATCH_ONE_LINE=PASS")
PY
PATCHED_BASE_BLOB="$(blob "$STAGE/base.html")"
echo "PATCHED_BASE_BLOB=$PATCHED_BASE_BLOB"

"$APP_ROOT/venv/bin/python" -m py_compile "$STAGE/advertising_report.py"
echo 'PYTHON_SYNTAX=PASS'

"$APP_ROOT/venv/bin/python" - "$STAGE/uploads.html" "$STAGE/base.html" <<'PY'
from pathlib import Path
from jinja2 import Environment
import sys
for name in sys.argv[1:]:
    Environment().parse(Path(name).read_text(encoding="utf-8"))
uploads=Path(sys.argv[1]).read_text(encoding="utf-8")
base=Path(sys.argv[2]).read_text(encoding="utf-8")
for marker in [
    "5. Яндекс Метрика",'name="metrika_zip"',
    "/api/reports/advertising/metrika/import","metrika_test",
    "type!=='clinical'&&out.requires_review",'id="reconcileCloseText"'
]:
    if marker not in uploads:
        raise SystemExit("STOP: UPLOAD_CONTRACT_MISSING="+marker)
if base.count("[data-az-metrika-form]") != 1:
    raise SystemExit("STOP: BASE_METRIKA_GUARD_INVALID")
print("JINJA_PARSE=PASS")
print("UPLOAD_CONTRACT=PASS")
PY

"$APP_ROOT/venv/bin/python" - "$STAGE/advertising_report.py" "$STAGE/advertising.html" "$STAGE/advertising.js" <<'PY'
from pathlib import Path
import sys
py=Path(sys.argv[1]).read_text(encoding="utf-8")
text=Path(sys.argv[2]).read_text(encoding="utf-8")+"\n"+Path(sys.argv[3]).read_text(encoding="utf-8")
for marker in [
    "advertising_metrika_goal_daily","advertising_metrika_imports",
    "provider_known_without_patient_detail","provider_balance_diff",
    "_provider_revenue_summary",'METRIKA_COUNTER_ID = "113776005"'
]:
    if marker not in py:
        raise SystemExit("STOP: PY_CONTRACT_MISSING="+marker)
for marker in [
    "Выручка клиники","Выручка по врачам / исполнителям",
    "Вся сумма «Оплачено» распределена по врачам/исполнителям",
    "Не хватает данных CRM для построения отчёта",
    "Визиты с открытием записи","Визиты с переходом в Medflex"
]:
    if marker not in text:
        raise SystemExit("STOP: UI_CONTRACT_MISSING="+marker)
for marker in ["Выручка новых пациентов за 30 дней","Без построчной детализации в выгрузке МИС"]:
    if marker in text:
        raise SystemExit("STOP: OLD_UI_PRESENT="+marker)
print("ADVERTISING_RUNTIME_CONTRACT=PASS")
PY

if command -v node >/dev/null 2>&1; then
  node --check "$STAGE/advertising.js"
  echo 'JAVASCRIPT_SYNTAX=PASS'
else
  echo 'JAVASCRIPT_SYNTAX=CI_VERIFIED_NODE_NOT_INSTALLED'
fi

SNAPSHOT_BEFORE="$STAGE/core-before.json"
"$APP_ROOT/venv/bin/python" - "$DB" "$SNAPSHOT_BEFORE" <<'PY'
import json, sqlite3, sys
db,out=sys.argv[1:3]
tables=[
 "users","permissions","daily_uploads","cash_receipts_daily",
 "service_payment_periods","service_payment_snapshots","report_data",
 "provider_registry","advertising_clinic_daily","advertising_cohort_daily",
 "advertising_traffic_daily","advertising_report_imports",
]
con=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
con.execute("PRAGMA query_only=ON")
result={}
for table in tables:
    exists=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
    result[table]=con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if exists else None
integrity=con.execute("PRAGMA integrity_check").fetchone()[0]
con.close()
if integrity!="ok": raise SystemExit("STOP: DB_INTEGRITY_PRE="+str(integrity))
open(out,"w",encoding="utf-8").write(json.dumps(result,sort_keys=True))
print("DB_INTEGRITY_PRE=ok")
print("CORE_ROW_COUNTS_PRE="+json.dumps(result,sort_keys=True))
PY

if [ "$MODE" = '--preflight' ]; then
  echo '=== FINAL ==='
  echo 'ADVERTISING_FINAL_V2_PREFLIGHT=PASS'
  echo 'LIVE_CHANGES=NONE'
  echo 'DATABASE_WRITES=NONE'
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_ROOT/advertising-final-v2-$STAMP"
mkdir -p "$BACKUP/files"
chmod 700 "$BACKUP"

for spec in   "advertising_report|$LIVE_AD"   "uploads|$LIVE_UPLOADS"   "base|$LIVE_BASE"   "advertising_html|$LIVE_HTML"   "advertising_js|$LIVE_JS"
do
  IFS='|' read -r label file <<<"$spec"
  cp -a "$file" "$BACKUP/files/$label"
done
echo 'RUNTIME_BACKUP=PASS'

DB_UID="$(stat -c %u "$DB")"
DB_GID="$(stat -c %g "$DB")"
DB_MODE="$(stat -c %a "$DB")"
"$APP_ROOT/venv/bin/python" - "$DB" "$BACKUP/auth.db" <<'PY'
import sqlite3, sys
src,dst=sys.argv[1:3]
source=sqlite3.connect(src)
target=sqlite3.connect(dst)
source.backup(target)
target.close(); source.close()
check=sqlite3.connect(dst).execute("PRAGMA integrity_check").fetchone()[0]
if check!="ok": raise SystemExit("BACKUP_DB_INTEGRITY_FAIL="+str(check))
print("DATABASE_BACKUP=PASS")
PY
chown "$DB_UID:$DB_GID" "$BACKUP/auth.db"
chmod "$DB_MODE" "$BACKUP/auth.db"
echo "BACKUP=$BACKUP"

ARCHIVE_CREATED=0
if [ ! -d "$ARCHIVE_DIR" ]; then
  install -d -o "$DB_UID" -g "$DB_GID" -m 750 "$ARCHIVE_DIR"
  ARCHIVE_CREATED=1
  echo 'METRIKA_ARCHIVE_DIR=CREATED'
else
  echo 'METRIKA_ARCHIVE_DIR=EXISTS'
fi

declare -A UID GID MODEBITS
for spec in   "advertising_report|$LIVE_AD"   "uploads|$LIVE_UPLOADS"   "base|$LIVE_BASE"   "advertising_html|$LIVE_HTML"   "advertising_js|$LIVE_JS"
do
  IFS='|' read -r label file <<<"$spec"
  UID[$label]="$(stat -c %u "$file")"
  GID[$label]="$(stat -c %g "$file")"
  MODEBITS[$label]="$(stat -c %a "$file")"
done

rollback(){
  set +e
  echo 'ROLLBACK=START'
  systemctl stop "$SERVICE" >/dev/null 2>&1
  install -D -o "${UID[advertising_report]}" -g "${GID[advertising_report]}" -m "${MODEBITS[advertising_report]}" "$BACKUP/files/advertising_report" "$LIVE_AD"
  install -D -o "${UID[uploads]}" -g "${GID[uploads]}" -m "${MODEBITS[uploads]}" "$BACKUP/files/uploads" "$LIVE_UPLOADS"
  install -D -o "${UID[base]}" -g "${GID[base]}" -m "${MODEBITS[base]}" "$BACKUP/files/base" "$LIVE_BASE"
  install -D -o "${UID[advertising_html]}" -g "${GID[advertising_html]}" -m "${MODEBITS[advertising_html]}" "$BACKUP/files/advertising_html" "$LIVE_HTML"
  install -D -o "${UID[advertising_js]}" -g "${GID[advertising_js]}" -m "${MODEBITS[advertising_js]}" "$BACKUP/files/advertising_js" "$LIVE_JS"
  rm -f "$DB-wal" "$DB-shm"
  install -D -o "$DB_UID" -g "$DB_GID" -m "$DB_MODE" "$BACKUP/auth.db" "$DB"
  if [ "$ARCHIVE_CREATED" -eq 1 ]; then rmdir "$ARCHIVE_DIR" >/dev/null 2>&1 || true; fi
  systemctl start "$SERVICE" >/dev/null 2>&1
  sleep 2
  ok=1
  systemctl is-active --quiet "$SERVICE" || ok=0
  [ "$(curl -fsS "$HEALTH" 2>/dev/null)" = 'ok' ] || ok=0
  [ "$(blob "$LIVE_BASE" 2>/dev/null)" = "$BASE_BASE_LIVE" ] || ok=0
  if [ "$ok" -eq 1 ]; then echo 'ROLLBACK=PASS'; else echo 'ROLLBACK=FAIL'; fi
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then rollback; fi; rm -rf "$STAGE"; exit "$rc"' EXIT

systemctl stop "$SERVICE"
if systemctl is-active --quiet "$SERVICE"; then echo 'STOP: SERVICE_DID_NOT_STOP'; exit 1; fi
echo 'SERVICE_STOP_FOR_CONTROLLED_APPLY=PASS'

install -D -o "${UID[advertising_report]}" -g "${GID[advertising_report]}" -m "${MODEBITS[advertising_report]}" "$STAGE/advertising_report.py" "$LIVE_AD"
install -D -o "${UID[uploads]}" -g "${GID[uploads]}" -m "${MODEBITS[uploads]}" "$STAGE/uploads.html" "$LIVE_UPLOADS"
install -D -o "${UID[base]}" -g "${GID[base]}" -m "${MODEBITS[base]}" "$STAGE/base.html" "$LIVE_BASE"
install -D -o "${UID[advertising_html]}" -g "${GID[advertising_html]}" -m "${MODEBITS[advertising_html]}" "$STAGE/advertising.html" "$LIVE_HTML"
install -D -o "${UID[advertising_js]}" -g "${GID[advertising_js]}" -m "${MODEBITS[advertising_js]}" "$STAGE/advertising.js" "$LIVE_JS"

[ "$(blob "$LIVE_AD")" = "$TARGET_AD" ] || { echo 'STOP: INSTALL_BLOB_FAIL=advertising_report'; exit 1; }
[ "$(blob "$LIVE_UPLOADS")" = "$TARGET_UPLOADS" ] || { echo 'STOP: INSTALL_BLOB_FAIL=uploads'; exit 1; }
[ "$(blob "$LIVE_BASE")" = "$PATCHED_BASE_BLOB" ] || { echo 'STOP: INSTALL_BLOB_FAIL=base'; exit 1; }
[ "$(blob "$LIVE_HTML")" = "$TARGET_HTML" ] || { echo 'STOP: INSTALL_BLOB_FAIL=advertising_html'; exit 1; }
[ "$(blob "$LIVE_JS")" = "$TARGET_JS" ] || { echo 'STOP: INSTALL_BLOB_FAIL=advertising_js'; exit 1; }
echo 'RUNTIME_FILES_INSTALL=PASS'

systemctl start "$SERVICE"
sleep 3
systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_RESTART_FAIL'; exit 1; }
[ "$(curl -fsS "$HEALTH")" = 'ok' ] || { echo 'STOP: HEALTH_POST_FAIL'; exit 1; }
echo 'SERVICE_HEALTH_POST=PASS'

"$APP_ROOT/venv/bin/python" - "$DB" "$SNAPSHOT_BEFORE" <<'PY'
import json, sqlite3, sys
db,before_path=sys.argv[1:3]
tables=[
 "users","permissions","daily_uploads","cash_receipts_daily",
 "service_payment_periods","service_payment_snapshots","report_data",
 "provider_registry","advertising_clinic_daily","advertising_cohort_daily",
 "advertising_traffic_daily","advertising_report_imports",
]
before=json.load(open(before_path,encoding="utf-8"))
con=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
con.execute("PRAGMA query_only=ON")
after={}
for table in tables:
    exists=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
    after[table]=con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if exists else None
integrity=con.execute("PRAGMA integrity_check").fetchone()[0]
new_tables={
 name: con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None
 for name in ("advertising_metrika_goal_daily","advertising_metrika_imports")
}
con.close()
print("DB_INTEGRITY_POST="+str(integrity))
print("CORE_ROW_COUNTS_POST="+json.dumps(after,sort_keys=True))
print("NEW_METRIKA_TABLES="+json.dumps(new_tables,sort_keys=True))
if integrity!="ok": raise SystemExit("STOP: DB_INTEGRITY_POST_FAIL")
if before!=after: raise SystemExit("STOP: EXISTING_TABLE_ROW_COUNTS_CHANGED")
if not all(new_tables.values()): raise SystemExit("STOP: METRIKA_TABLES_MISSING")
print("EXISTING_DATA_ROW_COUNTS_UNCHANGED=PASS")
print("METRIKA_SCHEMA=PASS")
PY

echo "LIVE_TARGET_BLOB=advertising_report:$(blob "$LIVE_AD")"
echo "LIVE_TARGET_BLOB=uploads:$(blob "$LIVE_UPLOADS")"
echo "LIVE_PATCHED_BLOB=base:$(blob "$LIVE_BASE")"
echo "LIVE_TARGET_BLOB=advertising_html:$(blob "$LIVE_HTML")"
echo "LIVE_TARGET_BLOB=advertising_js:$(blob "$LIVE_JS")"

trap - EXIT
rm -rf "$STAGE"

echo '=== FINAL ==='
echo 'ADVERTISING_FINAL_V2_DEPLOY=PASS'
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "PATCHED_BASE_BLOB=$PATCHED_BASE_BLOB"
echo "BACKUP=$BACKUP"
echo 'CHANGED_RUNTIME_FILES=5'
echo 'DATABASE_SCHEMA_CHANGES=2_NEW_METRIKA_TABLES'
echo 'EXISTING_DATA_ROW_COUNTS_UNCHANGED=PASS'
echo 'SERVICE=active'
echo 'HEALTH=ok'
