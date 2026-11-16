#!/usr/bin/env python3
"""PR221 guarded file-only deployment. No imports, backfill, timer or SQL writes."""
import fcntl
import hashlib,json,os,pathlib,shutil,sqlite3,subprocess,sys,tempfile,time,urllib.request
HEAD='1fbf0fb1c1101e33d9c18ba27c38d8d289d058a2'
ROWS=[['az-baze-auth/advertising_report.py', '05a2986eed0853fcbe8a2f31f6bf72df320947b4', '04a355efe10d4068405e3d34e6ec41329b913eee'], ['az-baze-auth/advertising_retry.py', '1cb48c3de8304038ea243775d0e4020e495cd122', '677f24b5507cb72683073f6ba8937223c0831389'], ['az-baze-auth/templates/uploads.html', '812643e12985763fed00c364216af76e1fed1c8e', '72514769a271cc7ea2f36f5b225b6339b52d523d']]
SERVICE='az-baze-auth'
DB=pathlib.Path('/var/lib/az-baze/auth.db')
def run(*args): subprocess.run(args,check=True)
def blob(data): return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
def live(name):
    return pathlib.Path('/opt/az-baze-auth')/name.split('/',1)[1] if name.startswith('az-baze-auth/') else pathlib.Path('/var/www/az-baze.ru')/name
def health():
    with urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=3) as r:
        if r.status != 200: raise RuntimeError('health status')
def wait_health():
    for _ in range(30):
        try: health(); return
        except Exception: time.sleep(1)
    raise RuntimeError('service health failed')
def check_base():
    for name,old,new in ROWS:
        p=live(name)
        if p.is_symlink(): raise RuntimeError('symlink: '+str(p))
        if old is None:
            if p.exists(): raise RuntimeError('new target already exists: '+str(p))
        elif not p.is_file() or blob(p.read_bytes()) != old:
            raise RuntimeError('LIVE_BASE_MISMATCH: '+str(p))
def install(data,p,st):
    fd,tmp=tempfile.mkstemp(prefix='.'+p.name+'.',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
        os.chown(tmp,st.st_uid,st.st_gid); os.chmod(tmp,st.st_mode & 0o777)
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def deploy():
    if sys.argv[1:] != ['--apply']: raise RuntimeError('use --apply')
    if os.geteuid()!=0: raise RuntimeError('run in root server terminal')
    if not DB.is_file() or DB.is_symlink(): raise RuntimeError('database path invalid')
    run('systemctl','is-active','--quiet',SERVICE); health(); check_base()
    data={}
    for name,old,new in ROWS:
        url='https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/'+HEAD+'/'+name
        with urllib.request.urlopen(url,timeout=45) as r: raw=r.read()
        if blob(raw)!=new: raise RuntimeError('download hash mismatch: '+name)
        if name.endswith('.py'): compile(raw,name,'exec')
        data[name]=raw
    print('PREFLIGHT=PASS; TARGET_HASHES=PASS',flush=True)
    backup=pathlib.Path(tempfile.mkdtemp(prefix='pr221-',dir='/var/lib/az-baze/backups'))
    print('BACKUP='+str(backup),flush=True)
    stopped=False; mutated=False
    try:
        stopped=True; run('systemctl','stop',SERVICE); check_base()
        with sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True) as source:
            with sqlite3.connect(str(backup/'auth.db')) as dest:
                source.backup(dest)
                if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('backup integrity')
        attrs={}
        for i,(name,old,new) in enumerate(ROWS):
            p=live(name)
            attrs[name]=p.stat() if old else live('az-baze-auth/advertising_report.py').stat()
            if old:
                shutil.copy2(p,backup/str(i))
                if blob((backup/str(i)).read_bytes())!=old: raise RuntimeError('backup hash mismatch')
        (backup/'manifest.json').write_text(json.dumps({'head':HEAD,'files':ROWS},indent=2))
        print('BACKUP_VERIFY=PASS',flush=True)
        mutated=True
        for name,old,new in ROWS: install(data[name],live(name),attrs[name])
        for name,old,new in ROWS:
            if blob(live(name).read_bytes())!=new: raise RuntimeError('installed hash mismatch')
        run('systemctl','start',SERVICE); wait_health()
        run('systemctl','is-active','--quiet',SERVICE)
        print('PR221_PRODUCTION_DEPLOY=PASS; FILES='+str(len(ROWS)))
        print('HEAD='+HEAD)
        print('HEALTH=PASS; DB_WRITES_BY_INSTALLER=NONE; BACKFILL=NOT_RUN; IMPORTS=NOT_RUN; TIMER_CONFIGURATION=UNCHANGED; RETRY_STATE=UNCHANGED')
    except BaseException:
        if mutated:
            run('systemctl','stop',SERVICE)
            for i,(name,old,new) in enumerate(ROWS):
                if old: install((backup/str(i)).read_bytes(),live(name),attrs[name])
                elif live(name).exists(): live(name).unlink()
            print('ROLLBACK_FILES=PASS; DATABASE_RESTORE=NOT_RUN',flush=True)
        if stopped: run('systemctl','start',SERVICE); wait_health()
        raise
TIMERS=('az-baze-advertising-hourly.timer','az-baze-advertising-retry.timer')
STATE=pathlib.Path('/var/lib/az-baze/advertising-retry.json')
GATE=pathlib.Path(str(STATE)+'.lock')
def main():
    if sys.argv[1:] != ['--apply'] or os.geteuid()!=0:raise RuntimeError('root and --apply required')
    if any(p.is_symlink() or not p.is_file() for p in (STATE,GATE)):raise RuntimeError('retry state missing or invalid')
    check_base();health()
    for timer in TIMERS:run('systemctl','is-active','--quiet',timer)
    with open('/run/az-advertising-hourly-install.lock','a') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        paused=[]
        try:
            for timer in TIMERS:
                paused.append(timer);run('systemctl','stop',timer)
            with GATE.open('r') as gate:
                deadline=time.monotonic()+900
                while True:
                    try:fcntl.flock(gate,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:
                        if time.monotonic()>=deadline:raise RuntimeError('sync still running; retry installation later')
                        time.sleep(1)
                state_before=STATE.read_bytes()
                deploy()
                if STATE.read_bytes()!=state_before:raise RuntimeError('retry state changed unexpectedly')
        finally:
            for timer in paused:run('systemctl','start',timer)
        for timer in TIMERS:run('systemctl','is-active','--quiet',timer)
        print('HOURLY_TIMER=ACTIVE; RETRY_TIMER=ACTIVE')
if __name__=='__main__':
    try: main()
    except Exception as e: print('STOP='+str(e),flush=True); sys.exit(1)
