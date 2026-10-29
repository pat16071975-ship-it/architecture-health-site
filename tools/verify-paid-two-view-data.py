"""Read-only validation of existing AZ-BAZE data against exact PR165 source.

No application import, schema initialization, SQL writes, installation, permission
changes or service actions. Only aggregate numeric output, never patient tokens.
"""
from __future__ import annotations
import ast
import calendar
from datetime import date, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys
from types import SimpleNamespace
from urllib.request import urlopen

TARGET = 'aa4d10259faff4d61479f0cfcf932d5edcf67b7c'
TARGET_STORAGE = '45da0bd7939240d5c2e109fea2ddde3519746b31'
BASE_STORAGE = 'de8529f5830609d4aeb5bd5edf0791c9355ba296'
CONFIG_SHA256 = '8fe1f6e18ffa04c081d67b5011fe1ec20fc12f0dec678a25b61bb7a634679b69'
DEPENDENCIES = {
    'ident_import.py': 'e9950b781b0f4ebdfe044860b948813938a8089d',
    'paid_services.py': '7731d49098494276e40442d06443a0729fc7fcf3',
    'paid_period_confirmation.py': 'dc3f409877e6d28bfbba4a90bd539e99f01b76cb',
}
FUNCTIONS = {
    '_management_doctor_provider_mappings', '_paid_patient_interval',
    '_paid_report_sources', '_paid_patient_intervals', '_cover_paid_patient_range',
    '_combine_paid_patient_intervals', '_paid_patient_counts',
    '_management_paid_patients_by_date', '_management_paid_patients_monthly',
    '_management_doctor_paid_snapshots', '_management_doctor_paid_periods',
}


def git_blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def extract(raw, names):
    nodes = [node for node in ast.parse(raw.decode('utf-8')).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    require(len(nodes) == len(names) and not any(n.decorator_list for n in nodes),
            'FUNCTION_CONTRACT_MISMATCH')
    return compile(ast.Module(body=nodes, type_ignores=[]), '<verified-functions>', 'exec')


def build_scope(raws, db_path):
    # Only pinned standalone standard-library helper is executed as a module.
    conf = {'__name__': 'verified_paid_period_confirmation'}
    exec(compile(raws['paid_period_confirmation.py'], '<verified-confirmation>', 'exec'), conf)
    declarations = {}
    for node in ast.parse(raws['ident_import.py'].decode('utf-8')).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            declarations[node.targets[0].id] = node.value
    names = ('DENTISTS', 'STRUCTURE_DOCTORS', 'LAB_DOCTORS')
    maps = {name: ast.literal_eval(declarations[name]) for name in names}
    require(all(isinstance(m, dict) and all(isinstance(k, str) and isinstance(v, str)
                for k, v in m.items()) for m in maps.values()), 'MAP_CONTRACT_MISMATCH')
    def staff(node):
        if isinstance(node, ast.Set):
            result = ast.literal_eval(node)
            require(all(isinstance(v, str) for v in result), 'STAFF_LITERAL_INVALID')
            return result
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return staff(node.left) | staff(node.right)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'set'
                and len(node.args) == 1 and not node.keywords and isinstance(node.args[0], ast.Name)
                and node.args[0].id in maps):
            return set(maps[node.args[0].id])
        raise RuntimeError('STAFF_CONTRACT_MISMATCH')
    maps['KNOWN_STAFF'] = staff(declarations['KNOWN_STAFF'])
    paid = {'math': math}
    exec(extract(raws['paid_services.py'], {'_num', 'direction_summary'}), paid)
    scope = {'json': json, 'math': math, 'calendar_date': date, 'timedelta': timedelta,
             'DATE_RE': re.compile(r'^\d{4}-\d{2}-\d{2}$'), 'DB_PATH': db_path,
             'ident_import': SimpleNamespace(**maps),
             'paid_services': SimpleNamespace(direction_summary=paid['direction_summary']),
             'paid_period_confirmation': SimpleNamespace(**conf)}
    exec(extract(raws['report_storage.py'], FUNCTIONS), scope)
    return scope


def amount(value):
    if value is None or isinstance(value, bool) or value == '':
        return None
    try:
        number = float(str(value).replace('\xa0', '').replace(' ', '').replace('₽', '').replace(',', '.'))
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def ratio(numerator, denominator):
    return round(numerator / denominator, 2) if numerator is not None and denominator > 0 else None


