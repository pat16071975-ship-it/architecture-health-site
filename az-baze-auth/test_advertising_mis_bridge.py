import importlib.util
import json
import sqlite3
import unittest
from pathlib import Path

MODULE = Path(__file__).with_name("advertising_mis_bridge.py")
spec = importlib.util.spec_from_file_location("advertising_mis_bridge", MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE daily_uploads("
        "data_date TEXT PRIMARY KEY, normalized_json TEXT NOT NULL, revision INTEGER NOT NULL)"
    )
    return c


def add_day(c, day, visits):
    c.execute(
        "INSERT INTO daily_uploads(data_date,normalized_json,revision) VALUES(?,?,1)",
        (day, json.dumps({"visits": visits}, ensure_ascii=False)),
    )


def item(patient, staff, invoice, service_date, amount, paid, service="Приём"):
    return {
        "patient": patient,
        "staff": staff,
        "invoice": invoice,
        "date": service_date,
        "group": "Приёмы",
        "service": service,
        "qty": 1,
        "amount": amount,
        "paid_amount": paid,
        "closing_debt": max(0, amount - paid),
    }


def report(start, end, items, paid_total=None):
    if paid_total is None:
        paid_total = sum(row["paid_amount"] for row in items)
    return {
        "period_start": start,
        "period_end": end,
        "totals": {"paid": paid_total},
        "items": items,
    }


class AdvertisingMisBridgeTests(unittest.TestCase):
    def test_primary_repeat_payments_and_new_patient_cohort(self):
        c = conn()
        try:
            add_day(
                c,
                "2026-10-10",
                [{"date": "2026-10-10", "patient": "Сидоров С. С.", "kind": "Первичные"}],
            )
            add_day(
                c,
                "2026-10-27",
                [
                    {"date": "2026-10-27", "patient": "Иванов И. И.", "kind": "Первичные"},
                    {"date": "2026-10-27", "patient": "Сидоров С. С.", "kind": "Первичные"},
                ],
            )
            add_day(
                c,
                "2026-10-28",
                [{"date": "2026-10-28", "patient": "Иванов И. И.", "kind": "Повторные"}],
            )

            day27 = report(
                "2026-10-27",
                "2026-10-27",
                [
                    item("Иванов И. И.", "Доктор А. А.", "1", "2026-10-27", 1000, 1000),
                    item("Сидоров С. С.", "Доктор Б. Б.", "2", "2026-10-27", 500, 500),
                ],
            )
            captured = m.capture_paid_report(c, day27, "27.xlsx", "sha27", "t27")
            self.assertEqual(captured["unmapped_paid"], 0)
            first = m.rebuild(c, "t27")
            self.assertEqual(first["payment_evidence_dates"], 1)

            row = c.execute(
                "SELECT primary_visits,primary_paid,primary_services,repeat_visits,repeat_paid "
                "FROM advertising_clinic_daily WHERE provider='mis' AND metric_date='2026-10-27'"
            ).fetchone()
            self.assertEqual(tuple(row), (2, 1500, 1500, 0, 0))

            cohort = c.execute(
                "SELECT new_patients,new_patient_30d_paid "
                "FROM advertising_cohort_daily WHERE provider='mis' AND cohort_date='2026-10-27'"
            ).fetchone()
            self.assertEqual(tuple(cohort), (1, 1000))

            day28 = report(
                "2026-10-27",
                "2026-10-28",
                [
                    item("Иванов И. И.", "Доктор А. А.", "1", "2026-10-27", 1000, 1300),
                    item("Сидоров С. С.", "Доктор Б. Б.", "2", "2026-10-27", 500, 500),
                    item("Иванов И. И.", "Доктор А. А.", "3", "2026-10-28", 700, 700, "Повтор"),
                ],
                paid_total=2500,
            )
            m.capture_paid_report(c, day28, "28.xlsx", "sha28", "t28")
            second = m.rebuild(c, "t28")
            self.assertEqual(second["payment_evidence_dates"], 2)

            row = c.execute(
                "SELECT primary_visits,primary_paid,repeat_visits,repeat_paid "
                "FROM advertising_clinic_daily WHERE provider='mis' AND metric_date='2026-10-28'"
            ).fetchone()
            self.assertEqual(tuple(row), (0, 300, 1, 700))

            cohort = c.execute(
                "SELECT new_patients,new_patient_30d_paid "
                "FROM advertising_cohort_daily WHERE provider='mis' AND cohort_date='2026-10-27'"
            ).fetchone()
            self.assertEqual(tuple(cohort), (1, 2000))
        finally:
            c.close()

    def test_exact_day_does_not_hide_cumulative_delta_for_other_line(self):
        c = conn()
        try:
            add_day(
                c,
                "2026-10-27",
                [{"date": "2026-10-27", "patient": "Иванов И. И.", "kind": "Первичные"}],
            )
            add_day(
                c,
                "2026-10-28",
                [{"date": "2026-10-28", "patient": "Петров П. П.", "kind": "Повторные"}],
            )
            m.capture_paid_report(
                c,
                report(
                    "2026-10-27",
                    "2026-10-27",
                    [item("Иванов И. И.", "Доктор А. А.", "1", "2026-10-27", 1000, 1000)],
                ),
                "27.xlsx",
                "sha27",
                "t27",
            )
            m.capture_paid_report(
                c,
                report(
                    "2026-10-27",
                    "2026-10-28",
                    [
                        item("Иванов И. И.", "Доктор А. А.", "1", "2026-10-27", 1000, 1300),
                        item("Петров П. П.", "Доктор Б. Б.", "2", "2026-10-28", 700, 700),
                    ],
                    paid_total=2000,
                ),
                "27-28.xlsx",
                "sha2728",
                "t2728",
            )
            m.capture_paid_report(
                c,
                report(
                    "2026-10-28",
                    "2026-10-28",
                    [item("Петров П. П.", "Доктор Б. Б.", "2", "2026-10-28", 700, 700)],
                ),
                "28.xlsx",
                "sha28",
                "t28",
            )

            events = m._payment_candidates(c)["2026-10-28"]["events"]
            amounts = {
                (row["patient_token"], row["service_date"]): row["paid_delta"]
                for row in events
            }
            self.assertEqual(
                amounts[(m.patient_token("Иванов И. И."), "2026-10-27")],
                300,
            )
            self.assertEqual(
                amounts[(m.patient_token("Петров П. П."), "2026-10-28")],
                700,
            )
            self.assertEqual(sum(amounts.values()), 1000)
        finally:
            c.close()

    def test_multi_day_first_snapshot_does_not_invent_payment_date(self):
        c = conn()
        try:
            add_day(
                c,
                "2026-10-28",
                [{"date": "2026-10-28", "patient": "Иванов И. И.", "kind": "Первичные"}],
            )
            m.capture_paid_report(
                c,
                report(
                    "2026-10-27",
                    "2026-10-28",
                    [item("Иванов И. И.", "Доктор А. А.", "1", "2026-10-28", 1000, 1000)],
                ),
                "27-28.xlsx",
                "sha",
                "t",
            )
            result = m.rebuild(c, "t")
            self.assertEqual(result["payment_evidence_dates"], 0)
            row = c.execute(
                "SELECT primary_visits,primary_paid FROM advertising_clinic_daily "
                "WHERE provider='mis' AND metric_date='2026-10-28'"
            ).fetchone()
            self.assertEqual(row["primary_visits"], 1)
            self.assertIsNone(row["primary_paid"])
        finally:
            c.close()

    def test_report_before_tracking_start_is_ignored(self):
        c = conn()
        try:
            result = m.capture_paid_report(
                c,
                report(
                    "2026-10-01",
                    "2026-10-26",
                    [item("Иванов И. И.", "Доктор А. А.", "1", "2026-10-20", 1000, 1000)],
                ),
                "old.xlsx",
                "old",
                "t",
            )
            self.assertFalse(result["captured"])
            m.init_schema(c)
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM advertising_mis_paid_ingests").fetchone()[0],
                0,
            )
        finally:
            c.close()

    def test_unmapped_paid_is_explicit_not_assigned(self):
        c = conn()
        try:
            captured = m.capture_paid_report(
                c,
                report(
                    "2026-10-27",
                    "2026-10-27",
                    [item("Иванов И. И.", "Доктор А. А.", "1", "2026-10-27", 1000, 1000)],
                    paid_total=1200,
                ),
                "27.xlsx",
                "sha",
                "t",
            )
            self.assertEqual(captured["detailed_paid_total"], 1000)
            self.assertEqual(captured["unmapped_paid"], 200)
        finally:
            c.close()

    def test_rebuild_preserves_non_mis_advertising_rows(self):
        c = conn()
        try:
            m.init_schema(c)
            c.execute(
                "INSERT INTO advertising_clinic_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "crm",
                    "2026-10-27",
                    "crm",
                    "CRM",
                    "CRM",
                    "",
                    "",
                    5,
                    2,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "t",
                    "crm",
                ),
            )
            c.execute(
                "INSERT INTO advertising_cohort_daily VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("manual", "2026-10-27", "manual", "Manual", "", "", "", 7, 1234, "t", "manual"),
            )
            m.rebuild(c, "t2")
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM advertising_clinic_daily WHERE provider='crm'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM advertising_cohort_daily WHERE provider='manual'"
                ).fetchone()[0],
                1,
            )
        finally:
            c.close()

    def test_source_hooks_are_post_commit_best_effort(self):
        root = Path(__file__).parent
        paid = (root / "paid_services_upload.py").read_text(encoding="utf-8")
        daily = (root / "daily_upload.py").read_text(encoding="utf-8")
        completed = (root / "completed_upload.py").read_text(encoding="utf-8")
        self.assertIn("advertising_mis_bridge.capture_paid_report", paid)
        self.assertIn('advertising_sync = {"status": "error"', paid)
        self.assertLess(
            paid.index("status = paid_services.store_snapshot"),
            paid.index("advertising_mis_bridge.capture_paid_report"),
        )
        self.assertIn('details=f"source=clinical; error={type(exc).__name__}"', daily)
        self.assertIn('details=f"source=completed; error={type(exc).__name__}"', completed)


if __name__ == "__main__":
    unittest.main()
