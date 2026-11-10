#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

MODE="${1:-}"
CONFIRM="${2:-}"

EXPECTED_HOST='vdska'
SERVICE='az-baze-auth.service'
HEALTH_URL='http://127.0.0.1:8001/health'

APP='/opt/az-baze-auth'
SITE='/var/www/az-baze.ru'
REPORTS="$SITE/reports"
DB='/var/lib/az-baze/auth.db'

TARGET_COMMIT='a437dd76a5cc38b39bd4e55f30c42af5d8d76fa6'
CONFIRM_TOKEN='MANAGEMENT-STAFF-LAYOUT-A437DD76'

BASE_STORAGE='44790d1ee20623c9312d552925262ce47decfa44'
BASE_INDEX='3e43b1124cc7f526ece4a5bc6ceaef8ad19f06b7'
BASE_PERIOD='153b1bba79fe506494ae941dc09254a8cc698988'

TARGET_STORAGE='de3e5a5167a4c454dc5ff700a06ae1b88c5234aa'
TARGET_INDEX='9eec90a944b55609fb4037053632d0f6da2862f4'
TARGET_PERIOD='39de3f683e2fa6323f30b8465a77f951155af3ed'
TARGET_LAYOUT='2d31aca285c31ceb112c7dc8cadb7a77140bef01'

STORAGE="$APP/report_storage.py"
INDEX="$REPORTS/index.html"
PERIOD="$REPORTS/period-view.js"
LAYOUT="$REPORTS/management-staff-layout.js"

TMP=''
BACKUP=''
MUTATED=0
LAYOUT_EXISTED_BEFORE=0

usage() {
  cat <<'EOF'
Usage:
  bash management-staff-layout-production-20261110.sh --preflight
  bash management-staff-layout-production-20261110.sh --apply MANAGEMENT-STAFF-LAYOUT-A437DD76
EOF
}

cleanup() {
  [[ -n "${TMP:-}" ]] && rm -rf "$TMP"
}

rollback() {
  rc=$?
  trap - ERR

  if [[ "$MUTATED" == '1' && -n "${BACKUP:-}" && -d "$BACKUP" ]]; then
    echo '=== ROLLBACK: FILES ONLY ==='
    systemctl stop "$SERVICE" || true

    cp -a "$BACKUP/report_storage.py" "$STORAGE"
    cp -a "$BACKUP/index.html" "$INDEX"
    cp -a "$BACKUP/period-view.js" "$PERIOD"

    if [[ "$LAYOUT_EXISTED_BEFORE" == '1' && -f "$BACKUP/management-staff-layout.js" ]]; then
      cp -a "$BACKUP/management-staff-layout.js" "$LAYOUT"
    else
      rm -f "$LAYOUT"
    fi

    systemctl start "$SERVICE" || true

    rb_health='FAIL'
    for _ in $(seq 1 30); do
      if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
        rb_health='PASS'
        break
      fi
      sleep 1
    done
    echo "ROLLBACK_HEALTH=$rb_health"
    echo 'ROLLBACK_DB_RESTORE=NOT_PERFORMED'
  fi

  cleanup
  exit "$rc"
}

trap rollback ERR
trap cleanup EXIT

case "$MODE" in
  --preflight)
    ;;
  --apply)
    [[ "$CONFIRM" == "$CONFIRM_TOKEN" ]] || {
      echo 'STOP=BAD_CONFIRM_TOKEN'
      usage
      exit 1
    }
    ;;
  *)
    usage
    exit 2
    ;;
esac

for cmd in curl git python3 systemctl install mktemp cp rm grep; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "STOP=MISSING_COMMAND:$cmd"
    exit 1
  }
done

echo '=== AZ MANAGEMENT STAFF LAYOUT PRODUCTION ==='
echo "MODE=$MODE"
echo "HOST=$(hostname)"
echo "USER_ID=$(id -u)"
echo "UTC=$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "TARGET_COMMIT=$TARGET_COMMIT"

[[ "$(hostname)" == "$EXPECTED_HOST" ]] || {
  echo "STOP=WRONG_HOST expected=$EXPECTED_HOST"
  exit 1
}

[[ "$(id -u)" == '0' ]] || {
  echo 'STOP=NEED_ROOT'
  exit 1
}

for f in "$STORAGE" "$INDEX" "$PERIOD" "$DB"; do
  [[ -f "$f" ]] || {
    echo "STOP=MISSING_FILE:$f"
    exit 1
  }
done

systemctl is-active --quiet "$SERVICE" || {
  echo 'STOP=SERVICE_NOT_ACTIVE'
  exit 1
}

curl -fsS "$HEALTH_URL" >/dev/null || {
  echo 'STOP=PRE_HEALTH_FAIL'
  exit 1
}
echo 'PRE_HEALTH=PASS'

