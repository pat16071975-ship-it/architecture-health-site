(()=>{
  const monthNames=['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек'];
  const additiveKeys=['factMedicine','factLab','primary','repeat','dentPrimary','dentRepeat','clinicPrimary','clinicRepeat','labOrders','leadsDent','leadsDentLost','leadsClinic','leadsReserve'];
  const blank=v=>v===''||v===null||v===undefined;
  const raw=v=>blank(v)||!isFinite(+v)?null:+v;
  const fmtMoney=v=>v===null||v===undefined?'—':money(v);
  const fmtCount=v=>v===null||v===undefined?'—':count(v);
  const fmtPct=v=>v===null||v===undefined?'—':pct(v);

  function mix(a,b,t){return Math.round(a+(b-a)*t)}
  function heatColor(t){
    const red=[244,215,210],yellow=[245,239,204],green=[216,235,217];
    const x=Math.max(0,Math.min(1,t));
    const from=x<.5?red:yellow,to=x<.5?yellow:green,u=x<.5?x*2:(x-.5)*2;
    return `rgb(${mix(from[0],to[0],u)},${mix(from[1],to[1],u)},${mix(from[2],to[2],u)})`;
  }
  function heatEnabled(label,key){return !/(потер|отказ|недош|не дош|lost|reject|noshow|no_show)/i.test((label+' '+(key||'')).toLowerCase())}
  function heatStyles(values,enabled=true){
    const nums=values.filter(v=>v!==null&&v!==undefined&&v!==''&&isFinite(+v)).map(Number);
    if(!enabled||nums.length<2)return values.map(()=>null);
    const min=Math.min(...nums),max=Math.max(...nums);
    if(min===max)return values.map(v=>v===null||v===undefined||v===''?null:'rgb(241,238,226)');
    return values.map(v=>v===null||v===undefined||v===''||!isFinite(+v)?null:heatColor((+v-min)/(max-min)));
  }
  function clampDate(year,month,day){const last=new Date(year,month,0).getDate(),d=Math.min(day,last);return `${year}-${String(month).padStart(2,'0')}-${String(d).padStart(2,'0')}`}
  function compareMonths(range,selectedMonth){const defs={q1:[1,3],q2:[4,6],q3:[7,9],q4:[10,12],h1:[1,6],h2:[7,12],year:[1,12],ytd:[1,selectedMonth]},r=defs[range]||defs.ytd;return Array.from({length:r[1]-r[0]+1},(_,i)=>r[0]+i)}
  function latestNonBlank(rows,key){for(let i=rows.length-1;i>=0;i--){if(!blank(rows[i]?.[key]))return rows[i][key]}return ''}
  function rowsThrough(store,year,month,target){
    const prefix=`${year}-${String(month).padStart(2,'0')}-`;
    return Object.values(store).filter(r=>r&&typeof r==='object'&&String(r.date||'').startsWith(prefix)&&r.date<=target).sort((a,b)=>a.date.localeCompare(b.date));
  }
  function monthToDate(store,year,month,day){
    const target=clampDate(year,month,day),rows=rowsThrough(store,year,month,target);
    if(!rows.length)return {month,target,r:null,d:null,rows:[],coverage:null,mode:'none'};
    const coverage=rows[rows.length-1].date;
    const hasCumulative=rows.some(r=>r._aggregation==='month_to_date');
    let r;
    if(hasCumulative){
      const source=rows[rows.length-1];
      r={...source,dentists:{...(source.dentists||{})},clinicDocs:{...(source.clinicDocs||{})}};
    }else{
      r=blankRecord(target);
      r.plan=latestNonBlank(rows,'plan');
      r.pp25=latestNonBlank(rows,'pp25');
      r.avg25=latestNonBlank(rows,'avg25');
      additiveKeys.forEach(key=>{r[key]=rows.reduce((sum,row)=>sum+num(row[key]),0)});
      r.labRevenue=rows.reduce((sum,row)=>sum+num(blank(row.labRevenue)?row.factLab:row.labRevenue),0);
      dentists.forEach(name=>{r.dentists[name]=rows.reduce((sum,row)=>sum+num((row.dentists||{})[name]),0)});
      clinicDocs.forEach(name=>{r.clinicDocs[name]=rows.reduce((sum,row)=>sum+num((row.clinicDocs||{})[name]),0)});
      r._aggregation='compare_month_to_date';
    }
    return {month,target,r,d:derive(r),rows,coverage,mode:hasCumulative?'snapshot':'summed'};
  }
  function addGroup(tbody,label,cls,colspan){const tr=document.createElement('tr');tr.className='group-row '+cls;tr.innerHTML=`<td colspan="${colspan}">${label}</td>`;tbody.appendChild(tr)}
  function addSub(tbody,label,cls,colspan){const tr=document.createElement('tr');tr.className='sub-row '+cls;tr.innerHTML=`<td colspan="${colspan}">${label}</td>`;tbody.appendChild(tr)}
  function addMetric(tbody,label,cls,months,getter,formatter,key='',important=false){
    const values=months.map(m=>m.r?getter(m.d,m.r):null),styles=heatStyles(values,heatEnabled(label,key));
    const tr=document.createElement('tr');tr.className=cls+(important?' important':'');
    tr.innerHTML='<td>'+label+'</td>'+values.map((v,i)=>{const out=v===null?'<span class="empty">—</span>':formatter(v),st=styles[i]?` style="background:${styles[i]}"`:'';return `<td class="heat-cell"${st}>${out}</td>`}).join('');
    tbody.appendChild(tr);
  }
  function render(){
    const mode=document.getElementById('mode')?.value,dateView=document.getElementById('dateViewMode')?.value;
    if(mode!=='date'||dateView!=='compare')return;
    const date=document.getElementById('reportDate')?.value;if(!date)return;
    const base=new Date(date+'T12:00:00'),year=base.getFullYear(),day=base.getDate(),selectedMonth=base.getMonth()+1,range=document.getElementById('compareRange')?.value||'ytd',store=loadStore();
    const months=compareMonths(range,selectedMonth).map(month=>monthToDate(store,year,month,day));
    const head=document.getElementById('dateCompareHead'),tbody=document.getElementById('dateCompareBody'),note=document.getElementById('dateSummaryNote');if(!head||!tbody)return;
    const title=document.getElementById('dateCompareTitle'),hint=document.getElementById('dateCompareHint');
    if(title)title.textContent=`На ${day} число по месяцам ${year}`;
    if(hint)hint.textContent='Каждый месяц считается накопительно с 1 числа по выбранное число включительно.';
    head.innerHTML='<tr><th>Показатель</th>'+months.map(m=>{
      if(!m.r)return `<th><span class="period-name">${monthNames[m.month-1]}</span><span class="period-months">до ${new Date(m.target+'T12:00:00').toLocaleDateString('ru-RU')}<br>нет данных</span></th>`;
      const coverage=new Date(m.coverage+'T12:00:00').toLocaleDateString('ru-RU');
      const modeText=m.mode==='snapshot'?'накопительный срез':`сумма ${m.rows.length} дн.`;
      return `<th><span class="period-name">${monthNames[m.month-1]}</span><span class="period-months">01–${new Date(m.target+'T12:00:00').toLocaleDateString('ru-RU')}<br>${modeText}; данные по ${coverage}</span></th>`;
    }).join('')+'</tr>';
    tbody.innerHTML='';
    note.textContent='Факт, приёмы, выручка врачей, лаборатория и лиды считаются накопительно с 1 числа месяца. Средние чеки, ПП в день, выполнение и конверсии пересчитываются из накопленных итогов, а не складываются.';
    const cols=months.length+1;
    addGroup(tbody,'Основные показатели','group-general',cols);addSub(tbody,'План и выполнение','sub-general',cols);addMetric(tbody,'План','row-general',months,(d,r)=>raw(r.plan),fmtMoney,'plan');addMetric(tbody,'Должно быть','row-general',months,d=>raw(d.due),fmtMoney,'due');addMetric(tbody,'Факт медицина','row-general',months,(d,r)=>raw(r.factMedicine),fmtMoney,'factMedicine');addMetric(tbody,'Факт лаборатория','row-general',months,(d,r)=>raw(r.factLab),fmtMoney,'factLab');addMetric(tbody,'Факт общий','row-general',months,d=>raw(d.factTotal),fmtMoney,'factTotal',true);addMetric(tbody,'% выполнения','row-general',months,d=>raw(d.execution),fmtPct,'execution',true);addMetric(tbody,'ПП 2025 на этот день','row-general',months,(d,r)=>raw(r.pp25),fmtCount,'pp25');addMetric(tbody,'Средний чек 2025','row-general',months,(d,r)=>raw(r.avg25),fmtMoney,'avg25');addSub(tbody,'Пациенты','sub-general',cols);addMetric(tbody,'Первичные приёмы','row-general',months,(d,r)=>raw(r.primary),fmtCount,'primary');addMetric(tbody,'Повторные приёмы','row-general',months,(d,r)=>raw(r.repeat),fmtCount,'repeat');addMetric(tbody,'ПП в день','row-general',months,d=>raw(d.ppDay),fmtCount,'ppDay');addMetric(tbody,'Средний чек медицины','row-general',months,d=>raw(d.avgCheck),fmtMoney,'avgCheck',true);
    addGroup(tbody,'Стоматология','group-dent',cols);addSub(tbody,'Приёмы','sub-visits-dent',cols);addMetric(tbody,'Первичные','row-visits-dent',months,(d,r)=>raw(r.dentPrimary),fmtCount,'dentPrimary');addMetric(tbody,'Повторные','row-visits-dent',months,(d,r)=>raw(r.dentRepeat),fmtCount,'dentRepeat');addMetric(tbody,'ПП в день','row-visits-dent',months,d=>raw(d.dentPPDay),fmtCount,'dentPPDay');addSub(tbody,'Выручка и экономика','sub-money-dent',cols);addMetric(tbody,'Выручка стоматологии','row-money-dent',months,d=>raw(d.dentRev),fmtMoney,'dentRev',true);addMetric(tbody,'Средний чек стоматологии','row-money-dent',months,d=>raw(d.dentAvg),fmtMoney,'dentAvg');addSub(tbody,'Выручка по врачам','sub-team-dent',cols);dentists.forEach(name=>addMetric(tbody,name,'row-team-dent',months,(d,r)=>raw((r.dentists||{})[name]),fmtMoney,'dentist'));
    addGroup(tbody,'Клиника','group-clinic',cols);addSub(tbody,'Приёмы','sub-visits-clinic',cols);addMetric(tbody,'Первичные','row-visits-clinic',months,(d,r)=>raw(r.clinicPrimary),fmtCount,'clinicPrimary');addMetric(tbody,'Повторные','row-visits-clinic',months,(d,r)=>raw(r.clinicRepeat),fmtCount,'clinicRepeat');addMetric(tbody,'ПП в день','row-visits-clinic',months,d=>raw(d.clinicPPDay),fmtCount,'clinicPPDay');addSub(tbody,'Выручка и экономика','sub-money-clinic',cols);addMetric(tbody,'Выручка клиники','row-money-clinic',months,d=>raw(d.clinicRev),fmtMoney,'clinicRev',true);addMetric(tbody,'Средний чек клиники','row-money-clinic',months,d=>raw(d.clinicAvg),fmtMoney,'clinicAvg');addSub(tbody,'Выручка по специалистам','sub-team-clinic',cols);clinicDocs.forEach(name=>addMetric(tbody,name,'row-team-clinic',months,(d,r)=>raw((r.clinicDocs||{})[name]),fmtMoney,'clinicDoctor'));
    addGroup(tbody,'Лаборатория','group-lab',cols);addSub(tbody,'Объём','sub-lab-volume',cols);addMetric(tbody,'Количество заказов','row-lab-volume',months,(d,r)=>raw(r.labOrders),fmtCount,'labOrders');addSub(tbody,'Выручка и экономика','sub-lab-money',cols);addMetric(tbody,'Выручка лаборатории','row-lab-money',months,(d,r)=>raw(r.labRevenue||r.factLab),fmtMoney,'labRevenue',true);addMetric(tbody,'Средний чек лаборатории','row-lab-money',months,d=>raw(d.labAvg),fmtMoney,'labAvg');
    addGroup(tbody,'Маркетинг','group-marketing',cols);addSub(tbody,'Лиды','sub-marketing-input',cols);addMetric(tbody,'Целевые лиды — стоматология','row-marketing-input',months,(d,r)=>raw(r.leadsDent),fmtCount,'leadsDent');addMetric(tbody,'Отказ / мониторинг — стоматология','row-marketing-input',months,(d,r)=>raw(r.leadsDentLost),fmtCount,'leadsDentLost');addMetric(tbody,'Целевые лиды — клиника','row-marketing-input',months,(d,r)=>raw(r.leadsClinic),fmtCount,'leadsClinic');addMetric(tbody,'Резерв','row-marketing-input',months,(d,r)=>raw(r.leadsReserve),fmtCount,'leadsReserve');addMetric(tbody,'Всего лидов','row-marketing-input',months,d=>raw(d.totalLeads),fmtCount,'totalLeads',true);addSub(tbody,'Конверсия','sub-marketing-conv',cols);addMetric(tbody,'Конверсия стоматология','row-marketing-conv',months,d=>raw(d.convDent),fmtPct,'convDent');addMetric(tbody,'Конверсия клиника','row-marketing-conv',months,d=>raw(d.convClinic),fmtPct,'convClinic');addMetric(tbody,'Общая конверсия','row-marketing-conv',months,d=>raw(d.totalConv),fmtPct,'totalConv',true);
  }
  function schedule(){setTimeout(render,0)}
  ['dateViewMode','compareRange','reportDate','mode','year'].forEach(id=>document.getElementById(id)?.addEventListener('change',schedule));
  document.getElementById('entrySave')?.addEventListener('click',()=>setTimeout(render,30));
  setTimeout(render,0);
})();