(() => {
  const frame = document.getElementById('serviceFrame');
  if (!frame) return;

  const DATA_KEY = 'az-service-analytics-v1';
  const SALARY_KEY = 'az-service-salary-v1';
  const EXTRA_KEY = 'az-service-extra-payments-v1';
  const LAB_DIRECTION = 'Лаборатория';
  let observer = null;
  let scheduled = false;

  frame.addEventListener('load', () => {
    const w = frame.contentWindow;
    const d = frame.contentDocument;
    if (!w || !d) return;

    installStyle(d);

    const schedule = () => {
      if (scheduled) return;
      scheduled = true;
      setTimeout(() => {
        scheduled = false;
        refresh(w, d);
      }, 0);
    };

    if (observer) observer.disconnect();
    const content = d.getElementById('content');
    if (content) {
      observer = new MutationObserver(() => {
        if (!d.querySelector('#content tr.az-summary-econ')) schedule();
      });
      observer.observe(content, { childList: true, subtree: true });
    }

    ['direction','grouping','focusPeriod','doctor','category'].forEach(id => {
      d.getElementById(id)?.addEventListener('change', schedule);
    });
    d.querySelectorAll('[data-tab]').forEach(btn => btn.addEventListener('click', schedule));
    d.addEventListener('click', e => {
      if (e.target.closest('#azPayrollSave') || e.target.closest('#azPaySave')) {
        setTimeout(() => refresh(w, d), 80);
      }
    });

    schedule();
  });

  function installStyle(d) {
    if (d.getElementById('azSummaryEconStyle')) return;
    const s = d.createElement('style');
    s.id = 'azSummaryEconStyle';
    s.textContent = `
      .table tr.az-summary-econ td{background:#fffaf0!important;font-weight:600}
      .table tr.az-summary-econ td:first-child{background:#fffaf0!important}
      .table tr.az-summary-econ-start td{border-top:2px solid #cdbb9a!important}
      .table tr.az-summary-econ-total td{background:#f7efe1!important;font-weight:700}
      .table tr.az-summary-econ-total td:first-child{background:#f7efe1!important}
      .table tr.az-summary-econ-margin td{background:#edf4ee!important;font-weight:700}
      .table tr.az-summary-econ-margin td:first-child{background:#edf4ee!important}
      .az-summary-dash{color:#aaa!important;font-weight:400!important}
      .az-summary-good{color:#32724b!important}
      .az-summary-bad{color:#9a4b48!important}
      .az-summary-note{display:block;margin-top:2px;font-size:8.5px;color:#8b8275;font-weight:500}
    `;
    d.head.appendChild(s);
  }

  function refresh(w, d) {
    d.querySelectorAll('#content tr.az-summary-econ').forEach(r => r.remove());

    const summaryTab = !!d.querySelector('[data-tab="summary"].active');
    const direction = d.getElementById('direction')?.value;
    if (!summaryTab || !direction || direction === LAB_DIRECTION) return;

    const data = getData(w);
    const dir = data?.directions?.[direction];
    const doctors = Object.keys(dir?.doctors || {});
    if (!data || !dir || !doctors.length) return;

    const table = d.querySelector('#content .table');
    const tbody = table?.querySelector('tbody');
    const totalRow = tbody?.querySelector('tr.total-row');
    if (!tbody || !totalRow) return;

    const groups = getGroups(w, data, d);
    const focus = d.getElementById('focusPeriod')?.value || 'all';
    const allIdx = (data.months || []).map((_, i) => i);
    const salaryStore = loadStore(w, SALARY_KEY);
    const extraStore = loadStore(w, EXTRA_KEY);

    const metricsFor = idx => economicsForAllDoctors(dir, direction, doctors, salaryStore, extraStore, data.months.length, idx);
    insertRows(tbody, totalRow, groups, focus, metricsFor, metricsFor(allIdx));
  }

  function insertRows(tbody, totalRow, groups, focus, metricsFor, grand) {
    const defs = [
      { key:'revenue', label:'Выручка всех врачей (все услуги)', fmt:money, start:true },
      { key:'salary', label:'Основная ЗП врачей', fmt:money },
      { key:'extras', label:'Прочие выплаты врачам', fmt:money },
      { key:'total', label:'Всего выплат врачам', fmt:money, total:true },
      { key:'profit', label:'Доход после ЗП и прочих выплат', fmt:money, color:true },
      { key:'margin', label:'Маржинальность после выплат, %', fmt:percent, color:true, margin:true }
    ];

    let anchor = totalRow;
    defs.forEach(def => {
      const row = tbody.ownerDocument.createElement('tr');
      row.className = `az-summary-econ${def.start?' az-summary-econ-start':''}${def.total?' az-summary-econ-total':''}${def.margin?' az-summary-econ-margin':''}`;
      let html = `<td>${escapeHtml(def.label)}${def.start ? '<span class="az-summary-note">Экономика считается по всей выручке направления; пустые поля выплат считаются нулём</span>' : ''}</td>`;
      groups.forEach(g => {
        const e = metricsFor(g.idx);
        html += `<td class="az-summary-dash ${focus===g.id?'focus':''}">—</td>`;
        html += valueCell(e[def.key], def.fmt, def.color, focus===g.id);
      });
      html += '<td class="total-col az-summary-dash">—</td>';
      html += valueCell(grand[def.key], def.fmt, def.color, false, true);
      html += '<td class="az-summary-dash">—</td>';
      row.innerHTML = html;
      anchor.insertAdjacentElement('afterend', row);
      anchor = row;
    });
  }

  function economicsForAllDoctors(dir, direction, doctors, salaryStore, extraStore, monthCount, idx) {
    const revenue = revenueDirection(dir, idx);
    let salary = 0;
    let extras = 0;

    doctors.forEach(doctor => {
      const salaryArr = normalizeArray(salaryStore?.[direction]?.[doctor], monthCount, null);
      const extraArr = normalizeArray(extraStore?.[direction]?.[doctor], monthCount, 0);
      idx.forEach(i => {
        salary += Number.isFinite(+salaryArr[i]) ? (+salaryArr[i] || 0) : 0;
        extras += Number.isFinite(+extraArr[i]) ? (+extraArr[i] || 0) : 0;
      });
    });

    const total = salary + extras;
    const profit = revenue - total;
    return {
      revenue,
      salary,
      extras,
      total,
      profit,
      margin: revenue ? profit / revenue : null
    };
  }

  function revenueDirection(dir, idx) {
    let total = 0;
    Object.keys(dir?.doctors || {}).forEach(doctor => {
      Object.values(dir.doctors?.[doctor] || {}).forEach(arr => {
        if (!Array.isArray(arr)) return;
        idx.forEach(i => total += +(arr[i]?.[1] || 0));
      });
    });
    return total;
  }

  function valueCell(value, formatter, color, focus, totalCol = false) {
    const classes = [];
    if (focus) classes.push('focus');
    if (totalCol) classes.push('total-col');
    if (color && value != null) classes.push(value >= 0 ? 'az-summary-good' : 'az-summary-bad');
    return `<td class="${classes.join(' ')}">${value == null ? '—' : formatter(value)}</td>`;
  }

  function getGroups(w, data, d) {
    try {
      if (typeof w.availableGroups === 'function') return w.availableGroups();
    } catch {}
    const n = data?.months?.length || 0;
    const grouping = d.getElementById('grouping')?.value || 'month';
    let defs = [];
    if (grouping === 'month') defs = (data.months || []).map((label,i)=>({id:'m'+i,label,idx:[i],expected:1}));
    if (grouping === 'quarter') defs = [
      {id:'q1',label:'I квартал',idx:[0,1,2],expected:3},
      {id:'q2',label:'II квартал',idx:[3,4,5],expected:3},
      {id:'q3',label:'III квартал',idx:[6,7,8],expected:3},
      {id:'q4',label:'IV квартал',idx:[9,10,11],expected:3}
    ];
    if (grouping === 'half') defs = [
      {id:'h1',label:'I полугодие',idx:[0,1,2,3,4,5],expected:6},
      {id:'h2',label:'II полугодие',idx:[6,7,8,9,10,11],expected:6}
    ];
    return defs.map(g => ({...g, idx:g.idx.filter(i => i < n)})).filter(g => g.idx.length);
  }

  function normalizeArray(arr, n, fill) {
    const out = Array.isArray(arr) ? arr.slice(0, n) : [];
    while (out.length < n) out.push(fill);
    return out;
  }

  function loadStore(w, key) {
    try { return JSON.parse(w.localStorage.getItem(key) || '{}'); }
    catch { return {}; }
  }

  function getData(w) {
    try { return JSON.parse(w.localStorage.getItem(DATA_KEY) || 'null'); }
    catch { return null; }
  }

  function money(n) {
    return new Intl.NumberFormat('ru-RU', { maximumFractionDigits:0 }).format(n || 0) + ' ₽';
  }

  function percent(n) {
    return new Intl.NumberFormat('ru-RU', { style:'percent', maximumFractionDigits:1 }).format(n);
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
})();
