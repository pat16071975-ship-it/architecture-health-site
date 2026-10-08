import json
import sqlite3
import unittest

import paid_services
import daily_upload_core
import ident_import


class PaidServicesStorageTests(unittest.TestCase):
    def test_snapshot_overlay_preserves_clinic_cash_and_versions_replacement(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE report_data(date TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
        )
        report = {
            "month": "2026-09",
            "period_end": "2026-09-30",
            "totals": {"opening": 0, "billed": 100, "paid": 80, "closing": 20},
            "providers": {
                "Dent D.": {"opening": 0, "billed": 100, "paid": 80, "closing": 20}
            },
            "provider_residual": {"opening": 0, "billed": 0, "paid": 0, "closing": 0},
        }
        self.assertEqual(
            paid_services.store_snapshot(conn, report, "paid.xlsx", "sha1", 1, "t1"),
            "imported",
        )
        conn.execute(
            "INSERT INTO report_data(date,payload,updated_by,updated_at) VALUES(?,?,?,?)",
            (
                "2026-09-30",
                json.dumps({"cashTotal": 70, "cashOOO": 30, "cashIP": 40}),
                1,
                "old",
            ),
        )
        self.assertEqual(
            paid_services.overlay_stored_month(
                conn,
                "2026-09",
                1,
                "t2",
                {"Dent D.": "Dent Doctor"},
                {},
                {},
            ),
            1,
        )
        payload = json.loads(
            conn.execute(
                "SELECT payload FROM report_data WHERE date='2026-09-30'"
            ).fetchone()[0]
        )
        self.assertEqual(payload["cashTotal"], 70)
        self.assertEqual(payload["cashOOO"], 30)
        self.assertEqual(payload["cashIP"], 40)
        self.assertEqual(payload["paidDentists"]["Dent Doctor"], 80)
        self.assertEqual(payload["paidDentistry"], 80)

        changed = {
            **report,
            "totals": {"opening": 0, "billed": 100, "paid": 90, "closing": 10},
            "providers": {
                "Dent D.": {"opening": 0, "billed": 100, "paid": 90, "closing": 10}
            },
        }
        self.assertEqual(
            paid_services.store_snapshot(
                conn, changed, "paid2.xlsx", "sha2", 1, "t3", decision="use_new"
            ),
            "replaced",
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM service_payment_versions").fetchone()[0],
            1,
        )

    def test_same_payload_with_new_export_hash_is_identical(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        report = {
            "month": "2026-09",
            "period_end": "2026-09-30",
            "totals": {"opening": 0, "billed": 100, "paid": 80, "closing": 20},
            "providers": {
                "Dent D.": {"opening": 0, "billed": 100, "paid": 80, "closing": 20}
            },
            "provider_residual": {"opening": 0, "billed": 0, "paid": 0, "closing": 0},
        }
        self.assertEqual(
            paid_services.store_snapshot(conn, report, "first.xlsx", "sha-one", 1, "t1"),
            "imported",
        )
        self.assertEqual(
            paid_services.store_snapshot(conn, report, "second.xlsx", "sha-two", 1, "t2"),
            "identical",
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM service_payment_versions").fetchone()[0],
            0,
        )
        conn.close()

    def test_new_mis_items_replace_service_analytics_month(self):
        source_name, display_name = next(iter(ident_import.DENTISTS.items()))
        group = "Терапия"
        service = "Тестовая терапевтическая услуга"
        category = ident_import._classify_dent(group, service)
        months = list(ident_import.MONTH_NAMES[:9])
        data = {
            "year": 2026,
            "months": months,
            "directions": {
                "Стоматология": {
                    "categories": [category],
                    "doctors": {
                        display_name: {
                            category: [[0, 0] for _ in months]
                        }
                    },
                }
            },
        }
        data["directions"]["Стоматология"]["doctors"][display_name][category][8] = [99, 99999]
        daily_upload_core.replace_service_month(
            data,
            [{
                "staff": source_name,
                "group": group,
                "service": service,
                "qty": 2,
                "amount": 5000,
            }],
            2026,
            9,
        )
        self.assertEqual(
            data["directions"]["Стоматология"]["doctors"][display_name][category][8],
            [2.0, 5000.0],
        )

    def test_snapshot_applies_only_from_its_as_of_date(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        report = {
            "month": "2026-09",
            "period_end": "2026-09-30",
            "totals": {"opening": 0, "billed": 100, "paid": 80, "closing": 20},
            "providers": {
                "Dent D.": {"opening": 0, "billed": 100, "paid": 80, "closing": 20}
            },
            "provider_residual": {"opening": 0, "billed": 0, "paid": 0, "closing": 0},
        }
        paid_services.store_snapshot(conn, report, "paid.xlsx", "sha", 1, "now")
        self.assertIsNone(paid_services.snapshot_for_date(conn, "2026-09-29"))
        self.assertIsNotNone(paid_services.snapshot_for_date(conn, "2026-09-30"))


if __name__ == "__main__":
    unittest.main()
