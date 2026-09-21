import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import db_migrations


MODULE_PATH = Path(__file__).resolve().parent / "tools" / "controlled_structure_foundation_apply.py"
SPEC = importlib.util.spec_from_file_location("controlled_structure_foundation_apply", MODULE_PATH)
controlled = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controlled)

HANDOFF_PATH = Path(__file__).resolve().parent / "tools" / "controlled_structure_foundation_handoff.py"
HANDOFF_SPEC = importlib.util.spec_from_file_location("controlled_structure_foundation_handoff", HANDOFF_PATH)
handoff = importlib.util.module_from_spec(HANDOFF_SPEC)
HANDOFF_SPEC.loader.exec_module(handoff)


def create_legacy_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA foreign_keys=ON;

        CREATE TABLE users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE permissions(
            user_id INTEGER NOT NULL,
            section TEXT NOT NULL,
            PRIMARY KEY(user_id, section),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE credential_trash(id INTEGER PRIMARY KEY);
        CREATE TABLE credentials(id INTEGER PRIMARY KEY);
        CREATE TABLE daily_uploads(id INTEGER PRIMARY KEY);
        CREATE TABLE report_blobs(id INTEGER PRIMARY KEY);
        CREATE TABLE report_data(id INTEGER PRIMARY KEY);
        CREATE TABLE survey_answers(id INTEGER PRIMARY KEY);
        CREATE TABLE survey_invites(id INTEGER PRIMARY KEY);
        CREATE TABLE survey_options(id INTEGER PRIMARY KEY);
        CREATE TABLE survey_questions(id INTEGER PRIMARY KEY);
        CREATE TABLE survey_responses(id INTEGER PRIMARY KEY);
        CREATE TABLE survey_sections(id INTEGER PRIMARY KEY);
        CREATE TABLE surveys(id INTEGER PRIMARY KEY);
        CREATE TABLE upload_log(id INTEGER PRIMARY KEY);
        CREATE TABLE work_contacts(id INTEGER PRIMARY KEY);

        CREATE INDEX idx_report_data_test ON report_data(id);
        """
    )
    conn.execute(
        """
        INSERT INTO users(email,password_hash,active,created_at,updated_at)
        VALUES('owner@example.test','x',1,'now','now')
        """
    )
    user_id = conn.execute("SELECT id FROM users").fetchone()[0]
    conn.execute(
        "INSERT INTO permissions(user_id,section) VALUES(?,?)",
        (user_id, "reports"),
    )
    conn.execute("INSERT INTO audit_log(action,created_at) VALUES('baseline','now')")
    conn.execute("INSERT INTO report_data(id) VALUES(1)")
    conn.execute("INSERT INTO report_blobs(id) VALUES(1)")
    conn.commit()
    conn.close()


class ControlledStructureFoundationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "auth.db"
        create_legacy_db(self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_preflight_accepts_exact_legacy_table_set(self):
        result = controlled.preflight_current_database(self.db_path)
        self.assertEqual(set(result["tables"]), controlled.LEGACY_TABLES)
        self.assertEqual(result["row_counts"]["users"], 1)
        self.assertEqual(result["row_counts"]["report_data"], 1)

    def test_preflight_rejects_unexpected_structure_or_missing_table(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE holdings(id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        with self.assertRaises(controlled.ControlledApplyError):
            controlled.preflight_current_database(self.db_path)

    def test_backup_is_verified_against_quiescent_baseline(self):
        baseline = controlled.capture_quiescent_baseline(self.db_path)
        backup = self.root / "backup-dir" / "auth-before.db"
        meta = controlled.backup_database(self.db_path, backup, baseline)

        self.assertTrue(backup.exists())
        self.assertEqual(meta["sha256"], controlled.sha256_file(backup))
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup.parent.stat().st_mode & 0o777, 0o700)
        controlled.verify_exact_baseline(backup, baseline)

    def test_postapply_verifies_foundation_and_preserves_legacy(self):
        baseline = controlled.capture_quiescent_baseline(self.db_path)
        migrations_dir = Path(__file__).resolve().parent / "migrations"

        result = db_migrations.apply_migrations(self.db_path, migrations_dir)
        self.assertEqual(
            result,
            {"applied": [controlled.FOUNDATION_VERSION], "already_applied": []},
        )

        summary = controlled.verify_postapply(self.db_path, baseline)
        self.assertEqual(summary["migration"]["version"], controlled.FOUNDATION_VERSION)
        self.assertEqual(summary["migration"]["name"], controlled.FOUNDATION_NAME)

        conn = sqlite3.connect(self.db_path)
        try:
            for table in controlled.FOUNDATION_TABLES:
                self.assertEqual(
                    conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0],
                    0,
                )
        finally:
            conn.close()

    def test_postapply_detects_legacy_row_change(self):
        baseline = controlled.capture_quiescent_baseline(self.db_path)
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        db_migrations.apply_migrations(self.db_path, migrations_dir)

        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO report_data(id) VALUES(2)")
        conn.commit()
        conn.close()

        with self.assertRaises(controlled.ControlledApplyError):
            controlled.verify_postapply(self.db_path, baseline)

    def test_configured_database_path_uses_explicit_or_default_value(self):
        env = self.root / "auth.env"
        env.write_text("AZBAZE_DB=/tmp/custom-auth.db\n", encoding="utf-8")
        self.assertEqual(
            controlled.configured_database_path(env),
            Path("/tmp/custom-auth.db"),
        )

        env.write_text("# no override\nAZBAZE_SECRET_KEY=hidden\n", encoding="utf-8")
        self.assertEqual(
            controlled.configured_database_path(env),
            controlled.DB_PATH.resolve(),
        )

    def test_postapply_rejects_unexpected_extra_schema_object(self):
        baseline = controlled.capture_quiescent_baseline(self.db_path)
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        db_migrations.apply_migrations(self.db_path, migrations_dir)

        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE INDEX unexpected_idx ON report_data(id)")
        conn.commit()
        conn.close()

        with self.assertRaises(controlled.ControlledApplyError):
            controlled.verify_postapply(self.db_path, baseline)

    def test_content_hash_detects_same_count_value_change(self):
        baseline = controlled.capture_quiescent_baseline(self.db_path)
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        db_migrations.apply_migrations(self.db_path, migrations_dir)

        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE audit_log SET action='changed' WHERE id=1")
        conn.commit()
        conn.close()

        with self.assertRaises(controlled.ControlledApplyError):
            controlled.verify_postapply(self.db_path, baseline)

    def test_quiescence_guard_detects_open_db_and_sidecar_handles(self):
        proc_root = self.root / "proc"
        fd_dir = proc_root / "424242" / "fd"
        fd_dir.mkdir(parents=True)
        (fd_dir / "3").symlink_to(self.db_path)

        with self.assertRaises(controlled.ControlledApplyError):
            controlled.assert_database_quiescent(self.db_path, proc_root=proc_root)

        (fd_dir / "3").unlink()
        wal = Path(str(self.db_path) + "-wal")
        wal.write_bytes(b"test")
        (fd_dir / "4").symlink_to(wal)
        with self.assertRaises(controlled.ControlledApplyError):
            controlled.assert_database_quiescent(self.db_path, proc_root=proc_root)

        (fd_dir / "4").unlink()
        controlled.assert_database_quiescent(self.db_path, proc_root=proc_root)

    def test_restore_refuses_backup_sha_mismatch(self):
        baseline = controlled.capture_quiescent_baseline(self.db_path)
        backup = self.root / "backup" / "auth-before.db"
        meta = controlled.backup_database(self.db_path, backup, baseline)
        original_stat = self.db_path.stat()

        conn = sqlite3.connect(backup)
        conn.execute("UPDATE audit_log SET action='tampered' WHERE id=1")
        conn.commit()
        conn.close()

        with self.assertRaises(controlled.ControlledApplyError):
            controlled.restore_backup(
                self.db_path,
                backup,
                baseline,
                original_stat,
                meta["sha256"],
            )

    def _controlled_apply_patches(self, service_state, backup_root, *, on_start=None):
        root = Path(__file__).resolve().parent

        def local_sources(_target_dir):
            return root / "db_migrations.py", root / "migrations"

        def service_is_active():
            return service_state["active"]

        def stop_service():
            service_state["stops"] += 1
            service_state["active"] = False

        def start_service():
            service_state["starts"] += 1
            service_state["active"] = True
            if on_start:
                on_start()

        return [
            patch.object(controlled, "download_foundation_sources", side_effect=local_sources),
            patch.object(controlled, "configured_database_path", return_value=self.db_path.resolve()),
            patch.object(controlled.socket, "gethostname", return_value=controlled.EXPECTED_HOSTNAME),
            patch.object(controlled.os, "geteuid", return_value=0),
            patch.object(controlled, "BACKUP_ROOT", backup_root),
            patch.object(controlled, "service_is_active", side_effect=service_is_active),
            patch.object(controlled, "local_health_ok", side_effect=service_is_active),
            patch.object(controlled, "stop_service", side_effect=stop_service),
            patch.object(controlled, "start_service", side_effect=start_service),
            patch.object(controlled, "assert_database_quiescent", return_value=None),
        ]

    def test_controlled_apply_failure_before_restart_rolls_back_exact_baseline(self):
        baseline = controlled.capture_quiescent_baseline(self.db_path)
        original_mode = self.db_path.stat().st_mode & 0o777
        os.chmod(self.db_path, 0o640)
        original_mode = self.db_path.stat().st_mode & 0o777
        original_stat = self.db_path.stat()
        backup_root = self.root / "backups"
        backup_root.mkdir()
        service = {"active": True, "starts": 0, "stops": 0}

        original_verify = controlled.verify_postapply

        def fail_after_verified_migration(db_path, captured):
            original_verify(db_path, captured)
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(db_path) + suffix).write_bytes(b"sidecar")
            raise controlled.ControlledApplyError("deliberate post-migration failure")

        chown_mock = Mock()
        patches = self._controlled_apply_patches(service, backup_root)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patch.object(controlled, "verify_postapply", side_effect=fail_after_verified_migration), \
             patch.object(controlled.os, "chown", chown_mock):
            with self.assertRaises(controlled.ControlledApplyError):
                controlled.controlled_apply(self.db_path)

        controlled.verify_exact_baseline(self.db_path, baseline)
        self.assertEqual(self.db_path.stat().st_mode & 0o777, original_mode)
        chown_mock.assert_called()
        _, uid, gid = chown_mock.call_args.args
        self.assertEqual((uid, gid), (original_stat.st_uid, original_stat.st_gid))
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(str(self.db_path) + suffix).exists())
        self.assertTrue(service["active"])
        self.assertGreaterEqual(service["starts"], 1)
        self.assertGreaterEqual(service["stops"], 1)

    def test_no_automatic_restore_after_service_restart_and_traffic_write(self):
        backup_root = self.root / "backups"
        backup_root.mkdir()
        service = {"active": True, "starts": 0, "stops": 0}

        def write_after_restart():
            conn = sqlite3.connect(self.db_path)
            conn.execute("INSERT INTO report_data(id) VALUES(2)")
            conn.commit()
            conn.close()

        patches = self._controlled_apply_patches(
            service,
            backup_root,
            on_start=write_after_restart,
        )
        restore_mock = Mock(side_effect=AssertionError("restore must not run after traffic resumes"))

        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patch.object(controlled, "restore_backup", restore_mock), \
             patch.object(
                 controlled,
                 "verify_runtime_postapply",
                 side_effect=controlled.ControlledApplyError("deliberate runtime diagnostic failure"),
             ):
            with self.assertRaises(controlled.ControlledApplyError):
                controlled.controlled_apply(self.db_path)

        restore_mock.assert_not_called()
        self.assertTrue(service["active"])
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM report_data").fetchone()[0], 2)
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='holdings'"
                ).fetchone()
            )
        finally:
            conn.close()

    def test_handoff_pins_current_audited_controller_blob(self):
        root = Path(__file__).resolve().parent
        controller_data = (
            root / "tools" / "controlled_structure_foundation_apply.py"
        ).read_bytes()
        self.assertEqual(
            handoff.git_blob_sha(controller_data),
            handoff.AUDITED_CONTROLLER_BLOB,
        )
        self.assertEqual(
            handoff.verify_controller_bytes(controller_data),
            handoff.AUDITED_CONTROLLER_BLOB,
        )
        self.assertIn(handoff.AUDITED_CONTROLLER_COMMIT, handoff.controller_url())

    def test_handoff_rejects_modified_controller(self):
        with self.assertRaises(handoff.HandoffError):
            handoff.verify_controller_bytes(b"print('modified controller')\n")

    def test_audited_foundation_blob_constants_match_repository_files(self):
        root = Path(__file__).resolve().parent
        runner_data = (root / "db_migrations.py").read_bytes()
        migration_data = (
            root / "migrations" / "v20260920_001_structure_foundation.py"
        ).read_bytes()

        self.assertEqual(controlled.git_blob_sha(runner_data), controlled.RUNNER_BLOB)
        self.assertEqual(
            controlled.git_blob_sha(migration_data),
            controlled.MIGRATION_BLOB,
        )


if __name__ == "__main__":
    unittest.main()
