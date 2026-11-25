"""Offline guarded PR264 release tests, synthetic SQLite and exact Git files."""
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

spec=importlib.util.spec_from_file_location("pr264_release",Path(__file__).with_name("controlled-pr264-medical-ocr.py"))
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
        for name in pkg.DEFERRED_IDENT:
            if not name.startswith("mis_"):
                (self.backend/name).write_bytes(b"approved pre-PR262 runtime")
        with closing(sqlite3.connect(self.db)) as conn:
            for name,columns in pkg.TABLES.items():
                conn.execute("CREATE TABLE "+name+" ("+",".join('"'+c+'" BLOB' for c in sorted(columns))+")")
            raw=b"%PDF-1.7 synthetic private bytes\x00"
            conn.execute("""INSERT INTO med_supply_documents
                 (id,sha256,size_bytes,original_bytes,content_type,version,status,fields_json)
                 VALUES(?,?,?,?,?,?,?,?)""",(1,hashlib.sha256(raw).hexdigest(),len(raw),raw,"application/pdf",1,"pending","{}"))
            conn.execute("INSERT INTO med_supply_days(item_id,day,qty_in,qty_out,version,value_in) VALUES(1,'2026-11-20','2','0',1,'200')")
            conn.commit()
        self.before=pkg.medical_state(self.db)
        self.schema=pkg.check_db()
        self.deferred=pkg.deferred_state()

    def test_exact_release_and_runtime_scope(self):
        self.assertEqual(set(pkg.FILES),{
            "az-baze-auth/medical_supplies_ocr.py",
            "az-baze-auth/medical_supplies_documents.py",
            "az-baze-auth/templates/medical_supplies_ocr.html",
            "az-baze-auth/templates/medical_supplies_documents.html",
        })
        self.assertEqual(subprocess.check_output(["git","rev-parse",pkg.MERGED_SHA+"^{tree}"]),
                         subprocess.check_output(["git","rev-parse",pkg.AUDITED_HEAD+"^{tree}"]))
        for name,(old,new) in pkg.FILES.items():
            self.assertEqual(pkg.git_blob(git_show(pkg.MERGED_SHA,name)),new)
            self.assertEqual(git_show(pkg.MERGED_SHA,name),git_show(pkg.AUDITED_HEAD,name))
            if old is None:
                self.assertNotEqual(subprocess.run(["git","cat-file","-e",pkg.BASE_SHA+":"+name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode,0)
            else:
                self.assertEqual(pkg.git_blob(git_show(pkg.BASE_SHA,name)),old)
        pkg.check_live_files()
        pkg.validate_stage(self.stage)

    def test_install_restore_and_deferred_ident_untouched(self):
        backup=self.backups/"manual"
        backup.mkdir()
        pkg.backup_files(backup)
        pkg.install(self.stage)
        for name,(old,new) in pkg.FILES.items():
            pkg.check_blob(pkg.destination(name),new,"TEST")
            self.assertEqual(pkg.destination(name).stat().st_mode&0o777,0o640)
        self.assertEqual(pkg.deferred_state(),self.deferred)
        pkg.restore(backup)
        pkg.check_live_files()
        self.assertEqual(pkg.deferred_state(),self.deferred)

    def test_drift_or_existing_new_file_stops_before_install(self):
        name="az-baze-auth/medical_supplies_ocr.py"
        path=pkg.destination(name)
        path.write_bytes(b"foreign")
        with self.assertRaisesRegex(RuntimeError,"NEW_FILE_ALREADY_EXISTS"):
            pkg.check_live_files()
        path.unlink()
        old="az-baze-auth/medical_supplies_documents.py"
        pkg.destination(old).write_bytes(b"drift")
        with self.assertRaisesRegex(RuntimeError,"BLOB_MISMATCH"):
            pkg.check_live_files()
        pkg.destination(old).write_bytes(git_show(pkg.BASE_SHA,old))
        (self.stage/name).write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError,"INSTALL_STAGE_BLOB_MISMATCH"):
            pkg.install(self.stage)
        pkg.check_live_files()

    def test_backup_and_medical_fingerprint(self):
        before=self.db.read_bytes()
        dst=self.backups/"copy.sqlite"
        digest=pkg.backup_sqlite(dst)
        self.assertEqual(digest,hashlib.sha256(dst.read_bytes()).hexdigest())
        self.assertEqual(pkg.medical_state(dst),self.before)
        self.assertEqual(pkg.check_db(dst),self.schema)
        self.assertEqual(self.db.read_bytes(),before)
        with closing(sqlite3.connect(dst)) as conn:
            conn.execute("UPDATE med_supply_documents SET original_bytes=?", (b"changed",))
            conn.commit()
        self.assertNotEqual(pkg.medical_state(dst),self.before)

    def test_new_routes_protected_and_no_authenticated_ocr_request(self):
        seen=[]
        def protected(path,method="GET"):
            seen.append((path,method))
            return 302,b""
        with patch.object(pkg,"http_status",side_effect=protected):
            pkg.smoke_routes(new_version=True)
        prefix="/accounting/medical-supplies/incoming/1/ocr"
        self.assertIn((prefix,"GET"),seen)
        self.assertIn((prefix+"/page","POST"),seen)
        self.assertIn((prefix+"/save","POST"),seen)
        def missing(path,method="GET"):
            return (404,b"") if path==prefix+"/save" else (302,b"")
        with patch.object(pkg,"http_status",side_effect=missing):
            with self.assertRaisesRegex(RuntimeError,"OCR_ROUTE_UNPROTECTED_OR_MISSING"):
                pkg.smoke_routes(new_version=True)

    def test_full_release_preserves_db_and_deferred_ident(self):
        before=self.db.read_bytes()
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"restart_and_smoke") as restart:
            pkg.apply(self.stage)
            restart.assert_called_once_with()
        self.assertEqual(pkg.medical_state(self.db),self.before)
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(pkg.deferred_state(),self.deferred)
        backup=next(self.backups.iterdir())
        self.assertEqual(pkg.medical_state(backup/"auth-pre-pr264.sqlite"),self.before)
        for name,(old,new) in pkg.FILES.items():
            pkg.check_blob(pkg.destination(name),new,"TEST")

    def test_failed_smoke_rolls_back_code_without_replacing_db(self):
        before=self.db.read_bytes()
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"restart_and_smoke",side_effect=[RuntimeError("SMOKE_FAIL"),None]) as restart:
            with self.assertRaisesRegex(RuntimeError,"SMOKE_FAIL"):
                pkg.apply(self.stage)
            restart.assert_called_with(rolled_back=True)
        pkg.check_live_files()
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(pkg.deferred_state(),self.deferred)

    def test_partial_install_removes_only_exact_new_paths(self):
        before=self.db.read_bytes()
        def partial(stage):
            for name in list(pkg.FILES)[:3]:
                exemplar=pkg.destination(pkg.EXEMPLARS[name]) if name in pkg.EXEMPLARS else None
                pkg.atomic_file_replace((stage/name).read_bytes(),pkg.destination(name),exemplar)
            raise RuntimeError("PARTIAL_FAIL")
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"install",side_effect=partial), \
             patch.object(pkg,"restart_and_smoke") as restart:
            with self.assertRaisesRegex(RuntimeError,"PARTIAL_FAIL"):
                pkg.apply(self.stage)
            restart.assert_called_once_with(rolled_back=True)
        pkg.check_live_files()
        self.assertEqual(self.db.read_bytes(),before)

    def test_rollback_preserves_foreign_edit(self):
        backup=self.backups/"manual"
        backup.mkdir()
        pkg.backup_files(backup)
        pkg.install(self.stage)
        path=pkg.destination("az-baze-auth/medical_supplies_ocr.py")
        path.write_bytes(b"foreign")
        with self.assertRaisesRegex(RuntimeError,"ROLLBACK_UNEXPECTED_THIRD_PARTY_CHANGE"):
            pkg.restore(backup)
        self.assertEqual(path.read_bytes(),b"foreign")


if __name__=="__main__":
    unittest.main()
