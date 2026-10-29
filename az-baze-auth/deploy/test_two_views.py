import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('restore',HERE/'restore-two-views-20261029.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
SOURCE=Path(os.environ.get('TWO_VIEWS_BASE',str(HERE/'base')))

class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name);self.reports=p/'reports';self.reports.mkdir()
        self.backups=p/'backups';self.backups.mkdir()
        self.original={}
        for n in d.FILES:
            raw=(SOURCE/'reports'/n).read_bytes()
            self.assertEqual(d.blob(raw),d.FILES[n][0])
            (self.reports/n).write_bytes(raw);(self.reports/n).chmod(0o640)
            self.original[n]=raw
        self.other=p/'auth.db';self.other.write_bytes(b'do-not-open')
        self.config=p/'paid-period-confirmations-v1.json';self.config.write_text('do-not-change')
        self.output=io.StringIO();r=contextlib.redirect_stdout(self.output);r.__enter__();self.addCleanup(r.__exit__,None,None,None)
        self.checks=[]
    def health(self):self.checks.append('read-only-health')
    def install(self):d.apply_files(self.reports,self.backups,self.health)
    def base(self):self.assertEqual(d.state(self.reports)[0],'BASE')
    def test_exactly_one_replacement_in_each_file(self):
        for n,b in self.original.items():
            new=d.transform(n,b)
            self.assertEqual(new.decode().replace(d.FILES[n][3],d.FILES[n][2],1).encode(),b)
    def test_two_labels_and_default_compare(self):
        out=d.transform('period-view.js',self.original['period-view.js']).decode()
        self.assertIn(d.NEW_SELECT,out);self.assertNotIn(d.OLD_SELECT,out)
    def test_success_metadata_backup_and_data_unchanged(self):
        before={n:d.metadata(self.reports/n) for n in d.FILES}
        self.install()
        self.assertEqual(d.state(self.reports)[0],'TARGET')
        self.assertEqual({n:d.metadata(self.reports/n) for n in d.FILES},before)
        self.assertEqual(self.other.read_bytes(),b'do-not-open')
        self.assertEqual(self.config.read_text(),'do-not-change')
        saved=next(self.backups.iterdir())
        for n,b in self.original.items():self.assertEqual((saved/n).read_bytes(),b)
        self.assertEqual(self.checks,['read-only-health','read-only-health'])
    def test_repeat_is_noop(self):
        self.install();count=len(list(self.backups.iterdir()));self.install()
        self.assertEqual(len(list(self.backups.iterdir())),count)
        self.assertIn('ALREADY_INSTALLED=PASS',self.output.getvalue())
    def test_unknown_revision_stops(self):
        (self.reports/'index.html').write_text('newer unrelated change')
        with self.assertRaisesRegex(d.Stop,'UNKNOWN_LIVE_FILE'):self.install()
        self.assertFalse(list(self.backups.iterdir()))
    def test_mixed_state_stops(self):
        n='index.html';(self.reports/n).write_bytes(d.transform(n,self.original[n]))
        with self.assertRaisesRegex(d.Stop,'MIXED'):self.install()
        self.assertFalse(list(self.backups.iterdir()))
    def test_symlink_stops(self):
        path=self.reports/'index.html';target=self.reports/'copy';path.rename(target);path.symlink_to(target)
        with self.assertRaisesRegex(d.Stop,'UNSAFE_PATH'):self.install()
    def test_partial_write_failure_rolls_back(self):
        orig=d.atomic_write;calls=[]
        def fail(*a):
            calls.append(a[0].name)
            if len(calls)==2:raise OSError('synthetic write error')
            return orig(*a)
        with patch.object(d,'atomic_write',fail):
            with self.assertRaisesRegex(OSError,'synthetic'):self.install()
        self.base()
        for n,b in self.original.items():self.assertEqual((self.reports/n).read_bytes(),b)
    def test_post_health_failure_rolls_back(self):
        def health():
            self.checks.append(1)
            if len(self.checks)==2:raise d.Stop('synthetic health error')
        with self.assertRaisesRegex(d.Stop,'synthetic'):
            d.apply_files(self.reports,self.backups,health)
        self.base();self.assertIn('FILE_ROLLBACK=PASS',self.output.getvalue())
    def test_failed_pre_health_never_writes(self):
        with self.assertRaisesRegex(d.Stop,'not active'):
            d.apply_files(self.reports,self.backups,lambda:(_ for _ in ()).throw(d.Stop('not active')))
        self.base();self.assertFalse(list(self.backups.iterdir()))
    def test_bad_token_prevents_live_reads(self):
        with patch.object(d.os,'geteuid',return_value=0),patch.object(d.socket,'gethostname',return_value='az-server'),patch.object(d,'state') as state:
            with self.assertRaisesRegex(d.Stop,'WRONG_APPLY_TOKEN'):d.main(['--apply','wrong'])
            state.assert_not_called()
    def test_preflight_no_backup_or_writes(self):
        with patch.object(d,'ROOT',self.reports),patch.object(d.os,'geteuid',return_value=0),patch.object(d.socket,'gethostname',return_value='az-server'),patch.object(d,'health',self.health):
            d.main(['--preflight'])
        self.base();self.assertFalse(list(self.backups.iterdir()));self.assertIn('APPLY_NOT_STARTED',self.output.getvalue())
    def test_not_enough_backup_space(self):
        from types import SimpleNamespace
        with patch.object(d.shutil,'disk_usage',return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(d.Stop,'BACKUP_SPACE_LOW'):self.install()
        self.base();self.assertFalse(list(self.backups.iterdir()))
    def test_transformation_rejects_unknown_source(self):
        with self.assertRaisesRegex(d.Stop,'SOURCE_VERSION_MISMATCH'):
            d.transform('period-view.js',b'not the approved revision')

if __name__=='__main__':unittest.main(verbosity=2)
