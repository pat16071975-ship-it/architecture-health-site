import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("AZBAZE_SECRET_KEY", "test-secret")
os.environ["AZBAZE_OWNER_EMAIL"] = "owner@example.com"

import app as app_module
import nn_upload


class NNUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        app_module.DB_PATH = root / "auth.db"
        app_module.SITE_ROOT = root / "site"
        nn_upload.DB_PATH = app_module.DB_PATH
        nn_upload.UPLOAD_ROOT = root / "nn-uploads"
        app_module.SITE_ROOT.mkdir(parents=True, exist_ok=True)
        (app_module.SITE_ROOT / "index.html").write_text(
            "<!doctype html><html><body><nav></nav></body></html>",
            encoding="utf-8",
        )
        app_module.init_db_file(app_module.DB_PATH)

        conn = sqlite3.connect(app_module.DB_PATH)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE clinics (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                address TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL
            );
            """
        )
        now = app_module.iso_now()
        conn.executemany(
            """INSERT INTO users(
                id,email,full_name,password_hash,is_admin,active,must_change_password,
                failed_attempts,locked_until,created_at,updated_at
            ) VALUES(?,?,?,?,?,1,0,0,NULL,?,?)""",
            [
                (1, "owner@example.com", "Owner", "x", 1, now, now),
                (2, "uploader@example.com", "Uploader", "x", 0, now, now),
                (3, "reports@example.com", "Reports only", "x", 0, now, now),
            ],
        )
        conn.executemany(
            "INSERT INTO permissions(user_id,section) VALUES(?,?)",
            [
                (2, "nn_upload"),
                (3, "nn_reports"),
            ],
        )
        conn.executemany(
            "INSERT INTO clinics(id,name,address,status) VALUES(?,?,?,'active')",
            [
                (11, "Клиника Север", "Адрес 1"),
                (22, "Клиника Юг", "Адрес 2"),
            ],
        )
        conn.commit()
        conn.close()

        self.web = app_module.create_app()
        self.web.config.update(TESTING=True)

    def tearDown(self):
        self.tmp.cleanup()

    def client_for(self, user_id):
        client = self.web.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf"] = "test-csrf"
        return client

    def bundle(self):
        return {
            "csrf": "test-csrf",
            "clinic_id": "11",
            "appointments_registry": (io.BytesIO(b"appointments"), "Реестр приемов.xls"),
            "medical_records": (io.BytesIO(b"records"), "Отчет по медицинским записям.xls"),
            "services_detailed": (io.BytesIO(b"services"), "отчет по услугам.xls"),
            "patients_general": (io.BytesIO(b"patients"), "Общий отчет по пациентам.xls"),
            "deleted_appointments": (io.BytesIO(b"%PDF-1.4\nmock"), "Отчет по удаленным приемам.pdf"),
        }

    def test_page_lists_clinics_separately_without_combined_option(self):
        response = self.client_for(2).get("/nn/uploads/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Клиника Север", html)
        self.assertIn("Клиника Юг", html)
        self.assertIn("Адрес 1", html)
        self.assertIn("Адрес 2", html)
        self.assertNotIn("Все клиники", html)

    def test_upload_bundle_is_bound_to_selected_clinic_and_stored_separately(self):
        response = self.client_for(2).post(
            "/nn/uploads/",
            data=self.bundle(),
            content_type="multipart/form-data",
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Пять исходных файлов сохранены", html)

        conn = sqlite3.connect(app_module.DB_PATH)
        conn.row_factory = sqlite3.Row
        batch = conn.execute(
            "SELECT * FROM nn_upload_batches ORDER BY id"
        ).fetchone()
        files = conn.execute(
            "SELECT * FROM nn_upload_files WHERE batch_id=? ORDER BY source_key",
            (batch["id"],),
        ).fetchall()
        conn.close()

        self.assertEqual(batch["clinic_id"], 11)
        self.assertEqual(batch["status"], "uploaded")
        self.assertEqual(batch["uploaded_by"], 2)
        self.assertEqual(len(files), 5)
        self.assertTrue((nn_upload.UPLOAD_ROOT / "11" / str(batch["id"])).is_dir())
        self.assertFalse((nn_upload.UPLOAD_ROOT / "22").exists())
        for row in files:
            target = nn_upload.UPLOAD_ROOT / "11" / str(batch["id"]) / row["stored_filename"]
            self.assertTrue(target.is_file())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

        clinic_two = self.client_for(2).get("/nn/uploads/?clinic_id=22").get_data(as_text=True)
        self.assertIn("Для выбранной клиники ещё нет загрузок.", clinic_two)

    def test_duplicate_bundle_does_not_create_second_batch(self):
        client = self.client_for(2)
        first = client.post("/nn/uploads/", data=self.bundle(), content_type="multipart/form-data")
        self.assertEqual(first.status_code, 200)
        second = client.post("/nn/uploads/", data=self.bundle(), content_type="multipart/form-data")
        html = second.get_data(as_text=True)
        self.assertIn("уже загружен", html)

        conn = sqlite3.connect(app_module.DB_PATH)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM nn_upload_batches").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM nn_upload_files").fetchone()[0], 5)
        finally:
            conn.close()

    def test_reports_only_user_cannot_upload(self):
        response = self.client_for(3).get("/nn/uploads/")
        self.assertEqual(response.status_code, 403)

    def test_invalid_pdf_rejects_entire_bundle(self):
        payload = self.bundle()
        payload["deleted_appointments"] = (
            io.BytesIO(b"not a pdf"),
            "Отчет по удаленным приемам.pdf",
        )
        response = self.client_for(2).post(
            "/nn/uploads/",
            data=payload,
            content_type="multipart/form-data",
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("не распознан как PDF", html)

        conn = sqlite3.connect(app_module.DB_PATH)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM nn_upload_batches").fetchone()[0], 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
