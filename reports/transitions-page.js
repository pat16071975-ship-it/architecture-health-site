const AUTH_USER='admin';
const AUTH_HASH='56c30f55c895d1d96f4b82f01c09e91f1e8cdc7eb77851f98a00cdd4d6aeddbf';
const SESSION_KEY='az-management-auth-v1';
const $=s=>document.querySelector(s);
let data=null;

const count=n=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(n||0);
const pct=n=>new Intl.NumberFormat('ru-RU',{style:'percent',maximumFractionDigits:1}).format(n||0);
const days=n=>n==null?'—':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(n)+' дн.';
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function sha256(s){const h=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s));return[...new Uint8Array(h)].map(x=>x.toString(16).padStart(2,'0')).join('')}

function levelData(){return data?.levels?.[$('#level').value]||null}
function cohortData(){return levelData()?.cohorts?.[$('#cohort').value]||{}}
function selectedSource(){return $('#source').value}
function windowKey(v){return v==='all'?'countAll':'count'+v}
function anyKey(v){return v==='all'?'anyAll':'any'+v}

function parseIsoDate(s){return s?new Date(s.slice(0,10)+'T00:00:00'):null}
function addDays(d,n){const x=new Date(d);x.setDate(x.getDate()+n);return x}
function completeForSource(sourceObj,n){
  if(!sourceObj||!n)return false;
  const anchor=parseIsoDate(sourceObj.anchorMax);
  const end=parseIsoDate(data.dataEnd);
  return !!anchor&&!!end&&addDays(anchor,n)<=end;
}
function completeness(n){
  if(n==='all')return{className:'observed',label:'до 31.08'};
  const cd=cohortData(),source=selectedSource();
  if(source!=='all')return completeForSource(cd[source],+n)
    ?{className:'complete',label:'полный'}
    :{className:'partial',label:'неполный'};
  const sources=Object.values(cd);
  return sources.length&&sources.every(s=>completeForSource(s,+n))
    ?{className:'complete',label:'полный'}
    :{className:'partial',label:'неполный'};
}
function badge(n){const s=completeness(n);return`<span class="badge ${s.className}">${s.label}</span>`}

function renderCohorts(){
  const old=$('#cohort').value||'2026-01';
  $('#cohort').innerHTML=data.months.map(m=>`<option value="${m.id}">${esc(m.label)}</option>`).join('');
  $('#cohort').value=data.months.some(m=>m.id===old)?old:'2026-01';
}
function renderSources(){
  const ld=levelData(),cd=cohortData(),old=$('#source').value;
  const available=(ld?.entities||[]).filter(x=>cd[x]);
  $('#source').innerHTML='<option value="all">Все исходные</option>'+available.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');
  if(available.includes(old)){$('#source').value=old;return}
  const preferred=$('#level').value==='direction'
    ?'Ортодонтия / функциональная стоматология'
    :'Зубачев Р. Н.';
  $('#source').value=available.includes(preferred)?preferred:(available[0]||'all');
}

function flattenPairs(){
  const cd=cohortData(),source=selectedSource(),rows=[];
  const sources=source==='all'?Object.keys(cd):[source];
  sources.forEach(src=>{
    const s=cd[src];if(!s)return;
    Object.entries(s.targets||{}).forEach(([target,t])=>{
      rows.push({source:src,target,cohort:s.cohort,...t});
    });
  });
  rows.sort((a,b)=>b.countAll-a.countAll||b.count90-a.count90||a.source.localeCompare(b.source,'ru')||a.target.localeCompare(b.target,'ru'));
  return rows;
}
function windowCell(n,cohort){
  const share=cohort?n/cohort:0;
  return`<span class="metric">${count(n)}</span><span class="metric-sub">${pct(share)} от когорты</span>`;
}

