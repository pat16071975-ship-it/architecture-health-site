#!/usr/bin/env python3
"""PR248: separate work group catalogues; preserve records, secrets and assignments."""
import ast,fcntl
import hashlib,json,os,pathlib,shutil,sqlite3,subprocess,sys,tempfile,time,urllib.request
HEAD='bafe5538e7453432f355f8b31967955ed6039089'
BASE='5ea04ec63d402d320fb9a54540883b3b48ceec21'
MERGE='0892b78b49086edff3c7d81b63b1a7c55faaa760'
ROWS=[['az-baze-auth/access_contacts_ext.py', '3cc3f5b21c0816ef61485b3b2dc8e0a59cb612a8', 'f20303c20097fcd8df81c803ebaeb9b0760d9240'], ['az-baze-auth/access_groups.py', '1957c50bef86bc15a17f93a523880c13ad2a050e', 'd9282bb4796e9418033f36906d31d72a509d42e7'], ['az-baze-auth/templates/work_group_controls.html', 'f7378c030644dea990e8fb3ea556e49bc2d125af', '061b99c13f1c7a41b85ff431eaba17f1fc42569d'], ['az-baze-auth/templates/work_groups.html', 'bed96322217fe9debc6fd43f2c30e6f333eb2598', 'd29c25b4c344654d232b368a48c793610faac8c6']]
DEPENDENCIES={'az-baze-auth/credentials_store.py': 'cce073b60f2ab8cf286a8bdf5af8d9c87ba27bde', 'az-baze-auth/personal_credentials.py': '8b0d44caad9fd3d55b57d08f1468dbb2acc8d26a', 'az-baze-auth/templates/base.html': '80b5bdb1ee05197d8eb9235b831bae9d14e810c7', 'az-baze-auth/section_navigation.py': '949987e39f768be9e208d9263e7e133e242aa501', 'az-baze-auth/app.py': 'ca05d3854b37c2e227fb2101fd83d056718b5088', 'az-baze-auth/contact_excel_import.py': 'e50b80ae6f39aed7e8f36897260dca0d19803be1', 'az-baze-auth/templates/contact_form.html': '8be8bab3a42d5aa349063ccb6a66b78a4e397102', 'az-baze-auth/templates/contact_import.html': '6294e976347d0ba8e22234bb74bde1428bbb1f20', 'az-baze-auth/templates/contacts.html': 'c79d8caa98fa1cae3fa5cc1a344ba5285c9adc6a', 'az-baze-auth/templates/credential_form.html': 'c0b584c673be81c83c531c3a2a35d0ec69c8478e', 'az-baze-auth/templates/credentials.html': '3855f8ebacb1eac0b56627c38fa7d5afc4f34e4a', 'az-baze-auth/templates/work_contact_row.html': '4eb5dafdc747c08bb5ffd59d49b2214c03fe7c04', 'az-baze-auth/templates/work_credential_row.html': '7aac11c0836787e1b0f042484a2999ddc5c191e8', 'az-baze-auth/templates/work_group_field.html': 'ec8e8e313873204921fa20bab44d0e6d12ee653e', 'az-baze-auth/templates/work_group_rows.html': '63972abb7b6ce60f13a6a14563e3c38aef80a5b9', 'az-baze-auth/templates/work_group_script.html': 'dd4afea284a39dc574b9eae217ea71029aa1acd0'}
LEGACY_SCHEMA='\nCREATE TABLE IF NOT EXISTS work_groups (\n id INTEGER PRIMARY KEY AUTOINCREMENT,\n name TEXT NOT NULL, name_key TEXT NOT NULL,\n parent_id INTEGER REFERENCES work_groups(id) ON DELETE RESTRICT,\n version INTEGER NOT NULL DEFAULT 1\n);\nCREATE UNIQUE INDEX IF NOT EXISTS work_groups_unique_name\n ON work_groups(COALESCE(parent_id,0),name_key);\nCREATE TABLE IF NOT EXISTS work_contact_groups (\n record_id INTEGER PRIMARY KEY REFERENCES work_contacts(id) ON DELETE CASCADE,\n group_id INTEGER NOT NULL REFERENCES work_groups(id) ON DELETE RESTRICT\n);\nCREATE TABLE IF NOT EXISTS work_credential_groups (\n record_id INTEGER PRIMARY KEY REFERENCES credentials(id) ON DELETE CASCADE,\n group_id INTEGER NOT NULL REFERENCES work_groups(id) ON DELETE RESTRICT\n);\n'
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

