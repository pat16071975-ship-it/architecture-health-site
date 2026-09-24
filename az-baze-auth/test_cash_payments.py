import io
import json
import unittest
from types import SimpleNamespace

import cash_payments


class DummyFile:
    def __init__(self, payload, name="cash.xlsx"):
        self._payload = payload
        self.filename = name
    def read(self):
        return self._payload


class CashPaymentsTests(unittest.TestCase):
    def setUp(self):
        self.original_table_candidates = cash_payments.core._table_candidates
        self.original_ident = cash_payments.core.ident_import
        cash_payments.core.ident_import = SimpleNamespace(
            DENTISTS={"Dent D.": "Dent Doctor"},
            STRUCTURE_DOCTORS={"Struct S.": "Struct Doctor"},
            LAB_DOCTORS={"Lab L.": "Lab Doctor"},
        )

    def tearDown(self):
        cash_payments.core._table_candidates = self.original_table_candidates
        cash_payments.core.ident_import = self.original_ident

    def _install_text(self, text):
        cash_payments.core._table_candidates = lambda _raw, _name: [("Sheet1", text)]

    def test_positive_receipts_only_and_entities(self):
        text = (
            "Дата и время\tПациент/Компания\tОперация\tСумма к оплате (₽)\tДвижение ДС (₽)\tКасса\tДата и время чека\tККМ\n"
            "01 янв 2026\n10:00\tA\t№1 Dent D.\t100\t100\tОсновная\t01.01.2026 10:00\t0531380019042729\n"
            "01 янв 2026\n11:00\tB\tВнесение ДС\t13\t200\tБезналичный расчет\t01.01.2026 11:00\t0463880019042725\n"
            "01 янв 2026\n12:00\tC\tИзъятие ДС\t13\t-50\tОсновная\t01.01.2026 12:00\t0531380019042729\n"
            "01 янв 2026\n13:00\tD\tПеревод ДС внутри семьи на счет (E)\t13\t500\tОсновная\t01.01.2026 13:00\t0531380019042729\n"
        )
        self._install_text(text)
        _raw, rows, billed_rows, _sheet, _source_rows = cash_payments.parse_upload(DummyFile(b"x"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row["amount"] for row in billed_rows), 113)\n        snapshots, start, end = cash_payments.build_daily_snapshots(rows, billed_rows)
        self.assertEqual((start, end), ("2026-01-01", "2026-01-01"))
        snap = snapshots["2026-01-01"]
        self.assertEqual(snap["cashOOO"], 100)
        self.assertEqual(snap["cashIP"], 200)
        self.assertEqual(snap["cashFact"], 300)
        self.assertEqual(snap["cashAllocated"], 100)
        self.assertEqual(snap["cashUnallocated"], 200)
        self.assertEqual(snap["cashDentists"]["Dent Doctor"], 100)

    def test_month_to_date_resets_each_month_and_keeps_ooo_ip_split(self):
        rows = [
            {"date": "2026-01-31", "amount": 100, "entity": "ooo", "department": "dent", "staff": "Dent D.", "staff_full": "Dent Doctor"},
            {"date": "2026-02-01", "amount": 300, "entity": "ip", "department": "structure", "staff": "Struct S.", "staff_full": "Struct Doctor"},
            {"date": "2026-02-02", "amount": 50, "entity": "ooo", "department": "lab", "staff": "Lab L.", "staff_full": "Lab Doctor"},
        ]
        snapshots, _start, _end = cash_payments.build_daily_snapshots(rows)
        self.assertEqual(snapshots["2026-01-31"]["cashFact"], 100)
        self.assertEqual(snapshots["2026-02-01"]["cashFact"], 300)
        self.assertEqual(snapshots["2026-02-02"]["cashFact"], 350)
        self.assertEqual(snapshots["2026-02-02"]["cashIP"], 300)
        self.assertEqual(snapshots["2026-02-02"]["cashOOO"], 50)
        self.assertEqual(snapshots["2026-02-02"]["cashLabRevenue"], 50)

    def test_billed_invoices_include_debt_rows_but_not_cash_movements(self):
        text = (
            "Дата и время\tПациент/Компания\tОперация\tСумма к оплате (₽)\tДвижение ДС (₽)\tКасса\tДата и время чека\tККМ\n"
            "01 янв 2026\n10:00\tA\t№1 Dent D.\t100\t100\tОсновная\t01.01.2026 10:00\t0531380019042729\n"
            "01 янв 2026\n11:00\tCompany\tЗадолженность по счету №1 за пациента: A\t250\t13\t13\t\t\n"
            "01 янв 2026\n12:00\tA\tВнесение ДС\t13\t200\tОсновная\t01.01.2026 12:00\t0463880019042725\n"
        )
        self._install_text(text)
        _raw, rows, billed_rows, _sheet, _source_rows = cash_payments.parse_upload(DummyFile(b"x"))
        self.assertEqual(sum(row["amount"] for row in billed_rows), 350)
        snapshots, _start, _end = cash_payments.build_daily_snapshots(rows, billed_rows)
        self.assertEqual(snapshots["2026-01-01"]["billedInvoices"], 350)
        self.assertEqual(snapshots["2026-01-01"]["cashFact"], 300)

    def test_unknown_positive_kkm_fails_closed(self):
        text = (
            "Дата и время\tПациент/Компания\tОперация\tСумма к оплате (₽)\tДвижение ДС (₽)\tКасса\tДата и время чека\tККМ\n"
            "01 янв 2026\n10:00\tA\t№1 Dent D.\t100\t100\tОсновная\t01.01.2026 10:00\t999\n"
        )
        self._install_text(text)
        with self.assertRaisesRegex(ValueError, "неизвестной ККМ"):
            cash_payments.parse_upload(DummyFile(b"x"))

    def test_non_invoice_receipt_stays_unallocated_but_in_fact(self):
        rows = [
            {"date": "2026-03-05", "amount": 700, "entity": "ip", "department": None, "staff": None, "staff_full": None}
        ]
        snapshots, _start, _end = cash_payments.build_daily_snapshots(rows)
        snap = snapshots["2026-03-05"]
        self.assertEqual(snap["cashFact"], 700)
        self.assertEqual(snap["cashUnallocated"], 700)
        self.assertEqual(snap["cashAllocated"], 0)


if __name__ == "__main__":
    unittest.main()
