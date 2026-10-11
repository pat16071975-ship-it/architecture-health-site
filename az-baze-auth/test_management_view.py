import copy
import json
import re
import shutil
import subprocess
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from flask import Flask, g

import app as app_core
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


# The real manual-entry handler is executed in Node, rather than merely
# grepping for an API call. Its static/offline path must also remain intact.
_MANUAL_SAVE_NODE = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0,'utf8'));
const source = fs.readFileSync('reports/period-view.js','utf8');
const start = source.indexOf('  async function saveEntry(){');
const end = source.indexOf('\n  function renderMismatchNote(',start);
if(start<0||end<0)throw Error('manual-entry save handler missing');
const code=source.slice(start,end);
let store=JSON.parse(JSON.stringify(input.store));
const initial=JSON.stringify(store);
let saved=null,saves=0,refreshes=0,closed=0,compares=0;
const requests=[],alerts=[],renders=[],errors=[];
const date=input.date;
const controls={
  entryDate:{value:date},
  entrySave:{disabled:!!input.preDisabled},
  reportDate:{value:'2026-09-01'},
  dateViewMode:{value:'compare'},
  status:{textContent:'Редактирование'}
};
const fields=[
  {dataset:{entryKey:'primary'},value:'6'},
  {dataset:{entryKey:'labOrders'},value:'7'}
];
const context=vm.createContext({
  document:{
    getElementById:id=>controls[id],
    querySelectorAll:selector=>selector==='[data-entry-key]'?fields:[],
  },
  loadStore:()=>store,
  saveStore:value=>{saves++;store=value;},
  blankRecord:d=>({date:d}),
  fillRecord:value=>renders.push(JSON.parse(JSON.stringify(value))),
  renderMismatchNote:()=>{},
  closeEntry:()=>{closed++;},
  renderDateComparison:()=>{compares++;},
  alert:message=>alerts.push(message),
  console:{error:error=>errors.push(String(error))}
});
if(input.mode!=='offline'){
  context.reportApi=async (url,options)=>{
    requests.push({url,method:options.method,body:JSON.parse(options.body)});
    if(input.mode==='fail')throw Error('Synthetic network failure');
    const incoming=JSON.parse(options.body);
    saved={...incoming,cashTotal:190000,cashOOO:120000,cashIP:70000,
      paidDentistry:25000,dentists:{'Контрольный врач':185000}};
    return {ok:true,record:JSON.parse(JSON.stringify(saved))};
  };
  context.loadServerStore=async ()=>{
    refreshes++;
    if(input.mode==='refresh_fail')throw Error('Synthetic refresh failure');
    store={[date]:JSON.parse(JSON.stringify(saved))};
  };
}
vm.runInContext(code,context);
const execPromise=input.mode==='double'
  ?Promise.all([vm.runInContext('saveEntry()',context),vm.runInContext('saveEntry()',context)])
  :vm.runInContext('saveEntry()',context);
