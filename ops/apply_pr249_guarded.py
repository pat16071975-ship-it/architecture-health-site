#!/usr/bin/env python3
"""PR249: guarded labor schema; optional explicit, idempotent technician import."""
import ast,contextlib,fcntl,re,types
import hashlib,json,os,pathlib,shutil,sqlite3,subprocess,sys,tempfile,time,urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
HEAD='0815348e0d03492595fc827851869a8ef06ed8c8'
BASE='0892b78b49086edff3c7d81b63b1a7c55faaa760'
ROWS=[['az-baze-auth/templates/ztl.html', '5bbc774829b218d0c030107d37d289f1117f8de3', '30b3445e9e6317316393b040c0cd608efb936523'], ['az-baze-auth/templates/ztl_labor_fields.html', None, 'e8f4cf92f9ff144ce9e8c2b4999d96856ed6f62d'], ['az-baze-auth/templates/ztl_labor_script.html', None, 'ffcf635b92d7df0b77af0ba70d5846c0c82a6d78'], ['az-baze-auth/templates/ztl_payroll_orders.html', '098142d252377e901d93a48de217523931526658', '84499124e77f532d7b926f3d1f59371bb3737dbc'], ['az-baze-auth/templates/ztl_products.html', '95a3c3da91f4f1cc4a2d99496df86cc4ae2192ea', '81a0c3bf1d6b3ae28df24952baa9e422a53ac322'], ['az-baze-auth/ztl.py', 'fe23781d9e7fff609ed8c80a49f115ff9d3f6248', '93f4de54fcc883e9571a8d6390ebc443a33d93a2'], ['az-baze-auth/ztl_control.py', '2a7d6726c3096a4db0f2fdc53c707cb3d6b1679a', '4114bee55e60f417338b66a34921b858b07ed3d8'], ['az-baze-auth/ztl_labor.py', None, '04ec70231b9346dbc3f53ca61ab2b27c11781fc1']]
DEPENDENCIES={'az-baze-auth/app.py': 'ca05d3854b37c2e227fb2101fd83d056718b5088', 'az-baze-auth/server.py': '7d186761a727baa1fe50707c80b3d9ab154a3b96', 'az-baze-auth/medical_supplies.py': '0c8866461ab5a75561154fd977665673822b783f', 'az-baze-auth/section_navigation.py': '949987e39f768be9e208d9263e7e133e242aa501', 'az-baze-auth/templates/base.html': '80b5bdb1ee05197d8eb9235b831bae9d14e810c7', 'az-baze-auth/templates/ztl_menu.html': '8983b485441f13b5fa74949220824a82b4028ae8', 'az-baze-auth/templates/ztl_economics.html': '8ee1f06a9bee2dd92195edc597392ffa70a894bb', 'az-baze-auth/templates/ztl_control.html': 'bee2f008303a9d5ed6c14f289d3be8d70aceb16e', 'az-baze-auth/templates/ztl_explanation.html': '3107fbc931873959159272ff8c58606492223d34'}
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


def extract(raw, names, path, scope=None):
    nodes=[];found=set()
    for node in ast.parse(raw).body:
        name=node.name if isinstance(node,(ast.FunctionDef,ast.ClassDef)) else node.targets[0].id if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) else None
        if name in names:
            if name in found:raise RuntimeError('duplicate migration source')
            found.add(name);nodes.append(node)
    if found!=set(names):raise RuntimeError('migration source incomplete: '+path)
    scope={} if scope is None else scope
    exec(compile(ast.Module(body=nodes,type_ignores=[]),path,'exec'),scope)
    return scope

