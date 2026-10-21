#!/usr/bin/env python3
"""Owner-approved five-file installation. No app import, DML, migration, or DB restore.

Only the five pinned files are installed. The existing application service alone
is stopped/started. Live SQLite is opened mode=ro/query_only. A verified private
SQLite backup and exact file backups remain on the server. On technical failure,
only files changed by this invocation are rolled back; new DB activity is never
overwritten. SIGKILL/power loss cannot be automatically recovered by this process.
"""
from __future__ import annotations
import argparse
import base64
import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

SOURCE = '16d4dd615b2b1ae3d830ca15910d737cf6e87ee2'
REPO = 'pat16071975-ship-it/architecture-health-site'
TOKEN = 'REPORT-CONSISTENCY-16D4DD61'
SERVICE = 'az-baze-auth'
HEALTH = 'http://127.0.0.1:8001/health'
# Repository path: (old Git blob, new Git blob, new SHA256).
FILES = {
 'az-baze-auth/daily_upload_core.py': ('c0c2faee2474d8b8a2f678898bbe0ec7053cfa37','7a58de237753dff1d5e72a11c14744d68c39a324','caca85f608f60622c290d0290d2825109243150973ecc219b8e3f7a397b19b7e'),
 'az-baze-auth/templates/uploads.html': ('be36446bbeaa82fc9c27769a8cabb24978ec0d36','fc7913c0ab812c6709a778aa706d969e438d3c93','1aac65d61e9024886e40c7cc4a9a6882d535f91f7dcb6a63693d284d6ed00497'),
 'az-baze-auth/upload_history.py': (None,'1709e9e984f0f521e081061b45b564a12d84f244','f3d7453779e443d86e359ee239344cde1ee39ed13f95b620426c766551dafb5d'),
 'reports/period-view.js': ('042d6574edc26fecc5c42893684175e858f0d59b','a3075abb77f562e8eddbde11364049c4362261e2','f0da1838c1cc61a20f51e9d8c1fbe4ef10aa05c4b4c8c493dd0bf53dff39eab3'),
 'reports/index.html': ('a6febb1769603e02e1433fac1402d9796e4b9632','ebf7ae4d4e5e121f20cff2302206e9a5fc25e350','10fe22e110aa3e8ac02f33060be7b2014adf50586f0b5eaef479c1f5260d3906'),
}
DEPENDENCIES = {
 'report_storage.py':'367592919431997255862ed179a5c6a4f93703ad',
 'paid_services.py':'76c1a33de8a711f85139a21aa5f66a48ea96910f',
 'paid_services_upload.py':'d2e682540f831d5adafd9b19e753a01d53749349',
 'upload_reconcile.py':'bce8384dbb8d4b6eacf98ad5d4d099705bf39914',
 'daily_upload.py':'b767890804e1f61a247476f13f73c01e20628f39',
 'ident_import.py':'e9950b781b0f4ebdfe044860b948813938a8089d',
}
COLUMNS = {
 'users':{'id','full_name'},
 'upload_log':{'id','data_date','status','details','completed_filename','services_filename','uploaded_by','created_at'},
 'audit_log':{'id','actor_user_id','action','details','created_at'},
}

class Stop(RuntimeError):
    pass

def require(ok, label):
    if not ok:
        raise Stop(label)

def emit(text):
    print(text, flush=True)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()

def plain(p, optional=False, directory=False):
    require(p.is_absolute(), 'NONABSOLUTE_PATH')
    require(not any(x.is_symlink() for x in (p,*p.parents)), 'SYMLINK_PATH')
    if optional and not p.exists():
        return p
    require(p.is_dir() if directory else p.is_file(), 'MISSING_OR_NONREGULAR_PATH:' + p.name)
    if not directory:
        require(stat.S_ISREG(p.stat().st_mode), 'NONREGULAR_FILE')
    return p

