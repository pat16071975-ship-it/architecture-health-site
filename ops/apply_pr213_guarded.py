#!/usr/bin/env python3
"""PR213 guarded deployment: eleven files and one empty personal table. No data imports or backfill."""
import ast,re,hashlib,json,os,pathlib,shutil,sqlite3,subprocess,sys,tempfile,time,urllib.request
HEAD='022adea25e8638187063506246a598f18ec1a7e1'
ROWS=[['az-baze-auth/access_contacts_ext.py', '9975ebed3330fc686273452b3906f8f52007ecc2', '8f3fd2805d59a6d20e22846c754c9dbe72f8e240'], ['az-baze-auth/app.py', 'fd2a03d8f85ec12db918b7ecd8ba1d5728971757', 'ac581c97662f1eef88fa41389de37472d48f0c64'], ['az-baze-auth/personal_credentials.py', None, '18e935d77af09fce1fb6eae202afbb98ad749b2a'], ['az-baze-auth/section_navigation.py', 'ba5ddde06732aee7b3446384aaba651f96526eb6', '949987e39f768be9e208d9263e7e133e242aa501'], ['az-baze-auth/templates/access_contacts.html', '91b1c426de729be601f7b7be5a4baf656d152fa6', '81a0d1885cebdeb25c27642eaee52b2202cbdbdb'], ['az-baze-auth/templates/credential_delete_confirm.html', 'a6eb529f64dfb1e99dcf2fb0204f164a728b6240', 'e1ab1fd24915a9842b085cba3ecea719304d734d'], ['az-baze-auth/templates/credential_form.html', 'b2136b1da6d59868f3838a1a2839ace769ee74b8', '170e964e199154cd7e2c909765bfc3ae895ecc21'], ['az-baze-auth/templates/credentials.html', '5460b1d8a8546d704e875fae09510315a157278a', 'ad72f6da79b70f78733003c269d1744d4e7628b6'], ['az-baze-auth/templates/credentials_trash.html', 'b69fab838465e8fb590f1b6c24d2355f2dc457aa', '85dcf4cc08c32fdd73ed5bf2532e75afbe25c1f0'], ['az-baze-auth/templates/nn_home.html', '8265b17d578d0354f440d29fc7570849953cd246', 'fd347e6b57644c112634d60abaa8710538116f07'], ['az-baze-auth/templates/user_form.html', 'fb218d3725d4c5247014934ba79747b672e7fec7', 'dca23cd79031b625e30c60fa0f72937afc8df675']]
TABLES=('personal_credentials',)
INDEXES=('personal_credentials_owner',)
def schema_statements(data):
    result=[]
    for name in ('personal_credentials.py',):
        tree=ast.parse(data['az-baze-auth/'+name])
        nodes=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SCHEMA' for t in n.targets)]
        if len(nodes)!=1: raise RuntimeError('schema source mismatch')
        statements=[s.strip() for s in ast.literal_eval(nodes[0].value).split(';') if s.strip()]
        result.extend(statements)
    allowed={('TABLE',n) for n in TABLES}|{('INDEX',n) for n in INDEXES}
    seen=set()
    for statement in result:
        m=re.match(r'CREATE (TABLE|INDEX) IF NOT EXISTS ([a-z_]+)\s*(?:\(|ON\s)',statement)
        if not m or m.groups() not in allowed or m.groups() in seen: raise RuntimeError('unexpected schema statement')
        seen.add(m.groups())
    if seen!=allowed: raise RuntimeError('schema objects mismatch')
    return result
def initialize_empty_tables(statements):
    with sqlite3.connect('file:'+str(DB)+'?mode=rw',uri=True) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        existing={r[0] for r in conn.execute('SELECT name FROM sqlite_master')}
        if existing.intersection(TABLES+INDEXES): raise RuntimeError('personal schema objects already exist; stop for review')
        for statement in statements: conn.execute(statement)
        for name in TABLES:
            if conn.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]!=0: raise RuntimeError('new table is not empty')
        if conn.total_changes!=0: raise RuntimeError('unexpected data writes')
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('database integrity')
    print('SCHEMA_INIT=PASS; NEW_EMPTY_TABLES=1; DML_WRITES=0',flush=True)
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
    statements=schema_statements(data)
    with sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True) as conn:
        if {r[0] for r in conn.execute('SELECT name FROM sqlite_master')}.intersection(TABLES+INDEXES): raise RuntimeError('personal schema objects already exist; stop for review')
    print('PREFLIGHT=PASS; TARGET_HASHES=PASS',flush=True)
    backup=pathlib.Path(tempfile.mkdtemp(prefix='pr213-',dir='/var/lib/az-baze/backups'))
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
            attrs[name]=p.stat() if old else live('az-baze-auth/app.py' if name.endswith('.py') else 'az-baze-auth/templates/base.html').stat()
            if old:
                shutil.copy2(p,backup/str(i))
                if blob((backup/str(i)).read_bytes())!=old: raise RuntimeError('backup hash mismatch')
        (backup/'manifest.json').write_text(json.dumps({'head':HEAD,'files':ROWS},indent=2))
        print('BACKUP_VERIFY=PASS',flush=True)
        mutated=True
        for name,old,new in ROWS: install(data[name],live(name),attrs[name])
        for name,old,new in ROWS:
            if blob(live(name).read_bytes())!=new: raise RuntimeError('installed hash mismatch')
        initialize_empty_tables(statements)
        run('systemctl','start',SERVICE); wait_health()
        run('systemctl','is-active','--quiet',SERVICE)
        print('PR213_PRODUCTION_DEPLOY=PASS; FILES='+str(len(ROWS)))
        print('HEAD='+HEAD)
        print('HEALTH=PASS; DB_SCHEMA=ONE_EMPTY_TABLE; DB_DATA_WRITES_BY_INSTALLER=NONE; BACKFILL=NOT_RUN; IMPORTS=NOT_RUN; TIMER=UNTOUCHED; CREDENTIAL_KEY=UNTOUCHED')
    except BaseException:
        if mutated:
            run('systemctl','stop',SERVICE)
            for i,(name,old,new) in enumerate(ROWS):
                if old: install((backup/str(i)).read_bytes(),live(name),attrs[name])
                elif live(name).exists(): live(name).unlink()
            print('ROLLBACK_FILES=PASS; DATABASE_RESTORE=NOT_RUN; CREATED_TABLES_RETAINED_IF_COMMITTED',flush=True)
        if stopped: run('systemctl','start',SERVICE); wait_health()
        raise
if __name__=='__main__':
    try: main()
    except Exception as e: print('STOP='+str(e),flush=True); sys.exit(1)
