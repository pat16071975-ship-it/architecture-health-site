import json
import os
import re
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g, jsonify
from jinja2 import Environment

os.environ.setdefault("AZBAZE_SECRET_KEY", "test-secret")
os.environ.setdefault("AZBAZE_DB", "/tmp/az-upload-reconcile-tests.db")
os.environ.setdefault("AZBAZE_SITE_ROOT", "/tmp/az-upload-reconcile-site")

import cash_payments
import paid_services
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

    def test_ajax_upload_forms_are_excluded_from_legacy_loading_modal(self):
        html = (
            Path(__file__).parent / "templates" / "base.html"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "form.matches('[data-az-upload-form],[data-az-cash-upload-form]')",
            html,
        )
        self.assertIn(
            "if (form.matches('[data-az-upload-form],[data-az-cash-upload-form]')) return;",
            html,
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

    def test_upload_ui_preserves_paired_clinical_source_and_separate_new_mis(self):
        html = (
            Path(__file__).parent / "templates" / "uploads.html"
        ).read_text(encoding="utf-8")
        self.assertIn("1. Завершённые приёмы", html)
        self.assertIn("2. Выручка по направлениям", html)
        self.assertIn("3. Счета и оплаты", html)
        self.assertIn("2. Выручка по направлениям (новый оплаты)", html)
        self.assertIn("data-az-upload-form", html)
        self.assertIn("data-az-paid-upload-form", html)
        self.assertIn('name="completed"', html)
        self.assertIn('name="services"', html)
        self.assertIn("data-az-completed-upload-form", html)
        self.assertIn("Прежняя парная загрузка", html)
        self.assertIn("/api/uploads/clinical/preview", html)
        self.assertIn("/api/uploads/clinical/commit", html)

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


class W01IndependentUploadStatusesTests(unittest.TestCase):
    """Source-specific dates must not imply all three MIS uploads are complete."""

    @staticmethod
    def conn():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE daily_uploads("
            "data_date TEXT PRIMARY KEY, normalized_json TEXT NOT NULL)"
        )
        return conn

    @staticmethod
    def add(conn, date, normalized):
        payload = normalized if isinstance(normalized, str) else json.dumps(
            normalized, ensure_ascii=False
        )
        conn.execute(
            "INSERT INTO daily_uploads(data_date,normalized_json) VALUES(?,?)",
            (date, payload),
        )

    @staticmethod
    def source_cards():
        html = (Path(__file__).parent / "templates" / "uploads.html").read_text(
            encoding="utf-8"
        )
        forms = {
            key: html.split(marker, 1)[1].split("</form>", 1)[0]
            for key, marker in (
                ("completed", "data-az-completed-upload-form"),
                ("paid", "data-az-paid-upload-form"),
                ("cash", "data-az-cash-upload-form"),
            )
        }
        statuses = {}
        for key, source in forms.items():
            match = re.search(
                r'<div class="status-line">.*?</div>', source, flags=re.DOTALL
            )
            if match is None:
                raise AssertionError(f"No source-specific status for {key}")
            statuses[key] = Environment(autoescape=True).from_string(
                match.group(0)
            )
        return html, statuses

    def test_w01_latest_completed_skips_service_only_and_invalid_rows(self):
        conn = self.conn()
        try:
            self.add(conn, "2026-10-07", {
                "overall": {"2026-10-07": {"Первичные": 5, "Повторные": 5}},
                "visits": [{"date": "2026-10-07", "kind": "Первичные"}],
            })
            # Old paired upload: 08 October has services but no completed source.
            self.add(conn, "2026-10-08", {
                "overall": {"2026-10-08": {}},
                "visits": [],
                "items": [{"date": "2026-10-08", "amount": 800}],
            })
            self.add(conn, "2026-10-09", "{broken-json")
            self.assertEqual(
                daily_upload._latest_confirmed_completed_date(conn), "2026-10-07"
            )
            # A legitimate completed export may explicitly confirm zero visits.
            self.add(conn, "2026-10-10", {
                "overall": {"2026-10-10": {"Первичные": 0, "Повторные": 0}},
                "visits": [],
            })
            self.assertEqual(
                daily_upload._latest_confirmed_completed_date(conn), "2026-10-10"
            )
            self.add(conn, "2026-10-11", {
                "overall": {}, "visits": [{"date": "2026-10-11", "kind": "Повторные"}],
            })
            self.assertEqual(
                daily_upload._latest_confirmed_completed_date(conn), "2026-10-11"
            )
        finally:
            conn.close()

    def test_w01_no_completed_source_never_uses_legacy_service_checkpoint(self):
        conn = self.conn()
        try:
            self.assertIsNone(daily_upload._latest_confirmed_completed_date(conn))
            self.add(conn, "2026-09-30", {
                "overall": {"2026-09-30": {}},
                "items": [{"date": "2026-09-30", "amount": 300}],
                "visits": [],
            })
            self.assertIsNone(daily_upload._latest_confirmed_completed_date(conn))
            self.assertNotIn(
                "_service_through",
                daily_upload._latest_confirmed_completed_date.__code__.co_names,
            )
        finally:
            conn.close()

    def test_w01_uploads_page_provides_three_independent_source_dates(self):
        conn = self.conn()
        try:
            self.add(conn, "2026-10-07", {
                "overall": {"2026-10-07": {"Первичные": 5}}, "visits": [],
            })
            self.add(conn, "2026-10-10", {
                "overall": {"2026-10-10": {}}, "items": [{"amount": 10}],
            })
            app = Flask("w01-source-status-fixture")
            # Registration must not create real local upload DB tables.
            with (
                patch.object(daily_upload.core, "_init_schema", return_value=None),
                patch.object(daily_upload, "permission_required",
                             side_effect=lambda _key: lambda func: func),
            ):
                daily_upload.register_daily_upload(app)
            with app.test_request_context("/uploads/", method="GET"):
                g.user = {"id": 42}
                with (
                    patch.object(daily_upload, "user_permissions",
                                 return_value={"upload_completed", "upload_services"}),
                    patch.object(daily_upload.core, "db", return_value=conn),
                    patch.object(daily_upload.cash_payments, "latest_loaded_date",
                                 return_value="2026-10-04"),
                    patch.object(daily_upload.paid_services, "latest_loaded_date",
                                 return_value="2026-09-30"),
                    patch.object(daily_upload.upload_reconcile, "pending_provider_rows",
                                 return_value=[]),
                    patch.object(daily_upload, "csrf_token", return_value="fixture"),
                    patch.object(daily_upload, "render_template",
                                 side_effect=lambda _name, **kwargs: kwargs),
                ):
                    status = app.view_functions["uploads_page"]()
            self.assertEqual(status["completed_latest_date"], "07.10.2026")
            self.assertEqual(status["cash_latest_date"], "04.10.2026")
            self.assertEqual(status["paid_services_latest_date"], "30.09.2026")
            self.assertNotIn("next_required_date", status)
            self.assertNotIn("cash_next_required_date", status)
            self.assertNotIn("latest", status)
        finally:
            conn.close()

    def test_w01_cards_render_distinct_dates_without_fake_next_upload(self):
        html, status_templates = self.source_cards()
        self.assertEqual(html.count('class="status-line"'), 3)
        self.assertNotIn("НУЖНО ЗАГРУЗИТЬ ДАННЫЕ С", html)
        self.assertNotIn("cash_next_required_date", html)
        self.assertNotIn("next_required_date", html)
        self.assertIn("Прежняя парная загрузка", html)
        self.assertIn("2. Выручка по направлениям (новый оплаты)", html)
        values = {
            "completed_latest_date": "07.10.2026",
            "paid_services_latest_date": "30.09.2026",
            "cash_latest_date": "04.10.2026",
        }
        for key, date in (("completed", "07.10.2026"),
                          ("paid", "30.09.2026"), ("cash", "04.10.2026")):
            rendered = status_templates[key].render(**values)
            self.assertIn(date, rendered)
            self.assertEqual(sum(mark in rendered for mark in values.values()), 1)
            self.assertNotIn("Нужно загрузить с", rendered)
        self.assertIn("последняя", status_templates["cash"].render(**values).lower())
        self.assertIn("срез", status_templates["paid"].render(**values))
        blank = {key: None for key in values}
        self.assertIn("пока не определена", status_templates["completed"].render(**blank))
        self.assertIn("ещё не загружался", status_templates["paid"].render(**blank))
        self.assertIn("пока не определена", status_templates["cash"].render(**blank))


class W01G01UploadsGetNoDDlTests(unittest.TestCase):
    """HTTP GET must not create SQLite tables even if source schemas are absent."""

    @staticmethod
    def connection(*, daily=False):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        if daily:
            conn.execute(
                "CREATE TABLE daily_uploads("
                "data_date TEXT PRIMARY KEY, normalized_json TEXT NOT NULL)"
            )
        return conn

    @staticmethod
    def db_state(conn):
        return (
            conn.total_changes,
            conn.execute("PRAGMA schema_version").fetchone()[0],
            tuple(conn.iterdump()),
        )

    @staticmethod
    def get_page(conn, *, permissions=None):
        if permissions is None:
            permissions = {"upload_completed", "upload_services"}
        app = Flask("w01-g01-real-get")
        @app.before_request
        def _fixture_user():
            g.user = {"id": 42}

        # Only application startup schema-init and the auth decorator are
        # bypassed. All three source readers and pending-provider reader run
        # for real on the same isolated SQLite connection.
        with (
            patch.object(daily_upload.core, "_init_schema", return_value=None),
            patch.object(
                daily_upload, "permission_required",
                side_effect=lambda _section: lambda function: function,
            ),
        ):
            daily_upload.register_daily_upload(app)

        def _render(_template, **values):
            return jsonify({
                "completed": values["completed_latest_date"],
                "cash": values["cash_latest_date"],
                "paid": values["paid_services_latest_date"],
                "pending": values["pending_providers"],
                "history_size": len(values["history"]),
                "can_daily": values["can_daily"],
            })

        with (
            patch.object(daily_upload.core, "db", return_value=conn),
            patch.object(
                daily_upload, "user_permissions", return_value=set(permissions),
            ),
            patch.object(daily_upload, "csrf_token", return_value="test-csrf"),
            patch.object(daily_upload, "render_template", side_effect=_render),
        ):
            return app.test_client().get("/uploads/")

    def test_w01_g01_real_get_on_completely_empty_db_has_no_ddl_dml(self):
        conn = self.connection()
        try:
            before = self.db_state(conn)
            trace = []
            conn.set_trace_callback(trace.append)
            response = self.get_page(conn)
            conn.set_trace_callback(None)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(response.get_json()["completed"], None)
            self.assertEqual(response.get_json()["cash"], None)
            self.assertEqual(response.get_json()["paid"], None)
            self.assertEqual(response.get_json()["pending"], [])
            self.assertEqual(self.db_state(conn), before)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], 0,
            )
            write_words = {"CREATE", "INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "REPLACE"}
            self.assertEqual([
                statement for statement in trace
                if statement.strip().split(None, 1)[0].upper() in write_words
            ], [])
        finally:
            conn.close()

    def test_w01_g01_real_get_on_partial_db_does_not_bootstrap_missing_sources(self):
        conn = self.connection(daily=True)
        try:
            conn.execute(
                "INSERT INTO daily_uploads VALUES(?,?)",
                ("2026-10-07", json.dumps({
                    "overall": {"2026-10-07": {"Первичные": 1}},
                    "visits": [],
                })),
            )
            # Legacy paired service-only activity is not a completed checkpoint.
            conn.execute(
                "INSERT INTO daily_uploads VALUES(?,?)",
                ("2026-10-08", json.dumps({
                    "overall": {"2026-10-08": {}},
                    "visits": [],
                    "items": [{"date": "2026-10-08", "amount": 800}],
                })),
            )
            conn.commit()
            before = self.db_state(conn)
            response = self.get_page(conn)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(response.get_json()["completed"], "07.10.2026")
            self.assertIsNone(response.get_json()["cash"])
            self.assertIsNone(response.get_json()["paid"])
            self.assertEqual(response.get_json()["pending"], [])
            self.assertEqual(self.db_state(conn), before)
            names = [
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            self.assertEqual(names, ["daily_uploads"])
        finally:
            conn.close()

    def test_w01_g01_real_get_preserves_cash_paid_registry_and_history(self):
        conn = self.connection(daily=True)
        try:
            conn.execute(
                "INSERT INTO daily_uploads VALUES(?,?)",
                ("2026-10-07", json.dumps({
                    "overall": {"2026-10-07": {"Повторные": 2}}, "visits": [],
                })),
            )
            cash_payments.init_schema(conn)
            paid_services.init_schema(conn)
            upload_reconcile.init_schema(conn)
            conn.execute(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, full_name TEXT)"
            )
            conn.execute(
                "CREATE TABLE upload_log ("
                "id INTEGER PRIMARY KEY, data_date TEXT, uploaded_by INTEGER)"
            )
            conn.execute("INSERT INTO users VALUES(?,?)", (42, "Синтетический пользователь"))
            conn.execute(
                "INSERT INTO upload_log VALUES(?,?,?)", (1, "2026-10-07", 42)
            )
            conn.execute(
                "INSERT INTO cash_receipts_daily VALUES(?,?,?,?,?,?)",
                ("2026-10-04", json.dumps({"cashTotal": 9000}),
                 "cash.xlsx", "source-hash", 42, "synthetic-now"),
            )
            conn.execute(
                "INSERT INTO service_payment_snapshots VALUES(?,?,?,?,?,?,?)",
                ("2026-09-30", "2026-09", json.dumps({"paid": 8000}),
                 "mis.xlsx", "source-hash", 42, "synthetic-now"),
            )
            conn.execute(
                "INSERT INTO provider_registry VALUES(?,?,?,?,?,?,?,?)",
                ("Unknown U.", "Unknown U.", "pending", "old.xlsx",
                 "synthetic-before", "synthetic-before", None, None),
            )
            conn.execute(
                "CREATE TABLE report_data (date TEXT PRIMARY KEY,payload TEXT)"
            )
            conn.execute(
                "INSERT INTO report_data VALUES(?,?)",
                ("2026-09-30", json.dumps({
                    "cashTotal": 9000, "cashOOO": 4000, "cashIP": 5000,
                    "primary": 2, "repeat": 7,
                })),
            )
            conn.commit()
            before = self.db_state(conn)
            response = self.get_page(
                conn,
                permissions={"upload_completed", "upload_services", "upload_history"},
            )
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            data = response.get_json()
            self.assertEqual(
                (data["completed"], data["cash"], data["paid"]),
                ("07.10.2026", "04.10.2026", "30.09.2026"),
            )
            self.assertEqual(data["history_size"], 1)
            self.assertEqual(data["pending"][0]["source_name"], "Unknown U.")
            self.assertEqual(data["pending"][0]["source_filename"], "old.xlsx")
            self.assertEqual(self.db_state(conn), before)
        finally:
            conn.close()

    def test_w01_g01_real_get_succeeds_with_readonly_file_sqlite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "w01-g01-readonly.sqlite"
            first = sqlite3.connect(path)
            try:
                first.execute(
                    "CREATE TABLE daily_uploads("
                    "data_date TEXT PRIMARY KEY, normalized_json TEXT NOT NULL)"
                )
                first.execute(
                    "INSERT INTO daily_uploads VALUES(?,?)",
                    ("2026-10-07", json.dumps({
                        "overall": {"2026-10-07": {"Первичные": 1}},
                    })),
                )
                first.commit()
            finally:
                first.close()
            conn = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                before = self.db_state(conn)
                response = self.get_page(conn)
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                self.assertEqual(response.get_json()["completed"], "07.10.2026")
                self.assertIsNone(response.get_json()["cash"])
                self.assertIsNone(response.get_json()["paid"])
                self.assertEqual(self.db_state(conn), before)
            finally:
                conn.close()

    def test_w01_g01_default_status_getters_still_initialize_on_write_path(self):
        conn = self.connection()
        try:
            self.assertIsNone(cash_payments.latest_loaded_date(conn))
            self.assertIsNone(paid_services.latest_loaded_date(conn))
            self.assertEqual(upload_reconcile.pending_provider_rows(conn), [])
            names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for name in (
                "cash_receipts_daily", "service_payment_snapshots",
                "provider_registry",
            ):
                self.assertIn(name, names)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
