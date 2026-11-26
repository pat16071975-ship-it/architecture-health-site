"""Derived unit costs and goods-only confirmation through real draft routes."""
import copy
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from flask import template_rendered

import medical_supplies_ocr as ocr
import test_medical_supplies_ocr as fixtures


class UnitCostTests(unittest.TestCase):
    def test_decimal_division_rounding_and_display(self):
        cases=(
            ("289.20","2","144.60"),
            ("10.00","3","3.3333"),
            ("1.00","32","0.0313"),
            ("1.00","6000","0.0002"),
            ("19.99","0.125","159.92"),
            ("1 234,50","2,5","493.80"),
            ("1000000000","0.001","1000000000000.00"),
        )
        for amount,quantity,expected in cases:
            with self.subTest(amount=amount,quantity=quantity):
                self.assertEqual(ocr.unit_cost(amount,quantity,"упак"),expected)

    def test_zero_amount_is_valid_and_unknown_is_not_zero(self):
        for amount in (0,"0","0.00",Decimal("0.00")):
            self.assertEqual(ocr.unit_cost(amount,"3","шт"),"0.00")
        for amount,quantity in ((None,"3"),("","3"),("2",None),("2",""),("2","0")):
            self.assertIsNone(ocr.unit_cost(amount,quantity,"шт"))

    def test_invalid_values_and_units_do_not_produce_a_cost(self):
        for amount,quantity,unit in (
            (True,"2","шт"),(1.2,"2","шт"),("2",False,"шт"),
            ("NaN","2","шт"),("Infinity","2","шт"),("-1","2","шт"),
            ("1.001","2","шт"),("1","0.0001","шт"),("1000000001","1","шт"),
            ("1","1 2","шт"),("1","2",None),("1","2",""),
            ("1","2","—"),("1","2","–"),("1","2","-"),("1","2","?"),
        ):
            with self.subTest(amount=amount,quantity=quantity,unit=unit):
                self.assertIsNone(ocr.unit_cost(amount,quantity,unit))

    def test_an_explicit_net_tax_or_unknown_basis_is_not_gross(self):
        for basis in ("printed_net","tax","unknown",""):
            self.assertIsNone(ocr.unit_cost("120.50","2","шт",value_basis=basis))
        for basis in (None,"printed_gross","printed_single_total"):
            self.assertEqual(ocr.unit_cost("289.20","2","шт",value_basis=basis),"144.60")


