import io
import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import Workbook


SPEC = importlib.util.spec_from_file_location(
    "payroll_registry",
    Path(__file__).with_name("payroll_registry.py"),
)
payroll_registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(payroll_registry)


def workbook_bytes(oct_error=False):
    wb = Workbook()
    wb.remove(wb.active)

    for title, base in (
        ("Расчёт_ЗП_сентябрь 26 ", 100),
        ("Расчёт_ЗП_октябрь 26  ", 200),
        ("Расчёт_ЗП_ноябрь 26  ", 300),
    ):
        ws = wb.create_sheet(title)
        ws["A4"] = "Врачебный блок"
        ws["A5"] = "Отделение терапии"
        ws["D5"] = "Иванов Иван Иванович"
        ws["E5"] = "Стоматолог-терапевт"
        ws["O5"] = "#DIV/0!" if (oct_error and "октябрь" in title) else base
        ws["P5"] = 10
        ws["T5"] = 5
        ws["W5"] = 20
        ws["Y5"] = 3

        ws["A8"] = "Зуботехническая лаборатория"
        ws["A9"] = "ЗТЛ"
        ws["D9"] = "Техников Петр Петрович"
        ws["E9"] = "Зубной техник"
        ws["O9"] = base / 2

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream


class PayrollRegistryTests(unittest.TestCase):
    def test_sep_oct_complete_nov_partial_and_accrued_formula(self):
        parsed = payroll_registry.parse_registry(
            workbook_bytes(),
            now=datetime(2026, 11, 11, 12, 0, tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )
        payroll = parsed["payroll"]
        self.assertEqual(payroll["period"], ["2026-09", "2026-10"])
        self.assertEqual(payroll["partial_period"], ["2026-11"])
        self.assertEqual(payroll["month_status"]["2026-09"], "complete")
        self.assertEqual(payroll["month_status"]["2026-10"], "complete")
        self.assertEqual(payroll["month_status"]["2026-11"], "partial")

        # 100 + KPI 10 - deduction 5 + vacation 20 + sick 3 = 128
        doctor = next(item for item in payroll["assignments"] if item["name"] == "Иванов Иван Иванович")
        self.assertAlmostEqual(doctor["months"]["2026-09"], 128.0)
        self.assertAlmostEqual(doctor["months"]["2026-10"], 228.0)
        self.assertNotIn("2026-11", doctor["months"])

        self.assertAlmostEqual(payroll["groups"]["Лаборатория"]["months"]["2026-09"], 50.0)
        self.assertAlmostEqual(payroll["groups"]["Лаборатория"]["months"]["2026-10"], 100.0)

    def test_formula_error_marks_closed_month_partial(self):
        parsed = payroll_registry.parse_registry(
            workbook_bytes(oct_error=True),
            now=datetime(2026, 12, 2, 12, 0, tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )
        self.assertEqual(parsed["payroll"]["month_status"]["2026-10"], "partial")
        self.assertIn("2026-10", parsed["payroll"]["partial_period"])
        self.assertNotIn("2026-10", parsed["payroll"]["period"])
        self.assertTrue(parsed["payroll"]["month_errors"]["2026-10"])

    def test_merge_keeps_opu_period_and_extends_only_payroll_period(self):
        base = {
            "version": 3,
            "available": True,
            "period": [f"2026-{m:02d}" for m in range(1, 9)],
            "source": "ОПУ + Свод по ЗП new",
            "payroll": {
                "assignments": [{
                    "assignment_id": "old",
                    "employee_id": "old",
                    "name": "Старый Врач",
                    "role": "Стоматолог",
                    "group": "Врачи",
                    "function": "Стоматолог",
                    "department": "Отделение терапии",
                    "months": {"2026-01": 111.0},
                    "total": 111.0,
                }],
                "total_by_month": {"2026-01": 111.0},
                "total": 111.0,
            },
            "control": {"status": "OK"},
        }
        overlay = payroll_registry.parse_registry(
            workbook_bytes(),
            now=datetime(2026, 11, 11, 12, 0, tzinfo=ZoneInfo("Asia/Novosibirsk")),
        )
        merged = payroll_registry.merge_economics_payload(base, overlay)

        self.assertEqual(merged["period"], base["period"])
        self.assertEqual(
            merged["payroll"]["period"],
            [f"2026-{m:02d}" for m in range(1, 11)],
        )
        self.assertEqual(merged["payroll"]["partial_period"], ["2026-11"])
        self.assertAlmostEqual(merged["payroll"]["total_by_month"]["2026-01"], 111.0)
        self.assertGreater(merged["payroll"]["total_by_month"]["2026-09"], 0)
        self.assertGreater(merged["payroll"]["total_by_month"]["2026-10"], 0)
        self.assertNotIn("2026-11", merged["payroll"]["total_by_month"])


if __name__ == "__main__":
    unittest.main()
