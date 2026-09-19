#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/az-baze-auth"
FILE="$ROOT/templates/contacts.html"
BASE="9fcbf279eb94dba25d84b80239682ef0eb9dc134"
TARGET="87e8b3abb1a99efba2517f600d5717ebf19a419b"
COMMIT="d246a07fd9e52ef806c6fbf4b6509235f83bc0dc"
TMP="/tmp/contacts-visual-emphasis.html"
BACKUP="/var/lib/az-baze/backups/contacts-visual-emphasis-$(date -u +%Y%m%dT%H%M%SZ)"

rollback() {
  echo "ROLLBACK: START"
  if [ -f "$BACKUP/contacts.html" ]; then
    cp -a "$BACKUP/contacts.html" "$FILE"
  fi
  systemctl restart az-baze-auth || true
  echo "ROLLBACK: DONE"
}

finish() {
  rc=$?
  if [ "$rc" -ne 0 ]; then rollback; fi
  rm -f "$TMP"
  exit "$rc"
}
trap finish EXIT

echo "PRECHECK: START"
[ "$(git hash-object "$FILE")" = "$BASE" ] || {
  echo "BASE FAIL: $(git hash-object "$FILE")"
  exit 1
}
echo "PRECHECK BASE: OK"

curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$COMMIT/az-baze-auth/templates/contacts.html" -o "$TMP"
[ "$(git hash-object "$TMP")" = "$TARGET" ] || {
  echo "TARGET BLOB FAIL"
  exit 1
}
grep -Fq '.organization-value{font-weight:700}' "$TMP"
grep -Fq '.phone-value{font-weight:700;white-space:nowrap;overflow:visible;text-overflow:clip;flex:0 0 auto}' "$TMP"
grep -Fq 'minmax(220px,1.25fr)' "$TMP"
echo "TARGET UI CHECKS: OK"

mkdir -p "$BACKUP"
cp -a "$FILE" "$BACKUP/contacts.html"
[ "$(git hash-object "$BACKUP/contacts.html")" = "$BASE" ] || {
  echo "BACKUP VERIFY FAIL"
  exit 1
}
echo "BACKUP VERIFIED: $BACKUP"

uid="$(stat -c %u "$FILE")"
gid="$(stat -c %g "$FILE")"
mode="$(stat -c %a "$FILE")"
install -o "$uid" -g "$gid" -m "$mode" "$TMP" "$FILE"

systemctl restart az-baze-auth
sleep 2
systemctl is-active --quiet az-baze-auth
[ "$(curl -fsS http://127.0.0.1:8001/health)" = "ok" ]
[ "$(git hash-object "$FILE")" = "$TARGET" ]

trap - EXIT
rm -f "$TMP"

echo "CONTACTS VISUAL EMPHASIS: PASS"
echo "ORGANIZATION=bold"
echo "PHONE=bold-full-width"
echo "SERVICE=active"
echo "HEALTH=ok"
