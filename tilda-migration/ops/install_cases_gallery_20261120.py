#!/usr/bin/env python3
"""Download one pinned, verified AZMEDNSK gallery release and run its installer."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

DATA_COMMIT = '69fabead23fe361c95bca7034563757d8ff26929'
MANIFEST_SHA256 = 'ffee3ff3926123c1b111163bea898d2447d3c5392c5f4d54712459c722a0c5cd'
INSTALLER_SHA256 = '91ec93f000736cb687ed23651c2b0383cf6e677ab4d24c7772dd774e7464f1f0'
ARCHIVE_SHA256 = 'df01b3a7ea5f8bbd4ac7fe6d8e706f88da24765eae67df8466765ddb7602f64e'
ARCHIVE_BYTES = 9337538
BASE = 'https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/' + DATA_COMMIT + '/'
MANIFEST_PATH = 'tilda-migration/delivery/cases-gallery/delivery-manifest.json'
INSTALLER_PATH = 'tilda-migration/ops/apply_cases_gallery_20261120.py'

class Stop(RuntimeError):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Stop('Unexpected download redirect')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def download(path, expected_sha, maximum):
    if not path.startswith('tilda-migration/') or '..' in path.split('/'):
        raise Stop('Invalid download path')
    for attempt in range(3):
        try:
            req = urllib.request.Request(BASE + path, headers={'User-Agent': 'AZMEDNSK-gallery-installer/1.0'})
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=45) as response:
                if response.status != 200:
                    raise Stop('Unexpected HTTP status')
                data = response.read(maximum + 1)
            if len(data) > maximum or digest(data) != expected_sha:
                raise Stop('Download integrity mismatch: ' + path.rsplit('/', 1)[-1])
            return data
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise Stop('HTTP ' + str(error.code) + ': ' + path.rsplit('/', 1)[-1]) from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 2:
                raise Stop('Download failed: ' + path.rsplit('/', 1)[-1]) from error
        time.sleep(1 + attempt)

def materialize(directory):
    raw = download(MANIFEST_PATH, MANIFEST_SHA256, 24000)
    manifest = json.loads(raw)
    if manifest.get('schema') != 1 or manifest.get('archive_sha256') != ARCHIVE_SHA256 or manifest.get('archive_bytes') != ARCHIVE_BYTES:
        raise Stop('Invalid release manifest')
    parts = manifest.get('parts')
    if not isinstance(parts, list) or len(parts) != 26:
        raise Stop('Invalid release part count')
    for index, part in enumerate(parts):
        name = 'data-%02d.part' % index
        if part.get('name') != name or part.get('path') != 'tilda-migration/delivery/cases-gallery/' + name or not isinstance(part.get('bytes'), int) or not 1 <= part['bytes'] <= 360000:
            raise Stop('Invalid release part')
    if sum(part['bytes'] for part in parts) != ARCHIVE_BYTES:
        raise Stop('Invalid release size')
    def get_part(part):
        data = download(part['path'], part['sha256'], part['bytes'])
        if len(data) != part['bytes']:
            raise Stop('Release part size mismatch')
        return data
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        blocks = list(executor.map(get_part, parts))
    archive_data = b''.join(blocks)
    if len(archive_data) != ARCHIVE_BYTES or digest(archive_data) != ARCHIVE_SHA256:
        raise Stop('Archive integrity mismatch')
    installer_data = download(INSTALLER_PATH, INSTALLER_SHA256, 200000)
    archive = directory / 'azmednsk-cases-gallery.pyz'
    installer = directory / 'apply_cases_gallery.py'
    archive.write_bytes(archive_data)
    installer.write_bytes(installer_data)
    print('DOWNLOAD=PASS; PARTS=26; ARCHIVE_SHA256=' + ARCHIVE_SHA256, flush=True)
    return archive, installer

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check-only', action='store_true')
    mode.add_argument('--verify-download', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='azmednsk-cases-gallery-') as temp:
        archive, installer = materialize(Path(temp))
        if args.verify_download:
            print('DELIVERY=PASS; SOURCE_COMMIT=' + DATA_COMMIT)
            return 0
        command = [sys.executable, '-B', str(installer), '--archive', str(archive), '--apply' if args.apply else '--check-only']
        return subprocess.run(command, check=False).returncode

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Stop, OSError, ValueError) as error:
        print('STOP=' + str(error), file=sys.stderr)
        sys.exit(1)
