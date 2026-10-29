#!/usr/bin/env python3
"""PR164 controlled file-only delivery. Preparation is NOT authorization to apply.

--preflight reads explicit selections from stdin; prints only aggregate results
and a configuration SHA256. It never installs configuration or changes services.
--apply additionally requires the release token AND the reviewed preflight hash.
All live SQLite connections are read-only. Recovery NEVER restores the database.
"""
from __future__ import annotations
import argparse
import ast
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

REPO = 'pat16071975-ship-it/architecture-health-site'
SOURCE_HEAD = '83512c73deee5d04703465d6efc131e7b7f61e30'
BASE_COMMIT = '2a0301e28d01a7ebaeae81e1e96b9ce4362930b7'
TARGET_COMMIT = 'ffd0a876562c3b9c1e74410d9cf644aafb3fff39'
TOKEN = 'PAID-HISTORY-FFD0A876'
APP = Path('/opt/az-baze-auth')
REPORTS = Path('/var/www/az-baze.ru/reports')
DB = Path('/var/lib/az-baze/auth.db')
BACKUPS = Path('/var/lib/az-baze/backups')
LOCK = Path('/run/lock/az-baze-paid-patient-deploy.lock')
SERVICE = 'az-baze-auth'
HEALTH = 'http://127.0.0.1:8001/health'
CONFIG_NAME = 'paid-period-confirmations-v1.json'
# Repository path, required prior Git blob (None = absent), merged Git blob.
FILES = (
 ('az-baze-auth/paid_period_confirmation.py', None, 'dc3f409877e6d28bfbba4a90bd539e99f01b76cb'),
 ('az-baze-auth/report_storage.py', '0935100bd955eb8c1c91afe833fb059db419ffa4', 'de8529f5830609d4aeb5bd5edf0791c9355ba296'),
 ('reports/index.html', '30d81dc254ad94d6def113b5f35a265d6f54949e', 'db160bf97814d18976a97afc5e680668819c6cd7'),
 ('reports/period-view.js', '47b1f8d8d6b1ea55071badb57465b11cac07473a', '5aeaf54d35dc98ac9b0ce7d0574813cf8034778a'),
)
# Exact permitted historical scope. Import stamps are private stdin, not in Git.
PERIODS = {
 '2026-01': ('period', '2026-01-05', '2026-01-31'),
 '2026-02': ('period', '2026-02-02', '2026-02-28'),
 '2026-03': ('period', '2026-03-02', '2026-03-31'),
 '2026-05': ('snapshot', '2026-05-01', '2026-05-30'),
}
REPORT_FUNCTIONS = {
 '_management_doctor_provider_mappings', '_paid_patient_interval',
 '_paid_report_sources', '_paid_patient_intervals', '_cover_paid_patient_range',
 '_combine_paid_patient_intervals', '_paid_patient_counts',
 '_management_paid_patients_monthly', '_management_doctor_paid_snapshots',
 '_management_doctor_paid_periods',
}

class Stop(RuntimeError):
    pass

def require(condition, message):
    if not condition:
        raise Stop(message)

def mark(message):
    print(message, flush=True)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()

def safe(path, absent=False):
    require(path.is_absolute() and path.resolve() == path and not path.is_symlink(), 'UNSAFE_PATH:' + str(path))
    if absent and not path.exists():
        require(path.parent.is_dir(), 'MISSING_PARENT')
        return
    require(path.is_file(), 'MISSING_FILE:' + str(path))

def live_path(relative):
    if relative.startswith('az-baze-auth/'):
        return APP / relative[len('az-baze-auth/'):]
    require(relative.startswith('reports/'), 'INVALID_SCOPE')
    return REPORTS / relative[len('reports/'):]

def file_blob(path):
    safe(path, absent=True)
    return blob(path.read_bytes()) if path.exists() else None

def config_path():
    return DB.parent / CONFIG_NAME

