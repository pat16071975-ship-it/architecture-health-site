"""Synthetic deployment tests: no Production, network, systemd or pip access.

Runtime swaps, dependency directory moves, SQLite backup/fingerprints and file
permissions and wheel/manifest hashes are exercised on real temporary files.
External service work, interpreter probes and downloads use deterministic
stand-ins; no package installation is executed by this test suite.
"""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "pr273_controlled_release", Path(__file__).with_name("controlled-pr273-medical-auto.py"))
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)
REAL_SERVICE_USER = release.service_user


def tree(path):
    """Preserve bytes, modes and symlink destinations in comparison evidence."""
    result = {}
    if not path.exists():
        return result
    for child in sorted(path.rglob("*")):
        name = child.relative_to(path).as_posix()
        if child.is_symlink():
            result[name] = ("symlink", str(child.readlink()))
        elif child.is_file():
            result[name] = (child.read_bytes(), stat.S_IMODE(child.stat().st_mode))
    return result


class ControlledMedicalAutoReleaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="pr273-test-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        self.root = self.directory / "runtime"
        (self.root / "templates").mkdir(parents=True)
        self.stage = self.directory / "stage"
        (self.stage / "templates").mkdir(parents=True)
        self.backups = self.directory / "backups"
        self.backups.mkdir()
        self.database = self.directory / "auth.db"
        self.originals = {
            "medical_supplies_ocr.py": b"VALUE = 'old synthetic parser'\n",
            "templates/medical_supplies_ocr.html": b"<main>Old synthetic table</main>\n",
            "requirements.txt": b"Flask==3.0.0\n",
            "medical_supplies_ocr_enrichment.py": None,
            "medical_supplies_ocr_image.py": None,
            "medical_supplies_ocr_quality.py": None,
        }
        self.targets = {
            "medical_supplies_ocr.py": b"VALUE = 'new synthetic parser'\n",
            "templates/medical_supplies_ocr.html": b"<main>New synthetic table</main>\n",
            "requirements.txt": b"Flask==3.0.0\nnumpy==2.2.6\nopencv-python-headless==4.12.0.88\n",
            "medical_supplies_ocr_enrichment.py": b"VALUE = 'synthetic categorizer'\n",
            "medical_supplies_ocr_image.py": b"VALUE = 'synthetic image reader'\n",
            "medical_supplies_ocr_quality.py": b"VALUE = 'synthetic quality checks'\n",
        }
        self.modes = {}
        for index, (name, data) in enumerate(self.originals.items()):
            if data is not None:
                (self.root / name).write_bytes(data)
                self.modes[name] = 0o640 if index == 0 else 0o644
                (self.root / name).chmod(self.modes[name])
            (self.stage / name).write_bytes(self.targets[name])
        self.protected = {
            "app.py": b"# protected application\n",
            "server.py": b"# protected server\n",
            "mis_source_archive.py": b"# deferred PR270 stays unchanged\n",
            "templates/medical_supplies_documents.html": b"<main>Protected inbox</main>\n",
        }
        for name, data in self.protected.items():
            (self.root / name).write_bytes(data)
        self.make_venv(self.root / "venv", upgraded=False)
        self.make_venv(self.stage / "venv", upgraded=True)
        self.old_dependencies = tree(self.root / "venv")
        self.new_dependencies = tree(self.stage / "venv")
        (self.stage / "wheels").mkdir()
        for name in ("numpy-synthetic.whl", "opencv_python_headless-synthetic.whl"):
            (self.stage / "wheels" / name).write_bytes(b"Synthetic wheel evidence; never installed\n")
        self.dependency_report = {
            "before": {"flask": "3.0.0", "numpy": "1.26.4"},
            "after": {"flask": "3.0.0", **release.DEPENDENCIES},
            "live_venv_signature": release.venv_signature(self.root / "venv"),
            "staged_venv_signature": release.venv_signature(self.stage / "venv"),
            "python": "3.11.0-synthetic", "wheel_sha256": {
                path.name: release.digest(path) for path in (self.stage / "wheels").iterdir()},
            "rewritten_entrypoints": [], "service_uid": 1000, "service_gid": 1000,
            "live_writes": False,
        }
        (self.stage / "dependency-manifest.json").write_text(json.dumps(self.dependency_report))
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
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (
            ("ROOT", self.root), ("DB", self.database), ("BACKUPS", self.backups),
            ("LOCK", self.directory / "release.lock"),
            ("FILES", {name: (release.blob(data) if data is not None else None,
                              release.blob(self.targets[name]))
                       for name, data in self.originals.items()}),
            ("PROTECTED", tuple(self.protected)),
        ):
            self.stack.enter_context(patch.object(release, name, value))
        self.running = True
        self.timeline = []
        self.start_views = []
        self.stop_effect = None
        self.start_effect = None
        self.stop_mock = self.stack.enter_context(patch.object(release, "stop", side_effect=self.stop))
        self.start_mock = self.stack.enter_context(patch.object(release, "start", side_effect=self.start))
        self.stack.enter_context(patch.object(release, "active", side_effect=lambda: self.running))
        self.stack.enter_context(patch.object(release, "command",
                                             side_effect=AssertionError("No system commands in isolated tests")))
        self.stack.enter_context(patch.object(release.urllib.request, "urlopen",
                                             side_effect=AssertionError("No network in isolated tests")))
        self.stack.enter_context(patch.object(release, "service_user", return_value=
                                             SimpleNamespace(pw_uid=1000, pw_gid=1000)))
        self.stack.enter_context(patch.object(release, "probe_venv", return_value={
            "packages": self.dependency_report["after"], "dependency_imports": "PASS"}))
        self.stack.enter_context(patch.object(release, "isolated_run",
                                             side_effect=AssertionError("No external process in isolated tests")))
        self.output = io.StringIO()

    def make_venv(self, path, *, upgraded):
        files = {
            "bin/python": b"synthetic executable; never run\n",
            "pyvenv.cfg": b"home = /usr/bin\ninclude-system-site-packages = false\n",
            "lib/python3.11/site-packages/existing/__init__.py": b"EXISTING = True\n",
            "lib/python3.11/site-packages/numpy/__init__.py":
                b"VERSION = 'new synthetic numpy'\n" if upgraded else b"VERSION = 'old synthetic numpy'\n",
        }
        if upgraded:
            files["lib/python3.11/site-packages/cv2/__init__.py"] = b"VERSION = 'new synthetic cv2'\n"
        for name, data in files.items():
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o750 if name == "bin/python" else 0o640)

    def stop(self):
        self.timeline.append("stop")
        self.running = False
        if self.stop_effect:
            self.stop_effect()

    def start(self):
        self.timeline.append("start")
        self.start_views.append((self.files(), tree(self.root / "venv")))
        self.running = True
        if self.start_effect:
            self.start_effect()

    def files(self):
        return {name: ((self.root / name).read_bytes() if (self.root / name).exists() else None)
                for name in self.originals}

    def rows(self, path=None):
        with sqlite3.connect(path or self.database) as conn:
            return conn.execute("SELECT id,note FROM activity ORDER BY id").fetchall()

    def write_activity(self, number, note):
        with sqlite3.connect(self.database) as conn:
            conn.execute("INSERT INTO activity VALUES(?,?)", (number, note))

    def apply(self):
        with redirect_stdout(self.output):
            return release.apply(self.stage)

    def assert_original_installation(self):
        self.assertEqual(self.files(), self.originals)
        self.assertEqual(tree(self.root / "venv"), self.old_dependencies)
        for name, mode in self.modes.items():
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), mode)
        for name, data in self.protected.items():
            self.assertEqual((self.root / name).read_bytes(), data)

    def backup(self):
        backups = list(self.backups.iterdir())
        self.assertEqual(len(backups), 1)
        return backups[0]

    def test_success_swaps_exact_runtime_and_dependencies_without_database_writes(self):
        before = release.database_bytes()
        old_rows = self.rows()

        def at_service_start():
            self.assertEqual(release.database_bytes(), before)
            self.assertEqual(self.files(), self.targets)
            self.assertEqual(tree(self.root / "venv"), self.new_dependencies)
            self.assertIn("DB_UNCHANGED_BEFORE_START=PASS", self.output.getvalue())

        self.start_effect = at_service_start
        backup = self.apply()
        self.assertEqual(self.timeline, ["stop", "start"])
        self.assertTrue(self.running)
        self.assertEqual(release.database_bytes(), before)
        self.assertEqual(self.rows(), old_rows)
        saved_db = backup / "auth-pre-pr273.sqlite"
        self.assertEqual(self.rows(saved_db), old_rows)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(saved_db.stat().st_mode), 0o600)
        manifest = json.loads((backup / "release-manifest.json").read_text())
        self.assertFalse(manifest["auto_db_restore"])
        self.assertEqual(manifest["database_before"], before)
        self.assertEqual(manifest["merged_sha"], release.MERGED)
        self.assertEqual(manifest["base"], release.BASE)
        self.assertEqual(manifest["audited_head"], release.AUDITED)
        self.assertEqual(manifest["files"], {name: list(hashes) for name, hashes in release.FILES.items()})
        for name, data in self.originals.items():
            if data is not None:
                self.assertEqual((backup / "files" / name).read_bytes(), data)
                self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), self.modes[name])
            else:
                self.assertFalse((backup / "files" / name).exists())
        for name, data in self.protected.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertIn("PR273_RELEASE=PASS", self.output.getvalue())

    def test_required_new_file_is_rejected_even_if_bytes_equal_the_target(self):
        name = "medical_supplies_ocr_image.py"
        for data in (b"VALUE = 'foreign implementation'\n", self.targets[name]):
            with self.subTest(data=data):
                (self.root / name).write_bytes(data)
                with self.assertRaises(RuntimeError):
                    self.apply()
                self.assertEqual(self.timeline, [])
                self.assertEqual(list(self.backups.iterdir()), [])
                self.assertEqual((self.root / name).read_bytes(), data)
                (self.root / name).unlink()
        self.assert_original_installation()

    def test_new_file_symlink_is_rejected_without_touching_its_target(self):
        outside = self.directory / "outside.py"
        outside.write_bytes(b"EXTERNAL = True\n")
        name = "medical_supplies_ocr_image.py"
        (self.root / name).symlink_to(outside)
        with self.assertRaises(RuntimeError):
            self.apply()
        self.assertEqual(outside.read_bytes(), b"EXTERNAL = True\n")
        self.assertTrue((self.root / name).is_symlink())
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_staged_runtime_tamper_is_rejected_before_stop_or_backup(self):
        (self.stage / "medical_supplies_ocr.py").write_bytes(b"VALUE = 'tampered'\n")
        with self.assertRaisesRegex(RuntimeError, "STAGED_TARGET_MISMATCH"):
            self.apply()
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assert_original_installation()

    def test_staged_dependency_failure_precedes_all_live_writes(self):
        dependency = self.stage / "venv/lib/python3.11/site-packages/cv2/__init__.py"
        dependency.write_bytes(b"INCOMPLETE_WHEEL = True\n")
        before = release.database_bytes()
        with self.assertRaisesRegex(RuntimeError, "STAGED_DEPENDENCIES_CHANGED"):
            self.apply()
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assertEqual(release.database_bytes(), before)
        self.assert_original_installation()

    def test_wheel_tampering_is_rejected_before_stop_or_backup(self):
        wheel = next((self.stage / "wheels").iterdir())
        wheel.write_bytes(b"unexpected wheel bytes\n")
        with self.assertRaisesRegex(RuntimeError, "WHEEL_HASH_CHANGED"):
            self.apply()
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assert_original_installation()

    def test_dependency_download_failure_only_touches_an_independent_staging_copy(self):
        shutil.rmtree(self.stage / "venv")
        shutil.rmtree(self.stage / "wheels")
        before = release.database_bytes()
        baseline = {"packages": self.dependency_report["before"],
                    "python": "3.11.0-synthetic"}
        commands = []

        def inventory(path, stage, dependencies=False, user=None):
            return {**baseline, "site_packages": [str(path / "lib/python3.11/site-packages")]}

        def failed_download(args, stage, marker, timeout=120, user=None):
            commands.append(args)
            self.assertEqual(args[0], str(self.stage / "venv/bin/python"))
            self.assertIn("download", args)
            self.assertEqual(stage, self.stage)
            self.assertEqual(tree(self.root / "venv"), self.old_dependencies)
            raise RuntimeError("DEPENDENCY_DOWNLOAD_FAILED")

        with patch.object(release, "probe_venv", side_effect=inventory), \
             patch.object(release, "isolated_run", side_effect=failed_download), \
             redirect_stdout(self.output):
            with self.assertRaisesRegex(RuntimeError, "DEPENDENCY_DOWNLOAD_FAILED"):
                release.prepare_dependencies(self.stage)
        self.assertEqual(len(commands), 1)
        self.assertEqual(tree(self.stage / "venv"), self.old_dependencies)
        relative = "lib/python3.11/site-packages/numpy/__init__.py"
        live = self.root / "venv" / relative
        staged = self.stage / "venv" / relative
        self.assertNotEqual(live.stat().st_ino, staged.stat().st_ino)
        staged.write_bytes(b"staged-only partial dependency changes\n")
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assertEqual(release.database_bytes(), before)
        self.assert_original_installation()

    def test_absolute_dependency_record_cannot_make_staged_pip_uninstall_live_files(self):
        shutil.rmtree(self.stage / "venv")
        shutil.rmtree(self.stage / "wheels")
        site_relative = "lib/python3.11/site-packages"
        live_package = self.root / "venv" / site_relative / "numpy/__init__.py"
        record = self.root / "venv" / site_relative / "numpy-1.26.4.dist-info/RECORD"
        record.parent.mkdir()
        record.write_text(str(live_package) + ",,\n")
        self.old_dependencies = tree(self.root / "venv")
        before = release.database_bytes()

        def inventory(path, stage, dependencies=False, user=None):
            return {"packages": self.dependency_report["before"],
                    "site_packages": [str(path / site_relative)], "python": "3.11.0-synthetic"}

        with patch.object(release, "probe_venv", side_effect=inventory), \
             patch.object(release, "isolated_run",
                          side_effect=AssertionError("Pip must not run with an outside RECORD")) as run:
            with self.assertRaisesRegex(RuntimeError, "DEPENDENCY_RECORD_OUTSIDE_STAGE"):
                release.prepare_dependencies(self.stage)
            run.assert_not_called()
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assertEqual(release.database_bytes(), before)
        self.assert_original_installation()

    def test_live_base_drift_is_rejected_and_never_overwritten(self):
        name = "templates/medical_supplies_ocr.html"
        external = b"<main>External newer edit</main>\n"
        (self.root / name).write_bytes(external)
        with self.assertRaisesRegex(RuntimeError, "LIVE_BASE_MISMATCH"):
            self.apply()
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assertEqual((self.root / name).read_bytes(), external)
        self.assertEqual(tree(self.root / "venv"), self.old_dependencies)

    def test_noncanonical_runtime_root_is_rejected_before_service_stop(self):
        alias = self.directory / "runtime-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with patch.object(release, "ROOT", alias):
            with self.assertRaisesRegex(RuntimeError, "DIRECTORY_NOT_CANONICAL"):
                self.apply()
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assert_original_installation()

    def test_partial_file_swap_restores_existing_files_and_removes_new_files(self):
        actual = release.atomic_replace
        created = []

        def fail_last_new_file(data, path, metadata, expected_blobs=None):
            name = path.relative_to(self.root).as_posix()
            if data == self.targets[name] and name == "medical_supplies_ocr_quality.py":
                raise OSError("synthetic last new-file replacement failure")
            result = actual(data, path, metadata, expected_blobs)
            if self.originals[name] is None and data == self.targets[name]:
                created.append(name)
            return result

        before = release.database_bytes()
        with patch.object(release, "atomic_replace", side_effect=fail_last_new_file):
            with self.assertRaisesRegex(OSError, "last new-file replacement"):
                self.apply()
        self.assertTrue(created, "The injected failure must follow creation of a new runtime file")
        self.assert_original_installation()
        self.assertEqual(self.timeline, ["stop", "stop", "start"])
        self.assertEqual(self.start_views, [(self.originals, self.old_dependencies)])
        self.assertEqual(release.database_bytes(), before)
        self.assertIn("DATABASE_NOT_RESTORED", self.output.getvalue())

    def test_second_dependency_rename_failure_restores_old_venv_and_runtime(self):
        actual = release.os.replace
        failures = []

        def fail_installing_staged_venv(source, destination):
            if Path(source) == self.stage / "venv" and Path(destination) == self.root / "venv":
                failures.append((source, destination))
                self.assertFalse((self.root / "venv").exists())
                raise OSError("synthetic second dependency rename failure")
            return actual(source, destination)

        before = release.database_bytes()
        with patch.object(release.os, "replace", side_effect=fail_installing_staged_venv):
            with self.assertRaisesRegex(OSError, "second dependency rename failure"):
                self.apply()
        self.assertEqual(len(failures), 1)
        self.assert_original_installation()
        self.assertEqual(self.start_views, [(self.originals, self.old_dependencies)])
        self.assertEqual(self.timeline, ["stop", "stop", "start"])
        self.assertEqual(release.database_bytes(), before)
        self.assertIn("DEPENDENCY_ROLLBACK=PASS", self.output.getvalue())

    def test_backup_failure_never_changes_runtime_or_dependencies(self):
        actual = release.backup_database

        def fail_after_real_backup(path):
            actual(path)
            raise sqlite3.OperationalError("synthetic backup verification failure")

        before = release.database_bytes()
        with patch.object(release, "backup_database", side_effect=fail_after_real_backup), \
             patch.object(release, "atomic_replace", wraps=release.atomic_replace) as swap:
            with self.assertRaisesRegex(sqlite3.OperationalError, "backup verification failure"):
                self.apply()
            swap.assert_not_called()
        self.assert_original_installation()
        self.assertEqual(self.timeline, ["stop", "start"])
        self.assertEqual(release.database_bytes(), before)

    def test_post_restart_failure_rolls_back_code_and_venv_but_keeps_business_writes(self):
        def traffic_then_first_start_failure():
            if self.start_mock.call_count == 1:
                self.write_activity(2, "legitimate transaction after restart")
                raise RuntimeError("synthetic post-restart smoke failure")

        self.start_effect = traffic_then_first_start_failure
        with self.assertRaisesRegex(RuntimeError, "post-restart smoke failure"):
            self.apply()
        self.assert_original_installation()
        self.assertEqual(self.timeline, ["stop", "start", "stop", "start"])
        self.assertEqual(self.start_views, [(self.targets, self.new_dependencies),
                                           (self.originals, self.old_dependencies)])
        self.assertEqual(self.rows(), [(1, "existing business record"),
                                      (2, "legitimate transaction after restart")])
        self.assertEqual(self.rows(self.backup() / "auth-pre-pr273.sqlite"),
                         [(1, "existing business record")])

    def test_foreign_runtime_edit_blocks_rollback_without_overwriting_it(self):
        name = "medical_supplies_ocr_image.py"
        external = b"VALUE = 'external change after restart'\n"

        def external_change():
            (self.root / name).write_bytes(external)
            raise RuntimeError("synthetic service failure with foreign code")

        self.start_effect = external_change
        with self.assertRaisesRegex(RuntimeError, "service failure with foreign"):
            self.apply()
        self.assertEqual((self.root / name).read_bytes(), external)
        self.assertFalse(self.running)
        self.assertEqual(self.timeline, ["stop", "start", "stop"])
        self.assertIn("ROLLBACK=FAILED", self.output.getvalue())

    def test_foreign_replacement_after_rollback_validation_is_not_unlinked(self):
        actual = release.file_state
        name = "medical_supplies_ocr_quality.py"
        external = b"VALUE = 'foreign replacement after rollback validation'\n"
        rolling_back = False
        injected = []

        def fail_first_start():
            nonlocal rolling_back
            if self.start_mock.call_count == 1:
                rolling_back = True
                raise RuntimeError("synthetic restart failure before unlink race")

        def replace_after_validating_last_file(path):
            value = actual(path)
            if rolling_back and path == self.root / name and not injected:
                self.assertEqual(value, release.FILES[name][1])
                path.write_bytes(external)
                injected.append(name)
            return value

        self.start_effect = fail_first_start
        with patch.object(release, "file_state", side_effect=replace_after_validating_last_file):
            with self.assertRaisesRegex(RuntimeError, "restart failure before unlink race"):
                self.apply()
        self.assertEqual(injected, [name])
        self.assertTrue((self.root / name).exists(), "Rollback must not unlink a foreign replacement")
        self.assertEqual((self.root / name).read_bytes(), external)
        self.assertFalse(self.running)
        self.assertEqual(self.timeline, ["stop", "start", "stop"])
        self.assertIn("ROLLBACK=FAILED", self.output.getvalue())

    def test_database_change_before_start_reverts_code_without_reverting_data(self):
        actual = release.validate_stage
        count = 0

        def external_write_after_backup(stage):
            nonlocal count
            actual(stage)
            count += 1
            if count == 2:
                self.write_activity(2, "concurrent database write before code swap")

        with patch.object(release, "validate_stage", side_effect=external_write_after_backup):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_CHANGED_BEFORE_START"):
                self.apply()
        self.assert_original_installation()
        self.assertEqual(self.start_views, [(self.originals, self.old_dependencies)])
        self.assertIn((2, "concurrent database write before code swap"), self.rows())
        self.assertNotIn("DB_UNCHANGED_BEFORE_START=PASS", self.output.getvalue())

    def test_live_database_connections_are_readonly_and_backup_is_separate(self):
        connect = sqlite3.connect
        calls = []

        def record_connection(database, *args, **kwargs):
            calls.append((str(database), kwargs.get("uri", False)))
            return connect(database, *args, **kwargs)

        with patch.object(release.sqlite3, "connect", side_effect=record_connection):
            self.apply()
        live = [call for call in calls if str(self.database) in call[0]]
        self.assertTrue(live)
        self.assertTrue(all(uri and value.endswith("?mode=ro") for value, uri in live), live)
        writable = [value for value, uri in calls if not uri]
        self.assertTrue(writable)
        self.assertTrue(all(Path(value).is_relative_to(self.backups) for value in writable), writable)

    def test_wal_backup_does_not_modify_live_database_or_wal(self):
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
        self.assertEqual(self.rows(destination), [(1, "existing business record"),
                                                 (2, "committed record still in WAL")])
        self.assertNotIn("auth.db-shm", before)

    def test_main_wrong_host_or_unprivileged_user_stops_before_lock_or_service(self):
        for uid, hostname in ((1000, "vdska"), (0, "some-other-host")):
            with self.subTest(uid=uid, hostname=hostname), \
                 patch.object(sys, "argv", ["installer", "--apply"]), \
                 patch.object(release.os, "geteuid", return_value=uid), \
                 patch.object(release.socket, "gethostname", return_value=hostname), \
                 patch.object(release.os, "open", side_effect=AssertionError("Must stop before lock")):
                with self.assertRaisesRegex(RuntimeError, "RUN_IN_ROOT_VDSKA"):
                    release.main()
        self.assertEqual(self.timeline, [])
        self.assert_original_installation()

    def test_service_account_must_be_explicit_and_nonroot_before_staging(self):
        for name in ("", "root"):
            with self.subTest(service_user=name), \
                 patch.object(sys, "argv", ["installer", "--preflight"]), \
                 patch.object(release.os, "geteuid", return_value=0), \
                 patch.object(release.socket, "gethostname", return_value="vdska"), \
                 patch.object(release.os, "umask"), \
                 patch.object(release, "smoke"), \
                 patch.object(release, "service_user", side_effect=REAL_SERVICE_USER), \
                 patch.object(release, "command", return_value=name), \
                 patch.object(release.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=0, pw_gid=0)), \
                 patch.object(release, "prepare", side_effect=AssertionError("Must reject before staging")):
                with self.assertRaisesRegex(RuntimeError, "SERVICE_MUST_BE_NONROOT"):
                    release.main()
        self.assertEqual(self.timeline, [])
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assert_original_installation()


if __name__ == "__main__":
    unittest.main()
