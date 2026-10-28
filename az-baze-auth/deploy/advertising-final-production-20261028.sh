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
CONFIRM='AZ-ADVERTISING-FINAL-1C55EE8D'

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

declare -A LIVE
declare -A BASE
declare -A TARGET
LIVE[advertising_report]="$APP_ROOT/advertising_report.py"
BASE[advertising_report]='e003a9ab4cdbd245897d23f2dbd9be471aebcaca'
TARGET[advertising_report]='a2c49523e9ebc90702209ef94cfa1d0e21b6b64a'

LIVE[uploads]="$APP_ROOT/templates/uploads.html"
BASE[uploads]='25983e1a40436910db0f7df91369629db75981cd'
TARGET[uploads]='c36b7393a90f72626425e92fac8b56b54c335cf6'

LIVE[base]="$APP_ROOT/templates/base.html"
BASE[base]='3501883c68f1afa7ff32df267bce4a0d00fd9d96'
TARGET[base]='c7156effeb3b10b022da06d20f743196d201005e'

LIVE[advertising_html]="$SITE_ROOT/reports/advertising.html"
BASE[advertising_html]='9256fe0f8dcb6a190f5b8f85839eca5766d32c1e'
TARGET[advertising_html]='d5e91cdbfd24cf65bfba77d2dd6c6a2f30c8a212'

LIVE[advertising_js]="$SITE_ROOT/reports/advertising.js"
BASE[advertising_js]='083e3e43abb65d41abc7e01c236bbe3271103613'
TARGET[advertising_js]='4d76056792a0742a694e57bf053d42f4e61da8e9'

declare -A REPO_PATH
REPO_PATH[advertising_report]='az-baze-auth/advertising_report.py'
REPO_PATH[uploads]='az-baze-auth/templates/uploads.html'
REPO_PATH[base]='az-baze-auth/templates/base.html'
REPO_PATH[advertising_html]='reports/advertising.html'
REPO_PATH[advertising_js]='reports/advertising.js'

blob(){ git hash-object "$1"; }

echo '=== AZ-BAZE ADVERTISING FINAL DEPLOY ==='
echo "TARGET_COMMIT=$TARGET_COMMIT"

ALL_TARGET=1
for key in advertising_report uploads base advertising_html advertising_js; do
  f="${LIVE[$key]}"
  [ -f "$f" ] || { echo "STOP: LIVE_MISSING=$key:$f"; exit 1; }
  actual="$(blob "$f")"
  if [ "$actual" = "${BASE[$key]}" ]; then
    state='BASE'
    ALL_TARGET=0
  elif [ "$actual" = "${TARGET[$key]}" ]; then
    state='TARGET'
  else
    echo "STOP: LIVE_BLOB_MISMATCH=$key"
    echo "ACTUAL=$actual"
    echo "EXPECTED_BASE=${BASE[$key]}"
    echo "EXPECTED_TARGET=${TARGET[$key]}"
    exit 1
  fi
  echo "LIVE_FILE=$key;STATE=$state;BLOB=$actual"
done

systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_NOT_ACTIVE'; exit 1; }
[ "$(curl -fsS "$HEALTH")" = 'ok' ] || { echo 'STOP: HEALTH_PRE_FAIL'; exit 1; }
echo 'SERVICE_HEALTH_PRE=PASS'

STAGE="$(mktemp -d /tmp/az-advertising-final.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

for key in advertising_report uploads base advertising_html advertising_js; do
  target="$STAGE/$key"
  curl -fsSL --retry 2 --connect-timeout 5 --max-time 30     "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/${REPO_PATH[$key]}"     -o "$target"
  [ "$(blob "$target")" = "${TARGET[$key]}" ] || {
    echo "STOP: TARGET_BLOB_FAIL=$key"
    exit 1
  }
  echo "TARGET_FILE=$key;BLOB=PASS"
done

