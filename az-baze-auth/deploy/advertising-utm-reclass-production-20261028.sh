#!/usr/bin/env bash
set -Eeuo pipefail

REPO='pat16071975-ship-it/architecture-health-site'
TARGET_COMMIT='4f78e1e5cdddc1baf9bce67f96e302d199c207fe'
SERVICE='az-baze-auth'
APP_ROOT='/opt/az-baze-auth'
SITE_ROOT='/var/www/az-baze.ru'
DB='/var/lib/az-baze/auth.db'
BACKUP_ROOT='/var/lib/az-baze/backups'
HEALTH='http://127.0.0.1:8001/health'
CONFIRM='AZ-AD-UTM-RECLASS-4F78E1E5'

LIVE_PY="$APP_ROOT/advertising_report.py"
LIVE_HTML="$SITE_ROOT/reports/advertising.html"
LIVE_JS="$SITE_ROOT/reports/advertising.js"

BASE_PY='a2c49523e9ebc90702209ef94cfa1d0e21b6b64a'
TARGET_PY='c2db5fe53f8df28d0beec2a23b2b3fa61895bfe4'
EXPECTED_HTML='99033f3739489069c529ec9e580cd90860cd4eab'
EXPECTED_JS='37ec2259e8cebb9ee2fc6f2855e6e37e66b8cb1c'

MODE="${1:---preflight}"
TOKEN="${2:-}"
case "$MODE" in
  --preflight) ;;
  --apply) [ "$TOKEN" = "$CONFIRM" ] || { echo 'CONFIRMATION=FAIL'; exit 2; } ;;
  *) echo "UNKNOWN_MODE=$MODE"; exit 2 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo 'STOP: ROOT_REQUIRED'; exit 1; }
[ "$(hostname -s)" = 'az-server' ] || { echo "STOP: WRONG_HOST=$(hostname -s)"; exit 1; }
[ -x "$APP_ROOT/venv/bin/python" ] || { echo 'STOP: VENV_PYTHON_MISSING'; exit 1; }
[ -f "$DB" ] || { echo 'STOP: DB_MISSING'; exit 1; }

blob(){ git hash-object "$1"; }

echo '=== AZ-BAZE UTM SOURCE RECLASSIFICATION ==='
echo "TARGET_COMMIT=$TARGET_COMMIT"

PY_BLOB="$(blob "$LIVE_PY")"
if [ "$PY_BLOB" = "$BASE_PY" ]; then
  echo "LIVE_PY=BASE;BLOB=$PY_BLOB"
elif [ "$PY_BLOB" = "$TARGET_PY" ]; then
  echo "LIVE_PY=TARGET;BLOB=$PY_BLOB"
else
  echo 'STOP: LIVE_PY_BLOB_MISMATCH'
  echo "ACTUAL=$PY_BLOB"
  echo "EXPECTED_BASE=$BASE_PY"
  echo "EXPECTED_TARGET=$TARGET_PY"
  exit 1
fi

[ "$(blob "$LIVE_HTML")" = "$EXPECTED_HTML" ] || {
  echo "STOP: FLAT_SOURCE_HTML_NOT_LIVE;ACTUAL=$(blob "$LIVE_HTML")"
  exit 1
}
[ "$(blob "$LIVE_JS")" = "$EXPECTED_JS" ] || {
  echo "STOP: FLAT_SOURCE_JS_NOT_LIVE;ACTUAL=$(blob "$LIVE_JS")"
  exit 1
}
echo 'FLAT_SOURCE_UI_BASELINE=PASS'

systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_NOT_ACTIVE'; exit 1; }
[ "$(curl -fsS "$HEALTH")" = 'ok' ] || { echo 'STOP: HEALTH_PRE_FAIL'; exit 1; }
echo 'SERVICE_HEALTH_PRE=PASS'

STAGE="$(mktemp -d /tmp/az-ad-utm-reclass.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

curl -fsSL --retry 2 --connect-timeout 5 --max-time 30   "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/az-baze-auth/advertising_report.py"   -o "$STAGE/advertising_report.py"

[ "$(blob "$STAGE/advertising_report.py")" = "$TARGET_PY" ] || {
  echo 'STOP: TARGET_PY_BLOB_FAIL'
  exit 1
}
"$APP_ROOT/venv/bin/python" -m py_compile "$STAGE/advertising_report.py"
echo 'TARGET_RUNTIME=PASS'

"$APP_ROOT/venv/bin/python" - "$APP_ROOT" "$STAGE/advertising_report.py" "$DB" <<'PY'
import importlib.util, json, sqlite3, sys, types
from datetime import datetime, timezone
from pathlib import Path

app_root, target_path, db = sys.argv[1:4]
sys.path.insert(0, app_root)

