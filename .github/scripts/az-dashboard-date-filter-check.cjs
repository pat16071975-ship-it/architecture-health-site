const { chromium } = require('playwright');
const fs = require('fs');

function fail(message){ throw new Error(message); }

const DATA = {
  '2026-08-15': {
    date:'2026-08-15', plan:4000, cashTotal:1150, factMedicine:1000, factLab:150,
    primary:10, repeat:20, dentPrimary:6, dentRepeat:12, dentists:{'A':700},
    clinicPrimary:4, clinicRepeat:8, clinicDocs:{'B':300}, labOrders:3, labRevenue:150,
    leadsDent:10, leadsClinic:8, leadsReserve:0
  },
  '2026-08-31': {
    date:'2026-08-31', plan:4000, cashTotal:3100, factMedicine:2800, factLab:300,
    primary:25, repeat:50, dentPrimary:15, dentRepeat:30, dentists:{'A':1900},
    clinicPrimary:10, clinicRepeat:20, clinicDocs:{'B':900}, labOrders:6, labRevenue:300,
    leadsDent:24, leadsClinic:18, leadsReserve:0
  },
  '2026-09-15': {
    date:'2026-09-15', plan:5000, cashTotal:1500, factMedicine:1300, factLab:200,
    primary:12, repeat:22, dentPrimary:7, dentRepeat:13, dentists:{'A':850},
    clinicPrimary:5, clinicRepeat:9, clinicDocs:{'B':450}, labOrders:4, labRevenue:200,
    leadsDent:12, leadsClinic:9, leadsReserve:0
  },
  '2026-09-23': {
    date:'2026-09-23', plan:5000, cashTotal:2300, factMedicine:2000, factLab:300,
    primary:20, repeat:38, dentPrimary:12, dentRepeat:23, dentists:{'A':1300},
    clinicPrimary:8, clinicRepeat:15, clinicDocs:{'B':700}, labOrders:6, labRevenue:300,
    leadsDent:20, leadsClinic:15, leadsReserve:0
  }
};

async function stub(context){
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if(url.hostname !== '127.0.0.1') return route.abort();
    if(url.pathname === '/api/reports/data'){
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({data:DATA})});
    }
    if(url.pathname === '/api/reports/finrez'){
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({expenses:{available:false}})});
    }
    return route.continue();
  });
}

(async()=>{
  fs.mkdirSync('artifacts',{recursive:true});
  const browser=await chromium.launch({headless:true});

  const context=await browser.newContext({viewport:{width:1440,height:900}});
  await stub(context);
  const page=await context.newPage();
  await page.goto('http://127.0.0.1:4173/reports/dashboard.html',{waitUntil:'domcontentloaded',timeout:10000});
  await page.waitForSelector('#app:not(.hidden)',{timeout:5000});

  const initial=await page.evaluate(()=>({
    month:document.querySelector('#period')?.value,
    date:document.querySelector('#dateFilter')?.value,
    min:document.querySelector('#dateFilter')?.min,
    max:document.querySelector('#dateFilter')?.max,
    revenue:document.querySelector('#metrics .metric strong')?.textContent.trim(),
    direction:document.querySelector('#directionHint')?.textContent.trim()
  }));
  if(initial.month!=='2026-09') fail('Initial month is not latest month');
  if(initial.date!=='') fail('Date filter must be optional/blank initially');
  if(initial.min!=='2026-09-15'||initial.max!=='2026-09-23') fail('Date bounds do not match available September data');
  if(!initial.revenue.includes('2 300')) fail('Latest month record not rendered initially');
  if(!initial.direction.includes('23.09.2026')) fail('Latest September date not rendered');

  await page.fill('#dateFilter','2026-09-15');
  await page.dispatchEvent('#dateFilter','change');
  await page.waitForTimeout(80);
  const dated=await page.evaluate(()=>({
    revenue:document.querySelector('#metrics .metric strong')?.textContent.trim(),
    direction:document.querySelector('#directionHint')?.textContent.trim(),
    chartHint:document.querySelector('#chartHint')?.textContent.trim(),
    bars:document.querySelectorAll('#chart .barcol').length
  }));
  if(!dated.revenue.includes('1 500')) fail('Specific date did not change dashboard metrics');
  if(!dated.direction.includes('15.09.2026')) fail('Specific date did not change direction slice');
  if(!dated.chartHint.includes('15-е число')) fail('Chart did not switch to same-day monthly comparison');
  if(dated.bars!==4) fail('Same-day chart should contain two months x two bars');

  await page.click('#dateClear');
  await page.waitForTimeout(50);
  const cleared=await page.evaluate(()=>({
    date:document.querySelector('#dateFilter')?.value,
    revenue:document.querySelector('#metrics .metric strong')?.textContent.trim()
  }));
  if(cleared.date!=='') fail('Whole-month reset did not clear date');
  if(!cleared.revenue.includes('2 300')) fail('Whole-month reset did not restore latest month slice');

  await page.selectOption('#period','2026-08');
  await page.waitForTimeout(50);
  const august=await page.evaluate(()=>({
    date:document.querySelector('#dateFilter')?.value,
    min:document.querySelector('#dateFilter')?.min,
    max:document.querySelector('#dateFilter')?.max,
    revenue:document.querySelector('#metrics .metric strong')?.textContent.trim(),
    direction:document.querySelector('#directionHint')?.textContent.trim()
  }));
  if(august.date!=='') fail('Changing month must reset specific date');
  if(august.min!=='2026-08-15'||august.max!=='2026-08-31') fail('August date bounds are wrong');
  if(!august.revenue.includes('3 100')) fail('August latest slice not rendered');
  if(!august.direction.includes('31.08.2026')) fail('August latest date not rendered');

  await page.fill('#dateFilter','2026-08-15');
  await page.dispatchEvent('#dateFilter','change');
  await page.waitForTimeout(50);
  const augDate=await page.textContent('#metrics .metric strong');
  if(!String(augDate).includes('1 150')) fail('August specific date not rendered');

  await page.screenshot({path:'artifacts/dashboard-date-filter-desktop.png',fullPage:false});
  console.log('DASHBOARD DATE FILTER DESKTOP: PASS');
  await context.close();

  const mobileContext=await browser.newContext({viewport:{width:390,height:844}});
  await stub(mobileContext);
  const mobile=await mobileContext.newPage();
  await mobile.goto('http://127.0.0.1:4173/reports/dashboard.html',{waitUntil:'domcontentloaded',timeout:10000});
  await mobile.waitForSelector('#app:not(.hidden)',{timeout:5000});
  const mobileState=await mobile.evaluate(()=>({
    bodyOverflow:document.documentElement.scrollWidth-window.innerWidth,
    month:!!document.querySelector('#period'),
    date:!!document.querySelector('#dateFilter'),
    clear:!!document.querySelector('#dateClear'),
    barWidth:document.querySelector('.bar')?.getBoundingClientRect().width||0,
    viewport:window.innerWidth
  }));
  if(mobileState.bodyOverflow>2) fail('Mobile page has horizontal body overflow');
  if(!mobileState.month||!mobileState.date||!mobileState.clear) fail('Mobile filters are incomplete');
  if(mobileState.barWidth>mobileState.viewport+2) fail('Mobile filter bar exceeds viewport');
  await mobile.screenshot({path:'artifacts/dashboard-date-filter-mobile.png',fullPage:false});
  console.log('DASHBOARD DATE FILTER MOBILE: PASS');

  await mobileContext.close();
  await browser.close();
})().catch(error=>{console.error(error.stack||error);process.exit(1);});
