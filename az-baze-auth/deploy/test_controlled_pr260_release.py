"""Offline tests for controlled PR260 release: synthetic paths and SQLite."""
import base64
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("controlled-pr260-management-lab.py")
spec = importlib.util.spec_from_file_location("pr260_release", SCRIPT)
pkg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pkg)


def git_show(ref, path):
    return subprocess.check_output(["git", "show", f"{ref}:{path}"])


class PR260ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.backend = self.root / "backend"
        self.reports = self.root / "reports"
        self.stage = self.root / "stage"
        self.backups = self.root / "backups"
        self.dbpath = self.root / "auth.db"
        for folder in (self.backend, self.reports, self.stage, self.backups):
            folder.mkdir()
        for attr, value in (("ROOT",self.backend),("REPORTS",self.reports),
                            ("DB",self.dbpath),("BACKUPS",self.backups)):
            ctx = patch.object(pkg, attr, value)
            ctx.start()
            self.addCleanup(ctx.stop)
        for name, (old, new) in pkg.FILES.items():
            target = self.stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git_show(pkg.MERGED_SHA,name))
            if old is not None:
                pkg.destination(name).write_bytes(git_show(pkg.BASE_SHA,name))
                pkg.destination(name).chmod(0o640)
        with closing(sqlite3.connect(self.dbpath)) as db:
            for table, columns in pkg.TABLES.items():
                db.execute("CREATE TABLE "+table+" ("+
                           ",".join('"'+key+'" TEXT' for key in sorted(columns))+")")
            db.execute("INSERT INTO report_data(date,payload) VALUES(?,?)",
                       ("2026-11-17",json.dumps({"labRevenue":9500,"cashTotal":12345})))
            db.execute("INSERT INTO service_payment_snapshots(as_of_date,payload_json) VALUES(?,?)",
                       ("2026-11-17",json.dumps({"providers":{"synthetic":{"paid":12345}}})))
            db.execute("CREATE TABLE unrelated_economics(amount INTEGER)")
            db.execute("INSERT INTO unrelated_economics VALUES(150000)")
            db.commit()

    def db_snapshot(self, path=None):
        with closing(sqlite3.connect(path or self.dbpath)) as db:
            return "\n".join(db.iterdump())

    def test_manifest_scope_git_blobs_schema_and_assets(self):
        self.assertEqual(len(pkg.FILES),6)
        self.assertEqual(sum(old is None for old,_ in pkg.FILES.values()),2)
        self.assertEqual(pkg.MERGED_SHA,"5f7fc09e25954034946a9a334f38d331ddc16284")
        self.assertEqual(pkg.AUDITED_HEAD,"11cfe9fbbfacf3fe8c6b1b06392160f2c16e49b2")
        self.assertEqual(subprocess.check_output(
            ["git","rev-parse",pkg.MERGED_SHA+"^{tree}"]),
            subprocess.check_output(["git","rev-parse",pkg.AUDITED_HEAD+"^{tree}"]))
        for name,(old,new) in pkg.FILES.items():
            self.assertEqual(pkg.git_blob(git_show(pkg.MERGED_SHA,name)),new)
            self.assertEqual(git_show(pkg.AUDITED_HEAD,name),git_show(pkg.MERGED_SHA,name))
            if old is None:
                self.assertNotEqual(subprocess.run(["git","cat-file","-e",f"{pkg.BASE_SHA}:{name}"],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode,0)
            else:
                self.assertEqual(pkg.git_blob(git_show(pkg.BASE_SHA,name)),old)
        pkg.check_live_files()
        pkg.validate_stage(self.stage)

    def test_runtime_install_rollback_and_preserve_unrelated(self):
        untouched = self.backend / "administrators.py"
        untouched.write_text("unchanged")
        other = self.reports / "advertising.js"
        other.write_text("unchanged")
        backup = self.backups / "manual"
        backup.mkdir()
        pkg.backup_files(backup)
        pkg.install(self.stage)
        for name,(old,new) in pkg.FILES.items():
            pkg.check_blob(pkg.destination(name),new,"TEST")
            self.assertEqual(pkg.destination(name).stat().st_mode & 0o777,0o640)
        self.assertEqual(untouched.read_text(),"unchanged")
        self.assertEqual(other.read_text(),"unchanged")
        pkg.restore(backup)
        pkg.check_live_files()

    def test_existing_new_target_and_drift_fail_before_any_replace(self):
        new = pkg.destination("reports/management-lab.js")
        new.write_bytes(b"foreign")
        with self.assertRaisesRegex(RuntimeError,"LIVE_BASE_NEW_FILE_ALREADY_EXISTS"):
            pkg.check_live_files()
        new.unlink()
        target = pkg.destination("az-baze-auth/paid_services.py")
        target.write_bytes(b"unexpected")
        with self.assertRaisesRegex(RuntimeError,"LIVE_BASE_BLOB_MISMATCH"):
            pkg.check_live_files()
        target.write_bytes(git_show(pkg.BASE_SHA,"az-baze-auth/paid_services.py"))
        (self.stage/"reports/index.html").write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError,"INSTALL_STAGE_BLOB_MISMATCH"):
            pkg.install(self.stage)
        pkg.check_live_files()

    def test_backup_is_readonly_and_no_history_mutation(self):
        before = self.db_snapshot()
        raw_before = self.dbpath.read_bytes()
        baseline = pkg.check_db()
        dest = self.backups/"copy.sqlite"
        self.assertEqual(pkg.backup_sqlite(dest),hashlib.sha256(dest.read_bytes()).hexdigest())
        self.assertEqual(pkg.check_db(dest),baseline)
        self.assertEqual(self.db_snapshot(dest),before)
        self.assertEqual(self.dbpath.read_bytes(),raw_before)

    def test_exact_wrapped_blob_and_reject_corruption(self):
        data = git_show(pkg.MERGED_SHA,"reports/management-lab.js")
        sha = pkg.FILES["reports/management-lab.js"][1]
        encoded = base64.b64encode(data).decode()
        class Response(io.BytesIO):
            status=200
        wrapped="\n".join(encoded[i:i+76] for i in range(0,len(encoded),76))
        with patch.object(pkg.urllib.request,"urlopen",return_value=Response(
                json.dumps(dict(encoding="base64",content=wrapped)).encode())):
            self.assertEqual(pkg.fetch_exact_blob(sha),data)
        with patch.object(pkg.urllib.request,"urlopen",return_value=Response(
                json.dumps(dict(encoding="base64",content="AAAA")).encode())):
            with self.assertRaisesRegex(RuntimeError,"GIT_BLOB_HASH_BAD"):
                pkg.fetch_exact_blob(sha)

    def test_routes_require_auth_and_new_dependency(self):
        calls=[]
        def protected(path,method="GET"):
            calls.append((path,method))
            return 302,b""
        with patch.object(pkg,"http_status",side_effect=protected):
            pkg.smoke_routes(new_version=True)
        self.assertIn(("/api/reports/data","GET"),calls)
        self.assertIn(("/reports/management-lab.js?v=20261124-2","GET"),calls)
        with patch.object(pkg,"http_status",return_value=(200,b"")):
            with self.assertRaisesRegex(RuntimeError,"ROUTE_UNPROTECTED_OR_MISSING"):
                pkg.smoke_routes(new_version=True)

    def test_apply_full_backup_and_unchanged_db(self):
        before=self.db_snapshot()
        schema=pkg.check_db()
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"restart_and_smoke") as restart:
            pkg.apply(self.stage)
            restart.assert_called_once_with()
        self.assertEqual(self.db_snapshot(),before)
        self.assertEqual(pkg.check_db(),schema)
        backup=next(self.backups.iterdir())
        self.assertEqual(self.db_snapshot(backup/"auth-pre-pr260.sqlite"),before)
        manifest=json.loads((backup/"release-manifest.json").read_text())
        self.assertEqual(manifest["db_delta"],[])
        self.assertFalse(manifest["auto_db_restore"])
        for name,(old,new) in pkg.FILES.items():
            pkg.check_blob(pkg.destination(name),new,"NEW_TEST")
            if old is not None:
                pkg.check_blob(backup/"files"/name,old,"BACKUP_TEST")

    def test_smoke_failure_restores_old_code_and_removes_new_files(self):
        before=self.db_snapshot()
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"restart_and_smoke",side_effect=[RuntimeError("SMOKE_FAIL"),None]) as restart:
            with self.assertRaisesRegex(RuntimeError,"SMOKE_FAIL"):
                pkg.apply(self.stage)
            restart.assert_called_with(rolled_back=True)
        pkg.check_live_files()
        self.assertEqual(self.db_snapshot(),before)

    def test_partial_install_restores_both_roots(self):
        before=self.db_snapshot()
        def partial(stage):
            for name in ("az-baze-auth/management_lab.py","az-baze-auth/paid_services.py",
                         "reports/management-lab.js"):
                exemplar=pkg.destination(pkg.EXEMPLARS[name]) if name in pkg.EXEMPLARS else None
                pkg.atomic_file_replace((stage/name).read_bytes(),pkg.destination(name),exemplar)
            raise RuntimeError("PARTIAL_INSTALL_FAIL")
        with patch.object(pkg,"run"),patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"restart_and_smoke") as restart, \
             patch.object(pkg,"install",side_effect=partial):
            with self.assertRaisesRegex(RuntimeError,"PARTIAL_INSTALL_FAIL"):
                pkg.apply(self.stage)
            restart.assert_called_once_with(rolled_back=True)
        pkg.check_live_files()
        self.assertEqual(self.db_snapshot(),before)

    def test_rollback_does_not_remove_third_party_file(self):
        backup=self.backups/"manual"
        backup.mkdir()
        pkg.backup_files(backup)
        pkg.install(self.stage)
        target=pkg.destination("reports/management-lab.js")
        target.write_bytes(b"foreign edit")
        with self.assertRaisesRegex(RuntimeError,"ROLLBACK_UNEXPECTED_THIRD_PARTY_CHANGE"):
            pkg.restore(backup)
        self.assertEqual(target.read_bytes(),b"foreign edit")


if __name__ == "__main__":
    unittest.main()