python3 - "$DB" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
try:
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    conn.close()
if result != "ok":
    raise SystemExit(f"STOP=PRE_DB_INTEGRITY:{result}")
print("PRE_DB_INTEGRITY=PASS")
PY

LIVE_STORAGE="$(git hash-object "$STORAGE")"
LIVE_INDEX="$(git hash-object "$INDEX")"
LIVE_PERIOD="$(git hash-object "$PERIOD")"
LIVE_LAYOUT='ABSENT'

if [[ -e "$LAYOUT" || -L "$LAYOUT" ]]; then
  [[ -f "$LAYOUT" && ! -L "$LAYOUT" ]] || {
    echo 'STOP=UNEXPECTED_LAYOUT_OBJECT'
    exit 1
  }
  LAYOUT_EXISTED_BEFORE=1
  LIVE_LAYOUT="$(git hash-object "$LAYOUT")"
fi

echo "LIVE_STORAGE=$LIVE_STORAGE"
echo "LIVE_INDEX=$LIVE_INDEX"
echo "LIVE_PERIOD=$LIVE_PERIOD"
echo "LIVE_LAYOUT=$LIVE_LAYOUT"

if [[ "$LIVE_STORAGE" == "$TARGET_STORAGE"    && "$LIVE_INDEX" == "$TARGET_INDEX"    && "$LIVE_PERIOD" == "$TARGET_PERIOD"    && "$LIVE_LAYOUT" == "$TARGET_LAYOUT" ]]; then
  echo 'ALREADY_DEPLOYED=YES'
  echo 'GLOBAL_MANAGEMENT_STAFF_LAYOUT_DEPLOY=PASS'
  exit 0
fi

[[ "$LIVE_STORAGE" == "$BASE_STORAGE" ]] || {
  echo "STOP=REPORT_STORAGE_DRIFT expected=$BASE_STORAGE actual=$LIVE_STORAGE"
  exit 1
}
[[ "$LIVE_INDEX" == "$BASE_INDEX" ]] || {
  echo "STOP=INDEX_DRIFT expected=$BASE_INDEX actual=$LIVE_INDEX"
  exit 1
}
[[ "$LIVE_PERIOD" == "$BASE_PERIOD" ]] || {
  echo "STOP=PERIOD_VIEW_DRIFT expected=$BASE_PERIOD actual=$LIVE_PERIOD"
  exit 1
}
[[ "$LIVE_LAYOUT" == 'ABSENT' ]] || {
  echo "STOP=MANAGEMENT_LAYOUT_ALREADY_EXISTS_UNEXPECTED actual=$LIVE_LAYOUT"
  exit 1
}
echo 'LIVE_BASE_GUARD=PASS'

TMP="$(mktemp -d /tmp/az-management-staff-layout-XXXXXX)"

curl -fL --retry 3   "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$TARGET_COMMIT/az-baze-auth/report_storage.py"   -o "$TMP/report_storage.py"
curl -fL --retry 3   "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$TARGET_COMMIT/reports/index.html"   -o "$TMP/index.html"
curl -fL --retry 3   "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$TARGET_COMMIT/reports/period-view.js"   -o "$TMP/period-view.js"
curl -fL --retry 3   "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$TARGET_COMMIT/reports/management-staff-layout.js"   -o "$TMP/management-staff-layout.js"

[[ "$(git hash-object "$TMP/report_storage.py")" == "$TARGET_STORAGE" ]]
[[ "$(git hash-object "$TMP/index.html")" == "$TARGET_INDEX" ]]
[[ "$(git hash-object "$TMP/period-view.js")" == "$TARGET_PERIOD" ]]
[[ "$(git hash-object "$TMP/management-staff-layout.js")" == "$TARGET_LAYOUT" ]]
echo 'TARGET_BLOBS=PASS'

python3 -m py_compile "$TMP/report_storage.py"

grep -Fq 'MANAGEMENT_STAFF_LAYOUT_KEY = "az-management-staff-layout-v1"' "$TMP/report_storage.py"
grep -Fq '@app.put("/api/reports/management-staff-layout")' "$TMP/report_storage.py"
grep -Fq 'management-staff-layout.js?v=20261110-1' "$TMP/index.html"
grep -Fq "managementStaffEntries('dentists')" "$TMP/period-view.js"
grep -Fq "managementStaffEntries('clinicDocs')" "$TMP/period-view.js"
grep -Fq '.az-management-staff-hidden-row{display:none!important}' "$TMP/period-view.js"
grep -Fq 'Скрытые сотрудники' "$TMP/management-staff-layout.js"
grep -Fq "info.hidden ? '+' : '−'" "$TMP/management-staff-layout.js"
grep -Fq "makeAction(info.kind, info.name, 'up', '↑'" "$TMP/management-staff-layout.js"
grep -Fq "makeAction(info.kind, info.name, 'down', '↓'" "$TMP/management-staff-layout.js"
grep -Fq 'setLayout(next);' "$TMP/management-staff-layout.js"
grep -Fq 'setLayout(previous);' "$TMP/management-staff-layout.js"
! grep -Fq 'SERVICE_STAFF_LAYOUT_KEY' "$TMP/report_storage.py"
! grep -Fq 'Настроить персонал' "$TMP/management-staff-layout.js"
echo 'TARGET_CONTRACT=PASS'

