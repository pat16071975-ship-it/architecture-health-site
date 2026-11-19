"""Synthetic UI checks against a loopback Flask server and a temporary database."""
import os
import shutil
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from test_ztl_control import ControlTests
import ztl_control as control
import app as core

fixture=ControlTests();fixture.setUp()
(core.SITE_ROOT/'assets').mkdir()
shutil.copyfile(Path(__file__).resolve().parents[1]/'assets/logo-site.webp',core.SITE_ROOT/'assets/logo-site.webp')
fixture.web.config['SESSION_COOKIE_SECURE']=False  # loopback test HTTP only
server=make_server('127.0.0.1',0,fixture.web,threaded=True)
thread=Thread(target=server.serve_forever,daemon=True);thread.start()
origin='http://127.0.0.1:'+str(server.server_port)
artifacts=Path(os.environ.get('ZTL_BROWSER_ARTIFACTS','ztl-browser-artifacts'));artifacts.mkdir(exist_ok=True)
try:
    item=fixture.item();fixture.receipt(item,qty='100',value='1000');fixture.card(item)
    plastic=fixture.item('Пластик ортодонтический',unit='г');fixture.receipt(plastic,qty='1000',value='20000')
    screw=fixture.item('Винт расширяющий',unit='шт.');fixture.receipt(screw,qty='100',value='12000')
    asset=fixture.item('Шкаф для проверки изоляции','asset','шт.')
    for name,qty,fee,internal,external in [('Каппа','10','600','2200','3500'),('Ретенционный аппарат','12','800','2500','4000')]:
        control.save_card(fixture.c,dict(name=name,version='0',technician_fee=fee,prices_present='1',internal_price=internal,external_price=external,**{'qty_'+str(plastic):qty}),1)
    for name,internal,external in [('Ремонт для клиники','0',''),('Работа для внешнего заказчика','','6000')]:
        control.save_card(fixture.c,dict(name=name,version='0',no_materials='1',technician_fee='0',prices_present='1',internal_price=internal,external_price=external),1)
    internal_only=fixture.c.execute("SELECT id FROM ztl_names WHERE name='Ремонт для клиники'").fetchone()[0]
    external_only=fixture.c.execute("SELECT id FROM ztl_names WHERE name='Работа для внешнего заказчика'").fetchone()[0]
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
            def submit(form,label):
                with page.expect_navigation(wait_until='networkidle'):
                    form.get_by_role('button',name=label,exact=True).click()
            response=page.goto(origin+'/accounting/ztl/?start=2026-11&end=2026-11')
            assert response.status==200
            menu=page.get_by_role('navigation',name='Разделы ЗТЛ')
            assert menu.get_by_role('link').count()==7
            assert page.get_by_role('heading',name='ЗТЛ',exact=True).count()==1
            assert page.locator('table,form,.ztl-filter').count()==0
            boxes=menu.get_by_role('link').evaluate_all('(links)=>links.map(a=>{const r=a.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})')
            assert all(b['h']>=60 and b['w']>=280 and b['x']>=0 and b['x']+b['w']<=width for b in boxes),boxes
            assert all(abs(b['x']-boxes[0]['x'])<1 and abs(b['w']-boxes[0]['w'])<1 for b in boxes),boxes
            assert all(boxes[i]['y']>=boxes[i-1]['y']+boxes[i-1]['h']+9 for i in range(1,len(boxes))),boxes
            page.screenshot(path=str(artifacts/('menu-'+str(width)+'.png')),full_page=True)
            for tab,label in control.TABS:
                menu.get_by_role('link',name=label,exact=True).click()
                page.wait_for_url('**/ztl/'+tab+'/**')
                assert page.locator('.ztl-title').inner_text()==label
                assert page.get_by_role('navigation',name='Разделы ЗТЛ').count()==0
                assert page.locator('nav.menu').count()==0
                assert page.locator('#technician').count()==int(tab in ('orders','payroll'))
                assert page.locator('[name=unpaid]').count()==int(tab=='orders')
                page.get_by_role('link',name='← К разделам ЗТЛ',exact=True).click()
                assert menu.get_by_role('link').count()==7
                assert 'start=2026-11' in page.url and 'end=2026-11' in page.url
                assert page.locator('table,form').count()==0
            print('ZTL_SEPARATE_PAGES',width,'PASS; seven vertical tiles, independent pages and return')
            menu.get_by_role('link',name='Реестр изделий (технологические карты)',exact=True).click()
            assert page.locator('form[action$="/item"],select[name=kind]').count()==0
            assert 'Шкаф для проверки изоляции' not in page.locator('body').inner_text()
            panel=page.locator('.ztl-product-new');panel.locator('summary').click()
            product=panel.locator('[data-product-form]')
            name='Ортодонтическая пластинка' if width==1365 else 'Пластинка с расширяющим винтом'
            product.locator('[name=name]').fill(name)
            product.locator('[name=internal_price]').fill('2500')
            product.locator('[name=external_price]').fill('4000')
            for idx,(iid,qty) in enumerate([(item,'0,35'),(plastic,'12'),(screw,'1')]):
                if idx:product.get_by_role('button',name='+ Добавить материал',exact=True).click()
                product.locator('[name=material_'+str(idx)+']').select_option(str(iid))
                product.locator('[name=material_qty_'+str(idx)+']').fill(qty)
            product.get_by_role('button',name='+ Добавить материал',exact=True).click()
            product.locator('[name=material_3]').select_option('new')
            product.locator('[name=material_name_3]').fill('Проволока')  # Existing name must fail without losing the form.
            product.locator('[name=material_unit_3]').select_option('__custom__')
            product.locator('[name=material_custom_unit_3]').fill('капля')
            product.locator('[name=material_qty_3]').fill('2')
            product.locator('[name=technician_fee]').fill('900')
            before='\n'.join(fixture.c.iterdump())
            product.get_by_role('button',name='Добавить изделие',exact=True).click()
            product.locator('[data-save-error]:not([hidden])').wait_for()
            assert 'уже есть' in product.locator('[data-save-error]').inner_text()
            assert product.locator('[name=name]').input_value()==name
            assert product.locator('[name=material_qty_1]').input_value()=='12'
            assert product.locator('[name=internal_price]').input_value()=='2500'
            assert product.locator('[name=external_price]').input_value()=='4000'
            assert '\n'.join(fixture.c.iterdump())==before
            material_name='Разделительный лак' if width==1365 else 'Изолирующий лак'
            product.locator('[name=material_name_3]').fill(material_name)
            submit(product,'Добавить изделие')
            wid=fixture.c.execute('SELECT id FROM ztl_names WHERE name=?',(name,)).fetchone()[0]
            lacquer=fixture.c.execute('SELECT id FROM ztl_items WHERE name=?',(material_name,)).fetchone()[0]
            fixture.receipt(lacquer,qty='100',value='100')
            page.reload()
            entry=page.locator('[data-product-entry="'+str(wid)+'"]');entry.locator('[data-edit-product]').first.click()
            assert len(next(r['parts'] for r in control.catalog(fixture.c) if r['id']==wid))==4
            assert next(r['total_cost'] for r in control.catalog(fixture.c) if r['id']==wid)==126550
            saved=next(r for r in control.catalog(fixture.c) if r['id']==wid)
            assert (saved['internal_price'],saved['external_price'])==(250000,400000)
            assert entry.locator('[name=internal_price]').input_value()=='2500'
            assert entry.locator('[name=external_price]').input_value()=='4000'
            assert '1\xa0265,50' in entry.locator('[data-product-total]').inner_text()
            entry.screenshot(path=str(artifacts/('product-card-'+str(width)+'.png')))
            page.screenshot(path=str(artifacts/('products-'+str(width)+'.png')),full_page=True)
            entry.locator('[name=name]').fill(name+' — исправлено')
            entry.locator('[name=internal_price]').fill('2600,50')
            entry.locator('[name=external_price]').fill('4200')
            submit(entry,'Сохранить изменения')
            entry.locator('[data-edit-product]').first.click()
            assert name+' — исправлено' in entry.locator('.ztl-product-list-name').first.inner_text()
            saved=next(r for r in control.catalog(fixture.c) if r['id']==wid)
            assert (saved['internal_price'],saved['external_price'],saved['total_cost'])==(260050,420000,126550)
            # Bad prices must retain all input and leave the database unchanged.
            before='\n'.join(fixture.c.iterdump())
            entry.locator('[name=external_price]').fill('-1')
            entry.get_by_role('button',name='Сохранить изменения',exact=True).click()
            entry.locator('[data-save-error]:not([hidden])').wait_for()
            assert 'Внешний прайс' in entry.locator('[data-save-error]:not([hidden])').inner_text()
            assert entry.locator('[name=internal_price]').input_value()=='2600.5'
            assert entry.locator('[name=external_price]').input_value()=='-1'
            assert '\n'.join(fixture.c.iterdump())==before
            entry.locator('[name=external_price]').fill('4200')
            page.once('dialog',lambda dialog:dialog.accept())
            submit(entry,'Убрать из ассортимента')
            page.goto(origin+'/accounting/ztl/order')
            assert page.locator('[name=price_key] option[value$=":'+str(wid)+'"]').count()==0
            page.goto(origin+'/accounting/ztl/products/')
            page.locator('.ztl-archived>summary').click();entry.locator('[data-edit-product]').first.click()
            page.once('dialog',lambda dialog:dialog.accept())
            submit(entry,'Вернуть в ассортимент')
            entry.locator('[data-edit-product]').first.click()
            page.once('dialog',lambda dialog:dialog.accept())
            submit(entry,'Удалить изделие')
            assert entry.count()==0
            typo=fixture.item('Ошибочная запись '+str(width),unit='ууу')
            page.reload();page.locator('.ztl-material-catalog>summary').click()
            material=page.locator('[data-material-entry="'+str(typo)+'"]');material.locator('summary').click()
            material.locator('[name=name]').fill('Исправленный материал')
            material.locator('[name=unit]').select_option('г')
            submit(material,'Сохранить материал')
            page.locator('.ztl-material-catalog>summary').click();material.locator('summary').click()
            assert 'Исправленный материал · г' in material.locator('summary').inner_text()
            page.once('dialog',lambda dialog:dialog.accept())
            submit(material,'Удалить материал');assert material.count()==0
            # A filled, unsaved example of the same real creation form for owner approval.
            panel=page.locator('.ztl-product-new');panel.locator('summary').click();product=panel.locator('[data-product-form]')
            product.locator('[name=name]').fill(name)
            product.locator('[name=internal_price]').fill('2500')
            product.locator('[name=external_price]').fill('4000')
            for idx,(iid,qty) in enumerate([(item,'0,35'),(plastic,'12'),(screw,'1'),(lacquer,'2')]):
                if idx:product.get_by_role('button',name='+ Добавить материал',exact=True).click()
                product.locator('[name=material_'+str(idx)+']').select_option(str(iid))
                product.locator('[name=material_qty_'+str(idx)+']').fill(qty)
            product.locator('[name=technician_fee]').fill('900')
            product.get_by_role('button',name='+ Добавить материал',exact=True).click()
            product.locator('[data-material-row]').last.get_by_role('button',name='Удалить строку').click()
            assert product.locator('[data-material-row]').count()==4
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert page.locator('.ztl-products-list [data-product-editor]:not([hidden])').count()==0
            form_box=panel.bounding_box();list_box=page.locator('.ztl-products-list').bounding_box()
            assert list_box['y']>=form_box['y']+form_box['height']
            if width==1365:
                assert form_box['height']<=650,form_box
                assert all(box['height']<=46 for box in page.locator('.ztl-products-list .ztl-price-row').evaluate_all('(rows)=>rows.map(row=>({height:row.getBoundingClientRect().height}))'))
            for saved_entry in page.locator('.ztl-products-list [data-product-entry]').all():
                price_rows=saved_entry.locator('[data-price-type]')
                assert price_rows.count()==2
                assert price_rows.evaluate_all('(rows)=>rows.every(row=>row.tagName==="TR" && row.cells.length===5)')
                assert price_rows.nth(0).locator('.ztl-product-list-name').inner_text()==price_rows.nth(1).locator('.ztl-product-list-name').inner_text()
                assert price_rows.nth(0).get_attribute('data-price-type')=='internal'
                assert price_rows.nth(1).get_attribute('data-price-type')=='external'
                internal_box=price_rows.nth(0).bounding_box();external_box=price_rows.nth(1).bounding_box()
                assert external_box['y']>=internal_box['y']+internal_box['height']-1
                assert abs(internal_box['x']-external_box['x'])<1
                saved=next(r for r in control.catalog(fixture.c) if r['id']==int(saved_entry.get_attribute('data-product-entry')))
                for idx,key in enumerate(('internal_price','external_price')):
                    expected=control.display(saved[key],100)+' ₽' if saved[key] is not None else 'Не задан'
                    assert price_rows.nth(idx).locator('[data-listed-price]').inner_text()==expected
                assert saved_entry.locator('[data-product-form]').count()==1
            panel.screenshot(path=str(artifacts/('new-product-'+str(width)+'.png')))
            page.locator('.ztl-products-list').screenshot(path=str(artifacts/('price-list-'+str(width)+'.png')))
            page.locator('.ztl-products-registry').screenshot(path=str(artifacts/('registry-'+str(width)+'.png')))
            print('ZTL_UNIFIED_PRODUCT',width,'PASS; compact form, two separate price entries below form, internal/external prices, four materials, custom unit, errors preserve input, edit/delete/archive/restore')
            # Actual order selector: only the selected scope, clearing on switches,
            # correct price/quantity autofill, and preserved manual invoice amount.
            cap=fixture.c.execute("SELECT id FROM ztl_names WHERE name='Каппа'").fetchone()[0]
            for scope,price in [('internal',2200),('external',3500)]:
                page.goto(origin+'/accounting/ztl/order')
                page.locator('[name=scope]').select_option(scope)
                selector=page.locator('[name=price_key]')
                values=selector.locator('option').evaluate_all('(options)=>options.map(o=>o.value).filter(Boolean)')
                assert values and all(value.startswith(scope+':') for value in values)
                assert ('internal:'+str(internal_only) in values)==(scope=='internal')
                assert ('external:'+str(external_only) in values)==(scope=='external')
                selector.select_option(scope+':'+str(cap))
                assert page.locator('[name=amount]').input_value()==str(price)
                page.locator('[name=pieces]').fill('2')
                assert page.locator('[name=amount]').input_value()==str(price*2)
                page.locator('.ztl-order-form').screenshot(path=str(artifacts/('order-price-'+scope+'-'+str(width)+'.png')))
                page.locator('[name=amount]').fill('1234')
                page.locator('[name=pieces]').fill('3')
                assert page.locator('[name=amount]').input_value()=='1234'
                page.locator('[name=scope]').select_option('external' if scope=='internal' else 'internal')
                assert selector.input_value()=='' and page.locator('[name=amount]').input_value()==''
                assert selector.locator('option[value="'+scope+':'+str(cap)+'"]').count()==0
            page.locator('[name=scope]').select_option('internal')
            page.locator('[name=price_key]').select_option('internal:'+str(internal_only))
            assert page.locator('[name=amount]').input_value()=='0'
            print('ZTL_SCOPED_ORDER_PRICES',width,'PASS; scope-filtered positions, reset on switch, zero, price × quantity, manual amount preserved')
            page.get_by_role('link',name='← К разделам ЗТЛ',exact=True).click()
            menu.get_by_role('link',name='Приход',exact=True).click()
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
            page.locator('[name=price_key]').select_option('external:'+str(fixture.work))
            assert page.locator('[name=work_id_new]').count()==0
            page.locator('[name=technician_id]').first.check()
            page.locator('[name=pieces]').fill('2')
            page.locator('[name=amount]').fill('500')
            page.locator('[name=completed_on]').fill('2026-11-16')
            invalid=page.locator('form input:invalid,form select:invalid').evaluate_all('(els)=>els.map(e=>({name:e.name,value:e.value,error:e.validationMessage}))')
            assert not invalid,invalid
            page.get_by_role('button',name='Сохранить заказ',exact=True).click()
            try:
                page.wait_for_url('**/ztl/orders/?**',timeout=10000)
            except Exception:
                print('ORDER_SUBMIT_FAILED',page.url,page.locator('body').inner_text())
                raise
            assert 'Синтетический заказ '+str(width) in page.locator('body').inner_text()
            row=page.locator('.ztl-orders tbody tr').filter(has_text='LAB-BROWSER-'+str(width))
            edit_url=origin+row.get_by_role('link',name='Изменить',exact=True).get_attribute('href')
            before='\n'.join(fixture.c.iterdump())
            # AZ-238-01/02: real form submissions must fail atomically, including
            # the second step after an attempted removal of the completion date.
            for change,message in [('clear-date','Снять отметку изготовления'),
                                   ('quantity','нельзя менять изделие или количество'),
                                   ('no-technicians','Выберите хотя бы одного техника.')]:
                page.goto(edit_url)
                assert page.locator('[name=technicians_present]').input_value()=='1'
                assert page.locator('[name=amount]').input_value()=='500'
                assert page.locator('[name=price_key]').input_value()=='external:'+str(fixture.work)
                if change=='clear-date':page.locator('[name=completed_on]').fill('')
                elif change=='quantity':page.locator('[name=pieces]').fill('3')
                else:
                    for checkbox in page.locator('[name=technician_id]').all():checkbox.uncheck()
                with page.expect_response(lambda response: response.request.method=='POST' and '/ztl/save/order' in response.url) as response:
                    page.get_by_role('button',name='Сохранить заказ',exact=True).click()
                assert response.value.status==400,(change,response.value.status)
                assert message in page.locator('body').inner_text()
                assert '\n'.join(fixture.c.iterdump())==before,change
            page.goto(edit_url)
            assert page.locator('[name=completed_on]').input_value()=='2026-11-16'
            assert page.locator('[name=pieces]').input_value()=='2'
            assert page.locator('[name=technician_id]:checked').count()==1
            page.get_by_role('button',name='Сохранить заказ',exact=True).click()
            page.wait_for_url('**/ztl/orders/?**')
            print('AZ_238_01_02_BROWSER',width,'PASS; unchanged after each rejected request')
            page.screenshot(path=str(artifacts/('orders-'+str(width)+'.png')),full_page=True)
            page.locator('#technician').select_option(str(fixture.technician))
            page.get_by_role('button',name='Показать',exact=True).click()
            assert 'Синтетический заказ '+str(width) in page.locator('body').inner_text()
            page.get_by_role('link',name='← К разделам ЗТЛ',exact=True).click()
            menu.get_by_role('link',name='ФОТ',exact=True).click()
            assert 'LAB-BROWSER-'+str(width) in page.locator('body').inner_text()
            assert page.locator('table').first.locator('tbody tr').filter(has_text='LAB-BROWSER-'+str(width)).count()==1
            page.screenshot(path=str(artifacts/('payroll-'+str(width)+'.png')),full_page=True)
            page.get_by_role('link',name='← К разделам ЗТЛ',exact=True).click()
            menu.get_by_role('link',name='Инвентаризация',exact=True).click()
            page.get_by_role('button',name='Начать инвентаризацию').click()
            for stock_item in control.stock(fixture.c):
                page.locator('[name=count_'+str(stock_item['id'])+']').fill(str(stock_item['qty']/1000))
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
