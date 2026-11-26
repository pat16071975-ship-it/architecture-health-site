#!/usr/bin/env python3
"""Guarded, first-install-only IDENT-006B deployment. No remote execution.
Run from independently verified, root-owned bundle on the approved host only.
Never merges, edits AZ BAZE code/DB, or creates a live token.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import grp
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from zoneinfo import ZoneInfo

from nginx_edit import plan, render_locations, LOCATIONS, PATHS, valid_hostname

NAME = "az-ident006b"
USER = "az-ident-recv"
PROXY = "www-data"
SOURCE = Path(__file__).resolve().parent.parent
APP = Path("/opt") / NAME
ETC = Path("/etc") / NAME
DATA = Path("/var/lib") / NAME
STATE = DATA / "state"
BACKUPS = DATA / "deploy"
RUN = Path("/run") / NAME
SOCK = RUN / "receiver.sock"
UNIT_DIR = Path("/etc/systemd/system")
HTTP_SNIPPET = Path("/etc/nginx/conf.d/az-ident006b-http.conf")
ROUTES_SNIPPET = Path(LOCATIONS)
ACTIVE_RECORD = BACKUPS / "active.json"
PENDING_RECORD = BACKUPS / "pending.json"
CONFIG = ETC / "config.json"
APP_SOURCE = {
    "gateway.py": "5eb3a01359dc27c092dad16f1405d22619aff4a7fd366d7132359b8b6bc1af6d",
    "core/__init__.py": "d02291e88af8212e439ea2d47dc226a8c1a0abf0d4e2a6a00b609cd734b09288",
    "core/receiver.py": "e1079f56598ea14e6b784d9d6376d04fede1fef4cf03f4c1bca32ef594657a1b",
    "core/sql/payments_daily_v1.sql": "277cc67cb4c0369f7f3461ae5d8c50ac0443c3342cb44a81625cf701c52e443f",
}
DEPLOY_NAMES = ("launch.py","gunicorn_conf.py","requirements.lock",
                NAME+".service",NAME+".socket","nginx-http.conf","nginx-locations.conf",
                "installer.py","nginx_edit.py")
WHEELS = {
    "gunicorn-23.0.0-py3-none-any.whl":"ec400d38950de4dfd418cff8328b2c8faed0edb0d517d3394e457c317908ca4d",
    "packaging-26.4-py3-none-any.whl":"81383611a09c1bc0b08b76e0fd5829d7c6824c65b27a6aee339da133f4233ea2",
}
HEX = re.compile(r"[0-9a-f]{64}\Z")
ENV = {"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LANG":"C.UTF-8"}
PYTHON = "/usr/bin/python3"
MIB = 1024*1024


class Stop(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise Stop(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def absent(path):
    return not os.path.lexists(path)


def root_parent(path):
    require(path.is_absolute() and path.resolve(strict=True)==path,"CANONICAL_PATH_REQUIRED")
    for p in (path,*path.parents):
        st=p.lstat()
        require(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022,
                "ROOT_DIRECTORY_REQUIRED")


def root_read(path, limit=4*MIB, private=False):
    root_parent(path.parent)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,"rb") as f:
        st=os.fstat(f.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and st.st_nlink==1 and not st.st_mode&0o022,
                "ROOT_FILE_REQUIRED")
        if private:
            require(stat.S_IMODE(st.st_mode)==0o600,"PRIVATE_INPUT_REQUIRED")
        raw=f.read(limit+1)
    require(len(raw)<=limit,"FILE_TOO_LARGE")
    return raw


def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, raw, mode=0o644, uid=0, gid=0):
    root_parent(path.parent)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,mode)
    with os.fdopen(fd,"wb") as f:
        os.fchown(f.fileno(),uid,gid)
        os.fchmod(f.fileno(),mode)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    sync_dir(path.parent)


def replace_checked(path, expected, raw, mode, gid):
    require(digest(root_read(path))==expected,"LIVE_FILE_CHANGED")
    fd,name=tempfile.mkstemp(prefix=".ident006b-",dir=path.parent)
    tmp=Path(name)
    try:
        with os.fdopen(fd,"wb") as f:
            os.fchown(f.fileno(),0,gid)
            os.fchmod(f.fileno(),mode)
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        require(digest(root_read(path))==expected,"LIVE_FILE_CHANGED")
        os.replace(tmp,path)
        sync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def delete_checked(path, expected):
    if absent(path):
        return
    require(digest(root_read(path))==expected,"OWN_FILE_CHANGED")
    path.unlink()
    sync_dir(path.parent)


def mkdir(path,mode=0o755,uid=0,gid=0):
    root_parent(path.parent)
    path.mkdir(mode=mode)
    os.chown(path,uid,gid)
    os.chmod(path,mode)
    sync_dir(path.parent)


def run(args, code, timeout=90, allow=False):
    # Never print child command lines, stderr or configuration contents.
    result=subprocess.run(args,env=ENV,stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          timeout=timeout,check=False)
    if not allow:
        require(result.returncode==0,code)
    return result


def systemctl(*args,allow=False):
    return run(["/usr/bin/systemctl",*args],"SYSTEMD_OPERATION_FAILED",allow=allow)


def active(service):
    return systemctl("is-active","--quiet",service,allow=True).returncode==0


def enabled(service):
    return systemctl("is-enabled","--quiet",service,allow=True).returncode==0


def json_object(raw):
    def pairs(items):
        out={}
        for k,v in items:
            require(k not in out,"DUPLICATE_CONFIG_KEY")
            out[k]=v
        return out
    def constant(_):
        raise Stop("INVALID_JSON_CONSTANT")
    value=json.loads(raw.decode("utf-8"),object_pairs_hook=pairs,parse_constant=constant)
    require(type(value) is dict,"JSON_OBJECT_REQUIRED")
    return value


def config_bytes(path):
    obj=json_object(root_read(path,4096,private=True))
    require(set(obj)=={"organization_id","source_id","stream_id","source_timezone",
                      "token_sha256","max_store_bytes","min_free_bytes"},"CONFIG_FIELDS")
    for k in ("organization_id","source_id","stream_id"):
        u=uuid.UUID(obj[k])
        require(u.int!=0 and str(u)==obj[k],"CONFIG_UUID")
    require(isinstance(obj["source_timezone"],str) and len(obj["source_timezone"])<=100,"CONFIG_TIMEZONE")
    ZoneInfo(obj["source_timezone"])
    require(isinstance(obj["token_sha256"],str) and HEX.fullmatch(obj["token_sha256"]),"TOKEN_VERIFIER")
    for k in ("max_store_bytes","min_free_bytes"):
        require(type(obj[k]) is int and 16*MIB<=obj[k]<=8192*MIB,"CAPACITY_CONFIG")
    return (json.dumps(obj,ensure_ascii=True,sort_keys=True,separators=(",",":"))+"\n").encode()


def verify_bundle(manifest_sha):
    require(bool(HEX.fullmatch(manifest_sha)),"BUNDLE_MANIFEST_HASH_REQUIRED")
    root_parent(SOURCE)
    raw=root_read(SOURCE/"deploy"/"INSTALL-MANIFEST.json",16384)
    require(digest(raw)==manifest_sha,"BUNDLE_MANIFEST_MISMATCH")
    manifest=json_object(raw)
    expected=set(APP_SOURCE)|{"deploy/"+x for x in DEPLOY_NAMES}
    require(set(manifest)==expected,"BUNDLE_MANIFEST_SCOPE")
    data={}
    for name in sorted(expected):
        value=root_read(SOURCE/name)
        require(digest(value)==manifest[name],"BUNDLE_FILE_MISMATCH")
        if name in APP_SOURCE:
            require(manifest[name]==APP_SOURCE[name],"AUDITED_RECEIVER_CHANGED")
        data[name]=value
    return data


def health_existing():
    require(active("az-baze-auth.service"),"AZ_BAZE_AUTH_NOT_ACTIVE")
    conn=http.client.HTTPConnection("127.0.0.1",8001,timeout=5)
    try:
        conn.request("GET","/health",headers={"Connection":"close"})
        r=conn.getresponse()
        require(r.status==200 and r.read(16)==b"ok","AZ_BAZE_HEALTH_FAILED")
    finally:
        conn.close()


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost",timeout=5)
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(SOCK))


def local_probe():
    require(active(NAME+".socket") and active(NAME+".service"),"RECEIVER_NOT_ACTIVE")
    st=SOCK.lstat()
    require(stat.S_ISSOCK(st.st_mode) and st.st_uid==0 and st.st_gid==grp.getgrnam(PROXY).gr_gid
            and stat.S_IMODE(st.st_mode)==0o660,"SOCKET_ACL")
    conn=UnixHTTP()
    try:
        conn.request("GET",PATHS[1],headers={"X-Forwarded-Proto":"https","Connection":"close"})
        r=conn.getresponse()
        require(r.status==401 and json.loads(r.read(2048))=={"error":"UNAUTHORIZED"},"LOCAL_AUTH_PROBE")
    finally:
        conn.close()


def wait_local():
    for _ in range(30):
        try:
            local_probe()
            return
        except (OSError,Stop,http.client.HTTPException,ValueError):
            time.sleep(1)
    raise Stop("LOCAL_STARTUP_TIMEOUT")


def svc_python(command):
    args=["/usr/sbin/runuser","-u",USER,"--","/usr/bin/env","-i",
          "PATH=/usr/bin:/bin","LANG=C.UTF-8",
          "IDENT_RECEIVER_CONFIG="+str(CONFIG),"IDENT_RECEIVER_STATE_DIR="+str(STATE),
          str(APP/"venv/bin/python"),"-E","-s","-B",str(APP/"app/gateway.py"),command]
    return run(args,"RECEIVER_CLI_FAILED").stdout


def stage_checks(bundle, config, wheelhouse):
    health_existing()
    require(sys.version_info>=(3,10),"PYTHON_VERSION")
    require(Path("/usr/bin/python3").is_file(),"SYSTEM_PYTHON_REQUIRED")
    run([PYTHON,"-c","import venv,sqlite3,zoneinfo; assert sqlite3.sqlite_version_info >= (3,31,0)"],"PYTHON_PREREQUISITES")
    grp.getgrnam(PROXY)
    # Fail before useradd or any mkdir if a deployment parent is unsafe.
    for label,parent in (("OPT",APP.parent),("ETC",ETC.parent),("VAR_LIB",DATA.parent),
                         ("RUN",RUN.parent),("SYSTEMD",UNIT_DIR),("NGINX",ROUTES_SNIPPET.parent),
                         ("NGINX_HTTP",HTTP_SNIPPET.parent)):
        try:
            root_parent(parent)
        except (Stop,OSError) as exc:
            raise Stop("UNSAFE_INSTALL_PARENT_"+label) from exc
    for p in (APP,ETC,DATA,RUN,UNIT_DIR/(NAME+".service"),UNIT_DIR/(NAME+".socket"),
              HTTP_SNIPPET,ROUTES_SNIPPET):
        require(absent(p),"INSTALL_TARGET_ALREADY_EXISTS")
    for unit in (NAME+".service",NAME+".socket"):
        require(systemctl("cat",unit,allow=True).returncode!=0,"SYSTEMD_UNIT_ALREADY_EXISTS")
    for lookup in (pwd.getpwnam,grp.getgrnam):
        try:
            lookup(USER)
        except KeyError:
            pass
        else:
            raise Stop("SERVICE_IDENTITY_ALREADY_EXISTS")
    cfg=config_bytes(config)
    root_parent(wheelhouse)
    require({p.name for p in wheelhouse.iterdir()}==set(WHEELS),"WHEELHOUSE_SCOPE")
    wheels={}
    for name,wanted in WHEELS.items():
        raw=root_read(wheelhouse/name)
        require(digest(raw)==wanted,"DEPENDENCY_HASH_MISMATCH")
        wheels[name]=raw
    return cfg,wheels


def stage(bundle, config, wheelhouse, execute):
    cfg,wheels=stage_checks(bundle,config,wheelhouse)
    if not execute:
        print("STAGE_PLAN=PASS; NO_CHANGES=YES; DATABASE=NOT_CREATED")
        return
    units=[]
    try:
        run(["/usr/sbin/useradd","--system","--user-group","--no-create-home",
             "--home-dir","/nonexistent","--shell","/usr/sbin/nologin",USER],"CREATE_SERVICE_USER")
        user=pwd.getpwnam(USER)
        require(user.pw_uid!=0 and user.pw_gid==grp.getgrnam(USER).gr_gid
                and user.pw_gid!=grp.getgrnam(PROXY).gr_gid,"SERVICE_IDENTITY")
        mkdir(APP); mkdir(APP/"app"); mkdir(APP/"app/core"); mkdir(APP/"app/core/sql")
        mkdir(APP/"wheels")
        for name in APP_SOURCE:
            write_new(APP/"app"/name,bundle[name])
        for name in ("launch.py","gunicorn_conf.py","requirements.lock","nginx-http.conf","nginx-locations.conf"):
            write_new(APP/"app"/name,bundle["deploy/"+name])
        for name,raw in wheels.items():
            write_new(APP/"wheels"/name,raw)
        # Only the new, dedicated venv; no system package install or network.
        os.umask(0o022)
        run([PYTHON,"-m","venv",str(APP/"venv")],"VENV_CREATE",timeout=120)
        py=str(APP/"venv/bin/python")
        run([py,"-m","pip","--isolated","--disable-pip-version-check","install","--no-cache-dir",
             "--no-index","--only-binary=:all:","--require-hashes","--no-deps",
             "--find-links",str(APP/"wheels"),"-r",str(APP/"app/requirements.lock")],
            "OFFLINE_LOCKED_INSTALL",timeout=120)
        run([py,"-m","pip","check"],"DEPENDENCY_CHECK")
        run([py,"-c","import importlib.metadata as m; assert m.version('gunicorn')=='23.0.0'; assert m.version('packaging')=='26.4'"],"DEPENDENCY_VERSION")
        os.umask(0o077)
        mkdir(ETC,0o750,0,user.pw_gid)
        write_new(CONFIG,cfg,0o640,0,user.pw_gid)
        mkdir(DATA,0o755)
        mkdir(STATE,0o700,user.pw_uid,user.pw_gid)
        mkdir(BACKUPS,0o700)
        require(svc_python("init").strip()==b"STORE_CREATED","SHADOW_INIT")
        for suffix in (".socket",".service"):
            path=UNIT_DIR/(NAME+suffix)
            write_new(path,bundle["deploy/"+path.name])
            units.append(path.name)
        run(["/usr/bin/systemd-analyze","verify",str(UNIT_DIR/(NAME+".socket")),
             str(UNIT_DIR/(NAME+".service"))],"SYSTEMD_VERIFY")
        systemctl("daemon-reload")
        systemctl("start",NAME+".socket")
        systemctl("start",NAME+".service")
        wait_local()
        require(json_object(svc_python("status"))["accepted_batches"]==0,"NONEMPTY_NEW_STORE")
        systemctl("enable",NAME+".socket",NAME+".service")
        require(enabled(NAME+".socket") and enabled(NAME+".service"),"AUTOSTART_NOT_ENABLED")
        health_existing()
        print("STAGE=PASS; PRIVATE_SOCKET=PASS; AUTOSTART=PASS; SHADOW_STORE=EMPTY; HTTPS_NOT_PUBLISHED=YES")
    except BaseException:
        # Never delete the new store or overwrite anything. Stop only units
        # created in this invocation; preserve partial files for inspection.
        if units:
            systemctl("stop",*reversed(units),allow=True)
            systemctl("disable",*units,allow=True)
        print("STAGE_INCOMPLETE; NEW_FILES_PRESERVED; DO_NOT_PUBLISH",file=sys.stderr)
        raise


def nginx_dump():
    result=run(["/usr/sbin/nginx","-T"],"NGINX_CONFIG_INVALID")
    require(b"conflicting server name" not in result.stderr.lower(),"NGINX_SERVER_NAME_CONFLICT")
    text=result.stdout.decode("utf-8")
    # Do not log full nginx -T: unrelated config may contain credentials.
    paths=[Path(x) for x in re.findall(r"^# configuration file (/.+):$",text,re.M)]
    require(paths and re.search(r"^\s*user\s+www-data(?:\s+www-data)?\s*;",text,re.M),
            "NGINX_PROXY_IDENTITY_NOT_CONFIRMED")
    return paths


def loaded_once(paths,path):
    return sum(1 for p in paths if p.resolve()==path)==1


class LocalHTTPS(http.client.HTTPSConnection):
    def connect(self):
        raw=socket.create_connection(("127.0.0.1",443),self.timeout)
        try:
            self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def https_get(host,path):
    conn=LocalHTTPS(host,timeout=8,context=ssl.create_default_context())
    try:
        conn.request("GET",path,headers={"Connection":"close"})
        r=conn.getresponse()
        return r.status,r.read(2048)
    finally:
        conn.close()


def publication_preflight(bundle,vhost,host,expected):
    require(valid_hostname(host) and bool(HEX.fullmatch(expected)),"HOST_OR_VHOST_HASH")
    require(vhost.is_absolute() and vhost.parent in (Path("/etc/nginx/sites-available"),Path("/etc/nginx/conf.d")),
            "EXPLICIT_VHOST_PATH_REQUIRED")
    require(vhost.resolve(strict=True)==vhost and vhost.suffix in ("",".conf"),"VHOST_PATH")
    for p in (HTTP_SNIPPET,ROUTES_SNIPPET,ACTIVE_RECORD,PENDING_RECORD):
        require(absent(p),"PUBLICATION_TARGET_EXISTS")
    root_parent(BACKUPS)
    health_existing()
    wait_local()
    for name in APP_SOURCE:
        require(digest(root_read(APP/"app"/name))==APP_SOURCE[name],"INSTALLED_SOURCE_CHANGED")
    for name in ("launch.py","gunicorn_conf.py","requirements.lock","nginx-http.conf","nginx-locations.conf"):
        require(root_read(APP/"app"/name)==bundle["deploy/"+name],"INSTALLED_DEPLOYMENT_CHANGED")
    for suffix in (".socket",".service"):
        require(root_read(UNIT_DIR/(NAME+suffix))==bundle["deploy/"+NAME+suffix],"UNIT_CHANGED")
    require(enabled(NAME+".socket") and enabled(NAME+".service"),"AUTOSTART_NOT_ENABLED")
    require(active("nginx.service"),"NGINX_NOT_ACTIVE")
    require(loaded_once(nginx_dump(),vhost),"VHOST_NOT_LOADED_ONCE")
    original=root_read(vhost,1024*1024)
    require(digest(original)==expected,"VHOST_HASH_MISMATCH")
    candidate=plan(original,host)
    status,_=https_get(host,"/")
    require(status in (200,301,302,303,307,308,401,403),"EXISTING_HTTPS_FAILED")
    # Existing public path may return site's 404/302; a pre-existing receiver
    # response is a conflict, never a takeover.
    status_before,body=https_get(host,PATHS[1])
    require(not (status_before==401 and b'UNAUTHORIZED' in body),"EXISTING_RECEIVER_ROUTE")
    st=vhost.stat()
    return original,candidate,stat.S_IMODE(st.st_mode),st.st_gid,status


def record_bytes(record):
    return (json.dumps(record,sort_keys=True,separators=(",",":"))+"\n").encode()


def rollback(record,backup,strict=False):
    vhost=Path(record["vhost"])
    current=digest(root_read(vhost,1024*1024))
    require(current in (record["before_sha"],record["after_sha"]),"ROLLBACK_VHOST_CHANGED_EXTERNALLY")
    if strict:
        require(current==record["after_sha"],"ROLLBACK_NOT_ACTIVE")
    for p,key in ((HTTP_SNIPPET,"http_sha"),(ROUTES_SNIPPET,"routes_sha")):
        if strict:
            require(not absent(p),"ROLLBACK_SNIPPET_MISSING")
        if not absent(p):
            require(digest(root_read(p))==record[key],"ROLLBACK_SNIPPET_CHANGED_EXTERNALLY")
    original=root_read(backup/"vhost.before",1024*1024,private=True)
    require(digest(original)==record["before_sha"],"BACKUP_HASH_MISMATCH")
    if current==record["after_sha"]:
        replace_checked(vhost,record["after_sha"],original,record["mode"],record["gid"])
    delete_checked(ROUTES_SNIPPET,record["routes_sha"])
    delete_checked(HTTP_SNIPPET,record["http_sha"])
    run(["/usr/sbin/nginx","-t"],"ROLLBACK_NGINX_TEST_FAILED")
    systemctl("reload","nginx.service")
    health_existing()
    status,_=https_get(record["host"],"/")
    require(status==record["baseline_status"],"ROLLBACK_HTTPS_HEALTH_FAILED")


def publish(bundle,vhost,host,expected,execute):
    original,candidate,mode,gid,baseline=publication_preflight(bundle,vhost,host,expected)
    print("VHOST_BEFORE_SHA256="+digest(original))
    print("VHOST_CANDIDATE_SHA256="+digest(candidate))
    if not execute:
        print("HTTPS_PLAN=PASS; NO_CHANGES=YES")
        return
    backup=BACKUPS/("publish-"+uuid.uuid4().hex)
    mkdir(backup,0o700)
    http=bundle["deploy/nginx-http.conf"]
    routes=render_locations(bundle["deploy/nginx-locations.conf"],host)
    record={"vhost":str(vhost),"host":host,"before_sha":digest(original),
            "after_sha":digest(candidate),"mode":mode,"gid":gid,
            "http_sha":digest(http),"routes_sha":digest(routes),"baseline_status":baseline,
            "backup":str(backup)}
    write_new(backup/"vhost.before",original,0o600)
    write_new(backup/"vhost.after",candidate,0o600)
    write_new(backup/"record.json",record_bytes(record),0o600)
    write_new(PENDING_RECORD,record_bytes(record),0o600)
    print("BACKUP=PASS; PATH="+str(backup))
    try:
        write_new(HTTP_SNIPPET,http)
        write_new(ROUTES_SNIPPET,routes)
        replace_checked(vhost,record["before_sha"],candidate,mode,gid)
        paths=nginx_dump()
        require(all(loaded_once(paths,p) for p in (HTTP_SNIPPET,ROUTES_SNIPPET,vhost)),
                "PUBLISHED_CONFIG_NOT_LOADED_ONCE")
        run(["/usr/sbin/nginx","-t"],"NGINX_TEST_FAILED")
        systemctl("reload","nginx.service")
        for _ in range(20):
            try:
                status,body=https_get(host,PATHS[1])
                if status==401 and json.loads(body)=={"error":"UNAUTHORIZED"}:
                    break
            except (OSError,ValueError,http.client.HTTPException):
                pass
            time.sleep(1)
        else:
            raise Stop("HTTPS_MACHINE_AUTH_PROBE_FAILED")
        require(https_get(host,"/")[0]==baseline,"EXISTING_HTTPS_CHANGED")
        health_existing()
        write_new(ACTIVE_RECORD,record_bytes(record),0o600)
        delete_checked(PENDING_RECORD,digest(record_bytes(record)))
        print("HTTPS_PUBLISH=PASS; VERIFIED_TLS=PASS; UNAUTHORIZED=401; EXISTING_HEALTH=PASS")
    except BaseException:
        try:
            rollback(record,backup)
            delete_checked(ACTIVE_RECORD,digest(record_bytes(record)))
            delete_checked(PENDING_RECORD,digest(record_bytes(record)))
            print("HTTPS_ROLLBACK=PASS; BACKUP_PRESERVED")
        except Exception:
            print("HTTPS_ROLLBACK=STOP; MANUAL_RECOVERY_REQUIRED; BACKUP="+str(backup),file=sys.stderr)
        raise


def unpublish(recover=False):
    marker=PENDING_RECORD if recover else ACTIVE_RECORD
    raw=root_read(marker,16384,private=True)
    record=json_object(raw)
    backup=Path(record["backup"])
    require(backup.parent==BACKUPS and bool(re.fullmatch(r"publish-[0-9a-f]{32}",backup.name)),"BACKUP_PATH")
    require(root_read(backup/"record.json",16384,private=True)==raw,"PUBLICATION_RECORD_MISMATCH")
    other=ACTIVE_RECORD if recover else PENDING_RECORD
    if not absent(other):
        require(root_read(other,16384,private=True)==raw,"PUBLICATION_RECORD_MISMATCH")
    # Persist the rollback intent before touching live files, so an interrupted
    # unpublish can be recovered with the same verified record.
    if not recover and absent(PENDING_RECORD):
        write_new(PENDING_RECORD,raw,0o600)
    rollback(record,backup,strict=not recover)
    delete_checked(ACTIVE_RECORD,digest(raw))
    delete_checked(PENDING_RECORD,digest(raw))
    print("HTTPS_UNPUBLISH=PASS; DATA_AND_BACKUP_PRESERVED")


@contextmanager
def mutation_lock():
    path=Path("/run/lock")/(NAME+".lock")
    # /run/lock can be root-owned sticky 1777 on Debian/Ubuntu.
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        st=os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and st.st_nlink==1
                and stat.S_IMODE(st.st_mode)==0o600,"DEPLOYMENT_LOCK")
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-host",required=True)
    parser.add_argument("--machine-id-sha256",required=True)
    parser.add_argument("--manifest-sha256",required=True)
    parser.add_argument("--confirm",default="")
    subs=parser.add_subparsers(dest="action",required=True)
    for action in ("plan-stage","stage"):
        p=subs.add_parser(action)
        p.add_argument("--config",required=True,type=Path)
        p.add_argument("--wheelhouse",required=True,type=Path)
    for action in ("plan-https","publish"):
        p=subs.add_parser(action)
        p.add_argument("--vhost",required=True,type=Path)
        p.add_argument("--https-host",required=True)
        p.add_argument("--vhost-sha256",required=True)
    subs.add_parser("unpublish")
    subs.add_parser("recover")
    args=parser.parse_args()
    try:
        require(os.geteuid()==0 and sys.platform=="linux","ROOT_LINUX_REQUIRED")
        require(socket.gethostname().split(".")[0]==args.expected_host,"WRONG_HOST")
        require(bool(HEX.fullmatch(args.machine_id_sha256)) and
                digest(Path("/etc/machine-id").read_bytes())==args.machine_id_sha256,"WRONG_MACHINE")
        bundle=verify_bundle(args.manifest_sha256)
        is_plan=args.action.startswith("plan-")
        if is_plan:
            require(not args.confirm,"PLAN_NO_CONFIRMATION")
            if args.action=="plan-stage":
                stage(bundle,args.config,args.wheelhouse,False)
            else:
                publish(bundle,args.vhost,args.https_host,args.vhost_sha256,False)
        else:
            require(args.confirm=="IDENT-006B-"+args.action.upper(),"EXPLICIT_ACTION_CONFIRMATION_REQUIRED")
            with mutation_lock():
                if args.action=="stage":
                    stage(bundle,args.config,args.wheelhouse,True)
                elif args.action=="publish":
                    publish(bundle,args.vhost,args.https_host,args.vhost_sha256,True)
                else:
                    unpublish(recover=args.action=="recover")
        return 0
    except (Stop,OSError,ValueError,TypeError,KeyError,RuntimeError,subprocess.SubprocessError,http.client.HTTPException) as e:
        safe=str(e) if isinstance(e,Stop) and re.fullmatch(r"[A-Z_]+",str(e)) else type(e).__name__
        print("STOP="+safe,file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
