(() => {
  const PROFIT_LABELS = [
    'Маржинальная прибыль',
    'Валовая прибыль',
    'EBITDA',
    'EBIT / операционная прибыль',
    'Прибыль до налогообложения',
    'Чистая прибыль'
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

  if (typeof buildRows === 'function' && typeof node === 'function' && typeof values12 === 'function') {
    const originalBuildRows = buildRows;
    buildRows = function(year) {
      const rows = originalBuildRows(year);
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
