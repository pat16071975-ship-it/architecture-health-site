from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
root = Path('tilda-migration/sections/team/besedina-maria')
profile = 'https://azmednsk.ru/team/besedina-maria/'
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
    for width in (390,1366):
        page = browser.new_page(viewport={'width':width,'height':900})
        def route(req):
            name = urlparse(req.request.url).path
            if name.startswith('/team/besedina-maria/'):
                path = root/name.removeprefix('/team/besedina-maria/')
                if path.is_dir(): path=path/'index.html'
                if path.is_file(): return req.fulfill(path=path)
            return req.abort()
        page.route('**/*', route)
        page.goto(profile)
        page.get_by_role('link',name='Сертификаты',exact=False).click()
        assert page.url==profile+'certificates/'
        cards=page.locator('.az-cert-item')
        assert cards.count()==15
        for card in cards.all():
            card.locator('.az-cert-thumb').click()
            dialog=page.locator('#az-certificate-dialog:modal')
            im=dialog.locator('img')
            im.evaluate('im=>im.decode()')
            assert im.evaluate('im=>im.naturalWidth>800')
            zoom=dialog.get_by_role('button',name='Увеличить')
            zoom.click()
            assert dialog.get_by_role('button',name='Уместить').is_visible()
            dialog.get_by_role('button',name='Закрыть сертификат').click()
            assert not dialog.count()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.get_by_role('link',name='О враче и запись на приём').click()
        assert page.url==profile
        print('WIDTH',width,'15/15 OPEN, DECODE, ZOOM, CLOSE, RETURN PASS',flush=True)
        page.close()
    browser.close()
