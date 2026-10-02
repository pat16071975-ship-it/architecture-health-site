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

const FIN = {
  management: {
    '2026-09': {
      date:'2026-09-24',
      cashTotal:1000000,
      factMedicine:900000,
      factLab:100000,
      dentPrimary:20,
      dentRepeat:40,
      dentists:{'Тестовый стоматолог':500000},
      clinicPrimary:10,
      clinicRepeat:20,
      structureDoctors:{'Тестовый специалист':300000}
    }
  },
  expenses: {
    months: {
      '2026-09': {
        operating: { base:{total:400000} }
      }
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
    const sels=['.controlbar','.panel','.metric','.scenario-plan','.result-card','.decision','.advanced','.method','.table-wrap'];
    const backgrounds={};
    for(const sel of sels){
      const el=document.querySelector(sel);
      backgrounds[sel]=el?getComputedStyle(el).backgroundColor:null;
    }
    return {
      currentMetrics:document.querySelectorAll('#currentSummary .metric').length,
      currentDirections:document.querySelectorAll('.current-direction').length,
      planners:document.querySelectorAll('.scenario-plan').length,
      results:document.querySelectorAll('.result-card').length,
      decisions:document.querySelectorAll('.decision').length,
      advancedOpen:document.querySelector('#advancedSettings')?.open || false,
      methodExists:!!document.querySelector('#forecastMethod'),
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
  if(s.currentMetrics!==4) fail('Expected 4 current clinic metrics, got '+s.currentMetrics);
  if(s.currentDirections!==3) fail('Expected 3 current direction cards, got '+s.currentDirections);
  if(s.planners!==3) fail('Expected 3 editable scenario plans, got '+s.planners);
  if(s.results!==3) fail('Expected 3 scenario result cards, got '+s.results);
  if(s.decisions!==3) fail('Expected 3 scenario decisions, got '+s.decisions);
  if(s.advancedOpen) fail('Technical settings must be closed by default');
  if(!s.methodExists) fail('Forecast method block is missing');
  if(s.bodyOverflow>2) fail('Desktop page has horizontal body overflow');
  for(const [sel,bg] of Object.entries(s.backgrounds)){
    if(bg && !opaque(bg)) fail('Transparent surface remains: '+sel+' = '+bg);
  }

  const before=await page.locator('.result-card[data-scenario="base"] .result-main strong').first().innerText();
  await page.check('#on-base-dent');
  await page.fill('#prim-base-dent','10');
  await page.fill('#mkt-base-dent','50000');
  await page.waitForTimeout(60);
  const after=await page.locator('.result-card[data-scenario="base"] .result-main strong').first().innerText();
  if(before===after) fail('Base scenario result did not react to management inputs');
  const profitText=await page.locator('.result-card[data-scenario="base"] .result-row').first().innerText();
  if(!profitText.includes('Доп. прибыль за горизонт')) fail('Cumulative horizon profit is missing');

  await page.fill('#prim-base-dent','1000');
  await page.waitForTimeout(60);
  const decision=await page.locator('[data-decision="base"] .badge').innerText();
  if(decision!=='Упирается в мощность') fail('Capacity warning not triggered for overloaded scenario: '+decision);

  await page.fill('#capadd-base-dent','3000');
  await page.waitForTimeout(60);
  const recovered=await page.locator('[data-decision="base"] .badge').innerText();
  if(recovered==='Упирается в мощность') fail('Added capacity did not clear overload warning');

  await page.click('#advancedSettings > summary');
  await page.waitForTimeout(30);
  if(!(await page.locator('#advancedSettings').evaluate(el=>el.open))) fail('Advanced settings did not open');
  if(await page.locator('#advancedGrid .advanced-direction').count()!==3) fail('Expected 3 advanced direction settings');

  await page.selectOption('#detailScenario','base');
  await page.locator('.acc-head').first().click();
  await page.waitForTimeout(30);
  if(!(await page.locator('.acc-body.open').count())) fail('Detailed monthly calculation did not open');

  await page.screenshot({path:'artifacts/forecast-management-desktop.png',fullPage:false});
  console.log('FORECAST MANAGEMENT DESKTOP: PASS');
  console.log('FORECAST SCENARIO REACTION: PASS');
  console.log('FORECAST CAPACITY WARNING: PASS');
  console.log('FORECAST ADVANCED SETTINGS: PASS');
  await context.close();

  const mobileContext=await browser.newContext({viewport:{width:390,height:844}});
  await stub(mobileContext);
  const mobile=await mobileContext.newPage();
  await mobile.goto('http://127.0.0.1:4173/reports/forecast.html',{waitUntil:'domcontentloaded',timeout:10000});
  await mobile.waitForFunction(() => document.querySelector('#app')?.style.display === 'block', null, {timeout:5000});
  const mobileState=await mobile.evaluate(()=>({
    bodyOverflow:document.documentElement.scrollWidth-window.innerWidth,
    plannerColumns:getComputedStyle(document.querySelector('#scenarioPlanner')).gridTemplateColumns,
    resultColumns:getComputedStyle(document.querySelector('#scenarioResults')).gridTemplateColumns,
    advancedOpen:document.querySelector('#advancedSettings')?.open || false
  }));
  if(mobileState.bodyOverflow>2) fail('Mobile forecast has horizontal body overflow');
  if(mobileState.advancedOpen) fail('Mobile advanced settings must remain closed by default');
  await mobile.screenshot({path:'artifacts/forecast-management-mobile.png',fullPage:false});
  console.log('FORECAST MANAGEMENT MOBILE: PASS');

  await mobileContext.close();
  await browser.close();
})().catch(error=>{console.error(error.stack||error);process.exit(1);});
