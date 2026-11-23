"""Source-only tests for immutable PR252 deploy package. Never touches Production."""
import base64
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

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
                pristine = git_show(pkg.MERGED_SHA, relative)
                if relative == "server.py":
                    self.assertEqual(pkg.git_blob(pristine), pkg.AUDITED_SERVER_BLOB)
                    patched = pkg.preserve_knowledge_catalog(pristine, pkg.AUDITED_SERVER_BLOB)
                    self.assertEqual(pkg.git_blob(patched), pkg.PRESERVED_SERVER_BLOB)
                    self.assertEqual(pkg.git_blob(patched), after)
                    original_base = git_show(pkg.BASE_SHA, relative)
                    self.assertEqual(pkg.git_blob(original_base),
                                     "7d186761a727baa1fe50707c80b3d9ab154a3b96")
                    preserved_live = pkg.preserve_knowledge_catalog(
                        original_base, "7d186761a727baa1fe50707c80b3d9ab154a3b96"
                    )
                    # Exactly the actual blob reported by read-only host diagnostics.
                    self.assertEqual(pkg.git_blob(preserved_live), pkg.LIVE_SERVER_BLOB)
                    self.assertEqual(pkg.git_blob(preserved_live), before)
                else:
                    self.assertEqual(pkg.git_blob(pristine), after)
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
        (live / "knowledge_catalog.py").write_bytes(b"# live module preserved\n")
        backup = self.root / "backup"
        backup.mkdir()
        for relative, (before, target) in pkg.FILES.items():
            destination = self.stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            new_data = git_show(pkg.MERGED_SHA, relative)
            if relative == "server.py":
                new_data = pkg.preserve_knowledge_catalog(new_data, pkg.AUDITED_SERVER_BLOB)
            destination.write_bytes(new_data)
            if before:
                old = live / relative
                old.parent.mkdir(parents=True, exist_ok=True)
                old_data = git_show(pkg.BASE_SHA, relative)
                if relative == "server.py":
                    old_data = pkg.preserve_knowledge_catalog(
                        old_data, "7d186761a727baa1fe50707c80b3d9ab154a3b96"
                    )
                old.write_bytes(old_data)
        with patch.object(pkg, "ROOT", live), patch.object(os, "chown", return_value=None):
            pkg.verify_knowledge_catalog_available()
            pkg.check_live_files()
            pkg.backup_runtime(backup)
            pkg.install_runtime(self.stage)
            for relative, (_before, target) in pkg.FILES.items():
                pkg.blob_check(live / relative, target, "TEST_NEW")
            self.assertEqual((live / "knowledge_catalog.py").read_bytes(), b"# live module preserved\n")
            self.assertEqual((live / "server.py").read_bytes().count(b"import knowledge_catalog"), 1)
            self.assertEqual((live / "server.py").read_bytes().count(b"knowledge_catalog.register(app)"), 1)
            self.assertEqual((live / "server.py").read_bytes().count(b"administrators.register(app)"), 1)
            pkg.restore_runtime(backup)
            pkg.check_live_files()
            self.assertEqual((live / "knowledge_catalog.py").read_bytes(), b"# live module preserved\n")
            old_server = live / "server.py"
            old_server.write_bytes(old_server.read_bytes() + b"\n# drift\n")
            with self.assertRaisesRegex(RuntimeError, "BLOB_MISMATCH"):
                pkg.check_live_files()

    def test_refuse_to_patch_unexpected_server_or_duplicate_catalog(self):
        valid = git_show(pkg.MERGED_SHA, "server.py")
        with self.assertRaisesRegex(RuntimeError, "UNEXPECTED_SOURCE_BLOB"):
            pkg.preserve_knowledge_catalog(valid + b"\n# unknown\n", pkg.AUDITED_SERVER_BLOB)
        already = pkg.preserve_knowledge_catalog(valid, pkg.AUDITED_SERVER_BLOB)
        with self.assertRaisesRegex(RuntimeError, "DUPLICATE_REGISTRATION"):
            pkg.preserve_knowledge_catalog(already, pkg.PRESERVED_SERVER_BLOB)
        self.assertEqual(already.count(b"knowledge_catalog.register(app)"), 1)

    def test_github_api_fallback_retains_exact_immutable_blobs(self):
        """If RAW DNS fails, GitHub API supplies verified bytes, not arbitrary code."""
        source_by_sha = {
            (pkg.AUDITED_SERVER_BLOB if relative == "server.py" else target): relative
            for relative, (_base, target) in pkg.FILES.items()
        }

        class Response(io.BytesIO):
            status = 200

        def fake_urlopen(request, timeout):
            url = request.full_url
            if "raw.githubusercontent.com" in url:
                raise URLError("simulated raw GitHub DNS failure")
            sha = url.rsplit("/", 1)[-1]
            if sha not in source_by_sha:
                raise AssertionError("unexpected API request: " + url)
            raw = git_show(pkg.MERGED_SHA, source_by_sha[sha])
            payload = json.dumps({
                "encoding": "base64", "content": base64.b64encode(raw).decode("ascii"),
            }).encode("utf-8")
            return Response(payload)

        with patch.object(pkg.urllib.request, "urlopen", side_effect=fake_urlopen), \
             patch.object(pkg.time, "sleep", return_value=None), \
             patch.object(pkg, "run", return_value=type("Ok", (), {"returncode": 0})()):
            pkg.fetch_audited_files(self.stage)
        for name, (_base, target) in pkg.FILES.items():
            self.assertEqual(pkg.git_blob((self.stage / name).read_bytes()), target)
        self.assertIn(b"knowledge_catalog.register(app)",
                      (self.stage / "server.py").read_bytes())
        self.assertIn(b"administrators.register(app)",
                      (self.stage / "server.py").read_bytes())

    def test_no_restore_of_db_in_automatic_rollback_contract(self):
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("DATABASE_NOT_RESTORED_TO_AVOID_LOST_WRITES", src)
        self.assertNotIn("shutil.copy2(backup / 'auth-pre-pr252.sqlite', DB)", src)
        self.assertIn('conn.execute("BEGIN IMMEDIATE")', src)


if __name__ == "__main__":
    unittest.main()
