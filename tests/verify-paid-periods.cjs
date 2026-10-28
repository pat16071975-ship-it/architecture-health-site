const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'reports/index.html'), 'utf8');
const view = fs.readFileSync(path.join(root, 'reports/period-view.js'), 'utf8');
const part = (source, start, end) => {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert(a >= 0 && b > a, `Missing source boundary: ${start}`);
  return source.slice(a, b);
};
const inline = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1].replace(/\ninit\(\);\s*$/, '');
new vm.Script(inline);
new vm.Script(view);
assert(html.includes('avgCheck:paidPatients?fm/paidPatients:null'), 'Medicine average check must use paid patients.');
assert(html.includes('dentAvg:paidDentPatients?dentRev/paidDentPatients:null'), 'Dentistry average check must use paid patients.');
assert(html.includes('clinicAvg:paidClinicPatients?clinicRev/paidClinicPatients:null'), 'Clinic average check must use paid patients.');
assert(html.includes("'paidPatients','paidDentPatients','paidClinicPatients'"), 'Period aggregation must carry paid-patient counts.');
assert(html.includes('out.paidPatientEvidenceComplete=rows.every'), 'Incomplete payer evidence must stay unavailable.');
assert.equal(
  createHash('sha256').update(part(html, 'function collectRecord(){', 'function refreshAutos(')).digest('hex'),
  'f512bdf6430e1be0f520d3bbc9178d557613271e1c197464a9c7466dea5d6396',
  'Manual input collection must remain unchanged'
);
for (const [start, end, expected] of [
  ['  function periodData(def,year){', '  function doctorPeriodData(', '55d442c0ae415edfd250e9ec3587b4390274aee149f53337434e9e204fea6305'],
  ['  function addDateMetric(', '  function addDateDoctorMetric(', '1963e92a0001c721a7319a5eaf3c201bb65dba1fd0cd6457bd496a6e5e68f34d'],
]) assert.equal(createHash('sha256').update(part(view, start, end)).digest('hex'), expected, `${start} must retain its original contract`);
assert(html.includes('./period-view.js?v=20261028-paid-patients-1'), 'Paid-patient script cache key');

const freeze = value => {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
};
const record = date => ({date, plan:2000, factMedicine:400, factLab:50, cashTotal:1000, billedTotal:1200,
  cashOOO:600, cashIP:400, cashUnallocated:50, primary:8, repeat:9, dentPrimary:4, dentRepeat:5,
  clinicPrimary:4, clinicRepeat:4, paidPatientEvidenceComplete:true, paidPatients:8, paidDentPatients:4,
  paidClinicPatients:4, labOrders:2, labRevenue:50,
  dentists:{D1:100,D2:200}, clinicDocs:{C1:40,C2:60}, leadsDent:10, leadsClinic:8, leadsReserve:2});
