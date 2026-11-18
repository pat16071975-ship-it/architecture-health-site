/* Doctor payments and services keep separate sources and exact periods. */
(()=>{
  'use strict';
  const validDate=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&
    Number.isFinite(Date.parse(value+'T12:00:00Z'))&&new Date(value+'T12:00:00Z').toISOString().slice(0,10)===value;
  const nextDay=value=>{const d=new Date(value+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+1);return d.toISOString().slice(0,10)};
  const cents=value=>value===null||value===undefined||value===''||typeof value==='boolean'||!Number.isFinite(Number(value))?null:Math.round(Number(value)*100);
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money=value=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(value)+' ₽';
  const dateLabel=value=>value.split('-').reverse().join('.');

  function calculator(snapshots={},periods=[],days=[],options={}){
    const ranges=new Map(),byDay=new Map(),cache=new Map();
    const add=p=>{if(p&&validDate(p.from)&&validDate(p.to)&&p.from<=p.to)ranges.set(p.from+'/'+p.to,p)};
    Object.entries(snapshots||{}).forEach(([month,rows])=>{
      if(Array.isArray(rows))rows.forEach(row=>{if(row&&String(row.asOf).slice(0,7)===month)add({...row,from:month+'-01',to:row.asOf})});
    });
    // Exact period imports supersede a legacy snapshot of the same range.
    if(Array.isArray(periods))periods.forEach(add);
    if(Array.isArray(days))days.forEach(day=>{if(day&&validDate(day.date))byDay.set(day.date,day)});
    function coverage(from,to){
      const key=from+'/'+to;
      if(cache.has(key))return cache.get(key);
      if(!validDate(from)||!validDate(to)||from>to)return {paid:null,services:null};
      const byStart=new Map();
      [...ranges.values()].filter(p=>p.from>=from&&p.to<=to).sort((a,b)=>b.to.localeCompare(a.to)).forEach(p=>{
        if(!byStart.has(p.from))byStart.set(p.from,[]);byStart.get(p.from).push(p);
      });
      const memo=new Map();
      function cover(start){
        if(start>to)return [];
        if(memo.has(start))return memo.get(start);
        for(const p of byStart.get(start)||[]){const tail=cover(nextDay(p.to));if(tail!==null){const result=[p,...tail];memo.set(start,result);return result}}
        memo.set(start,null);return null;
      }
      // Imports contain transaction dates, not a calendar of operating days.
      // Sum existing records once; never manufacture a zero row for an absent day.
      let services=[...byDay.values()].filter(day=>day.date>=from&&day.date<=to);
      const months=new Set(services.map(day=>day.date.slice(0,7)));
      const firstMonth=from.slice(0,7),lastMonth=to.slice(0,7);
      for(let month=firstMonth;month<=lastMonth;){
        if(!months.has(month)){services=null;break}
        const d=new Date(month+'-01T12:00:00Z');d.setUTCMonth(d.getUTCMonth()+1);
        month=d.toISOString().slice(0,7);
      }
      if(services&&services.some(day=>day.complete!==true))services=null;
      let paid=cover(from),loaded=false;
      // Calendar-edge gaps in archived monthly exports are not zero payments.
      // Expose a separate, explicitly labelled sum of uploaded periods. Never
      // bridge an internal gap, skip a month or use a source after the cutoff.
      if(paid===null&&options.loadedPeriods){
        const selected=[];
        for(let month=firstMonth;month<=lastMonth;){
          const monthEnd=new Date(Date.UTC(+month.slice(0,4),+month.slice(5,7),0)).toISOString().slice(0,10);
          const candidates=[...ranges.values()].filter(p=>p.from>=from&&p.to<=to&&p.from.slice(0,7)===month&&p.to.slice(0,7)===month);
          if(!candidates.length){selected.length=0;break}
          const begin=candidates.map(p=>p.from).sort()[0],end=candidates.map(p=>p.to).sort().at(-1);
          if(month===lastMonth&&end!==to){selected.length=0;break}
          const seen=new Map();
          function chain(at){
            if(at>end)return [];
            if(seen.has(at))return seen.get(at);
            for(const p of candidates.filter(p=>p.from===at).sort((a,b)=>b.to.localeCompare(a.to))){
              const tail=chain(nextDay(p.to));if(tail!==null){const out=[p,...tail];seen.set(at,out);return out}
            }
            seen.set(at,null);return null;
          }
          const parts=chain(begin);
          if(parts===null){selected.length=0;break}
          selected.push(...parts);
          month=nextDay(monthEnd).slice(0,7);
        }
        if(selected.length){paid=selected;loaded=true}
      }
      const result={paid,services,loaded};cache.set(key,result);return result;
    }
    function sum(parts,kind,name){
      if(parts===null||!parts.length)return null;
      let total=0;
      for(const part of parts){
        const values=part[kind];
        if(!values||typeof values!=='object'||Array.isArray(values))return null;
        const value=Object.prototype.hasOwnProperty.call(values,name)?cents(values[name]):0;
        if(value===null||!Number.isSafeInteger(value))return null;
        total+=value;
        if(!Number.isSafeInteger(total))return null;
      }
      return total/100;
    }
    return (from,to,kind,name)=>{
      const parts=coverage(from,to),paid=sum(parts.paid,kind,name),services=sum(parts.services,kind,name);
      const result={paid,services,balance:paid===null||services===null?null:(Math.round(paid*100)-Math.round(services*100))/100};
      if(options.loadedPeriods){result.loadedPeriods=!!parts.loaded;result.paymentPeriods=(parts.paid||[]).map(p=>({from:p.from,to:p.to}))}
      return result;
    };
  }

  function loadedBounds(month,snapshots={},periods=[],days=[],records={}){
    const dates=[...Object.keys(records||{}),...(Array.isArray(days)?days.map(d=>d?.date):[]),...(Array.isArray(periods)?periods.map(p=>p?.to):[])];
    Object.values(snapshots||{}).forEach(rows=>{if(Array.isArray(rows))rows.forEach(r=>dates.push(r?.asOf))});
    const end=dates.filter(validDate).sort().at(-1)||null;
    const m=Number(month);
    return {start:end&&Number.isInteger(m)&&m>=1&&m<=12?end.slice(0,4)+'-'+String(m).padStart(2,'0')+'-01':null,end};
  }

  function render({head,tbody,columns,date,year}){
    // The standalone legacy report has no server source contract. Preserve it.
    if(typeof DOCTOR_SERVICE_DAYS==='undefined')return;
    if(!head||!tbody||!Array.isArray(columns))return;
    const {start,end}=loadedBounds(document.getElementById('doctorBalanceMonth')?.value||1,
      typeof DOCTOR_PAID_SNAPSHOTS==='undefined'?{}:DOCTOR_PAID_SNAPSHOTS,
      typeof DOCTOR_PAID_PERIODS==='undefined'?[]:DOCTOR_PAID_PERIODS,
      typeof DOCTOR_SERVICE_DAYS==='undefined'?[]:DOCTOR_SERVICE_DAYS,
      typeof SERVER_STORE==='undefined'?{}:SERVER_STORE);
    const label=document.getElementById('doctorBalanceMonthLabel');
    if(label)label.textContent='Не закрыто'+(end?' · '+end.slice(0,4):'');
    const calculate=calculator(
      typeof DOCTOR_PAID_SNAPSHOTS==='undefined'?{}:DOCTOR_PAID_SNAPSHOTS,
      typeof DOCTOR_PAID_PERIODS==='undefined'?[]:DOCTOR_PAID_PERIODS,
      typeof DOCTOR_SERVICE_DAYS==='undefined'?[]:DOCTOR_SERVICE_DAYS,{loadedPeriods:true});
    const header=document.createElement('th');header.className='doctor-balance-heading';
    header.innerHTML='Не закрыто<span class="period-months">'+(start?dateLabel(start):'—')+' — '+(end?dateLabel(end):'—')+'</span>';
    header.title='Оплаты минус услуги с выбранного месяца по последний загруженный день, по загруженным записям. Плюс — оплат больше, минус — услуг больше.';
    head.querySelector('tr').appendChild(header);
    for(const row of tbody.querySelectorAll('tr')){
      if(row.classList.contains('group-row')||row.classList.contains('sub-row')){row.cells[0].colSpan+=1;continue}
      const final=document.createElement('td');row.appendChild(final);
      const kind=row.dataset.managementStaffKind,name=row.dataset.managementStaffName;
      if(!['dentists','clinicDocs'].includes(kind)||!name)continue;
      row.classList.add('doctor-comparison-row');
      // One table row keeps hide/move controls attached to BOTH lines.
      row.cells[0].innerHTML='<span class="doctor-comparison-name">'+escape(name)+'</span><span class="doctor-comparison-label">Оплаты / Услуги</span>';
      columns.forEach((bounds,index)=>{
        const result=calculate(bounds.from,bounds.to,kind,name),cell=row.cells[index+1];
        // Keep the original renderer's historical payment amount and source selection.
        const paidHtml=cell.innerHTML;
        // Keep the upstream payment heat colour on the entire two-line cell.
        // Service amounts must never influence or clear it.
        const line=(value,label,source)=>'<span class="doctor-comparison-line" title="'+escape(label+' · '+source+' · '+dateLabel(bounds.from)+' — '+dateLabel(bounds.to)+(value===null?' · Нет полных данных за период':''))+'">'+(value===null?'<span class="empty">—</span>':money(value))+'</span>';
        cell.innerHTML='<span class="doctor-comparison-line doctor-comparison-paid" title="Оплаты — прежнее значение отчёта">'+paidHtml+'</span>'+line(result.services,'Услуги','Выручка по направлениям · по загруженным записям');
      });
      const result=calculate(start,end,kind,name);final.className='doctor-balance-value';
      if(!start||!end||start>end){final.innerHTML='<span class="empty">—</span><span class="period-months">'+(!end?'Нет загруженных данных':'Нет данных с выбранного месяца')+'</span>';continue}
      if(result.balance===null){
        final.innerHTML='<span class="empty">—</span><span class="period-months">Нет полных данных</span>';
        final.title='Не закрыто не рассчитано: нет полных данных '+[result.paid===null?'оплат':'',result.services===null?'услуг':''].filter(Boolean).join(' и ')+' за '+(start?dateLabel(start):'—')+' — '+(end?dateLabel(end):'—')+'. Отсутствующий месяц или ошибочные записи не считаются нулём.';
      }else{
        final.textContent=(result.balance>0?'+':'')+money(result.balance);
        if(result.loadedPeriods){const label=document.createElement('span');label.className='period-months';label.textContent='По загруженным периодам';final.appendChild(label)}
        final.title='Оплаты '+money(result.paid)+' − услуги '+money(result.services)+' с '+dateLabel(start)+' по '+dateLabel(end)+'. По загруженным записям, без начального остатка прошлого года. Наличие записей не подтверждает полноту загрузки.'+(result.loadedPeriods?' Периоды оплат: '+result.paymentPeriods.map(p=>dateLabel(p.from)+' — '+dateLabel(p.to)).join('; ')+'. Даты вне этих периодов неизвестны, а не равны нулю.':'');
      }
    }
  }
  window.azDoctorBalance={calculator,render,loadedBounds};
  const style=document.createElement('style');
  style.textContent='.doctor-comparison-row td{padding-top:5px!important;padding-bottom:5px!important}.doctor-comparison-row td:first-child{white-space:nowrap!important}.doctor-comparison-name{display:inline;vertical-align:middle}.doctor-comparison-label{display:block;font-size:10px;line-height:16px;color:#627269}.doctor-comparison-line{display:block;line-height:16px}.doctor-comparison-line+.doctor-comparison-line{font-weight:500;color:#627269}.doctor-balance-value,.doctor-balance-heading{min-width:155px;background:#f4efe3!important}.doctor-balance-value .period-months{display:block;font-size:10px;line-height:14px;white-space:normal}';
  document.head.appendChild(style);
})();
