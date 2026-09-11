(() => {
  'use strict';

  const MQ = '(max-width:720px)';
  const STYLE_ID = 'az-management-mobile-layout-style';
  const TABLE_SELECTOR = '.period-compare table,.date-compare-table table';
  const VALUE_WIDTH = 124;
  const signatures = new WeakMap();

  function mobile() {
    return window.matchMedia(MQ).matches;
  }

  function logicalColumns(table) {
    const rows = Array.from(table.tHead?.rows || []);
    if (!rows.length) return 0;
    return Math.max(...rows.map(row =>
      Array.from(row.cells).reduce((sum, cell) => sum + (cell.colSpan || 1), 0)
    ));
  }

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      @media(max-width:720px){
        .period-compare,.date-compare-table{
          overflow-x:auto!important;
          overflow-y:visible!important;
          -webkit-overflow-scrolling:touch;
          overscroll-behavior-x:contain;
        }
        .az-management-mobile-table{
          table-layout:fixed!important;
          border-collapse:separate!important;
          border-spacing:0!important;
        }
        .az-management-mobile-table th,
        .az-management-mobile-table td{
          box-sizing:border-box!important;
        }
        .az-management-mobile-table .az-mgmt-label-head,
        .az-management-mobile-table .az-mgmt-label-cell{
          width:30vw!important;
          min-width:30vw!important;
          max-width:30vw!important;
          white-space:normal!important;
          overflow-wrap:anywhere!important;
          word-break:normal!important;
        }
        .az-management-mobile-table .az-mgmt-label-head{
          position:sticky!important;
          left:0!important;
          z-index:30!important;
          background:#f8f4ec!important;
          box-shadow:2px 0 0 rgba(216,205,187,.75)!important;
        }
        .az-management-mobile-table .az-mgmt-label-cell{
          position:sticky!important;
          left:0!important;
          z-index:20!important;
          box-shadow:2px 0 0 rgba(216,205,187,.62)!important;
        }
        .az-management-mobile-table thead th:not(.az-mgmt-label-head),
        .az-management-mobile-table tbody td:not(.az-mgmt-label-cell):not([colspan]){
          width:${VALUE_WIDTH}px!important;
          min-width:${VALUE_WIDTH}px!important;
          max-width:${VALUE_WIDTH}px!important;
          white-space:nowrap!important;
        }
      }
    `;
    document.head.appendChild(style);
  }

  function ensureColgroup(table, count) {
    let group = table.querySelector(':scope > colgroup[data-az-management-mobile-cols]');
    if (!group) {
      group = document.createElement('colgroup');
      group.dataset.azManagementMobileCols = '1';
      table.insertBefore(group, table.firstChild);
    }
    if (group.children.length !== count) {
      group.replaceChildren();
      for (let i = 0; i < count; i += 1) group.appendChild(document.createElement('col'));
    }
    Array.from(group.children).forEach((col, index) => {
      col.style.width = index === 0 ? '30vw' : `${VALUE_WIDTH}px`;
    });
  }

  function cellBackground(cell) {
    const value = getComputedStyle(cell).backgroundColor;
    return value && value !== 'transparent' && value !== 'rgba(0, 0, 0, 0)'
      ? value
      : '#fffdf8';
  }

  function applyTable(table) {
    if (!mobile()) return;
    const count = logicalColumns(table);
    if (count < 2) return;

    const signature = `${count}:${window.innerWidth}`;
    const firstHead = table.tHead?.rows?.[0]?.cells?.[0];
    if (!firstHead) return;

    table.classList.add('az-management-mobile-table');
    firstHead.classList.add('az-mgmt-label-head');

    Array.from(table.tBodies || []).forEach(body => {
      Array.from(body.rows || []).forEach(row => {
        const cell = row.cells?.[0];
        if (!cell || (cell.colSpan || 1) !== 1) return;
        cell.classList.add('az-mgmt-label-cell');
        cell.style.setProperty('background-color', cellBackground(cell), 'important');
      });
    });

    if (signatures.get(table) === signature && table.querySelector(':scope > colgroup[data-az-management-mobile-cols]')) return;

    ensureColgroup(table, count);
    const totalWidth = (count - 1) * VALUE_WIDTH;
    table.style.setProperty('width', `calc(30vw + ${totalWidth}px)`, 'important');
    table.style.setProperty('min-width', `calc(30vw + ${totalWidth}px)`, 'important');
    table.style.setProperty('max-width', 'none', 'important');
    signatures.set(table, signature);
  }

  function applyAll() {
    if (!mobile()) return;
    ensureStyle();
    document.querySelectorAll(TABLE_SELECTOR).forEach(applyTable);
  }

  let scheduled = false;
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      applyAll();
    });
  }

  const observer = new MutationObserver(records => {
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

  observer.observe(document.body || document.documentElement, {childList:true, subtree:true});
  window.addEventListener('resize', schedule, {passive:true});
  document.addEventListener('change', () => setTimeout(schedule, 0), true);

  schedule();
  setTimeout(schedule, 150);
  setTimeout(schedule, 700);
})();

(() => {
  'use strict';

  const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  const EXTRA_DENTISTS = ['Филатова А. Д.'];
  const EXTRA_STRUCTURE = ['Diers И.', 'Алатарцева П. В.', 'Борисовская А. И.'];

  let structureChanged = false;
  EXTRA_DENTISTS.forEach(name => {
    if (!dentists.includes(name)) {
      dentists.push(name);
      structureChanged = true;
    }
  });
  EXTRA_STRUCTURE.forEach(name => {
    if (!clinicDocs.includes(name)) {
      clinicDocs.push(name);
      structureChanged = true;
    }
  });

  if (structureChanged && typeof renderStructure === 'function') {
    renderStructure();
    if (typeof loadDate === 'function') loadDate();
  }

  // УТВЕРЖДЁННЫЕ ФОРМУЛЫ НЕ ПЕРЕОПРЕДЕЛЯЕМ.
  // Основной derive() остаётся источником правил:
  // средний чек медицины = factMedicine / primary;
  // стоматология = dentRev / dentPrimary;
  // клиника = clinicRev / clinicPrimary.

  const nativeLoadStore = window.loadStore;
  if (typeof nativeLoadStore === 'function' && !window.__azManagementCompareStoreFix) {
    window.__azManagementCompareStoreFix = true;
    window.loadStore = function fixedManagementLoadStore() {
      const store = nativeLoadStore();
      const compare = document.getElementById('dateViewMode')?.value === 'compare';
      if (!compare || !store || typeof store !== 'object') return store;

      return new Proxy(store, {
        get(target, prop, receiver) {
          if (typeof prop !== 'string' || !DATE_RE.test(prop)) {
            return Reflect.get(target, prop, receiver);
          }
          if (Object.prototype.hasOwnProperty.call(target, prop)) return target[prop];
          const month = prop.slice(0, 7);
          const latest = Object.keys(target)
            .filter(key => DATE_RE.test(key) && key.slice(0, 7) === month && key <= prop)
            .sort()
            .pop();
          if (!latest) return undefined;
          return {...target[latest], date: prop};
        }
      });
    };
  }

  function markCumulativeMode() {
    if (document.getElementById('dateViewMode')?.value !== 'compare') return;
    const hint = document.getElementById('dateCompareHint');
    const note = document.getElementById('dateSummaryNote');
    if (hint) hint.textContent = 'Каждый месяц: накопительный итог с 1-го числа по выбранный день включительно.';
    if (note) note.textContent = 'Абсолютные показатели берутся накопительно с начала месяца. Средний чек: выручка / первичные приёмы соответствующего направления. ПП в день, выполнение и конверсии пересчитываются из накопительных итогов.';
  }

  function refreshComparison() {
    const range = document.getElementById('compareRange');
    if (document.getElementById('dateViewMode')?.value === 'compare' && range) {
      range.dispatchEvent(new Event('change', {bubbles: true}));
    }
    setTimeout(markCumulativeMode, 60);
  }

  document.addEventListener('change', () => setTimeout(markCumulativeMode, 80), true);
  setTimeout(refreshComparison, 0);
  setTimeout(markCumulativeMode, 200);
})();
