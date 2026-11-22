"""Real browser navigation, owner delegation, reasons, review and read-only drilldown."""
import os
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from test_control_center import OwnerControlTests
import control_center as oversight
import ztl
import ztl_control

fixture=OwnerControlTests();fixture.setUp()
fixture.web.config['SESSION_COOKIE_SECURE']=False
server=make_server('127.0.0.1',0,fixture.web,threaded=True)
thread=Thread(target=server.serve_forever,daemon=True);thread.start()
origin='http://127.0.0.1:'+str(server.server_port)
artifacts=Path(os.environ.get('ZTL_BROWSER_ARTIFACTS','ztl-browser-artifacts'));artifacts.mkdir(exist_ok=True)
try:
    item=fixture.item();fixture.receipt(item);fixture.card(item,qty='0.5')
    oid=fixture.order(pieces='2')
    original=tuple(fixture.c.execute('SELECT * FROM ztl_production WHERE order_id=?',(oid,)).fetchone())
    inventory=ztl_control.inventory_action(fixture.c,'start',{},1)
    for action,fields in [('count',{'count_'+str(item):'9'}),('recheck',{}),('count',{'count_'+str(item):'9'}),
                          ('explain',dict(person='Первый ответственный',explanation='Недостача при пересчёте'))]:
        version=ztl.row_by_id(fixture.c,'ztl_inventories',inventory)['version']
        ztl_control.inventory_action(fixture.c,action,dict(id=inventory,version=version,**fields),1)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
        for width in (1365,390):
            page=browser.new_page(viewport={'width':width,'height':950});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if urlsplit(r.request.url).hostname=='127.0.0.1' else r.abort())
            def login(uid):
                page.context.clear_cookies()
                c=fixture.client(uid)
                page.context.add_cookies([{'name':'session','value':c.get_cookie('session').value,'url':origin}])
            login(3)
            assert page.goto(origin+'/control/').status==403
            page.goto(origin+'/');assert page.locator('#control').count()==0
            login(2)
            page.goto(origin+'/accounting/ztl/products/')
            entry=page.locator('[data-product-entry="'+str(fixture.work)+'"]')
            entry.locator('[data-edit-product]').first.click()
            form=entry.locator('[data-product-form]')
            old=form.locator('[data-material-qty]').input_value()
            wanted='0.6' if old in ('0.5','0,5') else '0.7'
            form.locator('[data-material-qty]').fill(wanted)
            before=fixture.snapshot()
            with page.expect_response(lambda r:r.request.method=='POST' and '/control/card' in r.url) as response:
                form.get_by_role('button',name='Сохранить изменения',exact=True).click()
            assert response.value.status==400
            assert 'Укажите причину' in form.locator('[data-save-error]').inner_text()
            assert fixture.snapshot()==before
            form.locator('[name=change_reason]').fill('Уточнена норма после пробного изготовления '+str(width))
            with page.expect_navigation(wait_until='networkidle'):
                form.get_by_role('button',name='Сохранить изменения',exact=True).click()
            assert tuple(fixture.c.execute('SELECT * FROM ztl_production WHERE order_id=?',(oid,)).fetchone())==original
            event=fixture.c.execute("SELECT MAX(id) FROM ztl_history WHERE entity='Технологическая карта'").fetchone()[0]
            login(1)
            page.goto(origin+'/');page.get_by_role('link',name='Контроль',exact=True).click()
            page.get_by_role('navigation',name='Разделы контроля').get_by_role('link').click()
            page.locator('.filters [name=start]').fill('2000-01-01');page.locator('.filters [name=end]').fill('2090-12-31')
            page.get_by_role('button',name='Показать',exact=True).click()
            ev=page.locator('[data-event="'+str(event)+'"]');ev.locator('summary').first.click()
            assert 'Уточнена норма' in ev.inner_text();assert 'Начальник ЗТЛ' in ev.inner_text()
            assert wanted in ev.locator('.new').all_inner_texts()
            if width==1365:assert '+0.1 / 20%' in ev.inner_text()
            page.screenshot(path=str(artifacts/('owner-control-layout-'+str(width)+'.png')),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),page.locator('body *').evaluate_all("els=>els.filter(e=>e.getBoundingClientRect().right>innerWidth+1&&!e.closest('.scroll')).map(e=>({tag:e.tagName,cls:e.className,name:e.getAttribute('name'),width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right})).slice(0,20)")
            page.screenshot(path=str(artifacts/('owner-control-event-'+str(width)+'.png')),full_page=True)
            ev.locator('[name=status]').select_option('question');ev.locator('[name=comment]').fill('Проверить норму по пробному изделию')
            ev.get_by_role('button',name='Сохранить проверку').click()
            assert fixture.c.execute('SELECT status FROM control_reviews WHERE history_id=?',(event,)).fetchone()[0]=='question'
            page.get_by_role('link',name='Работа и заполненность',exact=True).click()
            page.locator('[name=actor]').select_option('2');page.get_by_role('button',name='Показать',exact=True).click()
            assert 'Начальник ЗТЛ' in page.locator('[name=actor] option:checked').inner_text()
            before=fixture.snapshot()
            page.locator('.metric').filter(has_text='Изменено карт за период').click()
            page.locator('.records a').first.click()
            assert page.get_by_role('heading',name='Текущее состояние',exact=True).count()==1
            assert page.locator('input,textarea,select').count()==0
            assert fixture.snapshot()==before
            page.goto(origin+'/control/ztl/?view=work&start=2000-01-01&end=2090-12-31&actor=2')
            page.screenshot(path=str(artifacts/('owner-control-layout-'+str(width)+'.png')),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),page.locator('body *').evaluate_all("els=>els.filter(e=>e.getBoundingClientRect().right>innerWidth+1&&!e.closest('.scroll')).map(e=>({tag:e.tagName,cls:e.className,name:e.getAttribute('name'),width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right})).slice(0,20)")
            page.screenshot(path=str(artifacts/('owner-control-work-'+str(width)+'.png')),full_page=True)
            page.get_by_role('link',name='Доступ к контролю',exact=True).click()
            form=page.locator('form.access').filter(has=page.locator('input[name=user_id][value="2"]'))
            form.locator('[name=mode]').select_option('view');form.get_by_role('button',name='Сохранить доступ').click()
            login(2);page.goto(origin+'/');page.get_by_role('link',name='Контроль',exact=True).click()
            page.get_by_role('navigation',name='Разделы контроля').get_by_role('link').click()
            assert page.locator('form.review').count()==0
            assert page.get_by_role('link',name='Доступ к контролю',exact=True).count()==0
            assert page.goto(origin+'/control/access/').status==403
            login(1);page.goto(origin+'/control/access/')
            form=page.locator('form.access').filter(has=page.locator('input[name=user_id][value="2"]'))
            form.locator('[name=mode]').select_option('none');form.get_by_role('button',name='Сохранить доступ').click()
            login(2);assert page.goto(origin+'/control/').status==403
            # AZ-250-01: real cancellation form, then its actual owner-visible diff.
            values=ztl.payment_values(fixture.c,dict(party_id=fixture.party,scope='external',amount='100',
                paid_date='2026-11-15',method='bank',comment='Проверка отмены',order_id=oid))
            pid=ztl.save_payment(fixture.c,values,(None,None,None),1,ztl.ledger_stamp(fixture.c),'browser-cancel-'+str(width))
            old_payment=ztl.row_by_id(fixture.c,'ztl_payments',pid)
            page.goto(origin+'/accounting/ztl/payments/')
            form=page.locator('form[action$="/void"]').filter(has=page.locator('input[name=id][value="'+str(pid)+'"]'))
            form.locator('xpath=ancestor::details[2]').locator('summary').first.click()
            form.locator('xpath=ancestor::details[1]').locator('summary').first.click()
            reason='Ошибка повторного ввода '+str(width)
            form.locator('[name=reason]').fill(reason)
            with page.expect_navigation(wait_until='networkidle'):
                form.get_by_role('button',name='Отменить ошибочную запись',exact=True).click()
            assert ztl.row_by_id(fixture.c,'ztl_payments',pid)=={**old_payment,'version':old_payment['version']+1,'void_reason':reason}
            event=fixture.c.execute("SELECT MAX(id) FROM ztl_history WHERE entity='Оплата'").fetchone()[0]
            login(1);page.goto(origin+'/control/ztl/?start=2000-01-01&end=2090-12-31&category=payments')
            ev=page.locator('[data-event="'+str(event)+'"]');ev.locator('summary').first.click()
            assert set(ev.locator('.diff tbody tr td:first-child').all_inner_texts())=={'Оплата отменена','Причина отмены'}
            assert reason in ev.locator('.reason').first.inner_text()
            assert '2026-11-15' in ev.inner_text()
            page.screenshot(path=str(artifacts/('az-250-01-'+str(width)+'.png')),full_page=True)
            # AZ-250-02: change only the responsible person, preserve explanation text.
            login(2);page.goto(origin+'/accounting/ztl/inventory/')
            form=page.locator('form[action$="/inventory-explain"]')
            old_inventory=ztl.row_by_id(fixture.c,'ztl_inventories',inventory)
            person='Ответственный '+str(width)
            form.locator('[name=person]').fill(person)
            before=fixture.snapshot()
            with page.expect_navigation(wait_until='networkidle') as response:
                form.get_by_role('button',name='Сохранить объяснительную',exact=True).click()
            assert response.value.status==400
            assert 'Укажите причину' in page.locator('body').inner_text()
            assert fixture.snapshot()==before
            page.goto(origin+'/accounting/ztl/inventory/')
            form=page.locator('form[action$="/inventory-explain"]')
            form.locator('[name=person]').fill(person)
            form.locator('[name=change_reason]').fill('Исправлено ФИО по акту '+str(width))
            with page.expect_navigation(wait_until='networkidle'):
                form.get_by_role('button',name='Сохранить объяснительную',exact=True).click()
            assert ztl.row_by_id(fixture.c,'ztl_inventories',inventory)=={**old_inventory,'person':person,'version':old_inventory['version']+1}
            event=fixture.c.execute("SELECT MAX(id) FROM ztl_history WHERE entity='Инвентаризация'").fetchone()[0]
            login(1);page.goto(origin+'/control/ztl/?start=2000-01-01&end=2090-12-31&category=inventory')
            ev=page.locator('[data-event="'+str(event)+'"]');ev.locator('summary').first.click()
            assert ev.locator('.diff tbody tr td:first-child').all_inner_texts()==['Ответственный']
            assert ev.locator('.new').inner_text()==person
            assert 'Исправлено ФИО по акту' in ev.locator('.reason').first.inner_text()
            page.screenshot(path=str(artifacts/('az-250-02-'+str(width)+'.png')),full_page=True)
            print('AZ_250_01_02_BROWSER',width,'PASS; cancellation snapshots, separate person edit reason, complete rollback')
            assert errors==[],errors
            page.close();print('OWNER_CONTROL_BROWSER',width,'PASS; entry, reason, snapshot, review, work drilldown, delegated view and revoke')
        browser.close()
finally:
    server.shutdown();thread.join(timeout=5);fixture.doCleanups()
