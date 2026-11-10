import json
import os
import sqlite3
import unittest
from pathlib import Path

os.environ.setdefault("AZBAZE_SECRET_KEY", "service-staff-layout-test")
os.environ.setdefault("AZBAZE_DB", "/tmp/az-service-staff-layout-test.db")
os.environ.setdefault("AZBAZE_SITE_ROOT", "/tmp/az-service-staff-layout-site")

from werkzeug.exceptions import BadRequest

import report_storage


ROOT = Path(__file__).resolve().parent
REPORTS = ROOT.parent / "reports"


class ServiceStaffLayoutTests(unittest.TestCase):
    def conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE report_blobs(
                key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_by INTEGER,
                updated_at TEXT NOT NULL
            )
            """
        )
        analytics = {
            "version": 1,
            "months": ["Январь"],
            "directions": {
                "Стоматология": {
                    "doctors": {
                        "Доктор А": {},
                        "Доктор Б": {},
                        "Доктор В": {},
                    }
                },
                "Отделение структуры": {
                    "doctors": {
                        "Доктор Г": {},
                    }
                },
            },
        }
        conn.execute(
            "INSERT INTO report_blobs(key,payload,updated_by,updated_at) VALUES(?,?,?,?)",
            (
                "az-service-analytics-v1",
                json.dumps(analytics, ensure_ascii=False),
                1,
                "2026-11-10T00:00:00+00:00",
            ),
        )
        return conn

    def test_layout_is_global_read_only_for_generic_storage_api(self):
        self.assertIn(
            report_storage.SERVICE_STAFF_LAYOUT_KEY,
            report_storage.BOOTSTRAP_BLOB_KEYS,
        )
        self.assertNotIn(
            report_storage.SERVICE_STAFF_LAYOUT_KEY,
            report_storage.WRITABLE_BLOB_KEYS,
        )

    def test_normalize_preserves_direction_and_appends_new_staff(self):
        conn = self.conn()
        self.addCleanup(conn.close)
        layout = report_storage._normalize_service_staff_layout(
            {
                "version": 1,
                "directions": {
                    "Стоматология": {
                        "order": ["Доктор Б", "Доктор А"],
                        "hidden": ["Доктор А"],
                    }
                },
            },
            conn,
        )
        self.assertEqual(
            layout["directions"]["Стоматология"]["order"],
            ["Доктор Б", "Доктор А", "Доктор В"],
        )
        self.assertEqual(
            layout["directions"]["Стоматология"]["hidden"],
            ["Доктор А"],
        )
        self.assertEqual(
            layout["directions"]["Отделение структуры"]["order"],
            ["Доктор Г"],
        )

    def test_cross_direction_or_unknown_staff_is_rejected(self):
        conn = self.conn()
        self.addCleanup(conn.close)
        with self.assertRaises(BadRequest):
            report_storage._normalize_service_staff_layout(
                {
                    "version": 1,
                    "directions": {
                        "Стоматология": {
                            "order": ["Доктор Г"],
                            "hidden": [],
                        }
                    },
                },
                conn,
            )

    def test_saved_layout_is_reconciled_with_new_staff_without_write(self):
        conn = self.conn()
        self.addCleanup(conn.close)
        old = {
            "version": 1,
            "directions": {
                "Стоматология": {
                    "order": ["Доктор В", "Доктор А"],
                    "hidden": ["Доктор А"],
                },
                "Отделение структуры": {
                    "order": ["Доктор Г"],
                    "hidden": [],
                },
            },
        }
        conn.execute(
            "INSERT INTO report_blobs(key,payload,updated_by,updated_at) VALUES(?,?,?,?)",
            (
                report_storage.SERVICE_STAFF_LAYOUT_KEY,
                json.dumps(old, ensure_ascii=False),
                1,
                "2026-11-10T01:00:00+00:00",
            ),
        )
        layout, updated_at = report_storage._load_service_staff_layout(conn)
        self.assertEqual(updated_at, "2026-11-10T01:00:00+00:00")
        self.assertEqual(
            layout["directions"]["Стоматология"]["order"],
            ["Доктор В", "Доктор А", "Доктор Б"],
        )
        self.assertEqual(
            layout["directions"]["Стоматология"]["hidden"],
            ["Доктор А"],
        )

    def test_ui_contract_keeps_hidden_staff_in_totals_but_out_of_personal_views(self):
        base = (REPORTS / "services-base.html").read_text(encoding="utf-8")
        layout = (REPORTS / "services-staff-layout.js").read_text(encoding="utf-8")
        payroll = (REPORTS / "services-payroll-manager.js").read_text(encoding="utf-8")
        services = (REPORTS / "services.html").read_text(encoding="utf-8")

        self.assertIn("services-staff-layout.js?v=20261110-1", services)
        self.assertIn("staffDoctorNames($('#direction').value,false)", base)
        self.assertIn(
            "return Object.keys(d.doctors)",
            base,
        )
        self.assertIn("AZServiceStaffLayout", layout)
        self.assertIn("Сохранить для всех", layout)
        self.assertIn("data-hide=", layout)
        self.assertIn("data-show=", layout)
        self.assertIn("draggable=\"true\"", layout)
        self.assertIn("const doctors = staffNames(w, state.direction", payroll)

        # Hidden rows are presentation-only. Persistent salary/extras loops still
        # traverse all source doctors instead of only visible doctors.
        self.assertIn(
            "Object.keys(data.directions?.[direction]?.doctors || {}).forEach(doctor =>",
            payroll,
        )


if __name__ == "__main__":
    unittest.main()
