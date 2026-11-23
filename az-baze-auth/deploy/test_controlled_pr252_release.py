"""Source-only tests for immutable PR252 deploy package. Never touches Production."""
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("controlled-pr252-administrators.py")
SPEC = importlib.util.spec_from_file_location("pr252_release_package", SCRIPT)
pkg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pkg)


def git_show(sha, path):
    return subprocess.check_output(["git", "show", f"{sha}:az-baze-auth/{path}"])


class ControlledPR252Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / "stage"
        self.stage.mkdir()
        (self.stage / "administrators.py").write_bytes(
            git_show(pkg.MERGED_SHA, "administrators.py")
        )

    def test_target_and_base_git_blobs_match_manifest(self):
        self.assertEqual(len(pkg.FILES), 8)
        for relative, (before, after) in pkg.FILES.items():
            with self.subTest(relative=relative):
                self.assertEqual(pkg.git_blob(git_show(pkg.MERGED_SHA, relative)), after)
                if before is None:
                    result = subprocess.run(
                        ["git", "cat-file", "-e", f"{pkg.BASE_SHA}:az-baze-auth/{relative}"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    self.assertNotEqual(result.returncode, 0)
                else:
                    self.assertEqual(pkg.git_blob(git_show(pkg.BASE_SHA, relative)), before)

    def test_exact_additive_schema_and_rejected_unrelated_db_changes(self):
        statements = pkg.schema_statements(self.stage)
        self.assertEqual(len(statements), 8)
        db = self.root / "auth.db"
        conn = sqlite3.connect(db)
        try:
            conn.executescript("""
                CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT,
                    active INTEGER, created_at TEXT, updated_at TEXT);
                CREATE TABLE permissions (user_id INTEGER, section TEXT);
                CREATE TABLE audit_log (id INTEGER PRIMARY KEY, action TEXT,
                    created_at TEXT);
                CREATE TABLE payroll_existing (id INTEGER PRIMARY KEY, value INTEGER);
                INSERT INTO payroll_existing VALUES(1,3500);
            """)
        finally:
            conn.close()
        with patch.object(pkg, "DB", db):
            pkg.check_live_database()
            pkg.migrate_additive(statements)
            with sqlite3.connect(db) as conn:
                objects = pkg.schema_map(conn)
                self.assertTrue(pkg.NEW_OBJECTS <= set(objects))
                self.assertEqual(conn.execute(
                    "SELECT value FROM payroll_existing WHERE id=1").fetchone()[0], 3500)
                self.assertEqual(conn.execute(
                    "SELECT count(*) FROM az_admin_months").fetchone()[0], 0)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            with self.assertRaisesRegex(RuntimeError, "SCHEMA_ALREADY_PRESENT"):
                pkg.check_live_database()

    def test_base_guard_catches_drift_and_rollback_files(self):
        live = self.root / "live"
        (live / "templates").mkdir(parents=True)
        (live / "venv").mkdir()
        backup = self.root / "backup"
        backup.mkdir()
        for relative, (before, target) in pkg.FILES.items():
            destination = self.stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(git_show(pkg.MERGED_SHA, relative))
            if before:
                old = live / relative
                old.parent.mkdir(parents=True, exist_ok=True)
                old.write_bytes(git_show(pkg.BASE_SHA, relative))
        with patch.object(pkg, "ROOT", live), patch.object(os, "chown", return_value=None):
            pkg.check_live_files()
            pkg.backup_runtime(backup)
            pkg.install_runtime(self.stage)
            for relative, (_before, target) in pkg.FILES.items():
                pkg.blob_check(live / relative, target, "TEST_NEW")
            pkg.restore_runtime(backup)
            pkg.check_live_files()
            old_server = live / "server.py"
            old_server.write_bytes(old_server.read_bytes() + b"\n# drift\n")
            with self.assertRaisesRegex(RuntimeError, "BLOB_MISMATCH"):
                pkg.check_live_files()

    def test_no_restore_of_db_in_automatic_rollback_contract(self):
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("DATABASE_NOT_RESTORED_TO_AVOID_LOST_WRITES", src)
        self.assertNotIn("shutil.copy2(backup / 'auth-pre-pr252.sqlite', DB)", src)
        self.assertIn('conn.execute("BEGIN IMMEDIATE")', src)


if __name__ == "__main__":
    unittest.main()
