#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/az-baze-auth"
SERVER="$ROOT/server.py"
NAV="$ROOT/section_navigation.py"

BASE_SERVER="381677b67d6a95e4551ed83910149f3dd85a6126"
TARGET_SERVER="c1f74ed0ca02c10f7b704f75416f1855226c40f3"
TARGET_NAV="d50d6c9d513d05316674b2a7071865fa7c34eadb"
COMMIT="17c66266a9ad4fde614e0d069686424f2384b9f7"

TMP_SERVER="/tmp/az-server-nav-server.py"
TMP_NAV="/tmp/az-section-navigation.py"
BACKUP="/var/lib/az-baze/backups/section-navigation-$(date -u +%Y%m%dT%H%M%SZ)"

rollback() {
  echo "ROLLBACK: START"
  if [ -f "$BACKUP/server.py" ]; then
    cp -a "$BACKUP/server.py" "$SERVER"
  fi
  rm -f "$NAV"
  systemctl restart az-baze-auth || true
  echo "ROLLBACK: DONE"
}

finish() {
  rc=$?
  if [ "$rc" -ne 0 ]; then rollback; fi
  rm -f "$TMP_SERVER" "$TMP_NAV"
  exit "$rc"
}
trap finish EXIT

echo "PRECHECK: START"
[ "$(git hash-object "$SERVER")" = "$BASE_SERVER" ] || {
  echo "SERVER BASE FAIL: $(git hash-object "$SERVER")"
  exit 1
}
[ ! -e "$NAV" ] || {
  echo "NAV FILE ALREADY EXISTS: $NAV"
  exit 1
}
systemctl is-active --quiet az-baze-auth
echo "PRECHECK BASE: OK"

curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$COMMIT/az-baze-auth/server.py" -o "$TMP_SERVER"
curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$COMMIT/az-baze-auth/section_navigation.py" -o "$TMP_NAV"

[ "$(git hash-object "$TMP_SERVER")" = "$TARGET_SERVER" ] || {
  echo "SERVER TARGET BLOB FAIL"
  exit 1
}
[ "$(git hash-object "$TMP_NAV")" = "$TARGET_NAV" ] || {
  echo "NAV TARGET BLOB FAIL"
  exit 1
}

"$ROOT/venv/bin/python" -m py_compile "$TMP_SERVER" "$TMP_NAV"
grep -Fq 'import section_navigation' "$TMP_SERVER"
grep -Fq 'section_navigation.inject_navigation' "$TMP_SERVER"
grep -Fq '"/reports/", "Отчёты", "/reports/"' "$TMP_NAV"
grep -Fq '"/access-contacts/", "Доступы и контакты", "/access-contacts/"' "$TMP_NAV"
grep -Fq '"/surveys/", "Опросы", "/surveys/"' "$TMP_NAV"
grep -Fq '@media(max-width:760px)' "$TMP_NAV"
echo "TARGET CHECKS: OK"

mkdir -p "$BACKUP"
cp -a "$SERVER" "$BACKUP/server.py"
[ "$(git hash-object "$BACKUP/server.py")" = "$BASE_SERVER" ] || {
  echo "BACKUP VERIFY FAIL"
  exit 1
}
echo "BACKUP VERIFIED: $BACKUP"

uid="$(stat -c %u "$SERVER")"
gid="$(stat -c %g "$SERVER")"
mode="$(stat -c %a "$SERVER")"
install -o "$uid" -g "$gid" -m "$mode" "$TMP_SERVER" "$SERVER"
install -o "$uid" -g "$gid" -m "$mode" "$TMP_NAV" "$NAV"

systemctl restart az-baze-auth
sleep 2
systemctl is-active --quiet az-baze-auth
[ "$(curl -fsS http://127.0.0.1:8001/health)" = "ok" ]
[ "$(git hash-object "$SERVER")" = "$TARGET_SERVER" ]
[ "$(git hash-object "$NAV")" = "$TARGET_NAV" ]

cd "$ROOT"
./venv/bin/python - <<'PY'
import section_navigation as nav

checks = {
    "/reports/dashboard.html": ("Отчёты", "/reports/"),
    "/contacts/": ("Доступы и контакты", "/access-contacts/"),
    "/surveys/5/results": ("Опросы", "/surveys/"),
    "/uploads/": ("Загрузка данных", "/uploads/"),
    "/admin": ("Управление доступом", "/admin"),
}
for path, expected in checks.items():
    item = nav.section_for_path(path)
    assert item and (item["label"], item["root"]) == expected, (path, item)

sample = '<!doctype html><html><head></head><body><a class="btn" href="/">На главную</a><main>OK</main></body></html>'
rendered = nav.inject_navigation(sample, "/reports/dashboard.html")
assert rendered.count("az-section-nav-style") == 1
assert rendered.count("На главную") == 1
assert "Отчёты" in rendered
assert "@media(max-width:760px)" in rendered
print("NAV COMPONENT SELFTEST: OK")
PY

trap - EXIT
rm -f "$TMP_SERVER" "$TMP_NAV"

echo "SECTION NAVIGATION DEPLOY: PASS"
echo "DESKTOP=sticky-top"
echo "MOBILE=fixed-bottom-two-buttons"
echo "SERVICE=active"
echo "HEALTH=ok"
