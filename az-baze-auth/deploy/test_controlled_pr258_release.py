"""Offline tests of controlled PR258 package on synthetic files and SQLite."""
import base64
import hashlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("controlled-pr258-administrators.py")
spec = importlib.util.spec_from_file_location("pr258_release", SCRIPT)
pkg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pkg)


def git_show(sha, name):
    return subprocess.check_output(["git", "show", f"{sha}:az-baze-auth/{name}"])


class PR258ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.live = self.root / "runtime"
        self.stage = self.root / "stage"
        self.backups = self.root / "backups"
        self.dbpath = self.root / "auth.db"
        for d in (self.live, self.stage, self.backups):
            d.mkdir()
        for name in pkg.FILES:
            for folder, ref in ((self.live, pkg.BASE_SHA), (self.stage, pkg.MERGED_SHA)):
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(git_show(ref, name))
        with sqlite3.connect(self.dbpath) as db:
            db.executescript("""
                CREATE TABLE users (id INTEGER PRIMARY KEY);
                CREATE TABLE permissions (user_id INTEGER, section TEXT);
                CREATE TABLE audit_log (id INTEGER PRIMARY KEY);
                CREATE TABLE unrelated_economics (id INTEGER PRIMARY KEY, amount INTEGER);
                INSERT INTO unrelated_economics VALUES(1,150000);
            """)
            db.executescript(pkg.extract_schema(git_show(
                pkg.BASE_SHA, "administrators.py").decode()))
            db.execute("INSERT INTO az_admin_staff VALUES(1,'Test','2026-11',NULL,'t','t')")
            db.executemany("INSERT INTO az_admin_days VALUES(1,?,?)",
                           [("2026-11-01",7),("2026-11-02",61),
                            ("2026-11-03",500),("2026-11-04",30)])
            db.execute("INSERT INTO az_admin_rates VALUES(1,'2026-11',36000)")
        for attr, value in (("ROOT",self.live),("DB",self.dbpath),
                            ("BACKUPS",self.backups)):
            p = patch.object(pkg, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def db_snapshot(self):
        with sqlite3.connect(self.dbpath) as db:
            return "\n".join(db.iterdump())

    def test_manifest_scope_and_unchanged_audited_schema(self):
        self.assertEqual(set(pkg.FILES), {"administrators.py","templates/administrators.html"})
        self.assertEqual(pkg.MERGED_SHA, "0e8c5015e7442167941e68f410ba3029c1f8969e")
        self.assertEqual(pkg.AUDITED_HEAD, "40835befe9c9ff5be655adb1ba40eea55ae69941")
        for name, (old, new) in pkg.FILES.items():
            self.assertEqual(pkg.git_blob(git_show(pkg.BASE_SHA, name)), old)
            self.assertEqual(pkg.git_blob(git_show(pkg.MERGED_SHA, name)), new)
            self.assertEqual(git_show(pkg.AUDITED_HEAD, name), git_show(pkg.MERGED_SHA,name))
        self.assertEqual(pkg.extract_schema((self.live/"administrators.py").read_text()),
                         pkg.extract_schema((self.stage/"administrators.py").read_text()))
        compile((self.stage/"administrators.py").read_bytes(),"administrators.py","exec")

    def test_runtime_install_and_exact_rollback_only_two_files(self):
        unrelated = self.live / "server.py"
        unrelated.write_text("untouched knowledge_catalog")
        backup = self.backups / "manual"
        backup.mkdir()
        pkg.check_live_files()
        pkg.backup_files(backup)
        pkg.install(self.stage)
        for name, (_, target) in pkg.FILES.items():
            pkg.check_blob(self.live / name, target, "TEST")
        self.assertEqual(unrelated.read_text(), "untouched knowledge_catalog")
        pkg.restore(backup)
        pkg.check_live_files()

    def test_live_and_staged_drift_fails_before_any_replace(self):
        (self.live/"administrators.py").write_bytes(b"drift")
        with self.assertRaisesRegex(RuntimeError,"LIVE_BASE_BLOB_MISMATCH"):
            pkg.check_live_files()
        (self.live/"administrators.py").write_bytes(git_show(pkg.BASE_SHA,"administrators.py"))
        (self.stage/"templates/administrators.html").write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError,"INSTALL_STAGE_BLOB_MISMATCH"):
            pkg.install(self.stage)
        pkg.check_live_files()

    def test_backup_readonly_exact_data_and_required_schema(self):
        before = self.db_snapshot()
        raw_before = self.dbpath.read_bytes()
        schema = pkg.check_db()
        dest = self.backups / "auth.sqlite"
        digest = pkg.backup_sqlite(dest)
        self.assertEqual(digest, hashlib.sha256(dest.read_bytes()).hexdigest())
        self.assertEqual(pkg.check_db(dest), schema)
        self.assertEqual(self.db_snapshot(), before)
        self.assertEqual(self.dbpath.read_bytes(), raw_before)
        with sqlite3.connect(dest) as copy:
            self.assertEqual("\n".join(copy.iterdump()),before)
        with sqlite3.connect(self.dbpath) as db:
            db.execute("DROP INDEX ix_az_admin_days_date")
        with self.assertRaisesRegex(RuntimeError,"ADMIN_INDEX_MISSING"):
            pkg.check_db()

    def test_wrapped_base64_blob_and_corruption_rejection(self):
        data = git_show(pkg.MERGED_SHA, "administrators.py")
        sha = pkg.FILES["administrators.py"][1]
        encoded = base64.b64encode(data).decode()
        class Response(io.BytesIO):
            status = 200
        wrapped = "\n".join(encoded[i:i+76] for i in range(0,len(encoded),76))
        with patch.object(pkg.urllib.request,"urlopen",return_value=Response(
                json.dumps(dict(encoding="base64",content=wrapped)).encode())):
            self.assertEqual(pkg.fetch_exact_blob(sha),data)
        with patch.object(pkg.urllib.request,"urlopen",return_value=Response(
                json.dumps(dict(encoding="base64",content="AAAA")).encode())):
            with self.assertRaisesRegex(RuntimeError,"GIT_BLOB_HASH_BAD"):
                pkg.fetch_exact_blob(sha)

    def test_routes_protected_and_batch_registered(self):
        calls = []
        def protected(path, method="GET"):
            calls.append((path,method))
            return (302,b"")
        with patch.object(pkg,"http_status",side_effect=protected):
            pkg.smoke_routes(new_version=True)
        self.assertIn(("/administrators/hours/batch","POST"),calls)
        with patch.object(pkg,"http_status",return_value=(404,b"")):
            with self.assertRaisesRegex(RuntimeError,"ROUTE_UNPROTECTED_OR_MISSING"):
                pkg.smoke_routes(new_version=True)

    def test_apply_has_verified_backup_and_no_database_write(self):
        before = self.db_snapshot()
        schema = pkg.check_db()
        with patch.object(pkg,"run"), patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"restart_and_smoke") as restart:
            pkg.apply(self.stage)
            restart.assert_called_once_with()
        self.assertEqual(self.db_snapshot(),before)
        self.assertEqual(pkg.check_db(),schema)
        backup = next(self.backups.iterdir())
        self.assertEqual(pkg.check_db(backup/"auth-pre-pr258.sqlite"),schema)
        with sqlite3.connect(backup/"auth-pre-pr258.sqlite") as copy:
            self.assertEqual("\n".join(copy.iterdump()),before)
        for name, (old, new) in pkg.FILES.items():
            pkg.check_blob(backup/"files"/name,old,"BACKUP_TEST")
            pkg.check_blob(self.live/name,new,"NEW_TEST")

    def test_smoke_failure_restores_files_not_database(self):
        before = self.db_snapshot()
        with patch.object(pkg,"run"), patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"restart_and_smoke",
                          side_effect=[RuntimeError("SMOKE_FAIL"),None]) as restart:
            with self.assertRaisesRegex(RuntimeError,"SMOKE_FAIL"):
                pkg.apply(self.stage)
            self.assertEqual(restart.call_count,2)
            restart.assert_called_with(rolled_back=True)
        pkg.check_live_files()
        self.assertEqual(self.db_snapshot(),before)

    def test_partial_install_failure_restores_files_not_database(self):
        before = self.db_snapshot()
        def partial(stage):
            name = "administrators.py"
            pkg.atomic_file_replace((stage/name).read_bytes(),self.live/name)
            raise RuntimeError("PARTIAL_INSTALL_FAIL")
        with patch.object(pkg,"run"), patch.object(pkg,"active",return_value=False), \
             patch.object(pkg,"restart_and_smoke") as restart, \
             patch.object(pkg,"install",side_effect=partial):
            with self.assertRaisesRegex(RuntimeError,"PARTIAL_INSTALL_FAIL"):
                pkg.apply(self.stage)
            restart.assert_called_once_with(rolled_back=True)
        pkg.check_live_files()
        self.assertEqual(self.db_snapshot(),before)


if __name__ == "__main__":
    unittest.main()