Promise.resolve(execPromise).then(()=>{
  process.stdout.write(JSON.stringify({requests,alerts,errors,store,saves,refreshes,
    closed,compares,renders,controls,initialUnchanged:initial===JSON.stringify(store)}));
}).catch(err=>{process.stderr.write(String(err));process.exitCode=1;});
"""


class ManagementDialogSaveRegressionTests(unittest.TestCase):
    """Exercise the genuine JS manual dialog in both offline and server modes."""

    def execute(self, mode, *, disabled=False):
        node=shutil.which("node")
        if not node:
            self.skipTest("Node required to execute the real manual-entry handler")
        existing={
            "date":"2026-10-09", "primary":5, "labOrders":4,
            "cashTotal":190000, "cashOOO":120000, "cashIP":70000,
            "dentists":{"Контрольный врач":185000},
            "paidDentistry":25000, "paidDataComplete":True,
            "_cash_rule":"positive-receipts-only-v1",
            "grossRevenue":21500, "discountAmount":1500,
        }
        payload={"mode":mode,"date":"2026-10-09","store":{"2026-10-09":existing},
                 "preDisabled":disabled}
        result=subprocess.run(
            [node,"-e",_MANUAL_SAVE_NODE],input=json.dumps(payload,ensure_ascii=False),
            text=True,capture_output=True,cwd=ROOT.parent,timeout=20,
        )
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def test_management_dialog_server_page_loads_bumped_handler_asset(self):
        with patch.object(
            report_storage, "_read_report_file",
            lambda filename: (REPORTS / filename).read_text(encoding="utf-8"),
        ):
            html=report_storage._render_server_reports()
        self.assertIn('<script src="./period-view.js?v=20261011-1"></script>',html)
        self.assertNotIn('period-view.js?v=20260918-1',html)
        self.assertIn("async function reportApi(url,options={})",html)
        self.assertIn("async function loadServerStore()",html)

    def test_management_dialog_server_save_put_roundtrip_refresh_and_cash_preservation(self):
        actual=self.execute("success")
        self.assertEqual(len(actual["requests"]),1)
        request=actual["requests"][0]
        self.assertEqual(request["url"],"/api/reports/data/2026-10-09")
        self.assertEqual(request["method"],"PUT")
        self.assertEqual((request["body"]["primary"],request["body"]["labOrders"]),("6","7"))
        self.assertEqual((request["body"]["cashTotal"],request["body"]["cashOOO"],
                          request["body"]["cashIP"]),(190000,120000,70000))
        self.assertEqual(actual["refreshes"],1)
        self.assertEqual((actual["saves"],actual["closed"],actual["compares"]),(1,1,1))
        self.assertFalse(actual["controls"]["entrySave"]["disabled"])
        self.assertIn("Сохранено на сервере",actual["controls"]["status"]["textContent"])
        stored=actual["store"]["2026-10-09"]
        self.assertEqual((stored["primary"],stored["labOrders"]),("6","7"))
        self.assertEqual((stored["cashTotal"],stored["cashOOO"],stored["cashIP"]),
                         (190000,120000,70000))
        self.assertEqual(stored["dentists"]["Контрольный врач"],185000)
        self.assertEqual(stored["paidDentistry"],25000)
        self.assertEqual(stored["grossRevenue"],21500)
        self.assertEqual(stored["discountAmount"],1500)

    def test_management_dialog_rejected_put_keeps_input_modal_and_raw_unchanged(self):
        actual=self.execute("fail")
        self.assertEqual(len(actual["requests"]),1)
        self.assertTrue(actual["initialUnchanged"])
        self.assertEqual((actual["saves"],actual["closed"],actual["refreshes"]),(0,0,0))
        self.assertTrue(actual["alerts"])
        self.assertIn("Не удалось сохранить",actual["controls"]["status"]["textContent"])
        self.assertNotIn("Сохранено",actual["controls"]["status"]["textContent"])
        self.assertFalse(actual["controls"]["entrySave"]["disabled"])

    def test_management_dialog_refresh_failure_does_not_misreport_put_success(self):
        actual=self.execute("refresh_fail")
        self.assertEqual(len(actual["requests"]),1)
        self.assertEqual((actual["saves"],actual["closed"],actual["refreshes"]),(1,1,1))
        self.assertEqual(actual["compares"],0)
        self.assertIn("Сохранено на сервере, но не удалось обновить",
                      actual["controls"]["status"]["textContent"])
        self.assertFalse(actual["alerts"])
        self.assertEqual(actual["store"]["2026-10-09"]["cashTotal"],190000)

    def test_management_dialog_offline_still_saves_only_in_browser(self):
        actual=self.execute("offline")
        self.assertEqual(actual["requests"],[])
        self.assertEqual((actual["saves"],actual["closed"],actual["compares"]),(1,1,1))
        self.assertIn("Сохранено в этом браузере",actual["controls"]["status"]["textContent"])
        self.assertEqual(actual["store"]["2026-10-09"]["primary"],"6")

    def test_management_dialog_blocks_parallel_or_disabled_save(self):
        actual=self.execute("double")
        self.assertEqual(len(actual["requests"]),1)
        self.assertEqual(actual["closed"],1)
        disabled=self.execute("success",disabled=True)
        self.assertEqual(disabled["requests"],[])
        self.assertTrue(disabled["initialUnchanged"])
        self.assertEqual(disabled["closed"],0)


class ManagementDialogServerEndpointTests(unittest.TestCase):
    """Fully synthetic Flask/SQLite POST->GET roundtrip, actual CSRF and permissions."""

    def setUp(self):
        self.conn=sqlite3.connect(":memory:")
        self.conn.row_factory=sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE report_data(
                date TEXT PRIMARY KEY,payload TEXT NOT NULL,
                updated_by INTEGER,updated_at TEXT
            );
            CREATE TABLE report_blobs(
                key TEXT PRIMARY KEY,payload TEXT NOT NULL,
                updated_by INTEGER,updated_at TEXT
            );
        """)
        self.original={
            "date":"2026-10-09",
            "cashTotal":190000,"cashOOO":120000,"cashIP":70000,
            "_cash_rule":"positive-receipts-only-v1",
            "dentists":{"Контрольный врач":185000},
            "paidDataComplete":True,"paidDentistry":25000,
            "primary":5,"repeat":4,"labOrders":3,
            "discountAmount":1500,"grossRevenue":21500,
        }
        self.conn.execute(
            "INSERT INTO report_data VALUES(?,?,?,?)",
            ("2026-10-09",json.dumps(self.original,ensure_ascii=False),42,"seed"),
        )
        self.conn.commit()
        self.allowed=True
        self.app=Flask("az_management_dialog_roundtrip")
        self.app.secret_key="synthetic-test-session"
        @self.app.before_request
        def test_user():
            g.user={"id":42}
        self.patches=[
            patch.object(report_storage,"_ensure_schema",return_value=None),
            patch.object(report_storage,"db",return_value=self.conn),
            patch.object(report_storage,"audit",return_value=None),
            patch.object(app_core,"user_permissions",
                         side_effect=lambda user:{"reports"} if self.allowed else set()),
        ]
        for p in self.patches:p.start()
        report_storage.register_report_storage(self.app)
        self.client=self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["csrf"]="synthetic-csrf"

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.conn.close()

    def stored(self):
        row=self.conn.execute(
            "SELECT payload FROM report_data WHERE date=?",("2026-10-09",)
        ).fetchone()
        return json.loads(row["payload"])

    def test_management_manual_put_then_get_roundtrip_keeps_owned_money(self):
        body=dict(self.original)
        body.update(primary="6",labOrders="7",cashTotal=0,cashOOO=0,cashIP=0,
                    paidDentistry=0,dentists={"Контрольный врач":0})
        response=self.client.put(
            "/api/reports/data/2026-10-09",
            json=body,headers={"X-CSRF-Token":"synthetic-csrf"},
        )
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        saved=response.get_json()["record"]
        self.assertEqual((saved["primary"],saved["labOrders"]),("6","7"))
        self.assertEqual((saved["cashTotal"],saved["cashOOO"],saved["cashIP"]),
                         (190000,120000,70000))
        self.assertEqual(saved["dentists"],{"Контрольный врач":185000})
        self.assertEqual(saved["paidDentistry"],25000)
        fetched=self.client.get("/api/reports/data")
        self.assertEqual(fetched.status_code,200,fetched.get_data(as_text=True))
        raw=fetched.get_json()["data"]["2026-10-09"]
        self.assertEqual(raw,saved)
        self.assertEqual(self.stored(),saved)

    def test_management_manual_put_invalid_csrf_or_permission_never_changes_db(self):
        before=self.stored()
        denied=self.client.put(
            "/api/reports/data/2026-10-09",json={"primary":999},
        )
        self.assertEqual(denied.status_code,400)
        self.assertEqual(self.stored(),before)
        self.allowed=False
        forbidden=self.client.put(
            "/api/reports/data/2026-10-09",
            json={"primary":999},headers={"X-CSRF-Token":"synthetic-csrf"},
        )
        self.assertEqual(forbidden.status_code,403)
        self.assertEqual(self.stored(),before)


if __name__ == "__main__":
    unittest.main()
