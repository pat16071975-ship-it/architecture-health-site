import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("AZBAZE_SECRET_KEY", "test-secret")
os.environ["AZBAZE_OWNER_EMAIL"] = "owner@example.com"

import app as app_module


class NNFoundationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        app_module.DB_PATH = root / "auth.db"
        app_module.SITE_ROOT = root / "site"
        app_module.SITE_ROOT.mkdir(parents=True, exist_ok=True)
        (app_module.SITE_ROOT / "index.html").write_text(
            """<!doctype html><html><body><nav>
<a class="menu-btn active" href="../reports/">Отчёты</a>
<a class="menu-btn active" href="#knowledge" id="knowledge">База знаний</a>
<div class="menu-btn placeholder" data-section="section4" aria-disabled="true" hidden>Дни рождения сотрудников</div>
<div class="menu-btn placeholder" data-section="section5" aria-disabled="true" hidden>Раздел в разработке</div>
</nav></body></html>""",
            encoding="utf-8",
        )
        app_module.init_db_file(app_module.DB_PATH)
        self.web = app_module.create_app()
        self.web.config.update(TESTING=True)

        conn = sqlite3.connect(app_module.DB_PATH)
        now = app_module.iso_now()
        users = [
            (1, "owner@example.com", "Owner", 1),
            (2, "admin@example.com", "Other Admin", 1),
            (3, "nn@example.com", "NN User", 0),
            (4, "normal@example.com", "Normal User", 0),
        ]
        for uid, email, name, is_admin in users:
            conn.execute(
                """INSERT INTO users(
                    id,email,full_name,password_hash,is_admin,active,must_change_password,
                    failed_attempts,locked_until,created_at,updated_at
                ) VALUES(?,?,?,?,?,1,0,0,NULL,?,?)""",
                (uid, email, name, "x", is_admin, now, now),
            )
        conn.executemany(
            "INSERT INTO permissions(user_id,section) VALUES(?,?)",
            [
                (3, "nn_reports"),
                (3, "nn_upload"),
                (4, "knowledge"),
            ],
        )
        conn.execute(
            """INSERT INTO audit_log(actor_user_id,action,target_user_id,details,created_at)
               VALUES(?,?,?,?,?)""",
            (1, "nn_secret_change", 3, "permissions=nn_reports,nn_upload; Варикоза нет - KZ", now),
        )
        conn.execute(
            """INSERT INTO audit_log(actor_user_id,action,target_user_id,details,created_at)
               VALUES(?,?,?,?,?)""",
            (2, "ordinary_change", 4, "permissions=knowledge", now),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def client_for(self, user_id):
        client = self.web.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf"] = "test-csrf"
        return client

    def test_nn_permissions_are_explicit_even_for_admin(self):
        with self.web.test_request_context("/"):
            other_admin = app_module.db().execute("SELECT * FROM users WHERE id=2").fetchone()
            self.assertNotIn("nn_reports", app_module.user_permissions(other_admin))
            self.assertNotIn("nn_upload", app_module.user_permissions(other_admin))
            self.assertFalse(app_module.has_any_nn_access(other_admin))

    def test_owner_has_private_access_but_keeps_standard_shell(self):
        response = self.client_for(1).get("/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="nn-private"', html)
        self.assertIn("Варикоза нет - KZ", html)
        self.assertIn("Отчёты", html)
        self.assertLess(html.index("Отчёты"), html.index('id="nn-private"'))

    def test_nn_only_user_gets_separate_brand_without_az_branding(self):
        response = self.client_for(3).get("/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Варикоза нет - KZ", html)
        self.assertIn("Отчёты для НН", html)
        self.assertIn("Загрузка данных", html)
        self.assertNotIn("Архитектура здоровья", html)
        self.assertNotIn("Зубач", html)

    def test_user_without_nn_access_is_denied(self):
        response = self.client_for(4).get("/nn/")
        self.assertEqual(response.status_code, 403)

    def test_other_admin_cannot_see_nn_permission_controls(self):
        response = self.client_for(2).get("/admin/users/3/edit")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Варикоза нет - KZ — закрытые права", html)
        self.assertNotIn('name="perm_nn_reports"', html)
        self.assertNotIn('name="perm_nn_upload"', html)

    def test_owner_can_see_nn_permission_controls(self):
        response = self.client_for(1).get("/admin/users/3/edit")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Варикоза нет - KZ — закрытые права", html)
        self.assertIn('name="perm_nn_reports"', html)
        self.assertIn('name="perm_nn_upload"', html)

    def test_other_admin_admin_page_hides_nn_assignments_and_audit(self):
        response = self.client_for(2).get("/admin")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("nn_reports", html)
        self.assertNotIn("nn_upload", html)
        self.assertNotIn("nn_secret_change", html)
        self.assertNotIn("Варикоза нет - KZ — отчёты", html)
        self.assertNotIn("Варикоза нет - KZ — загрузка данных", html)
        self.assertIn("ordinary_change", html)

    def test_owner_admin_page_can_see_nn_assignments_and_audit(self):
        response = self.client_for(1).get("/admin")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("nn_secret_change", html)
        self.assertIn("Варикоза нет - KZ — отчёты", html)
        self.assertIn("Варикоза нет - KZ — загрузка данных", html)

    def test_other_admin_cannot_edit_owner(self):
        response = self.client_for(2).get("/admin/users/1/edit")
        self.assertEqual(response.status_code, 403)

    def test_other_admin_edit_preserves_hidden_nn_permissions(self):
        client = self.client_for(2)
        response = client.post(
            "/admin/users/3/edit",
            data={
                "csrf": "test-csrf",
                "full_name": "NN User Updated",
                "email": "nn@example.com",
                "active": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect(app_module.DB_PATH)
        rows = conn.execute(
            "SELECT section FROM permissions WHERE user_id=3 ORDER BY section"
        ).fetchall()
        conn.close()
        self.assertEqual([row[0] for row in rows], ["nn_reports", "nn_upload"])

    def test_nn_routes_are_capability_specific(self):
        conn = sqlite3.connect(app_module.DB_PATH)
        conn.execute("DELETE FROM permissions WHERE user_id=3 AND section='nn_upload'")
        conn.commit()
        conn.close()
        client = self.client_for(3)
        self.assertEqual(client.get("/nn/reports/").status_code, 200)
        self.assertEqual(client.get("/nn/uploads/").status_code, 403)


if __name__ == "__main__":
    unittest.main()
