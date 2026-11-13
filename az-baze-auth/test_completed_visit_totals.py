"""AZ-204 regression: completed counts must agree across every read/write route.

No live DB, identifiable patients, API requests, or Production writes.
"""
import json
import os
import sqlite3
import unittest
from unittest.mock import patch
from pathlib import Path
from flask import Flask, g

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
            self.assertEqual(updated, 2)  # update Nov 12 + restore missing Nov 03
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
            projection = storage._canonical_completed_months(c)
            self.assertEqual(projection["2026-09-06"], (1, 7))
            self.assertEqual(projection["2026-09-07"], (2, 9))
            with patch.object(upload, "db", return_value=c):
                records = upload._rebuild_management(
                    "2026-09", "isolated historical regeneration"
                )
            self.assertEqual(records["2026-09-07"]["primary"], 2)
            self.assertEqual(records["2026-09-07"]["repeat"], 9)
            self.assertEqual(records["2026-09-07"]["factMedicine"], 150000)
            # A stale manual value on 07 September must be corrected, not
            # skipped just because the source starts after a legacy baseline.
            c.execute("INSERT INTO report_data VALUES(?,?,?,?)", (
                "2026-09-07",
                json.dumps({"date":"2026-09-07", "primary":1, "repeat":2,
                            "cashTotal":7000, "plan":20000}),
                1, "OLD",
            ))
            self.assertEqual(
                storage._reapply_completed_after_restore(c, 2, "NEW"), 1
            )
            repaired=json.loads(c.execute(
                "SELECT payload FROM report_data WHERE date='2026-09-07'"
            ).fetchone()["payload"])
            self.assertEqual((repaired["primary"], repaired["repeat"]), (2, 9))
            self.assertEqual(repaired["cashTotal"], 7000)
            self.assertEqual(repaired["plan"], 20000)
        finally:
            c.close()

    def test_real_management_get_and_put_reject_stale_daily_snapshot(self):
        # Exercise the actual view functions, not only pure projections.
        # Permission / CSRF wrappers are separately protected by their
        # own suite; this test checks the completed-count business contract.
        c = conn()
        try:
            insert_day(c, "2026-11-03", primary=40, repeat=167)
            insert_day(c, "2026-11-12", primary=2, repeat=22)
            stale = {
                "date": "2026-11-12", "primary": 2, "repeat": 22,
                "cashTotal": 3583126, "factMedicine": 120000,
                "paidPatients": 149,
            }
            c.execute("INSERT INTO report_data VALUES(?,?,?,?)", (
                "2026-11-12", json.dumps(stale), 1, "OLD",
            ))
            c.commit()

            class FakeApp:
                def __init__(self):
                    self.routes = {}
                def before_request(self, fn):
                    return fn
                def __getattr__(self, method):
                    def registrar(path, *args, **kwargs):
                        def decorator(fn):
                            self.routes[(method.upper(), path)] = fn
                            return fn
                        return decorator
                    return registrar

            def raw(fn):
                while hasattr(fn, "__wrapped__"):
                    fn = fn.__wrapped__
                return fn

            fake = FakeApp()
            with patch.object(storage, "_ensure_schema"), \
                 patch.object(storage.advertising_report, "register_advertising_report"), \
                 patch.object(storage, "db", return_value=c), \
                 patch.object(storage, "management_monthly_plans", return_value={}), \
                 patch.object(storage, "_paid_patient_intervals", return_value=[]), \
                 patch.object(storage, "_management_paid_patients_by_date", return_value={}), \
                 patch.object(storage, "_management_doctor_paid_snapshots", return_value={}), \
                 patch.object(storage, "_management_doctor_paid_periods", return_value=[]), \
                 patch.object(storage, "_management_paid_patients_monthly", return_value={}), \
                 patch.object(storage, "_load_management_staff_layout",
                              return_value=({"version": 1, "directions": {}}, None)), \
                 patch.object(storage, "_current_user_is_admin", return_value=False), \
                 patch.object(storage, "csrf_token", return_value="SYNTHETIC_CSRF"), \
                 patch.object(storage, "_require_api_csrf"), \
                 patch.object(storage, "audit"):
                storage.register_report_storage(fake)
                app = Flask("AZ204_ISOLATED_TEST")
                with app.test_request_context("/api/reports/data"):
                    g.user = {"id": 1}
                    response = raw(fake.routes[
                        ("GET", "/api/reports/data")
                    ])()
                    data = response.get_json()["data"]["2026-11-12"]
                    self.assertEqual((data["primary"], data["repeat"]), (42, 189))
                    self.assertEqual(data["cashTotal"], 3583126)
                    # Read correction never writes the live source or record.
                    stored = json.loads(c.execute(
                        "SELECT payload FROM report_data WHERE date='2026-11-12'"
                    ).fetchone()["payload"])
                    self.assertEqual((stored["primary"], stored["repeat"]), (2, 22))
                with app.test_request_context(
                    "/api/reports/data/2026-11-12",
                    method="PUT",
                    json={
                        "primary": 2, "repeat": 22,
                        "cashTotal": 3583126,
                        "paidPatients": 149,
                    },
                ):
                    g.user = {"id": 1}
                    response = raw(fake.routes[
                        ("PUT", "/api/reports/data/<date>")
                    ])("2026-11-12")
                    data = response.get_json()["record"]
                    self.assertEqual((data["primary"], data["repeat"]), (42, 189))
                    self.assertEqual(data["cashTotal"], 3583126)
                stored = json.loads(c.execute(
                    "SELECT payload FROM report_data WHERE date='2026-11-12'"
                ).fetchone()["payload"])
                self.assertEqual((stored["primary"], stored["repeat"]), (42, 189))
        finally:
            c.close()

    def test_old_backup_cannot_erase_nov_13_confirmed_44_211(self):
        c = conn()
        try:
            insert_day(c, "2026-11-03", primary=40, repeat=167)
            insert_day(c, "2026-11-12", primary=2, repeat=22)
            insert_day(c, "2026-11-13", primary=2, repeat=22)
            live_12 = {"date":"2026-11-12", "primary":2, "repeat":22,
                       "cashTotal":3500000, "paidPatients":149}
            live_13 = {"date":"2026-11-13", "primary":2, "repeat":22,
                       "cashTotal":3583126, "clinicDocs":{"Doctor":17000}}
            for day, payload in (("2026-11-12", live_12),
                                 ("2026-11-13", live_13)):
                c.execute("INSERT INTO report_data VALUES(?,?,?,?)",
                          (day, json.dumps(payload), 1, "LIVE"))
            c.commit()
            # The live source and newer clinical/finance snapshots must
            # survive restoring an old backup containing only 12 November.
            before = storage._canonical_completed_months(c)
            records = storage._preserved_completed_records(c, before)
            self.assertEqual(before["2026-11-12"], (42, 189))
            self.assertEqual(before["2026-11-13"], (44, 211))
            with c:
                c.execute("DELETE FROM report_data")
                c.execute("INSERT INTO report_data VALUES(?,?,?,?)", (
                    "2026-11-12",
                    json.dumps({"date":"2026-11-12",
                                "primary":2,"repeat":22,
                                "cashTotal":3500000}),
                    1, "BACKUP"))
                changes = storage._reapply_completed_after_restore(
                    c, 2, "RESTORED",
                    confirmed_before=before, preserved_records=records,
                )
            self.assertGreaterEqual(changes, 2)
            dates = [row["date"] for row in c.execute(
                "SELECT date FROM report_data ORDER BY date"
            ).fetchall()]
            self.assertIn("2026-11-12", dates)
            self.assertIn("2026-11-13", dates)
            reloaded = {
                row["date"]: json.loads(row["payload"])
                for row in c.execute("SELECT date,payload FROM report_data")
            }
            self.assertEqual(
                (reloaded["2026-11-12"]["primary"],
                 reloaded["2026-11-12"]["repeat"]), (42, 189)
            )
            self.assertEqual(
                (reloaded["2026-11-13"]["primary"],
                 reloaded["2026-11-13"]["repeat"]), (44, 211)
            )
            self.assertEqual(reloaded["2026-11-12"]["cashTotal"], 3500000)
            self.assertEqual(reloaded["2026-11-13"]["cashTotal"], 3583126)
            self.assertEqual(
                reloaded["2026-11-13"]["clinicDocs"], {"Doctor":17000}
            )
        finally:
            c.close()

    def test_missing_backup_and_live_date_creates_only_completed_facts(self):
        c = conn()
        try:
            insert_day(c, "2026-11-12", primary=42, repeat=189)
            insert_day(c, "2026-11-13", primary=2, repeat=22)
            current = {"date":"2026-11-12", "primary":42,
                       "repeat":189, "cashTotal":3500000}
            c.execute("INSERT INTO report_data VALUES(?,?,?,?)", (
                "2026-11-12", json.dumps(current), 1, "LIVE",
            ))
            before = storage._canonical_completed_months(c)
            old_records = storage._preserved_completed_records(c, before)
            with c:
                c.execute("DELETE FROM report_data")
                c.execute("INSERT INTO report_data VALUES(?,?,?,?)", (
                    "2026-11-12", json.dumps(current), 1, "BACKUP",
                ))
                storage._reapply_completed_after_restore(
                    c, 2, "RESTORED",
                    confirmed_before=before, preserved_records=old_records,
                )
            future = json.loads(c.execute(
                "SELECT payload FROM report_data WHERE date='2026-11-13'"
            ).fetchone()["payload"])
            self.assertEqual((future["primary"], future["repeat"]),
                             (44, 211))
            self.assertEqual(future["_completed_only"], True)
            for field in (
                "cashTotal", "cashOOO", "cashIP", "factMedicine",
                "factLab", "paidPatients", "plan", "dentists", "clinicDocs",
            ):
                self.assertNotIn(field, future)
            self.assertEqual(future["_source"],
                             "verified_completed_visits_restore")
        finally:
            c.close()

    def test_historical_legacy_baseline_survives_replace_all_backup(self):
        c = conn()
        try:
            baseline = {"date":"2026-09-06", "primary":1,"repeat":7,
                        "cashTotal":100000}
            insert_day(c, "2026-09-07", primary=1, repeat=2)
            c.execute("INSERT INTO report_data VALUES(?,?,?,?)", (
                "2026-09-06", json.dumps(baseline), 1, "LIVE",
            ))
            before = storage._canonical_completed_months(c)
            previous = storage._preserved_completed_records(c, before)
            with c:
                c.execute("DELETE FROM report_data")
                c.execute("INSERT INTO report_data VALUES(?,?,?,?)", (
                    "2026-09-07",
                    json.dumps({"date":"2026-09-07",
                                "primary":1,"repeat":2,
                                "cashTotal":7000}),
                    1, "BACKUP",
                ))
                storage._reapply_completed_after_restore(
                    c, 2, "RESTORED",
                    confirmed_before=before, preserved_records=previous,
                )
            values = {
                row["date"]: json.loads(row["payload"])
                for row in c.execute("SELECT date,payload FROM report_data")
            }
            self.assertEqual((values["2026-09-06"]["primary"],
                              values["2026-09-06"]["repeat"]), (1, 7))
            self.assertEqual((values["2026-09-07"]["primary"],
                              values["2026-09-07"]["repeat"]), (2, 9))
            self.assertEqual(values["2026-09-06"]["cashTotal"],100000)
            self.assertEqual(values["2026-09-07"]["cashTotal"],7000)
            self.assertEqual(
                storage._canonical_completed_months(c)["2026-09-07"],
                (2,9),
            )
        finally:
            c.close()

    def test_both_restore_endpoints_preserve_newer_verified_date(self):
        # Regression for both destructive backup APIs, not just the helper:
        # a saved backup through 12 Nov must not erase source-backed 13 Nov.
        for endpoint in ("/api/reports/import", "/api/reports/import-all"):
            with self.subTest(endpoint=endpoint):
                c = conn()
                try:
                    c.execute(
                        "CREATE TABLE report_blobs("
                        "key TEXT PRIMARY KEY,payload TEXT NOT NULL,"
                        "updated_by INTEGER,updated_at TEXT)"
                    )
                    insert_day(c, "2026-11-12", primary=42, repeat=189)
                    insert_day(c, "2026-11-13", primary=2, repeat=22)
                    c.execute("INSERT INTO report_data VALUES(?,?,?,?)", (
                        "2026-11-13",
                        json.dumps({
                            "date":"2026-11-13","primary":2,"repeat":22,
                            "cashTotal":3583126,"paidPatients":149,
                        }),
                        1, "LIVE",
                    ))
                    c.commit()

                    class FakeApp:
                        def __init__(self):
                            self.routes = {}
                        def __getattr__(self, method):
                            def register(path, *args, **kwargs):
                                def decorator(fn):
                                    self.routes[(method.upper(), path)] = fn
                                    return fn
                                return decorator
                            return register

                    def raw(fn):
                        while hasattr(fn, "__wrapped__"):
                            fn = fn.__wrapped__
                        return fn

                    app_routes = FakeApp()
                    backup_record = {
                        "date": "2026-11-12", "primary": 2,
                        "repeat": 22, "cashTotal": 3500000,
                    }
                    if endpoint == "/api/reports/import":
                        body = {"data":{"2026-11-12":backup_record}}
                    else:
                        body = {
                            "version": 1,
                            "storage": {
                                storage.MANAGEMENT_KEY: json.dumps({
                                    "2026-11-12":backup_record
                                }),
                                "az-management-monthly-plan-v1": json.dumps({
                                    "version": 1, "months": {},
                                }),
                            },
                        }

                    with patch.object(storage, "_ensure_schema"), \
                         patch.object(storage.advertising_report,
                                      "register_advertising_report"), \
                         patch.object(storage, "db", return_value=c), \
                         patch.object(storage.cash_payments, "init_schema"), \
                         patch.object(storage,
                                      "_reapply_cash_after_restore",return_value=0), \
                         patch.object(storage,
                                      "_reapply_paid_after_restore",return_value=0), \
                         patch.object(storage,"_require_api_csrf"), \
                         patch.object(storage,"audit"):
                        storage.register_report_storage(app_routes)
                        webapp = Flask("AZ204_RESTORE_TEST")
                        with webapp.test_request_context(
                            endpoint, method="POST", json=body
                        ):
                            g.user = {"id":1}
                            result = raw(
                                app_routes.routes[("POST",endpoint)]
                            )()
                            self.assertTrue(result.get_json()["ok"])

                    stored = {
                        row["date"]: json.loads(row["payload"])
                        for row in c.execute(
                            "SELECT date,payload FROM report_data"
                        ).fetchall()
                    }
                    self.assertEqual(
                        (stored["2026-11-12"]["primary"],
                         stored["2026-11-12"]["repeat"]), (42,189)
                    )
                    self.assertEqual(
                        (stored["2026-11-13"]["primary"],
                         stored["2026-11-13"]["repeat"]), (44,211)
                    )
                    self.assertEqual(stored["2026-11-12"]["cashTotal"],3500000)
                    self.assertEqual(stored["2026-11-13"]["cashTotal"],3583126)
                    self.assertEqual(stored["2026-11-13"]["paidPatients"],149)
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
        self.assertIn("def _reapply_completed_after_restore(", src)
        self.assertIn("confirmed_before=completed_before", src)
        self.assertIn("preserved_records=completed_records", src)
        self.assertEqual(
            src.count("confirmed_before=completed_before"),
            2,
        )
        self.assertIn("if date in _canonical_completed_months(db()):", src)


if __name__ == "__main__":
    unittest.main()