def migration_source(data):
    # Execute only the pinned schema and explicit seed functions, not app startup.
    for name in ('az-baze-auth/ztl.py','az-baze-auth/ztl_control.py','az-baze-auth/ztl_labor.py'):
        if blob(data[name])!=next(new for n,old,new in ROWS if n==name):
            raise RuntimeError('migration source hash mismatch')
    medical=live('az-baze-auth/medical_supplies.py').read_bytes()
    if blob(medical)!=DEPENDENCIES['az-baze-auth/medical_supplies.py']:
        raise RuntimeError('migration helper hash mismatch')
    common=extract(medical,{'text','Conflict'},'medical_supplies.py')
    ztl_scope=dict(common,hashlib=hashlib,json=json,datetime=datetime,ZoneInfo=ZoneInfo)
    extract(data['az-baze-auth/ztl.py'],{'SCHEMA','KINDS','log','catalog_entry'},'ztl.py',ztl_scope)
    control=extract(data['az-baze-auth/ztl_control.py'],{'SCHEMA'},'ztl_control.py')
    labor=extract(data['az-baze-auth/ztl_labor.py'],{'SCHEMA','TECHNICIANS','init_schema','seed_technicians'},'ztl_labor.py',dict(common,re=re))
    module=types.ModuleType('ztl');module.catalog_entry=ztl_scope['catalog_entry']
    seed=labor['seed_technicians']
    def explicit_seed(conn):
        previous=sys.modules.get('ztl');sys.modules['ztl']=module
        try:return seed(conn,0)  # Explicit system operation; authorization recorded in deployment manifest.
        finally:
            if previous is None:sys.modules.pop('ztl',None)
            else:sys.modules['ztl']=previous
    labor['seed']=explicit_seed
    with sqlite3.connect(':memory:') as expected:
        expected.executescript(ztl_scope['SCHEMA']+control['SCHEMA'])
        labor['legacy']=schema_objects(expected)
        labor['init_schema'](expected)
        labor['desired']=schema_objects(expected)
    return labor

def ztl_snapshot(conn):
    objects={n:r for n,r in schema_objects(conn).items() if r[1].startswith('ztl_')}
    return {'schema':objects,'data':data_fingerprint(conn,[n for n,r in objects.items() if r[0]=='table'])}

def check_legacy(conn,source):
    if ztl_snapshot(conn)['schema']!=source['legacy']:
        raise RuntimeError('legacy ZTL schema mismatch or already installed; no changes applied')
    for n,r in source['legacy'].items():
        if r[0]=='table' and conn.execute('PRAGMA foreign_key_check("'+n+'")').fetchone():
            raise RuntimeError('invalid existing ZTL relation')

def prepare(conn,source,add_technicians):
    check_legacy(conn,source)
    before=schema_objects(conn)
    protected=[n for n,r in before.items() if r[0]=='table' and n not in ('ztl_names','ztl_history')]
    original=data_fingerprint(conn,protected)
    old_names={r['id']:tuple(r) for r in conn.execute('SELECT * FROM ztl_names')}
    old_history={r['id']:tuple(r) for r in conn.execute('SELECT * FROM ztl_history')}
    changes=conn.total_changes
    source['init_schema'](conn)
    # Seed is an optional, separately authorized operation, never schema init.
    employees=source['seed'](conn) if add_technicians else []
    created={r['id']:r['name'] for r in employees if r['created']}
    expected_schema=dict(before,ztl_labor_splits=source['desired']['ztl_labor_splits'])
    if schema_objects(conn)!=expected_schema:raise RuntimeError('unexpected schema change')
    names={r['id']:tuple(r) for r in conn.execute('SELECT * FROM ztl_names')}
    if names!={**old_names,**{iid:(iid,'employee',name,1) for iid,name in created.items()}}:
        raise RuntimeError('unexpected employee change')
    history={r['id']:tuple(r) for r in conn.execute('SELECT * FROM ztl_history')}
    if any(history.get(i)!=r for i,r in old_history.items()):raise RuntimeError('existing history changed')
    added_history={i:r for i,r in history.items() if i not in old_history}
    if len(added_history)!=len(created):raise RuntimeError('employee history count mismatch')
    seen=set()
    for row in added_history.values():
        iid,entity,eid,action,actor,changed,before_json,after_json=row
        if entity!='ztl_names' or eid not in created or eid in seen or action!='Создание' or actor!=0 or before_json is not None or not changed or json.loads(after_json)!={'name':created[eid],'kind':'employee'}:
            raise RuntimeError('unexpected employee history')
        seen.add(eid)
    if add_technicians:
        if len(employees)!=5 or tuple(r['name'] for r in employees)!=source['TECHNICIANS'] or len({r['id'] for r in employees})!=5:
            raise RuntimeError('technician catalogue verification failed')
        normalize=lambda s:' '.join(s.casefold().replace('ё','е').split())
        for r in employees:
            saved=names.get(r['id'])
            if not saved or saved[1]!='employee' or normalize(saved[2])!=normalize(r['name']):
                raise RuntimeError('technician catalogue verification failed')
    if data_fingerprint(conn,protected)!=original:raise RuntimeError('existing data changed')
    if conn.execute('SELECT COUNT(*) FROM ztl_labor_splits').fetchone()[0]:raise RuntimeError('labor table must be empty')
    if conn.total_changes-changes!=2*len(created):raise RuntimeError('unexpected DML')
    if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('database integrity failed')
    if not conn.in_transaction:raise RuntimeError('migration transaction lost')
    return {'employees':employees,'added_ids':list(created),'history_ids':list(added_history)}