def live_state():
    states = []
    for relative, old, new in FILES:
        actual = file_blob(live_path(relative))
        require(actual in (old, new), 'UNKNOWN_RUNTIME:' + relative)
        states.append('TARGET' if actual == new else 'BASE')
    require(len(set(states)) == 1, 'MIXED_RUNTIME')
    safe(config_path(), absent=True)
    require(config_path().exists() == (states[0] == 'TARGET'), 'CONFIG_RUNTIME_MISMATCH')
    return states[0]

def protected_state():
    targets = {live_path(r) for r, _, _ in FILES}
    paths = list(APP.glob('*.py')) + list((APP/'templates').rglob('*.html'))
    paths += list(REPORTS.rglob('*.html')) + list(REPORTS.rglob('*.js'))
    result = {}
    for path in sorted(set(paths) - targets):
        safe(path)
        info = path.stat()
        result[str(path)] = [blob(path.read_bytes()), info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]
    return result

def run(args, timeout=45):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    require(result.returncode == 0, 'COMMAND_FAILED:' + Path(args[0]).name)
    return result.stdout

def active():
    return subprocess.run(['systemctl','is-active','--quiet',SERVICE], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=5).returncode == 0

def health():
    try:
        return active() and run(['curl','-fsS','--connect-timeout','2','--max-time','3',HEALTH],6).strip() == b'ok'
    except (Stop, subprocess.TimeoutExpired):
        return False

def service(action):
    run(['systemctl', action, SERVICE])
    if action == 'stop':
        require(not active(), 'SERVICE_NOT_STOPPED')
        require(run(['systemctl','show','--property=MainPID','--value',SERVICE]).strip() == b'0', 'SERVICE_PID_REMAINS')
        return
    end = time.monotonic() + 35
    while time.monotonic() < end:
        if health():
            return
        time.sleep(1)
    raise Stop('SERVICE_HEALTH_TIMEOUT')

def host_guard():
    require(os.geteuid() == 0, 'ROOT_REQUIRED')
    require(socket.gethostname().split('.')[0] == 'az-server', 'WRONG_HOST')
    require(run(['systemctl','show','--property=User','--value',SERVICE]).strip() == b'azadmin', 'UNEXPECTED_SERVICE_USER')
    require(run(['systemctl','show','--property=Group','--value',SERVICE]).strip() == b'www-data', 'UNEXPECTED_SERVICE_GROUP')

@contextlib.contextmanager
def read_db(path):
    safe(path)
    conn = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        yield conn
    finally:
        conn.close()

