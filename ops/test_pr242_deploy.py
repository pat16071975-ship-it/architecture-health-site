"""Installer regressions: only temporary files/SQLite and mocked systemd/network."""
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
BASE = '910aef586d1e73c25cc40cc891c3d02e4da80d4c'


def schema(path):
    tree = ast.parse(path.read_bytes())
    return next(ast.literal_eval(n.value) for n in tree.body
                if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SCHEMA' for t in n.targets))


class InstallerTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('pr242', ROOT / 'ops/apply_pr242_guarded.py')
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
        with sqlite3.connect(m.DB) as c:
            c.executescript(schema(ROOT / 'az-baze-auth/medical_supplies.py'))
            c.executescript('CREATE TABLE users(id INTEGER PRIMARY KEY, permission TEXT, secret BLOB);'
                "INSERT INTO users VALUES(1,'unchanged',X'00ff12');"
                'CREATE TABLE reports(id INTEGER PRIMARY KEY, value REAL, note TEXT);'
                "INSERT INTO reports VALUES(1,NULL,'нет данных'),(2,0,'ноль'),(3,-35.75,'возврат');"
                "INSERT INTO med_supply_items VALUES(1,'Старый материал','шт','2026-10',10125,1000,1250,0,0,2);"
                "INSERT INTO med_supply_periods VALUES(1,'2026-10',0,1000,1);"
                "INSERT INTO med_supply_coverage VALUES(1,'2026-10',1,1);"
                "INSERT INTO med_supply_history VALUES(1,1,NULL,'item_create',1,'2026-10-01',NULL,'{}');")
        with sqlite3.connect(m.DB) as c:
            self.before = m.schema_objects(c)
            self.tables = [n for n, r in self.before.items() if r[0] == 'table']
            self.data = m.data_fingerprint(c, self.tables)
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

    def preserved(self, schema_same=False, files_old=False):
        m = self.m
        with sqlite3.connect(m.DB) as c:
            self.assertEqual(m.data_fingerprint(c, self.tables), self.data)
            objects = m.schema_objects(c)
            self.assertEqual({n: objects[n] for n in self.before}, self.before)
            if schema_same:
                self.assertEqual(objects, self.before)
        if files_old:
            for name, old, new in m.ROWS:
                if old:
                    self.assertEqual(m.live(name).read_bytes(), self.old[name])
                else:
                    self.assertFalse(m.live(name).exists())
        self.assertTrue(all(self.active.values()))
        self.assertEqual(m.STATE.read_bytes(), b'{"preserve":"retry state"}')

    def test_success_backup_all_records_and_payload(self):
        m = self.m
        m.main()
        self.preserved()
        for name, old, new in m.ROWS:
            self.assertEqual(m.blob(m.live(name).read_bytes()), new)
        with sqlite3.connect(m.DB) as c:
            self.assertEqual(set(m.schema_objects(c)) - set(self.before),
                             set(m.TABLES) | {'sqlite_autoindex_med_supply_stock_imports_1'})
            for name in m.TABLES:
                self.assertEqual(c.execute('SELECT count(*) FROM ' + name).fetchone()[0], 0)
        backup, = m.BACKUP_ROOT.glob('pr242-*')
        with sqlite3.connect(backup / 'auth.db') as c:
            self.assertEqual(m.schema_objects(c), self.before)
            self.assertEqual(m.data_fingerprint(c, self.tables), self.data)
        self.assertIn('PR242_PRODUCTION_DEPLOY=PASS; FILES=4', self.output.getvalue())
        self.assertIn('EXISTING_DATA=UNCHANGED', self.output.getvalue())

    def test_download_mismatch_stops_before_live_writes(self):
        self.m.download = lambda _: b'wrong payload'
        with self.assertRaisesRegex(RuntimeError, 'download hash mismatch'):
            self.m.main()
        self.preserved(schema_same=True, files_old=True)
        self.assertNotIn(('systemctl', 'stop', self.m.SERVICE), self.calls)
        self.assertNotIn('PRODUCTION_DEPLOY=PASS', self.output.getvalue())

    def test_live_mismatch_is_not_overwritten(self):
        path = self.m.live('az-baze-auth/medical_supplies.py')
        path.write_bytes(b'unexpected existing content')
        with self.assertRaisesRegex(RuntimeError, 'LIVE_BASE_MISMATCH'):
            self.m.main()
        self.assertEqual(path.read_bytes(), b'unexpected existing content')
        self.preserved(schema_same=True)
        self.assertFalse(self.calls)

    def test_schema_dml_is_rejected_and_transaction_rolled_back(self):
        m = self.m
        statements = m.schema_statements(self.raw)
        original = m.schema_objects
        count = 0

        def changed(c):
            nonlocal count
            count += 1
            if count == 3:
                c.execute('UPDATE reports SET value=99 WHERE id=2')
            return original(c)

        m.schema_objects = changed
        with self.assertRaisesRegex(RuntimeError, 'unexpected DML'):
            m.initialize_empty_tables(statements)
        self.preserved(schema_same=True, files_old=True)

    def test_extra_schema_is_rejected_and_rolled_back(self):
        m = self.m
        statements = m.schema_statements(self.raw)
        original = m.schema_objects
        count = 0

        def changed(c):
            nonlocal count
            count += 1
            if count == 3:
                c.execute('ALTER TABLE reports ADD COLUMN stray TEXT')
            return original(c)

        m.schema_objects = changed
        with self.assertRaisesRegex(RuntimeError, 'unexpected schema change'):
            m.initialize_empty_tables(statements)
        self.preserved(schema_same=True, files_old=True)

    def test_schema_source_accepts_only_one_new_table(self):
        m = self.m
        statements = m.schema_statements(self.raw)
        self.assertEqual(len(statements), 1)
        key = 'az-baze-auth/medical_supplies_import.py'
        for before, after in [(b'CREATE TABLE IF NOT EXISTS med_supply_stock_imports', b'CREATE TABLE IF NOT EXISTS other_stock'),
                              (b'sha256 TEXT NOT NULL UNIQUE', b'sha256 TEXT UNIQUE'),
                              (b'CREATE TABLE IF NOT EXISTS med_supply_stock_imports (', b'DELETE FROM med_supply_items; CREATE TABLE IF NOT EXISTS med_supply_stock_imports (')]:
            bad = dict(self.raw)
            bad[key] = bad[key].replace(before, after, 1)
            with self.assertRaises(RuntimeError):
                m.schema_statements(bad)
        self.preserved(schema_same=True, files_old=True)

    def test_existing_new_table_stops_without_overwriting(self):
        m = self.m
        with sqlite3.connect(m.DB) as c:
            c.execute('CREATE TABLE med_supply_stock_imports(id INTEGER PRIMARY KEY, payload TEXT)')
            c.execute("INSERT INTO med_supply_stock_imports VALUES(1,'00123')")
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            m.main()
        self.preserved(files_old=True)
        with sqlite3.connect(m.DB) as c:
            self.assertEqual(c.execute('SELECT * FROM med_supply_stock_imports').fetchall(), [(1,'00123')])
        self.assertNotIn(('systemctl','stop',m.SERVICE),self.calls)

    def test_mid_install_failure_restores_all_files(self):
        m = self.m
        original = m.install
        count = 0

        def fail_once(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError('synthetic write failure')
            return original(*args)

        m.install = fail_once
        with self.assertRaisesRegex(RuntimeError, 'synthetic write failure'):
            m.main()
        self.preserved(schema_same=True, files_old=True)
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
        with sqlite3.connect(m.DB) as c:
            for name in m.TABLES:
                self.assertEqual(c.execute('SELECT count(*) FROM ' + name).fetchone()[0], 0)
        self.assertIn('DATABASE_RESTORE=NOT_RUN', self.output.getvalue())
        self.assertNotIn('PRODUCTION_DEPLOY=PASS', self.output.getvalue())

    def test_repeated_install_refused_without_changes(self):
        m = self.m
        m.main()
        with sqlite3.connect(m.DB) as c:
            before = '\n'.join(c.iterdump())
        with self.assertRaisesRegex(RuntimeError, 'LIVE_BASE_MISMATCH'):
            m.main()
        with sqlite3.connect(m.DB) as c:
            self.assertEqual('\n'.join(c.iterdump()), before)
        self.preserved()


if __name__ == '__main__':
    unittest.main()
