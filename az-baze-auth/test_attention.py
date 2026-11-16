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
        self.conn.execute("CREATE TABLE daily_uploads(data_date TEXT PRIMARY KEY, normalized_json TEXT)")
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

    def add_completed(self, day, primary, repeat=0, dent=0, clinic=0):
        normalized = {
            "overall": {day: {"Первичные": primary, "Повторные": repeat}},
            "doctors": {day: {"Первичные": {
                "Чирков М. С.": dent, "Лапина Н. С.": clinic,
            }}},
        }
        self.conn.execute("UPDATE daily_uploads SET normalized_json=? WHERE data_date=?",
                          (json.dumps(normalized), day))

    def test_summary_canonical_counts_preserve_money_and_do_not_write(self):
        import completed_visit_totals as completed
        day = "2026-11-15"
        record = {"primary": 55, "repeat": 1, "dentPrimary": 1,
                  "clinicPrimary": 1, "cashTotal": 4102460, "plan": 10000000,
                  "dentists": {"D": 2768110}, "clinicDocs": {"C": 752340},
                  "labRevenue": 9500, "labOrders": 20}
        self.add_day(day, record)
        self.add_completed(day, 56, 258, 31, 24)
        attention.upload_reconcile.init_schema(self.conn)
        before = "\n".join(self.conn.iterdump())
        self.conn.execute("PRAGMA query_only=ON")
        with patch.object(attention, "_clinic_today", return_value=date(2026,11,16)), \
             patch.object(completed, "management_month_to_date",
                          wraps=completed.management_month_to_date) as read:
            summary = attention.build_summary(self.conn)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(summary["primary"]["total"], 56)
        self.assertEqual(summary["primary"]["dentistry"], 31)
        self.assertEqual(summary["primary"]["structure"], 24)
        self.assertEqual(summary["primary"]["lab_orders"], 20)
        expected = dict(record)
        expected.update(completed.management_month_to_date(self.conn, "2026-11")[day])
        expected["date"] = day
        self.assertEqual(attention._report_for_date(self.conn, day), expected)
        self.assertEqual("\n".join(self.conn.iterdump()), before)

    def test_history_comparison_and_forecast_use_corrected_counts(self):
        for month in (9, 10):
            for suffix in ("15", "30"):
                day = f"2026-{month:02d}-{suffix}"
                self.add_day(day, {"primary": 999, "repeat": 999,
                                  "dentPrimary": 999, "clinicPrimary": 999})
                self.add_completed(day, 10, dent=4, clinic=5)
        day = "2026-11-15"
        self.add_day(day, {"primary": 55, "repeat": 1})
        self.add_completed(day, 56, dent=31, clinic=24)
        with patch.object(attention, "_clinic_today", return_value=date(2026,11,16)):
            summary = attention.build_summary(self.conn)
        self.assertEqual(summary["primary"]["forecast_total"], 112)
        cache = {}
        averages = attention._primary_comparable_averages(
            self.conn, date(2026,11,16), day, cache)
        self.assertEqual(averages["total"]["average"], 10)
        self.assertEqual(averages["dentistry"]["average"], 4)
        multipliers = attention._primary_forecast_multipliers(
            self.conn, date(2026,11,16), day, cache)
        self.assertEqual(multipliers["total"]["multiplier"], 2)
        self.assertEqual(multipliers["total"]["months"], 2)

    def test_fallback_uses_actual_snapshot_not_requested_later_day(self):
        self.add_day("2026-11-10", {"primary": 999, "repeat": 999})
        self.add_completed("2026-11-10", 4, 7)
        self.conn.execute("INSERT INTO daily_uploads(data_date) VALUES('2026-11-12')")
        self.add_completed("2026-11-12", 2, 3)
        row = attention._report_for_date(self.conn, "2026-11-12")
        self.assertEqual(row["date"], "2026-11-10")
        self.assertEqual((row["primary"], row["repeat"]), (4, 7))
        self.assertEqual(attention._report_for_date(self.conn, "2026-10-31"), {})

    def test_legacy_baseline_and_service_only_snapshot_are_preserved(self):
        legacy = {"primary": 40, "repeat": 100, "dentPrimary": 20,
                  "dentRepeat": 60, "clinicPrimary": 19, "clinicRepeat": 35}
        self.add_report_snapshot("2026-11-09", legacy)
        self.add_day("2026-11-10", {"primary": 999, "repeat": 999})
        self.add_completed("2026-11-10", 4, 7, dent=2, clinic=1)
        row = attention._report_for_date(self.conn, "2026-11-10")
        self.assertEqual((row["primary"], row["repeat"]), (44,107))
        self.assertEqual((row["dentPrimary"], row["clinicPrimary"]), (22,20))
        service = {"primary": 44, "repeat": 107, "cashTotal": 123}
        self.add_day("2026-11-11", service)
        self.assertEqual(attention._report_for_date(self.conn, "2026-11-11"), service)

    def test_explicit_zero_replaces_stale_counter(self):
        self.add_day("2026-11-01", {"primary": 55, "repeat": 99})
        self.add_completed("2026-11-01", 0)
        row = attention._report_for_date(self.conn, "2026-11-01")
        self.assertEqual((row["primary"], row["repeat"]), (0,0))

    def test_summary_uses_comparable_loaded_days_and_current_snapshot(self):
        # August: three comparable loaded days.
        self.add_day("2026-08-01", {"paidDataComplete": True, "paidDentists": {"D": 100}, "paidClinicDocs": {"C": 200}, "paidLab": 50})
        self.add_day("2026-08-02", {"paidDataComplete": True, "paidDentists": {"D": 200}, "paidClinicDocs": {"C": 400}, "paidLab": 100})
        self.add_day("2026-08-03", {"paidDataComplete": True, "paidDentists": {"D": 300}, "paidClinicDocs": {"C": 600}, "paidLab": 150})

        # September: two loaded days, latest is current snapshot.
        self.add_day("2026-09-26", {"paidDataComplete": True, "paidDentists": {"D": 150}, "paidClinicDocs": {"C": 210}, "paidLab": 55})
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
                "paidDataComplete": True,
                "paidDentists": {"D": 240},
                "paidClinicDocs": {"C": 500},
                "paidLab": 120,
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

    def test_current_direction_values_keep_approved_clinical_source_when_paid_snapshot_exists(self):
        self.add_day(
            "2026-09-27",
            {
                "plan": 3100,
                "cashTotal": 2700,
                "cashUnallocated": 1000,
                "primary": 50,
                "dentPrimary": 20,
                "clinicPrimary": 30,
                "labOrders": 10,
                "dentists": {"D": 240},
                "clinicDocs": {"C": 500},
                "labRevenue": 120,
                "paidDataComplete": True,
                "paidDentists": {"D": 900},
                "paidClinicDocs": {"C": 700},
                "paidLab": 180,
            },
            cash=True,
        )
        with patch.object(attention, "_clinic_today", return_value=self.today):
            summary = attention.build_summary(self.conn)

        rows = {row["key"]: row for row in summary["revenue"]}
        self.assertEqual(rows["dentistry"]["amount"], 240)
        self.assertEqual(rows["structure"]["amount"], 500)
        self.assertEqual(rows["lab"]["amount"], 120)
        self.assertNotIn("unallocated", rows)

    def test_revenue_comparison_uses_report_data_history_without_old_daily_uploads(self):
        self.add_report_snapshot(
            "2026-07-28",
            {"dentists": {"D": 400}, "clinicDocs": {"C": 200}, "labRevenue": 100,
             "paidDataComplete": True, "paidDentists": {"D": 900}, "paidClinicDocs": {"C": 800}, "paidLab": 700},
        )
        self.add_report_snapshot(
            "2026-08-28",
            {"dentists": {"D": 500}, "clinicDocs": {"C": 300}, "labRevenue": 150,
             "paidDataComplete": True, "paidDentists": {"D": 950}, "paidClinicDocs": {"C": 850}, "paidLab": 750},
        )

        self.add_day("2026-09-27", {"dentists": {"D": 550}, "clinicDocs": {"C": 250}, "labRevenue": 125,
                                         "paidDataComplete": True, "paidDentists": {"D": 980}, "paidClinicDocs": {"C": 880}, "paidLab": 780})
        self.add_day(
            "2026-09-28",
            {"dentists": {"D": 600}, "clinicDocs": {"C": 350}, "labRevenue": 200,
             "paidDataComplete": True, "paidDentists": {"D": 990}, "paidClinicDocs": {"C": 890}, "paidLab": 790},
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