"$APP_ROOT/venv/bin/python" -m py_compile "$STAGE/advertising_report"
"$APP_ROOT/venv/bin/python" - "$STAGE/uploads" "$STAGE/base" <<'PY'
from pathlib import Path
from jinja2 import Environment
import sys

for name in sys.argv[1:]:
    Environment().parse(Path(name).read_text(encoding="utf-8"))

uploads = Path(sys.argv[1]).read_text(encoding="utf-8")
base = Path(sys.argv[2]).read_text(encoding="utf-8")
required_uploads = [
    "5. Яндекс Метрика",
    'name="metrika_zip"',
    "/api/reports/advertising/metrika/import",
    "metrika_test",
    "type!=='clinical'&&out.requires_review",
    'id="reconcileCloseText"',
]
for marker in required_uploads:
    if marker not in uploads:
        raise SystemExit("STOP: UPLOAD_CONTRACT_MISSING="+marker)
if "[data-az-metrika-form]" not in base:
    raise SystemExit("STOP: BASE_METRIKA_AJAX_GUARD_MISSING")
print("JINJA_PARSE=PASS")
print("UPLOAD_CONTRACT=PASS")
PY

"$APP_ROOT/venv/bin/python" - "$STAGE/advertising_report" "$STAGE/advertising_html" "$STAGE/advertising_js" <<'PY'
from pathlib import Path
import sys

py = Path(sys.argv[1]).read_text(encoding="utf-8")
html = Path(sys.argv[2]).read_text(encoding="utf-8")
js = Path(sys.argv[3]).read_text(encoding="utf-8")
text = html + "\n" + js

required_py = [
    "advertising_metrika_goal_daily",
    "advertising_metrika_imports",
    "provider_known_without_patient_detail",
    "provider_balance_diff",
    "_provider_revenue_summary",
    "METRIKA_COUNTER_ID = \"113776005\"",
    "METRIKA_EXPORT_SCHEMA = \"az_analytics_export_v1\"",
]
for marker in required_py:
    if marker not in py:
        raise SystemExit("STOP: PY_CONTRACT_MISSING="+marker)

required_ui = [
    "Выручка клиники",
    "Выручка по врачам / исполнителям",
    "Вся сумма «Оплачено» распределена по врачам/исполнителям",
    "Не хватает данных CRM для построения отчёта",
    "Визиты с открытием записи",
    "Визиты с переходом в Medflex",
]
for marker in required_ui:
    if marker not in text:
        raise SystemExit("STOP: UI_CONTRACT_MISSING="+marker)

forbidden_ui = [
    "Выручка новых пациентов за 30 дней",
    "Без построчной детализации в выгрузке МИС",
]
for marker in forbidden_ui:
    if marker in text:
        raise SystemExit("STOP: OLD_UI_PRESENT="+marker)

print("ADVERTISING_RUNTIME_CONTRACT=PASS")
PY

if command -v node >/dev/null 2>&1; then
  node --check "$STAGE/advertising_js"
  echo 'JAVASCRIPT_SYNTAX=PASS'
else
  echo 'JAVASCRIPT_SYNTAX=CI_VERIFIED_NODE_NOT_INSTALLED'
fi

SNAPSHOT_BEFORE="$STAGE/core-before.json"
"$APP_ROOT/venv/bin/python" - "$DB" "$SNAPSHOT_BEFORE" <<'PY'
import json, sqlite3, sys
db, out = sys.argv[1:3]
tables = [
    "users","permissions","daily_uploads","cash_receipts_daily",
    "service_payment_periods","service_payment_snapshots","report_data",
    "provider_registry","advertising_clinic_daily","advertising_cohort_daily",
    "advertising_traffic_daily","advertising_report_imports",
]
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
con.execute("PRAGMA query_only=ON")
result = {}
for table in tables:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    result[table] = (
        con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        if exists else None
    )
