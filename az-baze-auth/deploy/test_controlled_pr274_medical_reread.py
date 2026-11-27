"""Isolated deployment/rollback tests; never contact or use a live installation.

External downloads, service/host checks and injected faults are mocked. All
file replacements, hashes, SQLite backups and fingerprints use temporary files.
The real artifact stays unauthorized; successful apply tests patch its gate
only inside their isolated process.
"""
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch
from types import SimpleNamespace


SPEC = importlib.util.spec_from_file_location(
    "pr274_controlled_release", Path(__file__).with_name("controlled-pr274-medical-reread.py"))
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


class ReleasePinTests(unittest.TestCase):
    def test_audited_source_and_four_existing_runtime_paths_are_explicitly_pinned(self):
        self.assertEqual(release.BASE, "e30b84c374da87013cc4f7089558cfeeba68c206")
        self.assertEqual(release.AUDITED, "58d8bb351d9e216269f4d27cbe30647fe8e1e890")
        self.assertEqual(set(release.FILES), {
            "medical_supplies_ocr.py", "medical_supplies_documents.py",
            "templates/medical_supplies_ocr.html", "templates/medical_supplies_documents.html",
        })
        self.assertEqual(set(release.DEPENDENT_FILES), {
            "medical_supplies_ocr_image.py", "medical_supplies_ocr_enrichment.py",
            "medical_supplies_ocr_quality.py", "requirements.txt",
        })
        self.assertFalse(set(release.FILES) & set(release.DEPENDENT_FILES))
        for old, target in release.FILES.values():
            self.assertRegex(old, r"^[0-9a-f]{40}$")
            self.assertRegex(target, r"^[0-9a-f]{40}$")
            self.assertNotEqual(old, target)


class ControlledMedicalRereadReleaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="pr274-test-")
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
            "templates/medical_supplies_ocr.html": b"<main>Old synthetic table</main>\n",
            "medical_supplies_documents.py": b"VALUE = 'old synthetic inbox'\n",
            "templates/medical_supplies_documents.html": b"<main>Old synthetic inbox</main>\n",
        }
        self.targets = {
            "medical_supplies_ocr.py": b"VALUE = 'new synthetic parser'\n",
            "templates/medical_supplies_ocr.html": b"<main>New synthetic table</main>\n",
            "medical_supplies_documents.py": b"VALUE = 'new synthetic inbox'\n",
            "templates/medical_supplies_documents.html": b"<main>New synthetic inbox</main>\n",
        }
        self.modes = {}
        for index, name in enumerate(self.originals):
            (self.root / name).write_bytes(self.originals[name])
            (self.stage / name).write_bytes(self.targets[name])
            self.modes[name] = 0o640 if index == 0 else 0o644
            (self.root / name).chmod(self.modes[name])
        self.dependent = {
            "medical_supplies_ocr_image.py": b"VALUE = 'unchanged image engine'\n",
            "medical_supplies_ocr_enrichment.py": b"VALUE = 'unchanged enrichment'\n",
            "medical_supplies_ocr_quality.py": b"VALUE = 'unchanged quality checks'\n",
            "requirements.txt": b"numpy==2.2.6\nopencv-python-headless==4.12.0.88\n",
        }
        self.protected = {
            "app.py": b"# protected application\n",
            "server.py": b"# protected server\n",
            "mis_source_archive.py": b"# deferred PR270 stays unchanged\n",
            "venv/dependency-marker.txt": b"Installed dependencies must not change\n",
            **self.dependent,
        }
        for name, data in self.protected.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
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
            ("LOCK", self.directory / "release.lock"),
            ("RELEASE_AUTHORIZED", True), ("MERGED", "a" * 40),
            ("DEPENDENT_FILES", {name: release.blob(data) for name, data in self.dependent.items()}),
            ("FILES", {name: (release.blob(self.originals[name]), release.blob(self.targets[name]))
                       for name in self.originals}),
            ("PROTECTED", tuple(self.protected)),
        ):
            stack.enter_context(patch.object(release, name, value))
        self.running = True
        self.timeline = []
        self.start_views = []
        self.started_targets = []
        self.stop_effect = None
        self.start_effect = None
        self.stop_mock = stack.enter_context(patch.object(release, "stop", side_effect=self.stop))
        self.start_mock = stack.enter_context(patch.object(release, "start", side_effect=self.start))
        stack.enter_context(patch.object(release, "active", side_effect=lambda: self.running))
        stack.enter_context(patch.object(release, "command", side_effect=AssertionError("No system commands in isolated tests")))
        stack.enter_context(patch.object(release.urllib.request, "urlopen",
                                        side_effect=AssertionError("No network in isolated tests")))
        self.output = io.StringIO()
        self.download_urls = []

    def stop(self):
        self.timeline.append("stop")
        self.running = False
        if self.stop_effect:
            self.stop_effect()

    def start(self, target=False):
        self.timeline.append("start")
        self.started_targets.append(target)
        self.start_views.append(self.files())
        self.running = True
        if self.start_effect:
            self.start_effect()

    def files(self):
        return {name: (self.root / name).read_bytes() for name in self.originals}

    def runtime_snapshot(self):
        return {path.relative_to(self.root).as_posix():
                (path.read_bytes(), stat.S_IMODE(path.stat().st_mode), path.stat().st_uid, path.stat().st_gid)
                for path in self.root.rglob("*") if path.is_file()}

    def synthetic_download(self, request, **kwargs):
        class Response(io.BytesIO):
            status = 200
        url = request.full_url
        self.download_urls.append(url)
        name = url.split("/az-baze-auth/", 1)[1]
        return Response(self.targets[name])

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
        self.assertEqual(list(self.root.rglob(".pr274-*")), [])

    def backup(self):
        backups = list(self.backups.iterdir())
        self.assertEqual(len(backups), 1)
        return backups[0]

    def test_success_exact_four_files_backup_and_fingerprint_before_start(self):
        before = release.database_bytes()
        old_rows = self.rows()

        def verify_start_boundary():
            self.assertEqual(release.database_bytes(), before)
            self.assertEqual(self.files(), self.targets)
            self.assertIn("DB_UNCHANGED_BEFORE_START=PASS", self.output.getvalue())

        self.start_effect = verify_start_boundary
        backup = self.apply()
        self.assertEqual(self.timeline, ["stop", "start"])
        self.assertEqual(self.started_targets, [True])
        self.assertTrue(self.running)
        self.assertEqual(self.files(), self.targets)
        self.assertEqual(release.database_bytes(), before)
        self.assertEqual(self.rows(), old_rows)
        self.assertEqual(self.rows(backup / "auth-pre-pr274.sqlite"), old_rows)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((backup / "auth-pre-pr274.sqlite").stat().st_mode), 0o600)
        manifest = json.loads((backup / "release-manifest.json").read_text())
        self.assertFalse(manifest["auto_db_restore"])
        self.assertEqual(manifest["database_before"], before)
        self.assertEqual(manifest["merged_sha"], release.MERGED)
        self.assertEqual(manifest["base"], release.BASE)
        self.assertEqual(manifest["audited_head"], release.AUDITED)
        self.assertEqual(manifest["files"], {name: list(pair) for name, pair in release.FILES.items()})
        for name, data in self.originals.items():
            self.assertEqual((backup / "files" / name).read_bytes(), data)
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), self.modes[name])
        for name, data in self.protected.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertIn("PR274_RELEASE=PASS; RUNTIME_FILES=4", self.output.getvalue())
        self.assertIn("DEPENDENCIES=UNCHANGED", self.output.getvalue())

    def test_partial_swap_failure_restores_all_four_files(self):
        actual = release.atomic_replace
        replaced = []

        def fail_second_install(data, path, metadata, expected_blobs=None):
            name = str(path.relative_to(self.root))
            if data == self.targets[name] and name == "templates/medical_supplies_ocr.html":
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
        name = "templates/medical_supplies_ocr.html"
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
        self.assertEqual(self.rows(self.backup() / "auth-pre-pr274.sqlite"), self.rows())

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
        self.assertEqual(self.started_targets, [True, False])
        self.assertEqual(self.timeline, ["stop", "start", "stop", "start"])
        self.assertEqual(self.rows(), [(1, "existing business record"),
                                      (2, "legitimate transaction after service restart")])
        self.assertEqual(self.rows(self.backup() / "auth-pre-pr274.sqlite"), [(1, "existing business record")])
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
        name = next(reversed(self.originals))
        external = b"<main>Concurrent edit after whole-set rollback validation</main>\n"
        raced = []

        def swap_after_validation(data, path, metadata, expected_blobs=None):
            relative = str(path.relative_to(self.root))
            if relative == name and data == self.originals[name]:
                # restore() has already verified all four live files. Its
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
        self.assertEqual(list(self.root.rglob(".pr274-*")), [])
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
        self.assertEqual(self.rows(self.backup() / "auth-pre-pr274.sqlite"), [(1, "existing business record")])
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
        self.assertEqual(self.rows(backup / "auth-pre-pr274.sqlite"), self.rows())
        self.assertIn((2, "record committed before service stops"), self.rows())
        self.assertIn("DB_UNCHANGED_BEFORE_START=PASS", self.output.getvalue())

    def test_apply_gate_precedes_host_lock_staging_and_all_live_operations(self):
        before = self.runtime_snapshot(), release.database_bytes()
        for authorized, merged in (
            (False, None), (False, "a" * 40), (True, None), (True, "main"),
            (True, release.BASE), (True, release.AUDITED), ("yes", "a" * 40),
        ):
            with self.subTest(authorized=authorized, merged=merged), \
                 patch.object(release, "RELEASE_AUTHORIZED", authorized), \
                 patch.object(release, "MERGED", merged), \
                 patch.object(release, "validate_stage", side_effect=AssertionError("Gate must precede staging")), \
                 patch.object(release.os, "geteuid", side_effect=AssertionError("Gate must precede host check")), \
                 patch.object(release.os, "open", side_effect=AssertionError("Gate must precede lock")), \
                 patch.object(sys, "argv", ["installer", "--apply"]):
                with self.assertRaisesRegex(RuntimeError, "RELEASE_NOT_AUTHORIZED"):
                    self.apply()
                with self.assertRaisesRegex(RuntimeError, "RELEASE_NOT_AUTHORIZED"):
                    release.main()
        self.assertEqual((self.runtime_snapshot(), release.database_bytes()), before)
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assertFalse(release.LOCK.exists())

    def test_prepare_downloads_only_pinned_audited_or_merged_bytes_and_rejects_wrong_content(self):
        before = self.runtime_snapshot(), release.database_bytes()
        for authorized, merged, expected_commit in (
            (False, None, release.AUDITED), (True, "a" * 40, "a" * 40),
        ):
            with self.subTest(commit=expected_commit), \
                 patch.object(release, "RELEASE_AUTHORIZED", authorized), \
                 patch.object(release, "MERGED", merged), \
                 patch.object(release.urllib.request, "urlopen", side_effect=self.synthetic_download):
                self.download_urls.clear()
                with redirect_stdout(self.output):
                    release.prepare(self.stage)
                self.assertEqual(set(self.download_urls), {
                    f"https://raw.githubusercontent.com/{release.REPO}/{expected_commit}/az-baze-auth/{name}"
                    for name in self.targets
                })
        def wrong_content(request, **kwargs):
            response = self.synthetic_download(request, **kwargs)
            response.seek(0)
            response.write(b"altered source")
            response.seek(0)
            return response
        with patch.object(release.urllib.request, "urlopen", side_effect=wrong_content):
            with self.assertRaisesRegex(RuntimeError, "EXACT_SOURCE_MISMATCH"):
                release.prepare(self.stage)
        self.assertEqual((self.runtime_snapshot(), release.database_bytes()), before)
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_default_preflight_needs_no_release_permission_and_has_no_live_writes(self):
        before = self.runtime_snapshot(), release.database_bytes()
        def service_query(*args):
            self.assertEqual(args, ("systemctl", "show", "-p", "User", "--value", release.SERVICE))
            return "synthetic-service-user"
        with patch.object(sys, "argv", ["installer"]), \
             patch.object(release, "RELEASE_AUTHORIZED", False), \
             patch.object(release, "MERGED", None), \
             patch.object(release.os, "geteuid", return_value=0), \
             patch.object(release.socket, "gethostname", return_value="vdska"), \
             patch.object(release.os, "umask"), \
             patch.object(release, "smoke") as smoke, \
             patch.object(release, "command", side_effect=service_query), \
             patch.object(release.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000)), \
             patch.object(release.shutil, "disk_usage", return_value=SimpleNamespace(free=10**9)), \
             patch.object(release.urllib.request, "urlopen", side_effect=self.synthetic_download), \
             redirect_stdout(self.output):
            release.main()
        smoke.assert_called_once_with()
        self.assertEqual((self.runtime_snapshot(), release.database_bytes()), before)
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assertFalse(release.LOCK.exists())
        self.assertIn("PR274_PREFLIGHT=PASS", self.output.getvalue())
        self.assertIn("NOT_RELEASED; APPLY=DISABLED; SOURCE_COMMIT=" + release.AUDITED, self.output.getvalue())
        self.assertNotIn("PR274_RELEASE=PASS", self.output.getvalue())

    def test_dependent_helper_or_requirements_drift_blocks_apply_before_stop(self):
        for name in self.dependent:
            with self.subTest(name=name):
                external = b"Changed dependency baseline\n"
                (self.root / name).write_bytes(external)
                with self.assertRaises(RuntimeError):
                    self.apply()
                self.assertEqual((self.root / name).read_bytes(), external)
                self.assertEqual(self.files(), self.originals)
                self.assertEqual(self.timeline, [])
                self.assertEqual(list(self.backups.iterdir()), [])
                (self.root / name).write_bytes(self.dependent[name])

    def test_live_database_connections_are_readonly_and_backup_writes_are_separate(self):
        connect = sqlite3.connect
        calls = []
        def record_connection(database, *args, **kwargs):
            calls.append((str(database), kwargs.get("uri", False)))
            return connect(database, *args, **kwargs)
        with patch.object(release.sqlite3, "connect", side_effect=record_connection):
            self.apply()
        live = [(path, uri) for path, uri in calls if str(self.database) in path]
        self.assertTrue(live)
        self.assertTrue(all(uri and path.endswith("?mode=ro") for path, uri in live), live)
        writable = [path for path, uri in calls if not uri]
        self.assertTrue(writable)
        self.assertTrue(all(Path(path).is_relative_to(self.backups) for path in writable), writable)

    def test_install_race_does_not_overwrite_a_foreign_edit_after_final_validation(self):
        actual = release.atomic_replace
        name = next(iter(self.originals))
        external = b"VALUE = 'foreign edit at install boundary'\n"
        def race(data, path, metadata, expected_blobs=None):
            if path == self.root / name and data == self.targets[name]:
                path.write_bytes(external)
            return actual(data, path, metadata, expected_blobs)
        with patch.object(release, "atomic_replace", side_effect=race):
            with self.assertRaisesRegex(RuntimeError, "REPLACE_THIRD_PARTY_CHANGE"):
                self.apply()
        expected = dict(self.originals)
        expected[name] = external
        self.assertEqual(self.files(), expected)
        self.assertEqual(self.timeline, ["stop", "stop"])
        self.assertFalse(self.running)
        self.assertEqual(self.start_views, [])
        self.assertIn("CODE_ROLLBACK=FAILED; DATABASE_NOT_RESTORED", self.output.getvalue())

    def test_wrong_host_or_unprivileged_user_stops_before_lock_or_service(self):
        for uid, hostname in ((1000, "vdska"), (0, "other-host")):
            with self.subTest(uid=uid, hostname=hostname), \
                 patch.object(sys, "argv", ["installer", "--apply"]), \
                 patch.object(release.os, "geteuid", return_value=uid), \
                 patch.object(release.socket, "gethostname", return_value=hostname), \
                 patch.object(release.os, "open", side_effect=AssertionError("Must stop before lock")):
                with self.assertRaisesRegex(RuntimeError, "RUN_IN_ROOT_VDSKA"):
                    release.main()
        self.assertEqual(self.timeline, [])
        self.assert_old_files()

    def test_smoke_checks_new_apply_route_only_after_target_code_is_installed(self):
        new_route = "/accounting/medical-supplies/incoming/1/ocr/automatic/apply"
        def get(path):
            if path == "/health":
                return 200, b"ok", ""
            if path.endswith(("/page", "/save", "/automatic/apply")):
                return 405, b"", ""
            return 302, b"", "/login"
        with patch.object(release, "http_get", side_effect=get) as http, redirect_stdout(self.output):
            release.smoke(target=False)
            self.assertNotIn(new_route, [call.args[0] for call in http.call_args_list])
            http.reset_mock()
            release.smoke(target=True)
            self.assertIn(new_route, [call.args[0] for call in http.call_args_list])
        self.assertEqual(self.timeline, [])


if __name__ == "__main__":
    unittest.main()
