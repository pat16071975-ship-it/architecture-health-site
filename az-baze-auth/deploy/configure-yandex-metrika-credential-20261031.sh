#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HOST='az-server'
SERVICE='az-baze-auth'
ENV_FILE='/etc/az-baze/auth.env'
APP_ROOT='/opt/az-baze-auth'
HEALTH='http://127.0.0.1:8001/health'
BACKUP_ROOT='/var/lib/az-baze/backups'
CONFIRM_TOKEN='METRIKA-CREDENTIAL-91356C4F'
KEY='AZBAZE_YANDEX_METRIKA_TOKEN'
TARGET_REPORT_BLOB='d9def18c1a227e388586ddde55f24d1f3ce57e79'
TARGET_AUTO_BLOB='9967320615c4f9bd6ebdf5062545da3d8a7b3d6c'
TARGET_UPLOADS_BLOB='1effc5ae1b123816bde3ff792efaa2e649a6a90d'

MODE="${1:-}"
CONFIRM="${2:-}"
BACKUP=''
ENV_WRITTEN=0
CANDIDATE=''

fail(){ echo "STOP: $*" >&2; exit 1; }
health(){ curl -fsS --connect-timeout 2 --max-time 5 "$HEALTH" >/dev/null; }
wait_health(){ local i; for i in $(seq 1 30); do health && return 0; sleep 1; done; return 1; }
blob(){ git hash-object "$1"; }

product_guard(){
  [[ "$(blob "$APP_ROOT/advertising_report.py")" == "$TARGET_REPORT_BLOB" ]] || fail 'PR180 advertising_report.py is not deployed'
  [[ "$(blob "$APP_ROOT/metrika_auto.py")" == "$TARGET_AUTO_BLOB" ]] || fail 'PR180 metrika_auto.py is not deployed'
  [[ "$(blob "$APP_ROOT/templates/uploads.html")" == "$TARGET_UPLOADS_BLOB" ]] || fail 'PR180 uploads.html is not deployed'
  echo 'PR180_PRODUCT_TARGET_GUARD=PASS'
}

key_state(){
  python3 -B - "$ENV_FILE" "$KEY" <<'PY'
from pathlib import Path
import re,sys
text=Path(sys.argv[1]).read_text(encoding='utf-8')
key=re.escape(sys.argv[2])
rows=[line for line in text.splitlines() if re.match(r'^\s*(?:export\s+)?'+key+r'\s*=',line)]
print('present' if rows else 'absent')
if len(rows)>1: raise SystemExit(12)
PY
}

