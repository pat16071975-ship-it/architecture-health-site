(() => {
  'use strict';

  const frame = document.getElementById('serviceFrame');
  if (!frame) return;

  const STYLE_ID = 'az-mobile-report-fix';
  const DOC_FLAG = '__azMobileReportFixInstalled';
  const TABLE_SELECTOR = '#content table.table,#content table.matrix,.table-wrap table,.matrix-wrap table';
  const NUMERIC_WIDTH = 92;
  const signatures = new WeakMap();

  function mobile() {
    return window.matchMedia('(max-width:700px)').matches;
  }

  function logicalColumns(table) {
    const rows = Array.from(table.tHead?.rows || []);
    if (!rows.length) return 0;
    return Math.max(...rows.map(row =>
      Array.from(row.cells).reduce((sum, cell) => sum + (cell.colSpan || 1), 0)
    ));
  }

  function ensureStyle(d) {
    if (d.getElementById(STYLE_ID)) return;
    const style = d.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      @media (max-width:700px) {
        html,body{
          height:auto!important;
          min-height:100%!important;
          overflow-x:hidden!important;
          overflow-y:auto!important;
          overscroll-behavior-y:auto!important;
          -webkit-overflow-scrolling:touch;
        }
        .az-sticky-report-scroll,.table-wrap,.matrix-wrap{
          max-height:none!important;
          overflow-x:auto!important;
          overflow-y:visible!important;
          overscroll-behavior-x:contain!important;
          overscroll-behavior-y:auto!important;
          touch-action:auto!important;
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

  function ensureColgroup(table, count) {
    let group = table.querySelector(':scope > colgroup[data-az-mobile-cols]');
    if (!group) {
      group = table.ownerDocument.createElement('colgroup');
      group.dataset.azMobileCols = '1';
      table.insertBefore(group, table.firstChild);
    }
    if (group.children.length !== count) {
      group.replaceChildren();
      for (let i = 0; i < count; i += 1) group.appendChild(table.ownerDocument.createElement('col'));
    }
    Array.from(group.children).forEach((col, index) => {
      col.style.width = index === 0 ? '30vw' : `${NUMERIC_WIDTH}px`;
    });
  }

  function applyColumns(table) {
    const count = logicalColumns(table);
    if (count < 2) return;

    const signature = `${count}:${window.innerWidth}`;
    table.classList.add('az-mobile-fixed-table');

    const firstHead = table.tHead?.rows?.[0]?.cells?.[0];
    if (firstHead) firstHead.classList.add('az-mobile-first-head');

    Array.from(table.tBodies || []).forEach(body => {
      Array.from(body.rows || []).forEach(row => {
        const cell = row.cells?.[0];
        if (cell && (cell.colSpan || 1) === 1) cell.classList.add('az-mobile-first-col');
      });
    });

    if (signatures.get(table) === signature && table.querySelector(':scope > colgroup[data-az-mobile-cols]')) return;

    ensureColgroup(table, count);
    const numericTotal = (count - 1) * NUMERIC_WIDTH;
    table.style.setProperty('width', `calc(30vw + ${numericTotal}px)`, 'important');
    table.style.setProperty('min-width', `calc(30vw + ${numericTotal}px)`, 'important');
    table.style.setProperty('max-width', 'none', 'important');
    signatures.set(table, signature);
  }

  function layout(d) {
    if (!d || !mobile()) return;
    d.querySelectorAll(TABLE_SELECTOR).forEach(applyColumns);
  }

  function install() {
    const d = frame.contentDocument;
    if (!d || !mobile()) return;

    ensureStyle(d);
    layout(d);

    if (d[DOC_FLAG]) return;
    d[DOC_FLAG] = true;

    let pending = false;
    const schedule = () => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        layout(d);
      });
    };

    const target = d.getElementById('content') || d.body || d.documentElement;
    const Observer = frame.contentWindow?.MutationObserver || MutationObserver;
    const observer = new Observer(records => {
      const relevant = records.some(record =>
        Array.from(record.addedNodes || []).some(node =>
          node.nodeType === 1 && (
            node.matches?.(TABLE_SELECTOR) ||
            node.querySelector?.(TABLE_SELECTOR)
          )
        )
      );
      if (relevant) schedule();
    });
    observer.observe(target, {childList:true, subtree:true});

    frame.contentWindow?.addEventListener('resize', schedule, {passive:true});
    d.addEventListener('change', () => setTimeout(schedule, 0), true);
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
