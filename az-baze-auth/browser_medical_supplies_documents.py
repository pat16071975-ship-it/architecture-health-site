"""Browser checks for med supplies document inbox, synthetic documents only."""
import os
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from test_medical_supplies_documents import IncomingDocumentTests, PDF

ARTIFACTS = Path(os.environ.get("MED_BROWSER_ARTIFACTS", "med-browser-artifacts"))
ARTIFACTS.mkdir(exist_ok=True)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        executable_path=os.environ.get("CHROMIUM_PATH") or None,
        headless=True, args=["--no-sandbox"],
    )
    for width in (1365, 390):
        fixture = IncomingDocumentTests()
        fixture.setUp()
        fixture.app.config["SESSION_COOKIE_SECURE"] = False
        server = make_server("127.0.0.1", 0, fixture.app, threaded=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            client = fixture.client(2)
            page = browser.new_page(
                viewport={"width": width, "height": 900},
                accept_downloads=True,
            )
            page.context.add_cookies([{
                "name": "session", "value": client.get_cookie("session").value,
                "url": origin,
            }])
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(origin + "/accounting/medical-supplies/?tab=stock")
            page.get_by_role("link", name="Входящие документы").click()
            assert page.url == origin + "/accounting/medical-supplies/incoming/"
            assert page.get_by_role("heading", name="Загрузить документ").is_visible()
            assert page.locator("#inbox-file").count() == 1
            assert fixture.conn.execute("SELECT COUNT(*) FROM med_supply_documents").fetchone()[0] == 0
            page.screenshot(path=str(ARTIFACTS / f"inbox-empty-{width}.png"),
                            full_page=True)

            page.locator("#inbox-file").set_input_files({
                "name": "Комус УПД.pdf",
                "mimeType": "application/pdf", "buffer": PDF,
            })
            page.locator("#inbox-type").select_option("upd")
            page.locator("#inbox-day").fill("2026-11-20")
            page.locator("#inbox-party").fill("ООО Комус")
            page.locator("#inbox-number").fill("S269000100839695")
            with page.expect_navigation():
                page.get_by_role("button", name="Сохранить во входящих").click()
            assert "uploaded=1" in page.url
            assert page.get_by_text("Документ загружен, оригинал сохранён.",
                                    exact=False).is_visible()
            assert fixture.conn.execute("SELECT COUNT(*) FROM med_supply_documents").fetchone()[0] == 1
            assert fixture.conn.execute("SELECT COUNT(*) FROM med_supply_days").fetchone()[0] == 0
            assert fixture.conn.execute("SELECT COUNT(*) FROM med_supply_periods").fetchone()[0] == 0
            assert page.locator("#card-party").input_value() == "ООО Комус"
            assert page.locator("#card-number").input_value() == "S269000100839695"
            with page.expect_download() as dl:
                page.get_by_role("link", name="Скачать оригинал").click()
            assert dl.value.suggested_filename == "Комус УПД.pdf"
            page.screenshot(path=str(ARTIFACTS / f"inbox-uploaded-{width}.png"),
                            full_page=True)

            page.locator("#card-status").select_option("reviewed")
            with page.expect_navigation():
                page.get_by_role("button", name="Сохранить карточку").click()
            assert page.locator("#card-status").input_value() == "reviewed"
            assert fixture.conn.execute(
                "SELECT version FROM med_supply_documents WHERE id=1"
            ).fetchone()[0] == 2

            page.locator("#inbox-file").set_input_files({
                "name": "Комус-дубль.pdf", "mimeType": "application/pdf",
                "buffer": PDF,
            })
            with page.expect_navigation():
                page.get_by_role("button", name="Сохранить во входящих").click()
            assert "duplicate=1" in page.url
            assert fixture.conn.execute("SELECT COUNT(*) FROM med_supply_documents").fetchone()[0] == 1
            page.get_by_role("searchbox", name="Поиск").fill("комУС")
            with page.expect_navigation():
                page.get_by_role("button", name="Найти").click()
            assert "Комус УПД.pdf" in page.locator(".inbox-table").inner_text()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
            page.close()

            reader = fixture.client(3)
            view = browser.new_page(viewport={"width": width, "height": 900})
            view.context.add_cookies([{
                "name": "session", "value": reader.get_cookie("session").value,
                "url": origin,
            }])
            view.goto(origin + "/accounting/medical-supplies/incoming/")
            assert view.get_by_text("Доступен просмотр документов", exact=False).is_visible()
            assert view.locator("#inbox-file").count() == 0
            assert view.locator(".inbox-table tbody tr").count() == 1
            assert view.evaluate("document.documentElement.scrollWidth <= innerWidth")
            view.screenshot(path=str(ARTIFACTS / f"inbox-reader-{width}.png"),
                            full_page=True)
            view.close()
            print("MED_INCOMING_DOCUMENTS_BROWSER", width, "PASS")
        finally:
            server.shutdown()
            fixture.doCleanups()
    browser.close()
