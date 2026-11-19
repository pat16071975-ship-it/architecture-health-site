"""Synthetic UI checks against a loopback Flask server and a temporary database."""
import os
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from test_ztl_control import ControlTests
import ztl_control as control

fixture=ControlTests();fixture.setUp()
fixture.web.config['SESSION_COOKIE_SECURE']=False  # loopback test HTTP only
server=make_server('127.0.0.1',0,fixture.web,threaded=True)
thread=Thread(target=server.serve_forever,daemon=True);thread.start()
origin='http://127.0.0.1:'+str(server.server_port)
artifacts=Path(os.environ.get('ZTL_BROWSER_ARTIFACTS','ztl-browser-artifacts'));artifacts.mkdir(exist_ok=True)
try:
    item=fixture.item();fixture.receipt(item,qty='100',value='1000');fixture.card(item)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
        for width in (1365,390):
            client=fixture.client(1)
            page=browser.new_page(viewport={'width':width,'height':950})
            page.context.add_cookies([{'name':'session','value':client.get_cookie('session').value,'url':origin}])
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            def route(route):
                req=route.request;u=urlsplit(req.url)
                if u.hostname!='127.0.0.1':return route.abort()
                route.continue_()
            page.route('**/*',route)
            for tab,label in control.TABS:
                response=page.goto(origin+'/accounting/ztl/?tab='+tab+'&start=2026-11&end=2026-11')
                assert response.status==200,(tab,response.status)
                assert page.get_by_role('navigation',name='Разделы ЗТЛ').get_by_role('link').count()==7
                assert page.get_by_role('navigation',name='Разделы ЗТЛ').get_by_role('link',name=label,exact=True).count()==1
                assert page.locator('#technician').count()==int(tab in ('orders','payroll'))
                assert page.locator('[name=unpaid]').count()==int(tab=='orders')
            page.get_by_role('link',name='Приход',exact=True).click()
            panel=page.locator('details').filter(has=page.get_by_text('Добавить приход / загрузить УПД',exact=True))
            panel.locator('summary').click()
            panel.locator('[name=supplier]').fill('Поставщик UI')
            panel.locator('[name=reference]').fill('UI-'+str(width))
            panel.locator('[name=item_0]').select_option(str(item))
            panel.locator('[name=qty_0]').fill('10')
            panel.locator('[name=value_0]').fill('100')
            panel.get_by_role('button',name='Создать черновик').click()
            draft=page.locator('details').filter(has=page.locator('summary').filter(has_text='UI-'+str(width)))
            draft.locator('summary').click()
            draft.locator('[name=confirmed]').check();draft.locator('[name=paid]').check()
            draft.get_by_role('button',name='Сохранить; при двух отметках провести приход').click()
            assert 'Проведён' in page.locator('summary').filter(has_text='UI-'+str(width)).inner_text()
            page.goto(origin+'/accounting/ztl/order')
            page.locator('[name=number]').fill('BROWSER-'+str(width))
            page.locator('[name=invoice_date]').fill('2026-11-16')
            page.locator('[name=scope]').select_option('external')
            page.locator('[name=party_id]').select_option(str(fixture.party))
            page.locator('[name=patient]').fill('Синтетический заказ '+str(width))
            page.locator('[name=work_id]').select_option(str(fixture.work))
            assert page.locator('[name=work_id_new]').count()==0
            page.locator('[name=technician_id]').first.check()
            page.locator('[name=pieces]').fill('2')
            page.locator('[name=amount]').fill('500')
            page.locator('[name=completed_on]').fill('2026-11-16')
            invalid=page.locator('form input:invalid,form select:invalid').evaluate_all('(els)=>els.map(e=>({name:e.name,value:e.value,error:e.validationMessage}))')
            assert not invalid,invalid
            page.get_by_role('button',name='Сохранить заказ',exact=True).click()
            try:
                page.wait_for_url('**/ztl/?**',timeout=10000)
            except Exception:
                print('ORDER_SUBMIT_FAILED',page.url,page.locator('body').inner_text())
                raise
            assert 'Синтетический заказ '+str(width) in page.locator('body').inner_text()
            page.screenshot(path=str(artifacts/('orders-'+str(width)+'.png')),full_page=True)
            page.locator('#technician').select_option(str(fixture.technician))
            page.get_by_role('button',name='Показать',exact=True).click()
            assert 'Синтетический заказ '+str(width) in page.locator('body').inner_text()
            page.get_by_role('link',name='ФОТ',exact=True).click()
            assert 'LAB-BROWSER-'+str(width) in page.locator('body').inner_text()
            assert page.locator('table').first.locator('tbody tr').filter(has_text='LAB-BROWSER-'+str(width)).count()==1
            page.screenshot(path=str(artifacts/('payroll-'+str(width)+'.png')),full_page=True)
            page.get_by_role('link',name='Инвентаризация',exact=True).click()
            page.get_by_role('button',name='Начать инвентаризацию').click()
            page.locator('[name=count_'+str(item)+']').fill(str(control.stock(fixture.c)[0]['qty']/1000))
            page.get_by_role('button',name='Сдать остатки на проверку').click()
            page.get_by_role('button',name='Принять инвентаризацию',exact=True).click()
            assert 'Принята' in page.locator('body').inner_text()
            page.screenshot(path=str(artifacts/('inventory-'+str(width)+'.png')),full_page=True)
            assert not errors,errors
            print('ZTL_BROWSER',width,'PASS')
            page.close()
        browser.close()
finally:
    server.shutdown();thread.join(timeout=5);server.server_close()
    fixture.doCleanups()
