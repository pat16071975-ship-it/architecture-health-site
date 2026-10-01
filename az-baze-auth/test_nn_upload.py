import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AZBAZE_SECRET_KEY", "test-secret")
os.environ["AZBAZE_OWNER_EMAIL"] = "owner@example.com"

import app as app_module
import nn_normalize
import nn_upload


class NNUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        app_module.DB_PATH = root / "auth.db"
        app_module.SITE_ROOT = root / "site"
        nn_upload.DB_PATH = app_module.DB_PATH
        nn_upload.UPLOAD_ROOT = root / "nn-uploads"
        nn_normalize.DB_PATH = app_module.DB_PATH
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
            [(2, "nn_upload"), (3, "nn_reports")],
        )
        conn.execute(
            "INSERT INTO clinics(id,name,address,status) VALUES(11,'Архитектура здоровья','AZ address','active')"
        )
        conn.commit()
        conn.close()

        nn_upload._init_schema()

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

    def fake_normalize(self, conn, batch_id, _root):
        conn.execute("UPDATE nn_upload_batches SET status='ready' WHERE id=?", (batch_id,))
        conn.commit()
        return {"period": {"start": "2026-06-01", "end": "2026-09-26"}}

    def bundle(self, clinic_id="1"):
        return {
            "csrf": "test-csrf",
            "clinic_id": clinic_id,
            "appointments_registry": (io.BytesIO(b"appointments"), "Реестр приемов.xls"),
            "medical_records": (io.BytesIO(b"records"), "Отчет по медицинским записям.xls"),
            "services_detailed": (io.BytesIO(b"%PDF-1.4\nmock"), "отчет по услугам подробно.pdf"),
            "patients_general": (io.BytesIO(b"patients"), "Общий отчет по пациентам.xls"),
            "deleted_appointments": (io.BytesIO(b"%PDF-1.4\nmock"), "Отчет по удаленным приемам.pdf"),
        }

    def test_private_clinics_are_seeded_and_global_az_clinic_is_not_exposed(self):
        response = self.client_for(2).get("/nn/uploads/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Клиника 1 (Толи Бе)", html)
        self.assertIn("Клиника 2 (Шевчеко)", html)
        self.assertNotIn("Архитектура здоровья", html)
        self.assertNotIn("Сохранить список клиник НН", html)
        self.assertNotIn("Все клиники", html)

        conn = sqlite3.connect(app_module.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT clinic_id,name,active FROM nn_clinics ORDER BY clinic_id"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            rows,
            [
                (1, "Клиника 1 (Толи Бе)", 1),
                (2, "Клиника 2 (Шевчеко)", 1),
            ],
        )

    def test_existing_default_clinic_2_is_renamed_in_place(self):
        conn = sqlite3.connect(app_module.DB_PATH)
        try:
            conn.execute(
                "UPDATE nn_clinics SET name='Клиника 2 (другая)' WHERE clinic_id=2"
            )
            conn.commit()
        finally:
            conn.close()

        nn_upload._init_schema()

        conn = sqlite3.connect(app_module.DB_PATH)
        try:
            row = conn.execute(
                "SELECT clinic_id,name,active FROM nn_clinics WHERE clinic_id=2"
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(row, (2, "Клиника 2 (Шевчеко)", 1))

    def test_upload_bundle_is_bound_to_private_clinic_and_stored_separately(self):
        with patch("nn_normalize.normalize_batch", side_effect=self.fake_normalize):
            response = self.client_for(2).post(
                "/nn/uploads/",
                data=self.bundle(),
                content_type="multipart/form-data",
            )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Пять исходных файлов сохранены и обработаны", html)

        conn = sqlite3.connect(app_module.DB_PATH)
        conn.row_factory = sqlite3.Row
        batch = conn.execute("SELECT * FROM nn_upload_batches ORDER BY id").fetchone()
        files = conn.execute(
            "SELECT * FROM nn_upload_files WHERE batch_id=? ORDER BY source_key",
            (batch["id"],),
        ).fetchall()
        conn.close()

        self.assertEqual(batch["clinic_id"], 1)
        self.assertEqual(batch["status"], "ready")
        self.assertEqual(batch["uploaded_by"], 2)
        self.assertEqual(len(files), 5)
        self.assertTrue((nn_upload.UPLOAD_ROOT / "1" / str(batch["id"])).is_dir())
        self.assertEqual(nn_upload.UPLOAD_ROOT.stat().st_mode & 0o777, 0o700)
        self.assertEqual((nn_upload.UPLOAD_ROOT / "1").stat().st_mode & 0o777, 0o700)
        self.assertFalse((nn_upload.UPLOAD_ROOT / "2").exists())
        for row in files:
            target = nn_upload.UPLOAD_ROOT / "1" / str(batch["id"]) / row["stored_filename"]
            self.assertTrue(target.is_file())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

        clinic_two = self.client_for(2).get("/nn/uploads/?clinic_id=2").get_data(as_text=True)
        self.assertIn("Для выбранной клиники ещё нет загрузок.", clinic_two)

    def test_duplicate_bundle_does_not_create_second_batch(self):
        client = self.client_for(2)
        with patch("nn_normalize.normalize_batch", side_effect=self.fake_normalize):
            first = client.post("/nn/uploads/", data=self.bundle(), content_type="multipart/form-data")
            self.assertEqual(first.status_code, 200)
            second = client.post("/nn/uploads/", data=self.bundle(), content_type="multipart/form-data")
        self.assertIn("уже загружен", second.get_data(as_text=True))

        conn = sqlite3.connect(app_module.DB_PATH)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM nn_upload_batches").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM nn_upload_files").fetchone()[0], 5)
        finally:
            conn.close()

    def test_unknown_private_clinic_is_rejected_server_side(self):
        response = self.client_for(2).post(
            "/nn/uploads/",
            data=self.bundle("999"),
            content_type="multipart/form-data",
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("не найдена или отключена", html)

        conn = sqlite3.connect(app_module.DB_PATH)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM nn_upload_batches").fetchone()[0], 0)
        finally:
            conn.close()

    def test_reports_only_user_cannot_upload(self):
        self.assertEqual(self.client_for(3).get("/nn/uploads/").status_code, 403)

    def test_services_source_requires_pdf(self):
        payload = self.bundle()
        payload["services_detailed"] = (
            io.BytesIO(b"not a pdf"),
            "отчет по услугам подробно.pdf",
        )
        response = self.client_for(2).post(
            "/nn/uploads/",
            data=payload,
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("не распознан как PDF", response.get_data(as_text=True))

        conn = sqlite3.connect(app_module.DB_PATH)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM nn_upload_batches").fetchone()[0], 0)
        finally:
            conn.close()

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
        self.assertEqual(response.status_code, 200)
        self.assertIn("не распознан как PDF", response.get_data(as_text=True))

        conn = sqlite3.connect(app_module.DB_PATH)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM nn_upload_batches").fetchone()[0], 0)
        finally:
            conn.close()

    def test_empty_legacy_mapping_schema_migrates_to_private_clinics(self):
        conn = sqlite3.connect(app_module.DB_PATH)
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript(
            """
            DROP TABLE IF EXISTS nn_upload_files;
            DROP TABLE IF EXISTS nn_upload_batches;
            DROP TABLE IF EXISTS nn_clinics;
            CREATE TABLE nn_clinics (
                clinic_id INTEGER PRIMARY KEY,
                enabled_at TEXT NOT NULL,
                enabled_by INTEGER
            );
            CREATE TABLE nn_upload_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clinic_id INTEGER NOT NULL,
                bundle_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                uploaded_by INTEGER,
                uploaded_at TEXT NOT NULL
            );
            CREATE TABLE nn_upload_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                source_key TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                mime_type TEXT NOT NULL
            );
            CREATE TABLE nn_normalized_batches (
                batch_id INTEGER PRIMARY KEY,
                clinic_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                period_start TEXT,
                period_end TEXT,
                payload_json TEXT NOT NULL,
                normalized_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
        conn.close()

        nn_upload._init_schema()
        nn_normalize._init_schema()

        conn = sqlite3.connect(app_module.DB_PATH)
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(nn_clinics)")}
            names = [
                row[0]
                for row in conn.execute("SELECT name FROM nn_clinics ORDER BY clinic_id")
            ]
            fk_upload = conn.execute("PRAGMA foreign_key_list(nn_upload_batches)").fetchall()
            fk_norm = conn.execute("PRAGMA foreign_key_list(nn_normalized_batches)").fetchall()
        finally:
            conn.close()

        self.assertIn("name", cols)
        self.assertEqual(names, ["Клиника 1 (Толи Бе)", "Клиника 2 (Шевчеко)"])
        self.assertTrue(any(row[2] == "nn_clinics" for row in fk_upload))
        self.assertTrue(any(row[2] == "nn_clinics" for row in fk_norm))


if __name__ == "__main__":
    unittest.main()
