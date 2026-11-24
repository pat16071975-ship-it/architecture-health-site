const assert=require('node:assert/strict');
const {calculate,title}=require('../reports/management-lab.js');
const day=(date,services,orders)=>({date,services,orders});
const p=(from,to,paid)=>({from,to,paid});
const from='2026-11-01',to='2026-11-17';
const days=[day('2026-11-03',600,1),day('2026-11-10',300,1),day('2026-11-18',99999,99)];
const parts=[p(from,'2026-11-10',100),p('2026-11-11',to,300),p(from,to,400),p(from,'2026-11-30',9999)];
assert.deepEqual(calculate(parts,days,from,to),{paid:400,services:900,orders:2,avg:null,loadedDays:2,expectedDays:17,ordersComplete:false});
assert.equal(calculate(parts.slice(0,2),days,from,to).paid,400);
assert.equal(calculate([parts[0],parts[3]],days,from,to).paid,null);
assert.equal(calculate([...parts,p(from,to,null)],days,from,to).paid,null);
assert.equal(calculate([p(from,to,0)],days,from,to).avg,null);
assert.equal(calculate([p(from,to,-10)],days,from,to).paid,-10);
assert.equal(calculate([p(from,to,400)],[],from,to).services,null);
assert.equal(calculate([p(from,to,400)],[day('2026-11-03',0,0)],from,to).avg,null);
assert.equal(calculate([p(from,to,400)],[day('2026-11-03',600,null)],from,to).orders,null);
assert.equal(calculate([p(from,to,400)],[day('2026-11-03',null,null)],from,to).services,null);
assert.equal(calculate([p('2026-10-01',to,400)],days,'2026-10-01',to).services,null);
assert.equal(calculate([p(from,to,400)],days,'2026-11-30','2026-11-01').paid,null);

// AZ-260-01: reproduce the independent auditor's two original counterexamples.
let result=calculate([p(from,to,1700)],[day('2026-11-03',600,1)],from,to);
assert.deepEqual(result,{paid:1700,services:600,orders:1,avg:null,loadedDays:1,expectedDays:17,ordersComplete:false});
assert.match(title('avg',result,from,to),/Полнота количества счетов за весь период не подтверждена/);
assert.match(title('orders',result,from,to),/1 из 17/);
assert.match(title('services',result,from,to),/по загруженным датам/);
result=calculate([p('2026-01-01','2026-03-31',9000)],[
  day('2026-01-03',100,1),day('2026-02-03',100,1),day('2026-03-03',100,1)
],'2026-01-01','2026-03-31');
assert.deepEqual(result,{paid:9000,services:300,orders:3,avg:null,loadedDays:3,expectedDays:90,ordersComplete:false});

function completeDays(start,end){
  const out=[];
  for(let d=new Date(start+'T12:00:00Z');d.toISOString().slice(0,10)<=end;d.setUTCDate(d.getUTCDate()+1))
    out.push(day(d.toISOString().slice(0,10),100,1));
  return out;
}
// Full coverage is sufficient, but every edge/internal missing day, duplicate
// or unknown invoice count must prevent division in every comparison mode.
for(const [label,start,end,n] of [
  ['three days','2026-11-01','2026-11-03',3],
  ['month-to-date',from,to,17],
  ['month','2026-11-01','2026-11-30',30],
  ['quarter','2026-01-01','2026-03-31',90],
  ['half-year','2026-01-01','2026-06-30',181],
  ['year','2026-01-01','2026-12-31',365],
  ['leap-year','2024-01-01','2024-12-31',366],
]){
  const all=completeDays(start,end),payments=[p(start,end,n*100)];
  assert.equal(all.length,n,label);
  const complete=calculate(payments,all,start,end);
  assert.equal(complete.avg,100,label);
  assert.equal(complete.ordersComplete,true,label);
  assert.equal(complete.expectedDays,n,label);
  for(const i of [0,Math.floor(n/2),n-1]){
    const partial=all.filter((_,index)=>index!==i);
    // Even duplicate rows on a loaded date cannot fill an absent date.
    const missing=calculate(payments,[...partial,partial[0]],start,end);
    assert.equal(missing.loadedDays,n-1,label);
    assert.equal(missing.orders,n-1,label);
    assert.equal(missing.services,(n-1)*100,label);
    assert.equal(missing.avg,null,label);
    assert.equal(missing.ordersComplete,false,label);
  }
  const monthlySparse=all.filter(x=>x.date.endsWith('-01'));
  assert.equal(calculate(payments,monthlySparse,start,end).avg,null,label);
  assert.equal(calculate(payments,all.map((x,i)=>i===0?{...x,orders:null}:x),start,end).avg,null,label);
  assert.equal(calculate(payments,all.map((x,i)=>i===0?{...x,services:null,orders:null}:x),start,end).avg,null,label);
  // A proved zero-order day can participate; no invented zero row for a gap.
  const knownZero=all.map((x,i)=>i===0?{...x,orders:0,services:0}:x);
  assert.equal(calculate(payments,knownZero,start,end).ordersComplete,true,label);
  assert.equal(calculate([p(start,end,0)],knownZero,start,end).avg,0,label);
  assert.equal(calculate([p(start,end,-(n-1)*100)],knownZero,start,end).avg,-100,label);
  const none=calculate(payments,all.map(x=>({...x,orders:0,services:0})),start,end);
  assert.equal(none.ordersComplete,true,label);
  assert.equal(none.avg,null,label);
}
const full=completeDays(from,to);
assert.equal(calculate(parts.slice(0,2),full,from,to).avg,400/17);
assert.equal(calculate([p(from,'2026-11-10',100)],full,from,to).avg,null);
assert.equal(calculate([p(from,to,null)],full,from,to).avg,null);
assert.equal(calculate([p(from,to,1700)],[...full,day('2026-11-18',999,999)],from,to).avg,100);
assert.equal(calculate([p('2026-02-28','2026-03-01',200)],[day('2026-02-28',100,1),day('2026-03-01',100,1)],'2026-02-28','2026-03-01').avg,100);
assert.equal(calculate([p('2024-02-28','2024-03-01',300)],[day('2024-02-28',100,1),day('2024-03-01',100,1)],'2024-02-28','2024-03-01').avg,null);
console.log('MANAGEMENT_LAB_PERIODS=PASS; AZ-260-01=PASS');
