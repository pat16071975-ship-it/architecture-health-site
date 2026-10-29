#!/usr/bin/env python3
"""Owner-requested restoration of TWO view options. No database/service writes."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

BASE_COMMIT = 'ffd0a876562c3b9c1e74410d9cf644aafb3fff39'
TOKEN = 'RESTORE-TWO-VIEWS-20261029'
ROOT = Path('/var/www/az-baze.ru/reports')
BACKUPS = Path('/var/lib/az-baze/backups')
LOCK = Path('/run/lock/az-baze-paid-patient-deploy.lock')
OLD_SELECT = '<select id="dateViewMode"><option value="single">Одна дата</option><option value="compare" selected>На эту дату по месяцам</option><option value="monthly">Итоги месяцев</option></select>'
NEW_SELECT = '<select id="dateViewMode"><option value="compare" selected>Сравнение</option><option value="single">На дату</option></select>'
FILES = {
    'period-view.js': ('5aeaf54d35dc98ac9b0ce7d0574813cf8034778a', 'f1b65ad1a72925959d4c0215c871cf271755034f', OLD_SELECT, NEW_SELECT),
    'index.html': ('db160bf97814d18976a97afc5e680668819c6cd7', '6b4c114162dd4da125ab7257fe46be7854a68651', './period-view.js?v=20261029-paid-history-1', './period-view.js?v=20261029-two-views-1'),
}

class Stop(RuntimeError):
    pass

def require(ok, message):
    if not ok:
        raise Stop(message)

def blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()

def safe(path):
    require(path.is_absolute() and path.resolve() == path and not path.is_symlink(), 'UNSAFE_PATH:' + str(path))
    require(path.is_file(), 'MISSING_FILE:' + str(path))

def transform(name, raw):
    old, new, before, after = FILES[name]
    require(blob(raw) == old, 'SOURCE_VERSION_MISMATCH:' + name)
    text = raw.decode('utf-8')
    require(text.count(before) == 1, 'SOURCE_ANCHOR_MISMATCH:' + name)
    output = text.replace(before, after, 1).encode('utf-8')
    require(blob(output) == new, 'TARGET_VERSION_MISMATCH:' + name)
    return output

def health():
    r = subprocess.run(['systemctl','is-active','--quiet','az-baze-auth'], capture_output=True, timeout=5)
    require(r.returncode == 0, 'SERVICE_NOT_ACTIVE')
    r = subprocess.run(['curl','-fsS','--connect-timeout','2','--max-time','5','http://127.0.0.1:8001/health'], capture_output=True, timeout=8)
    require(r.returncode == 0 and r.stdout.strip() == b'ok', 'HEALTH_FAILED')

def metadata(path):
    s = path.stat()
    return [s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)]

def state(root):
    raw, meta, states = {}, {}, []
    for name,(old,new,_,_) in FILES.items():
        path = root/name
        safe(path)
        raw[name] = path.read_bytes()
        meta[name] = metadata(path)
        value = blob(raw[name])
        require(value in (old,new), 'UNKNOWN_LIVE_FILE:' + name)
        states.append('BASE' if value == old else 'TARGET')
    require(len(set(states)) == 1, 'MIXED_LIVE_STATE')
    return states[0], raw, meta

def atomic_write(path, raw, meta, expected):
    safe(path)
    require(blob(path.read_bytes()) == expected and metadata(path) == meta, 'CONCURRENT_CHANGE:' + path.name)
    fd, tmp = tempfile.mkstemp(prefix='.az-two-views-', dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(raw)
            f.flush()
            os.fchown(f.fileno(), meta[0], meta[1])
            os.fchmod(f.fileno(), meta[2])
            os.fsync(f.fileno())
        require(blob(path.read_bytes()) == expected and metadata(path) == meta, 'CONCURRENT_CHANGE:' + path.name)
        os.replace(tmp,path)
        fd = os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def apply_files(root, backups, check_health):
    current, original, meta = state(root)
    check_health()
    if current == 'TARGET':
        print('TWO_VIEWS_ALREADY_INSTALLED=PASS; LIVE_CHANGES=NONE', flush=True)
        return
    target = {n:transform(n,b) for n,b in original.items()}
    require(backups.is_dir() and backups.resolve() == backups, 'BACKUP_ROOT_UNSAFE')
    require(shutil.disk_usage(backups).free > sum(len(b) for b in original.values())*3 + 1048576, 'BACKUP_SPACE_LOW')
    backup = Path(tempfile.mkdtemp(prefix='ui-two-views-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-', dir=backups))
    backup.chmod(0o700)
    for name,raw in original.items():
        p = backup/name
        with p.open('xb') as f:
            os.fchmod(f.fileno(),0o600)
            f.write(raw); f.flush(); os.fsync(f.fileno())
        require(p.read_bytes() == raw,'BACKUP_VERIFY_FAILED:' + name)
    manifest = {'baseCommit':BASE_COMMIT,'metadata':meta,'files':{n:{'before':FILES[n][0],'after':FILES[n][1]} for n in FILES}}
    with (backup/'manifest.json').open('x') as f:
        os.fchmod(f.fileno(),0o600)
        json.dump(manifest,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    fd=os.open(backup,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
    print('BACKUP='+str(backup),flush=True)
    print('FILE_BACKUP=PASS; DATABASE_ACCESS=NONE',flush=True)
    attempted = []
    try:
        for name,raw in target.items():
            attempted.append(name)
            atomic_write(root/name,raw,meta[name],FILES[name][0])
        result,_,after_meta = state(root)
        require(result == 'TARGET' and after_meta == meta,'FINAL_VERIFY_FAILED')
        check_health()
    except BaseException:
        # Never overwrite an unrelated concurrent file revision. No DB restore.
        for name in reversed(attempted):
            p=root/name;safe(p)
            actual=blob(p.read_bytes())
            require(actual in FILES[name][:2] and metadata(p)==meta[name],'ROLLBACK_DRIFT:' + name)
            if actual == FILES[name][1]:
                raw=(backup/name).read_bytes()
                require(blob(raw)==FILES[name][0],'BACKUP_CORRUPTED:' + name)
                atomic_write(p,raw,meta[name],actual)
        require(state(root)[0]=='BASE','ROLLBACK_VERIFY_FAILED')
        print('TWO_VIEWS_FILE_ROLLBACK=PASS; DATABASE_RESTORE=NOT_PERFORMED',flush=True)
        raise
    print('TWO_VIEWS_PRODUCTION_RESTORE=PASS; VIEW_OPTIONS=Сравнение|На дату',flush=True)
    print('CHANGED_FILES=2; CALCULATIONS=UNCHANGED; DATABASE_ACCESS=NONE; SERVICE_RESTART=NONE; FILE_PERMISSIONS=PRESERVED; HEALTH=ok',flush=True)

def main(argv):
    p=argparse.ArgumentParser(description=__doc__)
    modes=p.add_mutually_exclusive_group(required=True)
    modes.add_argument('--prepare-source',type=Path)
    modes.add_argument('--preflight',action='store_true')
    modes.add_argument('--apply')
    args=p.parse_args(argv)
    if args.prepare_source is not None:
        root=args.prepare_source.resolve()/'reports'
        current,raw,_=state(root)
        require(current=='BASE','PREPARE_EXPECTS_BASE')
        targets={n:transform(n,b) for n,b in raw.items()}
        for n,b in targets.items(): (root/n).write_bytes(b)
        print('TWO_VIEWS_SOURCE=PASS; RUNTIME_FILES=2')
        return
    require(os.geteuid()==0 and socket.gethostname().split('.')[0]=='az-server','ROOT_AT_AZ_SERVER_REQUIRED')
    if args.apply is not None:
        require(args.apply==TOKEN,'WRONG_APPLY_TOKEN')
    current,raw,_=state(ROOT)
    if current=='BASE':
        for n,b in raw.items(): transform(n,b)
    health()
    print('TWO_VIEWS_PREFLIGHT=PASS; LIVE_STATE='+current,flush=True)
    if args.preflight:
        print('LIVE_CHANGES=NONE; APPLY_NOT_STARTED',flush=True)
        return
    fd=os.open(LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_uid==0,'UNSAFE_LOCK')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        apply_files(ROOT,BACKUPS,health)
    finally:
        os.close(fd)

if __name__=='__main__':
    def interrupted(signum,frame): raise Stop('INTERRUPTED')
    signal.signal(signal.SIGTERM,interrupted)
    try: main(sys.argv[1:])
    except (Exception,KeyboardInterrupt) as e:
        print('TWO_VIEWS_RESTORE=STOP; ERROR='+type(e).__name__+':'+str(e),flush=True)
        sys.exit(1)
