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


def workbook(paid=False,amount=600,paid_amount=400,alt=False):
    wb=Workbook()
    ws=wb.active
    header=["Группа услуг","Услуги",None,None]+(
        ["Задолженность на нач. периода","Сумма со скидкой","Оплачено","Задолженность на конец периода"] if paid
        else ["Сумма по прейскуранту","Скидка","Сумма со скидкой"])
    if alt:header[1]="Врач / Исполнитель"
    ws.append(header)
    if paid:
        ws.append(["Услуги",None,None,None,100,amount+200,paid_amount+250,amount+50-paid_amount])
        ws.append(["Сотрудник А.",None,None,None,100,amount+200,paid_amount+250,amount+50-paid_amount])
        ws.append(["Счет №1 от 05.01.2026 9:00:00",None,None,None,0,amount+200,paid_amount+200,amount-paid_amount])
        ws.append([LAB,"05.01.2026 9:00","Аппарат",1,0,amount,paid_amount,amount-paid_amount])
        ws.append([OTHER,None,"Лечение",1,0,200,200,0])
        ws.append(["Неоплаченные услуги на начало периода",None,None,None,100,0,50,50])
    else:
        ws.append(["Итого",None,None,None,amount+200,0,amount+200])
        ws.append(["Сотрудник А.",None,None,None,amount+200,0,amount+200])
        ws.append(["Счет №1 от 05.01.2026 9:00:00",None,None,None,amount+200,0,amount+200])
        ws.append([LAB,"05.01.2026 9:00","Аппарат",1,amount,0,amount])
        ws.append([OTHER,None,"Лечение",1,200,0,200])
    out=io.BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()


def make_pack(path,service=None,paid=None):
    import zipfile
    source=[("service_prices_discounts",service or workbook()),("paid_and_debt",paid or workbook(True))]
    manifest={"version":1,"files":[],"monthly_candidates":{}}
    with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as z:
        for kind,raw in source:
            sha=hashlib.sha256(raw).hexdigest()
            member="originals/"+sha+".xlsx"
            z.writestr(member,raw)
            manifest["files"].append({"sha256":sha,"path":member,"size_bytes":len(raw),
                "type":kind,"observed_from":"2026-01-05","observed_to":"2026-01-05",
                "original_names":[{"archive":"synthetic","filename":kind+".xlsx"}],
                "selection":"monthly_candidate"})
            manifest["monthly_candidates"][kind+"/2026-01"]=sha
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
        recovery.activate(self.conn,self.pack,self.path)
        self.assertEqual(recovery.project(self.conn,[])[0],[])

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
