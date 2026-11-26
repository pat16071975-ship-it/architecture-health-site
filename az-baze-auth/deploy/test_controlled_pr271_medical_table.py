"""Isolated deployment/rollback tests; never contact or use a live installation.

Only service operations and injected faults are mocked. All file replacements,
hashes, SQLite backups and database fingerprints use real temporary files.
"""
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "pr271_controlled_release", Path(__file__).with_name("controlled-pr271-medical-table.py"))
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ControlledReleaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="pr271-test-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        self.root = self.directory / "runtime"
        self.root.mkdir()
        (self.root / "templates").mkdir()
        self.stage = self.directory / "stage"
        (self.stage / "templates").mkdir(parents=True)
        self.backups = self.directory / "backups"
        self.backups.mkdir()
        self.database = self.directory / "auth.db"
        self.originals = {
            "medical_supplies_ocr.py": b"VALUE = 'old synthetic parser'\n",
            "templates/medical_supplies_documents.html": b"<main>Old synthetic inbox</main>\n",
            "templates/medical_supplies_ocr.html": b"<main>Old synthetic table</main>\n",
        }
        self.targets = {
            "medical_supplies_ocr.py": b"VALUE = 'new synthetic parser'\n",
            "templates/medical_supplies_documents.html": b"<main>New synthetic inbox</main>\n",
            "templates/medical_supplies_ocr.html": b"<main>New synthetic table</main>\n",
        }
        self.modes = {}
        for index, name in enumerate(self.originals):
            (self.root / name).write_bytes(self.originals[name])
            (self.stage / name).write_bytes(self.targets[name])
            self.modes[name] = 0o640 if index == 0 else 0o644
            (self.root / name).chmod(self.modes[name])
        self.protected = {"server.py": b"# protected server\n",
                          "mis_source_archive.py": b"# deferred PR270 stays unchanged\n"}
        for name, data in self.protected.items():
            (self.root / name).write_bytes(data)
        with sqlite3.connect(self.database) as conn:
            conn.executescript("""
                CREATE TABLE med_supply_documents(
                    id INTEGER PRIMARY KEY, original_bytes BLOB NOT NULL,
                    sha256 TEXT NOT NULL, fields_json TEXT NOT NULL, version INTEGER NOT NULL);
                INSERT INTO med_supply_documents VALUES(1,x'73796e746865746963','test','{}',1);
                CREATE TABLE med_supply_document_events(id INTEGER PRIMARY KEY);
                CREATE TABLE med_supply_items(id INTEGER PRIMARY KEY);
                CREATE TABLE med_supply_folders(id INTEGER PRIMARY KEY);
                CREATE TABLE med_supply_daily_items(id INTEGER PRIMARY KEY);
                CREATE TABLE activity(id INTEGER PRIMARY KEY,note TEXT NOT NULL);
                INSERT INTO activity VALUES(1,'existing business record');
            """)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in (
            ("ROOT", self.root), ("DB", self.database), ("BACKUPS", self.backups),
            ("FILES", {name: (release.blob(self.originals[name]), release.blob(self.targets[name]))
                       for name in self.originals}),
            ("PROTECTED", tuple(self.protected)),
        ):
            stack.enter_context(patch.object(release, name, value))
        self.running = True
        self.timeline = []
        self.start_views = []
        self.stop_effect = None
        self.start_effect = None
        self.stop_mock = stack.enter_context(patch.object(release, "stop", side_effect=self.stop))
        self.start_mock = stack.enter_context(patch.object(release, "start", side_effect=self.start))
        stack.enter_context(patch.object(release, "active", side_effect=lambda: self.running))
        stack.enter_context(patch.object(release, "command", side_effect=AssertionError("No system commands in isolated tests")))
        stack.enter_context(patch.object(release.urllib.request, "urlopen",
                                        side_effect=AssertionError("No network in isolated tests")))
        self.output = io.StringIO()

    def stop(self):
        self.timeline.append("stop")
        self.running = False
        if self.stop_effect:
            self.stop_effect()

    def start(self):
        self.timeline.append("start")
        self.start_views.append(self.files())
        self.running = True
        if self.start_effect:
            self.start_effect()

    def files(self):
        return {name: (self.root / name).read_bytes() for name in self.originals}

    def rows(self, path=None):
        with sqlite3.connect(path or self.database) as conn:
            return conn.execute("SELECT id,note FROM activity ORDER BY id").fetchall()

    def write_activity(self, number, note):
        with sqlite3.connect(self.database) as conn:
            conn.execute("INSERT INTO activity VALUES(?,?)", (number, note))

    def apply(self):
        with redirect_stdout(self.output):
            return release.apply(self.stage)

    def assert_old_files(self):
        self.assertEqual(self.files(), self.originals)
        for name, mode in self.modes.items():
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), mode)
        self.assertEqual(list(self.root.rglob(".pr271-*")), [])

    def backup(self):
        backups = list(self.backups.iterdir())
        self.assertEqual(len(backups), 1)
        return backups[0]

    def test_success_exact_three_files_backup_and_fingerprint_before_start(self):
        before = release.database_bytes()
        old_rows = self.rows()

        def verify_start_boundary():
            self.assertEqual(release.database_bytes(), before)
            self.assertEqual(self.files(), self.targets)
            self.assertIn("DB_UNCHANGED_BEFORE_START=PASS", self.output.getvalue())

        self.start_effect = verify_start_boundary
        backup = self.apply()
        self.assertEqual(self.timeline, ["stop", "start"])
        self.assertTrue(self.running)
        self.assertEqual(self.files(), self.targets)
        self.assertEqual(release.database_bytes(), before)
        self.assertEqual(self.rows(), old_rows)
        self.assertEqual(self.rows(backup / "auth-pre-pr271.sqlite"), old_rows)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((backup / "auth-pre-pr271.sqlite").stat().st_mode), 0o600)
        manifest = json.loads((backup / "release-manifest.json").read_text())
        self.assertFalse(manifest["auto_db_restore"])
        self.assertEqual(manifest["database_before"], before)
        self.assertEqual(manifest["merged_sha"], release.MERGED)
        for name, data in self.originals.items():
            self.assertEqual((backup / "files" / name).read_bytes(), data)
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), self.modes[name])
        for name, data in self.protected.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertIn("PR271_RELEASE=PASS; RUNTIME_FILES=3", self.output.getvalue())

    def test_partial_swap_failure_restores_all_three_files(self):
        actual = release.atomic_replace
        replaced = []

        def fail_second_install(data, path, metadata, expected_blobs=None):
            name = str(path.relative_to(self.root))
            if data == self.targets[name] and name == "templates/medical_supplies_documents.html":
                raise OSError("synthetic second replacement failure")
            actual(data, path, metadata, expected_blobs)
            replaced.append((name, data))

        before = release.database_bytes()
        with patch.object(release, "atomic_replace", side_effect=fail_second_install):
            with self.assertRaisesRegex(OSError, "second replacement"):
                self.apply()
        self.assert_old_files()
        self.assertEqual(self.timeline, ["stop", "stop", "start"])
        self.assertEqual(self.start_views, [self.originals])
        self.assertEqual(release.database_bytes(), before)
        restored = {name for name, data in replaced if data == self.originals[name]}
        self.assertEqual(restored, set(self.originals))
        self.assertIn("CODE_ROLLBACK=PASS; DATABASE_NOT_RESTORED", self.output.getvalue())

    def test_staged_tamper_is_rejected_before_stop_or_backup(self):
        (self.stage / "medical_supplies_ocr.py").write_bytes(b"VALUE = 'tampered'\n")
        with self.assertRaisesRegex(RuntimeError, "STAGED_TARGET_MISMATCH"):
            self.apply()
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assert_old_files()

    def test_live_drift_is_rejected_before_stop_and_not_overwritten(self):
        name = "templates/medical_supplies_documents.html"
        external = b"<main>External newer edit</main>\n"
        (self.root / name).write_bytes(external)
        with self.assertRaisesRegex(RuntimeError, "LIVE_BASE_MISMATCH"):
            self.apply()
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assertEqual((self.root / name).read_bytes(), external)

    def test_backup_failure_restores_service_without_changing_code(self):
        actual = release.backup_database

        def failed_after_real_backup(path):
            actual(path)
            raise sqlite3.OperationalError("synthetic backup verification failure")

        before = release.database_bytes()
        with patch.object(release, "backup_database", side_effect=failed_after_real_backup), \
             patch.object(release, "atomic_replace", wraps=release.atomic_replace) as replace:
            with self.assertRaisesRegex(sqlite3.OperationalError, "backup verification failure"):
                self.apply()
            replace.assert_not_called()
        self.assert_old_files()
        self.assertEqual(self.timeline, ["stop", "start"])
        self.assertEqual(release.database_bytes(), before)
        self.assertEqual(self.rows(self.backup() / "auth-pre-pr271.sqlite"), self.rows())

    def test_post_restart_failure_preserves_new_business_data_during_code_rollback(self):
        def restart_with_normal_traffic_then_fail():
            if self.start_mock.call_count == 1:
                self.write_activity(2, "legitimate transaction after service restart")
                raise RuntimeError("synthetic post-restart smoke failure")

        self.start_effect = restart_with_normal_traffic_then_fail
        with self.assertRaisesRegex(RuntimeError, "post-restart smoke failure"):
            self.apply()
        self.assert_old_files()
        self.assertEqual(self.start_views, [self.targets, self.originals])
        self.assertEqual(self.timeline, ["stop", "start", "stop", "start"])
        self.assertEqual(self.rows(), [(1, "existing business record"),
                                      (2, "legitimate transaction after service restart")])
        self.assertEqual(self.rows(self.backup() / "auth-pre-pr271.sqlite"), [(1, "existing business record")])
        self.assertIn("DATABASE_NOT_RESTORED", self.output.getvalue())

    def test_third_party_code_edit_blocks_rollback_without_overwriting_any_file(self):
        name = "templates/medical_supplies_ocr.html"
        external = b"<main>External replacement during release failure</main>\n"

        def third_party_change_then_failure():
            (self.root / name).write_bytes(external)
            raise RuntimeError("synthetic service failure with external code change")

        self.start_effect = third_party_change_then_failure
        with self.assertRaisesRegex(RuntimeError, "service failure with external"):
            self.apply()
        expected = dict(self.targets)
        expected[name] = external
        self.assertEqual(self.files(), expected)
        self.assertEqual(self.timeline, ["stop", "start", "stop"])
        self.assertFalse(self.running)
        self.assertIn("CODE_ROLLBACK=FAILED; DATABASE_NOT_RESTORED", self.output.getvalue())

    def test_change_after_rollback_validation_is_not_overwritten_at_atomic_replace(self):
        actual = release.atomic_replace
        name = "templates/medical_supplies_ocr.html"
        external = b"<main>Concurrent edit after whole-set rollback validation</main>\n"
        raced = []

        def swap_after_validation(data, path, metadata, expected_blobs=None):
            relative = str(path.relative_to(self.root))
            if relative == name and data == self.originals[name]:
                # restore() has already verified all three live files. Its
                # first atomic replacement now encounters a new foreign edit.
                self.assertEqual(expected_blobs, release.FILES[name])
                path.write_bytes(external)
                raced.append(relative)
            return actual(data, path, metadata, expected_blobs)

        def fail_new_service():
            raise RuntimeError("synthetic restart failure before rollback race")

        self.start_effect = fail_new_service
        with patch.object(release, "atomic_replace", side_effect=swap_after_validation):
            with self.assertRaisesRegex(RuntimeError, "restart failure before rollback race"):
                self.apply()
        self.assertEqual(raced, [name])
        expected = dict(self.targets)
        expected[name] = external
        self.assertEqual(self.files(), expected)
        self.assertEqual(self.timeline, ["stop", "start", "stop"])
        self.assertFalse(self.running)
        self.assertEqual(list(self.root.rglob(".pr271-*")), [])
        self.assertIn("CODE_ROLLBACK=FAILED; DATABASE_NOT_RESTORED", self.output.getvalue())

    def test_database_write_between_backup_and_swap_prevents_start_of_new_code(self):
        actual = release.validate_stage
        calls = 0

        def external_write_after_backup(stage):
            nonlocal calls
            actual(stage)
            calls += 1
            if calls == 2:
                self.write_activity(2, "concurrent database write before code swap")

        with patch.object(release, "validate_stage", side_effect=external_write_after_backup):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_CHANGED_BEFORE_START"):
                self.apply()
        self.assert_old_files()
        # No start ever sees target code: the only start follows rollback.
        self.assertEqual(self.start_views, [self.originals])
        self.assertIn((2, "concurrent database write before code swap"), self.rows())
        self.assertEqual(self.rows(self.backup() / "auth-pre-pr271.sqlite"), [(1, "existing business record")])
        self.assertNotIn("DB_UNCHANGED_BEFORE_START=PASS", self.output.getvalue())

    def test_retained_wal_backup_leaves_main_and_wal_fingerprint_unchanged(self):
        writer = sqlite3.connect(self.database)
        self.addCleanup(writer.close)
        self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        writer.execute("INSERT INTO activity VALUES(2,'committed record still in WAL')")
        writer.commit()
        before = release.database_bytes()
        self.assertIsNotNone(before["auth.db-wal"])
        destination = self.backups / "isolated-wal-backup.sqlite"
        digest = release.backup_database(destination)
        self.assertEqual(digest, release.digest(destination))
        self.assertEqual(release.database_bytes(), before)
        self.assertEqual(set(before), {"auth.db", "auth.db-wal"})
        self.assertEqual(self.rows(destination), [(1, "existing business record"),
                                                 (2, "committed record still in WAL")])
        # Read-only fingerprinting excludes the -shm locking/index metadata.
        self.assertNotIn("auth.db-shm", release.database_bytes())

    def test_service_shutdown_wal_checkpoint_is_not_a_false_release_failure(self):
        writer = sqlite3.connect(self.database)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO activity VALUES(2,'record committed before service stops')")
        writer.commit()
        self.assertTrue(Path(str(self.database) + "-wal").exists())
        self.stop_effect = writer.close
        backup = self.apply()
        self.assertEqual(self.files(), self.targets)
        self.assertEqual(self.timeline, ["stop", "start"])
        self.assertEqual(self.rows(backup / "auth-pre-pr271.sqlite"), self.rows())
        self.assertIn((2, "record committed before service stops"), self.rows())
        self.assertIn("DB_UNCHANGED_BEFORE_START=PASS", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
