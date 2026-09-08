(() => {
  const frame = document.getElementById('serviceFrame');
  if (!frame) return;

  const DATA_KEY = 'az-service-analytics-v1';
  const SALARY_KEY = 'az-service-salary-v1';
  const EXTRA_KEY = 'az-service-extra-payments-v1';
  const LAB_DIRECTION = 'Лаборатория';
  const LAB_TOTAL_KEY = '__LAB_TOTAL__';
  let economics = null;
  let syncing = false;
  let textObserver = null;
  let syncRetries = 0;

  const norm = value => String(value || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/\(\s*[cс]\s*\)/gi, '')
    .replace(/[^а-яa-z0-9\s-]/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  const surname = value => norm(value).split(' ')[0] || '';

  function assignmentDirection(item) {
    if (item?.group !== 'Врачи') return null;
    const text = norm([item.department, item.role, item.function].join(' '));
    if (/(остеопат|нутрициолог|подиатр|гастроэнтеролог|нейропсихолог|массаж|миофункцион|логопед|медицинская сестра по массажу)/.test(text)) {
      return 'Отделение структуры';
    }
    if (/(ортодонт|стоматолог|ортопед|хирург|гигиенист|терапия|терапевт)/.test(text)) {
      return 'Стоматология';
    }
    return null;
  }

  function canonicalDirection(direction) {
    return direction === 'Клиника' ? 'Отделение структуры' : direction;
  }

  function loadJson(storage, key, fallback = {}) {
    try { return JSON.parse(storage.getItem(key) || JSON.stringify(fallback)); }
    catch { return fallback; }
  }

  function monthArrayFromAssignments(assignments, period, count) {
    const out = Array(count).fill(null);
    period.forEach((month, i) => {
      if (i >= count) return;
      let total = 0;
      let found = false;
      assignments.forEach(item => {
        if (Object.prototype.hasOwnProperty.call(item.months || {}, month)) {
          total += Number(item.months[month]) || 0;
          found = true;
        }
      });
      out[i] = found ? total : null;
    });
    return out;
  }

  function patchFirstMonths(target, source, count) {
    const out = Array.isArray(target) ? target.slice(0, count) : [];
    while (out.length < count) out.push(null);
    source.forEach((value, i) => {
      if (i < count && value !== null && value !== undefined) out[i] = value;
    });
    return out;
  }

  function zeroAuditedExtras(target, periodLength, count) {
    const out = Array.isArray(target) ? target.slice(0, count) : [];
    while (out.length < count) out.push(0);
    for (let i = 0; i < Math.min(periodLength, count); i++) out[i] = 0;
    return out;
  }

  function exactDoctorAssignments(direction, doctor) {
    const canonical = canonicalDirection(direction);
    const wantedSurname = surname(doctor);
    return (economics?.payroll?.assignments || []).filter(item =>
      item.group === 'Врачи' &&
      assignmentDirection(item) === canonical &&
      surname(item.name) === wantedSurname
    );
  }

  function showStatus(message, bad = false) {
    let badge = document.getElementById('azEconomicsSyncBadge');
    if (!badge) {
      badge = document.createElement('div');
      badge.id = 'azEconomicsSyncBadge';
      badge.style.cssText = 'font-size:9px;font-weight:700;padding:5px 8px;border-radius:999px;border:1px solid #c8d4ca;background:#edf5ee;color:#355440;white-space:nowrap';
      document.querySelector('.nav')?.insertBefore(badge, document.querySelector('.nav-actions'));
    }
    badge.textContent = message;
    if (bad) {
      badge.style.background = '#fff0e8';
      badge.style.borderColor = '#e1b7aa';
      badge.style.color = '#8b493d';
    } else {
      badge.style.background = '#edf5ee';
      badge.style.borderColor = '#c8d4ca';
      badge.style.color = '#355440';
    }
  }

  function relabelAuditedEconomics(d) {
    if (!d?.body) return;
    const replacements = [
      [/Основная ЗП врачей/g, 'ФОТ врачей по зарплатному реестру'],
      [/Основная ЗП/g, 'ФОТ по зарплатному реестру'],
      [/Прочие выплаты врачам/g, 'Доп. выплаты вне зарплатного реестра'],
      [/Прочие выплаты/g, 'Доп. выплаты вне зарплатного реестра'],
      [/пустые поля выплат считаются нулём/g, 'январь–июнь: зарплатный реестр; отсутствие ФОТ не считается нулём'],
    ];
    const walker = d.createTreeWalker(d.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => {
      let value = node.nodeValue || '';
      replacements.forEach(([re, to]) => { value = value.replace(re, to); });
      node.nodeValue = value;
    });
  }

  function startRelabelObserver(d) {
    relabelAuditedEconomics(d);
    if (textObserver) textObserver.disconnect();
    textObserver = new MutationObserver(() => setTimeout(() => relabelAuditedEconomics(d), 0));
    textObserver.observe(d.body, { childList: true, subtree: true });
  }

  function syncIntoExistingReport() {
    if (syncing || !economics?.available || economics?.control?.status !== 'OK') return;
    const w = frame.contentWindow;
    const d = frame.contentDocument;
    if (!w || !d) return;
    const analytics = loadJson(w.localStorage, DATA_KEY, null);
    if (!analytics?.directions || !Array.isArray(analytics.months)) {
      if (syncRetries++ < 30) setTimeout(syncIntoExistingReport, 150);
      return;
    }
    syncRetries = 0;

    syncing = true;
    try {
      const count = analytics.months.length;
      const period = economics.period || [];
      const salaryStore = loadJson(w.localStorage, SALARY_KEY, {});
      const extraStore = loadJson(w.localStorage, EXTRA_KEY, {});

      Object.keys(analytics.directions).forEach(direction => {
        if (direction === LAB_DIRECTION) return;
        const canonical = canonicalDirection(direction);
        if (!['Стоматология', 'Отделение структуры'].includes(canonical)) return;
        salaryStore[direction] ||= {};
        extraStore[direction] ||= {};
        Object.keys(analytics.directions?.[direction]?.doctors || {}).forEach(doctor => {
          const assignments = exactDoctorAssignments(direction, doctor);
          if (!assignments.length) return;
          const audited = monthArrayFromAssignments(assignments, period, count);
          salaryStore[direction][doctor] = patchFirstMonths(salaryStore[direction][doctor], audited, count);
          extraStore[direction][doctor] = zeroAuditedExtras(extraStore[direction][doctor], period.length, count);
        });
      });

      const labAssignments = (economics.payroll?.assignments || []).filter(item => item.group === 'Лаборатория');
      if (labAssignments.length) {
        const auditedLab = monthArrayFromAssignments(labAssignments, period, count);
        salaryStore[LAB_DIRECTION] ||= {};
        extraStore[LAB_DIRECTION] ||= {};
        salaryStore[LAB_DIRECTION][LAB_TOTAL_KEY] = patchFirstMonths(salaryStore[LAB_DIRECTION][LAB_TOTAL_KEY], auditedLab, count);
        extraStore[LAB_DIRECTION][LAB_TOTAL_KEY] = zeroAuditedExtras(extraStore[LAB_DIRECTION][LAB_TOTAL_KEY], period.length, count);
      }

      w.localStorage.setItem(SALARY_KEY, JSON.stringify(salaryStore));
      w.localStorage.setItem(EXTRA_KEY, JSON.stringify(extraStore));
      w.localStorage.setItem('az-economics-sync-meta', JSON.stringify({
        generatedAt: economics.generated_at,
        control: economics.control.status,
        period: economics.period,
        source: economics.source,
      }));

      startRelabelObserver(d);
      showStatus('Экономика: реестр ФОТ OK');
      const direction = d.getElementById('direction');
      if (direction) direction.dispatchEvent(new Event('change', { bubbles: true }));
      window.dispatchEvent(new CustomEvent('az-economics-synced', { detail: economics }));
    } finally {
      syncing = false;
    }
  }

  async function loadEconomics() {
    try {
      const response = await fetch('/api/reports/economics-control', { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const payload = await response.json();
      economics = payload.data;
      if (!economics?.available || economics?.control?.status !== 'OK') {
        showStatus('Экономика: неполные данные', true);
        return;
      }
      syncIntoExistingReport();
    } catch (error) {
      showStatus('Экономика: ошибка загрузки', true);
      console.error('AZ economics sync failed', error);
    }
  }

  frame.addEventListener('load', () => {
    if (economics) syncIntoExistingReport();
    else loadEconomics();
  });
  if (frame.contentDocument?.readyState === 'complete') loadEconomics();
})();
