#!/usr/bin/env bash
set -euo pipefail

ROOT="/var/www/az-baze.ru/reports"
TARGET_COMMIT="c8819e12e6844726729e8e6da7a1eff598f3d9b0"
BACKUP="/var/lib/az-baze/backups/report-fonts-$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$(mktemp -d)"
ROLLBACK=0

declare -A BASE=(
  [dashboard.html]="fdccd096f5ace9b9c517465f884c5faf6b49f0bb"
  [services-base.html]="ab8d3f8fd1c7888b5c8c4957ef169b74da495fd5"
  [index.html]="2483a0e262d434e4d21e2bb2a7cae579c99d7576"
  [finrez.html]="1b44b3f9ed756c4d037809d6b3d85a685e794871"
  [forecast.html]="48905cde3b5392d2f58d0022fd976d319a3ddbbb"
  [economics-dashboard.html]="daa642144f584dff73342728bccee1d0082ab443"
  [transitions.html]="61878c44cc7de49f50e36e76ec15d7b7fc2ee53c"
  [import-20260831.html]="1044db19cdb853ed7e0b029f6b183a22d880d026"
  [services.html]="206545a727bd2d7e8738f2cc2dd260e5e9a15f5a"
  [economics-import.html]="6b0bf2f4d832969386f89ddc935c62eb971eb1e6"
)

declare -A TARGET=(
  [dashboard.html]="d0e362182c44d38d064ed8aa30a88bbd07ad8c76"
  [services-base.html]="34dafc898536afedb01250bfa3b244beffcbedb0"
  [index.html]="67f818b2b82b627c95c04a8e94f0071010df6ec6"
  [finrez.html]="f5a7cc0627c7962ae96cabf21bbe6ce40036bed7"
  [forecast.html]="0402f23d7f97672ec6a574d391a6ac2b4c499ba5"
  [economics-dashboard.html]="dbe7502966eca0d2bbf151310a5afc085ab214e6"
  [transitions.html]="654697860a2e13071882336148fdf98d26d4229a"
  [import-20260831.html]="07b5f6da1521393639b045bf2080aa92d877c742"
  [services.html]="4dd01dd2b2680a3f4e76beb7c4021951b2a532cd"
  [economics-import.html]="c6500819c3699a3bab4b0747c44e0ecb590e9384"
)

FILES=(
  dashboard.html
  services-base.html
  index.html
  finrez.html
  forecast.html
  economics-dashboard.html
  transitions.html
  import-20260831.html
  services.html
  economics-import.html
)

rollback() {
  if [ "$ROLLBACK" = "1" ] && [ -d "$BACKUP" ]; then
    echo "ROLLBACK: START"
    for file in "${FILES[@]}"; do
      if [ -f "$BACKUP/$file" ]; then
        cp -a "$BACKUP/$file" "$ROOT/$file"
      fi
    done
    echo "ROLLBACK: DONE"
  fi
}

cleanup() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    rollback
  fi
  rm -rf "$TMP"
  exit "$rc"
}
trap cleanup EXIT

echo "PRECHECK: START"
for file in "${FILES[@]}"; do
  live="$ROOT/$file"
  [ -f "$live" ] || { echo "MISSING LIVE FILE: $file"; exit 1; }
  actual="$(git hash-object "$live")"
  [ "$actual" = "${BASE[$file]}" ] || {
    echo "BASE FAIL $file: $actual"
    exit 1
  }
done
echo "PRECHECK LIVE BASES: OK"

for file in "${FILES[@]}"; do
  if [ "$file" = "services.html" ]; then
    cp -a "$ROOT/$file" "$TMP/$file"
    python3 - "$TMP/$file" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")
old = "font-family:Arial,sans-serif"
new = "font-family:Montserrat,Arial,sans-serif"
if old not in s:
    raise SystemExit("services.html font anchor missing")
s = s.replace(old, new, 1)
link = '<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&display=swap" rel="stylesheet">'
if link not in s:
    s = s.replace("</title>", "</title>\n" + link, 1)
p.write_text(s, encoding="utf-8")
PY
  else
    curl -fsSL "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$TARGET_COMMIT/reports/$file" -o "$TMP/$file"
  fi
  actual="$(git hash-object "$TMP/$file")"
  [ "$actual" = "${TARGET[$file]}" ] || {
    echo "TARGET BLOB FAIL $file: $actual"
    exit 1
  }
done
echo "TARGET DOWNLOAD+BLOBS: OK"

mkdir -p "$BACKUP"
for file in "${FILES[@]}"; do
  cp -a "$ROOT/$file" "$BACKUP/$file"
done

for file in "${FILES[@]}"; do
  [ "$(git hash-object "$BACKUP/$file")" = "${BASE[$file]}" ] || {
    echo "BACKUP VERIFY FAIL: $file"
    exit 1
  }
done
echo "BACKUP VERIFIED: $BACKUP"

ROLLBACK=1
for file in "${FILES[@]}"; do
  uid="$(stat -c %u "$ROOT/$file")"
  gid="$(stat -c %g "$ROOT/$file")"
  mode="$(stat -c %a "$ROOT/$file")"
  install -o "$uid" -g "$gid" -m "$mode" "$TMP/$file" "$ROOT/$file"
done

for file in "${FILES[@]}"; do
  actual="$(git hash-object "$ROOT/$file")"
  [ "$actual" = "${TARGET[$file]}" ] || {
    echo "POSTCHECK BLOB FAIL $file: $actual"
    exit 1
  }
done

systemctl is-active --quiet az-baze-auth
[ "$(curl -fsS http://127.0.0.1:8001/health)" = "ok" ]

ROLLBACK=0
echo "REPORT TYPOGRAPHY DEPLOY: PASS"
echo "FILES=10"
echo "FONT=Montserrat"
echo "SERVICE=active"
echo "HEALTH=ok"