def fingerprint(conn):
    require([tuple(r) for r in conn.execute('PRAGMA integrity_check')] == [('ok',)], 'DB_INTEGRITY_FAILED')
    schema = [tuple(r) for r in conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
    result = {'schema': sha(repr(schema).encode()), 'tables': {}}
    for kind, name, _, _ in schema:
        if kind != 'table':
            continue
        escaped = name.replace('"','""')
        hashes = sorted(hashlib.sha256(repr(tuple(r)).encode()).digest()
                        for r in conn.execute('SELECT * FROM "'+escaped+'"'))
        result['tables'][name] = [len(hashes), sha(b''.join(hashes))]
    for key in ('user_version','application_id'):
        result[key] = conn.execute('PRAGMA '+key).fetchone()[0]
    return result

def db_state():
    with read_db(DB) as conn:
        return fingerprint(conn)

def stage_targets(stage):
    for relative, _, expected in FILES:
        path = stage/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        run(['curl','-fsSL','--retry','2','--connect-timeout','5','--max-time','30',
             'https://raw.githubusercontent.com/'+REPO+'/'+TARGET_COMMIT+'/'+relative,'-o',str(path)],100)
        require(blob(path.read_bytes()) == expected, 'TARGET_BLOB_MISMATCH:'+relative)
        if path.suffix == '.py':
            compile(path.read_bytes(), str(path), 'exec')
    python = APP/'venv/bin/python'
    require(python.is_file(), 'PRODUCTION_PYTHON_MISSING')
    run([str(python),'-B','-c',"import pathlib,sys; [compile(pathlib.Path(p).read_bytes(),p,'exec') for p in sys.argv[1:]]",
         *[str(stage/r) for r,_,_ in FILES if r.endswith('.py')]])
    node = shutil.which('node')
    if node:
        run([node,'--check',str(stage/'reports/period-view.js')])
    mark('TARGET_BLOBS=PASS; JS_SYNTAX='+('PASS' if node else 'CI_ONLY_NODE_NOT_INSTALLED'))

def load_helper(stage):
    path = stage/'az-baze-auth/paid_period_confirmation.py'
    require(blob(path.read_bytes()) == FILES[0][2], 'HELPER_BLOB_MISMATCH')
    # This exact standalone helper imports only stdlib. Never import app/server.
    scope = {'__name__':'isolated_confirmation'}
    exec(compile(path.read_bytes(),str(path),'exec'),scope)
    return SimpleNamespace(**scope)

def bind(conn, selections, helper):
    require(isinstance(selections,list) and len(selections)==4, 'EXACTLY_FOUR_SELECTIONS_REQUIRED')
    sources = helper.read_sources(conn)
    entries, seen = [], set()
    fields = {'kind','sourceFrom','sourceTo','importedAt','reportMonth'}
    for item in selections:
        require(isinstance(item,dict) and set(item)==fields, 'INVALID_SELECTION_FIELDS')
        month = item['reportMonth']
        require(isinstance(month,str) and month in PERIODS and month not in seen, 'UNAPPROVED_MONTH')
        require(tuple(item[k] for k in ('kind','sourceFrom','sourceTo')) == PERIODS[month], 'UNAPPROVED_SOURCE_RANGE')
        require(isinstance(item['importedAt'],str) and bool(item['importedAt']), 'IMPORT_STAMP_REQUIRED')
        matches = [s for s in sources if all(s[k]==item[k] for k in ('kind','sourceFrom','sourceTo','importedAt'))]
        require(len(matches)==1, 'SOURCE_CHANGED_OR_AMBIGUOUS:'+month)
        source = matches[0]
        require(source['payload'].get('paid_patient_evidence_complete') is True and
                isinstance(source['payload'].get('paid_patients'),dict), 'MISSING_PATIENT_EVIDENCE:'+month)
        entries.append(helper.make_confirmation(source,month))
        seen.add(month)
    entries.sort(key=lambda e:e['reportMonth'])
    helper.validate_config({'version':1,'confirmations':entries})
    projected = helper.read_sources(conn,entries)
    require({s['confirmedMonth'] for s in projected if s['confirmedMonth']} == set(PERIODS), 'NEWER_CALENDAR_SOURCE_REQUIRES_REVIEW')
    for source in projected:
        original = next(s for s in sources if helper.source_identity(s)==helper.source_identity(source))
        require(original['payload']==source['payload'], 'SOURCE_PAYLOAD_CHANGED')
    return (json.dumps({'version':1,'confirmations':entries},ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()

def extract_functions(path, names, scope):
    tree = ast.parse(path.read_bytes())
    nodes = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    require({n.name for n in nodes}==names and all(not n.decorator_list for n in nodes), 'READ_FUNCTIONS_CHANGED')
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),scope)

def reporter(stage, config_raw, helper):
    ident = APP/'ident_import.py'
    safe(ident)
    tree = ast.parse(ident.read_bytes())
    names = {'DENTISTS','STRUCTURE_DOCTORS','LAB_DOCTORS','KNOWN_STAFF'}
    nodes = [n for n in tree.body if isinstance(n,ast.Assign) and len(n.targets)==1 and
             isinstance(n.targets[0],ast.Name) and n.targets[0].id in names]
    require({n.targets[0].id for n in nodes}==names, 'PROVIDER_CONSTANTS_CHANGED')
    # Only literal maps/sets, names, unions and set(NAME) are admitted.
    allowed = (ast.Assign,ast.Name,ast.Load,ast.Store,ast.Dict,ast.Set,ast.Constant,
               ast.BinOp,ast.BitOr,ast.Call)
    for node in nodes:
        for part in ast.walk(node):
            require(isinstance(part,allowed), 'UNSAFE_PROVIDER_CONSTANT')
            if isinstance(part,ast.Call):
                require(isinstance(part.func,ast.Name) and part.func.id=='set' and len(part.args)==1
                        and isinstance(part.args[0],ast.Name) and part.args[0].id in names and not part.keywords,
                        'UNSAFE_PROVIDER_CONSTANT_CALL')
    constants = {'set':set,'__builtins__':{}}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ident),'exec'),constants)
    paid = {'math':math}
    extract_functions(APP/'paid_services.py',{'_num','direction_summary'},paid)
    config = json.loads(config_raw)['confirmations']
    scope = {'json':json,'math':math,'re':re,'calendar_date':date,'timedelta':timedelta,
             'DB_PATH':DB,'DATE_RE':re.compile(r'^\d{4}-\d{2}-\d{2}$'),
             'ident_import':SimpleNamespace(**{k:constants[k] for k in names}),
             'paid_services':SimpleNamespace(**paid),
             'paid_period_confirmation':SimpleNamespace(read_sources=helper.read_sources,load_config=lambda _:config)}
    extract_functions(stage/'az-baze-auth/report_storage.py',REPORT_FUNCTIONS,scope)
    return scope

