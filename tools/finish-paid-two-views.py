"""Repository-only preparation of the scoped PR165 correction. Not an installer."""
from pathlib import Path
import hashlib
import subprocess

BASE='e93462b64d64cd1689629328d189a8301cb97332'
RUNTIME=['az-baze-auth/report_storage.py','reports/index.html','reports/period-view.js','reports/management-mobile-layout.js']
TESTS=['tests/verify-paid-periods.cjs','az-baze-auth/test_manual_entry_consistency.py','az-baze-auth/test_paid_history_reporting.py','az-baze-auth/test_report_consistency_preservation.py']
BACKEND='''def _management_paid_patients_by_date(conn, intervals=None):
    """Every provable MTD endpoint, not just the latest endpoint per month.

    A completed monthly export cannot prove an earlier cutoff. Source intervals
    and patient tokens stay private; only aggregate counts leave this function.
    """
    intervals = _paid_patient_intervals(conn) if intervals is None else intervals
    result = {}
    for end in sorted({part["to"] for part in intervals}):
        start = end[:7] + "-01"
        counts = _paid_patient_counts(conn, start, end, intervals)
        if counts is not None:
            result[end] = {"from": start, "to": end, "asOf": end, **counts}
    return result


def _management_paid_patients_monthly(conn, intervals=None):
    # A late incremental slice can extend an earlier MTD source. It need not
    # itself begin on day 1; the complete covering is proven above.
    result = {}
    for end, row in _management_paid_patients_by_date(conn, intervals).items():
        result[end[:7]] = {key: value for key, value in row.items()
                          if key not in ("from", "to")}
    return result


'''
PERIOD_HELPER='''

  function mergedPaidRanges(parts){
    const ranges=[];
    for(const part of parts){
      const to=part.to||part.asOf,from=part.from||(part.asOf?part.asOf.slice(0,7)+'-01':null);
      if(!paidDate(from)||!paidDate(to)||from>to)return null;
      ranges.push({from,to});
    }
    ranges.sort((a,b)=>a.from.localeCompare(b.from));
    const out=[];
    for(const row of ranges){
      const prev=out[out.length-1];
      if(prev&&row.from<=prev.to)return null;
      if(prev&&row.from===nextPaidDay(prev.to))prev.to=row.to;
      else out.push({...row});
    }
    return out;
  }
  function paidEconomicsForPeriod(pd,year){
    const store=loadStore(),monthly=typeof PAID_PATIENTS_MONTHLY==='undefined'?{}:PAID_PATIENTS_MONTHLY;
    const empty={paidPatients:null,paidDentPatients:null,paidClinicPatients:null,avgCheck:null,dentAvg:null,clinicAvg:null};
    const dates=[];
    for(let m=pd.def.sm;m<=pd.def.em;m++){
      const month=`${year}-${String(m).padStart(2,'0')}`,row=pd.rows.find(r=>r.date?.slice(0,7)===month);
      const candidate=monthly[month],sourceDate=candidate?.asOf;
      const sourceValid=paidDate(sourceDate)&&sourceDate.slice(0,7)===month;
      if(!row&&!sourceValid&&!pd.paidParts.some(p=>(p.from||p.asOf||'').slice(0,7)<=month&&(p.to||p.asOf||'').slice(0,7)>=month))continue;
      // A period total may use its available MTD source, but no later source
      // than the actual record when that period is still incomplete.
      const date=sourceValid&&(!row||sourceDate<=row.date)?sourceDate:row?.date;
      if(!paidDate(date))return empty;
      const payers=exactPayersForDate(date,store[date]);
      if(!payers)return empty;
      dates.push({date,payers});
    }
    if(!dates.length)return empty;
    const result={...empty};
    for(const [out,key] of [['paidPatients','medicine'],['paidDentPatients','dent'],['paidClinicPatients','structure']])
      result[out]=dates.reduce((sum,x)=>sum+x.payers[key],0);
    // Preserve the existing period denominator: sum of monthly unique counts.
    // Never average already calculated monthly average checks.
    const medicine=dates.map(({date})=>{
      const r=store[date];return r&&(!r._reportSourceDate||r._reportSourceDate===date)&&(!r._cashAsOf||r._cashAsOf===date)?paidAmount(r.factMedicine):null;
    });
    if(result.paidPatients>0&&medicine.every(x=>x!==null))
      result.avgCheck=medicine.reduce((s,x)=>s+x,0)/result.paidPatients;
    const expected=mergedPaidRanges(dates.map(x=>({from:x.date.slice(0,7)+'-01',to:x.date})));
    const actual=mergedPaidRanges(pd.paidParts);
    if(expected&&actual&&JSON.stringify(expected)===JSON.stringify(actual)){
      const dent=doctorPartsDirectionPaid(pd.paidParts,'dentists'),clinic=doctorPartsDirectionPaid(pd.paidParts,'clinicDocs');
      if(result.paidDentPatients>0&&Number.isFinite(dent))result.dentAvg=dent/result.paidDentPatients;
      if(result.paidClinicPatients>0&&Number.isFinite(clinic))result.clinicAvg=clinic/result.paidClinicPatients;
    }
    return result;
  }
'''

