"""Real browser through HTTP, isolated synthetic ledger. No Production data."""
import os
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from datetime import date
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from test_medical_supplies_daily import DailyTests
import medical_supplies_daily as daily

artifacts=Path(os.environ.get('MED_BROWSER_ARTIFACTS','med-browser-artifacts'));artifacts.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    for width in (1365,390):
        case=DailyTests();case.setUp()
        case.web.config['SESSION_COOKIE_SECURE']=False
        server=make_server('127.0.0.1',0,case.web,threaded=True)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        try:
            client=case.client(1);page=browser.new_page(viewport={'width':width,'height':950})
            page.context.add_cookies([{'name':'session','value':client.get_cookie('session').value,'url':origin}])
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin+'/accounting/medical-supplies/?month=2026-11')
            assert page.locator('.md-review tbody tr').count()==2
            row=page.locator('[data-source-row="4"]')
            assert row.bounding_box()['height']<=32
            row.locator('[data-folder]').select_option(str(case.root))
            assert row.locator('[data-subfolder] option').all_text_contents()==['Без подпапки','Анестезия','Аптечки']
            row.locator('[data-subfolder]').select_option(str(case.sub))
            page.screenshot(path=str(artifacts/f'distribution-{width}.png'),full_page=True)
            with page.expect_navigation():row.get_by_role('button',name='Внести',exact=True).click()
            assert page.locator('.md-review tbody tr').count()==1
            assert page.locator('.md-product').count()==1
            assert page.locator('.md-product:visible').count()==0
            page.locator('#md-expand').click()
            assert page.locator('.md-sub:visible').count()>0
            assert page.locator('.md-product:visible').count()==0
            page.locator('#md-expand').click()
            assert page.locator('.md-product:visible').count()==1
            product=page.locator('.md-product')
            page.screenshot(path=str(artifacts/f'expanded-{width}.png'),full_page=True)
            height=product.bounding_box()['height']
            assert height<=28,f'product row height {height}'
            iid=product.locator('[data-day]').first.get_attribute('data-item')
            product.locator('[data-day="2026-11-20"]').click()
            page.locator('#md-day-qty').fill('10')
            with page.expect_navigation():page.locator('#md-day-dialog').get_by_role('button',name='Сохранить',exact=True).click()
            page.locator('#md-expand').click();page.locator('#md-expand').click()
            product=page.locator('.md-product')
            assert product.locator('td').nth(-3).inner_text()=='1\u00a0464,50*'
            assert product.locator('td').nth(-1).inner_text()=='13\u00a0180,50'
            assert '1\u00a0464,50' in page.locator(f'[data-folder-row="{case.root}"]').inner_text()
            assert '1\u00a0464,50' in page.locator(f'[data-sub-row="{case.sub}"]').inner_text()
            page.locator('#md-distribute summary').first.click()
            page.screenshot(path=str(artifacts/f'daily-{width}.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),'page overflow'
            header=page.locator('.md-grid th').last
            page.locator('.md-grid').evaluate('(el)=>{el.scrollLeft=el.scrollWidth;el.scrollTop=80;}')
            assert header.is_visible()
            product.locator('.md-first').evaluate('(el)=>el.scrollIntoView({block:"nearest"})')
            assert product.locator('.md-first').is_visible()
            page.get_by_role('link',name='Приход',exact=True).click()
            page.locator('#md-search').fill('Материал А')
            product=page.locator('.md-product:visible')
            product.locator('[data-day="2026-11-20"]').click()
            page.locator('#md-day-qty').fill('5');page.locator('#md-day-value').fill('1000')
            with page.expect_navigation():page.locator('#md-day-dialog').get_by_role('button',name='Сохранить',exact=True).click()
            record=case.c.execute('SELECT * FROM med_supply_days WHERE item_id=?',(iid,)).fetchone()
            assert record['qty_in']=='5' and record['qty_out']=='10'
            page.get_by_role('link',name='Справочник',exact=True).click()
            assert 'Материал А' in page.locator('body').inner_text()
            assert '2026-11-19' in page.locator('body').inner_text()
            page.get_by_role('link',name='Движение',exact=True).click()
            page.get_by_label('Включая распределённые').check()
            with page.expect_navigation():page.get_by_role('button',name='Показать',exact=True).click()
            assert page.locator('.md-review tbody tr').count()==2
            assert not errors,errors
            page.close();print('MED_DAILY_BROWSER',width,'PASS')
        finally:
            server.shutdown();case.doCleanups()
    browser.close()