def rollback_migration(backup,expected,changes):
    # Remove only this installation's empty table and employee/history inserts.
    # Never replace the working DB with a backup over newer unrelated writes.
    with contextlib.closing(sqlite3.connect((backup/'auth.db').as_uri()+'?mode=ro',uri=True)) as old:
        original=ztl_snapshot(old)
    conn=sqlite3.connect(DB.as_uri()+'?mode=rw',uri=True)
    try:
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        if ztl_snapshot(conn)!=expected:
            raise RuntimeError('ZTL data changed after startup; automatic rollback blocked')
        before=schema_objects(conn)
        protected=[n for n,r in before.items() if r[0]=='table' and not n.startswith('ztl_')]
        data_before=data_fingerprint(conn,protected)
        conn.execute('DROP TABLE ztl_labor_splits')
        conn.executemany('DELETE FROM ztl_history WHERE id=?',[(i,) for i in changes['history_ids']])
        conn.executemany('DELETE FROM ztl_names WHERE id=?',[(i,) for i in changes['added_ids']])
        if ztl_snapshot(conn)!=original:raise RuntimeError('ZTL rollback verification failed')
        if schema_objects(conn)!={n:r for n,r in before.items() if n!='ztl_labor_splits'} or data_fingerprint(conn,protected)!=data_before:
            raise RuntimeError('unrelated data changed during rollback')
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('rollback database integrity failed')
        conn.commit()
    except BaseException:
        conn.rollback();raise
    finally:conn.close()
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


def options():
    if sys.argv[1:] not in (['--apply'],['--apply','--add-technicians']) or os.geteuid()!=0:
        raise RuntimeError('root and --apply required; employee import additionally requires --add-technicians')
    return '--add-technicians' in sys.argv[1:]

