const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../reports/doctor-balance.js'),'utf8');
const ctx={window:{},document:{createElement:()=>({}),head:{appendChild(){}}}};
vm.createContext(ctx);vm.runInContext(source,ctx);
const calc=ctx.window.azDoctorBalance.calculator;
const part=(from,to,value)=>({from,to,dentists:{D:value},clinicDocs:{C:0}});
const daily=(date,value=1)=>({date,complete:true,dentists:{D:value},clinicDocs:{C:0}});
const days=[];
for(let d=new Date('2026-01-01T12:00:00Z');d<=new Date('2026-11-17T12:00:00Z');d.setUTCDate(d.getUTCDate()+1))days.push(daily(d.toISOString().slice(0,10)));
const periods=[part('2026-01-01','2026-06-30',1000),part('2026-07-01','2026-10-31',500),part('2026-11-01','2026-11-17',100)];
const query=(c,from='2026-01-01',to='2026-11-17',name='D')=>JSON.parse(JSON.stringify(c(from,to,'dentists',name)));
let c=calc({},periods,days);
assert.deepEqual(query(c),{paid:1600,services:321,balance:1279});
assert.deepEqual(query(c,'2026-11-01'),{paid:100,services:17,balance:83});
// Quarter/month filters cannot replace the YTD source period with displayed slices.
assert.equal(query(c,'2026-10-01','2026-10-17').paid,null);
assert.equal(query(c).balance,1279);
assert.equal(query(c,'2026-01-01','2026-11-18').balance,null);
assert.equal(query(c,'2026-01-01','2026-11-16').paid,null);
assert.equal(query(c,'2025-01-01','2026-11-17').balance,null);
// Exact source wins over overlapping month/half-year sources, never double-add.
c=calc({'2026-11':[{asOf:'2026-11-17',dentists:{D:999},clinicDocs:{}}]},periods,days);
assert.equal(query(c).paid,1600);
c=calc({},[...periods,part('2026-01-01','2026-11-17',1700)],days);
assert.equal(query(c).paid,1700);
assert.equal(query(calc({},periods,days.filter(d=>d.date!=='2026-02-01'))).services,320);
assert.equal(query(calc({},periods,days.map(d=>d.date==='2026-02-01'?{...d,complete:false}:d))).balance,null);
assert.deepEqual(query(calc({},[part('2026-01-01','2026-01-01',0)],[daily('2026-01-01',0)]),'2026-01-01','2026-01-01'),{paid:0,services:0,balance:0});
assert.equal(query(calc({},[part('2026-01-01','2026-01-01',10)],[daily('2026-01-01',100)]),'2026-01-01','2026-01-01').balance,-90);
assert.equal(query(calc({},[part('2026-01-01','2026-01-01',0.3)],[daily('2026-01-01',0.1)]),'2026-01-01','2026-01-01').balance,0.2);
assert.equal(query(calc({},[part('2026-01-01','2026-01-01',null)],[daily('2026-01-01')]),'2026-01-01','2026-01-01').balance,null);
assert.equal(query(c,'2026-01-01','2026-11-17','Absent').balance,0);
assert.equal(query(c,'2026-02-30','2026-11-17').balance,null);
assert.equal(query(c,'2026-11-17','2026-01-01').balance,null);
assert.equal(query(calc({},[part('2024-02-28','2024-03-01',10)],[daily('2024-02-28'),daily('2024-02-29'),daily('2024-03-01')]),'2024-02-28','2024-03-01').balance,7);
console.log('DOCTOR_BALANCE=PASS; exact coverage, YTD, overlaps, gaps, zero, negative, decimals, leap year');


// Sparse imported dates are transactions, not a required daily calendar.
const sparse=days.filter(d=>!['2026-01-01','2026-01-02','2026-01-03','2026-01-04','2026-02-01','2026-05-31'].includes(d.date));
assert.deepEqual(query(calc({},periods,sparse)),{paid:1600,services:315,balance:1285});
assert.equal(query(calc({},periods,sparse),'2026-01-01','2026-01-17').services,13);
assert.equal(query(calc({},periods,sparse),'2026-01-01','2026-01-04').services,null);
assert.equal(query(calc({},periods,days.filter(d=>!d.date.startsWith('2026-03')))).balance,null);
assert.equal(query(calc({},periods,days.filter(d=>d.date>='2026-07-01'))).balance,null);
// Replacement for an identical date is counted once; no future services leak.
assert.equal(query(calc({},periods,[...sparse,daily('2026-01-05',-2)])).services,312);
assert.equal(query(calc({},periods,[...sparse,daily('2026-11-18',10000)])).services,315);
console.log('SPARSE_SOURCE_DATES=PASS; January start, missing month, invalid record, duplicate and cutoff');

