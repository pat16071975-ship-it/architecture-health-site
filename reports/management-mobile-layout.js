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
      @media(min-width:901px){
        .period-compare,.date-compare-table{
          overflow:visible!important;
        }
        .period-compare thead th,.date-compare-table thead th{
          top:var(--az-management-sticky-top,0px)!important;
          z-index:40!important;
          background:#f8f4ec!important;
          box-shadow:0 1px 0 rgba(216,205,187,.95)!important;
        }
        .period-compare thead th:first-child,.date-compare-table thead th:first-child{
          z-index:41!important;
        }
      }
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

  function syncStickyTop() {
    const toolbar = document.querySelector('.toolbar');
    if (!toolbar || window.innerWidth <= 900) {
      document.documentElement.style.setProperty('--az-management-sticky-top', '0px');
      return;
    }
    const style = getComputedStyle(toolbar);
    const top = style.position === 'sticky' ? Math.ceil(toolbar.getBoundingClientRect().height) : 0;
    document.documentElement.style.setProperty('--az-management-sticky-top', `${Math.max(0, top)}px`);
  }

  function applyAll() {
    ensureStyle();
    syncStickyTop();
    if (!mobile()) return;
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

(() => {
  'use strict';

  const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  const SECTION_ID = 'azMarketingSourcesSection';
  const ROW_ATTR = 'data-az-marketing-sources';
  const SOURCE_TITLE = 'Источники первичных пациентов (внесенные администраторами)';
  const countFmt = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0});
  const shareFmt = new Intl.NumberFormat('ru-RU', {style: 'percent', maximumFractionDigits: 1});

  const TEXT_REPLACEMENTS = new Map([
    ['Клиника', 'Отделение структуры'],
    ['Выручка клиника', 'Выручка отделения структуры'],
    ['Выручка клиники', 'Выручка отделения структуры'],
    ['Клиника ср. чек', 'Средний чек отделения структуры'],
    ['Средний чек клиники', 'Средний чек отделения структуры'],
    ['Первичные клиника', 'Первичные отделение структуры'],
    ['Повторные клиника', 'Повторные отделение структуры'],
    ['Первичные — клиника', 'Первичные — отделение структуры'],
    ['Повторные — клиника', 'Повторные — отделение структуры'],
    ['Клиника — целевые лиды', 'Отделение структуры — целевые лиды'],
    ['Целевые лиды — клиника', 'Целевые лиды — отделение структуры'],
    ['Конверсия клиника', 'Конверсия отделение структуры'],
  ]);

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, ch => ({
      '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;'
    }[ch]));
  }

  function replaceLabels(root = document) {
    root.querySelectorAll('h2,h3,label,th,td').forEach(node => {
      if (node.children.length) return;
      const current = node.textContent.trim();
      const replacement = TEXT_REPLACEMENTS.get(current);
      if (replacement) node.textContent = replacement;
    });
  }

  function cleanSources(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    const result = {};
    Object.entries(value).forEach(([label, raw]) => {
      const n = Number(raw);
      if (!label || !Number.isFinite(n) || n <= 0) return;
      result[String(label)] = n;
    });
    return result;
  }

  function sortedSourceEntries(sources) {
    return Object.entries(cleanSources(sources))
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'ru'));
  }

  function sourceTotal(sources) {
    return Object.values(cleanSources(sources)).reduce((sum, value) => sum + value, 0);
  }

  function formatSourceValue(value, total) {
    if (!value || !total) return '—';
    return `${countFmt.format(value)} (${shareFmt.format(value / total)})`;
  }

  function ensureSingleDateSection() {
    const editView = document.getElementById('editView');
    if (!editView) return null;
    let section = document.getElementById(SECTION_ID);
    if (section) return section;
    const marketing = editView.querySelector('.section.marketing');
    if (!marketing) return null;

    section = document.createElement('div');
    section.id = SECTION_ID;
    section.className = 'section marketing az-marketing-sources';
    section.innerHTML = `
      <h2>${SOURCE_TITLE}</h2>
      <div class="table-wrap">
        <table class="data-table" style="min-width:560px">
          <thead><tr><th>Источник</th><th>Первичные пациенты</th><th>Доля</th></tr></thead>
          <tbody data-source-body></tbody>
        </table>
      </div>`;
    marketing.insertAdjacentElement('afterend', section);
    return section;
  }

  function currentRecord() {
    const date = document.getElementById('reportDate')?.value;
    if (!date || typeof window.loadStore !== 'function') return null;
    const store = window.loadStore();
    return store && typeof store === 'object' ? (store[date] || null) : null;
  }

  function renderSingleDateSources() {
    const section = ensureSingleDateSection();
    if (!section) return;
    const body = section.querySelector('[data-source-body]');
    if (!body) return;
    const sources = cleanSources(currentRecord()?.marketingSources);
    const entries = sortedSourceEntries(sources);
    const total = sourceTotal(sources);

    if (!entries.length) {
      body.innerHTML = '<tr><td colspan="3" class="muted">Данные об источниках первичных пациентов пока не загружены.</td></tr>';
      return;
    }

    body.innerHTML = entries.map(([label, value]) =>
      `<tr><td>${escapeHtml(label)}</td><td>${countFmt.format(value)}</td><td>${shareFmt.format(value / total)}</td></tr>`
    ).join('') + `<tr class="sum-row"><td>Всего первичных по источникам</td><td>${countFmt.format(total)}</td><td>100%</td></tr>`;
  }

  function addSources(target, sources) {
    Object.entries(cleanSources(sources)).forEach(([label, value]) => {
      target[label] = (target[label] || 0) + value;
    });
    return target;
  }

  function latestMonthRows(store, year, startMonth, endMonth) {
    const byMonth = {};
    Object.entries(store || {}).forEach(([dateValue, row]) => {
      if (!DATE_RE.test(dateValue) || !row || typeof row !== 'object') return;
      const yearValue = Number(dateValue.slice(0, 4));
      const month = Number(dateValue.slice(5, 7));
      if (yearValue !== year || month < startMonth || month > endMonth) return;
      if (!byMonth[month] || dateValue > byMonth[month].date) byMonth[month] = {date: dateValue, row};
    });
    return Object.values(byMonth).sort((a, b) => a.date.localeCompare(b.date)).map(item => item.row);
  }

  function periodSourceColumns() {
    const mode = document.getElementById('mode')?.value;
    const year = Number(document.getElementById('year')?.value);
    if (!year || !['quarter', 'half', 'year'].includes(mode) || typeof window.loadStore !== 'function') return [];
    const store = window.loadStore();
    let defs;
    if (mode === 'quarter') defs = [[1,3],[4,6],[7,9],[10,12]];
    else if (mode === 'half') defs = [[1,6],[7,12]];
    else defs = [[1,12]];
    return defs.map(([startMonth, endMonth]) => {
      const combined = {};
      latestMonthRows(store, year, startMonth, endMonth).forEach(row => addSources(combined, row.marketingSources));
      return combined;
    });
  }

  function clampDate(year, month, day) {
    const last = new Date(year, month, 0).getDate();
    return `${year}-${String(month).padStart(2, '0')}-${String(Math.min(day, last)).padStart(2, '0')}`;
  }

  function comparisonMonths(range, selectedMonth) {
    const defs = {
      q1:[1,3], q2:[4,6], q3:[7,9], q4:[10,12],
      h1:[1,6], h2:[7,12], year:[1,12], ytd:[1,selectedMonth]
    };
    const [start, end] = defs[range] || defs.ytd;
    return Array.from({length: end - start + 1}, (_, index) => start + index);
  }

  function dateSourceColumns() {
    if (document.getElementById('dateViewMode')?.value !== 'compare' || typeof window.loadStore !== 'function') return [];
    const selected = document.getElementById('reportDate')?.value;
    if (!selected || !DATE_RE.test(selected)) return [];
    const base = new Date(selected + 'T12:00:00');
    const year = base.getFullYear();
    const day = base.getDate();
    const selectedMonth = base.getMonth() + 1;
    const range = document.getElementById('compareRange')?.value || 'ytd';
    const store = window.loadStore();
    return comparisonMonths(range, selectedMonth).map(month => {
      const target = clampDate(year, month, day);
      return cleanSources(store?.[target]?.marketingSources);
    });
  }

  function appendComparisonSources(tbody, columns) {
    if (!tbody) return;
    tbody.querySelectorAll(`[${ROW_ATTR}]`).forEach(row => row.remove());
    if (!columns.length) return;

    const expectedColumns = (tbody.closest('table')?.tHead?.rows?.[0]?.cells?.length || 1) - 1;
    if (expectedColumns !== columns.length) return;

    const totals = columns.map(sourceTotal);
    const labels = new Set();
    columns.forEach(sources => Object.keys(cleanSources(sources)).forEach(label => labels.add(label)));
    const sortedLabels = Array.from(labels).sort((a, b) => {
      const totalA = columns.reduce((sum, sources) => sum + (cleanSources(sources)[a] || 0), 0);
      const totalB = columns.reduce((sum, sources) => sum + (cleanSources(sources)[b] || 0), 0);
      return totalB - totalA || a.localeCompare(b, 'ru');
    });
    const colSpan = columns.length + 1;

    const group = document.createElement('tr');
    group.className = 'group-row group-marketing';
    group.setAttribute(ROW_ATTR, '1');
    group.innerHTML = `<td colspan="${colSpan}">${SOURCE_TITLE}</td>`;
    tbody.appendChild(group);

    if (!sortedLabels.length) {
      const empty = document.createElement('tr');
      empty.setAttribute(ROW_ATTR, '1');
      empty.className = 'row-marketing-input';
      empty.innerHTML = `<td>Источники</td>${columns.map(() => '<td><span class="empty">—</span></td>').join('')}`;
      tbody.appendChild(empty);
      return;
    }

    sortedLabels.forEach(label => {
      const row = document.createElement('tr');
      row.setAttribute(ROW_ATTR, '1');
      row.className = 'row-marketing-input';
      row.innerHTML = `<td>${escapeHtml(label)}</td>` + columns.map((sources, index) => {
        const value = cleanSources(sources)[label] || 0;
        return `<td>${value ? formatSourceValue(value, totals[index]) : '<span class="empty">—</span>'}</td>`;
      }).join('');
      tbody.appendChild(row);
    });

    const totalRow = document.createElement('tr');
    totalRow.setAttribute(ROW_ATTR, '1');
    totalRow.className = 'row-marketing-input important';
    totalRow.innerHTML = '<td>Всего первичных по источникам</td>' + totals.map(total =>
      `<td>${total ? `${countFmt.format(total)} (100%)` : '<span class="empty">—</span>'}</td>`
    ).join('');
    tbody.appendChild(totalRow);

    if (window.matchMedia('(max-width:720px)').matches && tbody.closest('table')?.classList.contains('az-management-mobile-table')) {
      tbody.querySelectorAll(`[${ROW_ATTR}]`).forEach(row => {
        const firstCell = row.cells?.[0];
        if (!firstCell || (firstCell.colSpan || 1) !== 1) return;
        firstCell.classList.add('az-mgmt-label-cell');
        const background = getComputedStyle(firstCell).backgroundColor;
        firstCell.style.setProperty('background-color', background && background !== 'rgba(0, 0, 0, 0)' ? background : '#fcf9fc', 'important');
      });
    }
  }

  function renderComparisonSources() {
    appendComparisonSources(document.getElementById('periodCompareBody'), periodSourceColumns());
    appendComparisonSources(document.getElementById('dateCompareBody'), dateSourceColumns());
  }

  function refreshAll() {
    replaceLabels();
    renderSingleDateSources();
    renderComparisonSources();
  }

  // Ручное редактирование отчёта не должно стирать автоматически загруженные источники.
  const nativeSaveStore = window.saveStore;
  if (typeof nativeSaveStore === 'function' && !window.__azMarketingSourcesSaveGuard) {
    window.__azMarketingSourcesSaveGuard = true;
    window.saveStore = function preservingMarketingSources(nextStore) {
      const previousStore = typeof window.loadStore === 'function' ? window.loadStore() : {};
      if (nextStore && typeof nextStore === 'object') {
        Object.entries(nextStore).forEach(([dateValue, row]) => {
          if (!row || typeof row !== 'object' || Object.prototype.hasOwnProperty.call(row, 'marketingSources')) return;
          const previous = previousStore?.[dateValue]?.marketingSources;
          if (previous && typeof previous === 'object' && !Array.isArray(previous)) row.marketingSources = {...previous};
        });
      }
      const result = nativeSaveStore(nextStore);
      setTimeout(refreshAll, 0);
      return result;
    };
  }

  const nativeLoadDate = window.loadDate;
  if (typeof nativeLoadDate === 'function' && !window.__azMarketingSourcesLoadDateHook) {
    window.__azMarketingSourcesLoadDateHook = true;
    window.loadDate = function marketingSourcesLoadDateHook(...args) {
      const result = nativeLoadDate.apply(this, args);
      Promise.resolve(result).finally(() => setTimeout(refreshAll, 0));
      return result;
    };
  }

  const nativeRenderPeriod = window.renderPeriod;
  if (typeof nativeRenderPeriod === 'function' && !window.__azMarketingSourcesPeriodHook) {
    window.__azMarketingSourcesPeriodHook = true;
    window.renderPeriod = function marketingSourcesPeriodHook(...args) {
      const result = nativeRenderPeriod.apply(this, args);
      setTimeout(refreshAll, 0);
      return result;
    };
  }

  document.addEventListener('change', () => setTimeout(refreshAll, 0), true);
  document.addEventListener('click', event => {
    if (event.target?.closest?.('#entryBtn,#entrySave,#todayBtn')) setTimeout(refreshAll, 80);
  }, true);

  setTimeout(refreshAll, 0);
  setTimeout(refreshAll, 250);
  setTimeout(refreshAll, 800);
})();
