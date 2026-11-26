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
        parsed["header_candidate"].update(document_type="unknown", number="", date=None,
            issues=["Дата не подтверждена двумя чтениями оригинала."])
        return 2, parsed

    def failed_with_stale_form():
        page.wait_for_function(
            "message => document.getElementById('ocr-progress').textContent.includes(message) "
            "&& !document.getElementById('ocr-start').disabled", arg=FAILURE)
        assert page.locator('#ocr-review [name="value"]').input_value() == "120.00"
        assert page.locator('#ocr-review .ocr-unit-cost').input_value() == "60.00"
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
        assert row.locator('[name="value"]').is_hidden()
        assert row.locator('.ocr-unit-cost').input_value() == "144.60"
        assert row.locator('.ocr-unit-cost').evaluate("input => input.readOnly && !input.name")
        assert row.locator('[name="folder_id"]').input_value() == str(folder_id)
        assert row.locator('[name="action"]').input_value() == "new"
        assert page.locator('#ocr-review [name="version"]').input_value() == "3"
        assert page.get_by_role("button", name="Сохранить разбор", exact=True).is_enabled()
        assert page.get_by_text("Сумма строк совпадает с итогом документа: 289.20 ₽.", exact=True).is_visible()
        assert page.locator('#ocr-header, [name="document_type"], [name="counterparty"], [name="number"], [name="document_date"], [name="part"], [name="page_note"]').count() == 0
        assert page.locator('[name="review_scope"]').input_value() == "goods"
        assert row.locator('.ocr-control-cell .ocr-supplier').inner_text() == "Учебный поставщик"
        assert row.locator('.ocr-supplier').evaluate("el => parseFloat(getComputedStyle(el).fontSize)") <= 11
        assert not page.locator('[name="checked"]').is_checked()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        checks = page.locator("#ocr-checks")
        assert checks.count() == 1
        assert checks.get_attribute("open") is None
        checks.locator("summary").click()
        assert checks.locator("p").first.is_visible()
        assert "Дата не подтверждена" not in checks.inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, "checks")
        checks.locator("summary").click()
        # The displayed amount is value / quantity, not the supplier's printed
        # tax-exclusive price. Editing either source recomputes with decimal
        # HALF_UP at four places without rounding the stored line amount.
        row.locator('[name="quantity"]').fill("3")
        assert row.locator('.ocr-unit-cost').input_value() == "96.40"
        assert row.locator('[name="value"]').input_value() == "289.20"
        row.get_by_role("button", name="Ещё", exact=True).click()
        assert row.locator('[name="value"]').is_visible()
        assert row.locator('[name="price"]').is_hidden()
        for quantity, amount, expected in (("6", "1.00", "0.1667"),
                                           ("200", "0.01", "0.0001"),
                                           ("0,5", "1 234,50", "2469.00"),
                                           ("2", "0", "0.00"),
                                           ("0", "5.00", ""),
                                           ("", "5.00", ""),
                                           ("1 2", "5.00", ""),
                                           ("2", "0.001", "")):
            row.locator('[name="quantity"]').fill(quantity)
            row.locator('[name="value"]').fill(amount)
            assert row.locator('.ocr-unit-cost').input_value() == expected, (quantity, amount, expected)
            assert row.locator('[name="value"]').input_value() == amount
        row.locator('[name="quantity"]').fill("2")
        row.locator('[name="value"]').fill("289.20")
        for unit in ("", "—", "–", "-", "?"):
            row.locator('[name="unit"]').fill(unit)
            assert row.locator('.ocr-unit-cost').input_value() == ""
        row.locator('[name="unit"]').fill("упак")
        assert row.locator('.ocr-unit-cost').input_value() == "144.60"
        assert row.locator('[name="price"]').input_value() == "120.50"
        row.get_by_role("button", name="Ещё", exact=True).click()
        page.locator('[name="checked"]').check()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, "goods checked")
        page.screenshot(path=str(ARTIFACTS / f"ocr-automatic-{width}.png"), full_page=True)
        with page.expect_navigation():
            page.get_by_role("button", name="Сохранить разбор", exact=True).click()
        saved = state(fixture)["pages"]["1"]
        assert saved["rows"][0]["value"] == "289.20"
        assert saved["rows"][0]["folder_id"] == folder_id
        assert saved.get("header", {}).get("number", "") == ""
        assert saved["checked"]
        assert page.locator('.ocr-unit-cost').input_value() == "144.60"
        assert "Дата не подтверждена" not in page.locator('body').inner_text()
        assert fixture.get_doc()["version"] == 4
        assert state(fixture)["pages"]["2"] == old["pages"]["2"]
        assert bytes(fixture.get_doc()["original_bytes"]) == original
        assert OCRRouteTests.snapshot_stock(fixture) == stock
        assert not errors, errors
        print("MED_OCR_AUTOMATIC_BROWSER", width, "STALE_FORM_RETRY_UNIT_COST_GOODS_ONLY_SAVE_PASS", flush=True)
    finally:
        context.close()
        server.shutdown()
        fixture.doCleanups()


