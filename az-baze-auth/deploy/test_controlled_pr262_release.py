"""Offline release guard: real audited modules, synthetic private pack only."""
import hashlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import mis_source_archive as archive
import mis_archive_bundle as bundle
from test_mis_source_archive import wb, make_pack

spec=importlib.util.spec_from_file_location("pr262_release",HERE/"controlled-pr262-mis-archive.py")
pkg=importlib.util.module_from_spec(spec)
spec.loader.exec_module(pkg)


def git_show(ref,name):
    return subprocess.check_output(["git","show",ref+":az-baze-auth/"+name])


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.backend=self.root/"backend"
        self.stage=self.root/"stage"
        self.backups=self.root/"backups"
        self.path=self.root/"auth.db"
        for d in (self.backend,self.stage,self.backups):
            d.mkdir()
        for attr,value in (("ROOT",self.backend),("DB",self.path),("BACKUPS",self.backups)):
            p=patch.object(pkg,attr,value)
            p.start()
            self.addCleanup(p.stop)
        for name,(old,new) in pkg.FILES.items():
            (self.stage/name).write_bytes(git_show(pkg.MERGED_SHA,name))
            if old is not None:
                (self.backend/name).write_bytes(git_show(pkg.BASE_SHA,name))
                (self.backend/name).chmod(0o640)
        with closing(sqlite3.connect(self.path)) as conn:
            for name,columns in pkg.TABLES.items():
                conn.execute("CREATE TABLE "+pkg.quoted(name)+" ("+
                    ",".join(pkg.quoted(c)+" TEXT" for c in sorted(columns))+")")
            conn.execute("INSERT INTO report_data(date,payload) VALUES(?,?)",("2026-10-31",'{"fact":400,"labRevenue":20}'))
            conn.execute("INSERT INTO report_blobs(key,payload) VALUES(?,?)",("unrelated","approved"))
            conn.execute("CREATE TABLE prior_versions(id INTEGER PRIMARY KEY AUTOINCREMENT,payload BLOB)")
            conn.execute("INSERT INTO prior_versions(payload) VALUES(?)",(b"\x00original\xff",))
            conn.commit()
        self.before=pkg.read_business(self.path)
        self.packfile=self.root/"bundle.zip"
        self.raw=wb(100)
        self.packsha=make_pack(self.packfile,self.raw)
        self.pack=bundle.read_bundle(self.packfile,self.packsha)
        self.old_schema=pkg.check_db()

    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as conn:
            return list(conn.iterdump())

    def test_exact_release_source_and_scope(self):
        self.assertEqual(len(pkg.FILES),9)
        self.assertEqual(sum(old is None for old,new in pkg.FILES.values()),2)
        self.assertEqual(subprocess.check_output(["git","rev-parse",pkg.MERGED_SHA+"^{tree}"]),
                         subprocess.check_output(["git","rev-parse",pkg.AUDITED_HEAD+"^{tree}"]))
        for name,(old,new) in pkg.FILES.items():
            self.assertEqual(pkg.git_blob(git_show(pkg.MERGED_SHA,name)),new)
            self.assertEqual(git_show(pkg.MERGED_SHA,name),git_show(pkg.AUDITED_HEAD,name))
            if old is not None:
                self.assertEqual(pkg.git_blob(git_show(pkg.BASE_SHA,name)),old)
        pkg.check_live_files()
        pkg.validate_stage(self.stage)

    def test_private_bundle_pinned_size_and_hash(self):
        with patch.object(pkg,"BUNDLE_SIZE",self.packfile.stat().st_size), \
             patch.object(pkg,"BUNDLE_SHA",self.packsha):
            pkg.verify_private_bundle(self.packfile)
        with patch.object(pkg,"BUNDLE_SIZE",self.packfile.stat().st_size), \
             patch.object(pkg,"BUNDLE_SHA","0"*64):
            with self.assertRaisesRegex(RuntimeError,"SHA256_MISMATCH"):
                pkg.verify_private_bundle(self.packfile)
        with self.assertRaisesRegex(RuntimeError,"MISSING_OR_SIZE"):
            pkg.verify_private_bundle(self.root/"absent.zip")

    def test_install_and_restore_only_allowlisted_files(self):
        untouched=self.backend/"report_storage.py"
        untouched.write_bytes(b"approved unrelated")
        backup=self.backups/"manual"
        backup.mkdir()
        pkg.backup_files(backup)
        pkg.install(self.stage)
        for name,(_old,new) in pkg.FILES.items():
            pkg.check_blob(self.backend/name,new,"TEST")
            self.assertEqual((self.backend/name).stat().st_mode&0o777,0o640)
        self.assertEqual(untouched.read_bytes(),b"approved unrelated")
        pkg.restore(backup)
        pkg.check_live_files()

    def test_existing_new_path_and_old_drift_stop(self):
        f=self.backend/"mis_source_archive.py"
        f.write_bytes(b"foreign")
        with self.assertRaisesRegex(RuntimeError,"NEW_FILE_ALREADY_EXISTS"):
            pkg.check_live_files()
        f.unlink()
        (self.backend/"cash_upload.py").write_bytes(b"drift")
        with self.assertRaisesRegex(RuntimeError,"BLOB_MISMATCH"):
            pkg.check_live_files()

    def test_backup_content_and_recovery(self):
        dst=self.backups/"old.sqlite"
        digest=pkg.backup_sqlite(dst)
        self.assertEqual(pkg.file_digest(dst),digest)
        self.assertEqual(pkg.read_business(dst),self.before)
        self.assertEqual(pkg.read_business(self.path),self.before)

    def test_real_archive_guard_and_readback(self):
        pkg.archive_transaction(self.path,archive,bundle,self.pack)
        self.assertEqual(pkg.read_business(self.path),self.before)
        self.assertEqual(pkg.check_db(),{**self.old_schema,**pkg.archive_schema(archive)})
        with closing(pkg.ro_conn(self.path)) as conn:
            pkg.check_archive_rows(conn,archive,self.pack)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mis_source_objects").fetchone()[0],2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mis_source_events").fetchone()[0],3)
        before=self.snapshot()
        with self.assertRaisesRegex(RuntimeError,"ARCHIVE_ALREADY_PRESENT"):
            pkg.archive_transaction(self.path,archive,bundle,self.pack)
        self.assertEqual(self.snapshot(),before)

    def test_reject_and_rollback_business_mutation_before_commit(self):
        before=self.snapshot()
        original=archive.record
        def bad(conn,*args,**kwargs):
            result=original(conn,*args,**kwargs)
            conn.execute("UPDATE report_data SET payload='unexpected'")
            return result
        with patch.object(archive,"record",side_effect=bad):
            with self.assertRaisesRegex(RuntimeError,"EXISTING_BUSINESS_DATA_CHANGED"):
                pkg.archive_transaction(self.path,archive,bundle,self.pack)
        self.assertEqual(self.snapshot(),before)

    def test_reject_and_rollback_unexpected_schema_before_commit(self):
        before=self.snapshot()
        original=archive.record
        def bad(conn,*args,**kwargs):
            result=original(conn,*args,**kwargs)
            conn.execute("CREATE TABLE IF NOT EXISTS unexpected_schema(x)")
            return result
        with patch.object(archive,"record",side_effect=bad):
            with self.assertRaisesRegex(RuntimeError,"ARCHIVE_SCHEMA_DELTA_MISMATCH"):
                pkg.archive_transaction(self.path,archive,bundle,self.pack)
        self.assertEqual(self.snapshot(),before)

    def test_reject_and_rollback_corrupt_receipt(self):
        before=self.snapshot()
        original=archive.record
        def bad(conn,*args,**kwargs):
            result=original(conn,*args,**kwargs)
            conn.execute("UPDATE mis_source_events SET context_json='{}'")
            return result
        with patch.object(archive,"record",side_effect=bad):
            with self.assertRaisesRegex(RuntimeError,"ARCHIVE_RECEIPT_BUNDLE_MISMATCH"):
                pkg.archive_transaction(self.path,archive,bundle,self.pack)
        self.assertEqual(self.snapshot(),before)

    def test_apply_full_release_synthetic(self):
        def worker(*_args):
            pkg.archive_transaction(self.path,archive,bundle,self.pack)
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"run_archive_worker",side_effect=worker), \
             patch.object(pkg,"restart_and_smoke") as restart:
            pkg.apply(self.stage,None,None,archive,self.pack)
            restart.assert_called_once_with()
        self.assertEqual(pkg.read_business(self.path),self.before)
        backup=next(self.backups.iterdir())
        self.assertEqual(pkg.read_business(backup/"auth-pre-pr262.sqlite"),self.before)
        self.assertEqual(pkg.check_db(),{**self.old_schema,**pkg.archive_schema(archive)})
        for name,(_old,new) in pkg.FILES.items():
            pkg.check_blob(self.backend/name,new,"TEST")

    def test_worker_failure_restores_files_and_old_db(self):
        before=self.snapshot()
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"run_archive_worker",side_effect=RuntimeError("synthetic failure")), \
             patch.object(pkg,"restart_and_smoke") as restart:
            with self.assertRaisesRegex(RuntimeError,"synthetic failure"):
                pkg.apply(self.stage,None,None,archive,self.pack)
            restart.assert_called_once_with(rolled_back=True)
        pkg.check_live_files()
        self.assertEqual(self.snapshot(),before)

    def test_restart_failure_keeps_verified_archive_and_restores_code(self):
        def worker(*_args):
            pkg.archive_transaction(self.path,archive,bundle,self.pack)
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"run_archive_worker",side_effect=worker), \
             patch.object(pkg,"restart_and_smoke",side_effect=[RuntimeError("synthetic smoke"),None]) as restart:
            with self.assertRaisesRegex(RuntimeError,"synthetic smoke"):
                pkg.apply(self.stage,None,None,archive,self.pack)
            restart.assert_called_with(rolled_back=True)
        pkg.check_live_files()
        self.assertEqual(pkg.read_business(self.path),self.before)
        with closing(pkg.ro_conn(self.path)) as conn:
            pkg.check_archive_rows(conn,archive,self.pack)


if __name__=="__main__":
    unittest.main()
