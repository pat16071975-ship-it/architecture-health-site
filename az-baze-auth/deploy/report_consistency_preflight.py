#!/usr/bin/env python3
"""Read-only file/schema inspection. No app import, HTTP, or source/data writes."""
import contextlib
import hashlib
import os
from pathlib import Path
import socket
import sqlite3
import subprocess

TARGETS = {
    'daily_upload_core.py': ('c0c2faee2474d8b8a2f678898bbe0ec7053cfa37', '7a58de237753dff1d5e72a11c14744d68c39a324'),
    'templates/uploads.html': ('be36446bbeaa82fc9c27769a8cabb24978ec0d36', 'fc7913c0ab812c6709a778aa706d969e438d3c93'),
    'upload_history.py': (None, '1709e9e984f0f521e081061b45b564a12d84f244'),
}
REPORTS = {
    'index.html': ('a6febb1769603e02e1433fac1402d9796e4b9632', 'ebf7ae4d4e5e121f20cff2302206e9a5fc25e350'),
    'period-view.js': ('042d6574edc26fecc5c42893684175e858f0d59b', 'a3075abb77f562e8eddbde11364049c4362261e2'),
}
DEPENDENCIES = {
    'report_storage.py': '367592919431997255862ed179a5c6a4f93703ad',
    'paid_services.py': '76c1a33de8a711f85139a21aa5f66a48ea96910f',
    'paid_services_upload.py': 'd2e682540f831d5adafd9b19e753a01d53749349',
    'upload_reconcile.py': 'bce8384dbb8d4b6eacf98ad5d4d099705bf39914',
    'daily_upload.py': 'b767890804e1f61a247476f13f73c01e20628f39',
    'ident_import.py': 'e9950b781b0f4ebdfe044860b948813938a8089d',
}
COLUMNS = {
    'users': {'id', 'full_name'},
    'upload_log': {'id', 'data_date', 'status', 'details', 'completed_filename', 'services_filename', 'uploaded_by', 'created_at'},
    'audit_log': {'id', 'actor_user_id', 'action', 'details', 'created_at'},
}


def plain(path):
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Unsafe path')
    return path


def fingerprint(path):
    plain(path)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Unexpected source file')
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('Source changed during inspection')
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def version_state(actual, old, new):
    if actual == new:
        return 'TARGET'
    if actual == old:
        return 'BASE'
    return 'OTHER'


def inspect_schema(path):
    plain(path)
    if not path.is_file():
        raise ValueError('Database file missing')
    result = {}
    with contextlib.closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        for table, required in COLUMNS.items():
            present = {row[1] for row in conn.execute('PRAGMA table_info("' + table + '")')}
            result[table] = required <= present
    return result


def validate_inspection(states, dependencies_match, schemas):
    if (not dependencies_match or not schemas or not all(schemas.values())
            or not states or len(set(states)) != 1 or states[0] not in {'BASE', 'TARGET'}):
        raise ValueError('Source/schema mismatch; do not install blindly')
    return states[0]


def service():
    result = subprocess.run(['systemctl', 'show', 'az-baze-auth', '-p', 'ActiveState', '-p', 'MainPID'],
                            capture_output=True, text=True, timeout=10, check=True)
    props = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if props.get('ActiveState') != 'active' or not props.get('MainPID', '').isdigit() or int(props['MainPID']) < 2:
        raise ValueError('Application service not active')
    return int(props['MainPID'])


def main():
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'az-server':
        raise ValueError('Requires root on az-server')
    pid = service()
    proc = Path('/proc') / str(pid)
    runtime = Path('/opt/az-baze-auth')
    if (proc / 'cwd').resolve() != runtime:
        raise ValueError('Unexpected application directory')
    defaults = {'AZBAZE_SITE_ROOT': '/var/www/az-baze.ru', 'AZBAZE_DB': '/var/lib/az-baze/auth.db'}
    paths = dict(defaults)
    for item in (proc / 'environ').read_bytes().split(b'\0'):
        key, sep, value = item.partition(b'=')
        if key in {k.encode() for k in defaults} and sep:
            paths[key.decode()] = value.decode()
    if paths != defaults:
        raise ValueError('Nondefault application paths require review')
    print('SERVICE=active; RUNTIME=' + str(runtime))
    states = []
    for root, entries in ((runtime, TARGETS), (Path(paths['AZBAZE_SITE_ROOT']) / 'reports', REPORTS)):
        for name, (old, new) in entries.items():
            actual = fingerprint(root / name)
            state = version_state(actual, old, new)
            states.append(state)
            print('FILE=' + str(root / name) + '; BLOB=' + str(actual) + '; STATE=' + state)
    matched = True
    for name, expected in DEPENDENCIES.items():
        actual = fingerprint(runtime / name)
        ok = actual == expected
        matched = matched and ok
        print('DEPENDENCY=' + name + '; BLOB=' + str(actual) + '; MATCH=' + str(ok))
    schemas = inspect_schema(Path(paths['AZBAZE_DB']))
    for name, ok in schemas.items():
        print('SCHEMA=' + name + '; REQUIRED_COLUMNS=' + ('PASS' if ok else 'MISSING'))
    if service() != pid:
        raise ValueError('Application restarted during inspection')
    state = validate_inspection(states, matched, schemas)
    print('REPORT_CONSISTENCY_PREFLIGHT=PASS; SOURCE_STATE=' + state)
    print('DB_WRITES=NONE; FILE_CHANGES=NONE; SERVICE_CHANGES=NONE; DEPLOY=NONE')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        raise SystemExit('REPORT_CONSISTENCY_PREFLIGHT=STOP: ' + str(error))
