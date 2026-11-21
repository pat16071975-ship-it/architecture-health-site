#!/usr/bin/env python3
"""PR247: audited work groups. Metadata seed only; existing contacts and secrets unchanged."""
import ast,re,fcntl
import hashlib,json,os,pathlib,shutil,sqlite3,subprocess,sys,tempfile,time,urllib.request
HEAD='15e768785483c36e31a00ae8a0f02d2817516046'
BASE='6c50bf5b53c87fea0c74da0c71605155757c93db'
MERGE='5ea04ec63d402d320fb9a54540883b3b48ceec21'
ROWS=[['az-baze-auth/access_contacts_ext.py', 'bfa7dd85b3d66aec6e896158bdf9838e31b9eeb4', '3cc3f5b21c0816ef61485b3b2dc8e0a59cb612a8'], ['az-baze-auth/access_groups.py', None, '1957c50bef86bc15a17f93a523880c13ad2a050e'], ['az-baze-auth/app.py', '884ef925fca6aeeb96c0872aa5a9aafb395cc323', 'ca05d3854b37c2e227fb2101fd83d056718b5088'], ['az-baze-auth/contact_excel_import.py', '51982fc1d108925d91137b59aec3c8cac22b72e6', 'e50b80ae6f39aed7e8f36897260dca0d19803be1'], ['az-baze-auth/templates/contact_form.html', '564b2e2c20eebc2977c3f449f09a92c9d19e094f', '8be8bab3a42d5aa349063ccb6a66b78a4e397102'], ['az-baze-auth/templates/contact_import.html', '432a6393e510f779ff3c0d2a28f27c9768c5c545', '6294e976347d0ba8e22234bb74bde1428bbb1f20'], ['az-baze-auth/templates/contacts.html', 'e1c432056bc7a377503f495c6a25b18c599cfbb5', 'c79d8caa98fa1cae3fa5cc1a344ba5285c9adc6a'], ['az-baze-auth/templates/credential_form.html', '170e964e199154cd7e2c909765bfc3ae895ecc21', 'c0b584c673be81c83c531c3a2a35d0ec69c8478e'], ['az-baze-auth/templates/credentials.html', 'ad72f6da79b70f78733003c269d1744d4e7628b6', '3855f8ebacb1eac0b56627c38fa7d5afc4f34e4a'], ['az-baze-auth/templates/work_contact_row.html', None, '4eb5dafdc747c08bb5ffd59d49b2214c03fe7c04'], ['az-baze-auth/templates/work_credential_row.html', None, '7aac11c0836787e1b0f042484a2999ddc5c191e8'], ['az-baze-auth/templates/work_group_controls.html', None, 'f7378c030644dea990e8fb3ea556e49bc2d125af'], ['az-baze-auth/templates/work_group_field.html', None, 'ec8e8e313873204921fa20bab44d0e6d12ee653e'], ['az-baze-auth/templates/work_group_rows.html', None, '63972abb7b6ce60f13a6a14563e3c38aef80a5b9'], ['az-baze-auth/templates/work_group_script.html', None, 'dd4afea284a39dc574b9eae217ea71029aa1acd0'], ['az-baze-auth/templates/work_groups.html', None, 'bed96322217fe9debc6fd43f2c30e6f333eb2598']]
DEPENDENCIES={'az-baze-auth/credentials_store.py': 'cce073b60f2ab8cf286a8bdf5af8d9c87ba27bde', 'az-baze-auth/personal_credentials.py': '8b0d44caad9fd3d55b57d08f1468dbb2acc8d26a', 'az-baze-auth/templates/base.html': '80b5bdb1ee05197d8eb9235b831bae9d14e810c7', 'az-baze-auth/section_navigation.py': '949987e39f768be9e208d9263e7e133e242aa501'}
TABLES=('work_groups','work_contact_groups','work_credential_groups')
INDEX='work_groups_unique_name'
SCHEMA_SHA256='889818a0cf4af7ef52c5e9ed9c217bcb74256bd2ee03dcadeaf5a80b1aaf43f0'
DEFAULTS=('Программы и IT', 'Реклама и продвижение', 'Почта и связь', 'Финансы и документы', 'Оборудование', 'Помещения и обслуживание', 'Безопасность', 'Поставщики')
OLD_TABLES=('work_contacts','credentials','credential_trash','personal_credentials','audit_log','users')
def schema_statements(data):
    tree=ast.parse(data['az-baze-auth/access_groups.py'])
    values={}
    for n in tree.body:
        if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('SCHEMA','DEFAULTS'):
            if n.targets[0].id in values:raise RuntimeError('duplicate schema source')
            values[n.targets[0].id]=ast.literal_eval(n.value)
    if values.get('DEFAULTS')!=DEFAULTS or hashlib.sha256(values.get('SCHEMA','').encode()).hexdigest()!=SCHEMA_SHA256:
        raise RuntimeError('schema source mismatch')
    statements=[s.strip() for s in values['SCHEMA'].split(';') if s.strip()]
    if len(statements)!=4:raise RuntimeError('schema statement count')
    return statements

