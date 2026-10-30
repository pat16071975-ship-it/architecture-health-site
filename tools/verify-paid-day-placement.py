#!/usr/bin/env python3
"""Read-only check that every confirmed MIS paid patient can be placed on a historical day."""
import ast,json,pathlib,sqlite3,types
from datetime import date as calendar_date,timedelta

DB=pathlib.Path('/var/lib/az-baze/auth.db')
RS=pathlib.Path('/opt/az-baze-auth/report_storage.py')
II=pathlib.Path('/opt/az-baze-auth/ident_import.py')
PPC=pathlib.Path('/opt/az-baze-auth/paid_period_confirmation.py')

def payer_evidence(payload):
    d=payload.get('payerDirectionTokens') or {}
    if payload.get('payerEvidenceVersion')!=1 or payload.get('payerEvidenceComplete') is not True:return None
    return {'all':set(payload.get('payerTokens') or []),
            'dent':set(d.get('dent') or []),'structure':set(d.get('structure') or []),
            'lab':set(d.get('lab') or []),'unallocated':set(d.get('unallocated') or [])}

# provider constants only
tree=ast.parse(II.read_text(encoding='utf-8')); ns0={'set':set}; nodes=[]
for n in tree.body:
    if isinstance(n,ast.Assign):
        names={t.id for t in n.targets if isinstance(t,ast.Name)}
        if names & {'DENTISTS','STRUCTURE_DOCTORS','LAB_DOCTORS','KNOWN_STAFF'}:nodes.append(n)
m=ast.Module(body=nodes,type_ignores=[]);ast.fix_missing_locations(m);exec(compile(m,str(II),'exec'),ns0)
ident=types.SimpleNamespace(**{k:ns0[k] for k in ('DENTISTS','STRUCTURE_DOCTORS','LAB_DOCTORS','KNOWN_STAFF')})

# pure helpers
import importlib.util
spec=importlib.util.spec_from_file_location('ppc',PPC);ppc=importlib.util.module_from_spec(spec);spec.loader.exec_module(ppc)
src=RS.read_text(encoding='utf-8');tree=ast.parse(src)
wanted={'_paid_report_sources','_management_doctor_provider_mappings','_paid_patient_interval',
'_paid_patient_intervals','_cover_paid_patient_range','_combine_paid_patient_sets','_mis_paid_patient_sets_by_month'}
nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in wanted]
mod=ast.Module(body=nodes,type_ignores=[]);ast.fix_missing_locations(mod)
ns={'json':json,'sqlite3':sqlite3,'Path':pathlib.Path,'DB_PATH':DB,'calendar_date':calendar_date,
'timedelta':timedelta,'ident_import':ident,'paid_period_confirmation':ppc}
exec(compile(mod,str(RS),'exec'),ns)

c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);c.row_factory=sqlite3.Row
try:
 c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
 intervals=ns['_paid_patient_intervals'](c)
 months=ns['_mis_paid_patient_sets_by_month'](intervals)

 # collect visit dates/directions from exact stored source payloads
 source_rows=ns['_paid_report_sources'](c)
 maps,_=ns['_management_doctor_provider_mappings'](c)
 visits={}
 for source in source_rows:
  month=source['to'][:7]
  if month not in months or source['to']>months[month]['asOf']:continue
  for key,providers in (source['payload'].get('visit_links') or {}).items():
   try: day,token=key.split('|',1)
   except ValueError: continue
   if not day.startswith(month+'-'):continue
   ent=visits.setdefault((month,token),{'all':set(),'dent':set(),'structure':set()})
   ent['all'].add(day)
   for p in providers or []:
    if p in maps['dent']:ent['dent'].add(day)
    if p in maps['structure']:ent['structure'].add(day)

 cash={}
 for row in c.execute("SELECT data_date,payload_json FROM cash_receipts_daily ORDER BY data_date"):
  day=str(row['data_date']);month=day[:7]
  try:p=json.loads(row['payload_json'])
  except Exception:continue
  ev=payer_evidence(p)
  if not ev:continue
  for token in ev['all']:
   ent=cash.setdefault((month,token),{'all':set(),'dent':set(),'structure':set(),'unallocated':set()})
   ent['all'].add(day)
   for d in ('dent','structure','unallocated'):
    if token in ev[d]:ent[d].add(day)

 print('PLACEMENT_CHECK=START')
 for month in sorted(months):
  mis=months[month]['sets']; tokens=mis['medicine']
  unresolved=[]; both=0; visit_only=0; cash_only=0
  for token in tokens:
   v=visits.get((month,token),{}).get('all',set())
   k=cash.get((month,token),{}).get('all',set())
   if v and k: both+=1
   elif v: visit_only+=1
   elif k: cash_only+=1
   else: unresolved.append(token)
  out={'month':month,'asOf':months[month]['asOf'],'medicine':len(tokens),
       'bothCashAndVisit':both,'visitOnly':visit_only,'cashOnly':cash_only,
       'unresolved':len(unresolved)}
  print('MONTH='+json.dumps(out,sort_keys=True))
 print('DB_TOTAL_CHANGES='+str(c.total_changes));print('MODE=READ_ONLY')
finally:c.close()
