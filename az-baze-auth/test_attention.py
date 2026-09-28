import json
import sqlite3
import unittest
from datetime import date
from unittest.mock import patch

import attention


class AttentionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE report_data(date TEXT PRIMARY KEY,payload TEXT)")
        self.conn.execute("CREATE TABLE daily_uploads(data_date TEXT PRIMARY KEY)")
        self.conn.execute("CREATE TABLE cash_receipts_daily(data_date TEXT PRIMARY KEY)")
        self.conn.execute("CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT)")
        self.today = date(2026, 9, 28)

    def add_day(self, data_date, payload, cash=False):
        self.conn.execute("INSERT INTO daily_uploads(data_date) VALUES(?)", (data_date,))
        self.conn.execute(
            "INSERT INTO report_data(date,payload) VALUES(?,?)",
            (data_date, json.dumps(payload, ensure_ascii=False)),
        )
        if cash:
            self.conn.execute("INSERT INTO cash_receipts_daily(data_date) VALUES(?)", (data_date,))

    def test_summary_uses_comparable_loaded_days_and_current_snapshot(self):
        # August: three comparable loaded days.
        self.add_day("2026-08-01", {"dentists": {"D": 100}, "clinicDocs": {"C": 200}, "labRevenue": 50})
        self.add_day("2026-08-02", {"dentists": {"D": 200}, "clinicDocs": {"C": 400}, "labRevenue": 100})
        self.add_day("2026-08-03", {"dentists": {"D": 300}, "clinicDocs": {"C": 600}, "labRevenue": 150})

        # September: two loaded days, latest is current snapshot.
        self.add_day("2026-09-26", {"dentists": {"D": 150}, "clinicDocs": {"C": 210}, "labRevenue": 55})
        self.add_day(
            "2026-09-27",
            {
                "plan": 3100,
                "cashTotal": 2700,
                "primary": 50,
                "dentPrimary": 20,
                "clinicPrimary": 30,
                "labOrders": 10,
                "dentists": {"D": 240},
                "clinicDocs": {"C": 500},
                "labRevenue": 120,
            },
            cash=True,
        )

        with patch.object(attention, "_clinic_today", return_value=self.today):
            summary = attention.build_summary(self.conn)

        self.assertEqual(summary["snapshot_date"], "2026-09-27")
        self.assertEqual(summary["primary"]["total"], 50)
        self.assertEqual(summary["primary"]["dentistry"], 20)
        self.assertEqual(summary["primary"]["structure"], 30)
        self.assertEqual(summary["primary"]["lab_orders"], 10)

        rows = {row["key"]: row for row in summary["revenue"]}
        # Compare against August day 2: dentistry 200, structure 400, lab 100.
        self.assertEqual(rows["dentistry"]["deviation"], 20.0)
        self.assertEqual(rows["structure"]["deviation"], 25.0)
        self.assertEqual(rows["lab"]["deviation"], 20.0)
        self.assertIn("выше среднего темпа текущего года", rows["dentistry"]["comment"])

        # Выполнение плана месяца считается от полного месячного плана.
        self.assertAlmostEqual(summary["plan"]["execution"], 87.1, places=1)
        # Отклонение для комментария сравнивает факт с строкой «Должно быть».
        self.assertAlmostEqual(summary["plan"]["delta"], -3.2, places=1)
        self.assertFalse(summary["clinical_stale"])
        self.assertFalse(summary["cash_stale"])

    def test_management_plan_blob_overrides_raw_record_plan(self):
        self.conn.execute(
            "INSERT INTO report_blobs(key,payload) VALUES(?,?)",
            (
                "az-management-monthly-plan-v1",
                json.dumps({"version": 1, "months": {"2026-09": 9000000}}, ensure_ascii=False),
            ),
        )
        self.add_day(
            "2026-09-27",
            {
                "plan": 0,
                "cashTotal": 8377290,
                "primary": 52,
                "dentPrimary": 22,
                "clinicPrimary": 30,
                "labOrders": 11,
            },
            cash=True,
        )
        with patch.object(attention, "_clinic_today", return_value=self.today):
            summary = attention.build_summary(self.conn)

        self.assertEqual(summary["plan"]["due"], 8100000)
        self.assertEqual(summary["plan"]["fact"], 8377290)
        self.assertAlmostEqual(summary["plan"]["execution"], 93.1, places=1)
        self.assertAlmostEqual(summary["plan"]["delta"], 3.4, places=1)

    def test_stale_sources_are_reported_independently(self):
        self.add_day("2026-09-25", {"plan": 1000, "cashTotal": 500})
        self.conn.execute("INSERT INTO cash_receipts_daily(data_date) VALUES(?)", ("2026-09-24",))
        with patch.object(attention, "_clinic_today", return_value=self.today):
            summary = attention.build_summary(self.conn)
        self.assertTrue(summary["clinical_stale"])
        self.assertTrue(summary["cash_stale"])
        self.assertEqual(summary["latest_clinical_label"], "25.09.2026")
        self.assertEqual(summary["latest_cash_label"], "24.09.2026")

    def test_home_modal_has_no_navigation_actions_and_is_mobile_adaptive(self):
        html = attention.home_modal_fragment(7)
        self.assertNotIn('href="/reports/', html)
        self.assertNotIn("Открыть", html)
        self.assertIn("@media(max-width:700px)", html)
        self.assertIn("az-attention-close", html)
        self.assertIn("az-attention-seen:7:", html)


if __name__ == "__main__":
    unittest.main()
