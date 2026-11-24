import json
import sqlite3
import unittest
from io import BytesIO

from openpyxl import Workbook

import management_lab
import paid_services


LAB = "Функциональная стоматология / Зуботехническая лаборатория (клиника)"


def parsed():
    wb = Workbook()
    ws = wb.active
    ws.append(["Группа услуг", "Дата", "Услуги", "Количество",
               "Задолженность на нач. периода", "Сумма со скидкой",
               "Оплачено", "Задолженность на конец периода"])
    ws.append(["Услуги", None, None, None, 0, 1200, 800, 400])
    ws.append(["Dent A.", None, None, None, 0, 1000, 600, 400])
    ws.append(["Счет №1 от 03.11.2026", None, None, None, 0, 1000, 600, 400])
    ws.append([LAB, "03.11.2026", "Аппарат", 1, 0, 600, 400, 200])
    ws.append(["Терапия", "03.11.2026", "Лечение", 1, 0, 400, 200, 200])
    ws.append(["Clinic B.", None, None, None, 0, 200, 200, 0])
    ws.append(["Счет №2 от 03.11.2026", None, None, None, 0, 200, 200, 0])
    ws.append(["Другие услуги", "03.11.2026", "Не ЗТЛ", 1, 0, 200, 200, 0])
    buf = BytesIO()
    wb.save(buf)
    wb.close()
    return paid_services.parse_bytes(buf.getvalue(), "synthetic.xlsx",
            known_staff={"Dent A.", "Clinic B."},
            period_start="2026-11-01", period_end="2026-11-17")


def service_row(day, group=LAB, amount=600, invoice="1", staff="Dent A."):
    return {"date": day, "group": group, "amount": amount, "invoice": invoice,
            "staff": staff, "patient": "SYNTHETIC NOT EXPOSED"}


def add_day(conn, day, rows, retail=None, sha="source"):
    conn.execute("INSERT INTO daily_uploads VALUES(?,?,?)",
                 (day, json.dumps({"data_date": day, "items": rows,
                                  "retail_items": retail or []}), sha))


