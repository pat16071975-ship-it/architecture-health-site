#!/usr/bin/env python3
"""PR250 guarded delivery: exact files, four empty control tables, no data import."""
import ast,contextlib,fcntl,hashlib,json,os,pathlib,shutil,sqlite3,subprocess,sys,tempfile,time,urllib.request
HEAD='bc4a2839f2a7ca2f65f27e963dd0f1901784c076'
BASE='098ca183bab3f61c42326423f11e87554ae10464'
ROWS=[['az-baze-auth/app.py', 'ca05d3854b37c2e227fb2101fd83d056718b5088', '7472be60508a497aa04ad969dac932877bf5d9bf'], ['az-baze-auth/control_center.py', None, '8cabd8d4cc00472f1db6b2e5f9fd2dbe96e97a3d'], ['az-baze-auth/section_navigation.py', '949987e39f768be9e208d9263e7e133e242aa501', 'b404a4727e92958d2be9ac0ddbafd659614b9c1f'], ['az-baze-auth/templates/control_center.html', None, '95592e982ace15320fb145fab7ea1e9930ac35df'], ['az-baze-auth/templates/ztl.html', '30b3445e9e6317316393b040c0cd608efb936523', '9bd9ca72d1203660a5e2c3c23617add56ca25235'], ['az-baze-auth/templates/ztl_control.html', 'bee2f008303a9d5ed6c14f289d3be8d70aceb16e', 'a8688b517000d5530a363d963ed9adee16462dcc'], ['az-baze-auth/templates/ztl_economics.html', '8ee1f06a9bee2dd92195edc597392ffa70a894bb', 'b10e6f7e8884cc23b55307b135a1643f0b2694f1'], ['az-baze-auth/templates/ztl_payroll_orders.html', '84499124e77f532d7b926f3d1f59371bb3737dbc', '4e562ffe1aaeb35b26528311e253daade46bf5f9'], ['az-baze-auth/templates/ztl_products.html', '81a0c3bf1d6b3ae28df24952baa9e422a53ac322', 'b6feed6cef912cb19e78db8f55a71b881e5609b6'], ['az-baze-auth/ztl.py', '93f4de54fcc883e9571a8d6390ebc443a33d93a2', 'cbe2a121015a2db75967029af9813ceeaaf9ceb1'], ['az-baze-auth/ztl_control.py', '4114bee55e60f417338b66a34921b858b07ed3d8', '4f06cbc920431d5395df59456fa30d3e13a98f8c']]
DEPENDENCIES={'az-baze-auth/server.py': '7d186761a727baa1fe50707c80b3d9ab154a3b96', 'az-baze-auth/medical_supplies.py': '0c8866461ab5a75561154fd977665673822b783f', 'az-baze-auth/ztl_labor.py': '04ec70231b9346dbc3f53ca61ab2b27c11781fc1', 'az-baze-auth/templates/base.html': '80b5bdb1ee05197d8eb9235b831bae9d14e810c7', 'az-baze-auth/templates/ztl_menu.html': '8983b485441f13b5fa74949220824a82b4028ae8', 'az-baze-auth/templates/ztl_explanation.html': '3107fbc931873959159272ff8c58606492223d34', 'az-baze-auth/templates/ztl_labor_fields.html': 'e8f4cf92f9ff144ce9e8c2b4999d96856ed6f62d', 'az-baze-auth/templates/ztl_labor_script.html': 'ffcf635b92d7df0b77af0ba70d5846c0c82a6d78'}
# Owner-supplied production diff, 2026-11-22: server.py additionally imports
# knowledge_catalog and calls knowledge_catalog.register(app). Reconstructing
# those exact two lines from BASE yields this blob. It is never deployed here.
COMPATIBLE_DEPENDENCIES={
    'az-baze-auth/server.py':('8c9a1b45390e273a1e8f1bc8a12f331cf0fbdd47',),
}
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
    mismatches=[]
    for name,old,new in ROWS + [[n,h,h] for n,h in DEPENDENCIES.items()]:
        p=live(name)
        if p.is_symlink() or p.parent.is_symlink(): raise RuntimeError('symlink: '+str(p))
        if not p.parent.is_dir(): raise RuntimeError('target directory missing: '+str(p.parent))
        if old is None:
            if p.exists():mismatches.append('new target already exists: '+str(p))
        else:
            actual=blob(p.read_bytes()) if p.is_file() else 'MISSING'
            accepted=(old,*COMPATIBLE_DEPENDENCIES.get(name,())) if name in DEPENDENCIES else (old,)
            if actual not in accepted:mismatches.append('LIVE_BASE_MISMATCH: '+str(p)+'; expected='+'|'.join(accepted)+'; actual='+actual)
    if mismatches:raise RuntimeError('\n'.join(mismatches))
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

