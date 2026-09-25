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

async function inspectPage(page){
  await page.waitForFunction(() => document.querySelector('#app')?.style.display === 'block', null, {timeout:5000});
  return page.evaluate(() => {
    const sels=['.bar','.method','.scenario','.scenario-body','.panel','.summary','.accordion','.acc-head','.acc-body','.metric','.card','.table-wrap','.insight'];
    const backgrounds={};
    for(const sel of sels){
      const el=document.querySelector(sel);
      backgrounds[sel]=el?getComputedStyle(el).backgroundColor:null;
    }
    return {
      helpCount: document.querySelectorAll('.field small').length,
      methodExists: !!document.querySelector('#forecastMethod'),
      methodOpen: document.querySelector('#forecastMethod')?.open || false,
      methodText: document.querySelector('#forecastMethod')?.textContent.replace(/\s+/g,' ').trim() || '',
      backgrounds,
      bodyOverflow: document.documentElement.scrollWidth-window.innerWidth,
      scenarioCount: document.querySelectorAll('.scenario').length
    };
  });
}

(async()=>{
  fs.mkdirSync('artifacts',{recursive:true});
  const browser=await chromium.launch({headless:true});

  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  await stub(context);
  const page=await context.newPage();
  await page.goto('http://127.0.0.1:4173/reports/forecast.html',{waitUntil:'domcontentloaded',timeout:10000});
  await page.waitForSelector('#scenarioGrid .scenario',{timeout:5000});

  // Open one accordion section so card/table surfaces exist.
  await page.click('.acc-head');
  await page.waitForTimeout(50);

  let state=await inspectPage(page);
  if(state.scenarioCount!==3) fail('Forecast directions did not render');
  if(state.helpCount!==27) fail('Expected 27 visible field hints, got '+state.helpCount);
  if(!state.methodExists) fail('Forecast method block is missing');
  if(state.bodyOverflow>2) fail('Desktop page has horizontal body overflow');

  for(const [sel,bg] of Object.entries(state.backgrounds)){
    if(bg && !opaque(bg)) fail('Transparent surface remains: '+sel+' = '+bg);
  }

  await page.click('#forecastMethod > summary');
  await page.waitForTimeout(30);
  state=await inspectPage(page);
  if(!state.methodOpen) fail('Forecast method block did not open');
  for(const phrase of [
    'Доп. визиты = новые первичные + новые повторные',
    'Загрузка = (текущие визиты + доп. визиты) / макс. приёмов',
    'Доп. выручка = доп. визиты × средняя выручка / визит',
    'Доп. прибыль = доп. выручка − ФОТ − мед. затраты − доп. маркетинг',
    'не обрезает расчётную выручку автоматически',
    'Новая опер. прибыль'
  ]){
    if(!state.methodText.includes(phrase)) fail('Method help missing: '+phrase);
  }

  await page.screenshot({path:'artifacts/forecast-help-opaque-desktop.png',fullPage:false});
  console.log('FORECAST OPAQUE SURFACES: PASS');
  console.log('FORECAST HELP TEXT: PASS');
  console.log('FORECAST DESKTOP: PASS');
  await context.close();

  const mobileContext=await browser.newContext({viewport:{width:390,height:844}});
  await stub(mobileContext);
  const mobile=await mobileContext.newPage();
  await mobile.goto('http://127.0.0.1:4173/reports/forecast.html',{waitUntil:'domcontentloaded',timeout:10000});
  await mobile.waitForSelector('#scenarioGrid .scenario',{timeout:5000});
  const mobileState=await mobile.evaluate(()=>({
    bodyOverflow:document.documentElement.scrollWidth-window.innerWidth,
    helps:document.querySelectorAll('.field small').length,
    method:!!document.querySelector('#forecastMethod'),
    methodWidth:document.querySelector('#forecastMethod')?.getBoundingClientRect().width||0,
    viewport:window.innerWidth
  }));
  if(mobileState.bodyOverflow>2) fail('Mobile forecast has horizontal body overflow');
  if(mobileState.helps!==27) fail('Mobile forecast field hints missing');
  if(!mobileState.method) fail('Mobile method block missing');
  if(mobileState.methodWidth>mobileState.viewport+2) fail('Mobile method block exceeds viewport');
  await mobile.screenshot({path:'artifacts/forecast-help-opaque-mobile.png',fullPage:false});
  console.log('FORECAST MOBILE: PASS');

  await mobileContext.close();
  await browser.close();
})().catch(error=>{console.error(error.stack||error);process.exit(1);});
