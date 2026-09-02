(() => {
  const frame = document.getElementById('serviceFrame');
  if (!frame) return;

  const EXTRA_KEY = 'az-service-extra-payments-v1';
  const LAB_DIRECTION = 'Лаборатория';
  const LAB_TOTAL_KEY = '__LAB_TOTAL__';
  let extraDraft = null;
  let observer = null;

  frame.addEventListener('load', () => {
    const w = frame.contentWindow;
    const d = frame.contentDocument;
    if (!w || !d) return;

    const enhance = () => enhanceLaboratory(w, d);
    if (observer) observer.disconnect();
    observer = new MutationObserver(enhance);
    observer.observe(d.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });

    d.addEventListener('click', e => {
      const manager = d.getElementById('azPayrollManager');
      if (!manager || manager.classList.contains('az-payroll-hide')) return;
      if (e.target.closest('.az-month-tab') || e.target.closest('#azPayrollSave')) captureExtra(d);
    }, true);

    d.addEventListener('change', e => {
      if (e.target?.id === 'azPayrollDirection') captureExtra(d);
    }, true);

    d.addEventListener('click', e => {
      if (!e.target.closest('#azPayrollSave')) return;
      setTimeout(() => persistExtras(w), 0);
    });

    enhance();
  });

  function enhanceLaboratory(w, d) {
    const manager = d.getElementById('azPayrollManager');
    if (!manager) return;
    if (manager.classList.contains('az-payroll-hide')) {
      extraDraft = null;
      return;
    }

    if (!extraDraft) extraDraft = loadDraft(w, d);
    const direction = d.getElementById('azPayrollDirection')?.value;
    if (direction !== LAB_DIRECTION) return;

    const box = d.querySelector('.az-payroll-labbox');
    const baseInput = d.getElementById('azPayrollLabTotal');
    if (!box || !baseInput || box.dataset.splitPayments === '1') return;

    const monthIndex = currentMonthIndex(d);
    const monthName = d.querySelector('.az-month-tab.active')?.textContent?.trim() || 'месяц';
    const baseValue = baseInput.value || '';
    const extraValue = extraDraft?.[monthIndex] ?? '';

    const note = d.getElementById('azPayrollNote');
    if (note) note.textContent = 'Для лаборатории вводим выплаты всем сотрудникам общими суммами, без разбивки по людям: отдельно ФОТ и отдельно прочие выплаты (отпускные, больничные, премии и другие доплаты).';

    box.dataset.splitPayments = '1';
    box.innerHTML = `
      <h4>Лаборатория — все сотрудники</h4>
      <div class="small">Общие суммы за ${escapeHtml(monthName)}, без разбивки по сотрудникам.</div>
      <table class="az-payroll-table" style="margin-top:12px;min-width:620px">
        <thead><tr><th>Сотрудники</th><th>ФОТ</th><th>Прочие выплаты</th><th>Всего</th></tr></thead>
        <tbody><tr>
          <td>Все сотрудники лаборатории</td>
          <td><input id="azPayrollLabTotal" inputmode="decimal" value="${escapeAttr(baseValue)}" placeholder="—"></td>
          <td><input id="azPayrollLabExtra" inputmode="decimal" value="${extraValue == null || extraValue === 0 ? '' : escapeAttr(extraValue)}" placeholder="0"></td>
          <td id="azPayrollLabCombined" class="az-payroll-total"></td>
        </tr></tbody>
      </table>`;

    const base = d.getElementById('azPayrollLabTotal');
    const extra = d.getElementById('azPayrollLabExtra');
    const recalc = () => {
      const b = parseMoney(base?.value, 0) || 0;
      const x = parseMoney(extra?.value, 0) || 0;
      const total = d.getElementById('azPayrollLabCombined');
      if (total) total.textContent = money(b + x);
      const status = d.getElementById('azPayrollStatus');
      if (status) status.textContent = `${monthName}: ФОТ ${b ? money(b) : '—'} · прочие ${x ? money(x) : '0 ₽'} · всего ${money(b + x)}`;
    };
    base?.addEventListener('input', recalc);
    extra?.addEventListener('input', () => {
      extraDraft[monthIndex] = parseMoney(extra.value, 0);
      recalc();
    });
    recalc();
  }

  function captureExtra(d) {
    if (!extraDraft) return;
    if (d.getElementById('azPayrollDirection')?.value !== LAB_DIRECTION) return;
    const input = d.getElementById('azPayrollLabExtra');
    if (!input) return;
    extraDraft[currentMonthIndex(d)] = parseMoney(input.value, 0);
  }

  function persistExtras(w) {
    if (!extraDraft) return;
    let store = {};
    try { store = JSON.parse(w.localStorage.getItem(EXTRA_KEY) || '{}'); } catch {}
    store[LAB_DIRECTION] ||= {};
    store[LAB_DIRECTION][LAB_TOTAL_KEY] = extraDraft.slice();
    w.localStorage.setItem(EXTRA_KEY, JSON.stringify(store));
  }

  function loadDraft(w, d) {
    const monthCount = d.querySelectorAll('.az-month-tab').length || 12;
    let store = {};
    try { store = JSON.parse(w.localStorage.getItem(EXTRA_KEY) || '{}'); } catch {}
    const source = store?.[LAB_DIRECTION]?.[LAB_TOTAL_KEY];
    const out = Array.isArray(source) ? source.slice(0, monthCount) : [];
    while (out.length < monthCount) out.push(0);
    return out;
  }

  function currentMonthIndex(d) {
    const active = d.querySelector('.az-month-tab.active');
    const i = Number(active?.dataset?.mi);
    return Number.isFinite(i) ? i : 0;
  }

  function parseMoney(v, blank) {
    const s = String(v ?? '').trim().replace(/\s/g, '').replace(',', '.');
    if (!s) return blank;
    const n = Number(s);
    return Number.isFinite(n) ? n : blank;
  }

  function money(n) {
    return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(n || 0) + ' ₽';
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
  }

  function escapeAttr(s) {
    return escapeHtml(String(s ?? ''));
  }
})();
