(() => {
  const frame = document.getElementById('serviceFrame');
  if (!frame) return;

  const DATA_KEY = 'az-service-analytics-v1';
  const COST_KEY = 'az-clinic-primary-cost-v1';
  const IMPORT_KEY = 'az-primary-cost-import-key';
  const SALARY_KEY = 'az-service-salary-v1';
  const EXTRA_KEY = 'az-service-extra-payments-v1';
  const LAB_DIRECTION = 'Лаборатория';
  const MEDICAL_DIRECTIONS = ['Стоматология', 'Клиника'];
  const META_URL = './services-primary-cost.meta.json?v=20260904-2';

  let observer = null;
  let scheduled = false;
  let PAID_PATIENTS_MONTHLY = {};
  const importPromise = importSecureCost();
  const paidPatientsPromise = loadPaidPatients();

  frame.addEventListener('load', async () => {
    await Promise.all([importPromise, paidPatientsPromise]);
    const w = frame.contentWindow;
    const d = frame.contentDocument;
    if (!w || !d) return;

    installStyle(d);

    const schedule = (delay = 60) => {
      if (scheduled) return;
      scheduled = true;
      setTimeout(() => {
        scheduled = false;
        refresh(w, d);
      }, delay);
    };

    if (observer) observer.disconnect();
    const content = d.getElementById('content');
    if (content) {
      observer = new MutationObserver(() => {
        const direction = d.getElementById('direction')?.value;
        const relevantTab = !!d.querySelector('[data-tab="summary"].active,[data-tab="doctor"].active');
        if (direction && direction !== LAB_DIRECTION && relevantTab &&
            !d.querySelector('#content tr.az-full-cost-econ')) {
          schedule(80);
        }
      });
      observer.observe(content, { childList: true, subtree: true });
    }

    ['direction','grouping','focusPeriod','doctor','category'].forEach(id => {
      d.getElementById(id)?.addEventListener('change', () => schedule(90));
    });
    d.querySelectorAll('[data-tab],[data-az-tab]').forEach(btn => {
      btn.addEventListener('click', () => schedule(110));
    });
    d.addEventListener('click', e => {
      if (e.target.closest('#azPayrollSave') || e.target.closest('#azPaySave')) {
        schedule(170);
      }
    });

    schedule(100);
  });

  async function loadPaidPatients() {
    try {
      const response = await fetch('/api/reports/data', { credentials:'same-origin', cache:'no-store' });
      if (!response.ok) throw Error('paid patients');
      const payload = await response.json();
      PAID_PATIENTS_MONTHLY = payload?.paidPatientsMonthly || {};
      return true;
    } catch (e) {
      console.error('Paid-patient load failed', e);
      PAID_PATIENTS_MONTHLY = {};
      return false;
    }
  }

  async function importSecureCost() {
    const keyText = sessionStorage.getItem(IMPORT_KEY);
    if (!keyText) return false;

    try {
      const meta = await fetch(META_URL, { cache:'no-store' }).then(r => {
        if (!r.ok) throw Error('meta');
        return r.json();
      });
      const cipherText = await fetch(meta.part + '?v=20260904-2', { cache:'no-store' }).then(r => {
        if (!r.ok) throw Error('part');
        return r.text();
      });
      const cleanCipher = cipherText.trim();

      if (meta.ciphertextSha256 && await sha256(cleanCipher) !== meta.ciphertextSha256) {
        throw Error('cipher hash');
      }

      const key = await crypto.subtle.importKey(
        'raw',
        b64u(keyText),
        { name:'AES-GCM' },
        false,
        ['decrypt']
      );
      const compressed = new Uint8Array(await crypto.subtle.decrypt({
        name:'AES-GCM',
        iv:b64u(meta.iv),
        additionalData:new TextEncoder().encode(meta.aad),
        tagLength:128
      }, key, b64u(cleanCipher)));

      if (meta.compressedSha256 && await sha256Bytes(compressed) !== meta.compressedSha256) {
        throw Error('compressed hash');
      }

      const ds = new DecompressionStream('gzip');
      const plain = new Uint8Array(
        await new Response(new Blob([compressed]).stream().pipeThrough(ds)).arrayBuffer()
      );
      if (meta.plaintextSha256 && await sha256Bytes(plain) !== meta.plaintextSha256) {
        throw Error('plain hash');
      }

      const parsed = JSON.parse(new TextDecoder().decode(plain));
      localStorage.setItem(COST_KEY, JSON.stringify(parsed));
      sessionStorage.removeItem(IMPORT_KEY);
      return true;
    } catch (e) {
      console.error('Clinic-cost import failed', e);
      return false;
    }
  }

  function installStyle(d) {
    if (d.getElementById('azFullCostEconStyle')) return;
    const s = d.createElement('style');
    s.id = 'azFullCostEconStyle';
    s.textContent = `
      .table tr.az-full-cost-econ td{background:#f3eee4!important;font-weight:650}
      .table tr.az-full-cost-econ td:first-child{background:#f3eee4!important}
      .table tr.az-full-cost-start td{border-top:2px solid #a98e61!important}
      .table tr.az-full-cost-load td{background:#f5ead8!important;font-weight:700}
      .table tr.az-full-cost-load td:first-child{background:#f5ead8!important}
      .table tr.az-full-cost-result td{background:#eef2e9!important;font-weight:700}
      .table tr.az-full-cost-result td:first-child{background:#eef2e9!important}
      .table tr.az-full-cost-margin td{background:#e3eee5!important;font-weight:750}
      .table tr.az-full-cost-margin td:first-child{background:#e3eee5!important}
      .az-full-cost-dash{color:#aaa!important;font-weight:400!important}
      .az-full-cost-good{color:#32724b!important}
      .az-full-cost-bad{color:#9a4b48!important}
      .az-full-cost-note{display:block;margin-top:2px;font-size:8.5px;line-height:1.2;color:#7c7162;font-weight:500}
      #azClinicEconomicsHost{margin-top:10px}
      #azClinicEconomicsHost .panel h2{background:#dce8df}
      #azClinicEconomicsHost .panel-note{line-height:1.35}
      #azClinicEconomicsHost .table td:first-child{min-width:285px!important}
    `;
    d.head.appendChild(s);
  }

  function refresh(w, d) {
    d.querySelectorAll('#content tr.az-full-cost-econ').forEach(r => r.remove());
    const clinicHost = ensureClinicHost(d);
    clinicHost.innerHTML = '';

    const direction = d.getElementById('direction')?.value;
    const summaryTab = !!d.querySelector('[data-tab="summary"].active');
    const doctorTab = !!d.querySelector('[data-tab="doctor"].active');
    if (!direction || direction === LAB_DIRECTION || (!summaryTab && !doctorTab)) return;

    const serviceData = getJson(w.localStorage, DATA_KEY);
    const costData = getJson(w.localStorage, COST_KEY);
    const dir = serviceData?.directions?.[direction];
    if (!serviceData || !dir) return;

    if (!isCostDataReady(costData)) {
      appendStatus(d, 'Для расчёта расходной нагрузки откройте новую защищённую ссылку импорта.');
      return;
    }

    const groups = getGroups(w, serviceData, d);
    const focus = d.getElementById('focusPeriod')?.value || 'all';

    let doctors = [];
    let payerSeries = null;
    let payerLabel = '';

    if (doctorTab) {
      const doctor = d.getElementById('doctor')?.value;
      if (!doctor || doctor === 'all' || !dir.doctors?.[doctor]) return;
      doctors = [doctor];
      payerSeries = paidPatientSeries(costData, direction, doctor);
      payerLabel = 'Оплатившие пациенты врача';
    } else {
      doctors = Object.keys(dir.doctors || {});
      payerSeries = paidPatientSeries(costData, direction);
      payerLabel = 'Оплатившие пациенты направления';
    }

    if (!Array.isArray(payerSeries) || payerSeries.every(value => value == null)) {
      appendStatus(d, 'Нет полной исходной выгрузки для расчёта оплативших пациентов за этот период.');
      return;
    }

    injectScopeRows(w, d, serviceData, costData, direction, doctors, payerSeries, payerLabel, groups, focus);

    if (summaryTab) {
      renderWholeClinicPanel(w, d, serviceData, costData, groups, focus, clinicHost);
    }
  }

  function injectScopeRows(w, d, serviceData, costData, direction, doctors, payerSeries, payerLabel, groups, focus) {
    const tbody = d.querySelector('#content .table tbody');
    const totalRow = tbody?.querySelector('tr.total-row');
    if (!tbody || !totalRow) return;

    const priorRows = tbody.querySelectorAll(
      direction === LAB_DIRECTION ? 'tr.az-inline-econ' :
      (d.querySelector('[data-tab="doctor"].active') ? 'tr.az-inline-econ' : 'tr.az-summary-econ')
    );
    let anchor = priorRows.length ? priorRows[priorRows.length - 1] : totalRow;

    const metricFor = idx => scopeMetrics(w, serviceData, costData, direction, doctors, payerSeries, idx);
    const grand = metricFor(availableIndexes(costData, payerSeries));

    const defs = [
      { key:'paidPatients', label:payerLabel, type:'count', start:true,
        note:'Уникальные пациенты с фактической оплатой за месяц; повторные оплаты одного пациента не увеличивают количество.' },
      { key:'cost', label:'Затраты на оплатившего пациента', type:'money',
        note:'Общеклинические затраты месяца / уникальные оплатившие пациенты.' },
      { key:'load', label:'Расходная общеклиническая нагрузка', type:'money', load:true,
        note:'Оплатившие пациенты × затраты на оплатившего пациента.' },
      { key:'final', label:'Финансовый результат после выплат и расходной нагрузки', type:'money', color:true, result:true },
      { key:'margin', label:'Рентабельность после выплат и расходной нагрузки, %', type:'percent', color:true, margin:true }
    ];

    defs.forEach(def => {
      const row = d.createElement('tr');
      row.className = `az-full-cost-econ${def.start?' az-full-cost-start':''}${def.load?' az-full-cost-load':''}${def.result?' az-full-cost-result':''}${def.margin?' az-full-cost-margin':''}`;
      let html = `<td>${escapeHtml(def.label)}${def.note ? `<span class="az-full-cost-note">${escapeHtml(def.note)}</span>` : ''}</td>`;

      groups.forEach(g => {
        const m = metricFor(g.idx);
        html += metricPair(def, m?.[def.key] ?? null, focus === g.id);
      });

      html += metricPair(def, grand?.[def.key] ?? null, false, true);
      html += '<td class="az-full-cost-dash">—</td>';
      row.innerHTML = html;
      anchor.insertAdjacentElement('afterend', row);
      anchor = row;
    });
  }

  function renderWholeClinicPanel(w, d, serviceData, costData, groups, focus, host) {
    const allIdx = (serviceData.months || []).map((_, i) => i);
    const clinicPayerSeries = paidPatientSeries(costData, 'medicine');
    const costIdx = availableIndexes(costData, clinicPayerSeries);
    const allMetrics = idx => wholeClinicMetrics(w, serviceData, costData, idx);
    const grandAll = allMetrics(allIdx);
    const grandCost = allMetrics(costIdx);

    const defs = [
      { key:'paidPatients', label:'Оплатившие пациенты', type:'count', costBound:true },
      { key:'revenue', label:'Выручка медицинской части', type:'money' },
      { key:'salary', label:'Основная ЗП врачей', type:'money' },
      { key:'extras', label:'Прочие выплаты врачам', type:'money' },
      { key:'payouts', label:'Всего выплат врачам', type:'money', total:true },
      { key:'afterPayout', label:'Финансовый результат после выплат', type:'money', color:true },
      { key:'payoutMargin', label:'Маржинальность после выплат, %', type:'percent', color:true, margin:true },
      { key:'cost', label:'Затраты на оплатившего пациента', type:'money', costBound:true },
      { key:'load', label:'Расходная общеклиническая нагрузка', type:'money', costBound:true, load:true },
      { key:'final', label:'Финансовый результат после выплат и расходной нагрузки', type:'money', costBound:true, color:true, result:true },
      { key:'margin', label:'Рентабельность после выплат и расходной нагрузки, %', type:'percent', costBound:true, color:true, margin:true }
    ];

    let html = `<div class="panel">
      <h2>Экономика всей медицинской части клиники</h2>
      <div class="panel-note">Лаборатория исключена. Показатели после выплат показываются за все месяцы, имеющиеся в аналитике услуг. Расходная нагрузка и итоговая рентабельность считаются только за месяцы, где одновременно есть финмодель и полные данные об оплативших пациентах.</div>
      <div class="table-wrap"><table class="table"><thead><tr><th rowspan="2">Показатель</th>`;

    groups.forEach(g => {
      html += `<th class="period-head ${focus===g.id?'focus':''}" colspan="2">${escapeHtml(g.label)}</th>`;
    });
    html += '<th class="period-head total-col" colspan="2">Итого</th><th rowspan="2">Примечание</th></tr><tr>';
    groups.forEach(g => {
      html += `<th class="${focus===g.id?'focus':''}">Кол.</th><th class="${focus===g.id?'focus':''}">Сумма</th>`;
    });
    html += '<th class="total-col">Кол.</th><th class="total-col">Сумма</th></tr></thead><tbody>';

    defs.forEach((def, index) => {
      const classes = [
        'az-full-cost-econ',
        index === 0 ? 'az-full-cost-start' : '',
        def.total ? 'az-full-cost-load' : '',
        def.load ? 'az-full-cost-load' : '',
        def.result ? 'az-full-cost-result' : '',
        def.margin ? 'az-full-cost-margin' : ''
      ].filter(Boolean).join(' ');
      html += `<tr class="${classes}"><td>${escapeHtml(def.label)}</td>`;
      groups.forEach(g => {
        const m = allMetrics(g.idx);
        html += metricPair(def, m?.[def.key] ?? null, focus === g.id);
      });
      const grand = def.costBound ? grandCost : grandAll;
      html += metricPair(def, grand?.[def.key] ?? null, false, true);
      html += `<td class="small">${def.costBound ? 'январь–июль' : 'все доступные месяцы'}</td></tr>`;
    });

    html += '</tbody></table></div></div>';
    host.innerHTML = html;
  }

  function scopeMetrics(w, serviceData, costData, direction, doctors, payerSeries, idx) {
    if (!validCostIndexes(costData, payerSeries, idx)) return null;

    const paidPatients = sumIndexes(payerSeries, idx);
    const cost = weightedCost(costData, payerSeries, idx);
    const load = idx.reduce((sum, i) => sum + (+payerSeries[i] || 0) * monthlyCost(costData, payerSeries, i), 0);
    const revenue = revenueForDoctors(serviceData?.directions?.[direction], doctors, idx);
    const payouts = payoutsForDoctors(w, direction, doctors, serviceData.months.length, idx);
    const final = revenue - payouts.total - load;

    return {
      paidPatients,
      cost,
      load,
      final,
      margin: revenue ? final / revenue : null
    };
  }

  function wholeClinicMetrics(w, serviceData, costData, idx) {
    let revenue = 0;
    let salary = 0;
    let extras = 0;

    MEDICAL_DIRECTIONS.forEach(direction => {
      const dir = serviceData?.directions?.[direction];
      const doctors = Object.keys(dir?.doctors || {});
      revenue += revenueForDoctors(dir, doctors, idx);
      const pay = payoutsForDoctors(w, direction, doctors, serviceData.months.length, idx);
      salary += pay.salary;
      extras += pay.extras;
    });

    const payouts = salary + extras;
    const afterPayout = revenue - payouts;
    const out = {
      revenue,
      salary,
      extras,
      payouts,
      afterPayout,
      payoutMargin: revenue ? afterPayout / revenue : null,
      paidPatients:null,
      cost:null,
      load:null,
      final:null,
      margin:null
    };

    const payerSeries = paidPatientSeries(costData, 'medicine');
    if (!validCostIndexes(costData, payerSeries, idx)) return out;

    const paidPatients = sumIndexes(payerSeries, idx);
    const cost = weightedCost(costData, payerSeries, idx);
    const load = idx.reduce((sum, i) => sum + (+payerSeries[i] || 0) * monthlyCost(costData, payerSeries, i), 0);
    const final = revenue - payouts - load;

    out.paidPatients = paidPatients;
    out.cost = cost;
    out.load = load;
    out.final = final;
    out.margin = revenue ? final / revenue : null;
    return out;
  }

  function metricPair(def, value, focus, totalCol = false) {
    const qClasses = ['az-full-cost-dash'];
    const vClasses = [];
    if (focus) {
      qClasses.push('focus');
      vClasses.push('focus');
    }
    if (totalCol) {
      qClasses.push('total-col');
      vClasses.push('total-col');
    }
    if (def.color && value != null) {
      vClasses.push(value >= 0 ? 'az-full-cost-good' : 'az-full-cost-bad');
    }

    if (def.type === 'count') {
      return `<td class="${vClasses.join(' ')}">${value == null ? '—' : number(value)}</td><td class="${qClasses.join(' ')}">—</td>`;
    }
    const formatter = def.type === 'percent' ? percent : money;
    return `<td class="${qClasses.join(' ')}">—</td><td class="${vClasses.join(' ')}">${value == null ? '—' : formatter(value)}</td>`;
  }

  function revenueForDoctors(dir, doctors, idx) {
    let total = 0;
    doctors.forEach(doctor => {
      Object.values(dir?.doctors?.[doctor] || {}).forEach(arr => {
        if (!Array.isArray(arr)) return;
        idx.forEach(i => total += +(arr[i]?.[1] || 0));
      });
    });
    return total;
  }

  function payoutsForDoctors(w, direction, doctors, monthCount, idx) {
    const salaryStore = getJson(w.localStorage, SALARY_KEY) || {};
    const extraStore = getJson(w.localStorage, EXTRA_KEY) || {};
    let salary = 0;
    let extras = 0;

    doctors.forEach(doctor => {
      const salaryArr = normalizeArray(salaryStore?.[direction]?.[doctor], monthCount, 0);
      const extraArr = normalizeArray(extraStore?.[direction]?.[doctor], monthCount, 0);
      idx.forEach(i => {
        salary += Number.isFinite(+salaryArr[i]) ? (+salaryArr[i] || 0) : 0;
        extras += Number.isFinite(+extraArr[i]) ? (+extraArr[i] || 0) : 0;
      });
    });
    return { salary, extras, total:salary + extras };
  }

  function costMonthKey(costData, i) {
    const raw = String(costData?.months?.[i] || '').trim();
    if (/^\d{4}-\d{2}$/.test(raw)) return raw;
    const year = Number(costData?.year || 2026);
    return year + '-' + String(i + 1).padStart(2, '0');
  }

  function normalizedDoctorKey(value) {
    return normalizeName(String(value || '').trim().split(/\s+/)[0]);
  }

  function paidPatientSeries(costData, direction, doctor = '') {
    const n = Array.isArray(costData?.months) ? costData.months.length : 0;
    const doctorKey = normalizedDoctorKey(doctor);
    return Array.from({length:n}, (_, i) => {
      const month = PAID_PATIENTS_MONTHLY[costMonthKey(costData, i)];
      if (!month) return null;
      if (direction === 'medicine') return Number.isFinite(+month.medicine) ? +month.medicine : null;
      if (direction === 'Стоматология') {
        if (!doctor) return Number.isFinite(+month.dent) ? +month.dent : null;
        const entries = month.dentists || {};
        const key = Object.keys(entries).find(name => normalizedDoctorKey(name) === doctorKey);
        return key && Number.isFinite(+entries[key]) ? +entries[key] : 0;
      }
      if (direction === 'Клиника') {
        if (!doctor) return Number.isFinite(+month.structure) ? +month.structure : null;
        const entries = month.clinicDocs || {};
        const key = Object.keys(entries).find(name => normalizedDoctorKey(name) === doctorKey);
        return key && Number.isFinite(+entries[key]) ? +entries[key] : 0;
      }
      return null;
    });
  }

  function isCostDataReady(data) {
    return data?.version >= 2 &&
      Array.isArray(data.months) &&
      Array.isArray(data.numerator);
  }

  function validCostIndexes(costData, payerSeries, idx) {
    const max = Number(costData?.availableThroughMonthIndex);
    return Array.isArray(idx) && idx.length > 0 && Array.isArray(payerSeries) &&
      idx.every(i =>
        Number.isInteger(i) &&
        i >= 0 &&
        i <= max &&
        costData.numerator?.[i] != null &&
        payerSeries?.[i] != null
      );
  }

  function availableIndexes(costData, payerSeries) {
    const max = Math.min(
      Number(costData?.availableThroughMonthIndex),
      (costData?.numerator?.length || 0) - 1,
      (payerSeries?.length || 0) - 1
    );
    if (!Number.isFinite(max) || max < 0) return [];
    return Array.from({ length:max + 1 }, (_, i) => i)
      .filter(i => payerSeries?.[i] != null);
  }

  function monthlyCost(costData, payerSeries, i) {
    const payers = +payerSeries?.[i] || 0;
    return payers ? (+costData.numerator?.[i] || 0) / payers : 0;
  }

  function weightedCost(costData, payerSeries, idx) {
    const numerator = sumIndexes(costData.numerator || [], idx);
    const payers = sumIndexes(payerSeries || [], idx);
    return payers ? numerator / payers : null;
  }

  function sumIndexes(arr, idx) {
    return idx.reduce((sum, i) => sum + (+arr?.[i] || 0), 0);
  }

  function getGroups(w, data, d) {
    try {
      if (typeof w.availableGroups === 'function') return w.availableGroups();
    } catch {}

    const n = data?.months?.length || 0;
    const grouping = d.getElementById('grouping')?.value || 'month';
    let defs = [];
    if (grouping === 'month') {
      defs = (data.months || []).map((label,i) => ({id:'m'+i,label,idx:[i],expected:1}));
    }
    if (grouping === 'quarter') {
      defs = [
        {id:'q1',label:'I квартал',idx:[0,1,2],expected:3},
        {id:'q2',label:'II квартал',idx:[3,4,5],expected:3},
        {id:'q3',label:'III квартал',idx:[6,7,8],expected:3},
        {id:'q4',label:'IV квартал',idx:[9,10,11],expected:3}
      ];
    }
    if (grouping === 'half') {
      defs = [
        {id:'h1',label:'I полугодие',idx:[0,1,2,3,4,5],expected:6},
        {id:'h2',label:'II полугодие',idx:[6,7,8,9,10,11],expected:6}
      ];
    }
    return defs
      .map(g => ({...g, idx:g.idx.filter(i => i < n)}))
      .filter(g => g.idx.length)
      .map(g => ({...g, label:g.idx.length < g.expected ? g.label + ' (неполный)' : g.label}));
  }

  function ensureClinicHost(d) {
    let host = d.getElementById('azClinicEconomicsHost');
    if (host) return host;
    host = d.createElement('div');
    host.id = 'azClinicEconomicsHost';
    const anchor = d.getElementById('azFinanceHost') || d.getElementById('content');
    anchor?.insertAdjacentElement('afterend', host);
    return host;
  }

  function appendStatus(d, message) {
    const status = d.getElementById('status');
    if (!status || status.textContent.includes(message)) return;
    status.textContent = `${status.textContent ? status.textContent + ' ' : ''}${message}`;
  }

  function normalizeArray(arr, n, fill) {
    const out = Array.isArray(arr) ? arr.slice(0, n) : [];
    while (out.length < n) out.push(fill);
    return out;
  }

  function normalizeName(value) {
    return String(value || '').trim().toLocaleLowerCase('ru-RU').replace(/ё/g, 'е');
  }

  function getJson(storage, key) {
    try { return JSON.parse(storage.getItem(key) || 'null'); }
    catch { return null; }
  }

  function b64u(s) {
    s = String(s || '').replace(/-/g,'+').replace(/_/g,'/');
    while (s.length % 4) s += '=';
    return Uint8Array.from(atob(s), c => c.charCodeAt(0));
  }

  async function sha256(text) {
    return sha256Bytes(new TextEncoder().encode(text));
  }

  async function sha256Bytes(bytes) {
    const hash = await crypto.subtle.digest('SHA-256', bytes);
    return [...new Uint8Array(hash)].map(x => x.toString(16).padStart(2,'0')).join('');
  }

  function number(n) {
    return new Intl.NumberFormat('ru-RU', { maximumFractionDigits:0 }).format(n || 0);
  }

  function money(n) {
    return new Intl.NumberFormat('ru-RU', { maximumFractionDigits:0 }).format(n || 0) + ' ₽';
  }

  function percent(n) {
    return new Intl.NumberFormat('ru-RU', { style:'percent', maximumFractionDigits:1 }).format(n);
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }
})();