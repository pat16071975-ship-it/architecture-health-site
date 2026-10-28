import io
import json
import sqlite3
import unittest

from openpyxl import Workbook

import paid_services


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
        self.assertEqual(parsed["month"], "")
        self.assertEqual(parsed["period_start"], "2026-09-10")
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
        paid_patients = {
            (row["staff"], row["patient_token"], row["paid"])
            for row in parsed["patients"]
        }
        self.assertIn(("Dent D.", paid_services._patient_token("Пациент Один"), 225.0), paid_patients)
        self.assertIn(("Struct S.", paid_services._patient_token("Пациент Два"), 100.0), paid_patients)
        self.assertIn(("Lab L.", paid_services._patient_token("Пациент Три"), 40.0), paid_patients)

    def test_compact_period_keeps_service_analytics_without_patient_names(self):
        parsed = paid_services.parse_bytes(
            self.reference_report(),
            "paid.xlsx",
            known_staff={"Старшийадминистратор -."},
        )
        compact = paid_services.compact_report(parsed)
        serialized = json.dumps(compact, ensure_ascii=False)
        self.assertEqual(compact["version"], 3)
        self.assertEqual(compact["period_start"], "2026-09-10")
        self.assertEqual(compact["period_end"], "2026-09-30")
        self.assertNotIn("Пациент Один", serialized)
        self.assertNotIn("Пациент Два", serialized)
        self.assertTrue(compact["service_items"])
        self.assertTrue(compact["visit_links"])
        self.assertEqual(len(compact["invoices"]), 4)
        self.assertTrue(compact["paid_patient_evidence_complete"])
        self.assertEqual(
            compact["paid_patients"]["Dent D."],
            [paid_services._patient_token("Пациент Один")],
        )
        self.assertEqual(
            compact["paid_patients"]["Struct S."],
            [paid_services._patient_token("Пациент Два")],
        )
        self.assertNotIn("Пациент", json.dumps(compact["paid_patients"], ensure_ascii=False))

    def test_paid_patient_evidence_counts_patient_once_per_provider_and_ignores_zero_paid(self):
        raw = self.workbook_bytes([
            ["Услуги", None, None, None, 0, 300, 200, 100],
            ["Dent D.", None, None, None, 0, 300, 200, 100],
            ["Пациент Один", None, None, None, 100, 200, 150, 150],
            ["Счет №100 от 10.09.2026 10:00:00", None, None, None, "-", 100, 100, 0],
            ["Терапия", "10.09.2026 10:00", "Услуга 1", 1, "-", 100, 100, 0],
            ["Пациент Один", None, None, None, 50, 100, 50, 100],
            ["Счет №101 от 11.09.2026 10:00:00", None, None, None, "-", 100, 50, 50],
            ["Терапия", "11.09.2026 10:00", "Услуга 2", 1, "-", 100, 50, 50],
            ["Пациент Ноль", None, None, None, 0, 100, 0, 100],
            ["Счет №102 от 12.09.2026 10:00:00", None, None, None, "-", 100, 0, 100],
            ["Терапия", "12.09.2026 10:00", "Услуга 3", 1, "-", 100, 0, 100],
        ])
        parsed = paid_services.parse_bytes(raw, "paid.xlsx")
        compact = paid_services.compact_report(parsed)
        self.assertEqual(
            compact["paid_patients"]["Dent D."],
            [paid_services._patient_token("Пациент Один")],
        )
        self.assertNotIn(paid_services._patient_token("Пациент Ноль"), compact["paid_patients"]["Dent D."])

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
        record = {"date": "2026-09-02", "primary": 1, "repeat": 1}
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
        record = {"date": "2026-09-01", "primary": 2, "repeat": 0}
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

    def test_paid_attribution_never_overwrites_general_completed_visit_totals(self):
        patient = "Alpha A."
        snapshot = {
            "visit_links": {
                "2026-10-26|" + paid_services._patient_token(patient): ["Struct S."],
            },
            "invoices": [],
        }
        rows = [
            {
                "data_date": "2026-10-26",
                "overall": {"2026-10-26": {"Повторные": 2}},
                "visits": [
                    {"date": "2026-10-26", "patient": patient, "kind": "Повторные"},
                    {"date": "2026-10-26", "patient": "Missing M.", "kind": "Повторные"},
                ],
            },
        ]
        record = {
            "date": "2026-10-26",
            "primary": 49,
            "repeat": 355,
        }
        paid_services.apply_visit_attribution(
            record,
            snapshot,
            rows,
            {},
            {"Struct S.": "Врач клиники"},
            {},
        )
        self.assertEqual(record["primary"], 49)
        self.assertEqual(record["repeat"], 355)
        self.assertEqual(record["clinicRepeat"], 1)
        self.assertEqual(record["_uploadControl"]["sourceRepeat"], 2)
        self.assertEqual(record["_uploadControl"]["unassignedRepeat"], 1)

    def test_manual_direction_counts_survive_paid_attribution(self):
        rows = [
            {
                "data_date": "2026-10-26",
                "overall": {"2026-10-26": {"Повторные": 2}},
                "visits": [
                    {"date": "2026-10-26", "patient": "Alpha A.", "kind": "Повторные"},
                    {"date": "2026-10-26", "patient": "Beta B.", "kind": "Повторные"},
                ],
                "manual_direction_counts": {
                    "dent": {},
                    "structure": {"Повторные": 1},
                },
            }
        ]
        snapshot = {"visit_links": {}, "invoices": []}
        record = {"date": "2026-10-26", "primary": 49, "repeat": 355}
        paid_services.apply_visit_attribution(
            record,
            snapshot,
            rows,
            {},
            {"Struct S.": "Врач клиники"},
            {},
        )
        self.assertEqual(record["primary"], 49)
        self.assertEqual(record["repeat"], 355)
        self.assertEqual(record["clinicRepeat"], 1)
        self.assertEqual(record["_uploadControl"]["sourceRepeat"], 2)
        self.assertEqual(record["_uploadControl"]["reportedRepeat"], 1)
        self.assertEqual(record["_uploadControl"]["unassignedRepeat"], 1)

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

    def test_same_month_incremental_slice_does_not_become_month_to_date(self):
        raw = self.workbook_bytes([
            ["Итого", None, None, None, 0, 3000, 92610, -89610],
            ["Услуги", None, None, None, 0, 3000, 92610, -89610],
            ["Назыров У. Ж.", None, None, None, 0, 3000, 92610, -89610],
            ["Пациент", None, None, None, 0, 3000, 92610, -89610],
            ["Счет №1 от 12.10.2026 12:00:00", None, None, None, "-", 1000, 30000, -29000],
            ["Терапия", "12.10.2026 12:00", "Услуга", 1, "-", 1000, 30000, -29000],
            ["Счет №2 от 13.10.2026 12:00:00", None, None, None, "-", 2000, 62610, -60610],
            ["Терапия", "13.10.2026 12:00", "Услуга", 1, "-", 2000, 62610, -60610],
        ])
        parsed = paid_services.parse_bytes(raw, "12-13.xlsx", known_staff=["Назыров У. Ж."])
        self.assertEqual(parsed["period_start"], "2026-10-12")
        self.assertEqual(parsed["period_end"], "2026-10-13")
        compact = paid_services.compact_report(parsed)
        self.assertEqual(compact["version"], 3)
        self.assertEqual(compact["period_start"], "2026-10-12")
        self.assertEqual(compact["period_end"], "2026-10-13")
        self.assertNotIn("as_of_date", compact)

    def test_multi_month_report_uses_dates_inside_file_without_manual_period(self):
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
        detected = paid_services.parse_bytes(raw, "two-months.xlsx")
        self.assertEqual(detected["period_start"], "2026-09-30")
        self.assertEqual(detected["period_end"], "2026-10-01")
        parsed = paid_services.parse_bytes(
            raw, "two-months.xlsx", period_start="2026-09-01", period_end="2026-10-01",
        )
        self.assertEqual(parsed["period_start"], "2026-09-01")
        self.assertEqual(parsed["period_end"], "2026-10-01")
        self.assertEqual(parsed["providers"]["Dent D."]["paid"], 200)
        self.assertEqual(paid_services.compact_report(parsed)["version"], 3)



