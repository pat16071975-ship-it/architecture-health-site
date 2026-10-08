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


if __name__ == "__main__":
    unittest.main()