function renderKpis(){
  const cd=cohortData(),source=selectedSource();
  if(source!=='all'){
    const s=cd[source];
    if(!s){$('#kpis').innerHTML='';return}
    $('#kpis').innerHTML=`
      <div class="kpi"><span>Пациентов в исходной когорте</span><strong>${count(s.cohort)}</strong><small>${esc(source)}</small></div>
      <div class="kpi"><span>Перешли хотя бы к одному • 30 дней</span><strong>${count(s.any30)}</strong><small>${pct(s.cohort?s.any30/s.cohort:0)}</small></div>
      <div class="kpi"><span>Перешли хотя бы к одному • 60 дней</span><strong>${count(s.any60)}</strong><small>${pct(s.cohort?s.any60/s.cohort:0)}</small></div>
      <div class="kpi"><span>Перешли хотя бы к одному • 90 дней</span><strong>${count(s.any90)}</strong><small>${pct(s.cohort?s.any90/s.cohort:0)}</small></div>
      <div class="kpi"><span>Перешли за весь доступный период</span><strong>${count(s.anyAll)}</strong><small>медиана до первого другого: ${days(s.medianFirstOtherDays)}</small></div>`;
    return;
  }
  const pairs=flattenPairs();
  const sources=Object.keys(cd).length;
  const sum=k=>pairs.reduce((a,x)=>a+(x[k]||0),0);
  $('#kpis').innerHTML=`
    <div class="kpi"><span>Исходных ${$('#level').value==='direction'?'направлений':'врачей / исполнителей'}</span><strong>${count(sources)}</strong></div>
    <div class="kpi"><span>Пар с фактическими переходами</span><strong>${count(pairs.length)}</strong></div>
    <div class="kpi"><span>Учётов пациентов по парам • 30 дней</span><strong>${count(sum('count30'))}</strong><small>пациент может входить в несколько пар</small></div>
    <div class="kpi"><span>Учётов пациентов по парам • 90 дней</span><strong>${count(sum('count90'))}</strong><small>пациент может входить в несколько пар</small></div>
    <div class="kpi"><span>Учётов пациентов по парам • весь период</span><strong>${count(sum('countAll'))}</strong><small>не число уникальных пациентов клиники</small></div>`;
}

function renderDetails(){
  const source=selectedSource(),rows=flattenPairs(),monthLabel=$('#cohort option:checked').textContent;
  const includeSource=source==='all';
  const title=includeSource
    ?`Все переходы — ${monthLabel}`
    :`После приёма у «${source}» — ${monthLabel}`;
  let h=`<div class="panel">
    <div class="panel-head"><h2>${esc(title)}</h2><div class="panel-meta">Строки отсортированы по общему числу пациентов</div></div>
    <div class="panel-note">В каждой ячейке показаны уникальные пациенты и доля от когорты исходного врача/направления.</div>
    <div class="table-wrap"><table class="table"><thead><tr>`;
  if(includeSource)h+='<th>После приёма у</th>';
  h+='<th>Обратились к</th>';
  if(includeSource)h+='<th>Когорта</th>';
  h+=`<th class="window-head">30 дней ${badge(30)}</th>
      <th class="window-head">60 дней ${badge(60)}</th>
      <th class="window-head">90 дней ${badge(90)}</th>
      <th class="window-head">Общий срез ${badge('all')}</th>
      <th class="window-head">Медиана до обращения</th></tr></thead><tbody>`;
  if(!rows.length){
    h+='<tr><td class="empty" colspan="8">Переходов для выбранной когорты пока нет.</td></tr>';
  }else{
    rows.forEach(r=>{
      h+='<tr>';
      if(includeSource)h+=`<td class="source-name">${esc(r.source)}</td>`;
      h+=`<td class="target-name">${esc(r.target)}</td>`;
      if(includeSource)h+=`<td><span class="metric">${count(r.cohort)}</span></td>`;
      h+=`<td>${windowCell(r.count30,r.cohort)}</td>
          <td>${windowCell(r.count60,r.cohort)}</td>
          <td>${windowCell(r.count90,r.cohort)}</td>
          <td>${windowCell(r.countAll,r.cohort)}</td>
          <td><span class="metric">${days(r.medianDays)}</span><span class="metric-sub">среднее: ${days(r.avgDays)}</span></td>
        </tr>`;
    });
  }
  h+='</tbody></table></div></div>';
  $('#detailsPanel').innerHTML=h;
}

