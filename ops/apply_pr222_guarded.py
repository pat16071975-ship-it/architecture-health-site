#!/usr/bin/env python3
"""Install only audited dashboard HTML; no service restart or DB access."""
import fcntl,hashlib,json,os,pathlib,shutil,sys,tempfile,urllib.request
HEAD='962338681eac052fe72897a464ede82b5114eb0e'
OLD='a022262277ddc67e9074bee92d1db6d10268d398'
NEW='e1991b11f7084bc376a209cfccaaa07238f404ce'
TARGET=pathlib.Path('/var/www/az-baze.ru/reports/dashboard.html')
BACKUPS=pathlib.Path('/var/lib/az-baze/backups')
def blob(raw):return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def health():
    with urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=5) as r:
        if r.status!=200:raise RuntimeError('health failed')
def check_base():
    if TARGET.is_symlink() or not TARGET.is_file() or blob(TARGET.read_bytes())!=OLD:
        raise RuntimeError('LIVE_BASE_MISMATCH: '+str(TARGET))
def install(raw,st):
    fd,tmp=tempfile.mkstemp(prefix='.dashboard-',dir=TARGET.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.chown(tmp,st.st_uid,st.st_gid);os.chmod(tmp,st.st_mode&0o777);os.replace(tmp,TARGET)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def deploy():
    check_base();health()
    url='https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/'+HEAD+'/reports/dashboard.html'
    with urllib.request.urlopen(url,timeout=45) as r:raw=r.read()
    if blob(raw)!=NEW:raise RuntimeError('download hash mismatch')
    print('PREFLIGHT=PASS; TARGET_HASHES=PASS',flush=True)
    backup=pathlib.Path(tempfile.mkdtemp(prefix='pr222-',dir=BACKUPS))
    st=TARGET.stat();shutil.copy2(TARGET,backup/'dashboard.html')
    if blob((backup/'dashboard.html').read_bytes())!=OLD:raise RuntimeError('backup hash mismatch')
    (backup/'manifest.json').write_text(json.dumps({'head':HEAD,'target':str(TARGET),'old':OLD,'new':NEW}))
    print('BACKUP='+str(backup)+'\nBACKUP_VERIFY=PASS',flush=True)
    check_base()
    try:
        install(raw,st)
        if blob(TARGET.read_bytes())!=NEW:raise RuntimeError('installed hash mismatch')
        health()
    except BaseException:
        install((backup/'dashboard.html').read_bytes(),st)
        if blob(TARGET.read_bytes())!=OLD:raise RuntimeError('rollback verification failed')
        print('ROLLBACK_FILE=PASS',flush=True)
        raise
    print('PR222_PRODUCTION_DEPLOY=PASS; FILES=1\nHEAD='+HEAD)
    print('HEALTH=PASS; DB_ACCESS=NONE; IMPORTS=NOT_RUN; TIMERS=UNTOUCHED; SERVICE_RESTART=NOT_RUN')
def main():
    if os.geteuid()!=0 or sys.argv[1:]!=['--apply']:raise RuntimeError('root and --apply required')
    with open('/run/az-pr222-install.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);deploy()
if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('STOP='+(str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__),flush=True);sys.exit(1)
