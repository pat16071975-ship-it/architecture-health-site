import unittest
import json
import sqlite3

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


if __name__ == "__main__":
    unittest.main()
