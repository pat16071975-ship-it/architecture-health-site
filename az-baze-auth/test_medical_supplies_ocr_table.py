"""Synthetic Russian invoice geometry and saved-document goods-table regressions.

The live Komus original is deliberately not a fixture. Route tests reuse the
real private inbox and SQLite, and replace only the OCR engine when necessary.
"""
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

from flask import template_rendered
from pypdf import PdfReader

import medical_supplies_ocr as ocr
import test_medical_supplies_ocr as fixtures


def tsv(lines):
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [header]
    for line_id, (y, tokens) in enumerate(lines, 1):
        for word_id, token in enumerate(tokens, 1):
            x, text = token[:2]
            width = token[2] if len(token) == 3 else max(10, len(text) * 8)
            rows.append("\t".join(map(str, (5, 1, 1, 1, line_id, word_id,
                                          x, y, width, 18, 96, text))))
    return "\n".join(rows) + "\n"


def simple_table(*, total="289,20", qualifier=None, wrapped=False, title="Счет"):
    lines = [
        (50, [(100, title), (240, "№"), (260, "123")]),
        (100, [(100, "Наименование", 200), (500, "Ед."),
               (650, "Количество"), (850, "Цена"), (1070, "Сумма")]),
    ]
    if qualifier is not None:
        words = qualifier.split()
        x = 1070
        qualified = []
        for word in words:
            qualified.append((x, word))
            x += len(word) * 8 + 7
        lines.append((124, qualified))
    goods = [(105, "Салфетки"), (505, "упак"), (680, "2"), (870, "120,50")]
    if total is not None:
        goods.append((1080, total))
    lines.append((170, goods))
    if wrapped:
        lines.append((192, [(105, "медицинские"), (205, "стерильные")]))
    lines.extend([
        (245, [(105, "Итого"), (1080, total or "241,00")]),
        (275, [(105, "Отпустил"), (240, "Иванов")]),
    ])
    return tsv(lines)


def upd_table(*, explicit=True):
    if explicit:
        headings = [
            (100, [(100, "Наименование", 180), (430, "Ед."), (540, "Количество"),
                   (690, "Цена"), (840, "Стоимость"), (1010, "Сумма"), (1200, "Стоимость")]),
            (124, [(840, "без"), (875, "налога"), (1010, "налога"),
                   (1200, "с"), (1215, "налогом")]),
        ]
    else:
        headings = [
            (100, [(100, "Наименование", 180), (430, "Ед."), (540, "Количество"),
                   (690, "Цена"), (840, "Сумма"), (1010, "Сумма"), (1200, "Сумма")]),
        ]
    return tsv([
        (50, [(100, "Универсальный"), (230, "передаточный"), (370, "документ")]),
        *headings,
        (175, [(105, "Перчатки"), (435, "упак"), (570, "2"), (700, "126,00"),
               (855, "252,00"), (1020, "50,41"), (1215, "302,41")]),
        (240, [(105, "Всего"), (160, "к"), (180, "оплате"), (1215, "302,41")]),
    ])