class ManagementLabTests(unittest.TestCase):
    def test_group_only_and_mis_parse(self):
        for group in (LAB, "Зуботехническая лаборатория",
                      "A / ЗУБОТЕХНИЧЕСКАЯ   ЛАБОРАТОРИЯ / аппараты"):
            self.assertTrue(management_lab.is_lab_group(group))
        for group in ("Терапия", "Не зуботехническая лаборатория",
                      "Зуботехническая лаборатория прочее", "", None):
            self.assertFalse(management_lab.is_lab_group(group))
        report = parsed()
        evidence = management_lab.paid_evidence(report)
        self.assertEqual(evidence, {"version": 1, "paid": 400, "reason": "reconciled"})
        compact = paid_services.compact_report(report)
        self.assertEqual(compact[management_lab.EVIDENCE_KEY], evidence)
        summary = paid_services.direction_summary(report, {"Dent A.": "D"},
                                                  {"Clinic B.": "C"}, {})
        self.assertEqual(summary["dentPaid"], 600)
        self.assertEqual(summary["structurePaid"], 200)
        self.assertEqual(summary["paidTotal"], 800)
        self.assertNotIn(management_lab.EVIDENCE_KEY,
                         paid_services.compact_report({k:v for k,v in report.items() if k!="source_type"}))

    def test_residual_and_missing_groups_not_allocated(self):
        report = parsed()
        # Missing opening-debt payment has no group proof.
        report["providers"]["Dent A."]["paid"] += 50
        report["totals"]["paid"] += 50
        evidence = management_lab.paid_evidence(report)
        self.assertIsNone(evidence["paid"])
        self.assertEqual(evidence["knownPaid"], 400)
        self.assertEqual(evidence["reason"], "unallocated_payment")
        report = parsed()
        report["items"][0]["group"] = ""
        self.assertIsNone(management_lab.paid_evidence(report)["paid"])
        report = parsed()
        report["items"][0]["paid_amount"] = None
        self.assertIsNone(management_lab.paid_evidence(report)["paid"])

    def test_zero_negative_and_duplicate_provider_orders(self):
        report = parsed()
        report["items"][0]["paid_amount"] = -10
        report["providers"]["Dent A."]["paid"] = 190
        report["totals"]["paid"] = 390
        self.assertEqual(management_lab.paid_evidence(report)["paid"], -10)
        report["items"][0]["paid_amount"] = 0
        report["providers"]["Dent A."]["paid"] = 200
        report["totals"]["paid"] = 400
        self.assertEqual(management_lab.paid_evidence(report)["paid"], 0)
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE daily_uploads(data_date TEXT PRIMARY KEY,normalized_json TEXT,services_sha256 TEXT)")
        day = "2026-11-03"
        add_day(conn, day, [service_row(day, amount=300),
                            service_row(day, amount=200, staff="Clinic B."),
                            service_row(day, amount=-20, invoice="2"),
                            service_row(day, group="Терапия", amount=900, invoice="3", staff="Lab")])
        add_day(conn, "2026-11-04", [service_row("2026-11-04", group="Терапия")])
        add_day(conn, "2026-11-05", [])
        add_day(conn, "2026-11-06", [service_row("2026-11-06", invoice="")])
        add_day(conn, "2026-11-07", [service_row("2026-11-07", group="")])
        add_day(conn, "2026-11-08", [service_row("2026-11-08")], sha="")
        conn.commit()
        before = "\n".join(conn.iterdump())
        conn.execute("PRAGMA query_only=ON")
        self.assertEqual(management_lab.service_days(conn), [
            {"date": day, "services": 480, "orders": 2},
            {"date": "2026-11-04", "services": 0, "orders": 0},
            {"date": "2026-11-06", "services": 600, "orders": None},
            {"date": "2026-11-07", "services": None, "orders": None},
        ])
        self.assertEqual("\n".join(conn.iterdump()), before)

    def test_exact_period_precedence_does_not_reuse_legacy_group_amount(self):
        evidence = {"version":1,"paid":400,"reason":"reconciled"}
        base = {"from":"2026-11-01","to":"2026-11-17"}
        result = management_lab.paid_periods([
            {**base,"payload":{management_lab.EVIDENCE_KEY:evidence}},
            {**base,"payload":{}},
        ])
        self.assertIsNone(result[0]["paid"])

    def test_real_api_readonly_and_cash_doctors_unchanged(self):
        from test_paid_period_report import PaidPeriodReportTests
        fixture = PaidPeriodReportTests()
        fixture.setUp()
        try:
            fixture.store(parsed())
            fixture.store(fixture.report("2026-10-01","2026-10-31"))
            conn = fixture.conn
            conn.execute("CREATE TABLE daily_uploads(data_date TEXT PRIMARY KEY,normalized_json TEXT,services_sha256 TEXT)")
            add_day(conn,"2026-11-03",[service_row("2026-11-03")])
            original = {"date":"2026-11-17","labRevenue":9500,"labOrders":26,
                        "cashTotal":10000,"factLab":9500,"dentists":{"Dent Doctor":600}}
            conn.execute("INSERT INTO report_data VALUES(?,?,?,?)",
                         (original["date"],json.dumps(original),1,"t"))
            conn.commit()
            before = "\n".join(conn.iterdump())
            conn.execute("PRAGMA query_only=ON")
            full = fixture.client().get("/api/reports/data").get_json()
            self.assertEqual(full["data"][original["date"]], original)
            self.assertEqual(full["doctorPaidSnapshots"]["2026-11"][0]["dentists"], {"Dent Doctor":600})
            self.assertIsNone(full["laboratoryPaidPeriods"][0]["paid"])
            self.assertEqual(full["laboratoryPaidPeriods"][1]["paid"],400)
            self.assertEqual(full["laboratoryServiceDays"],[
                {"date":"2026-11-03","services":600,"orders":1}])
            self.assertNotIn("SYNTHETIC NOT EXPOSED",json.dumps(full))
            dashboard=fixture.client().get("/api/reports/data?view=dashboard").get_json()
            self.assertNotIn("laboratoryPaidPeriods",dashboard)
            self.assertNotIn("laboratoryServiceDays",dashboard)
            self.assertEqual("\n".join(conn.iterdump()),before)
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