def matrix(conn, scope):
    intervals = scope['_paid_patient_intervals'](conn)
    endpoints = scope['_management_paid_patients_by_date'](conn, intervals)
    monthly = scope['_management_paid_patients_monthly'](conn, intervals)
    money = scope['_management_doctor_paid_periods'](conn)
    money += [{'from': s['asOf'][:7] + '-01', 'to': s['asOf'], **s}
              for rows in scope['_management_doctor_paid_snapshots'](conn).values() for s in rows]
    # Same priority as the application: exact explicit periods override legacy snapshots.
    money.sort(key=lambda part: 'asOf' not in part)
    money = list({(p['from'], p['to']): p for p in money}.values())
    stored = {r['date']: json.loads(r['payload']) for r in conn.execute('SELECT date,payload FROM report_data')}
    costs_row = conn.execute('SELECT payload FROM report_blobs WHERE key=?', ('az-clinic-primary-cost-v1',)).fetchone()
    costs = json.loads(costs_row[0]) if costs_row else {}
    rows = []
    for number in range(1, 11):
        month = f'2026-{number:02d}'
        payer = monthly.get(month)
        if payer is None:
            rows.append({'month': month, 'status': 'NO_CONFIRMED_PATIENT_EVIDENCE'})
            continue
        end = payer['asOf']
        parts = scope['_cover_paid_patient_range'](money, month + '-01', end)
        dent = sum(sum(part['dentists'].values()) for part in parts) if parts else None
        clinic = sum(sum(part['clinicDocs'].values()) for part in parts) if parts else None
        record = stored.get(end)
        medicine = amount(record.get('factMedicine')) if (isinstance(record, dict)
            and (not record.get('_cashAsOf') or record['_cashAsOf'] == end)) else None
        expense = None
        if (isinstance(costs, dict) and costs.get('version', 0) >= 2
                and isinstance(costs.get('months'), list) and isinstance(costs.get('numerator'), list)
                and end == f'{month}-{calendar.monthrange(2026,number)[1]:02d}'):
            for i, label in enumerate(costs['months']):
                key = str(label).strip()
                if not re.fullmatch(r'\d{4}-\d{2}', key):
                    key = str(costs.get('year') or 2026) + '-' + str(i + 1).zfill(2)
                if (key == month and i <= int(costs.get('availableThroughMonthIndex', -1))
                        and i < len(costs['numerator'])):
                    expense = amount(costs['numerator'][i])
        rows.append({'month': month, 'from': month + '-01', 'to': end,
                     'patients': {k: payer[k] for k in ('medicine', 'dent', 'structure')},
                     'revenue': {'medicine': medicine, 'dent': dent, 'structure': clinic},
                     'average': {'medicine': ratio(medicine, payer['medicine']),
                                 'dent': ratio(dent, payer['dent']), 'structure': ratio(clinic, payer['structure'])},
                     'commonExpense': expense,
                     'expensePerMedicalPayer': ratio(expense, payer['medicine']),
                     'exactDay27Available': month + '-27' in endpoints})
    return rows


def main():
    require(sys.argv[1:] == ['--verify-live'], 'EXPECTED_VERIFY_LIVE_ARGUMENT')
    require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'az-server', 'EXPECTED_ROOT_AZ_SERVER')
    root = Path('/opt/az-baze-auth')
    db_path = Path('/var/lib/az-baze/auth.db')
    require(db_path.is_file() and not db_path.is_symlink(), 'DATABASE_PATH_MISMATCH')
    raw_config = db_path.with_name('paid-period-confirmations-v1.json').read_bytes()
    require(hashlib.sha256(raw_config).hexdigest() == CONFIG_SHA256, 'ACTIVE_CONFIG_CHANGED')
    live = (root / 'report_storage.py').read_bytes()
    require(git_blob(live) in (BASE_STORAGE, TARGET_STORAGE), 'LIVE_REPORT_STORAGE_CHANGED')
    raws = {name: (root / name).read_bytes() for name in DEPENDENCIES}
    for name, expected in DEPENDENCIES.items():
        require(git_blob(raws[name]) == expected, 'DEPENDENCY_CHANGED:' + name)
    url = ('https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/'
           + TARGET + '/az-baze-auth/report_storage.py')
    with urlopen(url, timeout=30) as response:
        raws['report_storage.py'] = response.read(2_000_001)
    require(len(raws['report_storage.py']) <= 2_000_000 and git_blob(raws['report_storage.py']) == TARGET_STORAGE,
            'TARGET_STORAGE_MISMATCH')
    scope = build_scope(raws, db_path)
    conn = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        require(conn.execute('PRAGMA query_only').fetchone()[0] == 1, 'QUERY_ONLY_REQUIRED')
        rows = matrix(conn, scope)
        require(conn.total_changes == 0, 'UNEXPECTED_CHANGES')
    finally:
        conn.close()
    print('PR165_SOURCE_AND_CONFIG=PASS; TARGET=' + TARGET)
    for row in rows:
        print('MONTH=' + json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False))
    print('PATIENT_MONTHS=' + str(sum('patients' in row for row in rows)) + '/10')
    print('READ_ONLY_DATA_CHECK=PASS; DATABASE_WRITES=NONE; LIVE_FILE_WRITES=NONE; SERVICE_ACTIONS=NONE; INSTALL=NOT_PERFORMED')

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # No payloads, names, tokens, environment or connection secrets in errors.
        print('READ_ONLY_DATA_CHECK=STOP; ERROR_TYPE=' + type(error).__name__)
        if isinstance(error, RuntimeError):
            print('GUARD=' + str(error))
        sys.exit(1)
