"""Isolated browser regression; all business examples synthetic, network blocked."""
import json, os, re
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parent
BASE=Path(os.environ.get('TWO_VIEWS_BASE',str(ROOT/'base')))
TARGET=Path(os.environ.get('TWO_VIEWS_TARGET',str(ROOT/'target')))
store={}
for m in range(1,11):
 for day in (27,28):
  date=f'2026-{m:02d}-{day}'
  store[date]={'date':date,'plan':10000,'factMedicine':2000+m,'cashTotal':3000+m,'factLab':1000,
   'billedTotal':4000,'cashOOO':1500,'cashIP':1500+m,'primary':3,'repeat':4,
   'paidPatientEvidenceComplete':True,'paidPatients':10,'paidDentPatients':4,'paidClinicPatients':6,
   'dentists':{},'clinicDocs':{},'labOrders':2,'labRevenue':1000}

def run(browser,source,width):
 context=browser.new_context(viewport={'width':width,'height':950},locale='ru-RU')
 page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 # Offline fixture: no navigation, server authentication, remote assets, or real data.
 boot='const memory=new Map([["az-management-report-v1",'+json.dumps(json.dumps(store))+']]);Object.defineProperty(window,"localStorage",{value:{getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v)}});Object.defineProperty(window,"sessionStorage",{value:{getItem:k=>"1",setItem(){},removeItem(){}}});'
 html=(source/'reports/index.html').read_text()
 html=re.sub(r'<link\b[^>]*>','',html)
 html=re.sub(r'<script src="[^\"]*period-view.js[^\"]*"></script>',lambda _: '<script>'+(source/'reports/period-view.js').read_text()+'</script>',html)
 html=re.sub(r'<script src="[^\"]*"></script>','',html)
 html=html.replace('<head>','<head><script>'+boot+'</script><style>'+(BASE/'reports/mobile-date-fix.css').read_text()+'</style>',1)
 page.route('**/*',lambda r:r.abort())
 page.set_content(html)
 page.locator('#dateViewMode').wait_for(state='visible')
 options=page.locator('#dateViewMode option').evaluate_all('(xs)=>xs.map(x=>({value:x.value,label:x.textContent}))')
 assert page.locator('#dateViewMode').input_value()=='compare'
 if source==TARGET:
  assert options==[{'value':'compare','label':'Сравнение'},{'value':'single','label':'На дату'}],options
 page.locator('#reportDate').fill('2026-10-28');page.locator('#reportDate').dispatch_event('change')
 page.wait_for_function('document.querySelector("#dateCompareTitle").textContent.includes("28")')
 assert page.locator('#dateCompareView').is_visible() and not page.locator('#editView').is_visible()
 comparison=page.locator('#dateCompareBody').inner_text()
 page.locator('#dateViewMode').select_option('single')
 assert page.locator('#editView').is_visible() and not page.locator('#dateCompareView').is_visible()
 single=page.locator('#editView [data-key]').evaluate_all('(xs)=>xs.map(x=>[x.dataset.key,x.value])')
 page.locator('#dateViewMode').select_option('compare')
 assert page.locator('#dateCompareBody').inner_text()==comparison
 assert page.evaluate('JSON.parse(localStorage.getItem("az-management-report-v1"))')==store
 page.close();page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',lambda r:r.abort());page.set_content(html);assert page.locator('#dateViewMode').input_value()=='compare'
 if source==TARGET:assert page.locator('#dateViewMode option').count()==2
 assert not errors,errors
 context.close()
 return comparison,single

with sync_playwright() as p:
 b=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
 for width in (1440,768,390):
  before=run(b,BASE,width);after=run(b,TARGET,width)
  assert before==after,'Displayed values changed at width '+str(width)
  print('BROWSER_TWO_VIEWS=PASS; WIDTH='+str(width)+'; OPTIONS=2; DEFAULT=compare; SWITCH_BOTH_DIRECTIONS=PASS; DISPLAYED_VALUES_BEFORE_AFTER=IDENTICAL; STORAGE=UNCHANGED')
 b.close()
