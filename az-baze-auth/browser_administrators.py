"""Browser smoke for real /administrators/ Flask templates on desktop and phone."""
import os
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from test_administrators import AdministratorsTests

fixture = AdministratorsTests()
fixture.setUp()
sid = fixture.staff()
server = make_server("127.0.0.1", 0, fixture.web, threaded=True)
Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:" + str(server.server_port)
artifacts = Path("administrators-browser-artifacts")
artifacts.mkdir(exist_ok=True)
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"),
            headless=True, args=["--no-sandbox"],
        )
        for width in (1365, 390):
            context = browser.new_context(viewport={"width": width, "height": 900})
            session = fixture.client(1).get_cookie("session").value
            context.add_cookies([{"name": "session", "value": session, "url": origin}])
            context.route("**/*", lambda route: route.continue_()
                          if urlsplit(route.request.url).hostname == "127.0.0.1" else route.abort())
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            assert page.goto(origin + "/administrators/?month=2026-11").status == 200
            assert page.get_by_role("heading", name="Администраторы").is_visible()
            assert page.locator(".az-admin-grid thead th").count() == 32
            assert page.locator(".az-admin-grid th.az-name").first.is_visible()
            assert page.locator(".az-admin-grid th.az-total").first.is_visible()
            assert page.locator(".az-admin-grid select").count() == 30
            assert page.locator(".az-admin-grid select").first.input_value() == "off"
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(artifacts / f"table-before-{width}.png"), full_page=True)
            work = page.locator("form:has(input[name=work_date][value='2026-11-04']) select")
            with page.expect_navigation(): work.select_option("12")
            assert page.locator(".az-admin-grid td.az-total").first.inner_text().strip() == "12"
            page.screenshot(path=str(artifacts / f"table-hours-{width}.png"), full_page=True)
            page.get_by_role("link", name="ФОТ админов").click()
            for name, value in (("standards", "1800"), ("conversion", "900"),
                                ("category", "200"), ("plan", "300"), ("extras", "100")):
                page.locator(f"input[form='az-pay-{sid}'][name='{name}']").fill(value)
            with page.expect_navigation(): page.get_by_role("button", name="Сохранить").click()
            assert "3 180,00" in page.locator(".az-admin-pay").inner_text()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(artifacts / f"payroll-{width}.png"), full_page=True)
            page.on("dialog", lambda dialog: dialog.accept())
            with page.expect_navigation(): page.get_by_role("button", name="Закрыть месяц").click()
            assert "Месяц закрыт" in page.locator("body").inner_text()
            assert page.get_by_role("button", name="Сохранить").count() == 0
            page.get_by_role("link", name="Табель рабочего времени").click()
            assert "12" in page.locator(".az-admin-grid td.az-total").first.inner_text()
            page.screenshot(path=str(artifacts / f"closed-{width}.png"), full_page=True)
            page.get_by_role("link", name="ФОТ админов").click()
            with page.expect_navigation(): page.get_by_role("button", name="Повторно открыть месяц").click()
            page.get_by_role("link", name="Настройки").click()
            assert "Иванова Анна" in page.locator(".az-admin-settings").inner_text()
            page.screenshot(path=str(artifacts / f"settings-{width}.png"), full_page=True)
            assert not errors, errors
            context.close()
            print("ADMINISTRATORS_BROWSER", width, "PASS: table, editing, auto payroll, month lock, reopen, settings")
        browser.close()
except BaseException:
    if "page" in locals() and not page.is_closed():
        try: page.screenshot(path=str(artifacts / "failure.png"), full_page=True)
        except Exception: pass
    raise
finally:
    server.shutdown()
    fixture.doCleanups()