class DoctorPaidOnlyManagementTests(unittest.TestCase):
    """Only individual doctor rows are sourced from monthly MIS 'Оплачено'."""

    def memory_db(self, *, unknown=False):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE service_payment_snapshots(
                as_of_date TEXT PRIMARY KEY,month TEXT,payload_json TEXT);
            CREATE TABLE provider_registry(
                source_name TEXT PRIMARY KEY,display_name TEXT,direction TEXT);
            CREATE TABLE report_data(date TEXT PRIMARY KEY,payload TEXT);
        """)
        conn.executemany(
            "INSERT INTO provider_registry VALUES(?,?,?)",
            [
                ("Dent D.", "Врач стоматолог", "dent"),
                ("Struct S.", "Врач клиники", "structure"),
                ("Lab L.", "Врач лаборатории", "lab"),
                ("Администраторы", "Администраторы", "ignore"),
            ],
        )
        providers = {
            "Dent D.": {"paid": 225, "billed": 200},
            "Struct S.": {"paid": 100, "billed": 100},
            "Lab L.": {"paid": 15, "billed": 15},
            "Администраторы": {"paid": 0, "billed": 0},
        }
        if unknown:
            providers["Unknown Doctor"] = {"paid": 80, "billed": 80}
        payload = {
            "version": 2, "month": "2026-09", "as_of_date": "2026-09-30",
            "providers": providers, "totals": {"paid": 340},
            "provider_residual": {"paid": -3800},
        }
        conn.execute(
            "INSERT INTO service_payment_snapshots VALUES(?,?,?)",
            ("2026-09-30", "2026-09", json.dumps(payload, ensure_ascii=False)),
        )
        original = {"date": "2026-09-30", "cashTotal": 9479310,
                    "cashOOO": 4000000, "cashIP": 5479310,
                    "dentists": {"Врач стоматолог": 350},
                    "clinicDocs": {"Врач клиники": 400},
                    "dentPrimary": 10, "labRevenue": 173240}
        conn.execute(
            "INSERT INTO report_data VALUES(?,?)",
            ("2026-09-30", json.dumps(original, ensure_ascii=False)),
        )
        conn.commit()
        return conn, original

    def test_doctor_paid_rows_are_read_only_and_do_not_change_any_approved_totals(self):
        import report_storage
        conn, original = self.memory_db()
        try:
            baseline = list(conn.iterdump())
            conn.execute("PRAGMA query_only=ON")
            rows = report_storage._management_doctor_paid_snapshots(conn)
            self.assertEqual(
                rows, {"2026-09": [{
                    "asOf": "2026-09-30",
                    "dentists": {"Врач стоматолог": 225.0},
                    "clinicDocs": {"Врач клиники": 100.0},
                }]},
            )
            self.assertEqual(list(conn.iterdump()), baseline)
            stored = json.loads(conn.execute(
                "SELECT payload FROM report_data WHERE date='2026-09-30'"
            ).fetchone()[0])
            self.assertEqual(stored, original)
            self.assertEqual(
                (stored["cashTotal"], stored["dentPrimary"], stored["labRevenue"]),
                (9479310, 10, 173240),
            )
        finally:
            conn.close()

    def test_unknown_paid_provider_is_not_guessed_or_overlaid(self):
        import report_storage
        conn, _ = self.memory_db(unknown=True)
        try:
            conn.execute("PRAGMA query_only=ON")
            self.assertEqual(report_storage._management_doctor_paid_snapshots(conn), {})
        finally:
            conn.close()

    def test_absent_monthly_source_does_not_fabricate_doctor_values(self):
        import report_storage
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            self.assertEqual(report_storage._management_doctor_paid_snapshots(conn), {})
        finally:
            conn.close()

    def test_actual_report_ui_only_replaces_doctor_cells_with_asof_guard(self):
        import shutil
        import subprocess
        from pathlib import Path
        node = shutil.which("node")
        if not node:
            self.skipTest("Node required for real report JS execution")
        js = r"""