GROUP_OBJECTS=('work_groups','work_groups_unique_name','work_contact_groups','work_credential_groups')
def check_legacy(conn):
    objects=schema_objects(conn)
    with sqlite3.connect(':memory:') as expected:
        expected.executescript(LEGACY_SCHEMA)
        expected_objects=schema_objects(expected)
    if any(objects.get(n)!=expected_objects[n] for n in GROUP_OBJECTS):
        raise RuntimeError('legacy group schema mismatch or already separated; no changes applied')
    if not {'work_contacts','credentials','credential_trash','personal_credentials','audit_log','users'}<=objects.keys():
        raise RuntimeError('existing access/contact schema missing')
    for table in ('work_groups','work_contact_groups','work_credential_groups'):
        if conn.execute('PRAGMA foreign_key_check('+table+')').fetchone():
            raise RuntimeError('invalid existing group relation')

def migration_source(data):
    # Only these two audited functions and literal constants are needed. Do not
    # import the application, initialize other modules or require system Flask.
    key='az-baze-auth/access_groups.py'
    if blob(data[key])!=next(new for name,old,new in ROWS if name==key):
        raise RuntimeError('migration source hash mismatch')
    names={'SCHEMA','DEFAULTS','CREDENTIAL_DEFAULTS','TABLES','_copy_credential_groups','init_schema'}
    nodes=[];found=set()
    for node in ast.parse(data[key]).body:
        name=node.name if isinstance(node,ast.FunctionDef) else node.targets[0].id if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) else None
        if name in names:
            if name in found:raise RuntimeError('duplicate migration source')
            found.add(name);nodes.append(node)
    if found!=names:raise RuntimeError('migration source incomplete')
    scope={}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),key,'exec'),scope)
    return scope

def group_path(rows,ident):
    row=rows[ident];parent=row[3]
    if parent is None:return (row[2],)
    if parent not in rows or rows[parent][3] is not None:raise RuntimeError('invalid group hierarchy')
    return (rows[parent][2],row[2])