def check_schema_absent():
    with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        names={r[0] for r in conn.execute('SELECT name FROM sqlite_master')}
        if names.intersection((*TABLES,INDEX)):raise RuntimeError('new group schema already exists; stop for review')
        if not set(OLD_TABLES).issubset(names):raise RuntimeError('existing access/contact schema missing')
def schema_objects(conn):
    return {r[0]:tuple(r[1:]) for r in conn.execute('SELECT name,type,tbl_name,sql FROM sqlite_master')}
def data_fingerprint(conn,tables):
    # Sorted row hashes preserve duplicates, NULL, zero, negative values and blobs.
    # No source records are printed or written to a log.
    result={}
    for name in tables:
        quoted='"'+name.replace('"','""')+'"'
        rows=sorted(hashlib.sha256(repr(tuple(row)).encode('utf-8')).digest() for row in conn.execute('SELECT * FROM '+quoted))
        result[name]=(len(rows),hashlib.sha256(b''.join(rows)).hexdigest())
    return result
def initialize_groups(statements):
    with sqlite3.connect(DB.as_uri()+'?mode=rw',uri=True) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        before=schema_objects(conn)
        if set(before).intersection((*TABLES,INDEX)):raise RuntimeError('new group schema already exists')
        if not set(OLD_TABLES).issubset(before):raise RuntimeError('existing access/contact schema missing')
        old_tables=[name for name,row in before.items() if row[0]=='table' and name!='sqlite_sequence']
        original=data_fingerprint(conn,old_tables)
        sequence_before=sorted(conn.execute('SELECT name,seq FROM sqlite_sequence').fetchall())
        with sqlite3.connect(':memory:') as expected_db:
            for statement in statements:expected_db.execute(statement)
            expected={n:r for n,r in schema_objects(expected_db).items() if n!='sqlite_sequence'}
        for statement in statements:conn.execute(statement)
        conn.executemany('INSERT INTO work_groups(name,name_key) VALUES(?,?)',[(n,n.casefold()) for n in DEFAULTS])
        if schema_objects(conn)!={**before,**expected}:raise RuntimeError('unexpected schema change')
        actual=conn.execute('SELECT name,name_key,parent_id,version FROM work_groups ORDER BY id').fetchall()
        if actual!=[(n,n.casefold(),None,1) for n in DEFAULTS]:raise RuntimeError('group metadata mismatch')
        for name in TABLES[1:]:
            if conn.execute('SELECT count(*) FROM '+name).fetchone()[0]:raise RuntimeError('unexpected automatic assignment')
        if conn.total_changes!=len(DEFAULTS):raise RuntimeError('unexpected DML')
        if data_fingerprint(conn,old_tables)!=original:raise RuntimeError('existing records changed')
        if sorted(conn.execute('SELECT name,seq FROM sqlite_sequence').fetchall())!=sorted(sequence_before+[('work_groups',len(DEFAULTS))]):
            raise RuntimeError('existing sequences changed')
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('database integrity')
    print('SCHEMA_INIT=PASS; NEW_TABLES=3; NEW_EMPTY_TABLES=2; NEW_INDEXES=1; GROUP_ROWS=8; EXISTING_DATA=UNCHANGED',flush=True)
SERVICE='az-baze-auth' 
DB=pathlib.Path('/var/lib/az-baze/auth.db')
BACKUP_ROOT=pathlib.Path('/var/lib/az-baze/backups')
LOCK=pathlib.Path('/run/az-advertising-hourly-install.lock')
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
    for name,old,new in ROWS + [[n,h,h] for n,h in DEPENDENCIES.items()]:
        p=live(name)
        if p.is_symlink() or p.parent.is_symlink(): raise RuntimeError('symlink: '+str(p))
        if not p.parent.is_dir(): raise RuntimeError('target directory missing: '+str(p.parent))
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
def download(name):
    url='https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/'+HEAD+'/'+name
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url,timeout=45) as r:return r.read()
        except Exception:
            if attempt==3:raise
            time.sleep(attempt+1)
