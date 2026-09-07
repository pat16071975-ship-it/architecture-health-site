(() => {
  'use strict';

  const STYLE_ID = 'az-sticky-report-headers-style';
  const INSTALL_FLAG = '__azStickyReportHeadersInstalled';
  const TABLE_SELECTOR = [
    '.table-wrap table',
    '.matrix-wrap table',
    'table.table',
    'table.matrix',
    'table.data-table',
    'table.period-table'
  ].join(',');
  const SCROLL_SELECTOR = '.table-wrap,.matrix-wrap';

  function install(doc) {
    if (!doc?.documentElement || doc[INSTALL_FLAG]) return;
    doc[INSTALL_FLAG] = true;

    injectStyle(doc);

    const win = doc.defaultView;
    let frameId = 0;

    const schedule = () => {
      if (frameId) return;
      const requestFrame = win?.requestAnimationFrame?.bind(win) || (fn => setTimeout(fn, 16));
      frameId = requestFrame(() => {
        frameId = 0;
        refresh(doc);
      });
    };

    const Observer = win?.MutationObserver || globalThis.MutationObserver;
    if (Observer) {
      const observer = new Observer(schedule);
      observer.observe(doc.documentElement, { childList: true, subtree: true });
    }

    win?.addEventListener('resize', schedule, { passive: true });
    doc.addEventListener('change', schedule, true);
    doc.addEventListener('click', () => setTimeout(schedule, 0), true);
    doc.fonts?.ready?.then(schedule).catch(() => {});

    schedule();
    setTimeout(schedule, 250);
    setTimeout(schedule, 1000);
  }

  function injectStyle(doc) {
    if (doc.getElementById(STYLE_ID)) return;

    const style = doc.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      .az-sticky-report-scroll {
        position: relative !important;
        overflow: auto !important;
        max-height: min(72vh, calc(100vh - 190px)) !important;
        overscroll-behavior: contain;
        scrollbar-gutter: stable;
      }
      .az-sticky-report-table {
        border-collapse: separate !important;
        border-spacing: 0 !important;
      }
      .az-sticky-report-table thead .az-sticky-report-cell {
        position: sticky !important;
        top: var(--az-sticky-row-top, 0px) !important;
        z-index: 40 !important;
        background: var(--az-sticky-cell-bg, #faf6ef) !important;
        background-clip: padding-box !important;
      }
      .az-sticky-report-table thead .az-sticky-report-cell:first-child {
        z-index: 44 !important;
      }
      .az-sticky-report-table thead tr.az-sticky-report-last-row .az-sticky-report-cell {
        box-shadow: 0 2px 0 rgba(181, 150, 98, .42), 0 5px 10px rgba(69, 55, 35, .08);
      }

      /* В многострочной шапке первый "Кол." второго ряда не должен
         прилипать слева и съезжать относительно названия месяца. */
      .az-sticky-report-table thead .az-sticky-report-cell {
        left: auto !important;
      }
      .az-sticky-report-table thead .az-sticky-report-first-head {
        left: 0 !important;
        z-index: 48 !important;
      }

      @media (max-width: 700px) {
        .az-sticky-report-scroll {
          max-height: min(68dvh, calc(100dvh - 150px)) !important;
          scrollbar-gutter: auto;
        }

        /* Статичная колонка занимает ровно 30% экрана телефона. */
        .az-sticky-report-table .az-sticky-report-first-head,
        .az-sticky-report-table .az-sticky-report-first-col {
          width: 30vw !important;
          min-width: 30vw !important;
          max-width: 30vw !important;
          white-space: normal !important;
          overflow-wrap: anywhere !important;
          word-break: normal !important;
        }
        .az-sticky-report-table .az-sticky-report-first-col {
          position: sticky !important;
          left: 0 !important;
          z-index: 18 !important;
          background: var(--az-sticky-cell-bg, #fffdf8) !important;
          background-clip: padding-box !important;
        }

        /* Числовые колонки получают одинаковую ширину — месяц,
           Кол. и Сумма больше не гуляют относительно друг друга. */
        .az-sticky-report-table tbody tr > *:not(.az-sticky-report-first-col),
        .az-sticky-report-table thead tr:last-child > *:not(.az-sticky-report-first-head) {
          width: 76px !important;
          min-width: 76px !important;
          max-width: 76px !important;
          white-space: nowrap !important;
        }
        .az-sticky-report-table thead tr:first-child > *:not(.az-sticky-report-first-head) {
          white-space: nowrap !important;
          text-align: center !important;
        }

        .az-sticky-report-table th,
        .az-sticky-report-table td {
          padding-left: 6px !important;
          padding-right: 6px !important;
        }
        .az-sticky-report-table .az-sticky-report-first-head,
        .az-sticky-report-table .az-sticky-report-first-col {
          font-size: 10.5px !important;
          line-height: 1.18 !important;
        }
      }
    `;
    (doc.head || doc.documentElement).appendChild(style);
  }

  function refresh(doc) {
    doc.querySelectorAll(TABLE_SELECTOR).forEach(table => pinTable(doc, table));
  }

  function pinTable(doc, table) {
    const head = table.tHead;
    if (!head?.rows?.length) return;

    table.classList.add('az-sticky-report-table');
    table.closest(SCROLL_SELECTOR)?.classList.add('az-sticky-report-scroll');

    table.querySelectorAll('.az-sticky-report-first-head').forEach(cell => {
      cell.classList.remove('az-sticky-report-first-head');
    });
    const firstHeadCell = head.rows[0]?.cells?.[0];
    if (firstHeadCell) firstHeadCell.classList.add('az-sticky-report-first-head');

    table.querySelectorAll('.az-sticky-report-first-col').forEach(cell => {
      cell.classList.remove('az-sticky-report-first-col');
    });
    Array.from(table.tBodies || []).forEach(body => {
      Array.from(body.rows || []).forEach(row => {
        const cell = row.cells?.[0];
        if (!cell) return;
        cell.classList.add('az-sticky-report-first-col');
        cell.style.setProperty('--az-sticky-cell-bg', cellBackground(doc, cell));
      });
    });

    const rows = Array.from(head.rows);
    let top = 0;

    rows.forEach((row, index) => {
      row.classList.toggle('az-sticky-report-last-row', index === rows.length - 1);
      row.style.setProperty('--az-sticky-row-top', `${top}px`);

      Array.from(row.cells).forEach(cell => {
        cell.classList.add('az-sticky-report-cell');
        cell.style.setProperty('--az-sticky-cell-bg', cellBackground(doc, cell));
      });

      top += rowHeight(doc, row);
    });
  }

  function rowHeight(doc, row) {
    const measured = row.getBoundingClientRect().height;
    if (measured > 0) return Math.ceil(measured);

    let fallback = 0;
    Array.from(row.cells).forEach(cell => {
      const style = doc.defaultView?.getComputedStyle(cell);
      const height = parseFloat(style?.height || '0');
      const padding = parseFloat(style?.paddingTop || '0') + parseFloat(style?.paddingBottom || '0');
      fallback = Math.max(fallback, height + padding);
    });
    return Math.ceil(fallback || 34);
  }

  function cellBackground(doc, cell) {
    const value = doc.defaultView?.getComputedStyle(cell).backgroundColor;
    return value && value !== 'transparent' && value !== 'rgba(0, 0, 0, 0)'
      ? value
      : '#faf6ef';
  }

  install(document);

  const frame = document.getElementById('serviceFrame');
  if (frame) {
    const installFrame = () => {
      try {
        install(frame.contentDocument);
      } catch (error) {
        console.warn('Sticky report headers: iframe is unavailable', error);
      }
    };

    frame.addEventListener('load', installFrame);
    if (frame.contentDocument?.readyState === 'interactive' || frame.contentDocument?.readyState === 'complete') {
      installFrame();
    }
  }
})();