const fs=require('node:fs'),vm=require('node:vm');
const src=fs.readFileSync('reports/period-view.js','utf8');
const start=src.indexOf('  function paidDate(value){');
const end=src.indexOf('  function mix(',start);
if(start<0||end<0)throw Error('doctor-only helper not found');
const ctx={
  window:{},
  num:v=>v===''||v==null||!Number.isFinite(+v)?0:+v,
  DOCTOR_PAID_SNAPSHOTS:{
    '2026-07':[{asOf:'2026-07-31',dentists:{'D':10},clinicDocs:{'S':20}}],
    '2026-08':[{asOf:'2026-08-31',dentists:{'D':30},clinicDocs:{'S':40}}],
  }
};
vm.createContext(ctx);vm.runInContext(src.slice(start,end),ctx);
const old={date:'2026-07-31',dentists:{D:999},clinicDocs:{S:888},cashTotal:9479310,primary:12};
const n=ctx.window.azDoctorPaidValue;
if(n('2026-07-30','dentists','D',999)!==999)throw Error('future paid leaked');
if(n('2026-07-31','dentists','D',999)!==10)throw Error('July dentist not shown');
if(n('2026-07-31','clinicDocs','S',888)!==20)throw Error('July clinician not shown');
if(n('2026-08-31','dentists','D',999)!==30)throw Error('August dentist not shown');
if(n('2026-06-30','dentists','D',999)!==999)throw Error('no-source fallback changed');
if(n('2026-08-31','dentists','MISSING',999)!==0)throw Error('missing doctor misattributed');
if(!ctx.window.azDoctorHasPaidForDate('2026-07-31'))throw Error('missing snapshot detection');
if(ctx.window.azDoctorHasPaidForDate('2026-07-30'))throw Error('future snapshot detected');
const rows=[{...old},{...old,date:'2026-08-31'}];
if(vm.runInContext('doctorPeriodPaid',ctx)(rows,'dentists','D')!==40)throw Error('quarter sum wrong');
if(old.cashTotal!==9479310||old.primary!==12||old.dentists.D!==999)
  throw Error('approved old raw values changed');
