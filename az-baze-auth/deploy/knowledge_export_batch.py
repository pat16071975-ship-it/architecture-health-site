#!/usr/bin/env python3
"""Export existing local pilot MP4s. No conversion, QNAP access or web changes.

A private JSON plan arrives on stdin; material names never need to enter Git.
This is a transport package, NOT an original backup or publication approval.
Only ffprobe runs. Output has owner-only permissions and no public HTTP route.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import zipfile

GIB = 1024 ** 3
CHUNK = 4 * 1024 ** 2
README = """AZ-BAZE: пакет пилотных совместимых видео для ручной передачи.
Это НЕ архив оригиналов. Оригиналы в этот пакет не включены.
В папке video сохранена исходная структура. MANIFEST.json содержит соответствия,
параметры, контрольные суммы и ограничения проверок. SHA256SUMS.txt — суммы видео.
Распакуйте отдельно от рабочей библиотеки. Не распаковывайте поверх оригиналов.
Сначала проверьте воспроизведение, звук, перемотку и файлы с замечаниями.
CRF18 требует визуальной проверки именно этой копии, не прежнего 720p-кэша.
Оригиналы сохраняются отдельно до согласованного архивирования/замены.
Автоматической раскладки, переиндексации или публикации этот пакет не выполняет.
После ручного размещения нужна проверка файлов и подключения к каталогу AZ-BAZE.
Никакие исходники и готовые партии с сервера автоматически не удаляются.
"""

class Stop(RuntimeError):
    pass


def canonical(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise Stop("Invalid path")
    if any(c in value for c in '\\:*?"<>|'):
        raise Stop("Unsafe/nonportable path")
    p = PurePosixPath(value)
    if p.is_absolute() or p.as_posix() != value or '..' in p.parts or value == '.':
        raise Stop("Unsafe/noncanonical path")
    if any(x.endswith((' ', '.')) for x in p.parts):
        raise Stop("Nonportable path ending")
    return p


def plain_directory(path: Path) -> Path:
    path = path.absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise Stop("Symlinked directory")
    if not path.is_dir():
        raise Stop("Directory missing: " + str(path))
    return path


def snapshot(path: Path) -> tuple:
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_size <= 0:
        raise Stop("Input must be a nonempty regular file: " + path.name)
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def read_hash(stream, guard=lambda: None) -> str:
    h = hashlib.sha256()
    for block in iter(lambda: stream.read(CHUNK), b''):
        guard()
        h.update(block)
    return h.hexdigest()


def file_hash(path: Path, guard=lambda: None) -> str:
    snapshot(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        return read_hash(f, guard)


def probe(path: Path) -> dict:
    proc = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                           '-of', 'json', str(path)], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode:
        raise Stop('ffprobe failed: ' + proc.stderr[-500:])
    d = json.loads(proc.stdout)
    videos = [s for s in d['streams'] if s.get('codec_type') == 'video'
              and not s.get('disposition', {}).get('attached_pic')]
    audios = [s for s in d['streams'] if s.get('codec_type') == 'audio']
    if len(videos) != 1 or len(audios) != 1:
        raise Stop('Pilot needs one real video and one audio track')
    v, a = videos[0], audios[0]
    if v.get('codec_name') != 'h264' or v.get('pix_fmt') not in {'yuv420p', 'yuvj420p'}:
        raise Stop('Expected H.264 8-bit 4:2:0 pilot')
    if a.get('codec_name') != 'aac':
        raise Stop('Expected AAC pilot audio')
    duration = float(d['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise Stop('Invalid duration')
    return {'video': {k: v.get(k) for k in ('codec_name', 'pix_fmt', 'width', 'height',
             'avg_frame_rate', 'sample_aspect_ratio', 'field_order')},
            'audio': {k: a.get(k) for k in ('codec_name', 'sample_rate', 'channels')},
            'duration_sec': duration}


def normalize_plan(plan: list) -> list:
    if not isinstance(plan, list) or not 1 <= len(plan) <= 100:
        raise Stop('Plan needs 1..100 entries')
    result, names, inputs = [], set(), set()
    for item in plan:
        source = canonical(item['input'])
        original = canonical(item['original'])
        if len(source.parts) != 1 or source.suffix.lower() != '.mp4':
            raise Stop('Input must be a local MP4 basename')
        if not original.suffix:
            raise Stop('Original extension missing')
        dest = original if original.suffix.lower() == '.mp4' else original.with_suffix('.mp4')
        archive_name = 'video/' + str(dest)
        if archive_name.casefold() in names or str(source) in inputs:
            raise Stop('Duplicate input or case-insensitive output collision')
        names.add(archive_name.casefold()); inputs.add(str(source))
        n, seconds = item['bytes'], item['duration_sec']
        if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
            raise Stop('Invalid expected size')
        if not isinstance(seconds, (float, int)) or not math.isfinite(seconds) or seconds <= 0:
            raise Stop('Invalid expected duration')
        note = item.get('note', '')
        if not isinstance(note, str) or len(note) > 2000:
            raise Stop('Invalid review note')
        result.append({'input': str(source), 'original': str(original),
                       'archive_name': archive_name, 'bytes': n,
                       'duration_sec': seconds, 'note': note})
    return result


def verify_zip(path: Path, manifest: dict, guard=lambda: None) -> None:
    with zipfile.ZipFile(path) as z:
        expected = [r['archive_name'] for r in manifest['files']] + [
            'MANIFEST.json', 'SHA256SUMS.txt', 'README.txt']
        if z.namelist() != expected:
            raise Stop('ZIP membership differs')
        if json.loads(z.read('MANIFEST.json')) != manifest:
            raise Stop('ZIP manifest differs')
        sums = ''.join(r['sha256'] + '  ' + r['archive_name'] + '\n' for r in manifest['files'])
        if z.read('SHA256SUMS.txt') != sums.encode('utf-8') or z.read('README.txt') != README.encode('utf-8'):
            raise Stop('ZIP instructions/checksum list differs')
        for row in manifest['files']:
            if z.getinfo(row['archive_name']).file_size != row['bytes']:
                raise Stop('ZIP member size differs')
            with z.open(row['archive_name']) as member:
                if read_hash(member, guard) != row['sha256']:
                    raise Stop('ZIP member checksum differs')


def build(plan, input_root: Path, output_root: Path, batch: str,
          reserve: int = 8 * GIB, limit: int = 5 * GIB) -> dict:
    if not re.fullmatch(r'AZ-VIDEO-BATCH-[0-9]{3}', batch):
        raise Stop('Invalid batch ID')
    plan = normalize_plan(plan)
    input_root = plain_directory(input_root)
    output_root = plain_directory(output_root)
    if input_root == output_root or input_root in output_root.parents or output_root in input_root.parents:
        raise Stop('Input and output roots overlap')
    if reserve < 0 or limit <= 0:
        raise Stop('Invalid disk limits')
    device = output_root.stat().st_dev
    def guard(additional=0):
        plain_directory(output_root)
        if output_root.stat().st_dev != device:
            raise Stop('Output volume changed')
        if shutil.disk_usage(output_root).free < reserve + additional:
            raise Stop('Insufficient free space; no finished files deleted')
    total = sum(x['bytes'] for x in plan)
    if total + 1024 * 1024 > limit:
        raise Stop('Batch exceeds size limit')
    lock_fd = os.open(output_root / '.export.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise Stop('Another export is running') from e
        final = output_root / batch
        if final.is_symlink():
            raise Stop('Symlinked final batch')
        if final.exists():
            plain_directory(final)
            for name in ('MANIFEST.json', batch + '.zip', batch + '.zip.sha256'):
                snapshot(final / name)
            manifest = json.loads((final / 'MANIFEST.json').read_text(encoding='utf-8'))
            if manifest.get('plan') != plan or manifest.get('batch') != batch or manifest.get('status') != 'PILOT_EXPORT_ONLY':
                raise Stop('Existing batch has a different plan or status')
            for row in manifest['files']:
                if file_hash(input_root / row['input'], guard) != row['sha256']:
                    raise Stop('Local pilot changed since export')
            verify_zip(final / (batch + '.zip'), manifest, guard)
            sha = file_hash(final / (batch + '.zip'), guard)
            if (final / (batch + '.zip.sha256')).read_text() != sha + '  ' + batch + '.zip\n':
                raise Stop('Package checksum differs')
            return {'path': str(final / (batch + '.zip')), 'sha256': sha, 'files': len(plan), 'reused': True}
        # Leave failed/aborted staging intact for inspection, never grow copies on retry.
        if list(output_root.glob('.' + batch + '.building-*')):
            raise Stop('An unfinished batch exists; inspect it before retrying')
        records = []
        for item in plan:
            path = input_root / item['input']
            before = snapshot(path)
            if before[2] != item['bytes']:
                raise Stop('Pilot size changed: ' + item['input'])
            meta = probe(path)
            if abs(meta['duration_sec'] - item['duration_sec']) > 0.005:
                raise Stop('Pilot duration changed: ' + item['input'])
            if snapshot(path) != before:
                raise Stop('Pilot changed during probe')
            records.append({**item, 'snapshot': list(before), 'media': meta})
        guard(total + 16 * 1024 * 1024)
        work = Path(tempfile.mkdtemp(prefix='.' + batch + '.building-', dir=output_root))
        package = work / (batch + '.zip')
        fd = os.open(package, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output, zipfile.ZipFile(output, 'w', zipfile.ZIP_STORED, allowZip64=True) as z:
            for record in records:
                path = input_root / record['input']
                if list(snapshot(path)) != record['snapshot']:
                    raise Stop('Pilot changed before packaging')
                h = hashlib.sha256()
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, 'rb') as inp, z.open(record['archive_name'], 'w', force_zip64=True) as dest:
                    for chunk in iter(lambda: inp.read(CHUNK), b''):
                        guard(len(chunk)); dest.write(chunk); h.update(chunk)
                if list(snapshot(path)) != record['snapshot']:
                    raise Stop('Pilot changed during packaging')
                record['sha256'] = h.hexdigest()
                print('PACKED', record['archive_name'], flush=True)
            manifest = {'schema': 1, 'batch': batch, 'status': 'PILOT_EXPORT_ONLY',
                        'original_backup': False, 'catalog_published': False,
                        'plan': plan, 'files': records}
            z.writestr('MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            z.writestr('SHA256SUMS.txt', ''.join(r['sha256'] + '  ' + r['archive_name'] + '\n' for r in records))
            z.writestr('README.txt', README)
        with package.open('rb') as durable:
            os.fsync(durable.fileno())
        if package.stat().st_size > limit:
            raise Stop('ZIP exceeds batch limit')
        verify_zip(package, manifest, guard)
        for r in records:
            if list(snapshot(input_root / r['input'])) != r['snapshot']:
                raise Stop('Pilot changed during verification')
        sha = file_hash(package, guard)
        for name, text in [('MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2)),
                           (batch + '.zip.sha256', sha + '  ' + batch + '.zip\n')]:
            fd = os.open(work / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text); f.flush(); os.fsync(f.fileno())
        guard()
        if final.exists():
            raise Stop('Final batch unexpectedly exists')
        os.rename(work, final)
        fd = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return {'path': str(final / (batch + '.zip')), 'sha256': sha, 'files': len(plan), 'reused': False}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--batch', required=True)
    args = p.parse_args()
    try:
        if not args.apply or socket.gethostname().split('.')[0] != 'az-server' or os.geteuid() != 0:
            raise Stop('Requires --apply and root on az-server')
        os.umask(0o077)
        plan = json.loads(sys.stdin.read(1024 * 1024))
        parent = plain_directory(Path('/var/lib/az-baze'))
        out = parent / 'video-batches'
        if not out.exists():
            out.mkdir(mode=0o700)
        out = plain_directory(out)
        if out.stat().st_uid != 0 or out.stat().st_mode & 0o077:
            raise Stop('Output directory must be root-owned and mode 0700')
        if out.stat().st_dev != Path('/').stat().st_dev:
            raise Stop('This pilot export must stay on the local VPS system volume')
        result = build(plan, Path('/var/lib/az-baze/pilot-remux'), out, args.batch)
        print('BATCH_EXPORT=PASS; FILES=' + str(result['files']) + '; REUSED=' + str(result['reused']))
        print('ZIP=' + result['path'])
        print('ZIP_SHA256=' + result['sha256'])
        print('ZIP_BYTES=' + str(Path(result['path']).stat().st_size))
        print('FREE_GiB=' + str(round(shutil.disk_usage(out).free / GIB, 2)))
        print('QNAP_READS=NONE; QNAP_WRITES=NONE; CONVERSION=NONE; ORIGINAL_ARCHIVE=NOT_CREATED')
        print('DB_CHANGES=NONE; SERVICE_CHANGES=NONE; DOWNLOAD=NOT_YET; CATALOG_PUBLICATION=NOT_PERFORMED')
        return 0
    except (Stop, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, subprocess.SubprocessError) as e:
        print('BATCH_EXPORT=STOP: ' + str(e), file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
