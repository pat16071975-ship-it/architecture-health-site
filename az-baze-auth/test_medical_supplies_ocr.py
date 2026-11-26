"""Synthetic local OCR and real Flask/SQLite draft routes. No private PDF."""
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import template_rendered

import test_medical_supplies_documents as fixtures
import medical_supplies_ocr as ocr
from pypdf import PdfWriter
from pypdf.generic import (
    DictionaryObject, NameObject, DecodedStreamObject,
)


PREFIX="/accounting/medical-supplies/incoming/1/ocr"


def tsv_fixture():
    header="level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows=[]
    for y,words in [
        (100,[(80,"Наименование"),(460,"Ед."),(620,"Количество"),(850,"Цена"),(1040,"Сумма"),(1260,"Сумма")]),
        (160,[(100,"Салфетки"),(465,"упак"),(645,"2"),(865,"120,50"),(1060,"241,00"),(1265,"289,20")]),
        (220,[(100,"Итого"),(1060,"241,00"),(1265,"289,20")]),
    ]:
        for i,(x,text) in enumerate(words,1):
            rows.append("\t".join(map(str,[5,1,1,1,1,i,x,y,max(20,len(text)*7),22,94,text])))
    return header+"\n"+"\n".join(rows)+"\n"


def data_fixture():
    return ocr.parse_tsv(tsv_fixture())


def make_pdf():
    writer=PdfWriter()
    page=writer.add_blank_page(width=595,height=842)
    font=DictionaryObject({
        NameObject("/Type"):NameObject("/Font"),
        NameObject("/Subtype"):NameObject("/Type1"),
        NameObject("/BaseFont"):NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")]=DictionaryObject({
        NameObject("/Font"):DictionaryObject({NameObject("/F1"):font})
    })
    stream=DecodedStreamObject()
    stream.set_data(b"BT /F1 18 Tf 50 750 Td (TOTAL QUANTITY PRICE) Tj ET")
    page[NameObject("/Contents")]=writer._add_object(stream)
    out=io.BytesIO()
    writer.write(out)
    return out.getvalue()


class OCRParserTests(unittest.TestCase):
    def test_table_candidates_keep_ambiguous_gross_value_unknown(self):
        parsed=data_fixture()
        self.assertEqual(len(parsed["candidates"]),1)
        r=parsed["candidates"][0]
        self.assertEqual(r["name"],"Салфетки")
        self.assertEqual(r["unit"],"упак")
        self.assertEqual(r["quantity"],"2")
        self.assertEqual(r["price"],"120.50")
        self.assertIsNone(r["value"])
        self.assertEqual(r["action"],"unresolved")
        self.assertEqual(r["source_line"],2)
        self.assertEqual(len(parsed["lines"]),3)
        self.assertEqual(parsed["confidence"],94.0)

    def test_missing_header_does_not_invent_rows(self):
        raw=tsv_fixture()
        raw=raw.replace("Наименование","Текст")
        self.assertEqual(ocr.parse_tsv(raw)["candidates"],[])

    def test_number_unknown_and_no_ambiguous_join(self):
        self.assertIsNone(ocr.number("",100))
        self.assertEqual(ocr.number("1 234,50",100),"1234.50")
        for value in ("1 2","1,2,3","-1","nan","0.001"):
            with self.assertRaises(ValueError):
                ocr.number(value,100)

    def test_real_local_tools_on_synthetic_pdf(self):
        if os.environ.get("AZ_TEST_REAL_OCR")!="1":
            self.skipTest("Real OCR is required in the dedicated CI step.")
        with tempfile.TemporaryDirectory() as tmp:
            count,parsed=ocr.recognize_pdf_page(make_pdf(),1,Path(tmp))
            self.assertEqual(count,1)
            self.assertIn("total"," ".join(x["text"] for x in parsed["lines"]).casefold())
            self.assertTrue(parsed["words"])


