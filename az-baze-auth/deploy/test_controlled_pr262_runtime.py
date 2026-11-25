"""Synthetic installation contract; no actual historical bundle required."""
import hashlib
import importlib.util
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location("archive_runtime",Path(__file__).with_name("controlled-pr262-runtime-only.py"))
pkg=importlib.util.module_from_spec(spec)
spec.loader.exec_module(pkg)


def git_show(ref,name):
    return subprocess.check_output(["git","show",ref+":"+name])


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.backend=self.root/"backend"
        self.stage=self.root/"stage"
        self.backups=self.root/"backups"
        self.db=self.root/"auth.db"
        for d in (self.backend,self.backend/"templates",self.stage,self.backups):
            d.mkdir()
        for key,value in (("ROOT",self.backend),("DB",self.db),("BACKUPS",self.backups),("VENV",Path(sys.executable))):
            p=patch.object(pkg,key,value)
            p.start()
            self.addCleanup(p.stop)
        for name,(old,new) in pkg.FILES.items():
            target=self.stage/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(git_show(pkg.MERGED_SHA,name))
            if old is not None:
                path=pkg.destination(name)
                path.write_bytes(git_show(pkg.BASE_SHA,name))
                path.chmod(0o640)
        for name,sha in pkg.PROTECTED_OCR.items():
            (self.backend/name).write_bytes(git_show(pkg.CURRENT_SOURCE,"az-baze-auth/"+name))
        with closing(sqlite3.connect(self.db)) as conn:
            for name,columns in pkg.TABLES.items():
                conn.execute("CREATE TABLE "+name+" ("+",".join('"'+c+'" BLOB' for c in sorted(columns))+")")
            conn.execute("INSERT INTO report_data(date,payload) VALUES('2026-11-20','historical')")
            conn.execute("INSERT INTO med_supply_documents(id,original_bytes,fields_json) VALUES(1,?,?)",(b"synthetic\x00", "{}"))
            conn.execute("CREATE TABLE other_data(id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT)")
            conn.execute("INSERT INTO other_data(data) VALUES('unchanged')")
            conn.commit()
        self.before=pkg.database_state(self.db)
        self.schema=pkg.check_db()

    def test_exact_source_scope_and_archive_schema(self):
        self.assertEqual(len(pkg.FILES),9)
        self.assertEqual(sum(old is None for old,new in pkg.FILES.values()),2)
        self.assertEqual(subprocess.check_output(["git","rev-parse",pkg.MERGED_SHA+"^{tree}"]),
                         subprocess.check_output(["git","rev-parse",pkg.AUDITED_HEAD+"^{tree}"]))
        for name,(old,new) in pkg.FILES.items():
            self.assertEqual(pkg.git_blob(git_show(pkg.MERGED_SHA,name)),new)
            self.assertEqual(git_show(pkg.MERGED_SHA,name),git_show(pkg.AUDITED_HEAD,name))
            self.assertEqual(git_show(pkg.MERGED_SHA,name),git_show(pkg.CURRENT_SOURCE,name))
            if old is not None:
                self.assertEqual(pkg.git_blob(git_show(pkg.BASE_SHA,name)),old)
        pkg.check_live_files()
        pkg.validate_stage(self.stage)
        delta=pkg.expected_archive_schema(self.stage)
        self.assertEqual(set(delta),pkg.ARCHIVE_OBJECTS)
        self.assertEqual(pkg.check_db(),self.schema)

    def test_no_bundle_required_full_release(self):
        before=self.db.read_bytes()
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"restart_and_smoke") as restart:
            pkg.apply(self.stage)
            restart.assert_called_once_with()
        self.assertEqual(pkg.database_state(self.db),self.before)
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(pkg.check_db(),self.schema)
        pkg.check_protected_ocr()
        self.assertFalse(pkg.ARCHIVE_OBJECTS & self.schema.keys())
        backup=next(self.backups.iterdir())
        self.assertEqual(pkg.database_state(backup/"auth-pre-pr262-runtime.sqlite"),self.before)

    def test_preflight_rejects_new_path_old_drift_and_ocr_drift(self):
        new=pkg.destination("az-baze-auth/mis_source_archive.py")
        new.write_bytes(b"foreign")
        with self.assertRaisesRegex(RuntimeError,"NEW_FILE_ALREADY_EXISTS"):
            pkg.check_live_files()
        new.unlink()
        old="az-baze-auth/cash_upload.py"
        pkg.destination(old).write_bytes(b"drift")
        with self.assertRaisesRegex(RuntimeError,"BLOB_MISMATCH"):
            pkg.check_live_files()
        pkg.destination(old).write_bytes(git_show(pkg.BASE_SHA,old))
        (self.backend/"medical_supplies_ocr.py").write_bytes(b"drift")
        with self.assertRaisesRegex(RuntimeError,"PR264_PROTECTED_BLOB_MISMATCH"):
            pkg.check_live_files()

    def test_backup_all_rows_including_unrelated_and_original_bytes(self):
        dst=self.backups/"copy.sqlite"
        digest=pkg.backup_sqlite(dst)
        self.assertEqual(hashlib.sha256(dst.read_bytes()).hexdigest(),digest)
        self.assertEqual(pkg.database_state(dst),self.before)
        with closing(sqlite3.connect(dst)) as conn:
            conn.execute("UPDATE other_data SET data='changed'")
            conn.commit()
        self.assertNotEqual(pkg.database_state(dst),self.before)
        self.assertEqual(pkg.database_state(self.db),self.before)

    def test_existing_archive_schema_stops(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TABLE mis_source_objects(dummy)")
            conn.commit()
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"restart_and_smoke"):
            with self.assertRaisesRegex(RuntimeError,"ARCHIVE_ALREADY_PRESENT_STOP"):
                pkg.apply(self.stage)
        pkg.check_live_files()

    def test_post_start_allows_exact_lazy_schema_only(self):
        delta=pkg.expected_archive_schema(self.stage)
        pkg.check_post_schema(self.schema,self.schema,delta)
        pkg.check_post_schema(self.schema,{**self.schema,**delta},delta)
        with self.assertRaisesRegex(RuntimeError,"UNEXPECTED_SCHEMA"):
            pkg.check_post_schema(self.schema,{**self.schema,"unrelated":("table","unrelated","CREATE TABLE unrelated(x)")},delta)
        with self.assertRaisesRegex(RuntimeError,"UNEXPECTED_SCHEMA"):
            pkg.check_post_schema(self.schema,{**self.schema,"mis_source_objects":delta["mis_source_objects"]},delta)

    def test_six_routes_are_empty_anonymous_checks(self):
        seen=[]
        def response(path,method="GET"):
            seen.append((path,method))
            return 302,b""
        with patch.object(pkg,"http_status",side_effect=response):
            pkg.smoke_routes(new_version=True)
        self.assertEqual({path for path,method in seen if method=="POST"},set(pkg.COMMIT_ROUTES))
        self.assertEqual(len(pkg.COMMIT_ROUTES),6)
        def bad(path,method="GET"):
            return (404,b"") if path==pkg.COMMIT_ROUTES[0] else (302,b"")
        with patch.object(pkg,"http_status",side_effect=bad):
            with self.assertRaisesRegex(RuntimeError,"IMPORT_ROUTE_UNPROTECTED_OR_MISSING"):
                pkg.smoke_routes(new_version=True)

    def test_rollback_after_restart_failure_preserves_db_and_ocr(self):
        before=self.db.read_bytes()
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"restart_and_smoke",side_effect=[RuntimeError("SMOKE_FAIL"),None]) as restart:
            with self.assertRaisesRegex(RuntimeError,"SMOKE_FAIL"):
                pkg.apply(self.stage)
            restart.assert_called_with(rolled_back=True)
        pkg.check_live_files()
        self.assertEqual(self.db.read_bytes(),before)

    def test_partial_install_rollback(self):
        before=self.db.read_bytes()
        def partial(stage):
            for name in list(pkg.FILES)[:3]:
                exemplar=pkg.destination(pkg.EXEMPLARS[name]) if name in pkg.EXEMPLARS else None
                pkg.atomic_file_replace((stage/name).read_bytes(),pkg.destination(name),exemplar)
            raise RuntimeError("PARTIAL")
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"install",side_effect=partial), \
             patch.object(pkg,"restart_and_smoke"):
            with self.assertRaisesRegex(RuntimeError,"PARTIAL"):
                pkg.apply(self.stage)
        pkg.check_live_files()
        self.assertEqual(self.db.read_bytes(),before)

    def test_stage_drift_rejected_before_install(self):
        (self.stage/"az-baze-auth/mis_source_archive.py").write_bytes(b"bad")
        with self.assertRaisesRegex(RuntimeError,"INSTALL_STAGE_BLOB_MISMATCH"):
            pkg.install(self.stage)
        pkg.check_live_files()


if __name__=="__main__":
    unittest.main()
