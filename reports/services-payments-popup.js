(() => {
  const frame = document.getElementById('serviceFrame');
  if (!frame) return;
  const DATA_KEY='az-service-analytics-v1';
  const SALARY_KEY='az-service-salary-v1';
  const EXTRA_KEY='az-service-extra-payments-v1';
  let obs=null;

  frame.addEventListener('load',()=>{
    const w=frame.contentWindow,d=frame.contentDocument;
    if(!w||!d)return;
    installStyle(d);
    installModal(w,d);
    if(obs)obs.disconnect();
    obs=new MutationObserver(()=>setTimeout(()=>refresh(w,d),0));
    obs.observe(d.body,{childList:true,subtree:true});
    setTimeout(()=>refresh(w,d),0);
  });

  function installStyle(d){
    if(d.getElementById('azPayPopupStyle'))return;
    const s=d.createElement('style');s.id='azPayPopupStyle';s.textContent=`
      #azFinanceHost>.az-finance{display:none!important}
      .az-econ-v2{margin-top:8px!important}
      .az-econ-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 12px;background:#efe5d2}
      .az-econ-v2.dent .az-econ-head{background:#dfe9f2}.az-econ-v2.clinic .az-econ-head{background:#dfe9e2}.az-econ-v2.lab .az-econ-head{background:#f1dfc9}
      .az-econ-head h2{margin:0!important;padding:0!important;background:transparent!important}
      .az-econ-note{padding:7px 12px;font-size:10px;color:#6f6658;background:#fffaf0;border-top:1px solid #eadfc9}
      .az-econ-good{color:#32724b;font-weight:700}.az-econ-bad{color:#9a4b48;font-weight:700}
      .az-pay-backdrop{position:fixed;inset:0;background:rgba(36,42,38,.46);display:grid;place-items:center;padding:18px;z-index:12000}.az-pay-hide{display:none!important}
      .az-pay-modal{width:min(760px,96vw);max-height:90vh;overflow:auto;background:#fffdf8;border:1px solid #d8cdbb;border-radius:16px;box-shadow:0 24px 70px rgba(38,34,27,.25)}
      .az-pay-head{display:flex;justify-content:space-between;gap:12px;padding:15px 18px 11px;border-bottom:1px solid #e6ded1}.az-pay-head h3{margin:0;font:700 23px/1.1 'Cormorant Garamond',serif}.az-pay-head small{color:#6d746c}
      .az-pay-body{padding:12px 18px}.az-pay-note{font-size:10px;color:#6f6658;margin:0 0 10px}.az-pay-table{width:100%;border-collapse:collapse}.az-pay-table th,.az-pay-table td{padding:7px 8px;border-bottom:1px solid #eee7dc;font-size:11px;text-align:right}.az-pay-table th:first-child,.az-pay-table td:first-child{text-align:left}.az-pay-table th{background:#faf6ef}.az-pay-table input{width:120px;text-align:right;border:1px solid #d7cdbd;border-radius:7px;padding:6px 7px;font:600 11px Inter;background:#fff}.az-pay-total{font-weight:700}
      .az-pay-actions{display:flex;justify-content:flex-end;gap:8px;padding:12px 18px 16px;border-top:1px solid #e6ded1}.az-pay-x{border:0;background:transparent;font-size:23px;cursor:pointer;color:#5f675f}
      .az-compare-econ{margin-bottom:8px!important}.az-compare-econ .table{min-width:850px!important}
    `;d.head.appendChild(s);
  }

  function installModal(w,d){
    if(d.getElementById('azPayModal'))return;
    const el=d.createElement('div');el.id='azPayModal';el.className='az-pay-backdrop az-pay-hide';el.innerHTML=`<div class="az-pay-modal"><div class="az-pay-head"><div><h3>ЗП и иные выплаты</h3><small id="azPayDoctor"></small></div><button class="az-pay-x" id="azPayX">×</button></div><div class="az-pay-body"><p class="az-pay-note">В «Иные выплаты» заноси отпускные, больничные, премии и другие доплаты. Пустое поле доплат считается нулём. Если основная ЗП не заполнена, прибыльность за этот период не считается.</p><div id="azPayTable"></div></div><div class="az-pay-actions"><button class="btn" id="azPayCancel">Отмена</button><button class="btn primary" id="azPaySave">Сохранить</button></div></div>`;d.body.appendChild(el);
    el.addEventListener('click',e=>{if(e.target===el)close(d)});d.getElementById('azPayX').onclick=()=>close(d);d.getElementById('azPayCancel').onclick=()=>close(d);
  }

  function open(w,d){
    const data=getData(w),direction=d.getElementById('direction')?.value,doctor=d.getElementById('doctor')?.value;if(!data||!direction||!doctor||doctor==='all')return;
    const months=data.months||[],salary=getArr(w,SALARY_KEY,direction,doctor,months.length,null),extras=getArr(w,EXTRA_KEY,direction,doctor,months.length,0);
    d.getElementById('azPayDoctor').textContent=doctor;d.getElementById('azPayTable').innerHTML=`<table class="az-pay-table"><thead><tr><th>Месяц</th><th>Основная ЗП</th><th>Иные выплаты</th><th>Всего</th></tr></thead><tbody>${months.map((m,i)=>`<tr><td>${esc(m)}</td><td><input class="az-base" data-i="${i}" value="${salary[i]??''}" inputmode="decimal"></td><td><input class="az-extra" data-i="${i}" value="${extras[i]??''}" placeholder="0" inputmode="decimal"></td><td class="az-pay-total" data-t="${i}">${money((+salary[i]||0)+(+extras[i]||0))}</td></tr>`).join('')}</tbody></table>`;
    d.getElementById('azPayModal').classList.remove('az-pay-hide');
    const recalc=i=>{const a=parse(d.querySelector(`.az-base[data-i="${i}"]`)?.value,null),b=parse(d.querySelector(`.az-extra[data-i="${i}"]`)?.value,0);d.querySelector(`[data-t="${i}"]`).textContent=money((a||0)+(b||0))};d.querySelectorAll('.az-base,.az-extra').forEach(x=>x.oninput=()=>recalc(+x.dataset.i));
    const old=d.getElementById('azPaySave'),btn=old.cloneNode(true);old.replaceWith(btn);btn.onclick=()=>{saveArr(w,SALARY_KEY,direction,doctor,months.map((_,i)=>parse(d.querySelector(`.az-base[data-i="${i}"]`)?.value,null)));saveArr(w,EXTRA_KEY,direction,doctor,months.map((_,i)=>parse(d.querySelector(`.az-extra[data-i="${i}"]`)?.value,0)));close(d);refresh(w,d)};
  }
  function close(d){d.getElementById('azPayModal')?.classList.add('az-pay-hide')}

  function refresh(w,d){
    const data=getData(w);if(!data)return;const compare=d.querySelector('[data-az-tab="compare"].active');
    if(compare){renderCompareEconomics(w,d);return}
    d.getElementById('azCompareEconomics')?.remove();
    if(d.querySelector('[data-tab="doctor"].active'))renderDoctorEconomics(w,d);else d.getElementById('azEconomicsV2')?.remove();
  }

  function renderDoctorEconomics(w,d){
    const data=getData(w),direction=d.getElementById('direction')?.value,doctor=d.getElementById('doctor')?.value,dir=data?.directions?.[direction],host=d.getElementById('azFinanceHost');if(!dir||!host||!doctor||doctor==='all')return;
    const months=data.months||[],salary=getArr(w,SALARY_KEY,direction,doctor,months.length,null),extras=getArr(w,EXTRA_KEY,direction,doctor,months.length,0),groups=groupsFor(data,d.getElementById('grouping')?.value||'month'),focus=d.getElementById('focusPeriod')?.value||'all',all=months.map((_,i)=>i);
    let box=d.getElementById('azEconomicsV2');if(!box){box=d.createElement('div');box.id='azEconomicsV2';host.appendChild(box)}box.className=`panel ${panelClass(direction)} az-econ-v2`;
    const row=(label,key,fmt,color=false)=>`<tr><td>${label}</td>${groups.map(g=>cell(econ(dir,doctor,salary,extras,g.idx)[key],fmt,color,focus===g.id)).join('')}<td class="total-col">${cell(econ(dir,doctor,salary,extras,all)[key],fmt,color)}</td></tr>`;
    box.innerHTML=`<div class="az-econ-head"><h2>Экономика врача — ${esc(doctor)}</h2><button id="azPayOpen" class="btn primary">ЗП и выплаты</button></div><div class="az-econ-note">Прибыль и маржинальность считаются после основной ЗП и иных выплат врачу, но до материалов, лаборатории и прочих расходов клиники.</div><div class="table-wrap"><table class="table"><thead><tr><th>Показатель</th>${groups.map(g=>`<th class="${focus===g.id?'focus':''}">${esc(g.label)}</th>`).join('')}<th class="total-col">Итого</th></tr></thead><tbody>${row('Выручка врача','revenue',money)}${row('Основная ЗП','salary',money)}${row('Иные выплаты','extras',money)}${row('Всего выплат врачу','total',money)}${row('Выплаты / выручка, %','share',percent)}${row('Прибыль после выплат врачу','profit',money,true)}${row('Маржинальность после выплат, %','margin',percent,true)}</tbody></table></div>`;d.getElementById('azPayOpen').onclick=()=>open(w,d);
    const idx=focusIdx(data,d),e=econ(dir,doctor,salary,extras,idx),k=d.getElementById('kpis');if(k)k.innerHTML=`<div class="kpi"><span>Выручка врача</span><strong>${money(e.revenue)}</strong></div><div class="kpi"><span>Всего выплат врачу</span><strong>${e.total==null?'—':money(e.total)}</strong></div><div class="kpi"><span>Прибыль после выплат</span><strong class="${cls(e.profit)}">${e.profit==null?'—':money(e.profit)}</strong></div><div class="kpi"><span>Маржинальность</span><strong class="${cls(e.margin)}">${e.margin==null?'—':percent(e.margin)}</strong></div>`;
  }

  function renderCompareEconomics(w,d){
    d.getElementById('azEconomicsV2')?.remove();const data=getData(w),direction=d.getElementById('direction')?.value,dir=data?.directions?.[direction],a=d.getElementById('doctor')?.value,b=d.getElementById('azDoctor2')?.value,content=d.getElementById('content');if(!dir||!a||!b||!content)return;const months=data.months||[],idx=focusIdx(data,d),ea=econ(dir,a,getArr(w,SALARY_KEY,direction,a,months.length,null),getArr(w,EXTRA_KEY,direction,a,months.length,0),idx),eb=econ(dir,b,getArr(w,SALARY_KEY,direction,b,months.length,null),getArr(w,EXTRA_KEY,direction,b,months.length,0),idx);
    let box=d.getElementById('azCompareEconomics');if(!box){box=d.createElement('div');box.id='azCompareEconomics';content.prepend(box)}box.className=`panel ${panelClass(direction)} az-compare-econ`;box.innerHTML=`<h2>Экономика двух врачей</h2><div class="az-econ-note">Иные выплаты включают отпускные, больничные, премии и доплаты.</div><div class="table-wrap"><table class="table"><thead><tr><th>Врач</th><th>Выручка</th><th>Основная ЗП</th><th>Иные выплаты</th><th>Всего выплат</th><th>Прибыль после выплат</th><th>Маржинальность</th></tr></thead><tbody>${econRow(a,ea)}${econRow(b,eb)}</tbody></table></div>`;
  }

  function econ(dir,doctor,salary,extras,idx){const revenue=revenueFor(dir,doctor,idx),s=idx.map(i=>salary[i]),complete=s.every(v=>v!==null&&v!==''&&v!==undefined&&Number.isFinite(+v));if(!complete)return{revenue,salary:null,extras:null,total:null,share:null,profit:null,margin:null};const base=s.reduce((x,v)=>x+(+v||0),0),extra=idx.reduce((x,i)=>x+(+extras[i]||0),0),total=base+extra,profit=revenue-total;return{revenue,salary:base,extras:extra,total,share:revenue?total/revenue:null,profit,margin:revenue?profit/revenue:null}}
  function revenueFor(dir,doctor,idx){let r=0;Object.values(dir?.doctors?.[doctor]||{}).forEach(arr=>{if(Array.isArray(arr))idx.forEach(i=>r+=+(arr[i]?.[1]||0))});return r}
  function groupsFor(data,g){const n=data.months?.length||0;let x=[];if(g==='month')x=data.months.map((label,i)=>({id:'m'+i,label,idx:[i],expected:1}));if(g==='quarter')x=[{id:'q1',label:'I квартал',idx:[0,1,2],expected:3},{id:'q2',label:'II квартал',idx:[3,4,5],expected:3},{id:'q3',label:'III квартал',idx:[6,7,8],expected:3},{id:'q4',label:'IV квартал',idx:[9,10,11],expected:3}];if(g==='half')x=[{id:'h1',label:'I полугодие',idx:[0,1,2,3,4,5],expected:6},{id:'h2',label:'II полугодие',idx:[6,7,8,9,10,11],expected:6}];return x.map(a=>({...a,idx:a.idx.filter(i=>i<n)})).filter(a=>a.idx.length).map(a=>({...a,label:a.idx.length<a.expected?a.label+' (неполный)':a.label}))}
  function focusIdx(data,d){const gs=groupsFor(data,d.getElementById('grouping')?.value||'month'),id=d.getElementById('focusPeriod')?.value||'all';return gs.find(g=>g.id===id)?.idx||data.months.map((_,i)=>i)}
  function getArr(w,key,direction,doctor,n,fill){try{const a=JSON.parse(w.localStorage.getItem(key)||'{}')?.[direction]?.[doctor],out=Array.isArray(a)?a.slice(0,n):[];while(out.length<n)out.push(fill);return out}catch{return Array(n).fill(fill)}}
  function saveArr(w,key,direction,doctor,arr){let s={};try{s=JSON.parse(w.localStorage.getItem(key)||'{}')}catch{}s[direction]||={};s[direction][doctor]=arr;w.localStorage.setItem(key,JSON.stringify(s))}
  function parse(v,blank){const s=String(v??'').trim().replace(/\s/g,'').replace(',','.');if(!s)return blank;const n=Number(s);return Number.isFinite(n)?n:blank}
  function cell(v,fmt,color,focus){return`<td class="${focus?'focus ':''}${color?cls(v):''}">${v==null?'—':fmt(v)}</td>`}
  function cls(v){return v==null?'':v>=0?'az-econ-good':'az-econ-bad'}
  function econRow(name,e){return`<tr><td>${esc(name)}</td><td>${money(e.revenue)}</td><td>${e.salary==null?'—':money(e.salary)}</td><td>${e.extras==null?'—':money(e.extras)}</td><td>${e.total==null?'—':money(e.total)}</td><td class="${cls(e.profit)}">${e.profit==null?'—':money(e.profit)}</td><td class="${cls(e.margin)}">${e.margin==null?'—':percent(e.margin)}</td></tr>`}
  function money(n){return new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(n||0)+' ₽'}function percent(n){return new Intl.NumberFormat('ru-RU',{style:'percent',maximumFractionDigits:1}).format(n)}function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function panelClass(x){return x==='Стоматология'?'dent':x==='Клиника'?'clinic':'lab'}
})();
