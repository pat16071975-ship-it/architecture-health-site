'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const index=fs.readFileSync(path.join(root,'reports/index.html'),'utf8');
const view=fs.readFileSync(path.join(root,'reports/period-view.js'),'utf8');
const slice=(s,a,b)=>{const x=s.indexOf(a),y=s.indexOf(b,x);assert(x>=0&&y>x,a);return s.slice(x,y)};
const inline=index.match(/<script>\s*([\s\S]*?)<\/script>/)[1].replace(/\ninit\(\);\s*$/,'');
const helpers=slice(view,'  const fmtMoney=','  const toolbar=');
const rendering=slice(view,'  const monthNames=','  function cashSplit(')+slice(view,'  function monthlyReportColumn(','  let dateStickyRaf=');
let passed=0;
const test=(name,fn)=>{fn();console.log('PASS '+name);passed++};
class Element{
 constructor(){this.children=[];this.value='';this.dataset={};this.readOnly=true;this.textContent='';}
 set innerHTML(v){this._html=v;this.children=[];}get innerHTML(){return this._html||'';}
 appendChild(v){this.children.push(v);}
}
function harness(){
 const store={},nodes={},node=id=>nodes[id]||(nodes[id]=new Element());
 const keys=['paidPatients','paidDentPatients','paidClinicPatients','avgCheck','dentAvg','clinicAvg','dentRev','clinicRev','cashTotal','cashOOO','cashIP','primary','repeat','labOrders','labRevenue','labAvg'];
 const metrics=keys.map(key=>Object.assign(new Element(),{dataset:{key,type:'money'}}));
 const ctx={Date,Map,Set,Number,String,Array,JSON,Math,console,
  localStorage:{getItem:()=>JSON.stringify(store)},DOCTOR_PAID_SNAPSHOTS:{},DOCTOR_PAID_PERIODS:[],PAID_PATIENTS_MONTHLY:{},
  queueDateStickyClone(){},document:{querySelector:s=>node(s.slice(1)),getElementById:node,createElement:()=>new Element(),
   querySelectorAll:s=>s==='[data-key]'?metrics:[]}};
 ctx.window=ctx;vm.createContext(ctx);vm.runInContext(inline,ctx);
 vm.runInContext("dentists.splice(0,dentists.length,'D');clinicDocs.splice(0,clinicDocs.length,'C');",ctx);
 vm.runInContext(helpers+'\n'+rendering,ctx);
 node('reportDate').value='2026-10-27';node('dateViewMode').value='compare';node('compareRange').value='ytd';
 function add(m,day,{raw=true,patients=10,dent=4,clinic=8,dentMoney=100,clinicMoney=200}={}){
  const month=`2026-${String(m).padStart(2,'0')}`,date=`${month}-${String(day).padStart(2,'0')}`;
  ctx.PAID_PATIENTS_MONTHLY[month]={asOf:date,medicine:patients,dent,structure:clinic};
  ctx.DOCTOR_PAID_PERIODS.push({from:month+'-01',to:date,dentists:{D:dentMoney},clinicDocs:{C:clinicMoney}});
  if(raw)store[date]={date,factMedicine:1000,factLab:200,cashTotal:1200,cashOOO:700,cashIP:500,
   primary:3,repeat:7,labOrders:2,labRevenue:200,dentists:{D:900},clinicDocs:{C:800},
   paidPatientEvidenceComplete:true,paidPatientPeriodFrom:month+'-01',paidPatientPeriodTo:date,
   paidPatients:patients,paidDentPatients:dent,paidClinicPatients:clinic};
  return date;
 }
 function cell(label,column){
  const row=node('dateCompareBody').children.find(r=>r.innerHTML.startsWith('<td>'+label+'</td>'));assert(row,label);
  return [...row.innerHTML.matchAll(/<td[^>]*>(.*?)<\/td>/g)][column+1][1];
 }
 return {ctx,store,node,add,metrics,cell,run:code=>vm.runInContext(code,ctx),
  value:key=>metrics.find(e=>e.dataset.key===key).value,
  fill:date=>ctx.fillRecord(store[date]||vm.runInContext(`blankRecord('${date}')`,ctx)),render:()=>ctx.renderDateComparison(),
  money:v=>vm.runInContext(`money(${JSON.stringify(v)})`,ctx)};
}
const empty='<span class="empty">—</span>';
test('exactly two approved options; no third user-facing mode',()=>{
 const s=view.match(/<select id="dateViewMode">([\s\S]*?)<\/select>/)[1];
 assert.equal((s.match(/<option /g)||[]).length,2);
 assert(s.includes('value="compare" selected>Сравнение'));assert(s.includes('value="single">На дату'));assert(!s.includes('monthly'));
});
test('both existing views: January through October exact evidence, all linked averages',()=>{
 const h=harness(),ends=[31,28,31,30,31,30,31,31,30,27],before=[];
 ends.forEach((d,i)=>{const date=h.add(i+1,d,{patients:10+i,dent:4+i,clinic:8+i});before.push(JSON.stringify(h.store[date]));});
 ends.forEach((d,i)=>{
  const date=`2026-${String(i+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
  h.node('reportDate').value=date;h.render();h.fill(date);
  assert.equal(h.value('paidPatients'),h.money(10+i));
  assert.equal(h.value('avgCheck'),h.money(1000/(10+i)));
  assert.equal(h.value('dentRev'),h.money(100));assert.equal(h.value('clinicRev'),h.money(200));
  assert.equal(h.value('dentAvg'),h.money(100/(4+i)));assert.equal(h.value('clinicAvg'),h.money(200/(8+i)));
  assert.equal(h.cell('Средний чек стоматологии на оплатившего пациента',i),h.money(100/(4+i)));
  assert.equal(h.cell('Средний чек клиники на оплатившего пациента',i),h.money(200/(8+i)));
  assert.equal(JSON.stringify(h.store[date]),before[i]);
 });
});
test('day 27 must not manufacture historical day-27 payers from final months',()=>{
 const h=harness();[31,28,31,30,31,30,31,31,30,27].forEach((d,i)=>h.add(i+1,d));h.render();
 for(let i=0;i<9;i++)assert.equal(h.cell('Оплатившие пациенты',i),empty);
 assert.equal(h.cell('Оплатившие пациенты',9),h.money(10).replace(/ ₽$/, ''));
});
test('existing date31 comparison exposes all nine closed months, not invented October31',()=>{
 const h=harness();[31,28,31,30,31,30,31,31,30,27].forEach((d,i)=>h.add(i+1,d));
 h.node('reportDate').value='2026-10-31';h.render();
 for(let i=0;i<9;i++)assert.equal(h.cell('Средний чек стоматологии на оплатившего пациента',i),h.money(225));
 assert.equal(h.cell('Оплатившие пациенты',9),empty);
});
test('paid-only exact source renders payers and direction averages without fabricated clinic cash',()=>{
 const h=harness(),date=h.add(1,31,{raw:false});h.fill(date);h.node('reportDate').value=date;h.render();
 assert.equal(h.value('paidPatients'),h.money(10));assert.equal(h.value('dentAvg'),h.money(null));
 assert.equal(h.value('clinicAvg'),h.money(null));assert.equal(h.value('cashTotal'),h.money(null));
 assert.equal(h.value('avgCheck'),h.money(null));assert.equal(h.cell('Средний чек стоматологии на оплатившего пациента',0),empty);
});
test('old paid snapshot does not supply numerator for newer payer date',()=>{
 const h=harness(),date=h.add(1,27);h.ctx.DOCTOR_PAID_PERIODS.length=0;
 h.ctx.DOCTOR_PAID_SNAPSHOTS['2026-01']=[{asOf:'2026-01-20',dentists:{D:111},clinicDocs:{C:222}}];
 h.fill(date);h.node('reportDate').value=date;h.render();
 assert.equal(h.value('dentRev'),h.money(111));assert.equal(h.value('dentAvg'),h.money(null));
 assert.equal(h.cell('Средний чек стоматологии на оплатившего пациента',0),empty);
});
test('known older cash date cannot supply medicine average for newer payer period',()=>{
 const h=harness(),date=h.add(1,27);h.store[date]._cashAsOf='2026-01-20';h.fill(date);
 assert.equal(h.value('avgCheck'),h.money(null));assert.equal(h.value('cashTotal'),h.money(1200));
});
test('cash, legal split, laboratory and clinical counts retain their values',()=>{
 const h=harness(),date=h.add(1,31);h.fill(date);
 for(const [k,v] of Object.entries({cashTotal:1200,cashOOO:700,cashIP:500,primary:3,repeat:7,labOrders:2,labRevenue:200,labAvg:100}))assert.equal(h.value(k),h.money(v),k);
});
test('missing medicine amount does not become a zero average',()=>{
 const h=harness(),date=h.add(1,31);delete h.store[date].factMedicine;h.fill(date);
 assert.equal(h.value('avgCheck'),h.money(null));assert.equal(h.value('paidPatients'),h.money(10));
});
test('zero payers stay zero, averages remain unavailable',()=>{
 const h=harness(),date=h.add(1,31,{patients:0,dent:0,clinic:0});h.fill(date);
 assert.equal(h.value('paidPatients'),h.money(0));assert.equal(h.value('avgCheck'),h.money(null));assert.equal(h.value('dentAvg'),h.money(null));
});
test('null boolean and malformed counts cannot silently become zero',()=>{
 for(const bad of [null,'',true,-1,1.5,'garbage']){const h=harness(),date=h.add(1,31);h.store[date].paidPatients=bad;h.fill(date);assert.equal(h.value('paidPatients'),h.money(null));}
});
test('wrong period on a record cannot override exact monthly source',()=>{
 const h=harness(),date=h.add(1,31);h.store[date].paidPatientPeriodTo='2026-01-27';h.store[date].paidPatients=999;h.fill(date);assert.equal(h.value('paidPatients'),h.money(10));
});
test('changing date and re-rendering does not carry a future month result backwards',()=>{
 const h=harness(),date=h.add(1,31);h.fill(date);h.fill('2026-01-27');assert.equal(h.value('paidPatients'),h.money(null));h.fill(date);assert.equal(h.value('dentAvg'),h.money(225));
});
if(process.env.SKIP_COST!=='1'){
 const cost=fs.readFileSync(path.join(root,'reports/services-primary-patient-cost.js'),'utf8');
 const functions=slice(cost,'  function scopeMetrics(','  function getGroups(');
 function cHarness(){
  const data={year:2026,months:['2026-01','2026-02'],numerator:[1000,2400],availableThroughMonthIndex:1};
  const ctx={Date,Number,Object,Array,Math,JSON,MEDICAL_DIRECTIONS:['Стоматология','Клиника'],SALARY_KEY:'salary',EXTRA_KEY:'extra',
   PAID_PATIENTS_MONTHLY:{'2026-01':{asOf:'2026-01-31',medicine:100,dent:30,structure:80,dentists:{'Same A.':20}},
    '2026-02':{asOf:'2026-02-28',medicine:120,dent:40,structure:90,dentists:{'Same A.':25}}},
   normalizeName:s=>s.toLowerCase(),normalizeArray:(arr,n,fill)=>Array.from({length:n},(_,i)=>arr?.[i]??fill),getJson:()=>({})};
  vm.createContext(ctx);vm.runInContext(functions,ctx);
  const services={months:data.months,directions:{'Стоматология':{doctors:{'Same A.':{service:[[1,5000],[1,9000]]}}},'Клиника':{doctors:{'Clinic B.':{service:[[1,6000],[1,10000]]}}}}};
  const w={localStorage:{}};
  return {ctx,data,services,w,scope:(idx=[0])=>ctx.scopeMetrics(w,services,data,'Стоматология',['Same A.'],ctx.paidPatientSeries(data,'Стоматология'),idx)};
 }
 test('scope expense burden uses the common medical payer denominator',()=>{
  const h=cHarness(),r=h.scope();assert.equal(r.paidPatients,30);assert.equal(r.cost,10);assert.equal(r.load,300);assert.equal(r.final,4700);
  const all=h.ctx.wholeClinicMetrics(h.w,h.services,h.data,[0]);assert.equal(all.cost,10);assert.equal(all.load,1000);assert.equal(all.final,10000);
 });
 test('two-month cost remains weighted by medical patients; scope load sums monthly allocations',()=>{
  const h=cHarness(),r=h.scope([0,1]);assert.equal(r.cost,3400/220);assert.equal(r.load,1100);assert.equal(r.paidPatients,70);
 });
 test('zero scope patients does not receive the whole expense pool',()=>{
  const h=cHarness();h.ctx.PAID_PATIENTS_MONTHLY['2026-01'].dent=0;const r=h.scope();assert.equal(r.load,0);assert.equal(r.cost,10);
 });
 test('zero overall denominator cannot fabricate zero cost and inflated result',()=>{
  const h=cHarness();h.ctx.PAID_PATIENTS_MONTHLY['2026-01'].medicine=0;assert.equal(h.scope(),null);
  assert.equal(h.ctx.wholeClinicMetrics(h.w,h.services,h.data,[0]).final,null);
 });
 test('partial payer month cannot be divided into full-month expenses',()=>{
  const h=cHarness();h.ctx.PAID_PATIENTS_MONTHLY['2026-01'].asOf='2026-01-27';assert.equal(h.scope(),null);
 });
 test('missing and invalid expense amounts remain unknown',()=>{
  for(const v of [null,'',true,'unknown']){const h=cHarness();h.data.numerator[0]=v;assert.equal(h.scope(),null);}
 });
 test('missing patient counts remain unknown rather than zero cost',()=>{
  const h=cHarness();h.ctx.PAID_PATIENTS_MONTHLY['2026-01'].medicine=null;assert.equal(h.scope(),null);
 });
 test('ambiguous surnames never select an arbitrary first doctor',()=>{
  const h=cHarness();h.ctx.PAID_PATIENTS_MONTHLY['2026-01'].dentists={'Same A.':20,'Same B.':10};
  assert.equal(h.ctx.paidPatientSeries(h.data,'Стоматология','Same X.')[0],null);
  assert.equal(h.ctx.paidPatientSeries(h.data,'Стоматология','Same B.')[0],10);
 });
 test('ten full monthly cost denominators are selected independently, missing expenses stay missing',()=>{
  const h=cHarness(),ends=[31,28,31,30,31,30,31,31,30,31];
  h.data.months=ends.map((_,i)=>'2026-'+String(i+1).padStart(2,'0'));h.data.numerator=ends.map(()=>1000);h.data.availableThroughMonthIndex=9;
  h.data.months.forEach((m,i)=>h.ctx.PAID_PATIENTS_MONTHLY[m]={asOf:m+'-'+ends[i],medicine:100,dent:30,structure:80});
  assert.deepEqual(Array.from(h.ctx.paidPatientSeries(h.data,'medicine')),Array(10).fill(100));
  h.data.availableThroughMonthIndex=8;assert.equal(h.scope([9]),null);
 });
}
console.log(`TWO_VIEW_PAID_REGRESSION=PASS; SCENARIOS=${passed}; SYNTHETIC_DATA_ONLY=YES`);
