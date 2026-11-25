"""Synthetic exact release tests. No historical archive or live database."""
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

spec=importlib.util.spec_from_file_location("pr267_release",Path(__file__).with_name("controlled-pr267-lab-recovery.py"))
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
        self.reports=self.root/"reports"
        self.stage=self.root/"stage"
        self.backups=self.root/"backups"
        self.db=self.root/"auth.db"
        for d in (self.backend,self.backend/"templates",self.reports,self.stage,self.backups):
            d.mkdir()
        for key,value in (("ROOT",self.backend),("REPORTS",self.reports),("DB",self.db),("BACKUPS",self.backups),("VENV",Path(sys.executable))):
            p=patch.object(pkg,key,value)
            p.start()
            self.addCleanup(p.stop)
        for name,(old,new) in pkg.FILES.items():
            target=self.stage/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(git_show(pkg.MERGED_SHA,name))
            if old is not None:
                path=pkg.destination(name)
                ref=pkg.ARCHIVE_BASE if name in pkg.ARCHIVE_FILES else pkg.BASE_SHA
                path.write_bytes(git_show(ref,name))
                path.chmod(0o640)
        for name in (*pkg.PROTECTED_OCR,*pkg.PROTECTED_DEPENDENCIES):
            (self.backend/name).write_bytes(git_show(pkg.MERGED_SHA,"az-baze-auth/"+name))
        with closing(sqlite3.connect(self.db)) as conn:
            for name,columns in pkg.TABLES.items():
                conn.execute("CREATE TABLE "+name+" ("+",".join('"'+c+'" BLOB' for c in sorted(columns))+")")
            conn.execute("INSERT INTO report_data(date,payload) VALUES('2026-11-24','untouched')")
            conn.execute("INSERT INTO med_supply_documents(id,original_bytes,fields_json) VALUES(1,?,?)",(b"synthetic PDF\x00","{}"))
            conn.execute("CREATE TABLE other_data(id INTEGER PRIMARY KEY AUTOINCREMENT,data TEXT)")
            conn.execute("INSERT INTO other_data(data) VALUES('untouched')")
            conn.commit()

    def set_archive_installed(self,with_schema=False):
        for name,(_old,new) in pkg.ARCHIVE_FILES.items():
            path=pkg.destination(name)
            path.write_bytes((self.stage/name).read_bytes())
            path.chmod(0o640)
        if with_schema:
            # Use the exact audited literal schema, not a live archive.
            with closing(sqlite3.connect(self.db)) as conn:
                for info in pkg.expected_archive_schema(self.stage).values():
                    if info[0]=="table":
                        conn.execute(info[2])
                for info in pkg.expected_archive_schema(self.stage).values():
                    if info[0]=="index":
                        conn.execute(info[2])
                conn.execute("INSERT INTO mis_source_objects VALUES(?,?,?,?)",("a"*64,3,b"abc","synthetic"))
                conn.commit()

    def test_exact_source_and_two_baselines(self):
        self.assertEqual(len(pkg.LAB_FILES),7)
        self.assertEqual(len(pkg.ARCHIVE_FILES),9)
        self.assertEqual(subprocess.check_output(["git","rev-parse",pkg.MERGED_SHA+"^{tree}"]),
                         subprocess.check_output(["git","rev-parse",pkg.AUDITED_HEAD+"^{tree}"]))
        self.assertEqual(subprocess.check_output(["git","rev-parse",pkg.ARCHIVE_MERGE+"^{tree}"]),
                         subprocess.check_output(["git","rev-parse",pkg.ARCHIVE_HEAD+"^{tree}"]))
        for name,(old,new) in pkg.FILES.items():
            self.assertEqual(pkg.git_blob(git_show(pkg.MERGED_SHA,name)),new)
            self.assertEqual(git_show(pkg.MERGED_SHA,name),git_show(pkg.AUDITED_HEAD,name))
            if name in pkg.ARCHIVE_FILES:
                self.assertEqual(git_show(pkg.ARCHIVE_MERGE,name),git_show(pkg.MERGED_SHA,name))
            ref=pkg.ARCHIVE_BASE if name in pkg.ARCHIVE_FILES else pkg.BASE_SHA
            if old is not None:
                self.assertEqual(pkg.git_blob(git_show(ref,name)),old)
            else:
                self.assertNotEqual(subprocess.run(["git","cat-file","-e",ref+":"+name],capture_output=True).returncode,0)
        self.assertEqual(pkg.check_live_files(),"INSTALL_PR262")
        pkg.validate_stage(self.stage,"INSTALL_PR262")
        self.set_archive_installed()
        self.assertEqual(pkg.check_live_files(),"KEEP_PR262")
        pkg.validate_stage(self.stage,"KEEP_PR262")
        self.assertEqual(len(pkg.plan("KEEP_PR262")),7)
        self.assertEqual(len(pkg.plan("INSTALL_PR262")),16)

    def test_mixed_archive_and_drift_fail_closed(self):
        name=next(iter(pkg.ARCHIVE_FILES))
        pkg.destination(name).write_bytes((self.stage/name).read_bytes())
        with self.assertRaisesRegex(RuntimeError,"PR262_MIXED_OR_UNKNOWN_RUNTIME"):
            pkg.check_live_files()
        pkg.destination(name).unlink()
        (self.backend/"medical_supplies_ocr.py").write_bytes(b"drift")
        with self.assertRaisesRegex(RuntimeError,"PR264_PROTECTED_BLOB_MISMATCH"):
            pkg.check_live_files()

    def test_archive_schema_is_preserved_and_exact(self):
        delta=pkg.expected_archive_schema(self.stage)
        self.assertEqual(set(delta),pkg.ARCHIVE_OBJECTS)
        base=pkg.check_db()
        pkg.check_baseline_schema(base,delta,"INSTALL_PR262")
        with self.assertRaisesRegex(RuntimeError,"UNEXPECTED_ARCHIVE_SCHEMA"):
            pkg.check_baseline_schema({**base,**delta},delta,"INSTALL_PR262")
        with self.assertRaisesRegex(RuntimeError,"UNEXPECTED_ARCHIVE_SCHEMA"):
            pkg.check_baseline_schema({**base,"mis_source_objects":delta["mis_source_objects"]},delta,"KEEP_PR262")
        pkg.check_baseline_schema({**base,**delta},delta,"KEEP_PR262")
        with self.assertRaisesRegex(RuntimeError,"HISTORICAL_SELECTION_ALREADY_PRESENT"):
            pkg.check_baseline_schema({**base,pkg.RECOVERY_TABLE:("table",pkg.RECOVERY_TABLE,"x")},delta,"KEEP_PR262")
        pkg.check_post_schema(base,{**base,**delta},delta)
        with self.assertRaisesRegex(RuntimeError,"POST_RELEASE_UNEXPECTED_SCHEMA"):
            pkg.check_post_schema(base,{**base,pkg.RECOVERY_TABLE:("table",pkg.RECOVERY_TABLE,"x")},delta)

    def _apply(self,mode):
        before=pkg.database_state(self.db)
        raw=self.db.read_bytes()
        schema=pkg.check_db()
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"restart_and_smoke") as restart:
            pkg.apply(self.stage,mode)
            restart.assert_called_once_with()
        pkg.check_target()
        self.assertEqual(pkg.database_state(self.db),before)
        self.assertEqual(self.db.read_bytes(),raw)
        self.assertEqual(pkg.check_db(),schema)
        backup=next(self.backups.iterdir())
        self.assertEqual(pkg.database_state(backup/"auth-pre-pr267.sqlite"),before)
        self.assertNotIn(pkg.RECOVERY_TABLE,schema)

    def test_release_without_archive_installs_only_audited_runtime(self):
        self._apply("INSTALL_PR262")
        self.assertFalse(pkg.ARCHIVE_OBJECTS & pkg.check_db().keys())

    def test_release_keeps_existing_archive_and_originals(self):
        self.set_archive_installed(with_schema=True)
        files={name:(pkg.destination(name).read_bytes(),pkg.destination(name).stat().st_ino)
               for name in pkg.ARCHIVE_FILES}
        self._apply("KEEP_PR262")
        for name,state in files.items():
            path=pkg.destination(name)
            self.assertEqual((path.read_bytes(),path.stat().st_ino),state)

    def _rollback(self,mode):
        raw=self.db.read_bytes()
        before=pkg.database_state(self.db)
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"restart_and_smoke",side_effect=[RuntimeError("SMOKE_FAIL"),None]) as restart:
            with self.assertRaisesRegex(RuntimeError,"SMOKE_FAIL"):
                pkg.apply(self.stage,mode)
            restart.assert_called_with(rolled_back=True)
        self.assertEqual(pkg.check_live_files(),mode)
        self.assertEqual(self.db.read_bytes(),raw)
        self.assertEqual(pkg.database_state(self.db),before)

    def test_rollback_absent_archive(self):
        self._rollback("INSTALL_PR262")

    def test_rollback_installed_archive(self):
        self.set_archive_installed(with_schema=True)
        self._rollback("KEEP_PR262")

    def test_partial_install_rolls_back_new_dependencies_only(self):
        raw=self.db.read_bytes()
        def partial(stage,mode):
            for name,(old,new) in list(pkg.plan(mode).items())[:11]:
                path=pkg.destination(name)
                exemplar=pkg.destination(pkg.EXEMPLARS[name]) if old is None else None
                pkg.atomic_file_replace((stage/name).read_bytes(),path,exemplar)
            raise RuntimeError("PARTIAL")
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"install",side_effect=partial),patch.object(pkg,"restart_and_smoke"):
            with self.assertRaisesRegex(RuntimeError,"PARTIAL"):
                pkg.apply(self.stage,"INSTALL_PR262")
        self.assertEqual(pkg.check_live_files(),"INSTALL_PR262")
        self.assertEqual(self.db.read_bytes(),raw)

    def test_stage_tampering_stops_before_runtime_change(self):
        (self.stage/"az-baze-auth/management_lab_recovery.py").write_bytes(b"bad")
        with self.assertRaisesRegex(RuntimeError,"INSTALL_STAGE_BLOB_MISMATCH"):
            pkg.install(self.stage,"INSTALL_PR262")
        self.assertEqual(pkg.check_live_files(),"INSTALL_PR262")

    def test_smoke_uses_only_empty_anonymous_requests(self):
        seen=[]
        def response(path,method="GET"):
            seen.append((path,method))
            return 302,b""
        with patch.object(pkg,"http_status",side_effect=response):
            pkg.smoke_routes(new_version=True)
        self.assertEqual({path for path,method in seen if method=="POST"},set(pkg.COMMIT_ROUTES))
        self.assertIn(("/api/reports/data?view=dashboard","GET"),seen)
        self.assertIn(("/reports/management-lab.js?v=20261125-recovery-1","GET"),seen)
        self.assertIn(("/accounting/medical-supplies/incoming/1/ocr","GET"),seen)


if __name__=="__main__":
    unittest.main()
