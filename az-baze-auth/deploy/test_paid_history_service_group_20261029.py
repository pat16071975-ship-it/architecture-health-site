"""Real local permission regression; no live accounts, services or data changes."""
import grp
import hashlib
import importlib.util
import os
from pathlib import Path
import pwd
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('delivery_group_check', HERE / 'paid-history-production-20261029.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class ServiceGroupTests(unittest.TestCase):
    def setUp(self):
        # CI already runs this suite under sudo. Never create/modify an account.
        self.assertEqual(os.geteuid(), 0, 'This isolated permission suite requires root')
        self.assertIsNotNone(shutil.which('runuser'))
        self.user = pwd.getpwnam('nobody')
        self.group = grp.getgrnam('www-data')
        self.assertNotEqual(self.user.pw_uid, 0)
        self.assertNotIn(self.group.gr_gid, os.getgrouplist(self.user.pw_name, self.user.pw_gid))
        self.tmp = tempfile.TemporaryDirectory(prefix='pr164-group-regression-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.chmod(0o755)
        self.app = self.root / 'app'
        (self.app / 'venv/bin').mkdir(parents=True, mode=0o755)
        (self.app / 'venv/bin/python').symlink_to(sys.executable)
        for key, value in {'APP': self.app, 'DB': self.root / 'unused.db'}.items():
            p = patch.object(d, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.raw = b'{"synthetic": "permission-test-only"}\n'
        self.config = d.config_path()
        self.config.write_bytes(self.raw)
        os.chown(self.config, 0, self.group.gr_gid)
        self.config.chmod(0o640)
        self.accounts = {str(p): p.read_bytes() for p in (Path('/etc/passwd'), Path('/etc/group'))}
        self.before = (self.config.read_bytes(), d.metadata(self.config))
        self.calls = []
        self.original_run = d.run

    def real_fixture_run(self, args, timeout=45):
        # Only substitute the local fixture account; preserve real -g/runuser/Python.
        self.assertEqual(args[:7], ['runuser', '-u', 'azadmin', '-g', 'www-data', '--', str(self.app / 'venv/bin/python')])
        self.calls.append(list(args))
        fixture_args = list(args)
        fixture_args[2] = self.user.pw_name
        return self.original_run(fixture_args, timeout)

    def assert_unchanged(self):
        self.assertEqual((self.config.read_bytes(), d.metadata(self.config)), self.before)
        for name, raw in self.accounts.items():
            self.assertEqual(Path(name).read_bytes(), raw)
        self.assertFalse(d.DB.exists())

    def test_exact_one_line_installer_delta(self):
        raw = (HERE / 'paid-history-production-20261029.py').read_bytes()
        new = b"['runuser','-u','azadmin','-g','www-data','--',str(APP/'venv/bin/python')"
        old = b"['runuser','-u','azadmin','--',str(APP/'venv/bin/python')"
        self.assertEqual(raw.count(new), 1)
        self.assertEqual(raw.count(old), 0)
        original = raw.replace(new, old, 1)
        self.assertEqual(d.blob(original), '12e8a038b800e3f44289e03ffa027081de144be0')
        self.assertEqual(d.TARGET_COMMIT, 'ffd0a876562c3b9c1e74410d9cf644aafb3fff39')

    def test_real_default_group_fails_and_explicit_service_group_reads(self):
        # Reproduce the old failure on the same file, without mocking permissions.
        old = ['runuser', '-u', self.user.pw_name, '--', str(self.app / 'venv/bin/python'), '-B', '-c',
               'import pathlib,sys; pathlib.Path(sys.argv[1]).read_bytes()', str(self.config)]
        with self.assertRaisesRegex(d.Stop, 'COMMAND_FAILED:runuser'):
            self.original_run(old)
        with patch.object(d, 'run', side_effect=self.real_fixture_run):
            d.verify_config_readable(self.raw)
        self.assertEqual(len(self.calls), 1)
        self.assert_unchanged()

    def test_readable_file_with_wrong_hash_still_fails(self):
        with patch.object(d, 'run', side_effect=self.real_fixture_run):
            with self.assertRaisesRegex(d.Stop, 'SERVICE_CANNOT_READ_CONFIG'):
                d.verify_config_readable(b'different synthetic contents')
        self.assert_unchanged()

    def test_public_mode_is_rejected_before_subprocess(self):
        self.config.chmod(0o644)
        with patch.object(d, 'run') as run:
            with self.assertRaisesRegex(d.Stop, 'UNSAFE_ACTIVE_CONFIG_PERMISSIONS'):
                d.verify_config_readable(self.raw)
            run.assert_not_called()
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o644)

    def test_wrong_file_group_is_rejected_without_fixing_permissions(self):
        os.chown(self.config, 0, self.user.pw_gid)
        with patch.object(d, 'run') as run:
            with self.assertRaisesRegex(d.Stop, 'UNSAFE_ACTIVE_CONFIG_PERMISSIONS'):
                d.verify_config_readable(self.raw)
            run.assert_not_called()
        self.assertEqual(self.config.stat().st_gid, self.user.pw_gid)

    def test_genuine_access_denial_still_stops(self):
        self.root.chmod(0o700)
        with patch.object(d, 'run', side_effect=self.real_fixture_run):
            with self.assertRaisesRegex(d.Stop, 'COMMAND_FAILED:runuser'):
                d.verify_config_readable(self.raw)
        self.assert_unchanged()


if __name__ == '__main__':
    unittest.main(verbosity=2)
