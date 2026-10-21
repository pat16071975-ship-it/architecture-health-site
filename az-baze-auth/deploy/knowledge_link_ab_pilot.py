#!/usr/bin/env python3
"""Connect one existing approved pilot to the existing protected test-media route.

Only a new hard-link directory entry is made. No app import, DB writes, HTTP
requests, chmod/chown, service changes, QNAP writes or media conversion. Existing
targets are never replaced. All filesystem roots must already exist. Full hash
reads can warm the OS cache; this is connection verification, NOT a cold benchmark.
"""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import sqlite3
import stat
import subprocess
import sys

EXPECTED = {
    'app.py': '00b03487d6216faf9d222b83b67e2b32a811be89',
    'server.py': '029b4b42a6176d50b9424de987575d880f54ef30',
    'knowledge_catalog.py': '5913a73639f517f48737a80667ef3007940a374c',
}
DEFAULTS = {
    'AZBAZE_DB': '/var/lib/az-baze/auth.db',
    'AZBAZE_KNOWLEDGE_ROOT': '/mnt/qnap-obuchenie',
    'AZBAZE_KNOWLEDGE_TEST_ROOT': '/var/lib/az-baze/knowledge-test',
}
CHUNK = 4 * 1024 * 1024

class Stop(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Stop(message)


def plain(path: Path, directory=False):
    require(path.is_absolute(), 'Path must be absolute')
    for p in (path, *path.parents):
        require(not p.is_symlink(), 'Symlinked path rejected')
    require(path.is_dir() if directory else path.is_file(), 'Required path is missing: ' + str(path))
    return path


def snapshot(path: Path):
    plain(path)
    s = path.stat()
    require(stat.S_ISREG(s.st_mode) and s.st_size > 0, 'Empty/nonregular file')
    # A hard link changes ctime/nlink, but not payload, mode, owner or mtime.
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode, s.st_uid, s.st_gid)