class OCRRouteTests(unittest.TestCase):
    setUp=fixtures.IncomingDocumentTests.setUp
    client=fixtures.IncomingDocumentTests.client
    upload=fixtures.IncomingDocumentTests.upload
    get_doc=fixtures.IncomingDocumentTests.get_doc

    def snapshot_stock(self):
        names=[r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'med_supply_%' "
            "AND name NOT IN ('med_supply_documents','med_supply_document_events') ORDER BY name")]
        return {name:[tuple(r) for r in self.conn.execute('SELECT * FROM "'+name+'"')] for name in names}

    def initial(self):
        self.assertEqual(self.upload().status_code,303)
        return self.snapshot_stock()

    def recognize(self,version=1,page=1,uid=2):
        return self.client(uid).post(PREFIX+"/page",data={"csrf":"token","version":str(version),"page":str(page)})

    def state(self):
        return json.loads(self.get_doc()["fields_json"])[ocr.KEY]

    def draft(self,version=2,page=1,**overrides):
        values={"csrf":"token","version":str(version),"page":str(page),"part":"1",
            "document_type":"bill","counterparty":"Синтетический поставщик","number":"A-1",
            "document_date":"2026-11-20","source_line":"2","name":"Салфетки","unit":"упак",
            "quantity":"2","price":"120.50","value":"289.20","action":"skip","item_id":"",
            "folder_id":"","note":"Не подтверждена поставка","page_note":"","checked":"1"}
        values.update(overrides)
        return self.client(2).post(PREFIX+"/save",data=values)

    def test_real_routes_page_cache_review_original_and_stock(self):
        before=self.initial()
        original=bytes(self.get_doc()["original_bytes"])
        with patch.object(ocr,"recognize_pdf_page",return_value=(2,data_fixture())) as engine:
            self.assertEqual(self.client(2).get(PREFIX).status_code,200)
            engine.assert_not_called()
            r=self.recognize()
            self.assertEqual(r.status_code,200,r.get_data(as_text=True))
            self.assertEqual(r.json["version"],2)
            self.assertEqual(self.get_doc()["status"],"review")
            r=self.recognize(2)
            self.assertTrue(r.json["cached"])
            self.assertEqual(engine.call_count,1)
            self.assertEqual(self.recognize(1,2).status_code,409)
            self.assertEqual(self.draft().status_code,303)
            self.assertEqual(self.get_doc()["status"],"review")
            self.assertEqual(self.recognize(3,2).status_code,200)
            self.assertEqual(self.draft(4,2).status_code,303)
        self.assertEqual(self.get_doc()["status"],"reviewed")
        self.assertEqual(bytes(self.get_doc()["original_bytes"]),original)
        state=self.state()
        self.assertEqual(state["pages"]["1"]["rows"][0]["value"],"289.20")
        self.assertIsNone(state["pages"]["1"]["candidates"][0]["value"])
        self.assertEqual(state["pages"]["1"]["lines"][1]["id"],2)
        self.assertEqual(self.snapshot_stock(),before)
        view=self.client(3).get(PREFIX)
        self.assertEqual(view.status_code,200)
        self.assertEqual(view.headers["Cache-Control"],"private, no-store")
        self.assertIn("Только просмотр",view.get_data(as_text=True))
        self.assertNotIn('id="ocr-start"',view.get_data(as_text=True))

    def test_default_page_opens_saved_goods_after_cover_but_respects_explicit_selection(self):
        self.initial()
        tsv_header=tsv_fixture().splitlines()[0]
        contract=ocr.parse_tsv(tsv_header+"\n"+
            "5\t1\t1\t1\t1\t1\t100\t100\t120\t22\t94\tДоговор\n")
        goods=data_fixture()
        for page in (contract,goods):
            page.pop("extractor_version",None)
        state={"page_count":2,"pages":{"1":contract,"2":goods}}
        self.conn.execute("UPDATE med_supply_documents SET fields_json=?,version=2 WHERE id=1",
                          (json.dumps({ocr.KEY:state},ensure_ascii=False),))
        self.conn.commit()
        before=list(self.conn.iterdump())
        shown=[]
        def capture(sender,template,context,**extra):
            if template.name=="medical_supplies_ocr.html":
                shown.append(context["page"])
        with template_rendered.connected_to(capture,self.app), \
                patch.object(ocr,"recognize_pdf_page",side_effect=AssertionError("GET must not OCR")):
            self.assertEqual(self.client(2).get(PREFIX).status_code,200)
            self.assertEqual(self.client(2).get(PREFIX+"?p=1").status_code,200)
        self.assertEqual(shown,[2,1])
        self.assertEqual(list(self.conn.iterdump()),before)

        # An intentionally cleared goods draft must not reappear or cause an
        # automatic jump merely because the original OCR has candidates.
        state["pages"]["2"].update(rows=[],no_items=True,checked=True)
        self.conn.execute("UPDATE med_supply_documents SET fields_json=? WHERE id=1",
                          (json.dumps({ocr.KEY:state},ensure_ascii=False),))
        self.conn.commit()
        shown.clear()
        before=list(self.conn.iterdump())
        with template_rendered.connected_to(capture,self.app):
            self.assertEqual(self.client(2).get(PREFIX).status_code,200)
        self.assertEqual(shown,[1])
        self.assertEqual(list(self.conn.iterdump()),before)

    def test_permission_csrf_and_failed_engine_do_not_write(self):
        before=self.initial()
        with patch.object(ocr,"recognize_pdf_page",side_effect=ocr.OCRUnavailable("Ошибка OCR")) as engine:
            self.assertEqual(self.recognize(uid=3).status_code,403)
            self.assertEqual(self.recognize(uid=4).status_code,403)
            self.assertEqual(self.client(2).post(PREFIX+"/page",data={"version":"1","page":"1"}).status_code,400)
            engine.assert_not_called()
            self.assertEqual(self.recognize().status_code,503)
            self.assertEqual(engine.call_count,1)
        self.assertEqual(self.get_doc()["version"],1)
        self.assertEqual(self.get_doc()["fields_json"],"{}")
        self.assertEqual(self.snapshot_stock(),before)

    def test_no_unknown_material_or_unverified_number_can_be_checked(self):
        before=self.initial()
        with patch.object(ocr,"recognize_pdf_page",return_value=(1,data_fixture())):
            self.assertEqual(self.recognize().status_code,200)
        for changes in (
            {"action":"existing","item_id":"99999"},
            {"action":"new","folder_id":"99999"},
            {"action":"unresolved"},
            {"action":"new","folder_id":"","value":""},
            {"source_line":"999"},
            {"no_items":"1"},
            {"document_date":"bad"},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.draft(**changes).status_code,400)
                self.assertEqual(self.get_doc()["version"],2)
        self.assertEqual(self.snapshot_stock(),before)

    def test_same_part_cannot_have_conflicting_checked_identity(self):
        self.initial()
        with patch.object(ocr,"recognize_pdf_page",return_value=(2,data_fixture())):
            self.assertEqual(self.recognize().status_code,200)
            self.assertEqual(self.draft().status_code,303)
            self.assertEqual(self.recognize(3,2).status_code,200)
        self.assertEqual(self.draft(4,2,number="OTHER").status_code,400)
        self.assertEqual(self.get_doc()["version"],4)
        self.assertEqual(self.draft(4,2,number="OTHER",part="2").status_code,303)

    def empty_page(self,version,page,**overrides):
        # A genuine rowless page: no synthetic goods form fields.
        values={"csrf":"token","version":str(version),"page":str(page),"part":"1",
                "document_type":"bill","counterparty":"Синтетический поставщик",
                "number":"A-1","document_date":"2026-11-20","page_note":"",
                "checked":"1","no_items":"1"}
        values.update(overrides)
        return self.client(2).post(PREFIX+"/save",data=values)

    def review_snapshot(self):
        return (tuple(self.get_doc()),
                [tuple(r) for r in self.conn.execute(
                    "SELECT * FROM med_supply_document_events ORDER BY id")],
                self.snapshot_stock())

    def assert_identity_rejected(self,submit,version,page,**changes):
        before=self.review_snapshot()
        response=submit(version,page,**changes)
        self.assertEqual(response.status_code,400)
        self.assertIn("У проверенных страниц одной части разные реквизиты",
                      response.get_data(as_text=True))
        # Includes original bytes, OCR evidence, editable draft, version,
        # status, timestamps, complete document history and stock tables.
        self.assertEqual(self.review_snapshot(),before)

    def test_checked_goods_then_rowless_page_conflict_is_atomic(self):
        self.initial()
        with patch.object(ocr,"recognize_pdf_page",return_value=(2,data_fixture())):
            self.assertEqual(self.recognize().status_code,200)
            self.assertEqual(self.recognize(2,2).status_code,200)
        self.assertEqual(self.draft(3,1).status_code,303)
        self.assertEqual(self.get_doc()["status"],"review")
        for changes in (
            {"counterparty":"Другой поставщик","number":"B-99","document_date":"2026-11-21"},
            {"counterparty":"Другой поставщик"},
            {"number":"B-99"},
            {"document_date":"2026-11-21"},
            {"document_type":"upd"},
            {"counterparty":"","number":"","document_date":"","document_type":"unknown"},
        ):
            with self.subTest(changes=changes):
                self.assert_identity_rejected(self.empty_page,4,2,**changes)
        # Same identity in the same part is valid without any goods rows.
        self.assertEqual(self.empty_page(4,2).status_code,303)
        self.assertEqual(self.get_doc()["status"],"reviewed")
        self.assertEqual(self.state()["pages"]["2"]["rows"],[])
        self.assertTrue(self.state()["pages"]["2"]["no_items"])
        # An already checked rowless page cannot introduce a contradiction.
        self.assert_identity_rejected(self.empty_page,5,2,number="CHANGED")
        self.assertEqual(self.get_doc()["status"],"reviewed")
        self.assertEqual(self.empty_page(5,2,part="2",number="B-99").status_code,303)
        self.assertEqual(self.get_doc()["status"],"reviewed")

    def test_checked_rowless_then_goods_page_conflict_is_atomic(self):
        self.initial()
        with patch.object(ocr,"recognize_pdf_page",return_value=(2,data_fixture())):
            self.assertEqual(self.recognize().status_code,200)
            self.assertEqual(self.recognize(2,2).status_code,200)
        self.assertEqual(self.empty_page(3,1).status_code,303)
        self.assertEqual(self.get_doc()["status"],"review")
        for changes in (
            {"counterparty":"Другой поставщик","number":"B-99","document_date":"2026-11-21"},
            {"counterparty":"Другой поставщик"},
            {"number":"B-99"},
            {"document_date":"2026-11-21"},
            {"document_type":"upd"},
        ):
            with self.subTest(changes=changes):
                self.assert_identity_rejected(self.draft,4,2,**changes)
        # A different logical part may carry independent document identity.
        self.assertEqual(self.draft(4,2,part="2",number="B-99",
                                    counterparty="Другой поставщик",
                                    document_date="2026-11-21").status_code,303)
        self.assertEqual(self.get_doc()["status"],"reviewed")
        # Reassigning that checked page to the conflicting first part is blocked.
        self.assert_identity_rejected(self.draft,5,2,part="1",number="B-99",
                                      counterparty="Другой поставщик",
                                      document_date="2026-11-21")
        self.assertEqual(self.get_doc()["status"],"reviewed")

    def test_two_checked_rowless_pages_also_require_same_identity(self):
        self.initial()
        with patch.object(ocr,"recognize_pdf_page",return_value=(2,data_fixture())):
            self.assertEqual(self.recognize().status_code,200)
            self.assertEqual(self.recognize(2,2).status_code,200)
        self.assertEqual(self.empty_page(3,1).status_code,303)
        self.assert_identity_rejected(self.empty_page,4,2,number="B-99")
        self.assertEqual(self.empty_page(4,2).status_code,303)
        self.assertEqual(self.get_doc()["status"],"reviewed")

    def test_history_failure_rolls_back_ocr_fields_and_status(self):
        before=self.initial()
        with patch.object(ocr,"recognize_pdf_page",return_value=(1,data_fixture())), \
             patch.object(ocr.inbox,"_event",side_effect=sqlite3.IntegrityError("synthetic failure")):
            self.assertEqual(self.recognize().status_code,400)
        self.assertEqual(self.get_doc()["version"],1)
        self.assertEqual(self.get_doc()["fields_json"],"{}")
        self.assertEqual(self.get_doc()["status"],"pending")
        self.assertEqual(self.snapshot_stock(),before)

    def test_stale_version_after_ocr_never_overwrites_new_card(self):
        before=self.initial()
        def changed(*args):
            self.conn.execute("UPDATE med_supply_documents SET version=2,note='New' WHERE id=1")
            self.conn.commit()
            return 1,data_fixture()
        with patch.object(ocr,"recognize_pdf_page",side_effect=changed):
            self.assertEqual(self.recognize().status_code,409)
        self.assertEqual(self.get_doc()["note"],"New")
        self.assertEqual(self.get_doc()["fields_json"],"{}")
        self.assertEqual(self.snapshot_stock(),before)


if __name__=="__main__":
    unittest.main()
