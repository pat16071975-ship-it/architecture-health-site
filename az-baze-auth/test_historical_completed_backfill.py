import json
import sqlite3
import unittest
from pathlib import Path

import historical_completed_backfill as history


class HistoricalCompletedBackfillTests(unittest.TestCase):
    def test_exact_duplicate_overlap_is_collapsed(self):
        left = {
            "data_date": "2026-10-27",
            "overall": {"2026-10-27": {"Первичные": 1, "Повторные": 1}},
            "visits": [
                {"date": "2026-10-27", "patient": "Тест А.", "kind": "Первичные"},
                {"date": "2026-10-27", "patient": "Тест Б.", "kind": "Повторные"},
            ],
            "source_filename": "left.xlsx",
        }
        right = json.loads(json.dumps(left, ensure_ascii=False))
        right["source_filename"] = "right.xlsx"
        merged, action = history.merge_history_day(left, right)
        self.assertIs(merged, left)
        self.assertEqual(action, "duplicate")

    def test_strict_subset_prefers_fuller_source(self):
        partial = {
            "data_date": "2026-10-27",
            "overall": {"2026-10-27": {"Первичные": 2}},
            "visits": [
                {"date": "2026-10-27", "patient": "Тест А.", "kind": "Первичные"},
                {"date": "2026-10-27", "patient": "Тест Б.", "kind": "Первичные"},
            ],
            "source_filename": "primary-only.xlsx",
        }
        full = {
            "data_date": "2026-10-27",
            "overall": {
                "2026-10-27": {"Первичные": 2, "Повторные": 2}
            },
            "visits": [
                *partial["visits"],
                {"date": "2026-10-27", "patient": "Тест В.", "kind": "Повторные"},
                {"date": "2026-10-27", "patient": "Тест Г.", "kind": "Повторные"},
            ],
            "source_filename": "full.xlsx",
        }
        merged, action = history.merge_history_day(partial, full)
        self.assertIs(merged, full)
        self.assertEqual(action, "superset_replaced")

    def test_non_subset_overlap_fails_closed(self):
        left = {
            "data_date": "2026-10-27",
            "overall": {"2026-10-27": {"Первичные": 1}},
            "visits": [
                {"date": "2026-10-27", "patient": "Тест А.", "kind": "Первичные"}
            ],
            "source_filename": "left.xlsx",
        }
        right = {
            "data_date": "2026-10-27",
            "overall": {"2026-10-27": {"Первичные": 1}},
            "visits": [
                {"date": "2026-10-27", "patient": "Тест Б.", "kind": "Первичные"}
            ],
            "source_filename": "right.xlsx",
        }
        with self.assertRaises(ValueError):
            history.merge_history_day(left, right)

    def test_management_merge_changes_only_completed_counters(self):
        existing = {
            "date": "2026-10-31",
            "factMedicine": 123456.0,
            "factLab": 10000.0,
            "cashTotal": 110000.0,
            "paidServicesTotal": 90000.0,
            "billedDentists": {"Doctor": 55555.0},
            "plan": 700000.0,
            "_source": "existing-source",
        }
        rebuilt = {
            "date": "2026-10-31",
            "primary": 15,
            "repeat": 101,
            "factMedicine": 0.0,
            "cashTotal": 0.0,
        }
        merged = history.merge_completed_management(
            existing, rebuilt, "historical-completed"
        )
        self.assertEqual(merged["primary"], 15)
        self.assertEqual(merged["repeat"], 101)
        self.assertEqual(merged["factMedicine"], 123456.0)
        self.assertEqual(merged["factLab"], 10000.0)
        self.assertEqual(merged["cashTotal"], 110000.0)
        self.assertEqual(merged["paidServicesTotal"], 90000.0)
        self.assertEqual(merged["billedDentists"], {"Doctor": 55555.0})
        self.assertEqual(merged["plan"], 700000.0)
        self.assertEqual(merged["_source"], "existing-source")
        self.assertEqual(merged["_completed_source"], "historical-completed")

    def test_compare_history_never_deletes_missing_dates(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE daily_uploads(
                data_date TEXT PRIMARY KEY,
                normalized_json TEXT NOT NULL
            )
            """
        )
        old = {
            "data_date": "2026-10-30",
            "overall": {"2026-10-30": {"Повторные": 1}},
            "visits": [
                {"date": "2026-10-30", "patient": "Старый А.", "kind": "Повторные"}
            ],
        }
        conn.execute(
            "INSERT INTO daily_uploads(data_date,normalized_json) VALUES(?,?)",
            ("2026-10-30", json.dumps(old, ensure_ascii=False)),
        )
        incoming_day = {
            "data_date": "2026-10-31",
            "overall": {"2026-10-31": {"Первичные": 1}},
            "visits": [
                {"date": "2026-10-31", "patient": "Новый А.", "kind": "Первичные"}
            ],
        }
        incoming_day["completed_hash"] = history.daily_upload._payload_hash(
            {
                "data_date": incoming_day["data_date"],
                "overall": incoming_day["overall"],
                "visits": incoming_day["visits"],
            }
        )
        prepared = {
            "months": ["2026-10"],
            "days": {"2026-10-31": incoming_day},
        }
        comparison = history.compare_history(conn, prepared)
        self.assertEqual(comparison["new"], ["2026-10-31"])
        self.assertNotIn("removed", comparison)
        self.assertIsNotNone(
            conn.execute(
                "SELECT 1 FROM daily_uploads WHERE data_date='2026-10-30'"
            ).fetchone()
        )

    def test_server_keeps_paired_upload_guard_and_registers_migration_only(self):
        server = (Path(__file__).resolve().parent / "server.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(
            "completed_upload.register_completed_upload(app)",
            server,
        )
        self.assertIn("historical_completed_backfill.register(app)", server)

        uploads = (
            Path(__file__).resolve().parent / "templates" / "uploads.html"
        ).read_text(encoding="utf-8")
        self.assertIn("data-az-upload-form", uploads)
        self.assertIn("name=\"completed\"", uploads)
        self.assertIn("name=\"services\"", uploads)
        self.assertNotIn("data-az-completed-history-form", uploads)

        injected = history.inject_uploads_page(uploads, authorized=True, csrf_value='test-csrf')
        self.assertIn("data-az-completed-history-form", injected)
        self.assertIn("name=\"bundle\"", injected)
        self.assertIn("/api/uploads/completed-history/preview", injected)
        self.assertIn("/api/uploads/completed-history/commit", injected)
        self.assertIn(
            "файл «Выручка по направлениям» не требуется.",
            injected,
        )


if __name__ == "__main__":
    unittest.main()
