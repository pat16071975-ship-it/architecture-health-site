"""Actual menu settings UI, shared order and permissions on 1365/390 px."""
import os
import unittest
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from test_menu_order import MenuFixture, PATH, MENU_PAGES
import menu_order

class Fixture(MenuFixture, unittest.TestCase): pass
fixture=Fixture(); fixture.setUp()
server=make_server('127.0.0.1',0,fixture.web,threaded=True)
Thread(target=server.serve_forever,daemon=True).start()
origin='http://127.0.0.1:'+str(server.server_port)
artifacts=Path('menu-order-browser-artifacts'); artifacts.mkdir(exist_ok=True)
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
        for width in (1365,390):
            with fixture.conn:
                fixture.conn.execute('DELETE FROM navigation_order')
            context=browser.new_context(viewport={'width':width,'height':950})
            context.add_cookies([{'name':'session','value':fixture.client(1).get_cookie('session').value,'url':origin}])
            context.route('**/*',lambda r:r.continue_() if urlsplit(r.request.url).hostname=='127.0.0.1' else r.abort())
            page=context.new_page(); errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin+'/'); page.get_by_role('button',name='Закрыть',exact=True).click()
            page.get_by_role('link',name='Настройка клиник',exact=True).click()
            page.get_by_role('link',name='Порядок плашек разделов',exact=True).click()
            assert page.url.startswith(origin+PATH)
            assert page.locator('.order-row').count()==len(menu_order.ids('home'))
            for scope,hub in MENU_PAGES.items():
                with page.expect_navigation(): page.locator('#order-menu').select_option(scope)
                rows=page.locator('.order-row'); original=rows.evaluate_all('rows=>rows.map(r=>r.dataset.key)')
                assert original==menu_order.ids(scope)
                if len(original)>1:
                    row=rows.last
                    # Move the last item to the top with the controls, not API/DOM edits.
                    for _ in range(len(original)-1): row=page.locator('[data-key="'+original[-1]+'"]'); row.locator('[data-move="-1"]').click()
                wanted=[original[-1]]+original[:-1] if len(original)>1 else original
                with page.expect_navigation(): page.get_by_role('button',name='Сохранить порядок',exact=True).click()
                assert 'Порядок сохранён' in page.locator('body').inner_text()
                page.reload(); assert rows.evaluate_all('rows=>rows.map(r=>r.dataset.key)')==wanted
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                if scope in ('home','ztl'):page.screenshot(path=str(artifacts/(scope+'-settings-'+str(width)+'.png')),full_page=True)
                opened=context.new_page(); assert opened.goto(origin+hub).status==200
                visible=fixture.tiles(opened.content(),scope,hub)
                assert [t['key'] for t in visible]==wanted
                if scope=='ztl':
                    links=opened.get_by_role('navigation',name='Разделы ЗТЛ').get_by_role('link')
                    assert all('start=2026-08' in h and 'end=2026-11' in h for h in links.evaluate_all('a=>a.map(x=>x.href)'))
                    opened.screenshot(path=str(artifacts/('ztl-menu-'+str(width)+'.png')),full_page=True)
                opened.close()
            # Staff see the same order, limited to the tiles they already have access to.
            staff=browser.new_context(viewport={'width':width,'height':950})
            staff.add_cookies([{'name':'session','value':fixture.client(2).get_cookie('session').value,'url':origin}])
            staff.route('**/*',lambda r:r.continue_() if urlsplit(r.request.url).hostname=='127.0.0.1' else r.abort())
            employee=staff.new_page(); employee.goto(origin+'/accounting/ztl/')
            labels=employee.get_by_role('navigation',name='Разделы ЗТЛ').get_by_role('link').all_text_contents()
            assert labels[0]=='Экономика' and len(labels)==5
            assert employee.goto(origin+PATH).status==403
            assert employee.goto(origin+'/accounting/ztl/receipts/').status==403
            employee.goto(origin+'/'); assert employee.locator('#control,#nn-private,#clinic-structure').count()==0
            staff.close()
            # Two open owner forms: older save must fail and keep the newer order.
            with page.expect_navigation():page.locator('#order-menu').select_option('accounting')
            stale=context.new_page(); stale.goto(page.url)
            page.locator('.order-row').first.locator('[data-move="1"]').click()
            with page.expect_navigation():page.get_by_role('button',name='Сохранить порядок',exact=True).click()
            before=fixture.snapshot()
            with stale.expect_navigation() as nav:stale.get_by_role('button',name='Сохранить порядок',exact=True).click()
            assert nav.value.status==409 and 'другом окне' in stale.locator('[role=alert]').inner_text()
            assert before==fixture.snapshot();stale.close()
            page.on('dialog',lambda d:d.accept())
            with page.expect_navigation():page.get_by_role('button',name='Вернуть исходный порядок',exact=True).click()
            assert page.locator('.order-row').evaluate_all('r=>r.map(x=>x.dataset.key)')==menu_order.ids('accounting')
            opened=context.new_page();opened.goto(origin+'/accounting/')
            assert [t['key'] for t in fixture.tiles(opened.content(),'accounting','/accounting/')]==menu_order.ids('accounting')
            assert not errors,errors
            context.close()
            print('MENU_ORDER_BROWSER',width,'PASS; 9 menus, save, reload, shared order, periods, permissions, stale form, reset')
        browser.close()
except Exception:
    if 'page' in locals() and not page.is_closed():
        try: page.screenshot(path=str(artifacts/'failure.png'),full_page=True)
        except Exception: pass
    raise
finally:
    server.shutdown();fixture.doCleanups()
