"""Synthetic browser acceptance for choosing a new OCR result explicitly.

Uses local Flask/SQLite and a fake image reader. No private source, deployed
application, external browser session or real OCR service is accessed.
"""
import copy
import json
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

import medical_supplies_ocr as ocr
from test_medical_supplies_documents import IncomingDocumentTests
from test_medical_supplies_ocr import OCRRouteTests
from test_medical_supplies_ocr_table import simple_table


PATH = "/accounting/medical-supplies/incoming/1/ocr"
ARTIFACTS = Path(os.environ.get("MED_BROWSER_ARTIFACTS", "med-browser-artifacts"))
OLD_NAME = "Сохранённое вручную название учебного материала"
LONG_NAME = (
    "Бахилы медицинские одноразовые из нетканого материала, с двойной резинкой, "
    "для использования в процедурных и диагностических кабинетах; упаковка "
    "повышенной прочности с полным наименованием товара для сверки по оригиналу. "
    "Дополнительная характеристика учебного изделия должна оставаться видимой "
    "целиком на узком и широком экране, без обрезки окончания названия."
)
SUPPLIER = "Учебный поставщик сохранённого документа"
FAILURE = "Учебная ошибка второй страницы"


def state(fixture):
    return json.loads(fixture.get_doc()["fields_json"])[ocr.KEY]


def saved_snapshot(fixture):
    document = fixture.get_doc()
    events = fixture.conn.execute("SELECT COUNT(*) FROM med_supply_document_events").fetchone()[0]
    return document["fields_json"], document["version"], events


def parsed_page(folder_id, *, saved=False, page_number=1):
    data = ocr.parse_tsv(simple_table(total="298,00", qualifier="с НДС"))
    candidate = dict(data["candidates"][0], name=LONG_NAME, quantity="4", unit="упак",
                     value="298.00", value_basis="printed_gross", action="new",
                     folder_id=folder_id, item_id=None, note="")
    data["candidates"] = [candidate]
    data["printed_total"] = "298.00"
    data["total_basis"] = "printed_gross"
    data["header_candidate"] = {"document_type": "act", "counterparty": SUPPLIER,
        "number": "TRAINING-81", "date": "2026-10-12", "part": page_number,
        "note": "", "issues": [], "pagination": {"page": 1, "of": 1}}
    if saved:
        data["rows"] = [dict(candidate, name=OLD_NAME, quantity="2", value="111.00",
                             note="Ручное примечание владельца")]
        data["header"] = copy.deepcopy(data["header_candidate"])
        data["checked"] = True
        data["review_scope"] = "goods"
    return data


@contextmanager
def local_session(browser, width):
    fixture = IncomingDocumentTests()
    fixture.setUp()
    fixture.app.config["SESSION_COOKIE_SECURE"] = False
    fixture.upload(filename="Учебный документ для повторного разбора.pdf")
    folder_id = fixture.conn.execute("SELECT id FROM med_supply_folders WHERE name='СИЗ'").fetchone()[0]
    old = {"page_count": 2, "pages": {
        "1": parsed_page(folder_id, saved=True),
        "2": parsed_page(folder_id, saved=True, page_number=2),
    }}
    fixture.conn.execute("UPDATE med_supply_documents SET fields_json=?,version=2 WHERE id=1",
                         (json.dumps({ocr.KEY: old}, ensure_ascii=False),))
    fixture.conn.commit()
    server = make_server("127.0.0.1", 0, fixture.app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    context = browser.new_context(viewport={"width": width, "height": 1100})
    client = fixture.client(2)
    context.add_cookies([{"name": "session", "value": client.get_cookie("session").value, "url": origin}])
    page = context.new_page()
    page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
    page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        yield SimpleNamespace(fixture=fixture, old=old, folder_id=folder_id, server=server,
            origin=origin, context=context, page=page, errors=errors,
            original=bytes(fixture.get_doc()["original_bytes"]),
            stock=OCRRouteTests.snapshot_stock(fixture))
    finally:
        context.close()
        server.shutdown()
        fixture.doCleanups()


def assert_no_overflow(page, width, stage):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, stage)


def assert_preview(page, width, *, can_apply=True):
    assert page.get_by_role("heading", name="Новый разбор · страница 1", exact=True).is_visible()
    assert page.locator("#ocr-review").count() == 0
    table = page.get_by_role("table", name="Товары из документа", exact=True)
    assert table.locator("input, textarea, select").count() == 0
    row = table.locator(".ocr-row").first
    assert row.locator('.ocr-name-cell').inner_text() == LONG_NAME
    assert row.locator('.ocr-name-cell').evaluate("el => el.scrollHeight <= el.clientHeight + 1")
    assert row.locator('[data-label="Количество"]').inner_text() == "4"
    assert row.locator('[data-label="Единица"]').inner_text() == "упак"
    assert row.locator('[data-label="Стоимость за единицу, ₽"]').inner_text() == "74.50"
    assert "СИЗ" in row.locator('.ocr-section-cell').inner_text()
    assert row.locator('.ocr-supplier').inner_text() == SUPPLIER
    assert page.locator('[data-ocr-view="automatic"]').get_attribute("aria-current") == "page"
    assert page.get_by_role("button", name="Использовать для этой страницы", exact=True).count() == int(can_apply)
    assert_no_overflow(page, width, "preview")


