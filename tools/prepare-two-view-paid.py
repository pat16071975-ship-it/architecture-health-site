"""Exact source/tests preparation only. No live paths or database access."""
from pathlib import Path
import hashlib
import subprocess

BASE='0053dd8eabf935a7daf0321c0ba9d075e32987ba'
PINS={
 'reports/index.html':'6b4c114162dd4da125ab7257fe46be7854a68651',
 'reports/period-view.js':'f1b65ad1a72925959d4c0215c871cf271755034f',
 'reports/services-primary-patient-cost.js':'8fde6671dcf9b5d05b7628f7d9bf10f05743c2a9',
 'reports/services.html':'29c57407610d52115f7df4ce28a5a5930a67645a',
}
def blob(raw):return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def one(text,old,new):
 if text.count(old)!=1:raise RuntimeError('PATCH_ANCHOR_MISMATCH: '+old[:90])
 return text.replace(old,new,1)

def patch_index(text):
 text=one(text,"d.clinicRev=window.azDoctorDirectionPaid([r],'clinicDocs')}\n", "d.clinicRev=window.azDoctorDirectionPaid([r],'clinicDocs')}\nif(typeof window.azPaidEconomicsForDate==='function')Object.assign(d,window.azPaidEconomicsForDate(r.date,paidOnly?null:r));\n")
 text=one(text,"k!=='dentRev'&&k!=='clinicRev'?null:d[k]", "!['dentRev','clinicRev','paidPatients','paidDentPatients','paidClinicPatients','dentAvg','clinicAvg'].includes(k)?null:d[k]")
 return one(text,'20261029-two-views-1','20261029-paid-two-views-2')

HELPERS=r'''
  // Counts and average checks share one exact period in BOTH approved views.
  // A final monthly patient list is never relabelled as an earlier day.
  function paidCount(value){
    if(value===null||value===undefined||value===''||typeof value==='boolean')return null;
    const n=Number(value);return Number.isSafeInteger(n)&&n>=0?n:null;
  }
  function paidAmount(value){
    if(value===null||value===undefined||value===''||typeof value==='boolean')return null;
    const n=Number(value);return Number.isFinite(n)?n:null;
  }
  function exactPayersForDate(date,record){
    if(!paidDate(date))return null;
    const from=date.slice(0,7)+'-01';
    let row=null;
    if(record&&record.date===date&&record.paidPatientEvidenceComplete===true&&
       (!record.paidPatientPeriodFrom||record.paidPatientPeriodFrom===from)&&
       (!record.paidPatientPeriodTo||record.paidPatientPeriodTo===date)){
      row={medicine:record.paidPatients,dent:record.paidDentPatients,structure:record.paidClinicPatients};
    }else{
      const source=typeof PAID_PATIENTS_MONTHLY==='undefined'?{}:PAID_PATIENTS_MONTHLY;
      const candidate=source[date.slice(0,7)];
      if(candidate&&candidate.asOf===date)row=candidate;
    }
    if(!row)return null;
    const result={medicine:paidCount(row.medicine),dent:paidCount(row.dent),structure:paidCount(row.structure)};
    return Object.values(result).some(value=>value===null)?null:result;
  }
  function paidEconomicsForDate(date,record){
    const payers=exactPayersForDate(date,record);
    const result={paidPatients:payers?payers.medicine:null,paidDentPatients:payers?payers.dent:null,
      paidClinicPatients:payers?payers.structure:null,avgCheck:null,dentAvg:null,clinicAvg:null};
    if(!payers)return result;
    const sameCashPeriod=record&&record.date===date&&(!record._cashAsOf||record._cashAsOf===date);
    const medicine=sameCashPeriod?paidAmount(record.factMedicine):null;
    if(payers.medicine>0&&medicine!==null)result.avgCheck=medicine/payers.medicine;
    const from=date.slice(0,7)+'-01';
    const parts=completeDoctorPaidParts(from,date,doctorPaidIntervals(from,date));
    if(parts!==null&&parts.length){
      const dent=doctorPartsDirectionPaid(parts,'dentists'),clinic=doctorPartsDirectionPaid(parts,'clinicDocs');
      if(payers.dent>0&&Number.isFinite(dent))result.dentAvg=dent/payers.dent;
      if(payers.structure>0&&Number.isFinite(clinic))result.clinicAvg=clinic/payers.structure;
    }
    return result;
  }
  window.azPaidEconomicsForDate=paidEconomicsForDate;
'''