def read_code(p, optional=False):
    plain(p, optional=optional)
    if not p.exists():
        return None
    s = p.stat()
    require(s.st_size <= 2*1024*1024 and s.st_nlink == 1, 'UNEXPECTED_SOURCE_FILE:' + p.name)
    raw = p.read_bytes()
    a = p.stat()
    require((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==(a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns), 'SOURCE_CHANGED_DURING_READ')
    return raw

def meta(p):
    plain(p)
    s = p.stat()
    return {'uid':s.st_uid,'gid':s.st_gid,'mode':stat.S_IMODE(s.st_mode),
            'mtime_ns':s.st_mtime_ns,
            'xattrs':{key:base64.b64encode(os.getxattr(p,key)).decode() for key in os.listxattr(p)}}

def rights(m):
    return {k:m[k] for k in ('uid','gid','mode','xattrs')}

def sync_dir(p):
    fd = os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)

def atomic_write(p,raw,m):
    """Replace in the same filesystem, preserving owner/mode/ACL/xattrs."""
    plain(p.parent,directory=True); plain(p,optional=True)
    fd,name = tempfile.mkstemp(prefix='.az-consistency-',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(raw); f.flush()
            os.fchown(f.fileno(),m['uid'],m['gid'])
            os.fchmod(f.fileno(),m['mode'])
            for key in os.listxattr(name):
                if key not in m['xattrs']: os.removexattr(name,key)
            for key,value in m['xattrs'].items(): os.setxattr(name,key,base64.b64decode(value))
            os.utime(name,ns=(m['mtime_ns'],m['mtime_ns']))
            os.fsync(f.fileno())
        require(meta(Path(name))==m,'STAGED_METADATA_MISMATCH')
        os.replace(name,p); sync_dir(p.parent)
    finally:
        if os.path.lexists(name): os.unlink(name)

def private_json(p,value):
    with p.open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=True,sort_keys=True,indent=2)
        f.write('\n');f.flush();os.fsync(f.fileno())
    os.chmod(p,0o600)

@contextlib.contextmanager
def ro_db(p):
    plain(p)
    c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=8)
    deadline=time.monotonic()+90
    c.set_progress_handler(lambda: int(time.monotonic()>deadline),50000)
    try:
        c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        require(c.execute('PRAGMA query_only').fetchone()==(1,),'READONLY_NOT_SET')
        yield c
    finally: c.close()

