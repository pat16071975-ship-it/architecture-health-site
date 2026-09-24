import unittest

import cash_receipts


HEADER = "\t".join([
    "Дата и время",
    "Пациент/Компания",
    "Операция",
    "Сумма к оплате (₽)",
    "Движение ДС (₽)",
    "Касса",
    "Дата чека",
    "ККМ",
])


def row(date, operation, due="", movement="", kkm=""):
    return "\t".join([date, "Пациент", operation, str(due), str(movement), "", "", kkm])


class CashReceiptsTests(unittest.TestCase):
    def test_positive_approved_kkm_is_fact_and_withdrawal_is_ignored(self):
        text = "\n".join([
            HEADER,
            row("01.08.2026", "№70001 Счет (Зубачев Р. Н.)", 1000, 600, cash_receipts.OOO_KKM),
            row("01.08.2026", "Внесение ДС", 13, 400, cash_receipts.IP_KKM),
            row("01.08.2026", "Изъятие ДС", "", -300, cash_receipts.OOO_KKM),
            row("01.08.2026", "Внесение ДС", "", 900, ""),
        ])
        parsed = cash_receipts.parse_payments_text(text, ["Зубачев Р. Н."])
        day = parsed["daily"]["2026-08-01"]
        self.assertEqual(day["billed"], 1000)
        self.assertEqual(day["cashOoo"], 600)
        self.assertEqual(day["cashIp"], 400)
        self.assertEqual(day["cashFact"], 1000)
        self.assertEqual(day["staff"]["Зубачев Р. Н."]["total"], 600)

    def test_debt_is_billed_and_can_inherit_invoice_staff(self):
        text = "\n".join([
            HEADER,
            row("01.08.2026", "№70002 Счет (Чирков М. С.)", 1000, "", ""),
            row("02.08.2026", "Задолженность по счету №70002", 250, 250, cash_receipts.IP_KKM),
        ])
        parsed = cash_receipts.parse_payments_text(text, ["Чирков М. С."])
        self.assertEqual(parsed["daily"]["2026-08-01"]["billed"], 1000)
        self.assertEqual(parsed["daily"]["2026-08-02"]["billed"], 250)
        self.assertEqual(parsed["daily"]["2026-08-02"]["staff"]["Чирков М. С."]["ip"], 250)

    def test_cumulative_is_month_to_date_and_resets_each_month(self):
        text = "\n".join([
            HEADER,
            row("01.07.2026", "№1 Счет (Чирков М. С.)", 100, 100, cash_receipts.OOO_KKM),
            row("02.07.2026", "Внесение ДС", "", 50, cash_receipts.IP_KKM),
            row("01.08.2026", "№2 Счет (Чирков М. С.)", 200, 200, cash_receipts.OOO_KKM),
        ])
        parsed = cash_receipts.parse_payments_text(text, ["Чирков М. С."])
        july = cash_receipts.cumulative_for_date(parsed, "2026-07-02")
        august = cash_receipts.cumulative_for_date(parsed, "2026-08-01")
        self.assertEqual(july["cashFact"], 150)
        self.assertEqual(july["billed"], 100)
        self.assertEqual(august["cashFact"], 200)
        self.assertEqual(august["billed"], 200)

    def test_russian_month_date_is_supported(self):
        text = "\n".join([
            HEADER,
            row("5 янв 2026\n9:17", "№3 Счет (Чирков М. С.)", 300, 300, cash_receipts.OOO_KKM),
        ])
        parsed = cash_receipts.parse_payments_text(text, ["Чирков М. С."])
        self.assertEqual(parsed["sourceStart"], "2026-01-05")
        self.assertEqual(parsed["sourceEnd"], "2026-01-05")


if __name__ == "__main__":
    unittest.main()
