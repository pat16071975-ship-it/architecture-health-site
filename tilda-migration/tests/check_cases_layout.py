from pathlib import Path
from playwright.sync_api import sync_playwright
root = Path('tilda-migration/sections/cases')
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox', '--allow-file-access-from-files'])
    for width in (390, 768, 1366):
        page = browser.new_page(viewport={'width':width,'height':900})
        def route(req):
            from urllib.parse import urlparse
            name = urlparse(req.request.url).path
            if name.startswith('/cases/'):
                p = root / name[7:]
                if p.is_dir(): p = p / 'index.html'
                if p.is_file(): return req.fulfill(path=p)
            return req.abort()
        page.route('**/*', route)
        page.goto('https://azmednsk.ru/cases/')
        assert page.locator('.case-portrait svg image').count() == 10
        links = page.locator('main a[href^="/cases/"]').evaluate_all('(links)=>[...new Set(links.map(a=>a.href))]')
        assert len(links)==11
        count = 0
        for url in links:
            page.goto(url)
            for btn in page.locator('.case-photo').all():
                metrics = btn.locator(':scope > svg').evaluate("""s => {
                    const r=s.getBoundingClientRect(),b=s.viewBox.baseVal;
                    return [r.width,r.height,b.width/b.height,s.parentElement.clientWidth];
                }""")
                w,h,ratio,card=metrics
                assert abs(w/h-ratio)<0.02 and card-w<=17 and w>0, (url,width,metrics)
                if width==1366 and 'mezialnyj' in url: print(btn.inner_text(), round(w), round(h), flush=True)
                if width==1366 and 'mezialnyj' in url and btn.inner_text() in ('Профиль','Осанка'): assert h>350
                btn.click()
                assert page.locator('#case-viewer:modal').is_visible()
                page.get_by_role('button',name='Закрыть ×').click()
                count+=1
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert count==30
        print(width, 'PASS', flush=True)
        page.close()
    browser.close()
