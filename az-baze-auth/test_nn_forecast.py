import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

os.environ.setdefault("AZBAZE_SECRET_KEY", "test-secret")
os.environ["AZBAZE_OWNER_EMAIL"] = "owner@example.com"

import app as app_module
import nn_forecast
import nn_normalize
import nn_upload


class NNForecastTests(unittest.TestCase):
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
            {"name": "Пациент 1", "dob": "1980-01-01", "gender": "", "source": "", "visits_count": 3, "iin": "", "chart": "1", "phone": "7000000001", "note": "", "source_amount": 0},
            {"name": "Пациент 2", "dob": "1981-01-01", "gender": "", "source": "", "visits_count": 1, "iin": "", "chart": "2", "phone": "7000000002", "note": "", "source_amount": 0},
            {"name": "Пациент 3", "dob": "1982-01-01", "gender": "", "source": "", "visits_count": 3, "iin": "", "chart": "3", "phone": "7000000003", "note": "", "source_amount": 0},
            {"name": "Пациент 4", "dob": "1983-01-01", "gender": "", "source": "", "visits_count": 1, "iin": "", "chart": "4", "phone": "7000000004", "note": "", "source_amount": 0},
        ]

        def visit(patient, chart, day, amount, services, repeat="", note="", doctor="Врач А"):
            return {
                "date": day,
                "start": "09:00",
                "end": "09:30",
                "doctor": doctor,
                "patient": patient,
                "chart": chart,
                "phone": "700000000" + chart,
                "iin": "",
                "repeat_field": repeat,
                "note": note,
                "visit_type": "Амбулаторно",
                "help_type": "",
                "services_text": services,
                "amount": amount,
            }

        visits = [
            visit("Пациент 1", "1", "2026-01-05", 6000, "Прием хирурга флеболога с УЗИ"),
            visit("Пациент 1", "1", "2026-01-10", 300000, "Лазерное лечение варикоза ЭВЛК"),
            visit("Пациент 1", "1", "2026-01-20", 0, "", "повторный", "контроль после операции"),
            visit("Пациент 2", "2", "2026-01-06", 0, "Прием хирурга флеболога с УЗИ", doctor="Врач Б"),
            visit("Пациент 3", "3", "2026-02-05", 6000, "Прием хирурга флеболога с УЗИ"),
            visit("Пациент 3", "3", "2026-02-10", 100000, "Пенная склеротерапия варикозных вен"),
            visit("Пациент 3", "3", "2026-02-20", 0, "", "повторный", "контроль"),
            visit("Пациент 4", "4", "2026-03-05", 10000, "Прием хирурга флеболога с УЗИ"),
        ]

        payload = nn_normalize.normalize_rows(master, visits, [], [], [], 0)

        conn = sqlite3.connect(app_module.DB_PATH)
        conn.row_factory = sqlite3.Row
        nn_normalize._init_schema(conn)
        conn.execute(
            "INSERT INTO nn_upload_batches(id,clinic_id,bundle_sha256,status,uploaded_by,uploaded_at) VALUES(301,1,'forecast-hash','ready',1,?)",
            (now,),
        )
        conn.execute(
            """
            INSERT INTO nn_normalized_batches(
                batch_id,clinic_id,version,period_start,period_end,payload_json,normalized_at
            ) VALUES(301,1,1,?,?,?,?)
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
            [(3, 250000, now), (4, 260000, now)],
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

    def test_forecast_page_is_single_phlebology_contour_with_az_psychology(self):
        home = self.client_for(2).get("/nn/")
        home_body = home.get_data(as_text=True)
        self.assertEqual(home.status_code, 200)
        self.assertIn('href="/nn/forecast/"', home_body)
        self.assertIn("Прогноз роста", home_body)

        page = self.client_for(2).get("/nn/forecast/")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        for label in (
            "1. База прогноза",
            "2. Что хотим изменить",
            "3. Что получим",
            "4. Управленческий вывод",
            "Подробности расчёта",
            "Точные настройки",
            "Как считается прогноз",
            "Консервативный",
            "Базовый",
            "Активный",
            "+ первичных / мес.",
            "Маркетинг / мес.",
            "+ мощность, визитов / мес.",
            "Конверсия первичный → лечение, %",
        ):
            self.assertIn(label, body)
        self.assertIn("Один контур: флебологическая клиника целиком", body)
        self.assertNotIn("Стоматология", body)
        self.assertNotIn("Лаборатория", body)

    def test_base_uses_only_full_months_and_current_month_is_separate(self):
        conn = sqlite3.connect(app_module.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            data = nn_forecast.forecast_base_data(
                conn,
                1,
                preset="3",
                today=date(2026, 3, 15),
            )
        finally:
            conn.close()

        self.assertEqual(data["selected_months"], ["2026-01", "2026-02"])
        self.assertEqual(data["forecast_start"], "2026-03")
        self.assertEqual(data["partial"]["month"], "2026-03")
        self.assertEqual(data["partial"]["revenue"], 10000.0)

        base = data["baseline"]
        self.assertEqual(base["month_count"], 2)
        self.assertEqual(base["revenue"], 206000.0)
        self.assertEqual(base["primaries"], 1.5)
        self.assertEqual(base["primary_unpaid"], 0.5)
        self.assertEqual(base["repeats"], 1.0)
        self.assertEqual(base["treatments"], 1.0)
        self.assertAlmostEqual(base["conversion"], 2 / 3, places=5)
        self.assertEqual(base["avg_check_paid"], 206000.0)
        self.assertAlmostEqual(base["avg_check_all"], 412000 / 3, places=2)
        self.assertEqual(base["coefficients"]["primary_revenue"], 4000.0)
        self.assertEqual(base["coefficients"]["treatment_revenue"], 200000.0)
        self.assertEqual(data["plans"]["2026-03"], 250000.0)
        self.assertEqual(data["plans"]["2026-04"], 260000.0)

    def test_custom_period_and_permissions(self):
        conn = sqlite3.connect(app_module.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            data = nn_forecast.forecast_base_data(
                conn,
                1,
                preset="custom",
                month_from="2026-02",
                month_to="2026-01",
                today=date(2026, 3, 15),
            )
        finally:
            conn.close()
        self.assertEqual(data["selected_months"], ["2026-01", "2026-02"])

        self.assertEqual(self.client_for(2).get("/nn/forecast/").status_code, 200)
        self.assertEqual(self.client_for(3).get("/nn/forecast/").status_code, 403)
        self.assertEqual(
            self.client_for(3).get("/api/nn/forecast/base?clinic_id=1").status_code,
            403,
        )

    def test_forecast_never_invents_economic_parameters(self):
        page = self.client_for(2).get("/nn/forecast/")
        body = page.get_data(as_text=True)
        self.assertIn('placeholder="Не задан"', body)
        self.assertIn("Прибыль появится только после заполнения ФОТ и медзатрат.", body)
        self.assertIn("Без мощности загрузка не рассчитывается.", body)
        self.assertIn('economicsReady:fotRaw!==""&&medRaw!==""', body)
        self.assertIn("ФОТ, переменные медзатраты и мощность не подменяются условными коэффициентами.", body)


if __name__ == "__main__":
    unittest.main()
