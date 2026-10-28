
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const money=v=>v==null?'Нет данных':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(v)+' ₽';
const number=v=>v==null?'Нет данных':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(v);
const iso=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
const sameMonth=(a,b)=>a.slice(0,7)===b.slice(0,7);
let payload=null;

function h(s){return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;')}
function setDefaultDates(){const n=new Date();$('#fromDate').value=iso(new Date(n.getFullYear(),n.getMonth(),1));$('#toDate').value=iso(n)}
function quickRange(k){
  const n=new Date();let a,b;
  if(k==='day'){a=new Date(2026,9,27);b=new Date(2026,9,27)}
  else if(k==='month'){a=new Date(n.getFullYear(),n.getMonth(),1);b=n}
  else{a=new Date(n.getFullYear(),n.getMonth()-1,1);b=new Date(n.getFullYear(),n.getMonth(),0)}
  $('#fromDate').value=iso(a);$('#toDate').value=iso(b);load()
}
function comparisonOptions(){
  const old=$('#compareMode').value||'previous_period',month=sameMonth($('#fromDate').value,$('#toDate').value);
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
  $('#crmNote').textContent=crm.available?'Данные CRM подключены.':(crm.message||'Не хватает данных CRM для построения отчёта.');
}

function renderActivity(){
  const a=payload?.activity||{};
  const items=[['Первичные',a.primary_visits,a.primary_paid],['Повторные',a.repeat_visits,a.repeat_paid],['Всего',a.total_visits,a.total_paid]];
  $('#activity').innerHTML=items.map(x=>'<div class="card"><div><div class="name">'+x[0]+'</div><div class="value">'+number(x[1])+'</div></div><div class="money">'+money(x[2])+'<br><small>классифицированные платежи периода</small></div></div>').join('')
}

function renderRevenue(){
  const r=payload?.revenue||{};
  $('#clinicRevenue').textContent=money(r.clinic_paid_total);
  const details=[
    ['Построчно детализировано в МИС',r.detailed_paid_total],
    ['Первичные',r.primary_paid],
    ['Повторные',r.repeat_paid],
    ['Детализировано, но тип приёма не определён',r.unclassified_detailed],
    ['Без построчной детализации в выгрузке МИС',r.without_line_detail]
  ];
  $('#revenueBreakdown').innerHTML=details.map(x=>'<div class="card"><div class="name">'+x[0]+'</div><div class="value">'+money(x[1])+'</div></div>').join('');
  $('#revenueExplanation').innerHTML='Сумма <b>«Без построчной детализации в выгрузке МИС»</b> — это разница между общей суммой «Оплачено» и теми строками, где МИС дал пациент/услугу/врача. Это не потерянные деньги и не отдельный вид выручки; без дополнительного источника мы не относим её к первичным или повторным.';
  const control=[
    ['Касса за период',r.cash_total],
    ['ООО',r.cash_ooo],
    ['ИП',r.cash_ip],
    ['Не распределено по врачу/направлению в кассовом слое',r.cash_unallocated]
  ];
  $('#cashControl').innerHTML=control.map(x=>'<div class="control-item"><span>'+x[0]+'</span><strong>'+money(x[1])+'</strong></div>').join('')
}

const GROUP_ORDER={'Платная реклама':1,'Поиск':2,'Карты':3,'Социальные сети':4,'Прямые заходы':5,'Другие источники':6};
function sum(rows,key){const v=rows.map(r=>r?.[key]).filter(x=>x!=null);return v.length?v.reduce((a,b)=>a+b,0):null}
function sourceGroups(){
  const sources=payload?.sources||[];
  const byChannel=new Map();
  sources.forEach(row=>{
    if(row.visits==null&&row.booking_open_visits==null&&row.medflex_visits==null)return;
    const label=(row.channel_name||row.source_name||'Другие источники').trim()||'Другие источники';
    if(!byChannel.has(label))byChannel.set(label,[]);
    byChannel.get(label).push(row)
  });
  return [...byChannel.entries()].map(([label,rows])=>({
    label,
    summary:{visits:sum(rows,'visits'),booking_open_visits:sum(rows,'booking_open_visits'),medflex_visits:sum(rows,'medflex_visits')},
    children:rows.length>1?rows:[]
  })).sort((a,b)=>(GROUP_ORDER[a.label]||99)-(GROUP_ORDER[b.label]||99)||a.label.localeCompare(b.label,'ru'))
}
function sourceName(r){return (r.source_name||r.campaign_name||r.channel_name||'Источник не определён').trim()}
function cells(r){return '<td>'+number(r.visits)+'</td><td>'+number(r.booking_open_visits)+'</td><td>'+number(r.medflex_visits)+'</td>'}
function renderSources(){
  const groups=sourceGroups(),body=$('#sourceRows');
  if(!groups.length){body.innerHTML='<tr><td colspan="4">Метрика за выбранный период ещё не загружена.</td></tr>';return}
  let html='';
  groups.forEach((g,i)=>{
    const exp=g.children.length>0;
    html+='<tr class="group-row"><td><button class="group-btn '+(exp?'':'no-children')+'" data-group="'+i+'" '+(exp?'':'disabled')+'>'+(exp?'▸ ':'')+h(g.label)+'</button></td>'+cells(g.summary)+'</tr>';
    if(exp)g.children.forEach(r=>html+='<tr class="child-row child-'+i+'" hidden><td>'+h(sourceName(r))+'</td>'+cells(r)+'</tr>')
  });
  body.innerHTML=html;
  $$('.group-btn:not(.no-children)').forEach(btn=>btn.addEventListener('click',()=>{
    const rows=$$('.child-'+btn.dataset.group),open=rows.length&&!rows[0].hidden;
    rows.forEach(r=>r.hidden=open);btn.textContent=(open?'▸ ':'▾ ')+groups[+btn.dataset.group].label
  }))
}
function renderFreshness(){
  const items=payload?.freshness||[];
  const spend=payload?.summary?.spend;
  const prefix=spend==null?'Расходы и клики Директа пока не загружены — неизвестные значения не заменяются нулями. ':'';
  $('#freshness').textContent=prefix+(items.length?'Актуальность источников: '+items.map(x=>x.label+' — '+(String(x.generated_at||'').slice(0,10)||'нет данных')).join(' · '):'Данные Метрики ещё не загружены.')
}
function render(){renderSite();renderActivity();renderRevenue();renderSources();renderFreshness()}
async function load(){
  comparisonOptions();$('#page').classList.add('loading');
  try{
    const q=new URLSearchParams({from:$('#fromDate').value,to:$('#toDate').value,compare:$('#compareMode').value,trend:'visits'});
    const r=await fetch('/api/reports/advertising?'+q,{credentials:'same-origin',cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);payload=await r.json();render()
  }catch(e){console.error(e);$('#freshness').innerHTML='<span class="error">Не удалось загрузить рекламный отчёт.</span>'}
  finally{$('#page').classList.remove('loading')}
}
setDefaultDates();comparisonOptions();
$$('.chip').forEach(b=>b.addEventListener('click',()=>quickRange(b.dataset.range)));
['fromDate','toDate','compareMode'].forEach(id=>$('#'+id).addEventListener('change',load));
load();
