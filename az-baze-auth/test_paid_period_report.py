import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from flask import Flask, g

import app as app_core
import ident_import
import paid_services
import report_storage
import upload_reconcile


class PaidPeriodReportTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        paid_services.init_schema(self.conn)
        upload_reconcile.init_schema(self.conn)
        self.conn.executescript(report_storage.REPORT_SCHEMA)
        self.conn.execute("CREATE TABLE permissions(user_id INTEGER,section TEXT)")
        self.conn.commit()
        self.patches = [
            patch.object(ident_import, "DENTISTS", {"Dent A.": "Dent Doctor"}),
            patch.object(ident_import, "STRUCTURE_DOCTORS", {"Clinic B.": "Clinic Doctor"}),
            patch.object(ident_import, "LAB_DOCTORS", {}),
            patch.object(ident_import, "KNOWN_STAFF", {"Dent A.", "Clinic B."}),
            patch.object(report_storage, "db", return_value=self.conn),
            patch.object(app_core, "db", return_value=self.conn),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.conn.close()

    def report(self, start="2026-01-01", end="2026-06-30"):
        month = end[:7] if start == end[:7] + "-01" else ""
        return {
            "month": month, "period_start": start, "period_end": end,
            "totals": {"opening": 0, "billed": 125, "paid": 125, "closing": 0},
            "providers": {
                "Dent A.": {"opening": 0, "billed": 100, "paid": 100, "closing": 0},
                "Clinic B.": {"opening": 0, "billed": 25, "paid": 25, "closing": 0},
            },
            "provider_residual": {"opening": 0, "billed": 0, "paid": 0, "closing": 0},
        }

    def store(self, report):
        paid_services.store_snapshot(self.conn, report, "test.xlsx", "test-sha", 1, "test-time")
        self.conn.commit()

    def client(self):
        app = Flask(__name__)
        app.secret_key = "paid-period-test-only"

        @app.before_request
        def actor():
            g.user = {"id": 1, "is_admin": True}

        with patch.object(report_storage, "_ensure_schema"):
            report_storage.register_report_storage(app)
        return app.test_client()

    def test_endpoint_exposes_whole_period_without_changing_months_or_report_data(self):
        self.store(self.report())
        self.store(self.report("2026-07-01", "2026-07-31"))
        original = {
            "date": "2026-06-30", "cashTotal": 430, "cashOOO": 200,
            "cashIP": 230, "primary": 7, "labRevenue": 17,
            "dentists": {"Dent Doctor": 72}, "clinicDocs": {"Clinic Doctor": 11},
        }
        serialized = json.dumps(original, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO report_data(date,payload,updated_by,updated_at) VALUES(?,?,?,?)",
            (original["date"], serialized, 1, "test-time"),
        )
        self.conn.commit()
        before = "\n".join(self.conn.iterdump())
        self.conn.execute("PRAGMA query_only=ON")
        response = self.client().get("/api/reports/data")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["data"], {original["date"]: original})
        self.assertEqual(set(payload["doctorPaidSnapshots"]), {"2026-07"})
        self.assertEqual(payload["doctorPaidPeriods"], [{
            "from": "2026-01-01", "to": "2026-06-30",
            "dentists": {"Dent Doctor": 100}, "clinicDocs": {"Clinic Doctor": 25},
        }])
        self.assertEqual(before, "\n".join(self.conn.iterdump()))
        self.assertNotIn("source_sha", response.get_data(as_text=True))
        self.assertNotIn("visit_links", response.get_data(as_text=True))

    def test_new_period_and_same_end_month_can_coexist(self):
        self.store(self.report())
        monthly = self.report("2026-06-01", "2026-06-30")
        monthly["providers"]["Dent A."]["paid"] = 9
        monthly["providers"]["Dent A."]["closing"] = 91
        monthly["totals"]["paid"] = 34
        monthly["totals"]["closing"] = 91
        self.store(monthly)
        periods = report_storage._management_doctor_paid_periods(self.conn)
        months = report_storage._management_doctor_paid_snapshots(self.conn)
        self.assertEqual(periods[0]["dentists"]["Dent Doctor"], 100)
        self.assertEqual(months["2026-06"][0]["dentists"]["Dent Doctor"], 9)

    def test_registry_decision_is_identical_for_month_and_range(self):
        self.store(self.report())
        self.store(self.report("2026-07-01", "2026-07-31"))
        self.conn.execute(
            "INSERT INTO provider_registry(source_name,display_name,direction,"
            "first_seen_at,last_seen_at) VALUES(?,?,?,?,?)",
            ("Dent A.", "Confirmed Doctor", "structure", "t", "t"),
        )
        self.conn.commit()
        self.conn.execute("PRAGMA query_only=ON")
        period = report_storage._management_doctor_paid_periods(self.conn)[0]
        month = report_storage._management_doctor_paid_snapshots(self.conn)["2026-07"][0]
        for record in (period, month):
            self.assertEqual(record["dentists"], {})
            self.assertEqual(record["clinicDocs"]["Confirmed Doctor"], 100)
            self.assertEqual(record["clinicDocs"]["Clinic Doctor"], 25)

    def test_unclassified_provider_does_not_silently_omit_money(self):
        report = self.report()
        report["providers"]["Unknown C."] = {"paid": 7, "billed": 7}
        self.store(report)
        self.assertEqual(report_storage._management_doctor_paid_periods(self.conn), [])

    def test_mismatched_invalid_and_nonfinite_payloads_are_not_reported(self):
        self.store(self.report())
        row = self.conn.execute("SELECT payload_json FROM service_payment_periods").fetchone()
        original = json.loads(row[0])
        variants = [
            {**original, "period_start": "2025-01-01"},
            {**original, "version": 2},
            {**original, "providers": {"Dent A.": {"paid": float("nan")}}},
            {**original, "providers": {"Dent A.": []}},
        ]
        for value in variants:
            with self.subTest(value=value):
                self.conn.execute(
                    "UPDATE service_payment_periods SET payload_json=?", (json.dumps(value),),
                )
                self.assertEqual(report_storage._management_doctor_paid_periods(self.conn), [])
        self.conn.execute(
            "UPDATE service_payment_periods SET period_start='2026-02-30',payload_json=?",
            (json.dumps({**original, "period_start": "2026-02-30"}),),
        )
        self.assertEqual(report_storage._management_doctor_paid_periods(self.conn), [])

    def test_no_period_schema_yet_is_a_read_only_empty_result(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        try:
            self.assertEqual(report_storage._management_doctor_paid_periods(conn), [])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], 0)
        finally:
            conn.close()

    def test_rendered_page_receives_range_state_and_provider_names(self):
        app = Flask(__name__)
        app.secret_key = "paid-period-render-test"
        with app.test_request_context("/reports/management/"), patch.object(
            report_storage, "SITE_ROOT", Path(__file__).resolve().parent.parent,
        ):
            g.user = {"id": 1, "is_admin": True}
            html = report_storage._render_server_reports()
        self.assertIn("DOCTOR_PAID_PERIODS=[]", html)
        self.assertIn("payload.doctorPaidPeriods", html)
        self.assertIn("DOCTOR_PAID_PERIODS.forEach", html)
        self.assertIn("syncProviderNames({[r.to]", html)
        self.assertNotIn("localStorage.getItem(STORAGE_KEY)", html)


if __name__ == "__main__":
    unittest.main()
