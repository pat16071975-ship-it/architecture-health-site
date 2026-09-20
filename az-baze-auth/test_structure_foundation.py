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
    email TEXT NOT NULL UNIQUE COLLATE NOCASE
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
        self.db_path = Path(self.tempdir.name) / "app.db"
        conn = sqlite3.connect(self.db_path)
        conn.executescript(LEGACY_SUBSET)
        conn.execute("INSERT INTO users(id,email) VALUES(1,'owner@example.test')")
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