def check_unknown_amount_roundtrip(browser):
    """Only an explicit gross-value edit can resolve an unknown amount basis.

    Removing an earlier manual row exercises stable row association when both
    rows lack source_line; changing name/quantity must not promote the amount.
    """
    fixture = IncomingDocumentTests()
    fixture.setUp()
    fixture.app.config["SESSION_COOKIE_SECURE"] = False
    fixture.upload(filename="Учебная стоимость.pdf")
    folder_id = fixture.conn.execute("SELECT id FROM med_supply_folders WHERE name='СИЗ'").fetchone()[0]
    data = parsed_page(1, "289,20")
    base = dict(data["candidates"][0], source_line=None, action="new",
                folder_id=folder_id, item_id=None, note="", manual=True)
    data["rows"] = [dict(base, name="Учебный комплект А", value_basis="printed_gross"),
                    dict(base, name="Учебный комплект Б", value_basis="")]
    data["checked"] = False
    fixture.conn.execute("UPDATE med_supply_documents SET fields_json=?,version=2 WHERE id=1",
                         (json.dumps({ocr.KEY: {"page_count": 1, "pages": {"1": data}}}, ensure_ascii=False),))
    fixture.conn.commit()
    original = bytes(fixture.get_doc()["original_bytes"])
    stock = OCRRouteTests.snapshot_stock(fixture)
    server = make_server("127.0.0.1", 0, fixture.app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    context = browser.new_context(viewport={"width": 1365, "height": 1000})
    client = fixture.client(2)
    context.add_cookies([{"name": "session", "value": client.get_cookie("session").value, "url": origin}])
    page = context.new_page()
    page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
    page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(origin + PATH)
        rows = page.locator("#ocr-rows .ocr-row")
        assert rows.nth(0).locator('.ocr-unit-cost').input_value() == "144.60"
        assert rows.nth(1).locator('.ocr-unit-cost').input_value() == ""
        rows.nth(0).get_by_role("button", name="Убрать строку", exact=True).click()
        row = rows.first
        assert row.locator('[name="row_index"]').input_value() == "1"
        assert row.locator('[name="source_line"]').input_value() == ""
        row.locator('[name="name"]').fill("Учебный комплект Б — уточнённое название")
        row.locator('[name="quantity"]').fill("3")
        row.locator('[name="unit"]').fill("упаковка")
        assert row.locator('[name="value_edited"]').input_value() == "0"
        assert row.locator('.ocr-unit-cost').input_value() == ""
        with page.expect_navigation():
            page.get_by_role("button", name="Сохранить разбор", exact=True).click()
        saved = state(fixture)["pages"]["1"]["rows"]
        assert len(saved) == 1
        assert saved[0]["name"] == "Учебный комплект Б — уточнённое название"
        assert saved[0]["value_basis"] == ""
        assert saved[0]["unit_cost"] is None
        assert saved[0]["value"] == "289.20"
        row = page.locator("#ocr-rows .ocr-row").first
        assert row.locator('.ocr-unit-cost').input_value() == ""
        assert row.locator('[name="row_index"]').input_value() == "0"
        assert row.locator('[name="value_edited"]').input_value() == "0"
        row.get_by_role("button", name="Ещё", exact=True).click()
        # Re-entering the same amount is an explicit assertion that this is the
        # gross total, unlike merely saving the initially displayed value.
        row.locator('[name="value"]').fill("")
        row.locator('[name="value"]').fill("289.20")
        assert row.locator('[name="value_edited"]').input_value() == "1"
        assert row.locator('.ocr-unit-cost').input_value() == "96.40"
        row.get_by_role("button", name="Ещё", exact=True).click()
        page.locator('[name="checked"]').check()
        with page.expect_navigation():
            page.get_by_role("button", name="Сохранить разбор", exact=True).click()
        assert state(fixture)["pages"]["1"]["rows"][0]["unit_cost"] == "96.40"
        assert state(fixture)["pages"]["1"]["checked"]
        assert page.locator('.ocr-unit-cost').input_value() == "96.40"
        assert bytes(fixture.get_doc()["original_bytes"]) == original
        assert OCRRouteTests.snapshot_stock(fixture) == stock
        assert not errors, errors
        print("MED_OCR_AMOUNT_BASIS_BROWSER UNKNOWN_SAVE_STABLE_ROW_EXPLICIT_EDIT_PASS", flush=True)
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
            check_unknown_amount_roundtrip(browser)
        finally:
            browser.close()