NEW_TABLES=('control_grants','ztl_event_context','control_reviews','control_review_history')
NEW_INDEX='idx_ztl_history_date_actor'

def constant(raw,name='SCHEMA'):
    found=[ast.literal_eval(n.value) for n in ast.parse(raw).body if isinstance(n,ast.Assign)
           and any(isinstance(t,ast.Name) and t.id==name for t in n.targets)]
    if len(found)!=1 or not isinstance(found[0],str):raise RuntimeError('invalid pinned schema')
    return found[0]

def migration_source(data):
    for name,old,new in ROWS:
        if blob(data[name])!=new:raise RuntimeError('migration source hash mismatch')
    labor=live('az-baze-auth/ztl_labor.py').read_bytes()
    if blob(labor)!=DEPENDENCIES['az-baze-auth/ztl_labor.py']:raise RuntimeError('migration dependency hash mismatch')
    sql=constant(data['az-baze-auth/control_center.py'])
    statements=[];pending=''
    for line in sql.splitlines(True):
        pending+=line
        if sqlite3.complete_statement(pending):statements.append(pending.strip());pending=''
    if pending.strip() or not statements or any(not s.startswith(('CREATE TABLE ','CREATE INDEX ')) for s in statements):
        raise RuntimeError('schema must contain only complete CREATE statements')
    with sqlite3.connect(':memory:') as expected:
        expected.executescript(constant(data['az-baze-auth/ztl.py'])+constant(data['az-baze-auth/ztl_control.py'])+constant(labor))
        legacy=schema_objects(expected)
        expected.executescript(sql)
        desired=schema_objects(expected)
    added={n:r for n,r in desired.items() if n not in legacy}
    if {n for n,r in added.items() if r[0]=='table'}!=set(NEW_TABLES) or NEW_INDEX not in added:
        raise RuntimeError('unexpected control schema')
    return dict(statements=statements,legacy=legacy,desired=desired,added=added)

def relevant_schema(conn):
    return {n:r for n,r in schema_objects(conn).items() if r[1].startswith('ztl_') or r[1] in NEW_TABLES}

def control_snapshot(conn):
    objects=relevant_schema(conn)
    return dict(schema=objects,data=data_fingerprint(conn,[n for n,r in objects.items() if r[0]=='table']))

def check_legacy(conn,source):
    if relevant_schema(conn)!=source['legacy']:
        raise RuntimeError('legacy ZTL/control schema mismatch or already installed; no changes applied')
    if any(n in schema_objects(conn) for n in source['added']):raise RuntimeError('new schema name already exists')
    for n,r in source['legacy'].items():
        if r[0]=='table' and conn.execute('PRAGMA foreign_key_check("'+n+'")').fetchone():
            raise RuntimeError('invalid existing ZTL relation')

def prepare(conn,source):
    check_legacy(conn,source)
    before=schema_objects(conn);tables=[n for n,r in before.items() if r[0]=='table']
    original=data_fingerprint(conn,tables);changes=conn.total_changes
    # execute(), not executescript(): schema remains in the caller's transaction.
    for sql in source['statements']:conn.execute(sql)
    if schema_objects(conn)!={**before,**source['added']}:raise RuntimeError('unexpected schema change')
    if data_fingerprint(conn,tables)!=original:raise RuntimeError('existing data changed')
    if conn.total_changes!=changes:raise RuntimeError('unexpected DML')
    for table in NEW_TABLES:
        if conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]:raise RuntimeError('control tables must be empty')
    if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('database integrity failed')
    if not conn.in_transaction:raise RuntimeError('migration transaction lost')

