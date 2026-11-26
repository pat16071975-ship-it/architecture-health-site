"""Synthetic browser regression for partially successful automatic rereading.

Uses real Flask routes and SQLite, with only the image OCR engine replaced.
Never reads a private document or connects to a deployed application.
"""
import json
import os
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

import medical_supplies_ocr as ocr
from test_medical_supplies_documents import IncomingDocumentTests
from test_medical_supplies_ocr import OCRRouteTests
from test_medical_supplies_ocr_table import simple_table


PATH = "/accounting/medical-supplies/incoming/1/ocr"
FAILURE = "Учебная ошибка второй страницы"
ARTIFACTS = Path(os.environ.get("MED_BROWSER_ARTIFACTS", "med-browser-artifacts"))


def parsed_page(page, total):
    data = ocr.parse_tsv(simple_table(total=total, qualifier="с НДС").replace("Салфетки", "Бахилы"))
    data["printed_total"] = total.replace(",", ".")
    data["total_basis"] = "printed_gross"
    data["header_candidate"] = {
        "document_type": "act", "counterparty": "Учебный поставщик",
        "number": "TRAINING-61", "date": "2026-10-11", "part": 1,
        "note": "", "issues": [], "pagination": {"page": page, "of": 2},
    }
    return data


def state(fixture):
    return json.loads(fixture.get_doc()["fields_json"])[ocr.KEY]


def check_partial_reread(browser, width):
    fixture = IncomingDocumentTests()
    fixture.setUp()
    fixture.app.config["SESSION_COOKIE_SECURE"] = False
    fixture.upload(filename="Учебный двухстраничный документ.pdf", counterparty="Учебный поставщик")
    old = {"page_count": 2, "pages": {"1": parsed_page(1, "120,00"), "2": parsed_page(2, "120,00")}}
    fixture.conn.execute("UPDATE med_supply_documents SET fields_json=?,version=2 WHERE id=1",
                         (json.dumps({ocr.KEY: old}, ensure_ascii=False),))
    fixture.conn.commit()
    original = bytes(fixture.get_doc()["original_bytes"])
    stock = OCRRouteTests.snapshot_stock(fixture)
    folder_id = fixture.conn.execute("SELECT id FROM med_supply_folders WHERE name='СИЗ'").fetchone()[0]
    server = make_server("127.0.0.1", 0, fixture.app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    context = browser.new_context(viewport={"width": width, "height": 1000})
    client = fixture.client(2)
    context.add_cookies([{"name": "session", "value": client.get_cookie("session").value, "url": origin}])
    page = context.new_page()
    page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
    page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    calls = []

    def engine(raw, page_number, lock_dir):
        assert raw == original
        calls.append(page_number)
        if page_number == 2:
            raise ocr.OCRUnavailable(FAILURE)
        parsed = parsed_page(1, "289,20")
        parsed["recognition_issues"] = ["Нечётко распознан артикул первой позиции."]
        return 2, parsed

    def failed_with_stale_form():
        page.wait_for_function(
            "message => document.getElementById('ocr-progress').textContent.includes(message) "
            "&& !document.getElementById('ocr-start').disabled", arg=FAILURE)
        assert page.locator('#ocr-review [name="value"]').input_value() == "120.00"
        assert page.locator('#ocr-review [name="version"]').input_value() == "2"
        assert page.locator("#ocr-start").get_attribute("data-version") == "3"
        assert page.get_by_role("button", name="Сохранить разбор", exact=True).is_disabled()
        assert page.locator("#ocr-review").evaluate("form => Array.from(form.elements).every(el => el.disabled)")
        assert page.get_by_role("link", name="Показать сохранённые строки", exact=True).is_visible()
        assert fixture.get_doc()["version"] == 3
        assert "rows" not in state(fixture)["pages"]["1"]
        assert state(fixture)["pages"]["1"]["candidates"][0]["value"] == "289.20"

    try:
        with patch.object(ocr, "recognize_pdf_page", side_effect=engine):
            page.goto(origin + PATH)
            assert calls == []
            assert page.locator('#ocr-review [name="value"]').input_value() == "120.00"
            page.get_by_role("button", name="Обновить разбор", exact=True).click()
            failed_with_stale_form()
            assert calls == [1, 2]
            saved_after_first_failure = fixture.get_doc()["fields_json"]
            # A retry first visits the cached upgraded page, then fails again.
            # It must not enable fields from the original, now obsolete HTML.
            page.get_by_role("button", name="Обновить разбор", exact=True).click()
            failed_with_stale_form()
            assert calls == [1, 2, 2]
            assert fixture.get_doc()["fields_json"] == saved_after_first_failure
            with page.expect_navigation():
                page.get_by_role("link", name="Показать сохранённые строки", exact=True).click()
            assert calls == [1, 2, 2]

        row = page.locator("#ocr-rows .ocr-row").first
        assert row.locator('[name="name"]').input_value() == "Бахилы"
        assert row.locator('[name="quantity"]').input_value() == "2"
        assert row.locator('[name="unit"]').input_value() == "упак"
        assert row.locator('[name="value"]').input_value() == "289.20"
        assert row.locator('[name="folder_id"]').input_value() == str(folder_id)
        assert row.locator('[name="action"]').input_value() == "new"
        assert page.locator('#ocr-review [name="version"]').input_value() == "3"
        assert page.get_by_role("button", name="Сохранить разбор", exact=True).is_enabled()
        assert page.get_by_text("Сумма строк совпадает с итогом документа: 289.20 ₽.", exact=True).is_visible()
        assert page.locator('[name="document_type"]').input_value() == "act"
        assert page.locator('[name="counterparty"]').input_value() == "Учебный поставщик"
        assert page.locator('[name="number"]').input_value() == "TRAINING-61"
        assert page.locator('[name="document_date"]').input_value() == "2026-10-11"
        assert not page.locator('[name="checked"]').is_checked()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        checks = page.locator("#ocr-checks")
        assert checks.count() == 1
        assert checks.get_attribute("open") is None
        checks.locator("summary").click()
        assert checks.locator("p").first.is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, "checks")
        checks.locator("summary").click()
        page.locator("#ocr-header summary").click()
        assert page.locator('[name="counterparty"]').is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, "header")
        page.screenshot(path=str(ARTIFACTS / f"ocr-automatic-{width}.png"), full_page=True)
        with page.expect_navigation():
            page.get_by_role("button", name="Сохранить разбор", exact=True).click()
        saved = state(fixture)["pages"]["1"]
        assert saved["rows"][0]["value"] == "289.20"
        assert saved["rows"][0]["folder_id"] == folder_id
        assert saved["header"]["number"] == "TRAINING-61"
        assert not saved["checked"]
        assert fixture.get_doc()["version"] == 4
        assert state(fixture)["pages"]["2"] == old["pages"]["2"]
        assert bytes(fixture.get_doc()["original_bytes"]) == original
        assert OCRRouteTests.snapshot_stock(fixture) == stock
        assert not errors, errors
        print("MED_OCR_AUTOMATIC_BROWSER", width, "STALE_FORM_RETRY_AUTOFILL_SAVE_PASS", flush=True)
    finally:
        context.close()
        server.shutdown()
        fixture.doCleanups()


if __name__ == "__main__":
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_PATH") or None,
            headless=True, args=["--no-sandbox"])
        try:
            for viewport in (320, 390, 1365):
                check_partial_reread(browser, viewport)
        finally:
            browser.close()
