import copy
import json
import re
import shutil
import subprocess
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import finrez
import management_view
import report_storage

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT.parent / "reports"


def historical_source():
    return {
        "2026-10-06": {
            "date": "2026-10-06", "_source": "legacy paired",
            "primary": 2, "repeat": 1, "dentPrimary": 1,
            "clinicPrimary": 0, "labOrders": 4,
            "discountAmount": 1500, "grossRevenue": 21500,
            "discountDataComplete": True,
            "_uploadControl": {"sourcePrimary": 2, "sourceRepeat": 1},
            "cashTotal": 95000, "cashOOO": 75000, "cashIP": 20000,
        },
        "2026-10-07": {
            "date": "2026-10-07", "_source": "completed only",
            "primary": 5, "repeat": 5, "dentPrimary": 1,
            "clinicPrimary": 0, "labOrders": 4,
            "_uploadControl": {"sourcePrimary": 5, "sourceRepeat": 5},
            "discountAmount": 1500, "grossRevenue": 21500,
            "discountDataComplete": True,
            "_serviceAsOf": "2026-10-06",
            "_clinicalAsOf": "2026-10-07",
            "cashTotal": 95000, "cashOOO": 75000, "cashIP": 20000,
        },
        "2026-10-09": {
            "date": "2026-10-09", "_cash_rule": "positive-receipts-only-v1",
            "cashTotal": 190000, "cashOOO": 120000, "cashIP": 70000,
            "factMedicine": 185000,
            "dentists": {"Кассовый врач": 185000},
        },
    }