function renderMatrix(){
  const ld=levelData(),cd=cohortData(),w=$('#matrixWindow').value,key=windowKey(w),source=selectedSource();
  const rowEntities=(ld.entities||[]).filter(x=>cd[x]);
  const targetSet=new Set();
  rowEntities.forEach(src=>Object.keys(cd[src]?.targets||{}).forEach(t=>targetSet.add(t)));
  const colEntities=(ld.entities||[]).filter(x=>targetSet.has(x)||cd[x]);
  let max=0;
  rowEntities.forEach(src=>colEntities.forEach(t=>{max=Math.max(max,cd[src]?.targets?.[t]?.[key]||0)}));
  const periodLabel=$('#matrixWindow option:checked').textContent;
  let h=`<div class="panel">
    <div class="panel-head gold"><h2>Матрица переходов — ${esc(periodLabel)}</h2><div class="panel-meta">${badge(w)} • число и доля от исходной когорты</div></div>
    <div class="panel-note">Диагональ не считается. Ноль означает, что в доступных данных переходов по этой паре нет.</div>
    <div class="matrix-wrap"><table class="matrix"><thead><tr><th>После приёма у ↓ / обратились к →</th>`;
  colEntities.forEach(t=>h+=`<th>${esc(t)}</th>`);
  h+='</tr></thead><tbody>';
  rowEntities.forEach(src=>{
    const s=cd[src],highlight=source===src?' class="highlight"':'';
    h+=`<tr${highlight}><td>${esc(src)}<span class="metric-sub">когорта: ${count(s.cohort)}</span></td>`;
    colEntities.forEach(t=>{
      if(t===src){h+='<td class="diag">—</td>';return}
      const n=s.targets?.[t]?.[key]||0;
      if(!n){h+='<td class="zero">·</td>';return}
      const share=s.cohort?n/s.cohort:0;
      const alpha=max?(.10+.38*n/max):.1;
      const title=`${src} → ${t}: ${n} пациентов (${pct(share)})`;
      h+=`<td title="${esc(title)}" style="background:rgba(68,99,79,${alpha.toFixed(3)})"><div class="cell-count">${count(n)}</div><div class="cell-share">${pct(share)}</div></td>`;
    });
    h+='</tr>';
  });
  h+='</tbody></table></div></div>';
  $('#matrixPanel').innerHTML=h;
}

function renderPartialNotice(){
  const cd=cohortData(),source=selectedSource();
  const checks=[30,60,90].filter(n=>{
    if(source!=='all')return !completeForSource(cd[source],n);
    const list=Object.values(cd);
    return !list.length||!list.every(s=>completeForSource(s,n));
  });
  if(!checks.length){
    $('#partialNotice').classList.add('hidden');
    $('#partialNotice').textContent='';
    return;
  }
  const cohortLabel=$('#cohort option:checked').textContent;
  $('#partialNotice').textContent=`Для когорты «${cohortLabel}» срезы ${checks.join('/')} дней ещё не полностью созрели к дате окончания данных 31.08.2026. Показаны фактически успевшие состояться обращения; итог может увеличиться после добавления следующих месяцев.`;
  $('#partialNotice').classList.remove('hidden');
}

function renderStatus(){
  const q=data.quality||{};
  $('#status').textContent=`Источник заканчивается ${data.dataEnd.split('-').reverse().join('.')}. В расчёте: ${count(q.uniquePatients)} пациентов, ${count(q.patientDoctorDateEvents)} сочетаний «пациент–специалист–дата»; исключено тестовых ФИО: ${count(q.excludedObviousTestPatients||0)}.`;
}

function render(){
  if(!data){
    $('#missingData').classList.remove('hidden');
    $('#kpis').innerHTML='';
    $('#detailsPanel').innerHTML='';
    $('#matrixPanel').innerHTML='';
    return;
  }
  $('#missingData').classList.add('hidden');
  renderStatus();
  renderKpis();
  renderPartialNotice();
  renderDetails();
  renderMatrix();
}

async function showApp(){
  $('#loginView').classList.add('hidden');
  $('#appView').classList.remove('hidden');
  data=await window.AZTransitionsData.ready;
  if(!data)data=window.AZTransitionsData.get();
  if(data){
    renderCohorts();
    renderSources();
  }
  render();
}

async function login(e){
  e.preventDefault();
  const u=$('#loginName').value.trim(),p=$('#loginPassword').value;
  const h=await sha256(u+'|'+p);
  if(u===AUTH_USER&&h===AUTH_HASH){
    sessionStorage.setItem(SESSION_KEY,'1');
    await showApp();
  }else{
    $('#loginError').textContent='Неверный логин или пароль';
  }
}

function init(){
  $('#loginForm').addEventListener('submit',login);
  $('#logoutBtn').addEventListener('click',()=>{sessionStorage.removeItem(SESSION_KEY);location.reload()});
  $('#level').addEventListener('change',()=>{renderSources();render()});
  $('#cohort').addEventListener('change',()=>{renderSources();render()});
  $('#source').addEventListener('change',render);
  $('#matrixWindow').addEventListener('change',render);
  window.addEventListener('az-transitions-ready',e=>{data=e.detail;renderCohorts();renderSources();render()});
  if(sessionStorage.getItem(SESSION_KEY)==='1')showApp();
}
init();