def rollback_migration(backup,expected):
    # Never overwrite the live database with its backup over newer user writes.
    with contextlib.closing(sqlite3.connect((backup/'auth.db').as_uri()+'?mode=ro',uri=True)) as old:
        original=control_snapshot(old)
    conn=sqlite3.connect(DB.as_uri()+'?mode=rw',uri=True)
    try:
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        if control_snapshot(conn)!=expected:raise RuntimeError('control/ZTL data changed after startup; automatic rollback blocked')
        before=schema_objects(conn)
        tables=[n for n,r in before.items() if r[0]=='table' and n not in NEW_TABLES]
        protected=data_fingerprint(conn,tables)
        for table in NEW_TABLES:
            if conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]:raise RuntimeError('control data exists; automatic rollback blocked')
        conn.execute('DROP INDEX '+NEW_INDEX)
        for table in reversed(NEW_TABLES):conn.execute('DROP TABLE '+table)
        if control_snapshot(conn)!=original:raise RuntimeError('control rollback verification failed')
        wanted={n:r for n,r in before.items() if r[1] not in NEW_TABLES and n!=NEW_INDEX}
        if schema_objects(conn)!=wanted or data_fingerprint(conn,tables)!=protected:raise RuntimeError('unrelated data changed during rollback')
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('rollback database integrity failed')
        conn.commit()
    except BaseException:
        conn.rollback();raise
    finally:conn.close()

def configured_owner():
    # Read only the existing owner's setting from the running service; never log its environment.
    pid=subprocess.check_output(['systemctl','show',SERVICE,'-p','MainPID','--value'],text=True).strip()
    if not pid.isdigit() or int(pid)<=0:raise RuntimeError('running service PID unavailable')
    entries=(pathlib.Path('/proc')/pid/'environ').read_bytes().split(b'\0')
    values=[x.split(b'=',1)[1].decode('utf-8').strip().lower() for x in entries if x.startswith(b'AZBAZE_OWNER_EMAIL=')]
    if len(values)!=1 or not values[0]:raise RuntimeError('AZBAZE_OWNER_EMAIL is not configured; no changes applied')
    return values[0]

def options():
    if sys.argv[1:]!=['--apply'] or os.geteuid()!=0:raise RuntimeError('root and --apply required')

def deploy():
    options()
    if not DB.is_file() or DB.is_symlink():raise RuntimeError('database path invalid')
    if BACKUP_ROOT.is_symlink() or not BACKUP_ROOT.is_dir():raise RuntimeError('backup directory invalid')
    run('systemctl','is-active','--quiet',SERVICE);health();check_base()
    owner=configured_owner()
    data={}
    for name,old,new in ROWS:
        raw=download(name)
        if blob(raw)!=new:raise RuntimeError('download hash mismatch: '+name)
        if name.endswith('.py'):compile(raw,name,'exec')
        data[name]=raw
    source=migration_source(data)
    with contextlib.closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON');check_legacy(conn,source)
        if conn.execute('SELECT COUNT(*) FROM users WHERE lower(trim(email))=? AND active=1',(owner,)).fetchone()[0]!=1:
            raise RuntimeError('configured owner account unavailable; no changes applied')
    print('PREFLIGHT=PASS; TARGET_HASHES=PASS; OWNER_ACCESS=CONFIGURED',flush=True)
    backup=pathlib.Path(tempfile.mkdtemp(prefix='pr250-',dir=BACKUP_ROOT))
    print('BACKUP='+str(backup),flush=True)
    stopped=False;mutated=False;committed=False;conn=None;attrs={}
    try:
        stopped=True;run('systemctl','stop',SERVICE);check_base()
        conn=sqlite3.connect(DB.as_uri()+'?mode=rw',uri=True)
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE');check_legacy(conn,source)
        with contextlib.closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True)) as reader:
            with contextlib.closing(sqlite3.connect(str(backup/'auth.db'))) as dest:
                reader.backup(dest)
                original=schema_objects(conn);tables=[n for n,r in original.items() if r[0]=='table']
                if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or schema_objects(dest)!=original:
                    raise RuntimeError('backup schema or integrity mismatch')
                if data_fingerprint(conn,tables)!=data_fingerprint(dest,tables):raise RuntimeError('backup data mismatch')
        for i,(name,old,new) in enumerate(ROWS):
            p=live(name)
            if old is not None:
                attrs[name]=p.stat();shutil.copy2(p,backup/str(i))
                if blob((backup/str(i)).read_bytes())!=old:raise RuntimeError('backup hash mismatch')
            else:
                template='az-baze-auth/templates/ztl.html' if '/templates/' in name else 'az-baze-auth/ztl.py'
                attrs[name]=live(template).stat()
        (backup/'manifest.json').write_text(json.dumps(dict(head=HEAD,base=BASE,files=ROWS,new_tables=NEW_TABLES,
            data_writes='NONE',authorization='owner READY/MERGE/DEPLOY PR250'),ensure_ascii=False,indent=2))
        print('BACKUP_VERIFY=PASS',flush=True)
        prepare(conn,source)
        mutated=True
        for name,old,new in ROWS:install(data[name],live(name),attrs[name])
        for name,old,new in ROWS:
            if blob(live(name).read_bytes())!=new:raise RuntimeError('installed hash mismatch')
        if not conn.in_transaction:raise RuntimeError('migration transaction lost')
        migrated=control_snapshot(conn)
        conn.commit();committed=True
        conn.close();conn=None
        run('systemctl','start',SERVICE);wait_health();run('systemctl','is-active','--quiet',SERVICE)
        with contextlib.closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True)) as verify:
            if relevant_schema(verify)!=source['desired']:raise RuntimeError('post-start schema verification failed')
        print('SCHEMA_INIT=PASS; NEW_EMPTY_TABLES=4; NEW_INDEXES=1; DML_WRITES=0; EXISTING_DATA=UNCHANGED',flush=True)
        print('FILES_VERIFY=PASS; HEALTH=PASS',flush=True)
    except BaseException as error:
        if mutated:run('systemctl','stop',SERVICE)
        if conn is not None:
            if not committed:conn.rollback()
            conn.close();conn=None
        if committed:
            try:rollback_migration(backup,migrated)
            except Exception as rollback_error:
                print('MIGRATION_ROLLBACK=BLOCKED; NEW_FILES_RETAINED; DATABASE_RESTORE=NOT_RUN',flush=True)
                run('systemctl','start',SERVICE);wait_health()
                raise RuntimeError(str(error)+'; '+str(rollback_error)) from rollback_error
        if mutated:
            for i,(name,old,new) in enumerate(ROWS):
                if old is None:live(name).unlink(missing_ok=True)
                else:install((backup/str(i)).read_bytes(),live(name),attrs[name])
            print('ROLLBACK_FILES=PASS; MIGRATION_ROLLBACK=PASS; DATABASE_RESTORE=NOT_RUN',flush=True)
        if stopped:
            run('systemctl','start',SERVICE);wait_health();print('PREVIOUS_SERVICE_HEALTH=PASS',flush=True)
        raise
    finally:
        if conn is not None:conn.close()