const period = (from, to, dent, clinic=0) => ({from,to,dentists:{D1:dent,D2:0},clinicDocs:{C1:clinic,C2:0}});
const snapshots = freeze({
  '2026-07':[{asOf:'2026-07-31',dentists:{D1:11,D2:13},clinicDocs:{C1:17,C2:19}}],
  '2026-08':[{asOf:'2026-08-15',dentists:{D1:2,D2:3},clinicDocs:{C1:4,C2:5}},
    {asOf:'2026-08-31',dentists:{D1:23,D2:29},clinicDocs:{C1:31,C2:37}}],
  '2026-09':[{asOf:'2026-09-30',dentists:{D1:41,D2:43},clinicDocs:{C1:47,C2:53}}],
});
const h1 = freeze({from:'2026-01-01',to:'2026-06-30',dentists:{D1:500,D2:100},clinicDocs:{C1:40,C2:20}});
const bounds = (from, to) => ({from,to});
const yearBounds = bounds('2026-01-01','2026-12-31');
const h1Bounds = bounds('2026-01-01','2026-06-30');
class Element {
  constructor(){this.children=[];this.value='';this.dataset={};this.readOnly=true;}
  set innerHTML(value){this._html=value;this.children=[];}
  get innerHTML(){return this._html||'';}
  appendChild(value){this.children.push(value);}
}
function setup({data={}, periods=[], paid={}, install=true}={}) {
  let store = freeze(data);
  const nodes = {}, node = id => nodes[id] || (nodes[id]=new Element());
  const metrics = ['dentRev','clinicRev','dentAvg','clinicAvg','cashTotal','labRevenue','labAvg','billedTotal',
    'due','execution','primary','repeat','dentPrimary','clinicPrimary','paidPatients','paidDentPatients','paidClinicPatients','plan','cashOOO','cashIP']
    .map(key=>Object.assign(new Element(),{dataset:{key,type:'money'}}));
  const doctors = ['dentists','clinicDocs'].flatMap(type=>(type==='dentists'?['D1','D2']:['C1','C2'])
    .map(name=>Object.assign(new Element(),{dataset:{doctorType:type,doctor:name}})));
  const context = {console,DOCTOR_PAID_SNAPSHOTS:freeze(paid),DOCTOR_PAID_PERIODS:freeze(periods),
    localStorage:{getItem:()=>JSON.stringify(store)},queueDateStickyClone:()=>{},
    document:{querySelector:selector=>node(selector.slice(1)),getElementById:node,
      querySelectorAll:selector=>selector==='[data-key]'?metrics:selector==='[data-doctor]'?doctors:[],
      createElement:()=>new Element()}};
  context.window=context;
  vm.createContext(context);
  vm.runInContext(inline,context);
  vm.runInContext("dentists.splice(0,dentists.length,'D1','D2');clinicDocs.splice(0,clinicDocs.length,'C1','C2');",context);
  context.legacyRender=vm.runInContext('renderPeriod',context);
  function helpers(){
    vm.runInContext(part(view,'  const fmtMoney=','  const toolbar='),context);
    vm.runInContext(part(view,'  const monthNames=','  function cashSplit(')+part(view,'  function renderDateComparison(){','  let dateStickyRaf='),context);
  }
  function startup(){
    context.currentRecord=()=>store[node('reportDate').value]||null;
    context.makeViewOnly=()=>{};context.renderMismatchNote=()=>{};context.syncDateUx=()=>{};
    context.setTimeout=callback=>callback();
    vm.runInContext(view.split('\n').find(line=>line.includes('setTimeout(()=>{makeViewOnly();')),context);
  }
  if(install)helpers();
  node('year').value='2026';node('quarter').value='3';node('half').value='1';
  node('reportDate').value='2026-09-30';node('compareRange').value='q3';
  return {context,node,metrics,doctors,helpers,startup,run:code=>vm.runInContext(code,context),
    setData:data=>store=freeze(data),money:vm.runInContext('money',context),
    fill:row=>context.fillRecord(row),sum:(rows,type,window)=>context.azDoctorDirectionPaid(rows,type,window),
    doctor:(rows,type,name,window)=>context.azDoctorPeriodPaid(rows,type,name,window),
    dateDoctor:(date,type,name,original)=>context.azDoctorPaidValue(date,type,name,original)};
}
function cellValues(table,label){
  const row=table.children.find(row=>row.innerHTML.startsWith('<td>'+label+'</td>')||row.innerHTML.startsWith('<th>'+label+'</th>'));
  assert(row,`Missing rendered row: ${label}`);
  return [...row.innerHTML.matchAll(/<td[^>]*>(.*?)<\/td>/g)].map(match=>match[1]).slice(row.innerHTML.startsWith('<td>')?1:0);
}
const empty='<span class="empty">—</span>';
const rendered=(h,values)=>values.map(value=>value===null?empty:h.money(value));
function assertDirections(h,table,dent,clinic){
  assert.deepEqual(cellValues(table,'Выручка стоматологии'),rendered(h,dent));
  assert.deepEqual(cellValues(table,'Выручка клиники'),rendered(h,clinic));
  for(const [total,names] of [[dent,['D1','D2']],[clinic,['C1','C2']]]){
    const doctors=names.map(name=>cellValues(table,name));
    for(let index=0;index<total.length;index++){
      const sum=doctors.reduce((sum,cells)=>sum+Number(cells[index].replace(/<[^>]*>|[^\d,.-]/g,'').replace(',','.')),0);
      if(total[index]!==null)assert.equal(sum,total[index],`Visible doctor sum for column ${index}`);
    }
  }
}
let checks=0;
function check(name,callback){callback();checks++;console.log('PASS '+name);}

