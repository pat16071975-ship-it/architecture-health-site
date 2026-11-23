"""Source-only tests for immutable PR254 deployment. Never contacts Production."""
import base64
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("controlled-pr254-instagram.py")
spec = importlib.util.spec_from_file_location("pr254_release", SCRIPT)
pkg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pkg)


def git_show(commit, name):
    return subprocess.check_output(
        ["git", "show", f"{commit}:az-baze-auth/{name}"]
    )


class ControlledPR254ReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.live = self.root / "runtime"
        self.stage = self.root / "stage"
        self.backup = self.root / "backup"
        (self.live / "templates").mkdir(parents=True)
        (self.stage / "templates").mkdir(parents=True)
        self.backup.mkdir()
        for name in pkg.FILES:
            old = self.live / name
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_bytes(git_show(pkg.BASE_SHA, name))
            new = self.stage / name
            new.parent.mkdir(parents=True, exist_ok=True)
            new.write_bytes(git_show(pkg.MERGED_SHA, name))

    def test_manifest_pins_exact_three_git_blobs_from_merged_pr(self):
        self.assertEqual(set(pkg.FILES), {
            "advertising_report.py",
            "advertising_monthly_costs.py",
            "templates/advertising_costs_fragment.html",
        })
        self.assertEqual(pkg.MERGED_SHA,
                         "ffd069d35c9a79f40e3035d1746e2dbd0c9be508")
        for path, (old, new) in pkg.FILES.items():
            with self.subTest(path=path):
                self.assertEqual(pkg.git_blob(git_show(pkg.BASE_SHA, path)), old)
                self.assertEqual(pkg.git_blob(git_show(pkg.MERGED_SHA, path)), new)
                if path.endswith(".py"):
                    compile((self.stage / path).read_bytes(), path, "exec")
        import jinja2
        jinja2.Environment().parse(
            (self.stage / "templates/advertising_costs_fragment.html").read_text()
        )

    def test_live_blob_guard_refuses_drift_before_changes(self):
        with patch.object(pkg, "ROOT", self.live):
            pkg.check_live_files()
            altered = self.live / "advertising_monthly_costs.py"
            altered.write_bytes(altered.read_bytes() + b"\n# unauthorized drift\n")
            with self.assertRaisesRegex(RuntimeError, "LIVE_BASE_BLOB_MISMATCH"):
                pkg.check_live_files()

    def test_rollback_restores_all_three_exact_blobs(self):
        with patch.object(pkg, "ROOT", self.live):
            pkg.backup_files(self.backup)
            with patch.object(pkg.os, "chown", return_value=None):
                pkg.install(self.stage)
                for name, (_before, new) in pkg.FILES.items():
                    pkg.blob_check(self.live / name, new, "TEST_TARGET")
                pkg.rollback(self.backup)
            pkg.check_live_files()
            self.assertEqual(
                (self.live / "advertising_monthly_costs.py").read_bytes(),
                git_show(pkg.BASE_SHA, "advertising_monthly_costs.py"),
            )

    def test_immutable_github_blob_fetch_checks_sha_not_url_only(self):
        class Response(io.BytesIO):
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False
        data_map = {new: git_show(pkg.MERGED_SHA, name)
                    for name, (_old, new) in pkg.FILES.items()}
        def fake_open(req, timeout):
            sha = req.full_url.rsplit("/", 1)[-1]
            self.assertIn(sha, data_map)
            encoded = base64.b64encode(data_map[sha]).decode()
            # Match actual GitHub Git Blob API: base64 is wrapped in
            # newlines, sometimes with trailing whitespace.
            wrapped = "\n".join(
                encoded[i:i+76] for i in range(0, len(encoded), 76)
            ) + "\n"
            return Response(json.dumps({
                "encoding": "base64", "content": wrapped
            }).encode())
        with patch.object(pkg.urllib.request, "urlopen", side_effect=fake_open):
            for sha, expected in data_map.items():
                self.assertEqual(pkg.fetch_blob(sha), expected)
        bad = b"unexpected payload"
        def fake_bad(_req, timeout):
            return Response(json.dumps({
                "encoding": "base64",
                "content": base64.b64encode(bad).decode()
            }).encode())
        with patch.object(pkg.urllib.request, "urlopen", side_effect=fake_bad):
            with self.assertRaisesRegex(RuntimeError, "BLOB_INVALID"):
                pkg.fetch_blob(next(iter(data_map)))

        def fake_corrupted(_req, timeout):
            return Response(json.dumps({
                "encoding": "base64", "content": "SGVsbG8=\\n@@bad"
            }).encode())
        with patch.object(pkg.urllib.request, "urlopen", side_effect=fake_corrupted):
            with self.assertRaisesRegex(RuntimeError, "BASE64_INVALID"):
                pkg.fetch_blob(next(iter(data_map)))

    def test_db_backup_readonly_and_no_schema_changes(self):
        path = self.root / "auth.db"
        with sqlite3.connect(path) as db:
            db.executescript("""
              CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
              CREATE TABLE report_blobs (key TEXT PRIMARY KEY, payload TEXT);
              CREATE TABLE audit_log (id INTEGER PRIMARY KEY, action TEXT);
              INSERT INTO users(id,name) VALUES(1,'Owner');
              INSERT INTO report_blobs(key,payload) VALUES('az-advertising-monthly-costs-v1','{"version":1}');
              INSERT INTO audit_log(id,action) VALUES(1,'original');
            """)
        before = path.read_bytes()
        with patch.object(pkg, "DB", path):
            pkg.check_db()
            destination = self.root / "snapshot.sqlite"
            sha = pkg.backup_db(destination)
            self.assertEqual(len(sha), 64)
            self.assertTrue(destination.is_file())
            pkg.check_db(destination)
        self.assertEqual(before, path.read_bytes())
        with sqlite3.connect(destination) as db:
            self.assertEqual(db.execute(
                "SELECT payload FROM report_blobs WHERE key='az-advertising-monthly-costs-v1'"
            ).fetchone()[0], '{"version":1}')

    def test_protected_routes_are_smoked_without_authorized_user(self):
        with patch.object(pkg, "http_status", return_value=(302,b"")):
            pkg.route_smoke()
        with patch.object(pkg, "http_status", return_value=(404,b"")):
            with self.assertRaisesRegex(RuntimeError,"ROUTE_SMOKE_FAIL"):
                pkg.route_smoke()

    def test_release_has_no_sql_migration_and_limits_changed_runtime_files(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(len(pkg.FILES), 3)
        self.assertIn('PR254_RELEASE=PASS', source)
        self.assertIn('PR254_RELEASE=FAIL; SAFE_CODE_ROLLBACK', source)
        self.assertIn('DATABASE_NOT_RESTORED_AUTOMATICALLY', source)
        self.assertNotIn("migrate_additive(", source)
        self.assertNotIn("DROP TABLE", source)
        self.assertNotIn("UPDATE report_blobs SET", source)
        self.assertNotIn("service restart", source)
        self.assertIn('"db_migration": False', source)


if __name__ == "__main__":
    unittest.main()
