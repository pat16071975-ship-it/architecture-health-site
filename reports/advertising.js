const $=s=>document.querySelector(s),all=s=>[...document.querySelectorAll(s)];
const nf=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0});
const money=v=>v==null?'—':nf.format(Number(v)||0)+' ₽';
const number=v=>v==null?'—':nf.format(Number(v)||0);
const pct=v=>v==null?'—':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(v)+'%';
const h=s=>String(s??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const validDate=s=>/^\d{4}-\d{2}-\d{2}$/.test(String(s||''));
const sameMonth=(a,b)=>validDate(a)&&validDate(b)&&a.slice(0,7)===b.slice(0,7);
let payload=null,clinicToday='';
const expandedSources=new Set();

function monthStart(day){return day.slice(0,7)+'-01'}
function previousMonth(day){
  const [rawY,rawM]=day.slice(0,7).split('-').map(Number);
  let y=rawY,m=rawM-1;
  if(m===0){y-=1;m=12}
  const mm=String(m).padStart(2,'0');
  const last=String(new Date(Date.UTC(y,m,0)).getUTCDate()).padStart(2,'0');
  return [y+'-'+mm+'-01',y+'-'+mm+'-'+last]
}
function quickRange(k){
  const today=clinicToday||payload?.today;
  if(!validDate(today)){load({serverDefaults:true});return}
  let a=today,b=today;
  if(k==='month')a=monthStart(today);
  else if(k==='prev-month')[a,b]=previousMonth(today);
  $('#fromDate').value=a;$('#toDate').value=b;load()
}
function comparisonOptions(preferred){
  const old=preferred||$('#compareMode').value||'previous_period',month=sameMonth($('#fromDate').value,$('#toDate').value);
  let o=[['none','Без сравнения'],['previous_period','Предыдущий сопоставимый период']];
  if(month)o.push(['previous_quarter_avg','Среднее предыдущего квартала'],['year_avg','Среднее текущего года']);
  o.push(['last_year','Тот же период прошлого года']);
  $('#compareMode').innerHTML=o.map(x=>'<option value="'+x[0]+'">'+x[1]+'</option>').join('');
  $('#compareMode').value=o.some(x=>x[0]===old)?old:'none'
}
function dclass(metric,v){
  if(v==null)return'neutral';
  if(metric==='spend')return v<=0?'good':'bad';
  return v>=0?'good':'bad'
}
function compareCard(metric,fmt){
  if(payload?.compare?.mode==='none')return'';
  const c=payload?.compare?.summary?.[metric],d=payload?.compare?.delta_pct?.[metric];
  return '<div class="compare-card"><div class="clabel">'+h(payload?.compare?.label||'Сравнение')+'</div><div class="cvalue">'+fmt(c)+'</div><div class="delta '+dclass(metric,d)+'">'+(d==null?'—':(d>0?'+':'')+pct(d))+'</div></div>'
}
function expenseExtra(s){
  const parts=[];
  if(s.advertising_payment!=null)parts.push('Оплата рекламы: <b>'+money(s.advertising_payment)+'</b>');
  if(s.contractors_payment!=null)parts.push('Подрядчики: <b>'+money(s.contractors_payment)+'</b>');
  return parts.join('<br>')||'Расходы за выбранный период ещё не введены.'
}
function mainKpis(){
  const s=payload?.summary||{},crm=payload?.crm||{};
  const items=[
    {
      key:'spend',label:'Расходы на рекламу',value:money(s.spend),fmt:money,
      extra:expenseExtra(s)
    },
    {
      key:'clicks',label:'Клики по рекламе',
      value:s.clicks==null?'Нет данных':number(s.clicks),fmt:number,text:s.clicks==null,
      extra:s.clicks==null
        ?'Нет данных по рекламным кликам. Появятся после подключения рекламной системы.'
        :(s.cost_per_click!=null?'Стоимость клика: <b>'+money(s.cost_per_click)+'</b>':'Стоимость клика не считается, пока охват расходов и кликов не совпадает.')
    },
    {
      key:'visits',label:'Посещения сайта',value:number(s.visits),fmt:number,
      extra:'Рабочие посещения Метрики; metrika_test исключена.'
    },
    {
      key:'target_leads',label:'Целевые лиды',
      value:crm.available&&s.target_leads!=null?number(s.target_leads):(crm.message||'Не хватает данных CRM для построения отчёта'),
      fmt:number,text:!(crm.available&&s.target_leads!=null),
      extra:crm.available&&s.lead_to_primary_pct!=null?'Дошли хотя бы до одного первичного: <b>'+pct(s.lead_to_primary_pct)+'</b>':''
    },
    {
      key:'primary_visits',label:'Состоявшиеся первичные приёмы',value:number(s.primary_visits),fmt:number,
      extra:'Первый состоявшийся приём пациента у конкретного специалиста.'
    }
  ];
  $('#kpis').innerHTML=items.map(item=>
    '<div class="kpi-wrap"><div class="kpi"><div class="name">'+h(item.label)+'</div>'+
    '<div class="value'+(item.text?' text':'')+'">'+item.value+'</div>'+
    '<div class="extra">'+item.extra+'</div></div>'+
    (item.text?'':compareCard(item.key,item.fmt))+'</div>'
  ).join('')
}
function moneyCards(){
  const s=payload?.summary||{},e=payload?.economics||{},crm=payload?.crm||{};
  const services=s.primary_services!=null&&s.primary_paid!=null&&Math.abs(s.primary_services-s.primary_paid)>.5
    ?'Оказано услуг на '+money(s.primary_services)
    :'';
  const avg=e.primary_avg_check!=null?'Средний чек первичного: <b>'+money(e.primary_avg_check)+'</b>':'Средний чек первичного: нет данных';
  const payers=e.primary_payers!=null?'Оплативших первичных: <b>'+number(e.primary_payers)+'</b>':'';
  const adCost=crm.available&&e.advertising_primary_cost!=null
    ?'Стоимость рекламного первичного: <b>'+money(e.advertising_primary_cost)+'</b>'
    :'Стоимость рекламного первичного: <b>нет данных CRM</b>';
  const roas=crm.available&&e.primary_roas!=null
    ?'ROAS первичных: <b>×'+new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(e.primary_roas)+'</b>'
    :'ROAS первичных: <b>нет данных CRM</b>';
  $('#primaryMoney').innerHTML=
    '<div class="kpi-wrap"><div class="kpi"><div class="name">Выручка первичных приёмов — фактически получено</div>'+
    '<div class="value">'+money(s.primary_paid)+'</div>'+
    '<div class="extra">'+services+'</div>'+
    '<div class="primary-economics"><span>'+avg+'</span>'+(payers?'<span>'+payers+'</span>':'')+'<span>'+adCost+'</span><span>'+roas+'</span></div>'+
    '</div>'+compareCard('primary_paid',money)+'</div>';

  $('#cohortCard').innerHTML=
    '<div class="name">Выручка новых пациентов за 30 дней</div>'+
    '<div class="value">'+money(s.new_patient_30d_paid)+'</div>'+
    '<div class="extra">Новых пациентов, впервые пришедших в клинику в выбранном периоде: <b>'+number(s.new_patients)+'</b><br>'+
    'Учтены только фактически полученные от них деньги за первые 30 дней после первого визита.</div>'
}
function activity(){
  const a=payload?.activity||{};
  const items=[['Первичные',a.primary_visits,a.primary_paid],['Повторные',a.repeat_visits,a.repeat_paid],['Всего',a.total_visits,a.total_paid]];
  $('#activity').innerHTML=items.map(x=>
    '<div class="activity-card"><div class="a-title">'+x[0]+'</div><div class="activity-values">'+number(x[1])+' <span>·</span> '+money(x[2])+'</div></div>'
  ).join('')
}
function trendMoneyMetric(metric){return ['primary_paid','actual_received_total','spend','total_paid'].includes(metric)}
function lineChart(){
  const cur=payload?.trend?.current||[],cmp=payload?.trend?.compare||[],vals=[...cur,...cmp].map(x=>x.value).filter(v=>v!=null),box=$('#trendChart');
  if(!vals.length){box.innerHTML='<div class="empty">Нет данных за выбранный период</div>';$('#trendLegend').innerHTML='';return}
  const w=1000,H=210,p=28,max=Math.max(...vals.map(Number),1),x=i=>p+i*(w-2*p)/Math.max(cur.length-1,1),y=v=>H-p-Number(v)*(H-2*p)/max;
  const path=arr=>arr.map((d,i)=>d.value==null?null:[i,d.value]).filter(Boolean).map((a,j)=>(j?'L':'M')+x(a[0]).toFixed(1)+','+y(a[1]).toFixed(1)).join(' ');
  const dots=cur.map((d,i)=>d.value==null?'':'<circle cx="'+x(i).toFixed(1)+'" cy="'+y(d.value).toFixed(1)+'" r="2.5" fill="#44634f"><title>'+h(d.date)+' — '+h(trendMoneyMetric(payload?.trend?.metric)?money(d.value):number(d.value))+'</title></circle>').join('');
  box.innerHTML='<svg viewBox="0 0 '+w+' '+H+'" preserveAspectRatio="none"><line x1="'+p+'" y1="'+(H-p)+'" x2="'+(w-p)+'" y2="'+(H-p)+'" stroke="#ded4c4"/><path d="'+path(cmp)+'" fill="none" stroke="#b59662" stroke-width="3"/><path d="'+path(cur)+'" fill="none" stroke="#44634f" stroke-width="3"/>'+dots+'</svg>';
  $('#trendLegend').innerHTML='<span><i style="background:#44634f"></i>Выбранный период</span>'+(cmp.length?'<span><i style="background:#b59662"></i>'+h(payload?.compare?.label||'Сравнение')+'</span>':'')
}
function sourceName(row){return (row.source_name||row.campaign_name||row.channel_name||'Источник не определён').trim()}
function campaignName(row){const name=String(row.campaign_name||'').trim();if(name)return name;const id=String(row.campaign_id||'').trim();return id?'Кампания '+id:'Не определено'}
function sourceRows(){
  return (payload?.sources||[])
    .filter(row=>row.spend!=null||row.total_spend!=null||row.clicks!=null||row.visits!=null||row.booking_open_visits!=null||row.medflex_visits!=null)
    .sort((a,b)=>(Number(b.total_spend??b.spend)||0)-(Number(a.total_spend??a.spend)||0)||(Number(b.visits)||0)-(Number(a.visits)||0)||sourceName(a).localeCompare(sourceName(b),'ru'))
}
function campaignRowsForSource(sourceKey){
  return (payload?.campaigns||[])
    .filter(row=>row.source_key===sourceKey&&(row.spend!=null||row.total_spend!=null||row.clicks!=null||row.visits!=null||row.booking_open_visits!=null||row.medflex_visits!=null))
    .sort((a,b)=>(Number(b.total_spend??b.spend)||0)-(Number(a.total_spend??a.spend)||0)||(Number(b.visits)||0)-(Number(a.visits)||0)||campaignName(a).localeCompare(campaignName(b),'ru'))
}
function provenance(row){
  if(row.spend_provenance==='direct_api')return 'Direct API';
  if(row.spend_provenance==='manual')return 'вручную';
  if(row.spend_provenance==='legacy_manual')return 'старый ручной расход';
  return ''
}
function metricCell(value,cost,label){
  return '<td>'+number(value)+(cost!=null?'<small>'+money(cost)+' / '+h(label)+'</small>':'')+'</td>'
}
function spendCell(row){
  const total=row.total_spend!=null?row.total_spend:row.spend;
  const parts=[];
  if(row.advertising_payment!=null)parts.push('реклама '+money(row.advertising_payment));
  if(row.contractors_payment!=null)parts.push('подрядчик '+money(row.contractors_payment));
  if(provenance(row))parts.push(provenance(row));
  return '<td>'+money(total)+(parts.length?'<small>'+parts.map(h).join(' · ')+'</small>':'')+'</td>'
}
function sourceCells(row){
  return spendCell(row)+
    metricCell(row.clicks,row.cost_per_click,'клик')+
    metricCell(row.visits,row.cost_per_visit,'посещение')+
    metricCell(row.booking_open_visits,row.cost_per_booking_open,'открытие')+
    metricCell(row.medflex_visits,row.cost_per_medflex,'переход')
}
function renderSources(){
  const rows=sourceRows(),body=$('#sourceRows');
  if(!rows.length){body.innerHTML='<tr><td colspan="6">Нет данных за выбранный период.</td></tr>';return}
  const html=rows.map(row=>{
    const campaigns=campaignRowsForSource(row.source_key);
    const expandable=campaigns.length>0;
    const expanded=expandable&&expandedSources.has(row.source_key);
    const label=expandable
      ?'<button type="button" class="source-expand" data-source-key="'+h(row.source_key)+'" aria-expanded="'+(expanded?'true':'false')+'">'+(expanded?'−':'+')+'</button><span>'+h(sourceName(row))+'</span>'
      :h(sourceName(row));
    const parent='<tr class="group-row"><td>'+label+'</td>'+sourceCells(row)+'</tr>';
    if(!expanded)return parent;
    const children=campaigns.map(c=>
      '<tr class="child-row source-campaign-row" data-source-key="'+h(row.source_key)+'"><td><span class="campaign-name">'+h(campaignName(c))+'</span></td>'+sourceCells(c)+'</tr>'
    ).join('');
    return parent+children
  }).join('');
  const s=payload?.summary||{};
  const total='<tr class="group-row"><td>Разнесено по источникам</td><td>'+money(s.source_total_spend??s.source_spend_total)+'</td><td>'+number(s.clicks)+'</td><td>'+number(s.visits)+'</td><td>'+number(s.booking_open_visits)+'</td><td>'+number(s.medflex_visits)+'</td></tr>';
  body.innerHTML=html+total;
  body.querySelectorAll('.source-expand').forEach(button=>button.addEventListener('click',()=>{
    const key=button.dataset.sourceKey||'';
    if(expandedSources.has(key))expandedSources.delete(key);else expandedSources.add(key);
    renderSources()
  }))
}
function shortDate(value){
  if(!validDate(value))return '';
  const [y,m,d]=value.split('-');return d+'.'+m+'.'+y
}
function coveragePart(label,item,allowComplete=true){
  if(!item||!item.days)return label+': нет данных';
  const range=item.from===item.to?shortDate(item.from):shortDate(item.from)+'–'+shortDate(item.to);
  const suffix=allowComplete&&item.expectedDays?(item.complete?' — полный период':' — частично, '+item.days+' из '+item.expectedDays+' дней'):' — '+item.days+' дн.';
  return label+': '+range+suffix
}
function renderCoverage(){
  const c=payload?.coverage||{};
  $('#coverage').textContent='Покрытие выбранного периода: '+[
    coveragePart('Метрика',c.metrika),
    coveragePart('Приёмы',c.clinic),
    coveragePart('«Оплачено» MIS',c.misPaid),
    coveragePart('Кассовые операции',c.cash,false)
  ].join(' · ')
}
function renderFreshness(){
  const items=payload?.freshness||[],s=payload?.summary||{},parts=[];
  if(s.spend==null)parts.push('Общие расходы на рекламу за выбранный период не введены полностью.');
  if(s.clicks==null)parts.push('Клики рекламных систем пока не подключены.');
  if(items.length)parts.push('Актуальность источников: '+items.map(x=>x.label+' — '+(String(x.generated_at||'').slice(0,10)||'нет данных')).join(' · '));
  $('#freshness').textContent=parts.join(' ')
}
function render(){mainKpis();moneyCards();activity();lineChart();renderSources();renderCoverage();renderFreshness()}
function syncUrl(){
  const q=new URLSearchParams(location.search);
  q.set('from',$('#fromDate').value);q.set('to',$('#toDate').value);q.set('compare',$('#compareMode').value);
  history.replaceState(null,'',location.pathname+'?'+q.toString())
}
function showPeriodError(message){$('#freshness').innerHTML='<span class="error">'+h(message)+'</span>'}
async function load(options={}){
  const serverDefaults=options.serverDefaults===true;
  comparisonOptions();$('#page').classList.add('loading');
  try{
    const from=$('#fromDate').value,to=$('#toDate').value;
    if(!serverDefaults){
      if(!validDate(from)||!validDate(to)){showPeriodError('Выберите обе даты периода.');return}
      if(from>to){showPeriodError('Дата «с» не может быть позже даты «по».');return}
    }
    const q=new URLSearchParams({compare:$('#compareMode').value||'previous_period',trend:$('#trendMetric').value||'visits'});
    if(!serverDefaults){q.set('from',from);q.set('to',to)}
    const r=await fetch('/api/reports/advertising?'+q,{credentials:'same-origin',cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    payload=await r.json();clinicToday=payload.today||clinicToday;
    $('#fromDate').value=payload.period.from;$('#toDate').value=payload.period.to;
    comparisonOptions(payload.compare?.mode||$('#compareMode').value);syncUrl();render()
  }catch(e){console.error(e);showPeriodError('Не удалось загрузить рекламный отчёт.')}
  finally{$('#page').classList.remove('loading')}
}
function initialState(){
  const q=new URLSearchParams(location.search),from=q.get('from'),to=q.get('to'),compare=q.get('compare');
  if(validDate(from)&&validDate(to)&&from<=to){
    $('#fromDate').value=from;$('#toDate').value=to;comparisonOptions(compare||'previous_period');load();return
  }
  comparisonOptions(compare||'previous_period');load({serverDefaults:true})
}
all('.chip').forEach(b=>b.addEventListener('click',()=>quickRange(b.dataset.range)));
['fromDate','toDate','compareMode'].forEach(id=>$('#'+id).addEventListener('change',()=>load()));
$('#trendMetric').addEventListener('change',()=>load());
initialState();
