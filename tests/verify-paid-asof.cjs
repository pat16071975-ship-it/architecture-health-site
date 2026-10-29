'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const prior=fs.readFileSync(path.join(__dirname,'verify-two-view-paid.cjs'),'utf8');
vm.runInNewContext(prior+String.raw`
const countString=n=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(n);
function periodCell(h,label,column=0){
 const row=h.node('periodCompareBody').children.find(r=>r.innerHTML.startsWith('<td>'+label+'</td>'));assert(row,label);
 return [...row.innerHTML.matchAll(/<td[^>]*>(.*?)<\/td>/g)][column+1][1];
}
test('earlier exact endpoint survives a later upload and needs no raw row',()=>{
 const h=harness();h.add(1,31);
 h.ctx.PAID_PATIENTS_BY_DATE={'2026-01-20':{from:'2026-01-01',to:'2026-01-20',asOf:'2026-01-20',medicine:6,dent:2,structure:5}};
 h.ctx.DOCTOR_PAID_PERIODS.push({from:'2026-01-01',to:'2026-01-20',dentists:{D:40},clinicDocs:{C:50}});
 h.node('reportDate').value='2026-01-20';h.render();h.fill('2026-01-20');
 assert.equal(h.value('paidPatients'),h.money(6));assert.equal(h.value('dentAvg'),h.money(20));
 assert.equal(h.cell('Оплатившие пациенты',0),countString(6));
});
test('misdated exact endpoint is not trusted',()=>{
 const h=harness();h.add(1,31);
 h.ctx.PAID_PATIENTS_BY_DATE={'2026-01-20':{from:'2026-01-01',to:'2026-01-31',asOf:'2026-01-20',medicine:99,dent:99,structure:99}};
 assert.equal(h.run("exactPayersForDate('2026-01-20',null)"),null);
});
test('actual source date on a copied cash row cannot manufacture medicine revenue',()=>{
 const h=harness(),date=h.add(1,31);h.store[date]._reportSourceDate='2026-01-20';h.fill(date);
 assert.equal(h.value('avgCheck'),h.money(null));assert.equal(h.value('paidPatients'),h.money(10));
});
test('year with October cash28 and payer27 uses matched available monthly sources',()=>{
 const h=harness();[31,28,31,30,31,30,31,31,30,27].forEach((d,i)=>h.add(i+1,d));
 h.store['2026-10-28']={...h.store['2026-10-27'],date:'2026-10-28',factMedicine:99999,paidPatientEvidenceComplete:false};
 h.node('mode').value='year';h.node('year').value='2026';h.run('window.renderPeriod()');
 assert.equal(periodCell(h,'Оплатившие пациенты'),countString(100));
 assert.equal(periodCell(h,'Средний чек медицины на оплатившего пациента'),h.money(100));
 assert.equal(periodCell(h,'Выручка стоматологии'),h.money(1000));
 assert.equal(periodCell(h,'Средний чек стоматологии на оплатившего пациента'),h.money(25));
 assert.equal(periodCell(h,'Средний чек клиники на оплатившего пациента'),h.money(25));
});
test('quarter and half remain weighted, not averages of monthly averages',()=>{
 const h=harness();h.add(1,31,{patients:10,dent:2,clinic:5,dentMoney:100,clinicMoney:50});h.add(2,28,{patients:30,dent:8,clinic:15,dentMoney:900,clinicMoney:150});
 for(const mode of ['quarter','half']){
  h.node('mode').value=mode;h.node('year').value='2026';h.run('window.renderPeriod()');
  assert.equal(periodCell(h,'Оплатившие пациенты'),countString(40));
  assert.equal(periodCell(h,'Средний чек стоматологии на оплатившего пациента'),h.money(100));
  assert.equal(periodCell(h,'Средний чек клиники на оплатившего пациента'),h.money(10));
 }
});
test('unknown monthly patient evidence is not zero in a year total',()=>{
 const h=harness();h.add(1,31);h.add(2,28);delete h.ctx.PAID_PATIENTS_MONTHLY['2026-02'];h.store['2026-02-28'].paidPatientEvidenceComplete=false;
 h.node('mode').value='year';h.node('year').value='2026';h.run('window.renderPeriod()');
 assert.equal(periodCell(h,'Оплатившие пациенты'),empty);
 assert.equal(periodCell(h,'Средний чек стоматологии на оплатившего пациента'),empty);
});
test('source-only month is counted but no missing medical numerator is invented',()=>{
 const h=harness();h.add(1,31,{raw:false});h.add(2,28);
 h.node('mode').value='year';h.node('year').value='2026';h.run('window.renderPeriod()');
 assert.equal(periodCell(h,'Оплатившие пациенты'),countString(20));
 assert.equal(periodCell(h,'Средний чек медицины на оплатившего пациента'),empty);
 assert.equal(periodCell(h,'Средний чек стоматологии на оплатившего пациента'),h.money(25));
});
test('legacy fallback money from another period cannot create a year average',()=>{
 const h=harness();h.add(1,31);h.ctx.DOCTOR_PAID_PERIODS.length=0;
 h.node('mode').value='year';h.node('year').value='2026';h.run('window.renderPeriod()');
 assert.equal(periodCell(h,'Средний чек стоматологии на оплатившего пациента'),empty);
});

test('arbitrary exact days 9 15 and 17 render in both approved views',()=>{
 const h=harness();h.add(1,31);
 const cases=[
  ['2026-01-09',3,1,2,30,40],
  ['2026-01-15',5,2,3,80,90],
  ['2026-01-17',6,3,4,150,160],
 ];
 h.ctx.PAID_PATIENTS_BY_DATE={};
 for(const [date,medicine,dent,structure,dentMoney,clinicMoney] of cases){
   h.ctx.PAID_PATIENTS_BY_DATE[date]={from:'2026-01-01',to:date,asOf:date,medicine,dent,structure};
   h.ctx.DOCTOR_PAID_PERIODS.push({from:'2026-01-01',to:date,dentists:{D:dentMoney},clinicDocs:{C:clinicMoney}});
   h.node('reportDate').value=date;
   h.node('dateViewMode').value='single';h.fill(date);
   assert.equal(h.value('paidPatients'),h.money(medicine),date+' single patients');
   assert.equal(h.value('dentAvg'),h.money(dentMoney/dent),date+' single dent avg');
   h.node('dateViewMode').value='compare';h.render();
   assert.equal(h.cell('Оплатившие пациенты',0),countString(medicine),date+' compare patients');
 }
});
test('later month-end must never backfill an earlier arbitrary day',()=>{
 const h=harness();h.add(1,31);
 h.ctx.PAID_PATIENTS_BY_DATE={'2026-01-31':{from:'2026-01-01',to:'2026-01-31',asOf:'2026-01-31',medicine:20,dent:10,structure:7}};
 for(const date of ['2026-01-09','2026-01-15','2026-01-17'])
   assert.equal(h.run(`exactPayersForDate('${date}',null)`),null,date);
});

console.log('PAID_ASOF_AND_PERIOD=PASS; ADDITIONAL_SCENARIOS=10');
`,{require,__dirname,__filename,console,process});