def checksum(path: Path):
    before = snapshot(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        require((s.st_dev, s.st_ino) == before[:2], 'File changed before reading')
        h = hashlib.sha256()
        for block in iter(lambda: f.read(CHUNK), b''):
            h.update(block)
    require(snapshot(path) == before, 'File changed during reading')
    return h.hexdigest()


def batch_record(manifest, basename, entry_id, size):
    require(manifest.get('batch') == 'AZ-VIDEO-BATCH-001' and
            manifest.get('status') == 'PILOT_EXPORT_ONLY', 'Unexpected batch manifest')
    matches = [x for x in manifest['files'] if x.get('input') == basename]
    require(len(matches) == 1, 'Pilot must have exactly one batch record')
    row = matches[0]
    p = PurePosixPath(row['original'])
    require(not p.is_absolute() and '..' not in p.parts and p.as_posix() == row['original']
            and '\\' not in str(p) and all(ord(x) >= 32 for x in str(p))
            and p.suffix.lower() == '.mp4', 'Unsafe or renamed source path')
    require(hashlib.sha256(str(p).encode()).hexdigest()[:32] == entry_id, 'Material ID mismatch')
    require(type(row['bytes']) is int and row['bytes'] == size and size > 0, 'Batch size mismatch')
    require(re.fullmatch(r'[0-9a-f]{64}', row['sha256']) is not None, 'Invalid batch checksum')
    return row


def catalog_check(db_path, entry_id, relative, actual):
    # Read-only connection, no schema initialization, no application bootstrap.
    plain(db_path)
    with contextlib.closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True, timeout=10)) as c:
        c.execute('PRAGMA query_only=ON')
        a = c.execute('SELECT relative_path,size_bytes,modified_at,hidden,entry_type,media_kind '
                      'FROM knowledge_entries WHERE id=?', (entry_id,)).fetchone()
        b = c.execute('SELECT relative_path,source_size,source_mtime,status '
                      'FROM knowledge_video_compat WHERE entry_id=?', (entry_id,)).fetchone()
    size, mtime = actual[2], str(actual[3] // 1_000_000_000)
    require(a == (relative, size, mtime, 0, 'file', 'video'), 'Working catalog changed; stop')
    require(b == (relative, size, mtime, 'source_ok'), 'Working playback is not source_ok; stop')
    return a, b


def connect(source, target, expected_sha, size, readable, final_guard):
    """No overwrite. Rollback removes only the new link created by this call."""
    plain(source)
    plain(target.parent, directory=True)
    require(source != target, 'Input must differ from target')
    require(source.stat().st_dev == target.parent.stat().st_dev, 'Hard link would cross filesystems')
    require(snapshot(source)[2] == size and checksum(source) == expected_sha, 'Local pilot checksum mismatch')
    before = snapshot(source)
    dfd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    made = False
    created_identity = None
    try:
        try:
            fcntl.flock(dfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise Stop('Another test-file operation holds the directory lock') from e
        if os.path.lexists(target):
            require(not target.is_symlink(), 'Existing symlink will not be replaced')
            require(snapshot(target)[2] == size and checksum(target) == expected_sha,
                    'A different test file already exists; nothing replaced')
            reused = True
        else:
            require(snapshot(source) == before, 'Pilot changed before linking')
            os.link(source, target.name, dst_dir_fd=dfd, follow_symlinks=False)
            made = True
            created = target.lstat()
            created_identity = (created.st_dev, created.st_ino)
            require(snapshot(target) == before, 'New link identity mismatch')
            os.fsync(dfd)
            reused = False
        readable(target)
        final_guard()
        require(snapshot(source) == before and checksum(target) == expected_sha,
                'Pilot changed after connection')
        return reused
    except BaseException:
        if made:
            try:
                s = os.stat(target.name, dir_fd=dfd, follow_symlinks=False)
                if (s.st_dev, s.st_ino) == created_identity:
                    os.unlink(target.name, dir_fd=dfd)
                    os.fsync(dfd)
            except FileNotFoundError:
                pass
        raise
    finally:
        os.close(dfd)


def service_context():
    r = subprocess.run(['systemctl', 'show', 'az-baze-auth', '-p', 'ActiveState', '-p', 'MainPID'],
                       capture_output=True, text=True, check=True, timeout=10)
    props = dict(x.split('=', 1) for x in r.stdout.splitlines() if '=' in x)
    require(props.get('ActiveState') == 'active', 'Application service is not active')
    pid = int(props.get('MainPID', '0'))
    require(pid > 1, 'Service PID unavailable')
    proc = Path('/proc') / str(pid)
    require((proc / 'cwd').resolve() == Path('/opt/az-baze-auth'), 'Unexpected service working directory')
    # Only these path settings are retained; no credentials are displayed or used.
    env = {}
    for item in (proc / 'environ').read_bytes().split(b'\0'):
        key, sep, value = item.partition(b'=')
        if sep and key.decode('ascii', 'ignore') in DEFAULTS:
            env[key.decode('ascii')] = value.decode('utf-8')
    paths = {k: Path(env.get(k, v)) for k, v in DEFAULTS.items()}
    # Fail closed on unreviewed overrides rather than modify configuration.
    require(all(str(paths[k]) == v for k, v in DEFAULTS.items()), 'Service path override requires review')
    status = dict(x.split(':', 1) for x in (proc / 'status').read_text().splitlines() if ':' in x)
    uid = int(status['Uid'].split()[1]); gid = int(status['Gid'].split()[1])
    groups = [int(x) for x in status['Groups'].split()]
    require(uid != 0, 'Expected an unprivileged service identity')
    return pid, paths, uid, gid, groups


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--input', required=True)
    p.add_argument('--entry-id', required=True)
    p.add_argument('--expected-bytes', type=int, required=True)
    args = p.parse_args()
    require(args.apply and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'az-server',
            'Requires --apply as root on az-server')
    require(re.fullmatch(r'[0-9a-f]{32}', args.entry_id) is not None, 'Invalid entry ID')
    require(Path(args.input).name == args.input and args.input.lower().endswith('.mp4')
            and '\\' not in args.input and all(ord(c) >= 32 for c in args.input), 'Invalid pilot basename')
    runtime = plain(Path('/opt/az-baze-auth'), directory=True)
    for name, expected in EXPECTED.items():
        data = plain(runtime / name).read_bytes()
        require(hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == expected,
                'Runtime changed: ' + name)
    ctx = service_context()
    pid, paths, uid, gid, groups = ctx
    source_root = plain(paths['AZBAZE_KNOWLEDGE_ROOT'], directory=True)
    test_root = plain(paths['AZBAZE_KNOWLEDGE_TEST_ROOT'], directory=True)
    require(test_root.stat().st_dev == Path('/').stat().st_dev, 'Test directory is not on local system disk')
    mount = subprocess.run(['findmnt', '-J', '-T', str(source_root), '-o', 'TARGET,FSTYPE,OPTIONS'],
                           capture_output=True, text=True, check=True, timeout=10)
    mounts = json.loads(mount.stdout)['filesystems']
    require(len(mounts) == 1 and mounts[0]['target'] == str(source_root)
            and mounts[0]['fstype'] == 'cifs' and 'ro' in mounts[0]['options'].split(','),
            'Expected read-only QNAP mount')
    manifest_path = plain(Path('/var/lib/az-baze/video-batches/AZ-VIDEO-BATCH-001/MANIFEST.json'))
    record = batch_record(json.loads(manifest_path.read_text(encoding='utf-8')),
                          args.input, args.entry_id, args.expected_bytes)
    source = plain(Path('/var/lib/az-baze/pilot-remux') / args.input)
    qnap = plain(source_root / record['original'])
    target = test_root / (args.entry_id + '.mp4')
    require(snapshot(qnap)[2] == args.expected_bytes and checksum(qnap) == record['sha256'],
            'QNAP and approved batch bytes differ')
    qnap_before = snapshot(qnap)
    catalog_check(paths['AZBAZE_DB'], args.entry_id, record['original'], qnap_before)

    def readable(path):
        # Match file identity in the service namespace and open using its OS UID/GID.
        visible = Path('/proc') / str(pid) / 'root' / str(path).lstrip('/')
        require(visible.stat().st_dev == path.stat().st_dev and visible.stat().st_ino == path.stat().st_ino,
                'Service sees a different file')
        code = 'import sys; f=open(sys.argv[1],"rb"); assert f.read(1); f.seek(-1,2); assert f.read(1); f.close()'
        r = subprocess.run([sys.executable, '-B', '-c', code, str(path)], cwd='/',
                           user=uid, group=gid, extra_groups=groups, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
                           env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'})
        require(r.returncode == 0, 'Service cannot read pilot; permissions were not changed')

    def final_guard():
        require(service_context() == ctx, 'Service changed during operation')
        require(snapshot(qnap) == qnap_before, 'QNAP source changed during operation')
        catalog_check(paths['AZBAZE_DB'], args.entry_id, record['original'], qnap_before)

    reused = connect(source, target, record['sha256'], args.expected_bytes, readable, final_guard)
    print('TEST_FILE_CONNECTED=PASS; SERVICE_READ=PASS; REUSED=' + str(reused))
    print('FULLHD_SAME_BYTES=PASS; BYTES=' + str(args.expected_bytes))
    print('TEST_FILE=' + str(target))
    print('VPS_TEST_PATH=/knowledge-test-media/' + args.entry_id)
    print('QNAP_PATH=/knowledge-media/' + args.entry_id)
    print('DB_WRITES=NONE; QNAP_WRITES=NONE; CONVERSION=NONE; PERMISSION_CHANGES=NONE')
    print('APP_CODE_CHANGES=NONE; SERVICE_CHANGES=NONE; WORKING_CARD_CHANGES=NONE')
    print('HASH_READS_MAY_WARM_OS_CACHE=YES; BROWSER_VERIFICATION=PENDING')

if __name__ == '__main__':
    try:
        main()
    except (Stop, OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        raise SystemExit('TEST_FILE_CONNECTED=STOP: ' + str(exc))
