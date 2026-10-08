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

    def add_report_snapshot(self, data_date, payload):
        self.conn.execute(
            "INSERT INTO report_data(date,payload) VALUES(?,?)",
            (data_date, json.dumps(payload, ensure_ascii=False)),
        )

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
        self.assertEqual(rows["dentistry"]["amount"], 240)
        self.assertEqual(rows["structure"]["amount"], 500)
        self.assertEqual(rows["lab"]["amount"], 120)

        # Выполнение плана месяца считается от полного месячного плана.
        self.assertAlmostEqual(summary["plan"]["execution"], 87.1, places=1)
        # Отклонение для комментария сравнивает факт с строкой «Должно быть».
        self.assertAlmostEqual(summary["plan"]["delta"], -3.2, places=1)
        self.assertFalse(summary["clinical_stale"])
        self.assertFalse(summary["cash_stale"])

    def test_revenue_uses_paid_provider_fields_and_has_no_user_unallocated_row(self):
        self.add_day(
            "2026-09-27",
            {
                "plan": 5000,
                "cashTotal": 3000,
                "cashUnallocated": 1200,
                "dentists": {"D": 500},
                "clinicDocs": {"C": 300},
                "labRevenue": 100,
                "paidDentists": {"D": 1400},
                "paidClinicDocs": {"C": 900},
                "paidLabRevenue": 250,
            },
            cash=True,
        )

        with patch.object(attention, "_clinic_today", return_value=self.today):
            summary = attention.build_summary(self.conn)

        rows = {row["key"]: row for row in summary["revenue"]}
        self.assertEqual(rows["dentistry"]["amount"], 1400)
        self.assertEqual(rows["structure"]["amount"], 900)
        self.assertEqual(rows["lab"]["amount"], 250)
        self.assertNotIn("unallocated", rows)

    def test_revenue_comparison_uses_report_data_history_without_old_daily_uploads(self):
        self.add_report_snapshot(
            "2026-07-28",
            {"dentists": {"D": 400}, "clinicDocs": {"C": 200}, "labRevenue": 100},
        )
        self.add_report_snapshot(
            "2026-08-28",
            {"dentists": {"D": 500}, "clinicDocs": {"C": 300}, "labRevenue": 150},
        )

        self.add_day("2026-09-27", {"dentists": {"D": 550}, "clinicDocs": {"C": 250}, "labRevenue": 125})
        self.add_day(
            "2026-09-28",
            {"dentists": {"D": 600}, "clinicDocs": {"C": 350}, "labRevenue": 200},
            cash=True,
        )

        with patch.object(attention, "_clinic_today", return_value=self.today):
            summary = attention.build_summary(self.conn)

        rows = {row["key"]: row for row in summary["revenue"]}
        self.assertEqual(rows["dentistry"]["amount"], 600)
        self.assertEqual(rows["dentistry"]["deviation"], 33.3)
        self.assertEqual(rows["structure"]["deviation"], 40.0)
        self.assertEqual(rows["lab"]["deviation"], 60.0)
        self.assertIn("выше среднего", rows["dentistry"]["comment"].lower())

    def test_primary_details_use_report_data_history_for_comparison_and_forecast(self):
        # Historical cumulative snapshots exist only in report_data.
        # Each month has a comparable snapshot on day 27 and a later final snapshot.
        self.add_report_snapshot(
            "2026-07-27",
            {"primary": 40, "dentPrimary": 15, "clinicPrimary": 25, "labOrders": 8},
        )
        self.add_report_snapshot(
            "2026-07-31",
            {"primary": 50, "dentPrimary": 18, "clinicPrimary": 32, "labOrders": 10},
        )
        self.add_report_snapshot(
            "2026-08-27",
            {"primary": 50, "dentPrimary": 20, "clinicPrimary": 30, "labOrders": 10},
        )
        self.add_report_snapshot(
            "2026-08-31",
            {"primary": 60, "dentPrimary": 24, "clinicPrimary": 36, "labOrders": 12},
        )

        self.add_day("2026-09-26", {"primary": 30, "dentPrimary": 12, "clinicPrimary": 18, "labOrders": 6})
        self.add_day(
            "2026-09-27",
            {"primary": 60, "dentPrimary": 24, "clinicPrimary": 36, "labOrders": 12},
            cash=True,
        )

        with patch.object(attention, "_clinic_today", return_value=self.today):
            summary = attention.build_summary(self.conn)

        rows = {row["key"]: row for row in summary["primary_details"]}

        self.assertEqual(rows["total"]["average"], 45.0)
        self.assertEqual(rows["dentistry"]["average"], 17.5)
        self.assertEqual(rows["structure"]["average"], 27.5)
        self.assertEqual(rows["lab_orders"]["average"], 9.0)
        self.assertEqual(rows["total"]["comparison_months"], 2)
        self.assertIn("выше среднего", rows["total"]["comment"].lower())

        # Forecast uses average historical growth from comparable snapshot to final snapshot:
        # July 50/40=1.25; August 60/50=1.2; average multiplier=1.225.
        # Current 60 * 1.225 = 73.5 -> round() = 74.
        self.assertEqual(rows["total"]["forecast"], 74)
        self.assertEqual(rows["total"]["forecast_months"], 2)
        self.assertEqual(rows["dentistry"]["forecast"], 29)
        self.assertEqual(rows["structure"]["forecast"], 45)
        self.assertEqual(rows["lab_orders"]["forecast"], 15)
        self.assertEqual(summary["primary"]["forecast_total"], 74)

    def test_primary_details_require_two_comparable_months(self):
        self.add_day("2026-08-01", {"primary": 20, "dentPrimary": 8, "clinicPrimary": 12, "labOrders": 4})
        self.add_day("2026-08-02", {"primary": 40, "dentPrimary": 16, "clinicPrimary": 24, "labOrders": 8})
        self.add_day("2026-09-26", {"primary": 30, "dentPrimary": 12, "clinicPrimary": 18, "labOrders": 6})
        self.add_day("2026-09-27", {"primary": 60, "dentPrimary": 24, "clinicPrimary": 36, "labOrders": 12})

        with patch.object(attention, "_clinic_today", return_value=self.today):
            summary = attention.build_summary(self.conn)

        rows = {row["key"]: row for row in summary["primary_details"]}
        self.assertIsNone(rows["total"]["average"])
        self.assertIsNone(rows["total"]["deviation"])
        self.assertEqual(rows["total"]["comment"], "Недостаточно данных для сравнения")
        self.assertIsNone(rows["total"]["forecast"])
        self.assertIsNone(summary["primary"]["forecast_total"])

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

    def test_plan_fact_and_due_values_use_bold_spans(self):
        html = attention.home_modal_fragment(7)
        self.assertIn('az-attention-value-strong', html)
        self.assertIn('Факт месяца — <span class="az-attention-value-strong">', html)
        self.assertIn('Должно быть — <span class="az-attention-value-strong">', html)

    def test_mobile_plan_values_are_split_into_two_lines(self):
        html = attention.home_modal_fragment(7)
        self.assertIn('az-attention-plan-line', html)
        self.assertIn('.az-attention-plan-line{display:block}', html)
        self.assertIn('Должно быть — <span class="az-attention-value-strong">', html)

    def test_attention_page_typography_has_desktop_and_mobile_scales(self):
        from pathlib import Path
        template = (Path(__file__).resolve().parent / "templates" / "attention.html").read_text(encoding="utf-8")
        self.assertIn(".attention-row strong{font-size:17px", template)
        self.assertIn(".attention-primary-forecast{margin-top:9px;font-size:18px", template)
        self.assertIn("@media(max-width:700px)", template)
        self.assertIn(".attention-row strong{font-size:16.5px", template)
        self.assertIn(".attention-primary-grid{grid-template-columns:1fr", template)

    def test_home_modal_has_no_navigation_actions_and_is_mobile_adaptive(self):
        html = attention.home_modal_fragment(7)
        self.assertNotIn('href="/reports/', html)
        self.assertNotIn("Открыть", html)
        self.assertIn("@media(max-width:700px)", html)
        self.assertIn("az-attention-close", html)
        self.assertIn("az-attention-seen:7:", html)


if __name__ == "__main__":
    unittest.main()
