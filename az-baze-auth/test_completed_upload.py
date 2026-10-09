import unittest
import json
import sqlite3
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from contextlib import ExitStack
from unittest.mock import patch

from flask import Flask, g
import upload_integrity
import management_view
import finrez

import completed_upload


class CompletedUploadTests(unittest.TestCase):
    def test_compare_completed(self):
        existing = {
            "2026-09-01": {"completed_hash": "same", "has_completed": True},
            "2026-09-02": {"completed_hash": "old", "has_completed": True},
            "2026-09-03": {"completed_hash": "", "has_completed": False},
        }
        prepared = {
            "days": {
                "2026-09-01": {"completed_hash": "same"},
                "2026-09-02": {"completed_hash": "new"},
                "2026-09-04": {"completed_hash": "fresh"},
            }
        }
        compared = completed_upload.compare_completed(existing, prepared)
        self.assertEqual(compared["identical"], ["2026-09-01"])
        self.assertEqual(compared["conflict"], ["2026-09-02"])
        self.assertEqual(compared["new"], ["2026-09-04"])
        self.assertEqual(compared["removed"], [])

    def test_removed_completed_date_is_scoped_to_uploaded_range(self):
        existing = {
            "2026-09-01": {"completed_hash": "a", "has_completed": True},
            "2026-09-02": {"completed_hash": "b", "has_completed": True},
            "2026-09-03": {"completed_hash": "", "has_completed": False},
            "2026-09-20": {"completed_hash": "z", "has_completed": True},
        }
        prepared = {
            "days": {
                "2026-09-01": {"completed_hash": "a"},
                "2026-09-03": {"completed_hash": "c"},
            }
        }
        compared = completed_upload.compare_completed(existing, prepared)
        self.assertEqual(compared["removed"], ["2026-09-02"])
        self.assertNotIn("2026-09-20", compared["removed"])

    def test_merge_preserves_independent_service_payload(self):
        existing = {
            "data_date": "2026-09-16",
            "items": [{"service": "Service A", "amount": 100}],
            "retail_items": [{"service": "Retail"}],
            "lab_invoices": [["700", "2026-09-16"]],
            "doctors": {"2026-09-16": {"Primary": {"Old Doctor": 1}}},
            "overall": {"2026-09-16": {"Primary": 1}},
            "visits": [{"date": "2026-09-16", "patient": "Old A", "kind": "Primary"}],
        }
        day = {
            "data_date": "2026-09-16",
            "overall": {"2026-09-16": {"Primary": 2}},
            "visits": [{"date": "2026-09-16", "patient": "New A", "kind": "Primary"}],
        }
        merged = completed_upload._merge_normalized(existing, day=day)
        self.assertEqual(merged["items"], existing["items"])
        self.assertEqual(merged["retail_items"], existing["retail_items"])
        self.assertEqual(merged["lab_invoices"], existing["lab_invoices"])
        self.assertEqual(merged["doctors"], {})
        self.assertEqual(merged["overall"], day["overall"])
        self.assertEqual(merged["visits"], day["visits"])

    def test_removed_layer_preserves_service_payload(self):
        existing = {
            "data_date": "2026-09-16",
            "items": [{"service": "Service A"}],
            "retail_items": [],
            "lab_invoices": [],
            "doctors": {"2026-09-16": {"Primary": {"Doctor": 1}}},
            "overall": {"2026-09-16": {"Primary": 1}},
            "visits": [{"date": "2026-09-16", "patient": "A", "kind": "Primary"}],
        }
        merged = completed_upload._merge_normalized(existing, removed=True)
        self.assertEqual(merged["items"], existing["items"])
        self.assertEqual(merged["doctors"], {})
        self.assertEqual(merged["visits"], [])
        self.assertEqual(merged["overall"], {"2026-09-16": {}})

    def test_existing_completed_dates_use_actual_schema_column(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE daily_uploads (data_date TEXT, completed_sha256 TEXT, normalized_json TEXT)")
        data_date = "2026-09-16"
        normalized = {"overall": {data_date: {"Первичные": 2}}, "visits": []}
        conn.execute("INSERT INTO daily_uploads VALUES (?,?,?)", (data_date, "sha", json.dumps(normalized)))
        existing = completed_upload._existing_completed_dates(conn, "2026-09")
        self.assertTrue(existing[data_date]["has_completed"])
        self.assertNotEqual(existing[data_date]["completed_hash"], "sha")
        conn.close()

    def test_completed_update_preserves_items_and_recalculates_approved_attribution(self):
        data_date = "2026-09-16"
        old = {
            "data_date": data_date,
            "items": [{"staff": "Чирков М. С.", "patient": "Иванов П. С.", "date": data_date,
                       "group": "Услуги", "service": "Приём", "qty": 1, "amount": 100}],
            "retail_items": [], "lab_invoices": [],
            "doctors": {data_date: {"Первичные": {"Чирков М. С.": 2}}},
            "overall": {data_date: {"Первичные": 2}}, "visits": [],
        }
        day = {"data_date": data_date, "overall": {data_date: {"Первичные": 1}},
               "visits": [{"date": data_date, "patient": "Иванов П. С.", "kind": "Первичные"}]}
        updated = completed_upload._merge_normalized(old, day=day)
        self.assertEqual(updated["items"], old["items"])
        self.assertEqual(updated["doctors"][data_date]["Первичные"]["Чирков М. С."], 1)
        self.assertEqual(updated["overall"], day["overall"])



class W03ApprovedUnassignedAttributionTests(unittest.TestCase):
    """Owner-approved: one visit + two departments cannot guess a direction."""

    DAY = "2026-10-09"
    PATIENT = "Иванов П. С."

    def _services(self):
        return [
            {"date": self.DAY, "patient": self.PATIENT,
             "staff": "Чирков М. С.", "group": "Стоматология",
             "service": "Приём", "qty": 1, "amount": 2000},
            {"date": self.DAY, "patient": self.PATIENT,
             "staff": "Старостенко В. А.", "group": "Клиника",
             "service": "Консультация", "qty": 1, "amount": 3000},
        ]

    def _normalized(self, kind, services, counts):
        visits = [
            {"date": self.DAY, "patient": self.PATIENT, "kind": kind}
        ]
        period = completed_upload.daily_upload._split_period(
            {self.DAY: counts}, visits, services, [],
        )
        self.assertEqual(len(period), 1)
        return period[0]["normalized"]

    def test_w03_one_primary_two_directions_unassigned_in_either_file_order(self):
        for reversed_file in (False, True):
            with self.subTest(reversed_file=reversed_file):
                services = self._services()
                if reversed_file:
                    services.reverse()
                normalized = self._normalized(
                    "Первичные", services, {"Первичные": 1}
                )
                self.assertEqual(normalized["doctors"], {self.DAY: {}})
                self.assertEqual(
                    normalized["overall"], {self.DAY: {"Первичные": 1}}
                )
                self.assertEqual(len(normalized["visits"]), 1)

                delta = upload_integrity.daily_delta(
                    completed_upload.core, normalized
                )
                self.assertEqual(delta["sourcePrimary"], 1)
                self.assertEqual(delta["primary"], 0)
                self.assertEqual(delta["dentPrimary"], 0)
                self.assertEqual(delta["clinicPrimary"], 0)
                self.assertEqual(delta["unassignedPrimary"], 1)
                self.assertEqual(delta["sourceRepeat"], 0)
                self.assertEqual(delta["factMedicine"], 5000)
                # Do not remove actual financial services or doctor revenue.
                self.assertEqual(
                    delta["dentists"]["Чирков Максим Сергеевич"], 2000
                )
                self.assertEqual(
                    delta["clinicDocs"]["Старостенко Вадим Анатольевич"],
                    3000,
                )
                stored = {self.DAY: {
                    "primary": delta["primary"], "repeat": delta["repeat"],
                    "_uploadControl": {
                        "sourcePrimary": delta["sourcePrimary"],
                        "sourceRepeat": delta["sourceRepeat"],
                    },
                }}
                completed_upload._preserve_completed_totals(stored)
                self.assertEqual(stored[self.DAY]["primary"], 1)
                self.assertEqual(stored[self.DAY]["repeat"], 0)

    def test_w03_repeat_or_consulted_two_directions_stay_unassigned(self):
        for kind in ("Повторные", "Отконсультированные"):
            with self.subTest(kind=kind):
                normalized = self._normalized(
                    kind, self._services(), {kind: 1}
                )
                self.assertEqual(normalized["doctors"], {self.DAY: {}})
                delta = upload_integrity.daily_delta(
                    completed_upload.core, normalized
                )
                self.assertEqual(delta["sourceRepeat"], 1)
                self.assertEqual(delta["repeat"], 0)
                self.assertEqual(delta["dentRepeat"], 0)
                self.assertEqual(delta["clinicRepeat"], 0)
                self.assertEqual(delta["unassignedRepeat"], 1)
                self.assertEqual(delta["factMedicine"], 5000)

    def test_w03_single_proven_direction_stays_attributed(self):
        for services, expected in (
            ([self._services()[0]], "dentPrimary"),
            ([self._services()[1]], "clinicPrimary"),
        ):
            with self.subTest(expected=expected):
                normalized = self._normalized(
                    "Первичные", services, {"Первичные": 1}
                )
                delta = upload_integrity.daily_delta(
                    completed_upload.core, normalized
                )
                self.assertEqual(delta["sourcePrimary"], 1)
                self.assertEqual(delta["primary"], 1)
                self.assertEqual(delta[expected], 1)
                self.assertEqual(delta["unassignedPrimary"], 0)

    def test_w03_two_visits_with_two_directions_keep_existing_attribution(self):
        visits = [
            {"date": self.DAY, "patient": self.PATIENT,
             "kind": "Первичные"},
            {"date": self.DAY, "patient": self.PATIENT,
             "kind": "Повторные"},
        ]
        period = completed_upload.daily_upload._split_period(
            {self.DAY: {"Первичные": 1, "Повторные": 1}},
            visits, self._services(), [],
        )
        normalized = period[0]["normalized"]
        delta = upload_integrity.daily_delta(completed_upload.core, normalized)
        self.assertEqual(delta["sourcePrimary"], 1)
        self.assertEqual(delta["sourceRepeat"], 1)
        self.assertEqual(delta["dentPrimary"], 1)
        self.assertEqual(delta["clinicRepeat"], 1)
        self.assertEqual(delta["unassignedPrimary"], 0)
        self.assertEqual(delta["unassignedRepeat"], 0)


class W03R01PairedPersistenceRegressionTests(unittest.TestCase):
    """Exercise the actual paired commit and SQLite, not a hand-built report."""

    DAY = "2026-10-09"
    PATIENT = "Иванов П. С."

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE daily_uploads (
                data_date TEXT PRIMARY KEY,
                completed_filename TEXT NOT NULL,
                services_filename TEXT NOT NULL,
                completed_sha256 TEXT NOT NULL,
                services_sha256 TEXT NOT NULL,
                normalized_json TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                uploaded_by INTEGER,
                uploaded_at TEXT NOT NULL
            );
            CREATE TABLE report_data (
                date TEXT PRIMARY KEY, payload TEXT NOT NULL,
                updated_by INTEGER, updated_at TEXT
            );
            CREATE TABLE report_blobs (
                key TEXT PRIMARY KEY, payload TEXT NOT NULL,
                updated_by INTEGER, updated_at TEXT
            );
            CREATE TABLE upload_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_date TEXT, status TEXT NOT NULL, details TEXT,
                completed_filename TEXT, services_filename TEXT,
                uploaded_by INTEGER, created_at TEXT NOT NULL
            );
        """)
        self.conn.commit()
        self.app = Flask("pr76_w03_r01_paired_persistence")

    def tearDown(self):
        self.conn.close()

    def _service(self, data_date, staff, patient=None, amount=1000):
        return {
            "date": data_date, "patient": patient or self.PATIENT,
            "staff": staff, "group": "Приём", "service": "Консультация",
            "qty": 1, "amount": amount,
        }

    def _visit(self, data_date, kind):
        return {"date": data_date, "patient": self.PATIENT, "kind": kind}

    def _stored(self, date):
        row = self.conn.execute(
            "SELECT payload FROM report_data WHERE date=?", (date,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def _paired(self, overall, visits, items):
        """Real paired _process_period_upload and real SQLite report_data upsert.

        Mock only raw workbook parsing and external service-analytics dependencies.
        Keep _prepare_period_raw, _split_period, attribution, compare_clinical,
        upload_integrity.rebuild_management and the actual SQLite write intact.
        """
        paired = completed_upload.daily_upload
        core = completed_upload.core
        source_service_data = {
            "id": "az-services-2026-through-2026-09-30-v1",
            "months": [],
            "directions": {},
        }

        def parse_fixture(_raw, _filename, kind):
            if kind == "completed":
                return (overall, visits), "Завершённые"
            if kind == "services":
                return (items, [], (2026, 10)), "Выручка"
            raise AssertionError("Unexpected paired parser type")

        with self.app.test_request_context(
            "/api/uploads/clinical/commit", method="POST"
        ):
            g.user = {"id": 1}
            with ExitStack() as stack:
                for obj, name, fn in (
                    (paired, "user_permissions",
                     lambda _u: {"upload_completed", "upload_services", "upload_replace"}),
                    (paired, "_parse_fixed_raw", parse_fixture),
                    (paired.upload_reconcile, "refresh_runtime",
                     lambda *a, **kw: None),
                    (core, "db", lambda: self.conn),
                    (core.ident_import, "_load_blob",
                     lambda _key: source_service_data),
                    (core.ident_import, "_pad_financial_months",
                     lambda _index: {}),
                    (core, "_ensure_month", lambda *_args: 0),
                    (core, "_apply_service_items", lambda *_args: None),
                    (core, "_rebuild_management",
                     lambda month, source: upload_integrity.rebuild_management(
                         core, month, source)),
                    (core, "iso_now", lambda: "2026-10-09T00:00:00"),
                    (core, "audit", lambda *a, **kw: None),
                ):
                    stack.enter_context(patch.object(obj, name, fn))
                return paired._process_period_upload(
                    SimpleNamespace(
                        filename="completed.txt",
                        read=lambda: b"test-completed-source"
                    ),
                    SimpleNamespace(
                        filename="services.txt",
                        read=lambda: b"test-services-source"
                    ),
                )

    def _assert_marked_unassigned(self, date, kind, reversed_services=False):
        services = [
            self._service(date, "Чирков М. С.", amount=2000),
            self._service(date, "Старостенко В. А.", amount=3000),
        ]
        if reversed_services:
            services.reverse()
        out = self._paired(
            {date: {kind: 1}},
            [self._visit(date, kind)],
            services,
        )
        self.assertEqual(out["status"], "imported")
        saved = self._stored(date)
        self.assertIsNotNone(saved)
        repeat = kind != "Первичные"
        total_key = "repeat" if repeat else "primary"
        source_key = "sourceRepeat" if repeat else "sourcePrimary"
        witness_key = "w03UnassignedRepeat" if repeat else "w03UnassignedPrimary"
        unknown_key = "unassignedRepeat" if repeat else "unassignedPrimary"
        dental_key = "dentRepeat" if repeat else "dentPrimary"
        clinic_key = "clinicRepeat" if repeat else "clinicPrimary"
        self.assertEqual(saved[total_key], 1)
        self.assertEqual(saved[dental_key], 0)
        self.assertEqual(saved[clinic_key], 0)
        control = saved["_uploadControl"]
        self.assertEqual(control[source_key], 1)
        self.assertEqual(control[witness_key], 1)
        self.assertEqual(control[unknown_key], 1)
        self.assertEqual(control["reportedRepeat" if repeat else "reportedPrimary"], 1)
        self.assertEqual(saved["factMedicine"], 5000)
        self.assertEqual(saved["dentists"]["Чирков Максим Сергеевич"], 2000)
        self.assertEqual(
            saved["clinicDocs"]["Старостенко Вадим Анатольевич"], 3000
        )
        raw_day = json.loads(self.conn.execute(
            "SELECT normalized_json FROM daily_uploads WHERE data_date=?",
            (date,),
        ).fetchone()[0])
        self.assertEqual(raw_day["doctors"], {date: {}})
        self.assertEqual(
            raw_day["_w03Unassigned"], {
                "primary": 0 if repeat else 1,
                "repeat": 1 if repeat else 0,
            },
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM upload_log WHERE data_date=?", (date,)
            ).fetchone()[0], 1
        )
        # Read-only as-of view and dashboard source consume ACTUAL stored total.
        projected = management_view.project_for_reports({date: saved})[date]
        self.assertEqual(projected[total_key], 1)
        self.assertEqual(
            projected[dental_key] + projected[clinic_key], 0
        )

    def test_w03_r01_paired_primary_persists_unassigned_in_both_service_orders(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                if reverse:
                    # Fresh state avoids another import being treated as replace.
                    with self.conn:
                        self.conn.execute("DELETE FROM report_data")
                        self.conn.execute("DELETE FROM daily_uploads")
                        self.conn.execute("DELETE FROM upload_log")
                        self.conn.execute("DELETE FROM report_blobs")
                self._assert_marked_unassigned(
                    self.DAY, "Первичные", reversed_services=reverse
                )

    def test_w03_r01_paired_repeat_and_consulted_persist_unassigned(self):
        for kind in ("Повторные", "Отконсультированные"):
            with self.subTest(kind=kind):
                self._assert_marked_unassigned(self.DAY, kind)
                with self.conn:
                    self.conn.execute("DELETE FROM report_data")
                    self.conn.execute("DELETE FROM daily_uploads")
                    self.conn.execute("DELETE FROM upload_log")
                    self.conn.execute("DELETE FROM report_blobs")

    def test_w03_r01_existing_baseline_preserved_and_next_day_accumulates(self):
        before_date = "2026-10-08"
        baseline = {
            "date": before_date,
            "primary": 3, "repeat": 2,
            "dentPrimary": 2, "dentRepeat": 1,
            "clinicPrimary": 1, "clinicRepeat": 1,
            "factMedicine": 10000,
            "factLab": 0,
            "dentists": {"Чирков Максим Сергеевич": 7000},
            "clinicDocs": {"Старостенко Вадим Анатольевич": 3000},
            "labOrders": 4,
            "_source": "earlier-approved-paired",
            "_uploadControl": {"sourcePrimary": 3, "sourceRepeat": 2},
        }
        saved_old_bytes = json.dumps(baseline, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO report_data(date,payload,updated_by,updated_at)"
            "VALUES(?,?,1,'prior')",
            (before_date, saved_old_bytes),
        )
        self.conn.commit()

        next_date = "2026-10-10"
        result = self._paired(
            {
                self.DAY: {"Первичные": 1},
                next_date: {"Повторные": 1},
            },
            [
                self._visit(self.DAY, "Первичные"),
                self._visit(next_date, "Повторные"),
            ],
            [
                self._service(self.DAY, "Чирков М. С.", amount=2000),
                self._service(self.DAY, "Старостенко В. А.", amount=3000),
                self._service(next_date, "Старостенко В. А.", amount=1000),
            ],
        )
        self.assertEqual(result["status"], "imported")
        ninth = self._stored(self.DAY)
        tenth = self._stored(next_date)
        self.assertEqual(
            (ninth["primary"], ninth["repeat"], ninth["dentPrimary"],
             ninth["clinicPrimary"]), (4, 2, 2, 1)
        )
        self.assertEqual(
            (tenth["primary"], tenth["repeat"], tenth["dentPrimary"],
             tenth["clinicPrimary"], tenth["clinicRepeat"]), (4, 3, 2, 1, 2)
        )
        self.assertEqual(ninth["_uploadControl"]["w03UnassignedPrimary"], 1)
        self.assertEqual(tenth["_uploadControl"]["w03UnassignedPrimary"], 1)
        self.assertEqual(ninth["_uploadControl"]["sourcePrimary"], 4)
        self.assertEqual(tenth["_uploadControl"]["sourceRepeat"], 3)
        self.assertEqual(
            (ninth["factMedicine"], tenth["factMedicine"]), (15000, 16000)
        )
        self.assertEqual(
            (ninth["labOrders"], tenth["labOrders"]), (4, 4)
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT payload FROM report_data WHERE date=?", (before_date,)
            ).fetchone()[0], saved_old_bytes
        )

    def test_w03_r01_certain_direction_is_not_counted_twice(self):
        for doctor, direction in (
            ("Чирков М. С.", "dentPrimary"),
            ("Старостенко В. А.", "clinicPrimary"),
        ):
            with self.subTest(direction=direction):
                result = self._paired(
                    {self.DAY: {"Первичные": 1}},
                    [self._visit(self.DAY, "Первичные")],
                    [self._service(self.DAY, doctor, amount=1700)],
                )
                self.assertEqual(result["status"], "imported")
                saved = self._stored(self.DAY)
                self.assertEqual(saved["primary"], 1)
                self.assertEqual(saved[direction], 1)
                self.assertEqual(saved["_uploadControl"]["w03UnassignedPrimary"], 0)
                with self.conn:
                    self.conn.execute("DELETE FROM report_data")
                    self.conn.execute("DELETE FROM daily_uploads")
                    self.conn.execute("DELETE FROM upload_log")
                    self.conn.execute("DELETE FROM report_blobs")


class IndependentCompletedIntegrationTests(unittest.TestCase):
    """Isolated SQLite regression without Production or external data."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE daily_uploads (
                data_date TEXT PRIMARY KEY,
                completed_filename TEXT NOT NULL,
                services_filename TEXT NOT NULL,
                completed_sha256 TEXT NOT NULL,
                services_sha256 TEXT NOT NULL,
                normalized_json TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                uploaded_by INTEGER,
                uploaded_at TEXT NOT NULL
            );
            CREATE TABLE report_data (
                date TEXT PRIMARY KEY, payload TEXT NOT NULL,
                updated_by INTEGER, updated_at TEXT
            );
            CREATE TABLE report_blobs (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
        """)
        self.conn.commit()
        self.app = Flask("pr76_completed_integration")

    def tearDown(self):
        self.conn.close()

    def _prepared(self, day="2026-10-09", primary=2, repeat=3):
        return {
            "filename": "completed.xlsx", "sheet": "Приёмы",
            "month": day[:7], "dates": [day],
            "days": {day: {
                "data_date": day,
                "overall": {day: {"Первичные": primary, "Повторные": repeat}},
                "visits": [], "completed_hash": "source-hash",
            }},
        }

    def _report(self, day, data):
        self.conn.execute(
            "INSERT INTO report_data(date,payload,updated_by,updated_at) VALUES(?,?,1,'old')",
            (day, json.dumps(data, ensure_ascii=False)),
        )
        self.conn.commit()

    def _run(self, prepared, decision=None, cash_replay=None, cash_overlay=None):
        with self.app.test_request_context("/api/uploads/completed/commit", method="POST"):
            g.user = {"id": 1}
            with ExitStack() as stack:
                defaults = [
                    (completed_upload, "db", lambda: self.conn),
                    (completed_upload, "user_permissions", lambda _u: {"upload_completed", "upload_replace"}),
                    (completed_upload, "_file_from_request", lambda: ("completed.xlsx", b"test")),
                    (completed_upload, "_parse", lambda _raw, _name: prepared),
                    (completed_upload, "iso_now", lambda: "2026-10-09T00:00:00"),
                    (completed_upload, "audit", lambda *a, **kw: None),
                    (completed_upload.core, "db", lambda: self.conn),
                    (completed_upload.cash_payments, "init_schema", lambda conn: None),
                    (completed_upload.upload_reconcile, "init_schema", lambda conn: None),
                    (completed_upload.upload_reconcile, "archive_daily_row", lambda *a: None),
                    (completed_upload.upload_reconcile, "create_db_backup", lambda _label: "fixture-backup"),
                    (completed_upload.cash_payments, "overlay_record_map", cash_overlay or (lambda _conn, _month, records: records)),
                    (completed_upload.cash_payments, "overlay_stored_month", cash_replay or (lambda *a: 0)),
                ]
                for obj, name, replacement in defaults:
                    stack.enter_context(patch.object(obj, name, replacement))
                stack.enter_context(patch.object(
                    completed_upload.core, "_rebuild_management",
                    lambda month, source: upload_integrity.rebuild_management(
                        completed_upload.core, month, source,
                    ),
                ))
                return completed_upload._commit(decision)

    def _stored(self, day):
        row = self.conn.execute(
            "SELECT payload FROM report_data WHERE date=?", (day,),
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def test_b01_historical_report_never_replaced_without_daily_record(self):
        day = "2026-09-16"
        old = {"date": day, "_source": "legacy-ident", "primary": 5,
               "dentists": {"Чирков Максим Сергеевич": 150000}, "cashTotal": 180000}
        self._report(day, old)
        with self.assertRaisesRegex(ValueError, "исторические клинические данные"):
            self._run(self._prepared(day), decision="use_new")
        self.assertEqual(self._stored(day), old)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM daily_uploads").fetchone()[0], 0)

    def test_b01_partial_before_later_historical_report_stops(self):
        self._report("2026-09-30", {
            "date": "2026-09-30", "dentPrimary": 17, "factMedicine": 1000000,
        })
        with self.assertRaisesRegex(ValueError, "исторические клинические данные"):
            self._run(self._prepared("2026-09-16"))
        self.assertIsNone(self._stored("2026-09-16"))
        self.assertEqual(self._stored("2026-09-30")["factMedicine"], 1000000)

    def test_b01_service_through_blocks_new_legacy_days(self):
        self.conn.execute(
            "INSERT INTO report_blobs(key,payload) VALUES(?,?)",
            ("az-service-analytics-v1", json.dumps({
                "id": "az-services-2026-through-2026-09-26-v1",
            })),
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "сводную базу услуг"):
            self._run(self._prepared("2026-09-20"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM daily_uploads").fetchone()[0], 0)

    def test_b02_totals_survive_without_services_or_direction_match(self):
        day = "2026-10-09"
        self.assertEqual(self._run(self._prepared(day, primary=4, repeat=7))["status"], "imported")
        record = self._stored(day)
        self.assertEqual((record["primary"], record["repeat"]), (4, 7))
        self.assertEqual((record["dentPrimary"], record["clinicPrimary"]), (0, 0))
        self.assertEqual(record["_uploadControl"]["sourcePrimary"], 4)
        self.assertEqual(record["_uploadControl"]["sourceRepeat"], 7)
        self.assertEqual(record["_uploadControl"]["unassignedPrimary"], 4)
        self.assertEqual(record["_uploadControl"]["unassignedRepeat"], 7)
        self.assertEqual(record["factMedicine"], 0)
        self.assertEqual(record["labOrders"], 0)

    def test_b01_cash_only_report_can_precede_completed_upload(self):
        day = "2026-10-09"
        old = {"date": day, "_cash_rule": "positive-receipts-only-v1",
               "_cash_source": "Счета и оплаты", "cashTotal": 2200,
               "cashOOO": 1500, "cashIP": 700}
        self._report(day, old)
        self.assertEqual(self._run(self._prepared(day, primary=3, repeat=1))["status"], "imported")
        now = self._stored(day)
        self.assertEqual((now["cashTotal"], now["cashOOO"], now["cashIP"]), (2200, 1500, 700))
        self.assertEqual((now["primary"], now["repeat"]), (3, 1))

    def test_b01_financial_difference_rolls_back_entire_completed_replace(self):
        day = "2026-10-09"
        normalized = {
            "data_date": day, "items": [], "retail_items": [], "lab_invoices": [],
            "doctors": {}, "overall": {day: {"Первичные": 1}}, "visits": [],
        }
        self.conn.execute(
            "INSERT INTO daily_uploads VALUES (?,?,?,?,?,?,1,1,'old')",
            (day, "old.xlsx", "", "old-hash", "", json.dumps(normalized, ensure_ascii=False)),
        )
        old = {"date": day, "_source": "prior daily", "primary": 1,
               "factMedicine": 98765, "dentists": {"Чирков Максим Сергеевич": 98765}}
        self.conn.execute(
            "INSERT INTO report_data VALUES (?,?,1,'old')",
            (day, json.dumps(old, ensure_ascii=False)),
        )
        self.conn.commit()
        def corrupt_cash_overlay(_conn, _month, records):
            for record in records.values():
                record["factMedicine"] = 0
            return records

        with self.assertRaisesRegex(ValueError, "сохранённый показатель"):
            self._run(
                self._prepared(day, primary=2),
                decision="use_new",
                cash_overlay=corrupt_cash_overlay,
            )
        self.assertEqual(self._stored(day), old)
        row = self.conn.execute(
            "SELECT completed_sha256 FROM daily_uploads WHERE data_date=?", (day,),
        ).fetchone()
        self.assertEqual(row[0], "old-hash")


    def _legacy_day(self, day="2026-10-06"):
        """An old paired upload, its management report, and service-through cutoff."""
        normalized = {
            "data_date": day,
            "items": [
                {"date": day, "staff": "Чирков М. С.", "patient": "Иванов П. С.",
                 "group": "Стоматология", "service": "Приём", "qty": 1,
                 "amount": 20000},
            ],
            "retail_items": [],
            "lab_invoices": [["76001", day]],
            "overall": {day: {"Первичные": 2, "Повторные": 1}},
            "visits": [
                {"date": day, "patient": "Иванов П. С.", "kind": "Первичные"}
            ],
            "doctors": {day: {"Первичные": {"Чирков М. С.": 1}}},
        }
        self.conn.execute(
            "INSERT INTO daily_uploads VALUES (?,?,?,?,?,?,1,1,'old')",
            (day, "old-completed.xlsx", "old-services.xlsx", "legacy-hash",
             "legacy-services-hash", json.dumps(normalized, ensure_ascii=False)),
        )
        self.conn.execute(
            "INSERT INTO report_blobs(key,payload) VALUES(?,?)",
            ("az-service-analytics-v1", json.dumps({
                "id": "az-services-2026-through-2026-10-06-v1"
            })),
        )
        old_management = {
            "date": day, "_source": "previous paired clinical",
            "primary": 2, "repeat": 1,
            "dentPrimary": 1, "dentRepeat": 0,
            "factMedicine": 20000, "factLab": 0,
            "dentists": {"Чирков Максим Сергеевич": 20000},
            "clinicDocs": {},
            "labRevenue": 0, "labOrders": 4,
            "discountAmount": 1500, "grossRevenue": 21500,
            "discountDataComplete": True,
            "cashTotal": 95000, "cashOOO": 75000, "cashIP": 20000,
            "_cash_rule": "positive-receipts-only-v1",
            "_uploadControl": {"sourcePrimary": 2, "sourceRepeat": 1},
        }
        self.conn.execute(
            "INSERT INTO report_data VALUES (?,?,1,'old')",
            (day, json.dumps(old_management, ensure_ascii=False)),
        )
        self.conn.commit()
        return normalized, old_management

    def test_b03_next_day_after_legacy_paired_source_commits_without_rewrite(self):
        """Critical path: legacy 06.10 then standalone completed 07.10."""
        legacy, old_report = self._legacy_day()
        old_normalized = self.conn.execute(
            "SELECT normalized_json FROM daily_uploads WHERE data_date='2026-10-06'"
        ).fetchone()[0]
        old_json = self.conn.execute(
            "SELECT payload FROM report_data WHERE date='2026-10-06'"
        ).fetchone()[0]

        def forbidden_cash_replay(*args):
            self.fail("Completed-only import may not rewrite historical cash data")

        result = self._run(
            self._prepared("2026-10-07", primary=3, repeat=4),
            cash_replay=forbidden_cash_replay,
        )
        self.assertEqual(result["status"], "imported")
        self.assertEqual(
            self.conn.execute(
                "SELECT normalized_json FROM daily_uploads WHERE data_date='2026-10-06'"
            ).fetchone()[0], old_normalized
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT payload FROM report_data WHERE date='2026-10-06'"
            ).fetchone()[0], old_json
        )
        self.assertEqual(self._stored("2026-10-06"), old_report)
        added = self._stored("2026-10-07")
        self.assertEqual((added["primary"], added["repeat"]), (5, 5))
        self.assertEqual((added["dentPrimary"], added["dentRepeat"]), (1, 0))
        self.assertEqual(
            (added["_uploadControl"]["sourcePrimary"],
             added["_uploadControl"]["sourceRepeat"]), (5, 5)
        )
        self.assertEqual(added["factMedicine"], 20000)
        self.assertEqual(added["labOrders"], 4)
        self.assertEqual(added["discountAmount"], 1500)
        self.assertEqual(added["grossRevenue"], 21500)
        self.assertTrue(added["discountDataComplete"])
        self.assertEqual(added["_serviceAsOf"], "2026-10-06")
        self.assertEqual(added["dentists"]["Чирков Максим Сергеевич"], 20000)
        row = self.conn.execute(
            "SELECT services_filename,services_sha256 FROM daily_uploads "
            "WHERE data_date='2026-10-07'"
        ).fetchone()
        self.assertEqual(tuple(row), ("", ""))

    def test_b03_prior_paired_day_still_blocked_from_replacement(self):
        self._legacy_day()
        original = self._stored("2026-10-06")
        with self.assertRaisesRegex(ValueError, "сводную базу услуг"):
            self._run(self._prepared("2026-10-06"), decision="use_new")
        self.assertEqual(self._stored("2026-10-06"), original)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM daily_uploads").fetchone()[0],
            1,
        )

    def test_b03_unregistered_later_legacy_report_still_blocks_partial_import(self):
        self._legacy_day()
        later = {
            "date": "2026-10-08", "_source": "historical-clinic",
            "primary": 12, "factMedicine": 80000,
        }
        self._report("2026-10-08", later)
        with self.assertRaisesRegex(ValueError, "исторические клинические данные"):
            self._run(self._prepared("2026-10-07"))
        self.assertEqual(self._stored("2026-10-08"), later)
        self.assertIsNone(self._stored("2026-10-07"))

    def test_b03_untouched_existing_cash_snapshot_remains_byte_identical(self):
        self._legacy_day()
        old_cash = {
            "date": "2026-10-09", "_cash_rule": "positive-receipts-only-v1",
            "cashTotal": 123456, "cashOOO": 100000, "cashIP": 23456,
        }
        self._report("2026-10-09", old_cash)
        before = self.conn.execute(
            "SELECT payload FROM report_data WHERE date='2026-10-09'"
        ).fetchone()[0]
        result = self._run(self._prepared("2026-10-07", primary=1, repeat=1))
        self.assertEqual(result["status"], "imported")
        self.assertEqual(self.conn.execute(
            "SELECT payload FROM report_data WHERE date='2026-10-09'"
        ).fetchone()[0], before)
        self.assertEqual(self._stored("2026-10-09"), old_cash)


    def _span(self, *days):
        prepared = self._prepared(days[0][0], days[0][1], days[0][2])
        for day, primary, repeat in days[1:]:
            part = self._prepared(day, primary, repeat)
            prepared["dates"].append(day)
            prepared["days"].update(part["days"])
        prepared["dates"].sort()
        return prepared

    def test_b04_checkpoint_overrides_incomplete_legacy_daily_detail(self):
        self._legacy_day()
        previous = self._stored("2026-10-06")
        self.assertEqual(previous["labOrders"], 4)
        # Source daily_uploads only knows one lab invoice, not all historical orders.
        old_normalized = json.loads(self.conn.execute(
            "SELECT normalized_json FROM daily_uploads WHERE data_date='2026-10-06'"
        ).fetchone()[0])
        self.assertEqual(len(old_normalized["lab_invoices"]), 1)
        self.assertEqual(self._run(self._prepared("2026-10-07", 3, 4))["status"], "imported")
        new = self._stored("2026-10-07")
        self.assertEqual(new["labOrders"], 4)
        self.assertEqual(new["grossRevenue"], 21500)
        self.assertEqual(new["discountAmount"], 1500)
        self.assertTrue(new["discountDataComplete"])
        self.assertEqual(new["_serviceAsOf"], "2026-10-06")
        self.assertEqual(new["primary"], 5)
        self.assertEqual(new["repeat"], 5)
        self.assertEqual(self._stored("2026-10-06"), previous)

    def test_b04_two_new_days_accumulate_only_new_completed_visits(self):
        self._legacy_day()
        res = self._run(self._span(
            ("2026-10-07", 3, 4),
            ("2026-10-08", 1, 2),
        ))
        self.assertEqual(res["status"], "imported")
        seventh = self._stored("2026-10-07")
        eighth = self._stored("2026-10-08")
        self.assertEqual((seventh["primary"], seventh["repeat"]), (5, 5))
        self.assertEqual((eighth["primary"], eighth["repeat"]), (6, 7))
        self.assertEqual((eighth["labOrders"], eighth["grossRevenue"],
                          eighth["discountAmount"]), (4, 21500, 1500))
        self.assertEqual(eighth["_serviceAsOf"], "2026-10-06")
        self.assertEqual(self._stored("2026-10-06")["labOrders"], 4)

    def test_b04_different_verified_clinic_total_beats_partial_daily_ledger(self):
        self._legacy_day()
        clinical = self._stored("2026-10-06")
        clinical["primary"] = 100
        clinical["_uploadControl"]["sourcePrimary"] = 100
        self.conn.execute(
            "UPDATE report_data SET payload=? WHERE date='2026-10-06'",
            (json.dumps(clinical, ensure_ascii=False),),
        )
        self.conn.commit()
        self._run(self._prepared("2026-10-07", 3, 2))
        self.assertEqual(self._stored("2026-10-07")["primary"], 103)
        self.assertEqual(self._stored("2026-10-07")["repeat"], 3)
        self.assertEqual(self._stored("2026-10-06")["primary"], 100)

    def test_b04_backward_edit_refused_if_later_clinical_snapshot_exists(self):
        self._legacy_day()
        self._run(self._span(("2026-10-07", 2, 2), ("2026-10-08", 1, 1)))
        before = self._stored("2026-10-07")
        later = self._stored("2026-10-08")
        with self.assertRaisesRegex(ValueError, "более поздний клинический срез"):
            self._run(self._prepared("2026-10-07", 5, 5), decision="use_new")
        self.assertEqual(self._stored("2026-10-07"), before)
        self.assertEqual(self._stored("2026-10-08"), later)

    def test_b04_fails_closed_if_unproven_service_items_present(self):
        self._legacy_day()
        day = "2026-10-07"
        normalized = {
            "data_date": day, "items": [{"date": day, "amount": 999}],
            "lab_invoices": [], "retail_items": [], "doctors": {},
            "overall": {day: {"Первичные": 1}}, "visits": [],
        }
        self.conn.execute(
            "INSERT INTO daily_uploads VALUES(?,?,?,?,?,?,1,1,'old')",
            (day, "c.xlsx", "", "hash-old", "hash-services",
             json.dumps(normalized, ensure_ascii=False)),
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "услуги"):
            self._run(self._prepared(day, 2, 0), decision="use_new")
        old = self.conn.execute(
            "SELECT normalized_json FROM daily_uploads WHERE data_date=?", (day,)
        ).fetchone()[0]
        self.assertEqual(json.loads(old)["items"], normalized["items"])
        self.assertIsNone(self._stored(day))

    def test_b04_incomplete_discount_history_is_not_falsely_marked_complete(self):
        self._legacy_day()
        old = self._stored("2026-10-06")
        old["discountDataComplete"] = False
        self.conn.execute(
            "UPDATE report_data SET payload=? WHERE date='2026-10-06'",
            (json.dumps(old, ensure_ascii=False),),
        )
        self.conn.commit()
        self._run(self._prepared("2026-10-07", 1, 1))
        self.assertFalse(self._stored("2026-10-07")["discountDataComplete"])
        self.assertEqual(self._stored("2026-10-07")["discountAmount"], 1500)


    def test_b05_real_completed_commit_then_finrez_joins_later_cash_date(self):
        self._legacy_day()
        day_cash = "2026-10-09"
        latest_cash = {
            "date": day_cash, "_cash_rule": "positive-receipts-only-v1",
            "cashTotal": 190000, "cashOOO": 120000, "cashIP": 70000,
            "dentists": {"Кассовый врач": 180000}, "factMedicine": 185000,
        }
        self._report(day_cash, latest_cash)
        original_cash = self.conn.execute(
            "SELECT payload FROM report_data WHERE date=?", (day_cash,)
        ).fetchone()[0]
        self._run(self._prepared("2026-10-07", 3, 4))
        # Independent completed -> read-only projection -> exact Finrez selector.
        with patch.object(finrez, "db", lambda: self.conn):
            output = finrez._management_months()["2026-10"]
        self.assertEqual(output["date"], day_cash)
        self.assertEqual(output["clinicalAsOf"], "2026-10-07")
        self.assertEqual(output["cashAsOf"], "2026-10-09")
        self.assertEqual(output["serviceAsOf"], "2026-10-06")
        self.assertEqual((output["cashTotal"], output["cashOOO"], output["cashIP"]),
                         (190000, 120000, 70000))
        self.assertEqual((output["dentPrimary"], output["clinicPrimary"]), (1, 0))
        self.assertEqual(output["discountAmount"], 1500)
        self.assertEqual(output["grossRevenue"], 21500)
        self.assertEqual(self.conn.execute(
            "SELECT payload FROM report_data WHERE date=?", (day_cash,)
        ).fetchone()[0], original_cash)

        raw = {
            row["date"]: json.loads(row["payload"]) for row in self.conn.execute(
                "SELECT date,payload FROM report_data ORDER BY date"
            ).fetchall()
        }
        self.assertNotIn("primary", raw[day_cash])
        projection = management_view.project_for_reports(raw)
        self.assertEqual((projection[day_cash]["primary"], projection[day_cash]["repeat"]), (5, 5))

    def test_b05_completed_marks_actual_cash_receipt_snapshot_date(self):
        self._legacy_day()
        day = "2026-10-07"
        completed_upload.cash_payments.init_schema(self.conn)
        cash_delta = completed_upload.cash_payments._blank_day()
        cash_delta.update({
            "cashTotal": 24000, "cashOOO": 24000,
            "factMedicine": 24000,
            "dentists": {"Чирков Максим Сергеевич": 24000},
        })
        completed_upload.cash_payments.replace_range(
            self.conn, {day: cash_delta}, "cash.xlsx", "sha", 1, "now",
        )
        self.conn.commit()
        real_overlay = completed_upload.cash_payments.overlay_record_map
        self._run(
            self._prepared(day, 3, 1),
            cash_overlay=real_overlay,
        )
        record = self._stored(day)
        self.assertEqual(record["_clinicalAsOf"], day)
        self.assertEqual(record["_cashAsOf"], day)
        self.assertEqual(record["_serviceAsOf"], "2026-10-06")
        self.assertEqual(record["cashTotal"], 24000)
        self.assertEqual(record["cashOOO"], 24000)
        self.assertEqual(record["labOrders"], 4)
        self.assertEqual(record["discountAmount"], 1500)


    def _cash_forward_to_ninth(self):
        """Use the REAL cash receipts overlay; do not handcraft a cash-only row."""
        cash = completed_upload.cash_payments
        cash.init_schema(self.conn)
        values = cash._blank_day()
        values.update({
            "cashOOO": 120000, "cashIP": 70000, "cashTotal": 190000,
            "factMedicine": 185000,
            "dentists": {"Чирков Максим Сергеевич": 185000},
        })
        cash.replace_range(
            self.conn, {"2026-10-09": values},
            "cash.xlsx", "cash-source-sha", 1, "now",
        )
        count = cash.overlay_stored_month(self.conn, "2026-10", 1, "now")
        self.assertGreaterEqual(count, 1)
        self.conn.commit()
        return self._stored("2026-10-09")

    def test_b06_real_cash_forward_clone_then_next_completed_day(self):
        self._legacy_day()
        original_cash = self._cash_forward_to_ninth()
        self.assertTrue(original_cash["_cashForwardClone"])
        self.assertEqual(original_cash["_clinicalAsOf"], "2026-10-06")
        self.assertEqual(original_cash["_cashAsOf"], "2026-10-09")
        before_cash = self.conn.execute(
            "SELECT payload FROM report_data WHERE date='2026-10-09'"
        ).fetchone()[0]
        before_legacy = self.conn.execute(
            "SELECT payload FROM report_data WHERE date='2026-10-06'"
        ).fetchone()[0]

        out = self._run(self._prepared("2026-10-07", 3, 4))
        self.assertEqual(out["status"], "imported")
        self.assertEqual((self._stored("2026-10-07")["primary"],
                          self._stored("2026-10-07")["repeat"]), (5, 5))
        self.assertEqual(self.conn.execute(
            "SELECT payload FROM report_data WHERE date='2026-10-09'"
        ).fetchone()[0], before_cash)
        self.assertEqual(self.conn.execute(
            "SELECT payload FROM report_data WHERE date='2026-10-06'"
        ).fetchone()[0], before_legacy)

        with patch.object(finrez, "db", lambda: self.conn):
            latest = finrez._management_months()["2026-10"]
        self.assertEqual((latest["clinicalAsOf"], latest["cashAsOf"]),
                         ("2026-10-07", "2026-10-09"))
        self.assertEqual((latest["cashTotal"], latest["cashOOO"], latest["cashIP"]),
                         (190000, 120000, 70000))
        view = management_view.project_for_reports({
            row["date"]: json.loads(row["payload"])
            for row in self.conn.execute(
                "SELECT date,payload FROM report_data ORDER BY date"
            ).fetchall()
        })
        self.assertEqual((view["2026-10-09"]["primary"], view["2026-10-09"]["repeat"]), (5, 5))
        self.assertEqual(view["2026-10-09"]["labOrders"], 4)

    def test_b06_preexisting_unmarked_cash_clone_is_identified_by_earlier_source(self):
        self._legacy_day()
        original = self._cash_forward_to_ninth()
        self.assertTrue(original["_cashForwardClone"])
        # Emulate existing cash-forward rows produced BEFORE the new markers.
        original.pop("_cashForwardClone")
        original.pop("_clinicalAsOf")
        original.pop("_cashAsOf")
        self.conn.execute(
            "UPDATE report_data SET payload=? WHERE date='2026-10-09'",
            (json.dumps(original, ensure_ascii=False),),
        )
        self.conn.commit()
        before = self.conn.execute(
            "SELECT payload FROM report_data WHERE date='2026-10-09'"
        ).fetchone()[0]
        self.assertEqual(self._run(self._prepared("2026-10-07", 3, 1))["status"], "imported")
        self.assertEqual(self.conn.execute(
            "SELECT payload FROM report_data WHERE date='2026-10-09'"
        ).fetchone()[0], before)
        view = management_view.project_for_reports({
            row["date"]: json.loads(row["payload"])
            for row in self.conn.execute("SELECT date,payload FROM report_data").fetchall()
        })
        self.assertEqual(view["2026-10-09"]["_clinicalAsOf"], "2026-10-07")
        self.assertEqual(view["2026-10-09"]["primary"], 5)

    def test_b06_unknown_clinical_cash_report_stays_protected(self):
        self._legacy_day()
        ambiguous = self._cash_forward_to_ninth()
        ambiguous.pop("_cashForwardClone")
        ambiguous.pop("_clinicalAsOf")
        ambiguous["_source"] = "different manually recorded clinic"
        ambiguous["primary"] = 999
        self.conn.execute(
            "UPDATE report_data SET payload=? WHERE date='2026-10-09'",
            (json.dumps(ambiguous, ensure_ascii=False),),
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "исторические клинические данные"):
            self._run(self._prepared("2026-10-07", 1, 1))
        self.assertIsNone(self._stored("2026-10-07"))
        self.assertEqual(self._stored("2026-10-09")["primary"], 999)

    def test_b06_completed_then_real_cash_forward_preserves_clinical_as_of(self):
        self._legacy_day()
        self.assertEqual(self._run(self._prepared("2026-10-07", 3, 4))["status"], "imported")
        cloned = self._cash_forward_to_ninth()
        self.assertTrue(cloned["_cashForwardClone"])
        self.assertEqual(cloned["_clinicalAsOf"], "2026-10-07")
        self.assertEqual(cloned["_cashAsOf"], "2026-10-09")
        with patch.object(finrez, "db", lambda: self.conn):
            projected = finrez._management_months()["2026-10"]
        self.assertEqual(projected["clinicalAsOf"], "2026-10-07")
        self.assertEqual(projected["cashAsOf"], "2026-10-09")
        self.assertEqual((projected["cashTotal"], projected["cashOOO"], projected["cashIP"]),
                         (190000, 120000, 70000))

    def test_b06_cash_clone_does_not_block_next_second_day_either(self):
        self._legacy_day()
        self._cash_forward_to_ninth()
        self._run(self._prepared("2026-10-07", 1, 1))
        self.assertEqual(self._run(self._prepared("2026-10-08", 2, 3))["status"], "imported")
        self.assertEqual((self._stored("2026-10-08")["primary"],
                          self._stored("2026-10-08")["repeat"]), (5, 5))
        self.assertEqual(self._stored("2026-10-09")["_clinicalAsOf"], "2026-10-06")


    def test_b06_same_day_cash_forward_becomes_real_completed_clinical(self):
        self._legacy_day()
        before = self._cash_forward_to_ninth()
        self.assertTrue(before["_cashForwardClone"])
        self.assertEqual(before["_clinicalAsOf"], "2026-10-06")
        self.assertEqual(self._run(self._prepared("2026-10-09", 3, 2))["status"], "imported")
        after = self._stored("2026-10-09")
        self.assertNotIn("_cashForwardClone", after)
        self.assertEqual(after["_clinicalAsOf"], "2026-10-09")
        self.assertEqual(after["_cashAsOf"], "2026-10-09")
        self.assertEqual((after["primary"], after["repeat"]), (5, 3))
        self.assertEqual((after["cashTotal"], after["cashOOO"], after["cashIP"]),
                         (190000, 120000, 70000))
        view = management_view.project_for_reports({
            row["date"]: json.loads(row["payload"])
            for row in self.conn.execute(
                "SELECT date,payload FROM report_data ORDER BY date"
            ).fetchall()
        })
        self.assertEqual(view["2026-10-09"]["_clinicalAsOf"], "2026-10-09")
        self.assertEqual((view["2026-10-09"]["primary"],
                          view["2026-10-09"]["repeat"]), (5, 3))


    def test_b08_cash20_completed31_cash31_updates_source_and_forecast(self):
        """End-to-end isolated SQLite: cash20 -> completed31 -> cash31.

        Uses the actual cash ledger, completed-only commit, Finrez selector,
        and live Forecast.fullMonth JavaScript source. No production writes.
        """
        cash = completed_upload.cash_payments
        cash.init_schema(self.conn)
        cash20 = cash._blank_day()
        cash20.update({
            "cashOOO": 3000, "cashIP": 2000, "cashTotal": 5000,
            "factMedicine": 4800, "factLab": 200,
        })
        cash.replace_range(
            self.conn, {"2026-10-20": cash20},
            "cash20.xlsx", "sha20", 1, "cash-at-20",
        )
        self.assertEqual(
            cash.overlay_stored_month(self.conn, "2026-10", 1, "cash-at-20"), 1
        )
        self.conn.commit()
        self.assertEqual(self._stored("2026-10-20")["_cashAsOf"], "2026-10-20")

        # Completed source reaches month-end, but the last confirmed cash
        # receipt is still dated 20 Oct.
        result = self._run(
            self._prepared("2026-10-31", primary=4, repeat=6),
            cash_overlay=cash.overlay_record_map,
        )
        self.assertEqual(result["status"], "imported")
        earlier = self._stored("2026-10-31")
        self.assertEqual(
            (earlier["_clinicalAsOf"], earlier["_cashAsOf"]),
            ("2026-10-31", "2026-10-20"),
        )
        self.assertEqual((earlier["primary"], earlier["repeat"]), (4, 6))
        self.assertEqual(
            (earlier["cashTotal"], earlier["cashOOO"], earlier["cashIP"]),
            (5000, 3000, 2000),
        )
        with patch.object(finrez, "db", lambda: self.conn):
            before = finrez._management_months()["2026-10"]
        self.assertEqual(
            (before["clinicalAsOf"], before["cashAsOf"]),
            ("2026-10-31", "2026-10-20"),
        )

        # Later the actual 31 Oct cash file arrives and overlays the ALREADY
        # EXISTING report_data 31 Oct row; it must refresh stale provenance.
        cash31 = cash._blank_day()
        cash31.update({
            "cashOOO": 6000, "cashIP": 3000, "cashTotal": 9000,
            "factMedicine": 8500, "factLab": 500,
        })
        cash.replace_range(
            self.conn, {"2026-10-31": cash31},
            "cash31.xlsx", "sha31", 1, "cash-at-31",
        )
        self.assertGreaterEqual(
            cash.overlay_stored_month(self.conn, "2026-10", 1, "cash-at-31"),
            1,
        )
        self.conn.commit()
        final = self._stored("2026-10-31")
        self.assertEqual(
            (final["_clinicalAsOf"], final["_cashAsOf"]),
            ("2026-10-31", "2026-10-31"),
        )
        self.assertEqual((final["primary"], final["repeat"]), (4, 6))
        self.assertNotIn("_cashForwardClone", final)
        self.assertEqual(
            (final["cashTotal"], final["cashOOO"], final["cashIP"]),
            (14000, 9000, 5000),
        )
        self.assertEqual(
            final["cashTotal"], final["cashOOO"] + final["cashIP"]
        )
        self.assertEqual(
            (final["factMedicine"], final["factLab"]), (13300, 700)
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM daily_uploads WHERE data_date=?",
                ("2026-10-31",),
            ).fetchone()[0], 1,
        )
        self.assertEqual(self._stored("2026-10-20")["_cashAsOf"], "2026-10-20")
        with patch.object(finrez, "db", lambda: self.conn):
            after = finrez._management_months()["2026-10"]
        self.assertEqual(after["date"], "2026-10-31")
        self.assertEqual(
            (after["clinicalAsOf"], after["cashAsOf"]),
            ("2026-10-31", "2026-10-31"),
        )
        self.assertEqual(
            (after["cashTotal"], after["cashOOO"], after["cashIP"]),
            (14000, 9000, 5000),
        )

        # Execute the actual Forecast.fullMonth function on BOTH genuine
        # Finrez API-shaped checkpoints: incomplete before cash31, complete
        # afterwards. This does not duplicate the business formula in Python.
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node required for B08 Forecast regression")
        html = (
            Path(__file__).resolve().parent.parent / "reports" / "forecast.html"
        ).read_text(encoding="utf-8")
        functions = []
        for name in ("rec", "monthEndISO", "fullMonth"):
            match = re.search(r"^function " + name + r"\([^\n]+$", html, re.M)
            self.assertIsNotNone(match, f"Missing Forecast function {name}")
            functions.append(match.group(0))
        script = (
            "const FIN={management:" +
            json.dumps({"2026-10": before}, ensure_ascii=False) +
            "};\n" +
            "\n".join(functions) +
            "\nif(fullMonth('2026-10')!==false)"
            "{throw Error('B08 incorrectly accepted cash-through-20');}\n" +
            "FIN.management=" +
            json.dumps({"2026-10": after}, ensure_ascii=False) +
            ";\nif(fullMonth('2026-10')!==true)"
            "{throw Error('B08 incorrectly rejected cash-through-31');}"
        )
        check = subprocess.run(
            [node, "-e", script], capture_output=True, text=True, check=False
        )
        self.assertEqual(check.returncode, 0, check.stderr)


if __name__ == "__main__":
    unittest.main()
