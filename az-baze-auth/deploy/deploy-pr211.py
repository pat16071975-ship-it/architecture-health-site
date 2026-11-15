#!/usr/bin/env python3
"""PR211 guarded deployment: three files and three empty tables. No data imports or backfill."""
import ast,re,hashlib,json,os,pathlib,shutil,sqlite3,subprocess,sys,tempfile,time,urllib.request
HEAD='04a9e3ab9606da4059d4183cf25086b799f241ae'
ROWS=[['az-baze-auth/app.py', 'b4ddd446b163cd026af715ef0f6bf8d9ee9aff63', '92628a6c8c2de0f1df43ed823f7810a6ae2a610b'], ['az-baze-auth/medical_supplies.py', None, '64ec88fc0737ace385b95bc20b25b8cad0b78ad6'], ['az-baze-auth/templates/medical_supplies.html', None, 'cd367550fce922212ad9b2fad06c2a40e251a9a1']]
TABLES=('med_supply_items','med_supply_periods','med_supply_history')
def schema_statements(raw):
    tree=ast.parse(raw)
    nodes=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SCHEMA' for t in n.targets)]
    if len(nodes)!=1: raise RuntimeError('schema source mismatch')
    statements=[s.strip() for s in ast.literal_eval(nodes[0].value).split(';') if s.strip()]
    if len(statements)!=3: raise RuntimeError('unexpected schema statement count')
    for statement,name in zip(statements,TABLES):
        if not re.match(r'CREATE TABLE IF NOT EXISTS '+name+r'\s*\(',statement): raise RuntimeError('unexpected schema statement')
    return statements
def initialize_empty_tables(statements):
    with sqlite3.connect('file:'+str(DB)+'?mode=rw',uri=True) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        existing={r[0] for r in conn.execute('SELECT name FROM sqlite_master')}
        if existing.intersection(TABLES): raise RuntimeError('medical tables already exist; stop for review')
        for statement in statements: conn.execute(statement)
        for name in TABLES:
            if conn.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]!=0: raise RuntimeError('new table is not empty')
        if conn.total_changes!=0: raise RuntimeError('unexpected data writes')
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('database integrity')
    print('SCHEMA_INIT=PASS; NEW_EMPTY_TABLES=3; DML_WRITES=0',flush=True)
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
    statements=schema_statements(data['az-baze-auth/medical_supplies.py'])
    with sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True) as conn:
        if {r[0] for r in conn.execute('SELECT name FROM sqlite_master')}.intersection(TABLES): raise RuntimeError('medical tables already exist; stop for review')
    print('PREFLIGHT=PASS; TARGET_HASHES=PASS',flush=True)
    backup=pathlib.Path(tempfile.mkdtemp(prefix='pr211-',dir='/var/lib/az-baze/backups'))
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
        print('PR211_PRODUCTION_DEPLOY=PASS; FILES='+str(len(ROWS)))
        print('HEAD='+HEAD)
        print('HEALTH=PASS; DB_SCHEMA=THREE_EMPTY_TABLES; DB_DATA_WRITES_BY_INSTALLER=NONE; BACKFILL=NOT_RUN; IMPORTS=NOT_RUN; TIMER=UNTOUCHED')
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
