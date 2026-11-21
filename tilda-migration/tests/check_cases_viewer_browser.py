import asyncio,hashlib
from pathlib import Path
from playwright.async_api import async_playwright
from urllib.request import urlopen
from concurrent.futures import ThreadPoolExecutor

import re
root=Path('tilda-migration/sections/cases')
async def run():
 async with async_playwright() as pw:
  browser=await pw.chromium.launch(executable_path='/usr/bin/google-chrome',args=['--no-sandbox','--disable-dev-shm-usage'])
  async def route(r):
   from urllib.parse import urlparse
   name=urlparse(r.request.url).path
   if name=='/site.js':return await r.fulfill(path='/tmp/azsitejs',content_type='application/javascript')
   if name.startswith('/cases/'):
    rel=name[7:];p=root/rel if not rel.startswith('assets/full') else Path('/tmp/azsrc/data')/rel
    if p.is_dir():p=p/'index.html'
    if p.is_file():return await r.fulfill(path=p)
   await r.abort()
  for width in (390,1366):
   page=await browser.new_page(viewport={'width':width,'height':850});await page.route('**/*',route)
   count=0
   for p in sorted(root.glob('*/index.html')):
    await page.goto('https://azmednsk.ru/cases/'+p.parent.name+'/')
    await page.evaluate("document.querySelector('.cookie-accept')?.click()")
    for i,btn in enumerate(await page.locator('.case-gallery button').all()):
     await btn.click();viewer=page.locator('#case-viewer:modal');assert await viewer.is_visible()
     if await btn.get_attribute('data-sheet'):await viewer.locator('img').evaluate('(im)=>im.decode()')
     assert await viewer.locator('button').is_visible()
     if i%3==0:await viewer.locator('button').click()
     elif i%3==1:await page.keyboard.press('Escape')
     else:await page.mouse.click(1,1)
     assert not await viewer.count() and await btn.evaluate('b=>document.activeElement===b')
     count+=1
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   print(width,count,'open/close PASS',flush=True);await page.close()
  await browser.close()
asyncio.run(run())

