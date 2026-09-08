(() => {
  let economics = null;
  let auditExpanded = true;
  let observer = null;
  let scheduled = false;

  const money = value => value === null || value === undefined
    ? '—'
    : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(Number(value) || 0) + ' ₽';

  const norm = value => String(value || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/\(\s*[cс]\s*\)/gi, '')
    .replace(/[^а-яa-z0-9\s-]/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  function assignmentDirection(item) {
    if (item?.group !== 'Врачи') return null;
    const text = norm([item.department, item.role, item.function].join(' '));
    if (/(остеопат|нутрициолог|подиатр|гастроэнтеролог|нейропсихолог|массаж|миофункцион|логопед|медицинская сестра по массажу)/.test(text)) return 'structure';
    if (/(ортодонт|стоматолог|ортопед|хирург|гигиенист|терапия|терапевт)/.test(text)) return 'dent';
    return null;
  }

  function periodValues(filter) {
    const assignments = (economics?.payroll?.assignments || []).filter(filter);
    const period = new Set(economics?.period || []);
    return Array.from({ length: 12 }, (_, index) => {
      const month = `2026-${String(index + 1).padStart(2, '0')}`;
      if (!period.has(month)) return null;
      return assignments.reduce((sum, item) => sum + (Number(item.months?.[month]) || 0), 0);
    });
  }

  function total(values) {
    return values.reduce((sum, value) => sum + (Number(value) || 0), 0);
  }

  function groups() {
    const all = periodValues(() => true);
    const rows = [
      ['Стоматология — начисленный ФОТ врачей', periodValues(item => item.group === 'Врачи' && assignmentDirection(item) === 'dent')],
      ['Отделение структуры — начисленный ФОТ врачей', periodValues(item => item.group === 'Врачи' && assignmentDirection(item) === 'structure')],
      ['Административный блок — АУП + администраторы', periodValues(item => item.group === 'АУП' || item.group === 'Администраторы')],
      ['Вспомогательный персонал', periodValues(item => item.group === 'Вспомогательный персонал')],
      ['Лаборатория', periodValues(item => item.group === 'Лаборатория')],
      ['Маркетинг — штатный ФОТ', periodValues(item => item.group === 'Штатный маркетинг')],
    ];
    const unassigned = periodValues(item => item.group === 'Врачи' && !assignmentDirection(item));
    if (Math.abs(total(unassigned)) > 0.01) rows.splice(2, 0, ['Врачи — направление не распределено', unassigned]);
    return { all, rows };
  }

  function installStyle() {
    if (document.getElementById('azAccrualFotStyle')) return;
    const style = document.createElement('style');
    style.id = 'azAccrualFotStyle';
    style.textContent = `
      tr.az-accrual-main td{background:#edf5ee!important;border-top:2px solid #9fb5a4;font-weight:700}
      tr.az-accrual-main td.first{background:#edf5ee!important}
      tr.az-accrual-child td{background:#f7fbf7!important}
      tr.az-accrual-child td.first{background:#f7fbf7!important}
      .az-accrual-note{display:block;margin-top:2px;font-size:8px;line-height:1.25;color:#607066;font-weight:500}
      .az-accrual-total{font-weight:700;color:#31543d}
    `;
    document.head.appendChild(style);
  }

  function rowHtml(label, values, main = false) {
    const aggregate = total(values);
    const toggle = main
      ? `<button type="button" class="toggle az-accrual-toggle">${auditExpanded ? '−' : '+'}</button>`
      : '<span class="leaf-space"></span>';
    const level = main ? 2 : 3;
    const note = main
      ? `<span class="az-accrual-note">Контроль начислений; в кассовую сумму строки «ФОТ — по фактической оплате» повторно не включается. Январь–июнь: <span class="az-accrual-total">${money(aggregate)}</span></span>`
      : `<span class="az-accrual-note">Январь–июнь: ${money(aggregate)}</span>`;
    const cells = values.map(value => `<td><span class="cell-main">${money(value)}</span></td>`).join('');
    return `<tr class="${main ? 'az-accrual-main' : 'az-accrual-child'}"><td class="first"><div class="label"><span class="indent" style="--level:${level}"></span>${toggle}<span class="row-label">${label}${note}</span></div></td>${cells}</tr>`;
  }

  function inject() {
    scheduled = false;
    if (!economics?.available || economics?.control?.status !== 'OK') return;
    if (String(document.getElementById('year')?.value || '') !== '2026') return;
    const body = document.getElementById('body');
    if (!body || body.querySelector('tr.az-accrual-main')) return;

    const fotRow = [...body.querySelectorAll('tr')].find(row => {
      const label = row.querySelector('.row-label')?.textContent?.trim() || '';
      return label === 'ФОТ' || label.startsWith('ФОТ —');
    });
    if (!fotRow) return;

    installStyle();
    const labelEl = fotRow.querySelector('.row-label');
    if (labelEl) {
      labelEl.textContent = 'ФОТ — по фактической оплате';
      labelEl.title = 'ФОТ — по фактической оплате';
    }

    const data = groups();
    const wrapper = document.createElement('tbody');
    wrapper.innerHTML = rowHtml('Начисленный ФОТ по зарплатному реестру', data.all, true)
      + (auditExpanded ? data.rows.map(([label, values]) => rowHtml(label, values, false)).join('') : '');
    const fragment = document.createDocumentFragment();
    [...wrapper.children].forEach(row => fragment.appendChild(row));
    fotRow.insertAdjacentElement('afterend', fragment.firstChild);
    let anchor = fotRow.nextElementSibling;
    while (fragment.firstChild) {
      anchor.insertAdjacentElement('afterend', fragment.firstChild);
      anchor = anchor.nextElementSibling;
    }

    body.querySelector('.az-accrual-toggle')?.addEventListener('click', event => {
      event.stopPropagation();
      auditExpanded = !auditExpanded;
      body.querySelectorAll('tr.az-accrual-main,tr.az-accrual-child').forEach(row => row.remove());
      schedule();
    });
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    setTimeout(inject, 0);
  }

  async function load() {
    try {
      const response = await fetch('/api/reports/economics-control', { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      economics = (await response.json()).data;
      if (!economics?.available || economics?.control?.status !== 'OK') return;
      const body = document.getElementById('body');
      if (body) {
        observer = new MutationObserver(() => {
          if (!body.querySelector('tr.az-accrual-main')) schedule();
        });
        observer.observe(body, { childList: true, subtree: false });
      }
      document.getElementById('year')?.addEventListener('change', schedule);
      schedule();
    } catch (error) {
      console.error('AZ Finrez payroll audit rows failed', error);
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load, { once: true });
  else load();
})();
