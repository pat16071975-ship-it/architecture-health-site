#!/usr/bin/env python3
"""Read-only Production diagnostic for arbitrary-day paid-patient reconciliation."""
import ast
import json
import pathlib
import sqlite3
import sys
import types

DB=pathlib.Path('/var/lib/az-baze/auth.db')
REPORT_STORAGE=pathlib.Path('/opt/az-baze-auth/report_storage.py')
CASH_PAYMENTS=pathlib.Path('/opt/az-baze-auth/cash_payments.py')
CONFIG=pathlib.Path('/var/lib/az-baze/paid-period-confirmations-v1.json')

def stop(msg):
    print('PAID_CASH_RECON=STOP; '+msg)
    raise SystemExit(1)

for p in (DB,REPORT_STORAGE,CASH_PAYMENTS,CONFIG):
    if not p.exists():
        stop('MISSING='+str(p))

# Load cash module only; it has no import-time DB writes.
sys.path.insert(0,str(CASH_PAYMENTS.parent))
import cash_payments

# Extract exact pure/read functions from live report_storage without importing Flask/app.
source=REPORT_STORAGE.read_text(encoding='utf-8')
tree=ast.parse(source)
wanted={
    '_paid_report_sources',
    '_management_doctor_provider_mappings',
    '_paid_patient_interval',
    '_paid_patient_intervals',
    '_cover_paid_patient_range',
    '_combine_paid_patient_sets',
    '_mis_paid_patient_sets_by_month',
}
nodes=[]
for node in tree.body:
    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in wanted:
        nodes.append(node)
module=ast.Module(body=nodes,type_ignores=[])
ast.fix_missing_locations(module)

ns={
    'json':json,
    'sqlite3':sqlite3,
    'Path':pathlib.Path,
    'paid_services':types.SimpleNamespace(),
    'paid_period_confirmation':None,
}
# _paid_report_sources depends on confirmation helper; extract that module as normal
# because it is pure config/JSON logic.
import importlib.util
spec=importlib.util.spec_from_file_location('paid_period_confirmation',REPORT_STORAGE.parent/'paid_period_confirmation.py')
ppc=importlib.util.module_from_spec(spec); spec.loader.exec_module(ppc)
ns['paid_period_confirmation']=ppc

# minimal paid_services.direction_summary is not needed by selected functions.
exec(compile(module,str(REPORT_STORAGE),'exec'),ns)

conn=sqlite3.connect(f'file:{DB}?mode=ro',uri=True,timeout=10)
conn.row_factory=sqlite3.Row
try:
    conn.execute('PRAGMA query_only=ON')
    if conn.execute('PRAGMA query_only').fetchone()[0]!=1:
        stop('QUERY_ONLY_OFF')
    conn.execute('BEGIN')

    # selected functions expect config helper to find live config in the normal place.
    intervals=ns['_paid_patient_intervals'](conn)
    months=ns['_mis_paid_patient_sets_by_month'](intervals)

    rows=conn.execute(
        "SELECT data_date,payload_json FROM cash_receipts_daily ORDER BY data_date"
    ).fetchall()
    by_month={}
    for row in rows:
        day=str(row['data_date'])
        try:
            payload=json.loads(row['payload_json'])
        except Exception:
            continue
        by_month.setdefault(day[:7],[]).append((day,payload))

    print('PAID_CASH_RECON=START')
    for month in sorted(months):
        as_of=months[month]['asOf']
        mis=months[month]['sets']
        mrows=[(d,p) for d,p in by_month.get(month,[]) if d<=as_of]

        ev_all=set(); ev_dent=set(); ev_structure=set(); ev_lab=set(); ev_unalloc=set()
        incomplete_days=[]
        complete_days=0
        coverage_ok=False
        coverage_min=None; coverage_max=None
        for day,payload in mrows:
            ev=cash_payments.payer_evidence(payload)
            cash_total=float(payload.get('cashTotal') or 0)
            if ev is None:
                if cash_total>0:
                    incomplete_days.append(day)
                continue
            complete_days+=1
            coverage_min=ev['coverageFrom'] if coverage_min is None else min(coverage_min,ev['coverageFrom'])
            coverage_max=ev['coverageTo'] if coverage_max is None else max(coverage_max,ev['coverageTo'])
            if ev['coverageFrom']<=month+'-01' and ev['coverageTo']>=as_of:
                coverage_ok=True
            ev_all.update(ev['all'])
            ev_dent.update(ev['dent'])
            ev_structure.update(ev['structure'])
            ev_lab.update(ev['lab'])
            ev_unalloc.update(ev['unallocated'])

        # cash patients that are in confirmed MIS membership; this mirrors the intended filter.
        cash_seen_med=ev_all & mis['medicine']
        cash_seen_dent=ev_all & mis['dent']
        cash_seen_structure=ev_all & mis['structure']
        cash_seen_lab=ev_all & mis['lab']

        out={
            'month':month,
            'asOf':as_of,
            'mis':{
                'medicine':len(mis['medicine']),
                'dent':len(mis['dent']),
                'structure':len(mis['structure']),
                'lab':len(mis['lab']),
            },
            'cash':{
                'uniqueAll':len(ev_all),
                'directDent':len(ev_dent),
                'directStructure':len(ev_structure),
                'directLab':len(ev_lab),
                'unallocated':len(ev_unalloc),
                'completeDays':complete_days,
                'incompletePositiveDays':len(incomplete_days),
            },
            'overlapWithMIS':{
                'medicine':len(cash_seen_med),
                'dent':len(cash_seen_dent),
                'structure':len(cash_seen_structure),
                'lab':len(cash_seen_lab),
            },
            'missingFromCash':{
                'medicine':len(mis['medicine']-ev_all),
                'dent':len(mis['dent']-ev_all),
                'structure':len(mis['structure']-ev_all),
                'lab':len(mis['lab']-ev_all),
            },
            'cashOutsideMIS':len(ev_all-(mis['medicine']|mis['lab'])),
            'coverageOk':coverage_ok,
            'coverageFrom':coverage_min,
            'coverageTo':coverage_max,
            'firstIncompleteDays':incomplete_days[:5],
        }
        print('MONTH='+json.dumps(out,ensure_ascii=False,sort_keys=True))

    print('DB_TOTAL_CHANGES='+str(conn.total_changes))
    print('MODE=READ_ONLY')
finally:
    conn.close()