check('existing July–September, dates, averages and raw values remain unchanged',()=>{
  const dates=['2026-06-30','2026-07-30','2026-07-31','2026-08-31','2026-09-30'];
  const data=Object.fromEntries(dates.map(date=>[date,record(date)])),before=JSON.stringify(data),h=setup({data,paid:snapshots});
  for(const [date,dent,clinic] of [['2026-06-30',300,100],['2026-07-30',300,100],['2026-07-31',24,36],['2026-08-31',52,68],['2026-09-30',84,100]]){
    h.fill(data[date]);const values=Object.fromEntries(h.metrics.map(input=>[input.dataset.key,input.value]));
    assert.equal(values.dentRev,h.money(dent));assert.equal(values.clinicRev,h.money(clinic));
    assert.equal(values.dentAvg,h.money(75));assert.equal(values.clinicAvg,h.money(25));
    assert.equal(values.cashTotal,h.money(1000));assert.equal(values.cashOOO,h.money(600));assert.equal(values.cashIP,h.money(400));
  }
  assert.equal(h.dateDoctor('2026-08-20','dentists','D1',999),2);
  assert.equal(h.dateDoctor('2026-07-30','dentists','D1',999),999);
  for(const [mode,dent,clinic] of [['quarter',[null,300,160,null],[null,100,204,null]],['half',[300,160],[100,204]],['year',[460],[304]]]){
    h.node('mode').value=mode;h.run('renderPeriod()');assertDirections(h,h.node('periodCompareBody'),dent,clinic);
  }
  h.node('reportDate').value='2026-08-31';h.run('renderDateComparison()');assertDirections(h,h.node('dateCompareBody'),[24,52,84],[36,68,100]);
  assert.equal(JSON.stringify(data),before);
});

check('one half-year file overrides its six months without making monthly paid figures',()=>{
  const data={};for(let month=1;month<=6;month++){const date=`2026-${String(month).padStart(2,'0')}-${new Date(2026,month,0).getDate()}`;data[date]=record(date);}
  const h=setup({data,periods:[h1]});
  assert.equal(h.sum(Object.values(data),'dentists',h1Bounds),600);
  assert.equal(h.doctor(Object.values(data),'dentists','D1',h1Bounds),500);
  assert.equal(h.dateDoctor('2026-06-30','dentists','D1',100),100);
  assert.equal(h.doctor([], 'dentists','D1',bounds('2026-01-01','2026-01-31')),null);
  h.node('mode').value='half';h.run('renderPeriod()');assertDirections(h,h.node('periodCompareBody'),[600,null],[60,null]);
  assert.deepEqual(cellValues(h.node('periodCompareBody'),'Средний чек стоматологии на оплатившего пациента'),[h.money(25),empty]);
  assert.deepEqual(cellValues(h.node('periodCompareBody'),'Факт'),[h.money(6000),empty]);
});

check('legacy monthly snapshots cannot pass the raw month as-of date',()=>{
  const row=record('2026-07-15'),data={[row.date]:row};
  const legacy=setup({data,paid:snapshots});
  assert.equal(legacy.sum([row],'dentists',bounds('2026-07-01','2026-09-30')),436);
  assert.equal(legacy.doctor([row],'dentists','D1',bounds('2026-07-01','2026-07-31')),100);
  const onlyJuly=setup({data,paid:{'2026-07':snapshots['2026-07']}});
  assert.equal(onlyJuly.sum([row],'dentists',yearBounds),300);
  onlyJuly.node('mode').value='quarter';onlyJuly.run('renderPeriod()');
  assertDirections(onlyJuly,onlyJuly.node('periodCompareBody'),[null,null,300,null],[null,null,100,null]);
  const mixed=setup({data,periods:[h1],paid:{'2026-07':snapshots['2026-07']}});
  assert.equal(mixed.sum([row],'dentists',yearBounds),900);
  assert.equal(mixed.sum([row],'clinicDocs',yearBounds),160);
  const exactNew=setup({data,periods:[period('2026-07-01','2026-07-31',24)],paid:{'2026-07':snapshots['2026-07']}});
  assert.equal(exactNew.sum([row],'dentists',yearBounds),24);
});

