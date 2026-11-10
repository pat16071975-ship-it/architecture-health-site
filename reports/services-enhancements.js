(() => {
  const frame = document.getElementById('serviceFrame');
  if (!frame) return;

  const SALARY_KEY = 'az-service-salary-v1';
  let compareActive = false;
  let observer = null;

  frame.addEventListener('load', () => {
    const w = frame.contentWindow;
    const d = frame.contentDocument;
    if (!w || !d) return;

    injectStyles(d);
    installUi(w, d);
  });

  function injectStyles(d) {
    if (d.getElementById('azEnhanceStyles')) return;
    const st = d.createElement('style');
    st.id = 'azEnhanceStyles';
    st.textContent = `
      .app{width:min(1760px,99vw)!important;padding:12px 0 30px!important}
      .topbar{margin-bottom:10px!important}
      .toolbar{padding:10px!important;gap:7px!important}
      .toolbar .field{min-width:158px!important}
      .field label{font-size:11px!important}
      .field select,.field input{padding:8px 10px!important;font-size:12px!important}
      .tabs{margin:9px 0!important}
      .btn{padding:8px 11px!important;font-size:12px!important}
      .status{margin:7px 0 9px!important;font-size:10px!important}
      .kpis{gap:7px!important;margin:9px 0!important}
      .kpi{padding:8px 10px!important}
      .kpi span{font-size:10px!important;margin-bottom:4px!important}
      .kpi strong{font-size:20px!important}
      .panel{margin-top:8px!important}
      .panel h2{padding:8px 12px!important;font-size:20px!important}
      .panel-note{padding:7px 12px!important;font-size:10px!important}
      .table-wrap{max-height:76vh!important}
      .table{min-width:900px!important}
      .table th,.table td{padding:5px 6px!important;font-size:10px!important;line-height:1.12!important}
      .table thead tr:nth-child(2) th{top:27px!important}
      .table td:first-child{min-width:225px!important;max-width:275px!important}
      .period-head{font-size:10px!important}
      .matrix{min-width:1060px!important}
      .matrix th,.matrix td{padding:5px 6px!important;font-size:9.5px!important}
      .matrix th:first-child,.matrix td:first-child{min-width:185px!important}
      .cell-sub{font-size:9px!important}
      .az-hidden{display:none!important}
      .az-finance{margin-top:8px}
      .az-finance .table{min-width:760px!important}
      .az-finance input{width:88px;max-width:100%;text-align:right;border:1px solid #d7cdbd;border-radius:6px;padding:5px 6px;font:600 10px Inter;background:#fff}
      .az-finance .readonly{font-weight:700}
      .az-finance .bad{color:#9a4b48}
      .az-finance .good{color:#32724b}
      .az-finance-note{padding:7px 12px;font-size:10px;color:#6f6658;background:#fffaf0;border-top:1px solid #eadfc9}
      .az-compare-field{min-width:185px!important}
      .az-compare-table{min-width:980px!important}
      .az-compare-table th,.az-compare-table td{padding:5px 7px!important}
      .az-compare-head{background:#f3eee4!important;text-align:center!important}
      .az-chip{display:inline-block;padding:2px 7px;border-radius:999px;background:#f0ece5;color:#686a64;font-size:9px;font-weight:700}
      .az-neutral{color:#747b74}
      .az-pos{color:#32724b;font-weight:700}
      .az-neg{color:#9a4b48;font-weight:700}
    `;
    d.head.appendChild(st);
  }

  function installUi(w, d) {
    const toolbar = d.querySelector('.toolbar');
    const doctor = d.querySelector('#doctor');
    const tabs = d.querySelector('.tabs');
    const content = d.querySelector('#content');
    if (!toolbar || !doctor || !tabs || !content) return;

    const doctorField = doctor.closest('.field');

    let doctor2Field = d.getElementById('azDoctor2Field');
    if (!doctor2Field) {
      doctor2Field = d.createElement('div');
      doctor2Field.id = 'azDoctor2Field';
      doctor2Field.className = 'field az-compare-field az-hidden';
      doctor2Field.innerHTML = '<label>Врач 2</label><select id="azDoctor2"></select>';
      doctorField.insertAdjacentElement('afterend', doctor2Field);
    }

    let compareBtn = d.querySelector('[data-az-tab="compare"]');
    if (!compareBtn) {
      compareBtn = d.createElement('button');
      compareBtn.className = 'btn';
      compareBtn.dataset.azTab = 'compare';
      compareBtn.textContent = 'Сравнение 2 врачей';
      const doctorBtn = d.querySelector('[data-tab="doctor"]');
      doctorBtn ? doctorBtn.insertAdjacentElement('afterend', compareBtn) : tabs.appendChild(compareBtn);
    }

    let financeHost = d.getElementById('azFinanceHost');
    if (!financeHost) {
      financeHost = d.createElement('div');
      financeHost.id = 'azFinanceHost';
      content.insertAdjacentElement('afterend', financeHost);
    }

    compareBtn.addEventListener('click', () => {
      compareActive = true;
      d.querySelectorAll('.tabs .btn').forEach(b => b.classList.remove('active'));
      compareBtn.classList.add('active');
      syncControls(w, d);
      renderCompare(w, d);
    });

    d.querySelectorAll('[data-tab]').forEach(btn => {
      btn.addEventListener('click', () => {
        compareActive = false;
        setTimeout(() => {
          syncControls(w, d);
          renderFinanceIfNeeded(w, d);
        }, 0);
      });
    });

    ['direction','grouping','focusPeriod','doctor','category'].forEach(id => {
      const el = d.getElementById(id);
      if (!el) return;
      el.addEventListener('change', () => {
        setTimeout(() => {
          syncControls(w, d);
          if (compareActive) renderCompare(w, d);
          else renderFinanceIfNeeded(w, d);
        }, 0);
      });
    });

    const doctor2 = d.getElementById('azDoctor2');
    doctor2.addEventListener('change', () => compareActive && renderCompare(w, d));

    if (observer) observer.disconnect();
    observer = new MutationObserver(() => {
      setTimeout(() => {
        syncControls(w, d);
        if (compareActive) renderCompare(w, d);
        else renderFinanceIfNeeded(w, d);
      }, 0);
    });
    observer.observe(content, { childList: true, subtree: false });

    syncControls(w, d);
    renderFinanceIfNeeded(w, d);
  }

  function getData(w) {
    try { return JSON.parse(w.localStorage.getItem('az-service-analytics-v1') || 'null'); }
    catch { return null; }
  }

  function loadSalary(w) {
    try { return JSON.parse(w.localStorage.getItem(SALARY_KEY) || '{}'); }
    catch { return {}; }
  }

  function saveSalaryStore(w, store) {
    w.localStorage.setItem(SALARY_KEY, JSON.stringify(store));
  }

  function getSalaryArray(w, direction, doctor, monthsCount) {
    const store = loadSalary(w);
    const arr = store?.[direction]?.[doctor];
    const out = Array.isArray(arr) ? arr.slice(0, monthsCount) : [];
    while (out.length < monthsCount) out.push(null);
    return out;
  }

  function setSalary(w, direction, doctor, monthIndex, value, monthsCount) {
    const store = loadSalary(w);
    store[direction] ||= {};
    const arr = getSalaryArray(w, direction, doctor, monthsCount);
    arr[monthIndex] = value === '' || value == null || Number.isNaN(+value) ? null : +value;
    store[direction][doctor] = arr;
    saveSalaryStore(w, store);
  }

  function currentBaseTab(d) {
    const active = d.querySelector('.tabs [data-tab].active');
    return active?.dataset.tab || 'summary';
  }

  function syncControls(w, d) {
    const data = getData(w);
    const direction = d.getElementById('direction')?.value;
    const dir = data?.directions?.[direction];
    const doctor = d.getElementById('doctor');
    const doctor2 = d.getElementById('azDoctor2');
    const doctor2Field = d.getElementById('azDoctor2Field');
    const financeHost = d.getElementById('azFinanceHost');
    if (!dir || !doctor || !doctor2 || !doctor2Field) return;

    const names = Object.keys(dir.doctors || {});
    const old2 = doctor2.value;
    doctor2.innerHTML = names.map(n => `<option>${escapeHtml(n)}</option>`).join('');
    if (names.includes(old2)) doctor2.value = old2;

    if (compareActive) {
      doctor.disabled = false;
      doctor2Field.classList.remove('az-hidden');
      if (doctor.value === 'all' || !names.includes(doctor.value)) doctor.value = names[0] || '';
      if (!names.includes(doctor2.value) || doctor2.value === doctor.value) {
        doctor2.value = names.find(n => n !== doctor.value) || doctor.value || '';
      }
      if (financeHost) financeHost.innerHTML = '';
      return;
    }

    doctor2Field.classList.add('az-hidden');
    const tab = currentBaseTab(d);
    if (tab === 'summary' || tab === 'matrix') {
      if ([...doctor.options].some(o => o.value === 'all')) doctor.value = 'all';
      doctor.disabled = true;
    } else {
      doctor.disabled = false;
      if (doctor.value === 'all' || !names.includes(doctor.value)) doctor.value = names[0] || '';
    }
  }

  function renderFinanceIfNeeded(w, d) {
    if (compareActive) return;
    const host = d.getElementById('azFinanceHost');
    if (!host) return;
    if (currentBaseTab(d) !== 'doctor') {
      host.innerHTML = '';
      return;
    }
    renderFinance(w, d);
  }

  function availableGroups(data, grouping) {
    const n = data?.months?.length || 0;
    let defs = [];
    if (grouping === 'month') defs = (data.months || []).map((label, i) => ({ id:'m'+i, label, idx:[i], expected:1 }));
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
    return defs.map(g => ({...g, idx:g.idx.filter(i => i < n)}))
      .filter(g => g.idx.length)
      .map(g => ({...g, label:g.idx.length < g.expected ? g.label+' (неполный)' : g.label}));
  }

  function doctorRevenueForIndexes(dir, doctor, indexes) {
    const doc = dir?.doctors?.[doctor] || {};
    let revenue = 0;
    Object.values(doc).forEach(arr => {
      if (!Array.isArray(arr)) return;
      indexes.forEach(i => revenue += +(arr[i]?.[1] || 0));
    });
    return revenue;
  }

  function salaryForIndexes(arr, indexes) {
    const vals = indexes.map(i => arr[i]);
    const complete = vals.every(v => v !== null && v !== '' && v !== undefined && Number.isFinite(+v));
    if (!complete) return { complete:false, value:null };
    return { complete:true, value:vals.reduce((a,v) => a + (+v || 0), 0) };
  }

  function renderFinance(w, d) {
    const data = getData(w);
    const direction = d.getElementById('direction')?.value;
    const doctor = d.getElementById('doctor')?.value;
    const dir = data?.directions?.[direction];
    const host = d.getElementById('azFinanceHost');
    if (!data || !dir || !doctor || doctor === 'all' || !host) return;

    const months = data.months || [];
    const salaries = getSalaryArray(w, direction, doctor, months.length);
    const grouping = d.getElementById('grouping')?.value || 'month';
    const groups = availableGroups(data, grouping);
    const focusId = d.getElementById('focusPeriod')?.value || 'all';

    let html = `<div class="panel ${panelClass(direction)} az-finance">
      <h2>Экономика врача — ${escapeHtml(doctor)}</h2>
      <div class="az-finance-note">Зарплата вводится вручную по месяцам и сохраняется только в этом браузере. Расчёты повторяют логику таблицы: ЗП / выручка, прибыль после ЗП и рентабельность после ЗП.</div>
      <div class="table-wrap"><table class="table"><thead><tr><th>Показатель</th>${months.map(m=>`<th>${escapeHtml(m)}</th>`).join('')}</tr></thead><tbody>
      <tr><td>Зарплата</td>${months.map((m,i)=>`<td><input class="az-salary-input" data-mi="${i}" inputmode="decimal" value="${salaries[i] == null ? '' : salaries[i]}"></td>`).join('')}</tr>
      </tbody></table></div>
    </div>`;

    html += `<div class="panel ${panelClass(direction)} az-finance">
      <h2>Прибыльность и рентабельность</h2>
      <div class="table-wrap"><table class="table"><thead><tr><th>Показатель</th>${groups.map(g=>`<th class="${focusId===g.id?'focus':''}">${escapeHtml(g.label)}</th>`).join('')}<th class="total-col">Итого</th></tr></thead><tbody>`;

    const allIdx = months.map((_,i)=>i);
    html += metricRow('Выручка врача', groups, g => doctorRevenueForIndexes(dir, doctor, g.idx), doctorRevenueForIndexes(dir, doctor, allIdx), money);
    html += metricRowNullable('Зарплата', groups, g => salaryForIndexes(salaries, g.idx), salaryForIndexes(salaries, allIdx), money);
    html += ratioRow('ЗП / выручка врача, %', groups, g => {
      const r = doctorRevenueForIndexes(dir, doctor, g.idx), s = salaryForIndexes(salaries, g.idx);
      return s.complete && r ? s.value/r : null;
    }, (() => {
      const r = doctorRevenueForIndexes(dir, doctor, allIdx), s = salaryForIndexes(salaries, allIdx);
      return s.complete && r ? s.value/r : null;
    })());
    html += metricRowNullable('Прибыль после ЗП', groups, g => {
      const r = doctorRevenueForIndexes(dir, doctor, g.idx), s = salaryForIndexes(salaries, g.idx);
      return s.complete ? {complete:true,value:r-s.value} : {complete:false,value:null};
    }, (() => {
      const r = doctorRevenueForIndexes(dir, doctor, allIdx), s = salaryForIndexes(salaries, allIdx);
      return s.complete ? {complete:true,value:r-s.value} : {complete:false,value:null};
    })(), money, true);
    html += ratioRow('Рентабельность после ЗП, %', groups, g => {
      const r = doctorRevenueForIndexes(dir, doctor, g.idx), s = salaryForIndexes(salaries, g.idx);
      return s.complete && r ? (r-s.value)/r : null;
    }, (() => {
      const r = doctorRevenueForIndexes(dir, doctor, allIdx), s = salaryForIndexes(salaries, allIdx);
      return s.complete && r ? (r-s.value)/r : null;
    })(), true);

    html += `</tbody></table></div></div>`;
    host.innerHTML = html;

    host.querySelectorAll('.az-salary-input').forEach(input => {
      input.addEventListener('change', () => {
        const mi = +input.dataset.mi;
        const raw = input.value.replace(/\s/g,'').replace(',','.');
        setSalary(w, direction, doctor, mi, raw, months.length);
        renderFinance(w, d);
        renderDoctorFinanceKpis(w, d);
      });
    });

    renderDoctorFinanceKpis(w, d);
  }

  function renderDoctorFinanceKpis(w, d) {
    const data = getData(w);
    const direction = d.getElementById('direction')?.value;
    const doctor = d.getElementById('doctor')?.value;
    const dir = data?.directions?.[direction];
    const kpis = d.getElementById('kpis');
    if (!data || !dir || !doctor || doctor === 'all' || !kpis || currentBaseTab(d) !== 'doctor') return;

    const months = data.months || [];
    const salaries = getSalaryArray(w, direction, doctor, months.length);
    const focusId = d.getElementById('focusPeriod')?.value || 'all';
    const groups = availableGroups(data, d.getElementById('grouping')?.value || 'month');
    const focus = groups.find(g => g.id === focusId);
    const idx = focus ? focus.idx : months.map((_,i)=>i);
    const revenue = doctorRevenueForIndexes(dir, doctor, idx);
    const salary = salaryForIndexes(salaries, idx);
    const profit = salary.complete ? revenue - salary.value : null;
    const margin = salary.complete && revenue ? profit/revenue : null;
    kpis.innerHTML = `
      <div class="kpi"><span>Выручка врача</span><strong>${money(revenue)}</strong></div>
      <div class="kpi"><span>Зарплата</span><strong>${salary.complete ? money(salary.value) : '—'}</strong></div>
      <div class="kpi"><span>Прибыль после ЗП</span><strong class="${profit == null ? '' : profit >= 0 ? 'good' : 'bad'}">${profit == null ? '—' : money(profit)}</strong></div>
      <div class="kpi"><span>Рентабельность после ЗП</span><strong class="${margin == null ? '' : margin >= 0 ? 'good' : 'bad'}">${margin == null ? '—' : percent(margin)}</strong></div>`;
  }

  function renderCompare(w, d) {
    const data = getData(w);
    const direction = d.getElementById('direction')?.value;
    const dir = data?.directions?.[direction];
    const content = d.getElementById('content');
    const host = d.getElementById('azFinanceHost');
    const doctor1 = d.getElementById('doctor')?.value;
    const doctor2 = d.getElementById('azDoctor2')?.value;
    if (!data || !dir || !content || !doctor1 || !doctor2) return;
    if (host) host.innerHTML = '';

    const categories = d.getElementById('category')?.value === 'all'
      ? dir.categories
      : dir.categories.filter(c => c === d.getElementById('category')?.value);
    const grouping = d.getElementById('grouping')?.value || 'month';
    const groups = availableGroups(data, grouping);
    const focusId = d.getElementById('focusPeriod')?.value || 'all';
    const focus = groups.find(g => g.id === focusId);
    const focusIdx = focus ? focus.idx : (data.months || []).map((_,i)=>i);
    const sal1 = getSalaryArray(w, direction, doctor1, data.months.length);
    const sal2 = getSalaryArray(w, direction, doctor2, data.months.length);

    const econ1 = economicsFor(dir, doctor1, sal1, focusIdx);
    const econ2 = economicsFor(dir, doctor2, sal2, focusIdx);
    const kpis = d.getElementById('kpis');
    if (kpis) {
      kpis.innerHTML = `
        <div class="kpi"><span>Прибыль • ${escapeHtml(shortName(doctor1))}</span><strong>${econ1.profit==null?'—':money(econ1.profit)}</strong></div>
        <div class="kpi"><span>Рентабельность • ${escapeHtml(shortName(doctor1))}</span><strong>${econ1.margin==null?'—':percent(econ1.margin)}</strong></div>
        <div class="kpi"><span>Прибыль • ${escapeHtml(shortName(doctor2))}</span><strong>${econ2.profit==null?'—':money(econ2.profit)}</strong></div>
        <div class="kpi"><span>Рентабельность • ${escapeHtml(shortName(doctor2))}</span><strong>${econ2.margin==null?'—':percent(econ2.margin)}</strong></div>`;
    }

    let html = `<div class="panel ${panelClass(direction)}"><h2>Экономика двух врачей ${focus ? '— '+escapeHtml(focus.label) : '— весь период'}</h2>
      <div class="az-finance-note">Прибыль и рентабельность считаются только если зарплата заполнена за все месяцы выбранного периода.</div>
      <div class="table-wrap"><table class="table az-compare-table"><thead><tr><th>Врач</th><th>Выручка</th><th>Зарплата</th><th>ЗП / выручка</th><th>Прибыль после ЗП</th><th>Рентабельность после ЗП</th></tr></thead><tbody>
      ${econRow(doctor1,econ1)}
      ${econRow(doctor2,econ2)}
      </tbody></table></div></div>`;

    html += `<div class="panel ${panelClass(direction)}"><h2>Сравнение услуг — ${escapeHtml(shortName(doctor1))} / ${escapeHtml(shortName(doctor2))}</h2>
      <div class="table-wrap"><table class="table az-compare-table"><thead><tr><th>Категория</th><th class="az-compare-head" colspan="2">${escapeHtml(shortName(doctor1))}</th><th class="az-compare-head" colspan="2">${escapeHtml(shortName(doctor2))}</th><th>Разница суммы</th></tr><tr><th></th><th>Кол.</th><th>Сумма</th><th>Кол.</th><th>Сумма</th><th></th></tr></thead><tbody>`;

    categories.forEach(cat => {
      const a = metricForIndexes(dir.doctors[doctor1]?.[cat], focusIdx);
      const b = metricForIndexes(dir.doctors[doctor2]?.[cat], focusIdx);
      const delta = b[1] - a[1];
      html += `<tr><td>${escapeHtml(cat)}</td><td>${number(a[0])}</td><td class="money">${money(a[1])}</td><td>${number(b[0])}</td><td class="money">${money(b[1])}</td><td class="${delta>0?'az-pos':delta<0?'az-neg':'az-neutral'}">${signedMoney(delta)}</td></tr>`;
    });
    html += `</tbody></table></div></div>`;

    html += `<div class="panel ${panelClass(direction)}"><h2>Динамика двух врачей</h2><div class="table-wrap"><table class="table az-compare-table"><thead><tr><th>Период</th><th>${escapeHtml(shortName(doctor1))} • Выручка</th><th>${escapeHtml(shortName(doctor1))} • Прибыль</th><th>${escapeHtml(shortName(doctor2))} • Выручка</th><th>${escapeHtml(shortName(doctor2))} • Прибыль</th></tr></thead><tbody>`;
    groups.forEach(g => {
      const a = economicsFor(dir, doctor1, sal1, g.idx);
      const b = economicsFor(dir, doctor2, sal2, g.idx);
      html += `<tr><td>${escapeHtml(g.label)}</td><td class="money">${money(a.revenue)}</td><td>${a.profit==null?'—':money(a.profit)}</td><td class="money">${money(b.revenue)}</td><td>${b.profit==null?'—':money(b.profit)}</td></tr>`;
    });
    html += `</tbody></table></div></div>`;
    content.innerHTML = html;
  }

  function economicsFor(dir, doctor, salaries, idx) {
    const revenue = doctorRevenueForIndexes(dir, doctor, idx);
    const salary = salaryForIndexes(salaries, idx);
    const profit = salary.complete ? revenue - salary.value : null;
    const salaryRate = salary.complete && revenue ? salary.value/revenue : null;
    const margin = salary.complete && revenue ? profit/revenue : null;
    return { revenue, salary:salary.complete ? salary.value : null, salaryRate, profit, margin };
  }

  function econRow(name, e) {
    return `<tr><td>${escapeHtml(name)}</td><td class="money">${money(e.revenue)}</td><td>${e.salary==null?'—':money(e.salary)}</td><td>${e.salaryRate==null?'—':percent(e.salaryRate)}</td><td>${e.profit==null?'—':money(e.profit)}</td><td>${e.margin==null?'—':percent(e.margin)}</td></tr>`;
  }

  function metricForIndexes(arr, indexes) {
    return indexes.reduce((a,i) => {
      const x = Array.isArray(arr) ? (arr[i] || [0,0]) : [0,0];
      a[0] += +x[0] || 0; a[1] += +x[1] || 0; return a;
    }, [0,0]);
  }

  function metricRow(label, groups, fn, total, formatter) {
    return `<tr><td>${escapeHtml(label)}</td>${groups.map(g=>`<td class="readonly">${formatter(fn(g))}</td>`).join('')}<td class="readonly total-col">${formatter(total)}</td></tr>`;
  }

  function metricRowNullable(label, groups, fn, totalObj, formatter, profitClass=false) {
    const cell = obj => obj?.complete
      ? `<span class="${profitClass ? (obj.value>=0?'good':'bad') : ''}">${formatter(obj.value)}</span>`
      : '—';
    return `<tr><td>${escapeHtml(label)}</td>${groups.map(g=>`<td>${cell(fn(g))}</td>`).join('')}<td class="total-col">${cell(totalObj)}</td></tr>`;
  }

  function ratioRow(label, groups, fn, total, color=false) {
    const cell = v => v == null ? '—' : `<span class="${color?(v>=0?'good':'bad'):''}">${percent(v)}</span>`;
    return `<tr><td>${escapeHtml(label)}</td>${groups.map(g=>`<td>${cell(fn(g))}</td>`).join('')}<td class="total-col">${cell(total)}</td></tr>`;
  }

  function panelClass(direction) {
    return direction === 'Стоматология' ? 'dent' : direction === 'Клиника' ? 'clinic' : 'lab';
  }

  function shortName(name) {
    const p = String(name||'').trim().split(/\s+/);
    return p.length >= 3 ? `${p[0]} ${p[1][0]}. ${p[2][0]}.` : name;
  }

  function number(n) {
    return new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(n||0);
  }
  function money(n) {
    return new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(n||0)+' ₽';
  }
  function signedMoney(n) {
    const s = n>0?'+':n<0?'−':'';
    return s + money(Math.abs(n));
  }
  function percent(n) {
    return new Intl.NumberFormat('ru-RU',{style:'percent',maximumFractionDigits:1}).format(n);
  }
  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
})();