def blob(raw):
    return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()

def one(text,old,new):
    if text.count(old)!=1:raise RuntimeError('SOURCE_ANCHOR_MISMATCH: '+old[:100])
    return text.replace(old,new,1)

def prepare():
    root=Path(__file__).resolve().parents[1]
    baseline={p:subprocess.check_output(['git','show',BASE+':'+p],cwd=root) for p in RUNTIME+TESTS}
    target={p:b.decode() for p,b in baseline.items()}
    p='az-baze-auth/report_storage.py';s=target[p]
    a=s.index('def _management_paid_patients_monthly(conn):');b=s.index('def _management_doctor_paid_snapshots(conn):',a)
    s=s[:a]+BACKEND+s[b:]
    s=one(s,'PAID_PATIENTS_MONTHLY={};','PAID_PATIENTS_MONTHLY={},PAID_PATIENTS_BY_DATE={};')
    s=one(s,'  PAID_PATIENTS_MONTHLY=payload.paidPatientsMonthly||{};','  PAID_PATIENTS_MONTHLY=payload.paidPatientsMonthly||{};\n  PAID_PATIENTS_BY_DATE=payload.paidPatientsByDate||{};')
    s=one(s,'            paidPatientsMonthly=_management_paid_patients_monthly(db()),','            paidPatientsMonthly=_management_paid_patients_monthly(db(), payer_intervals),\n            paidPatientsByDate=_management_paid_patients_by_date(db(), payer_intervals),')
    target[p]=s
    p='reports/period-view.js';s=target[p]
    s=one(s,"    let row=null;\n    if(record&&record.date===date&&record.paidPatientEvidenceComplete===true&&", "    let row=null;\n    const exact=typeof PAID_PATIENTS_BY_DATE==='undefined'?{}:PAID_PATIENTS_BY_DATE;\n    const saved=exact?.[date];\n    if(saved&&saved.from===from&&saved.to===date&&saved.asOf===date){\n      row=saved;\n    }else if(record&&record.date===date&&record.paidPatientEvidenceComplete===true&&")
    s=one(s,'  window.azPaidEconomicsForDate=paidEconomicsForDate;','  window.azPaidEconomicsForDate=paidEconomicsForDate;'+PERIOD_HELPER)
    s=one(s,"const sameCashPeriod=record&&record.date===date&&(!record._cashAsOf||record._cashAsOf===date);", "const sameCashPeriod=record&&record.date===date&&(!record._reportSourceDate||record._reportSourceDate===date)&&(!record._cashAsOf||record._cashAsOf===date);")
    s=one(s,"    return {...data,bounds:{from,to},paidParts:doctorPeriodParts(data.rows,{from,to})};", "    const pd={...data,bounds:{from,to},paidParts:doctorPeriodParts(data.rows,{from,to})};\n    return {...pd,economics:paidEconomicsForPeriod(pd,year)};")
    for key in ['paidPatients','paidDentPatients','paidClinicPatients','avgCheck']:
        s=one(s,f'pds,a=>a.{key},',f'pds,(a,rows,pd)=>pd.economics.{key},')
        a=s.index('  window.renderPeriod=function(){');b=s.index('  const toolbar=',a)
        s=s[:a]+one(s[a:b],f",'{key}');",f",'{key}',true);")+s[b:]
    for key,den,kind in [('dentAvg','paidDentPatients','dentists'),('clinicAvg','paidClinicPatients','clinicDocs')]:
        s=one(s,f"pds,(a,rows,pd)=>a?.{den}?doctorPartsDirectionPaid(pd.paidParts,'{kind}')/a.{den}:null,",f'pds,(a,rows,pd)=>pd.economics.{key},')
    s=one(s,"else{editView?.classList.remove('hidden');renderMismatchNote(currentRecord())}","else{editView?.classList.remove('hidden');const r=currentRecord(),date=document.getElementById('reportDate').value;fillRecord(r||blankRecord(date));renderMismatchNote(r)}")
    s=one(s,"    const from=date.slice(0,7)+'-01',covered=completeDoctorPaidParts(from,date,doctorPaidIntervals(from,date));\n    if(covered!==null)return covered;\n    const snapshot=doctorPaidSnapshot(date);\n    return snapshot?[snapshot]:[];", "    const from=date.slice(0,7)+'-01',intervals=doctorPaidIntervals(from,date);\n    const covered=completeDoctorPaidParts(from,date,intervals);\n    if(covered!==null)return covered;\n    // Preserve the existing latest-confirmed fallback for BOTH storage kinds.\n    // A newer cash/clinical day does not erase an earlier explicit MIS interval.\n    const ends=[...new Set(intervals.map(part=>part.to))].sort().reverse();\n    for(const end of ends){\n      const earlier=completeDoctorPaidParts(from,end,intervals);\n      if(earlier!==null)return earlier;\n    }\n    return [];")
    target[p]=s
    p='reports/index.html';s=one(target[p],'20261029-paid-two-views-2','20261029-paid-two-views-3')
    old='<script src="./period-view.js?v=20261029-paid-two-views-3"></script>'
    target[p]=one(s,old,old+'\n<script src="./management-mobile-layout.js?v=20261029-paid-two-views-3"></script>')
    p='reports/management-mobile-layout.js';s=target[p]
    for old,new in [('return {...target[latest], date: prop};','return {...target[latest], _reportSourceDate: latest, date: prop};'),('factMedicine / primary','factMedicine / paidPatients'),('dentRev / dentPrimary','dentRev / paidDentPatients'),('clinicRev / clinicPrimary','clinicRev / paidClinicPatients'),('Средний чек: выручка / первичные приёмы соответствующего направления.','Средний чек: выручка / оплатившие пациенты соответствующего направления за тот же период.')]:s=one(s,old,new)
    target[p]=s
    for p in TESTS:
        s=target[p]
        if p.endswith('test_report_consistency_preservation.py'):
            for name in ['reports/index.html','reports/period-view.js']:s=one(s,blob(baseline[name]),blob(target[name].encode()))
        else:s=one(s,'20261029-paid-two-views-2','20261029-paid-two-views-3')
        target[p]=s
    for p,s in target.items():
        if (root/p).read_bytes() not in (baseline[p],s.encode()):raise RuntimeError('REPOSITORY_DRIFT: '+p)
    for p,s in target.items():
        (root/p).write_text(s)
        print(p+'='+blob(s.encode()))
    print('PAID_TWO_VIEWS_PREPARED=PASS; LIVE_ACCESS=NONE')

if __name__=='__main__':prepare()
