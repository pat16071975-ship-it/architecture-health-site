"""Synthetic recovery only: exact paired rows, archive bytes, read-only projection."""
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook
import management_lab as lab
import management_lab_recovery as recovery
import management_lab_recovery_parser as parser
import mis_archive_bundle as bundle

LAB="Зуботехническая лаборатория / Услуги ЗТЛ"
OTHER="Терапия"


def workbook(paid=False,amount=600,paid_amount=400,alt=False,month="2026-01",day="05",old_paid=50,edit=None):
    wb=Workbook()
    ws=wb.active
    header=["Группа услуг","Услуги",None,None]+(
        ["Задолженность на нач. периода","Сумма со скидкой","Оплачено","Задолженность на конец периода"] if paid
        else ["Сумма по прейскуранту","Скидка","Сумма со скидкой"])
    if alt:header[1]="Врач / Исполнитель"
    ws.append(header)
    if paid:
        ws.append(["Услуги",None,None,None,100,amount+200,paid_amount+200+old_paid,amount+100-paid_amount-old_paid])
        ws.append(["Сотрудник А.",None,None,None,100,amount+200,paid_amount+200+old_paid,amount+100-paid_amount-old_paid])
        ws.append(["Счет №1 от 05.01.2026 9:00:00",None,None,None,0,amount+200,paid_amount+200,amount-paid_amount])
        ws.append([LAB,"05.01.2026 9:00","Аппарат",1,0,amount,paid_amount,amount-paid_amount])
        ws.append([OTHER,None,"Лечение",1,0,200,200,0])
        ws.append(["Неоплаченные услуги на начало периода",None,None,None,100,0,old_paid,100-old_paid])
    else:
        ws.append(["Итого",None,None,None,amount+200,0,amount+200])
        ws.append(["Сотрудник А.",None,None,None,amount+200,0,amount+200])
        ws.append(["Счет №1 от 05.01.2026 9:00:00",None,None,None,amount+200,0,amount+200])
        ws.append([LAB,"05.01.2026 9:00","Аппарат",1,amount,0,amount])
        ws.append([OTHER,None,"Лечение",1,200,0,200])
    source_day=day+"."+month[5:7]+"."+month[:4]
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value,str) and "05.01.2026" in cell.value:
                cell.value=cell.value.replace("05.01.2026",source_day)
    if edit:
        edit(ws)
    out=io.BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()


