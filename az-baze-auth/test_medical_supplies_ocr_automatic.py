"""Automatic proposals through real authorized routes; no private source files."""
import copy
import json
import unittest
from unittest.mock import patch

from flask import template_rendered

import medical_supplies_ocr as ocr
import test_medical_supplies_ocr as fixtures
from test_medical_supplies_ocr_table import simple_table


class AutomaticRouteTests(unittest.TestCase):
    setUp=fixtures.OCRRouteTests.setUp
    client=fixtures.OCRRouteTests.client
    upload=fixtures.OCRRouteTests.upload
    get_doc=fixtures.OCRRouteTests.get_doc
    initial=fixtures.OCRRouteTests.initial
    state=fixtures.OCRRouteTests.state
    snapshot_stock=fixtures.OCRRouteTests.snapshot_stock

    def page(self,version=1,**extra):
        return self.client(2).post(fixtures.PREFIX+"/page",data={
            "csrf":"token","page":"1","version":str(version),**extra})

    def automatic(self):
        parsed=ocr.parse_tsv(simple_table(qualifier="с НДС"))
        parsed["recognition_version"]=ocr.RECOGNITION_VERSION
        parsed["printed_total"]="289.20"
        parsed["total_basis"]="printed_gross"
        parsed["header_candidate"]={"document_type":"act","counterparty":"ООО Тестовый поставщик",
            "number":"ACT-61","date":"2026-10-11","part":1,"note":"","issues":[],
            "pagination":{"page":1,"of":1}}
        return parsed

    def save_state(self,previous):
        state={"page_count":1,"pages":{"1":previous}}
        self.conn.execute("UPDATE med_supply_documents SET fields_json=?,version=2 WHERE id=1",
            (json.dumps({ocr.KEY:state},ensure_ascii=False),))
        self.conn.commit()

    def test_new_image_proposals_and_header_are_saved_without_posting(self):
        stock=self.initial()
        original=bytes(self.get_doc()["original_bytes"])
        parsed=self.automatic()
        with patch.object(ocr,"recognize_pdf_page",return_value=(1,parsed)):
            result=self.page()
        self.assertEqual(result.status_code,200,result.get_data(as_text=True))
        saved=self.state()["pages"]["1"]
        self.assertEqual(saved["header_candidate"]["number"],"ACT-61")
        self.assertEqual(saved["candidates"][0]["value"],"289.20")
        self.assertNotIn("rows",saved)
        self.assertNotIn("checked",saved)
        self.assertEqual(self.get_doc()["status"],"review")
        self.assertEqual(self.snapshot_stock(),stock)
        self.assertEqual(bytes(self.get_doc()["original_bytes"]),original)
        observed=[]
        def capture(sender,template,context,**kwargs):
            if template.name=="medical_supplies_ocr.html": observed.append(context)
        before=list(self.conn.iterdump())
        with template_rendered.connected_to(capture,self.app), patch.object(ocr,"recognize_pdf_page") as engine:
            response=self.client(2).get(fixtures.PREFIX)
        self.assertEqual(response.status_code,200)
        engine.assert_not_called()
        self.assertEqual(observed[0]["header"]["number"],"ACT-61")
        self.assertTrue(observed[0]["quality"]["total_matches"])
        self.assertEqual(list(self.conn.iterdump()),before)

    def test_explicit_old_image_reread_keeps_first_raw_evidence(self):
        self.initial()
        old=fixtures.data_fixture()
        old["extractor_version"]=5
        self.save_state(old)
        stock=self.snapshot_stock()
        original=bytes(self.get_doc()["original_bytes"])
        with patch.object(ocr,"recognize_pdf_page",return_value=(1,self.automatic())) as engine:
            response=self.page(2,reread="1")
            self.assertEqual(response.status_code,200,response.get_data(as_text=True))
            self.assertEqual(self.page(3,reread="1").json["cached"],True)
            self.assertEqual(engine.call_count,1)
        saved=self.state()["pages"]["1"]
        self.assertEqual(saved["previous_ocr"],old)
        self.assertEqual(saved["candidates"][0]["value"],"289.20")
        self.assertEqual(self.snapshot_stock(),stock)
        self.assertEqual(bytes(self.get_doc()["original_bytes"]),original)

    def test_image_reread_never_replaces_manual_rows_headers_or_line_map(self):
        for manual_rows in ([],[{**fixtures.data_fixture()["candidates"][0],
                               "name":"Ручное название","quantity":"17","value":"91.01"}]):
            with self.subTest(empty=not manual_rows):
                if not self.get_doc(): self.initial()
                old=fixtures.data_fixture()
                old.update(extractor_version=5,rows=manual_rows,header={},checked=False,no_items=not manual_rows)
                self.save_state(old)
                with patch.object(ocr,"recognize_pdf_page",return_value=(1,self.automatic())):
                    response=self.page(2,reread="1")
                self.assertEqual(response.status_code,200,response.get_data(as_text=True))
                saved=self.state()["pages"]["1"]
                self.assertEqual({key:value for key,value in saved.items() if key!="automatic"},old)
                self.assertEqual(saved["automatic"]["candidates"][0]["quantity"],"2")
                observed=[]
                def capture(sender,template,context,**kwargs):
                    if template.name=="medical_supplies_ocr.html": observed.append(context)
                with template_rendered.connected_to(capture,self.app):
                    response=self.client(2).get(fixtures.PREFIX)
                self.assertEqual(response.status_code,200)
                self.assertEqual(observed[0]["header"],{})
                self.assertEqual([r["name"] for r in observed[0]["rows"]],[r["name"] for r in manual_rows])

    def test_failed_or_stale_reread_leaves_document_and_events_unchanged(self):
        self.initial()
        old=fixtures.data_fixture(); old["extractor_version"]=5
        self.save_state(old)
        before=list(self.conn.iterdump())
        with patch.object(ocr,"recognize_pdf_page",side_effect=ocr.OCRUnavailable("Время истекло")) as engine:
            self.assertEqual(self.page(1,reread="1").status_code,409)
            engine.assert_not_called()
            self.assertEqual(self.page(2,reread="1").status_code,503)
        self.assertEqual(list(self.conn.iterdump()),before)

    def test_partial_ocr_failure_can_retry_without_losing_saved_work(self):
        self.initial()
        old=fixtures.data_fixture()
        old.update(extractor_version=5,rows=[],header={},checked=False)
        self.save_state(old)
        stock=self.snapshot_stock()
        original=bytes(self.get_doc()["original_bytes"])
        partial=self.automatic()
        partial.update(recognition_retryable=True,
                       recognition_issues=["Повторный разбор ячеек не завершён."])
        with patch.object(ocr,"recognize_pdf_page",side_effect=[(1,partial),(1,self.automatic())]) as engine:
            first=self.page(2,reread="1")
            self.assertEqual(first.status_code,200)
            self.assertTrue(self.state()["pages"]["1"]["automatic"]["recognition_retryable"])
            second=self.page(3,reread="1")
            self.assertEqual(second.status_code,200,second.get_data(as_text=True))
            self.assertFalse(second.json["cached"])
            third=self.page(4,reread="1")
            self.assertEqual(third.status_code,200)
            self.assertTrue(third.json["cached"])
            self.assertEqual(engine.call_count,2)
        saved=self.state()["pages"]["1"]
        self.assertEqual({key:value for key,value in saved.items() if key!="automatic"},old)
        self.assertFalse(saved["automatic"].get("recognition_retryable"))
        self.assertEqual(self.snapshot_stock(),stock)
        self.assertEqual(bytes(self.get_doc()["original_bytes"]),original)

    def test_new_proposed_parts_match_identity_and_respect_other_parts(self):
        header=self.automatic()["header_candidate"]
        state={"pages":{"1":{"header":{**header,"part":4}}}}
        proposed=ocr.proposed_header({"header_candidate":{**header,"part":2}},2,state)
        self.assertEqual(proposed["part"],4)
        changed=ocr.proposed_header({"header_candidate":{**header,"number":"ACT-62","part":4}},2,state)
        self.assertNotEqual(changed["part"],4)
        self.assertEqual(changed["number"],"ACT-62")
        manual={"header":{},"header_candidate":header}
        self.assertEqual(ocr.proposed_header(manual,2,state),{})

    def test_saved_header_keeps_source_pagination_for_completeness_check(self):
        self.initial()
        first=self.automatic()
        first["header_candidate"]["pagination"]={"page":1,"of":2}
        first["header"]={k:v for k,v in first["header_candidate"].items()
                         if k not in ("pagination","issues","evidence")}
        first["rows"]=copy.deepcopy(first["candidates"])
        second=copy.deepcopy(first)
        second["header_candidate"]["pagination"]={"page":2,"of":2}
        state={"page_count":2,"pages":{"1":first,"2":second}}
        self.conn.execute("UPDATE med_supply_documents SET fields_json=?,version=2 WHERE id=1",
            (json.dumps({ocr.KEY:state},ensure_ascii=False),))
        self.conn.commit()
        before=list(self.conn.iterdump())
        for page in (1,2):
            response=self.client(2).get(fixtures.PREFIX+"?p="+str(page))
            self.assertEqual(response.status_code,200)
            self.assertNotIn("не найдены страницы",response.get_data(as_text=True))
        self.assertEqual(list(self.conn.iterdump()),before)

    def test_confirmed_history_read_excludes_current_draft_and_invalid_json(self):
        self.initial()
        old=self.automatic(); old.update(rows=[{**old["candidates"][0],"folder_id":99}],checked=True)
        self.save_state(old)
        self.assertEqual(ocr.classification_history(self.conn,1),[])
        self.upload(raw=fixtures.make_pdf(),filename="second.pdf")
        historical=ocr.classification_history(self.conn,2)
        self.assertEqual(len(historical),1)
        self.assertEqual(historical[0]["rows"][0]["folder_id"],99)
        old["checked"]=False
        self.save_state(old)
        self.assertEqual(ocr.classification_history(self.conn,2),[])
        self.conn.execute("UPDATE med_supply_documents SET fields_json='not json' WHERE id=1")
        self.conn.commit()
        self.assertEqual(ocr.classification_history(self.conn,2),[])


if __name__=="__main__":
    unittest.main()
