#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/az-baze-auth"
COMMIT="f4bdb800afc2142d4368df895f511567304a650c"
TMP="$(mktemp -d)"
BACKUP="/var/lib/az-baze/backups/contact-excel-import-$(date -u +%Y%m%dT%H%M%SZ)"
ROLLBACK=0

BASE_EXT="006b73d1fac64b22e938b8bcca3e79c5816967a0"
BASE_CONTACTS="4392a2acdec7109927ee59b0255148c51ecdd71a"

TARGET_MODULE="654aa42748905ce0694ca4383ce08243cb987d63"
TARGET_EXT="9975ebed3330fc686273452b3906f8f52007ecc2"
TARGET_CONTACTS="9fcbf279eb94dba25d84b80239682ef0eb9dc134"
TARGET_TEMPLATE="432a6393e510f779ff3c0d2a28f27c9768c5c545"

rollback() {
  if [ "$ROLLBACK" = "1" ]; then
    echo "ROLLBACK: START"
    cp -a "$BACKUP/access_contacts_ext.py" "$ROOT/access_contacts_ext.py"
    cp -a "$BACKUP/contacts.html" "$ROOT/templates/contacts.html"
    rm -f "$ROOT/contact_excel_import.py"
    rm -f "$ROOT/templates/contact_import.html"
    systemctl restart az-baze-auth || true
    echo "ROLLBACK: DONE"
  fi
}

finish() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    rollback
  fi
  rm -rf "$TMP"
  exit "$rc"
}
trap finish EXIT

echo "PRECHECK: START"
[ "$(git hash-object "$ROOT/access_contacts_ext.py")" = "$BASE_EXT" ] || {
  echo "BASE FAIL access_contacts_ext.py: $(git hash-object "$ROOT/access_contacts_ext.py")"
  exit 1
}
[ "$(git hash-object "$ROOT/templates/contacts.html")" = "$BASE_CONTACTS" ] || {
  echo "BASE FAIL contacts.html: $(git hash-object "$ROOT/templates/contacts.html")"
  exit 1
}
[ ! -e "$ROOT/contact_excel_import.py" ] || {
  echo "BASE FAIL: contact_excel_import.py already exists"
  exit 1
}
[ ! -e "$ROOT/templates/contact_import.html" ] || {
  echo "BASE FAIL: contact_import.html already exists"
  exit 1
}
echo "PRECHECK BASE: OK"

curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$COMMIT/az-baze-auth/contact_excel_import.py" -o "$TMP/contact_excel_import.py"
curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$COMMIT/az-baze-auth/access_contacts_ext.py" -o "$TMP/access_contacts_ext.py"
curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$COMMIT/az-baze-auth/templates/contacts.html" -o "$TMP/contacts.html"
curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$COMMIT/az-baze-auth/templates/contact_import.html" -o "$TMP/contact_import.html"

[ "$(git hash-object "$TMP/contact_excel_import.py")" = "$TARGET_MODULE" ]
[ "$(git hash-object "$TMP/access_contacts_ext.py")" = "$TARGET_EXT" ]
[ "$(git hash-object "$TMP/contacts.html")" = "$TARGET_CONTACTS" ]
[ "$(git hash-object "$TMP/contact_import.html")" = "$TARGET_TEMPLATE" ]
echo "TARGET BLOBS: OK"

"$ROOT/venv/bin/python" -m py_compile "$TMP/contact_excel_import.py" "$TMP/access_contacts_ext.py"
"$ROOT/venv/bin/python" - "$TMP/contacts.html" "$TMP/contact_import.html" <<'PY'
from pathlib import Path
import sys
from jinja2 import Environment
env = Environment()
for item in sys.argv[1:]:
    env.parse(Path(item).read_text(encoding="utf-8"))
print("JINJA: OK")
PY

mkdir -p "$BACKUP"
cp -a "$ROOT/access_contacts_ext.py" "$BACKUP/access_contacts_ext.py"
cp -a "$ROOT/templates/contacts.html" "$BACKUP/contacts.html"
[ "$(git hash-object "$BACKUP/access_contacts_ext.py")" = "$BASE_EXT" ]
[ "$(git hash-object "$BACKUP/contacts.html")" = "$BASE_CONTACTS" ]
echo "BACKUP VERIFIED: $BACKUP"

uid_ext="$(stat -c %u "$ROOT/access_contacts_ext.py")"
gid_ext="$(stat -c %g "$ROOT/access_contacts_ext.py")"
mode_ext="$(stat -c %a "$ROOT/access_contacts_ext.py")"
uid_tpl="$(stat -c %u "$ROOT/templates/contacts.html")"
gid_tpl="$(stat -c %g "$ROOT/templates/contacts.html")"
mode_tpl="$(stat -c %a "$ROOT/templates/contacts.html")"

ROLLBACK=1
install -o "$uid_ext" -g "$gid_ext" -m "$mode_ext" "$TMP/contact_excel_import.py" "$ROOT/contact_excel_import.py"
install -o "$uid_ext" -g "$gid_ext" -m "$mode_ext" "$TMP/access_contacts_ext.py" "$ROOT/access_contacts_ext.py"
install -o "$uid_tpl" -g "$gid_tpl" -m "$mode_tpl" "$TMP/contacts.html" "$ROOT/templates/contacts.html"
install -o "$uid_tpl" -g "$gid_tpl" -m "$mode_tpl" "$TMP/contact_import.html" "$ROOT/templates/contact_import.html"

systemctl restart az-baze-auth
sleep 2
systemctl is-active --quiet az-baze-auth
[ "$(curl -fsS http://127.0.0.1:8001/health)" = "ok" ]

[ "$(git hash-object "$ROOT/contact_excel_import.py")" = "$TARGET_MODULE" ]
[ "$(git hash-object "$ROOT/access_contacts_ext.py")" = "$TARGET_EXT" ]
[ "$(git hash-object "$ROOT/templates/contacts.html")" = "$TARGET_CONTACTS" ]
[ "$(git hash-object "$ROOT/templates/contact_import.html")" = "$TARGET_TEMPLATE" ]

set -a
source /etc/az-baze/auth.env
set +a
cd "$ROOT"
./venv/bin/python - <<'PY'
import server
routes = {rule.rule for rule in server.app.url_map.iter_rules()}
assert "/contacts/import" in routes
print("ROUTE /contacts/import: OK")
PY

ROLLBACK=0
trap - EXIT
rm -rf "$TMP"

echo "CONTACT EXCEL UPLOAD DEPLOY: PASS"
echo "SERVICE=active"
echo "HEALTH=ok"
