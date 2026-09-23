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

  await page.screenshot({
    path: 'artifacts/report-monthly-compare.png',
    fullPage: false,
  });

  // Header must release after the table ends.
  const afterTableY = await page.evaluate(() => {
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

  await browser.close();
  fs.rmSync('reports/__browser-test.html', { force: true });
})().catch(async err => {
  fs.rmSync('reports/__browser-test.html', { force: true });
  console.error(err.stack || err);
  process.exit(1);
});
