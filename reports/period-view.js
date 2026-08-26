(()=>{
  const style=document.createElement('style');
  style.textContent=`
    #periodView{overflow:visible;background:transparent;border:0}
    #periodView>h2{display:none}
    .period-card{padding:0;background:transparent}
    .period-compare,.date-compare-table{overflow:auto;border:1px solid var(--line);border-radius:14px;background:rgba(255,253,248,.9);box-shadow:0 10px 28px rgba(71,58,38,.05)}
    .period-compare table,.date-compare-table table{width:100%;min-width:760px;border-collapse:separate;border-spacing:0}
    .period-compare th,.period-compare td,.date-compare-table th,.date-compare-table td{padding:10px 12px;border-bottom:1px solid #e7dfd3;font-size:12px;vertical-align:middle}
    .period-compare thead th,.date-compare-table thead th{position:sticky;top:0;z-index:2;background:#f8f4ec;text-align:center;font-weight:750;color:#354039}
    .period-compare thead th:first-child,.date-compare-table thead th:first-child{text-align:left;min-width:260px;left:0;z-index:3}
    .period-compare tbody td,.date-compare-table tbody td{text-align:right;font-weight:650;white-space:nowrap}
    .period-compare tbody td:first-child,.date-compare-table tbody td:first-child{text-align:left;font-weight:600;white-space:normal}
    .period-compare .period-name,.date-compare-table .period-name{font:700 17px/1.05 'Cormorant Garamond',serif;color:#2f3a33}
    .period-compare .period-months,.date-compare-table .period-months{display:block;margin-top:4px;font:500 10px/1.15 Inter,sans-serif;color:#777d76}
    .period-compare .group-row td,.date-compare-table .group-row td{padding:11px 14px;font:700 18px/1.1 'Cormorant Garamond',serif;text-align:left!important;border-bottom:1px solid rgba(0,0,0,.05)}
    .group-general td{background:#efe5d2!important;color:#5e5037}.group-dent td{background:#dfe9f2!important;color:#365268}.group-clinic td{background:#dfe9e2!important;color:#355744}.group-lab td{background:#f1dfc9!important;color:#705337}.group-marketing td{background:#eee5f1!important;color:#654a6b}
    .period-compare .sub-row td,.date-compare-table .sub-row td{padding:7px 12px;font-size:10px;text-transform:uppercase;letter-spacing:.11em;text-align:left!important;font-weight:800}
    .sub-general td{background:#faf5e9!important;color:#8a7249}.sub-visits-dent td{background:#eef5fa!important;color:#547790}.sub-money-dent td{background:#e5eff7!important;color:#3f6988}.sub-team-dent td{background:#f4f8fb!important;color:#6d8292}.sub-visits-clinic td{background:#eef6f0!important;color:#52765f}.sub-money-clinic td{background:#e5f0e8!important;color:#3f6850}.sub-team-clinic td{background:#f3f8f4!important;color:#6b8170}.sub-lab-volume td{background:#fbf1e6!important;color:#8a6845}.sub-lab-money td{background:#f6e8d8!important;color:#755231}.sub-marketing-input td{background:#f7f1f8!important;color:#765a7b}.sub-marketing-conv td{background:#efe6f1!important;color:#634a69}
    .row-general td{background:#fffdf8}.row-visits-dent td{background:#f8fbfd}.row-money-dent td{background:#f0f6fa}.row-team-dent td{background:#fbfdfe}.row-visits-clinic td{background:#f8fcf9}.row-money-clinic td{background:#f0f7f2}.row-team-clinic td{background:#fbfdfb}.row-lab-volume td{background:#fffaf4}.row-lab-money td{background:#fcf4ea}.row-marketing-input td{background:#fcf9fc}.row-marketing-conv td{background:#f7f1f8}
    .period-compare .important td,.date-compare-table .important td{font-weight:800}.empty{color:#aaa;font-weight:500}
    .period-summary-note,.date-summary-note{margin:0 0 10px;padding:10px 13px;border-left:3px solid var(--gold);border-radius:8px;background:#fffaf0;color:#665b49;font-size:12px}
    .date-view-field{min-width:190px!important}.compare-range-field{min-width:180px!important}.today-btn{align-self:end}.entry-btn{white-space:nowrap}
    #dateCompareView{margin-top:16px}.date-compare-shell{padding:0}.date-compare-title{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:10px}.date-compare-title h2{margin:0;font:700 24px/1.1 'Cormorant Garamond',serif}.date-compare-title .muted{max-width:720px;text-align:right}
    .entry-overlay{position:fixed;inset:0;z-index:1000;background:rgba(38,45,40,.38);backdrop-filter:blur(3px);display:grid;place-items:center;padding:18px}.entry-dialog{width:min(1180px,98vw);max-height:94vh;display:flex;flex-direction:column;background:#fffdf8;border:1px solid #d8cdbb;border-radius:18px;box-shadow:0 24px 80px rgba(37,42,39,.26);overflow:hidden}.entry-head{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 18px;border-bottom:1px solid #e3dacd;background:#faf6ef}.entry-head h2{margin:0;font:700 26px/1 'Cormorant Garamond',serif}.entry-close{border:0;background:transparent;font-size:24px;line-height:1;cursor:pointer;color:#687067;padding:4px 8px}.entry-body{overflow:auto;padding:14px 18px 10px}.entry-top{display:grid;grid-template-columns:minmax(180px,240px) 1fr;gap:12px;align-items:end;margin-bottom:12px}.entry-columns{display:grid;grid-template-columns:.9fr 1.08fr 1.08fr;gap:12px;align-items:start}.entry-card{border:1px solid #e2d9ca;border-radius:14px;overflow:hidden;background:#fff}.entry-card h3{margin:0;padding:10px 12px;font:700 18px/1.1 'Cormorant Garamond',serif}.entry-card.general h3{background:#efe5d2}.entry-card.dent h3{background:#dfe9f2}.entry-card.clinic h3{background:#dfe9e2}.entry-card.lab h3{background:#f1dfc9}.entry-card.marketing h3{background:#eee5f1}.entry-card-body{padding:10px 12px}.entry-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.entry-field{display:grid;gap:4px}.entry-field label{font-size:10px;font-weight:700;color:#626a62}.entry-field input{width:100%;padding:8px 9px;border:1px solid #d7cdbd;border-radius:8px;background:#fff;font:600 13px Inter;color:#2f3732}.entry-field input:focus{outline:2px solid rgba(68,99,79,.18);border-color:#78917f}.entry-list{display:grid;gap:7px}.entry-doctor{display:grid;grid-template-columns:minmax(0,1fr) 130px;gap:8px;align-items:center}.entry-doctor span{font-size:11px;line-height:1.2}.entry-doctor input{width:100%;padding:8px;border:1px solid #d7cdbd;border-radius:8px;text-align:right;font:600 12px Inter}.entry-reconcile{margin-top:12px;padding:10px 12px;border-radius:10px;background:#f3f7f3;border:1px solid #d5e2d8;font-size:12px;color:#42564a}.entry-reconcile.bad{background:#fff1ef;border-color:#e3aaa3;color:#8d403b}.entry-foot{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 18px;border-top:1px solid #e3dacd;background:#faf7f1}.entry-foot-note{font-size:11px;color:#737970}.entry-foot-actions{display:flex;gap:8px}.reconcile-note{margin:10px 14px 0;padding:9px 11px;border:1px solid #e0aaa3;border-radius:9px;background:#fff1ef;color:#8d403b;font-size:12px;font-weight:700}.view-only{cursor:default!important;background:transparent!important}
    .heat-cell{transition:background-color .2s ease}
    @media(max-width:900px){.entry-columns{grid-template-columns:1fr 1fr}.entry-columns>.entry-stack:first-child{grid-column:1/-1}.date-compare-title{align-items:flex-start;flex-direction:column}.date-compare-title .muted{text-align:left}}
    @media(max-width:720px){.period-compare th,.period-compare td,.date-compare-table th,.date-compare-table td{padding:8px 9px}.period-compare thead th:first-child,.date-compare-table thead th:first-child{min-width:210px}.period-compare .group-row td,.date-compare-table .group-row td{font-size:16px}.entry-overlay{padding:0}.entry-dialog{width:100vw;max-height:100vh;height:100vh;border-radius:0}.entry-columns{grid-template-columns:1fr}.entry-columns>.entry-stack:first-child{grid-column:auto}.entry-top{grid-template-columns:1fr}.entry-grid{grid-template-columns:1fr 1fr}.entry-doctor{grid-template-columns:minmax(0,1fr) 115px}.entry-foot{position:sticky;bottom:0}.date-view-field,.compare-range-field{min-width:100%!important}}
    @media(max-width:430px){.entry-grid{grid-template-columns:1fr}.entry-head h2{font-size:22px}.entry-body{padding:10px}.entry-foot{padding:10px;align-items:stretch;flex-direction:column}.entry-foot-actions .btn{flex:1}}
  `;
  document.head.appendChild(style);

  const qWrap=document.getElementById('quarterWrap');
  const hWrap=document.getElementById('halfWrap');
  if(qWrap)qWrap.classList.add('hidden');
  if(hWrap)hWrap.classList.add('hidden');

  const periodView=document.getElementById('periodView');
  const periodCard=periodView?.querySelector('.period-card');
  if(periodCard){periodCard.innerHTML='<div id="periodSummaryNote" class="period-summary-note"></div><div class="period-compare"><table><thead id="periodCompareHead"></thead><tbody id="periodCompareBody"></tbody></table></div>';}

  const fmtMoney=v=>v===''||v==null||!isFinite(+v)?'—':money(v);
  const fmtCount=v=>v===''||v==null||!isFinite(+v)?'—':count(v);
  const fmtPct=v=>v===''||v==null||!isFinite(+v)?'—':pct(v);
  const isBlankValue=v=>v===''||v===null||v===undefined;
  const escapeHtml=s=>String(s??'').replace(/[&<>'"]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));

  function mix(a,b,t){return Math.round(a+(b-a)*t)}
  function heatColor(t){
    const red=[244,215,210],yellow=[245,239,204],green=[216,235,217];
    const x=Math.max(0,Math.min(1,t));
    const from=x<.5?red:yellow,to=x<.5?yellow:green,u=x<.5?x*2:(x-.5)*2;
    return `rgb(${mix(from[0],to[0],u)},${mix(from[1],to[1],u)},${mix(from[2],to[2],u)})`;
  }
  function heatEnabled(label,key){
    const text=(label+' '+(key||'')).toLowerCase();
    return !/(потер|отказ|недош|не дош|lost|reject|noshow|no_show)/i.test(text);
  }
  function heatStyles(values,enabled=true){
    const nums=values.filter(v=>v!==null&&v!==undefined&&v!==''&&isFinite(+v)).map(Number);
    if(!enabled||nums.length<2)return values.map(()=>null);
    const min=Math.min(...nums),max=Math.max(...nums);
    if(min===max)return values.map(v=>v===null||v===undefined||v===''?null:'rgb(241,238,226)');
    return values.map(v=>v===null||v===undefined||v===''||!isFinite(+v)?null:heatColor((+v-min)/(max-min)));
  }

  function getPeriodDefs(mode,year){
    if(mode==='quarter')return [
      {name:'1 квартал',months:'январь — март',sm:1,em:3},{name:'2 квартал',months:'апрель — июнь',sm:4,em:6},{name:'3 квартал',months:'июль — сентябрь',sm:7,em:9},{name:'4 квартал',months:'октябрь — декабрь',sm:10,em:12}
    ];
    if(mode==='half')return [{name:'1 полугодие',months:'январь — июнь',sm:1,em:6},{name:'2 полугодие',months:'июль — декабрь',sm:7,em:12}];
    return [{name:String(year)+' год',months:'январь — декабрь',sm:1,em:12}];
  }
  function periodData(def,year){const rows=latestMonthlyRecords(year,def.sm,def.em);return {def,rows,a:aggregate(rows),expected:def.em-def.sm+1}}
  function addGroup(tbody,label,cls,colspan){const tr=document.createElement('tr');tr.className='group-row '+cls;tr.innerHTML=`<td colspan="${colspan}">${label}</td>`;tbody.appendChild(tr)}
  function addSub(tbody,label,cls,colspan){const tr=document.createElement('tr');tr.className='sub-row '+cls;tr.innerHTML=`<td colspan="${colspan}">${label}</td>`;tbody.appendChild(tr)}
  function addMetric(tbody,label,cls,pds,getter,formatter,important=false,key=''){
    const values=pds.map(pd=>pd.a?getter(pd.a,pd.rows):null),styles=heatStyles(values,heatEnabled(label,key));
    const tr=document.createElement('tr');tr.className=cls+(important?' important':'');
    tr.innerHTML='<td>'+label+'</td>'+values.map((v,i)=>{const out=v===null||v===undefined?'<span class="empty">—</span>':formatter(v);const st=styles[i]?` style="background:${styles[i]}"`:'';return `<td class="heat-cell"${st}>${out==='—'?'<span class="empty">—</span>':out}</td>`}).join('');
    tbody.appendChild(tr);
  }

  window.renderPeriod=function(){
    const mode=document.getElementById('mode').value;if(mode==='date')return;
    const year=+document.getElementById('year').value,defs=getPeriodDefs(mode,year),pds=defs.map(d=>periodData(d,year));
    const head=document.getElementById('periodCompareHead'),tbody=document.getElementById('periodCompareBody'),note=document.getElementById('periodSummaryNote');if(!head||!tbody)return;
    document.getElementById('periodTitle').textContent=mode==='quarter'?'Кварталы '+year:mode==='half'?'Полугодия '+year:'Год '+year;
    head.innerHTML='<tr><th>Показатель</th>'+pds.map(pd=>`<th><span class="period-name">${pd.def.name}</span><span class="period-months">${pd.def.months}<br>данных: ${pd.rows.length} из ${pd.expected} мес.</span></th>`).join('')+'</tr>';
    tbody.innerHTML='';const cols=pds.length+1,any=pds.some(x=>x.a);
    note.textContent=any?'Периоды показаны рядом для сравнения. Цветовой градиент применяется только к сопоставимым показателям: меньшее значение красноватое, большее — зеленоватое. Потери, отказы и недошедшие не окрашиваются.':'За выбранный год пока нет сохранённых данных.';
    addGroup(tbody,'Основные показатели','group-general',cols);addSub(tbody,'План и выполнение','sub-general',cols);
    addMetric(tbody,'План','row-general',pds,a=>a.plan,fmtMoney,false,'plan');addMetric(tbody,'Должно быть','row-general',pds,a=>a.due,fmtMoney,false,'due');addMetric(tbody,'Факт медицина','row-general',pds,a=>a.factMedicine,fmtMoney,false,'factMedicine');addMetric(tbody,'Факт лаборатория','row-general',pds,a=>a.factLab,fmtMoney,false,'factLab');addMetric(tbody,'Факт общий','row-general',pds,a=>a.factTotal,fmtMoney,true,'factTotal');addMetric(tbody,'% выполнения','row-general',pds,a=>a.execution,fmtPct,true,'execution');
    addSub(tbody,'Пациенты','sub-general',cols);addMetric(tbody,'Первичные приёмы','row-general',pds,a=>a.primary,fmtCount,false,'primary');addMetric(tbody,'Повторные приёмы','row-general',pds,a=>a.repeat,fmtCount,false,'repeat');addMetric(tbody,'Средний чек медицины','row-general',pds,a=>a.avgCheck,fmtMoney,true,'avgCheck');
    addGroup(tbody,'Стоматология','group-dent',cols);addSub(tbody,'Приёмы','sub-visits-dent',cols);addMetric(tbody,'Первичные','row-visits-dent',pds,a=>a.dentPrimary,fmtCount,false,'dentPrimary');addMetric(tbody,'Повторные','row-visits-dent',pds,a=>a.dentRepeat,fmtCount,false,'dentRepeat');addSub(tbody,'Выручка и экономика','sub-money-dent',cols);addMetric(tbody,'Выручка стоматологии','row-money-dent',pds,a=>a.dentRev,fmtMoney,true,'dentRev');addMetric(tbody,'Средний чек стоматологии','row-money-dent',pds,a=>a.dentAvg,fmtMoney,false,'dentAvg');addSub(tbody,'Выручка по врачам','sub-team-dent',cols);dentists.forEach(name=>addMetric(tbody,name,'row-team-dent',pds,a=>num((a.dentists||{})[name]),fmtMoney,false,'dentist'));
    addGroup(tbody,'Клиника','group-clinic',cols);addSub(tbody,'Приёмы','sub-visits-clinic',cols);addMetric(tbody,'Первичные','row-visits-clinic',pds,a=>a.clinicPrimary,fmtCount,false,'clinicPrimary');addMetric(tbody,'Повторные','row-visits-clinic',pds,a=>a.clinicRepeat,fmtCount,false,'clinicRepeat');addSub(tbody,'Выручка и экономика','sub-money-clinic',cols);addMetric(tbody,'Выручка клиники','row-money-clinic',pds,a=>a.clinicRev,fmtMoney,true,'clinicRev');addMetric(tbody,'Средний чек клиники','row-money-clinic',pds,a=>a.clinicAvg,fmtMoney,false,'clinicAvg');addSub(tbody,'Выручка по специалистам','sub-team-clinic',cols);clinicDocs.forEach(name=>addMetric(tbody,name,'row-team-clinic',pds,a=>num((a.clinicDocs||{})[name]),fmtMoney,false,'clinicDoctor'));
    addGroup(tbody,'Лаборатория','group-lab',cols);addSub(tbody,'Объём','sub-lab-volume',cols);addMetric(tbody,'Количество заказов','row-lab-volume',pds,a=>a.labOrders,fmtCount,false,'labOrders');addSub(tbody,'Выручка и экономика','sub-lab-money',cols);addMetric(tbody,'Выручка лаборатории','row-lab-money',pds,a=>a.labRevenue,fmtMoney,true,'labRevenue');addMetric(tbody,'Средний чек лаборатории','row-lab-money',pds,a=>a.labAvg,fmtMoney,false,'labAvg');
    addGroup(tbody,'Маркетинг','group-marketing',cols);addSub(tbody,'Лиды','sub-marketing-input',cols);addMetric(tbody,'Целевые лиды — стоматология','row-marketing-input',pds,a=>a.leadsDent,fmtCount,false,'leadsDent');addMetric(tbody,'Отказ / мониторинг — стоматология','row-marketing-input',pds,a=>a.leadsDentLost,fmtCount,false,'leadsDentLost');addMetric(tbody,'Целевые лиды — клиника','row-marketing-input',pds,a=>a.leadsClinic,fmtCount,false,'leadsClinic');addMetric(tbody,'Резерв','row-marketing-input',pds,a=>a.leadsReserve,fmtCount,false,'leadsReserve');addMetric(tbody,'Всего лидов','row-marketing-input',pds,a=>a.totalLeads,fmtCount,true,'totalLeads');addSub(tbody,'Конверсия','sub-marketing-conv',cols);addMetric(tbody,'Конверсия стоматология','row-marketing-conv',pds,a=>a.convDent,fmtPct,false,'convDent');addMetric(tbody,'Конверсия клиника','row-marketing-conv',pds,a=>a.convClinic,fmtPct,false,'convClinic');addMetric(tbody,'Общая конверсия','row-marketing-conv',pds,a=>a.totalConv,fmtPct,true,'totalConv');
  };

  const toolbar=document.querySelector('.toolbar'),actions=toolbar?.querySelector('.toolbar-actions'),saveBtn=document.getElementById('saveBtn');
  if(saveBtn)saveBtn.classList.add('hidden');
  const dateViewField=document.createElement('div');dateViewField.className='field date-view-field';dateViewField.id='dateViewField';dateViewField.innerHTML='<label>Просмотр</label><select id="dateViewMode"><option value="single">Одна дата</option><option value="compare">На эту дату по месяцам</option></select>';
  const compareRangeField=document.createElement('div');compareRangeField.className='field compare-range-field hidden';compareRangeField.id='compareRangeField';compareRangeField.innerHTML='<label>Период сравнения</label><select id="compareRange"><option value="ytd">С начала года</option><option value="q1">1 квартал</option><option value="q2">2 квартал</option><option value="q3">3 квартал</option><option value="q4">4 квартал</option><option value="h1">1 полугодие</option><option value="h2">2 полугодие</option><option value="year">Весь год</option></select>';
  if(toolbar&&actions){toolbar.insertBefore(dateViewField,actions);toolbar.insertBefore(compareRangeField,actions)}
  const entryBtn=document.createElement('button');entryBtn.id='entryBtn';entryBtn.className='btn primary entry-btn';entryBtn.textContent='Заполнить данные за день';
  const todayBtn=document.createElement('button');todayBtn.id='todayBtn';todayBtn.className='btn today-btn';todayBtn.textContent='Сегодня';
  if(actions){actions.insertBefore(todayBtn,actions.firstChild);actions.insertBefore(entryBtn,actions.firstChild)}

  const editView=document.getElementById('editView');
  const dateCompareView=document.createElement('div');dateCompareView.id='dateCompareView';dateCompareView.className='hidden';dateCompareView.innerHTML='<div class="date-compare-title"><h2 id="dateCompareTitle">Сравнение на эту дату</h2><div class="muted" id="dateCompareHint"></div></div><div id="dateSummaryNote" class="date-summary-note"></div><div class="date-compare-table"><table><thead id="dateCompareHead"></thead><tbody id="dateCompareBody"></tbody></table></div>';
  if(editView?.parentNode)editView.parentNode.insertBefore(dateCompareView,editView);

  function makeViewOnly(){
    document.querySelectorAll('#editView [data-key]:not([readonly])').forEach(inp=>{inp.readOnly=true;inp.classList.add('view-only')});
    document.querySelectorAll('#editView [data-doctor]').forEach(inp=>{inp.readOnly=true;inp.classList.add('view-only')});
  }
  makeViewOnly();

  const overlay=document.createElement('div');overlay.id='entryOverlay';overlay.className='entry-overlay hidden';overlay.innerHTML=`
    <div class="entry-dialog" role="dialog" aria-modal="true" aria-labelledby="entryTitle">
      <div class="entry-head"><div><h2 id="entryTitle">Заполнить данные за день</h2><div class="muted">Все ручные показатели — в одном окне</div></div><button type="button" class="entry-close" id="entryClose" aria-label="Закрыть">×</button></div>
      <div class="entry-body">
        <div class="entry-top"><div class="entry-field"><label>Дата</label><input type="date" id="entryDate"></div><div id="entryReconcileTop" class="entry-reconcile">Проверка оборота врачей появится после ввода данных.</div></div>
        <div class="entry-columns">
          <div class="entry-stack">
            <section class="entry-card general"><h3>Основные показатели</h3><div class="entry-card-body"><div class="entry-grid" id="entryGeneral"></div></div></section>
            <section class="entry-card lab" style="margin-top:12px"><h3>Лаборатория</h3><div class="entry-card-body"><div class="entry-grid" id="entryLab"></div></div></section>
            <section class="entry-card marketing" style="margin-top:12px"><h3>Маркетинг</h3><div class="entry-card-body"><div class="entry-grid" id="entryMarketing"></div></div></section>
          </div>
          <section class="entry-card dent"><h3>Стоматология</h3><div class="entry-card-body"><div class="entry-grid" id="entryDentSummary"></div><div class="entry-list" id="entryDentDoctors" style="margin-top:10px"></div></div></section>
          <section class="entry-card clinic"><h3>Клиника</h3><div class="entry-card-body"><div class="entry-grid" id="entryClinicSummary"></div><div class="entry-list" id="entryClinicDoctors" style="margin-top:10px"></div></div></section>
        </div>
      </div>
      <div class="entry-foot"><div class="entry-foot-note">Если общая выручка медицины не совпадает с суммой врачей, сохранение возможно только после отдельного подтверждения.</div><div class="entry-foot-actions"><button type="button" class="btn" id="entryCancel">Отмена</button><button type="button" class="btn primary" id="entrySave">Сохранить</button></div></div>
    </div>`;
  document.body.appendChild(overlay);

  const manualDefs={
    general:[['План','plan'],['Факт медицина — общая сумма','factMedicine'],['Факт лаборатория','factLab'],['ПП 2025 на этот день','pp25'],['Средний чек 2025','avg25'],['Первичный приём — всего','primary'],['Повторный приём — всего','repeat']],
    dent:[['Первичные — стоматология','dentPrimary'],['Повторные — стоматология','dentRepeat']],
    clinic:[['Первичные — клиника','clinicPrimary'],['Повторные — клиника','clinicRepeat']],
    lab:[['Количество заказов','labOrders'],['Выручка лаборатории','labRevenue']],
    marketing:[['Стоматология — целевые лиды','leadsDent'],['Отказ / мониторинг','leadsDentLost'],['Клиника — целевые лиды','leadsClinic'],['Резерв','leadsReserve']]
  };
  function inputHtml(label,key){return `<div class="entry-field"><label>${escapeHtml(label)}</label><input inputmode="decimal" data-entry-key="${key}"></div>`}
  document.getElementById('entryGeneral').innerHTML=manualDefs.general.map(x=>inputHtml(...x)).join('');
  document.getElementById('entryDentSummary').innerHTML=manualDefs.dent.map(x=>inputHtml(...x)).join('');
  document.getElementById('entryClinicSummary').innerHTML=manualDefs.clinic.map(x=>inputHtml(...x)).join('');
  document.getElementById('entryLab').innerHTML=manualDefs.lab.map(x=>inputHtml(...x)).join('');
  document.getElementById('entryMarketing').innerHTML=manualDefs.marketing.map(x=>inputHtml(...x)).join('');
  document.getElementById('entryDentDoctors').innerHTML=dentists.map(name=>`<div class="entry-doctor"><span>${escapeHtml(name)}</span><input inputmode="decimal" data-entry-doctor-type="dentists" data-entry-doctor="${escapeHtml(name)}" aria-label="Выручка ${escapeHtml(name)}"></div>`).join('');
  document.getElementById('entryClinicDoctors').innerHTML=clinicDocs.map(name=>`<div class="entry-doctor"><span>${escapeHtml(name)}</span><input inputmode="decimal" data-entry-doctor-type="clinicDocs" data-entry-doctor="${escapeHtml(name)}" aria-label="Выручка ${escapeHtml(name)}"></div>`).join('');

  function doctorReconciliation(r){
    const doctorValues=[...Object.values(r.dentists||{}),...Object.values(r.clinicDocs||{})],hasDoctors=doctorValues.some(v=>!isBlankValue(v)),hasTotal=!isBlankValue(r.factMedicine);
    const doctors=doctorValues.reduce((a,v)=>a+num(v),0),total=num(r.factMedicine),delta=total-doctors;
    return {hasData:hasDoctors||hasTotal,doctors,total,delta,mismatch:(hasDoctors||hasTotal)&&Math.abs(delta)>.01};
  }
  function updateEntryReconcile(){
    const total=document.querySelector('[data-entry-key="factMedicine"]')?.value||'';
    const r={factMedicine:total,dentists:{},clinicDocs:{}};document.querySelectorAll('[data-entry-doctor]').forEach(i=>r[i.dataset.entryDoctorType][i.dataset.entryDoctor]=i.value);
    const c=doctorReconciliation(r),box=document.getElementById('entryReconcileTop');if(!box)return;
    if(!c.hasData){box.className='entry-reconcile';box.textContent='Проверка оборота врачей появится после ввода данных.';return}
    if(c.mismatch){box.className='entry-reconcile bad';box.textContent=`Сумма врачей ${money(c.doctors)} не совпадает с общей выручкой ${money(c.total)}. Расхождение: ${money(Math.abs(c.delta))} (${c.delta>0?'общая сумма выше':'оборот врачей выше'}).`;}
    else{box.className='entry-reconcile';box.textContent=`Проверка пройдена: общая выручка совпадает с суммой врачей — ${money(c.total)}.`}
  }
  function openEntry(){
    const date=document.getElementById('reportDate').value;if(!date)return;
    const store=loadStore(),r=store[date]||blankRecord(date);document.getElementById('entryDate').value=date;
    document.querySelectorAll('[data-entry-key]').forEach(i=>i.value=r[i.dataset.entryKey]??'');
    document.querySelectorAll('[data-entry-doctor]').forEach(i=>i.value=(r[i.dataset.entryDoctorType]||{})[i.dataset.entryDoctor]??'');
    updateEntryReconcile();overlay.classList.remove('hidden');document.body.style.overflow='hidden';
  }
  function closeEntry(){overlay.classList.add('hidden');document.body.style.overflow=''}
  function saveEntry(){
    const date=document.getElementById('entryDate').value;if(!date)return;
    const store=loadStore(),existing=store[date]||blankRecord(date),r={...existing,date,dentists:{...(existing.dentists||{})},clinicDocs:{...(existing.clinicDocs||{})}};
    document.querySelectorAll('[data-entry-key]').forEach(i=>r[i.dataset.entryKey]=i.value.trim());
    document.querySelectorAll('[data-entry-doctor]').forEach(i=>r[i.dataset.entryDoctorType][i.dataset.entryDoctor]=i.value.trim());
    const c=doctorReconciliation(r);
    if(c.mismatch){
      const ok=confirm(`Введённая общая выручка медицины не совпадает с суммой врачей.\n\nОбщая сумма: ${money(c.total)}\nСумма врачей: ${money(c.doctors)}\nРасхождение: ${money(Math.abs(c.delta))}\n\nСохранить запись с расхождением?`);
      if(!ok)return;
      r._doctorRevenueMismatch=true;r._doctorRevenueDelta=c.delta;r._doctorRevenueTotal=c.doctors;
    }else{r._doctorRevenueMismatch=false;r._doctorRevenueDelta=0;r._doctorRevenueTotal=c.doctors}
    store[date]=r;saveStore(store);document.getElementById('reportDate').value=date;fillRecord(r);renderMismatchNote(r);document.getElementById('status').textContent='Сохранено в этом браузере: '+new Date(date+'T12:00:00').toLocaleString('ru-RU');closeEntry();if(document.getElementById('dateViewMode').value==='compare')renderDateComparison();
  }
  function renderMismatchNote(r){
    const section=document.querySelector('#editView .section.general');if(!section)return;let note=section.querySelector('.reconcile-note');
    if(r?._doctorRevenueMismatch){if(!note){note=document.createElement('div');note.className='reconcile-note';section.querySelector('h2')?.insertAdjacentElement('afterend',note)}const delta=Math.abs(num(r._doctorRevenueDelta));note.textContent=`⚠ Общая сумма медицины не совпадает с оборотом врачей. Расхождение: ${money(delta)}.`;}
    else if(note)note.remove();
  }
  function currentRecord(){const date=document.getElementById('reportDate').value;return loadStore()[date]||null}
  entryBtn.addEventListener('click',openEntry);todayBtn.addEventListener('click',()=>{const d=new Date(),date=[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');document.getElementById('reportDate').value=date;loadDate();renderMismatchNote(currentRecord());if(document.getElementById('dateViewMode').value==='compare')renderDateComparison()});
  document.getElementById('entryClose').addEventListener('click',closeEntry);document.getElementById('entryCancel').addEventListener('click',closeEntry);document.getElementById('entrySave').addEventListener('click',saveEntry);overlay.addEventListener('click',e=>{if(e.target===overlay)closeEntry()});document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!overlay.classList.contains('hidden'))closeEntry()});overlay.addEventListener('input',e=>{if(e.target.matches('[data-entry-key="factMedicine"],[data-entry-doctor]'))updateEntryReconcile()});

  const monthNames=['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек'];
  function clampDate(year,month,day){const last=new Date(year,month,0).getDate(),d=Math.min(day,last);return `${year}-${String(month).padStart(2,'0')}-${String(d).padStart(2,'0')}`}
  function compareMonths(range,selectedMonth){const defs={q1:[1,3],q2:[4,6],q3:[7,9],q4:[10,12],h1:[1,6],h2:[7,12],year:[1,12],ytd:[1,selectedMonth]},r=defs[range]||defs.ytd;return Array.from({length:r[1]-r[0]+1},(_,i)=>r[0]+i)}
  function rawOrNull(v){return isBlankValue(v)||!isFinite(+v)?null:+v}
  function addDateMetric(tbody,label,cls,months,getter,formatter,key='',important=false){
    const values=months.map(m=>m.r?getter(m.d,m.r):null),styles=heatStyles(values,heatEnabled(label,key));
    const tr=document.createElement('tr');tr.className=cls+(important?' important':'');tr.innerHTML='<td>'+label+'</td>'+values.map((v,i)=>{const out=v===null?'<span class="empty">—</span>':formatter(v),st=styles[i]?` style="background:${styles[i]}"`:'';return `<td class="heat-cell"${st}>${out}</td>`}).join('');tbody.appendChild(tr);
  }
  function renderDateComparison(){
    const date=document.getElementById('reportDate').value;if(!date)return;const base=new Date(date+'T12:00:00'),year=base.getFullYear(),day=base.getDate(),selectedMonth=base.getMonth()+1,range=document.getElementById('compareRange').value,store=loadStore();
    const months=compareMonths(range,selectedMonth).map(month=>{const target=clampDate(year,month,day),r=store[target]||null;return {month,target,r,d:r?derive(r):null}});
    const head=document.getElementById('dateCompareHead'),tbody=document.getElementById('dateCompareBody'),note=document.getElementById('dateSummaryNote');if(!head||!tbody)return;
    document.getElementById('dateCompareTitle').textContent=`На ${day} число по месяцам ${year}`;document.getElementById('dateCompareHint').textContent='Если такого числа в месяце нет, используется последний календарный день месяца.';
    head.innerHTML='<tr><th>Показатель</th>'+months.map(m=>`<th><span class="period-name">${monthNames[m.month-1]}</span><span class="period-months">${new Date(m.target+'T12:00:00').toLocaleDateString('ru-RU')}<br>${m.r?'есть данные':'нет данных'}</span></th>`).join('')+'</tr>';tbody.innerHTML='';
    note.textContent='Градиент применяется внутри каждой строки по видимым месяцам: меньшее значение красноватое, большее — зеленоватое. Потери, отказы и недошедшие не окрашиваются.';const cols=months.length+1;
    addGroup(tbody,'Основные показатели','group-general',cols);addSub(tbody,'План и выполнение','sub-general',cols);addDateMetric(tbody,'План','row-general',months,(d,r)=>rawOrNull(r.plan),fmtMoney,'plan');addDateMetric(tbody,'Должно быть','row-general',months,d=>rawOrNull(d.due),fmtMoney,'due');addDateMetric(tbody,'Факт медицина','row-general',months,(d,r)=>rawOrNull(r.factMedicine),fmtMoney,'factMedicine');addDateMetric(tbody,'Факт лаборатория','row-general',months,(d,r)=>rawOrNull(r.factLab),fmtMoney,'factLab');addDateMetric(tbody,'Факт общий','row-general',months,d=>rawOrNull(d.factTotal),fmtMoney,'factTotal',true);addDateMetric(tbody,'% выполнения','row-general',months,d=>rawOrNull(d.execution),fmtPct,'execution',true);addDateMetric(tbody,'ПП 2025 на этот день','row-general',months,(d,r)=>rawOrNull(r.pp25),fmtCount,'pp25');addDateMetric(tbody,'Средний чек 2025','row-general',months,(d,r)=>rawOrNull(r.avg25),fmtMoney,'avg25');addSub(tbody,'Пациенты','sub-general',cols);addDateMetric(tbody,'Первичные приёмы','row-general',months,(d,r)=>rawOrNull(r.primary),fmtCount,'primary');addDateMetric(tbody,'Повторные приёмы','row-general',months,(d,r)=>rawOrNull(r.repeat),fmtCount,'repeat');addDateMetric(tbody,'ПП в день','row-general',months,d=>rawOrNull(d.ppDay),fmtCount,'ppDay');addDateMetric(tbody,'Средний чек медицины','row-general',months,d=>rawOrNull(d.avgCheck),fmtMoney,'avgCheck',true);
    addGroup(tbody,'Стоматология','group-dent',cols);addSub(tbody,'Приёмы','sub-visits-dent',cols);addDateMetric(tbody,'Первичные','row-visits-dent',months,(d,r)=>rawOrNull(r.dentPrimary),fmtCount,'dentPrimary');addDateMetric(tbody,'Повторные','row-visits-dent',months,(d,r)=>rawOrNull(r.dentRepeat),fmtCount,'dentRepeat');addDateMetric(tbody,'ПП в день','row-visits-dent',months,d=>rawOrNull(d.dentPPDay),fmtCount,'dentPPDay');addSub(tbody,'Выручка и экономика','sub-money-dent',cols);addDateMetric(tbody,'Выручка стоматологии','row-money-dent',months,d=>rawOrNull(d.dentRev),fmtMoney,'dentRev',true);addDateMetric(tbody,'Средний чек стоматологии','row-money-dent',months,d=>rawOrNull(d.dentAvg),fmtMoney,'dentAvg');addSub(tbody,'Выручка по врачам','sub-team-dent',cols);dentists.forEach(name=>addDateMetric(tbody,name,'row-team-dent',months,(d,r)=>rawOrNull((r.dentists||{})[name]),fmtMoney,'dentist'));
    addGroup(tbody,'Клиника','group-clinic',cols);addSub(tbody,'Приёмы','sub-visits-clinic',cols);addDateMetric(tbody,'Первичные','row-visits-clinic',months,(d,r)=>rawOrNull(r.clinicPrimary),fmtCount,'clinicPrimary');addDateMetric(tbody,'Повторные','row-visits-clinic',months,(d,r)=>rawOrNull(r.clinicRepeat),fmtCount,'clinicRepeat');addDateMetric(tbody,'ПП в день','row-visits-clinic',months,d=>rawOrNull(d.clinicPPDay),fmtCount,'clinicPPDay');addSub(tbody,'Выручка и экономика','sub-money-clinic',cols);addDateMetric(tbody,'Выручка клиники','row-money-clinic',months,d=>rawOrNull(d.clinicRev),fmtMoney,'clinicRev',true);addDateMetric(tbody,'Средний чек клиники','row-money-clinic',months,d=>rawOrNull(d.clinicAvg),fmtMoney,'clinicAvg');addSub(tbody,'Выручка по специалистам','sub-team-clinic',cols);clinicDocs.forEach(name=>addDateMetric(tbody,name,'row-team-clinic',months,(d,r)=>rawOrNull((r.clinicDocs||{})[name]),fmtMoney,'clinicDoctor'));
    addGroup(tbody,'Лаборатория','group-lab',cols);addSub(tbody,'Объём','sub-lab-volume',cols);addDateMetric(tbody,'Количество заказов','row-lab-volume',months,(d,r)=>rawOrNull(r.labOrders),fmtCount,'labOrders');addSub(tbody,'Выручка и экономика','sub-lab-money',cols);addDateMetric(tbody,'Выручка лаборатории','row-lab-money',months,(d,r)=>rawOrNull(r.labRevenue||r.factLab),fmtMoney,'labRevenue',true);addDateMetric(tbody,'Средний чек лаборатории','row-lab-money',months,d=>rawOrNull(d.labAvg),fmtMoney,'labAvg');
    addGroup(tbody,'Маркетинг','group-marketing',cols);addSub(tbody,'Лиды','sub-marketing-input',cols);addDateMetric(tbody,'Целевые лиды — стоматология','row-marketing-input',months,(d,r)=>rawOrNull(r.leadsDent),fmtCount,'leadsDent');addDateMetric(tbody,'Отказ / мониторинг — стоматология','row-marketing-input',months,(d,r)=>rawOrNull(r.leadsDentLost),fmtCount,'leadsDentLost');addDateMetric(tbody,'Целевые лиды — клиника','row-marketing-input',months,(d,r)=>rawOrNull(r.leadsClinic),fmtCount,'leadsClinic');addDateMetric(tbody,'Резерв','row-marketing-input',months,(d,r)=>rawOrNull(r.leadsReserve),fmtCount,'leadsReserve');addDateMetric(tbody,'Всего лидов','row-marketing-input',months,d=>rawOrNull(d.totalLeads),fmtCount,'totalLeads',true);addSub(tbody,'Конверсия','sub-marketing-conv',cols);addDateMetric(tbody,'Конверсия стоматология','row-marketing-conv',months,d=>rawOrNull(d.convDent),fmtPct,'convDent');addDateMetric(tbody,'Конверсия клиника','row-marketing-conv',months,d=>rawOrNull(d.convClinic),fmtPct,'convClinic');addDateMetric(tbody,'Общая конверсия','row-marketing-conv',months,d=>rawOrNull(d.totalConv),fmtPct,'totalConv',true);
  }

  function syncDateUx(){
    const mode=document.getElementById('mode').value,isDate=mode==='date',compare=document.getElementById('dateViewMode').value==='compare';
    dateViewField.classList.toggle('hidden',!isDate);compareRangeField.classList.toggle('hidden',!isDate||!compare);entryBtn.classList.toggle('hidden',!isDate);todayBtn.classList.toggle('hidden',!isDate);
    if(isDate){periodView?.classList.add('hidden');dateCompareView.classList.toggle('hidden',!compare);editView?.classList.toggle('hidden',compare);if(compare)renderDateComparison();else{editView?.classList.remove('hidden');renderMismatchNote(currentRecord())}}
    else{dateCompareView.classList.add('hidden');editView?.classList.add('hidden')}
  }
  document.getElementById('dateViewMode').addEventListener('change',syncDateUx);document.getElementById('compareRange').addEventListener('change',renderDateComparison);
  document.getElementById('reportDate').addEventListener('change',()=>{setTimeout(()=>{renderMismatchNote(currentRecord());if(document.getElementById('dateViewMode').value==='compare')renderDateComparison()},0)});
  document.getElementById('mode').addEventListener('change',()=>setTimeout(syncDateUx,0));document.getElementById('deleteBtn')?.addEventListener('click',()=>setTimeout(()=>renderMismatchNote(currentRecord()),0));document.getElementById('loginForm')?.addEventListener('submit',()=>setTimeout(()=>renderMismatchNote(currentRecord()),450));
  document.getElementById('quarter')?.addEventListener('change',()=>window.renderPeriod());document.getElementById('half')?.addEventListener('change',()=>window.renderPeriod());
  setTimeout(()=>{makeViewOnly();renderMismatchNote(currentRecord());syncDateUx()},0);
})();
