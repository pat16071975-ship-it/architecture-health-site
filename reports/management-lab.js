/* Separate IDENT group totals; the legacy cash fields are not fallbacks. */
(()=>{
  'use strict';
  const validDate=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&
    Number.isFinite(Date.parse(value+'T12:00:00Z'))&&new Date(value+'T12:00:00Z').toISOString().slice(0,10)===value;
  const nextDay=value=>{const d=new Date(value+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+1);return d.toISOString().slice(0,10)};
  const nextMonth=value=>{const d=new Date(value+'-01T12:00:00Z');d.setUTCMonth(d.getUTCMonth()+1);return d.toISOString().slice(0,7)};
  const cents=value=>typeof value==='number'&&Number.isFinite(value)&&Number.isSafeInteger(Math.round(value*100))?Math.round(value*100):null;
  const empty=()=>({paid:null,services:null,orders:null,avg:null,loadedDays:0,expectedDays:0,ordersComplete:false});
  function calculate(paidPeriods,serviceDays,from,to){
    if(!validDate(from)||!validDate(to)||from>to)return empty();
    const ranges=new Map(),days=new Map(),recovered=[];
    for(const part of paidPeriods||[]){
      if(part?.recovered===true){
        if(validDate(part.from)&&validDate(part.to)&&part.from>=from&&part.to<=to&&part.from<=part.to&&part.from.slice(0,7)===part.to.slice(0,7))
          recovered.push(part);
        continue;
      }
      if(part&&validDate(part.from)&&validDate(part.to)&&part.from>=from&&part.to<=to&&part.from<=part.to)
        ranges.set(part.from+'/'+part.to,part);
    }
    const exact=ranges.get(from+'/'+to),starts=new Map(),memo=new Map();
    for(const part of [...ranges.values()].sort((a,b)=>b.to.localeCompare(a.to))){
      if(!starts.has(part.from))starts.set(part.from,[]);
      starts.get(part.from).push(part);
    }
    function cover(at){
      if(at>to)return [];
      if(memo.has(at))return memo.get(at);
      for(const part of starts.get(at)||[]){
        const tail=cover(nextDay(part.to));
        if(tail!==null){const found=[part,...tail];memo.set(at,found);return found}
      }
      memo.set(at,null);return null;
    }
    const parts=exact?[exact]:cover(from);
    function sumMoney(rows,key){
      if(!rows||!rows.length)return null;
      let total=0;
      for(const row of rows){const n=cents(row[key]);if(n===null)return null;total+=n;if(!Number.isSafeInteger(total))return null}
      return total/100;
    }
    const paid=sumMoney(parts,'paid');
    // Do not change the meaning of paid or the established full-period avg.
    // A separately labeled witnessed component can be displayed instead.
    let knownPaid=null,knownRanges=[];
    if(paid===null&&parts){
      const witnessed=parts.map(p=>{
        if(cents(p.paid)!==null)return {amount:p.paid};
        // Recovery is an explicitly selected and server-validated witness.
        // Do not let an older partial value shadow a corrected exact range.
        // A full active total retains priority; a different/ambiguous range
        // cannot substitute or be added to this part.
        const same=recovered.filter(r=>r.from===p.from&&r.to===p.to);
        if(same.length===1&&cents(same[0].knownPaid)!==null)return {amount:same[0].knownPaid};
        return {amount:p.knownPaid};
      });
      knownPaid=sumMoney(witnessed,'amount');
      if(knownPaid!==null)knownRanges=parts.map(p=>p.from+' — '+p.to);
    }
    if(paid===null&&knownPaid===null){
      // Use a whole recovered pair only, never prorate or sum it with an
      // overlapping active snapshot. Require one witness for every month.
      const byMonth=new Map();
      let ambiguous=false;
      for(const p of recovered){
        const m=p.from.slice(0,7);
        if(byMonth.has(m)){ambiguous=true;break}
        byMonth.set(m,p);
      }
      if(!ambiguous){
        const selected=[];
        for(let m=from.slice(0,7);m<=to.slice(0,7);m=nextMonth(m)){
          if(!byMonth.has(m)){selected.length=0;break}
          selected.push(byMonth.get(m));
        }
        knownPaid=sumMoney(selected,'knownPaid');
        if(knownPaid!==null)knownRanges=selected.map(p=>p.from+' — '+p.to);
      }
    }
    for(const day of serviceDays||[]){
      if(day&&validDate(day.date)&&day.date>=from&&day.date<=to)days.set(day.date,day);
    }
    const loaded=[...days.values()],months=new Set(loaded.map(d=>d.date.slice(0,7)));
    let covered=loaded.length>0;
    for(let month=from.slice(0,7);month<=to.slice(0,7);month=nextMonth(month)){
      if(!months.has(month)){covered=false;break}
    }
    const services=covered?sumMoney(loaded,'services'):null;
    let orders=covered?0:null;
    if(covered)for(const day of loaded){
      if(!Number.isSafeInteger(day.orders)||day.orders<0){orders=null;break}
      orders+=day.orders;if(!Number.isSafeInteger(orders)){orders=null;break}
    }
    // Partial sums are useful evidence, but one day per month does not
    // establish the denominator for a full-period average. The current
    // service API has no independent interval/zero-day coverage declaration.
    // Every calendar date must therefore have an actual valid source day to
    // prove coverage with THIS evidence. Never synthesize a missing zero day.
    const expectedDays=Math.round((Date.parse(to+'T12:00:00Z')-Date.parse(from+'T12:00:00Z'))/86400000)+1;
    const ordersComplete=orders!==null&&services!==null&&loaded.length===expectedDays;
    const avg=paid!==null&&ordersComplete&&orders>0?paid/orders:null;
    const result={paid,services,orders,avg,loadedDays:loaded.length,expectedDays,ordersComplete};
    if(knownPaid!==null){result.knownPaid=knownPaid;result.knownRanges=knownRanges}
    return result;
  }
  function available(){return typeof LAB_PAID_PERIODS!=='undefined'&&typeof LAB_SERVICE_DAYS!=='undefined'}
  function view(from,to){
    return available()?calculate(LAB_PAID_PERIODS,LAB_SERVICE_DAYS,from,to):empty();
  }
  function title(key,value,from,to){
    const range=from+' — '+to,missing='Нет полных данных; отсутствие не равно нулю. ';
    const coverage='Загружено календарных дат: '+value.loadedDays+' из '+value.expectedDays+'. ';
    if(key==='paid'){
      if(value.paid===null&&value.knownPaid!=null)return 'Оплаты, подтверждённые по строкам услуг; полная сумма с нераспределённой задолженностью неизвестна. Источники: '+value.knownRanges.join('; ')+'. Запрошено: '+range;
      return (value.paid===null?missing:'')+'Оплаты: «Выручка по направлениям (новый оплаты)», только подтверждённая группа ЗТЛ. '+range;
    }
    if(key==='services')return (value.services===null?missing:'')+'Услуги: «Выручка по направлениям», по загруженным датам. '+coverage+range;
    if(key==='orders')return (value.orders===null?missing:'')+(!value.ordersComplete?'Полнота количества счетов за весь период не подтверждена. ':'')+'Уникальные счета с услугами группы ЗТЛ, по загруженным датам. '+coverage+range;
    const reason=!value.ordersComplete?'Полнота количества счетов за весь период не подтверждена. ':value.paid===null?'Нет подтверждённых оплат за весь период. ':value.orders===0?'Нет счетов для расчёта среднего чека. ':'';
    return (value.avg===null?reason:'')+'Оплаты / уникальные счета с услугами ЗТЛ за весь подтверждённый период. '+coverage+range;
  }
  const paidDisplay=value=>value?.paid??value?.knownPaid??null;
  const partialPaid=value=>value?.paid==null&&value?.knownPaid!=null;
  const api={calculate,available,view,title,paidDisplay,partialPaid};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(typeof window!=='undefined'){
    window.azManagementLab=api;
    const style=document.createElement('style');
    style.textContent='.az-lab-comparison-row td{padding-top:5px!important;padding-bottom:5px!important}.az-lab-comparison-label,.az-lab-single-caption{display:block;font-size:10px;color:#627269;line-height:14px}.az-lab-comparison-line{display:block;line-height:16px}.az-lab-comparison-line+.az-lab-comparison-line{font-weight:500;color:#627269}.metric.az-lab-dual{height:auto;min-height:0}.metric.az-lab-dual input{width:100%;box-sizing:border-box}';
    document.head.appendChild(style);
  }
})();