class GoodsGeometryTests(unittest.TestCase):
    def one(self, raw):
        parsed = ocr.parse_tsv(raw)
        self.assertEqual(len(parsed["candidates"]), 1, parsed["candidates"])
        return parsed, parsed["candidates"][0]

    def test_bill_uses_one_printed_cost_without_multiplying_quantity_by_price(self):
        _, row = self.one(simple_table())
        self.assertEqual((row["name"], row["quantity"], row["unit"]),
                         ("Салфетки", "2", "упак"))
        self.assertEqual(row["price"], "120.50")
        self.assertEqual(row["value"], "289.20")
        self.assertNotEqual(Decimal(row["value"]), Decimal(row["quantity"]) * Decimal(row["price"]))
        self.assertEqual(row["action"], "unresolved")
        self.assertIsNone(row["item_id"])
        self.assertIsNone(row["folder_id"])

    def test_act_explicit_gross_uses_printed_value_even_when_vat_rounding_differs(self):
        _, row = self.one(simple_table(total="289,21", qualifier="с НДС", title="Акт"))
        self.assertEqual(row["value"], "289.21")
        self.assertNotEqual(Decimal(row["value"]), Decimal("2") * Decimal("120.50") * Decimal("1.2"))

    def test_upd_selects_explicit_gross_among_net_tax_and_gross(self):
        _, row = self.one(upd_table())
        self.assertEqual(row["value"], "302.41")
        self.assertEqual(row["price"], "126.00")
        self.assertNotIn(row["value"], ("252.00", "50.41"))

    def test_repeated_ambiguous_totals_stay_unknown(self):
        _, row = self.one(upd_table(explicit=False))
        self.assertIsNone(row["value"])
        self.assertEqual(row["price"], "126.00")

    def test_explicit_net_only_is_not_promoted_to_final_cost(self):
        for qualifier in ("без НДС", "без налога"):
            with self.subTest(qualifier=qualifier):
                _, row = self.one(simple_table(qualifier=qualifier, total="241,00"))
                self.assertIsNone(row["value"])

    def test_missing_or_unreadable_printed_cost_has_no_arithmetic_fallback(self):
        for total in (None, "2B9,20", "289,2,0"):
            with self.subTest(total=total):
                _, row = self.one(simple_table(total=total))
                self.assertEqual(row["quantity"], "2")
                self.assertEqual(row["price"], "120.50")
                self.assertIsNone(row["value"])

    def test_printed_zero_cost_remains_zero(self):
        _, row = self.one(simple_table(total="0,00"))
        self.assertEqual(row["value"], "0.00")

    def test_wrapped_name_keeps_both_source_lines_and_excludes_footer(self):
        parsed, row = self.one(simple_table(wrapped=True))
        self.assertEqual(row["name"], "Салфетки медицинские стерильные")
        expected = [line["id"] for line in parsed["lines"]
                    if "Салфетки" in line["text"] or "медицинские" in line["text"]]
        self.assertEqual(row["source_line"], expected[0])
        self.assertEqual(row["source_lines"], expected)
        self.assertNotIn("Итого", row["name"])
        self.assertNotIn("Отпустил", row["name"])
        self.assertEqual(row["quantity"], "2")
        self.assertEqual(row["value"], "289.20")

    def test_no_table_header_does_not_invent_goods(self):
        parsed = ocr.parse_tsv(tsv([
            (50, [(100, "Акт"), (200, "№"), (240, "15")]),
            (100, [(100, "Всего"), (200, "услуг"), (680, "2"), (1080, "1500,00")]),
        ]))
        self.assertEqual(parsed["candidates"], [])

    def test_split_header_and_three_line_name_keep_complete_goods(self):
        parsed, row = self.one(tsv([
            (100, [(100, "Наимено-", 200), (500, "Ед."),
                   (650, "Кол-"), (850, "Цена"), (1070, "Сумма")]),
            (122, [(100, "вание"), (650, "во")]),
            (170, [(105, "Салфетки"), (505, "упак"), (680, "2"),
                   (870, "120,50"), (1080, "289,20")]),
            (192, [(105, "медицинские")]),
            (214, [(105, "стерильные")]),
            (245, [(105, "Итого"), (1080, "289,20")]),
        ]))
        self.assertEqual(row["name"], "Салфетки медицинские стерильные")
        self.assertEqual(row["source_lines"], [3, 4, 5])
        self.assertEqual(row["quantity"], "2")
        self.assertEqual(row["value"], "289.20")


_UNSET = object()