def patch_period(text):
 text=one(text,'  window.azDoctorDirectionPaid=doctorDirectionPaid;\n','  window.azDoctorDirectionPaid=doctorDirectionPaid;\n'+HELPERS)
 text=one(text,"    return rawOrNull(column.d?.[keys[scope]]);", "    return rawOrNull(column.economics?.[keys[scope]]);")
 text=one(text,"      return {month,target,r,d:r?derive(r):null,paidParts:paid.length?paid:r?[r]:[]};", "      const economics=paidEconomicsForDate(target,r);\n      return {month,target,r,d:r?{...derive(r),...economics}:null,economics,paidParts:paid.length?paid:r?[r]:[]};")
 text=one(text,"months,m=>comparisonPayer(m,'dent')&&m.paidParts.length?doctorPartsDirectionPaid(m.paidParts,'dentists')/comparisonPayer(m,'dent'):null,fmtMoney,'dentAvg'", "months,m=>m.monthly?(comparisonPayer(m,'dent')&&m.paidParts.length?doctorPartsDirectionPaid(m.paidParts,'dentists')/comparisonPayer(m,'dent'):null):m.economics.dentAvg,fmtMoney,'dentAvg'")
 text=one(text,"months,m=>comparisonPayer(m,'structure')&&m.paidParts.length?doctorPartsDirectionPaid(m.paidParts,'clinicDocs')/comparisonPayer(m,'structure'):null,fmtMoney,'clinicAvg'", "months,m=>m.monthly?(comparisonPayer(m,'structure')&&m.paidParts.length?doctorPartsDirectionPaid(m.paidParts,'clinicDocs')/comparisonPayer(m,'structure'):null):m.economics.clinicAvg,fmtMoney,'clinicAvg'")
 return text

def patch_cost(text):
 old='''    if (!validCostIndexes(costData, payerSeries, idx)) return null;

    const paidPatients = sumIndexes(payerSeries, idx);
    const cost = weightedCost(costData, payerSeries, idx);
    const load = idx.reduce((sum, i) => sum + (+payerSeries[i] || 0) * monthlyCost(costData, payerSeries, i), 0);'''
 new='''    const clinicPayers = paidPatientSeries(costData, 'medicine');
    if (!validCostIndexes(costData, payerSeries, idx) ||
        !validCostIndexes(costData, clinicPayers, idx) ||
        idx.some(i => clinicPayers[i] <= 0)) return null;

    const paidPatients = sumIndexes(payerSeries, idx);
    // The common expense pool is divided by ALL medical payers, not this scope.
    const cost = weightedCost(costData, clinicPayers, idx);
    const load = idx.reduce((sum, i) => sum + payerSeries[i] * monthlyCost(costData, clinicPayers, i), 0);'''
 text=one(text,old,new)
 text=one(text,"    if (!validCostIndexes(costData, payerSeries, idx)) return out;", "    if (!validCostIndexes(costData, payerSeries, idx) || idx.some(i => payerSeries[i] <= 0)) return out;")
 text=one(text,"      if (!month) return null;", "      if (!month || !paidCostMonthComplete(month, costMonthKey(costData, i))) return null;")
 text=one(text,"      if (direction === 'medicine') return Number.isFinite(+month.medicine) ? +month.medicine : null;", "      if (direction === 'medicine') return costPayerCount(month.medicine);")
 text=one(text,"        if (!doctor) return Number.isFinite(+month.dent) ? +month.dent : null;", "        if (!doctor) return costPayerCount(month.dent);")
 text=one(text,"        if (!doctor) return Number.isFinite(+month.structure) ? +month.structure : null;", "        if (!doctor) return costPayerCount(month.structure);")
 # Exact display name first. A surname fallback is allowed only if unambiguous.
 old="        const key = Object.keys(entries).find(name => normalizedDoctorKey(name) === doctorKey);\n        return key && Number.isFinite(+entries[key]) ? +entries[key] : 0;"
 new="        const keys = Object.keys(entries).filter(name => normalizedDoctorKey(name) === doctorKey);\n        const key = Object.hasOwn(entries, doctor) ? doctor : keys.length === 1 ? keys[0] : null;\n        return key !== null ? costPayerCount(entries[key]) : keys.length ? null : 0;"
 if text.count(old)!=2:raise RuntimeError('DOCTOR_COST_ANCHOR_MISMATCH')
 text=text.replace(old,new)
 text=one(text,"  function isCostDataReady(data) {",'''  function costPayerCount(value) {
    if (value == null || value === '' || typeof value === 'boolean') return null;
    const n = Number(value);
    return Number.isSafeInteger(n) && n >= 0 ? n : null;
  }

  function paidCostMonthComplete(source, month) {
    if (!/^\\d{4}-\\d{2}$/.test(month) || !source || typeof source.asOf !== 'string') return false;
    const year = +month.slice(0,4), m = +month.slice(5,7);
    if (m < 1 || m > 12) return false;
    const end = month + '-' + String(new Date(Date.UTC(year, m, 0)).getUTCDate()).padStart(2, '0');
    return source.asOf === end;
  }

  function isCostDataReady(data) {''')
 text=one(text,"        costData.numerator?.[i] != null &&\n        payerSeries?.[i] != null", "        costData.numerator?.[i] != null && costData.numerator[i] !== '' &&\n        typeof costData.numerator[i] !== 'boolean' && Number.isFinite(+costData.numerator[i]) &&\n        costPayerCount(payerSeries?.[i]) !== null")
 text=one(text,"    return payers ? (+costData.numerator?.[i] || 0) / payers : 0;", "    return payers > 0 && validCostIndexes(costData, payerSeries, [i]) ? +costData.numerator[i] / payers : null;")
 return text