def deploy():
    add_technicians=options()
    if not DB.is_file() or DB.is_symlink():raise RuntimeError('database path invalid')
    if BACKUP_ROOT.is_symlink() or not BACKUP_ROOT.is_dir():raise RuntimeError('backup directory invalid')
    run('systemctl','is-active','--quiet',SERVICE);health();check_base()
    data={}
    for name,old,new in ROWS:
        raw=download(name)
        if blob(raw)!=new:raise RuntimeError('download hash mismatch: '+name)
        if name.endswith('.py'):compile(raw,name,'exec')
        data[name]=raw
    source=migration_source(data)
    with contextlib.closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON');check_legacy(conn,source)
    print('PREFLIGHT=PASS; TARGET_HASHES=PASS',flush=True)
    backup=pathlib.Path(tempfile.mkdtemp(prefix='pr249-',dir=BACKUP_ROOT))
    print('BACKUP='+str(backup),flush=True)
    stopped=False;mutated=False;committed=False;conn=None;attrs={}
    try:
        stopped=True;run('systemctl','stop',SERVICE);check_base()
        conn=sqlite3.connect(DB.as_uri()+'?mode=rw',uri=True);conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE');check_legacy(conn,source)
        with contextlib.closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True)) as reader:
            with contextlib.closing(sqlite3.connect(str(backup/'auth.db'))) as dest:
                reader.backup(dest)
                original=schema_objects(conn)
                tables=[n for n,r in original.items() if r[0]=='table']
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
        manifest={'head':HEAD,'base':BASE,'files':ROWS,'add_technicians':add_technicians,'history_actor_id':0,'actor_kind':'owner-authorized system installation'}
        (backup/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
        print('BACKUP_VERIFY=PASS',flush=True)
        changes=prepare(conn,source,add_technicians)
        manifest['changes']=changes
        (backup/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
        mutated=True
        for name,old,new in ROWS:install(data[name],live(name),attrs[name])
        for name,old,new in ROWS:
            if blob(live(name).read_bytes())!=new:raise RuntimeError('installed hash mismatch')
        if not conn.in_transaction:raise RuntimeError('migration transaction lost')
        migrated=ztl_snapshot(conn)
        conn.commit();committed=True
        conn.close();conn=None  # Real bootstrap needs the writer lock released.
        run('systemctl','start',SERVICE);wait_health();run('systemctl','is-active','--quiet',SERVICE)
        with contextlib.closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True)) as verify:
            for employee in changes['employees']:
                row=verify.execute("SELECT name FROM ztl_names WHERE id=? AND kind='employee'",(employee['id'],)).fetchone()
                normalize=lambda s:' '.join(s.casefold().replace('ё','е').split())
                if not row or normalize(row[0])!=normalize(employee['name']):raise RuntimeError('post-start employee verification failed')
        print('SCHEMA_INIT=PASS; NEW_EMPTY_TABLES=1; EXISTING_DATA=UNCHANGED',flush=True)
        if add_technicians:
            print('TECHNICIANS=5; ADDED='+str(len(changes['added_ids']))+'; REUSED='+str(5-len(changes['added_ids']))+'; HISTORY=PASS',flush=True)
        else:print('TECHNICIAN_IMPORT=NOT_RUN',flush=True)
        print('FILES_VERIFY=PASS; HEALTH=PASS',flush=True)
    except BaseException as error:
        if mutated:run('systemctl','stop',SERVICE)
        if conn is not None:
            if not committed:conn.rollback()
            conn.close();conn=None
        if committed:
            try:rollback_migration(backup,migrated,changes)
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
            run('systemctl','start',SERVICE);wait_health()
            print('PREVIOUS_SERVICE_HEALTH=PASS',flush=True)
        raise
    finally:
        if conn is not None:conn.close()

TIMERS=('az-baze-advertising-hourly.timer','az-baze-advertising-retry.timer')
STATE=pathlib.Path('/var/lib/az-baze/advertising-retry.json')
GATE=pathlib.Path(str(STATE)+'.lock')
def main():
    add_technicians=options()
    if any(p.is_symlink() or not p.is_file() for p in (STATE,GATE)):raise RuntimeError('retry state missing or invalid')
    if LOCK.is_symlink():raise RuntimeError('installer lock is a symlink')
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
                try:deploy()
                finally:
                    if STATE.read_bytes()!=state_before:raise RuntimeError('retry state changed unexpectedly')
        finally:
            for timer in paused:run('systemctl','start',timer)
        for timer in TIMERS:run('systemctl','is-active','--quiet',timer)
        print('PR249_PRODUCTION_DEPLOY=PASS; FILES='+str(len(ROWS)))
        print('HEAD='+HEAD)
        writes='FIVE_TECHNICIANS_AND_CREATION_HISTORY_ONLY' if add_technicians else 'NONE'
        print('HEALTH=PASS; DB_SCHEMA=ONE_EMPTY_TABLE; DB_DATA_WRITES_BY_INSTALLER='+writes+'; EXISTING_DATA=UNCHANGED; ORDERS_STOCK_PAYROLL=UNCHANGED; BACKFILL=NOT_RUN; IMPORTS='+('EXPLICIT_TECHNICIANS_ONLY' if add_technicians else 'NOT_RUN')+'; TIMER_CONFIGURATION=UNCHANGED; RETRY_STATE=UNCHANGED')
        print('HOURLY_TIMER=ACTIVE; RETRY_TIMER=ACTIVE')
if __name__=='__main__':
    try:main()
    except Exception as e:print('STOP='+str(e),flush=True);sys.exit(1)
