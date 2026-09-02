(() => {
  const frame = document.getElementById('serviceFrame');
  if (!frame) return;

  const DATA_KEY = 'az-service-analytics-v1';
  const SALARY_KEY = 'az-service-salary-v1';
  const EXTRA_KEY = 'az-service-extra-payments-v1';
  const LAB_DIRECTION = 'Лаборатория';
  const LAB_TOTAL_KEY = '__LAB_TOTAL__';
  let frameObserver = null;
  let draft = null;
  let state = { direction: '', month: 0, highlightDoctor: '' };

  frame.addEventListener('load', () => {
    const w = frame.contentWindow;
    const d = frame.contentDocument;
    if (!w || !d) return;
    installStyles(d);
    installManager(w, d);
    if (frameObserver) frameObserver.disconnect();
    frameObserver = new MutationObserver(() => setTimeout(() => bindEntryPoints(w, d), 0));
    frameObserver.observe(d.body, { childList: true, subtree: true });
    bindEntryPoints(w, d);
  });

  function installStyles(d) {
    if (d.getElementById('azPayrollManagerStyle')) return;
    const s = d.createElement('style');
    s.id = 'azPayrollManagerStyle';
    s.textContent = `
      .az-payroll-topbtn{white-space:nowrap}
      .az-payroll-backdrop{position:fixed;inset:0;background:rgba(34,40,36,.50);display:grid;place-items:center;padding:16px;z-index:14000}
      .az-payroll-hide{display:none!important}
      .az-payroll-modal{width:min(980px,97vw);max-height:92vh;display:flex;flex-direction:column;background:#fffdf8;border:1px solid #d8cdbb;border-radius:16px;box-shadow:0 26px 80px rgba(34,32,27,.28);overflow:hidden}
      .az-payroll-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;padding:14px 17px 11px;border-bottom:1px solid #e6ded1}
      .az-payroll-head h3{margin:0;font:700 24px/1.05 'Cormorant Garamond',serif}.az-payroll-head small{display:block;margin-top:4px;color:#6d746c;font-size:10px}
      .az-payroll-x{border:0;background:transparent;color:#596158;font-size:25px;line-height:1;cursor:pointer}
      .az-payroll-controls{display:flex;align-items:end;gap:12px;flex-wrap:wrap;padding:10px 17px;border-bottom:1px solid #eee7dc;background:#fbf8f2}
      .az-payroll-controls .field{margin:0;min-width:210px}.az-payroll-controls select{padding:7px 9px!important;font-size:12px!important}
      .az-month-tabs{display:flex;gap:5px;flex-wrap:wrap;align-items:center}.az-month-tab{border:1px solid #cfc5b5;background:#fff;border-radius:8px;padding:7px 9px;font:600 11px Inter;cursor:pointer}.az-month-tab.active{background:#dfe9e2;border-color:#91aa98;color:#2f4a39}
      .az-payroll-body{padding:10px 17px 14px;overflow:auto}.az-payroll-note{margin:0 0 9px;font-size:10px;color:#6f6658}
      .az-payroll-table{width:100%;border-collapse:separate;border-spacing:0;min-width:650px}.az-payroll-table th,.az-payroll-table td{padding:7px 8px;border-bottom:1px solid #eee7dc;font-size:11px;text-align:right}.az-payroll-table th{position:sticky;top:0;background:#faf6ef;z-index:2}.az-payroll-table th:first-child,.az-payroll-table td:first-child{text-align:left;min-width:220px}.az-payroll-table input{width:125px;text-align:right;border:1px solid #d7cdbd;border-radius:7px;padding:6px 7px;font:600 11px Inter;background:#fff}.az-payroll-table tr.az-highlight td{background:#fff7df}.az-payroll-total{font-weight:700}.az-payroll-summary td{background:#faf6ef;font-weight:700;border-top:1px solid #dfd4c4}
      .az-payroll-labbox{padding:18px;border:1px solid #eadfc9;border-radius:12px;background:#fffaf0}.az-payroll-labbox h4{margin:0 0 7px;font:700 18px/1.1 'Cormorant Garamond',serif}.az-payroll-labline{display:grid;grid-template-columns:minmax(220px,1fr) 180px 160px;gap:12px;align-items:center;margin-top:12px}.az-payroll-labline input{width:100%;text-align:right;border:1px solid #d7cdbd;border-radius:8px;padding:8px 9px;font:700 12px Inter;background:#fff}.az-payroll-labtotal{font-weight:700;text-align:right}
      .az-payroll-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 17px 14px;border-top:1px solid #e6ded1;background:#fffdf8}.az-payroll-status{font-size:10px;color:#687067}.az-payroll-buttons{display:flex;gap:8px}
      @media(max-width:760px){.az-payroll-modal{width:98vw}.az-payroll-controls{align-items:stretch}.az-payroll-controls .field{min-width:100%}.az-month-tabs{overflow:auto;flex-wrap:nowrap;padding-bottom:2px}.az-payroll-table input{width:100px}.az-payroll-labline{grid-template-columns:1fr}.az-payroll-labtotal{text-align:left}}
    `;
    d.head.appendChild(s);
  }

  function installManager(w, d) {
    if (d.getElementById('azPayrollManager')) return;
    const el = d.createElement('div');
    el.id = 'azPayrollManager';
    el.className = 'az-payroll-backdrop az-payroll-hide';
    el.innerHTML = `
      <div class="az-payroll-modal" role="dialog" aria-modal="true" aria-labelledby="azPayrollTitle">
        <div class="az-payroll-head">
          <div><h3 id="azPayrollTitle">ЗП и выплаты</h3><small>Врачи по направлениям и общий ФОТ лаборатории — месяц за месяцем</small></div>
          <button id="azPayrollX" class="az-payroll-x" aria-label="Закрыть">×</button>
        </div>
        <div class="az-payroll-controls">
          <div class="field"><label>Направление</label><select id="azPayrollDirection"></select></div>
          <div><label style="display:block;font-size:11px;font-weight:600;color:#60685f;margin-bottom:6px">Месяц</label><div id="azPayrollMonths" class="az-month-tabs"></div></div>
        </div>
        <div class="az-payroll-body">
          <p id="azPayrollNote" class="az-payroll-note"></p>
          <div id="azPayrollTable"></div>
        </div>
        <div class="az-payroll-actions"><div id="azPayrollStatus" class="az-payroll-status"></div><div class="az-payroll-buttons"><button id="azPayrollCancel" class="btn">Отмена</button><button id="azPayrollSave" class="btn primary">Сохранить всё</button></div></div>
      </div>`;
    d.body.appendChild(el);

    el.addEventListener('click', e => { if (e.target === el) closeManager(d); });
    d.getElementById('azPayrollX').onclick = () => closeManager(d);
    d.getElementById('azPayrollCancel').onclick = () => closeManager(d);
    d.getElementById('azPayrollSave').onclick = () => saveAll(w, d);
    d.getElementById('azPayrollDirection').addEventListener('change', e => {
      captureCurrentMonth(d);
      state.direction = e.target.value;
      state.highlightDoctor = '';
      renderManager(w, d);
    });
    d.addEventListener('keydown', e => { if (e.key === 'Escape' && !el.classList.contains('az-payroll-hide')) closeManager(d); });
  }

  function bindEntryPoints(w, d) {
    const data = getData(w);
    if (!data) return;
    const topActions = d.querySelector('.top-actions');
    if (topActions && !d.getElementById('azPayrollTopOpen')) {
      const btn = d.createElement('button');
      btn.id = 'azPayrollTopOpen';
      btn.className = 'btn primary az-payroll-topbtn';
      btn.textContent = 'ЗП и выплаты';
      const logout = d.getElementById('logoutBtn');
      logout ? topActions.insertBefore(btn, logout) : topActions.appendChild(btn);
      btn.onclick = () => openManager(w, d);
    }
    const old = d.getElementById('azPayOpen');
    if (old && old.dataset.managerBound !== '1') {
      old.dataset.managerBound = '1';
      old.textContent = 'ЗП и выплаты';
      old.onclick = () => openManager(w, d, d.getElementById('doctor')?.value || '');
    }
  }

  function openManager(w, d, highlightDoctor = '') {
    const data = getData(w);
    if (!data) return;
    draft = buildDraft(w, data);
    const currentDirection = d.getElementById('direction')?.value;
    const dirs = payrollDirections(data);
    state.direction = dirs.includes(currentDirection) ? currentDirection : (dirs[0] || '');
    state.month = Math.max(0, (data.months?.length || 1) - 1);
    state.highlightDoctor = highlightDoctor;
    d.getElementById('azPayrollManager').classList.remove('az-payroll-hide');
    renderManager(w, d);
  }

  function closeManager(d) {
    d.getElementById('azPayrollManager')?.classList.add('az-payroll-hide');
    draft = null;
  }

  function renderManager(w, d) {
    const data = getData(w);
    if (!data || !draft) return;
    const dirs = payrollDirections(data);
    if (!dirs.includes(state.direction)) state.direction = dirs[0] || '';
    const dirSelect = d.getElementById('azPayrollDirection');
    dirSelect.innerHTML = dirs.map(x => `<option${x===state.direction?' selected':''}>${esc(x)}</option>`).join('');

    const months = data.months || [];
    if (state.month >= months.length) state.month = Math.max(0, months.length - 1);
    const monthHost = d.getElementById('azPayrollMonths');
    monthHost.innerHTML = months.map((m,i) => `<button type="button" class="az-month-tab ${i===state.month?'active':''}" data-mi="${i}">${esc(m)}</button>`).join('');
    monthHost.querySelectorAll('[data-mi]').forEach(btn => btn.onclick = () => {
      captureCurrentMonth(d);
      state.month = +btn.dataset.mi;
      state.highlightDoctor = '';
      renderManager(w, d);
    });

    if (state.direction === LAB_DIRECTION) renderLaboratory(d, months);
    else renderDoctors(data, d, months);
  }

  function renderDoctors(data, d, months) {
    d.getElementById('azPayrollNote').textContent = 'Вносишь выплаты сразу по всем врачам выбранного месяца. «Иные выплаты» — отпускные, больничные, премии и прочие доплаты. Переключай месяцы — введённые значения не потеряются до нажатия «Сохранить всё».';
    const doctors = Object.keys(data.directions?.[state.direction]?.doctors || {});
    const rows = doctors.map(name => {
      const rec = ensureDraftRecord(state.direction, name, months.length);
      const base = rec.salary[state.month];
      const extra = rec.extra[state.month];
      return `<tr data-doctor="${attr(name)}" class="${state.highlightDoctor===name?'az-highlight':''}">
        <td>${esc(name)}</td>
        <td><input class="az-payroll-base" data-doctor="${attr(name)}" inputmode="decimal" value="${base==null?'':base}" placeholder="—"></td>
        <td><input class="az-payroll-extra" data-doctor="${attr(name)}" inputmode="decimal" value="${extra==null||extra===0?'':extra}" placeholder="0"></td>
        <td class="az-payroll-total">${money((+base||0)+(+extra||0))}</td>
      </tr>`;
    }).join('');
    d.getElementById('azPayrollTable').innerHTML = `<table class="az-payroll-table"><thead><tr><th>Врач</th><th>Основная ЗП</th><th>Иные выплаты</th><th>Всего</th></tr></thead><tbody>${rows}<tr class="az-payroll-summary"><td>ИТОГО ЗА МЕСЯЦ</td><td id="azPayrollBaseSum"></td><td id="azPayrollExtraSum"></td><td id="azPayrollGrandSum"></td></tr></tbody></table>`;
    d.querySelectorAll('.az-payroll-base,.az-payroll-extra').forEach(inp => inp.addEventListener('input', () => recalcRowAndSummary(d, inp.closest('tr'))));
    recalcSummary(d);
    updateDoctorStatus(d, doctors, months);
    if (state.highlightDoctor) setTimeout(() => d.querySelector(`tr[data-doctor="${cssEscape(state.highlightDoctor)}"]`)?.scrollIntoView({block:'center'}), 0);
  }

  function renderLaboratory(d, months) {
    d.getElementById('azPayrollNote').textContent = 'Для лаборатории не разбиваем выплаты по сотрудникам: вводишь общий ФОТ всех сотрудников лаборатории за выбранный месяц одной суммой. В эту сумму включай зарплату, отпускные, больничные, премии и любые другие выплаты.';
    const rec = ensureLabRecord(months.length);
    const value = rec.salary[state.month];
    d.getElementById('azPayrollTable').innerHTML = `<div class="az-payroll-labbox"><h4>Лаборатория — все сотрудники</h4><div class="small">Одна общая сумма за ${esc(months[state.month] || 'месяц')}.</div><div class="az-payroll-labline"><div><strong>ФОТ лаборатории за месяц</strong><div class="small">Все выплаты сотрудникам одной суммой</div></div><input id="azPayrollLabTotal" inputmode="decimal" value="${value==null?'':value}" placeholder="—"><div id="azPayrollLabShown" class="az-payroll-labtotal">${value==null?'—':money(value)}</div></div></div>`;
    const inp = d.getElementById('azPayrollLabTotal');
    if (inp) inp.addEventListener('input', () => {
      const v = parseMoney(inp.value, null);
      d.getElementById('azPayrollLabShown').textContent = v==null ? '—' : money(v);
      const s = d.getElementById('azPayrollStatus');
      if (s) s.textContent = `${months[state.month] || ''}: ${v==null?'ФОТ лаборатории не заполнен':'ФОТ лаборатории '+money(v)}`;
    });
    updateLabStatus(d, months, value);
  }

  function captureCurrentMonth(d) {
    if (!draft || !state.direction) return;
    if (state.direction === LAB_DIRECTION) {
      const rec = ensureLabRecord((getData(frame.contentWindow)?.months || []).length);
      const inp = d.getElementById('azPayrollLabTotal');
      if (inp) rec.salary[state.month] = parseMoney(inp.value, null);
      rec.extra[state.month] = 0;
      return;
    }
    d.querySelectorAll('#azPayrollTable tr[data-doctor]').forEach(tr => {
      const doctor = tr.dataset.doctor;
      const rec = draft[state.direction]?.[doctor];
      if (!rec) return;
      rec.salary[state.month] = parseMoney(tr.querySelector('.az-payroll-base')?.value, null);
      rec.extra[state.month] = parseMoney(tr.querySelector('.az-payroll-extra')?.value, 0);
    });
  }

  function recalcRowAndSummary(d, tr) {
    if (!tr) return;
    const base = parseMoney(tr.querySelector('.az-payroll-base')?.value, 0) || 0;
    const extra = parseMoney(tr.querySelector('.az-payroll-extra')?.value, 0) || 0;
    const total = tr.querySelector('.az-payroll-total');
    if (total) total.textContent = money(base + extra);
    recalcSummary(d);
  }

  function recalcSummary(d) {
    let base = 0, extra = 0;
    d.querySelectorAll('#azPayrollTable tr[data-doctor]').forEach(tr => {
      base += parseMoney(tr.querySelector('.az-payroll-base')?.value, 0) || 0;
      extra += parseMoney(tr.querySelector('.az-payroll-extra')?.value, 0) || 0;
    });
    const a=d.getElementById('azPayrollBaseSum'),b=d.getElementById('azPayrollExtraSum'),c=d.getElementById('azPayrollGrandSum');
    if(a)a.textContent=money(base);if(b)b.textContent=money(extra);if(c)c.textContent=money(base+extra);
  }

  function updateDoctorStatus(d, doctors, months) {
    const filled = doctors.filter(name => ensureDraftRecord(state.direction, name, months.length).salary[state.month] != null).length;
    const s = d.getElementById('azPayrollStatus');
    if (s) s.textContent = `${months[state.month] || ''}: основная ЗП заполнена у ${filled} из ${doctors.length}`;
  }

  function updateLabStatus(d, months, value) {
    const s = d.getElementById('azPayrollStatus');
    if (s) s.textContent = `${months[state.month] || ''}: ${value==null?'ФОТ лаборатории не заполнен':'ФОТ лаборатории '+money(value)}`;
  }

  function saveAll(w, d) {
    const data = getData(w);
    if (!data || !draft) return;
    captureCurrentMonth(d);
    const salaryStore = loadStore(w, SALARY_KEY);
    const extraStore = loadStore(w, EXTRA_KEY);
    payrollDirections(data).forEach(direction => {
      salaryStore[direction] ||= {};
      extraStore[direction] ||= {};
      if (direction === LAB_DIRECTION) {
        const rec = ensureLabRecord(data.months.length);
        salaryStore[direction][LAB_TOTAL_KEY] = rec.salary.slice();
        extraStore[direction][LAB_TOTAL_KEY] = Array(data.months.length).fill(0);
        return;
      }
      Object.keys(data.directions?.[direction]?.doctors || {}).forEach(doctor => {
        const rec = ensureDraftRecord(direction, doctor, data.months.length);
        salaryStore[direction][doctor] = rec.salary.slice();
        extraStore[direction][doctor] = rec.extra.slice();
      });
    });
    w.localStorage.setItem(SALARY_KEY, JSON.stringify(salaryStore));
    w.localStorage.setItem(EXTRA_KEY, JSON.stringify(extraStore));
    closeManager(d);
    const focus = d.getElementById('focusPeriod');
    if (focus) focus.dispatchEvent(new w.Event('change', { bubbles: true }));
  }

  function buildDraft(w, data) {
    const salary = loadStore(w, SALARY_KEY);
    const extra = loadStore(w, EXTRA_KEY);
    const out = {};
    payrollDirections(data).forEach(direction => {
      out[direction] = {};
      if (direction === LAB_DIRECTION) {
        out[direction][LAB_TOTAL_KEY] = {
          salary: normalizeArray(salary?.[direction]?.[LAB_TOTAL_KEY], data.months.length, null),
          extra: Array(data.months.length).fill(0)
        };
        return;
      }
      Object.keys(data.directions?.[direction]?.doctors || {}).forEach(doctor => {
        out[direction][doctor] = {
          salary: normalizeArray(salary?.[direction]?.[doctor], data.months.length, null),
          extra: normalizeArray(extra?.[direction]?.[doctor], data.months.length, 0)
        };
      });
    });
    return out;
  }

  function ensureDraftRecord(direction, doctor, n) {
    draft[direction] ||= {};
    draft[direction][doctor] ||= { salary: Array(n).fill(null), extra: Array(n).fill(0) };
    return draft[direction][doctor];
  }

  function ensureLabRecord(n) {
    draft[LAB_DIRECTION] ||= {};
    draft[LAB_DIRECTION][LAB_TOTAL_KEY] ||= { salary: Array(n).fill(null), extra: Array(n).fill(0) };
    return draft[LAB_DIRECTION][LAB_TOTAL_KEY];
  }

  function payrollDirections(data) {
    const dirs = Object.keys(data?.directions || {}).filter(direction => direction !== LAB_DIRECTION && Object.keys(data.directions?.[direction]?.doctors || {}).length);
    if (data?.directions?.[LAB_DIRECTION] || !dirs.includes(LAB_DIRECTION)) dirs.push(LAB_DIRECTION);
    return dirs;
  }

  function normalizeArray(arr, n, fill) {
    const out = Array.isArray(arr) ? arr.slice(0,n) : [];
    while (out.length < n) out.push(fill);
    return out;
  }

  function loadStore(w, key) { try { return JSON.parse(w.localStorage.getItem(key) || '{}'); } catch { return {}; } }
  function getData(w) { try { return JSON.parse(w.localStorage.getItem(DATA_KEY) || 'null'); } catch { return null; } }
  function parseMoney(v, blank) { const s=String(v??'').trim().replace(/\s/g,'').replace(',','.'); if(!s)return blank; const n=Number(s); return Number.isFinite(n)?n:blank; }
  function money(n) { return new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(n||0)+' ₽'; }
  function esc(s) { return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function attr(s) { return esc(s); }
  function cssEscape(s) { return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/["\\]/g,'\\$&'); }
})();