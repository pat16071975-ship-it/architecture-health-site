
const $=s=>document.querySelector(s), $=s=>[...document.querySelectorAll(s)];
const money=v=>v==null?'Нет данных':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(v)+' ₽';
const number=v=>v==null?'Нет данных':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(v);
const DATE_RE=/^\d{4}-\d{2}-\d{2}$/;
const validDate=s=>DATE_RE.test(String(s||''));
const sameMonth=(a,b)=>validDate(a)&&validDate(b)&&a.slice(0,7)===b.slice(0,7);
let payload=null,clinicToday='';

function h(s){return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;')}
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

function renderSite(){
  const s=payload?.summary||{};
  $('#siteVisits').textContent=number(s.visits);
  $('#bookingOpen').textContent=number(s.booking_open_visits);
  $('#bookingOpenExtra').textContent=s.booking_open_reaches==null?'':'Срабатываний цели: '+number(s.booking_open_reaches);
  $('#medflexVisits').textContent=number(s.medflex_visits);
  $('#medflexExtra').textContent=(s.medflex_reaches==null?'':'Срабатываний цели: '+number(s.medflex_reaches)+'. ')+'Это действие на сайте, а не подтверждённый лид.';
  const crm=payload?.crm||{};
  const targetLeads=$('#targetLeads');
  const targetLeadsExtra=$('#targetLeadsExtra');
  if(crm.available&&s.target_leads!=null){
    targetLeads.classList.remove('crm-missing');
    targetLeads.textContent=number(s.target_leads);
    targetLeadsExtra.textContent=s.lead_to_primary_pct==null?'':'Дошли до состоявшегося первичного приёма: '+new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(s.lead_to_primary_pct)+'%';
  }else{
    targetLeads.classList.add('crm-missing');
    targetLeads.textContent=crm.message||'Не хватает данных CRM для построения отчёта';
    targetLeadsExtra.textContent='';
  }
}

function renderActivity(){
  const a=payload?.activity||{};
  const items=[['Первичные',a.primary_visits,a.primary_paid],['Повторные',a.repeat_visits,a.repeat_paid],['Всего',a.total_visits,a.total_paid]];
  $('#activity').innerHTML=items.map(x=>'<div class="card"><div><div class="name">'+x[0]+'</div><div class="value">'+number(x[1])+'</div></div><div class="money">'+money(x[2])+'<br><small>классифицированные платежи периода</small></div></div>').join('')
}

function renderRevenue(){
  const r=payload?.revenue||{};
  $('#clinicRevenue').textContent=money(r.clinic_paid_total);

  const directions=r.direction_paid||{};
  const details=[
    ['Стоматология',directions.dent],
    ['Клиника',directions.structure],
    ['Лаборатория',directions.lab],
    ['Другие / не классифицированные исполнители',directions.other]
  ].filter(x=>x[1]!=null&&Math.abs(Number(x[1])||0)>.004);
  $('#revenueBreakdown').innerHTML=details.map(x=>'<div class="card"><div class="name">'+x[0]+'</div><div class="value">'+money(x[1])+'</div></div>').join('');

  const patientGap=r.provider_known_without_patient_detail;
  $('#revenueExplanation').innerHTML=
    '<b>Вся сумма «Оплачено» распределена по врачам/исполнителям.</b> '+
    'Построчная детализация до пациента/услуги доступна на '+money(r.detailed_paid_total)+'. '+
    (patientGap!=null&&patientGap>0
      ? 'Ещё '+money(patientGap)+' имеют известного врача/направление, но в выгрузке нет строки пациента/услуги для этой части оплаты. '
      : '')+
    'Классификация детализированной части по типу приёма: первичные '+money(r.primary_paid)+
    ', повторные '+money(r.repeat_paid)+
    ', тип приёма не определён '+money(r.unclassified_detailed)+'.';

  const control=[
    ['Касса за период',r.cash_total],
    ['ООО',r.cash_ooo],
    ['ИП',r.cash_ip],
    ['Не распределено по врачу/направлению в кассовом слое',r.cash_unallocated]
  ];
  $('#cashControl').innerHTML=control.map(x=>'<div class="control-item"><span>'+x[0]+'</span><strong>'+money(x[1])+'</strong></div>').join('')
}


function siteSourceRows(){
  return (payload?.sources||[])
    .filter(row=>row.visits!=null||row.booking_open_visits!=null||row.medflex_visits!=null)
    .sort((a,b)=>(Number(b.visits)||0)-(Number(a.visits)||0)||sourceName(a).localeCompare(sourceName(b),'ru'))
}
function sourceName(row){
  return (row.source_name||row.campaign_name||row.channel_name||'Источник не определён').trim()
}
function siteCells(row){
  return '<td>'+number(row.visits)+'</td><td>'+number(row.booking_open_visits)+'</td><td>'+number(row.medflex_visits)+'</td>'
}
function renderSources(){
  const rows=siteSourceRows(),body=$('#sourceRows');
  if(!rows.length){
    body.innerHTML='<tr><td colspan="4">Метрика за выбранный период ещё не загружена.</td></tr>';
    return
  }
  const s=payload?.summary||{};
  const sourceHtml=rows.map(row=>
    '<tr><td>'+h(sourceName(row))+'</td>'+siteCells(row)+'</tr>'
  ).join('');
  const totalHtml=
    '<tr class="group-row"><td>Всего</td><td>'+number(s.visits)+'</td><td>'+number(s.booking_open_visits)+'</td><td>'+number(s.medflex_visits)+'</td></tr>';
  body.innerHTML=sourceHtml+totalHtml
}

function renderFreshness(){
  const items=payload?.freshness||[];
  const spend=payload?.summary?.spend;
  const prefix=spend==null?'Расходы и клики Директа пока не загружены — неизвестные значения не заменяются нулями. ':'';
  $('#freshness').textContent=prefix+(items.length?'Актуальность источников: '+items.map(x=>x.label+' — '+(String(x.generated_at||'').slice(0,10)||'нет данных')).join(' · '):'Данные Метрики ещё не загружены.')
}
function render(){renderSite();renderActivity();renderRevenue();renderSources();renderFreshness()}
function syncUrl(){
  const q=new URLSearchParams(location.search);
  q.set('from',$('#fromDate').value);
  q.set('to',$('#toDate').value);
  q.set('compare',$('#compareMode').value);
  history.replaceState(null,'',location.pathname+'?'+q.toString())
}
function showPeriodError(message){
  $('#freshness').innerHTML='<span class="error">'+h(message)+'</span>'
}
async function load(options={}){
  const serverDefaults=options.serverDefaults===true;
  comparisonOptions();$('#page').classList.add('loading');
  try{
    const from=$('#fromDate').value,to=$('#toDate').value;
    if(!serverDefaults){
      if(!validDate(from)||!validDate(to)){showPeriodError('Выберите обе даты периода.');return}
      if(from>to){showPeriodError('Дата «с» не может быть позже даты «по».');return}
    }
    const q=new URLSearchParams({compare:$('#compareMode').value||'previous_period',trend:'visits'});
    if(!serverDefaults){q.set('from',from);q.set('to',to)}
    const r=await fetch('/api/reports/advertising?'+q,{credentials:'same-origin',cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    payload=await r.json();
    clinicToday=payload.today||clinicToday;
    $('#fromDate').value=payload.period.from;
    $('#toDate').value=payload.period.to;
    comparisonOptions(payload.compare?.mode||$('#compareMode').value);
    syncUrl();
    render()
  }catch(e){
    console.error(e);
    showPeriodError('Не удалось загрузить рекламный отчёт.')
  }finally{$('#page').classList.remove('loading')}
}
function initialState(){
  const q=new URLSearchParams(location.search);
  const from=q.get('from'),to=q.get('to'),compare=q.get('compare');
  if(validDate(from)&&validDate(to)&&from<=to){
    $('#fromDate').value=from;$('#toDate').value=to;
    comparisonOptions(compare||'previous_period');
    load();
    return
  }
  comparisonOptions(compare||'previous_period');
  load({serverDefaults:true})
}
$('.chip').forEach(b=>b.addEventListener('click',()=>quickRange(b.dataset.range)));
['fromDate','toDate','compareMode'].forEach(id=>$('#'+id).addEventListener('change',()=>load()));
initialState();