def projection_check(conn, stage, config_raw, helper):
    scope = reporter(stage,config_raw,helper)
    monthly = scope['_management_paid_patients_monthly'](conn)
    periods = scope['_management_doctor_paid_periods'](conn)
    snapshots = scope['_management_doctor_paid_snapshots'](conn)
    for month in sorted(PERIODS):
        first,last = helper.month_bounds(month)
        require(month in monthly and monthly[month]['asOf']==last, 'MONTH_NOT_READY:'+month)
        money = [p for p in periods if (p['from'],p['to'])==(first,last)] if PERIODS[month][0]=='period' else [s for s in snapshots.get(month,[]) if s['asOf']==last]
        require(len(money)==1, 'MATCHING_REVENUE_UNAVAILABLE:'+month)
    # Non-historical October keeps the same exact cutoff counts, not a month alias.
    empty = (json.dumps({'version':1,'confirmations':[]})+'\n').encode()
    baseline = reporter(stage,empty,helper)
    for end in ('2026-10-27','2026-10-31'):
        require(scope['_paid_patient_counts'](conn,'2026-10-01',end)==
                baseline['_paid_patient_counts'](conn,'2026-10-01',end), 'OCTOBER_CHANGED')
    return {m:{k:monthly[m][k] for k in ('asOf','medicine','dent','structure')} for m in sorted(PERIODS)}

def backup_capacity():
    require(BACKUPS.is_dir() and BACKUPS.resolve()==BACKUPS, 'BACKUP_ROOT_UNSAFE')
    required = DB.stat().st_size * 2 + 32 * 1024 * 1024
    require(shutil.disk_usage(BACKUPS).free >= required, 'INSUFFICIENT_BACKUP_SPACE')

def verify_config_readable(config):
    import grp
    safe(config_path())
    info=config_path().stat()
    require(info.st_uid==0 and info.st_gid==grp.getgrnam('www-data').gr_gid
            and stat.S_IMODE(info.st_mode)==0o640, 'UNSAFE_ACTIVE_CONFIG_PERMISSIONS')
    actual=run(['runuser','-u','azadmin','--',str(APP/'venv/bin/python'),'-B','-c',
                "import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())",
                str(config_path())]).strip().decode('ascii')
    require(actual==sha(config), 'SERVICE_CANNOT_READ_CONFIG')