def prepare_groups(conn,source):
    # Caller owns BEGIN IMMEDIATE until verified migration and files are ready.
    check_legacy(conn)
    before=schema_objects(conn)
    protected=[n for n,r in before.items() if r[0]=='table' and n not in ('work_groups','work_credential_groups','sqlite_sequence')]
    original=data_fingerprint(conn,protected)
    sequence=dict(conn.execute('SELECT name,seq FROM sqlite_sequence'))
    old={r[0]:tuple(r) for r in conn.execute('SELECT id,name,name_key,parent_id,version FROM work_groups')}
    assignments=dict(conn.execute('SELECT record_id,group_id FROM work_credential_groups'))
    expected_paths={record:group_path(old,group) for record,group in assignments.items()}
    needed=set(assignments.values())
    needed.update(old[n][3] for n in tuple(needed) if old[n][3] is not None)
    expected_services={group_path(old,n):(old[n][1],old[n][2],old[n][4]) for n in needed}
    for name in source['CREDENTIAL_DEFAULTS']:
        expected_services.setdefault((name.casefold(),),(name,name.casefold(),1))
    with sqlite3.connect(':memory:') as expected:
        expected.executescript(LEGACY_SCHEMA)
        expected.execute("ALTER TABLE work_groups ADD COLUMN kind TEXT NOT NULL DEFAULT 'contacts' CHECK(kind IN ('contacts','credentials'))")
        expected.execute('DROP INDEX work_groups_unique_name')
        expected.execute('CREATE UNIQUE INDEX work_groups_unique_name ON work_groups(kind,COALESCE(parent_id,0),name_key)')
        desired=schema_objects(expected)
    changes=conn.total_changes
    source['init_schema'](conn)
    after=schema_objects(conn)
    expected_schema=dict(before)
    for name in ('work_groups','work_groups_unique_name'):expected_schema[name]=desired[name]
    # SQLite preserves whitespace in CREATE INDEX. Compare the index separately
    # by its definition produced by the pinned schema, never by a relaxed regex.
    with sqlite3.connect(':memory:') as expected:
        for statement in source['SCHEMA'].split(';'):
            if statement.strip():expected.execute(statement)
        expected_schema['work_groups_unique_name']=schema_objects(expected)['work_groups_unique_name']
    if after!=expected_schema:raise RuntimeError('unexpected schema change')
    new={r[0]:tuple(r) for r in conn.execute('SELECT id,name,name_key,parent_id,version,kind FROM work_groups')}
    contacts={n:r[:5] for n,r in new.items() if r[5]=='contacts'}
    if old:
        if contacts!=old:raise RuntimeError('contact groups changed')
    else:
        if sorted((r[1],r[2],r[3],r[4]) for r in contacts.values())!=sorted((n,n.casefold(),None,1) for n in source['DEFAULTS']):
            raise RuntimeError('empty contact catalogue seed mismatch')
    services={n:r for n,r in new.items() if r[5]=='credentials'}
    actual_services={group_path(services,n):(r[1],r[2],r[4]) for n,r in services.items()}
    if len(actual_services)!=len(services) or actual_services!=expected_services:
        raise RuntimeError('service groups or hierarchy changed')
    actual_assignments={record:group_path(services,group) for record,group in conn.execute('SELECT record_id,group_id FROM work_credential_groups')}
    if actual_assignments!=expected_paths:raise RuntimeError('credential assignment changed')
    if data_fingerprint(conn,protected)!=original:raise RuntimeError('existing records or secrets changed')
    new_sequence=dict(conn.execute('SELECT name,seq FROM sqlite_sequence'))
    if {n:v for n,v in new_sequence.items() if n!='work_groups'}!={n:v for n,v in sequence.items() if n!='work_groups'}:
        raise RuntimeError('unrelated sequence changed')
    if new_sequence.get('work_groups')!=max(max(new),sequence.get('work_groups',0)):
        raise RuntimeError('group sequence mismatch')
    if conn.total_changes-changes!=len(new)-len(old)+len(assignments):raise RuntimeError('unexpected DML')
    for table in ('work_groups','work_contact_groups','work_credential_groups'):
        if conn.execute('PRAGMA foreign_key_check('+table+')').fetchone():raise RuntimeError('invalid resulting group relation')
    if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('database integrity')
    return len(services),len(assignments)

GROUP_TABLES=('work_groups','work_contact_groups','work_credential_groups')
def group_snapshot(conn):
    return {
        'schema':{n:r for n,r in schema_objects(conn).items() if r[1] in GROUP_TABLES},
        'data':data_fingerprint(conn,GROUP_TABLES),
        'sequence':conn.execute("SELECT seq FROM sqlite_sequence WHERE name='work_groups'").fetchall(),
    }

