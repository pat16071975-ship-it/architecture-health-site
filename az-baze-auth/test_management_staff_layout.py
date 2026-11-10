import json
import os
import sqlite3
import unittest
from pathlib import Path

os.environ.setdefault("AZBAZE_SECRET_KEY", "management-staff-layout-test")
os.environ.setdefault("AZBAZE_DB", "/tmp/az-management-staff-layout-test.db")
os.environ.setdefault("AZBAZE_SITE_ROOT", "/tmp/az-management-staff-layout-site")

from werkzeug.exceptions import BadRequest

import report_storage


ROOT = Path(__file__).resolve().parent
REPORTS = ROOT.parent / "reports"


class ManagementStaffLayoutTests(unittest.TestCase):
    def conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE report_data(
                date TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_by INTEGER,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE report_blobs(
                key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_by INTEGER,
                updated_at TEXT NOT NULL
            );
            """
        )
        payload = {
            "date": "2026-01-31",
            "dentists": {
                "Стоматолог А": 100,
                "Стоматолог Б": 200,
            },
            "clinicDocs": {
                "Специалист А": 300,
                "Специалист Б": 400,
            },
        }
        conn.execute(
            "INSERT INTO report_data(date,payload,updated_by,updated_at) VALUES(?,?,?,?)",
            (
                "2026-01-31",
                json.dumps(payload, ensure_ascii=False),
                1,
                "2026-11-10T00:00:00+00:00",
            ),
        )
        return conn

    def test_default_layout_does_not_reorder_before_admin_save(self):
        conn = self.conn()
        self.addCleanup(conn.close)
        layout, updated_at = report_storage._load_management_staff_layout(conn)
        self.assertIsNone(updated_at)
        self.assertEqual(
            layout,
            {
                "version": 1,
                "directions": {
                    "dentists": {"order": [], "hidden": []},
                    "clinicDocs": {"order": [], "hidden": []},
                },
            },
        )

    def test_admin_layout_reorders_and_hides_only_inside_direction(self):
        conn = self.conn()
        self.addCleanup(conn.close)
        layout = report_storage._normalize_management_staff_layout(
            {
                "version": 1,
                "directions": {
                    "dentists": {
                        "order": ["Стоматолог Б", "Стоматолог А"],
                        "hidden": ["Стоматолог А"],
                    },
                    "clinicDocs": {
                        "order": ["Специалист Б", "Специалист А"],
                        "hidden": [],
                    },
                },
            },
            conn,
        )
        self.assertEqual(
            layout["directions"]["dentists"]["order"][:2],
            ["Стоматолог Б", "Стоматолог А"],
        )
        self.assertEqual(
            layout["directions"]["dentists"]["hidden"],
            ["Стоматолог А"],
        )
        self.assertEqual(
            layout["directions"]["clinicDocs"]["order"][:2],
            ["Специалист Б", "Специалист А"],
        )

    def test_cross_direction_move_is_rejected_server_side(self):
        conn = self.conn()
        self.addCleanup(conn.close)
        with self.assertRaises(BadRequest):
            report_storage._normalize_management_staff_layout(
                {
                    "version": 1,
                    "directions": {
                        "dentists": {
                            "order": ["Специалист А"],
                            "hidden": [],
                        },
                        "clinicDocs": {
                            "order": [],
                            "hidden": [],
                        },
                    },
                },
                conn,
            )

    def test_stale_saved_name_is_ignored_without_resetting_valid_order(self):
        conn = self.conn()
        self.addCleanup(conn.close)
        stale = {
            "version": 1,
            "directions": {
                "dentists": {
                    "order": ["Стоматолог Б", "Уволенный сотрудник"],
                    "hidden": ["Уволенный сотрудник"],
                },
                "clinicDocs": {
                    "order": ["Специалист Б"],
                    "hidden": [],
                },
            },
        }
        conn.execute(
            "INSERT INTO report_blobs(key,payload,updated_by,updated_at) VALUES(?,?,?,?)",
            (
                report_storage.MANAGEMENT_STAFF_LAYOUT_KEY,
                json.dumps(stale, ensure_ascii=False),
                1,
                "2026-11-10T01:00:00+00:00",
            ),
        )
        layout, updated_at = report_storage._load_management_staff_layout(conn)
        self.assertEqual(updated_at, "2026-11-10T01:00:00+00:00")
        self.assertEqual(layout["directions"]["dentists"]["order"][0], "Стоматолог Б")
        self.assertNotIn(
            "Уволенный сотрудник",
            layout["directions"]["dentists"]["order"],
        )
        self.assertNotIn(
            "Уволенный сотрудник",
            layout["directions"]["dentists"]["hidden"],
        )

    def test_management_ui_uses_visible_order_but_totals_keep_full_source(self):
        index = (REPORTS / "index.html").read_text(encoding="utf-8")
        period = (REPORTS / "period-view.js").read_text(encoding="utf-8")
        layout = (REPORTS / "management-staff-layout.js").read_text(encoding="utf-8")

        self.assertIn("management-staff-layout.js?v=20261110-1", index)
        self.assertIn("window.azManagementStaffNames('dentists')", index)
        self.assertIn("window.azManagementStaffNames('clinicDocs')", index)

        self.assertIn(
            "managementStaffNames('dentists').forEach(name=>addDateDoctorMetric",
            period,
        )
        self.assertIn(
            "managementStaffNames('clinicDocs').forEach(name=>addDateDoctorMetric",
            period,
        )
        self.assertIn(
            "managementStaffNames('dentists').forEach(name=>addMetric",
            period,
        )
        self.assertIn(
            "managementStaffNames('clinicDocs').forEach(name=>addMetric",
            period,
        )

        # Direction totals still use the complete source arrays, not visible rows.
        self.assertIn(
            "const names=type==='dentists'?dentists:type==='clinicDocs'?clinicDocs:[];",
            period,
        )
        self.assertIn(
            "dentists.forEach(n=>out.dentists[n]=",
            index,
        )
        self.assertIn(
            "clinicDocs.forEach(n=>out.clinicDocs[n]=",
            index,
        )

        self.assertIn("Настроить персонал", layout)
        self.assertIn("Сохранить для всех", layout)
        self.assertIn("CAN_EDIT_MANAGEMENT_STAFF_LAYOUT", layout)
        self.assertIn("/api/reports/management-staff-layout", layout)
        self.assertIn("draggable=\"true\"", layout)
        self.assertIn("data-hide=", layout)
        self.assertIn("data-show=", layout)

    def test_wrong_pr196_service_analytics_artifacts_are_gone(self):
        self.assertFalse((REPORTS / "services-staff-layout.js").exists())
        self.assertFalse((ROOT / "test_service_staff_layout.py").exists())
        self.assertNotIn(
            "SERVICE_STAFF_LAYOUT_KEY",
            (ROOT / "report_storage.py").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
