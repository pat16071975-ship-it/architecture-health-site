#!/usr/bin/env python3
"""PR240: tested ZTL registry/order files and two empty product tables. No seed/import/backfill."""
import ast,re,fcntl
import hashlib,json,os,pathlib,shutil,sqlite3,subprocess,sys,tempfile,time,urllib.request
HEAD='a0b65217e0c31715c840379c8b8246a48ea0e84c'
MERGE='1b0504df208d5dd00aa9a3de7da3fccce25753e0'
ROWS=[['az-baze-auth/ztl.py', '398671c27057193ca092f5643309b3eb2061c5ab', 'fe23781d9e7fff609ed8c80a49f115ff9d3f6248'], ['az-baze-auth/ztl_control.py', '9601c922fbde7cbf93aa1c163015ae89ec37ddfd', '3a9bf45049442c53f798ebc0d73ad7a3680cfe93'], ['az-baze-auth/templates/ztl.html', 'f9f97b9204b2fc175945110c5a017c58bee7e561', '5bbc774829b218d0c030107d37d289f1117f8de3'], ['az-baze-auth/templates/ztl_control.html', 'b9bc68c0a93f6126df5ed0929b72b5a2674affe8', 'bee2f008303a9d5ed6c14f289d3be8d70aceb16e'], ['az-baze-auth/templates/ztl_products.html', None, '19add33338c42fe5922194ae7c18b9ccff8db379']]
TABLES=('ztl_product_archive','ztl_product_prices')
BASE_SCHEMA_SHA256='48ac99224b73aea9674924725292d66354a3884061cdf45ef94aeee3e7867c8d'
OLD_TABLES=('ztl_parties','ztl_names','ztl_orders','ztl_payments','ztl_allocations','ztl_costs','ztl_history','ztl_items','ztl_cards','ztl_receipts','ztl_moves','ztl_production','ztl_inventories','ztl_cost_reconciliation')
def schema_statements(data):
    tree=ast.parse(data['az-baze-auth/ztl_control.py'])
    nodes=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SCHEMA' for t in n.targets)]
    if len(nodes)!=1:raise RuntimeError('schema source mismatch')
    statements=[s.strip() for s in ast.literal_eval(nodes[0].value).split(';') if s.strip()]
    selected=[]; previous=[]; seen=[]
    for sql in statements:
        match=re.fullmatch(r'CREATE TABLE IF NOT EXISTS ('+'|'.join(TABLES)+r')\s*\([\s\S]*\)',sql)
        if match:
            seen.append(match[1]);selected.append(sql)
        else:previous.append(sql)
    if sorted(seen)!=sorted(TABLES):raise RuntimeError('schema objects mismatch')
    if hashlib.sha256('\n'.join(previous).encode()).hexdigest()!=BASE_SCHEMA_SHA256:
        raise RuntimeError('existing schema statements changed')
    return selected

def check_schema_absent():
    with sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True) as conn:
        names={r[0] for r in conn.execute('SELECT name FROM sqlite_master')}
        if names.intersection(TABLES):raise RuntimeError('new ZTL schema already exists; stop for review')
        if not set(OLD_TABLES).issubset(names):raise RuntimeError('existing ZTL schema missing')
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
def initialize_empty_tables(statements):
    with sqlite3.connect('file:'+str(DB)+'?mode=rw',uri=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        before=schema_objects(conn)
        if set(before).intersection(TABLES):raise RuntimeError('new ZTL schema already exists')
        if not set(OLD_TABLES).issubset(before):raise RuntimeError('existing ZTL schema missing')
        old_tables=[name for name,row in before.items() if row[0]=='table']
        original=data_fingerprint(conn,old_tables)
        with sqlite3.connect(':memory:') as expected_db:
            for statement in statements:expected_db.execute(statement)
            expected=schema_objects(expected_db)
        for statement in statements:conn.execute(statement)
        if schema_objects(conn)!={**before,**expected}:raise RuntimeError('unexpected schema change')
        for name in TABLES:
            if conn.execute('SELECT count(*) FROM '+name).fetchone()[0]:raise RuntimeError('new table is not empty')
        if conn.total_changes:raise RuntimeError('unexpected DML')
        if data_fingerprint(conn,old_tables)!=original:raise RuntimeError('existing records changed')
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('database integrity')
    print('SCHEMA_INIT=PASS; NEW_EMPTY_TABLES=2; NEW_INDEXES=0; DML_WRITES=0; EXISTING_DATA=UNCHANGED',flush=True)
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
    statements=schema_statements(data)
    check_schema_absent()
    print('PREFLIGHT=PASS; TARGET_HASHES=PASS',flush=True)
    backup=pathlib.Path(tempfile.mkdtemp(prefix='pr240-',dir=BACKUP_ROOT))
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
            attrs[name]=p.stat() if old else live('az-baze-auth/templates/ztl.html' if name.endswith('.html') else 'az-baze-auth/ztl.py').stat()
            if old:
                shutil.copy2(p,backup/str(i))
                if blob((backup/str(i)).read_bytes())!=old: raise RuntimeError('backup hash mismatch')
        (backup/'manifest.json').write_text(json.dumps({'head':HEAD,'merge':MERGE,'files':ROWS,'new_tables':TABLES},indent=2))
        print('BACKUP_VERIFY=PASS',flush=True)
        mutated=True
        for name,old,new in ROWS: install(data[name],live(name),attrs[name])
        for name,old,new in ROWS:
            if blob(live(name).read_bytes())!=new: raise RuntimeError('installed hash mismatch')
        initialize_empty_tables(statements)
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
        print('PR240_PRODUCTION_DEPLOY=PASS; FILES='+str(len(ROWS)))
        print('HEAD='+HEAD)
        print('HEALTH=PASS; DB_SCHEMA=TWO_EMPTY_TABLES; DB_DATA_WRITES_BY_INSTALLER=NONE; BACKFILL=NOT_RUN; IMPORTS=NOT_RUN; TIMER_CONFIGURATION=UNCHANGED; RETRY_STATE=UNCHANGED')
        print('HOURLY_TIMER=ACTIVE; RETRY_TIMER=ACTIVE')
if __name__=='__main__':
    try: main()
    except Exception as e: print('STOP='+str(e),flush=True); sys.exit(1)