def rollback_groups(backup,expected):
    # Undo only this migration. Never restore the whole DB over records that
    # startup or a user may have written after the service was restarted.
    with sqlite3.connect((backup/'auth.db').as_uri()+'?mode=ro',uri=True) as old:
        check_legacy(old)
        original=group_snapshot(old)
        groups=old.execute('SELECT id,name,name_key,parent_id,version FROM work_groups').fetchall()
        links=old.execute('SELECT record_id,group_id FROM work_credential_groups').fetchall()
    conn=sqlite3.connect(DB.as_uri()+'?mode=rw',uri=True)
    try:
        conn.execute('PRAGMA foreign_keys=OFF');conn.execute('BEGIN IMMEDIATE')
        if group_snapshot(conn)!=expected:
            raise RuntimeError('group metadata changed after startup; automatic rollback blocked')
        before=schema_objects(conn)
        protected=[n for n,r in before.items() if r[0]=='table' and n not in (*GROUP_TABLES,'sqlite_sequence')]
        data_before=data_fingerprint(conn,protected)
        sequences=conn.execute("SELECT name,seq FROM sqlite_sequence WHERE name<>'work_groups' ORDER BY name").fetchall()
        conn.execute('DELETE FROM work_credential_groups')
        conn.execute('DROP TABLE work_groups')
        conn.execute(original['schema']['work_groups'][2])
        conn.executemany('INSERT INTO work_groups(id,name,name_key,parent_id,version) VALUES(?,?,?,?,?)',groups)
        for name,row in original['schema'].items():
            if row[1]=='work_groups' and row[0]!='table' and row[2]:conn.execute(row[2])
        conn.executemany('INSERT INTO work_credential_groups(record_id,group_id) VALUES(?,?)',links)
        conn.execute("DELETE FROM sqlite_sequence WHERE name='work_groups'")
        conn.executemany("INSERT INTO sqlite_sequence(name,seq) VALUES('work_groups',?)",original['sequence'])
        desired={n:r for n,r in before.items() if r[1]!='work_groups'}
        desired.update({n:r for n,r in original['schema'].items() if r[1]=='work_groups'})
        if schema_objects(conn)!=desired or group_snapshot(conn)!=original:
            raise RuntimeError('group rollback verification failed')
        if data_fingerprint(conn,protected)!=data_before or conn.execute("SELECT name,seq FROM sqlite_sequence WHERE name<>'work_groups' ORDER BY name").fetchall()!=sequences:
            raise RuntimeError('unrelated data changed during rollback')
        for table in GROUP_TABLES:
            if conn.execute('PRAGMA foreign_key_check('+table+')').fetchone():raise RuntimeError('rollback relation verification failed')
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('rollback database integrity failed')
        conn.commit()
    except BaseException:
        conn.rollback();raise
    finally:
        conn.close()
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
    if sys.argv[1:]!=['--apply'] or os.geteuid()!=0:raise RuntimeError('root and --apply required')
    if not DB.is_file() or DB.is_symlink():raise RuntimeError('database path invalid')
    run('systemctl','is-active','--quiet',SERVICE);health();check_base()
    data={}
    for name,old,new in ROWS:
        raw=download(name)
        if blob(raw)!=new:raise RuntimeError('download hash mismatch: '+name)
        if name.endswith('.py'):compile(raw,name,'exec')
        data[name]=raw
    source=migration_source(data)
    with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as conn:
        conn.execute('PRAGMA query_only=ON');check_legacy(conn)
    print('PREFLIGHT=PASS; TARGET_HASHES=PASS',flush=True)
    backup=pathlib.Path(tempfile.mkdtemp(prefix='pr248-',dir=BACKUP_ROOT))
    print('BACKUP='+str(backup),flush=True)
    stopped=False;mutated=False;committed=False;conn=None;attrs={}
    try:
        stopped=True;run('systemctl','stop',SERVICE);check_base()
        conn=sqlite3.connect(DB.as_uri()+'?mode=rw',uri=True)
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE');check_legacy(conn)
        # The writer lock freezes the live DB while a separate reader creates
        # and verifies the backup. No production data is exported to the log.
        with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as reader:
            with sqlite3.connect(str(backup/'auth.db')) as dest:
                reader.backup(dest)
                original=schema_objects(conn)
                tables=[n for n,r in original.items() if r[0]=='table']
                if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or schema_objects(dest)!=original:
                    raise RuntimeError('backup schema or integrity mismatch')
                if data_fingerprint(conn,tables)!=data_fingerprint(dest,tables):raise RuntimeError('backup data mismatch')
        for i,(name,old,new) in enumerate(ROWS):
            p=live(name);attrs[name]=p.stat();shutil.copy2(p,backup/str(i))
            if blob((backup/str(i)).read_bytes())!=old:raise RuntimeError('backup hash mismatch')
        (backup/'manifest.json').write_text(json.dumps({'head':HEAD,'base':BASE,'merge':MERGE,'files':ROWS,'migration':'separate work group catalogues; preserve paths and records'},indent=2))
        print('BACKUP_VERIFY=PASS',flush=True)
        count,moved=prepare_groups(conn,source)
        mutated=True
        for name,old,new in ROWS:install(data[name],live(name),attrs[name])
        for name,old,new in ROWS:
            if blob(live(name).read_bytes())!=new:raise RuntimeError('installed hash mismatch')
        # The real server bootstrap seeds the provider registry even when every
        # provider exists. It must start only AFTER this writer lock is released.
        if not conn.in_transaction:raise RuntimeError('migration transaction lost')
        separated=group_snapshot(conn)
        conn.commit();committed=True
        conn.close();conn=None
        run('systemctl','start',SERVICE);wait_health();run('systemctl','is-active','--quiet',SERVICE)
        print('SCHEMA_SEPARATION=PASS; SERVICE_GROUPS='+str(count)+'; PRESERVED_CREDENTIAL_ASSIGNMENTS='+str(moved),flush=True)
        print('FILES_VERIFY=PASS; HEALTH=PASS',flush=True)
    except BaseException as error:
        if mutated:run('systemctl','stop',SERVICE)
        if conn is not None and not committed:conn.rollback()
        if committed:
            try:rollback_groups(backup,separated)
            except Exception as rollback_error:
                print('GROUP_ROLLBACK=BLOCKED; NEW_FILES_RETAINED; DATABASE_RESTORE=NOT_RUN',flush=True)
                run('systemctl','start',SERVICE);wait_health()
                raise RuntimeError(str(error)+'; '+str(rollback_error)) from rollback_error
        if mutated:
            for i,(name,old,new) in enumerate(ROWS):install((backup/str(i)).read_bytes(),live(name),attrs[name])
            print('ROLLBACK_FILES=PASS; MIGRATION_ROLLBACK=PASS; DATABASE_RESTORE=NOT_RUN',flush=True)
        if stopped:
            run('systemctl','start',SERVICE);wait_health()
            print('PREVIOUS_SERVICE_HEALTH=PASS',flush=True)
        raise
    finally:
        if conn is not None:conn.close()
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
        print('PR248_PRODUCTION_DEPLOY=PASS; FILES='+str(len(ROWS)))
        print('HEAD='+HEAD)
        print('HEALTH=PASS; DB_SCHEMA=GROUP_CATALOGUES_SEPARATED; DB_DATA_WRITES_BY_INSTALLER=GROUP_METADATA_AND_CREDENTIAL_LINKS_ONLY; RECORDS_AND_SECRETS=UNCHANGED; CONTACT_GROUPS=PRESERVED; CREDENTIAL_ASSIGNMENTS=PRESERVED; AUTO_CLASSIFICATION=NOT_RUN; BACKFILL=NOT_RUN; IMPORTS=NOT_RUN; TIMER_CONFIGURATION=UNCHANGED; RETRY_STATE=UNCHANGED')
        print('HOURLY_TIMER=ACTIVE; RETRY_TIMER=ACTIVE')
if __name__=='__main__':
    try: main()
    except Exception as e: print('STOP='+str(e),flush=True); sys.exit(1)