def db_state(c):
    require(c.execute('PRAGMA quick_check').fetchall()==[('ok',)],'DB_QUICK_CHECK')
    schema=list(c.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'))
    tables={}
    for name, in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted='"'+name.replace('"','""')+'"'
        # Hash all typed values. Sorting row hashes makes this independent of query order.
        hashes=sorted(hashlib.sha256(repr(tuple(row)).encode()).digest() for row in c.execute('SELECT * FROM '+quoted))
        h=hashlib.sha256()
        for value in hashes: h.update(value)
        tables[name]={'rows':len(hashes),'sha256':h.hexdigest()}
    return {'schema':schema,'tables':tables,
            'user_version':c.execute('PRAGMA user_version').fetchone()[0],
            'application_id':c.execute('PRAGMA application_id').fetchone()[0]}

def database_state(p):
    with ro_db(p) as c: return db_state(c)

def schema_check(p):
    with ro_db(p) as c:
        for table,columns in COLUMNS.items():
            require(columns <= {r[1] for r in c.execute('PRAGMA table_info("'+table+'")')},'SCHEMA_MISMATCH:'+table)

def database_backup(src,dst):
    require(not dst.exists(),'BACKUP_EXISTS')
    with ro_db(src) as c:
        before=db_state(c)
        with contextlib.closing(sqlite3.connect(dst)) as d:
            deadline=time.monotonic()+90
            def progress(*_): require(time.monotonic()<deadline,'BACKUP_TIMEOUT')
            c.backup(d,pages=256,progress=progress,sleep=0.02)
    os.chmod(dst,0o600)
    require(database_state(dst)==before,'DB_BACKUP_MISMATCH')
    with dst.open('rb') as f: os.fsync(f.fileno())
    return before

class Layout:
    def __init__(self,app=Path('/opt/az-baze-auth'),reports=Path('/var/www/az-baze.ru/reports'),
                 db=Path('/var/lib/az-baze/auth.db'),backups=Path('/var/lib/az-baze/backups')):
        self.app,self.reports,self.db,self.backups=app,reports,db,backups
    def live(self,name):
        return self.app/name[len('az-baze-auth/'):] if name.startswith('az-baze-auth/') else self.reports/name[len('reports/'):]
    def footprint(self):
        states=[]
        for name,(old,new,_) in FILES.items():
            raw=read_code(self.live(name),optional=old is None)
            actual=blob(raw) if raw is not None else None
            states.append('BASE' if actual==old else 'TARGET' if actual==new else 'OTHER')
        require(len(set(states))==1 and states[0] in ('BASE','TARGET'),'SOURCE_MIXED_OR_UNKNOWN')
        for name,expected in DEPENDENCIES.items():
            require(blob(read_code(self.app/name))==expected,'DEPENDENCY_CHANGED:'+name)
        schema_check(self.db)
        return states[0]
    def protected(self):
        # All application modules/templates and reports assets, excluding only targets.
        paths=set(self.app.glob('*.py'))|set((self.app/'templates').rglob('*'))|set(self.reports.rglob('*'))
        targets={self.live(n) for n in FILES}
        result={}
        for p in sorted(paths):
            if p in targets or p.suffix not in {'.py','.html','.js','.css','.json','.svg'} or p.is_dir(): continue
            raw=read_code(p)
            result[str(p)]={'sha256':sha(raw),'meta':meta(p)}
        return result

class SystemService:
    def __init__(self,layout): self.layout=layout
    def props(self):
        r=subprocess.run(['systemctl','show',SERVICE,'-p','ActiveState','-p','SubState','-p','MainPID'],capture_output=True,text=True,timeout=10,check=True)
        return dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)
    def context(self):
        d=self.props();pid=int(d.get('MainPID','0'))
        require(d.get('ActiveState')=='active' and pid>1,'SERVICE_NOT_ACTIVE')
        proc=Path('/proc')/str(pid)
        require((proc/'cwd').resolve()==self.layout.app,'SERVICE_CWD_CHANGED')
        paths={'AZBAZE_DB':str(self.layout.db),'AZBAZE_SITE_ROOT':str(self.layout.reports.parent)}
        for item in (proc/'environ').read_bytes().split(b'\0'):
            k,sep,v=item.partition(b'=')
            if sep and k in {key.encode() for key in paths}: require(v.decode()==paths[k.decode()],'SERVICE_PATH_OVERRIDE')
        s=dict(x.split(':',1) for x in (proc/'status').read_text().splitlines() if ':' in x)
        uid=int(s['Uid'].split()[1]);gid=int(s['Gid'].split()[1])
        return (uid,gid,tuple(map(int,s['Groups'].split())))
    def stop(self):
        subprocess.run(['systemctl','stop',SERVICE],check=True,timeout=45,capture_output=True)
        d=self.props()
        require(d.get('ActiveState')=='inactive' and d.get('MainPID')=='0','SERVICE_NOT_STOPPED')
    def start(self):
        subprocess.run(['systemctl','start',SERVICE],check=True,timeout=45,capture_output=True)
    def health(self):
        try:
            if self.props().get('ActiveState')!='active': return False
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(HEALTH,timeout=3) as r:
                return r.geturl()==HEALTH and r.status==200 and r.read(16).strip()==b'ok'
        except (OSError,ValueError,subprocess.SubprocessError): return False
    def wait(self):
        end=time.monotonic()+35
        while time.monotonic()<end:
            if self.health(): return
            time.sleep(1)
        raise Stop('SERVICE_HEALTH_TIMEOUT')
    def readable(self,context):
        uid,gid,groups=context
        paths=[str(self.layout.live(n)) for n in FILES]
        code='import sys; [open(p,"rb").read(1) for p in sys.argv[1:]]'
        r=subprocess.run([sys.executable,'-B','-I','-c',code,*paths],cwd='/',user=uid,group=gid,extra_groups=groups,
                         stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        require(r.returncode==0,'SERVICE_FILE_ACCESS_FAILED')

def fetch_targets(stage):
    sources={}
    for name,(_,expected,sha256) in FILES.items():
        dest=stage/name;dest.parent.mkdir(parents=True,exist_ok=True)
        url='https://raw.githubusercontent.com/'+REPO+'/'+SOURCE+'/'+name
        subprocess.run(['curl','-fsSL','--retry','2','--connect-timeout','10','--max-time','90',url,'-o',str(dest)],check=True,timeout=300)
        raw=read_code(dest)
        require(blob(raw)==expected and sha(raw)==sha256,'DOWNLOAD_HASH_MISMATCH:'+name)
        sources[name]=dest
    return sources

def validate_targets(sources,python):
    for name,(_,b,h) in FILES.items():
        raw=read_code(sources[name]);require(blob(raw)==b and sha(raw)==h,'TARGET_HASH_MISMATCH:'+name)
        if name.endswith('.py'): compile(raw,name,'exec')
    code='from pathlib import Path; import sys; from jinja2 import Environment; Environment().parse(Path(sys.argv[1]).read_text(encoding="utf-8"))'
    result=subprocess.run([str(python),'-B','-I','-c',code,str(sources['az-baze-auth/templates/uploads.html'])],capture_output=True,timeout=15)
    require(result.returncode==0,'TEMPLATE_SYNTAX_FAILED')

def journal_smoke(module_file,db):
    spec=importlib.util.spec_from_file_location('_az_deploy_history_check',module_file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with ro_db(db) as c:
        rows=module.recent_uploads(c)
        require(isinstance(rows,list) and len(rows)<=60,'JOURNAL_READ_FAILED')
    # No row values, filenames, patient/employee names or audit details are printed.
    emit('JOURNAL_READONLY_SMOKE=PASS')

def install(layout,sources,service):
    state=layout.footprint();require(service.health(),'SERVICE_HEALTH_PRE_FAILED')
    context=service.context()
    if state=='TARGET':
        service.readable(context)
        journal_smoke(layout.live('az-baze-auth/upload_history.py'),layout.db)
        emit('REPORT_CONSISTENCY_DEPLOY=PASS; REUSED=True; FILES_CHANGED=0; SERVICE_RESTART=NONE')
        return None
    protected=layout.protected()
    oldmeta={n:meta(layout.live(n)) for n,(old,_,_) in FILES.items() if old is not None}
    baseline_raw={n:read_code(layout.live(n)) for n in oldmeta}
    # Clone the peer module's ownership/mode, not world-writable guessed defaults.
    newmeta=dict(oldmeta['az-baze-auth/daily_upload_core.py'])
    require(not newmeta['mode'] & 0o022,'INSECURE_MODULE_PERMISSION')
    plain(layout.backups,directory=True)
    require(shutil.disk_usage(layout.backups).free > max(512*1024**2,layout.db.stat().st_size*3+64*1024**2),'INSUFFICIENT_BACKUP_SPACE')
    backup=Path(tempfile.mkdtemp(prefix='report-consistency-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-',dir=layout.backups))
    os.chmod(backup,0o700)
    emit('BACKUP='+str(backup))
    changed=[];stop_attempted=False;success=False
    try:
        # File backups and target staging are prepared before the brief service stop.
        for n in oldmeta:
            dest=backup/'files'/n;dest.parent.mkdir(parents=True,exist_ok=True)
            atomic_write(dest,baseline_raw[n],oldmeta[n])
            require(read_code(dest)==baseline_raw[n] and meta(dest)==oldmeta[n],'FILE_BACKUP_MISMATCH')
        private_json(backup/'manifest.json',{'source_commit':SOURCE,'files':FILES,'old_metadata':oldmeta,
            'new_file_absent_before':'az-baze-auth/upload_history.py','protected':protected})
        sync_dir(backup)
        require(layout.footprint()=='BASE' and layout.protected()==protected,'SOURCE_CHANGED_BEFORE_STOP')
        for n,m in oldmeta.items(): require(meta(layout.live(n))==m,'METADATA_CHANGED_BEFORE_STOP')
        require(service.context()==context,'SERVICE_CONTEXT_CHANGED')
        emit('FILE_BACKUP_VERIFY=PASS; TARGET_FILES=5')
        stop_attempted=True;service.stop()
        require(layout.footprint()=='BASE' and layout.protected()==protected,'SOURCE_CHANGED_DURING_STOP')
        for n,m in oldmeta.items(): require(meta(layout.live(n))==m,'METADATA_CHANGED_DURING_STOP')
        before=database_backup(layout.db,backup/'database.sqlite3')
        private_json(backup/'database-before.json',before)
        sync_dir(backup)
        require(database_state(layout.db)==before,'DB_CHANGED_DURING_BACKUP')
        emit('DB_BACKUP_VERIFY=PASS')
        # Check the real historical journal read path against the private snapshot.
        journal_smoke(sources['az-baze-auth/upload_history.py'],backup/'database.sqlite3')
        for n,(_,b,h) in FILES.items():
            raw=read_code(sources[n]);require(blob(raw)==b and sha(raw)==h,'STAGE_CHANGED')
            m=dict(oldmeta.get(n,newmeta));m['mtime_ns']=time.time_ns()
            changed.append(n)  # Includes failures after os.replace but before directory fsync.
            atomic_write(layout.live(n),raw,m)
            require(blob(read_code(layout.live(n)))==b and rights(meta(layout.live(n)))==rights(m),'INSTALLED_FILE_MISMATCH')
        require(layout.footprint()=='TARGET','TARGET_FOOTPRINT_FAILED')
        require(layout.protected()==protected,'PROTECTED_SOURCE_CHANGED')
        service.readable(context)
        require(database_state(layout.db)==before,'DB_CHANGED_BEFORE_START')
        emit('FIVE_FILES_INSTALLED=PASS; PROTECTED_SOURCE=PASS; RIGHTS_PRESERVED=PASS')
        emit('DB_EXACT_CONTENT_UNCHANGED_BEFORE_START=PASS')
        service.start();service.wait()
        require(service.context()==context,'SERVICE_CONTEXT_CHANGED_AFTER_START')
        require(layout.footprint()=='TARGET' and layout.protected()==protected,'POSTCHECK_SOURCE_FAILED')
        # Logical comparison tolerates SQLite housekeeping, not changes to stored values.
        require(database_state(layout.db)==before,'DB_CHANGED_AFTER_START_OR_CONCURRENT_ACTIVITY')
        emit('DB_LOGICAL_CONTENT_UNCHANGED_AFTER_START=PASS')
        private_json(backup/'result.json',{'status':'PASS','source':SOURCE,'installed_files':list(FILES),
                      'finished_utc':datetime.now(timezone.utc).isoformat(),'db_writes_by_installer':False})
        success=True
    except BaseException:
        # Do not let a second terminal signal interrupt an already-started rollback.
        if os.geteuid()==0:
            for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP): signal.signal(sig,signal.SIG_IGN)
        if stop_attempted:
            try:
                service.stop()
                # Verify ALL backups before touching the first live file during recovery.
                for n in oldmeta:
                    saved=backup/'files'/n
                    require(read_code(saved)==baseline_raw[n] and meta(saved)==oldmeta[n],'ROLLBACK_BACKUP_DAMAGED')
                for n in reversed(changed):
                    p=layout.live(n);current=read_code(p,optional=FILES[n][0] is None)
                    actual=blob(current) if current is not None else None
                    require(actual in {FILES[n][0],FILES[n][1]},'ROLLBACK_REFUSES_UNKNOWN_FILE')
                    if n in oldmeta:
                        atomic_write(p,baseline_raw[n],oldmeta[n])
                    elif p.exists():
                        p.unlink();sync_dir(p.parent)
                require(layout.footprint()=='BASE' and layout.protected()==protected,'ROLLBACK_SOURCE_FAILED')
                for n,m in oldmeta.items(): require(meta(layout.live(n))==m,'ROLLBACK_METADATA_FAILED')
                service.start();service.wait()
                emit('FILE_ROLLBACK=PASS; SERVICE_HEALTH_ROLLBACK=PASS')
            except BaseException as rollback_error:
                emit('FILE_ROLLBACK=FAIL; RECOVERY_REQUIRES_REVIEW; ERROR_TYPE='+type(rollback_error).__name__)
            emit('DB_RESTORE=NOT_PERFORMED')
        raise
    finally:
        if not success: emit('BACKUP_PRESERVED='+str(backup))
    emit('REPORT_CONSISTENCY_DEPLOY=PASS; REUSED=False; FILES_CHANGED=5')
    emit('TARGET_COMMIT='+SOURCE)
    emit('SERVICE=active; HEALTH=ok; SERVICE_RESTART=az-baze-auth_ONLY')
    emit('DB_WRITES_BY_INSTALLER=NONE; DB_RESTORE=NOT_PERFORMED; SQL_MIGRATIONS=NONE')
    emit('QNAP_WRITES=NONE; OTHER_SERVICES_CHANGED=NONE; USER_UI_VERIFICATION=PENDING')
    return backup

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',required=True,choices=[TOKEN])
    parser.parse_args()
    require(os.geteuid()==0 and socket.gethostname().split('.')[0]=='az-server','ROOT_ON_AZ_SERVER_REQUIRED')
    os.umask(0o077);sys.dont_write_bytecode=True
    for name in ('curl','systemctl'): require(shutil.which(name) is not None,'MISSING_TOOL:'+name)
    layout=Layout();plain(layout.app,directory=True);plain(layout.reports,directory=True)
    require((layout.app/'venv/bin/python').is_file(),'APPLICATION_PYTHON_MISSING')
    lockpath=Path('/run/lock/az-baze-report-consistency.lock');plain(lockpath,optional=True)
    fd=os.open(lockpath,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise Stop('ANOTHER_INSTALLATION_RUNNING')
        def interrupted(signum,_frame): raise Stop('INTERRUPTED:'+str(signum))
        for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP): signal.signal(sig,interrupted)
        # Validate known baseline before downloads; no application initialization.
        emit('LIVE_SOURCE_STATE='+layout.footprint())
        with tempfile.TemporaryDirectory(prefix='az-report-five-') as tmp:
            sources=fetch_targets(Path(tmp))
            validate_targets(sources,layout.app/'venv/bin/python')
            emit('PINNED_TARGETS_AND_SYNTAX=PASS')
            install(layout,sources,SystemService(layout))
    finally: os.close(fd)

if __name__=='__main__':
    try: main()
    except (Stop,OSError,ValueError,sqlite3.Error,subprocess.SubprocessError) as exc:
        # Known labels are safe; do not print DB rows, source code, env or process output.
        reason=str(exc) if isinstance(exc,Stop) else type(exc).__name__
        emit('REPORT_CONSISTENCY_DEPLOY=STOP_OR_FAIL; REASON='+reason)
        raise SystemExit(1)
