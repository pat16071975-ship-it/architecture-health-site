import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, g
from openpyxl import Workbook
from werkzeug.exceptions import Forbidden

import paid_services
import paid_services_upload
import upload_reconcile


class PaidServicesTests(unittest.TestCase):
    def workbook_bytes(self, rows):
        wb = Workbook()
        ws = wb.active
        ws.title = "Выручка по направлениям"
        ws.append([
            "Группа услуг",
            "Услуги",
            None,
            None,
            " Задолженность на нач. периода ",
            "Сумма со скидкой",
            " Оплачено ",
            " Задолженность на конец периода ",
        ])
        for row in rows:
            ws.append(row)
        stream = io.BytesIO()
        wb.save(stream)
        wb.close()
        return stream.getvalue()

    def reference_report(self):
        return self.workbook_bytes([
            ["Итого", None, None, None, 135, 400, 392, 143],
            ["Авансы", None, None, None, -50, "-", 20, -70],
            ["Услуги", None, None, None, 185, 400, 372, 213],
            ["Dent D.", None, None, None, 0, 200, 225, -25],
            ["Пациент Один", None, None, None, 0, 200, 225, -25],
            ["Счет №100 от 10.09.2026 10:00:00", None, None, None, "-", 200, 225, -25],
            ["Терапия", "10.09.2026 10:00", "Услуга 1", 1, "-", 200, 225, -25],
            ["Struct S.", None, None, None, 50, 100, 100, 50],
            ["Пациент Два", None, None, None, 50, 100, 100, 50],
            ["Счет №101 от 11.09.2026 11:00:00", None, None, None, "-", 100, 100, 0],
            ["Структура", "11.09.2026 11:00", "Услуга 2", 1, "-", 100, 100, 0],
            ["Lab L.", None, None, None, 100, 50, 40, 110],
            ["Пациент Три", None, None, None, 100, 50, 40, 110],
            ["Счет №102 от 12.09.2026 12:00:00", None, None, None, "-", 50, 40, 10],
            ["Лаборатория", "12.09.2026 12:00", "Работа", 1, "-", 50, 40, 10],
            ["Unknown U.", None, None, None, 25, 40, 7, 58],
            ["Пациент Четыре", None, None, None, 25, 40, 7, 58],
            ["Счет №103 от 30.09.2026 18:00:00", None, None, None, "-", 40, 7, 33],
            ["Прочее", "30.09.2026 18:00", "Услуга 3", 1, "-", 40, 7, 33],
            # Non-clinical staff-like header is intentionally not accepted by
            # STAFF_HEADER_RE and remains in provider_residual.
            ["Старшийадминистратор -.", None, None, None, 10, 10, 0, 20],
        ])

    def test_parse_new_report_preserves_provider_paid_and_debt(self):
        parsed = paid_services.parse_bytes(self.reference_report(), "paid.xlsx", known_staff={"Старшийадминистратор -."})
        self.assertEqual(parsed["source_type"], "paid_services_v1")
        self.assertEqual(parsed["sheet"], "Выручка по направлениям")
        self.assertEqual(parsed["month"], "2026-09")
        self.assertEqual(parsed["period_end"], "2026-09-30")
        self.assertEqual(parsed["totals"]["paid"], 372)
        self.assertEqual(parsed["providers"]["Dent D."]["paid"], 225)
        self.assertEqual(parsed["providers"]["Struct S."]["closing"], 50)
        self.assertEqual(parsed["providers"]["Unknown U."]["paid"], 7)
        self.assertEqual(parsed["provider_residual"]["billed"], 0)
        self.assertEqual(parsed["providers"]["Старшийадминистратор -."]["billed"], 10)
        self.assertEqual(parsed["provider_residual"]["paid"], 0)
        self.assertEqual(len(parsed["items"]), 4)
        self.assertEqual(parsed["items"][0]["amount"], 200)
        self.assertEqual(parsed["items"][0]["paid_amount"], 225)
        self.assertEqual(parsed["items"][0]["invoice"], "100")

    def test_compact_snapshot_keeps_service_analytics_without_patient_names(self):
        parsed = paid_services.parse_bytes(
            self.reference_report(),
            "paid.xlsx",
            known_staff={"Старшийадминистратор -."},
        )
        compact = paid_services.compact_report(parsed)
        serialized = json.dumps(compact, ensure_ascii=False)
        self.assertEqual(compact["version"], 2)
        self.assertNotIn("Пациент Один", serialized)
        self.assertNotIn("Пациент Два", serialized)
        self.assertTrue(compact["service_items"])
        self.assertTrue(compact["visit_links"])
        self.assertEqual(len(compact["invoices"]), 4)

    def test_visit_attribution_uses_hashed_patient_links(self):
        parsed = paid_services.parse_bytes(
            self.reference_report(),
            "paid.xlsx",
            known_staff={"Старшийадминистратор -."},
        )
        compact = paid_services.compact_report(parsed)
        doctors, matched, unmatched = paid_services.visit_doctors(
            compact,
            [
                {"date": "2026-09-10", "patient": "Пациент Один", "kind": "Первичные"},
                {"date": "2026-09-11", "patient": "Пациент Два", "kind": "Повторные"},
                {"date": "2026-09-20", "patient": "Нет В. Базе", "kind": "Первичные"},
            ],
            {"Dent D.": "Dent Doctor"},
            {"Struct S.": "Struct Doctor"},
        )
        self.assertEqual(matched, 2)
        self.assertEqual(unmatched, 1)
        self.assertEqual(doctors["2026-09-10"]["Первичные"]["Dent D."], 1)
        self.assertEqual(doctors["2026-09-11"]["Повторные"]["Struct S."], 1)

    def test_visit_attribution_does_not_guess_between_two_directions(self):
        patient = "Alpha A."
        snapshot = {
            "visit_links": {
                "2026-09-01|" + paid_services._patient_token(patient): [
                    "Dent D.",
                    "Struct S.",
                ],
            },
            "invoices": [],
        }
        doctors, matched, unmatched = paid_services.visit_doctors(
            snapshot,
            [{"date": "2026-09-01", "patient": patient, "kind": "Первичные"}],
            {"Dent D.": "Dent Doctor"},
            {"Struct S.": "Struct Doctor"},
        )
        self.assertEqual(doctors, {})
        self.assertEqual(matched, 0)
        self.assertEqual(unmatched, 1)

    def test_visit_attribution_is_cumulative_month_to_date(self):
        p1 = "Alpha A."
        p2 = "Beta B."
        snapshot = {
            "visit_links": {
                "2026-09-01|" + paid_services._patient_token(p1): ["Dent D."],
                "2026-09-02|" + paid_services._patient_token(p2): ["Struct S."],
            },
            "invoices": [],
        }
        rows = [
            {
                "data_date": "2026-09-01",
                "overall": {"2026-09-01": {"Первичные": 1}},
                "visits": [{"date": "2026-09-01", "patient": p1, "kind": "Первичные"}],
            },
            {
                "data_date": "2026-09-02",
                "overall": {"2026-09-02": {"Повторные": 1}},
                "visits": [{"date": "2026-09-02", "patient": p2, "kind": "Повторные"}],
            },
        ]
        record = {"date": "2026-09-02"}
        paid_services.apply_visit_attribution(
            record,
            snapshot,
            rows,
            {"Dent D.": "Dent Doctor"},
            {"Struct S.": "Struct Doctor"},
            {},
        )
        self.assertEqual(record["primary"], 1)
        self.assertEqual(record["repeat"], 1)
        self.assertEqual(record["dentPrimary"], 1)
        self.assertEqual(record["clinicRepeat"], 1)
        self.assertEqual(record["paidVisitMatched"], 2)
        self.assertEqual(record["_uploadControl"]["unassignedPrimary"], 0)
        self.assertEqual(record["_uploadControl"]["unassignedRepeat"], 0)

    def test_unmatched_visit_keeps_completed_source_total(self):
        p1 = "Alpha A."
        missing = "Missing M."
        snapshot = {
            "visit_links": {
                "2026-09-01|" + paid_services._patient_token(p1): ["Dent D."],
            },
            "invoices": [],
        }
        rows = [
            {
                "data_date": "2026-09-01",
                "overall": {"2026-09-01": {"Первичные": 2}},
                "visits": [
                    {"date": "2026-09-01", "patient": p1, "kind": "Первичные"},
                    {"date": "2026-09-01", "patient": missing, "kind": "Первичные"},
                ],
            },
        ]
        record = {"date": "2026-09-01"}
        paid_services.apply_visit_attribution(
            record,
            snapshot,
            rows,
            {"Dent D.": "Dent Doctor"},
            {},
            {},
        )
        self.assertEqual(record["primary"], 2)
        self.assertEqual(record["dentPrimary"], 1)
        self.assertEqual(record["clinicPrimary"], 0)
        self.assertEqual(record["paidVisitMatched"], 1)
        self.assertEqual(record["paidVisitUnmatched"], 1)
        self.assertEqual(record["_uploadControl"]["unassignedPrimary"], 1)

    def test_direction_summary_uses_paid_not_billed_and_keeps_unknown_separate(self):
        parsed = paid_services.parse_bytes(self.reference_report(), "paid.xlsx", known_staff={"Старшийадминистратор -."})
        summary = paid_services.direction_summary(
            parsed,
            {"Dent D.": "Dent Doctor"},
            {"Struct S.": "Struct Doctor"},
            {"Lab L.": "Lab Doctor"},
            ignored={"Старшийадминистратор -."},
        )
        self.assertEqual(summary["dentists"]["Dent Doctor"], 225)
        self.assertEqual(summary["clinicDocs"]["Struct Doctor"], 100)
        self.assertEqual(summary["labDocs"]["Lab Doctor"], 40)
        self.assertEqual(summary["dentPaid"], 225)
        self.assertEqual(summary["structurePaid"], 100)
        self.assertEqual(summary["labPaid"], 40)
        self.assertEqual(summary["classifiedPaid"], 365)
        self.assertEqual(summary["unknownProviders"]["Unknown U."]["paid"], 7)
        self.assertEqual(summary["unknownPaid"], 7)
        self.assertEqual(summary["paidTotal"], 372)
        self.assertEqual(summary["unclassifiedPaid"], 7)

    def test_ignored_provider_is_not_reported_as_unknown(self):
        parsed = paid_services.parse_bytes(self.reference_report(), "paid.xlsx", known_staff={"Старшийадминистратор -."})
        summary = paid_services.direction_summary(
            parsed,
            {"Dent D.": "Dent Doctor"},
            {"Struct S.": "Struct Doctor"},
            {"Lab L.": "Lab Doctor"},
            ignored={"Unknown U.", "Старшийадминистратор -."},
        )
        self.assertEqual(summary["unknownProviders"], {})
        self.assertEqual(summary["ignoredPaid"], 7)
        self.assertEqual(summary["unclassifiedPaid"], 7)

    def test_opening_debt_payment_is_kept_at_provider_level_without_fake_service(self):
        raw = self.workbook_bytes([
            ["Итого", None, None, None, 100, 0, 60, 40],
            ["Услуги", None, None, None, 100, 0, 60, 40],
            ["Dent D.", None, None, None, 100, 0, 60, 40],
            ["Неоплаченные услуги на начало периода", None, None, None, 100, "-", 60, 40],
            ["Неоплаченные услуги на начало периода", "Пациент П.", None, None, 100, "-", 60, 40],
            # One zero-cost current service supplies the selected calendar month.
            ["Пациент Новый", None, None, None, 0, 0, 0, 0],
            ["Счет №104 от 30.09.2026 12:00:00", None, None, None, "-", 0, 0, 0],
            ["Консультации", "30.09.2026 12:00", "Повторный осмотр-приём", 1, "-", 0, 0, 0],
        ])
        parsed = paid_services.parse_bytes(raw, "paid.xlsx")
        self.assertEqual(parsed["providers"]["Dent D."]["paid"], 60)
        self.assertEqual(len(parsed["items"]), 1)
        self.assertEqual(parsed["items"][0]["paid_amount"], 0)

    def test_report_balance_is_fail_closed(self):
        raw = self.workbook_bytes([
            ["Итого", None, None, None, 0, 100, 90, 0],
            ["Услуги", None, None, None, 0, 100, 90, 0],
            ["Dent D.", None, None, None, 0, 100, 90, 0],
            ["Пациент", None, None, None, 0, 100, 90, 0],
            ["Счет №1 от 30.09.2026 12:00:00", None, None, None, "-", 100, 90, 10],
            ["Терапия", "30.09.2026 12:00", "Услуга", 1, "-", 100, 90, 10],
        ])
        with self.assertRaisesRegex(ValueError, "Нарушен баланс"):
            paid_services.parse_bytes(raw, "bad.xlsx")

    def test_multi_month_report_is_rejected(self):
        raw = self.workbook_bytes([
            ["Итого", None, None, None, 0, 200, 200, 0],
            ["Услуги", None, None, None, 0, 200, 200, 0],
            ["Dent D.", None, None, None, 0, 200, 200, 0],
            ["Пациент", None, None, None, 0, 200, 200, 0],
            ["Счет №1 от 30.09.2026 12:00:00", None, None, None, "-", 100, 100, 0],
            ["Терапия", "30.09.2026 12:00", "Услуга", 1, "-", 100, 100, 0],
            ["Счет №2 от 01.10.2026 12:00:00", None, None, None, "-", 100, 100, 0],
            ["Терапия", "01.10.2026 12:00", "Услуга", 1, "-", 100, 100, 0],
        ])
        with self.assertRaisesRegex(ValueError, "один календарный месяц"):
            paid_services.parse_bytes(raw, "two-months.xlsx")


