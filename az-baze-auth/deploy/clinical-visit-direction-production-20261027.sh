#!/usr/bin/env bash
set -Eeuo pipefail

REPO='pat16071975-ship-it/architecture-health-site'
TARGET_COMMIT='397cadf617750f2c1af908a643bf25d13a442763'
EXPECTED_HOST='az-server'
APP_ROOT='/opt/az-baze-auth'
DB='/var/lib/az-baze/auth.db'
SERVICE='az-baze-auth'
HEALTH_URL='http://127.0.0.1:8001/health'
BACKUP_ROOT='/var/lib/az-baze/backups'
CONFIRM_TOKEN='CLINICAL-VISIT-DIRECTION-397CADF6'

MODE="${1:---preflight}"
CONFIRM="${2:-}"

case "$MODE" in
  --preflight) ;;
  --apply) [ "$CONFIRM" = "$CONFIRM_TOKEN" ] || { echo 'CONFIRMATION=FAIL'; exit 2; } ;;
  *) echo "UNKNOWN_MODE=$MODE"; exit 2 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo 'STOP: ROOT_REQUIRED'; exit 1; }
[ "$(hostname -s)" = "$EXPECTED_HOST" ] || { echo "STOP: WRONG_HOST=$(hostname -s)"; exit 1; }
command -v curl >/dev/null
command -v git >/dev/null
command -v systemctl >/dev/null
[ -x "$APP_ROOT/venv/bin/python" ] || { echo 'STOP: VENV_PYTHON_MISSING'; exit 1; }
[ -f "$DB" ] || { echo 'STOP: DB_MISSING'; exit 1; }

declare -A BASE TARGET REL
REL[daily_upload.py]='daily_upload.py'
BASE[daily_upload.py]='0ceb17ceb4b0f3f1feec20e711a22a692f2a3d06'
TARGET[daily_upload.py]='c6291e649c0d40fbae457572349cd2f2967f14bc'

REL[daily_upload_core.py]='daily_upload_core.py'
BASE[daily_upload_core.py]='7a58de237753dff1d5e72a11c14744d68c39a324'
TARGET[daily_upload_core.py]='d2a44fd700a3b0b03e6db64dc5936da25e7b7e39'

REL[paid_services.py]='paid_services.py'
BASE[paid_services.py]='76c1a33de8a711f85139a21aa5f66a48ea96910f'
TARGET[paid_services.py]='6aadd5b5e1464d027425d163b4b47fe718ddd589'

REL[upload_reconcile.py]='upload_reconcile.py'
BASE[upload_reconcile.py]='bce8384dbb8d4b6eacf98ad5d4d099705bf39914'
TARGET[upload_reconcile.py]='5cad7baa20e2f8c77d7ab389d8137f7d5452db75'

REL[uploads.html]='templates/uploads.html'
BASE[uploads.html]='d0ecaec2d569c61bec113e30fde63de90ec3b6fe'
TARGET[uploads.html]='63a7e261c71d8695649d7e8aa7d55e20edd0dd1c'

FILES=(daily_upload.py daily_upload_core.py paid_services.py upload_reconcile.py uploads.html)

blob(){ git hash-object "$1"; }

BASE_COUNT=0
TARGET_COUNT=0
for key in "${FILES[@]}"; do
  live="$APP_ROOT/${REL[$key]}"
  [ -f "$live" ] || { echo "STOP: LIVE_FILE_MISSING=$key"; exit 1; }
  actual="$(blob "$live")"
  if [ "$actual" = "${BASE[$key]}" ]; then
    state='BASE'; BASE_COUNT=$((BASE_COUNT+1))
  elif [ "$actual" = "${TARGET[$key]}" ]; then
    state='TARGET'; TARGET_COUNT=$((TARGET_COUNT+1))
  else
    echo "STOP: LIVE_BLOB_MISMATCH=$key"
    echo "ACTUAL=$actual"
    echo "EXPECTED_BASE=${BASE[$key]}"
    echo "EXPECTED_TARGET=${TARGET[$key]}"
    exit 1
  fi
  echo "LIVE_FILE=$key;STATE=$state;BLOB=$actual"
done

if [ "$BASE_COUNT" -eq "${#FILES[@]}" ]; then
  LIVE_STATE='BASE'
