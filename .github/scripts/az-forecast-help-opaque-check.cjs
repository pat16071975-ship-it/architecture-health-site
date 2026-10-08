const { chromium } = require('playwright');
const fs = require('fs');

function fail(message){ throw new Error(message); }
function opaque(value){
  if(!value || value === 'transparent') return false;
  const m = value.match(/^rgba?\(([^)]+)\)$/);
  if(!m) return true;
  const parts = m[1].split(',').map(x=>x.trim());
  return parts.length < 4 || Number(parts[3]) >= 0.999;
}

function month(date,cash,dentPrimary,dentRepeat,clinicPrimary,clinicRepeat,dentRev,structureRev,labRev){
  return {
    date,cashTotal:cash,factMedicine:dentRev+structureRev,factLab:labRev,
    dentPrimary,dentRepeat,dentists:{'Тестовый стоматолог':dentRev},
    clinicPrimary,clinicRepeat,structureDoctors:{'Тестовый специалист':structureRev}
  };
}

const FIN = {
  management: {
    '2026-06': month('2026-06-30',900000,18,36,9,18,430000,360000,110000),
    '2026-07': month('2026-07-31',1000000,20,40,10,20,480000,390000,130000),
    '2026-08': month('2026-08-31',1100000,22,44,11,22,520000,430000,150000),
    '2026-09': month('2026-09-30',1200000,24,48,12,24,560000,480000,160000),
    '2026-10': month('2026-10-02',132140,1,8,0,4,107560,13500,0)
  },
  expenses: {
    months: {
      '2026-06': {operating:{base:{total:300000}}},
      '2026-07': {operating:{base:{total:350000}}},
      '2026-08': {operating:{base:{total:400000}}},
      '2026-09': {operating:{base:{total:450000}}},
      '2026-10': {operating:{base:{total:0}}}
    }
  }
};

async function stub(context){
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if(url.hostname !== '127.0.0.1') return route.abort();
    if(url.pathname === '/api/reports/finrez'){
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(FIN)});
    }
    if(url.pathname === '/api/reports/storage'){
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({storage:{}})});
    }
    return route.continue();
  });
}

async function state(page){
  return page.evaluate(() => {
    const sels=['.controlbar','.panel','.metric','.scenario-plan','.result-card','.decision','.advanced','.method','.partial-strip','.table-wrap'];
    const backgrounds={};
    for(const sel of sels){
      const el=document.querySelector(sel);
      backgrounds[sel]=el?getComputedStyle(el).backgroundColor:null;
    }
    return {
      baseMetrics:document.querySelectorAll('#baseSummary .metric').length,
      baseDirections:document.querySelectorAll('#baseDirections .current-direction').length,
      planners:document.querySelectorAll('.scenario-plan').length,
      results:document.querySelectorAll('.result-card').length,
      decisions:document.querySelectorAll('.decision').length,
      advancedOpen:document.querySelector('#advancedSettings')?.open || false,
      methodExists:!!document.querySelector('#forecastMethod'),
      partialText:document.querySelector('#partialCurrent')?.textContent.replace(/\s+/g,' ').trim() || '',
      periodText:document.querySelector('#periodSummary')?.textContent.replace(/\s+/g,' ').trim() || '',
      baseFirst:document.querySelector('#baseSummary .metric strong')?.textContent.trim() || '',
      baseText:document.querySelector('#baseSummary')?.textContent.replace(/\s+/g,' ').trim() || '',
      reconcileText:document.querySelector('#baseReconcile')?.textContent.replace(/\s+/g,' ').trim() || '',
      bodyOverflow:document.documentElement.scrollWidth-window.innerWidth,
      backgrounds
    };
  });
}

