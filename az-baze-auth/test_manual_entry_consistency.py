import json
import os
import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask, g

os.environ.setdefault("AZBAZE_SECRET_KEY", "manual-entry-isolated-test")
import app as app_core
import report_storage
ROOT=Path(__file__).resolve().parent
REPORTS=ROOT.parent / "reports"

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
        self.assertIn('<script src="./period-view.js?v=20261029-paid-history-1"></script>',html)
        self.assertNotIn('period-view.js?v=20260918-1',html)
        self.assertIn("async function reportApi(url,options={})",html)
        self.assertIn("async function loadServerStore()",html)
        self.assertIn("$('#reportDate').dispatchEvent(new Event('change'))",html)

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
