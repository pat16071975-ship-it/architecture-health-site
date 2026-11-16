#!/usr/bin/env python3
"""Install reviewed PR203 scheduler against PR219; smoke before enabling timer."""
import datetime, fcntl, hashlib, json, os, pathlib, sqlite3, subprocess, sys, tempfile, urllib.request
HEAD = '8faec16720e7ca99c76ea2eb0a4c4a5a5373ebee'
ROOT = pathlib.Path('/opt/az-baze-auth')
UNITS = pathlib.Path('/etc/systemd/system')
DB = pathlib.Path('/var/lib/az-baze/auth.db')
SERVICE = 'az-baze-advertising-hourly.service'
TIMER = 'az-baze-advertising-hourly.timer'
ROWS = [
 ('az-baze-auth/advertising_hourly.py', ROOT/'advertising_hourly.py', '9649ce5014d6962403ca1629bb313ebc8ee9a4e2'),
 ('az-baze-auth/deploy/'+SERVICE, UNITS/SERVICE, '3b99c04bae3140686a88e27cf009ac2256e2f414'),
 ('az-baze-auth/deploy/'+TIMER, UNITS/TIMER, 'f7dea276493696f67b70bb6209d93779081dd5c7'),
]
GUARDS = {'advertising_report.py':'2f94995681724a9800c76dce2be9a5d426decf60', 'metrika_auto.py':'617c3cf7aa526541ff283ede8bfe0c6d0070a9cf','direct_auto.py':'f73fc4f75e5e3714d64d7412403f0433d5a34013'}
def run(*args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode: raise RuntimeError('command failed: '+args[0]+' '+args[1])
    return r.stdout.strip()
def blob(raw): return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def prop(unit, key): return run('systemctl','show',unit,'-p',key,'--value')
def preflight():
    if os.geteuid() != 0: raise RuntimeError('root required')
    for unit in (SERVICE,TIMER):
        if prop(unit,'LoadState') != 'not-found': raise RuntimeError('unit already exists: '+unit)
    for _,p,_ in ROWS:
        if p.exists() or p.is_symlink(): raise RuntimeError('target already exists: '+str(p))
    for name, sha in GUARDS.items():
        p=ROOT/name
        if p.is_symlink() or blob(p.read_bytes()) != sha: raise RuntimeError('runtime mismatch: '+name)
    for key, expected in [('User','azadmin'),('Group','www-data'),('WorkingDirectory',str(ROOT)),('ActiveState','active')]:
        if prop('az-baze-auth.service',key) != expected: raise RuntimeError('main service mismatch: '+key)
    if prop('az-baze-auth.service','EnvironmentFiles') != '/etc/az-baze/auth.env (ignore_errors=no)':
        raise RuntimeError('environment configuration mismatch')
    if not DB.is_file() or DB.is_symlink(): raise RuntimeError('database path invalid')
    if not pathlib.Path('/etc/az-baze/auth.env').is_file(): raise RuntimeError('environment file missing')
def install(raw,p):
    fd,tmp=tempfile.mkstemp(prefix='.'+p.name+'.',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp,0o644); os.chown(tmp,0,0); os.replace(tmp,p)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def main():
    if sys.argv[1:] != ['--apply']: raise RuntimeError('use --apply')
    with open('/run/az-advertising-hourly-install.lock','a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        preflight()
        data={}
        for name,p,sha in ROWS:
            with urllib.request.urlopen('https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/'+HEAD+'/'+name,timeout=45) as r: raw=r.read()
            if blob(raw)!=sha: raise RuntimeError('download hash mismatch: '+name)
            if name.endswith('.py'): compile(raw,name,'exec')
            data[name]=raw
        backup=pathlib.Path(tempfile.mkdtemp(prefix='hourly-activate-',dir='/var/lib/az-baze/backups'))
        os.chmod(backup,0o700)
        with sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True) as source:
            with sqlite3.connect(str(backup/'auth.db')) as dest:
                source.backup(dest)
                if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('backup integrity')
        (backup/'manifest.json').write_text(json.dumps({'head':HEAD,'new_files':[str(p) for _,p,_ in ROWS]}))
        print('PREFLIGHT=PASS; BACKUP_VERIFY=PASS; BACKUP='+str(backup),flush=True)
        preflight()
        installed=[]
        try:
            for name,p,sha in ROWS:
                install(data[name],p); installed.append(p)
                if blob(p.read_bytes())!=sha: raise RuntimeError('installed hash mismatch')
            run('systemd-analyze','verify',str(UNITS/SERVICE),str(UNITS/TIMER))
            run('systemctl','daemon-reload')
            started=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            print('HOURLY_SMOKE=RUNNING; TIMER=NOT_ENABLED',flush=True)
            run('systemctl','start',SERVICE)
            if prop(SERVICE,'Result')!='success' or prop(SERVICE,'ExecMainStatus')!='0': raise RuntimeError('hourly smoke failed')
            run('journalctl','--sync')
            logs=run('journalctl','-u',SERVICE,'--since',started,'-o','cat','--no-pager')
            success=[]
            for line in logs.splitlines():
                try: value=json.loads(line)
                except ValueError: continue
                if isinstance(value,dict) and value.get('status')=='ok' and 'direct_rows' in value: success.append(value)
            if not success: raise RuntimeError('no successful combined sync evidence; timer remains disabled')
            result=success[-1]
            print('HOURLY_SMOKE=PASS; '+json.dumps({k:result.get(k) for k in ('direct_rows','direct_period','direct_reused','completed_updated','current_updated')},ensure_ascii=False),flush=True)
            run('systemctl','enable','--now',TIMER)
            if prop(TIMER,'ActiveState')!='active' or prop(TIMER,'UnitFileState')!='enabled': raise RuntimeError('timer activation failed')
            next_run=prop(TIMER,'NextElapseUSecRealtime')
            if not next_run: raise RuntimeError('next trigger missing')
            print('HOURLY_TIMER=ENABLED; CADENCE=HOURLY; NEXT='+next_run)
            print('APPLICATION_FILES=UNCHANGED; DB_SCHEMA=UNCHANGED; SYNC=SHARED_METRIKA_DIRECT_CORE')
        except BaseException:
            subprocess.run(['systemctl','disable','--now',TIMER],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run(['systemctl','stop',SERVICE],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for p in reversed(installed): p.unlink(missing_ok=True)
            run('systemctl','daemon-reload')
            print('SCHEDULER_FILES_ROLLED_BACK; TIMER=NOT_ENABLED; DATABASE_RESTORE=NOT_RUN',flush=True)
            raise
if __name__=='__main__':
    try: main()
    except Exception as exc:
        print('STOP='+ (str(exc) if type(exc) is RuntimeError else type(exc).__name__),flush=True)
        sys.exit(1)
