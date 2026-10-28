#!/usr/bin/env python3
"""PR162 delivery. No live SQL writes, schema init, imports, or data backfill."""
import ast
import contextlib
import fcntl
import hashlib
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
from datetime import datetime, timezone

REPO = 'pat16071975-ship-it/architecture-health-site'
SOURCE_HEAD = '9a96472ea6f43b26bf709098c6c52d86a35d8c6b'
BASE_COMMIT = '4f78e1e5cdddc1baf9bce67f96e302d199c207fe'
TARGET_COMMIT = '2a0301e28d01a7ebaeae81e1e96b9ce4362930b7'
TOKEN = 'PAID-PATIENTS-2A0301E2'
APP = Path('/opt/az-baze-auth')
REPORTS = Path('/var/www/az-baze.ru/reports')
DB = Path('/var/lib/az-baze/auth.db')
BACKUPS = Path('/var/lib/az-baze/backups')
LOCK = Path('/run/lock/az-baze-paid-patient-deploy.lock')
SERVICE = 'az-baze-auth'
HEALTH = 'http://127.0.0.1:8001/health'
# repo path, exact pre-release blob, exact merged-release blob
FILES = (
 ('az-baze-auth/paid_services.py','6aadd5b5e1464d027425d163b4b47fe718ddd589','7731d49098494276e40442d06443a0729fc7fcf3'),
 ('az-baze-auth/report_storage.py','a6edef274755a18fb9e17b2841bb124647bb159b','0935100bd955eb8c1c91afe833fb059db419ffa4'),
 ('reports/index.html','ea68923b8eaf5ac1354c807290fd97ab1a589b55','30d81dc254ad94d6def113b5f35a265d6f54949e'),
 ('reports/period-view.js','68cd5606f0d4a82d378d66d470eb16ea76fbca42','47b1f8d8d6b1ea55071badb57465b11cac07473a'),
 ('reports/dashboard.html','deec8d5174a1205b405a674c668e571334f8a85c','6ee30907828c4b9c6d0746eceffe92ff26c62389'),
 ('reports/services-primary-patient-cost.js','11b88dea8c00f2f15ce3b678a96aee388a74335f','8fde6671dcf9b5d05b7628f7d9bf10f05743c2a9'),
 ('reports/services.html','7f160a9739d7b2be965c024d22ae101e88f3c4fd','29c57407610d52115f7df4ce28a5a5930a67645a'),
)

class Stop(RuntimeError):
    pass

def require(ok, message):
    if not ok:
        raise Stop(message)

def mark(message):
    print(message, flush=True)

def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

def regular(path):
    require(path.is_file() and not path.is_symlink() and path.resolve() == path, 'UNSAFE_FILE:' + str(path))

def live_path(rel):
    if rel.startswith('az-baze-auth/'):
        return APP / rel[len('az-baze-auth/'):]
    require(rel.startswith('reports/'), 'INVALID_SCOPE')
    return REPORTS / rel[len('reports/'):]

def live_state():
    states = []
    for rel, base, target in FILES:
        path = live_path(rel)
        regular(path)
        actual = blob(path.read_bytes())
        require(actual in (base, target), 'LIVE_BLOB_MISMATCH:' + rel + ':' + actual)
        states.append('TARGET' if actual == target else 'BASE')
    require(len(set(states)) == 1, 'MIXED_RUNTIME_STATE')
    return states[0]

def protected_state():
    # Content only; no .env, credentials, runtime DB or video files are read here.
    targets = {live_path(rel) for rel, _, _ in FILES}
    candidates = list(APP.glob('*.py')) + list((APP / 'templates').rglob('*.html'))
    candidates += list(REPORTS.rglob('*.html')) + list(REPORTS.rglob('*.js'))
    result = {}
    for path in sorted(set(candidates) - targets):
        regular(path)
        result[str(path)] = blob(path.read_bytes())
    return result