// Production-shaped January–June import: 24/24/26/26/26/29 transaction dates.
// Synthetic doctor allocation; totals are fixture amounts, not a patient export.
const counts=[24,24,26,26,26,29],starts=[5,2,2,1,1,1],ends=[31,28,31,30,30,30];
const history=[],historyPaid=[];
for(let i=0;i<6;i++){
  const month='2026-'+String(i+1).padStart(2,'0');
  for(let n=0;n<counts[i];n++){
    const day=n===counts[i]-1?ends[i]:starts[i]+n;
    history.push(daily(month+'-'+String(day).padStart(2,'0'),10));
  }
  const monthEnd=new Date(Date.UTC(2026,i+1,0)).toISOString().slice(0,10);
  historyPaid.push(part(month+'-01',monthEnd,1000));
}
const historical=calc({},historyPaid,history);
assert.deepEqual(query(historical,'2026-01-01','2026-06-30'),{paid:6000,services:1550,balance:4450});
assert.equal(query(historical,'2026-05-01','2026-05-31').services,260);
assert.equal(query(historical,'2026-01-01','2026-01-17').services,130);
console.log('JANUARY_JUNE_IMPORT_SHAPE=PASS; 155 source dates, month boundaries and YTD');

// Loaded-period mode is explicit and does not redefine exact coverage.
const archived=[part('2026-01-05','2026-01-31',100),part('2026-02-02','2026-02-16',80),part('2026-02-17','2026-02-17',20)];
const transactions=[daily('2026-01-05',10),daily('2026-02-02',5),daily('2026-02-17',2)];
const loaded=(ps=archived,ds=transactions)=>calc({},ps,ds,{loadedPeriods:true});
assert.equal(query(calc({},archived,transactions),'2026-01-01','2026-02-17').balance,null);
const a=query(loaded(),'2026-01-01','2026-02-17');
assert.equal(a.balance,183);assert.equal(a.loadedPeriods,true);assert.equal(a.paymentPeriods[0].from,'2026-01-05');
assert.equal(query(loaded(),'2026-01-01','2026-02-16').balance,165);
assert.equal(query(loaded(),'2026-01-01','2026-02-15').balance,null);
assert.equal(query(loaded(),'2026-01-01','2026-02-18').balance,null);
assert.equal(query(loaded(archived.slice(1)),'2026-01-01','2026-02-17').balance,null);
assert.equal(query(loaded([archived[0],part('2026-02-02','2026-02-10',80),archived[2]]),'2026-01-01','2026-02-17').balance,null);
assert.equal(query(loaded([...archived,part('2026-02-02','2026-02-17',100)]),'2026-01-01','2026-02-17').balance,183);
assert.equal(query(loaded(archived,[transactions[0],{...transactions[1],complete:false},transactions[2]]),'2026-01-01','2026-02-17').balance,null);
assert.equal(query(loaded([part('2026-01-05','2026-01-31',null),...archived.slice(1)]),'2026-01-01','2026-02-17').balance,null);
const prodRanges=[['01-05','01-31'],['02-02','02-28'],['03-02','03-31'],['04-01','04-30'],['05-01','05-30'],['06-01','06-30'],['07-01','07-31'],['08-01','08-31'],['09-01','09-30'],['10-01','10-31'],['11-01','11-11'],['11-12','11-12'],['11-13','11-15'],['11-16','11-16'],['11-17','11-17']];
assert.equal(query(loaded(prodRanges.map(([a,b])=>part('2026-'+a,'2026-'+b,100)),days)).balance,1179);
console.log('LOADED_PAYMENT_PERIODS=PASS; explicit provenance, all 15 production-shaped periods, date changes, missing month and internal gaps');
