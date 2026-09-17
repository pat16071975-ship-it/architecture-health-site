(() => {
  const PROFIT_LABELS = [
    'Маржинальная прибыль',
    'Валовая прибыль',
    'EBITDA',
    'EBIT / операционная прибыль',
    'Прибыль до налогообложения',
    'Чистая прибыль'
  ];

  const FUTURE_PROFIT_IDS = [
    'amortization',
    'ebit',
    'interest',
    'ebt',
    'income-tax',
    'net'
  ];

  const style = document.createElement('style');
  style.id = 'az-finrez-profit-emphasis-style';
  style.textContent = `
    #body tr.az-profit-row td,
    #body tr.cash-result td {
      border-top:2px solid #9d8357 !important;
      border-bottom:2px solid #9d8357 !important;
      padding-top:10px !important;
      padding-bottom:10px !important;
      font-size:12px !important;
      font-weight:800 !important;
    }
    #body tr.az-profit-row td:first-child,
    #body tr.cash-result td:first-child { border-left:2px solid #9d8357 !important; }
    #body tr.az-profit-row td:last-child,
    #body tr.cash-result td:last-child { border-right:2px solid #9d8357 !important; }
    #body tr.az-profit-row .row-label,
    #body tr.cash-result .row-label { font-size:12.5px !important; font-weight:800 !important; }
    #body tr.cash-result td { background:#d8e7dc !important; }
    #body tr.cash-result .cell-main { font-size:12.5px !important; }
    @media(max-width:760px){
      #body tr.az-profit-row td,
      #body tr.cash-result td { font-size:11px !important; }
      #body tr.az-profit-row .row-label,
      #body tr.cash-result .row-label { font-size:11px !important; }
    }
  `;
  document.head.appendChild(style);

  function collapseFutureProfitRows(rows) {
    const targetIds = new Set(FUTURE_PROFIT_IDS);
    const children = rows
      .filter(row => targetIds.has(row.id))
      .map(row => ({ ...row, level: 1 }));
    if (children.length !== FUTURE_PROFIT_IDS.length) return rows;

    const compactRows = rows.filter(row => !targetIds.has(row.id));
    const ebitdaIndex = compactRows.findIndex(row => row.id === 'ebitda');
    if (ebitdaIndex < 0) return rows;

    const group = node(
      'future-profit-block',
      'Показатели после EBITDA — данные пока не загружены',
      0,
      Array(12).fill(null),
      children,
      'unavailable'
    );
    compactRows.splice(ebitdaIndex + 1, 0, group);
    return compactRows;
  }

  if (typeof buildRows === 'function' && typeof node === 'function' && typeof values12 === 'function') {
    const originalBuildRows = buildRows;
    buildRows = function(year) {
      const rows = collapseFutureProfitRows(originalBuildRows(year));
      const cashResult = node(
        'cash-result',
        'Остаток после всех учтённых выплат',
        0,
        values12(i => {
          const fact = factFor(year, i);
          if (fact === null) return null;
          return fact - operatingExpenses(year, i) - debtExpenses(year, i);
        }),
        [],
        'cash-result'
      );
      return [...rows, cashResult];
    };
  }

  function emphasizeRows() {
    const body = document.getElementById('body');
    if (!body) return;
    body.querySelectorAll('tr').forEach(tr => {
      const label = tr.querySelector('.row-label')?.textContent?.trim() || '';
      if (PROFIT_LABELS.some(prefix => label.startsWith(prefix))) {
        tr.classList.add('az-profit-row');
      }
    });
  }

  const body = document.getElementById('body');
  if (body) {
    new MutationObserver(() => requestAnimationFrame(emphasizeRows))
      .observe(body, { childList: true });
  }
  window.addEventListener('load', () => requestAnimationFrame(emphasizeRows));
  requestAnimationFrame(emphasizeRows);
})();
