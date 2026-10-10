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
        self.assertIn("separate=!!clinic||!!cash", forecast)
        self.assertIn("(!separate||(!!clinic&&clinic>=end&&!!cash&&cash>=end))", forecast)

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
            found = re.search(r"^function " + name + r"\([^\n]+$", html, re.M)
            self.assertIsNotNone(found, "Missing forecast helper: " + name)
            defs.append(found.group(0))
        js = (
            "const FIN={management:{"
            "'2026-09':{date:'2026-09-30',clinicalAsOf:'2026-09-30',cashAsOf:'2026-09-29'},"
            "'2026-08':{date:'2026-08-31',clinicalAsOf:'2026-08-30',cashAsOf:'2026-08-31'},"
            "'2026-07':{date:'2026-07-31',clinicalAsOf:'2026-07-31',cashAsOf:'2026-07-31'},"
            "'2026-10':{date:'2026-10-31',clinicalAsOf:'2026-10-31',cashAsOf:''},"
            "'2026-11':{date:'2026-11-30',clinicalAsOf:'',cashAsOf:'2026-11-30'},"
            "'2026-12':{date:'2026-12-31'}"
            "}};\n" + "\n".join(defs) + "\n"
            "if(fullMonth('2026-09')!==false)throw Error('late cash counted full');"
            "if(fullMonth('2026-08')!==false)throw Error('late visits counted full');"
            "if(fullMonth('2026-07')!==true)throw Error('complete month rejected');"
            "if(fullMonth('2026-10')!==false)throw Error('missing cash accepted');"
            "if(fullMonth('2026-11')!==false)throw Error('missing clinic accepted');"
            "if(fullMonth('2026-12')!==true)throw Error('legacy complete month rejected');"
        )
        result = subprocess.run([node, "-e", js], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_b06_legacy_cash_clone_without_markers_stays_cash_as_of_only(self):
        records = historical_source()
        from_sixth = copy.deepcopy(records["2026-10-06"])
        from_sixth["date"] = "2026-10-09"
        from_sixth["_cash_rule"] = "positive-receipts-only-v1"
        from_sixth["_cash_source"] = "Счета и оплаты"
        from_sixth["cashTotal"] = 190000
        from_sixth["cashOOO"] = 120000
        from_sixth["cashIP"] = 70000
        records["2026-10-09"] = from_sixth
        snapshot = copy.deepcopy(records)
        projected = management_view.project_for_reports(records)
        self.assertEqual(records, snapshot)
        self.assertEqual(projected["2026-10-09"]["_clinicalAsOf"], "2026-10-07")
        self.assertEqual(projected["2026-10-09"]["_cashAsOf"], "2026-10-09")
        self.assertEqual((projected["2026-10-09"]["primary"],
                          projected["2026-10-09"]["repeat"]), (5, 5))
        self.assertEqual(projected["2026-10-09"]["cashTotal"], 190000)

    def test_b06_explicit_cash_clone_without_clinic_does_not_forge_visits(self):
        rows = {
            "2026-10-31": {
                "date": "2026-10-31", "_cash_rule": "positive-receipts-only-v1",
                "_cashForwardClone": True, "_clinicalAsOf": "",
                "_cashAsOf": "2026-10-31", "cashTotal": 3000,
            }
        }
        view = management_view.project_for_reports(rows)
        self.assertEqual(view["2026-10-31"]["_clinicalAsOf"], "")
        self.assertNotIn("primary", view["2026-10-31"])
        self.assertEqual(view["2026-10-31"]["cashTotal"], 3000)

    def test_b05_projection_read_is_deterministic_no_write(self):
        source = historical_source()
        one = management_view.project_for_reports(source)
        two = management_view.project_for_reports(source)
        self.assertEqual(one, two)
        one["2026-10-09"]["primary"] = 999999
        self.assertNotIn("primary", source["2026-10-09"])
        self.assertEqual(two["2026-10-09"]["primary"], 5)


# W05: execute the actual date-comparison renderer and formatting helpers in
# a DOM stub with the backend's genuine read-only projection fixture.
_W05_NODE_RENDER = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync('reports/period-view.js', 'utf8');
const html = fs.readFileSync('reports/index.html', 'utf8');

function line(prefix) {
  const found = html.split('\n').find(s => s.startsWith(prefix));
  if (!found) throw Error('missing management helper: ' + prefix);
  return found;
}
function between(startText, endText) {
  const start = source.indexOf(startText);
  const end = source.indexOf(endText, start + startText.length);
  if (start < 0 || end < 0) throw Error('date renderer region missing: ' + startText);
  return source.slice(start, end);
}
const actualCode = [
  line('const money='), line('const num='), line('const pct='),
  line('const count='), line('function daysInMonth('), line('function derive('),
  between('  const fmtMoney=', '  function getPeriodDefs('),
  between('  function addGroup(', '  function addMetric('),
  between('  const monthNames=', '\n  let dateStickyRaf=')
].join('\n');

class Element {
  constructor() { this._html = ''; this.textContent = ''; this.children = [];
    this.className = ''; this.value = ''; }
  get innerHTML() { return this._html; }
  set innerHTML(value) { this._html = value; this.children = []; }
  appendChild(child) { this.children.push(child); return child; }
}
const elements = {};
for (const id of ['reportDate', 'compareRange', 'dateCompareHead',
                  'dateCompareBody', 'dateSummaryNote', 'dateCompareTitle',
                  'dateCompareHint']) elements[id] = new Element();
elements.reportDate.value = input.date;
elements.compareRange.value = input.range || 'q4';

let rawReads = 0;
const rawBefore = JSON.stringify(input.raw);
const viewBefore = JSON.stringify(input.view);
const context = vm.createContext({
  document: {getElementById: id => elements[id] || null,
             createElement: () => new Element()},
  dentists: input.dentists || [], clinicDocs: input.clinicDocs || [],
  loadStore: () => {rawReads += 1; return input.raw;},
  queueDateStickyClone: () => {}, __view: input.view,
  __viewUpdated: input.updatedView
});
if (!input.offline) vm.runInContext('let MANAGEMENT_VIEW = __view;', context);
vm.runInContext(actualCode, context);

function cells() {
  const selectors = {
    primary: ['Первичные приёмы', 'row-general'],
    repeat: ['Повторные приёмы', 'row-general'],
    fact: ['Факт', 'row-general'],
    ooo: ['ДС ООО', 'row-general'],
    ip: ['ДС ИП', 'row-general'],
    dentPrimary: ['Первичные', 'row-visits-dent'],
    labOrders: ['Количество заказов', 'row-lab-volume'],
    dentist: ['Кассовый врач', 'row-team-dent']
  };
  const result = {};
  for (const [name, [label, css]] of Object.entries(selectors)) {
    const row = elements.dateCompareBody.children.find(
      el => el.className.split(' ').includes(css) &&
            el.innerHTML.startsWith('<td>' + label + '</td>'));
    const htmlCells = row
      ? [...row.innerHTML.matchAll(/<td(?:\s+[^>]*)?>([\s\S]*?)<\/td>/g)]
          .slice(1).map(match => match[1].replace(/<[^>]*>/g, '')
            .replace(/[\u00a0\u202f\s]/g, ''))
      : null;
    result[name] = htmlCells;
  }
  return result;
}
vm.runInContext('renderDateComparison();', context);
const initial = cells();
let updated = null;
if (input.updatedView) {
  vm.runInContext('MANAGEMENT_VIEW = __viewUpdated; renderDateComparison();', context);
  updated = cells();
}
process.stdout.write(JSON.stringify({
  title: elements.dateCompareTitle.textContent,
  head: elements.dateCompareHead.innerHTML,
  initial, updated, rawReads,
  rawUnchanged: rawBefore === JSON.stringify(input.raw),
  viewUnchanged: viewBefore === JSON.stringify(input.view)
}));
"""


class W05ActualDateComparisonTests(unittest.TestCase):
    def render_date(self, raw, date, *, projected=None, interval="q4",
                    offline=False, updated_view=None):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for executing actual W05 renderer")
        payload = {
            "raw": raw, "view": projected, "date": date,
            "range": interval, "offline": offline,
            "updatedView": updated_view, "dentists": ["Кассовый врач"],
        }
        result = subprocess.run(
            [node, "-e", _W05_NODE_RENDER],
            input=json.dumps(payload, ensure_ascii=False),
            text=True, capture_output=True, cwd=ROOT.parent, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_w05_cash_only_day_displays_confirmed_visits_and_exact_cash(self):
        raw = historical_source()
        view = management_view.project_for_reports(raw)
        actual = self.render_date(raw, "2026-10-09", projected=view)
        self.assertEqual(actual["initial"]["primary"][0], "5")
        self.assertEqual(actual["initial"]["repeat"][0], "5")
        self.assertEqual(actual["initial"]["dentPrimary"][0], "1")
        self.assertEqual(actual["initial"]["labOrders"][0], "4")
        self.assertEqual(actual["initial"]["fact"][0], "190000₽")
        self.assertEqual(actual["initial"]["ooo"][0], "120000₽")
        self.assertEqual(actual["initial"]["ip"][0], "70000₽")
        self.assertEqual(actual["initial"]["dentist"][0], "185000₽")
        self.assertEqual(actual["rawReads"], 0)
        self.assertTrue(actual["rawUnchanged"] and actual["viewUnchanged"])

    def test_w05_cash_only_without_clinic_does_not_invent_visits(self):
        raw = {"2026-10-09": {"date": "2026-10-09",
               "_cash_rule": "positive-receipts-only-v1",
               "cashTotal": 2100, "cashOOO": 2100}}
        actual = self.render_date(
            raw, "2026-10-09",
            projected=management_view.project_for_reports(raw),
        )
        self.assertEqual(actual["initial"]["primary"][0], "—")
        self.assertEqual(actual["initial"]["repeat"][0], "—")
        self.assertEqual(actual["initial"]["fact"][0], "2100₽")

    def test_w05_31st_clamps_to_february_and_never_carries_prior_month(self):
        raw = {
            "2026-01-31": {"date": "2026-01-31", "primary": 8, "repeat": 3},
            "2026-02-28": {"date": "2026-02-28",
                           "_cash_rule": "positive-receipts-only-v1",
                           "cashTotal": 900},
        }
        actual = self.render_date(
            raw, "2026-01-31", projected=management_view.project_for_reports(raw),
            interval="q1",
        )
        self.assertIn("28.02.2026", actual["head"])
        self.assertEqual(actual["initial"]["primary"], ["8", "—", "—"])
        self.assertEqual(actual["initial"]["repeat"], ["3", "—", "—"])
        self.assertEqual(actual["initial"]["fact"][1], "900₽")

    def test_w05_missing_exact_date_stays_missing_and_offline_falls_back(self):
        raw = historical_source()
        view = management_view.project_for_reports(raw)
        missing = self.render_date(raw, "2026-10-08", projected=view)
        self.assertIn("нет данных", missing["head"])
        self.assertEqual(missing["initial"]["primary"][0], "—")
        self.assertEqual(missing["rawReads"], 0)
        offline = self.render_date(raw, "2026-10-09", offline=True)
        self.assertEqual(offline["initial"]["primary"][0], "—")
        self.assertEqual(offline["initial"]["fact"][0], "190000₽")
        self.assertGreater(offline["rawReads"], 0)

    def test_w05_marked_and_legacy_cash_clones_use_true_as_of_source(self):
        marked = historical_source()
        clone = copy.deepcopy(marked["2026-10-07"])
        clone.update(date="2026-10-09",
                     _cash_rule="positive-receipts-only-v1",
                     _cashForwardClone=True, cashTotal=190000)
        marked["2026-10-09"] = clone
        marked["2026-10-10"] = {
            "date": "2026-10-10", "_cash_rule": "positive-receipts-only-v1",
            "cashTotal": 200000,
        }
        actual = self.render_date(
            marked, "2026-10-10",
            projected=management_view.project_for_reports(marked),
        )
        self.assertEqual(actual["initial"]["primary"][0], "5")
        self.assertEqual(actual["initial"]["fact"][0], "200000₽")

        legacy = historical_source()
        copy_of_sixth = copy.deepcopy(legacy["2026-10-06"])
        copy_of_sixth.update(
            date="2026-10-09", _cash_rule="positive-receipts-only-v1",
            _cash_source="Счета и оплаты", cashTotal=190000,
            cashOOO=120000, cashIP=70000,
        )
        legacy["2026-10-09"] = copy_of_sixth
        actual = self.render_date(
            legacy, "2026-10-09",
            projected=management_view.project_for_reports(legacy),
        )
        self.assertEqual(actual["initial"]["primary"][0], "5")
        self.assertEqual(actual["initial"]["fact"][0], "190000₽")
        self.assertTrue(actual["rawUnchanged"])

    def test_w05_refetch_rerender_changes_view_only_raw_and_quarters_unchanged(self):
        raw = historical_source()
        view = management_view.project_for_reports(raw)
        later = copy.deepcopy(view)
        later["2026-10-09"]["primary"] = 6
        actual = self.render_date(
            raw, "2026-10-09", projected=view, updated_view=later,
        )
        self.assertEqual(actual["initial"]["primary"][0], "5")
        self.assertEqual(actual["updated"]["primary"][0], "6")
        self.assertEqual(actual["rawReads"], 0)
        self.assertTrue(actual["rawUnchanged"])
        source = (REPORTS / "period-view.js").read_text(encoding="utf-8")
        backend = (ROOT / "report_storage.py").read_text(encoding="utf-8")
        self.assertIn(
            "store=(typeof MANAGEMENT_VIEW!=='undefined'&&MANAGEMENT_VIEW)"
            "?MANAGEMENT_VIEW:loadStore();", source,
        )
        self.assertIn("const store=loadStore(),r=store[date]||blankRecord(date)", source)
        self.assertIn("store[date]=r;saveStore(store)", source)
        self.assertIn("return loadStore()[date]||null", source)
        self.assertIn(
            "function latestMonthlyRecords(year,startMonth,endMonth){const s=MANAGEMENT_VIEW,rows=",
            backend,
        )
        self.assertIn("function loadStore(){return SERVER_STORE}", backend)
        self.assertIn("function saveStore(v){SERVER_STORE=v}", backend)


if __name__ == "__main__":
    unittest.main()
