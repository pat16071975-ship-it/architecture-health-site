#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="/opt/az-baze-auth"
SERVICE="az-baze-auth"
WORKER="az-baze-knowledge-video-worker"
DB="/var/lib/az-baze/auth.db"
CACHE_ROOT="/var/lib/az-baze/knowledge-compatible"
UNIT="/etc/systemd/system/${WORKER}.service"

COMMIT="253a0df48b22ae8333cf77d476f3f42165949bb4"

BASE_CATALOG="ecda99182910d7d727e34a78d675bef0d62b840e"
BASE_VIEW="7d20d334966fbb9dfe0550deb94858fcc2892bd5"
BASE_NAV="5ab12244c78bb4b322f598c984d48c57b8b0d4a7"

TARGET_CATALOG="5913a73639f517f48737a80667ef3007940a374c"
TARGET_COMPAT="0aadec2e8fd3347a93ccb61689e42870cecb214b"
TARGET_VIEW="1211888359da609778dc0a479399ccd88157ea3b"
TARGET_UNIT="28af3c1e007637483ea63b0ecf961f15cf7abf58"

TMP="$(mktemp -d /tmp/az-knowledge-video-compat.XXXXXX)"
BACKUP="/var/lib/az-baze/backups/knowledge-video-compat-$(date -u +%Y%m%dT%H%M%SZ)"
APPLIED=0

cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

rollback() {
  rc=$?
  trap - ERR
  set +e
  if [ "$APPLIED" -eq 1 ]; then
    echo "ROLLBACK=START"
    systemctl disable --now "$WORKER" >/dev/null 2>&1 || true
    if [ -f "$BACKUP/worker.service" ]; then
      cp -a "$BACKUP/worker.service" "$UNIT"
    else
      rm -f "$UNIT"
    fi
    cp -a "$BACKUP/knowledge_catalog.py" "$ROOT/knowledge_catalog.py"
    cp -a "$BACKUP/knowledge_view.html" "$ROOT/templates/knowledge_view.html"
    if [ -f "$BACKUP/knowledge_video_compat.py" ]; then
      cp -a "$BACKUP/knowledge_video_compat.py" "$ROOT/knowledge_video_compat.py"
    else
      rm -f "$ROOT/knowledge_video_compat.py"
    fi
    systemctl daemon-reload
    systemctl restart "$SERVICE" || true
    echo "ROLLBACK=PASS"
  fi
  exit "$rc"
}
trap rollback ERR

echo "=== AZ-BAZE KNOWLEDGE VIDEO COMPAT DEPLOY ==="

test "$(hostname -s)" = "az-server"
test "$(id -u)" -eq 0
test -x "$ROOT/venv/bin/python"
test -f "$DB"
test -d /mnt/qnap-obuchenie
mountpoint -q /mnt/qnap-obuchenie
findmnt -no OPTIONS /mnt/qnap-obuchenie | tr ',' '\n' | grep -qx ro
command -v ffmpeg >/dev/null
command -v ffprobe >/dev/null
systemctl is-active --quiet "$SERVICE"

test "$(git hash-object "$ROOT/knowledge_catalog.py")" = "$BASE_CATALOG"
test "$(git hash-object "$ROOT/templates/knowledge_view.html")" = "$BASE_VIEW"
test "$(git hash-object "$ROOT/section_navigation.py")" = "$BASE_NAV"

echo "PREFLIGHT=PASS"
echo "QNAP_READ_ONLY=PASS"
echo "FFMPEG=PASS"

mkdir -p "$TMP/templates" "$TMP/deploy"

curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$COMMIT/az-baze-auth/knowledge_catalog.py" -o "$TMP/knowledge_catalog.py"
curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$COMMIT/az-baze-auth/knowledge_video_compat.py" -o "$TMP/knowledge_video_compat.py"
curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$COMMIT/az-baze-auth/templates/knowledge_view.html" -o "$TMP/templates/knowledge_view.html"
curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$COMMIT/az-baze-auth/deploy/az-baze-knowledge-video-worker.service" -o "$TMP/deploy/worker.service"

test "$(git hash-object "$TMP/knowledge_catalog.py")" = "$TARGET_CATALOG"
test "$(git hash-object "$TMP/knowledge_video_compat.py")" = "$TARGET_COMPAT"
test "$(git hash-object "$TMP/templates/knowledge_view.html")" = "$TARGET_VIEW"
test "$(git hash-object "$TMP/deploy/worker.service")" = "$TARGET_UNIT"