process.stdout.write('DOCTOR_ONLY_UI=PASS');
"""
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [node, "-e", js], cwd=root, text=True, capture_output=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DOCTOR_ONLY_UI=PASS", result.stdout)

    def test_existing_direction_totals_and_other_sections_stay_on_old_source(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        html = (root / "reports/index.html").read_text(encoding="utf-8")
        view = (root / "reports/period-view.js").read_text(encoding="utf-8")
        storage = (Path(__file__).resolve().parent / "report_storage.py").read_text(encoding="utf-8")
        for fragment, source in (
            ("const dentRev=Object.values(r.dentists||{}).reduce", html),
            ("const clinicRev=Object.values(r.clinicDocs||{}).reduce", html),
            ("addMetric(tbody,'Выручка стоматологии'", view),
            ("addMetric(tbody,'Выручка клиники'", view),
            ("doctorPartsPaid(pd.paidParts,'dentists',name)", view),
            ("doctorPartsPaid(pd.paidParts,'clinicDocs',name)", view),
            ("doctorPaidSnapshots=_management_doctor_paid_snapshots(db())", storage),
            ("function collectRecord(){", html),
            ("window.azDoctorHasPaidForDate(date)", html),
        ):
            self.assertTrue(fragment in source, "Missing report contract: " + fragment)

if __name__ == "__main__":
    unittest.main()
