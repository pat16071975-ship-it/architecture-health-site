import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import db_migrations
import structure_seed


MODULE_PATH = Path(__file__).resolve().parent / "tools" / "controlled_structure_seed.py"
SPEC = importlib.util.spec_from_file_location("controlled_structure_seed", MODULE_PATH)
controlled = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controlled)

HANDOFF_PATH = Path(__file__).resolve().parent / "tools" / "controlled_structure_seed_handoff.py"
HANDOFF_SPEC = importlib.util.spec_from_file_location(
    "controlled_structure_seed_handoff",
    HANDOFF_PATH,
)
handoff = importlib.util.module_from_spec(HANDOFF_SPEC)
HANDOFF_SPEC.loader.exec_module(handoff)


LEGACY_SQL = """
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


def create_foundation_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SQL)
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

    migrations_dir = Path(__file__).resolve().parent / "migrations"
    result = db_migrations.apply_migrations(path, migrations_dir)
    assert result == {"applied": [controlled.FOUNDATION_VERSION], "already_applied": []}


class ControlledStructureSeedTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "auth.db"
        create_foundation_db(self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def _migration_path(self):
        return (
            Path(__file__).resolve().parent
            / "migrations"
            / "v20260920_001_structure_foundation.py"
        )

    def _exact_schema(self):
        return controlled.expected_foundation_schema(self._migration_path())

    def _baseline(self):
        return controlled.capture_baseline(
            self.db_path,
            expected_schema=self._exact_schema(),
            migration_path=self._migration_path(),
        )

    def test_seed_spec_requires_valid_timezone_and_binds_confirmation(self):
        with self.assertRaises(controlled.ControlledSeedError):
            controlled.seed_spec("")
        with self.assertRaises(controlled.ControlledSeedError):
            controlled.seed_spec("Not/A_Timezone")

        moscow = controlled.seed_spec("Europe/Moscow")
        amsterdam = controlled.seed_spec("Europe/Amsterdam")
        self.assertEqual(moscow["holding"]["name"], "Архитектура здоровья")
        self.assertEqual(moscow["organization"]["name"], "Архитектура здоровья")
        self.assertEqual(moscow["cluster"]["name"], "Кластер по умолчанию")
        self.assertEqual(moscow["clinic"]["name"], "Архитектура здоровья")
        self.assertEqual(moscow["directions"], [])
        self.assertNotEqual(
            controlled.required_confirmation(moscow),
            controlled.required_confirmation(amsterdam),
        )

    def test_capture_baseline_requires_applied_empty_foundation(self):
        baseline = self._baseline()
        self.assertEqual(set(baseline["tables"]), controlled.ALL_TABLES)
        self.assertEqual(
            {table: baseline["row_counts"][table] for table in controlled.FOUNDATION_TABLES},
            {table: 0 for table in controlled.FOUNDATION_TABLES},
        )

        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO holdings(name,short_name,status) VALUES('X','','active')")
        conn.commit()
        conn.close()

        with self.assertRaises(controlled.ControlledSeedError):
            self._baseline()

    def test_exact_foundation_schema_rejects_weakened_compatible_ddl(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DROP TABLE directions")
            conn.execute(
                """
                CREATE TABLE directions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    organization_id INTEGER NOT NULL,
                    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
                    short_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    display_order INTEGER,
                    UNIQUE (id, organization_id),
                    FOREIGN KEY (organization_id)
                        REFERENCES organizations(id) ON DELETE RESTRICT
                )
                """
            )
            conn.execute(
                "CREATE INDEX idx_directions_organization "
                "ON directions(organization_id)"
            )
            conn.commit()
        finally:
            conn.close()

        with self.assertRaises(controlled.ControlledSeedError):
            self._baseline()

    def test_exact_migration_history_rejects_checksum_change(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE schema_migrations SET checksum='tampered' "
                "WHERE version=?",
                (controlled.FOUNDATION_VERSION,),
            )
            conn.commit()
        finally:
            conn.close()

        with self.assertRaises(controlled.ControlledSeedError):
            self._baseline()

    def test_pristine_foundation_rejects_prior_directions_sequence_use(self):
        conn = sqlite3.connect(self.db_path)
        try:
            # Foreign keys are OFF by default on this direct test connection.
            # This isolates directions AUTOINCREMENT history without touching
            # holdings/organizations sequences.
            conn.execute(
                """
                INSERT INTO directions(
                    organization_id,name,short_name,status,display_order
                ) VALUES(999,'Temporary','','active',NULL)
                """
            )
            conn.execute("DELETE FROM directions")
            conn.commit()
            self.assertEqual(
                conn.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name='directions'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM directions").fetchone()[0],
                0,
            )
        finally:
            conn.close()

        with self.assertRaises(controlled.ControlledSeedError):
            self._baseline()

    def test_seed_transaction_creates_only_current_az_structure(self):
        baseline = self._baseline()
        spec = controlled.seed_spec("Europe/Moscow")

        result = controlled.apply_seed_transaction(
            self.db_path,
            spec,
            baseline,
            structure_seed,
        )

        conn = controlled._connect_ro(self.db_path)
        try:
            verified = controlled.verify_seeded_state(
                conn,
                spec,
                baseline=baseline,
                strict_unchanged=True,
            )
        finally:
            conn.close()

        self.assertEqual(result, verified)
        self.assertEqual(verified["timezone"], "Europe/Moscow")
        self.assertGreater(verified["holding_id"], 0)
        self.assertGreater(verified["clinic_id"], 0)

    def test_seed_verification_detects_change_outside_target_tables(self):
        baseline = self._baseline()
        spec = controlled.seed_spec("Europe/Moscow")
        controlled.apply_seed_transaction(self.db_path, spec, baseline, structure_seed)

        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE audit_log SET action='changed' WHERE id=1")
        conn.commit()
        conn.close()

        conn = controlled._connect_ro(self.db_path)
        try:
            with self.assertRaises(controlled.ControlledSeedError):
                controlled.verify_seeded_state(
                    conn,
                    spec,
                    baseline=baseline,
                    strict_unchanged=True,
                )
        finally:
            conn.close()

    def test_backup_preserves_exact_baseline_sequences_and_modes(self):
        baseline = self._baseline()
        backup = self.root / "backup-dir" / "auth-before-seed.db"
        meta = controlled.backup_database(self.db_path, backup, baseline)

        self.assertEqual(meta["sha256"], controlled.sha256_file(backup))
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup.parent.stat().st_mode & 0o777, 0o700)
        controlled.verify_exact_baseline(backup, baseline)

    def test_quiescence_guard_detects_db_and_sidecar_handles(self):
        proc_root = self.root / "proc"
        fd_dir = proc_root / "4242" / "fd"
        fd_dir.mkdir(parents=True)
        (fd_dir / "3").symlink_to(self.db_path)

        with self.assertRaises(controlled.ControlledSeedError):
            controlled.assert_database_quiescent(self.db_path, proc_root=proc_root)

        (fd_dir / "3").unlink()
        wal = Path(str(self.db_path) + "-wal")
        wal.write_bytes(b"x")
        (fd_dir / "4").symlink_to(wal)
        with self.assertRaises(controlled.ControlledSeedError):
            controlled.assert_database_quiescent(self.db_path, proc_root=proc_root)

        (fd_dir / "4").unlink()
        controlled.assert_database_quiescent(self.db_path, proc_root=proc_root)

    def test_restore_refuses_tampered_backup_digest(self):
        baseline = self._baseline()
        backup = self.root / "backup-dir" / "auth-before-seed.db"
        meta = controlled.backup_database(self.db_path, backup, baseline)
        original_stat = self.db_path.stat()

        conn = sqlite3.connect(backup)
        conn.execute("UPDATE audit_log SET action='tampered' WHERE id=1")
        conn.commit()
        conn.close()

        with self.assertRaises(controlled.ControlledSeedError):
            controlled.restore_backup(
                self.db_path,
                backup,
                baseline,
                original_stat,
                meta["sha256"],
            )

    def test_handoff_pins_current_seed_controller_blob(self):
        root = Path(__file__).resolve().parent
        controller_data = (
            root / "tools" / "controlled_structure_seed.py"
        ).read_bytes()
        self.assertEqual(
            handoff.git_blob_sha(controller_data),
            handoff.AUDITED_CONTROLLER_BLOB,
        )
        self.assertEqual(
            handoff.verify_controller_bytes(controller_data),
            handoff.AUDITED_CONTROLLER_BLOB,
        )
        self.assertIn(
            handoff.AUDITED_CONTROLLER_COMMIT,
            handoff.controller_url(),
        )

    def test_handoff_rejects_modified_seed_controller(self):
        with self.assertRaises(handoff.HandoffError):
            handoff.verify_controller_bytes(b"print('modified seed controller')\n")

    def test_pinned_source_blobs_match_repository_files(self):
        root = Path(__file__).resolve().parent
        self.assertEqual(
            controlled.git_blob_sha((root / "db_migrations.py").read_bytes()),
            controlled.RUNNER_BLOB,
        )
        self.assertEqual(
            controlled.git_blob_sha(
                (root / "migrations" / "v20260920_001_structure_foundation.py").read_bytes()
            ),
            controlled.MIGRATION_BLOB,
        )
        self.assertEqual(
            controlled.git_blob_sha((root / "structure_seed.py").read_bytes()),
            controlled.SEED_HELPER_BLOB,
        )

    def _controlled_seed_patches(self, service_state, backup_root, *, on_start=None):
        root = Path(__file__).resolve().parent

        def local_sources(_target_dir):
            return (
                root / "db_migrations.py",
                root / "migrations",
                root / "structure_seed.py",
            )

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
            patch.object(controlled, "download_sources", side_effect=local_sources),
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

    def test_controlled_seed_failure_before_restart_rolls_back_exact_baseline(self):
        baseline = self._baseline()
        os.chmod(self.db_path, 0o640)
        original_stat = self.db_path.stat()
        original_mode = original_stat.st_mode & 0o777

        backup_root = self.root / "backups"
        backup_root.mkdir()
        service = {"active": True, "starts": 0, "stops": 0}

        original_verify = controlled.verify_seeded_state
        post_commit_seen = {"value": False}

        def fail_after_committed_seed(conn, spec, baseline=None, strict_unchanged=False):
            result = original_verify(
                conn,
                spec,
                baseline=baseline,
                strict_unchanged=strict_unchanged,
            )
            # Inside apply_seed_transaction() the connection is still in the
            # explicit BEGIN IMMEDIATE transaction. The second verification
            # uses a fresh read-only connection after commit.
            if (
                baseline is not None
                and strict_unchanged
                and not conn.in_transaction
            ):
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM clinics").fetchone()[0],
                    1,
                )
                post_commit_seen["value"] = True
                for suffix in ("-wal", "-shm", "-journal"):
                    Path(str(self.db_path) + suffix).write_bytes(b"sidecar")
                raise controlled.ControlledSeedError(
                    "deliberate post-commit/pre-restart failure"
                )
            return result

        chown_mock = Mock()
        patches = self._controlled_seed_patches(service, backup_root)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patch.object(
                 controlled,
                 "verify_seeded_state",
                 side_effect=fail_after_committed_seed,
             ), \
             patch.object(controlled.os, "chown", chown_mock):
            with self.assertRaises(controlled.ControlledSeedError):
                controlled.controlled_seed("Europe/Moscow", self.db_path)

        self.assertTrue(post_commit_seen["value"])

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

    def test_no_automatic_restore_after_restart_and_simulated_traffic(self):
        backup_root = self.root / "backups"
        backup_root.mkdir()
        service = {"active": True, "starts": 0, "stops": 0}

        def write_after_restart():
            conn = sqlite3.connect(self.db_path)
            conn.execute("INSERT INTO report_data(id) VALUES(2)")
            conn.commit()
            conn.close()

        patches = self._controlled_seed_patches(
            service,
            backup_root,
            on_start=write_after_restart,
        )
        restore_mock = Mock(side_effect=AssertionError("restore must not run after restart"))
        original_verify = controlled.verify_seeded_state

        def runtime_fail(conn, spec, baseline=None, strict_unchanged=False):
            result = original_verify(
                conn,
                spec,
                baseline=baseline,
                strict_unchanged=strict_unchanged,
            )
            if baseline is None:
                raise controlled.ControlledSeedError("deliberate runtime failure")
            return result

        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patch.object(controlled, "restore_backup", restore_mock), \
             patch.object(controlled, "verify_seeded_state", side_effect=runtime_fail):
            with self.assertRaises(controlled.ControlledSeedError):
                controlled.controlled_seed("Europe/Moscow", self.db_path)

        restore_mock.assert_not_called()
        self.assertTrue(service["active"])
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM clinics").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM report_data").fetchone()[0], 2)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
