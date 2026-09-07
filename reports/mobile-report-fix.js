(() => {
  'use strict';

  const frame = document.getElementById('serviceFrame');
  if (!frame) return;

  const STYLE_ID = 'az-mobile-report-fix';
  const DOC_FLAG = '__azMobileReportFixInstalled';
  const TABLE_SELECTOR = '#content table.table,#content table.matrix,.table-wrap table,.matrix-wrap table';
  const NUMERIC_WIDTH = 92;

  function logicalColumns(table) {
    const rows = Array.from(table.tHead?.rows || []);
    if (!rows.length) return 0;
    return Math.max(...rows.map(row => Array.from(row.cells).reduce((sum, cell) => sum + (cell.colSpan || 1), 0)));
  }

  function ensureStyle(d) {
    if (d.getElementById(STYLE_ID)) return;
    const style = d.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      @media (max-width:700px) {
        html,body{
          overflow-x:hidden!important;
          overflow-y:auto!important;
          overscroll-behavior-y:auto!important;
          -webkit-overflow-scrolling:touch;
        }
        .az-sticky-report-scroll,.table-wrap,.matrix-wrap{
          max-height:none!important;
          overflow-x:auto!important;
          overflow-y:hidden!important;
          overscroll-behavior-x:contain!important;
          overscroll-behavior-y:auto!important;
          touch-action:pan-x pan-y!important;
          -webkit-overflow-scrolling:touch;
        }
        .az-mobile-fixed-table{
          table-layout:fixed!important;
          border-collapse:separate!important;
          border-spacing:0!important;
        }
        .az-mobile-fixed-table .az-mobile-first-head,
        .az-mobile-fixed-table .az-mobile-first-col{
          width:30vw!important;
          min-width:30vw!important;
          max-width:30vw!important;
          white-space:normal!important;
          overflow-wrap:anywhere!important;
          word-break:normal!important;
          box-sizing:border-box!important;
        }
        .az-mobile-fixed-table .az-mobile-first-head{
          position:sticky!important;
          left:0!important;
          z-index:70!important;
          background:#faf6ef!important;
          background-clip:padding-box!important;
          box-shadow:2px 0 0 rgba(216,205,187,.72);
        }
        .az-mobile-fixed-table .az-mobile-first-col{
          position:sticky!important;
          left:0!important;
          z-index:22!important;
          background:#fffdf8!important;
          background-clip:padding-box!important;
          box-shadow:2px 0 0 rgba(216,205,187,.55);
        }
        .az-mobile-fixed-table th,
        .az-mobile-fixed-table td{
          box-sizing:border-box!important;
        }
        .az-mobile-fixed-table thead th:not(.az-mobile-first-head),
        .az-mobile-fixed-table tbody td:not(.az-mobile-first-col):not([colspan]){
          min-width:0!important;
          max-width:none!important;
          white-space:nowrap!important;
        }
        .az-mobile-fixed-table tbody td:not(.az-mobile-first-col):not([colspan]){
          font-size:10.5px!important;
          padding-left:5px!important;
          padding-right:5px!important;
        }
      }
    `;
    (d.head || d.documentElement).appendChild(style);
  }

  function applyColumns(table) {
    const count = logicalColumns(table);
    if (count < 2) return;

    table.classList.add('az-mobile-fixed-table');
    table.querySelectorAll('.az-mobile-first-head').forEach(c => c.classList.remove('az-mobile-first-head'));
    table.querySelectorAll('.az-mobile-first-col').forEach(c => c.classList.remove('az-mobile-first-col'));

    const firstHead = table.tHead?.rows?.[0]?.cells?.[0];
    if (firstHead) firstHead.classList.add('az-mobile-first-head');

    Array.from(table.tBodies || []).forEach(body => {
      Array.from(body.rows || []).forEach(row => {
        const cell = row.cells?.[0];
        if (cell && (cell.colSpan || 1) === 1) cell.classList.add('az-mobile-first-col');
      });
    });

    table.querySelector('colgroup[data-az-mobile-cols]')?.remove();
    const group = table.ownerDocument.createElement('colgroup');
    group.dataset.azMobileCols = '1';
    for (let i = 0; i < count; i += 1) {
      const col = table.ownerDocument.createElement('col');
      col.style.width = i === 0 ? '30vw' : `${NUMERIC_WIDTH}px`;
      group.appendChild(col);
    }
    table.insertBefore(group, table.firstChild);

    const numericTotal = (count - 1) * NUMERIC_WIDTH;
    table.style.setProperty('width', `calc(30vw + ${numericTotal}px)`, 'important');
    table.style.setProperty('min-width', `calc(30vw + ${numericTotal}px)`, 'important');
    table.style.setProperty('max-width', 'none', 'important');
  }

  function layout(d) {
    if (!d || !matchMedia('(max-width:700px)').matches) return;
    d.querySelectorAll(TABLE_SELECTOR).forEach(applyColumns);
  }

  function install() {
    const d = frame.contentDocument;
    if (!d) return;
    ensureStyle(d);
    layout(d);

    if (d[DOC_FLAG]) return;
    d[DOC_FLAG] = true;

    let pending = false;
    const schedule = () => {
      if (pending) return;
      pending = true;
      setTimeout(() => {
        pending = false;
        layout(d);
      }, 0);
    };

    const Observer = frame.contentWindow?.MutationObserver || MutationObserver;
    const observer = new Observer(schedule);
    observer.observe(d.body || d.documentElement, {childList:true, subtree:true});
    frame.contentWindow?.addEventListener('resize', schedule, {passive:true});
  }

  frame.addEventListener('load', () => {
    install();
    setTimeout(install, 100);
    setTimeout(install, 500);
  });

  if (frame.contentDocument?.readyState === 'interactive' || frame.contentDocument?.readyState === 'complete') {
    install();
  }
})();