def check_preview_and_explicit_apply(browser, width):
    with local_session(browser, width) as session:
        page, fixture = session.page, session.fixture
        calls = []
        posts = []
        page.on("request", lambda request: posts.append(request.url) if request.method == "POST" else None)

        def engine(raw, page_number, lock_dir):
            assert raw == session.original
            calls.append(page_number)
            return 2, parsed_page(session.folder_id, page_number=page_number)

        with patch.object(ocr, "recognize_pdf_page", side_effect=engine):
            before_get = saved_snapshot(fixture)
            page.goto(session.origin + PATH + "?p=1")
            assert saved_snapshot(fixture) == before_get
            assert page.locator('#ocr-review [name="name"]').input_value() == OLD_NAME
            assert page.locator('[data-ocr-view]').count() == 0
            # A new OCR run must not discard unsaved manual edits.
            page.locator('#ocr-review [name="name"]').fill(OLD_NAME + " — ещё не сохранено")
            page.get_by_role("button", name="Обновить разбор", exact=True).click()
            assert page.locator('#ocr-progress').inner_text() == "Сначала сохраните изменения в таблице."
            assert calls == []
            assert saved_snapshot(fixture) == before_get
            page.reload()
            with page.expect_navigation():
                page.get_by_role("button", name="Обновить разбор", exact=True).click()
            assert calls == [1, 2]
            assert "view=automatic" in page.url
            assert_preview(page, width)
            pending = state(fixture)
            assert pending["pages"]["1"]["rows"] == session.old["pages"]["1"]["rows"]
            assert pending["pages"]["1"]["checked"]
            assert pending["pages"]["1"]["automatic"]["candidates"][0]["name"] == LONG_NAME
            before_switch = saved_snapshot(fixture)
            page.screenshot(path=str(ARTIFACTS / f"ocr-reread-preview-{width}.png"), full_page=True)
            with page.expect_navigation():
                page.get_by_role("link", name="Сохранённый разбор", exact=True).click()
            assert saved_snapshot(fixture) == before_switch
            assert page.locator('#ocr-review [name="name"]').input_value() == OLD_NAME
            assert page.locator('#ocr-review [name="quantity"]').input_value() == "2"
            assert page.locator('#ocr-review .ocr-unit-cost').input_value() == "55.50"
            assert page.locator('#ocr-review [name="checked"]').is_checked()
            page.locator('#ocr-review [name="quantity"]').fill("3")
            page.get_by_role("link", name="Новый разбор", exact=True).click()
            assert "view=automatic" not in page.url
            assert page.locator('#ocr-progress').inner_text() == "Сначала сохраните изменения в таблице."
            assert page.locator('#ocr-review [name="quantity"]').input_value() == "3"
            assert saved_snapshot(fixture) == before_switch
            page.reload()
            with page.expect_navigation():
                page.get_by_role("link", name="Новый разбор", exact=True).click()
            assert saved_snapshot(fixture) == before_switch
            assert_preview(page, width)
            # No switch, GET or OCR refresh has applied the proposal.
            assert not any(url.endswith("/automatic/apply") for url in posts)
            other_page = copy.deepcopy(state(fixture)["pages"]["2"])
            with page.expect_navigation():
                page.get_by_role("button", name="Использовать для этой страницы", exact=True).click()
            assert sum(url.endswith("/automatic/apply") for url in posts) == 1
            assert page.locator('#ocr-review [name="name"]').input_value() == LONG_NAME
            assert page.locator('#ocr-review [name="quantity"]').input_value() == "4"
            assert page.locator('#ocr-review .ocr-unit-cost').input_value() == "74.50"
            assert not page.locator('#ocr-review [name="checked"]').is_checked()
            assert "Прежняя версия сохранена в истории." in page.locator('body').inner_text()
            assert state(fixture)["pages"]["2"] == other_page
            assert page.locator('#ocr-review [name="name"]').evaluate("el => el.scrollHeight <= el.clientHeight + 1")
            assert_no_overflow(page, width, "applied")
            page.screenshot(path=str(ARTIFACTS / f"ocr-reread-applied-{width}.png"), full_page=True)
        assert bytes(fixture.get_doc()["original_bytes"]) == session.original
        assert OCRRouteTests.snapshot_stock(fixture) == session.stock
        assert not session.errors, session.errors
        print("MED_OCR_REREAD_BROWSER", width, "SAVED_PREVIEW_EXPLICIT_APPLY_FULL_NAMES_DIRTY_GUARD_PASS", flush=True)