check('half-year plus monthly snapshots and uncovered legacy month compose once',()=>{
  const data={};for(const date of ['2026-01-31','2026-06-30','2026-07-31','2026-08-31','2026-09-30','2026-10-31'])data[date]=record(date);
  const h=setup({data,periods:[h1],paid:snapshots});
  h.node('mode').value='year';h.run('renderPeriod()');assertDirections(h,h.node('periodCompareBody'),[1060],[364]);
  const sourceOnly=setup({periods:[h1],paid:snapshots});sourceOnly.node('mode').value='year';sourceOnly.run('renderPeriod()');
  assertDirections(sourceOnly,sourceOnly.node('periodCompareBody'),[760],[264]);
  assert.deepEqual(cellValues(sourceOnly.node('periodCompareBody'),'Факт'),[empty]);
  assert.deepEqual(cellValues(sourceOnly.node('periodCompareBody'),'Средний чек стоматологии на оплатившего пациента'),[empty]);
});

check('exact interval wins over smaller overlapping alternatives',()=>{
  const h=setup({periods:[period('2026-01-01','2026-03-31',111),period('2026-04-01','2026-06-30',222),h1]});
  assert.equal(h.sum([],'dentists',h1Bounds),600);
  const duplicate=setup({periods:[period('2026-07-01','2026-07-31',5)],paid:snapshots});
  assert.equal(duplicate.sum([],'dentists',bounds('2026-07-01','2026-07-31')),5);
});

check('overlaps are never summed or followed by fallback inside their months',()=>{
  const data=Object.fromEntries(['2026-01-31','2026-02-28','2026-03-31','2026-04-30','2026-05-31','2026-06-30'].map(date=>[date,record(date)]));
  const h=setup({data,periods:[period('2026-01-01','2026-02-28',10),period('2026-02-01','2026-03-31',50)]});
  assert.equal(h.sum(Object.values(data),'dentists',h1Bounds),1210);
  const maxCoverage=setup({data,periods:[period('2026-01-01','2026-04-30',400),period('2026-03-01','2026-08-31',600),period('2026-09-01','2026-12-31',400)]});
  assert.equal(maxCoverage.sum(Object.values(data),'dentists',yearBounds),1600);
});

check('complete covering can backtrack and crosses calendar years',()=>{
  const h=setup({periods:[period('2026-01-01','2026-01-20',999),period('2026-01-01','2026-01-10',10),
    period('2026-01-11','2026-01-31',20),period('2026-02-01','2026-02-28',30)]});
  assert.equal(h.sum([],'dentists',bounds('2026-01-01','2026-02-28')),60);
  const crossYear=setup({periods:[period('2025-11-01','2025-12-31',100),period('2026-01-01','2026-02-28',200)]});
  assert.equal(crossYear.sum([],'dentists',bounds('2025-11-01','2026-02-28')),300);
  assert.equal(crossYear.sum([],'dentists',bounds('2026-01-01','2026-01-31')),null);
});

check('midmonth sources require a complete window and preserve earlier as-of fallback',()=>{
  const paid={'2026-07':[{asOf:'2026-07-10',dentists:{D1:7},clinicDocs:{C1:2}}]};
  const h=setup({paid,periods:[period('2026-07-11','2026-07-20',8,3),period('2026-07-21','2026-07-31',9,4)]});
  assert.equal(h.dateDoctor('2026-07-20','dentists','D1',100),15);
  assert.equal(h.dateDoctor('2026-07-19','dentists','D1',100),7);
  assert.equal(h.dateDoctor('2026-07-31','dentists','D1',100),24);
  assert.equal(h.sum([],'dentists',bounds('2026-07-11','2026-07-31')),17);
  assert.equal(h.sum([],'dentists',bounds('2026-07-12','2026-07-31')),null);
  const gap=setup({periods:[period('2026-01-15','2026-02-15',900)]});
  assert.equal(gap.sum([],'dentists',h1Bounds),null);
  assert.equal(gap.dateDoctor('2026-02-15','dentists','D1',''),'');
});

