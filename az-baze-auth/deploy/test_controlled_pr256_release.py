"""Offline source-only tests for pinned PR256 installer. No Production access."""
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
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("controlled-pr256-incoming-documents.py")
spec = importlib.util.spec_from_file_location("pr256_release", SCRIPT)
pkg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pkg)


def git_show(sha, name):
    return subprocess.check_output(
        ["git", "show", f"{sha}:az-baze-auth/{name}"]
    )


class PR256ControlledReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.live = self.root / "runtime"
        self.stage = self.root / "stage"
        self.backup = self.root / "backup"
        self.dbpath = self.root / "auth.db"
        (self.live / "templates").mkdir(parents=True)
        self.stage.mkdir()
        self.backup.mkdir()
        for name, (old, target) in pkg.FILES.items():
            source = self.stage / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(git_show(pkg.MERGED_SHA, name))
            if old:
                destination = self.live / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(git_show(pkg.BASE_SHA, name))

    def db_fixture(self):
        db = sqlite3.connect(self.dbpath)
        db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE users (id INTEGER PRIMARY KEY, full_name TEXT);
            CREATE TABLE permissions (user_id INTEGER, section TEXT);
            CREATE TABLE audit_log (id INTEGER PRIMARY KEY, details TEXT);
            CREATE TABLE med_supply_items (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE med_supply_stock_imports (id INTEGER PRIMARY KEY, filename TEXT);
            CREATE TABLE med_supply_days (item_id INTEGER, day TEXT);
            CREATE TABLE unrelated_economics (id INTEGER PRIMARY KEY, amount INTEGER);
            INSERT INTO users VALUES (1,'Owner');
            INSERT INTO med_supply_items VALUES (1,'old inventory');
            INSERT INTO unrelated_economics VALUES (1,150000);
        """)
        db.commit()
        db.close()

    def test_exact_manifest_matches_audited_merged_and_old_files(self):
        self.assertEqual(len(pkg.FILES), 6)
        self.assertEqual(sum(1 for old, _new in pkg.FILES.values() if old is None), 2)
        self.assertEqual(pkg.MERGED_SHA,
                         "9bf6a042f771af063ac280ac95186d2ccd784fe4")
        self.assertEqual(pkg.AUDITED_HEAD,
                         "35f08a9d034945fab613e35fd65149e96b011211")
        for name, (old, new) in pkg.FILES.items():
            with self.subTest(name=name):
                self.assertEqual(pkg.git_blob(git_show(pkg.MERGED_SHA, name)), new)
                if old:
                    self.assertEqual(pkg.git_blob(git_show(pkg.BASE_SHA, name)), old)
                else:
                    result = subprocess.run(
                        ["git", "cat-file", "-e",
                         f"{pkg.BASE_SHA}:az-baze-auth/{name}"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    self.assertNotEqual(result.returncode, 0)
                if name.endswith(".py"):
                    compile((self.stage / name).read_bytes(), name, "exec")

    def test_exact_schema_only_two_tables_two_indexes_and_valid_sqlite(self):
        sql = pkg.schema_statements(self.stage)
        self.assertEqual(len(sql), 4)
        self.assertEqual(pkg.NEW_TABLES,
                         {"med_supply_documents","med_supply_document_events"})
        self.assertEqual(pkg.NEW_INDEXES,
                         {"ix_med_supply_documents_status",
                          "ix_med_supply_document_events_document"})
        self.assertTrue(all("CREATE" in stmt for stmt in sql))
        self.assertFalse(any("DELETE FROM" in stmt or "INSERT INTO" in stmt
                             for stmt in sql))

    def test_database_migration_additive_old_rows_unchanged(self):
        self.db_fixture()
        with patch.object(pkg, "DB", self.dbpath):
            old = pkg.check_db()
            pkg.migrate(pkg.schema_statements(self.stage), old)
            after = pkg.check_db(before_release=False)
            self.assertEqual(set(after) - set(old), pkg.NEW_OBJECTS)
            for key in old:
                self.assertEqual(after[key], old[key])
            with sqlite3.connect(self.dbpath) as db:
                self.assertEqual(db.execute(
                    "SELECT name FROM med_supply_items WHERE id=1").fetchone()[0],
                    "old inventory")
                self.assertEqual(db.execute(
                    "SELECT amount FROM unrelated_economics WHERE id=1"
                ).fetchone()[0], 150000)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM med_supply_documents"
                ).fetchone()[0], 0)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM med_supply_document_events"
                ).fetchone()[0], 0)
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],
                                 "ok")
            with self.assertRaisesRegex(RuntimeError, "SCHEMA_ALREADY_PRESENT"):
                pkg.check_db()

    def test_failed_or_partial_schema_migration_fails_closed(self):
        self.db_fixture()
        with patch.object(pkg, "DB", self.dbpath):
            with sqlite3.connect(self.dbpath) as db:
                db.execute("CREATE TABLE med_supply_documents(id INTEGER)")
            with self.assertRaisesRegex(RuntimeError, "SCHEMA_ALREADY_PRESENT_OR_PARTIAL"):
                pkg.check_db()
            with sqlite3.connect(self.dbpath) as db:
                db.execute("DROP TABLE med_supply_documents")
            baseline = pkg.check_db()
            statements = pkg.schema_statements(self.stage)
            with self.assertRaisesRegex(sqlite3.OperationalError, "syntax error"):
                pkg.migrate([statements[0], "INVALID SQL COMMAND"], baseline)
            # BEGIN IMMEDIATE rollback must not leave a partial schema.
            self.assertEqual(pkg.check_db(), baseline)

    def test_runtime_drift_guard_exact_install_then_rollback(self):
        with patch.object(pkg, "ROOT", self.live), \
             patch.object(pkg.os, "chown", return_value=None):
            pkg.check_live_files()
            pkg.backup_files(self.backup)
            pkg.install(self.stage)
            for name, (_old, new) in pkg.FILES.items():
                pkg.check_blob(self.live / name, new, "NEW_TEST")
            pkg.restore(self.backup)
            pkg.check_live_files()
            for name, (old, _new) in pkg.FILES.items():
                dest = self.live / name
                if old is None:
                    self.assertFalse(dest.exists())
                else:
                    self.assertEqual(dest.read_bytes(),git_show(pkg.BASE_SHA,name))
            existing = self.live / "medical_supplies.py"
            existing.write_bytes(existing.read_bytes() + b"\n# unauthorized\n")
            with self.assertRaisesRegex(RuntimeError, "LIVE_BASE_BLOB_MISMATCH"):
                pkg.check_live_files()

    def test_readonly_backup_snapshot_leaves_original_untouched(self):
        self.db_fixture()
        before = self.dbpath.read_bytes()
        archive = self.root / "db-copy.sqlite"
        with patch.object(pkg, "DB", self.dbpath):
            sha = pkg.backup_sqlite(archive)
            self.assertEqual(sha, hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(len(sha), 64)
            self.assertEqual(self.dbpath.read_bytes(), before)
        with sqlite3.connect(archive) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM med_supply_items")
                             .fetchone()[0],1)
        self.assertFalse(pkg.NEW_TABLES & set(pkg.schema_map(sqlite3.connect(archive))))

    def test_github_wrapped_base64_exact_git_blob_and_reject_corrupt(self):
        payloads = {target: git_show(pkg.MERGED_SHA, name)
                    for name, (_old, target) in pkg.FILES.items()}
        class Response(io.BytesIO):
            status = 200
        def fake_open(request, timeout):
            sha = request.full_url.rsplit("/", 1)[-1]
            self.assertIn(sha, payloads)
            data = base64.b64encode(payloads[sha]).decode()
            wrapped = "\n".join(data[i:i+76] for i in range(0, len(data), 76)) + "\n"
            return Response(json.dumps({
                "encoding":"base64","content":wrapped
            }).encode())
        with patch.object(pkg.urllib.request, "urlopen", side_effect=fake_open):
            for sha, data in payloads.items():
                self.assertEqual(pkg.fetch_exact_blob(sha), data)
        def corrupt(_request, timeout):
            return Response(json.dumps({
                "encoding":"base64","content":"!!!not-base64$$$"
            }).encode())
        with patch.object(pkg.urllib.request, "urlopen", side_effect=corrupt):
            with self.assertRaisesRegex(RuntimeError, "BASE64_BAD"):
                pkg.fetch_exact_blob(next(iter(payloads)))

    def test_new_routes_are_anonymous_protected_and_no_auto_migration(self):
        self.assertEqual(len(pkg.ROUTES), 4)
        with patch.object(pkg, "http_status", return_value=(302,b"")):
            pkg.smoke_routes()
        with patch.object(pkg, "http_status", return_value=(404,b"")):
            with self.assertRaisesRegex(RuntimeError, "UNPROTECTED_OR_MISSING"):
                pkg.smoke_routes()
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("PR256_PREFLIGHT=PASS; PRODUCTION_NOT_MODIFIED",src)
        self.assertIn("PR256_RELEASE=PASS",src)
        self.assertIn("DB_SCHEMA_NOT_ROLLED_BACK_AUTOMATICALLY",src)
        self.assertNotIn("os.system(",src)
        self.assertNotIn("shutil.copy2(backup / 'auth-pre-pr256.sqlite', DB)",src)


if __name__ == "__main__":
    unittest.main()
