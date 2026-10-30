#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_COMMIT='eb5949c3b3bc4d2d82695e85f9ca87fd40b11503'
CONFIRM_TOKEN='ADVERTISING-STRUCTURE-LABEL-EB5949C3'
LIVE='/var/www/az-baze.ru/reports/advertising.js'
BASE_BLOB='4af7824727f464f8555757664515a80fb74cd979'
TARGET_BLOB='0bc09dc6c1a0013d18877bff8cd110fb94cb0216'
BACKUP_ROOT='/var/lib/az-baze/backups'
HEALTH='http://127.0.0.1:8001/health'

MODE="${1:-}"
TOKEN="${2:-}"
TMP=''

cleanup(){ [[ -n "${TMP:-}" && -f "$TMP" ]] && rm -f -- "$TMP"; }
trap cleanup EXIT
fail(){ echo "STOP: $*" >&2; exit 1; }

preflight(){
  [[ "$(id -u)" == '0' ]] || fail 'run as root'
  [[ "$(hostname -s)" == 'az-server' ]] || fail "wrong host $(hostname -s)"
  for c in curl git; do command -v "$c" >/dev/null 2>&1 || fail "missing command $c"; done

  [[ -f "$LIVE" && ! -L "$LIVE" ]] || fail 'live JS path invalid'
  curl -fsS --connect-timeout 2 --max-time 5 "$HEALTH" >/dev/null || fail 'service health failed'
  echo 'SERVICE_HEALTH=PASS'

  actual="$(git hash-object "$LIVE")"
  [[ "$actual" == "$BASE_BLOB" ]] || fail "live base mismatch: $actual"
  echo 'LIVE_BASE_GUARD=PASS'

  TMP="$(mktemp /tmp/az-ad-structure-label-XXXXXX.js)"
  curl -fsSL --retry 2 --connect-timeout 5 --max-time 30     "https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/$TARGET_COMMIT/reports/advertising.js"     -o "$TMP"

  [[ "$(git hash-object "$TMP")" == "$TARGET_BLOB" ]] || fail 'target blob mismatch'
  grep -Fq "['Отделение структуры',directions.structure]" "$TMP"
  ! grep -Fq "['Клиника',directions.structure]" "$TMP"

  echo 'TARGET_CONTRACT=PASS'
  echo 'ADVERTISING_STRUCTURE_LABEL_PREFLIGHT=PASS; LIVE_CHANGES=NONE'
}

case "$MODE" in
  --preflight)
    preflight
    ;;
  --apply)
    [[ "$TOKEN" == "$CONFIRM_TOKEN" ]] || fail 'wrong confirm token'
    preflight

    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    backup="$BACKUP_ROOT/advertising-structure-label-$stamp"
    mkdir -p "$backup"
    cp -a -- "$LIVE" "$backup/advertising.js"
    [[ "$(git hash-object "$backup/advertising.js")" == "$BASE_BLOB" ]] || fail 'backup verify failed'
    echo "BACKUP=$backup"
    echo 'BACKUP_VERIFY=PASS'

    uid="$(stat -c %u "$LIVE")"
    gid="$(stat -c %g "$LIVE")"
    mode="$(stat -c %a "$LIVE")"
    install -o "$uid" -g "$gid" -m "$mode" "$TMP" "$LIVE"

    [[ "$(git hash-object "$LIVE")" == "$TARGET_BLOB" ]] || fail 'live target mismatch'
    curl -fsS --connect-timeout 2 --max-time 5 "$HEALTH" >/dev/null || fail 'service health failed after install'

    echo 'LIVE_TARGET_BLOB=PASS'
    echo 'SERVICE_HEALTH_POST=PASS'
    echo 'ADVERTISING_STRUCTURE_LABEL_PRODUCTION_DEPLOY=PASS'
    echo "TARGET_COMMIT=$TARGET_COMMIT"
    echo "BACKUP=$backup"
    echo 'CHANGED_FILES=1; DATABASE_WRITES=NONE; SERVICE_RESTART=NONE'
    ;;
  *)
    fail 'use --preflight or --apply <token>'
    ;;
esac