def preflight(stage, selections):
    require(health(),'SERVICE_HEALTH_PRE_FAILED')
    state = live_state()
    backup_capacity()
    protected = protected_state()
    require(blob((APP/'paid_services.py').read_bytes()) == '7731d49098494276e40442d06443a0729fc7fcf3','PAID_SOURCE_VERSION_CHANGED')
    stage_targets(stage)
    helper = load_helper(stage)
    with read_db(DB) as conn:
        before = fingerprint(conn)
        config = bind(conn,selections,helper)
        summary = projection_check(conn,stage,config,helper)
        require(fingerprint(conn)==before,'READ_PROJECTION_MUTATION')
    if state == 'TARGET':
        require(config_path().read_bytes()==config,'ACTIVE_CONFIG_CHANGED')
        verify_config_readable(config)
    require(protected_state()==protected,'PROTECTED_RUNTIME_CHANGED')
    mark('FOUR_MONTH_PROJECTION='+json.dumps(summary,ensure_ascii=False,sort_keys=True))
    mark('CONFIG_SHA256='+sha(config))
    mark('LIVE_STATE='+state)
    mark('PAID_HISTORY_PREFLIGHT=PASS; DATABASE_WRITES=NONE; ACTIVE_CONFIG_WRITES=NONE; SERVICE_ACTIONS=NONE')
    return state, protected, config

def metadata(path):
    info = path.stat()
    return [info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode)]

def atomic_write(raw, destination, meta, expected):
    require(file_blob(destination)==expected,'CONCURRENT_FILE_CHANGE:'+str(destination))
    fd, temporary = tempfile.mkstemp(prefix='.az-paid-history-',dir=destination.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw); stream.flush()
            os.fchown(stream.fileno(),meta[0],meta[1]); os.fchmod(stream.fileno(),meta[2]); os.fsync(stream.fileno())
        require(file_blob(destination)==expected,'CONCURRENT_FILE_CHANGE:'+str(destination))
        if expected is None:
            os.link(temporary,destination,follow_symlinks=False)
            os.unlink(temporary)
        else:
            os.replace(temporary,destination)
        fd = os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