"$ROOT/venv/bin/python" -m py_compile   "$TMP/knowledge_catalog.py"   "$TMP/knowledge_video_compat.py"

echo "TARGET_SOURCE=PASS"

mkdir -p "$BACKUP"
cp -a "$ROOT/knowledge_catalog.py" "$BACKUP/knowledge_catalog.py"
cp -a "$ROOT/templates/knowledge_view.html" "$BACKUP/knowledge_view.html"
[ ! -f "$ROOT/knowledge_video_compat.py" ] || cp -a "$ROOT/knowledge_video_compat.py" "$BACKUP/knowledge_video_compat.py"
[ ! -f "$UNIT" ] || cp -a "$UNIT" "$BACKUP/worker.service"

"$ROOT/venv/bin/python" - "$DB" "$BACKUP/auth.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
check = sqlite3.connect(f"file:{sys.argv[2]}?mode=ro", uri=True)
try:
    assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
finally:
    check.close()
print("DB_BACKUP=PASS")
PY

echo "BACKUP=$BACKUP"

APPLIED=1

uid="$(stat -c %u "$ROOT/knowledge_catalog.py")"
gid="$(stat -c %g "$ROOT/knowledge_catalog.py")"
mode="$(stat -c %a "$ROOT/knowledge_catalog.py")"
install -o "$uid" -g "$gid" -m "$mode" "$TMP/knowledge_catalog.py" "$ROOT/knowledge_catalog.py"
install -o "$uid" -g "$gid" -m "$mode" "$TMP/knowledge_video_compat.py" "$ROOT/knowledge_video_compat.py"

tuid="$(stat -c %u "$ROOT/templates/knowledge_view.html")"
tgid="$(stat -c %g "$ROOT/templates/knowledge_view.html")"
tmode="$(stat -c %a "$ROOT/templates/knowledge_view.html")"
install -o "$tuid" -g "$tgid" -m "$tmode" "$TMP/templates/knowledge_view.html" "$ROOT/templates/knowledge_view.html"

install -o root -g root -m 0644 "$TMP/deploy/worker.service" "$UNIT"
install -d -o azadmin -g www-data -m 0750 "$CACHE_ROOT"

systemctl daemon-reload
systemctl enable --now "$WORKER"
systemctl restart "$SERVICE"

for _ in $(seq 1 30); do
  if systemctl is-active --quiet "$SERVICE" && systemctl is-active --quiet "$WORKER" &&      [ "$(curl -fsS http://127.0.0.1:8001/health 2>/dev/null)" = "ok" ]; then
    break
  fi
  sleep 1
done

systemctl is-active --quiet "$SERVICE"
systemctl is-active --quiet "$WORKER"
test "$(curl -fsS http://127.0.0.1:8001/health)" = "ok"

test "$(git hash-object "$ROOT/knowledge_catalog.py")" = "$TARGET_CATALOG"
test "$(git hash-object "$ROOT/knowledge_video_compat.py")" = "$TARGET_COMPAT"
test "$(git hash-object "$ROOT/templates/knowledge_view.html")" = "$TARGET_VIEW"

"$ROOT/venv/bin/python" - "$DB" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
files = conn.execute("SELECT COUNT(*) FROM knowledge_entries WHERE entry_type='file' AND hidden=0").fetchone()[0]
dirs = conn.execute("SELECT COUNT(*) FROM knowledge_entries WHERE entry_type='directory' AND hidden=0").fetchone()[0]
table = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='knowledge_video_compat'").fetchone()[0]
assert files == 2371, files
assert dirs == 455, dirs
assert table == 1, table
print(f"VISIBLE_FILES={files}")
print(f"VISIBLE_DIRECTORIES={dirs}")
print("VIDEO_COMPAT_TABLE=PASS")
conn.close()
PY

runuser -u azadmin -- test -r "/mnt/qnap-obuchenie/Управление и сервис/Скляров Управление клиникой/1 день 1 часть.MP4"

echo "SERVICE=PASS"
echo "WORKER=PASS"
echo "CACHE_ROOT=$CACHE_ROOT"
echo "CACHE_LIMIT=12_GIB"
echo "MIN_FREE_DISK=4_GIB"
echo "QNAP_WRITES=NONE"
echo "KNOWLEDGE_VIDEO_COMPAT_PRODUCTION_DEPLOY=PASS"

trap - ERR
APPLIED=0