class SourceAsOfProjectionTests(unittest.TestCase):
    def test_b05_later_cash_date_shows_last_completed_totals_without_cash_mutation(self):
        raw = historical_source()
        baseline = copy.deepcopy(raw)
        view = management_view.project_for_reports(raw)
        self.assertEqual(raw, baseline)
        projected = view["2026-10-09"]
        self.assertEqual(projected["_clinicalAsOf"], "2026-10-07")
        self.assertEqual(projected["_cashAsOf"], "2026-10-09")
        self.assertEqual(projected["_serviceAsOf"], "2026-10-06")
        self.assertEqual((projected["primary"], projected["repeat"]), (5, 5))
        self.assertEqual((projected["dentPrimary"], projected["labOrders"]), (1, 4))
        self.assertEqual((projected["discountAmount"], projected["grossRevenue"]), (1500, 21500))
        self.assertEqual((projected["cashTotal"], projected["cashOOO"], projected["cashIP"]), (190000, 120000, 70000))
        self.assertEqual(projected["dentists"], {"Кассовый врач": 185000})
        self.assertNotIn("primary", raw["2026-10-09"])

    def test_b05_never_invents_clinic_when_only_cash_uploaded(self):
        cash = {"2026-10-09": {"cashTotal": 2100, "cashOOO": 2100,
                               "_cash_rule": "positive-receipts-only-v1"}}
        view = management_view.project_for_reports(cash)
        self.assertEqual(view["2026-10-09"]["_clinicalAsOf"], "")
        self.assertEqual(view["2026-10-09"]["_cashAsOf"], "2026-10-09")
        self.assertNotIn("primary", view["2026-10-09"])

    def test_b05_never_carries_prior_month_visits_to_new_month(self):
        rows = {
            "2026-09-30": {"date": "2026-09-30", "primary": 100, "repeat": 120},
            "2026-10-01": {
                "_cash_rule": "positive-receipts-only-v1", "cashTotal": 300,
            },
        }
        view = management_view.project_for_reports(rows)
        self.assertEqual(view["2026-10-01"]["_clinicalAsOf"], "")
        self.assertNotIn("primary", view["2026-10-01"])

    def test_b05_copied_cash_row_keeps_original_clinic_as_of(self):
        rows = historical_source()
        copy_of_seventh = copy.deepcopy(rows["2026-10-07"])
        copy_of_seventh["date"] = "2026-10-09"
        copy_of_seventh["_cash_rule"] = "positive-receipts-only-v1"
        copy_of_seventh["cashTotal"] = 190000
        rows["2026-10-09"] = copy_of_seventh
        rows["2026-10-10"] = {"_cash_rule": "positive-receipts-only-v1",
                              "cashTotal": 200000}
        projected = management_view.project_for_reports(rows)
        self.assertEqual(projected["2026-10-10"]["_clinicalAsOf"], "2026-10-07")
        self.assertEqual(projected["2026-10-09"]["_clinicalAsOf"], "2026-10-07")

    def test_b05_finrez_uses_same_as_of_view_as_dashboard(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE report_data(date TEXT PRIMARY KEY,payload TEXT)")
        for day, record in historical_source().items():
            conn.execute("INSERT INTO report_data VALUES(?,?)",
                         (day, json.dumps(record, ensure_ascii=False)))
        with patch.object(finrez, "db", lambda: conn):
            month = finrez._management_months()["2026-10"]
        conn.close()
        self.assertEqual(month["date"], "2026-10-09")
        self.assertEqual(month["clinicalAsOf"], "2026-10-07")
        self.assertEqual(month["cashAsOf"], "2026-10-09")
        self.assertEqual(month["serviceAsOf"], "2026-10-06")
        self.assertEqual((month["cashTotal"], month["cashOOO"], month["cashIP"]),
                         (190000, 120000, 70000))
        self.assertEqual((month["dentPrimary"], month["clinicPrimary"]), (1, 0))
        self.assertEqual(month["grossRevenue"], 21500)
        self.assertEqual(month["discountAmount"], 1500)

    def test_b05_editable_api_returns_raw_storage_and_separate_projection(self):
        source = (ROOT / "report_storage.py").read_text(encoding="utf-8")
        self.assertIn("data=data,", source)
        self.assertIn("managementView=management_view.project_for_reports(data)", source)
        self.assertIn("SERVER_STORE=payload.data||{}", source)
        self.assertIn("MANAGEMENT_VIEW=payload.managementView||payload.data||{}", source)
        self.assertIn("function latestMonthlyRecords(year,startMonth,endMonth){const s=MANAGEMENT_VIEW,rows=", source)
        self.assertIn("const r=collectRecord()", source)

    def test_b05_clients_annotate_as_of_and_reject_partial_full_month(self):
        dashboard = (REPORTS / "dashboard.html").read_text(encoding="utf-8")
        forecast = (REPORTS / "forecast.html").read_text(encoding="utf-8")
        self.assertIn("response.managementView||response.data", dashboard)
        self.assertIn("Приёмы по ", dashboard)
        self.assertIn("Касса по ", dashboard)
        self.assertIn("r.clinicalAsOf", forecast)
        self.assertIn("(!cash||!!clinic)", forecast)
        self.assertIn("(!cash||cash>=end)", forecast)

    def test_b05_actual_management_page_period_uses_view_not_editable_raw(self):
        with patch.object(
            report_storage, "_read_report_file",
            lambda filename: (REPORTS / filename).read_text(encoding="utf-8"),
        ):
            rendered = report_storage._render_server_reports()
        self.assertIn("MANAGEMENT_VIEW=payload.managementView||payload.data||{}", rendered)
        self.assertIn("const s=MANAGEMENT_VIEW,rows=", rendered)
        self.assertIn("SERVER_STORE=payload.data||{}", rendered)
        self.assertIn("const r=collectRecord()", rendered)
        self.assertIn("await loadServerStore();fillRecord(saved)", rendered)
        self.assertIn("await loadServerStore();loadDate()", rendered)
        self.assertNotIn("const s=loadStore(),rows=", rendered)

    def test_b05_full_month_forecast_rejects_either_lagging_source(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node required to validate forecast evaluation")
        html = (REPORTS / "forecast.html").read_text(encoding="utf-8")
        defs = []
        for name in ("rec", "monthEndISO", "fullMonth"):
            found = re.search(r"^function " + name + r"\\([^\\n]+$", html, re.M)
            self.assertIsNotNone(found, "Missing forecast helper: " + name)
            defs.append(found.group(0))
        js = (
            "const FIN={management:{"
            "'2026-09':{date:'2026-09-30',clinicalAsOf:'2026-09-30',cashAsOf:'2026-09-29'},"
            "'2026-08':{date:'2026-08-31',clinicalAsOf:'2026-08-30',cashAsOf:'2026-08-31'},"
            "'2026-07':{date:'2026-07-31',clinicalAsOf:'2026-07-31',cashAsOf:'2026-07-31'}"
            "}};\\n" + "\\n".join(defs) + "\\n"
            "if(fullMonth('2026-09')!==false)throw Error('late cash counted full');"
            "if(fullMonth('2026-08')!==false)throw Error('late visits counted full');"
            "if(fullMonth('2026-07')!==true)throw Error('complete month rejected');"
        )
        result = subprocess.run([node, "-e", js], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_b05_projection_read_is_deterministic_no_write(self):
        source = historical_source()
        one = management_view.project_for_reports(source)
        two = management_view.project_for_reports(source)
        self.assertEqual(one, two)
        one["2026-10-09"]["primary"] = 999999
        self.assertNotIn("primary", source["2026-10-09"])
        self.assertEqual(two["2026-10-09"]["primary"], 5)


if __name__ == "__main__":
    unittest.main()