probe_token(){
  local token="$1"
  printf '%s' "$token" | python3 -c '
import datetime,json,sys,urllib.error,urllib.parse,urllib.request
from zoneinfo import ZoneInfo

token=sys.stdin.read().strip()
if not token:
    raise SystemExit("EMPTY_TOKEN")

def fetch_json(url, params=None, auth=True):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    headers={"Accept":"application/json","User-Agent":"AZ-BAZE-Credential-Probe/1.0"}
    if auth:
        headers["Authorization"]="OAuth "+token
    req=urllib.request.Request(url,headers=headers,method="GET")
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            raw=r.read()
    except urllib.error.HTTPError as exc:
        raise SystemExit("API_HTTP_ERROR_%s" % exc.code)
    except Exception:
        raise SystemExit("API_CONNECTION_ERROR")
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        raise SystemExit("API_JSON_ERROR")

def fetch_text(url):
    req=urllib.request.Request(url,headers={"Accept":"text/html,application/json","User-Agent":"AZ-BAZE-Credential-Probe/1.0"},method="GET")
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            return r.read().decode("utf-8","replace")
    except urllib.error.HTTPError as exc:
        raise SystemExit("OAUTH_APP_INFO_HTTP_ERROR_%s" % exc.code)
    except Exception:
        raise SystemExit("OAUTH_APP_INFO_CONNECTION_ERROR")

identity=fetch_json("https://login.yandex.ru/info",{"format":"json"})
client_id=str(identity.get("client_id") or "").strip()
if not client_id:
    raise SystemExit("OAUTH_CLIENT_ID_NOT_FOUND")

app_info=fetch_text("https://oauth.yandex.ru/client/%s/info" % urllib.parse.quote(client_id,safe=""))
if "metrika:read" not in app_info:
    raise SystemExit("CANNOT_PROVE_METRIKA_READ_SCOPE")
for forbidden in ("metrika:write","metrika:expenses","metrika:user_params","metrika:offline_data"):
    if forbidden in app_info:
        raise SystemExit("OAUTH_APP_HAS_FORBIDDEN_METRIKA_SCOPE_"+forbidden.replace(":","_"))
print("OAUTH_APP_METRIKA_SCOPE=READ_ONLY_PASS")

counter=fetch_json("https://api-metrika.yandex.net/management/v1/counter/113776005")
c=counter.get("counter") if isinstance(counter,dict) else None
if not isinstance(c,dict) or str(c.get("id") or "")!="113776005":
    raise SystemExit("COUNTER_ID_MISMATCH")
if str(c.get("permission") or "")!="own":
    raise SystemExit("COUNTER_PERMISSION_NOT_OWN")
if str(c.get("status") or "Active")!="Active":
    raise SystemExit("COUNTER_NOT_ACTIVE")
zone=str(c.get("time_zone_name") or "")
if zone and zone!="Asia/Novosibirsk":
    raise SystemExit("COUNTER_TIMEZONE_MISMATCH")
print("COUNTER_113776005_READ=PASS; PERMISSION=own")

goals=fetch_json("https://api-metrika.yandex.net/management/v1/counter/113776005/goals")
rows=goals.get("goals") if isinstance(goals,dict) else None
if not isinstance(rows,list):
    raise SystemExit("GOALS_READ_FAILED")
required={
"Открытие окна записи","Переход к записи в Medflex","Нажатие на телефон",
"Автоцель: клик по номеру телефона","Автоцель: переход в мессенджер",
"Автоцель: переход в соц.сеть","Автоцель: скачивание файла"}
active={str(x.get("name") or "") for x in rows if isinstance(x,dict) and str(x.get("status") or "Active")=="Active"}
if not required.issubset(active):
    raise SystemExit("REQUIRED_GOALS_MISSING")
print("METRIKA_GOALS_READ=PASS")

yesterday=datetime.datetime.now(ZoneInfo("Asia/Novosibirsk")).date()-datetime.timedelta(days=1)
report=fetch_json("https://api-metrika.yandex.net/stat/v1/data",{
    "ids":"113776005",
    "dimensions":"ym:s:date",
    "metrics":"ym:s:visits",
    "date1":yesterday.isoformat(),
    "date2":yesterday.isoformat(),
    "filters":"ym:s:isRobot==\x27No\x27",
    "attribution":"cross_device_last_significant",
    "limit":100000,
    "offset":1,
})
if report.get("sampled") is True or float(report.get("sample_share",1.0))!=1.0:
    raise SystemExit("REPORT_SAMPLED")
if not isinstance(report.get("data"),list):
    raise SystemExit("REPORT_READ_FAILED")
print("METRIKA_REPORT_READ=PASS")
print("CREDENTIAL_READONLY_PROBE=PASS")
'
}

write_env(){
  local token="$1"
  printf '%s' "$token" | python3 -c '
import os,pathlib,re,shlex,sys,tempfile
path=pathlib.Path(sys.argv[1]); key=sys.argv[2]; token=sys.stdin.read().strip()
if not token: raise SystemExit("EMPTY_TOKEN")
text=path.read_text(encoding="utf-8")
pat=re.compile(r"^\s*(?:export\s+)?"+re.escape(key)+r"\s*=",re.M)
if pat.search(text): raise SystemExit("TOKEN_KEY_ALREADY_PRESENT")
st=path.stat()
new=text
if new and not new.endswith("\n"): new+="\n"
new+=key+"="+shlex.quote(token)+"\n"
fd,tmp=tempfile.mkstemp(prefix="."+path.name+".",dir=str(path.parent))
try:
    with os.fdopen(fd,"w",encoding="utf-8") as out:
        out.write(new); out.flush(); os.fsync(out.fileno())
    os.chown(tmp,st.st_uid,st.st_gid); os.chmod(tmp,st.st_mode & 0o7777)
    os.replace(tmp,path)
    dfd=os.open(path.parent,os.O_DIRECTORY)
    try: os.fsync(dfd)
    finally: os.close(dfd)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
' "$ENV_FILE" "$KEY"
  ENV_WRITTEN=1
}

