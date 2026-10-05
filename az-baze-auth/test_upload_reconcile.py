import json
import os
import sqlite3
import unittest
from types import SimpleNamespace

os.environ.setdefault("AZBAZE_SECRET_KEY", "test-secret")
os.environ.setdefault("AZBAZE_DB", "/tmp/az-upload-reconcile-tests.db")
os.environ.setdefault("AZBAZE_SITE_ROOT", "/tmp/az-upload-reconcile-site")

import cash_payments
import upload_reconcile


class FakeCash:
    def __init__(self):
        self.maps = None

    def configure_providers(self, dent, structure, lab):
        self.maps = (dict(dent), dict(structure), dict(lab))


class UploadReconcileTests(unittest.TestCase):
    def conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    def ident(self):
        return SimpleNamespace(
            DENTISTS={"Dent D.": "Dent Doctor"},
            STRUCTURE_DOCTORS={"Struct S.": "Struct Doctor"},
            LAB_DOCTORS={"Lab L.": "Lab Doctor"},
            KNOWN_STAFF={"Dent D.", "Struct S.", "Lab L.", "Администраторы -."},
        )

    def test_unknown_provider_is_persisted_resolved_and_applied(self):
        conn = self.conn()
        ident = self.ident()
        fake_cash = FakeCash()
        upload_reconcile.seed_defaults(conn, ident)
        period = [
            {
                "normalized": {
                    "items": [{"staff": "Иванова И. И."}],
                    "doctors": {
                        "2026-09-10": {
                            "Первичные": {"Иванова И. И.": 1}
                        }
                    },
                }
            }
        ]
        unknown = upload_reconcile.detect_unknown_providers(period, ident, conn)
        self.assertEqual(unknown, ["Иванова И. И."])

        upload_reconcile.record_pending_providers(
            conn, unknown, "services.xlsx", actor_id=1
        )
        self.assertEqual(
            [row["source_name"] for row in upload_reconcile.pending_provider_rows(conn)],
            ["Иванова И. И."],
        )

        upload_reconcile.resolve_providers(
            conn,
            {"Иванова И. И.": {"direction": "structure", "display_name": "Иванова И. И."}},
            1,
        )
        upload_reconcile.refresh_runtime(conn, ident, fake_cash)

        self.assertEqual(
            ident.STRUCTURE_DOCTORS["Иванова И. И."],
            "Иванова И. И.",
        )
        self.assertEqual(upload_reconcile.pending_provider_rows(conn), [])
        self.assertIn("Иванова И. И.", fake_cash.maps[1])

    def test_clinical_compare_detects_identical_conflict_new_and_removed(self):
        conn = self.conn()
        conn.execute(
            """
            CREATE TABLE daily_uploads(
                data_date TEXT PRIMARY KEY,
                normalized_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO daily_uploads(data_date,normalized_json) VALUES(?,?)",
            ("2026-09-01", json.dumps({"v": 1})),
        )
        conn.execute(
            "INSERT INTO daily_uploads(data_date,normalized_json) VALUES(?,?)",
            ("2026-09-02", json.dumps({"v": 2})),
        )
        conn.execute(
            "INSERT INTO daily_uploads(data_date,normalized_json) VALUES(?,?)",
            ("2026-09-03", json.dumps({"v": 3})),
        )
        period = [
            {"data_date": "2026-09-01", "normalized": {"v": 1}},
            {"data_date": "2026-09-02", "normalized": {"v": 20}},
            {"data_date": "2026-09-04", "normalized": {"v": 4}},
        ]
        result = upload_reconcile.compare_clinical(conn, period, None)
        self.assertEqual(result["identical"], ["2026-09-01"])
        self.assertEqual(result["conflict"], ["2026-09-02"])
        self.assertEqual(result["new"], ["2026-09-04"])
        self.assertEqual(result["removed"], ["2026-09-03"])

    def test_clinical_compare_marks_legacy_covered_date_as_historical_conflict(self):
        conn = self.conn()
        conn.execute(
            "CREATE TABLE daily_uploads(data_date TEXT PRIMARY KEY,normalized_json TEXT NOT NULL)"
        )
        period = [{"data_date": "2026-09-05", "normalized": {"v": 1}}]
        result = upload_reconcile.compare_clinical(
            conn, period, "2026-09-30"
        )
        self.assertEqual(result["historical"], ["2026-09-05"])
        self.assertEqual(result["new"], [])

    def test_cash_compare_detects_removed_and_changed_days(self):
        conn = self.conn()
        cash_payments.init_schema(conn)
        upload_reconcile.init_schema(conn)
        base = {**cash_payments._blank_day(), "cashTotal": 100, "cashUnallocated": 100}
        conn.execute(
            """
            INSERT INTO cash_receipts_daily(
                data_date,payload_json,source_filename,source_sha256,imported_by,imported_at
            ) VALUES(?,?,?,?,?,?)
            """,
            ("2026-09-01", json.dumps(base), "old.xlsx", "sha", 1, "t"),
        )
        conn.execute(
            """
            INSERT INTO cash_receipts_daily(
                data_date,payload_json,source_filename,source_sha256,imported_by,imported_at
            ) VALUES(?,?,?,?,?,?)
            """,
            ("2026-09-02", json.dumps(base), "old.xlsx", "sha", 1, "t"),
        )
        changed = {**base, "cashTotal": 200, "cashUnallocated": 200}
        incoming = {
            "2026-09-01": changed,
            "2026-09-03": base,
        }
        result = upload_reconcile.compare_cash(conn, incoming)
        self.assertEqual(result["conflict"], ["2026-09-01"])
        self.assertEqual(result["new"], ["2026-09-03"])
        self.assertEqual(result["removed"], ["2026-09-02"])

    def test_cash_reconciliation_requires_visible_unallocated_component(self):
        payload = {
            "cashTotal": 1000,
            "dentCashOOO": 300,
            "dentCashIP": 100,
            "clinicCashOOO": 200,
            "clinicCashIP": 0,
            "labCashOOO": 50,
            "labCashIP": 50,
            "cashUnallocated": 300,
        }
        check = upload_reconcile.assert_cash_reconciliation(payload)
        self.assertEqual(check["dent"], 400)
        self.assertEqual(check["structure"], 200)
        self.assertEqual(check["lab"], 100)
        self.assertEqual(check["unallocated"], 300)
        self.assertEqual(check["delta"], 0)

        bad = dict(payload)
        bad["cashUnallocated"] = 250
        with self.assertRaisesRegex(ValueError, "Нарушен баланс"):
            upload_reconcile.assert_cash_reconciliation(bad)


if __name__ == "__main__":
    unittest.main()
