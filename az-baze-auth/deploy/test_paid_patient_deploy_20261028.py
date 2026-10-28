"""Isolated delivery tests. No network, live filesystem, or systemd calls."""
import importlib.util
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).with_name('paid-patient-production-20261028.py')
spec = importlib.util.spec_from_file_location('delivery', PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='az-paid-patient-lab-')
        self.root = Path(self.temp.name)
        self.app = self.root/'app'
        self.reports = self.root/'reports'
        self.db = self.root/'auth.db'
        self.backups = self.root/'backups'
        self.stage = self.root/'stage'
        for path in (self.app, self.reports, self.backups, self.stage, self.app/'templates'):
            path.mkdir(parents=True, exist_ok=True)
        self.protected = self.app/'unrelated.py'
        self.protected.write_text('# untouched\n')
        conn = sqlite3.connect(self.db)
        conn.execute('CREATE TABLE report_data(id INTEGER PRIMARY KEY, amount REAL, data BLOB)')
        conn.execute('INSERT INTO report_data VALUES(1, 123.45, ?)', (b'\x00\xff',))
        conn.commit()
        conn.close()
        self.base = {}
        self.target = {}
        self.files = []
        for index, (rel, _, _) in enumerate(m.FILES):
            base = ('# base ' + str(index) + '\n').encode()
            target = ('# target ' + str(index) + '\n').encode()
            live = self.app/rel.split('/',1)[1] if rel.startswith('az-baze-auth/') else self.reports/rel.split('/',1)[1]
            live.write_bytes(base)
            live.chmod(0o640)
            staged = self.stage/rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(target)
            self.base[rel], self.target[rel] = base, target
            self.files.append((rel, m.blob(base), m.blob(target)))
        self.patchers = [
            patch.object(m,'APP',self.app), patch.object(m,'REPORTS',self.reports),
            patch.object(m,'DB',self.db), patch.object(m,'BACKUPS',self.backups),
            patch.object(m,'FILES',tuple(self.files)), patch.object(m,'LOCK',self.root/'lock'),
            patch.object(m,'health',return_value=True), patch.object(m,'service'),
            patch.object(m,'stage_targets'), patch.object(m,'mark'),
        ]
        results = [p.start() for p in self.patchers]
        self.service = results[7]
        self.initial = m.db_state()
        self.protected_initial = m.protected_state()

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.temp.cleanup()

    def checkpoint(self):
        paths = list(self.backups.glob('paid-patients-*'))
        self.assertEqual(len(paths),1)
        return paths[0]

    def unchanged(self):
        self.assertEqual(m.db_state(),self.initial)
        self.assertEqual(m.protected_state(),self.protected_initial)

    def test_readonly_connection_rejects_writes(self):
        with m.read_db(self.db) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('UPDATE report_data SET amount=0')
        self.unchanged()

    def test_preflight_no_service_actions(self):
        state, protected = m.preflight(self.stage)
        self.assertEqual(state,'BASE')
        self.assertEqual(protected,self.protected_initial)
        self.service.assert_not_called()
        self.assertEqual(list(self.backups.iterdir()),[])
        self.unchanged()

    def test_success_seven_files_and_backup(self):
        m.apply(self.stage,self.protected_initial)
        self.assertEqual(m.live_state(),'TARGET')
        self.assertEqual([x.args[0] for x in self.service.call_args_list],['stop','start'])
        backup = self.checkpoint()
        self.assertEqual((backup/'auth.db').stat().st_mode & 0o777,0o600)
        for rel, _, _ in self.files:
            self.assertEqual(m.live_path(rel).read_bytes(),self.target[rel])
            self.assertEqual((backup/'files'/rel).read_bytes(),self.base[rel])
            self.assertEqual(m.live_path(rel).stat().st_mode & 0o777,0o640)
        self.unchanged()

    def test_partial_install_rolls_back(self):
        original = m.atomic_copy
        calls = 0
        def failing(source,dest,meta):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise m.Stop('SIMULATED_INSTALL_FAILURE')
            return original(source,dest,meta)
        with patch.object(m,'atomic_copy',side_effect=failing):
            with self.assertRaises(m.Stop):
                m.apply(self.stage,self.protected_initial)
        self.assertEqual(m.live_state(),'BASE')
        self.unchanged()

    def test_start_failure_recovers_base(self):
        starts = 0
        def failing(action):
            nonlocal starts
            if action == 'start':
                starts += 1
                if starts == 1:
                    raise m.Stop('SIMULATED_HEALTH_FAILURE')
        self.service.side_effect = failing
        with self.assertRaises(m.Stop):
            m.apply(self.stage,self.protected_initial)
        self.assertEqual(m.live_state(),'BASE')
        self.assertEqual(starts,2)
        self.unchanged()

    def test_backup_failure_no_install_and_service_recovery(self):
        with patch.object(m,'backup_db',side_effect=m.Stop('SIMULATED_BACKUP_FAILURE')):
            with self.assertRaises(m.Stop):
                m.apply(self.stage,self.protected_initial)
        self.assertEqual(m.live_state(),'BASE')
        self.assertEqual([x.args[0] for x in self.service.call_args_list],['stop','start'])
        self.unchanged()

    def test_new_database_write_is_never_lost_by_rollback(self):
        written = False
        def activity(action):
            nonlocal written
            if action == 'start' and not written:
                conn = sqlite3.connect(self.db)
                conn.execute('INSERT INTO report_data VALUES(2, 999, NULL)')
                conn.commit()
                conn.close()
                written = True
        self.service.side_effect = activity
        with self.assertRaisesRegex(m.Stop,'DB_CHANGED_AFTER_START'):
            m.apply(self.stage,self.protected_initial)
        self.assertEqual(m.live_state(),'BASE')
        with m.read_db(self.db) as conn:
            self.assertEqual(conn.execute('SELECT amount FROM report_data WHERE id=2').fetchone(),(999.0,))

    def test_unknown_base_stops_before_service(self):
        m.live_path(self.files[0][0]).write_text('newer unknown version')
        with self.assertRaisesRegex(m.Stop,'LIVE_BLOB_MISMATCH'):
            m.apply(self.stage,self.protected_initial)
        self.service.assert_not_called()
        self.assertEqual(list(self.backups.iterdir()),[])

    def test_mixed_state_rejected(self):
        rel = self.files[0][0]
        m.live_path(rel).write_bytes(self.target[rel])
        with self.assertRaisesRegex(m.Stop,'MIXED_RUNTIME_STATE'):
            m.preflight(self.stage)
        self.service.assert_not_called()

    def test_protected_runtime_drift_preserved(self):
        self.protected.write_text('# newer independent edit\n')
        with self.assertRaisesRegex(m.Stop,'PROTECTED_RUNTIME_CHANGED'):
            m.apply(self.stage,self.protected_initial)
        self.assertEqual(m.live_state(),'BASE')
        self.assertEqual(self.protected.read_text(),'# newer independent edit\n')

    def test_manual_file_rollback_keeps_database(self):
        m.apply(self.stage,self.protected_initial)
        conn = sqlite3.connect(self.db)
        conn.execute('UPDATE report_data SET amount=456')
        conn.commit()
        conn.close()
        later = m.db_state()
        m.manual_rollback(self.checkpoint())
        self.assertEqual(m.live_state(),'BASE')
        self.assertEqual(m.db_state(),later)

    def test_rollback_rejects_newer_runtime(self):
        m.apply(self.stage,self.protected_initial)
        self.service.reset_mock()
        m.live_path(self.files[0][0]).write_text('different future release')
        with self.assertRaisesRegex(m.Stop,'ROLLBACK_NEWER_RUNTIME'):
            m.manual_rollback(self.checkpoint())
        self.service.assert_not_called()

    def test_corrupted_backup_rejected(self):
        m.apply(self.stage,self.protected_initial)
        checkpoint = self.checkpoint()
        (checkpoint/'files'/self.files[0][0]).write_text('corruption')
        with self.assertRaisesRegex(m.Stop,'CORRUPT_BACKUP'):
            m.validate_backup(checkpoint)

    def test_bad_token_cannot_write(self):
        with self.assertRaisesRegex(m.Stop,'USAGE'):
            m.main(['--apply','wrong'])
        self.service.assert_not_called()
        self.unchanged()

    def test_symlink_rejected(self):
        rel = self.files[0][0]
        live = m.live_path(rel)
        live.unlink()
        live.symlink_to(self.protected)
        with self.assertRaisesRegex(m.Stop,'UNSAFE_FILE'):
            m.live_state()
        self.service.assert_not_called()

if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Run isolated tests as root in disposable CI/container; no production access is used.')
    unittest.main(verbosity=2)
