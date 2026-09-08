(() => {
  let economics = null;
  let previousFotRate = null;
  let uiRetries = 0;

  const norm = value => String(value || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/\(\s*[cс]\s*\)/gi, '')
    .replace(/[^а-яa-z0-9\s-]/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  function doctorDirection(item) {
    if (item?.group !== 'Врачи') return null;
    const text = norm([item.department, item.role, item.function].join(' '));
    if (/(отделение структуры|остеопат|нутрициолог|подиатр|гастроэнтеролог|нейропсихолог|массаж|миофункцион|логопед|медицинская сестра по массажу)/.test(text)) {
      return 'structure';
    }
    if (/(функциональная стоматология|ортодонт|стоматолог|ортопед|хирург|гигиенист|отделение терапии|терапия|терапевт)/.test(text)) {
      return 'dent';
    }
    return null;
  }

  function auditedFot(month, direction) {
    if (!economics?.available || economics?.control?.status !== 'OK') return null;
    if (!(economics.period || []).includes(month)) return null;
    const assignments = economics.payroll?.assignments || [];
    if (direction === 'lab') {
      return assignments
        .filter(item => item.group === 'Лаборатория')
        .reduce((sum, item) => sum + (Number(item.months?.[month]) || 0), 0);
    }
    return assignments
      .filter(item => item.group === 'Врачи' && doctorDirection(item) === direction)
      .reduce((sum, item) => sum + (Number(item.months?.[month]) || 0), 0);
  }

  function installRatePatch() {
    if (typeof fotRate !== 'function') return false;
    if (!previousFotRate) {
      previousFotRate = fotRate;
      fotRate = function(month, direction, revenue) {
        const total = auditedFot(month, direction);
        if (total !== null && revenue > 0) return total / revenue;
        return previousFotRate(month, direction, revenue);
      };
    }
    return true;
  }

  function refreshUiWhenReady() {
    const baseMonth = document.getElementById('baseMonth');
    const structureSelect = document.getElementById('seg-structure');
    if (!baseMonth || !baseMonth.options.length || !structureSelect) {
      if (uiRetries++ < 50) setTimeout(refreshUiWhenReady, 100);
      return;
    }
    if (typeof fillSegments === 'function' && typeof applyBase === 'function' && typeof calculate === 'function') {
      for (const [direction] of (typeof DIRS !== 'undefined' ? DIRS : [])) {
        fillSegments(direction);
        applyBase(direction, true);
      }
      calculate();
    }
  }

  function patchForecast() {
    if (!installRatePatch()) {
      setTimeout(patchForecast, 100);
      return;
    }
    refreshUiWhenReady();
  }

  async function load() {
    try {
      const response = await fetch('/api/reports/economics-control', { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) return;
      const payload = await response.json();
      economics = payload.data;
      if (!economics?.available || economics?.control?.status !== 'OK') return;
      patchForecast();
    } catch (error) {
      console.error('AZ forecast economics sync failed', error);
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load, { once: true });
  else load();
})();