check('October 1-11 snapshot plus 12-13 slice sums to MIS paid total',()=>{
  const paid={'2026-10':[{
    asOf:'2026-10-11',
    dentists:{D1:1055490,D2:0},
    clinicDocs:{C1:0,C2:0}
  }]};
  const h=setup({
    paid,
    periods:[period('2026-10-12','2026-10-13',92610)]
  });
  assert.equal(h.dateDoctor('2026-10-13','dentists','D1',0),1148100);
  assert.equal(
    h.doctor([], 'dentists','D1',bounds('2026-10-01','2026-10-13')),
    1148100
  );
});

check('zero paid is retained and absent sources remain empty',()=>{
  const h=setup({periods:[period('2026-01-01','2026-06-30',0,0)]});
  assert.equal(h.sum([],'dentists',h1Bounds),0);assert.equal(h.doctor([],'dentists','D2',h1Bounds),0);
  h.node('mode').value='half';h.run('renderPeriod()');assertDirections(h,h.node('periodCompareBody'),[0,null],[0,null]);
  const missing=setup();missing.node('mode').value='year';missing.run('renderPeriod()');
  assertDirections(missing,missing.node('periodCompareBody'),[null],[null]);
  assert.equal(missing.sum([],'dentists',yearBounds),null);
});

check('doctor-only date comparison and single date do not fabricate other metrics',()=>{
  const h=setup({periods:[period('2026-07-01','2026-07-31',21,4)]});
  h.node('reportDate').value='2026-08-31';h.run('renderDateComparison()');assertDirections(h,h.node('dateCompareBody'),[21,null,null],[4,null,null]);
  for(const label of ['Факт','ДС ООО','ДС ИП','Средний чек стоматологии'])assert.deepEqual(cellValues(h.node('dateCompareBody'),label),[empty,empty,empty]);
  h.node('reportDate').value='2026-07-31';h.run('loadDate()');
  const values=Object.fromEntries(h.metrics.map(input=>[input.dataset.key,input.value]));
  assert.equal(values.dentRev,h.money(21));assert.equal(values.clinicRev,h.money(4));
  for(const [key,value] of Object.entries(values))if(!['dentRev','clinicRev'].includes(key))assert.equal(value,'',key);
  const noMonth=setup({periods:[h1]});noMonth.node('reportDate').value='2026-06-30';noMonth.node('compareRange').value='h1';noMonth.run('renderDateComparison()');
  assertDirections(noMonth,noMonth.node('dateCompareBody'),Array(6).fill(null),Array(6).fill(null));
});

check('missing legacy doctor value stays blank in date comparison',()=>{
  const row=record('2026-07-31');row.dentists={D1:9};row.clinicDocs={C1:2};const h=setup({data:{[row.date]:row}});
  h.node('reportDate').value='2026-08-31';h.run('renderDateComparison()');
  assert.deepEqual(cellValues(h.node('dateCompareBody'),'D2'),[empty,empty,empty]);
  assert.deepEqual(cellValues(h.node('dateCompareBody'),'Выручка стоматологии'),[h.money(9),empty,empty]);
});

check('legacy period renderer receives whole bounds and can show source-only totals',()=>{
  const h=setup({periods:[h1]});h.node('mode').value='half';h.run('legacyRender()');
  assert.deepEqual(cellValues(h.node('periodTable'),'Выручка стоматологии'),[h.money(600)]);
  assert.deepEqual(cellValues(h.node('periodTable'),'Выручка клиники'),[h.money(60)]);
  assert.deepEqual(cellValues(h.node('periodTable'),'Факт'),['—']);
});

check('both API and helper load orders handle source-only dates',()=>{
  const source=period('2026-07-01','2026-07-31',21,4);
  const apiFirst=setup({periods:[source],install:false});apiFirst.node('reportDate').value='2026-07-31';apiFirst.run('loadDate()');apiFirst.helpers();apiFirst.startup();
  assert.equal(apiFirst.metrics[0].value,apiFirst.money(21));assert.equal(apiFirst.metrics.find(input=>input.dataset.key==='cashTotal').value,'');
  const jsFirst=setup();jsFirst.node('reportDate').value='2026-07-31';jsFirst.startup();jsFirst.context.DOCTOR_PAID_PERIODS=freeze([source]);jsFirst.run('loadDate()');
  assert.equal(jsFirst.metrics[0].value,jsFirst.money(21));
});

console.log(JSON.stringify({status:'PASS',checks,source_data:'synthetic',paid_patient_methodology:'verified'}));
