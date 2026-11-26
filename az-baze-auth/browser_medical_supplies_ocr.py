"""Real Flask/browser goods-table flow using synthetic OCR geometry only."""
import copy
import json
import os
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from test_medical_supplies_documents import IncomingDocumentTests
from test_medical_supplies_ocr_table import simple_table
import medical_supplies_ocr as ocr

ARTIFACTS = Path(os.environ.get("MED_BROWSER_ARTIFACTS", "med-browser-artifacts"))
ARTIFACTS.mkdir(exist_ok=True)
PATH = "/accounting/medical-supplies/incoming/1/ocr"


def state(fixture):
    return json.loads(fixture.get_doc()["fields_json"])[ocr.KEY]


def new_page(browser, fixture, origin, width, uid=2):
    client = fixture.client(uid)
    page = browser.new_page(viewport={"width": width, "height": 1000})
    page.context.add_cookies([{"name": "session", "value": client.get_cookie("session").value, "url": origin}])
    page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
    page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
    return page


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None,
                                headless=True, args=["--no-sandbox"])
    for width in (1818, 1365, 768, 430, 390, 360, 320):
        fixture = IncomingDocumentTests()
        fixture.setUp()
        fixture.app.config["SESSION_COOKIE_SECURE"] = False
        fixture.upload(filename="Учебный документ.pdf", counterparty="Учебный поставщик")
        server = make_server("127.0.0.1", 0, fixture.app, threaded=True)
        Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            page = new_page(browser, fixture, origin, width)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            parsed = ocr.parse_tsv(simple_table(wrapped=True))
            original = bytes(fixture.get_doc()["original_bytes"])
            with patch.object(ocr, "recognize_pdf_page", return_value=(2, parsed)) as engine:
                page.goto(origin + PATH + "?start=1")
                assert engine.call_count == 0
                assert fixture.get_doc()["version"] == 1
                page.goto(origin + "/accounting/medical-supplies/incoming/?id=1")
                page.get_by_role("link", name="Разобрать документ", exact=True).click()
                page.wait_for_selector("#ocr-rows .ocr-row")
                assert engine.call_count == 2
                assert "2 / 2" in page.locator("#ocr-progress").inner_text()
                page.reload()
                assert engine.call_count == 2
            assert page.locator("#ocr-rows").evaluate("element => element.tagName") == "TABLE"
            assert page.locator("#ocr-rows th").all_text_contents()[:5] == [
                "Название", "Количество", "Единица", "Стоимость, ₽", "В какой раздел"]
            assert not page.locator(".ocr-source").evaluate("element => element.open")
            assert not page.locator("#ocr-header").evaluate("element => element.open")
            assert not page.locator("#ocr-show-saved").is_visible()
            first = page.locator("#ocr-rows .ocr-row").first
            assert first.locator('[name="name"]').input_value() == "Салфетки медицинские стерильные"
            assert first.locator('[name="quantity"]').input_value() == "2"
            assert first.locator('[name="unit"]').input_value() == "упак"
            assert first.locator('[name="value"]').input_value() == "289.20"
            folder = first.locator('[name="folder_id"]')
            assert folder.evaluate("element => element.tagName") == "SELECT"
            folder_value = folder.locator('option[value]:not([value=""])').first.get_attribute("value")
            assert folder_value
            folder.select_option(folder_value)
            assert first.locator('[name="action"]').input_value() == "new"
            first.locator('[name="quantity"]').fill("3")
            assert first.locator('[name="value"]').input_value() == "289.20"
            page.get_by_role("button", name="Обновить разбор", exact=True).click()
            assert "Сначала сохраните" in page.locator("#ocr-progress").inner_text()
            page.get_by_role("button", name="+ Добавить строку", exact=True).click()
            added = page.locator("#ocr-rows .ocr-row").last
            assert page.locator("#ocr-rows .ocr-row").count() == 2
            for key, value in (("name", "Бумага для принтера A4"), ("quantity", "1"), ("unit", "пач"), ("value", "456,78")):
                added.locator(f'[name="{key}"]').fill(value)
            added.locator('[name="folder_id"]').select_option(folder_value)
            with page.expect_navigation():
                page.get_by_role("button", name="Сохранить разбор", exact=True).click()
            saved = state(fixture)["pages"]["1"]["rows"]
            assert [(row["name"], row["quantity"], row["value"], row["folder_id"]) for row in saved] == [
                ("Салфетки медицинские стерильные", "3", "289.20", int(folder_value)),
                ("Бумага для принтера A4", "1", "456.78", int(folder_value))]
            assert saved[0]["source_line"] is not None and saved[1]["source_line"] is None
            assert saved[0]["action"] == saved[1]["action"] == "new"
            assert bytes(fixture.get_doc()["original_bytes"]) == original
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            if width > 850:
                assert page.locator("#ocr-rows").bounding_box()["y"] < 600
            page.screenshot(path=str(ARTIFACTS / f"ocr-goods-table-{width}.png"), full_page=True)
            first = page.locator("#ocr-rows .ocr-row").first
            first.get_by_role("button", name="Ещё", exact=True).click()
            assert first.locator('[name="item_id"]').is_visible()
            assert first.locator('[name="price"]').input_value() == "120.50"
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, "details")
            first.get_by_role("button", name="Ещё", exact=True).click()
            page.locator("#ocr-rows .ocr-row").last.get_by_role("button", name="Убрать строку", exact=True).click()
            with page.expect_navigation():
                page.get_by_role("button", name="Сохранить разбор", exact=True).click()
            assert len(state(fixture)["pages"]["1"]["rows"]) == 1
            assert not errors, errors
            page.close()
            reader = new_page(browser, fixture, origin, width, uid=3)
            reader.goto(origin + PATH)
            assert reader.get_by_role("table", name="Товары из документа").count() == 1
            assert reader.locator("#ocr-start, #ocr-review").count() == 0
            assert reader.evaluate("document.documentElement.scrollWidth <= innerWidth")
            reader.close()
            print("MED_OCR_GOODS_TABLE_BROWSER", width, "PASS", flush=True)
        finally:
            server.shutdown()
            fixture.doCleanups()
    fixture = IncomingDocumentTests()
    fixture.setUp()
    fixture.app.config["SESSION_COOKIE_SECURE"] = False
    fixture.upload(filename="Учебный сохранённый документ.pdf")
    parsed = ocr.parse_tsv(simple_table())
    legacy = {key: copy.deepcopy(parsed[key]) for key in ("words", "lines", "confidence")}
    legacy["candidates"] = []
    fixture.conn.execute("UPDATE med_supply_documents SET fields_json=?,version=2 WHERE id=1",
                         (json.dumps({ocr.KEY: {"page_count": 1, "pages": {"1": legacy}}}),))
    fixture.conn.commit()
    before = fixture.get_doc()["fields_json"]
    server = make_server("127.0.0.1", 0, fixture.app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        page = new_page(browser, fixture, origin, 1365)
        with patch.object(ocr, "recognize_pdf_page", side_effect=AssertionError("legacy view must not re-OCR")):
            page.goto(origin + "/accounting/medical-supplies/incoming/?id=1")
            page.get_by_role("link", name="Разобрать документ", exact=True).click()
            page.wait_for_selector("#ocr-rows .ocr-row")
            assert page.locator('[name="value"]').input_value() == "289.20"
        assert fixture.get_doc()["fields_json"] == before
        assert fixture.get_doc()["version"] == 2
        page.close()
        print("MED_OCR_STORED_GOODS_IMMEDIATE PASS", flush=True)
    finally:
        server.shutdown()
        fixture.doCleanups()
    browser.close()
