
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const money=v=>v==null?'Нет данных':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(v)+' ₽';
const number=v=>v==null?'Нет данных':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(v);
const pct=v=>v==null?'—':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(v)+'%';
const iso=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
const sameMonth=(a,b)=>a.slice(0,7)===b.slice(0,7);
let payload=null;

function h(s){return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;')}
function setDefaultDates(){const n=new Date();$('#fromDate').value=iso(new Date(n.getFullYear(),n.getMonth(),1));$('#toDate').value=iso(n)}
function quickRange(k){const n=new Date();let a,b;if(k==='month'){a=new Date(n.getFullYear(),n.getMonth(),1);b=n}else if(k==='prev-month'){a=new Date(n.getFullYear(),n.getMonth()-1,1);b=new Date(n.getFullYear(),n.getMonth(),0)}else if(k==='quarter'){a=new Date(n.getFullYear(),Math.floor(n.getMonth()/3)*3,1);b=n}else{a=new Date(n.getFullYear(),0,1);b=n}$('#fromDate').value=iso(a);$('#toDate').value=iso(b);load()}
function comparisonOptions(){const old=$('#compareMode').value||'previous_period',month=sameMonth($('#fromDate').value,$('#toDate').value);let o=[['previous_period','Предыдущий сопоставимый период']];if(month)o.push(['previous_quarter_avg','Среднее предыдущего квартала'],['year_avg','Среднее текущего года']);o.push(['last_year','Тот же период прошлого года']);$('#compareMode').innerHTML=o.map(x=>'<option value="'+x[0]+'">'+x[1]+'</option>').join('');$('#compareMode').value=o.some(x=>x[0]===old)?old:'previous_period'}
function dclass(metric,v){if(v==null)return'neutral';if(metric==='spend')return v<=0?'good':'bad';return v>=0?'good':'bad'}
function compareCard(metric,fmt){const c=payload?.compare?.summary?.[metric],d=payload?.compare?.delta_pct?.[metric];return '<div class="compare-card"><div class="clabel">'+h(payload?.compare?.label||'Сравнение')+'</div><div class="cvalue">'+fmt(c)+'</div><div class="delta '+dclass(metric,d)+'">'+(d==null?'—':(d>0?'+':'')+pct(d))+'</div></div>'}

function mainKpis(){
  const s=payload.summary||{},items=[
    ['spend','Расходы на рекламу',money],
    ['clicks','Клики по рекламе',number],
    ['visits','Посещения сайта',number],
    ['target_leads','Целевые лиды',number],
    ['primary_visits','Состоявшиеся первичные приёмы',number]
  ];
  $('#kpis').innerHTML=items.map(x=>{
    const key=x[0],label=x[1],fmt=x[2];
    const extra=key==='target_leads'&&s.lead_to_primary_pct!=null?'Дошли хотя бы до одного первичного: <b>'+pct(s.lead_to_primary_pct)+'</b>':'';
    return '<div class="kpi-wrap"><div class="kpi"><div class="name">'+label+'</div><div class="value">'+fmt(s[key])+'</div><div class="extra">'+extra+'</div></div>'+compareCard(key,fmt)+'</div>'
  }).join('')
}
function moneyCards(){
  const s=payload.summary||{},services=s.primary_services!=null&&s.primary_paid!=null&&Math.abs(s.primary_services-s.primary_paid)>.5?'Оказано услуг на '+money(s.primary_services):'';
  $('#primaryMoney').innerHTML='<div class="kpi-wrap"><div class="kpi"><div class="name">Выручка первичных приёмов — фактически получено</div><div class="value">'+money(s.primary_paid)+'</div><div class="extra">'+services+'</div></div>'+compareCard('primary_paid',money)+'</div>';
  $('#cohortCard').innerHTML='<div class="name">Выручка новых пациентов за 30 дней</div><div class="value">'+money(s.new_patient_30d_paid)+'</div><div class="extra">Только фактически полученные деньги. Новых пациентов в выбранной когорте: <b>'+number(s.new_patients)+'</b></div>'
}
function activity(){
  const a=payload.activity||{},items=[['Первичные',a.primary_visits,a.primary_paid],['Повторные',a.repeat_visits,a.repeat_paid],['Всего',a.total_visits,a.total_paid]];
  $('#activity').innerHTML=items.map(x=>'<div class="activity-card"><div class="a-title">'+x[0]+'</div><strong>'+number(x[1])+' приёмов</strong><strong>'+money(x[2])+'</strong></div>').join('')
}

function lineChart(){
  const cur=payload?.trend?.current||[],cmp=payload?.trend?.compare||[],vals=[...cur,...cmp].map(x=>x.value).filter(v=>v!=null),box=$('#trendChart');
  if(!vals.length){box.innerHTML='<div class="empty">Нет данных за выбранный период</div>';$('#trendLegend').innerHTML='';return}
  const w=1000,H=210,p=28,max=Math.max(...vals,1),x=i=>p+i*(w-2*p)/Math.max(cur.length-1,1),y=v=>H-p-v*(H-2*p)/max;
  const path=arr=>arr.map((d,i)=>d.value==null?null:[i,d.value]).filter(Boolean).map((a,j)=>(j?'L':'M')+x(a[0]).toFixed(1)+','+y(a[1]).toFixed(1)).join(' ');
  box.innerHTML='<svg viewBox="0 0 '+w+' '+H+'" preserveAspectRatio="none"><line x1="'+p+'" y1="'+(H-p)+'" x2="'+(w-p)+'" y2="'+(H-p)+'" stroke="#dce8f2"/><path d="'+path(cmp)+'" fill="none" stroke="#debc19" stroke-width="3"/><path d="'+path(cur)+'" fill="none" stroke="#2080c4" stroke-width="3"/></svg>';
  $('#trendLegend').innerHTML='<span><i style="background:#2080c4"></i>Выбранный период</span><span><i style="background:#debc19"></i>'+h(payload.compare.label)+'</span>'
}

const GROUP_ORDER={'Платная реклама':1,'Поиск':2,'Карты':3,'Прямые заходы':4,'Другие источники':5};
const METRICS=['spend','visits','target_leads','primary_visits','primary_paid','total_paid'];

function metricSum(rows,key){
  const vals=rows.map(r=>r?.[key]).filter(v=>v!=null);
  return vals.length?vals.reduce((a,b)=>a+b,0):null
}
function summarizeRows(rows){
  const out={};
  METRICS.forEach(k=>out[k]=metricSum(rows,k));
  return out
}
function sourceGroups(){
  const sources=payload?.sources||[],campaigns=payload?.campaigns||[];
  const byChannel=new Map();
  sources.forEach(row=>{
    const label=(row.channel_name||row.source_name||'Другие источники').trim()||'Другие источники';
    if(!byChannel.has(label))byChannel.set(label,[]);
    byChannel.get(label).push(row)
  });
  return [...byChannel.entries()].map(([label,rows])=>{
    const campaignChildren=campaigns.filter(c=>{
      if((c.channel_name||'').trim()===label)return true;
      return rows.some(r=>r.source_key&&r.source_key===c.source_key)
    });
    let children=rows;
    if(label==='Платная реклама'&&campaignChildren.length)children=campaignChildren;
    const meaningful=children.length>1||children.some(c=>(c.campaign_name||'').trim()&&c.campaign_name!==label)||children.some(c=>(c.source_name||'').trim()!==label);
    return {label,summary:summarizeRows(rows),children:meaningful?children:[]}
  }).sort((a,b)=>(GROUP_ORDER[a.label]||99)-(GROUP_ORDER[b.label]||99)||a.label.localeCompare(b.label,'ru'))
}
function rowCells(r){
  return '<td>'+money(r.spend)+'</td><td>'+number(r.visits)+'</td><td>'+number(r.target_leads)+'</td><td>'+number(r.primary_visits)+'</td><td>'+money(r.primary_paid)+'</td><td>'+money(r.total_paid)+'</td>'
}
function childLabel(row){
  return (row.campaign_name||row.source_name||row.channel_name||'Источник не определён').trim()
}
function sourceTable(){
  const groups=sourceGroups(),body=$('#sourceRows');
  if(!groups.length){body.innerHTML='<tr><td colspan="7">Нет данных</td></tr>';return}
  let html='';
  groups.forEach((g,i)=>{
    const expandable=g.children.length>0;
    html+='<tr class="group-row"><td><button class="group-btn '+(expandable?'':'no-children')+'" data-group="'+i+'" '+(expandable?'':'disabled')+'>'+(expandable?'▸ ':'')+h(g.label)+'</button></td>'+rowCells(g.summary)+'</tr>';
    if(expandable){
      g.children.forEach((r,j)=>{
        html+='<tr class="child-row child-'+i+'" hidden><td>'+h(childLabel(r))+'</td>'+rowCells(r)+'</tr>'
      })
    }
  });
  body.innerHTML=html;
  $$('.group-btn:not(.no-children)').forEach(btn=>btn.addEventListener('click',()=>{
    const idx=btn.dataset.group,rows=$$('.child-'+idx),open=rows.length&&!rows[0].hidden;
    rows.forEach(r=>r.hidden=open);
    btn.textContent=(open?'▸ ':'▾ ')+groups[+idx].label
  }))
}

function moneyBarChart(){
  const campaignMode=$('#breakdown').value==='campaign';
  let rows;
  if(campaignMode){
    rows=payload?.campaigns||[]
  }else{
    rows=sourceGroups().map(g=>({source_name:g.label,...g.summary}))
  }
  const data=rows.filter(r=>(r.spend||0)>0||(r.total_paid||0)>0).slice(0,8),box=$('#moneyChart');
  if(!data.length){box.innerHTML='<div class="empty">Нет данных</div>';return}
  const w=1000,H=220,p=28,slot=(w-2*p)/data.length,max=Math.max(...data.flatMap(r=>[r.spend||0,r.total_paid||0]),1),bh=v=>(H-43)*(v/max);
  let bars='';
  data.forEach((r,i)=>{
    const x=p+i*slot+slot*.18,bw=slot*.25,sh=bh(r.spend||0),rh=bh(r.total_paid||0),label=h((r.campaign_name||r.source_name||'').slice(0,18));
    bars+='<rect x="'+x+'" y="'+(H-28-sh)+'" width="'+bw+'" height="'+sh+'" fill="#debc19" rx="2"/><rect x="'+(x+bw+4)+'" y="'+(H-28-rh)+'" width="'+bw+'" height="'+rh+'" fill="#2080c4" rx="2"/><text x="'+x+'" y="'+(H-9)+'" font-size="9" fill="#426174">'+label+'</text>'
  });
  box.innerHTML='<svg viewBox="0 0 '+w+' '+H+'" preserveAspectRatio="none"><line x1="'+p+'" y1="'+(H-28)+'" x2="'+(w-p)+'" y2="'+(H-28)+'" stroke="#dce8f2"/>'+bars+'</svg>'
}

function freshness(){
  const items=payload.freshness||[];
  $('#freshness').textContent=items.length?'Актуальность источников: '+items.map(x=>x.label+' — '+(String(x.generated_at||'').slice(0,10)||'нет данных')).join(' · '):'Данные для рекламного отчёта ещё не загружены.'
}
function render(){mainKpis();moneyCards();activity();lineChart();moneyBarChart();sourceTable();freshness()}
async function load(){
  comparisonOptions();$('#page').classList.add('loading');
  try{
    const q=new URLSearchParams({from:$('#fromDate').value,to:$('#toDate').value,compare:$('#compareMode').value,trend:$('#trendMetric').value});
    const r=await fetch('/api/reports/advertising?'+q,{credentials:'same-origin',cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    payload=await r.json();render()
  }catch(e){console.error(e);$('#freshness').innerHTML='<span class="error">Не удалось загрузить рекламный отчёт.</span>'}
  finally{$('#page').classList.remove('loading')}
}
setDefaultDates();comparisonOptions();
$$('.chip').forEach(b=>b.addEventListener('click',()=>quickRange(b.dataset.range)));
['fromDate','toDate','compareMode','trendMetric'].forEach(id=>$('#'+id).addEventListener('change',load));
$('#breakdown').addEventListener('change',moneyBarChart);
load();
