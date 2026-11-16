#!/usr/bin/env python3
"""Deploy audited PR220 and enable retries; never restore a live database."""
import fcntl,hashlib,json,os,pathlib,pwd,grp,shutil,sqlite3,subprocess,sys,tempfile,time,urllib.request
HEAD='acf394c184420ea39387b396ac0cf86cd5c48a7f'
MAIN='az-baze-auth.service'
HOUR='az-baze-advertising-hourly'
RETRY='az-baze-advertising-retry'
DB=pathlib.Path('/var/lib/az-baze/auth.db')
STATE=pathlib.Path('/var/lib/az-baze/advertising-retry.json')
GATE=pathlib.Path(str(STATE)+'.lock')
ROWS=[['az-baze-auth/advertising_hourly.py', '9649ce5014d6962403ca1629bb313ebc8ee9a4e2', '7dc02a0d1c03597742a46b8d3dc1d99da0a5a306'], ['az-baze-auth/advertising_report.py', '2f94995681724a9800c76dce2be9a5d426decf60', '05a2986eed0853fcbe8a2f31f6bf72df320947b4'], ['az-baze-auth/advertising_retry.py', None, '1cb48c3de8304038ea243775d0e4020e495cd122'], ['az-baze-auth/deploy/az-baze-advertising-hourly.service', '3b99c04bae3140686a88e27cf009ac2256e2f414', '15be1f6b55f5c8048cc576bababf7c50eb2f30d5'], ['az-baze-auth/deploy/az-baze-advertising-retry.service', None, 'd7044fb167bd5891b6e770cd0d916d8752e211f0'], ['az-baze-auth/deploy/az-baze-advertising-retry.timer', None, '2d0ca6a4c2f17d17f55a1f47c6819d5d1b3c05a9']]
def live(name):
    if '/deploy/' in name: return pathlib.Path('/etc/systemd/system')/name.rsplit('/',1)[1]
    return pathlib.Path('/opt/az-baze-auth')/name.split('/',1)[1]
def blob(raw):return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def run(*args):
    r=subprocess.run(args,capture_output=True,text=True)
    if r.returncode:raise RuntimeError('command failed: '+args[0]+' '+args[1])
    return r.stdout.strip()
def prop(unit,key):return run('systemctl','show',unit,'-p',key,'--value')
def health():
    with urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=3) as r:
        if r.status!=200:raise RuntimeError('health failed')
def wait_health():
    for _ in range(30):
        try:health();return
        except Exception:time.sleep(1)
    raise RuntimeError('health timeout')
def check_files():
    for name,old,new in ROWS:
        p=live(name)
        if p.is_symlink():raise RuntimeError('symlink: '+str(p))
        if old is None:
            if p.exists():raise RuntimeError('new target exists: '+str(p))
        elif not p.is_file() or blob(p.read_bytes())!=old:raise RuntimeError('LIVE_BASE_MISMATCH: '+str(p))
    for p in (STATE,GATE):
        if p.exists() or p.is_symlink():raise RuntimeError('retry state already exists')
    timer=live('az-baze-auth/deploy/'+HOUR+'.timer')
    if timer.is_symlink() or blob(timer.read_bytes())!='f7dea276493696f67b70bb6209d93779081dd5c7':raise RuntimeError('hourly timer mismatch')
