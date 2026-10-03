import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("AZBAZE_SECRET_KEY", "test-secret")
os.environ["AZBAZE_OWNER_EMAIL"] = "owner@example.com"

import app as app_module
import nn_management
import nn_normalize
import nn_upload


class NNManagementReportTests(unittest.TestCase):
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
        now = app_module.iso_now()
        conn.executemany(
            """INSERT INTO users(
                id,email,full_name,password_hash,is_admin,active,must_change_password,
                failed_attempts,locked_until,created_at,updated_at
            ) VALUES(?,?,?,?,?,1,0,0,NULL,?,?)""",
            [
                (1, "owner@example.com", "Owner", "x", 1, now, now),
                (2, "reports@example.com", "Reports", "x", 0, now, now),
                (3, "upload@example.com", "Upload", "x", 0, now, now),
            ],
        )
        conn.executemany(
            "INSERT INTO permissions(user_id,section) VALUES(?,?)",
            [(2, "nn_reports"), (3, "nn_upload")],
        )
        conn.commit()
        conn.close()

        nn_upload._init_schema()

        master = [
            {"name": "Пациент Один", "dob": "1980-01-01", "gender": "", "source": "", "visits_count": 2, "iin": "", "chart": "1", "phone": "7000000001", "note": "", "source_amount": 0},
            {"name": "Пациент Два", "dob": "1985-01-01", "gender": "", "source": "", "visits_count": 1, "iin": "", "chart": "2", "phone": "7000000002", "note": "", "source_amount": 0},
            {"name": "Пациент Три", "dob": "1990-01-01", "gender": "", "source": "", "visits_count": 2, "iin": "", "chart": "3", "phone": "7000000003", "note": "", "source_amount": 0},
        ]
        visits = [
            {"date": "2026-01-10", "start": "09:00", "end": "09:30", "doctor": "Имиров Ялкунжан Ахметжанович", "patient": "Пациент Один", "chart": "1", "phone": "7000000001", "iin": "", "repeat_field": "", "note": "", "visit_type": "Амбулаторно", "help_type": "", "services_text": "Прием хирурга флеболога", "amount": 100},
            {"date": "2026-01-15", "start": "09:00", "end": "09:30", "doctor": "Имиров Ялкунжан Ахметжанович", "patient": "Пациент Один", "chart": "1", "phone": "7000000001", "iin": "", "repeat_field": "повторный", "note": "контроль", "visit_type": "Амбулаторно", "help_type": "", "services_text": "", "amount": 0},
            {"date": "2026-01-20", "start": "10:00", "end": "10:30", "doctor": "Нурмаганбет Самал Тимуркызы", "patient": "Пациент Два", "chart": "2", "phone": "7000000002", "iin": "", "repeat_field": "", "note": "", "visit_type": "Амбулаторно", "help_type": "", "services_text": "Прием хирурга флеболога", "amount": 0},
            {"date": "2026-02-10", "start": "11:00", "end": "11:30", "doctor": "Имиров Ялкунжан Ахметжанович", "patient": "Пациент Три", "chart": "3", "phone": "7000000003", "iin": "", "repeat_field": "", "note": "", "visit_type": "Амбулаторно", "help_type": "", "services_text": "Прием хирурга флеболога", "amount": 200},
            {"date": "2026-02-15", "start": "11:00", "end": "11:30", "doctor": "Нурмаганбет Самал Тимуркызы", "patient": "Пациент Три", "chart": "3", "phone": "7000000003", "iin": "", "repeat_field": "повторный", "note": "контроль", "visit_type": "Амбулаторно", "help_type": "", "services_text": "", "amount": 50},
        ]
        payload = nn_normalize.normalize_rows(master, visits, [], [], [], 0)

        conn = sqlite3.connect(app_module.DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        nn_normalize._init_schema(conn)
        conn.execute(
            "INSERT INTO nn_upload_batches(id,clinic_id,bundle_sha256,status,uploaded_by,uploaded_at) VALUES(101,1,'mgmt-hash','ready',1,?)",
            (now,),
        )
        conn.execute(
            """
            INSERT INTO nn_normalized_batches(
                batch_id,clinic_id,version,period_start,period_end,payload_json,normalized_at
            ) VALUES(101,1,1,?,?,?,?)
            """,
            (
                payload["period"]["start"],
                payload["period"]["end"],
                json.dumps(payload, ensure_ascii=False),
                now,
            ),
        )
        conn.executemany(
            """
            INSERT INTO nn_monthly_plans(clinic_id,year,month,amount,updated_by,updated_at)
            VALUES(1,2026,?,?,1,?)
            """,
            [(1, 3100, now), (2, 2800, now)],
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

    def test_management_report_is_primary_nn_menu_item_and_uses_compare_first(self):
        home = self.client_for(2).get("/nn/")
        body = home.get_data(as_text=True)
        self.assertEqual(home.status_code, 200)
        self.assertIn('href="/nn/management/"', body)
        self.assertLess(body.index("Управленческий отчёт"), body.index("BI-аналитика"))

        page = self.client_for(2).get("/nn/management/")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("На эту дату по месяцам", body)
        self.assertIn('<option value="compare" selected>', body)
        self.assertIn("Первичные приёмы (в скобках — без оплаты)", body)
        self.assertIn("Средний чек 2025 (в скобках — по всем первичным)", body)
        self.assertIn("Выручка по врачам", body)
        self.assertIn("Маркетинг", body)
        self.assertIn(".nn-shell{width:98vw;max-width:none}", body)
        self.assertIn("width:320px;min-width:320px;max-width:320px", body)
        self.assertIn("background:#fffdf8!important", body)
        self.assertIn("box-shadow:3px 0 0 rgba(216,205,187,.92)", body)
        self.assertIn("tr.mg-group td{position:static!important", body)

    def test_compare_uses_same_day_across_months_and_expected_formulas(self):
        response = self.client_for(2).get(
            "/api/nn/management/compare?clinic_id=1&date=2026-02-20&range=ytd&view=compare"
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual([m["label"] for m in data["months"]], ["Январь", "Февраль"])

        jan, feb = data["months"]
        self.assertEqual(jan["target"], "2026-01-20")
        self.assertEqual(jan["plan"], 3100.0)
        self.assertEqual(jan["due"], 2000.0)
        self.assertEqual(jan["fact"], 100.0)
        self.assertEqual(jan["primary"], 2)
        self.assertEqual(jan["primary_unpaid"], 1)
        self.assertEqual(jan["repeat"], 1)
        self.assertEqual(jan["pp_day"], 0.1)
        self.assertEqual(jan["avg_paid"], 100.0)
        self.assertEqual(jan["avg_all"], 50.0)

        self.assertEqual(feb["target"], "2026-02-20")
        self.assertEqual(feb["plan"], 2800.0)
        self.assertEqual(feb["due"], 2000.0)
        self.assertEqual(feb["fact"], 250.0)
        self.assertEqual(feb["primary"], 1)
        self.assertEqual(feb["primary_unpaid"], 0)
        self.assertEqual(feb["repeat"], 1)
        self.assertEqual(feb["pp_day"], 0.05)
        self.assertEqual(feb["avg_paid"], 250.0)
        self.assertEqual(feb["avg_all"], 250.0)

        doctors = {row["label"]: row["values"] for row in data["doctors"]}
        self.assertEqual(doctors["Имиров Я.А."], [100.0, 200.0])
        self.assertEqual(doctors["Нурмаганбет С.Т."], [0.0, 50.0])

    def test_plan_button_lives_in_uploads_and_plan_is_manual_per_clinic_month(self):
        upload_page = self.client_for(3).get("/nn/uploads/?clinic_id=1")
        body = upload_page.get_data(as_text=True)
        self.assertEqual(upload_page.status_code, 200)
        self.assertIn('href="/nn/uploads/plan/?clinic_id=1"', body)
        self.assertIn(">План<", body)

        payload = {
            "csrf": "test-csrf",
            "clinic_id": "1",
            "year": "2027",
            "plan_1": "1 500 000",
            "plan_2": "2500000,50",
        }
        response = self.client_for(3).post("/nn/uploads/plan/", data=payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn("План сохранён.", response.get_data(as_text=True))

        conn = sqlite3.connect(app_module.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT month,amount FROM nn_monthly_plans WHERE clinic_id=1 AND year=2027 ORDER BY month"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [(1, 1500000.0), (2, 2500000.5)])

    def test_plan_and_management_permissions_remain_separate(self):
        self.assertEqual(self.client_for(2).get("/nn/uploads/plan/").status_code, 403)
        self.assertEqual(self.client_for(3).get("/nn/management/").status_code, 403)


if __name__ == "__main__":
    unittest.main()
