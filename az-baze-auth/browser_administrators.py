"""Browser: full 31-day grid, one explicit save, modal and audit journal.

Only synthetic local SQLite and ephemeral Flask HTTP. Never Production.
"""
import os
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from test_administrators import AdministratorsTests

fixture = AdministratorsTests()
fixture.setUp()
sid = fixture.staff(name="Иванова Анна Петровна")
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
            context = browser.new_context(
                viewport={"width": width, "height": 900},
                accept_downloads=True,
            )
            session = fixture.client(1).get_cookie("session").value
            context.add_cookies([
                {"name": "session", "value": session, "url": origin}
            ])
            context.route("**/*", lambda route: route.continue_()
                          if urlsplit(route.request.url).hostname == "127.0.0.1"
                          else route.abort())
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))

            assert page.goto(origin + "/administrators/?month=2026-12").status == 200
            assert page.locator(".az-admin-grid thead th").count() == 33
            assert page.locator(".az-admin-grid .az-hour-input").count() == 31
            assert page.locator(f"input[name='hours_{sid}_31']").count() == 1
            assert page.locator(".az-admin-grid .az-name").last.inner_text().startswith(
                "Иванова А. П."
            )
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            assert page.locator(".az-admin-grid").evaluate(
                "(e) => e.scrollWidth <= e.clientWidth+1"
            ), "December grid has a horizontal scrollbar"
            page.screenshot(path=str(artifacts/f"december-31-days-{width}.png"),
                            full_page=True)

            assert page.goto(origin + "/administrators/?month=2026-11").status == 200
            assert page.get_by_role("heading", name="Администраторы").is_visible()
            assert page.locator(".az-admin-grid thead th").count() == 32
            assert page.locator(".az-admin-grid select").count() == 0
            assert page.locator(".az-admin-grid .az-hour-input").count() == 30
            assert page.locator(".az-admin-grid").evaluate(
                "(e) => e.scrollWidth <= e.clientWidth+1"
            ), "November grid has a horizontal scrollbar"
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(artifacts/f"table-before-{width}.png"),
                            full_page=True)

            work = page.locator(f"input[name='hours_{sid}_04']")
            before_hours = fixture.conn.execute(
                "SELECT minutes FROM az_admin_days WHERE staff_id=? "
                "AND work_date='2026-11-04'", (sid,)
            ).fetchone()
            work.fill("11,5")
            assert "Есть несохранённые изменения" in page.locator(
                "#az-hours-save-status"
            ).inner_text()
            assert page.locator(".az-admin-grid td.az-total").first.inner_text() == "11,5"
            after_input = fixture.conn.execute(
                "SELECT minutes FROM az_admin_days WHERE staff_id=? "
                "AND work_date='2026-11-04'", (sid,)
            ).fetchone()
            assert (before_hours[0] if before_hours else None) == (
                after_input[0] if after_input else None
            ), "Typing must not write to database"

            work.fill("12")
            with page.expect_navigation():
                page.locator("#az-hours-batch").get_by_role(
                    "button", name="Сохранить", exact=True
                ).click()
            assert page.locator(".az-admin-grid td.az-total").first.inner_text() == "12"
            assert page.locator("#azUploadModalTitle").inner_text() == "ТАБЕЛЬ СОХРАНЁН"
            assert "ноябрь 2026 года" in page.locator(
                "#azUploadModalMessage"
            ).inner_text()
            assert page.locator("#azUploadModal").is_visible()
            assert fixture.conn.execute(
                "SELECT minutes FROM az_admin_days WHERE staff_id=? "
                "AND work_date='2026-11-04'", (sid,)
            ).fetchone()[0] == 720
            page.screenshot(path=str(artifacts/f"table-saved-{width}.png"),
                            full_page=True)
            page.locator("#azUploadModalOk").click()
            assert page.locator("#azUploadModal").is_hidden()
            page.reload()
            assert page.locator("#azUploadModal").is_hidden()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")

            page.get_by_role("link", name="ФОТ админов").click()
            for name, value in (
                ("standards", "1800"), ("conversion", "900"),
                ("category", "200"), ("plan", "300"), ("extras", "100")
            ):
                page.locator(
                    f"input[form='az-pay-{sid}'][name='{name}']"
                ).fill(value)
            with page.expect_navigation():
                page.get_by_role("button", name="Сохранить").click()
            assert "3 180,00" in page.locator(".az-admin-pay").inner_text()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(artifacts/f"payroll-{width}.png"), full_page=True)

            page.on("dialog", lambda dialog: dialog.accept())
            with page.expect_navigation():
                page.get_by_role("button", name="Закрыть месяц").click()
            assert "Месяц закрыт" in page.locator("body").inner_text()
            assert page.get_by_role("button", name="Сохранить").count() == 0
            page.get_by_role("link", name="Табель рабочего времени").click()
            assert page.locator(".az-hour-input").count() == 0
            assert "12" in page.locator(".az-admin-grid td.az-total").first.inner_text()
            page.screenshot(path=str(artifacts/f"closed-{width}.png"), full_page=True)

            page.get_by_role("link", name="ФОТ админов").click()
            with page.expect_navigation():
                page.get_by_role("button", name="Повторно открыть месяц").click()
            page.get_by_role("link", name="Настройки").click()
            assert page.locator(
                ".az-admin-settings input[name=full_name]"
            ).first.input_value() == "Иванова Анна Петровна"
            assert page.get_by_role(
                "heading", name="Журнал изменений"
            ).is_visible()
            journal = page.locator(".az-admin-history")
            for name in ("04 число:", "Стандарты:", "Месяц закрыт",
                         "Месяц повторно открыт"):
                assert name in journal.inner_text(), name
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(artifacts/f"settings-history-{width}.png"),
                            full_page=True)
            assert not errors, errors
            context.close()
            print("ADMINISTRATORS_BROWSER", width,
                  "PASS: 31-day grid, no horizontal scrolling, manual hours,"
                  " atomic Save popup, FOT, month lock, audit journal")
        browser.close()
except BaseException:
    if "page" in locals() and not page.is_closed():
        try:
            page.screenshot(path=str(artifacts/"failure.png"), full_page=True)
        except Exception:
            pass
    raise
finally:
    server.shutdown()
    fixture.doCleanups()