def install(raw,p,uid,gid,mode):
    fd,tmp=tempfile.mkstemp(prefix='.'+p.name+'.',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.chown(tmp,uid,gid);os.chmod(tmp,mode);os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def wait_idle():
    for _ in range(900):
        if prop(HOUR+'.service','ActiveState') not in ('active','activating','deactivating'):return
        time.sleep(1)
    raise RuntimeError('hourly job still running; retry installation later')
def main():
    if sys.argv[1:]!=['--apply'] or os.geteuid()!=0:raise RuntimeError('root and --apply required')
    with open('/run/az-advertising-hourly-install.lock','a') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        check_files();health()
        if not DB.is_file() or DB.is_symlink():raise RuntimeError('database path invalid')
        for key,value in [('User','azadmin'),('Group','www-data'),('WorkingDirectory','/opt/az-baze-auth'),('ActiveState','active')]:
            if prop(MAIN,key)!=value:raise RuntimeError('main service mismatch: '+key)
        for unit in (HOUR+'.service',HOUR+'.timer'):
            if prop(unit,'DropInPaths'):raise RuntimeError('unexpected hourly override')
        for unit in (RETRY+'.service',RETRY+'.timer'):
            if prop(unit,'LoadState')!='not-found':raise RuntimeError('retry unit already exists')
        if prop(HOUR+'.timer','ActiveState')!='active' or prop(HOUR+'.timer','UnitFileState')!='enabled':raise RuntimeError('hourly timer not active/enabled')
        uid=pwd.getpwnam('azadmin').pw_uid;gid=grp.getgrnam('www-data').gr_gid
        data={}
        for name,old,new in ROWS:
            with urllib.request.urlopen('https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/'+HEAD+'/'+name,timeout=45) as r:raw=r.read()
            if blob(raw)!=new:raise RuntimeError('download hash mismatch: '+name)
            if name.endswith('.py'):compile(raw,name,'exec')
            data[name]=raw
        backup=pathlib.Path(tempfile.mkdtemp(prefix='pr220-',dir='/var/lib/az-baze/backups'));os.chmod(backup,0o700)
        print('PREFLIGHT=PASS; TARGET_HASHES=PASS; BACKUP='+str(backup),flush=True)
        paused=False;main_stopped=False;mutated=False;state_created=False;gate_created=False
        attrs={}
        try:
            paused=True;run('systemctl','stop',HOUR+'.timer');wait_idle()
            main_stopped=True;run('systemctl','stop',MAIN);check_files()
            with sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True) as src:
                with sqlite3.connect(str(backup/'auth.db')) as dst:
                    src.backup(dst)
                    if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('backup integrity')
            for i,(name,old,new) in enumerate(ROWS):
                p=live(name)
                if old:
                    attrs[name]=p.stat();shutil.copy2(p,backup/str(i))
                    if blob((backup/str(i)).read_bytes())!=old:raise RuntimeError('backup hash mismatch')
            (backup/'manifest.json').write_text(json.dumps({'head':HEAD,'files':ROWS}))
            print('BACKUP_VERIFY=PASS',flush=True)
            mutated=True
            for name,old,new in ROWS:
                st=attrs.get(name)
                install(data[name],live(name),st.st_uid if st else 0,st.st_gid if st else 0,st.st_mode&0o777 if st else 0o644)
                if blob(live(name).read_bytes())!=new:raise RuntimeError('installed hash mismatch')
            install(b'{}\n',STATE,uid,gid,0o600);state_created=True
            install(b'',GATE,uid,gid,0o600);gate_created=True
            run('runuser','-u','azadmin','--','test','-w',str(STATE))
            run('runuser','-u','azadmin','--','test','-w',str(STATE.parent))
            run('systemd-analyze','verify',str(live('az-baze-auth/deploy/'+HOUR+'.service')),str(live('az-baze-auth/deploy/'+RETRY+'.service')),str(live('az-baze-auth/deploy/'+RETRY+'.timer')))
            run('systemctl','daemon-reload')
            # Empty queue: this validates the real service without API imports.
            run('systemctl','start',RETRY+'.service')
            if prop(RETRY+'.service','Result')!='success' or prop(RETRY+'.service','ExecMainStatus')!='0':raise RuntimeError('retry service smoke failed')
            if json.loads(STATE.read_text())!={}:raise RuntimeError('empty queue smoke changed state')
            run('systemctl','start',MAIN);wait_health()
            run('systemctl','enable','--now',RETRY+'.timer')
            run('systemctl','start',HOUR+'.timer')
            for unit in (HOUR+'.timer',RETRY+'.timer'):
                if prop(unit,'ActiveState')!='active' or prop(unit,'UnitFileState')!='enabled':raise RuntimeError('timer activation failed: '+unit)
            print('PR220_PRODUCTION_DEPLOY=PASS; FILES='+str(len(ROWS)))
            print('HEAD='+HEAD)
            print('HOURLY_TIMER=ENABLED; RETRY_TIMER=ENABLED; RETRY_DELAY_SECONDS=600; QUEUE_CHECK=EVERY_MINUTE')
            print('HEALTH=PASS; EMPTY_QUEUE_SMOKE=PASS; DB_WRITES_BY_INSTALLER=NONE; SCHEMA=UNCHANGED')
        except BaseException:
            if mutated:
                run('systemctl','stop',HOUR+'.timer')
                subprocess.run(['systemctl','disable','--now',RETRY+'.timer'],capture_output=True)
                subprocess.run(['systemctl','stop',RETRY+'.service'],capture_output=True)
                run('systemctl','stop',HOUR+'.service',MAIN)
                for i,(name,old,new) in enumerate(ROWS):
                    if old:
                        st=attrs[name];install((backup/str(i)).read_bytes(),live(name),st.st_uid,st.st_gid,st.st_mode&0o777)
                    else:live(name).unlink(missing_ok=True)
                if state_created:STATE.unlink(missing_ok=True)
                if gate_created:GATE.unlink(missing_ok=True)
                run('systemctl','daemon-reload')
                print('ROLLBACK_FILES=PASS; DATABASE_RESTORE=NOT_RUN',flush=True)
            if main_stopped:run('systemctl','start',MAIN);wait_health()
            if paused:run('systemctl','start',HOUR+'.timer')
            raise
if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('STOP='+(str(exc) if type(exc) is RuntimeError else type(exc).__name__),flush=True);sys.exit(1)
