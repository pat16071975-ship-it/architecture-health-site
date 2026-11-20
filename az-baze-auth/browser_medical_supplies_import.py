"""Real HTTP upload using synthetic values only; never production data."""
import os
import shutil
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from test_medical_supplies_import import ImportTests, fixture
import app as core

artifacts=Path(os.environ.get('MED_BROWSER_ARTIFACTS','med-browser-artifacts'));artifacts.mkdir(exist_ok=True)
content=fixture([('Тест: материал А',100,'шт',14645),('Тест: салфетки, упаковка',2,'упак',600),('Тест: нулевая стоимость',7,'шт',0)])
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    for width,uid in ((1365,1),(390,1),(1365,2),(390,2)):
        # Each role/viewport must reach its FIRST import from an empty database.
        case=ImportTests();case.setUp()
        (core.SITE_ROOT/'assets').mkdir()
        shutil.copyfile(Path(__file__).resolve().parents[1]/'assets/logo-site.webp',core.SITE_ROOT/'assets/logo-site.webp')
        case.web.config['SESSION_COOKIE_SECURE']=False
        server=make_server('127.0.0.1',0,case.web,threaded=True)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        try:
            client=case.client(uid);page=browser.new_page(viewport={'width':width,'height':950})
            page.context.add_cookies([{'name':'session','value':client.get_cookie('session').value,'url':origin}])
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            assert case.c.execute('SELECT COUNT(*) FROM med_supply_stock_imports').fetchone()[0]==0
            before='\n'.join(case.c.iterdump())
            page.goto(origin+'/accounting/')
            page.get_by_role('link',name='Медрасходка',exact=True).click()
            assert page.url==origin+'/accounting/medical-supplies/'
            assert page.locator('#stock-file').count()==1
            for label in ('К заказу','Справочник','История изменений','Движение'):
                page.get_by_role('link',name=label,exact=True).click()
                assert page.locator('#stock-file').count()==1,label
                assert 'tab=stock' not in page.url
            assert before=='\n'.join(case.c.iterdump()),'navigation wrote to database'
            page.get_by_text('Загрузить остатки из Excel',exact=True).click()
            assert page.locator('#stock-file').is_visible()
            page.screenshot(path=str(artifacts/f'first-upload-{uid}-{width}.png'),full_page=True)
            page.locator('#stock-file').set_input_files({'name':'Остатки (2026.11.19).xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':content})
            page.locator('#stock-date').fill('2026-11-19')
            with page.expect_navigation():page.get_by_role('button',name='Загрузить файл',exact=True).click()
            assert page.locator('.md-review tbody tr').count()==3
            assert page.locator('[data-source-row="4"]').inner_text().count('146,45')==2
            assert 'Требует решения' in page.locator('[data-source-row="5"]').inner_text()
            assert '0,00' in page.locator('[data-source-row="6"]').inner_text()
            assert page.get_by_text('Остатки на 19.11.2026',exact=True).count()==1
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'page-level overflow'
            page.screenshot(path=str(artifacts/f'import-{uid}-{width}.png'),full_page=True)
            page.get_by_label('Только с вопросами').check()
            with page.expect_navigation():page.get_by_role('button',name='Показать',exact=True).click()
            assert page.locator('.md-review tbody tr').count()==2
            page.locator('#stock-search').fill('нулевая')
            with page.expect_navigation():page.get_by_role('button',name='Показать',exact=True).click()
            assert page.locator('.md-review tbody tr').count()==1
            page.get_by_role('link',name='Движение',exact=True).click()
            assert 'Распределение материалов' in page.locator('body').inner_text()
            page.get_by_role('link',name='Движение',exact=True).click()
            assert page.locator('.md-review tbody tr').count()==3
            # Retry the same bytes using the normal entry's upload form as well.
            page.get_by_text('Загрузить остатки из Excel',exact=True).click()
            page.locator('#stock-file').set_input_files({'name':'Остатки (2026.11.19).xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':content})
            page.locator('#stock-date').fill('2026-11-19')
            with page.expect_navigation():page.get_by_role('button',name='Загрузить файл',exact=True).click()
            assert page.get_by_text('Этот файл уже загружен. Повторных позиций не создано.',exact=True).is_visible()
            assert case.c.execute('SELECT COUNT(*) FROM med_supply_stock_imports').fetchone()[0]==1
            assert case.c.execute('SELECT source_xlsx FROM med_supply_stock_imports').fetchone()[0]==content
            assert page.locator('.md-review tbody tr').count()==3
            assert not errors,errors
            page.close();print('MED_STOCK_FIRST_UPLOAD_BROWSER',uid,width,'PASS')
        finally:
            server.shutdown();case.doCleanups()
    browser.close()
