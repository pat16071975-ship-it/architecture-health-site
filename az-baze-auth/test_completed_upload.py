import unittest
import json
import sqlite3
from contextlib import ExitStack
from unittest.mock import patch

from flask import Flask, g
import upload_integrity

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

    def _run(self, prepared, decision=None, cash_replay=None):
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
                    (completed_upload.cash_payments, "overlay_record_map", lambda _conn, _month, records: records),
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
        with self.assertRaisesRegex(ValueError, "сохранённый показатель"):
            self._run(self._prepared(day, primary=2), decision="use_new")
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
        self.assertEqual(added["labOrders"], 1)
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


if __name__ == "__main__":
    unittest.main()
