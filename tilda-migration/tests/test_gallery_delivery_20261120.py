"""Focused integrity tests for the one-command download wrapper."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
candidate = HERE / 'install_cases_gallery.py.in'
if not candidate.exists():
    candidate = HERE.parent / 'ops' / 'install_cases_gallery_20261120.py'
loader = importlib.machinery.SourceFileLoader('gallery_delivery', str(candidate))
spec = importlib.util.spec_from_loader(loader.name, loader)
app = importlib.util.module_from_spec(spec)
loader.exec_module(app)
parts_dir = HERE / 'delivery_parts'
manifest_path = HERE / 'delivery_manifest.json'
if not parts_dir.exists():
    parts_dir = HERE.parent / 'delivery' / 'cases-gallery'
    manifest_path = parts_dir / 'delivery-manifest.json'

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(manifest_path.read_text())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def fixture(self, path, expected, maximum):
        if path == app.MANIFEST_PATH:
            return json.dumps(self.manifest).encode()
        if path == app.INSTALLER_PATH:
            return b'# verified installer fixture\n'
        return (parts_dir / path.rsplit('/', 1)[-1]).read_bytes()

    def test_complete_ordered_archive(self):
        with patch.object(app, 'download', side_effect=self.fixture):
            archive, installer = app.materialize(self.directory)
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), app.ARCHIVE_SHA256)
        self.assertEqual(archive.stat().st_size, app.ARCHIVE_BYTES)
        self.assertTrue(installer.exists())

    def test_injected_path_rejected_before_asset_fetch(self):
        self.manifest['parts'][0]['path'] = '../../outside'
        with patch.object(app, 'download', side_effect=self.fixture) as mock:
            with self.assertRaises(app.Stop):
                app.materialize(self.directory)
        self.assertEqual(mock.call_count, 1)
        self.assertFalse(list(self.directory.iterdir()))

    def test_reordered_parts_rejected(self):
        parts = self.manifest['parts']
        parts[0], parts[1] = parts[1], parts[0]
        with patch.object(app, 'download', side_effect=self.fixture):
            with self.assertRaises(app.Stop):
                app.materialize(self.directory)

    def test_corrupt_archive_never_written(self):
        def corrupted(path, expected, maximum):
            data = self.fixture(path, expected, maximum)
            return bytes([data[0] ^ 1]) + data[1:] if path.endswith('data-00.part') else data
        with patch.object(app, 'download', side_effect=corrupted):
            with self.assertRaisesRegex(app.Stop, 'Archive integrity'):
                app.materialize(self.directory)
        self.assertFalse(list(self.directory.iterdir()))

    def test_redirect_rejected(self):
        with self.assertRaises(app.Stop):
            app.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.invalid/')

    def test_download_hash_and_size_guard(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, maximum): return b'unexpected'
        class Opener:
            def open(self, *args, **kwargs): return Response()
        with patch.object(app.urllib.request, 'build_opener', return_value=Opener()):
            with self.assertRaisesRegex(app.Stop, 'integrity mismatch'):
                app.download(app.INSTALLER_PATH, '0' * 64, 100)
            with self.assertRaisesRegex(app.Stop, 'integrity mismatch'):
                app.download(app.INSTALLER_PATH, hashlib.sha256(b'unexpected').hexdigest(), 2)

if __name__ == '__main__':
    unittest.main(verbosity=2)