elif [ "$TARGET_COUNT" -eq "${#FILES[@]}" ]; then
  LIVE_STATE='TARGET'
else
  echo "STOP: MIXED_LIVE_STATE;BASE=$BASE_COUNT;TARGET=$TARGET_COUNT"
  exit 1
fi
echo "LIVE_STATE=$LIVE_STATE"

systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_NOT_ACTIVE'; exit 1; }
[ "$(curl -fsS "$HEALTH_URL")" = 'ok' ] || { echo 'STOP: HEALTH_PRE_FAIL'; exit 1; }
echo 'SERVICE_HEALTH_PRE=PASS'

"$APP_ROOT/venv/bin/python" - "$DB" "$LIVE_STATE" <<'PY'
import sqlite3, sys, json
db, live_state = sys.argv[1], sys.argv[2]
c=sqlite3.connect(f"file:{db}?mode=ro", uri=True)
c.row_factory=sqlite3.Row
try:
    integrity=c.execute("PRAGMA integrity_check").fetchone()[0]
    print("DB_INTEGRITY="+str(integrity))
    if integrity!="ok":
        raise SystemExit("STOP: DB_INTEGRITY_FAIL")
    table=c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='clinical_visit_direction_overrides'"
    ).fetchone()
    idx=c.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_clinical_visit_direction_date'"
    ).fetchone()
    if live_state=="BASE":
        if table or idx:
            raise SystemExit("STOP: NEW_SCHEMA_PRESENT_WHILE_SOURCE_BASE")
        print("SCHEMA_STATE=ABSENT")
    else:
        if not table or not idx:
            raise SystemExit("STOP: TARGET_SOURCE_WITHOUT_SCHEMA")
        count=c.execute("SELECT COUNT(*) FROM clinical_visit_direction_overrides").fetchone()[0]
        print("SCHEMA_STATE=PRESENT")
        print("VISIT_DIRECTION_ROWS="+str(count))
finally:
    c.close()
PY

STAGE="$(mktemp -d /tmp/az-clinical-visit-direction.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

for key in "${FILES[@]}"; do
  rel="${REL[$key]}"
  target="$STAGE/$key"
  curl -fsSL --retry 2 --connect-timeout 5 --max-time 30     "https://raw.githubusercontent.com/$REPO/$TARGET_COMMIT/az-baze-auth/$rel"     -o "$target"
  [ "$(blob "$target")" = "${TARGET[$key]}" ] || { echo "STOP: TARGET_BLOB_FAIL=$key"; exit 1; }
  echo "TARGET_FILE=$key;BLOB=PASS"
done

for key in daily_upload.py daily_upload_core.py paid_services.py upload_reconcile.py; do
  "$APP_ROOT/venv/bin/python" -m py_compile "$STAGE/$key"
done
echo 'PYTHON_SYNTAX=PASS'

"$APP_ROOT/venv/bin/python" - "$STAGE/daily_upload.py" "$STAGE/upload_reconcile.py" "$STAGE/uploads.html" "$STAGE/paid_services.py" <<'PY'
from pathlib import Path
import sys
daily=Path(sys.argv[1]).read_text(encoding="utf-8")
recon=Path(sys.argv[2]).read_text(encoding="utf-8")
html=Path(sys.argv[3]).read_text(encoding="utf-8")
paid=Path(sys.argv[4]).read_text(encoding="utf-8")
assert "clinical_visit_direction_overrides" in recon
assert "CHECK (direction IN ('dent','structure','unassigned'))" in recon
assert "patient_name" not in recon
assert "data-visit-direction" in html
assert "Оставить нераспределённым" in html
assert "visit_direction_decisions" in html
assert "Что нашли в источниках" in html
assert "record[\"primary\"] = source_primary" not in paid
assert "record[\"repeat\"] = source_repeat" not in paid
assert "_visit_direction_rows" in daily
assert "_apply_visit_direction_rows" in daily
print("VISIT_DIRECTION_CONTRACT=PASS")
PY

if [ "$MODE" = '--preflight' ]; then
  echo '=== FINAL ==='
  echo 'CLINICAL_VISIT_DIRECTION_PREFLIGHT=PASS'
  echo 'LIVE_CHANGES=NONE'
  exit 0