TIMERS=('az-baze-advertising-hourly.timer','az-baze-advertising-retry.timer')
STATE=pathlib.Path('/var/lib/az-baze/advertising-retry.json')
GATE=pathlib.Path(str(STATE)+'.lock')
def main():
    options()
    if any(p.is_symlink() or not p.is_file() for p in (STATE,GATE)):raise RuntimeError('retry state missing or invalid')
    if LOCK.is_symlink():raise RuntimeError('installer lock is a symlink')
    check_base();health()
    for timer in TIMERS:run('systemctl','is-active','--quiet',timer)
    with LOCK.open('a') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        paused=[]
        try:
            for timer in TIMERS:paused.append(timer);run('systemctl','stop',timer)
            with GATE.open('r') as gate:
                deadline=time.monotonic()+900
                while True:
                    try:fcntl.flock(gate,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:
                        if time.monotonic()>=deadline:raise RuntimeError('sync still running; retry installation later')
                        time.sleep(1)
                state_before=STATE.read_bytes()
                try:deploy()
                finally:
                    if STATE.read_bytes()!=state_before:raise RuntimeError('retry state changed unexpectedly')
        finally:
            for timer in paused:run('systemctl','start',timer)
        for timer in TIMERS:run('systemctl','is-active','--quiet',timer)
        print('PR250_PRODUCTION_DEPLOY=PASS; FILES='+str(len(ROWS)))
        print('HEAD='+HEAD)
        print('HEALTH=PASS; DB_SCHEMA=FOUR_EMPTY_TABLES; DB_DATA_WRITES_BY_INSTALLER=NONE; EXISTING_DATA=UNCHANGED; ORDERS_STOCK_PAYROLL=UNCHANGED; BACKFILL=NOT_RUN; IMPORTS=NOT_RUN; TIMER_CONFIGURATION=UNCHANGED; RETRY_STATE=UNCHANGED')
        print('HOURLY_TIMER=ACTIVE; RETRY_TIMER=ACTIVE')
if __name__=='__main__':
    try:main()
    except Exception as e:print('STOP='+str(e),flush=True);sys.exit(1)

