"""AZ-204 regression: completed counts must agree across every read/write route.

No live DB, identifiable patients, API requests, or Production writes.
"""
import json
import os
import sqlite3
import unittest
from pathlib import Path

os.environ.setdefault("AZBAZE_SECRET_KEY", "AZ204_ISOLATED_TEST_KEY_NO_PRODUCTION")

import completed_visit_totals as completed
import advertising_mis_bridge as bridge
import daily_upload_core as upload
import report_storage as storage


def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE daily_uploads("
        "data_date TEXT PRIMARY KEY, normalized_json TEXT NOT NULL)"
    )
    c.execute(
        "CREATE TABLE report_data("
        "date TEXT PRIMARY KEY,payload TEXT NOT NULL,"
        "updated_by INTEGER,updated_at TEXT)"
    )
    return c


def insert_day(c, day, primary=None, repeat=None, consulted=None, visits=None):
    total = {}
    if primary is not None:
        total["Первичные"] = primary
    if repeat is not None:
        total["Повторные"] = repeat
    if consulted is not None:
        total["Отконсультированные"] = consulted
    normalized = {
        "data_date": day,
        "overall": {day: total},
        "visits": visits or [],
        "items": [],
        "doctors": {},
        "lab_invoices": [],
    }
    c.execute(
        "INSERT INTO daily_uploads VALUES(?,?)",
        (day, json.dumps(normalized, ensure_ascii=False)),
    )


