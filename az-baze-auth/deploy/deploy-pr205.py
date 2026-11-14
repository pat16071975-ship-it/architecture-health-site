#!/usr/bin/env python3
"""PR205 guarded file-only deployment. No imports, backfill, timer or SQL writes."""
import hashlib,json,os,pathlib,shutil,sqlite3,subprocess,sys,tempfile,time,urllib.request
HEAD='1474fd1acfc41e594e934231293e13532aca791f'
ROWS=[['az-baze-auth/completed_visit_totals.py', '6b76d803600ecef7a7c640800c66a20576d0212e', '969c78e41b64d64a468206e3c7693d521baa7a0a'], ['az-baze-auth/daily_upload_core.py', '1168c1783322953dc3515c1f1a9766b94cfc0626', '94058db4e6792192af917fc050567aacce7d1a3d'], ['az-baze-auth/finrez.py', '383cb601f3207c684d76cecdc65b1dc6cb95b5ac', '967cf653a53c605165ef1752dc15f6caa82a45c0'], ['az-baze-auth/report_accounting.py', None, 'b169141891acd4ba2579d9906500aae00a81c030'], ['az-baze-auth/report_storage.py', 'c1e51a03b9825b7321ec4eda93cb8d4b71a84977', '26968754324f5e2be7f01fc39d86caac42041a57'], ['az-baze-auth/terminology.py', '1b690a53638cbd204732f50f91a325ddac1102ae', '850d4d3a41fac971eac6c6e8ed15600d6de318c2'], ['reports/dashboard.html', '6ee30907828c4b9c6d0746eceffe92ff26c62389', 'a022262277ddc67e9074bee92d1db6d10268d398'], ['reports/finrez-economics-integrated.js', '4c6cb9d5bf6eea442a210acd9f78a1d2ebf2bf5e', '08b4d6188af11ac2aae6f51f47527303e952f383'], ['reports/finrez.html', '67839a1d77de32b5929d2bc0f5548b5ba37bf8cd', 'a6e57170b250f2ee2c92289bca6fddf1fcf95744'], ['reports/forecast-audit-sync.js', '47b98a0500e7dac00ddf306db399dc052055d633', '85e165331f8f54e299a54106ce7ec0cfd833cf90'], ['reports/forecast.html', '93fb996b02e00e9f5e5b5d118a00858c009c5094', '5d7685211ee81712d821093fcd0656d6d6e9a795'], ['reports/services-summary-economics.js', '72696edd1265c4844c12b2f160b2bf15f1be2806', '93c440870ef8a7ec0cf4b809e515800b9a86e5d3'], ['reports/services.html', 'd4f9a040058e86e0560fc15cce8fe840764616e4', '3abd768603aecd5832f2e0f9bf2b1cce9c3c757a']]
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
def main():
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
    backup=pathlib.Path(tempfile.mkdtemp(prefix='pr205-',dir='/var/lib/az-baze/backups'))
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
            attrs[name]=p.stat() if old else pathlib.Path('/opt/az-baze-auth/finrez.py').stat()
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
        print('PR205_PRODUCTION_DEPLOY=PASS; FILES='+str(len(ROWS)))
        print('HEAD='+HEAD)
        print('HEALTH=PASS; DB_WRITES_BY_INSTALLER=NONE; BACKFILL=NOT_RUN; IMPORTS=NOT_RUN; TIMER=UNTOUCHED')
    except BaseException:
        if mutated:
            run('systemctl','stop',SERVICE)
            for i,(name,old,new) in enumerate(ROWS):
                if old: install((backup/str(i)).read_bytes(),live(name),attrs[name])
                elif live(name).exists(): live(name).unlink()
            print('ROLLBACK_FILES=PASS; DATABASE_RESTORE=NOT_RUN',flush=True)
        if stopped: run('systemctl','start',SERVICE); wait_health()
        raise
if __name__=='__main__':
    try: main()
    except Exception as e: print('STOP='+str(e),flush=True); sys.exit(1)
