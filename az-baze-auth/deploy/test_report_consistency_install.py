"""Isolated file/SQLite delivery tests; systemd and HTTP are simulated, never live."""
import contextlib
import io
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import report_consistency_install as m

APPROVED = Path(os.environ.get('AZ_INSTALL_APPROVED_ROOT', Path(__file__).resolve().parents[2]))
BASE = Path(os.environ.get('AZ_INSTALL_BASE_ROOT', APPROVED/'baseline'))

@contextlib.contextmanager
def synthetic_db(path):
    connection=sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()

class FakeService:
    def __init__(self, layout):
        self.layout=layout;self.active=True;self.calls=[];self.fail_wait=False;self.fail_read=False;self.start_write=False
    def health(self): return self.active
    def context(self): return (os.geteuid(),os.getegid(),tuple(os.getgroups()))
    def stop(self): self.calls.append('stop');self.active=False
    def start(self):
        self.calls.append('start');self.active=True
        if self.start_write:
            self.start_write=False
            with synthetic_db(self.layout.db) as c: c.execute("INSERT INTO report_data VALUES('new legitimate input','new value')")
    def wait(self):
        self.calls.append('wait')
        if self.fail_wait:
            self.fail_wait=False;raise m.Stop('SYNTHETIC_HEALTH_FAILURE')
    def readable(self,context):
        self.calls.append('readable')
        if self.fail_read: raise m.Stop('SYNTHETIC_FILE_ACCESS_FAILURE')
        for n in m.FILES: self.layout.live(n).open('rb').close()

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.layout=m.Layout(root/'app',root/'site/reports',root/'state/auth.db',root/'backups')
        for p in (self.layout.app,self.layout.reports,self.layout.db.parent,self.layout.backups): p.mkdir(parents=True)
        self.sources={}
        for n,(old,new,sha) in m.FILES.items():
            target=root/'stage'/n;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(APPROVED/n,target);self.sources[n]=target
            self.assertEqual(m.blob(target.read_bytes()),new)
            self.assertEqual(m.sha(target.read_bytes()),sha)
            if old:
                dest=self.layout.live(n);dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(BASE/n,dest);os.chmod(dest,0o640)
                self.assertEqual(m.blob(dest.read_bytes()),old)
        for name,sha in m.DEPENDENCIES.items():
            p=self.layout.app/name;shutil.copyfile(BASE/'az-baze-auth'/name,p)
            self.assertEqual(m.blob(p.read_bytes()),sha)
        (self.layout.app/'unrelated.py').write_text('SENTINEL = "not to be modified"\n')
        (self.layout.reports/'forecast.html').write_text('<h1>Unchanged financial formulas</h1>')
        with synthetic_db(self.layout.db) as c:
            c.executescript('''
                CREATE TABLE users(id INTEGER PRIMARY KEY, full_name TEXT, password_hash TEXT);
                INSERT INTO users VALUES(1,'synthetic private name','synthetic secret hash');
                CREATE TABLE permissions(user_id INTEGER, section TEXT);
                INSERT INTO permissions VALUES(1,'upload_history');
                CREATE TABLE report_data(id TEXT PRIMARY KEY, payload TEXT);
                INSERT INTO report_data VALUES('synthetic history','{"cashTotal":1234.56}');
                CREATE TABLE provider_registry(name TEXT, direction TEXT);
                INSERT INTO provider_registry VALUES('Synthetic provider','dent');
                CREATE TABLE upload_log(id INTEGER PRIMARY KEY,data_date TEXT,status TEXT,details TEXT,
                    completed_filename TEXT,services_filename TEXT,uploaded_by INTEGER,created_at TEXT);
                INSERT INTO upload_log VALUES(1,'2026-09-30','imported','synthetic private details',
                    'fixture.xlsx','fixture2.xlsx',1,'2026-10-01T01:00:00+00:00');
                CREATE TABLE audit_log(id INTEGER PRIMARY KEY,actor_user_id INTEGER,action TEXT,
                    details TEXT,created_at TEXT);
                CREATE TABLE knowledge_entries(id TEXT,relative_path TEXT);
                INSERT INTO knowledge_entries VALUES('fixture','keep QNAP catalogue untouched');
            ''')
        self.before_db=m.database_state(self.layout.db)
        self.before_files={n:(p.read_bytes(),m.meta(p)) for n in m.FILES if (p:=self.layout.live(n)).exists()}
        self.protected=self.layout.protected()
        self.service=FakeService(self.layout)
        self.log=io.StringIO()
        handlers={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
        self.addCleanup(lambda:[signal.signal(s,h) for s,h in handlers.items()])

    def go(self):
        with contextlib.redirect_stdout(self.log): return m.install(self.layout,self.sources,self.service)

    def unchanged(self):
        self.assertEqual(self.layout.footprint(),'BASE')
        for n,(raw,meta) in self.before_files.items():
            self.assertEqual(self.layout.live(n).read_bytes(),raw)
            self.assertEqual(m.meta(self.layout.live(n)),meta)
        self.assertFalse(self.layout.live('az-baze-auth/upload_history.py').exists())
        self.assertEqual(self.layout.protected(),self.protected)

    def test_success_only_five_targets_verified_backup_and_data_unchanged(self):
        backup=self.go()
        self.assertEqual(self.layout.footprint(),'TARGET')
        self.assertEqual(self.layout.protected(),self.protected)
        self.assertEqual(m.database_state(self.layout.db),self.before_db)
        self.assertEqual(m.database_state(backup/'database.sqlite3'),self.before_db)
        self.assertEqual(self.service.calls.count('stop'),1)
        self.assertEqual(self.service.calls.count('start'),1)
        self.assertTrue(self.service.active)
        self.assertEqual(backup.stat().st_mode & 0o777,0o700)
        self.assertEqual((backup/'database.sqlite3').stat().st_mode & 0o777,0o600)
        for n,(_,meta) in self.before_files.items(): self.assertEqual(m.rights(m.meta(self.layout.live(n))),m.rights(meta))
        for value in ('synthetic private name','synthetic secret hash','synthetic private details','1234.56'):
            self.assertNotIn(value,self.log.getvalue())
        self.assertIn('USER_UI_VERIFICATION=PENDING',self.log.getvalue())

    def test_repeat_is_noop_without_second_backup_or_restart(self):
        self.go();calls=list(self.service.calls);backups=list(self.layout.backups.iterdir())
        self.go()
        self.assertEqual(self.service.calls.count('start'),calls.count('start'))
        self.assertEqual(list(self.layout.backups.iterdir()),backups)
        self.assertIn('REUSED=True',self.log.getvalue())

    def test_unknown_base_stops_before_service_or_backup(self):
        self.layout.live('reports/index.html').write_text('unexpected change')
        with self.assertRaises(m.Stop): self.go()
        self.assertEqual(self.service.calls,[]);self.assertEqual(list(self.layout.backups.iterdir()),[])

    def test_mixed_state_is_not_finished_blindly(self):
        self.layout.live('reports/index.html').write_bytes(self.sources['reports/index.html'].read_bytes())
        with self.assertRaises(m.Stop): self.go()
        self.assertEqual(self.service.calls,[])

    def test_different_new_module_is_not_overwritten(self):
        p=self.layout.live('az-baze-auth/upload_history.py');p.write_text('different')
        with self.assertRaises(m.Stop): self.go()
        self.assertEqual(p.read_text(),'different');self.assertEqual(self.service.calls,[])

    def test_dependency_drift_is_not_allowed(self):
        p=self.layout.app/'paid_services.py';p.write_text('other importer')
        with self.assertRaises(m.Stop): self.go()
        self.assertEqual(p.read_text(),'other importer');self.assertEqual(self.service.calls,[])

    def test_symlink_target_is_rejected(self):
        p=self.layout.live('reports/index.html');p.unlink();p.symlink_to(self.sources['reports/index.html'])
        with self.assertRaises(m.Stop): self.go()
        self.assertTrue(p.is_symlink());self.assertEqual(self.service.calls,[])

    def test_hardlinked_target_is_rejected(self):
        p=self.layout.live('reports/index.html');os.link(p,p.with_name('link.html'))
        with self.assertRaises(m.Stop): self.go()
        self.assertEqual(self.service.calls,[])

    def test_missing_schema_is_not_initialized(self):
        with synthetic_db(self.layout.db) as c:c.execute('DROP TABLE audit_log')
        state=m.database_state(self.layout.db)
        with self.assertRaises(m.Stop):self.go()
        self.assertEqual(m.database_state(self.layout.db),state);self.assertEqual(self.service.calls,[])

    def test_unhealthy_service_is_not_restarted(self):
        self.service.active=False
        with self.assertRaises(m.Stop):self.go()
        self.assertEqual(self.service.calls,[])

    def test_corrupt_download_fails_validation(self):
        self.sources['reports/index.html'].write_text('bad download')
        with self.assertRaises(m.Stop):m.validate_targets(self.sources,Path(sys.executable))
        self.unchanged();self.assertEqual(self.service.calls,[])

    def test_exact_targets_compile_and_jinja_parse(self):
        m.validate_targets(self.sources,Path(sys.executable))

    def test_partial_install_failure_restores_existing_and_removes_only_new_file(self):
        original=m.atomic_write
        def fail(p,raw,meta):
            if p==self.layout.live('reports/period-view.js') and m.blob(raw)==m.FILES['reports/period-view.js'][1]:
                raise OSError('synthetic disk error')
            return original(p,raw,meta)
        with patch.object(m,'atomic_write',side_effect=fail),self.assertRaises(OSError):self.go()
        self.unchanged();self.assertEqual(m.database_state(self.layout.db),self.before_db)
        self.assertTrue(self.service.active);self.assertIn('FILE_ROLLBACK=PASS',self.log.getvalue())

    def test_failure_after_replace_is_rolled_back(self):
        original=m.atomic_write
        def fail(p,raw,meta):
            original(p,raw,meta)
            if p==self.layout.live('az-baze-auth/upload_history.py'):raise OSError('synthetic fsync fault')
        with patch.object(m,'atomic_write',side_effect=fail),self.assertRaises(OSError):self.go()
        self.unchanged();self.assertTrue(self.service.active)

    def test_health_failure_restores_all_five_targets(self):
        self.service.fail_wait=True
        with self.assertRaises(m.Stop):self.go()
        self.unchanged();self.assertTrue(self.service.active)
        self.assertIn('DB_RESTORE=NOT_PERFORMED',self.log.getvalue())

    def test_read_access_failure_rolls_back_without_chmod_existing_paths(self):
        self.service.fail_read=True
        with self.assertRaises(m.Stop):self.go()
        self.unchanged();self.assertTrue(self.service.active)

    def test_concurrent_real_data_never_overwritten_from_backup(self):
        self.service.start_write=True
        with self.assertRaisesRegex(m.Stop,'DB_CHANGED_AFTER_START'):self.go()
        self.unchanged();self.assertTrue(self.service.active)
        with synthetic_db(self.layout.db) as c:
            self.assertEqual(c.execute("SELECT payload FROM report_data WHERE id='new legitimate input'").fetchone(),('new value',))
        self.assertIn('DB_RESTORE=NOT_PERFORMED',self.log.getvalue())

    def test_corrupt_file_backup_is_detected_before_stop(self):
        original=m.atomic_write
        def bad(p,raw,meta):
            original(p,raw,meta)
            if self.layout.backups in p.parents:p.write_bytes(b'corrupt backup')
        with patch.object(m,'atomic_write',side_effect=bad),self.assertRaisesRegex(m.Stop,'FILE_BACKUP_MISMATCH'):self.go()
        self.assertEqual(self.service.calls,[]);self.unchanged()

    def test_backup_failure_restarts_unchanged_original(self):
        with patch.object(m,'database_backup',side_effect=m.Stop('BACKUP_TEST_FAILURE')),self.assertRaises(m.Stop):self.go()
        self.unchanged();self.assertTrue(self.service.active)

    def test_metadata_and_xattrs_preserved(self):
        p=self.layout.live('az-baze-auth/daily_upload_core.py')
        os.setxattr(p,'user.az_delivery_test',b'synthetic')
        before=m.meta(p)
        self.go()
        self.assertEqual(m.rights(m.meta(p)),m.rights(before))
        self.assertEqual(os.getxattr(self.layout.live('az-baze-auth/upload_history.py'),'user.az_delivery_test'),b'synthetic')

    def test_low_disk_stops_without_service_changes(self):
        fake=shutil._ntuple_diskusage(100,99,1)
        with patch.object(m.shutil,'disk_usage',return_value=fake),self.assertRaises(m.Stop):self.go()
        self.unchanged();self.assertEqual(self.service.calls,[])

    def test_readonly_journal_smoke_preserves_database(self):
        with contextlib.redirect_stdout(self.log):m.journal_smoke(self.sources['az-baze-auth/upload_history.py'],self.layout.db)
        self.assertEqual(m.database_state(self.layout.db),self.before_db)

    def test_snapshot_is_consistent_with_wal_writer_after_read_transaction(self):
        with synthetic_db(self.layout.db) as c:c.execute('PRAGMA journal_mode=WAL')
        baseline=m.database_state(self.layout.db)
        with m.ro_db(self.layout.db) as r:
            self.assertEqual(m.db_state(r),baseline)
            with synthetic_db(self.layout.db) as w:w.execute("INSERT INTO report_data VALUES('concurrent','preserve')")
            dst=self.layout.backups/'concurrent.sqlite3'
            with synthetic_db(dst) as b:r.backup(b)
        self.assertEqual(m.database_state(dst),baseline)
        self.assertNotEqual(m.database_state(self.layout.db),baseline)

    def test_damaged_backup_during_rollback_is_not_used(self):
        original=m.atomic_write
        def fail(p,raw,meta):
            if p==self.layout.live('reports/period-view.js') and m.blob(raw)==m.FILES['reports/period-view.js'][1]:
                for backup in self.layout.backups.glob('*/files/az-baze-auth/daily_upload_core.py'):
                    backup.write_bytes(b'corrupted backup')
                raise OSError('synthetic failure after backup damage')
            return original(p,raw,meta)
        with patch.object(m,'atomic_write',side_effect=fail),self.assertRaises(OSError):self.go()
        self.assertFalse(self.service.active)
        self.assertIn('FILE_ROLLBACK=FAIL',self.log.getvalue())
        self.assertNotEqual(self.layout.live('az-baze-auth/daily_upload_core.py').read_bytes(),b'corrupted backup')

    def test_unknown_live_file_is_not_overwritten_during_rollback(self):
        original=m.atomic_write
        def fail(p,raw,meta):
            if p==self.layout.live('reports/period-view.js') and m.blob(raw)==m.FILES['reports/period-view.js'][1]:
                p.write_bytes(b'concurrent external deployment')
                raise OSError('concurrent operation')
            return original(p,raw,meta)
        with patch.object(m,'atomic_write',side_effect=fail),self.assertRaises(OSError):self.go()
        self.assertEqual(self.layout.live('reports/period-view.js').read_bytes(),b'concurrent external deployment')
        self.assertFalse(self.service.active)
        self.assertIn('FILE_ROLLBACK=FAIL',self.log.getvalue())

    def test_parent_symlink_is_not_followed(self):
        p=self.layout.app/'templates';other=p.with_name('actual-templates');p.rename(other);p.symlink_to(other)
        with self.assertRaises(m.Stop):self.go()
        self.assertEqual(self.service.calls,[])

    def test_wrong_token_never_calls_install(self):
        with patch.object(sys,'argv',['script','--apply','WRONG']),patch.object(m,'install') as operation,contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):m.main()
        operation.assert_not_called()

    def test_wrong_host_never_calls_install(self):
        with patch.object(sys,'argv',['script','--apply',m.TOKEN]),patch.object(m.socket,'gethostname',return_value='another-server'),patch.object(m,'install') as operation,self.assertRaises(m.Stop):m.main()
        operation.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
