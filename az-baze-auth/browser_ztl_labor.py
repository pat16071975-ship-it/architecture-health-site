"""Real HTTP/UI checks of ZTL labor splitting on isolated synthetic orders."""
import os
import shutil
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from test_ztl_labor import LaborTests
import app as core
import ztl
import ztl_labor as labor
import ztl_control as control

fixture=LaborTests();fixture.setUp()
(core.SITE_ROOT/'assets').mkdir()
shutil.copyfile(Path(__file__).resolve().parents[1]/'assets/logo-site.webp',core.SITE_ROOT/'assets/logo-site.webp')
fixture.web.config['SESSION_COOKIE_SECURE']=False
server=make_server('127.0.0.1',0,fixture.web,threaded=True)
thread=Thread(target=server.serve_forever,daemon=True);thread.start()
origin='http://127.0.0.1:'+str(server.server_port)
artifacts=Path(os.environ.get('ZTL_BROWSER_ARTIFACTS','ztl-browser-artifacts'));artifacts.mkdir(exist_ok=True)
try:
    seeded=labor.seed_technicians(fixture.c,1);ids=[str(r['id']) for r in seeded[:3]]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
        for width in (1365,390):
            page=browser.new_page(viewport={'width':width,'height':950});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            client=fixture.client(1)
            page.context.add_cookies([{'name':'session','value':client.get_cookie('session').value,'url':origin}])
            page.route('**/*',lambda route:route.continue_() if urlsplit(route.request.url).hostname=='127.0.0.1' else route.abort())
            page.goto(origin+'/accounting/ztl/order')
            page.locator('[name=number]').fill('SPLIT-'+str(width))
            page.locator('[name=invoice_date]').fill('2026-11-15')
            page.locator('[name=scope]').select_option('external')
            page.locator('[name=party_id]').select_option(str(fixture.party))
            page.locator('[name=patient]').fill('Синтетический пример')
            page.locator('[name=price_key]').select_option('external:'+str(fixture.work))
            page.locator('[name=completed_on]').fill('2026-11-15')
            page.locator('[name=amount]').fill('24000')
            for r in seeded:assert page.get_by_text(r['name'],exact=True).count()>=1
            for tid in ids:page.locator('[name=technician_id][value="'+tid+'"]').check()
            for tid,mode,value in zip(ids,['percent','rub','rub'],['50','2000','1000']):
                page.locator('[name=labor_mode_'+tid+']').select_option(mode)
                page.locator('[name=labor_value_'+tid+']').fill(value)
            root=page.locator('[data-labor]')
            assert root.locator('[data-labor-total]').inner_text().replace('\xa0',' ')=='6 000,00 ₽'
            assert 'Осталось распределить: 0,00 ₽' in root.locator('[data-labor-summary]').inner_text()
            assert root.locator('[data-labor-summary]').get_attribute('data-error')=='0'
            for row in root.locator('[data-labor-row]').all():
                box=row.bounding_box();assert box['x']>=0 and box['x']+box['width']<=width+1,box
            root.screenshot(path=str(artifacts/('labor-editor-'+str(width)+'.png')))
            page.get_by_role('button',name='Сохранить заказ',exact=True).click();page.wait_for_url('**/ztl/orders/?**')
            oid=fixture.c.execute('SELECT id FROM ztl_orders WHERE number=?',('LAB-SPLIT-'+str(width),)).fetchone()[0]
            state=fixture.state(oid);assert state['assigned']==600000 and state['complete']
            assert [e['amount'] for e in state['entries']]==[300000,200000,100000]
            edit_url=origin+'/accounting/ztl/order?id='+str(oid)
            page.goto(edit_url)
            assert page.locator('[name=labor_value_'+ids[1]+']').input_value()=='2000'
            before=fixture.snapshot()
            page.locator('[name=labor_value_'+ids[1]+']').fill('2000.01')
            assert 'превышает' in page.locator('[data-labor-summary]').inner_text()
            with page.expect_response(lambda r:r.request.method=='POST' and '/ztl/save/order' in r.url) as response:
                page.get_by_role('button',name='Сохранить заказ',exact=True).click()
            assert response.value.status==400
            assert fixture.snapshot()==before
            for tid,amount in zip(ids,['3000','2000','1000']):
                page.goto(origin+'/accounting/ztl/payroll/?start=2026-11&end=2026-11&technician='+tid)
                row=page.locator('[data-payroll-order="'+str(oid)+'"]')
                assert row.count()==1
                assert row.locator('[data-selected-labor]').inner_text()==amount
            page.screenshot(path=str(artifacts/('labor-payroll-'+str(width)+'.png')),full_page=True)
            row.locator('summary').click()
            form=row.locator('form')
            form.locator('[name=labor_mode_'+ids[0]+']').select_option('percent')
            form.locator('[name=labor_value_'+ids[0]+']').fill('40')
            form.locator('[name=labor_mode_'+ids[1]+']').select_option('percent')
            form.locator('[name=labor_value_'+ids[1]+']').fill('40')
            form.locator('[name=labor_mode_'+ids[2]+']').select_option('percent')
            form.locator('[name=labor_value_'+ids[2]+']').fill('20')
            form.get_by_role('button',name='Сохранить распределение',exact=True).click();page.wait_for_url('**/ztl/payroll/?**')
            assert [e['amount'] for e in fixture.state(oid)['entries']]==[240000,240000,120000]
            assert fixture.c.execute('SELECT COUNT(*) FROM ztl_costs').fetchone()[0]==0
            # Removing an executor and explicitly redistributing does not reissue materials.
            stock_before=control.stock_stamp(fixture.c)
            page.goto(edit_url);page.locator('[name=technician_id][value="'+ids[2]+'"]').uncheck()
            assert page.locator('[name=labor_value_'+ids[2]+']').is_disabled()
            for tid in ids[:2]:page.locator('[name=labor_value_'+tid+']').fill('50')
            page.get_by_role('button',name='Сохранить заказ',exact=True).click();page.wait_for_url('**/ztl/orders/?**')
            assert fixture.state(oid)['complete'] and len(fixture.state(oid)['entries'])==2
            assert control.stock_stamp(fixture.c)==stock_before
            assert errors==[],errors
            page.close();print('ZTL_LABOR_BROWSER',width,'PASS; mixed, percent, filter, zero remainder, server rollback, edit and five technicians')
        browser.close()
finally:
    server.shutdown();thread.join(timeout=5);fixture.doCleanups()
