#!/usr/bin/env python3
"""Register ONE verified, manually placed batch MP4 with the existing catalog.

No reindex, conversion, deletion, permission change, service restart or QNAP write.
Uses the existing source_ok route; does not install PR 130's permanent library.
"""
from __future__ import annotations
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import tempfile

RUNTIME_BLOBS = {
    'server.py': '029b4b42a6176d50b9424de987575d880f54ef30',
    'knowledge_catalog.py': '5913a73639f517f48737a80667ef3007940a374c',
    'knowledge_video_compat.py': '0aadec2e8fd3347a93ccb61689e42870cecb214b',
    'templates/knowledge_view.html': '1211888359da609778dc0a479399ccd88157ea3b',
}

class Stop(RuntimeError):
    pass


def need(condition, message):
    if not condition:
        raise Stop(message)


def safe_path(root: Path, relative: str) -> Path:
    need(isinstance(relative, str) and bool(relative), 'Empty relative path')
    p = PurePosixPath(relative)
    need(not p.is_absolute() and '..' not in p.parts and p.as_posix() == relative
         and relative != '.' and '\\' not in relative
         and not any(ord(c) < 32 for c in relative), 'Unsafe relative path')
    path = root.joinpath(*p.parts)
    for part in (path, *path.parents):
        need(not part.is_symlink(), 'Symlink in path')
    need(root.resolve() in path.resolve().parents, 'Path outside root')
    return path


def snapshot(path: Path) -> tuple:
    s = path.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_size > 0, 'Not a nonempty regular file')
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def sha256(path: Path) -> str:
    snapshot(path)
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_media(path: Path) -> dict:
    p = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                        '-of', 'json', str(path)], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=60, check=True)
    d = json.loads(p.stdout)
    videos = [s for s in d.get('streams', []) if s.get('codec_type') == 'video'
              and not (s.get('disposition') or {}).get('attached_pic')]
    audio = [s for s in d.get('streams', []) if s.get('codec_type') == 'audio']
    need(path.suffix.lower() == '.mp4' and len(videos) == 1 and len(audio) == 1,
         'Expected one real MP4 video and one audio track')
    v, a = videos[0], audio[0]
    need(v.get('codec_name') == 'h264' and v.get('pix_fmt') in {'yuv420p', 'yuvj420p'}
         and a.get('codec_name') == 'aac', 'File is not the verified H.264/AAC copy')
    duration = float(d['format']['duration'])
    need(math.isfinite(duration) and duration > 0, 'Invalid duration')
    return {'video': v, 'audio': a, 'duration_sec': duration}