print("DB_INTEGRITY_PRE="+con.execute("PRAGMA integrity_check").fetchone()[0])
open(out,"w",encoding="utf-8").write(json.dumps(result,sort_keys=True))
print("CORE_ROW_COUNTS_PRE="+json.dumps(result,sort_keys=True))
con.close()
PY

if [ "$MODE" = '--preflight' ]; then
  echo '=== FINAL ==='
  echo 'ADVERTISING_FINAL_PREFLIGHT=PASS'
  echo 'LIVE_CHANGES=NONE'
  echo 'DATABASE_WRITES=NONE'
  exit 0
fi

if [ "$ALL_TARGET" -eq 1 ]; then
  echo '=== FINAL ==='
  echo 'ADVERTISING_FINAL_DEPLOY=PASS'
  echo 'REUSED=TRUE'
  echo 'CHANGED_RUNTIME_FILES=0'
  echo 'SERVICE=active'
  echo 'HEALTH=ok'
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_ROOT/advertising-final-$STAMP"
mkdir -p "$BACKUP/files"
chmod 700 "$BACKUP"

for key in advertising_report uploads base advertising_html advertising_js; do
  src="${LIVE[$key]}"
  cp -a "$src" "$BACKUP/files/$key"
  [ "$(blob "$BACKUP/files/$key")" = "${BASE[$key]}" ] || {
    if [ "$(blob "$BACKUP/files/$key")" != "${TARGET[$key]}" ]; then
      echo "STOP: FILE_BACKUP_BLOB_FAIL=$key"
      exit 1
    fi
  }
done
echo 'RUNTIME_BACKUP=PASS'

"$APP_ROOT/venv/bin/python" - "$DB" "$BACKUP/auth.db" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1:3]
source = sqlite3.connect(src)
target = sqlite3.connect(dst)
source.backup(target)
target.close()
source.close()
check = sqlite3.connect(dst).execute("PRAGMA integrity_check").fetchone()[0]
if check != "ok":
    raise SystemExit("BACKUP_DB_INTEGRITY_FAIL="+str(check))
print("DATABASE_BACKUP=PASS")
PY
chmod 600 "$BACKUP/auth.db"
echo "BACKUP=$BACKUP"

ARCHIVE_CREATED=0
if [ ! -d "$ARCHIVE_DIR" ]; then
  DB_UID="$(stat -c %u "$DB")"
  DB_GID="$(stat -c %g "$DB")"
  install -d -o "$DB_UID" -g "$DB_GID" -m 750 "$ARCHIVE_DIR"
  ARCHIVE_CREATED=1
  echo 'METRIKA_ARCHIVE_DIR=CREATED'
else
  echo 'METRIKA_ARCHIVE_DIR=EXISTS'
fi

declare -A UID
declare -A GID
declare -A MODEBITS
for key in advertising_report uploads base advertising_html advertising_js; do
  UID[$key]="$(stat -c %u "${LIVE[$key]}")"
  GID[$key]="$(stat -c %g "${LIVE[$key]}")"
  MODEBITS[$key]="$(stat -c %a "${LIVE[$key]}")"
done

