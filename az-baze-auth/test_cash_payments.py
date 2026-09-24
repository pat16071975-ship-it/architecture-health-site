import io
import json
import sqlite3
import unittest
from pathlib import Path

from openpyxl import Workbook

import cash_payments

cash_payments.configure_providers(
    {"Чирков М. С.": "Чирков Максим Сергеевич"},
    {"Старостенко В. А.": "Старостенко Вадим Анатольевич"},
    {"Казанцев Л. Е.": "Казанцев Л. Е."},
)


class CashPaymentsTests(unittest.TestCase):
    def workbook_bytes(self, rows):
        wb = Workbook()
        ws = wb.active
        ws.title = "Счета и оплаты"
        ws.append([
            "Дата и время", "Пациент/Компания", "Операция", "Сумма к оплате (₽)",
            "Движение ДС (₽)", "Касса", "Дата и время чека", "ККМ",
            "РН ККТ", "ФН", "№ ФД", "ФПД",
        ])
        for row in rows:
            ws.append(row)
        stream = io.BytesIO()
        wb.save(stream)
        wb.close()
        return stream.getvalue()

    def test_positive_receipts_only_and_legal_split(self):
        raw = self.workbook_bytes([
            ["31 авг 2026\n19:00", "А", "№1 Чирков М. С.", 1000, 1000, "Основная", "", cash_payments.OOO_KKM, "", "", "", ""],
            ["31 авг 2026\n18:00", "Б", "№2 Старостенко В. А.", 2000, 1500, "Основная", "", cash_payments.IP_KKM, "", "", "", ""],
            ["31 авг 2026\n17:00", "В", "№3 Казанцев Л. Е.", 3000, 2500, "Основная", "", cash_payments.OOO_KKM, "", "", "", ""],
            ["31 авг 2026\n16:00", "Г", "Внесение ДС", 0, 4000, "Основная", "", cash_payments.IP_KKM, "", "", "", ""],
            ["31 авг 2026\n15:00", "Д", "Изъятие ДС", 0, -700, "Основная", "", cash_payments.OOO_KKM, "", "", "", ""],
            ["31 авг 2026\n14:00", "Е", "Внесение ДС", 0, 900, "Дополнительная", "", "", "", "", "", ""],
        ])
        daily, sheet = cash_payments.parse_file(raw, "cash.xlsx")
        self.assertEqual(sheet, "Счета и оплаты")
        day = daily["2026-08-31"]
        self.assertEqual(day["billedTotal"], 6000)
        self.assertEqual(day["cashOOO"], 3500)
        self.assertEqual(day["cashIP"], 5500)
        self.assertEqual(day["cashTotal"], 9000)
        self.assertEqual(day["cashUnallocated"], 4000)
        self.assertEqual(day["factMedicine"], 2500)
        self.assertEqual(day["factLab"], 2500)
        self.assertEqual(day["dentists"]["Чирков Максим Сергеевич"], 1000)
        self.assertEqual(day["clinicDocs"]["Старостенко Вадим Анатольевич"], 1500)
        self.assertEqual(day["dentistsLegal"]["Чирков Максим Сергеевич"]["ooo"], 1000)
        self.assertEqual(day["clinicDocsLegal"]["Старостенко Вадим Анатольевич"]["ip"], 1500)
        self.assertEqual(day["labLegal"]["ooo"], 2500)

    def test_debt_line_is_included_in_billed_total(self):
        raw = self.workbook_bytes([
            ["31 авг 2026\n19:00", "А", "№100 Счет", 1000, 0, "Основная", "", "", "", "", "", ""],
            ["31 авг 2026\n18:00", "А", "Задолженность по счету №100", 250, 250, "Основная", "", cash_payments.OOO_KKM, "", "", "", ""],
        ])
        daily, _ = cash_payments.parse_file(raw, "cash.xlsx")
        self.assertEqual(daily["2026-08-31"]["billedTotal"], 1250)

    def test_sequential_daily_uploads_accumulate_without_erasing_previous_days(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cash_payments.init_schema(conn)
        first = {**cash_payments._blank_day(), "cashOOO": 100000, "cashTotal": 100000}
        second = {**cash_payments._blank_day(), "cashIP": 120000, "cashTotal": 120000}
        cash_payments.replace_range(conn, {"2026-08-01": first}, "day1.xlsx", "sha1", 1, "t1")
        cash_payments.replace_range(conn, {"2026-08-02": second}, "day2.xlsx", "sha2", 1, "t2")
        snapshots = cash_payments.month_snapshots(conn, "2026-08")
        self.assertEqual(snapshots["2026-08-01"]["cashTotal"], 100000)
        self.assertEqual(snapshots["2026-08-02"]["cashTotal"], 220000)
        self.assertEqual(snapshots["2026-08-02"]["cashOOO"], 100000)
        self.assertEqual(snapshots["2026-08-02"]["cashIP"], 120000)

    def test_partial_period_replace_does_not_delete_unmentioned_day(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cash_payments.init_schema(conn)
        base = {**cash_payments._blank_day(), "cashOOO": 100, "cashTotal": 100}
        cash_payments.replace_range(
            conn,
            {"2026-08-01": base, "2026-08-02": base, "2026-08-03": base},
            "base.xlsx", "base", 1, "t0",
        )
        replacement = {**cash_payments._blank_day(), "cashIP": 300, "cashTotal": 300}
        cash_payments.replace_range(
            conn,
            {"2026-08-01": replacement, "2026-08-03": replacement},
            "partial.xlsx", "partial", 1, "t1",
        )
        dates = [row[0] for row in conn.execute(
            "SELECT data_date FROM cash_receipts_daily ORDER BY data_date"
        ).fetchall()]
        self.assertEqual(dates, ["2026-08-01", "2026-08-02", "2026-08-03"])

    def test_unknown_positive_kkm_fails_closed(self):
        raw = self.workbook_bytes([
            ["31 авг 2026\n19:00", "А", "Внесение ДС", 0, 1000, "Основная", "", "9999999999999999", "", "", "", ""],
        ])
        with self.assertRaisesRegex(ValueError, "неизвестная ККМ"):
            cash_payments.parse_file(raw, "cash.xlsx")

    def test_anonymized_reference_totals_match_verified_controls(self):
        raw = (Path(__file__).parent / "fixtures" / "cash_reference_2026_anonymized.tsv").read_bytes()
        daily, sheet = cash_payments.parse_file(raw, "cash_reference_2026_anonymized.tsv")
        self.assertEqual(sheet, "текст")
        billed = sum(day["billedTotal"] for day in daily.values())
        cash_ooo = sum(day["cashOOO"] for day in daily.values())
        cash_ip = sum(day["cashIP"] for day in daily.values())
        cash_total = sum(day["cashTotal"] for day in daily.values())
        self.assertEqual(billed, 65878370)
        self.assertEqual(cash_ooo, 29149782)
        self.assertEqual(cash_ip, 30742013)
        self.assertEqual(cash_total, 59891795)
        august = [day for date, day in daily.items() if date.startswith("2026-08")]
        self.assertEqual(sum(day["billedTotal"] for day in august), 7782970)
        self.assertEqual(sum(day["cashOOO"] for day in august), 3617945)
        self.assertEqual(sum(day["cashIP"] for day in august), 4178520)
        self.assertEqual(sum(day["cashTotal"] for day in august), 7796465)

    def test_month_snapshots_is_empty_before_first_cash_table_exists(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.assertEqual(cash_payments.month_snapshots(conn, "2026-08"), {})

    def test_multiline_russian_date_is_one_operation(self):
        raw = self.workbook_bytes([
            ["05 янв 2026\n09:15", "Пациент", "Внесение ДС", 0, 1234, "Основная", "", cash_payments.OOO_KKM, "", "", "", ""],
        ])
        daily, _ = cash_payments.parse_file(raw, "cash.xlsx")
        self.assertEqual(list(daily), ["2026-01-05"])
        self.assertEqual(daily["2026-01-05"]["cashTotal"], 1234)

    def test_overlay_recreates_cash_month_when_backup_has_no_report_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE report_data(date TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
        )
        cash_payments.init_schema(conn)
        cash_payments.replace_range(
            conn,
            {
                "2026-08-31": {
                    **cash_payments._blank_day(),
                    "cashOOO": 3000,
                    "cashIP": 2000,
                    "cashTotal": 5000,
                    "cashUnallocated": 5000,
                }
            },
            "cash.xlsx", "sha", 1, "now",
        )
        changed = cash_payments.overlay_stored_month(conn, "2026-08", 1, "now")
        self.assertEqual(changed, 1)
        row = conn.execute(
            "SELECT payload FROM report_data WHERE date='2026-08-31'"
        ).fetchone()
        self.assertIsNotNone(row)
        payload = json.loads(row[0])
        self.assertEqual(payload["cashTotal"], 5000)
        self.assertEqual(payload["cashOOO"], 3000)
        self.assertEqual(payload["cashIP"], 2000)
        self.assertEqual(payload["cashUnallocated"], 5000)

    def test_overlay_preserves_billed_baseline_and_adds_latest_cash_date(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE report_data(date TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)"
        )
        cash_payments.init_schema(conn)
        baseline = {
            "date": "2026-08-30",
            "factMedicine": 7000,
            "factLab": 500,
            "dentists": {"Чирков Максим Сергеевич": 7000},
            "clinicDocs": {},
            "labRevenue": 500,
            "primary": 2,
        }
        conn.execute(
            "INSERT INTO report_data(date,payload,updated_by,updated_at) VALUES(?,?,?,?)",
            ("2026-08-30", json.dumps(baseline, ensure_ascii=False), 1, "old"),
        )
        cash_payments.replace_range(
            conn,
            {
                "2026-08-30": {
                    **cash_payments._blank_day(),
                    "billedTotal": 7500,
                    "cashOOO": 3000,
                    "cashTotal": 3000,
                    "factMedicine": 3000,
                    "dentists": {"Чирков Максим Сергеевич": 3000},
                    "dentistsLegal": {"Чирков Максим Сергеевич": {"ooo": 3000, "ip": 0}},
                },
                "2026-08-31": {
                    **cash_payments._blank_day(),
                    "cashIP": 2000,
                    "cashTotal": 2000,
                    "cashUnallocated": 2000,
                },
            },
            "cash.xlsx",
            "sha",
            1,
            "now",
        )
        changed = cash_payments.overlay_stored_month(conn, "2026-08", 1, "now")
        self.assertEqual(changed, 2)

        row30 = json.loads(conn.execute("SELECT payload FROM report_data WHERE date='2026-08-30'").fetchone()[0])
        self.assertEqual(row30["cashTotal"], 3000)
        self.assertEqual(row30["factMedicine"], 3000)
        self.assertEqual(row30["billedMedicine"], 7000)
        self.assertEqual(row30["billedLab"], 500)

        row31 = json.loads(conn.execute("SELECT payload FROM report_data WHERE date='2026-08-31'").fetchone()[0])
        self.assertEqual(row31["cashTotal"], 5000)
        self.assertEqual(row31["cashOOO"], 3000)
        self.assertEqual(row31["cashIP"], 2000)
        self.assertEqual(row31["cashUnallocated"], 2000)
        self.assertEqual(row31["primary"], 2)


if __name__ == "__main__":
    unittest.main()
