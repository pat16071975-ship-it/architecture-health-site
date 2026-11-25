"""Exercise page-by-page OCR requests and review controls on desktop/mobile."""
import os
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from test_medical_supplies_documents import IncomingDocumentTests
from test_medical_supplies_ocr import data_fixture
import medical_supplies_ocr as ocr

ARTIFACTS=Path(os.environ.get("MED_BROWSER_ARTIFACTS","med-browser-artifacts"))
ARTIFACTS.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None,
                              headless=True,args=["--no-sandbox"])
    for width in (1365,390):
        fixture=IncomingDocumentTests()
        fixture.setUp()
        fixture.app.config["SESSION_COOKIE_SECURE"]=False
        fixture.upload()
        server=make_server("127.0.0.1",0,fixture.app,threaded=True)
        thread=Thread(target=server.serve_forever,daemon=True)
        thread.start()
        origin=f"http://127.0.0.1:{server.server_port}"
        try:
            client=fixture.client(2)
            page=browser.new_page(viewport={"width":width,"height":900})
            page.context.add_cookies([{"name":"session","value":client.get_cookie("session").value,"url":origin}])
            errors=[]
            page.on("pageerror",lambda e:errors.append(str(e)))
            page.goto(origin+"/accounting/medical-supplies/incoming/?id=1")
            page.get_by_role("link",name="Разобрать документ").click()
            with patch.object(ocr,"recognize_pdf_page",return_value=(2,data_fixture())) as engine:
                page.get_by_role("button",name="Разобрать документ").click()
                page.wait_for_selector("#ocr-rows")
                assert engine.call_count==2
            assert "2 / 2" in page.locator("#ocr-progress").inner_text()
            assert page.locator("#ocr-rows .ocr-row").count()==1
            assert page.locator('input[name="quantity"]').first.input_value()=="2"
            page.get_by_role("button",name="+ Добавить строку").click()
            assert page.locator("#ocr-rows .ocr-row").count()==2
            page.locator("#ocr-rows .ocr-row").last.get_by_role("button",name="Убрать из черновика").click()
            assert page.locator("#ocr-rows .ocr-row").count()==1
            page.locator('input[name="quantity"]').first.fill("3")
            with page.expect_navigation():
                page.get_by_role("button",name="Сохранить разбор").click()
            assert page.locator('input[name="quantity"]').first.input_value()=="3"
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors,errors
            page.screenshot(path=str(ARTIFACTS/f"ocr-review-{width}.png"),full_page=True)
            page.close()
            print("MED_OCR_BROWSER",width,"PASS")
        finally:
            server.shutdown()
            fixture.doCleanups()
    browser.close()
