(() => {
  let applying = false;

  const sumPresent = values => {
    let any = false;
    let total = 0;
    (values || []).forEach(value => {
      if (value === null || value === undefined || value === '' || !Number.isFinite(+value)) return;
      any = true;
      total += +value;
    });
    return any ? total : null;
  };

  const rowTotal = (row, year) => {
    if (row && Object.prototype.hasOwnProperty.call(row, 'yearTotal')) return row.yearTotal;
    if (row?.id === 'execution') {
      let planTotal = 0;
      let factTotal = 0;
      let any = false;
      for (let index = 0; index < 12; index++) {
        const fact = factFor(year, index);
        const plan = planFor(year, index);
        if (fact === null || plan === null) continue;
        factTotal += Number(fact) || 0;
        planTotal += Number(plan) || 0;
        any = true;
      }
      return any && planTotal ? factTotal / planTotal : null;
    }
    if (row?.fmt === 'percent') return null;
    return sumPresent(row?.values);
  };

  const annualShare = (row, year, value) => {
    if (!row?.shareMode || value === null || value === undefined) return '';
    const all = Array.from({ length: 12 }, (_, index) => operatingExpenses(year, index))
      .reduce((sum, item) => sum + (Number(item) || 0), 0);
    const allShare = all ? Number(value) / all : null;
    if (row.shareMode === 'all') {
      return `<span class="cell-share"><strong>${sharePercent(allShare)}</strong> всех расходов</span>`;
    }
    const categoryTotal = sumPresent(row.categoryValues);
    const categoryShare = categoryTotal ? Number(value) / categoryTotal : null;
    return `<span class="cell-share"><strong>${sharePercent(allShare)}</strong> всех · <span class="share-category"><strong>${sharePercent(categoryShare)}</strong> категории</span></span>`;
  };

  function applyTotalColumn() {
    if (applying) return;
    if (typeof buildRows !== 'function' || typeof flatten !== 'function' || typeof money !== 'function' || typeof percent !== 'function') return;
    const body = document.getElementById('body');
    const head = document.getElementById('head');
    const yearSelect = document.getElementById('year');
    if (!body || !head || !yearSelect || !yearSelect.value) return;

    applying = true;
    try {
      let style = document.getElementById('azFinrezNativeTotalStyle');
      if (!style) {
        style = document.createElement('style');
        style.id = 'azFinrezNativeTotalStyle';
        style.textContent = [
          '.az-finrez-total{background:#f6f0e4!important;font-weight:700}',
          '.az-finrez-total-head{background:#eee5d6!important;font-weight:800}',
          '#finrezTable col[data-az-total]{width:118px;min-width:118px;max-width:118px}'
        ].join('');
        document.head.appendChild(style);
      }

      if (!head.querySelector('.az-finrez-total-head')) {
        head.insertAdjacentHTML('beforeend', '<th class="az-finrez-total az-finrez-total-head">Итого</th>');
      }

      const colgroup = document.querySelector('#finrezTable colgroup');
      if (colgroup && !colgroup.querySelector('col[data-az-total]')) {
        const col = document.createElement('col');
        col.className = 'month-col';
        col.dataset.azTotal = '1';
        colgroup.appendChild(col);
      }

      const year = Number(yearSelect.value);
      const rows = flatten(buildRows(year));
      const tableRows = Array.from(body.children).filter(node => node.tagName === 'TR');

      tableRows.forEach((tr, index) => {
        if (tr.querySelector('td.az-finrez-total')) return;
        const row = rows[index];
        if (!row) return;
        const value = rowTotal(row, year);
        const formatted = row.fmt === 'percent' ? percent(value) : money(value);
        let cls = 'az-finrez-total';
        if ((row.cls === 'operating' || row.cls === 'net') && value !== null && value !== undefined) {
          cls += Number(value) >= 0 ? ' positive' : ' negative';
        }
        if (formatted === '—') cls += ' muted';
        const share = row.fmt === 'money' ? annualShare(row, year, value) : '';
        tr.insertAdjacentHTML('beforeend', `<td class="${cls}"><span class="cell-main">${formatted}</span>${share}</td>`);
      });
    } finally {
      applying = false;
    }
  }

  const body = document.getElementById('body');
  if (body) {
    const observer = new MutationObserver(() => requestAnimationFrame(applyTotalColumn));
    observer.observe(body, { childList: true });
  }

  window.addEventListener('load', () => requestAnimationFrame(applyTotalColumn));
  requestAnimationFrame(applyTotalColumn);
})();