def make_pack(path,service=None,paid=None,month="2026-01",day="05"):
    import zipfile
    source=[("service_prices_discounts",service or workbook(month=month,day=day)),("paid_and_debt",paid or workbook(True,month=month,day=day))]
    observed=month+"-"+day
    manifest={"version":1,"files":[],"monthly_candidates":{}}
    with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as z:
        for kind,raw in source:
            sha=hashlib.sha256(raw).hexdigest()
            member="originals/"+sha+".xlsx"
            z.writestr(member,raw)
            manifest["files"].append({"sha256":sha,"path":member,"size_bytes":len(raw),
                "type":kind,"observed_from":observed,"observed_to":observed,
                "original_names":[{"archive":"synthetic","filename":kind+".xlsx"}],
                "selection":"monthly_candidate"})
            manifest["monthly_candidates"][kind+"/"+month]=sha
        manifest.update(source_count=2,unique_source_count=2,duplicate_occurrences=0,
                        raw_unique_bytes=sum(len(x[1]) for x in source))
        z.writestr("manifest.json",json.dumps(manifest))
    return bundle.read_bundle(path,hashlib.sha256(path.read_bytes()).hexdigest())


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.path=Path(t.name)/"auth.db"
        self.conn=sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("CREATE TABLE report_data(date TEXT PRIMARY KEY,payload TEXT)")
        self.conn.execute("INSERT INTO report_data VALUES('2026-01-05','unchanged')")
        self.conn.execute("CREATE TABLE daily_uploads(data_date TEXT PRIMARY KEY,services_sha256 TEXT,normalized_json TEXT)")
        self.conn.commit()
        self.addCleanup(self.conn.close)
        self.pack=make_pack(Path(t.name)/"sources.zip")

    def test_matched_pair_not_full_paid_or_calendar_month(self):
        item=recovery.build(self.pack)["periods"][0]
        self.assertEqual(item["knownPaid"],400)
        self.assertIsNone(item["paid"])
        self.assertEqual(item["clinicUnallocatedPaid"],50)
        self.assertEqual(item["from"],"2026-01-05")
        self.assertEqual(item["to"],"2026-01-05")
        self.assertEqual(item["days"],[{"date":"2026-01-05","services":600,"orders":1}])
        self.assertEqual(item["rowCount"],2)
        self.assertNotIn("Аппарат",json.dumps(item,ensure_ascii=False))
        self.assertNotIn("Сотрудник",json.dumps(item,ensure_ascii=False))

    def test_wrong_layout_and_mismatched_pairs_stop_before_archive(self):
        before=list(self.conn.iterdump())
        for i,kwargs in enumerate(({"paid":workbook(True,amount=601)},{"paid":workbook(True,alt=True)})):
            with self.subTest(i=i):
                pack=make_pack(self.path.parent/f"bad{i}.zip",**kwargs)
                with self.assertRaises(ValueError):
                    recovery.activate(self.conn,pack,self.path)
                self.assertEqual(list(self.conn.iterdump()),before)

    def test_apply_originals_then_exact_projection_and_noop_replay(self):
        first,created=recovery.activate(self.conn,self.pack,self.path)
        self.assertTrue(created)
        before=list(self.conn.iterdump())
        self.assertEqual(recovery.activate(self.conn,self.pack,self.path),(first,False))
        self.assertEqual(list(self.conn.iterdump()),before)
        self.assertEqual(self.conn.execute("SELECT payload FROM report_data").fetchone()[0],"unchanged")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM mis_source_objects").fetchone()[0],3)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM daily_uploads").fetchone()[0],0)
        self.conn.execute("PRAGMA query_only=ON")
        paid,days=recovery.project(self.conn,[])
        self.assertEqual(paid,[{"from":"2026-01-05","to":"2026-01-05","paid":None,"knownPaid":400,"recovered":True,"reason":"archived_matched_detail"}])
        self.assertEqual(days,[{"date":"2026-01-05","services":600,"orders":1}])
        self.assertEqual(list(self.conn.iterdump()),before)

    def test_live_change_invalidates_not_reactivates_on_replay(self):
        recovery.activate(self.conn,self.pack,self.path)
        self.conn.execute("INSERT INTO daily_uploads VALUES(?,?,?)",
                          ("2026-01-05","new","{}"))
        self.conn.commit()
        self.assertEqual(recovery.project(self.conn,[])[0],[])
        recovery.activate(self.conn,self.pack,self.path)
        self.assertEqual(recovery.project(self.conn,[])[0],[])

    def test_live_conflicting_or_removed_service_not_overwritten(self):
        self.conn.execute("INSERT INTO daily_uploads VALUES(?,?,?)",
                          ("2026-01-05","old","{}"))
        self.conn.commit()
        with self.assertRaisesRegex(ValueError,"conflicts with live services"):
            recovery.activate(self.conn,self.pack,self.path)
        self.assertEqual(recovery.project(self.conn,[])[0],[])
        self.assertFalse(recovery.exists(self.conn,recovery.TABLE))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM mis_source_objects").fetchone()[0],3)

    def test_new_overlapping_paid_source_invalidates_old_witness(self):
        recovery.activate(self.conn,self.pack,self.path)
        source={"kind":"period","from":"2026-01-01","to":"2026-01-10",
                "sourceFrom":"2026-01-01","sourceTo":"2026-01-10",
                "sourceSha256":"a"*64,"payloadSha256":"b"*64,"importedAt":"now","payload":{}}
        paid,_=recovery.project(self.conn,[source])
        self.assertFalse(any(x.get("recovered") for x in paid))

    def test_missing_archive_receipt_blocks_projection(self):
        recovery.activate(self.conn,self.pack,self.path)
        self.conn.execute("DELETE FROM mis_source_events WHERE source_kind='services'")
        self.conn.commit()
        self.assertEqual(recovery.project(self.conn,[])[0],[])

    def test_paid_subtotals_conflicting_even_when_row_balances(self):
        import subprocess
        import sys
        before=list(self.conn.iterdump())
        # Every edited summary still satisfies its own balance, so the
        # actual hierarchy comparison must reject it.
        for name,row_number,paid_value in (
            ("provider",3,0),("invoice",4,0),("services",2,0),
            ("unexplained_positive",2,800),
        ):
            with self.subTest(name=name):
                def edit(ws):
                    r=row_number
                    ws.cell(r,7,paid_value)
                    ws.cell(r,8,ws.cell(r,5).value+ws.cell(r,6).value-paid_value)
                raw=workbook(True,edit=edit)
                path=self.path.parent/(name+".zip")
                pack=make_pack(path,paid=raw)
                with self.assertRaises(ValueError):
                    recovery.build(pack)
                with self.assertRaises(ValueError):
                    recovery.activate(self.conn,pack,self.path)
                self.assertEqual(list(self.conn.iterdump()),before)
                sha=hashlib.sha256(path.read_bytes()).hexdigest()
                for mode in ("--verify","--apply"):
                    result=subprocess.run([sys.executable,str(Path(recovery.__file__)),
                        "--bundle",str(path),"--sha256",sha,mode,"--database",str(self.path)],
                        capture_output=True,text=True)
                    self.assertNotEqual(result.returncode,0)
                    self.assertIn("ZTL_RECOVERY_STOP=ValueError",result.stderr)
                    self.assertEqual(list(self.conn.iterdump()),before)

    def test_explicit_old_debt_adjustment_and_repeated_subtotal(self):
        def repeated(ws):
            ws.append(["Неоплаченные услуги на начало периода",None,None,None,40,"-",20,20])
            ws.append(["Неоплаченные услуги на начало периода",None,None,None,60,"-",30,30])
        data=parser.pair(workbook(),workbook(True,edit=repeated),lab.is_lab_group)
        self.assertEqual(data["knownPaid"],400)
        self.assertEqual(data["clinicUnallocatedPaid"],50)
        self.assertIsNone(data["paid"])
        def wrong_child(ws):
            repeated(ws)
            ws.cell(8,7,25)
            ws.cell(8,8,15)
        with self.assertRaisesRegex(ValueError,"opening debt"):
            parser.pair(workbook(),workbook(True,edit=wrong_child),lab.is_lab_group)
        data=parser.pair(workbook(),workbook(True,old_paid=-50),lab.is_lab_group)
        self.assertEqual(data["knownPaid"],400)
        self.assertEqual(data["clinicUnallocatedPaid"],-50)
        self.assertIsNone(data["paid"])
        data=parser.pair(workbook(),workbook(True,paid_amount=-20),lab.is_lab_group)
        self.assertEqual(data["knownPaid"],-20)

    def test_styled_provider_patient_invoice_and_grand_totals(self):
        from openpyxl.styles import Font
        def styled(ws,conflict=None):
            # Reproduce standard IDENT heading levels without real names.
            wb=ws.parent
            styles={}
            for i in range(1,16):
                c=ws.cell(100+i,1)
                c.font=Font(name="Synthetic"+str(i))
                self.assertEqual(c.style_id,i)
                styles[i]=c._style
            ws.delete_rows(101,15)
            ws.insert_rows(4)
            for col,val in enumerate(["Пациент тест",None,None,None,100,800,650,250],1):
                ws.cell(4,col,val)
            for i,style in enumerate((15,14,13,13,15,15,15,15),1):
                ws.cell(1,i)._style=styles[style]
            ws.cell(3,1)._style=styles[6]
            ws.cell(4,1)._style=styles[3]
            if conflict=="patient":
                ws.cell(4,7,0)
                ws.cell(4,8,900)
        correct=workbook(True,edit=styled)
        self.assertEqual(parser.pair(workbook(),correct,lab.is_lab_group)["knownPaid"],400)
        with self.assertRaisesRegex(ValueError,"patient"):
            parser.pair(workbook(),workbook(True,edit=lambda ws:styled(ws,"patient")),lab.is_lab_group)
        def grand(ws,bad=False):
            ws.insert_rows(2,2)
            for i,v in enumerate(["Итого",None,None,None,50,800,670 if bad else 660,180 if bad else 190],1):
                ws.cell(2,i,v)
            for i,v in enumerate(["Авансы",None,None,None,-50,"-",10,-60],1):
                ws.cell(3,i,v)
        self.assertEqual(parser.pair(workbook(),workbook(True,edit=grand),lab.is_lab_group)["knownPaid"],400)
        with self.assertRaisesRegex(ValueError,"grand total"):
            parser.pair(workbook(),workbook(True,edit=lambda ws:grand(ws,True)),lab.is_lab_group)

    def test_partial_selections_preserve_months_and_replace_only_overlap(self):
        jan_id,_=recovery.activate(self.conn,self.pack,self.path)
        nov=make_pack(self.path.parent/"nov.zip",month="2026-11")
        nov_id,_=recovery.activate(self.conn,nov,self.path)
        paid,days=recovery.project(self.conn,[])
        self.assertEqual([(x["from"],x["knownPaid"]) for x in paid],[("2026-01-05",400),("2026-11-05",400)])
        self.assertEqual([x["date"] for x in days],["2026-01-05","2026-11-05"])
        corrected=make_pack(self.path.parent/"nov2.zip",month="2026-11",
                           paid=workbook(True,month="2026-11",paid_amount=350))
        corrected_id,_=recovery.activate(self.conn,corrected,self.path)
        self.assertGreater(corrected_id,nov_id)
        before=list(self.conn.iterdump())
        self.assertEqual(recovery.activate(self.conn,self.pack,self.path),(jan_id,False))
        self.assertEqual(recovery.activate(self.conn,nov,self.path),(nov_id,False))
        self.assertEqual(list(self.conn.iterdump()),before)
        self.conn.execute("PRAGMA query_only=ON")
        paid,days=recovery.project(self.conn,[])
        self.assertEqual([(x["from"],x["knownPaid"]) for x in paid],[("2026-01-05",400),("2026-11-05",350)])
        self.assertEqual(len(days),2)
        self.assertEqual(list(self.conn.iterdump()),before)

    def test_conflicting_or_shrinking_new_selection_preserves_prior_months(self):
        recovery.activate(self.conn,self.pack,self.path)
        nov=make_pack(self.path.parent/"nov.zip",month="2026-11")
        recovery.activate(self.conn,nov,self.path)
        prior_rows=list(self.conn.execute("SELECT * FROM "+recovery.TABLE))
        prior=recovery.project(self.conn,[])
        shrunk=make_pack(self.path.parent/"shifted.zip",month="2026-11",day="06")
        with self.assertRaisesRegex(ValueError,"remove observed days"):
            recovery.activate(self.conn,shrunk,self.path)
        self.assertEqual(list(self.conn.execute("SELECT * FROM "+recovery.TABLE)),prior_rows)
        self.assertEqual(recovery.project(self.conn,[]),prior)
        # A conflicting new November selection must not replace either month.
        normalized={"data_date":"2026-11-05","items":[{
            "date":"2026-11-05","group":LAB,"amount":600,"invoice":"1"}],"retail_items":[]}
        self.conn.execute("INSERT INTO daily_uploads VALUES(?,?,?)",
                          ("2026-11-05","live",json.dumps(normalized)))
        self.conn.commit()
        # Re-establish a valid explicit selection for the current live baseline.
        compatible=make_pack(self.path.parent/"nov-compatible.zip",month="2026-11",
                             paid=workbook(True,month="2026-11",paid_amount=390))
        recovery.activate(self.conn,compatible,self.path)
        prior=recovery.project(self.conn,[])
        prior_rows=list(self.conn.execute("SELECT * FROM "+recovery.TABLE))
        conflict=make_pack(self.path.parent/"nov-conflict.zip",month="2026-11",
                           service=workbook(month="2026-11",amount=700),
                           paid=workbook(True,month="2026-11",amount=700))
        with self.assertRaisesRegex(ValueError,"conflicts with live services"):
            recovery.activate(self.conn,conflict,self.path)
        self.assertEqual(list(self.conn.execute("SELECT * FROM "+recovery.TABLE)),prior_rows)
        self.assertEqual(recovery.project(self.conn,[]),prior)
        self.assertEqual([(x["from"],x["knownPaid"]) for x in prior[0]],[("2026-01-05",400),("2026-11-05",390)])

    def test_stale_latest_month_never_resurrects_old_overlap(self):
        recovery.activate(self.conn,self.pack,self.path)
        nov=make_pack(self.path.parent/"nov.zip",month="2026-11")
        recovery.activate(self.conn,nov,self.path)
        corrected=make_pack(self.path.parent/"nov2.zip",month="2026-11",
                            paid=workbook(True,month="2026-11",paid_amount=350))
        recovery.activate(self.conn,corrected,self.path)
        # The older Nov run still exists; invalidating only the newest receipt
        # must not fall back to that superseded Nov, or hide Jan.
        sha=recovery.build(corrected)["periods"][0]["paidSha256"]
        self.conn.execute("DELETE FROM mis_source_events WHERE sha256=?",(sha,))
        self.conn.commit()
        paid,days=recovery.project(self.conn,[])
        self.assertEqual([(x["from"],x["knownPaid"]) for x in paid],[("2026-01-05",400)])
        self.assertEqual([x["date"] for x in days],["2026-01-05"])

    def test_existing_primary_paid_lines_require_exact_active_identity(self):
        self.conn.executescript("""
            CREATE TABLE advertising_mis_paid_ingests(period_start TEXT,period_end TEXT,
                source_sha256 TEXT,imported_at TEXT,paid_total REAL,detailed_paid_total REAL,line_count INTEGER);
            CREATE TABLE advertising_mis_paid_lines(period_start TEXT,period_end TEXT,staff TEXT,
                group_name TEXT,service_date TEXT,billed_amount REAL,paid_amount REAL,
                source_sha256 TEXT,imported_at TEXT);
        """)
        start,end="2026-01-01","2026-01-31"
        sha="a"*64
        source={"from":start,"to":end,"sourceFrom":start,"sourceTo":end,
                "sourceSha256":sha,"importedAt":"stamp","payload":{
                    "providers":{"A":{"paid":650}},"totals":{"paid":650,"billed":800},
                    "provider_residual":{"paid":0}}}
        self.conn.execute("INSERT INTO advertising_mis_paid_ingests VALUES(?,?,?,?,?,?,?)",
                          (start,end,sha,"stamp",650,600,2))
        self.conn.executemany("INSERT INTO advertising_mis_paid_lines VALUES(?,?,?,?,?,?,?,?,?)",[
            (start,end,"A",LAB,"2026-01-05",600,400,sha,"stamp"),
            (start,end,"A",OTHER,"2026-01-05",200,200,sha,"stamp"),
        ])
        self.conn.commit()
        before=list(self.conn.iterdump())
        self.conn.execute("PRAGMA query_only=ON")
        self.assertEqual(lab.paid_periods([source],conn=self.conn)[0]["knownPaid"],400)
        self.assertIsNone(lab.paid_periods([source],conn=self.conn)[0]["paid"])
        for change in ({"sourceSha256":"b"*64},{"importedAt":"other"},{"sourceFrom":"2026-01-02"}):
            self.assertNotIn("knownPaid",lab.paid_periods([{**source,**change}],conn=self.conn)[0])
        self.assertEqual(list(self.conn.iterdump()),before)
        self.conn.execute("PRAGMA query_only=OFF")
        self.conn.execute("UPDATE advertising_mis_paid_lines SET source_sha256=?",("b"*64,))
        self.conn.commit()
        self.assertNotIn("knownPaid",lab.paid_periods([source],conn=self.conn)[0])

    def test_compact_known_component_retained_and_full_not_changed(self):
        key=lab.EVIDENCE_KEY
        source={"from":"2026-01-01","to":"2026-01-31","payload":{
            key:{"version":1,"paid":None,"knownPaid":400,"reason":"unallocated_payment"}}}
        self.assertEqual(lab.paid_periods([source]),[
            {"from":"2026-01-01","to":"2026-01-31","paid":None,"knownPaid":400,"reason":"unallocated_payment"}])
        source["payload"][key]["knownPaid"]=0
        self.assertEqual(lab.paid_periods([source])[0]["knownPaid"],0)
        source["payload"][key]["knownPaid"]="bad"
        self.assertNotIn("knownPaid",lab.paid_periods([source])[0])


if __name__=="__main__":
    unittest.main()
