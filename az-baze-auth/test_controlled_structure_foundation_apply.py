import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

import db_migrations


MODULE_PATH = Path(__file__).resolve().parent / "tools" / "controlled_structure_foundation_apply.py"
SPEC = importlib.util.spec_from_file_location("controlled_structure_foundation_apply", MODULE_PATH)
controlled = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controlled)


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