def check_partial_failure_and_readonly(browser):
    with local_session(browser, 390) as session:
        page, fixture = session.page, session.fixture
        calls = []

        def engine(raw, page_number, lock_dir):
            calls.append(page_number)
            if page_number == 2:
                raise ocr.OCRUnavailable(FAILURE)
            return 2, parsed_page(session.folder_id)

        def assert_stale():
            page.wait_for_function("message => document.getElementById('ocr-progress').textContent.includes(message) "
                "&& !document.getElementById('ocr-start').disabled", arg=FAILURE)
            assert page.locator('#ocr-review [name="name"]').input_value() == OLD_NAME
            assert page.locator('#ocr-review [name="version"]').input_value() == "2"
            assert page.locator('#ocr-review').evaluate("form => Array.from(form.elements).every(el => el.disabled)")
            assert page.get_by_role("link", name="Посмотреть новый разбор", exact=True).is_visible()

        with patch.object(ocr, "recognize_pdf_page", side_effect=engine):
            page.goto(session.origin + PATH + "?p=1")
            page.get_by_role("button", name="Обновить разбор", exact=True).click()
            assert_stale()
            first_failure = saved_snapshot(fixture)
            page.get_by_role("button", name="Обновить разбор", exact=True).click()
            assert_stale()
            assert calls == [1, 2, 2]
            assert saved_snapshot(fixture) == first_failure
            with page.expect_navigation():
                page.get_by_role("link", name="Посмотреть новый разбор", exact=True).click()
            assert_preview(page, 390)
            assert saved_snapshot(fixture) == first_failure

        # An accounting viewer can compare both versions but has no mutation UI.
        viewer = fixture.client(3)
        session.context.add_cookies([{"name": "session", "value": viewer.get_cookie("session").value,
                                     "url": session.origin}])
        page.reload()
        assert_preview(page, 390, can_apply=False)
        assert page.locator('#ocr-start, #ocr-apply-automatic, #ocr-review').count() == 0
        assert saved_snapshot(fixture) == first_failure
        with page.expect_navigation():
            page.get_by_role("link", name="Сохранённый разбор", exact=True).click()
        assert page.get_by_role("table", name="Товары из документа").locator('.ocr-name-cell').inner_text() == OLD_NAME
        assert page.locator('form').count() == 0
        assert saved_snapshot(fixture) == first_failure
        assert_no_overflow(page, 390, "viewer")
        assert not session.errors, session.errors
        print("MED_OCR_REREAD_BROWSER PARTIAL_FAILURE_CACHED_RETRY_READONLY_GET_PASS", flush=True)


def check_missing_preview_and_initial_recognition(browser):
    with local_session(browser, 1365) as session:
        page, fixture = session.page, session.fixture
        before = saved_snapshot(fixture)
        page.goto(session.origin + PATH + "?p=1&view=automatic")
        assert page.get_by_text("Нового разбора для этой страницы нет.", exact=True).is_visible()
        assert page.get_by_role("heading", name="Товары · страница 1", exact=True).is_visible()
        assert page.locator('#ocr-review [name="name"]').input_value() == OLD_NAME
        assert page.locator('#ocr-apply-automatic').count() == 0
        assert saved_snapshot(fixture) == before
        # Reset this synthetic upload to its initial state. The existing inbox
        # flow must still show an editable result, without a missing-new notice.
        fixture.conn.execute("UPDATE med_supply_documents SET fields_json='{}',version=1 WHERE id=1")
        fixture.conn.commit()
        with patch.object(ocr, "recognize_pdf_page", return_value=(1, parsed_page(session.folder_id))) as engine:
            page.goto(session.origin + "/accounting/medical-supplies/incoming/?id=1")
            page.get_by_role("link", name="Разобрать документ", exact=True).click()
            page.wait_for_selector("#ocr-review [name=name]")
            assert engine.call_count == 1
            assert "view=automatic" not in page.url
            assert page.locator('#ocr-review [name="name"]').input_value() == LONG_NAME
            assert page.locator('#ocr-review .ocr-unit-cost').input_value() == "74.50"
            assert page.get_by_text("Нового разбора для этой страницы нет.", exact=True).count() == 0
            after_recognition = saved_snapshot(fixture)
            page.reload()
            assert engine.call_count == 1
            assert saved_snapshot(fixture) == after_recognition
        assert_no_overflow(page, 1365, "initial recognition")
        assert bytes(fixture.get_doc()["original_bytes"]) == session.original
        assert OCRRouteTests.snapshot_stock(fixture) == session.stock
        assert not session.errors, session.errors
        print("MED_OCR_REREAD_BROWSER MISSING_PREVIEW_HINT_INITIAL_RECOGNITION_PASS", flush=True)