rollback_env(){
  echo 'CREDENTIAL_ROLLBACK=START'
  systemctl stop "$SERVICE" || true
  cp -a -- "$BACKUP/auth.env" "$ENV_FILE"
  systemctl start "$SERVICE" || true
  wait_health || true
  echo 'CREDENTIAL_ROLLBACK=PASS'
}

preflight(){
  [[ "$(id -u)" == '0' ]] || fail 'run as root'
  [[ "$(hostname -s)" == "$EXPECTED_HOST" ]] || fail "wrong host $(hostname -s)"
  for c in curl git python3 systemctl; do command -v "$c" >/dev/null 2>&1 || fail "missing command $c"; done
  [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail 'environment file invalid'
  systemctl is-active --quiet "$SERVICE" || fail 'service not active'
  health || fail 'service health failed'
  product_guard
  state="$(key_state)"
  [[ "$state" == 'absent' ]] || fail 'Metrika credential key already exists; refusing overwrite'
  systemctl show -p EnvironmentFiles --value "$SERVICE" | grep -Fq "$ENV_FILE" || fail 'service does not reference expected EnvironmentFile'
  echo 'SERVICE_ENVIRONMENT_FILE=PASS'
  echo 'METRIKA_CREDENTIAL_PREFLIGHT=PASS; SECRET_READ=NONE; LIVE_CHANGES=NONE'
}

read_candidate(){
  [[ -r /dev/tty ]] || fail 'interactive TTY required for hidden credential input'
  printf 'Paste existing Yandex Metrika OAuth token (hidden): ' >/dev/tty
  IFS= read -r -s CANDIDATE </dev/tty
  printf '\n' >/dev/tty
  [[ -n "$CANDIDATE" ]] || fail 'empty credential'
}

on_error(){
  local code="$1"
  trap - ERR
  if [[ "$ENV_WRITTEN" == '1' && -n "$BACKUP" ]]; then rollback_env; fi
  CANDIDATE=''
  unset CANDIDATE || true
  exit "$code"
}
trap 'on_error $?' ERR

case "$MODE" in
  --preflight)
    preflight
    ;;
  --verify-only)
    preflight
    read_candidate
    probe_token "$CANDIDATE"
    CANDIDATE=''; unset CANDIDATE
    echo 'METRIKA_CREDENTIAL_VERIFY_ONLY=PASS; SECRET_PERSISTED=NO'
    ;;
  --apply)
    [[ "$CONFIRM" == "$CONFIRM_TOKEN" ]] || fail 'wrong confirm token'
    preflight
    read_candidate
    probe_token "$CANDIDATE"

    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    BACKUP="$BACKUP_ROOT/metrika-credential-$stamp"
    mkdir -p -- "$BACKUP"
    chmod 0700 "$BACKUP"
    cp -a -- "$ENV_FILE" "$BACKUP/auth.env"
    echo "BACKUP=$BACKUP"
    echo 'ENV_BACKUP=PASS'

    write_env "$CANDIDATE"
    [[ "$(key_state)" == 'present' ]] || fail 'credential key not present after atomic write'
    echo 'ENV_ATOMIC_WRITE=PASS; SECRET_OUTPUT=NONE'

    systemctl restart "$SERVICE"
    wait_health || fail 'service health failed after credential install'
    echo 'SERVICE_HEALTH_POST=PASS'
    probe_token "$CANDIDATE"

    ENV_WRITTEN=0
    CANDIDATE=''; unset CANDIDATE
    echo 'METRIKA_CREDENTIAL_CONFIGURATION=PASS'
    echo 'SECRET_PERSISTED=YES; SECRET_PRINTED=NO; SECRET_ARGV=NO'
    ;;
  *)
    fail "use --preflight, --verify-only, or --apply $CONFIRM_TOKEN"
    ;;
esac
