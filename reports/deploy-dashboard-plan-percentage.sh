#!/usr/bin/env bash
set -euo pipefail

ROOT="/var/www/az-baze.ru/reports"
FILE="dashboard.html"
LIVE="$ROOT/$FILE"
BASE="d0e362182c44d38d064ed8aa30a88bbd07ad8c76"
TARGET="3a52d2018a2a93a1b83c55001e2bcd8d7a544c06"
COMMIT="2485a9d7fd34b40cb99936241800bdd369207b7f"
TMP="/tmp/dashboard-plan-percentage.html"
BACKUP="/var/lib/az-baze/backups/dashboard-plan-percentage-$(date -u +%Y%m%dT%H%M%SZ)"

actual="$(git hash-object "$LIVE")"
[ "$actual" = "$BASE" ] || {
  echo "BASE FAIL: $actual"
  exit 1
}
echo "PRECHECK BASE: OK"

curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$COMMIT/reports/dashboard.html" -o "$TMP"
[ "$(git hash-object "$TMP")" = "$TARGET" ] || {
  echo "TARGET BLOB FAIL"
  rm -f "$TMP"
  exit 1
}

grep -Fq "execution:hasPlan&&plan?factTotal/plan:null" "$TMP"
grep -Fq "Факт / план месяца" "$TMP"
if grep -Fq "execution:hasPlan&&due?factTotal/due:null" "$TMP"; then
  echo "OLD FORMULA STILL PRESENT"
  rm -f "$TMP"
  exit 1
fi
echo "TARGET FORMULA: OK"

mkdir -p "$BACKUP"
cp -a "$LIVE" "$BACKUP/$FILE"
[ "$(git hash-object "$BACKUP/$FILE")" = "$BASE" ] || {
  echo "BACKUP VERIFY FAIL"
  rm -f "$TMP"
  exit 1
}
echo "BACKUP VERIFIED: $BACKUP"

uid="$(stat -c %u "$LIVE")"
gid="$(stat -c %g "$LIVE")"
mode="$(stat -c %a "$LIVE")"

rollback() {
  echo "ROLLBACK: START"
  cp -a "$BACKUP/$FILE" "$LIVE"
  echo "ROLLBACK: DONE"
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then rollback; fi; rm -f "$TMP"; exit "$rc"' EXIT

install -o "$uid" -g "$gid" -m "$mode" "$TMP" "$LIVE"

[ "$(git hash-object "$LIVE")" = "$TARGET" ] || {
  echo "POSTCHECK BLOB FAIL"
  exit 1
}

grep -Fq "execution:hasPlan&&plan?factTotal/plan:null" "$LIVE"
grep -Fq "Факт / план месяца" "$LIVE"
systemctl is-active --quiet az-baze-auth
[ "$(curl -fsS http://127.0.0.1:8001/health)" = "ok" ]

trap - EXIT
rm -f "$TMP"

echo "DASHBOARD PLAN PERCENTAGE: PASS"
echo "FORMULA=fact/full_month_plan"
echo "SERVICE=active"
echo "HEALTH=ok"
