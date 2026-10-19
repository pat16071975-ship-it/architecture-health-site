#!/usr/bin/env python3
"""Read-only AZ-BAZE storage/deployment preflight. Does not import application code.

No service restart, mount change, conversion, SQL writes or QNAP writes.
Only whitelisted paths/statuses are printed; credentials and unit environments
are deliberately never printed. A successful inspection is NOT deployment PASS.
"""
from __future__ import annotations
import contextlib
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys

GIB = 1024 ** 3
SOURCE = Path('/mnt/qnap-obuchenie')
STATE = Path('/var/lib/az-baze')
RUNTIME = Path('/opt/az-baze-auth')
BASE_BLOBS = {
    'server.py': '029b4b42a6176d50b9424de987575d880f54ef30',
    'knowledge_catalog.py': '5913a73639f517f48737a80667ef3007940a374c',
    'knowledge_video_compat.py': '0aadec2e8fd3347a93ccb61689e42870cecb214b',
    'templates/knowledge_view.html': '1211888359da609778dc0a479399ccd88157ea3b',
}


def run(argv):
    p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, timeout=30, check=False)
    return p.returncode, p.stdout.strip()


def unescape_mount(value):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def mounts(text):
    result = []
    for line in text.splitlines():
        left, sep, right = line.partition(' - ')
        a, b = left.split(), right.split()
        if not sep or len(a) < 6 or len(b) < 3:
            continue
        # No mount source, usernames, passwords or arbitrary options in output.
        result.append({'target': unescape_mount(a[4]), 'fstype': b[0],
                       'read_only': 'ro' in a[5].split(',') or 'ro' in b[2].split(',')})
    return result


def summarize(rows):
    groups = {}
    for r in rows:
        status = r.get('status', '')
        codec = (r.get('video_codec') or '').lower()
        pix = (r.get('pix_fmt') or '').lower()
        audio = (r.get('audio_codec') or '').lower()
        size = int(r.get('source_size_bytes') or 0)
        if size < 0:
            raise ValueError('Negative source size')
        if status == 'SOURCE_OK':
            key = 'direct_candidates'
        elif status == 'NEEDS_PROXY' and codec == 'h264' and pix in {'yuv420p','yuvj420p'}:
            key = 'remux_candidates' if audio in {'aac','mp3','','none'} else 'audio_fix_candidates'
        elif status == 'NEEDS_PROXY' and codec in {'hevc','mpeg4','h264'}:
            key = 'transcode_candidates'
        else:
            key = 'unresolved'
        g = groups.setdefault(key, {'files': 0, 'source_bytes': 0})
        g['files'] += 1
        g['source_bytes'] += size
    return groups


