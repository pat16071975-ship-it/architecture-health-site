import sqlite3
import tempfile
import unittest
from pathlib import Path

import db_migrations
import structure_seed


LEGACY_SUBSET = """
PRAGMA foreign_keys = ON;
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL DEFAULT '',
    is_admin INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL DEFAULT 'now',
    updated_at TEXT NOT NULL DEFAULT 'now'
);
CREATE TABLE permissions (
    user_id INTEGER NOT NULL,
    section TEXT NOT NULL,
    PRIMARY KEY (user_id, section),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER,
    action TEXT NOT NULL,
    target_user_id INTEGER,
    details TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE report_data (
    date TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE report_blobs (
    key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE daily_uploads (
    data_date TEXT PRIMARY KEY,
    completed_filename TEXT NOT NULL,
    services_filename TEXT NOT NULL,
    completed_sha256 TEXT NOT NULL,
    services_sha256 TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    uploaded_by INTEGER,
    uploaded_at TEXT NOT NULL,
    FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL
);
"""


class StructureFoundationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "app.db"
        conn = sqlite3.connect(self.db_path)
        conn.executescript(LEGACY_SUBSET)
        conn.execute(
            """
            INSERT INTO users(
                id,email,full_name,password_hash,is_admin,active,must_change_password,
                failed_attempts,locked_until,created_at,updated_at
            ) VALUES(1,'owner@example.test','Owner','x',1,1,0,0,NULL,'now','now')
            """
        )
        conn.execute(
            "INSERT INTO report_data(date,payload,updated_by,updated_at) "
            "VALUES('2026-09-20','{}',1,'now')"
        )
        conn.execute(
            "INSERT INTO report_blobs(key,payload,updated_by,updated_at) "
            "VALUES('az-test','{}',1,'now')"
        )
        conn.execute(
            """
            INSERT INTO daily_uploads(
                data_date,completed_filename,services_filename,completed_sha256,
                services_sha256,normalized_json,revision,uploaded_by,uploaded_at
            ) VALUES('2026-09-20','a.xlsx','b.xlsx','a','b','{}',1,1,'now')
            """
        )
        conn.commit()
        self.legacy_sql = {
            name: conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()[0]
            for name in ("report_data", "report_blobs", "daily_uploads")
        }
        conn.close()

    def tearDown(self):
        self.tempdir.cleanup()

    def _apply(self):
        return db_migrations.apply_migrations(self.db_path)

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _migration_dir(self, name="custom-migrations"):
        path = self.root / name
        path.mkdir()
        return path

    def _write_migration(self, root, filename, body):
        path = root / filename
        path.write_text(body, encoding="utf-8")
        return path

    def _seed_two_organizations(self, conn):
        holding = conn.execute(
            "INSERT INTO holdings(name,short_name,status) VALUES('H','','active')"
        ).lastrowid
        org1 = conn.execute(
            "INSERT INTO organizations(holding_id,name,short_name,status) "
            "VALUES(?,'Org 1','','active')",
            (holding,),
        ).lastrowid
        org2 = conn.execute(
            "INSERT INTO organizations(holding_id,name,short_name,status) "
            "VALUES(?,'Org 2','','active')",
            (holding,),
        ).lastrowid
        cluster1 = conn.execute(
            "INSERT INTO clusters(organization_id,name,description,status) "
            "VALUES(?,'C1','','active')",
            (org1,),
        ).lastrowid
        cluster2 = conn.execute(
            "INSERT INTO clusters(organization_id,name,description,status) "
            "VALUES(?,'C2','','active')",
            (org2,),
        ).lastrowid
        return org1, org2, cluster1, cluster2

    def test_migration_adds_foundation_without_touching_legacy_tables_or_rows(self):
        result = self._apply()
        self.assertEqual(result["applied"], ["20260920_001"])
        conn = self._conn()
        try:
            for name, sql in self.legacy_sql.items():
                current = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (name,),
                ).fetchone()[0]
                self.assertEqual(current, sql)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM report_data").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM report_blobs").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM daily_uploads").fetchone()[0], 1)
            for table in (
                "schema_migrations",
                "holdings",
                "organizations",
                "clusters",
                "clinics",
                "directions",
                "clinic_directions",
            ):
                self.assertIsNotNone(
                    conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone()
                )
        finally:
            conn.close()

    def test_migration_is_idempotent_and_history_is_single_row(self):
        first = self._apply()
        second = self._apply()
        self.assertEqual(first["applied"], ["20260920_001"])
        self.assertEqual(second["applied"], [])
        self.assertEqual(second["already_applied"], ["20260920_001"])
        conn = self._conn()
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
                1,
            )
        finally:
            conn.close()

    def test_apply_fails_closed_when_database_path_does_not_exist(self):
        missing = self.root / "missing.db"
        self.assertFalse(missing.exists())
        with self.assertRaises(db_migrations.MigrationError):
            db_migrations.apply_migrations(missing)
        self.assertFalse(missing.exists())

    def test_malformed_v_migration_filename_is_hard_failure(self):
        root = self._migration_dir()
        self._write_migration(root, "vbroken.py", "VERSION = 'x'\n")
        with self.assertRaises(db_migrations.MigrationError):
            db_migrations.discover_migrations(root)

    def test_applied_history_version_missing_from_repository_is_hard_failure(self):
        self._apply()
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO schema_migrations(version,name,checksum,applied_at)
                VALUES('19990101_999','missing','deadbeef','now')
                """
            )
            conn.commit()
        finally:
            conn.close()

        with self.assertRaises(db_migrations.MigrationError):
            db_migrations.apply_migrations(self.db_path)
        with self.assertRaises(db_migrations.MigrationError):
            db_migrations.migration_status(self.db_path)

    def test_checksum_tamper_is_detected(self):
        root = self._migration_dir()
        path = self._write_migration(
            root,
            "v20260920_001_test.py",
            """