fi

if [ "$LIVE_STATE" = 'TARGET' ]; then
  echo '=== FINAL ==='
  echo 'CLINICAL_VISIT_DIRECTION_DEPLOY=PASS'
  echo 'REUSED=TRUE'
  echo 'CHANGED_RUNTIME_FILES=0'
  echo 'SCHEMA_CHANGE=NONE'
  echo 'DB_DATA_WRITES=NONE'
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_ROOT/clinical-visit-direction-$STAMP"
mkdir -p "$BACKUP/files"
chmod 700 "$BACKUP"

for key in "${FILES[@]}"; do
  live="$APP_ROOT/${REL[$key]}"
  mkdir -p "$BACKUP/files/$(dirname "${REL[$key]}")"
  cp -a "$live" "$BACKUP/files/${REL[$key]}"
  [ "$(blob "$BACKUP/files/${REL[$key]}")" = "${BASE[$key]}" ] || { echo "STOP: FILE_BACKUP_FAIL=$key"; exit 1; }
done

"$APP_ROOT/venv/bin/python" - "$DB" "$BACKUP/auth.db" <<'PY'
import sqlite3, sys
src_path,dst_path=sys.argv[1],sys.argv[2]
src=sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dst=sqlite3.connect(dst_path)
try:
    src.backup(dst)
    dst.commit()
finally:
    src.close(); dst.close()
check=sqlite3.connect(f"file:{dst_path}?mode=ro", uri=True)
try:
    if check.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
        raise SystemExit("BACKUP_INTEGRITY_FAIL")
finally:
    check.close()
print("DB_BACKUP=PASS")
PY
echo "BACKUP=$BACKUP"

declare -A UID GID MODE
for key in "${FILES[@]}"; do
  live="$APP_ROOT/${REL[$key]}"
  UID[$key]="$(stat -c %u "$live")"
  GID[$key]="$(stat -c %g "$live")"
  MODE[$key]="$(stat -c %a "$live")"
done