def make_backup(protected, config):
    require(BACKUPS.is_dir() and BACKUPS.resolve()==BACKUPS,'BACKUP_ROOT_UNSAFE')
    backup = Path(tempfile.mkdtemp(prefix='paid-history-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-',dir=BACKUPS))
    backup.chmod(0o700)
    records = {}
    for relative,old,_ in FILES:
        path = live_path(relative)
        require(file_blob(path)==old,'BASE_CHANGED_BEFORE_BACKUP')
        records[relative] = metadata(path) if old else None
        if old:
            saved = backup/'files'/relative
            saved.parent.mkdir(parents=True,exist_ok=True)
            saved.write_bytes(path.read_bytes()); saved.chmod(0o600)
            require(blob(saved.read_bytes())==old,'BACKUP_FILE_MISMATCH')
    require(not config_path().exists(),'EXISTING_CONFIG_NOT_REPLACED')
    database = backup/'auth.db'
    with read_db(DB) as source:
        expected = fingerprint(source)
        target = sqlite3.connect(database)
        deadline = time.monotonic()+90
        def progress(status,remaining,total):
            require(time.monotonic()<deadline,'BACKUP_TIMEOUT')
        try: source.backup(target,pages=256,progress=progress,sleep=0.05)
        finally: target.close()
    database.chmod(0o600)
    with read_db(database) as conn: require(fingerprint(conn)==expected,'BACKUP_DB_MISMATCH')
    (backup/'configuration.json').write_bytes(config); (backup/'configuration.json').chmod(0o600)
    manifest = {'target':TARGET_COMMIT,'source':SOURCE_HEAD,'files':[list(f) for f in FILES],
                'metadata':records,'configSha256':sha(config),'configPreviouslyAbsent':True,
                'dbState':expected,'dbSha256':sha(database.read_bytes()),'protected':protected}
    (backup/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n'); (backup/'manifest.json').chmod(0o600)
    shutil.copyfile(__file__,backup/'installer.py'); (backup/'installer.py').chmod(0o600)
    for path in backup.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream: os.fsync(stream.fileno())
    for directory in [p for p in backup.rglob('*') if p.is_dir()] + [backup]:
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    mark('BACKUP='+str(backup)); mark('FILE_AND_SQLITE_BACKUP=PASS')
    return backup,manifest

def validate_backup(backup):
    require(backup.parent==BACKUPS and backup.resolve()==backup and backup.name.startswith('paid-history-'),'INVALID_CHECKPOINT')
    require(backup.stat().st_uid==0 and stat.S_IMODE(backup.stat().st_mode)==0o700,'UNSAFE_CHECKPOINT')
    for name in ('manifest.json','configuration.json','auth.db'):
        path=backup/name; safe(path)
        require(path.stat().st_uid==0 and stat.S_IMODE(path.stat().st_mode)==0o600,'UNSAFE_BACKUP_PERMISSIONS')
    manifest = json.loads((backup/'manifest.json').read_bytes())
    require(manifest['target']==TARGET_COMMIT and manifest['source']==SOURCE_HEAD and manifest['files']==[list(f) for f in FILES]
            and manifest['configPreviouslyAbsent'] is True,'WRONG_CHECKPOINT_RELEASE')
    raw = (backup/'configuration.json').read_bytes()
    require(sha(raw)==manifest['configSha256'],'CORRUPT_CONFIG_BACKUP')
    require(sha((backup/'auth.db').read_bytes())==manifest['dbSha256'],'CORRUPT_DB_BACKUP')
    require(file_blob(config_path()) in (None,blob(raw)),'ROLLBACK_NEWER_CONFIG')
    for relative,old,new in FILES:
        require(file_blob(live_path(relative)) in (old,new),'ROLLBACK_NEWER_RUNTIME:'+relative)
        if old:
            saved=backup/'files'/relative; safe(saved)
            require(blob(saved.read_bytes())==old,'CORRUPT_FILE_BACKUP:'+relative)
            meta=manifest['metadata'][relative]
            require(isinstance(meta,list) and len(meta)==3 and all(type(v)==int and v>=0 for v in meta),'CORRUPT_FILE_METADATA')
    return manifest

def restore_files(backup):
    manifest=validate_backup(backup)
    # Files only. Newly created configuration/helper are removed only if exact.
    for relative,old,_ in reversed(FILES):
        path=live_path(relative)
        if old:
            atomic_write((backup/'files'/relative).read_bytes(),path,manifest['metadata'][relative],file_blob(path))
        elif path.exists(): path.unlink()
    if config_path().exists(): config_path().unlink()
    require(live_state()=='BASE','ROLLBACK_VERIFY_FAILED')
    mark('FILE_AND_CONFIG_ROLLBACK=PASS; DATABASE_RESTORE=NOT_PERFORMED')

def apply(stage, selections, protected, config):
    require(live_state()=='BASE','APPLY_REQUIRES_BASE')
    backup=None; stopped=False; touched=False
    try:
        stopped=True; service('stop')
        require(live_state()=='BASE' and protected_state()==protected,'DRIFT_AFTER_STOP')
        helper=load_helper(stage)
        with read_db(DB) as conn:
            require(bind(conn,selections,helper)==config,'STALE_PREFLIGHT_CONFIG')
            projection_check(conn,stage,config,helper)
        backup_capacity()
        backup,manifest=make_backup(protected,config)
        require(db_state()==manifest['dbState'],'DB_CHANGED_AFTER_BACKUP')
        touched=True
        template=metadata(APP/'report_storage.py')
        for relative,old,_ in FILES:
            meta=manifest['metadata'][relative] if old else template
            atomic_write((stage/relative).read_bytes(),live_path(relative),meta,old)
        # Readable by the existing service group; never placed in public web root.
        import grp
        atomic_write(config,config_path(),[0,grp.getgrnam('www-data').gr_gid,0o640],None)
        require(live_state()=='TARGET' and config_path().read_bytes()==config,'TARGET_VERIFY_FAILED')
        verify_config_readable(config)
        require(db_state()==manifest['dbState'] and protected_state()==protected,'STATE_CHANGED_BEFORE_START')
        service('start')
        require(db_state()==manifest['dbState'],'DB_CHANGED_AFTER_START')
        require(live_state()=='TARGET' and config_path().read_bytes()==config and protected_state()==protected,'FINAL_RUNTIME_CHANGED')
        mark('PAID_HISTORY_PRODUCTION_DEPLOY=PASS; CHANGED_RUNTIME_FILES=4; PRIVATE_CONFIG_FILES=1')
        mark('FULL_DATABASE_UNCHANGED=PASS; PROTECTED_RUNTIME_UNCHANGED=PASS; SERVICE=active; HEALTH=ok')
        mark('TARGET_COMMIT='+TARGET_COMMIT+'; XLSX_IMPORT=NOT_PERFORMED')
    except BaseException:
        if stopped:
            try:
                if touched:
                    service('stop'); restore_files(backup)
                service('start'); mark('RECOVERY_HEALTH=PASS')
            except BaseException as error:
                mark('RECOVERY=FAIL:'+type(error).__name__)
                if backup: mark('RECOVERY_CHECKPOINT='+str(backup))
        raise

def rollback(backup):
    manifest=validate_backup(backup)
    require(protected_state()==manifest['protected'],'PROTECTED_DRIFT_REVIEW_REQUIRED')
    before=db_state()
    service('stop')
    try:
        restore_files(backup)
    except BaseException:
        mark('ROLLBACK_STOPPED_FOR_REVIEW; DATABASE_RESTORE=NOT_PERFORMED')
        raise
    service('start')
    require(db_state()==before,'DB_CHANGED_DURING_ROLLBACK')
    mark('PAID_HISTORY_ROLLBACK=PASS; DATABASE_RESTORE=NOT_PERFORMED')

def main(argv):
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--preflight',action='store_true')
    modes.add_argument('--apply',nargs=2,metavar=('TOKEN','CONFIG_SHA256'))
    modes.add_argument('--rollback',nargs=2,metavar=('CHECKPOINT','TOKEN'))
    args=parser.parse_args(argv)
    if args.apply:
        require(args.apply[0]==TOKEN and re.fullmatch(r'[0-9a-f]{64}',args.apply[1]),'WRONG_APPLY_AUTHORIZATION')
    if args.rollback: require(args.rollback[1]==TOKEN,'WRONG_ROLLBACK_TOKEN')
    host_guard()
    # Preflight takes no lock and performs no service or persistent-file writes.
    with contextlib.ExitStack() as stack:
        if not args.preflight:
            fd=os.open(LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            stack.callback(os.close,fd)
            require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_uid==0,'UNSAFE_DEPLOY_LOCK')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.rollback:
            rollback(Path(args.rollback[0])); return
        selections=json.loads(sys.stdin.read(16385))
        stage=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='az-pr164-stage-')))
        state,protected,config=preflight(stage,selections)
        if args.preflight:
            mark('LIVE_CHANGES=NONE; APPLY_NOT_STARTED'); return
        require(sha(config)==args.apply[1],'PREFLIGHT_CONFIG_HASH_CHANGED')
        if state=='TARGET':
            mark('ALREADY_INSTALLED=PASS; LIVE_CHANGES=NONE'); return
        apply(stage,selections,protected,config)

if __name__=='__main__':
    def interrupted(signum,frame): raise Stop('INTERRUPTED')
    signal.signal(signal.SIGTERM,interrupted)
    try: main(sys.argv[1:])
    except (Exception,KeyboardInterrupt) as error:
        mark('PAID_HISTORY_DELIVERY=STOP; ERROR='+type(error).__name__+((':'+str(error)) if isinstance(error,Stop) else ''))
        sys.exit(1)