def main():
    if socket.gethostname().split('.')[0] != 'az-server' or os.geteuid() != 0:
        print('STOP: запустите root на az-server; изменений нет')
        return 1
    print('=== AZ KNOWLEDGE PERMANENT READ-ONLY PREFLIGHT ===', flush=True)
    checks = []
    for service in ['az-baze-auth', 'az-baze-knowledge-video-worker']:
        rc, value = run(['systemctl','is-active',service])
        print('SERVICE', service, value if value in {'active','inactive','failed','activating'} else 'unknown')
        if service == 'az-baze-auth':
            checks.append(rc == 0)
    for name, expected in BASE_BLOBS.items():
        p = RUNTIME / name
        if not p.is_file() or p.is_symlink():
            print('RUNTIME', name, 'MISSING_OR_SYMLINK')
            checks.append(False)
            continue
        data = p.read_bytes()
        sha = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        print('RUNTIME', name, 'blob='+sha, 'EXPECTED' if sha == expected else 'CHANGED_REVIEW_REQUIRED')
        checks.append(sha == expected)
    for tool in ['ffmpeg','ffprobe']:
        p = shutil.which(tool)
        print('TOOL', tool, 'PRESENT' if p else 'MISSING')
        checks.append(bool(p))
    table = mounts(Path('/proc/self/mountinfo').read_text())
    source_mount = [m for m in table if m['target'] == str(SOURCE)]
    print('SOURCE_MOUNT', json.dumps(source_mount, ensure_ascii=False))
    checks.append(len(source_mount) == 1 and SOURCE.is_dir())
    for path in [Path('/'), SOURCE]:
        if path.is_dir():
            d = shutil.disk_usage(path)
            print('SPACE', str(path), 'total_bytes='+str(d.total), 'free_bytes='+str(d.free),
                  'free_GiB='+str(round(d.free/GIB,2)))
    # Enumerate only mounted storage targets, no remote sources/credentials.
    for m in table:
        p = Path(m['target'])
        if str(p).startswith(('/mnt/','/media/')) and p.is_dir():
            print('STORAGE_MOUNT', json.dumps(m, ensure_ascii=False),
                  'free_bytes='+str(shutil.disk_usage(p).free))
    inv = STATE / 'knowledge-video-inventory-v2.csv'
    if inv.is_file():
        with inv.open(encoding='utf-8-sig', newline='') as f:
            groups = summarize(list(csv.DictReader(f)))
        print('INVENTORY', json.dumps(groups, ensure_ascii=False, sort_keys=True))
        archive_bytes = sum(g['source_bytes'] for k,g in groups.items() if k not in {'direct_candidates','unresolved'})
        light_bytes = sum(groups.get(k,{}).get('source_bytes',0) for k in ['remux_candidates','audio_fix_candidates'])
        print('ARCHIVE_BYTES_KEEPING_LOOSE_ORIGINALS='+str(archive_bytes))
        print('LIGHT_COPY_INPUT_BYTES='+str(light_bytes))
        print('CAPACITY_NOTE=archives add source bytes; remux copies stay near source size; transcode output not yet measured for whole library')
        if SOURCE.is_dir() and archive_bytes + 20*GIB >= shutil.disk_usage(SOURCE).free:
            print('ALL_ARCHIVES_ON_CURRENT_QNAP=BLOCKED_OR_NO_OUTPUT_HEADROOM')
        else:
            print('ALL_ARCHIVES_ON_CURRENT_QNAP=OUTPUT_SIZE_AND_DESTINATION_STILL_REQUIRE_REVIEW')
    else:
        print('INVENTORY=MISSING'); checks.append(False)
    db = STATE / 'auth.db'
    if db.is_file():
        with contextlib.closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as c:
            c.execute('PRAGMA query_only=ON')
            print('CATALOG_VIDEO_COUNT='+str(c.execute("SELECT COUNT(*) FROM knowledge_entries WHERE hidden=0 AND entry_type='file' AND media_kind='video'").fetchone()[0]))
            exists = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='knowledge_video_compat'").fetchone()[0]
            if exists:
                print('LEGACY_VIDEO_STATES', c.execute('SELECT status,COUNT(*) FROM knowledge_video_compat GROUP BY status ORDER BY status').fetchall())
    else:
        print('CATALOG_DB=MISSING'); checks.append(False)
    pilot = STATE / 'pilot-remux/sklyarov-hevc10-crf18.mp4'
    print('CRF18_PILOT', 'bytes='+str(pilot.stat().st_size) if pilot.is_file() else 'MISSING')
    print('PERMANENT_DESTINATION=NOT_SELECTED_OR_CREATED_BY_THIS_CHECK')
    print('QNAP_WRITES=NONE; DB_WRITES=NONE; SERVICE_CHANGES=NONE; CONVERSION_STARTED=NO')
    print('PREFLIGHT_INSPECTION=' + ('PASS' if all(checks) else 'REVIEW_REQUIRED'))
    print('PERMANENT_LIBRARY_DEPLOY=NOT_PERFORMED')
    return 0 if all(checks) else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as e:
        print('PREFLIGHT_INSPECTION=FAIL;', type(e).__name__, str(e))
        raise SystemExit(1)
