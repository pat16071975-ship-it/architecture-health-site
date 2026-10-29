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

def replace(path, old, new):
    target=root/path
    text=target.read_text()
    if text.count(old)!=1:
        raise RuntimeError('CORRECTION_ANCHOR_MISMATCH: '+path)
    target.write_text(text.replace(old,new,1))

# Preserve the original read contract for legacy/minimal source tables. An
# absent fingerprint never qualifies an upload for monthly confirmation.
replace('az-baze-auth/paid_period_confirmation.py', '''        rows = conn.execute(
            f'SELECT {start_column}, {end_column}, payload_json, source_sha256, imported_at '
            f'FROM "{table}" ORDER BY 1,2'
        )''', '''        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
        hash_column = 'source_sha256' if 'source_sha256' in columns else "''"
        stamp_column = 'imported_at' if 'imported_at' in columns else "''"
        rows = conn.execute(
            f'SELECT {start_column}, {end_column}, payload_json, {hash_column}, {stamp_column} '
            f'FROM "{table}" ORDER BY 1,2'
        )''')
replace('az-baze-auth/paid_period_confirmation.py', '                payload = json.loads(raw)\n', '                if not isinstance(raw, str):\n                    continue\n                payload = json.loads(raw)\n')
replace('az-baze-auth/test_paid_period_confirmation.py', "\n\nif __name__ == '__main__': unittest.main()", '''
    def test_legacy_metadata_absence_keeps_dates_but_cannot_confirm_month(self):
        conn=sqlite3.connect(':memory:')
        try:
            conn.execute('CREATE TABLE service_payment_snapshots(month TEXT,as_of_date TEXT,payload_json TEXT)')
            payload={'version':2,'providers':{},'month':'2026-05','as_of_date':'2026-05-30'}
            conn.execute('INSERT INTO service_payment_snapshots VALUES(?,?,?)',('2026-05','2026-05-30',json.dumps(payload)))
            conn.commit();conn.execute('PRAGMA query_only=ON')
            rows=periods.read_sources(conn)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['to'],'2026-05-30')
            self.assertIsNone(rows[0]['confirmedMonth'])
            with self.assertRaises(ValueError): periods.make_confirmation(rows[0],'2026-05')
        finally:
            conn.close()


if __name__ == '__main__': unittest.main()''')
# Missing clinic-money evidence must not become a fabricated zero average.
replace('reports/period-view.js', '    return {month,target,r,d:r?derive(r):null,paidParts,monthly:true,payers};', '''    const d=r?derive(r):null;
    if(d&&rawOrNull(r.factMedicine)===null)d.avgCheck=null;
    return {month,target,r,d,paidParts,monthly:true,payers};''')
replace('tests/verify-paid-history.cjs', 'console.log(`PAID_HISTORY_JS=PASS; SCENARIOS=${passed}`);', '''test('absent medicine revenue is unknown rather than a zero average',()=>{
 const h=harness();const day=h.add(1,31);delete h.store[day].factMedicine;h.render();
 assert.equal(h.values('paidPatients')[0],2);assert.equal(h.values('avgCheck')[0],null);
});
console.log(`PAID_HISTORY_JS=PASS; SCENARIOS=${passed}`);''')
replace('.github/workflows/az-paid-history.yml','/tmp/az-paid-history-ci/auth.db','/tmp/az-paid-history-ci.db')
# Existing advertising startup tests use this isolated file, not the patched
# in-memory reporting connection. The source application's path is unchanged.
Path('/tmp/az-paid-history-build').mkdir(exist_ok=True)
print('REVIEWED_SOURCE_BUNDLE=PASS; LIVE_ACCESS=NONE')