VERSION = "20260920_001"
NAME = "test"

def upgrade(conn):
    conn.execute("CREATE TABLE checksum_probe(id INTEGER PRIMARY KEY)")
""".lstrip(),
        )
        result = db_migrations.apply_migrations(self.db_path, root)
        self.assertEqual(result["applied"], ["20260920_001"])

        path.write_text(
            path.read_text(encoding="utf-8") + "\n# tampered after apply\n",
            encoding="utf-8",
        )
        with self.assertRaises(db_migrations.MigrationError):
            db_migrations.apply_migrations(self.db_path, root)
        with self.assertRaises(db_migrations.MigrationError):
            db_migrations.migration_status(self.db_path, root)

    def test_failed_first_migration_rolls_back_schema_and_history_table(self):
        root = self._migration_dir()
        self._write_migration(
            root,
            "v20260920_001_broken.py",
            """
VERSION = "20260920_001"
NAME = "broken"

def upgrade(conn):
    conn.execute("CREATE TABLE should_rollback(id INTEGER PRIMARY KEY)")
    raise RuntimeError("boom")
""".lstrip(),
        )

        with self.assertRaises(RuntimeError):
            db_migrations.apply_migrations(self.db_path, root)

        conn = self._conn()
        try:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='should_rollback'"
                ).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='schema_migrations'"
                ).fetchone()
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM report_data").fetchone()[0], 1)
        finally:
            conn.close()

    def test_existing_wrong_sqlite_is_rejected_by_apply_and_status(self):
        wrong = self.root / "wrong.db"
        conn = sqlite3.connect(wrong)
        conn.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO unrelated(note) VALUES('keep')")
        conn.commit()
        conn.close()

        with self.assertRaises(db_migrations.MigrationError):
            db_migrations.apply_migrations(wrong)
        with self.assertRaises(db_migrations.MigrationError):
            db_migrations.migration_status(wrong)

        conn = sqlite3.connect(wrong)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM unrelated").fetchone()[0], 1)
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='schema_migrations'"
                ).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='holdings'"
                ).fetchone()
            )
        finally:
            conn.close()

    def test_history_must_be_contiguous_catalogue_prefix(self):
        root = self._migration_dir()
        self._write_migration(
            root,
            "v20260920_001_first.py",
            """