(async()=>{
  fs.mkdirSync('artifacts',{recursive:true});
  const browser=await chromium.launch({headless:true});

  const context=await browser.newContext({viewport:{width:1440,height:1100}});
  await stub(context);
  const page=await context.newPage();
  await page.goto('http://127.0.0.1:4173/reports/forecast.html',{waitUntil:'domcontentloaded',timeout:10000});
  await page.waitForFunction(() => document.querySelector('#app')?.style.display === 'block', null, {timeout:5000});

  let s=await state(page);
  if(s.baseMetrics!==4) fail('Expected 4 base metrics after removing user-facing unallocated cash, got '+s.baseMetrics);
  if(s.baseText.includes('Нераспределённые ДС')) fail('User-facing unallocated cash must be absent: '+s.baseText);
  if(s.reconcileText.trim()!=='') fail('Obsolete cash-to-direction reconciliation must be absent: '+s.reconcileText);
  if(s.baseDirections!==3) fail('Expected 3 base direction cards, got '+s.baseDirections);
  if(!s.periodText.includes('01.07.2026 — 30.09.2026')) fail('Default base must be last 3 full months: '+s.periodText);
  if(!s.periodText.includes('3 полных мес.')) fail('Default period month count missing: '+s.periodText);
  const normalizedBaseFirst=s.baseFirst.replace(/\u00a0/g,' ');
  if(normalizedBaseFirst!=='1 100 000 ₽') fail('Monthly average revenue must be 1 100 000 ₽, got '+s.baseFirst);
  if(!s.partialText.includes('октябрь 2026')) fail('Current incomplete month is not shown separately: '+s.partialText);
  if(!s.partialText.includes('132 140 ₽')) fail('Current incomplete month revenue missing: '+s.partialText);
  if(!s.partialText.includes('не включён в базу прогноза')) fail('Partial month exclusion warning missing');

  if(s.planners!==3) fail('Expected 3 editable scenario plans, got '+s.planners);
  if(s.results!==3) fail('Expected 3 scenario result cards, got '+s.results);
  if(s.decisions!==3) fail('Expected 3 scenario decisions, got '+s.decisions);
  if(s.advancedOpen) fail('Technical settings must be closed by default');
  if(!s.methodExists) fail('Forecast method block is missing');
  if(s.bodyOverflow>2) fail('Desktop page has horizontal body overflow');
  for(const [sel,bg] of Object.entries(s.backgrounds)){
    if(bg && !opaque(bg)) fail('Transparent surface remains: '+sel+' = '+bg);
  }

  await page.click('.period-btn[data-months="6"]');
  await page.waitForTimeout(40);
  s=await state(page);
  if(!s.periodText.includes('01.06.2026 — 30.09.2026')) fail('6-month preset should use all 4 available full months: '+s.periodText);
  if(!s.periodText.includes('доступно только 4')) fail('Available-month notice missing: '+s.periodText);

  await page.click('.period-btn[data-months="custom"]');
  await page.selectOption('#periodFrom','2026-06');
  await page.selectOption('#periodTo','2026-08');
  await page.waitForTimeout(40);
  s=await state(page);
  if(!s.periodText.includes('01.06.2026 — 31.08.2026')) fail('Custom full-month period did not apply: '+s.periodText);

  await page.click('.period-btn[data-months="3"]');
  await page.waitForTimeout(40);

  const before=await page.locator('.result-card[data-scenario="base"] .result-main strong').first().innerText();
  await page.check('#on-base-dent');
  await page.fill('#prim-base-dent','10');
  await page.fill('#mkt-base-dent','50000');
  await page.waitForTimeout(60);
  const after=await page.locator('.result-card[data-scenario="base"] .result-main strong').first().innerText();
  if(before===after) fail('Base scenario result did not react to management inputs');

  await page.fill('#prim-base-dent','1000');
  await page.waitForTimeout(60);
  const decision=await page.locator('[data-decision="base"] .badge').innerText();
  if(decision!=='Упирается в мощность') fail('Capacity warning not triggered: '+decision);

  await page.fill('#capadd-base-dent','3000');
  await page.waitForTimeout(60);
  const recovered=await page.locator('[data-decision="base"] .badge').innerText();
  if(recovered==='Упирается в мощность') fail('Added capacity did not clear overload warning');

  await page.click('#advancedSettings > summary');
  if(!(await page.locator('#advancedSettings').evaluate(el=>el.open))) fail('Advanced settings did not open');
  if(await page.locator('#advancedGrid .advanced-direction').count()!==3) fail('Expected 3 advanced direction settings');

  await page.screenshot({path:'artifacts/forecast-period-base-desktop.png',fullPage:false});
  console.log('FORECAST PERIOD BASE: PASS');
  console.log('FORECAST PARTIAL MONTH EXCLUSION: PASS');
  console.log('FORECAST CUSTOM PERIOD: PASS');
  console.log('FORECAST MANAGEMENT DESKTOP: PASS');
  await context.close();

  const mobileContext=await browser.newContext({viewport:{width:390,height:844}});
  await stub(mobileContext);
  const mobile=await mobileContext.newPage();
  await mobile.goto('http://127.0.0.1:4173/reports/forecast.html',{waitUntil:'domcontentloaded',timeout:10000});
  await mobile.waitForFunction(() => document.querySelector('#app')?.style.display === 'block', null, {timeout:5000});
  const mobileState=await mobile.evaluate(()=>({
    bodyOverflow:document.documentElement.scrollWidth-window.innerWidth,
    advancedOpen:document.querySelector('#advancedSettings')?.open || false,
    partial:document.querySelector('#partialCurrent')?.textContent || ''
  }));
  if(mobileState.bodyOverflow>2) fail('Mobile forecast has horizontal body overflow');
  if(mobileState.advancedOpen) fail('Mobile advanced settings must remain closed by default');
  if(!mobileState.partial.includes('не включён в базу прогноза')) fail('Mobile partial-month note missing');
  await mobile.screenshot({path:'artifacts/forecast-period-base-mobile.png',fullPage:false});
  console.log('FORECAST MANAGEMENT MOBILE: PASS');

  await mobileContext.close();
  await browser.close();
})().catch(error=>{console.error(error.stack||error);process.exit(1);});
