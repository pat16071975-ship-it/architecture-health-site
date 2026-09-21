import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

TEST_TMP = tempfile.TemporaryDirectory()
os.environ["AZBAZE_DB"] = str(Path(TEST_TMP.name) / "app.db")
os.environ["AZBAZE_SECRET_KEY"] = "test-secret-key-for-clinic-timezone"
os.environ["AZBAZE_SITE_ROOT"] = str(Path(TEST_TMP.name) / "site")

import app as app_core
import clinic_structure_settings
import clinic_timezone
import db_migrations
import structure_seed


clinic_structure_settings.register(app_core.app)


class ClinicTimezoneDomainTests(unittest.TestCase):
    def test_timezone_validation_is_explicit_iana(self):
        self.assertEqual(
            clinic_timezone.normalize_timezone_name(" Europe/Moscow "),
            "Europe/Moscow",
        )
        with self.assertRaises(clinic_timezone.ClinicTimezoneError):
            clinic_timezone.normalize_timezone_name("")
        with self.assertRaises(clinic_timezone.ClinicTimezoneError):
            clinic_timezone.normalize_timezone_name("Not/A_Zone")
        with self.assertRaises(clinic_timezone.ClinicTimezoneError):
            clinic_timezone.normalize_timezone_name("localtime")

    def test_timezone_options_are_server_side_iana_choices(self):
        options = clinic_timezone.timezone_options(
            datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        )
        names = {item["name"] for item in options}
        self.assertIn("UTC", names)
        self.assertIn("Europe/Moscow", names)
        self.assertNotIn("localtime", names)
        self.assertTrue(all("UTC" in item["label"] for item in options))

    def test_clinic_local_now_uses_clinic_timezone_not_user_location(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE clinics(
                id INTEGER PRIMARY KEY,
                organization_id INTEGER NOT NULL,
                cluster_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                timezone TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO clinics(id,organization_id,cluster_id,name,timezone,status)
            VALUES(1,1,1,'Clinic','Asia/Tokyo','active')
            """
        )
        fixed = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        local = clinic_timezone.clinic_local_now(conn, 1, fixed)
        self.assertEqual(local.hour, 21)
        self.assertEqual(local.tzinfo.key, "Asia/Tokyo")
        conn.close()


class ClinicTimezoneSettingsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_core.init_db_file()
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        result = db_migrations.apply_migrations(app_core.DB_PATH, migrations_dir)
        cls.assertEqual(
            result,
            {"applied": ["20260920_001"], "already_applied": []},
        )

    def setUp(self):
        site_root = Path(os.environ["AZBAZE_SITE_ROOT"])
        site_root.mkdir(parents=True, exist_ok=True)
        (site_root / "index.html").write_text(
            """<!doctype html><html><body>
<a class="menu-btn active" href="../reports/">Отчёты</a>
<a class="menu-btn active" href="#knowledge" id="knowledge">База знаний</a>
</body></html>""",
            encoding="utf-8",
        )

        with app_core.app.app_context():
            conn = app_core.db()
            conn.execute("DELETE FROM clinic_directions")
            conn.execute("DELETE FROM directions")
            conn.execute("DELETE FROM clinics")
            conn.execute("DELETE FROM clusters")
            conn.execute("DELETE FROM organizations")
            conn.execute("DELETE FROM holdings")
            conn.execute("DELETE FROM permissions")
            conn.execute("DELETE FROM audit_log")
            conn.execute("DELETE FROM users")
            conn.execute(
                """
                INSERT INTO users(
                    id,email,full_name,password_hash,is_admin,active,must_change_password,
                    failed_attempts,locked_until,created_at,updated_at
                ) VALUES(1,'admin@example.test','Admin','x',1,1,0,0,NULL,'now','now')
                """
            )
            conn.execute(
                """
                INSERT INTO users(
                    id,email,full_name,password_hash,is_admin,active,must_change_password,
                    failed_attempts,locked_until,created_at,updated_at
                ) VALUES(2,'manager@example.test','Manager','x',0,1,0,0,NULL,'now','now')
                """
            )
            structure_seed.seed_initial_structure(
                conn,
                holding_name="Архитектура здоровья",
                organization_name="Архитектура здоровья",
                cluster_name="Кластер по умолчанию",
                clinic_name="Архитектура здоровья",
                timezone_name="Europe/Moscow",
            )
            conn.commit()
            self.clinic_id = conn.execute("SELECT id FROM clinics").fetchone()["id"]

    def _client(self, user_id, *, csrf="timezone-test-csrf"):
        client = app_core.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf"] = csrf
        return client

    def _grant_structure(self, user_id):
        with app_core.app.app_context():
            conn = app_core.db()
            conn.execute(
                "INSERT INTO permissions(user_id,section) VALUES(?,?)",
                (user_id, "structure_manage"),
            )
            conn.commit()

    def test_structure_manage_is_explicit_even_for_admin(self):
        with app_core.app.app_context():
            admin = app_core.db().execute("SELECT * FROM users WHERE id=1").fetchone()
            permissions = app_core.user_permissions(admin)
            self.assertNotIn("structure_manage", permissions)

            app_core.db().execute(
                "INSERT INTO permissions(user_id,section) VALUES(1,'structure_manage')"
            )
            app_core.db().commit()
            permissions = app_core.user_permissions(admin)
            self.assertIn("structure_manage", permissions)

    def test_admin_without_explicit_structure_permission_is_forbidden(self):
        response = self._client(1).get("/structure/clinics/")
        self.assertEqual(response.status_code, 403)

    def test_non_admin_with_explicit_permission_can_open_settings(self):
        self._grant_structure(2)
        response = self._client(2).get("/structure/clinics/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Архитектура здоровья", html)
        self.assertIn("Europe/Moscow", html)
        self.assertIn("Настройка клиник", html)

    def test_home_link_is_visible_only_with_explicit_permission(self):
        without = self._client(2).get("/").get_data(as_text=True)
        self.assertNotIn("/structure/clinics/", without)

        self._grant_structure(2)
        with_permission = self._client(2).get("/").get_data(as_text=True)
        self.assertIn("/structure/clinics/", with_permission)
        self.assertIn("Настройка клиник", with_permission)

    def test_timezone_update_changes_only_timezone_and_audits(self):
        self._grant_structure(2)
        with app_core.app.app_context():
            before = dict(
                app_core.db().execute(
                    "SELECT * FROM clinics WHERE id=?",
                    (self.clinic_id,),
                ).fetchone()
            )

        response = self._client(2).post(
            f"/structure/clinics/{self.clinic_id}/timezone",
            data={
                "csrf": "timezone-test-csrf",
                "timezone": "Asia/Tokyo",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/structure/clinics/"))

        with app_core.app.app_context():
            after = dict(
                app_core.db().execute(
                    "SELECT * FROM clinics WHERE id=?",
                    (self.clinic_id,),
                ).fetchone()
            )
            self.assertEqual(after["timezone"], "Asia/Tokyo")
            for key in before:
                if key != "timezone":
                    self.assertEqual(after[key], before[key], key)

            audit = app_core.db().execute(
                """
                SELECT action,details
                FROM audit_log
                WHERE action='clinic_timezone_updated'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            self.assertIsNotNone(audit)
            self.assertIn(f"clinic_id={self.clinic_id}", audit["details"])
            self.assertIn("old_timezone=Europe/Moscow", audit["details"])
            self.assertIn("new_timezone=Asia/Tokyo", audit["details"])

    def test_invalid_timezone_is_rejected_without_write(self):
        self._grant_structure(2)
        response = self._client(2).post(
            f"/structure/clinics/{self.clinic_id}/timezone",
            data={
                "csrf": "timezone-test-csrf",
                "timezone": "Browser/Automatic",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Неизвестный часовой пояс IANA", response.get_data(as_text=True))

        with app_core.app.app_context():
            value = app_core.db().execute(
                "SELECT timezone FROM clinics WHERE id=?",
                (self.clinic_id,),
            ).fetchone()["timezone"]
            self.assertEqual(value, "Europe/Moscow")
            count = app_core.db().execute(
                "SELECT COUNT(*) AS n FROM audit_log WHERE action='clinic_timezone_updated'"
            ).fetchone()["n"]
            self.assertEqual(count, 0)

    def test_csrf_is_required_for_timezone_change(self):
        self._grant_structure(2)
        response = self._client(2).post(
            f"/structure/clinics/{self.clinic_id}/timezone",
            data={"timezone": "Asia/Tokyo"},
        )
        self.assertEqual(response.status_code, 400)

        with app_core.app.app_context():
            value = app_core.db().execute(
                "SELECT timezone FROM clinics WHERE id=?",
                (self.clinic_id,),
            ).fetchone()["timezone"]
            self.assertEqual(value, "Europe/Moscow")


if __name__ == "__main__":
    unittest.main()