if [[ "$MODE" == '--preflight' ]]; then
  echo 'MUTATION=NONE'
  echo 'MANAGEMENT_STAFF_LAYOUT_PREFLIGHT=PASS'
  exit 0
fi

BACKUP="/var/lib/az-baze/backups/management-staff-layout-$(date -u '+%Y%m%dT%H%M%SZ')"
mkdir -p "$BACKUP"

cp -a "$STORAGE" "$BACKUP/report_storage.py"
cp -a "$INDEX" "$BACKUP/index.html"
cp -a "$PERIOD" "$BACKUP/period-view.js"
if [[ "$LAYOUT_EXISTED_BEFORE" == '1' ]]; then
  cp -a "$LAYOUT" "$BACKUP/management-staff-layout.js"
fi

DB="$DB" OUT="$BACKUP/auth.db" python3 - <<'PY'
import os, sqlite3
src = sqlite3.connect(f"file:{os.environ['DB']}?mode=ro", uri=True)
dst = sqlite3.connect(os.environ["OUT"])
try:
    src.backup(dst)
finally:
    src.close()
    dst.close()

check = sqlite3.connect(f"file:{os.environ['OUT']}?mode=ro", uri=True)
try:
    result = check.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    check.close()

if result != "ok":
    raise SystemExit(f"STOP=BACKUP_DB_INTEGRITY:{result}")
os.chmod(os.environ["OUT"], 0o600)
print("DB_BACKUP_VERIFY=PASS")
PY

echo "BACKUP=$BACKUP"

systemctl stop "$SERVICE"
MUTATED=1

install -o root -g root -m 0644 "$TMP/report_storage.py" "$STORAGE"
install -o root -g root -m 0644 "$TMP/index.html" "$INDEX"
install -o root -g root -m 0644 "$TMP/period-view.js" "$PERIOD"
install -o root -g root -m 0644 "$TMP/management-staff-layout.js" "$LAYOUT"

[[ "$(git hash-object "$STORAGE")" == "$TARGET_STORAGE" ]]
[[ "$(git hash-object "$INDEX")" == "$TARGET_INDEX" ]]
[[ "$(git hash-object "$PERIOD")" == "$TARGET_PERIOD" ]]
[[ "$(git hash-object "$LAYOUT")" == "$TARGET_LAYOUT" ]]
python3 -m py_compile "$STORAGE"
echo 'INSTALLED_BLOBS=PASS'

systemctl start "$SERVICE"

post_health='FAIL'
for _ in $(seq 1 30); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    post_health='PASS'
    break
  fi
  sleep 1
done
[[ "$post_health" == 'PASS' ]] || {
  echo 'POST_HEALTH=FAIL'
  false
}
echo 'POST_HEALTH=PASS'

python3 - "$DB" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
try:
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    conn.close()
if result != "ok":
    raise SystemExit(f"POST_DB_INTEGRITY_FAIL={result}")
print("POST_DB_INTEGRITY=PASS")
PY

grep -Fq 'MANAGEMENT_STAFF_LAYOUT_KEY = "az-management-staff-layout-v1"' "$STORAGE"
grep -Fq 'management-staff-layout.js?v=20261110-1' "$INDEX"
grep -Fq "managementStaffEntries('dentists')" "$PERIOD"
grep -Fq 'Скрытые сотрудники' "$LAYOUT"
grep -Fq 'setLayout(next);' "$LAYOUT"
grep -Fq 'setLayout(previous);' "$LAYOUT"

MUTATED=0

echo 'BUSINESS_DATA_BACKFILL=NOT_PERFORMED'
echo 'DB_RESTORE=NOT_PERFORMED'
echo 'INLINE_CONTROLS=UP_HIDE_RESTORE_DOWN'
echo 'OPTIMISTIC_UI=YES'
echo 'SERVER_PERSISTENCE=YES'
echo "REPORT_STORAGE_BLOB=$(git hash-object "$STORAGE")"
echo "INDEX_BLOB=$(git hash-object "$INDEX")"
echo "PERIOD_VIEW_BLOB=$(git hash-object "$PERIOD")"
echo "MANAGEMENT_STAFF_LAYOUT_BLOB=$(git hash-object "$LAYOUT")"
echo 'GLOBAL_MANAGEMENT_STAFF_LAYOUT_DEPLOY=PASS'
