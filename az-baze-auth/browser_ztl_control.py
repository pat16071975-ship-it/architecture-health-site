"""Synthetic UI checks through real Flask routes, without a network server or live DB."""
import os
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from test_ztl_control import ControlTests
import ztl_control as control

fixture=ControlTests();fixture.setUp()
try:
    item=fixture.item();fixture.receipt(item,qty='100',value='1000');fixture.card(item)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
        for width in (1365,390):
            client=fixture.client(1)
            page=browser.new_page(viewport={'width':width,'height':950})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            def route(route):
                req=route.request;u=urlsplit(req.url)
                if u.hostname!='ztl-test.invalid':return route.abort()
                target=u.path+('?' + u.query if u.query else '')
                headers={}
                if req.headers.get('content-type'):headers['Content-Type']=req.headers['content-type']
                response=client.open(target,method=req.method,data=req.post_data_buffer,headers=headers)
                response_headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','set-cookie')}
                route.fulfill(status=response.status_code,headers=response_headers,body=response.data)
            page.route('**/*',route)
            for tab,label in control.TABS:
                page.goto('http://ztl-test.invalid/accounting/ztl/?tab='+tab+'&start=2026-11&end=2026-11')
                assert page.get_by_role('navigation',name='Разделы ЗТЛ').get_by_role('link').count()==7
                assert page.get_by_role('navigation',name='Разделы ЗТЛ').get_by_role('link',name=label,exact=True).count()==1
            page.goto('http://ztl-test.invalid/accounting/ztl/order')
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
            page.get_by_role('button',name='Сохранить заказ',exact=True).click()
            page.wait_for_url('**/ztl/?**')
            assert 'Синтетический заказ '+str(width) in page.locator('body').inner_text()
            page.locator('#technician').select_option(str(fixture.technician))
            page.get_by_role('button',name='Показать',exact=True).click()
            assert 'Синтетический заказ '+str(width) in page.locator('body').inner_text()
            page.get_by_role('link',name='ФОТ',exact=True).click()
            assert 'LAB-BROWSER-'+str(width) in page.locator('body').inner_text()
            assert page.locator('table').first.locator('tbody tr').filter(has_text='LAB-BROWSER-'+str(width)).count()==1
            page.get_by_role('link',name='Инвентаризация',exact=True).click()
            page.get_by_role('button',name='Начать инвентаризацию').click()
            page.locator('[name=count_'+str(item)+']').fill(str(control.stock(fixture.c)[0]['qty']/1000))
            page.get_by_role('button',name='Сдать остатки на проверку').click()
            page.get_by_role('button',name='Принять инвентаризацию',exact=True).click()
            assert 'Принята' in page.locator('body').inner_text()
            assert not errors,errors
            print('ZTL_BROWSER',width,'PASS')
            page.close()
        browser.close()
finally:
    fixture.doCleanups()