def run(args, timeout=50):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    require(result.returncode == 0, 'COMMAND_FAILED:' + Path(args[0]).name)
    return result.stdout

def active():
    return subprocess.run(['systemctl','is-active','--quiet',SERVICE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5).returncode == 0

def health():
    try:
        return active() and run(['curl','-fsS','--connect-timeout','2','--max-time','3',HEALTH], 6).strip() == b'ok'
    except (Stop, subprocess.TimeoutExpired):
        return False

def service(action):
    run(['systemctl',action,SERVICE], 45)
    if action == 'stop':
        require(not active(), 'SERVICE_NOT_STOPPED')
    else:
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if health():
                return
            time.sleep(1)
        raise Stop('SERVICE_HEALTH_TIMEOUT')

@contextlib.contextmanager
def read_db(path):
    regular(path)
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        yield conn
    finally:
        conn.close()

def fingerprint(conn):
    require(conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'DB_INTEGRITY_FAILED')
    schema = list(conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'))
    result = {'schema': hashlib.sha256(repr(schema).encode()).hexdigest(), 'tables': {}}
    for kind, name, _, _ in schema:
        if kind != 'table':
            continue
        escaped = name.replace('"','""')
        # Ordered multiset of row digests; duplicate rows remain significant.
        rows = sorted(hashlib.sha256(repr(tuple(row)).encode()).digest() for row in conn.execute('SELECT * FROM "' + escaped + '"'))
        result['tables'][name] = [len(rows), hashlib.sha256(b''.join(rows)).hexdigest()]
    for field in ('user_version','application_id'):
        result[field] = conn.execute('PRAGMA ' + field).fetchone()[0]
    return result

def db_state():
    with read_db(DB) as conn:
        return fingerprint(conn)

def backup_db(destination):
    require(not destination.exists(), 'BACKUP_DB_EXISTS')
    with read_db(DB) as source:
        expected = fingerprint(source)
        target = sqlite3.connect(destination)
        deadline = time.monotonic() + 60
        def progress(status, remaining, total):
            require(time.monotonic() < deadline, 'DB_BACKUP_TIMEOUT')
        try:
            source.backup(target, pages=256, progress=progress, sleep=0.05)
        finally:
            target.close()
    destination.chmod(0o600)
    with read_db(destination) as check:
        require(fingerprint(check) == expected, 'BACKUP_DB_MISMATCH')
    return expected

def atomic_copy(source, destination, metadata):
    regular(source)
    regular(destination)
    fd, temp = tempfile.mkstemp(prefix='.az-paid-patient-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(source.read_bytes())
            output.flush()
            os.fchown(output.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(output.fileno(), stat.S_IMODE(metadata.st_mode))
            os.fsync(output.fileno())
        os.replace(temp, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def stage_targets(stage):
    for rel, _, expected in FILES:
        path = stage / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        url = 'https://raw.githubusercontent.com/' + REPO + '/' + TARGET_COMMIT + '/' + rel
        run(['curl','-fsSL','--retry','2','--connect-timeout','5','--max-time','30',url,'-o',str(path)], 100)
        require(blob(path.read_bytes()) == expected, 'TARGET_BLOB_MISMATCH:' + rel)
        if path.suffix == '.py':
            # Parse/compile only: never import app.py or any production module.
            ast.parse(path.read_text(encoding='utf-8'), filename=rel)
    python = APP / 'venv/bin/python'
    require(python.is_file(), 'PRODUCTION_PYTHON_MISSING')
    run([str(python),'-B','-c',"import pathlib,sys; [compile(pathlib.Path(p).read_bytes(),p,'exec') for p in sys.argv[1:]]", *[str(stage/r) for r,_,_ in FILES if r.endswith('.py')]])
    node = shutil.which('node')
    if node:
        for rel, _, _ in FILES:
            if rel.endswith('.js'):
                run([node,'--check',str(stage/rel)])
        mark('JS_SYNTAX=PASS')
    else:
        mark('JS_SYNTAX=CI_ONLY_NODE_NOT_INSTALLED')
    mark('TARGET_BLOBS_AND_PYTHON_SYNTAX=PASS')

def preflight(stage):
    require(health(), 'SERVICE_HEALTH_PRE_FAILED')
    state = live_state()
    before = protected_state()
    stage_targets(stage)
    db_state()
    require(protected_state() == before, 'PROTECTED_RUNTIME_CHANGED_DURING_PREFLIGHT')
    mark('LIVE_STATE=' + state)
    mark('DATABASE_SQL_WRITES=NONE')
    mark('SERVICE_ACTIONS=NONE')
    mark('PAID_PATIENT_PREFLIGHT=PASS')
    return state, before

def create_backup(protected):
    require(BACKUPS.is_dir() and BACKUPS.resolve() == BACKUPS, 'BACKUP_ROOT_UNSAFE')
    backup = Path(tempfile.mkdtemp(prefix='paid-patients-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-', dir=BACKUPS))
    for rel, base, _ in FILES:
        source = live_path(rel)
        require(blob(source.read_bytes()) == base, 'BASE_CHANGED_BEFORE_BACKUP:' + rel)
        dest = backup / 'files' / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        os.chown(dest, source.stat().st_uid, source.stat().st_gid)
        require(blob(dest.read_bytes()) == base, 'FILE_BACKUP_MISMATCH')
    expected = backup_db(backup / 'auth.db')
    manifest = {'target': TARGET_COMMIT, 'source': SOURCE_HEAD, 'files': FILES, 'db_state': expected, 'protected': protected}
    (backup / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    (backup / 'manifest.json').chmod(0o600)
    shutil.copyfile(__file__, backup / 'installer.py')
    (backup / 'installer.py').chmod(0o600)
    mark('BACKUP=' + str(backup))
    mark('FILE_AND_SQLITE_BACKUP=PASS')
    return backup, expected

def validate_backup(backup):
    require(backup.parent == BACKUPS and backup.resolve() == backup and backup.name.startswith('paid-patients-'), 'INVALID_CHECKPOINT_PATH')
    info = backup.stat()
    require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700, 'UNSAFE_CHECKPOINT_PERMISSIONS')
    manifest_path = backup / 'manifest.json'
    regular(manifest_path)
    info = manifest_path.stat()
    require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600, 'UNSAFE_MANIFEST_PERMISSIONS')
    manifest = json.loads(manifest_path.read_text())
    require(manifest['target'] == TARGET_COMMIT and manifest['files'] == [list(x) for x in FILES], 'WRONG_CHECKPOINT')
    for rel, base, target in FILES:
        saved = backup / 'files' / rel
        regular(saved)
        regular(live_path(rel))
        require(blob(saved.read_bytes()) == base, 'CORRUPT_BACKUP:' + rel)
        require(blob(live_path(rel).read_bytes()) in (base, target), 'ROLLBACK_NEWER_RUNTIME:' + rel)
    return manifest

def restore_files(backup):
    validate_backup(backup)
    for rel, base, _ in FILES:
        saved = backup / 'files' / rel
        atomic_copy(saved, live_path(rel), saved.stat())
    require(live_state() == 'BASE', 'ROLLBACK_VERIFY_FAILED')
    mark('FILE_ROLLBACK=PASS')
    mark('DATABASE_RESTORE=NOT_PERFORMED')

def apply(stage, protected):
    require(live_state() == 'BASE', 'APPLY_REQUIRES_BASE')
    backup = None
    stop_attempted = False
    installed = False
    try:
        stop_attempted = True
        service('stop')
        require(live_state() == 'BASE', 'BASE_CHANGED_AFTER_STOP')
        require(protected_state() == protected, 'PROTECTED_RUNTIME_CHANGED')
        backup, expected = create_backup(protected)
        require(db_state() == expected, 'DB_CHANGED_AFTER_BACKUP')
        installed = True
        for rel, _, _ in FILES:
            atomic_copy(stage/rel, live_path(rel), (backup/'files'/rel).stat())
        require(live_state() == 'TARGET', 'INSTALL_VERIFY_FAILED')
        require(protected_state() == protected, 'PROTECTED_RUNTIME_CHANGED')
        require(db_state() == expected, 'DB_CHANGED_BEFORE_START')
        service('start')
        require(db_state() == expected, 'DB_CHANGED_AFTER_START')
        require(live_state() == 'TARGET' and protected_state() == protected, 'FINAL_RUNTIME_MISMATCH')
        mark('PAID_PATIENT_PRODUCTION_DEPLOY=PASS')
        mark('TARGET_COMMIT=' + TARGET_COMMIT)
        mark('SOURCE_HEAD=' + SOURCE_HEAD)
        mark('CHANGED_RUNTIME_FILES=7')
        mark('FULL_DATABASE_UNCHANGED=PASS')
        mark('PROTECTED_RUNTIME_UNCHANGED=PASS')
        mark('SERVICE=active; HEALTH=ok; XLSX_IMPORT=NOT_PERFORMED')
    except BaseException:
        # File-only recovery must never overwrite uploads made after the checkpoint.
        if stop_attempted:
            try:
                if installed and backup:
                    service('stop')
                    restore_files(backup)
                service('start')
                mark('RECOVERY_HEALTH=PASS')
            except BaseException as recovery:
                mark('RECOVERY=FAIL:' + type(recovery).__name__)
                if backup:
                    mark('RECOVERY_CHECKPOINT=' + str(backup))
        raise

def manual_rollback(backup):
    manifest = validate_backup(backup)
    require(protected_state() == manifest['protected'], 'PROTECTED_RUNTIME_DRIFT_REVIEW_REQUIRED')
    before = db_state()
    service('stop')
    restore_files(backup)
    service('start')
    require(db_state() == before, 'DB_CHANGED_DURING_FILE_ROLLBACK')
    mark('PAID_PATIENT_ROLLBACK=PASS')

def main(argv):
    if argv == ['--preflight']:
        mode = 'preflight'
    elif argv == ['--apply', TOKEN]:
        mode = 'apply'
    elif len(argv) == 3 and argv[0] == '--rollback' and argv[2] == TOKEN:
        mode = 'rollback'
    else:
        raise Stop('USAGE: --preflight | --apply ' + TOKEN + ' | --rollback CHECKPOINT ' + TOKEN)
    require(os.geteuid() == 0, 'ROOT_REQUIRED')
    require(socket.gethostname().split('.')[0] == 'az-server', 'WRONG_HOST')
    for command in ('curl','systemctl'):
        require(shutil.which(command), 'MISSING_COMMAND:' + command)
    os.umask(0o077)
    def interrupted(signum, frame):
        raise Stop('INTERRUPTED')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    with contextlib.ExitStack() as stack:
        if mode != 'preflight':
            fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            stack.callback(os.close, fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if mode == 'rollback':
            manual_rollback(Path(argv[1]))
            return
        stage = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='az-paid-patient-')))
        state, protected = preflight(stage)
        if mode == 'preflight':
            mark('LIVE_CHANGES=NONE; APPLY_NOT_STARTED')
        elif state == 'TARGET':
            mark('ALREADY_INSTALLED=PASS; LIVE_CHANGES=NONE')
        else:
            apply(stage, protected)

if __name__ == '__main__':
    sys.dont_write_bytecode = True
    try:
        main(sys.argv[1:])
    except BaseException as exc:
        mark('PAID_PATIENT_OPERATION=STOP:' + (str(exc) if isinstance(exc, Stop) else type(exc).__name__))
        sys.exit(1)
