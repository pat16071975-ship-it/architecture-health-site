import base64
import hashlib
import hmac
import json
import os
import zlib
from pathlib import Path

from flask import Response, jsonify

from app import SITE_ROOT, csrf_token, permission_required

DATA_PATH = Path(os.environ.get('AZ_ECON_DATA_PATH', '/var/lib/az-baze/economics-control.enc'))
KEY_PATH = Path(os.environ.get('AZ_ECON_KEY_PATH', '/var/lib/az-baze/economics-control.key'))


def _xor_stream(data, key, nonce):
    out = bytearray(len(data))
    for block_index in range((len(data) + 31) // 32):
        stream = hmac.new(key, nonce + block_index.to_bytes(8, 'big'), hashlib.sha256).digest()
        start = block_index * 32
        for index, value in enumerate(data[start:start + 32]):
            out[start + index] = value ^ stream[index]
    return bytes(out)


def _close(left, right, tolerance=1.0):
    return abs(float(left or 0) - float(right or 0)) <= tolerance


def _self_checks(data):
    payroll = data.get('payroll') or {}
    opu = data.get('opu') or {}
    employees = payroll.get('employees') or []
    payments = payroll.get('payments') or []
    groups = payroll.get('groups') or {}
    period = data.get('period') or []
    total = float(payroll.get('total') or 0)

    checks = {}
    checks['payroll_employee_total'] = _close(sum(float(x.get('total') or 0) for x in employees), total)
    checks['payroll_group_total'] = _close(sum(float(x.get('total') or 0) for x in groups.values()), total)
    checks['payroll_payment_total'] = _close(sum(float(x.get('amount') or 0) for x in payments), total)
    checks['payment_ids_unique'] = len({x.get('payment_id') for x in payments}) == len(payments)
    checks['employee_ids_present'] = all(x.get('employee_id') for x in payments)

    monthly_ok = True
    for month in period:
        employee_month = sum(float((x.get('months') or {}).get(month) or 0) for x in employees)
        expected = float((payroll.get('total_by_month') or {}).get(month) or 0)
        monthly_ok = monthly_ok and _close(employee_month, expected)
    checks['payroll_months_reconcile'] = monthly_ok

    expense_lines = opu.get('expense_lines') or []
    normalized_expenses = sum(float(x.get('total') or 0) for x in expense_lines)
    source_expenses = sum(float(x or 0) for x in (opu.get('direct_expenses') or {}).values()) + sum(
        float(x or 0) for x in (opu.get('indirect_expenses') or {}).values()
    )
    checks['opu_expenses_reconcile'] = _close(normalized_expenses, source_expenses)
    checks['assistants_not_materials'] = sum(float(x or 0) for x in (opu.get('assistant_salary') or {}).values()) > 0 and sum(
        float(x or 0) for x in (opu.get('materials') or {}).values()
    ) > 0

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise ValueError('economics control failed: ' + ', '.join(failed))
    return {
        'status': 'OK',
        'checks': checks,
        'uniqueEmployees': len({x.get('employee_id') for x in payments if x.get('employee_id')}),
        'payments': len(payments),
    }


def _load_data():
    if not DATA_PATH.exists() or not KEY_PATH.exists():
        return {'version': 1, 'available': False, 'source': '', 'period': []}
    master = base64.urlsafe_b64decode(KEY_PATH.read_text(encoding='utf-8').strip())
    text = DATA_PATH.read_text(encoding='utf-8').strip()
    if not text.startswith('AZEC1.'):
        raise ValueError('invalid economics data format')
    packed = base64.urlsafe_b64decode(text.split('.', 1)[1])
    if len(packed) < 48:
        raise ValueError('invalid economics data')
    nonce, cipher, tag = packed[:16], packed[16:-32], packed[-32:]
    enc_key = hmac.new(master, b'az-econ-enc-v1', hashlib.sha256).digest()
    mac_key = hmac.new(master, b'az-econ-mac-v1', hashlib.sha256).digest()
    expected = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError('economics data authentication failed')
    plain = zlib.decompress(_xor_stream(cipher, enc_key, nonce))
    data = json.loads(plain.decode('utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('invalid economics payload')
    data['control'] = _self_checks(data)
    data['available'] = True
    return data


def register_economics_control(app):
    @app.get('/reports/dashboard.html')
    @permission_required('reports')
    def economics_dashboard_page():
        path = SITE_ROOT / 'reports' / 'dashboard.html'
        return Response(path.read_text(encoding='utf-8'), mimetype='text/html')

    @app.get('/api/reports/economics-control')
    @permission_required('reports')
    def economics_control_data():
        try:
            data = _load_data()
            error = ''
        except Exception as exc:
            data = {'version': 1, 'available': False, 'source': '', 'period': []}
            error = str(exc)
        return jsonify(data=data, error=error, csrf=csrf_token())