fake_app = types.ModuleType("app")
fake_app.DB_PATH = Path(db)
fake_app.SITE_ROOT = Path("/var/www/az-baze.ru")
fake_app.admin_required = lambda fn: fn
fake_app.audit = lambda *a, **k: None
fake_app.db = lambda: None
fake_app.iso_now = lambda: datetime.now(timezone.utc).isoformat()
fake_app.permission_required = lambda _key: (lambda fn: fn)
fake_app.require_csrf = lambda: None
sys.modules["app"] = fake_app

spec = importlib.util.spec_from_file_location("advertising_report_target", target_path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
con.execute("PRAGMA query_only=ON")
if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    raise SystemExit("STOP: DB_INTEGRITY_PRE_FAIL")

row = con.execute("""
    SELECT original_filename,archive_path,completed_days_json,payload_sha256
    FROM advertising_metrika_imports
    ORDER BY imported_at DESC
    LIMIT 1
""").fetchone()
if not row:
    raise SystemExit("STOP: METRIKA_IMPORT_NOT_FOUND")

archive = Path(row["archive_path"])
if not archive.exists():
    raise SystemExit("STOP: METRIKA_ARCHIVE_NOT_FOUND")

parsed = m._parse_metrika_zip(archive.read_bytes(), row["original_filename"])
if parsed["completed_days"] != ["2026-10-27"]:
    raise SystemExit("STOP: UNEXPECTED_COMPLETED_DAYS="+json.dumps(parsed["completed_days"]))

traffic = {}
for key, values in parsed["traffic_rows"].items():
    traffic[key[2]] = traffic.get(key[2], 0) + int(values["visits"])

if sum(traffic.values()) != 75:
    raise SystemExit("STOP: PLANNED_VISITS_NOT_75="+str(sum(traffic.values())))
expected = {
    "Яндекс Бизнес": 25,
    "Instagram — Старостенко": 6,
    "Instagram — ссылка в bio, профиль не определён": 2,
}
for name, value in expected.items():
    if traffic.get(name) != value:
        raise SystemExit(f"STOP: PLANNED_SOURCE_MISMATCH={name}:{traffic.get(name)}")
if "Яндекс-реклама" in traffic:
    raise SystemExit("STOP: OLD_YANDEX_AD_LABEL_REMAINS")

goal_totals = {}
for key, values in parsed["goal_rows"].items():
    goal_key = key[6]
    target = goal_totals.setdefault(goal_key, [0,0])
    target[0] += int(values["goal_visits"])
    target[1] += int(values["reaches"])

if goal_totals.get("booking_open") != [3,6]:
    raise SystemExit("STOP: BOOKING_GOALS_CHANGED="+repr(goal_totals.get("booking_open")))
if goal_totals.get("medflex") != [5,8]:
    raise SystemExit("STOP: MEDFLEX_GOALS_CHANGED="+repr(goal_totals.get("medflex")))

print("DB_INTEGRITY_PRE=ok")
print("ARCHIVE="+str(archive))
print("PLANNED_COMPLETED_DAYS="+json.dumps(parsed["completed_days"]))
print("PLANNED_VISITS_TOTAL=75")
print("PLANNED_YANDEX_BUSINESS=25")
print("PLANNED_INSTAGRAM_STAROSTENKO=6")
print("PLANNED_INSTAGRAM_OTHER=2")
print("PLANNED_BOOKING_OPEN=3/6")
print("PLANNED_MEDFLEX=5/8")
print("RECLASS_PREFLIGHT=PASS")
con.close()
PY

if [ "$MODE" = '--preflight' ]; then
  echo '=== FINAL ==='
  echo 'AD_UTM_RECLASS_PREFLIGHT=PASS'
  echo 'LIVE_CHANGES=NONE'
  echo 'DATABASE_WRITES=NONE'
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_ROOT/advertising-utm-reclass-$STAMP"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"

cp -a "$LIVE_PY" "$BACKUP/advertising_report.py"

DB_UID="$(stat -c %u "$DB")"
DB_GID="$(stat -c %g "$DB")"
DB_MODE="$(stat -c %a "$DB")"
FILE_UID="$(stat -c %u "$LIVE_PY")"
FILE_GID="$(stat -c %g "$LIVE_PY")"
FILE_MODE="$(stat -c %a "$LIVE_PY")"

"$APP_ROOT/venv/bin/python" - "$DB" "$BACKUP/auth.db" <<'PY'
import sqlite3, sys
src,dst=sys.argv[1:3]
a=sqlite3.connect(src)
b=sqlite3.connect(dst)
a.backup(b)
b.close(); a.close()
check=sqlite3.connect(dst).execute("PRAGMA integrity_check").fetchone()[0]
if check!="ok":
    raise SystemExit("STOP: BACKUP_DB_INTEGRITY="+str(check))
print("DATABASE_BACKUP=PASS")
PY
chown "$DB_UID:$DB_GID" "$BACKUP/auth.db"
chmod "$DB_MODE" "$BACKUP/auth.db"
echo 'RUNTIME_BACKUP=PASS'
echo "BACKUP=$BACKUP"

rollback(){
  set +e
  echo 'ROLLBACK=START'
  systemctl stop "$SERVICE" >/dev/null 2>&1
  install -D -o "$FILE_UID" -g "$FILE_GID" -m "$FILE_MODE" "$BACKUP/advertising_report.py" "$LIVE_PY"
  rm -f "$DB-wal" "$DB-shm"
  install -D -o "$DB_UID" -g "$DB_GID" -m "$DB_MODE" "$BACKUP/auth.db" "$DB"
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

install -D -o "$FILE_UID" -g "$FILE_GID" -m "$FILE_MODE"   "$STAGE/advertising_report.py" "$LIVE_PY"
[ "$(blob "$LIVE_PY")" = "$TARGET_PY" ] || {
  echo 'STOP: RUNTIME_INSTALL_BLOB_FAIL'
  exit 1
}
echo 'RUNTIME_INSTALL=PASS'

"$APP_ROOT/venv/bin/python" - "$APP_ROOT" "$LIVE_PY" "$DB" <<'PY'
import hashlib, importlib.util, json, sqlite3, sys, types
from datetime import datetime, timezone
from pathlib import Path

app_root, target_path, db = sys.argv[1:4]
sys.path.insert(0, app_root)

fake_app = types.ModuleType("app")
fake_app.DB_PATH = Path(db)
fake_app.SITE_ROOT = Path("/var/www/az-baze.ru")
fake_app.admin_required = lambda fn: fn
fake_app.audit = lambda *a, **k: None
fake_app.db = lambda: None
fake_app.iso_now = lambda: datetime.now(timezone.utc).isoformat()
fake_app.permission_required = lambda _key: (lambda fn: fn)
fake_app.require_csrf = lambda: None
sys.modules["app"] = fake_app

spec = importlib.util.spec_from_file_location("advertising_report_target", target_path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

con = sqlite3.connect(db)
con.row_factory = sqlite3.Row

def table_digest(table):
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()]
    rows = con.execute(f'SELECT * FROM "{table}"').fetchall()
    payload = sorted(
        json.dumps([row[col] for col in cols], ensure_ascii=False, default=str, separators=(",",":"))
        for row in rows
    )
    return hashlib.sha256("\n".join(payload).encode()).hexdigest()

tables = [
    r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
]
changed_tables = {"advertising_traffic_daily","advertising_metrika_goal_daily"}
protected = {t: table_digest(t) for t in tables if t not in changed_tables}

row = con.execute("""
    SELECT original_filename,archive_path,payload_sha256
    FROM advertising_metrika_imports
    ORDER BY imported_at DESC
    LIMIT 1
""").fetchone()
if not row:
    raise SystemExit("STOP: METRIKA_IMPORT_NOT_FOUND")
archive = Path(row["archive_path"])
parsed = m._parse_metrika_zip(archive.read_bytes(), row["original_filename"])

days = parsed["completed_days"]
placeholders = ",".join("?" for _ in days)
traffic_other_before = hashlib.sha256(
    "\n".join(sorted(
        json.dumps(tuple(r), ensure_ascii=False, default=str)
        for r in con.execute(
            f"SELECT * FROM advertising_traffic_daily "
            f"WHERE NOT(provider='metrika' AND metric_date IN ({placeholders}))",
            days,
        ).fetchall()
    )).encode()
).hexdigest()
goals_other_before = hashlib.sha256(
    "\n".join(sorted(
        json.dumps(tuple(r), ensure_ascii=False, default=str)
        for r in con.execute(
            f"SELECT * FROM advertising_metrika_goal_daily "
            f"WHERE NOT(provider='metrika' AND metric_date IN ({placeholders}))",
            days,
        ).fetchall()
    )).encode()
).hexdigest()

con.execute("BEGIN IMMEDIATE")
result = m._store_metrika(con, parsed, None)
con.commit()

if not m._stored_metrika_matches(con, parsed):
    raise SystemExit("STOP: STORED_METRIKA_DOES_NOT_MATCH_ARCHIVE")

protected_after = {t: table_digest(t) for t in tables if t not in changed_tables}
if protected != protected_after:
    changed = [t for t in protected if protected[t] != protected_after.get(t)]
    raise SystemExit("STOP: PROTECTED_TABLE_CHANGED="+",".join(changed))

traffic_other_after = hashlib.sha256(
    "\n".join(sorted(
        json.dumps(tuple(r), ensure_ascii=False, default=str)
        for r in con.execute(
            f"SELECT * FROM advertising_traffic_daily "
            f"WHERE NOT(provider='metrika' AND metric_date IN ({placeholders}))",
            days,
        ).fetchall()
    )).encode()
).hexdigest()
goals_other_after = hashlib.sha256(
    "\n".join(sorted(
        json.dumps(tuple(r), ensure_ascii=False, default=str)
        for r in con.execute(
            f"SELECT * FROM advertising_metrika_goal_daily "
            f"WHERE NOT(provider='metrika' AND metric_date IN ({placeholders}))",
            days,
        ).fetchall()
    )).encode()
).hexdigest()
if traffic_other_before != traffic_other_after:
    raise SystemExit("STOP: NON_TARGET_TRAFFIC_CHANGED")
if goals_other_before != goals_other_after:
    raise SystemExit("STOP: NON_TARGET_GOALS_CHANGED")

sources = con.execute("""
    SELECT source_name,SUM(visits) AS visits
    FROM advertising_traffic_daily
    WHERE provider='metrika' AND metric_date='2026-10-27'
    GROUP BY source_name
    ORDER BY visits DESC,source_name
""").fetchall()
source_map = {r["source_name"]: int(r["visits"] or 0) for r in sources}
if sum(source_map.values()) != 75:
    raise SystemExit("STOP: FINAL_VISITS_NOT_75")
for name,value in {
    "Яндекс Бизнес":25,
    "Instagram — Старостенко":6,
    "Instagram — ссылка в bio, профиль не определён":2,
}.items():
    if source_map.get(name) != value:
        raise SystemExit(f"STOP: FINAL_SOURCE_MISMATCH={name}:{source_map.get(name)}")
if "Яндекс-реклама" in source_map:
    raise SystemExit("STOP: OLD_YANDEX_AD_LABEL_PRESENT")

goal_rows = con.execute("""
    SELECT goal_key,SUM(goal_visits) AS visits,SUM(reaches) AS reaches
    FROM advertising_metrika_goal_daily
    WHERE provider='metrika' AND metric_date='2026-10-27'
    GROUP BY goal_key
""").fetchall()
goal_map = {r["goal_key"]:(int(r["visits"] or 0),int(r["reaches"] or 0)) for r in goal_rows}
if goal_map.get("booking_open") != (3,6):
    raise SystemExit("STOP: FINAL_BOOKING_GOALS_CHANGED="+repr(goal_map.get("booking_open")))
if goal_map.get("medflex") != (5,8):
    raise SystemExit("STOP: FINAL_MEDFLEX_GOALS_CHANGED="+repr(goal_map.get("medflex")))

source_goals = con.execute("""
    SELECT source_name,goal_key,SUM(goal_visits) AS visits,SUM(reaches) AS reaches
    FROM advertising_metrika_goal_daily
    WHERE provider='metrika' AND metric_date='2026-10-27'
    GROUP BY source_name,goal_key
    ORDER BY source_name,goal_key
""").fetchall()

if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    raise SystemExit("STOP: DB_INTEGRITY_POST_FAIL")

print("RECLASS_RESULT="+json.dumps(result,ensure_ascii=False,sort_keys=True))
print("SOURCE_TOTALS="+json.dumps(source_map,ensure_ascii=False,sort_keys=True))
print("GOAL_TOTALS="+json.dumps(goal_map,ensure_ascii=False,sort_keys=True))
print("SOURCE_GOALS="+json.dumps([dict(r) for r in source_goals],ensure_ascii=False,sort_keys=True))
print("PROTECTED_TABLES_UNCHANGED=PASS")
print("NON_TARGET_ADVERTISING_ROWS_UNCHANGED=PASS")
print("STORED_METRIKA_MATCHES_ARCHIVE=PASS")
print("DB_INTEGRITY_POST=ok")
con.close()
PY

systemctl start "$SERVICE"
sleep 3
systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_RESTART_FAIL'; exit 1; }
[ "$(curl -fsS "$HEALTH")" = 'ok' ] || { echo 'STOP: HEALTH_POST_FAIL'; exit 1; }
echo 'SERVICE_HEALTH_POST=PASS'

trap - EXIT
rm -rf "$STAGE"

echo '=== FINAL ==='
echo 'AD_UTM_RECLASS_DEPLOY=PASS'
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "LIVE_PY_BLOB=$(blob "$LIVE_PY")"
echo "BACKUP=$BACKUP"
echo 'CHANGED_RUNTIME_FILES=1'
echo 'DATABASE_WRITE_SCOPE=METRIKA_TRAFFIC_AND_GOALS_2026-10-27_ONLY'
echo 'OTHER_DATA_UNCHANGED=PASS'
echo 'SERVICE=active'
echo 'HEALTH=ok'