class GoodsReviewRouteTests(unittest.TestCase):
    client=fixtures.OCRRouteTests.client
    upload=fixtures.OCRRouteTests.upload
    get_doc=fixtures.OCRRouteTests.get_doc
    state=fixtures.OCRRouteTests.state
    snapshot_stock=fixtures.OCRRouteTests.snapshot_stock
    initial=fixtures.OCRRouteTests.initial
    review_snapshot=fixtures.OCRRouteTests.review_snapshot

    def setUp(self):
        fixtures.OCRRouteTests.setUp(self)
        self.folder_id=self.conn.execute(
            "INSERT INTO med_supply_folders(name) VALUES('Fixture goods')").lastrowid
        self.conn.commit()

    def automatic(self):
        page=fixtures.data_fixture()
        page["candidates"][0].update(value="289.20",value_basis="printed_gross")
        page.update(printed_total="289.20",total_basis="printed_gross",
                    header_candidate={"document_type":"unknown","counterparty":"Поставщик страницы",
                        "number":"","date":"","part":7,"note":"",
                        "issues":["Реквизиты документа не подтверждены."],
                        "pagination":{"page":1,"of":2}})
        return page

    def seed(self,pages=None):
        if self.get_doc() is None:
            self.initial()
        pages=pages or {"1":self.automatic()}
        fields={ocr.KEY:{"page_count":len(pages),"pages":pages},"unrelated":{"keep":True}}
        self.conn.execute("UPDATE med_supply_documents SET fields_json=?,version=2,status='review' WHERE id=1",
                          (json.dumps(fields,ensure_ascii=False),))
        self.conn.commit()

    def form(self,**changes):
        values={"csrf":"token","version":"2","page":"1","review_scope":"goods",
                "source_line":"2","name":"Салфетки 100 шт/упак","unit":"упак",
                "quantity":"2","price":"120.50","value":"289.20","action":"new",
                "item_id":"","folder_id":str(self.folder_id),"note":"","checked":"1"}
        values.update(changes)
        return values

    def submit(self,uid=2,**changes):
        return self.client(uid).post(fixtures.PREFIX+"/save",data=self.form(**changes))

    def context(self,page=1):
        captured=[]
        def capture(sender,template,context,**extra):
            if template.name=="medical_supplies_ocr.html":
                captured.append(context)
        with template_rendered.connected_to(capture,self.app):
            response=self.client(2).get(fixtures.PREFIX+"?p="+str(page))
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        return captured[0]

    def test_goods_confirmation_without_metadata_recomputes_and_records_its_scope(self):
        self.seed()
        stock=self.snapshot_stock()
        original=bytes(self.get_doc()["original_bytes"])
        source=copy.deepcopy(self.state()["pages"]["1"])
        response=self.submit(unit_cost="9999999.99")
        self.assertEqual(response.status_code,303,response.get_data(as_text=True))
        saved=self.state()["pages"]["1"]
        self.assertTrue(saved["checked"])
        self.assertEqual(saved["review_scope"],"goods")
        self.assertNotIn("header",saved)
        self.assertEqual(saved["header_candidate"],source["header_candidate"])
        self.assertEqual(saved["rows"][0]["unit_cost"],"144.60")
        self.assertEqual(saved["rows"][0]["value"],"289.20")
        self.assertEqual(saved["rows"][0]["price"],"120.50")
        for key in ("words","lines","candidates"):
            self.assertEqual(saved[key],source[key])
        event=json.loads(self.conn.execute(
            "SELECT after_json FROM med_supply_document_events WHERE action='ocr_review' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0])
        self.assertEqual(event["review_scope"],"goods")
        self.assertFalse(event["stock_posted"])
        self.assertEqual(bytes(self.get_doc()["original_bytes"]),original)
        self.assertEqual(self.snapshot_stock(),stock)
        self.assertEqual(json.loads(self.get_doc()["fields_json"])["unrelated"],{"keep":True})

    def test_goods_save_preserves_both_empty_and_populated_manual_headers(self):
        for header in ({},{"document_type":"act","counterparty":"Ручной поставщик",
                           "number":"A-8","date":"2026-11-20","part":8,"note":"Оставить"}):
            with self.subTest(header=header):
                page=self.automatic(); page["header"]=copy.deepcopy(header)
                self.seed({"1":page})
                # A goods form cannot alter metadata, even if a client adds it.
                response=self.submit(document_type="invalid",document_date="invalid",part="999",
                                     counterparty="Подмена",number="REPLACEMENT",page_note="Changed")
                self.assertEqual(response.status_code,303,response.get_data(as_text=True))
                self.assertEqual(self.state()["pages"]["1"]["header"],header)
                self.assertEqual(self.context()["supplier"],header.get("counterparty",""))

    def test_rounding_never_rewrites_the_line_amount_or_printed_price(self):
        self.seed()
        response=self.submit(quantity="6000",value="1.00",price="0.0001",unit_cost="500")
        self.assertEqual(response.status_code,303)
        row=self.state()["pages"]["1"]["rows"][0]
        self.assertEqual((row["quantity"],row["value"],row["price"],row["unit_cost"]),
                         ("6000","1.00","0.0001","0.0002"))
        response=self.submit(version="3",value="0.00",quantity="2")
        self.assertEqual(response.status_code,303)
        self.assertEqual(self.state()["pages"]["1"]["rows"][0]["unit_cost"],"0.00")

    def test_get_recomputes_cost_uses_only_this_page_supplier_and_keeps_money_issues(self):
        page=self.automatic()
        page["rows"]=[{**page["candidates"][0],"unit_cost":"0.01"}]
        page["printed_total"]="300.00"
        page["recognition_issues"]=["Не завершено чтение таблицы."]
        self.seed({"1":page})
        before=list(self.conn.iterdump())
        with patch.object(ocr,"proposed_header",side_effect=AssertionError("GET must not regroup parts")):
            context=self.context()
        self.assertEqual(context["view_scope"],"goods")
        self.assertEqual(context["supplier"],"Поставщик страницы")
        self.assertEqual(context["rows"][0]["unit_cost"],"144.60")
        self.assertNotIn("Реквизиты документа не подтверждены.",context["page_issues"])
        self.assertIn("Не завершено чтение таблицы.",context["page_issues"])
        self.assertTrue(any("не совпадает с печатным итогом" in issue for issue in context["page_issues"]))
        self.assertFalse(any("страниц" in issue and "документ" in issue for issue in context["page_issues"]))
        self.assertEqual(list(self.conn.iterdump()),before)

    def test_get_does_not_derive_a_gross_unit_cost_from_an_explicit_non_gross_amount(self):
        page=self.automatic()
        page["rows"]=[{**page["candidates"][0],"value_basis":"printed_net","unit_cost":"144.60"}]
        page["header_candidate"]["counterparty"]=""
        self.seed({"1":page})
        context=self.context()
        self.assertIsNone(context["rows"][0]["unit_cost"])
        self.assertEqual(context["supplier"],"")  # Inbox supplier belongs to the whole PDF.
        self.assertTrue(any("не подтверждена стоимость с налогом" in issue for issue in context["page_issues"]))

    def test_unchanged_non_gross_roundtrip_requires_explicit_amount_edit(self):
        for basis in ("printed_net","unknown",""):
            for saved_rows in (False,True):
                with self.subTest(basis=basis,saved_rows=saved_rows):
                    page=self.automatic()
                    page["candidates"][0]["value_basis"]=basis
                    if saved_rows:
                        page["rows"]=copy.deepcopy(page["candidates"])
                    self.seed({"1":page})
                    changed={"row_index":"0","value_edited":"0","quantity":"3",
                             "name":"Исправленное название","value_basis":"printed_gross"}
                    self.assertEqual(self.submit(checked="",**changed).status_code,303)
                    row=self.state()["pages"]["1"]["rows"][0]
                    self.assertEqual(row["value_basis"],basis)
                    self.assertEqual(row["value"],"289.20")
                    self.assertIsNone(row["unit_cost"])
                    self.assertIsNone(self.context()["rows"][0]["unit_cost"])
                    before=self.review_snapshot()
                    self.assertEqual(self.submit(version="3",**changed).status_code,400)
                    self.assertEqual(self.review_snapshot(),before)
                    # Re-entering the same number is a deliberate manual
                    # gross correction; quantity/name changes alone are not.
                    changed["value_edited"]="1"
                    self.assertEqual(self.submit(version="3",**changed).status_code,303)
                    row=self.state()["pages"]["1"]["rows"][0]
                    self.assertIsNone(row["value_basis"])
                    self.assertEqual((row["value"],row["unit_cost"]),("289.20","96.40"))

    def test_stable_manual_row_index_preserves_unknown_after_another_row_is_deleted(self):
        page=self.automatic()
        safe={**page["candidates"][0],"source_line":None,"manual":True,"name":"First"}
        uncertain={**safe,"name":"Second","value_basis":""}
        page["rows"]=[safe,uncertain]
        self.seed({"1":page})
        self.assertEqual(self.submit(source_line="",row_index="1",value_edited="0",
                                     checked="",name="Renamed second",quantity="3").status_code,303)
        row=self.state()["pages"]["1"]["rows"][0]
        self.assertEqual(row["value_basis"],"")
        self.assertIsNone(row["unit_cost"])
        self.assertIsNone(self.context()["rows"][0]["unit_cost"])
        self.assertEqual(self.submit(version="3",source_line="",row_index="0",value_edited="1",
                                     quantity="3").status_code,303)
        self.assertEqual(self.state()["pages"]["1"]["rows"][0]["unit_cost"],"96.40")

    def test_legacy_form_does_not_promote_non_gross_or_ambiguous_manual_rows(self):
        for manual in (False,True):
            with self.subTest(manual=manual):
                page=self.automatic()
                bad={**page["candidates"][0],"value_basis":"printed_net"}
                page["rows"]=[bad]
                if manual:
                    bad["source_line"]=None
                    page["rows"].insert(0,{**bad,"value_basis":"printed_gross","name":"Other manual row"})
                self.seed({"1":page})
                values={"source_line":"" if manual else "2","review_scope":"",
                        "document_type":"bill","counterparty":"Поставщик","number":"A",
                        "document_date":"2026-11-20","part":"1"}
                self.assertEqual(self.submit(checked="",**values).status_code,303)
                self.assertIsNone(self.state()["pages"]["1"]["rows"][0]["unit_cost"])
                self.assertEqual(self.submit(version="3",**values).status_code,400)

    def test_explicit_zero_gross_correction_and_invalid_row_associations(self):
        page=self.automatic()
        page["rows"]=[{**page["candidates"][0],"value":"0.00","value_basis":"unknown"}]
        self.seed({"1":page})
        for changes in ({"row_index":"999"},{"row_index":""},
                        {"row_index":"0","source_line":""},{"value_edited":"yes"}):
            with self.subTest(changes=changes):
                before=self.review_snapshot()
                self.assertEqual(self.submit(checked="",**changes).status_code,400)
                self.assertEqual(self.review_snapshot(),before)
        self.assertEqual(self.submit(row_index="0",value="0.00",value_edited="0").status_code,400)
        self.assertEqual(self.submit(row_index="0",value="0.00",value_edited="1").status_code,303)
        row=self.state()["pages"]["1"]["rows"][0]
        self.assertEqual((row["value"],row["unit_cost"]),("0.00","0.00"))

    def test_goods_confirmation_still_requires_complete_goods_and_category(self):
        self.seed()
        for changes in ({"quantity":""},{"quantity":"0"},{"unit":""},{"unit":"—"},
                        {"value":""},{"action":"unresolved"},{"folder_id":""},
                        {"review_scope":"unrecognized"}):
            with self.subTest(changes=changes):
                before=self.review_snapshot()
                self.assertEqual(self.submit(**changes).status_code,400)
                self.assertEqual(self.review_snapshot(),before)
        self.assertEqual(self.submit(checked="",value="").status_code,303)
        self.assertIsNone(self.state()["pages"]["1"]["rows"][0]["unit_cost"])

    def test_legacy_document_checks_remain_and_ignore_goods_only_confirmation(self):
        first=self.automatic()
        first.update(header={"document_type":"bill","counterparty":"Первый","number":"A",
                             "date":"2026-11-20","part":1,"note":""},checked=True,review_scope="goods")
        second=self.automatic()
        self.seed({"1":first,"2":second})
        before=self.review_snapshot()
        self.assertEqual(self.submit(page="2",review_scope="").status_code,400)
        self.assertEqual(self.review_snapshot(),before)
        legacy=dict(page="2",review_scope="",document_type="bill",counterparty="Другой",
                    number="B",document_date="2026-11-21",part="1")
        self.assertEqual(self.submit(**legacy).status_code,303)
        self.assertEqual(self.state()["pages"]["2"]["review_scope"],"document")
        # The first page becomes a full document confirmation only after the
        # old identity check passes; goods-only approval cannot bypass it.
        before=self.review_snapshot()
        self.assertEqual(self.submit(version="3",review_scope="",document_type="bill",
            counterparty="Первый",number="A",document_date="2026-11-20",part="1").status_code,400)
        self.assertEqual(self.review_snapshot(),before)
        self.assertEqual(self.submit(version="3").status_code,303)
        self.assertEqual(self.state()["pages"]["1"]["header"],first["header"])

    def test_goods_form_keeps_permission_csrf_and_version_protection(self):
        self.seed()
        before=self.review_snapshot()
        self.assertEqual(self.submit(uid=3).status_code,403)
        self.assertEqual(self.submit(csrf="wrong").status_code,400)
        self.assertEqual(self.submit(version="1").status_code,409)
        self.assertEqual(self.review_snapshot(),before)


if __name__=="__main__":
    unittest.main()
