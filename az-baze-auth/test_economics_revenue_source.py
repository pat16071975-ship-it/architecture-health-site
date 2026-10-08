import unittest

import economics_control


class EconomicsRevenueSourceTests(unittest.TestCase):
    def sheet(self, rows):
        data = {}
        max_row = 0
        for row_index, values in enumerate(rows, 1):
            max_row = row_index
            data[row_index] = {
                col_index: value
                for col_index, value in enumerate(values, 1)
                if value is not None
            }
        return economics_control._Sheet(data, max_row)

    def test_parses_monthly_gross_discount_and_net_independently_of_expense_period(self):
        ws = self.sheet([
            [None, "Месяц", "Год", None, None, None, None, "Сумма по прейскуранту", "Скидка", "Сумма со скидкой"],
            [None, 8, 2026, "ВСЕГО по филиалам", None, None, None, 1000, 100, 900, 0, 0],
            [None, 9, 2026, "ВСЕГО по филиалам", None, None, None, 1280, 80, 1200, 0, 0],
        ])
        parsed = economics_control._parse_revenue_source(ws)
        self.assertTrue(parsed["available"])
        self.assertEqual(parsed["months"]["2026-09"]["gross"], 1280)
        self.assertEqual(parsed["months"]["2026-09"]["discount"], 80)
        self.assertEqual(parsed["months"]["2026-09"]["net"], 1200)
        self.assertEqual(parsed["months"]["2026-09"]["control_gross"], 0)
        self.assertEqual(parsed["months"]["2026-09"]["control_net"], 0)

    def test_revenue_identity_is_fail_closed(self):
        ws = self.sheet([
            [None, 9, 2026, "ВСЕГО по филиалам", None, None, None, 1280, 80, 1199],
        ])
        with self.assertRaisesRegex(ValueError, "Не сходится выручка/скидка"):
            economics_control._parse_revenue_source(ws)

    def test_missing_optional_revenue_sheet_returns_unavailable(self):
        self.assertEqual(
            economics_control._parse_revenue_source(None),
            {"available": False, "months": {}},
        )


if __name__ == "__main__":
    unittest.main()
