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

  function assignmentDirection(item) {
    if (item?.group !== 'Врачи') return null;
    const text = norm([item.department, item.role, item.function].join(' '));
    if (/(остеопат|нутрициолог|подиатр|гастроэнтеролог|нейропсихолог|массаж|миофункцион|логопед|медицинская сестра по массажу)/.test(text)) return 'structure';
    if (/(ортодонт|стоматолог|ортопед|хирург|гигиенист|терапия|терапевт)/.test(text)) return 'dent';
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
      .filter(item => item.group === 'Врачи' && assignmentDirection(item) === direction)
      .reduce((sum, item) => sum + (Number(item.months?.[month]) || 0), 0);
  }

  function installBanner() {
    let banner = document.getElementById('azForecastAuditBanner');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'azForecastAuditBanner';
      banner.style.cssText = 'margin:10px 0 0;padding:9px 12px;border:1px solid #cfdccc;border-radius:10px;background:#f7fbf7;color:#435047;font-size:9px;line-height:1.45';
      document.querySelector('.bar')?.insertAdjacentElement('beforebegin', banner);
    }
    updateBanner();
  }

  function updateBanner() {
    const banner = document.getElementById('azForecastAuditBanner');
    if (!banner) return;
    const month = document.getElementById('baseMonth')?.value || '';
    const audited = (economics?.period || []).includes(month);
    if (audited) {
      banner.innerHTML = '<b>Источник ФОТ:</b> проверенный зарплатный реестр (контроль OK). Расходы клиники в модели остаются кассовыми из Финреза; переменные медицинские затраты — сценарное допущение.';
      banner.style.background = '#f7fbf7';
      banner.style.borderColor = '#cfdccc';
    } else {
      banner.innerHTML = '<b>Качество данных:</b> для выбранного месяца нет подтверждённого ФОТ из загруженного зарплатного реестра. Значение ФОТ берётся из ранее введённых данных и считается неполным.';
      banner.style.background = '#fff7ed';
      banner.style.borderColor = '#e3c9a8';
    }
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

    installBanner();
    if (economics?.period?.length) {
      const latestAudited = economics.period[economics.period.length - 1];
      if ([...baseMonth.options].some(option => option.value === latestAudited)) {
        baseMonth.value = latestAudited;
      }
      if (!baseMonth.dataset.azAuditBound) {
        baseMonth.dataset.azAuditBound = '1';
        baseMonth.addEventListener('change', () => setTimeout(updateBanner, 0));
      }
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
      if (!response.ok) throw new Error('HTTP ' + response.status);
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