PATCHES={
 'reports/index.html':patch_index,
 'reports/period-view.js':patch_period,
 'reports/services-primary-patient-cost.js':patch_cost,
 'reports/services.html':lambda s:one(s,'./services-primary-patient-cost.js?v=20261028-paid-patients-1','./services-primary-patient-cost.js?v=20261029-paid-two-views-2'),
}

def main():
 for name,expected in PINS.items():
  p=Path(name);raw=p.read_bytes()
  # Exact original Git object ensures preparation is repeatable without stacking.
  original=subprocess.check_output(['git','show',BASE+':'+name])
  if blob(original)!=expected:raise RuntimeError('BASE_BLOB_MISMATCH: '+name)
  target=PATCHES[name](original.decode()).encode()
  if raw not in (original,target):raise RuntimeError('SOURCE_DRIFT: '+name)
  p.write_bytes(target)
  print(name+'='+blob(target))
 # Exact expectation refreshes only; new behavioral regressions cover the changed averages.
 for name in ('tests/verify-paid-periods.cjs','az-baze-auth/test_manual_entry_consistency.py','az-baze-auth/test_paid_history_reporting.py'):
  p=Path(name);original=subprocess.check_output(['git','show',BASE+':'+name]).decode()
  old='20261029-two-views-1' if '20261029-two-views-1' in original else '20261029-paid-history-1'
  target=one(original,old,'20261029-paid-two-views-2')
  if name.endswith('verify-paid-periods.cjs'):
   target=one(target,"assert.equal(values.dentAvg,h.money(75));assert.equal(values.clinicAvg,h.money(25));", "const covered=['2026-07-31','2026-08-31','2026-09-30'].includes(date);\n    assert.equal(values.dentAvg,h.money(covered?dent/4:null));assert.equal(values.clinicAvg,h.money(covered?clinic/4:null));")
  if p.read_text() not in (original,target):raise RuntimeError('TEST_DRIFT:'+name)
  p.write_text(target)
 name='az-baze-auth/test_report_consistency_preservation.py';p=Path(name)
 original=subprocess.check_output(['git','show',BASE+':'+name]).decode();target=original
 for path in ('reports/index.html','reports/period-view.js'):
  target=one(target,PINS[path],blob(Path(path).read_bytes()))
 if p.read_text() not in (original,target):raise RuntimeError('PRESERVATION_TEST_DRIFT')
 p.write_text(target)
 print('TWO_VIEW_PAID_SOURCE=PREPARED; LIVE_ACCESS=NONE')

if __name__=='__main__':main()