def verify_item(source: Path, manifest: Path, batch: str, input_name: str,
                expected_size: int) -> dict:
    need(re.fullmatch(r'AZ-VIDEO-BATCH-[0-9]{3}', batch), 'Invalid batch ID')
    need(Path(input_name).name == input_name and input_name.lower().endswith('.mp4'),
         'Expected local pilot basename')
    snapshot(manifest)
    m = json.loads(manifest.read_text(encoding='utf-8'))
    need(m.get('batch') == batch and m.get('status') == 'PILOT_EXPORT_ONLY', 'Wrong batch')
    records = [r for r in m['files'] if r.get('input') == input_name]
    need(len(records) == 1, 'Ambiguous/missing batch item')
    r = records[0]
    path = safe_path(source, r['original'])
    # This operation is intentionally only for same-path replacements.
    # MOV/MTS -> a NEW .mp4 path needs a separately reviewed identity migration.
    need(path.suffix.lower() == '.mp4' and r['archive_name'] == 'video/' + r['original'],
         'Only same-path MP4 adoption is supported')
    before = snapshot(path)
    need(before[2] == r['bytes'] == expected_size, 'New file size differs')
    need(re.fullmatch(r'[0-9a-f]{64}', r['sha256']), 'Invalid manifest digest')
    need(sha256(path) == r['sha256'], 'New file SHA256 differs from exported copy')
    meta = read_media(path)
    expected_duration = float(r['duration_sec'])
    need(math.isfinite(expected_duration) and abs(meta['duration_sec'] - expected_duration) < 0.005,
         'New file duration differs')
    need(snapshot(path) == before, 'File changed during inspection')
    return {'id': hashlib.sha256(r['original'].encode()).hexdigest()[:32],
            'relative_path': r['original'], 'path': path, 'snapshot': before,
            'size': before[2], 'mtime': str(before[3] // 1_000_000_000),
            'sha256': r['sha256'], 'pix_fmt': meta['video']['pix_fmt']}


def write_json(path: Path, data: dict) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n'); f.flush(); os.fsync(f.fileno())


def read_rows(c, identity: str) -> tuple[dict, dict]:
    a = c.execute('SELECT * FROM knowledge_entries WHERE id=?', (identity,)).fetchone()
    b = c.execute('SELECT * FROM knowledge_video_compat WHERE entry_id=?', (identity,)).fetchone()
    need(a is not None and b is not None, 'Catalog or compatibility record missing')
    return dict(a), dict(b)


def sync_one(db: Path, item: dict, backup_root: Path, old_size: int, old_mtime: str,
             old_cache_size: int) -> dict:
    """Two UPDATEs in one transaction; durable exact-row backup before any UPDATE.

    Backup is intentionally just the two affected records, not a restoration of
    the whole live financial database. Errors before COMMIT roll back both rows.
    """
    snapshot(db)
    need(not backup_root.is_symlink() and backup_root.is_dir(), 'Backup directory missing')
    need(shutil.disk_usage(backup_root).free >= 1024 ** 3, 'Insufficient backup headroom')
    with contextlib.closing(sqlite3.connect(db.as_uri() + '?mode=rw', uri=True,
                                           timeout=15, isolation_level=None)) as c:
        c.row_factory = sqlite3.Row
        c.execute('BEGIN IMMEDIATE')
        try:
            need(snapshot(item['path']) == item['snapshot'], 'Source changed before transaction')
            old_entry, old_state = read_rows(c, item['id'])
            need(old_entry['relative_path'] == old_state['relative_path'] == item['relative_path']
                 and old_entry['entry_type'] == 'file' and old_entry['media_kind'] == 'video'
                 and old_entry['hidden'] == 0, 'Identity/visibility mismatch')
            sig = (item['size'], item['mtime'])
            current = (int(old_entry['size_bytes']), str(old_entry['modified_at']))
            state_sig = (int(old_state['source_size']), str(old_state['source_mtime']))
            if current == state_sig == sig and old_state['status'] == 'source_ok':
                c.rollback()
                return {'reused': True, 'backup': None, 'changes': 0}
            need(current == state_sig == (old_size, old_mtime)
                 and old_state['status'] == 'ready'
                 and old_state['output_size'] == old_cache_size,
                 'Live state changed or worker busy; no changes made')
            need(not c.execute("SELECT name FROM sqlite_master WHERE type='trigger' "
                 "AND tbl_name IN ('knowledge_entries','knowledge_video_compat')").fetchall(),
                 'Unexpected database triggers; review before writing')
            backup = Path(tempfile.mkdtemp(prefix='knowledge-adopt-' + item['id'][:8] + '-',
                                          dir=backup_root))
            stamp = datetime.now(timezone.utc).isoformat()
            new_entry = {**old_entry, 'size_bytes': item['size'],
                         'modified_at': item['mtime'], 'indexed_at': stamp}
            new_state = {**old_state, 'source_size': item['size'], 'source_mtime': item['mtime'],
                         'status': 'source_ok', 'codec_name': 'h264', 'pix_fmt': item['pix_fmt'],
                         'output_size': None, 'error': None, 'updated_at': stamp}
            write_json(backup / 'rows-before.json', {
                'schema': 1, 'entry_id': item['id'], 'entry': old_entry, 'state': old_state,
                'expected_entry_after': new_entry, 'expected_state_after': new_state,
                'verified_media_sha256': item['sha256'], 'created_at': stamp,
                'note': 'Backup of TWO records only. No file or full-DB restore.'})
            for directory in (backup, backup_root):
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try: os.fsync(fd)
                finally: os.close(fd)
            start_changes = c.total_changes
            need(c.execute('UPDATE knowledge_entries SET size_bytes=?,modified_at=?,indexed_at=? '
                           'WHERE id=? AND relative_path=?',
                           (item['size'], item['mtime'], stamp, item['id'], item['relative_path'])).rowcount == 1,
                 'Unexpected catalog row count')
            need(c.execute("UPDATE knowledge_video_compat SET source_size=?,source_mtime=?,"
                           "status='source_ok',codec_name='h264',pix_fmt=?,output_size=NULL,"
                           "error=NULL,updated_at=? WHERE entry_id=? AND relative_path=?",
                           (item['size'], item['mtime'], item['pix_fmt'], stamp,
                            item['id'], item['relative_path'])).rowcount == 1,
                 'Unexpected state row count')
            need(c.total_changes - start_changes == 2, 'Unexpected additional DB changes')
            need(read_rows(c, item['id']) == (new_entry, new_state), 'Transaction verification failed')
            need(snapshot(item['path']) == item['snapshot'], 'Source changed before commit')
            c.commit()
            return {'reused': False, 'backup': str(backup / 'rows-before.json'), 'changes': 2}
        except BaseException:
            if c.in_transaction:
                c.rollback()
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--batch', required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--expected-bytes', required=True, type=int)
    parser.add_argument('--old-bytes', required=True, type=int)
    parser.add_argument('--old-mtime', required=True)
    parser.add_argument('--old-cache-bytes', required=True, type=int)
    args = parser.parse_args()
    try:
        need(args.apply and socket.gethostname().split('.')[0] == 'az-server' and os.geteuid() == 0,
             'Requires --apply, root and az-server')
        os.umask(0o077)
        runtime = Path('/opt/az-baze-auth')
        for name, expected in RUNTIME_BLOBS.items():
            data = safe_path(runtime, name).read_bytes()
            got = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            need(got == expected, 'Runtime differs: ' + name)
        source = Path('/mnt/qnap-obuchenie')
        need(source.is_mount(), 'QNAP mount missing')
        opts = subprocess.run(['findmnt', '-n', '-o', 'OPTIONS', '--mountpoint', str(source)],
                              capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        need('ro' in opts.split(','), 'QNAP must remain read-only')
        need(re.fullmatch(r'AZ-VIDEO-BATCH-[0-9]{3}', args.batch), 'Invalid batch ID')
        manifest = safe_path(Path('/var/lib/az-baze/video-batches'), args.batch + '/MANIFEST.json')
        item = verify_item(source, manifest, args.batch, args.input, args.expected_bytes)
        # Verify the service account can actually read the manually placed media.
        subprocess.run(['runuser', '-u', 'azadmin', '--', 'test', '-r', str(item['path'])],
                       stdin=subprocess.DEVNULL, check=True, timeout=10)
        result = sync_one(Path('/var/lib/az-baze/auth.db'), item,
                          Path('/var/lib/az-baze/backups'), args.old_bytes,
                          args.old_mtime, args.old_cache_bytes)
        print('CATALOG_SYNC=PASS; DB_ROWS_CHANGED=' + str(result['changes']))
        print('VIDEO_STATE=source_ok; PLAYBACK=QNAP_DIRECT; REUSED=' + str(result['reused']))
        print('ENTRY_ID=' + item['id'])
        print('BYTES=' + str(item['size']))
        print('BACKUP=' + str(result['backup']))
        print('QNAP_WRITES=NONE; FILE_DELETIONS=NONE; CONVERSION=NONE; REINDEX=NONE')
        print('APP_CODE_CHANGES=NONE; SERVICE_CHANGES=NONE; PERMISSION_CHANGES=NONE')
        print('BROWSER_VERIFICATION=PENDING')
        return 0
    except (Stop, OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as e:
        print('CATALOG_SYNC=STOP: ' + str(e))
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
