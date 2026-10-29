"""Read-only January payer-source diagnostic for AZ-BAZE Production."""
import hashlib, json, socket, sqlite3
from pathlib import Path

def stop(msg):
    raise RuntimeError(msg)

if socket.gethostname().split('.')[0] != 'az-server':
    stop('WRONG_HOST')

db = '/var/lib/az-baze/auth.db'
cfg = '/var/lib/az-baze/paid-period-confirmations-v1.json'

conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
conn.row_factory = sqlite3.Row
try:
    conn.execute('PRAGMA query_only=ON')
    if conn.execute('PRAGMA query_only').fetchone()[0] != 1:
        stop('QUERY_ONLY_OFF')
    conn.execute('BEGIN')

    config = json.loads(Path(cfg).read_text(encoding='utf-8'))
    jan_cfg = [x for x in config.get('confirmations', []) if x.get('reportMonth') == '2026-01']
    print('JAN_CONFIRMATIONS=' + str(len(jan_cfg)))
    for x in jan_cfg:
        print('JAN_CONFIG=' + json.dumps({
            'kind': x.get('kind'),
            'sourceFrom': x.get('sourceFrom'),
            'sourceTo': x.get('sourceTo'),
            'reportMonth': x.get('reportMonth'),
            'importedAt': x.get('importedAt'),
        }, ensure_ascii=False, sort_keys=True))

    found = []
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_payment_periods'").fetchone():
        for r in conn.execute("""
            SELECT period_start AS source_from, period_end AS source_to,
                   source_sha256, imported_at, payload_json
            FROM service_payment_periods
            WHERE substr(period_start,1,7)='2026-01'
               OR substr(period_end,1,7)='2026-01'
            ORDER BY period_start, period_end, imported_at
        """):
            p = json.loads(r['payload_json'])
            item = {
                'kind':'period',
                'sourceFrom':r['source_from'],
                'sourceTo':r['source_to'],
                'sourceSha256':str(r['source_sha256'] or ''),
                'payloadSha256':hashlib.sha256(r['payload_json'].encode('utf-8')).hexdigest(),
                'importedAt':str(r['imported_at'] or ''),
            }
            found.append(item)
            print('JAN_PERIOD_SOURCE=' + json.dumps({
                'from':item['sourceFrom'],
                'to':item['sourceTo'],
                'importedAt':item['importedAt'],
                'patientEvidenceComplete':p.get('paid_patient_evidence_complete') is True,
                'providerCount':len(p.get('providers', {})) if isinstance(p.get('providers'), dict) else None,
            }, ensure_ascii=False, sort_keys=True))

    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_payment_snapshots'").fetchone():
        for r in conn.execute("""
            SELECT month || '-01' AS source_from, as_of_date AS source_to,
                   source_sha256, imported_at, payload_json
            FROM service_payment_snapshots
            WHERE month='2026-01'
            ORDER BY as_of_date, imported_at
        """):
            p = json.loads(r['payload_json'])
            item = {
                'kind':'snapshot',
                'sourceFrom':r['source_from'],
                'sourceTo':r['source_to'],
                'sourceSha256':str(r['source_sha256'] or ''),
                'payloadSha256':hashlib.sha256(r['payload_json'].encode('utf-8')).hexdigest(),
                'importedAt':str(r['imported_at'] or ''),
            }
            found.append(item)
            print('JAN_SNAPSHOT_SOURCE=' + json.dumps({
                'from':item['sourceFrom'],
                'to':item['sourceTo'],
                'importedAt':item['importedAt'],
                'patientEvidenceComplete':p.get('paid_patient_evidence_complete') is True,
                'providerCount':len(p.get('providers', {})) if isinstance(p.get('providers'), dict) else None,
            }, ensure_ascii=False, sort_keys=True))

    for cfg_row in jan_cfg:
        matches = [
            s for s in found
            if all(s.get(k) == cfg_row.get(k) for k in (
                'kind','sourceFrom','sourceTo','sourceSha256','payloadSha256','importedAt'
            ))
        ]
        print('JAN_CONFIRMATION_IDENTITY_MATCH=' + str(len(matches)))

    print('JAN_SOURCE_COUNT=' + str(len(found)))
    print('MODE=READ_ONLY')
    print('DATABASE_WRITES=NONE')
finally:
    conn.close()