rollback(){
  set +e
  echo 'ROLLBACK=START'
  systemctl stop "$SERVICE" >/dev/null 2>&1
  for key in advertising_report uploads base advertising_html advertising_js; do
    if [ -f "$BACKUP/files/$key" ]; then
      install -D -o "${UID[$key]}" -g "${GID[$key]}" -m "${MODEBITS[$key]}"         "$BACKUP/files/$key" "${LIVE[$key]}"
    fi
  done
  cp -a "$BACKUP/auth.db" "$DB"
  if [ "$ARCHIVE_CREATED" -eq 1 ]; then
    rmdir "$ARCHIVE_DIR" >/dev/null 2>&1 || true
  fi
  systemctl start "$SERVICE" >/dev/null 2>&1
  sleep 2
  ok=1
  systemctl is-active --quiet "$SERVICE" || ok=0
  [ "$(curl -fsS "$HEALTH" 2>/dev/null)" = 'ok' ] || ok=0
  if [ "$ok" -eq 1 ]; then echo 'ROLLBACK=PASS'; else echo 'ROLLBACK=FAIL'; fi
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then rollback; fi; rm -rf "$STAGE"; exit "$rc"' EXIT

systemctl stop "$SERVICE"
if systemctl is-active --quiet "$SERVICE"; then
  echo 'STOP: SERVICE_DID_NOT_STOP'
  exit 1
fi
echo 'SERVICE_STOP_FOR_CONTROLLED_APPLY=PASS'

for key in advertising_report uploads base advertising_html advertising_js; do
  install -D -o "${UID[$key]}" -g "${GID[$key]}" -m "${MODEBITS[$key]}"     "$STAGE/$key" "${LIVE[$key]}"
  [ "$(blob "${LIVE[$key]}")" = "${TARGET[$key]}" ] || {
    echo "STOP: INSTALL_BLOB_FAIL=$key"
    exit 1
  }
done
echo 'RUNTIME_FILES_INSTALL=PASS'

systemctl start "$SERVICE"
sleep 3
systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_RESTART_FAIL'; exit 1; }
[ "$(curl -fsS "$HEALTH")" = 'ok' ] || { echo 'STOP: HEALTH_POST_FAIL'; exit 1; }
echo 'SERVICE_HEALTH_POST=PASS'

SNAPSHOT_AFTER="$STAGE/core-after.json"
"$APP_ROOT/venv/bin/python" - "$DB" "$SNAPSHOT_BEFORE" "$SNAPSHOT_AFTER" <<'PY'
import json, sqlite3, sys
db, before_path, after_path = sys.argv[1:4]
tables = [
    "users","permissions","daily_uploads","cash_receipts_daily",
    "service_payment_periods","service_payment_snapshots","report_data",
    "provider_registry","advertising_clinic_daily","advertising_cohort_daily",
    "advertising_traffic_daily","advertising_report_imports",
]
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
con.execute("PRAGMA query_only=ON")
after = {}
for table in tables:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    after[table] = (
        con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        if exists else None
    )
integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
new_tables = {
    name: con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
    for name in ("advertising_metrika_goal_daily","advertising_metrika_imports")
}
con.close()
before = json.load(open(before_path,encoding="utf-8"))
json.dump(after,open(after_path,"w",encoding="utf-8"),sort_keys=True)
print("DB_INTEGRITY_POST="+str(integrity))
print("CORE_ROW_COUNTS_POST="+json.dumps(after,sort_keys=True))
print("NEW_METRIKA_TABLES="+json.dumps(new_tables,sort_keys=True))
if integrity != "ok":
    raise SystemExit("STOP: DB_INTEGRITY_POST_FAIL")
if before != after:
    raise SystemExit("STOP: EXISTING_TABLE_ROW_COUNTS_CHANGED")
if not all(new_tables.values()):
    raise SystemExit("STOP: METRIKA_TABLES_MISSING")
print("EXISTING_DATA_ROW_COUNTS_UNCHANGED=PASS")
print("METRIKA_SCHEMA=PASS")
PY

for key in advertising_report uploads base advertising_html advertising_js; do
  actual="$(blob "${LIVE[$key]}")"
  [ "$actual" = "${TARGET[$key]}" ] || { echo "STOP: POST_BLOB_FAIL=$key"; exit 1; }
  echo "LIVE_TARGET_BLOB=$key:$actual"
done

trap - EXIT
rm -rf "$STAGE"

echo '=== FINAL ==='
echo 'ADVERTISING_FINAL_DEPLOY=PASS'
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "BACKUP=$BACKUP"
echo 'CHANGED_RUNTIME_FILES=5'
echo 'DATABASE_SCHEMA_CHANGES=2_NEW_METRIKA_TABLES'
echo 'EXISTING_DATA_ROW_COUNTS_UNCHANGED=PASS'
echo 'SERVICE=active'
echo 'HEALTH=ok'
