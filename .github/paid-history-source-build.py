"""Temporary source-only build; no production host, data, or deployment access."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

EXPECTED = 'd0980550d6d7868b1eabc5853cfafb8bdad82b328df2f52a2ef4f2e9a74a720e'
ALLOWED = {
    'az-baze-auth/paid_period_confirmation.py',
    'az-baze-auth/test_paid_period_confirmation.py',
    'az-baze-auth/test_paid_history_reporting.py',
    'az-baze-auth/PAID-HISTORY-REPORTING.md',
    'tests/verify-paid-history.cjs',
    'tools/prepare-paid-history.py',
    'tools/paid-history-confirmation-plan.py',
    '.github/workflows/az-paid-history.yml',
}
root = Path(__file__).resolve().parents[1]
encoded = ''.join((root / f'.github/paid-history-pack-{i}.b64').read_text().strip() for i in range(1, 5))
raw = gzip.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(raw).hexdigest() != EXPECTED:
    raise RuntimeError('SOURCE_BUNDLE_SHA256_MISMATCH')
files = json.loads(raw)
if set(files) != ALLOWED or not all(isinstance(value, str) for value in files.values()):
    raise RuntimeError('SOURCE_BUNDLE_PATHS_MISMATCH')
for relative in sorted(files):
    target = root / relative
    if target.exists():
        raise RuntimeError('EXPECTED_NEW_SOURCE_PATH: ' + relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(files[relative], encoding='utf-8')
subprocess.run(['python', 'tools/prepare-paid-history.py'], cwd=root, check=True)
print('REVIEWED_SOURCE_BUNDLE=PASS; LIVE_ACCESS=NONE')