VERSION = "20260920_001"
NAME = "first"

def upgrade(conn):
    conn.execute("CREATE TABLE first_step(id INTEGER PRIMARY KEY)")
""".lstrip(),
        )
        self._write_migration(
            root,
            "v20260920_002_second.py",
            """
VERSION = "20260920_002"
NAME = "second"

def upgrade(conn):
    conn.execute("CREATE TABLE second_step(id INTEGER PRIMARY KEY)")
""".lstrip(),
        )
        migrations = db_migrations.discover_migrations(root)
        second = migrations[1]

        conn = self._conn()
        try:
            conn.execute(
                """
                CREATE TABLE schema_migrations (
                    version TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO schema_migrations(version,name,checksum,applied_at)
                VALUES(?,?,?,'now')
                """,
                (second.version, second.name, second.checksum),
            )
            conn.commit()
        finally:
            conn.close()

        with self.assertRaises(db_migrations.MigrationError):
            db_migrations.apply_migrations(self.db_path, root)
        with self.assertRaises(db_migrations.MigrationError):
            db_migrations.migration_status(self.db_path, root)

        conn = self._conn()
        try:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='first_step'"
                ).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='second_step'"
                ).fetchone()
            )
        finally:
            conn.close()

    def test_clinic_cannot_use_cluster_from_another_organization(self):
        self._apply()
        conn = self._conn()
        try:
            org1, _org2, cluster1, cluster2 = self._seed_two_organizations(conn)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO clinics(
                        organization_id,cluster_id,name,short_name,
                        region,city,address,timezone,status
                    ) VALUES(?,?,'Wrong','','','','','UTC','active')
                    """,
                    (org1, cluster2),
                )
            conn.execute(
                """
                INSERT INTO clinics(
                    organization_id,cluster_id,name,short_name,
                    region,city,address,timezone,status
                ) VALUES(?,?,'Right','','','','','UTC','active')
                """,
                (org1, cluster1),
            )
        finally:
            conn.close()

    def test_one_clinic_has_exactly_one_cluster_reference(self):
        self._apply()
        conn = self._conn()
        try:
            org1, _org2, cluster1, _cluster2 = self._seed_two_organizations(conn)
            cluster3 = conn.execute(
                "INSERT INTO clusters(organization_id,name,description,status) "
                "VALUES(?,'C3','','active')",
                (org1,),
            ).lastrowid
            clinic = conn.execute(
                """
                INSERT INTO clinics(
                    organization_id,cluster_id,name,short_name,
                    region,city,address,timezone,status
                ) VALUES(?,?,'Clinic','','','','','UTC','active')
                """,
                (org1, cluster1),
            ).lastrowid
            conn.execute("UPDATE clinics SET cluster_id=? WHERE id=?", (cluster3, clinic))
            row = conn.execute(
                "SELECT cluster_id FROM clinics WHERE id=?",
                (clinic,),
            ).fetchone()
            self.assertEqual(row["cluster_id"], cluster3)
        finally:
            conn.close()

    def test_clinic_direction_relation_rejects_cross_organization_links(self):
        self._apply()
        conn = self._conn()
        try:
            org1, org2, cluster1, _cluster2 = self._seed_two_organizations(conn)
            clinic1 = conn.execute(
                """
                INSERT INTO clinics(
                    organization_id,cluster_id,name,short_name,
                    region,city,address,timezone,status
                ) VALUES(?,?,'Clinic 1','','','','','UTC','active')
                """,
                (org1, cluster1),
            ).lastrowid
            direction2 = conn.execute(
                "INSERT INTO directions("
                "organization_id,name,short_name,status,display_order"
                ") VALUES(?,'UZI','','active',NULL)",
                (org2,),
            ).lastrowid
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO clinic_directions("
                    "clinic_id,direction_id,organization_id"
                    ") VALUES(?,?,?)",
                    (clinic1, direction2, org1),
                )
        finally:
            conn.close()

    def test_one_direction_can_belong_to_multiple_clinics_of_same_organization(self):
        self._apply()
        conn = self._conn()
        try:
            org1, _org2, cluster1, _cluster2 = self._seed_two_organizations(conn)
            clinic_ids = []
            for name in ("Clinic 1", "Clinic 2"):
                clinic_ids.append(
                    conn.execute(
                        """
                        INSERT INTO clinics(
                            organization_id,cluster_id,name,short_name,
                            region,city,address,timezone,status
                        ) VALUES(?,?,?,'','','','','UTC','active')
                        """,
                        (org1, cluster1, name),
                    ).lastrowid
                )
            direction = conn.execute(
                "INSERT INTO directions("
                "organization_id,name,short_name,status,display_order"
                ") VALUES(?,'UZI','','active',NULL)",
                (org1,),
            ).lastrowid
            for clinic_id in clinic_ids:
                conn.execute(
                    "INSERT INTO clinic_directions("
                    "clinic_id,direction_id,organization_id"
                    ") VALUES(?,?,?)",
                    (clinic_id, direction, org1),
                )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM clinic_directions WHERE direction_id=?",
                    (direction,),
                ).fetchone()[0],
                2,
            )
        finally:
            conn.close()

    def test_same_direction_name_is_allowed_in_different_organizations(self):
        self._apply()
        conn = self._conn()
        try:
            org1, org2, _cluster1, _cluster2 = self._seed_two_organizations(conn)
            for org in (org1, org2):
                conn.execute(
                    "INSERT INTO directions("
                    "organization_id,name,short_name,status,display_order"
                    ") VALUES(?,'УЗИ','','active',NULL)",
                    (org,),
                )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM directions WHERE name='УЗИ'"
                ).fetchone()[0],
                2,
            )
        finally:
            conn.close()

    def test_clinic_timezone_is_required_and_nonblank(self):
        self._apply()
        conn = self._conn()
        try:
            org1, _org2, cluster1, _cluster2 = self._seed_two_organizations(conn)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO clinics(
                        organization_id,cluster_id,name,short_name,
                        region,city,address,timezone,status
                    ) VALUES(?,?,'Clinic','','','','','','active')
                    """,
                    (org1, cluster1),
                )
        finally:
            conn.close()

    def test_initial_seed_is_explicit_validated_and_one_time(self):
        self._apply()
        conn = self._conn()
        try:
            result = structure_seed.seed_initial_structure(
                conn,
                holding_name="Архитектура здоровья",
                organization_name="Архитектура здоровья",
                cluster_name="Кластер по умолчанию",
                clinic_name="Архитектура здоровья",
                timezone_name="UTC",
            )
            conn.commit()
            self.assertGreater(result["clinic_id"], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM organizations").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM clinics").fetchone()[0], 1)
            with self.assertRaises(structure_seed.SeedError):
                structure_seed.seed_initial_structure(
                    conn,
                    holding_name="Архитектура здоровья",
                    organization_name="Архитектура здоровья",
                    cluster_name="Кластер по умолчанию",
                    clinic_name="Архитектура здоровья",
                    timezone_name="UTC",
                )
        finally:
            conn.close()

    def test_seed_rejects_unknown_timezone_before_writing(self):
        self._apply()
        conn = self._conn()
        try:
            with self.assertRaises(structure_seed.SeedError):
                structure_seed.seed_initial_structure(
                    conn,
                    holding_name="H",
                    organization_name="O",
                    cluster_name="C",
                    clinic_name="K",
                    timezone_name="Not/A_Timezone",
                )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0],
                0,
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
