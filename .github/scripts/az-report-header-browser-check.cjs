const { chromium } = require('playwright');
const fs = require('fs');

function fail(message) {
  throw new Error(message);
}

(async () => {
  fs.mkdirSync('artifacts', { recursive: true });

  const sourceHtml = fs.readFileSync('reports/index.html', 'utf8');
  const browserFixture = sourceHtml
    .replace(/<link\s+rel="preconnect"\s+href="https:\/\/fonts\.googleapis\.com">\s*/g, '')
    .replace(/<link\s+rel="preconnect"\s+href="https:\/\/fonts\.gstatic\.com"\s+crossorigin>\s*/g, '')
    .replace(/<link\s+href="https:\/\/fonts\.googleapis\.com\/[^"]+"\s+rel="stylesheet">\s*/g, '');
  fs.writeFileSync('reports/__browser-test.html', browserFixture);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1600, height: 900 },
    deviceScaleFactor: 1,
  });

  await context.addInitScript(() => {
    sessionStorage.setItem('az-management-auth-v1', '1');
    localStorage.setItem('az-management-seed-2026-07', '2026-07');
    localStorage.setItem('az-management-report-v1', JSON.stringify({
      '2026-09-21': {
        date:'2026-09-21', plan:5000, cashTotal:1000, cashOOO:600, cashIP:400,
        billedTotal:1200, factMedicine:600, factLab:100, primary:2, repeat:3,
        dentPrimary:1, dentRepeat:1,
        dentists:{'Чирков Максим Сергеевич':500},
        dentistsLegal:{'Чирков Максим Сергеевич':{ooo:300,ip:200}},
        dentCashOOO:300,dentCashIP:200,
        clinicPrimary:1, clinicRepeat:2,
        clinicDocs:{'Старостенко Вадим Анатольевич':100},
        clinicDocsLegal:{'Старостенко Вадим Анатольевич':{ooo:70,ip:30}},
        clinicCashOOO:70,clinicCashIP:30,
        labOrders:1, labRevenue:100, labLegal:{ooo:50,ip:50}, labCashOOO:50,labCashIP:50,
        leadsDent:2, leadsClinic:2, leadsReserve:0
      }
    }));
  });

  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.hostname === '127.0.0.1') {
      return route.continue();
    }
    return route.abort();
  });

  const page = await context.newPage();
  page.on('pageerror', error => console.error('PAGE_ERROR:', error.stack || error.message));
  page.on('console', msg => {
    if (msg.type() === 'error') console.error('PAGE_CONSOLE_ERROR:', msg.text());
  });

  await page.goto('http://127.0.0.1:4173/reports/__browser-test.html', {
    waitUntil: 'domcontentloaded',
    timeout: 10000,
  });

  await page.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(error => {
    console.error('DOMCONTENTLOADED_WAIT:', error.message);
  });
  await page.waitForTimeout(500);

  const bootstrap = await page.evaluate(() => ({
    mode: !!document.getElementById('mode'),
    appView: !!document.getElementById('appView'),
    dateViewMode: !!document.getElementById('dateViewMode'),
    scripts: [...document.scripts].map(script => script.getAttribute('src')).filter(Boolean),
    readyState: document.readyState,
  }));
  console.log('BROWSER_BOOTSTRAP=' + JSON.stringify(bootstrap));
  if (!bootstrap.mode || !bootstrap.appView) fail('Base report page did not initialize');
  if (!bootstrap.dateViewMode) fail('period-view.js did not initialize dateViewMode');

  await page.evaluate(() => {
    const style = document.createElement('style');
    style.id = 'az-section-nav-style';
    style.textContent = `
      .az-section-nav{
        position:sticky;top:0;z-index:10000;
        width:min(1180px,calc(100% - 24px));margin:0 auto;
        padding:8px 0 6px;display:flex;justify-content:flex-end;gap:8px;
        pointer-events:none;
      }
      .az-section-nav__btn{
        min-height:38px;padding:8px 13px;display:inline-flex;
        align-items:center;justify-content:center;
        border:1px solid rgba(181,150,98,.46);border-radius:9px;
        background:rgba(250,247,241,.96);color:#354039;text-decoration:none;
        font:600 11px/1.15 Montserrat,Arial,sans-serif;
        white-space:nowrap;pointer-events:auto;
      }
      @media(min-width:761px){body .toolbar{top:52px!important}}
    `;
    document.head.appendChild(style);

    const nav = document.createElement('nav');
    nav.className = 'az-section-nav';
    nav.innerHTML =
      '<span class="az-section-nav__btn">Отчёты</span>' +
      '<span class="az-section-nav__btn">На главную</span>';
    document.body.insertAdjacentElement('afterbegin', nav);
  });

  await page.evaluate(() => {
    const input = document.getElementById('reportDate');
    input.value = '2026-09-21';
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.waitForTimeout(100);

  // Editing a manual field must not wipe imported readonly cash values in the browser.
  const manualEditCash = await page.evaluate(() => {
    const plan = document.querySelector('[data-key="plan"]');
    if (!plan) return { ok:false, reason:'plan input missing' };
    plan.readOnly = false;
    plan.removeAttribute('readonly');
    plan.value = '6000';
    plan.dispatchEvent(new Event('input', { bubbles:true }));
    const record = collectRecord();
    const derived = derive(record);
    return {
      ok:true,
      cashTotal:derived.cashTotal,
      cashOOO:derived.cashOOO,
      cashIP:derived.cashIP,
      billedTotal:derived.billedTotal,
      plan:record.plan,
    };
  });
  if (!manualEditCash.ok) fail('Manual edit guard: '+manualEditCash.reason);
  if (manualEditCash.cashTotal !== 1000 || manualEditCash.cashOOO !== 600 || manualEditCash.cashIP !== 400) {
    fail('Manual edit guard: cash values disappeared while editing a manual field');
  }
  if (manualEditCash.billedTotal !== 1200 || String(manualEditCash.plan) !== '6000') {
    fail('Manual edit guard: billed/manual values are inconsistent');
  }

  // Mode 1: one date. Toolbar must scroll away; only section navigation remains.
  await page.selectOption('#dateViewMode', 'single');
  await page.evaluate(() => window.scrollTo(0, 1400));
  await page.waitForTimeout(100);

  const single = await page.evaluate(() => {
    const nav = document.querySelector('.az-section-nav').getBoundingClientRect();
    const toolbar = document.querySelector('.toolbar').getBoundingClientRect();
    const toolbarStyle = getComputedStyle(document.querySelector('.toolbar'));
    return {
      navTop: nav.top,
      navBottom: nav.bottom,
      toolbarTop: toolbar.top,
      toolbarBottom: toolbar.bottom,
      toolbarPosition: toolbarStyle.position,
    };
  });

  if (Math.abs(single.navTop) > 1) fail('Single-date: section navigation is not pinned at top');
  if (single.toolbarPosition !== 'static') fail('Single-date: toolbar is still sticky/fixed');
  if (single.toolbarBottom >= 0) fail('Single-date: toolbar did not scroll offscreen');

  await page.screenshot({
    path: 'artifacts/report-one-date.png',
    fullPage: false,
  });

  // Mode 2: monthly comparison. Use plain fixed HTML clone, never transformed text.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.selectOption('#dateViewMode', 'compare');
  await page.selectOption('#compareRange', 'ytd');
  await page.waitForTimeout(120);

  const targetY = await page.evaluate(() => {
    const shell = document.querySelector('.date-compare-table');
    return shell.getBoundingClientRect().top + window.scrollY + 550;
  });
  await page.evaluate(y => window.scrollTo(0, y), targetY);
  await page.waitForTimeout(120);

  const compare = await page.evaluate(() => {
    const nav = document.querySelector('.az-section-nav').getBoundingClientRect();
    const sticky = document.getElementById('dateCompareStickyHead');
    const stickyRect = sticky.getBoundingClientRect();
    const sourceHead = document.getElementById('dateCompareHead');
    const sourceRect = sourceHead.getBoundingClientRect();
    const toolbar = document.querySelector('.toolbar').getBoundingClientRect();
    const stickyTh = sticky.querySelector('th');
    const sourceTh = sourceHead.querySelector('th');
    return {
      navBottom: nav.bottom,
      stickyHidden: sticky.classList.contains('hidden'),
      stickyTop: stickyRect.top,
      stickyPosition: getComputedStyle(sticky).position,
      stickyTransform: getComputedStyle(sticky).transform,
      stickyThTransform: stickyTh ? getComputedStyle(stickyTh).transform : null,
      sourceThTransform: sourceTh ? getComputedStyle(sourceTh).transform : null,
      sourceHeadTop: sourceRect.top,
      sourceText: sourceHead.innerText.replace(/\s+/g, ' ').trim(),
      stickyText: sticky.innerText.replace(/\s+/g, ' ').trim(),
      toolbarBottom: toolbar.bottom,
    };
  });

  if (compare.stickyHidden) fail('Compare: cloned month header is hidden after scroll');
  if (compare.stickyPosition !== 'fixed') fail('Compare: cloned month header is not fixed');
  if (Math.abs(compare.stickyTop - compare.navBottom) > 1) {
    fail(`Compare: cloned header top ${compare.stickyTop} != nav bottom ${compare.navBottom}`);
  }
  if (compare.sourceHeadTop >= compare.navBottom) fail('Compare: original header did not scroll away');
  if (compare.sourceText !== compare.stickyText) fail('Compare: cloned header text differs from source');
  if (compare.stickyTransform !== 'none') fail('Compare: clone container uses transform');
  if (compare.stickyThTransform !== 'none') fail('Compare: cloned text cell uses transform');
  if (compare.sourceThTransform !== 'none') fail('Compare: original text cell uses transform');
  if (compare.toolbarBottom >= 0) fail('Compare: toolbar did not scroll offscreen');

  const horizontal = await page.evaluate(() => {
    const shell = document.querySelector('.date-compare-table');
    if (shell.scrollWidth <= shell.clientWidth + 2) {
      return { tested: false };
    }
    shell.scrollLeft = Math.min(120, shell.scrollWidth - shell.clientWidth);
    shell.dispatchEvent(new Event('scroll'));
    return { tested: true, scrollLeft: shell.scrollLeft };
  });
  if (horizontal.tested) {
    await page.waitForTimeout(80);
    const left = await page.evaluate(() =>
      parseFloat(document.querySelector('.date-compare-sticky-head-inner').style.left || '0')
    );
    if (Math.abs(left + horizontal.scrollLeft) > 1) {
      fail('Compare: cloned header does not follow horizontal table scroll');
    }
  }

  const cashSplit = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('#dateCompareBody tr')];
    const byLabel = label => rows.find(row => row.cells?.[0]?.textContent.trim() === label);
    const labels = [
      'Выручка стоматологии',
      'Чирков Максим Сергеевич',
      'Выручка клиники',
      'Старостенко Вадим Анатольевич',
      'Выручка лаборатории',
    ];
    const details = {};
    for (const label of labels) {
      const row = byLabel(label);
      const split = row?.querySelector('.cash-split');
      details[label] = split ? split.textContent.replace(/\s+/g,' ').trim() : null;
    }
    return {
      splitCount: document.querySelectorAll('#dateCompareBody .cash-split').length,
      splitRows: document.querySelectorAll('#dateCompareBody tr.cash-split').length,
      details,
    };
  });
  if (cashSplit.splitRows !== 0) fail('Cash split created extra table rows');
  if (cashSplit.splitCount < 5) fail('Cash split is missing from comparison cells');
  for (const [label, detail] of Object.entries(cashSplit.details)) {
    if (!detail || !detail.includes('ООО') || !detail.includes('ИП')) {
      fail('Cash split missing OOO/IP for ' + label);
    }
  }
  console.log('BROWSER CHECK CASH LEGAL SPLIT: PASS');

  const emphasis = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('#dateCompareBody tr')];
    const byLabel = label => rows.find(row => row.cells?.[0]?.textContent.trim() === label);
    const fact = byLabel('Факт');
    const due = byLabel('Должно быть');
    const billed = byLabel('Выставлено счетов');
    const ooo = byLabel('ДС ООО');
    const ip = byLabel('ДС ИП');
    const styleOf = row => {
      const cell = row?.cells?.[1];
      if (!cell) return null;
      const cs = getComputedStyle(cell);
      return { fontSize: cs.fontSize, backgroundColor: cs.backgroundColor };
    };
    return {
      fact: styleOf(fact),
      due: styleOf(due),
      billed: styleOf(billed),
      ooo: styleOf(ooo),
      ip: styleOf(ip),
      billedClass: billed?.className || '',
      dueClass: due?.className || '',
      oooClass: ooo?.className || '',
      ipClass: ip?.className || '',
    };
  });
  if (!emphasis.billedClass.includes('row-billed')) fail('Billed row is missing row-billed class');
  if (emphasis.billed?.backgroundColor !== 'rgb(255, 255, 255)') {
    fail('Billed row is not white: ' + emphasis.billed?.backgroundColor);
  }
  for (const [name, row] of [['due', emphasis.due], ['ooo', emphasis.ooo], ['ip', emphasis.ip]]) {
    if (!row) fail('Missing secondary row style: ' + name);
    if (parseFloat(row.fontSize) >= parseFloat(emphasis.fact?.fontSize || '0')) {
      fail('Secondary row is not smaller than Fact: ' + name);
    }
  }
  for (const cls of [emphasis.dueClass, emphasis.oooClass, emphasis.ipClass]) {
    if (!cls.includes('row-secondary')) fail('Secondary row is missing row-secondary class');
  }
  console.log('BROWSER CHECK FINANCIAL EMPHASIS: PASS');

  await page.screenshot({
    path: 'artifacts/report-monthly-compare.png',
    fullPage: false,
  });

  // Header must release after the table ends. Add test-only scroll room
  // so Chromium can physically move the table bottom above the navigation.
  const afterTableY = await page.evaluate(() => {
    const spacer = document.createElement('div');
    spacer.id = 'az-browser-test-release-spacer';
    spacer.style.height = '1200px';
    document.body.appendChild(spacer);
    const table = document.querySelector('.date-compare-table table');
    return table.getBoundingClientRect().bottom + window.scrollY + 120;
  });
  await page.evaluate(y => window.scrollTo(0, y), afterTableY);
  await page.waitForTimeout(100);

  const released = await page.evaluate(() =>
    document.getElementById('dateCompareStickyHead').classList.contains('hidden')
  );
  if (!released) fail('Compare: cloned month header did not release after table end');

  console.log('BROWSER CHECK ONE DATE: PASS');
  console.log('BROWSER CHECK MONTHLY COMPARE: PASS');
  console.log('BROWSER CHECK NO TEXT TRANSFORMS: PASS');
  console.log('BROWSER SCREENSHOTS: artifacts/report-one-date.png, artifacts/report-monthly-compare.png');

  // Mobile regression: cash fields must stay readable and tables must scroll
  // inside their own containers without widening the page.
  const mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
  });
  await mobileContext.addInitScript(() => {
    sessionStorage.setItem('az-management-auth-v1', '1');
    localStorage.setItem('az-management-seed-2026-07', '2026-07');
    localStorage.setItem('az-management-report-v1', JSON.stringify({
      '2026-09-21': {
        date:'2026-09-21', plan:5000, cashTotal:1000, cashOOO:600, cashIP:400,
        billedTotal:1200, factMedicine:600, factLab:100, primary:2, repeat:3,
        dentPrimary:1, dentRepeat:1,
        dentists:{'Чирков Максим Сергеевич':500},
        dentistsLegal:{'Чирков Максим Сергеевич':{ooo:300,ip:200}},
        dentCashOOO:300,dentCashIP:200,
        clinicPrimary:1, clinicRepeat:2,
        clinicDocs:{'Старостенко Вадим Анатольевич':100},
        clinicDocsLegal:{'Старостенко Вадим Анатольевич':{ooo:70,ip:30}},
        clinicCashOOO:70,clinicCashIP:30,
        labOrders:1, labRevenue:100, labLegal:{ooo:50,ip:50}, labCashOOO:50,labCashIP:50
      }
    }));
  });
  await mobileContext.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.hostname === '127.0.0.1') return route.continue();
    return route.abort();
  });
  const mobile = await mobileContext.newPage();
  await mobile.goto('http://127.0.0.1:4173/reports/__browser-test.html', {
    waitUntil: 'domcontentloaded',
    timeout: 10000,
  });
  await mobile.waitForSelector('#dateViewMode', { timeout: 5000 });
  await mobile.evaluate(() => {
    const input = document.getElementById('reportDate');
    input.value = '2026-09-21';
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await mobile.waitForTimeout(250);

  const mobileSingle = await mobile.evaluate(() => {
    const grid = document.getElementById('generalGrid');
    const firstMetric = grid?.querySelector('.metric');
    const labels = [...(grid?.querySelectorAll('label') || [])].map(x => x.textContent.trim());
    return {
      bodyOverflow: document.documentElement.scrollWidth - window.innerWidth,
      gridWidth: grid?.getBoundingClientRect().width || 0,
      metricWidth: firstMetric?.getBoundingClientRect().width || 0,
      labels,
      toolbarPosition: getComputedStyle(document.querySelector('.toolbar')).position,
    };
  });
  if (mobileSingle.bodyOverflow > 2) fail('Mobile single-date: page has horizontal body overflow');
  if (mobileSingle.gridWidth && mobileSingle.metricWidth < mobileSingle.gridWidth * 0.75) {
    fail('Mobile single-date: main metrics are not stacked/readable');
  }
  for (const label of ['Факт', 'Выставлено счетов', 'ДС ООО', 'ДС ИП']) {
    if (!mobileSingle.labels.includes(label)) fail('Mobile single-date: missing cash label ' + label);
  }
  if (mobileSingle.toolbarPosition !== 'static') fail('Mobile single-date: toolbar is unexpectedly sticky');
  await mobile.screenshot({ path: 'artifacts/report-mobile-one-date.png', fullPage: false });

  await mobile.selectOption('#dateViewMode', 'compare');
  await mobile.selectOption('#compareRange', 'ytd');
  await mobile.waitForTimeout(200);
  const mobileCompare = await mobile.evaluate(() => {
    const shell = document.querySelector('.date-compare-table');
    return {
      bodyOverflow: document.documentElement.scrollWidth - window.innerWidth,
      shellWidth: shell?.getBoundingClientRect().width || 0,
      viewport: window.innerWidth,
      shellScrollable: !!shell && shell.scrollWidth > shell.clientWidth,
      stickyCloneHidden: document.getElementById('dateCompareStickyHead')?.classList.contains('hidden'),
      cashSplitCount: document.querySelectorAll('#dateCompareBody .cash-split').length,
    };
  });
  if (mobileCompare.bodyOverflow > 2) fail('Mobile compare: page has horizontal body overflow');
  if (mobileCompare.shellWidth > mobileCompare.viewport + 2) fail('Mobile compare: table shell exceeds viewport');
  if (!mobileCompare.shellScrollable) fail('Mobile compare: wide comparison table is not horizontally scrollable');
  if (mobileCompare.stickyCloneHidden !== true) fail('Mobile compare: desktop sticky clone must remain disabled');
  if (mobileCompare.cashSplitCount < 5) fail('Mobile compare: OOO/IP cash split is missing');
  await mobile.screenshot({ path: 'artifacts/report-mobile-compare.png', fullPage: false });
  console.log('BROWSER CHECK MOBILE ONE DATE: PASS');
  console.log('BROWSER CHECK MOBILE COMPARE: PASS');
  await mobileContext.close();

  await browser.close();
  fs.rmSync('reports/__browser-test.html', { force: true });
})().catch(async err => {
  fs.rmSync('reports/__browser-test.html', { force: true });
  console.error(err.stack || err);
  process.exit(1);
});
