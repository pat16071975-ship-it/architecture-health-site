"""Guarded delivery regressions using temporary files/DB and mocked systemd/network."""
import ast
import contextlib
import importlib.util
import io
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BASE = 'f7155be56c3ad753435ddc5057718b44320f0dd8'


def schema(path):
    tree = ast.parse(path.read_bytes())
    return next(ast.literal_eval(n.value) for n in tree.body
                if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SCHEMA' for t in n.targets))


class InstallerTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('pr246', ROOT / 'ops/apply_pr246_guarded.py')
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        m = self.m
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        m.DB = self.root / 'auth.db'
        m.BACKUP_ROOT = self.root / 'backups'
        m.BACKUP_ROOT.mkdir()
        m.STATE = self.root / 'retry.json'
        m.STATE.write_bytes(b'{"preserve":"retry state"}')
        m.GATE = self.root / 'retry.lock'
        m.GATE.touch()
        m.LOCK = self.root / 'installer.lock'
        m.live = lambda name: self.root / 'live' / name
        self.raw = {n: (ROOT / n).read_bytes() for n, _, _ in m.ROWS}
        self.old = {}
        for name, old, new in m.ROWS:
            self.assertEqual(m.blob(self.raw[name]), new)
            m.live(name).parent.mkdir(parents=True, exist_ok=True)
            if old:
                value = subprocess.check_output(['git', 'show', BASE + ':' + name], cwd=ROOT)
                self.assertEqual(m.blob(value), old)
                self.old[name] = value
                m.live(name).write_bytes(value)
                m.live(name).chmod(0o640)
        for name, expected in m.DEPENDENCIES.items():
            value = (ROOT / name).read_bytes()
            self.assertEqual(m.blob(value), expected)
            m.live(name).parent.mkdir(parents=True, exist_ok=True)
            m.live(name).write_bytes(value)
        with sqlite3.connect(m.DB) as c:
            c.executescript(schema(ROOT / 'az-baze-auth/financial_model.py'))
            c.executescript(schema(ROOT / 'az-baze-auth/financial_model_v2.py'))
            c.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY, secret BLOB); INSERT INTO users VALUES(1,X'00ff12');"
                "CREATE TABLE reports(id INTEGER PRIMARY KEY, value REAL); INSERT INTO reports VALUES(1,NULL),(2,0),(3,-35.75);")
            for variant in ('financial_model', 'financial_model_v2'):
                c.execute('INSERT INTO '+variant+'_plan VALUES(?,?,?,?,?)',
                          (2027,4,'{"payer_checks":[null,0,12345]}','[null,0,-5]','[8]'))
                c.execute('INSERT INTO '+variant+'_history VALUES(?,?,?,?,?,?,?,?)',
                          (1,2027,4,1,'2026-11-20','keep','{"old":true}','{"new":true}'))
        with sqlite3.connect(m.DB) as c:
            self.before = m.schema_objects(c)
            self.tables = [n for n, r in self.before.items() if r[0] == 'table']
            self.data = m.data_fingerprint(c, self.tables)
        self.db_bytes = m.DB.read_bytes()
        self.active = {s: True for s in (m.SERVICE, *m.TIMERS)}
        self.calls = []

        def run(*args):
            self.calls.append(args)
            self.assertEqual(args[0], 'systemctl')
            unit = args[-1]
            self.assertIn(unit, self.active)
            if args[1] == 'is-active':
                self.assertTrue(self.active[unit])
            elif args[1] in ('start', 'stop'):
                self.active[unit] = args[1] == 'start'
            else:
                self.fail(str(args))

        m.run = run
        m.download = lambda name: self.raw[name]
        m.health = lambda: self.assertTrue(self.active[m.SERVICE])
        m.wait_health = m.health
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(sys, 'argv', ['installer', '--apply']))
        self.stack.enter_context(patch.object(m.os, 'geteuid', return_value=0))
        self.stack.enter_context(patch.object(m.os, 'chown'))
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))

    def preserved(self, files_old=False):
        m = self.m
        self.assertEqual(m.DB.read_bytes(), self.db_bytes)
        with sqlite3.connect(m.DB) as c:
            self.assertEqual(m.schema_objects(c), self.before)
            self.assertEqual(m.data_fingerprint(c, self.tables), self.data)
        if files_old:
            for name, old, new in m.ROWS:
                if old:
                    self.assertEqual(m.live(name).read_bytes(), self.old[name])
                else:
                    self.assertFalse(m.live(name).exists())
        self.assertTrue(all(self.active.values()))
        self.assertEqual(m.STATE.read_bytes(), b'{"preserve":"retry state"}')

    def test_success_backup_all_records_payload_and_permissions(self):
        m = self.m
        m.main()
        self.preserved()
        for name, old, new in m.ROWS:
            self.assertEqual(m.blob(m.live(name).read_bytes()), new)
            self.assertEqual(m.live(name).stat().st_mode & 0o777, 0o640)
        backup, = m.BACKUP_ROOT.glob('pr246-*')
        with sqlite3.connect(backup / 'auth.db') as c:
            self.assertEqual(m.schema_objects(c), self.before)
            self.assertEqual(m.data_fingerprint(c, self.tables), self.data)
        self.assertIn('PR246_PRODUCTION_DEPLOY=PASS; FILES=1', self.output.getvalue())
        self.assertIn('DB_WRITES_BY_INSTALLER=NONE; DB_SCHEMA=UNCHANGED', self.output.getvalue())

    def test_download_mismatch_stops_before_live_writes(self):
        self.m.download = lambda _: b'wrong payload'
        with self.assertRaisesRegex(RuntimeError, 'download hash mismatch'):
            self.m.main()
        self.preserved(files_old=True)
        self.assertNotIn(('systemctl', 'stop', self.m.SERVICE), self.calls)
        self.assertNotIn('PRODUCTION_DEPLOY=PASS', self.output.getvalue())

    def test_live_mismatch_is_not_overwritten(self):
        path = self.m.live('az-baze-auth/financial_model_v2.py')
        path.write_bytes(b'unexpected existing content')
        with self.assertRaisesRegex(RuntimeError, 'LIVE_BASE_MISMATCH'):
            self.m.main()
        self.assertEqual(path.read_bytes(), b'unexpected existing content')
        self.preserved()
        self.assertFalse(self.calls)

    def test_dependency_mismatch_stops_without_writes(self):
        path = self.m.live('az-baze-auth/financial_model.py')
        path.write_bytes(b'newer variant1 must not be replaced')
        with self.assertRaisesRegex(RuntimeError, 'LIVE_BASE_MISMATCH'):
            self.m.main()
        self.assertEqual(path.read_bytes(), b'newer variant1 must not be replaced')
        self.preserved(files_old=True)
        self.assertFalse(self.calls)

    def test_missing_schema_stops_before_service_stop(self):
        with sqlite3.connect(self.m.DB) as c:
            c.execute('DROP TABLE financial_model_v2_plan')
        before = self.m.DB.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'financial model schema missing'):
            self.m.main()
        self.assertEqual(self.m.DB.read_bytes(), before)
        self.assertFalse(self.calls)
        self.assertTrue(all(self.active.values()))
        for name, old, new in self.m.ROWS:
            self.assertEqual(self.m.live(name).read_bytes(), self.old[name])

    def test_mid_install_failure_restores_all_files(self):
        m = self.m
        original = m.install
        count = 0

        def fail_once(*args):
            nonlocal count
            count += 1
            if count == 1:
                original(*args)
                raise RuntimeError('synthetic write failure')
            return original(*args)

        m.install = fail_once
        with self.assertRaisesRegex(RuntimeError, 'synthetic write failure'):
            m.main()
        self.preserved(files_old=True)
        self.assertNotIn('PRODUCTION_DEPLOY=PASS', self.output.getvalue())

    def test_health_failure_restores_files_without_restoring_live_db(self):
        m = self.m
        count = 0

        def fail_once():
            nonlocal count
            count += 1
            if count == 1:
                raise RuntimeError('synthetic health failure')
            m.health()

        m.wait_health = fail_once
        with self.assertRaisesRegex(RuntimeError, 'synthetic health failure'):
            m.main()
        self.preserved(files_old=True)
        self.assertIn('DATABASE_RESTORE=NOT_RUN', self.output.getvalue())
        self.assertNotIn('PRODUCTION_DEPLOY=PASS', self.output.getvalue())

    def test_repeated_install_refused_without_changes(self):
        m = self.m
        m.main()
        with self.assertRaisesRegex(RuntimeError, 'LIVE_BASE_MISMATCH'):
            m.main()
        for name, old, new in m.ROWS:
            self.assertEqual(m.blob(m.live(name).read_bytes()), new)
        self.preserved()


if __name__ == '__main__':
    unittest.main()