def deploy():
    if sys.argv[1:] != ['--apply']: raise RuntimeError('use --apply')
    if os.geteuid()!=0: raise RuntimeError('run in root server terminal')
    if not DB.is_file() or DB.is_symlink(): raise RuntimeError('database path invalid')
    run('systemctl','is-active','--quiet',SERVICE); health(); check_base()
    data={}
    for name,old,new in ROWS:
        raw=download(name)
        if blob(raw)!=new: raise RuntimeError('download hash mismatch: '+name)
        if name.endswith('.py'): compile(raw,name,'exec')
        data[name]=raw
    statements=schema_statements(data)
    check_schema_absent()
    print('PREFLIGHT=PASS; TARGET_HASHES=PASS',flush=True)
    backup=pathlib.Path(tempfile.mkdtemp(prefix='pr247-',dir=BACKUP_ROOT))
    print('BACKUP='+str(backup),flush=True)
    stopped=False; mutated=False
    try:
        stopped=True; run('systemctl','stop',SERVICE); check_base(); check_schema_absent()
        with sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True) as source:
            with sqlite3.connect(str(backup/'auth.db')) as dest:
                source.backup(dest)
                if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('backup integrity')
                original=schema_objects(source)
                if schema_objects(dest)!=original:raise RuntimeError('backup schema mismatch')
                tables=[n for n,r in original.items() if r[0]=='table']
                if data_fingerprint(source,tables)!=data_fingerprint(dest,tables):raise RuntimeError('backup data mismatch')
        attrs={}
        for i,(name,old,new) in enumerate(ROWS):
            p=live(name)
            attrs[name]=p.stat() if old else live('az-baze-auth/templates/contacts.html' if name.endswith('.html') else 'az-baze-auth/app.py').stat()
            if old:
                shutil.copy2(p,backup/str(i))
                if blob((backup/str(i)).read_bytes())!=old: raise RuntimeError('backup hash mismatch')
        (backup/'manifest.json').write_text(json.dumps({'head':HEAD,'base':BASE,'merge':MERGE,'files':ROWS,'new_tables':TABLES,'new_groups':list(DEFAULTS)},indent=2))
        print('BACKUP_VERIFY=PASS',flush=True)
        mutated=True
        for name,old,new in ROWS: install(data[name],live(name),attrs[name])
        for name,old,new in ROWS:
            if blob(live(name).read_bytes())!=new: raise RuntimeError('installed hash mismatch')
        initialize_groups(statements)
        run('systemctl','start',SERVICE); wait_health()
        run('systemctl','is-active','--quiet',SERVICE)
        print('FILES_VERIFY=PASS; HEALTH=PASS',flush=True)
    except BaseException:
        if mutated:
            run('systemctl','stop',SERVICE)
            for i,(name,old,new) in enumerate(ROWS):
                if old: install((backup/str(i)).read_bytes(),live(name),attrs[name])
                elif live(name).exists(): live(name).unlink()
            print('ROLLBACK_FILES=PASS; DATABASE_RESTORE=NOT_RUN; CREATED_TABLES_RETAINED_IF_COMMITTED',flush=True)
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
    with LOCK.open('a') as guard:
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
        print('PR247_PRODUCTION_DEPLOY=PASS; FILES='+str(len(ROWS)))
        print('HEAD='+HEAD)
        print('HEALTH=PASS; DB_SCHEMA=THREE_TABLES; DB_DATA_WRITES_BY_INSTALLER=GROUP_METADATA_ONLY; EXISTING_DATA=UNCHANGED; AUTO_CLASSIFICATION=NOT_RUN; BACKFILL=NOT_RUN; IMPORTS=NOT_RUN; TIMER_CONFIGURATION=UNCHANGED; RETRY_STATE=UNCHANGED')
        print('HOURLY_TIMER=ACTIVE; RETRY_TIMER=ACTIVE')
if __name__=='__main__':
    try: main()
    except Exception as e: print('STOP='+str(e),flush=True); sys.exit(1)