class W04PaidServicesPreviewNoWriteTests(unittest.TestCase):
    """Exercise real MIS XLS parsing/preview on an isolated SQLite database."""

    @staticmethod
    def fixture(*, initialized=True):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        if initialized:
            paid_services.init_schema(conn)
            upload_reconcile.init_schema(conn)
            conn.execute("CREATE TABLE report_data(date TEXT PRIMARY KEY, payload TEXT)")
            conn.execute(
                "INSERT INTO report_data VALUES (?,?)",
                ("2026-09-30", json.dumps({
                    "cashTotal": 190000, "cashOOO": 120000, "cashIP": 70000,
                    "primary": 5, "repeat": 5,
                })),
            )
        ident = SimpleNamespace(
            DENTISTS={"Dent D.": "Dent Doctor"},
            STRUCTURE_DOCTORS={"Struct S.": "Struct Doctor"},
            LAB_DOCTORS={"Lab L.": "Lab Doctor"},
            KNOWN_STAFF={"Dent D.", "Struct S.", "Lab L.",
                         "Старшийадминистратор -."},
        )
        if initialized:
            upload_reconcile.seed_defaults(conn, ident)
            conn.commit()
        return conn, ident

    @staticmethod
    def db_state(conn):
        return conn.total_changes, tuple(conn.iterdump())

    def invoke(self, operation, conn, ident, permissions, *,
               filename="preview.xlsx", raw=None, decisions=None):
        if raw is None:
            raw = PaidServicesTests().reference_report()
        data = {"paid_services": (io.BytesIO(raw), filename)}
        if decisions is not None:
            data["provider_decisions"] = json.dumps(decisions, ensure_ascii=False)
        app = Flask("w04-paid-preview")
        with app.test_request_context(
            "/api/uploads/paid-services/" + operation,
            method="POST", data=data, content_type="multipart/form-data",
        ):
            g.user = {"id": 42}
            with (
                patch.object(paid_services_upload, "db", return_value=conn),
                patch.object(paid_services_upload, "user_permissions",
                             return_value=set(permissions)),
                patch.object(paid_services_upload, "ident_import", ident),
                patch.object(paid_services_upload, "cash_payments",
                             SimpleNamespace(configure_providers=lambda *a: None)),
                patch.object(paid_services_upload, "audit", return_value=None),
                patch.object(paid_services_upload, "iso_now",
                             return_value="2026-10-10T12:00:00+00:00"),
            ):
                return getattr(paid_services_upload, "_" + operation)()

    def test_w04_unknown_preview_is_no_write_without_replace_right(self):
        conn, ident = self.fixture()
        try:
            original = self.db_state(conn)
            response = self.invoke("preview", conn, ident, {"upload_services"})
            self.assertEqual(response["status"], "preview")
            self.assertEqual(response["unknown_providers"], ["Unknown U."])
            self.assertTrue(response["requires_provider_mapping"])
            self.assertFalse(response["can_replace"])
            self.assertEqual(response["summary"]["paid"], 372)
            self.assertEqual(self.db_state(conn), original)
            self.assertEqual(upload_reconcile.pending_provider_rows(conn), [])
        finally:
            conn.close()

    def test_w04_repeated_preview_cannot_overwrite_existing_pending(self):
        conn, ident = self.fixture()
        try:
            upload_reconcile.record_pending_providers(
                conn, ["Unknown U."], "original.xlsx", actor_id=7,
            )
            conn.commit()
            original = self.db_state(conn)
            for filename in ("newer.xlsx", "another.xlsx"):
                response = self.invoke(
                    "preview", conn, ident,
                    {"upload_services", "upload_replace"}, filename=filename,
                )
                self.assertEqual(response["unknown_providers"], ["Unknown U."])
                self.assertTrue(response["can_replace"])
                self.assertEqual(self.db_state(conn), original)
            pending = upload_reconcile.pending_provider_rows(conn)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["source_filename"], "original.xlsx")
        finally:
            conn.close()

    def test_w04_preview_then_explicit_commit_resolves_without_pending(self):
        conn, ident = self.fixture()
        try:
            original = self.db_state(conn)
            preview = self.invoke(
                "preview", conn, ident, {"upload_services", "upload_replace"},
            )
            self.assertTrue(preview["requires_provider_mapping"])
            self.assertEqual(self.db_state(conn), original)
            self.assertEqual(upload_reconcile.pending_provider_rows(conn), [])
            result = self.invoke(
                "commit", conn, ident, {"upload_services", "upload_replace"},
                decisions={"Unknown U.": {
                    "direction": "structure", "display_name": "Unknown U.",
                }},
            )
            self.assertEqual(result["status"], "imported")
            self.assertEqual(
                conn.execute(
                    "SELECT direction FROM provider_registry WHERE source_name=?",
                    ("Unknown U.",),
                ).fetchone()[0], "structure",
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM service_payment_snapshots").fetchone()[0],
                1,
            )
            self.assertEqual(
                json.loads(conn.execute(
                    "SELECT payload FROM report_data WHERE date='2026-09-30'"
                ).fetchone()[0]),
                {"cashTotal": 190000, "cashOOO": 120000, "cashIP": 70000,
                 "primary": 5, "repeat": 5},
            )
        finally:
            conn.close()

    def test_w04_commit_without_replace_permission_is_fail_closed(self):
        conn, ident = self.fixture()
        try:
            original = self.db_state(conn)
            with self.assertRaises(Forbidden):
                self.invoke(
                    "commit", conn, ident, {"upload_services"},
                    decisions={"Unknown U.": "dent"},
                )
            self.assertEqual(self.db_state(conn), original)
        finally:
            conn.close()

    def test_w04_bad_xls_preview_leaves_database_unchanged(self):
        conn, ident = self.fixture()
        try:
            invalid = PaidServicesTests().workbook_bytes([
                ["Итого", None, None, None, 0, 100, 90, 0],
                ["Услуги", None, None, None, 0, 100, 90, 0],
                ["Unknown U.", None, None, None, 0, 100, 90, 0],
                ["Пациент", None, None, None, 0, 100, 90, 0],
                ["Счет №1 от 30.09.2026", None, None, None, 0, 100, 90, 10],
                ["Терапия", "30.09.2026", "Услуга", 1, 0, 100, 90, 10],
            ])
            original = self.db_state(conn)
            with self.assertRaisesRegex(ValueError, "Нарушен баланс"):
                self.invoke("preview", conn, ident, {"upload_services"},
                            raw=invalid)
            self.assertEqual(self.db_state(conn), original)
        finally:
            conn.close()


    def test_w04_b01_first_preview_on_clean_schema_never_executes_ddl(self):
        conn, ident = self.fixture(initialized=False)
        try:
            before = self.db_state(conn)
            version_before = conn.execute("PRAGMA schema_version").fetchone()[0]
            sql_trace = []
            conn.set_trace_callback(sql_trace.append)
            result = self.invoke("preview", conn, ident, {"upload_services"})
            conn.set_trace_callback(None)
            self.assertEqual(result["status"], "preview")
            self.assertEqual(result["counts"]["new"], 1)
            self.assertEqual(result["unknown_providers"], ["Unknown U."])
            self.assertEqual(self.db_state(conn), before)
            self.assertEqual(
                conn.execute("PRAGMA schema_version").fetchone()[0],
                version_before,
            )
            self.assertFalse([
                sql for sql in sql_trace
                if sql.lstrip().split(None, 1)[0].upper()
                in {"CREATE", "INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "REPLACE"}
            ])
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], 0,
            )
        finally:
            conn.close()

    def test_w04_b01_bad_xlsx_first_preview_keeps_schema_empty(self):
        conn, ident = self.fixture(initialized=False)
        try:
            invalid = PaidServicesTests().workbook_bytes([
                ["Итого", None, None, None, 0, 100, 90, 0],
                ["Услуги", None, None, None, 0, 100, 90, 0],
                ["Unknown U.", None, None, None, 0, 100, 90, 0],
                ["Пациент", None, None, None, 0, 100, 90, 0],
                ["Счет №1 от 30.09.2026", None, None, None, 0, 100, 90, 10],
                ["Терапия", "30.09.2026", "Услуга", 1, 0, 100, 90, 10],
            ])
            before = self.db_state(conn)
            schema_before = conn.execute("PRAGMA schema_version").fetchone()[0]
            with self.assertRaisesRegex(ValueError, "Нарушен баланс"):
                self.invoke(
                    "preview", conn, ident, {"upload_services"}, raw=invalid,
                )
            self.assertEqual(self.db_state(conn), before)
            self.assertEqual(
                conn.execute("PRAGMA schema_version").fetchone()[0],
                schema_before,
            )
        finally:
            conn.close()

    def test_w04_b01_partial_schema_does_not_recreate_missing_paid_tables(self):
        conn, ident = self.fixture()
        try:
            conn.execute("DROP TABLE service_payment_snapshots")
            conn.execute("DROP TABLE service_payment_versions")
            conn.commit()
            before = self.db_state(conn)
            schema_before = conn.execute("PRAGMA schema_version").fetchone()[0]
            result = self.invoke(
                "preview", conn, ident, {"upload_services", "upload_replace"},
            )
            self.assertEqual(result["counts"]["new"], 1)
            self.assertEqual(result["unknown_providers"], ["Unknown U."])
            self.assertEqual(self.db_state(conn), before)
            self.assertEqual(
                conn.execute("PRAGMA schema_version").fetchone()[0],
                schema_before,
            )
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='service_payment_snapshots'"
            ).fetchone())
        finally:
            conn.close()

    def test_w04_b01_startup_initializes_paid_schema_before_readonly_preview(self):
        server_source = (Path(__file__).parent / "server.py").read_text(encoding="utf-8")
        self.assertIn("paid_services_upload.bootstrap_schema()", server_source)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "startup.db"
            with patch.object(paid_services_upload, "DB_PATH", path):
                paid_services_upload.bootstrap_schema()
            conn = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            unused, ident = self.fixture(initialized=False)
            unused.close()
            try:
                for table in ("service_payment_snapshots", "service_payment_versions"):
                    self.assertIsNotNone(conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone())
                before = self.db_state(conn)
                result = self.invoke("preview", conn, ident, {"upload_services"})
                self.assertEqual(result["status"], "preview")
                self.assertEqual(result["unknown_providers"], ["Unknown U."])
                self.assertEqual(self.db_state(conn), before)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
