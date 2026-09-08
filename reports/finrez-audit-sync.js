(() => {
  const MONTHS = {
    '01':'Январь','02':'Февраль','03':'Март','04':'Апрель','05':'Май','06':'Июнь',
    '07':'Июль','08':'Август','09':'Сентябрь','10':'Октябрь','11':'Ноябрь','12':'Декабрь'
  };
  const money = value => new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(Number(value)||0)+' ₽';
  const pct = value => new Intl.NumberFormat('ru-RU',{style:'percent',maximumFractionDigits:1}).format(Number(value)||0);

  function sumObject(obj) {
    return Object.values(obj || {}).reduce((sum, value) => sum + (Number(value) || 0), 0);
  }

  function cashFot(finrez, month) {
    return Number(finrez?.expenses?.months?.[month]?.operating?.['ФОТ']?.total) || 0;
  }

  function installStyle() {
    if (document.getElementById('azFinrezAuditStyle')) return;
    const style = document.createElement('style');
    style.id = 'azFinrezAuditStyle';
    style.textContent = `
      .az-finrez-audit{margin:0 0 12px;border:1px solid #cfdccc;border-radius:12px;background:#f8fbf8;overflow:hidden}
      .az-finrez-audit-head{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:10px 12px;background:#e8f0ea;font-size:10px}
      .az-finrez-audit-head b{font-size:11px}.az-finrez-audit-body{padding:10px 12px}.az-finrez-audit-note{font-size:9px;line-height:1.45;color:#5f695f;margin-bottom:8px}
      .az-finrez-audit-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-bottom:9px}.az-finrez-audit-kpi{padding:8px;border:1px solid #dde6df;border-radius:9px;background:#fff}.az-finrez-audit-kpi span{display:block;font-size:7.5px;color:#737a73;font-weight:700}.az-finrez-audit-kpi strong{display:block;margin-top:4px;font-size:12px}
      .az-finrez-audit-table{width:100%;border-collapse:collapse}.az-finrez-audit-table th,.az-finrez-audit-table td{padding:6px 7px;border-bottom:1px solid #e7eee8;font-size:8.5px;text-align:right}.az-finrez-audit-table th:first-child,.az-finrez-audit-table td:first-child{text-align:left}.az-finrez-audit-ok{color:#2f7048;font-weight:700}.az-finrez-audit-warn{color:#8a6630;font-weight:700}
      @media(max-width:760px){.az-finrez-audit{margin:0 8px 10px}.az-finrez-audit-kpis{grid-template-columns:repeat(2,1fr)}.az-finrez-audit-body{overflow:auto}.az-finrez-audit-table{min-width:620px}}
    `;
    document.head.appendChild(style);
  }

  function render(economics, finrez) {
    installStyle();
    let host = document.getElementById('azFinrezAudit');
    if (!host) {
      host = document.createElement('section');
      host.id = 'azFinrezAudit';
      host.className = 'az-finrez-audit';
      const status = document.getElementById('status');
      status?.insertAdjacentElement('afterend', host);
    }

    const period = economics.period || [];
    const payrollTotal = Number(economics.payroll?.total) || 0;
    const revenueTotal = sumObject(economics.opu?.revenue_net);
    const materialsTotal = Number(economics.control?.materials_jan_jun) || sumObject(economics.opu?.materials);
    const assistantsTotal = Number(economics.control?.assistants_jan_jun) || sumObject(economics.opu?.assistant_salary);

    const rows = period.map(month => {
      const accruedFot = Number(economics.payroll?.total_by_month?.[month]) || 0;
      const cash = cashFot(finrez, month);
      const revenue = Number(economics.opu?.revenue_net?.[month]) || 0;
      const operatingProfit = Number(economics.opu?.operating_profit?.[month]) || 0;
      const ratio = revenue ? accruedFot / revenue : 0;
      return `<tr><td>${MONTHS[month.slice(5,7)] || month}</td><td>${money(accruedFot)}</td><td>${money(cash)}</td><td>${money(revenue)}</td><td>${pct(ratio)}</td><td>${money(operatingProfit)}</td></tr>`;
    }).join('');

    host.innerHTML = `
      <div class="az-finrez-audit-head"><b>Экономический контроль · ${economics.control?.status === 'OK' ? '<span class="az-finrez-audit-ok">OK</span>' : '<span class="az-finrez-audit-warn">требует проверки</span>'}</b><span>Источник: первичный ОПиУ + зарплатный реестр</span></div>
      <div class="az-finrez-audit-body">
        <div class="az-finrez-audit-note"><b>Важно:</b> основной Финрез остаётся кассовым — расходы считаются по фактической дате оплаты. Этот блок показывает начисленный ОПиУ и полный ФОТ для контрольной сверки; начисления не подменяют кассовые расходы.</div>
        <div class="az-finrez-audit-kpis">
          <div class="az-finrez-audit-kpi"><span>Полный ФОТ, янв–июн</span><strong>${money(payrollTotal)}</strong></div>
          <div class="az-finrez-audit-kpi"><span>ФОТ / выручка</span><strong>${revenueTotal ? pct(payrollTotal/revenueTotal) : '—'}</strong></div>
          <div class="az-finrez-audit-kpi"><span>Материалы по ОПиУ</span><strong>${money(materialsTotal)}</strong></div>
          <div class="az-finrez-audit-kpi"><span>Ассистенты стоматологов — ФОТ</span><strong>${money(assistantsTotal)}</strong></div>
        </div>
        <div style="overflow:auto"><table class="az-finrez-audit-table"><thead><tr><th>Месяц</th><th>Начисленный ФОТ</th><th>ФОТ по оплатам</th><th>Выручка ОПиУ</th><th>ФОТ / выручка</th><th>Опер. прибыль ОПиУ</th></tr></thead><tbody>${rows}</tbody></table></div>
      </div>`;
  }

  async function load() {
    try {
      const [econResponse, finResponse] = await Promise.all([
        fetch('/api/reports/economics-control',{credentials:'same-origin',cache:'no-store'}),
        fetch('/api/reports/finrez',{credentials:'same-origin',cache:'no-store'})
      ]);
      if (!econResponse.ok || !finResponse.ok) return;
      const economicsPayload = await econResponse.json();
      const finrez = await finResponse.json();
      const economics = economicsPayload.data;
      if (!economics?.available || economics?.control?.status !== 'OK') return;
      render(economics, finrez);
    } catch (error) {
      console.error('AZ finrez economics control failed', error);
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load, {once:true});
  else load();
})();
