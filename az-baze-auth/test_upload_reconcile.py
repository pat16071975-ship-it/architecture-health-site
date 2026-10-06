import json
import os
import sqlite3
import unittest
from types import SimpleNamespace
from pathlib import Path

os.environ.setdefault("AZBAZE_SECRET_KEY", "test-secret")
os.environ.setdefault("AZBAZE_DB", "/tmp/az-upload-reconcile-tests.db")
os.environ.setdefault("AZBAZE_SITE_ROOT", "/tmp/az-upload-reconcile-site")

import cash_payments
import upload_reconcile
import daily_upload


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

    def test_historical_service_month_is_cleared_before_rebuild(self):
        data = {
            "directions": {
                "Стоматология": {
                    "doctors": {
                        "Doctor": {
                            "Category": [[1, 100], [2, 200], [3, 300]]
                        }
                    }
                },
                "Отделение структуры": {
                    "doctors": {
                        "Struct": {
                            "Category": [[4, 400], [5, 500], [6, 600]]
                        }
                    }
                },
            }
        }

        daily_upload._reset_service_month(data, 1)

        self.assertEqual(
            data["directions"]["Стоматология"]["doctors"]["Doctor"]["Category"],
            [[1, 100], [0, 0], [3, 300]],
        )
        self.assertEqual(
            data["directions"]["Отделение структуры"]["doctors"]["Struct"]["Category"],
            [[4, 400], [0, 0], [6, 600]],
        )

    def test_legacy_upload_page_post_collapses_to_clean_get(self):
        source = (
            Path(__file__).parent / "daily_upload.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'if request.method == "POST":\n'
            '            return redirect(url_for("uploads_page"), code=303)',
            source,
        )
        route_tail = source.split(
            '@app.route("/uploads/", methods=["GET", "POST"])',
            1,
        )[1]
        page_block = route_tail.split(
            'return render_template(',
            1,
        )[0]
        self.assertNotIn("_process_period_upload(", page_block)

    def test_provider_save_redirects_with_clean_get(self):
        html = (
            Path(__file__).parent / "templates" / "uploads.html"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "setTimeout(()=>location.replace('/uploads/'),1200)",
            html,
        )
        self.assertNotIn(
            "setTimeout(()=>location.reload(),1200)",
            html,
        )

    def test_upload_ui_uses_approved_revenue_by_directions_label(self):
        html = (
            Path(__file__).parent / "templates" / "uploads.html"
        ).read_text(encoding="utf-8")
        self.assertIn("2. Выручка по направлениям", html)
        self.assertNotIn("2. Выполненные услуги", html)
        self.assertIn(
            "Отдельные даты могут отсутствовать в одном из двух файлов",
            html,
        )

    def test_period_union_allows_dates_present_in_only_one_source(self):
        overall = {
            "2026-09-02": {"Первичные": 1},
        }
        visits = [
            {
                "date": "2026-09-02",
                "patient": "Пациент П. П.",
                "kind": "Первичные",
            }
        ]
        items = [
            {
                "staff": "Чирков М. С.",
                "patient": "Пациент Д. Д.",
                "date": "2026-09-01",
                "group": "Терапия",
                "service": "Приём",
                "qty": 1,
                "amount": 1000,
                "invoice": "1",
            }
        ]

        period = daily_upload._split_period(overall, visits, items, [])

        self.assertEqual(
            [row["data_date"] for row in period],
            ["2026-09-01", "2026-09-02"],
        )

        first = period[0]["normalized"]
        second = period[1]["normalized"]

        self.assertEqual(first["overall"], {"2026-09-01": {}})
        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(first["visits"], [])

        self.assertEqual(second["overall"], {"2026-09-02": {"Первичные": 1}})
        self.assertEqual(second["items"], [])
        self.assertEqual(len(second["visits"]), 1)

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