rollback(){
  set +e
  echo 'ROLLBACK=START'
  systemctl stop "$SERVICE" >/dev/null 2>&1
  for key in "${FILES[@]}"; do
    live="$APP_ROOT/${REL[$key]}"
    install -D -o "${UID[$key]}" -g "${GID[$key]}" -m "${MODE[$key]}"       "$BACKUP/files/${REL[$key]}" "$live"
  done
  cp -a "$BACKUP/auth.db" "$DB"
  systemctl start "$SERVICE" >/dev/null 2>&1
  sleep 2
  good=1
  for key in "${FILES[@]}"; do
    [ "$(blob "$APP_ROOT/${REL[$key]}" 2>/dev/null)" = "${BASE[$key]}" ] || good=0
  done
  systemctl is-active --quiet "$SERVICE" || good=0
  [ "$(curl -fsS "$HEALTH_URL" 2>/dev/null)" = 'ok' ] || good=0
  if [ "$good" -eq 1 ]; then echo 'ROLLBACK=PASS'; else echo 'ROLLBACK=FAIL'; fi
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then rollback; fi; rm -rf "$STAGE"; exit "$rc"' EXIT

for key in "${FILES[@]}"; do
  live="$APP_ROOT/${REL[$key]}"
  install -D -o "${UID[$key]}" -g "${GID[$key]}" -m "${MODE[$key]}" "$STAGE/$key" "$live"
  [ "$(blob "$live")" = "${TARGET[$key]}" ] || { echo "STOP: LIVE_INSTALL_FAIL=$key"; exit 1; }
done
echo 'RUNTIME_FILES_INSTALL=PASS'

"$APP_ROOT/venv/bin/python" - "$DB" "$BACKUP/schema-before.json" <<'PY'
import sqlite3, sys, json
db,out=sys.argv[1],sys.argv[2]
c=sqlite3.connect(db)
c.row_factory=sqlite3.Row
try:
    before={
        (r["type"],r["name"]):r["sql"]
        for r in c.execute("SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','index')")
    }
    with open(out,"w",encoding="utf-8") as f:
        json.dump({f"{k[0]}|{k[1]}":v for k,v in before.items()},f,ensure_ascii=False,sort_keys=True,indent=2)
    c.execute("BEGIN IMMEDIATE")
    c.execute("""
        CREATE TABLE clinical_visit_direction_overrides (
            visit_id TEXT PRIMARY KEY,
            data_date TEXT NOT NULL,
            patient_token TEXT NOT NULL,
            kind TEXT NOT NULL,
            occurrence INTEGER NOT NULL,
            direction TEXT NOT NULL
                CHECK (direction IN ('dent','structure','unassigned')),
            confirmed_by INTEGER,
            confirmed_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE INDEX idx_clinical_visit_direction_date
        ON clinical_visit_direction_overrides(data_date)
    """)
    c.commit()
finally:
    c.close()
print("SCHEMA_APPLY=PASS")
PY

"$APP_ROOT/venv/bin/python" - "$DB" "$BACKUP/schema-before.json" <<'PY'
import sqlite3, sys, json
db,before_path=sys.argv[1],sys.argv[2]
with open(before_path,encoding="utf-8") as f:
    raw=json.load(f)
before=set(tuple(k.split("|",1)) for k in raw)
c=sqlite3.connect(f"file:{db}?mode=ro", uri=True)
c.row_factory=sqlite3.Row
try:
    if c.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
        raise SystemExit("POST_SCHEMA_INTEGRITY_FAIL")
    after={(r["type"],r["name"]) for r in c.execute(
        "SELECT type,name FROM sqlite_master WHERE type IN ('table','index')"
    )}
    added=after-before
    allowed={
        ("table","clinical_visit_direction_overrides"),
        ("index","idx_clinical_visit_direction_date"),
        ("index","sqlite_autoindex_clinical_visit_direction_overrides_1"),
    }
    if added != allowed:
        raise SystemExit("UNEXPECTED_SCHEMA_DELTA:"+repr(sorted(added)))
    cols=[r["name"] for r in c.execute("PRAGMA table_info(clinical_visit_direction_overrides)")]
    expected=["visit_id","data_date","patient_token","kind","occurrence","direction","confirmed_by","confirmed_at"]
    if cols!=expected:
        raise SystemExit("COLUMN_CONTRACT_FAIL:"+repr(cols))
    if "patient" in cols or "patient_name" in cols:
        raise SystemExit("PATIENT_NAME_STORAGE_FORBIDDEN")
    count=c.execute("SELECT COUNT(*) FROM clinical_visit_direction_overrides").fetchone()[0]
    if count!=0:
        raise SystemExit("NEW_TABLE_NOT_EMPTY")
    print("SCHEMA_DELTA=PASS")
    print("VISIT_DIRECTION_ROWS=0")
finally:
    c.close()
PY

systemctl restart "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE" || { echo 'STOP: SERVICE_RESTART_FAIL'; exit 1; }
[ "$(curl -fsS "$HEALTH_URL")" = 'ok' ] || { echo 'STOP: HEALTH_POST_FAIL'; exit 1; }

for key in "${FILES[@]}"; do
  [ "$(blob "$APP_ROOT/${REL[$key]}")" = "${TARGET[$key]}" ] || { echo "STOP: POST_LIVE_BLOB_FAIL=$key"; exit 1; }
done

"$APP_ROOT/venv/bin/python" - "$DB" <<'PY'
import sqlite3, sys
db=sys.argv[1]
c=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
try:
    assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert c.execute("SELECT COUNT(*) FROM clinical_visit_direction_overrides").fetchone()[0]==0
finally:
    c.close()
print("POST_DEPLOY_DB=PASS")
PY

trap - EXIT
rm -rf "$STAGE"

echo '=== FINAL ==='
echo 'CLINICAL_VISIT_DIRECTION_DEPLOY=PASS'
echo "TARGET_COMMIT=$TARGET_COMMIT"
echo "BACKUP=$BACKUP"
echo 'CHANGED_RUNTIME_FILES=5'
echo 'SCHEMA_ADDED=clinical_visit_direction_overrides'
echo 'VISIT_DIRECTION_ROWS=0'
echo 'EXISTING_BUSINESS_ROWS_CHANGED=0'
echo 'SERVICE=active'
echo 'HEALTH=ok'
echo 'OCTOBER_RECALC_APPLIED=NO'