class SavedGoodsTableTests(unittest.TestCase):
    setUp = fixtures.OCRRouteTests.setUp
    client = fixtures.OCRRouteTests.client
    upload = fixtures.OCRRouteTests.upload
    get_doc = fixtures.OCRRouteTests.get_doc
    snapshot_stock = fixtures.OCRRouteTests.snapshot_stock
    initial = fixtures.OCRRouteTests.initial
    recognize = fixtures.OCRRouteTests.recognize
    draft = fixtures.OCRRouteTests.draft
    state = fixtures.OCRRouteTests.state
    review_snapshot = fixtures.OCRRouteTests.review_snapshot

    def saved_old_page(self, *, rows=_UNSET, checked=False):
        self.initial()
        parsed = ocr.parse_tsv(simple_table(wrapped=True))
        # Exact old shape: geometry was saved, but the earlier extractor did
        # not produce usable goods. No extractor marker and no new fields.
        page = {key: copy.deepcopy(parsed[key]) for key in ("words", "lines", "confidence")}
        page["candidates"] = []
        if rows is not _UNSET:
            page["rows"] = copy.deepcopy(rows)
            page.update(checked=checked, no_items=not rows,
                        header={"document_type": "bill", "counterparty": "Поставщик",
                                "number": "A-1", "date": "2026-11-20", "part": 1, "note": "Проверено"})
        fields = {ocr.KEY: {"page_count": 1, "pages": {"1": page}},
                  "unrelated_document_metadata": {"keep": "unchanged"}}
        self.conn.execute(
            "UPDATE med_supply_documents SET fields_json=?,version=2,status=? WHERE id=1",
            (json.dumps(fields, ensure_ascii=False), "reviewed" if checked else "review"),
        )
        self.conn.commit()
        return parsed

    def view_context(self, uid=2):
        contexts = []

        def collect(sender, template, context, **extra):
            if template.name == "medical_supplies_ocr.html":
                contexts.append(context)

        with template_rendered.connected_to(collect, self.app):
            result = self.client(uid).get(fixtures.PREFIX)
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True)[:500])
        self.assertEqual(len(contexts), 1)
        return result, contexts[0]

    def test_get_rederives_goods_from_saved_words_without_ocr_or_any_write(self):
        self.saved_old_page()
        before = self.review_snapshot()
        with patch.object(ocr, "recognize_pdf_page", side_effect=AssertionError("must not re-OCR")):
            response, context = self.view_context()
            self.assertEqual(len(context["rows"]), 1)
            row = context["rows"][0]
            self.assertEqual((row["name"], row["quantity"], row["unit"], row["value"]),
                             ("Салфетки медицинские стерильные", "2", "упак", "289.20"))
            self.view_context(uid=3)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertEqual(self.review_snapshot(), before)

    def test_earlier_partial_suggestion_is_rederived_without_losing_saved_evidence(self):
        parsed = self.saved_old_page()
        fields = json.loads(self.get_doc()["fields_json"])
        old = copy.deepcopy(parsed["candidates"][0])
        old.update(name="Салфетки", value=None)
        old.pop("source_lines", None)
        fields[ocr.KEY]["pages"]["1"]["candidates"] = [old]
        self.conn.execute("UPDATE med_supply_documents SET fields_json=? WHERE id=1",
                          (json.dumps(fields, ensure_ascii=False),))
        self.conn.commit()
        before = self.review_snapshot()
        _, context = self.view_context()
        self.assertEqual(context["rows"][0]["name"], "Салфетки медицинские стерильные")
        self.assertEqual(context["rows"][0]["value"], "289.20")
        self.assertEqual(self.review_snapshot(), before)

    def test_rederived_row_can_be_saved_with_the_existing_form_and_section_key(self):
        self.saved_old_page()
        before_stock = self.snapshot_stock()
        _, context = self.view_context()
        row = context["rows"][0]
        folder = self.conn.execute("SELECT id FROM med_supply_folders ORDER BY id LIMIT 1").fetchone()[0]
        result = self.draft(version=2, source_line=str(row["source_line"]), name=row["name"],
                            quantity=row["quantity"], unit=row["unit"], price=row["price"],
                            value=row["value"], action="new", folder_id=str(folder), note="")
        self.assertEqual(result.status_code, 303, result.get_data(as_text=True)[:500])
        saved = self.state()["pages"]["1"]
        self.assertEqual(saved["rows"][0]["name"], row["name"])
        self.assertEqual(saved["rows"][0]["value"], "289.20")
        self.assertEqual(saved["rows"][0]["folder_id"], folder)
        self.assertTrue(saved["checked"])
        self.assertEqual(self.get_doc()["version"], 3)
        self.assertEqual(self.get_doc()["status"], "reviewed")
        event = self.conn.execute("SELECT * FROM med_supply_document_events ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(event["action"], "ocr_review")
        self.assertFalse(json.loads(event["after_json"])["stock_posted"])
        self.assertEqual(self.snapshot_stock(), before_stock)

    def test_get_and_refresh_preserve_manual_rows_and_checked_identity(self):
        manual = {"source_line": None, "name": "Ручное проверенное название", "unit": "шт",
                  "quantity": "7", "price": "88.00", "value": "615.99", "action": "skip",
                  "item_id": None, "folder_id": None, "note": "Оставить ручное решение", "manual": True}
        self.saved_old_page(rows=[manual], checked=True)
        original = bytes(self.get_doc()["original_bytes"])
        before = self.review_snapshot()
        saved_before = copy.deepcopy(self.state()["pages"]["1"])
        with patch.object(ocr, "recognize_pdf_page", side_effect=AssertionError("must not re-OCR")):
            _, context = self.view_context()
            shown = dict(context["rows"][0])
            shown.pop("matches", None)
            self.assertEqual(shown, manual)
            self.assertEqual(self.review_snapshot(), before)
            result = self.recognize(version=2)
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True)[:500])
        page = self.state()["pages"]["1"]
        for key in ("rows", "header", "checked", "no_items", "words", "lines"):
            self.assertEqual(page[key], saved_before[key])
        self.assertEqual(self.get_doc()["status"], "reviewed")
        self.assertEqual(bytes(self.get_doc()["original_bytes"]), original)
        self.assertEqual(self.snapshot_stock(), before[2])

    def test_intentionally_empty_checked_rows_are_not_replaced_by_suggestions(self):
        self.saved_old_page(rows=[], checked=True)
        before = self.review_snapshot()
        _, context = self.view_context()
        self.assertEqual(context["rows"], [])
        self.assertEqual(self.review_snapshot(), before)
        self.assertTrue(self.state()["pages"]["1"]["no_items"])

    def test_unchecked_manual_draft_is_also_preserved(self):
        manual = {"source_line": None, "name": "Название редактируется", "unit": "",
                  "quantity": None, "price": None, "value": None, "action": "unresolved",
                  "item_id": None, "folder_id": None, "note": "Черновик", "manual": True}
        self.saved_old_page(rows=[manual])
        _, context = self.view_context()
        self.assertEqual(context["rows"][0]["name"], manual["name"])
        self.assertIsNone(context["rows"][0]["value"])
        with patch.object(ocr, "recognize_pdf_page", side_effect=AssertionError("must not re-OCR")):
            self.assertEqual(self.recognize(version=2).status_code, 200)
        page = self.state()["pages"]["1"]
        self.assertEqual(page["rows"], [manual])
        self.assertFalse(page["checked"])

    def test_post_refresh_only_updates_saved_extraction_and_audits_once(self):
        self.saved_old_page()
        original = bytes(self.get_doc()["original_bytes"])
        stock = self.snapshot_stock()
        events_before = self.conn.execute("SELECT count(*) FROM med_supply_document_events").fetchone()[0]
        with patch.object(ocr, "recognize_pdf_page", side_effect=AssertionError("must not re-OCR")):
            response = self.recognize(version=2)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
            self.assertEqual(response.json["version"], 3)
            self.assertFalse(response.json["cached"])
            page = self.state()["pages"]["1"]
            self.assertEqual(page["extractor_version"], ocr.EXTRACTOR_VERSION)
            self.assertEqual(page["candidates"][0]["value"], "289.20")
            self.assertNotIn("rows", page)
            after = self.review_snapshot()
            cached = self.recognize(version=3)
            self.assertEqual(cached.status_code, 200)
            self.assertTrue(cached.json["cached"])
        self.assertEqual(self.review_snapshot(), after)
        self.assertEqual(bytes(self.get_doc()["original_bytes"]), original)
        self.assertEqual(self.snapshot_stock(), stock)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM med_supply_document_events").fetchone()[0], events_before + 1)
        event = self.conn.execute("SELECT * FROM med_supply_document_events ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((event["action"], event["actor_id"]), ("ocr_page", 2))
        self.assertTrue(json.loads(event["after_json"])["extraction_refreshed"])
        self.assertFalse(json.loads(event["after_json"])["stock_posted"])
        fields = json.loads(self.get_doc()["fields_json"])
        self.assertEqual(fields["unrelated_document_metadata"], {"keep": "unchanged"})

    def test_old_extraction_refresh_obeys_permission_csrf_and_version(self):
        self.saved_old_page()
        before = self.review_snapshot()
        with patch.object(ocr, "recognize_pdf_page", side_effect=AssertionError("must not re-OCR")):
            for uid in (3, 4):
                self.assertEqual(self.recognize(version=2, uid=uid).status_code, 403)
            self.assertEqual(self.client(2).post(fixtures.PREFIX + "/page",
                data={"version": "2", "page": "1"}).status_code, 400)
            self.assertEqual(self.recognize(version=1).status_code, 409)
        self.assertEqual(self.review_snapshot(), before)

    def test_refresh_audit_failure_is_atomic(self):
        self.saved_old_page()
        before = self.review_snapshot()
        with patch.object(ocr, "recognize_pdf_page", side_effect=AssertionError("must not re-OCR")), \
             patch.object(ocr.inbox, "_event", side_effect=sqlite3.IntegrityError("audit unavailable")):
            response = self.recognize(version=2)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.review_snapshot(), before)


