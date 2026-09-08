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
    if (/(остеопат|нутрициолог|подиатр|гастроэнтеролог|нейропсихолог|массаж|миофункцион|логопед|медицинская сестра по массажу)/.test(text)) return 'Отделение структуры';
    if (/(ортодонт|стоматолог|ортопед|хирург|гигиенист|терапия|терапевт)/.test(text)) return 'Стоматология';
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
      const direction = d.getElementById('direction');
      if (direction) direction.dispatchEvent(new Event('change', { bubbles: true }));
    } finally {
      syncing = false;
    }
  }

  async function loadEconomics() {
    try {
      const response = await fetch('/api/reports/economics-control', { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) return;
      const payload = await response.json();
      economics = payload.data;
      if (economics?.available && economics?.control?.status === 'OK') syncIntoExistingReport();
    } catch (error) {
      console.error('AZ economics sync failed', error);
    }
  }

  frame.addEventListener('load', () => {
    if (economics) syncIntoExistingReport();
    else loadEconomics();
  });
  if (frame.contentDocument?.readyState === 'complete') loadEconomics();
})();
