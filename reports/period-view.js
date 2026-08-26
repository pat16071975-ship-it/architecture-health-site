(()=>{
  const style=document.createElement('style');
  style.textContent=`
    #periodView{overflow:visible;background:transparent;border:0}
    #periodView>h2{display:none}
    .period-card{padding:0;background:transparent}
    .period-compare{overflow:auto;border:1px solid var(--line);border-radius:14px;background:rgba(255,253,248,.9);box-shadow:0 10px 28px rgba(71,58,38,.05)}
    .period-compare table{width:100%;min-width:760px;border-collapse:separate;border-spacing:0}
    .period-compare th,.period-compare td{padding:10px 12px;border-bottom:1px solid #e7dfd3;font-size:12px;vertical-align:middle}
    .period-compare thead th{position:sticky;top:0;z-index:2;background:#f8f4ec;text-align:center;font-weight:750;color:#354039}
    .period-compare thead th:first-child{text-align:left;min-width:260px}
    .period-compare tbody td{text-align:right;font-weight:650;white-space:nowrap}
    .period-compare tbody td:first-child{text-align:left;font-weight:600;white-space:normal}
    .period-compare .period-name{font:700 17px/1.05 'Cormorant Garamond',serif;color:#2f3a33}
    .period-compare .period-months{display:block;margin-top:4px;font:500 10px/1.15 Inter,sans-serif;color:#777d76}
    .period-compare .group-row td{padding:11px 14px;font:700 18px/1.1 'Cormorant Garamond',serif;text-align:left!important;border-bottom:1px solid rgba(0,0,0,.05)}
    .period-compare .group-general td{background:#efe5d2;color:#5e5037}
    .period-compare .group-dent td{background:#dfe9f2;color:#365268}
    .period-compare .group-clinic td{background:#dfe9e2;color:#355744}
    .period-compare .group-lab td{background:#f1dfc9;color:#705337}
    .period-compare .group-marketing td{background:#eee5f1;color:#654a6b}
    .period-compare .sub-row td{padding:7px 12px;font-size:10px;text-transform:uppercase;letter-spacing:.11em;text-align:left!important;font-weight:800}
    .period-compare .sub-general td{background:#faf5e9;color:#8a7249}
    .period-compare .sub-visits-dent td{background:#eef5fa;color:#547790}
    .period-compare .sub-money-dent td{background:#e5eff7;color:#3f6988}
    .period-compare .sub-team-dent td{background:#f4f8fb;color:#6d8292}
    .period-compare .sub-visits-clinic td{background:#eef6f0;color:#52765f}
    .period-compare .sub-money-clinic td{background:#e5f0e8;color:#3f6850}
    .period-compare .sub-team-clinic td{background:#f3f8f4;color:#6b8170}
    .period-compare .sub-lab-volume td{background:#fbf1e6;color:#8a6845}
    .period-compare .sub-lab-money td{background:#f6e8d8;color:#755231}
    .period-compare .sub-marketing-input td{background:#f7f1f8;color:#765a7b}
    .period-compare .sub-marketing-conv td{background:#efe6f1;color:#634a69}
    .period-compare .row-general td{background:#fffdf8}
    .period-compare .row-visits-dent td{background:#f8fbfd}
    .period-compare .row-money-dent td{background:#f0f6fa}
    .period-compare .row-team-dent td{background:#fbfdfe}
    .period-compare .row-visits-clinic td{background:#f8fcf9}
    .period-compare .row-money-clinic td{background:#f0f7f2}
    .period-compare .row-team-clinic td{background:#fbfdfb}
    .period-compare .row-lab-volume td{background:#fffaf4}
    .period-compare .row-lab-money td{background:#fcf4ea}
    .period-compare .row-marketing-input td{background:#fcf9fc}
    .period-compare .row-marketing-conv td{background:#f7f1f8}
    .period-compare .important td{font-weight:800}
    .period-compare .empty{color:#aaa;font-weight:500}
    .period-summary-note{margin:0 0 10px;padding:10px 13px;border-left:3px solid var(--gold);border-radius:8px;background:#fffaf0;color:#665b49;font-size:12px}
    @media(max-width:720px){.period-compare th,.period-compare td{padding:8px 9px}.period-compare thead th:first-child{min-width:210px}.period-compare .group-row td{font-size:16px}}
  `;
  document.head.appendChild(style);

  const qWrap=document.getElementById('quarterWrap');
  const hWrap=document.getElementById('halfWrap');
  if(qWrap)qWrap.classList.add('hidden');
  if(hWrap)hWrap.classList.add('hidden');

  const periodView=document.getElementById('periodView');
  const periodCard=periodView?.querySelector('.period-card');
  if(periodCard){
    periodCard.innerHTML='<div id="periodSummaryNote" class="period-summary-note"></div><div class="period-compare"><table><thead id="periodCompareHead"></thead><tbody id="periodCompareBody"></tbody></table></div>';
  }

  const fmtMoney=v=>v===''||v==null||!isFinite(+v)?'—':money(v);
  const fmtCount=v=>v===''||v==null||!isFinite(+v)?'—':count(v);
  const fmtPct=v=>v===''||v==null||!isFinite(+v)?'—':pct(v);

  function getPeriodDefs(mode,year){
    if(mode==='quarter')return [
      {name:'1 квартал',months:'январь — март',sm:1,em:3},
      {name:'2 квартал',months:'апрель — июнь',sm:4,em:6},
      {name:'3 квартал',months:'июль — сентябрь',sm:7,em:9},
      {name:'4 квартал',months:'октябрь — декабрь',sm:10,em:12}
    ];
    if(mode==='half')return [
      {name:'1 полугодие',months:'январь — июнь',sm:1,em:6},
      {name:'2 полугодие',months:'июль — декабрь',sm:7,em:12}
    ];
    return [{name:String(year)+' год',months:'январь — декабрь',sm:1,em:12}];
  }

  function periodData(def,year){
    const rows=latestMonthlyRecords(year,def.sm,def.em);
    return {def,rows,a:aggregate(rows),expected:def.em-def.sm+1};
  }

  function valueFor(pd,getter,formatter){
    if(!pd.a)return '<span class="empty">—</span>';
    const v=getter(pd.a,pd.rows);
    const out=formatter(v);
    return out==='—'?'<span class="empty">—</span>':out;
  }

  function addGroup(tbody,label,cls,colspan){
    const tr=document.createElement('tr');tr.className='group-row '+cls;tr.innerHTML=`<td colspan="${colspan}">${label}</td>`;tbody.appendChild(tr);
  }
  function addSub(tbody,label,cls,colspan){
    const tr=document.createElement('tr');tr.className='sub-row '+cls;tr.innerHTML=`<td colspan="${colspan}">${label}</td>`;tbody.appendChild(tr);
  }
  function addMetric(tbody,label,cls,pds,getter,formatter,important=false){
    const tr=document.createElement('tr');tr.className=cls+(important?' important':'');
    tr.innerHTML='<td>'+label+'</td>'+pds.map(pd=>'<td>'+valueFor(pd,getter,formatter)+'</td>').join('');
    tbody.appendChild(tr);
  }

  window.renderPeriod=function(){
    const mode=document.getElementById('mode').value;
    if(mode==='date')return;
    const year=+document.getElementById('year').value;
    const defs=getPeriodDefs(mode,year),pds=defs.map(d=>periodData(d,year));
    const head=document.getElementById('periodCompareHead'),tbody=document.getElementById('periodCompareBody'),note=document.getElementById('periodSummaryNote');
    if(!head||!tbody)return;
    document.getElementById('periodTitle').textContent=mode==='quarter'?'Кварталы '+year:mode==='half'?'Полугодия '+year:'Год '+year;
    head.innerHTML='<tr><th>Показатель</th>'+pds.map(pd=>`<th><span class="period-name">${pd.def.name}</span><span class="period-months">${pd.def.months}<br>данных: ${pd.rows.length} из ${pd.expected} мес.</span></th>`).join('')+'</tr>';
    tbody.innerHTML='';
    const cols=pds.length+1;
    const any=pds.some(x=>x.a);
    note.textContent=any?'Периоды показаны рядом для сравнения. Если месяц ещё не закрыт или данные за него не внесены, он не участвует в агрегате и это видно в заголовке периода.':'За выбранный год пока нет сохранённых данных.';

    addGroup(tbody,'Основные показатели','group-general',cols);
    addSub(tbody,'План и выполнение','sub-general',cols);
    addMetric(tbody,'План','row-general',pds,a=>a.plan,fmtMoney);
    addMetric(tbody,'Должно быть','row-general',pds,a=>a.due,fmtMoney);
    addMetric(tbody,'Факт медицина','row-general',pds,a=>a.factMedicine,fmtMoney);
    addMetric(tbody,'Факт лаборатория','row-general',pds,a=>a.factLab,fmtMoney);
    addMetric(tbody,'Факт общий','row-general',pds,a=>a.factTotal,fmtMoney,true);
    addMetric(tbody,'% выполнения','row-general',pds,a=>a.execution,fmtPct,true);
    addSub(tbody,'Пациенты','sub-general',cols);
    addMetric(tbody,'Первичные приёмы','row-general',pds,a=>a.primary,fmtCount);
    addMetric(tbody,'Повторные приёмы','row-general',pds,a=>a.repeat,fmtCount);
    addMetric(tbody,'Средний чек медицины','row-general',pds,a=>a.avgCheck,fmtMoney,true);

    addGroup(tbody,'Стоматология','group-dent',cols);
    addSub(tbody,'Приёмы','sub-visits-dent',cols);
    addMetric(tbody,'Первичные','row-visits-dent',pds,a=>a.dentPrimary,fmtCount);
    addMetric(tbody,'Повторные','row-visits-dent',pds,a=>a.dentRepeat,fmtCount);
    addSub(tbody,'Выручка и экономика','sub-money-dent',cols);
    addMetric(tbody,'Выручка стоматологии','row-money-dent',pds,a=>a.dentRev,fmtMoney,true);
    addMetric(tbody,'Средний чек стоматологии','row-money-dent',pds,a=>a.dentAvg,fmtMoney);
    addSub(tbody,'Выручка по врачам','sub-team-dent',cols);
    dentists.forEach(name=>addMetric(tbody,name,'row-team-dent',pds,a=>num((a.dentists||{})[name]),fmtMoney));

    addGroup(tbody,'Клиника','group-clinic',cols);
    addSub(tbody,'Приёмы','sub-visits-clinic',cols);
    addMetric(tbody,'Первичные','row-visits-clinic',pds,a=>a.clinicPrimary,fmtCount);
    addMetric(tbody,'Повторные','row-visits-clinic',pds,a=>a.clinicRepeat,fmtCount);
    addSub(tbody,'Выручка и экономика','sub-money-clinic',cols);
    addMetric(tbody,'Выручка клиники','row-money-clinic',pds,a=>a.clinicRev,fmtMoney,true);
    addMetric(tbody,'Средний чек клиники','row-money-clinic',pds,a=>a.clinicAvg,fmtMoney);
    addSub(tbody,'Выручка по специалистам','sub-team-clinic',cols);
    clinicDocs.forEach(name=>addMetric(tbody,name,'row-team-clinic',pds,a=>num((a.clinicDocs||{})[name]),fmtMoney));

    addGroup(tbody,'Лаборатория','group-lab',cols);
    addSub(tbody,'Объём','sub-lab-volume',cols);
    addMetric(tbody,'Количество заказов','row-lab-volume',pds,a=>a.labOrders,fmtCount);
    addSub(tbody,'Выручка и экономика','sub-lab-money',cols);
    addMetric(tbody,'Выручка лаборатории','row-lab-money',pds,a=>a.labRevenue,fmtMoney,true);
    addMetric(tbody,'Средний чек лаборатории','row-lab-money',pds,a=>a.labAvg,fmtMoney);

    addGroup(tbody,'Маркетинг','group-marketing',cols);
    addSub(tbody,'Лиды','sub-marketing-input',cols);
    addMetric(tbody,'Целевые лиды — стоматология','row-marketing-input',pds,a=>a.leadsDent,fmtCount);
    addMetric(tbody,'Отказ / мониторинг — стоматология','row-marketing-input',pds,a=>a.leadsDentLost,fmtCount);
    addMetric(tbody,'Целевые лиды — клиника','row-marketing-input',pds,a=>a.leadsClinic,fmtCount);
    addMetric(tbody,'Резерв','row-marketing-input',pds,a=>a.leadsReserve,fmtCount);
    addMetric(tbody,'Всего лидов','row-marketing-input',pds,a=>a.totalLeads,fmtCount,true);
    addSub(tbody,'Конверсия','sub-marketing-conv',cols);
    addMetric(tbody,'Конверсия стоматология','row-marketing-conv',pds,a=>a.convDent,fmtPct);
    addMetric(tbody,'Конверсия клиника','row-marketing-conv',pds,a=>a.convClinic,fmtPct);
    addMetric(tbody,'Общая конверсия','row-marketing-conv',pds,a=>a.totalConv,fmtPct,true);
  };

  document.getElementById('quarter')?.addEventListener('change',()=>window.renderPeriod());
  document.getElementById('half')?.addEventListener('change',()=>window.renderPeriod());
})();