# Sanitized raster-only PDF: one synthetic Russian invoice row, DejaVu Sans,
# no real counterparty/patient records and no text layer. Generated once with
# Pillow/pypdf; running this regression needs only the existing dependencies.
# Printed 289.21 deliberately differs from quantity * price (241.00).
RUSSIAN_GOODS_PDF_BASE64 = (
    "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQovVGl0bGUgKFN5bnRo"
    "ZXRpYyBSdXNzaWFuIGdvb2RzIHRhYmxlXDA1NCBubyByZWFsIHJlY29yZHMpCj4+CmVuZG9iagoy"
    "IDAgb2JqCjw8Ci9UeXBlIC9QYWdlcwovQ291bnQgMQovS2lkcyBbIDQgMCBSIF0KPj4KZW5kb2Jq"
    "CjMgMCBvYmoKPDwKL1R5cGUgL0NhdGFsb2cKL1BhZ2VzIDIgMCBSCj4+CmVuZG9iago0IDAgb2Jq"
    "Cjw8Ci9UeXBlIC9QYWdlCi9SZXNvdXJjZXMgPDwKL1hPYmplY3QgPDwKL0ltMCA1IDAgUgo+Pgo+"
    "PgovTWVkaWFCb3ggWyAwLjAgMC4wIDU5NSA4NDIgXQovUGFyZW50IDIgMCBSCi9Db250ZW50cyA2"
    "IDAgUgo+PgplbmRvYmoKNSAwIG9iago8PAovVHlwZSAvWE9iamVjdAovU3VidHlwZSAvSW1hZ2UK"
    "L1dpZHRoIDE0MDAKL0hlaWdodCAxOTgwCi9Db2xvclNwYWNlIC9EZXZpY2VHcmF5Ci9CaXRzUGVy"
    "Q29tcG9uZW50IDEKL0ZpbHRlciAvRmxhdGVEZWNvZGUKL0xlbmd0aCA0Mjk5Cj4+CnN0cmVhbQp4"
    "2u3dT28bR5qA8SYYTOdguJObD4IKO3PY6wa+ZABBtcAEyG3nK2QRYHK14QsDEGx6GCxzCNyHXBLA"
    "ML/JhAqNNBYwzG+wbqYD85gmeEgR6XTtW01SJunWv4lpkrvPL0EsS5T4SOquqmbEkrUAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAB4c9rXuE1RtwP5I1O2Lf+GO6zNPXWt3Lr7zFTh+cV13mFrMi+4"
    "Rm7tPLdeXOcdtibR/nVyC9f4SBXqYRH6O8zt2/gauV7hjoBCFXZkbWeHuQM7vkbue4tc+W7Y8qzb"
    "FflSDUyYqczzbK5Hti/nfaaNV7eeb2vW897VieS2lrnZbnO7ZW7fX+SW530/mOcaL/S8W/I5FHf2"
    "K7ddX+Tm7rxv+/PczNP7lhuUuf6Z63C5LTnv/Y4JBzboToyyQa7OJPdUBee53V3ntoLRIvc3iSmC"
    "s3nuL9IXFGpgi6PTY3+eKyODv/Nctcx1516hRvNcCZfcQF5zdLzMPbOJ2nVuU2fnuQObq2SRK28N"
    "yuzj46NP57kDmVj2LFdnr+XePprOczs2CXabe2aaMiy4XK9m+/WVXM/ldn3pPM/1d37sJt6rXFnD"
    "vMo189xAOv+wyHVrhx0PZNlKbr6am5W5f3e5/7XIzeeDwy6nCeM1tV2Ou2rl2M1Cl/u9fE3V3xen"
    "mtE7z7XmVa7rnOcWqjsqT7W+y/1+MZBl4W5zO2WuWsstB7Lz3GQ1VxZkO811C0jTWsldThMutyhz"
    "tbymv5iEz3ac65bnMgn/tppbTsL5Wm6yWOJ07G5HBnfxY8L6ZJl7Wl8scYzuJsblyuGaL3ML3+bl"
    "ZdsuLy1lAVlbjgzeX8oFZDhIwm5WW+RmOnO5ba/lebm3y2nCXbjL8ry+yC28Qbk8l1zbNfVXuUf1"
    "89xdTsLWk5EhzILlmqE+KC9+wkFqu3lQ5rrT0JznWm+XD4tc4+g+rEelRoeVe0bu9hT1w8r1Dys3"
    "sAAAAAAAAAAAYI9luuq1v/68p7n9yv/1MBvuaW5U+dPG7ieE9pL6peq1hd7TXH1QZ5p8GXuRfuKp"
    "TuaN0+EwC89mZpyOU2v8TPcehc/CKCnCD72wqFmvWbd9/5lKQs92Zp3pZBjbb3TfnhkvDzKle/W8"
    "btR4Ok6zLf3PuCK0vUByj2uSOxgO+9qbTceD8UByE9XT+rEO2q1lbsPl9gO5UatmatPxsGvbqq09"
    "48mt3YfJa5kvuYNkq7m9ad6wPZcbHZ/OxmXubBxHT0KXe3wa6qEtPren96d2OB7EkTo9aZjGdCi5"
    "3ei709NZPknHsX6S5+aXscuNh1vLfVLmFmFv3BkOe8/D2XDcGXckNxo+turoyV/1k2XuA5cbj+Wr"
    "flI08mlv6NtoaJ+Eszyd2rHuFc3cTiVXPtD2cv8quTOX+/VwqJ/Zmbww/lpye+Nvbe/kyX+E67nD"
    "qR6e/rn4vHigh/8q3xP72M7y8czGLteUuV9vMffx0FNT40nu3Xjo3bJystwd3y1zH9onJ8+ee8fe"
    "6sHgcr2G93lxTz//THKTozI3DzzVbtbyelJ3H2hrud8sc1/eTQKXG7y8+3Ke27bPm6u5cqqt5qpn"
    "Lrd9nnvsNWuZ5LoPtLWB7NuhOxis7b18kAU6tyZ++eDlAzuR3K/C583R87BwB0Muua1lrv6znRX3"
    "o2djyVXnB8PjVlMO4vH05QMTbG2a+HqZ+5N8qyV3lv40lRenkvtEP83Pc2fFafPBMvdbyX0Q/7fL"
    "1YtceaFoFp9J7k8yIm9tEu64XCO5ieSqPJxNE/lHcqNhT/+UT5/rZW6reT4yfGNNPh2fjWVk0EfL"
    "keFxLrnpWN57uq3cbtx1uXnL9kYSE/3P6Ww2msqLMpBGvV6Sy6fwD5drZnnr8+W4q49aMu5OB2MZ"
    "d4+Py3H3ZzkYGs3808FY3nu8rdx+UOYW3jy3r7yZKXPbNZnVoonktstpIvHuFeXIUM5qp57MahPJ"
    "lVlN3iVfzGpNL6u73G2NDLI8j1yufWh78g0dZvrhLP90mrpcWTPELjdpzXMfFJ+63HLNED6UNYOJ"
    "x7JmOOvJu8iaQeveH5o1c3cs7x299fVyb+b+Oz3/+1jGvNKiZLa42Z6s1Q4yd3wouXMVP/O2z7nB"
    "YeUCAAAAAAAAAAAAAAAAAADg/5fC23/6cHPD8o/eG/hGrf8ov77klvlFb6jOIJdccskll1xyySWX"
    "XHLJJXfPcxOr3O1i8wZyzavfUePuw1zwqx/mucbd8eu5D6/IbW0v94Kdli7LLV7/rTzruc3wzeWu"
    "34fJ9Y1zzaneWa4Jb55rL8/98WR7uZm9ce7kilPtPLeoG9O87RsVp+73fSU6TtPRDX6tyzx3eeiV"
    "uSN7ZvuBCqIoke+i9059/oz589xMR/HqL8ksc409uu0/7Od+de7RIjevmUxykyBOM/lvX7LTM1P/"
    "PbkD+Ws/OJbctm6feO/U8tpabl9FcV+t5aY2b5243Ff3vJabHjXmub+ZMnfQjdPRIO2+iFxuNrph"
    "7tFqbrc40nHkcm+r4LtJfs/cW8vtBlH8IqrO/XVSnXvrXii363s2byTNjz6OY8lM09i6Px9OJjcb"
    "yNZz68Wp5B5JrlLKTvKGMWu5URTFq7/aep57cvLRx19++epEWM/96m96mduX3CiN03iyyK2l5nfl"
    "5qGKoztB9Eg/0i53koerufLG13OLk+ZluV98pucjg9d42vz3ZW6U+ZL7/g1zNw6Gf5nnRt1F7v2K"
    "3MzfzL0jFV++W3jVuU8XubnX+KL5Sdyu9+vxJFF9v1//8f3E+z2598vc96L+be+2y61v5Hp+2+9v"
    "5to7+SfxV3fMFbmm2bj1Ktfvxv168sHvy83K3FqULHK9itzu5sFg3ytzswsOhqQ81SQ3L3MjWx4M"
    "g0iO3eSD9IbTRLGWa5rzXPtIF+Wxe3/jYLBRHMWb00StzJ1clLsYyCT3qHl/mZuWufdvnHuymptL"
    "bhTVz3Ob18p9mN+PH314Ye5imjCmzJVT1Q1kadR1uZPfl3t6qsvcSFk3kDUbGwOZy+1urhk689ys"
    "Ojdb5t5rnDTDePBLOU0MujIyJJPst5vluq/nynr39EiryJeq4IWbJhqNjWni5yju+psrskEexpHJ"
    "ksrcbvbJYmR4p9GU3MQvJ2G//7FkT0ztZuPuPU+t5MrJ1FRRILlu90U51dYmYc/vy6zW/3hzvTvP"
    "Ne9W5kZmkVvcKnMXS5wgiV3uDZc4m7nBWR6WubLECScS66/llkucePNqIi5z8ztv4VptuvxQ17lW"
    "G1dfq3Xf3qXlm8iNDis3OKjc165H9/txhteuR3kUh1xyySWX3JWM3jT3bP+wcld+HGv/c/NCq0PK"
    "zcMXB5V7IKdaEZS55kByjV/mzg4kd/LLQeVOrW3XDyd3fHC5h3QwlLmzQ8s1h5V7KNNEusg9kEm4"
    "HHeneaEOY4lTzmp9Tx/IAjIP5rmHszx3H0pbcsk9xFweZyCXXHL/D+fy1FCeGkouueSSSy655JJL"
    "LrnkkksuueSSSy655JJLLrnkkkvuzXPjfc91T2eT29W2ltuuyvXlDjNtE9/WV3MzVdTckxD64atn"
    "2a3nJmVuvti24s3nFp5+Pdf4codyz4lf1FZz+757EkJoPZVckJuWuaalt5V7qipy47yp48jtHtFY"
    "zY0HxrSOtVVBekHufNehid1a7mvP9XQHQ/xboaJYck0eruS+GE2KU61s2LWX5qZbPNWiqly534pc"
    "O5kUJ2HPvf2q3LbtB9kPyqi2554uHhbBWeEXXhgNUpO3lIpzPx6kma1fmDuxx16Q6HhwvVyVyt1O"
    "Cv1P5Mbu2eX9IPkoyHzJHUyysPAf5jWX25HcpuSaetxNk8K7MNcUp17QVnH32rmJqsqNrpOby7E/"
    "6HTTQaHT7jzXZO6dl7nZr3GQJnkjvCRXR98FcXD9XH8z1zSa18qVF+IodvvMSG4wScKiXpsYl1uX"
    "3IbkpvYHlfZXD7bN3Kbk2vgHtZ6rKnMLLbk/dzZy06KWu9zoWrlRLF/YQv+oJv2w+NP7o7x1Ej5y"
    "ufckd2Cf6rSdFfqi3LzZcrlP9fp9VOfmLtemG7mDolbowD1z/Krc9jw3kg+Q6MnTsPjj+2mZ+yfJ"
    "vS+5sUw26ZeTS3Ibkpv4/XDtPnJdmWtClzvayO2aRus4cJ/ilafaPLddK3O/CIt/+yDxJPcrl/uf"
    "yvPL3Hcz78Lc4sNCR+3N3Ne2+ZrnZtblTjZyAznWvMh9ilfl6pVcNbl1nvvlp5L7l3mu/vHOVblR"
    "3F8/GDJbmTuqyi3cSHEWuU/xitxyDxt3MEj4KJjnui2LlrnuYPhe/XhncvE0UbwvuSr+fv1US6pz"
    "z6py83Jgi2wWXjVN5GG5JdQ8N8qOwuLD+6nb26SdSq5f5g5U8uEluXaeO1jPPavO7Vg1LnNXZzWj"
    "Xa5yn+JVua1XuWk3OznPfehyg3JkiIOkcVluzeV2N8bdTnWub92WOKP1STgL57lnV+RO7InLdeOu"
    "yx243JPJZC03tXE3aZhLcjvz3PVZza/MLYIyN13PTWyWuy1/OlfkmtbJiVLlrPar5KZZMyy0zBLy"
    "0TouV81ntTiRV4SX5ga+fMarK7KgMleW3zIrveisLyDPrAxkMiL7F+a6Xabc8rxVO5JBU9YMdckd"
    "zXNzL/FUJ028EzVfMwwSU7FT43lu1w1kH68vcQqvYgFpvBO3Ao/7m8vzWu7JokSGocS7LFeuRfwj"
    "G7kVmS+5WZa73KImuV2Xq+crstHI2IuXOPIt1tFZHI+ul5vpOFPrFz/9mvy9flnu5ZeW4/nb9PXW"
    "u90bXlrGF1xaqn/2SvhmudENc4cX5Oq3kxvcMDeqzi3eUq66YW7vgtzwrTwsYm6au9tHcVbWXjxG"
    "Ri655JJLLrnkkksuueSSSy655JJLLrlbyWXDgzcttAAAAAAAAAAAAAAAbI15bY+Juaq9RfYht6jO"
    "rdpbZI9zq/YW2YfcvDq3am+RPc6t2qxjH3JN4NWMSupGHUZu5NUSP6knfkVutHe5WZSHg0FqB4ON"
    "3Nf3FtmH3JHkxmlq43T9DRV7i+xDbiq50Si10Wj9DRV7i+xD7sDlTiR3Y/+Eir1F9iE3viC3Ym+R"
    "fcjtVudW7S2yD7lRdW7V3iL7kBtU51btLbIPuUpyuzIydNdHhqq9RfYlt2LcrdpbZB9ydfWsVrW3"
    "yL7kJr7b4Wt9eV6xt8g+5IaSa1RqN1ZkVXuL7D9lD4o+qNriwHJDHtoAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAgGv7X1knQssKZW5kc3RyZWFtCmVuZG9iago2IDAgb2JqCjw8Ci9MZW5n"
    "dGggMzEKPj4Kc3RyZWFtCnEgNTk1IDAgMCA4NDIgMCAwIGNtIC9JbTAgRG8gUQoKZW5kc3RyZWFt"
    "CmVuZG9iagp4cmVmCjAgNwowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMTUgMDAwMDAgbiAK"
    "MDAwMDAwMDExMyAwMDAwMCBuIAowMDAwMDAwMTcyIDAwMDAwIG4gCjAwMDAwMDAyMjEgMDAwMDAg"
    "biAKMDAwMDAwMDM1NyAwMDAwMCBuIAowMDAwMDA0ODI5IDAwMDAwIG4gCnRyYWlsZXIKPDwKL1Np"
    "emUgNwovUm9vdCAzIDAgUgovSW5mbyAxIDAgUgo+PgpzdGFydHhyZWYKNDkxMAolJUVPRgo="
)


class RealRussianGoodsOCRTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("AZ_TEST_REAL_OCR") == "1",
                         "Real Russian OCR is required in the dedicated CI step.")
    def test_russian_raster_invoice_through_poppler_tesseract_and_parser(self):
        tesseract = shutil.which("tesseract", path="/usr/bin:/bin")
        self.assertIsNotNone(tesseract, "Install the existing local Tesseract dependency.")
        language_list = subprocess.run([tesseract, "--list-langs"],
            capture_output=True, text=True, check=True, timeout=5)
        languages = set(language_list.stdout.splitlines())
        self.assertIn("rus", languages,
                      "AZ_TEST_REAL_OCR requires Russian traineddata; English fallback is not a pass.")
        self.assertIn("eng", languages)
        raw = base64.b64decode(RUSSIAN_GOODS_PDF_BASE64, validate=True)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
            "693af342a2d3b99c467917c1cbe20d6e95d6f2460b41eb285f88dc2eb8b62a20")
        self.assertEqual(PdfReader(io.BytesIO(raw)).pages[0].extract_text(), "")
        with tempfile.TemporaryDirectory() as directory:
            count, parsed = ocr.recognize_pdf_page(raw, 1, Path(directory))
        self.assertEqual(count, 1)
        self.assertTrue(parsed["words"])
        self.assertEqual(len(parsed["candidates"]), 1, parsed["lines"])
        row = parsed["candidates"][0]
        self.assertEqual(row["name"], "Салфетки медицинские")
        self.assertEqual((row["quantity"], row["unit"].casefold()), ("2", "упак"))
        self.assertEqual(row["price"], "120.50")
        self.assertEqual(row["value"], "289.21")
        self.assertEqual(row["action"], "unresolved")


if __name__ == "__main__":
    unittest.main()
