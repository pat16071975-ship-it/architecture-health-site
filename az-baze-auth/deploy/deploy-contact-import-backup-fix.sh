#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/az-baze-auth"
FILE="$ROOT/contact_excel_import.py"
BASE="654aa42748905ce0694ca4383ce08243cb987d63"
TARGET="51982fc1d108925d91137b59aec3c8cac22b72e6"
COMMIT="fb30058e69b6092ba12846b8bd6384ab45d4e98e"
TMP="/tmp/contact_excel_import.py"
BACKUP="/var/lib/az-baze/backups/contact-import-backup-dir-fix-$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="/var/lib/az-baze/contact-import-backups"

rollback() {
  echo "ROLLBACK: START"
  if [ -f "$BACKUP/contact_excel_import.py" ]; then
    cp -a "$BACKUP/contact_excel_import.py" "$FILE"
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
systemctl is-active --quiet az-baze-auth
echo "PRECHECK BASE: OK"

curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$COMMIT/az-baze-auth/contact_excel_import.py" -o "$TMP"
[ "$(git hash-object "$TMP")" = "$TARGET" ] || {
  echo "TARGET BLOB FAIL"
  exit 1
}
"$ROOT/venv/bin/python" -m py_compile "$TMP"
grep -Fq '/var/lib/az-baze/contact-import-backups' "$TMP"
echo "TARGET: OK"

mkdir -p "$BACKUP"
cp -a "$FILE" "$BACKUP/contact_excel_import.py"
[ "$(git hash-object "$BACKUP/contact_excel_import.py")" = "$BASE" ] || {
  echo "BACKUP VERIFY FAIL"
  exit 1
}
echo "BACKUP VERIFIED: $BACKUP"

install -d -o azadmin -g www-data -m 0750 "$BACKUP_DIR"
[ "$(stat -c %U "$BACKUP_DIR")" = "azadmin" ]
[ "$(stat -c %G "$BACKUP_DIR")" = "www-data" ]
echo "CONTACT IMPORT BACKUP DIR: OK"

uid="$(stat -c %u "$FILE")"
gid="$(stat -c %g "$FILE")"
mode="$(stat -c %a "$FILE")"
install -o "$uid" -g "$gid" -m "$mode" "$TMP" "$FILE"

systemctl restart az-baze-auth
sleep 2
systemctl is-active --quiet az-baze-auth
[ "$(curl -fsS http://127.0.0.1:8001/health)" = "ok" ]
[ "$(git hash-object "$FILE")" = "$TARGET" ]

set -a
source /etc/az-baze/auth.env
set +a
cd "$ROOT"
./venv/bin/python - <<'PY'
from pathlib import Path
p = Path("/var/lib/az-baze/contact-import-backups")
assert p.is_dir()
probe = p / ".write-test"
probe.write_text("ok", encoding="utf-8")
probe.unlink()
print("SERVICE BACKUP WRITE: OK")
PY

trap - EXIT
rm -f "$TMP"

echo "CONTACT IMPORT BACKUP FIX: PASS"
echo "SERVICE=active"
echo "HEALTH=ok"
