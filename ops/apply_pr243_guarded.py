#!/usr/bin/env python3
"""PR243: audited daily ledger files, four tables and folder metadata only. No stock import/backfill."""
import ast,re,fcntl
import hashlib,json,os,pathlib,shutil,sqlite3,subprocess,sys,tempfile,time,urllib.request
HEAD='75367ab311c5fd7cf4505604643d62dc0b58e793'
BASE='f49d4c741039a7422fe91eb7b859f7f538e40a41'
ROWS=[['az-baze-auth/medical_supplies.py', '091fb25b8583389f19c7687cfb13a45746474c3e', '0c8866461ab5a75561154fd977665673822b783f'], ['az-baze-auth/medical_supplies_daily.py', None, 'a54998c954084985fe1866899e8940514d1486df'], ['az-baze-auth/templates/medical_supplies.html', '114a30c219d0adb71330b67ade002286a537c4bd', 'fe43bd742106e8af608a1965ffe1ceeeab80413e'], ['az-baze-auth/templates/medical_supplies_daily.html', None, 'e1a22d6b9941b3cf24bbffda0808728a7a3328c1'], ['az-baze-auth/templates/medical_supplies_error.html', None, 'f40025191a697c7adcece11fd6912ab3d57b5574'], ['az-baze-auth/templates/medical_supplies_upload.html', None, '748374fbd8511e9bd0aca636e9466ca3fe49baba']]
TABLES=('med_supply_folders','med_supply_daily_items','med_supply_days','med_supply_folder_history')
OLD_TABLES=('users','med_supply_items','med_supply_periods','med_supply_coverage','med_supply_history','med_supply_stock_imports')
FOLDER_ROWS=31
def schema_plan(data):
    tree=ast.parse(data['az-baze-auth/medical_supplies_daily.py'])
    values={}
    for key in ('SCHEMA','FOLDERS'):
        nodes=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==key for t in n.targets)]
        if len(nodes)!=1:raise RuntimeError('schema source mismatch')
        values[key]=ast.literal_eval(nodes[0].value)
    statements=[s.strip() for s in values['SCHEMA'].split(';') if s.strip()]
    seen=[]
    for sql in statements:
        match=re.fullmatch(r'CREATE TABLE IF NOT EXISTS ('+'|'.join(TABLES)+r')\s*\([\s\S]*\)',sql)
        if not match:raise RuntimeError('unexpected schema statement')
        seen.append(match[1])
    if sorted(seen)!=sorted(TABLES):raise RuntimeError('schema objects mismatch')
    if hashlib.sha256('\n'.join(statements).encode()).hexdigest()!='97ab4e73df4c8ec824001df8dd8389ad10092976182aa4946dd98330835d1669':
        raise RuntimeError('new schema definition changed')
    if hashlib.sha256(json.dumps(values['FOLDERS'],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()!='2ee2d7f8b1c29882556f451263702caaad99623c6bbf1c202234ac0bc2819dd9':
        raise RuntimeError('folder metadata changed')
    seed=[]
    for name,children in values['FOLDERS']:
        fid=len(seed)+1;seed.append((fid,None,name,1))
        for name in children:seed.append((len(seed)+1,fid,name,1))
    if len(seed)!=FOLDER_ROWS:raise RuntimeError('folder count changed')
    return statements,seed

def check_schema_absent():
    with sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True) as conn:
        names={r[0] for r in conn.execute('SELECT name FROM sqlite_master')}
        if names.intersection(TABLES):raise RuntimeError('new medical supplies schema already exists; stop for review')
        if not set(OLD_TABLES).issubset(names):raise RuntimeError('existing medical supplies schema missing')
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
def initialize_tables(plan):
    statements,seed=plan
    with sqlite3.connect('file:'+str(DB)+'?mode=rw',uri=True) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        before=schema_objects(conn)
        if set(before).intersection(TABLES):raise RuntimeError('new medical supplies schema already exists')
        if not set(OLD_TABLES).issubset(before):raise RuntimeError('existing medical supplies schema missing')
        old_tables=[name for name,row in before.items() if row[0]=='table']
        original=data_fingerprint(conn,old_tables)
        with sqlite3.connect(':memory:') as expected_db:
            for statement in statements:expected_db.execute(statement)
            expected_db.executemany('INSERT INTO med_supply_folders(id,parent_id,name,version) VALUES(?,?,?,?)',seed)
            expected=schema_objects(expected_db)
            expected_data=data_fingerprint(expected_db,TABLES)
        for statement in statements:conn.execute(statement)
        def metadata_only(action,table,column,database,trigger):
            if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
                return sqlite3.SQLITE_OK if action==sqlite3.SQLITE_INSERT and table=='med_supply_folders' else sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        conn.set_authorizer(metadata_only)
        try:conn.executemany('INSERT INTO med_supply_folders(id,parent_id,name,version) VALUES(?,?,?,?)',seed)
        finally:conn.set_authorizer(None)
        if schema_objects(conn)!={**before,**expected}:raise RuntimeError('unexpected schema change')
        if data_fingerprint(conn,TABLES)!=expected_data:raise RuntimeError('new table data differs from folder metadata')
        if conn.total_changes!=FOLDER_ROWS:raise RuntimeError('unexpected DML')
        if data_fingerprint(conn,old_tables)!=original:raise RuntimeError('existing records changed')
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('database integrity')
    print('SCHEMA_INIT=PASS; NEW_TABLES=4; NEW_EMPTY_TABLES=3; NEW_INDEXES=3; FOLDER_ROWS='+str(FOLDER_ROWS)+'; EXISTING_DATA=UNCHANGED',flush=True)
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
    for name,old,new in ROWS:
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
    plan=schema_plan(data)
    check_schema_absent()
    print('PREFLIGHT=PASS; TARGET_HASHES=PASS',flush=True)
    backup=pathlib.Path(tempfile.mkdtemp(prefix='pr243-',dir=BACKUP_ROOT))
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
            attrs[name]=p.stat() if old else live('az-baze-auth/templates/medical_supplies.html' if name.endswith('.html') else 'az-baze-auth/medical_supplies.py').stat()
            if old:
                shutil.copy2(p,backup/str(i))
                if blob((backup/str(i)).read_bytes())!=old: raise RuntimeError('backup hash mismatch')
        (backup/'manifest.json').write_text(json.dumps({'head':HEAD,'base':BASE,'files':ROWS,'new_tables':TABLES,'folder_rows':FOLDER_ROWS},indent=2))
        print('BACKUP_VERIFY=PASS',flush=True)
        mutated=True
        for name,old,new in ROWS: install(data[name],live(name),attrs[name])
        for name,old,new in ROWS:
            if blob(live(name).read_bytes())!=new: raise RuntimeError('installed hash mismatch')
        initialize_tables(plan)
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
        print('PR243_PRODUCTION_DEPLOY=PASS; FILES='+str(len(ROWS)))
        print('HEAD='+HEAD)
        print('HEALTH=PASS; DB_SCHEMA=FOUR_TABLES; DB_DATA_WRITES_BY_INSTALLER=FOLDER_METADATA_ONLY; EXISTING_DATA=UNCHANGED; AUTO_CLASSIFICATION=NOT_RUN; BACKFILL=NOT_RUN; IMPORTS=NOT_RUN; TIMER_CONFIGURATION=UNCHANGED; RETRY_STATE=UNCHANGED')
        print('HOURLY_TIMER=ACTIVE; RETRY_TIMER=ACTIVE')
if __name__=='__main__':
    try: main()
    except Exception as e: print('STOP='+str(e),flush=True); sys.exit(1)
