'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const path=require('node:path'),root=path.resolve(__dirname,'..');
const period=fs.readFileSync(path.join(root,'reports/period-view.js'),'utf8');
const index=fs.readFileSync(path.join(root,'reports/index.html'),'utf8');
const slice=(text,start,end)=>{const a=text.indexOf(start),b=text.indexOf(end,a);assert(a>=0&&b>a,start);return text.slice(a,b)};
const moneyFunctions=slice(period,'  function paidDate(','  const escapeHtml=');
const monthFunctions=slice(period,'  function monthlyReportColumn(','  let dateStickyRaf=');
const derive=index.match(/^function derive\(r\).*$/m)[0];
const days=index.match(/^function daysInMonth\(d\).*$/m)[0];
const dates=slice(period,'  const monthNames=','  function addDateMetric(');
let passed=0;
function test(name,fn){fn();console.log('PASS '+name);passed++}
function harness(){
 const nodes={reportDate:{value:'2026-10-27'},compareRange:{value:'ytd'},dateViewMode:{value:'monthly'},
  dateCompareHead:{},dateCompareBody:{},dateSummaryNote:{},dateCompareTitle:{},dateCompareHint:{}};
 const store={},rows=[];
 const ctx={Date,Map,Set,Number,String,Array,JSON,Math,console,window:{},
  dentists:['Dent Doctor'],clinicDocs:['Clinic Doctor'],
  DOCTOR_PAID_SNAPSHOTS:{},DOCTOR_PAID_PERIODS:[],PAID_PATIENTS_MONTHLY:{},
  document:{getElementById:id=>nodes[id]},loadStore:()=>store,
  num:v=>v===''||v==null||Number.isNaN(+v)?0:+v,
  isBlankValue:v=>v===''||v==null,
  fmtMoney:v=>v,fmtPct:v=>v,fmtCount:v=>v,queueDateStickyClone(){},addGroup(){},addSub(){},
  addDateMetric(_tb,label,_cls,months,getter,_formatter,key){rows.push({label,key,values:months.map(m=>m.r?getter(m.d,m.r):null)})},
  addDateDoctorMetric(_tb,label,_cls,months,getter,_formatter,key){rows.push({label,key,values:months.map(getter)})},
 };
 vm.createContext(ctx);vm.runInContext(days+'\n'+derive+'\n'+dates+'\n'+moneyFunctions+'\n'+monthFunctions,ctx);
 const values=key=>rows.find(r=>r.key===key)?.values;
 const add=(month,end,raw=true)=>{
  const key=`2026-${String(month).padStart(2,'0')}`,day=`${key}-${String(end).padStart(2,'0')}`;
  ctx.PAID_PATIENTS_MONTHLY[key]={asOf:day,medicine:2,dent:1,structure:2};
  ctx.DOCTOR_PAID_PERIODS.push({from:key+'-01',to:day,dentists:{'Dent Doctor':100},clinicDocs:{'Clinic Doctor':25}});
  if(raw)store[day]={date:day,factMedicine:205,factLab:95,cashTotal:300,cashOOO:200,cashIP:100,
   primary:7,repeat:11,dentists:{'Dent Doctor':150},clinicDocs:{'Clinic Doctor':55},labOrders:2,labRevenue:95,
   paidPatientEvidenceComplete:true,paidPatients:2,paidDentPatients:1,paidClinicPatients:2};
  return day;
 };
 return {ctx,nodes,store,rows,values,add,render:()=>{rows.length=0;ctx.renderDateComparison()}};
}
test('ten monthly columns use real month ends and October 27',()=>{
 const h=harness();[31,28,31,30,31,30,31,31,30,27].forEach((day,i)=>h.add(i+1,day));
 h.render();assert.deepEqual(Array.from(h.values('paidPatients')),Array(10).fill(2));
 assert.deepEqual(Array.from(h.values('avgCheck')),Array(10).fill(102.5));
 assert.equal(h.nodes.dateCompareTitle.textContent,'Итоги месяцев 2026');
 assert(h.nodes.dateCompareHead.innerHTML.includes('31.01.2026'));
 assert(h.nodes.dateCompareHead.innerHTML.includes('27.10.2026'));
});
test('date 27 view never borrows full-month patients',()=>{
 const h=harness();[31,28,31,30,31,30,31,31,30,27].forEach((day,i)=>h.add(i+1,day));
 h.nodes.dateViewMode.value='compare';h.render();
 assert.deepEqual(Array.from(h.values('paidPatients')),[null,null,null,null,null,null,null,null,null,2]);
 assert.deepEqual(Array.from(h.values('avgCheck')),[null,null,null,null,null,null,null,null,null,102.5]);
 assert.equal(h.nodes.dateCompareTitle.textContent,'На 27 число по месяцам 2026');
});
test('full-month counts cannot be divided into a day27 revenue',()=>{
 const h=harness();h.add(1,31);h.store['2026-01-27']={date:'2026-01-27',factMedicine:999};
 h.nodes.dateViewMode.value='compare';h.render();assert.equal(h.values('avgCheck')[0],null);
});
test('monthly money and payer counts use one source period',()=>{
 const h=harness();h.add(1,31);h.render();
 assert.equal(h.values('dentAvg')[0],100);assert.equal(h.values('clinicAvg')[0],12.5);
 assert.equal(h.values('dentRev')[0],100);assert.equal(h.values('clinicRev')[0],25);
 assert.equal(h.values('dentist')[0],100);assert.equal(h.values('clinicDoctor')[0],25);
});
test('monthly selection does not mutate raw store or provider sums',()=>{
 const h=harness();h.add(1,31);const before=JSON.stringify(h.store);h.render();
 assert.equal(JSON.stringify(h.store),before);assert.equal(h.store['2026-01-31'].dentists['Dent Doctor'],150);
});
test('cash legal split visits and laboratory calculation preserved',()=>{
 const h=harness();h.add(1,31);h.render();
 for(const [key,value] of Object.entries({factTotal:300,cashOOO:200,cashIP:100,primary:7,repeat:11,labOrders:2,labRevenue:95,labAvg:47.5}))assert.equal(h.values(key)[0],value,key);
});
test('missing month evidence does not fall back to primary visits',()=>{
 const h=harness();const day=h.add(1,31);delete h.ctx.PAID_PATIENTS_MONTHLY['2026-01'];h.render();
 assert.equal(h.values('paidPatients')[0],null);assert.equal(h.values('avgCheck')[0],null);
 assert.equal(h.values('primary')[0],7);assert.equal(h.store[day].paidPatients,2);
});
test('paid-only month counts display without invented clinic cash',()=>{
 const h=harness();h.add(1,31,false);h.render();
 assert.equal(h.values('paidPatients')[0],2);assert.equal(h.values('factTotal')[0],null);
 assert.equal(h.values('avgCheck')[0],null);assert.equal(h.values('dentAvg')[0],100);
});
test('no old money fallback can supply a newer monthly average',()=>{
 const h=harness();h.add(1,31,false);h.ctx.DOCTOR_PAID_PERIODS=[];
 h.ctx.DOCTOR_PAID_SNAPSHOTS={'2026-01':[{asOf:'2026-01-20',dentists:{'Dent Doctor':999},clinicDocs:{'Clinic Doctor':999}}]};
 h.render();assert.equal(h.values('paidPatients')[0],2);assert.equal(h.values('dentAvg')[0],null);
 assert.equal(h.values('dentRev')[0],null);assert.equal(h.values('dentist')[0],null);
});
test('overlapping source period not double-counted',()=>{
 const h=harness();h.add(10,27);h.ctx.DOCTOR_PAID_PERIODS.push({from:'2026-10-12',to:'2026-10-13',dentists:{'Dent Doctor':500},clinicDocs:{}});
 h.render();assert.equal(h.values('dentRev')[9],100);assert.equal(h.values('dentAvg')[9],100);
});
test('zero paid patients keeps the count zero and average empty',()=>{
 const h=harness();h.add(1,31);h.ctx.PAID_PATIENTS_MONTHLY['2026-01']={asOf:'2026-01-31',medicine:0,dent:0,structure:0};
 h.render();assert.equal(h.values('paidPatients')[0],0);assert.equal(h.values('avgCheck')[0],null);assert.equal(h.values('dentAvg')[0],null);
});
test('month filtering and month boundary do not leak another month',()=>{
 const h=harness();h.add(1,31);h.add(10,27);h.nodes.compareRange.value='q1';h.render();
 assert.deepEqual(Array.from(h.values('paidPatients')),[2,null,null]);
});
test('monthly source with wrong asOf month is rejected',()=>{
 const h=harness();h.add(1,31);h.ctx.PAID_PATIENTS_MONTHLY['2026-01'].asOf='2026-02-28';h.render();
 assert.equal(h.values('paidPatients')[0],null);
});
test('switching monthly then compare then monthly has no stale values',()=>{
 const h=harness();h.add(1,31);h.render();assert.equal(h.values('paidPatients')[0],2);
 h.nodes.dateViewMode.value='compare';h.render();assert.equal(h.values('paidPatients')[0],null);
 h.nodes.dateViewMode.value='monthly';h.render();assert.equal(h.values('paidPatients')[0],2);
});
test('existing default date comparison and hidden entry button are retained',()=>{
 assert(period.includes('<option value="compare" selected>На эту дату по месяцам</option>'));
 assert(period.includes('<option value="monthly">Итоги месяцев</option>'));
 assert(!period.includes("document.getElementById('dateViewMode').value==='compare'"));
 const css=fs.readFileSync(path.join(root,'reports/mobile-date-fix.css'),'utf8');
 assert(css.includes('#entryBtn{display:none!important}'));
});
test('absent medicine revenue is unknown rather than a zero average',()=>{
 const h=harness();const day=h.add(1,31);delete h.store[day].factMedicine;h.render();
 assert.equal(h.values('paidPatients')[0],2);assert.equal(h.values('avgCheck')[0],null);
});
console.log(`PAID_HISTORY_JS=PASS; SCENARIOS=${passed}`);
