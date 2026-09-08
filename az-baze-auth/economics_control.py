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