class CompletedMonthToDateContract(unittest.TestCase):
    def test_day_12_and_13_are_cumulative_not_last_day_only(self):
        c = conn()
        try:
            insert_day(c, "2026-11-03", primary=40, repeat=160, consulted=7)
            insert_day(c, "2026-11-12", primary=2, repeat=20, consulted=2)
            insert_day(c, "2026-11-13", primary=2, repeat=20, consulted=2)
            series = completed.month_to_date(c, "2026-11")
            self.assertEqual(series["2026-11-12"], (42, 189))
            self.assertEqual(series["2026-11-13"], (44, 211))
            stale = {
                "date": "2026-11-12",
                "primary": 2, "repeat": 22,
                "cashTotal": 3583126,
                "paidPatients": 149,
                "plan": 20000000,
            }
            restored = completed.overlay_counts(
                stale, "2026-11-12", series
            )
            self.assertEqual((restored["primary"], restored["repeat"]), (42, 189))
            self.assertEqual(restored["cashTotal"], 3583126)
            self.assertEqual(restored["paidPatients"], 149)
            self.assertEqual(restored["plan"], 20000000)
            self.assertEqual(stale["primary"], 2)  # no mutation
        finally:
            c.close()

    def test_services_only_row_does_not_create_false_zero_completed_day(self):
        c = conn()
        try:
            insert_day(c, "2026-11-03", primary=2, repeat=5)
            insert_day(c, "2026-11-04")  # services-only {date:{}}, no visits
            self.assertEqual(
                completed.month_to_date(c, "2026-11"),
                {"2026-11-03": (2, 5)},
            )
            self.assertIsNone(completed.day_counts(
                {"data_date": "2026-11-04", "overall": {"2026-11-04": {}},
                 "visits": []}, "2026-11-04"
            ))
        finally:
            c.close()

    def test_explicit_zero_and_raw_repeats_consulted(self):
        c = conn()
        try:
            insert_day(c, "2026-11-01", primary=0, repeat=0, consulted=0)
            visits = [
                {"date": "2026-11-02", "patient": "Patient A", "kind": "Первичные"},
                {"date": "2026-11-02", "patient": "Patient B", "kind": "Повторные"},
                {"date": "2026-11-02", "patient": "Patient C", "kind": "Отконсультированные"},
            ]
            insert_day(c, "2026-11-02", visits=visits)
            self.assertEqual(completed.month_to_date(c, "2026-11"), {
                "2026-11-01": (0, 0), "2026-11-02": (1, 2)
            })
        finally:
            c.close()

    def test_overall_count_keeps_unassigned_doctor_visits(self):
        normalized = {
            "data_date": "2026-11-12",
            "overall": {"2026-11-12": {
                "Первичные": 3, "Повторные": 6, "Отконсультированные": 1,
            }},
            "visits": [
                {"date":"2026-11-12","kind":"Первичные",
                 "patient":"Patient A"},
            ],
        }
        self.assertEqual(completed.day_counts(normalized, "2026-11-12"), (3, 7))
        delta = upload._daily_delta({
            **normalized, "items":[], "doctors":{}, "lab_invoices":[],
        })
        self.assertEqual(delta["primary"], 3)
        self.assertEqual(delta["repeat"], 7)
        self.assertEqual(
            delta["dentPrimary"] + delta["clinicPrimary"], 0
        )  # never guess provider attribution

    def test_advertising_mis_and_management_same_day_totals(self):
        c = conn()
        try:
            bridge.init_schema(c)
            insert_day(c, "2026-11-03", primary=40, repeat=160, consulted=7)
            insert_day(c, "2026-11-12", primary=2, repeat=20, consulted=2)
            insert_day(c, "2026-11-13", primary=2, repeat=20, consulted=2)
            confirmed = completed.month_to_date(c, "2026-11")
            rebuilt = bridge.rebuild(c, "TEST_TIMESTAMP")
            self.assertEqual(rebuilt["clinic_rows"], 3)
            rows = c.execute(
                "SELECT metric_date,primary_visits,repeat_visits "
                "FROM advertising_clinic_daily "
                "WHERE provider='mis' ORDER BY metric_date"
            ).fetchall()
            daily = {row["metric_date"]:(row["primary_visits"],
                                        row["repeat_visits"]) for row in rows}
            self.assertEqual(daily["2026-11-03"], (40,167))
            self.assertEqual(daily["2026-11-12"], (2,22))
            self.assertEqual(daily["2026-11-13"], (2,22))
            running = [0, 0]
            for day in sorted(daily):
                running[0] += daily[day][0]
                running[1] += daily[day][1]
                self.assertEqual(tuple(running), confirmed[day])
        finally:
            c.close()

    def test_backup_restore_repairs_only_counters_without_touching_money(self):
        c = conn()
        try:
            insert_day(c, "2026-11-03", primary=40, repeat=167)
            insert_day(c, "2026-11-12", primary=2, repeat=22)
            before = {
                "date": "2026-11-12",
                "primary": 2, "repeat": 22,
                "factMedicine": 3583126,
                "cashTotal": 3583126,
                "dentists": {"Doctor": 99000},
                "marketingSources": {"source": 8},
                "paidPatients": 149,
            }
            c.execute("INSERT INTO report_data VALUES(?,?,?,?)",(
                "2026-11-12", json.dumps(before,ensure_ascii=False),
                1, "OLD",
            ))
            with c:
                updated = storage._reapply_completed_after_restore(c, 2, "NEW")
            self.assertEqual(updated, 1)
            row=c.execute(
                "SELECT payload FROM report_data WHERE date='2026-11-12'"
            ).fetchone()
            after=json.loads(row["payload"])
            self.assertEqual(after["primary"], 42)
            self.assertEqual(after["repeat"], 189)
            for name in ("factMedicine","cashTotal","dentists",
                         "marketingSources","paidPatients"):
                self.assertEqual(after[name], before[name])
        finally:
            c.close()

    def test_september_confirmed_mtd_baseline_is_not_lost(self):
        # Historical correction: 06 Sep has approved 1/7 MTD, but only
        # 07 Sep onward is represented in daily_uploads. Never overwrite
        # 06 Sep's approved baseline with 07 Sep's isolated delta.
        c = conn()
        try:
            baseline = {
                "date": "2026-09-06",
                "primary": 1, "repeat": 7,
                "cashTotal": 100000,
                "factMedicine": 150000,
            }
            c.execute("INSERT INTO report_data VALUES(?,?,?,?)",(
                "2026-09-06", json.dumps(baseline, ensure_ascii=False),
                1, "OLD",
            ))
            insert_day(c, "2026-09-07", primary=1, repeat=2)
            series = completed.month_to_date(c, "2026-09")
            self.assertEqual(series["2026-09-07"], (1, 2))
            self.assertTrue(completed.has_pre_source_legacy_counts(
                c, "2026-09", "2026-09-07"
            ))
            self.assertFalse(
                any(date.startswith("2026-09")
                    for date in storage._canonical_completed_months(c))
            )
            with unittest.mock.patch.object(upload, "db", return_value=c):
                records = upload._rebuild_management(
                    "2026-09", "isolated historical regeneration"
                )
            self.assertEqual(records["2026-09-07"]["primary"], 2)
            self.assertEqual(records["2026-09-07"]["repeat"], 9)
            self.assertEqual(records["2026-09-07"]["factMedicine"], 150000)
            self.assertEqual(
                storage._reapply_completed_after_restore(c, 2, "NEW"), 0
            )
        finally:
            c.close()

    def test_unconfirmed_dates_are_not_filled_with_estimates(self):
        c = conn()
        try:
            insert_day(c, "2026-11-03", primary=3, repeat=8)
            counts = completed.month_to_date(c, "2026-11")
            self.assertNotIn("2026-11-04", counts)
            record = {"primary": 99, "repeat": 99, "cashTotal": 500}
            self.assertIs(
                completed.overlay_counts(record, "2026-11-04", counts),
                record,
            )
        finally:
            c.close()

    def test_write_and_restore_routes_use_source_guard(self):
        src = (Path(__file__).parent / "report_storage.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("confirmed = _canonical_completed_months(db())", src)
        self.assertIn("record = completed_visit_totals.overlay_counts(", src)
        self.assertIn("_reapply_completed_after_restore(conn,", src)
        self.assertEqual(
            src.count("_reapply_completed_after_restore(conn, g.user"),
            2,
        )
        self.assertIn("if date in completed_visit_totals.month_to_date(", src)


if __name__ == "__main__":
    unittest.main()
