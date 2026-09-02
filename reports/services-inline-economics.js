(() => {
  const frame = document.getElementById('serviceFrame');
  if (!frame) return;

  const DATA_KEY = 'az-service-analytics-v1';
  const SALARY_KEY = 'az-service-salary-v1';
  const EXTRA_KEY = 'az-service-extra-payments-v1';
  const LAB_DIRECTION = 'Лаборатория';
  const LAB_TOTAL_KEY = '__LAB_TOTAL__';
  let observer = null;

  frame.addEventListener('load', () => {
    const w = frame.contentWindow;
    const d = frame.contentDocument;
    if (!w || !d) return;

    installStyle(d);

    if (observer) observer.disconnect();
    const content = d.getElementById('content');
    if (content) {
      observer = new MutationObserver(() => setTimeout(() => refresh(w, d), 0));
      observer.observe(content, { childList: true });
    }

    ['direction','grouping','focusPeriod','doctor','category'].forEach(id => {
      d.getElementById(id)?.addEventListener('change', () => setTimeout(() => refresh(w, d), 0));
    });
    d.querySelectorAll('[data-tab]').forEach(btn => btn.addEventListener('click', () => setTimeout(() => refresh(w, d), 0)));
    d.addEventListener('click', e => {
      if (e.target.closest('#azPayrollSave') || e.target.closest('#azPaySave')) {
        setTimeout(() => refresh(w, d), 60);
      }
    });

    setTimeout(() => refresh(w, d), 0);
  });

  function installStyle(d) {
    if (d.getElementById('azInlineEconStyle')) return;
    const s = d.createElement('style');
    s.id = 'azInlineEconStyle';
    s.textContent = `
      .table tr.az-inline-econ td{background:#fffaf0!important;font-weight:600}
      .table tr.az-inline-econ td:first-child{background:#fffaf0!important}
      .table tr.az-inline-econ-start td{border-top:2px solid #cdbb9a!important}
      .table tr.az-inline-econ-total td{background:#f7efe1!important;font-weight:700}
      .table tr.az-inline-econ-total td:first-child{background:#f7efe1!important}
      .table tr.az-inline-econ-margin td{background:#edf4ee!important;font-weight:700}
      .table tr.az-inline-econ-margin td:first-child{background:#edf4ee!important}
      .az-inline-dash{color:#aaa!important;font-weight:400!important}
      .az-inline-good{color:#32724b!important}
      .az-inline-bad{color:#9a4b48!important}
      .az-inline-note{display:block;margin-top:2px;font-size:8.5px;color:#8b8275;font-weight:500}
    `;
    d.head.appendChild(s);
  }

  function refresh(w, d) {
    d.querySelectorAll('#content tr.az-inline-econ').forEach(r => r.remove());

    const direction = d.getElementById('direction')?.value;
    const doctorTab = !!d.querySelector('[data-tab="doctor"].active');
    const summaryTab = !!d.querySelector('[data-tab="summary"].active');

    if (doctorTab && direction && direction !== LAB_DIRECTION) {
      injectDoctorRows(w, d, direction);
      return;
    }
    if (summaryTab && direction === LAB_DIRECTION) {
      injectLaboratoryRows(w, d);
    }
  }

  function injectDoctorRows(w, d, direction) {
    const data = getData(w);
    const dir = data?.directions?.[direction];
    const doctor = d.getElementById('doctor')?.value;
    if (!data || !dir || !doctor || doctor === 'all' || !dir.doctors?.[doctor]) return;

    const table = d.querySelector('#content .table');
    const tbody = table?.querySelector('tbody');
    const totalRow = tbody?.querySelector('tr.total-row');
    if (!tbody || !totalRow) return;

    const groups = getGroups(w, data, d);
    const focus = d.getElementById('focusPeriod')?.value || 'all';
    const allIdx = (data.months || []).map((_, i) => i);
    const salary = getArray(w, SALARY_KEY, direction, doctor, data.months.length, null);
    const extras = getArray(w, EXTRA_KEY, direction, doctor, data.months.length, 0);

    const metricsFor = idx => economicsForDoctor(dir, doctor, salary, extras, idx);
    insertEconomicsRows(tbody, totalRow, groups, focus, metricsFor, metricsFor(allIdx), false);
  }

  function injectLaboratoryRows(w, d) {
    const data = getData(w);
    const dir = data?.directions?.[LAB_DIRECTION];
    if (!data || !dir) return;

    const table = d.querySelector('#content .table');
    const tbody = table?.querySelector('tbody');
    const totalRow = tbody?.querySelector('tr.total-row');
    if (!tbody || !totalRow) return;

    const groups = getGroups(w, data, d);
    const focus = d.getElementById('focusPeriod')?.value || 'all';
    const allIdx = (data.months || []).map((_, i) => i);
    const salary = getArray(w, SALARY_KEY, LAB_DIRECTION, LAB_TOTAL_KEY, data.months.length, null);
    const extras = getArray(w, EXTRA_KEY, LAB_DIRECTION, LAB_TOTAL_KEY, data.months.length, 0);

    const metricsFor = idx => economicsForLaboratory(dir, salary, extras, idx);
    insertEconomicsRows(tbody, totalRow, groups, focus, metricsFor, metricsFor(allIdx), true);
  }

  function insertEconomicsRows(tbody, totalRow, groups, focus, metricsFor, grand, isLab) {
    const labels = isLab ? {
      revenue: 'Выручка лаборатории (все услуги)',
      salary: 'ФОТ лаборатории',
      extras: 'Прочие выплаты',
      total: 'Всего выплат',
      profit: 'Доход после ФОТ и прочих выплат',
      margin: 'Маржинальность после выплат, %'
    } : {
      revenue: 'Выручка врача (все услуги)',
      salary: 'Основная ЗП',
      extras: 'Прочие выплаты',
      total: 'Всего выплат врачу',
      profit: 'Доход после ЗП и прочих выплат',
      margin: 'Маржинальность после выплат, %'
    };

    const defs = [
      { key:'revenue', label:labels.revenue, fmt:money, start:true },
      { key:'salary', label:labels.salary, fmt:money },
      { key:'extras', label:labels.extras, fmt:money },
      { key:'total', label:labels.total, fmt:money, total:true },
      { key:'profit', label:labels.profit, fmt:money, color:true },
      { key:'margin', label:labels.margin, fmt:percent, color:true, margin:true }
    ];

    let anchor = totalRow;
    defs.forEach(def => {
      const row = tbody.ownerDocument.createElement('tr');
      row.className = `az-inline-econ${def.start?' az-inline-econ-start':''}${def.total?' az-inline-econ-total':''}${def.margin?' az-inline-econ-margin':''}`;
      let html = `<td>${escapeHtml(def.label)}${def.start ? '<span class="az-inline-note">Экономика считается по всей выручке, независимо от фильтра категории</span>' : ''}</td>`;
      groups.forEach(g => {
        const e = metricsFor(g.idx);
        html += `<td class="az-inline-dash ${focus===g.id?'focus':''}">—</td>`;
        html += valueCell(e[def.key], def.fmt, def.color, focus===g.id);
      });
      html += '<td class="total-col az-inline-dash">—</td>';
      html += valueCell(grand[def.key], def.fmt, def.color, false, true);
      html += '<td class="az-inline-dash">—</td>';
      row.innerHTML = html;
      anchor.insertAdjacentElement('afterend', row);
      anchor = row;
    });
  }

  function valueCell(value, formatter, color, focus, totalCol = false) {
    const classes = [];
    if (focus) classes.push('focus');
    if (totalCol) classes.push('total-col');
    if (color && value != null) classes.push(value >= 0 ? 'az-inline-good' : 'az-inline-bad');
    return `<td class="${classes.join(' ')}">${value == null ? '—' : formatter(value)}</td>`;
  }

  function economicsForDoctor(dir, doctor, salary, extras, idx) {
    return economics(revenueDoctor(dir, doctor, idx), salary, extras, idx);
  }

  function economicsForLaboratory(dir, salary, extras, idx) {
    return economics(revenueDirection(dir, idx), salary, extras, idx);
  }

  function economics(revenue, salary, extras, idx) {
    const baseValues = idx.map(i => salary[i]);
    const salaryComplete = baseValues.every(v => v !== null && v !== '' && v !== undefined && Number.isFinite(+v));
    const extra = idx.reduce((a, i) => a + (+extras[i] || 0), 0);
    if (!salaryComplete) {
      return { revenue, salary:null, extras:extra, total:null, profit:null, margin:null };
    }
    const base = baseValues.reduce((a, v) => a + (+v || 0), 0);
    const total = base + extra;
    const profit = revenue - total;
    return {
      revenue,
      salary:base,
      extras:extra,
      total,
      profit,
      margin:revenue ? profit / revenue : null
    };
  }

  function revenueDoctor(dir, doctor, idx) {
    let total = 0;
    Object.values(dir?.doctors?.[doctor] || {}).forEach(arr => {
      if (!Array.isArray(arr)) return;
      idx.forEach(i => total += +(arr[i]?.[1] || 0));
    });
    return total;
  }

  function revenueDirection(dir, idx) {
    let total = 0;
    Object.keys(dir?.doctors || {}).forEach(doctor => {
      total += revenueDoctor(dir, doctor, idx);
    });
    return total;
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

  function getArray(w, key, direction, subject, n, fill) {
    try {
      const store = JSON.parse(w.localStorage.getItem(key) || '{}');
      const source = store?.[direction]?.[subject];
      const out = Array.isArray(source) ? source.slice(0, n) : [];
      while (out.length < n) out.push(fill);
      return out;
    } catch {
      return Array(n).fill(fill);
    }
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
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#39;'}[c]));
  }
})();