def check_preview_token_cannot_select_another_page(browser):
    """Equal OCR bytes do not authorize applying a different page's preview."""
    with local_session(browser, 1365) as session:
        page, fixture = session.page, session.fixture
        pending = copy.deepcopy(session.old)
        automatic = parsed_page(session.folder_id)
        for number, suffix in (("1", "первая страница"), ("2", "вторая страница")):
            pending["pages"][number]["rows"][0]["name"] = OLD_NAME + " — " + suffix
            pending["pages"][number]["automatic"] = copy.deepcopy(automatic)
        fixture.conn.execute("UPDATE med_supply_documents SET fields_json=? WHERE id=1",
                             (json.dumps({ocr.KEY: pending}, ensure_ascii=False),))
        fixture.conn.commit()
        assert pending["pages"]["1"]["automatic"] == pending["pages"]["2"]["automatic"]
        before = saved_snapshot(fixture)
        database_before = tuple(fixture.conn.iterdump())
        page.goto(session.origin + PATH + "?p=1&view=automatic")
        assert_preview(page, 1365)
        first_token = page.locator('#ocr-apply-automatic [name="automatic_token"]').input_value()
        assert tuple(fixture.conn.iterdump()) == database_before
        # Keep the preview's real token and version, changing only its hidden
        # destination page. This was accepted when tokens hashed OCR bytes only.
        page.locator('#ocr-apply-automatic [name="page"]').evaluate("input => { input.value = '2'; }")
        with page.expect_response(lambda response: response.request.method == "POST"
                                  and response.url.endswith("/automatic/apply")) as refused, \
             page.expect_navigation():
            page.get_by_role("button", name="Использовать для этой страницы", exact=True).click()
        assert refused.value.status == 409
        assert page.get_by_role("alert").is_visible()
        assert "Изменения не сохранены." in page.get_by_role("alert").inner_text()
        assert page.get_by_role("link", name="Вернуться к медрасходке", exact=True).is_visible()
        assert tuple(fixture.conn.iterdump()) == database_before
        assert saved_snapshot(fixture) == before
        assert state(fixture) == pending
        assert_no_overflow(page, 1365, "wrong-page token rejected")

        page.goto(session.origin + PATH + "?p=2&view=automatic")
        assert page.get_by_role("heading", name="Новый разбор · страница 2", exact=True).is_visible()
        correct_token = page.locator('#ocr-apply-automatic [name="automatic_token"]').input_value()
        assert correct_token != first_token
        assert page.locator('#ocr-apply-automatic [name="page"]').input_value() == "2"
        assert tuple(fixture.conn.iterdump()) == database_before
        with page.expect_response(lambda response: response.request.method == "POST"
                                  and response.url.endswith("/automatic/apply")) as applied, \
             page.expect_navigation():
            page.get_by_role("button", name="Использовать для этой страницы", exact=True).click()
        assert applied.value.status == 303
        selected = state(fixture)
        assert selected["pages"]["1"] == pending["pages"]["1"]
        assert selected["pages"]["2"]["rows"][0]["name"] == LONG_NAME
        assert not selected["pages"]["2"]["checked"]
        assert "automatic" not in selected["pages"]["2"]
        assert fixture.get_doc()["version"] == before[1] + 1
        assert saved_snapshot(fixture)[2] == before[2] + 1
        event = fixture.conn.execute("SELECT action,before_json FROM med_supply_document_events "
                                     "ORDER BY id DESC LIMIT 1").fetchone()
        assert event["action"] == "ocr_automatic_apply"
        archived = json.loads(event["before_json"])
        assert archived["page"] == 2
        assert archived["page_data"] == pending["pages"]["2"]
        assert page.locator('#ocr-review [name="page"]').input_value() == "2"
        assert page.locator('#ocr-review [name="name"]').input_value() == LONG_NAME
        assert bytes(fixture.get_doc()["original_bytes"]) == session.original
        assert OCRRouteTests.snapshot_stock(fixture) == session.stock
        assert not session.errors, session.errors
        print("MED_OCR_REREAD_BROWSER PAGE_TOKEN_BINDING_409_NO_WRITES_CORRECT_PAGE_APPLY_PASS", flush=True)


if __name__ == "__main__":
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None,
            headless=True, args=["--no-sandbox"])
        try:
            for width in (1365, 390, 320):
                check_preview_and_explicit_apply(browser, width)
            check_partial_failure_and_readonly(browser)
            check_missing_preview_and_initial_recognition(browser)
            check_preview_token_cannot_select_another_page(browser)
        finally:
            browser.close()
