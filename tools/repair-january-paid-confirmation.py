"""Guarded repair of the stale January paid-period confirmation only.

Changes one private JSON config entry. Never writes SQLite, never restarts services.
"""
from __future__ import annotations
import hashlib, json, os, shutil, socket, sqlite3, stat, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

DB=Path('/var/lib/az-baze/auth.db')
CFG=Path('/var/lib/az-baze/paid-period-confirmations-v1.json')
BACKUPS=Path('/var/lib/az-baze/backups')
EXPECTED_CFG_SHA='8fe1f6e18ffa04c081d67b5011fe1ec20fc12f0dec678a25b61bb7a634679b69'
OLD_IMPORT='2026-10-28T15:28:19.184976+00:00'
NEW_IMPORT='2026-10-29T07:17:46.091831+00:00'
MONTH='2026-01'
FROM='2026-01-05'
TO='2026-01-31'
TOKEN='JAN-CONFIRM-CURRENT-20261029'

def die(msg):
    print('JAN_CONFIRMATION_REPAIR=STOP; '+msg)
    raise SystemExit(1)

def sha256(raw):
    return hashlib.sha256(raw).hexdigest()

def load_cfg():
    raw=CFG.read_bytes()
    if sha256(raw)!=EXPECTED_CFG_SHA:
        die('ACTIVE_CONFIG_SHA_MISMATCH')
    try: data=json.loads(raw)
    except Exception: die('ACTIVE_CONFIG_JSON_INVALID')
    if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('confirmations'),list):
        die('ACTIVE_CONFIG_CONTRACT_INVALID')
    return raw,data

def current_source(conn):
    rows=conn.execute("""
        SELECT period_start,period_end,source_sha256,imported_at,payload_json
        FROM service_payment_periods
        WHERE period_start=? AND period_end=?
        ORDER BY imported_at
    """,(FROM,TO)).fetchall()
    matches=[]
    for row in rows:
        raw=row['payload_json']
        try: p=json.loads(raw)
        except Exception: continue
        if (p.get('version')==3 and p.get('period_start')==FROM and p.get('period_end')==TO
            and p.get('paid_patient_evidence_complete') is True
            and isinstance(p.get('providers'),dict) and isinstance(p.get('paid_patients'),dict)
            and str(row['imported_at'] or '')==NEW_IMPORT):
            matches.append({
                'kind':'period','sourceFrom':FROM,'sourceTo':TO,
                'sourceSha256':str(row['source_sha256'] or ''),
                'payloadSha256':sha256(raw.encode('utf-8')),
                'importedAt':str(row['imported_at'] or ''),
                'reportMonth':MONTH,
            })
    if len(matches)!=1:
        die('CURRENT_JAN_SOURCE_NOT_UNIQUE')
    row=matches[0]
    if len(row['sourceSha256'])!=64 or any(c not in '0123456789abcdef' for c in row['sourceSha256']):
        die('CURRENT_SOURCE_SHA_INVALID')
    return row

def verify_old(data):
    jan=[x for x in data['confirmations'] if isinstance(x,dict) and x.get('reportMonth')==MONTH]
    if len(jan)!=1:
        die('JAN_CONFIRMATION_COUNT_NOT_ONE')
    old=jan[0]
    if (old.get('kind'),old.get('sourceFrom'),old.get('sourceTo'),old.get('importedAt')) != ('period',FROM,TO,OLD_IMPORT):
        die('JAN_OLD_IDENTITY_UNEXPECTED')
    return old

def main():
    if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='az-server':
        die('EXPECTED_ROOT_AZ_SERVER')
    if sys.argv[1:] not in (['--preflight'],['--apply',TOKEN]):
        die('USE_PREFLIGHT_OR_EXACT_APPLY_TOKEN')
    raw,data=load_cfg()
    conn=sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True,timeout=5)
    conn.row_factory=sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        if conn.execute('PRAGMA query_only').fetchone()[0]!=1: die('QUERY_ONLY_OFF')
        conn.execute('BEGIN')
        source=current_source(conn)
        if conn.total_changes!=0: die('UNEXPECTED_DATABASE_CHANGE')
    finally:
        conn.close()
    old=verify_old(data)
    changed=[dict(x) for x in data['confirmations']]
    idx=next(i for i,x in enumerate(changed) if x.get('reportMonth')==MONTH)
    changed[idx]=source
    candidate={'version':1,'confirmations':changed}
    # Preserve all non-January entries byte-for-value.
    old_other=sorted((x for x in data['confirmations'] if x.get('reportMonth')!=MONTH),key=lambda x:x.get('reportMonth',''))
    new_other=sorted((x for x in candidate['confirmations'] if x.get('reportMonth')!=MONTH),key=lambda x:x.get('reportMonth',''))
    if old_other!=new_other: die('NON_JANUARY_CONFIG_CHANGED')
    print('JAN_OLD_IMPORTED_AT='+OLD_IMPORT)
    print('JAN_NEW_IMPORTED_AT='+NEW_IMPORT)
    print('JAN_SOURCE_EXACT=PASS; PATIENT_EVIDENCE_COMPLETE=YES')
    print('OTHER_CONFIRMATIONS_UNCHANGED=PASS')
    print('DATABASE_WRITES=NONE')
    if sys.argv[1:] == ['--preflight']:
        print('JAN_CONFIRMATION_PREFLIGHT=PASS; CONFIG_WRITES=NONE')
        return
    BACKUPS.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=BACKUPS/f'paid-confirmation-before-jan-refresh-{stamp}.json'
    shutil.copy2(CFG,backup)
    if backup.read_bytes()!=raw: die('BACKUP_VERIFY_FAIL')
    st=CFG.stat()
    encoded=(json.dumps(candidate,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
    fd,tmp=tempfile.mkstemp(prefix='.paid-period-confirmations-',dir=str(CFG.parent))
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(encoded); f.flush(); os.fsync(f.fileno())
        os.chown(tmp,st.st_uid,st.st_gid); os.chmod(tmp,stat.S_IMODE(st.st_mode))
        os.replace(tmp,CFG)
        dfd=os.open(CFG.parent,os.O_DIRECTORY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    final=json.loads(CFG.read_text(encoding='utf-8'))
    jan=[x for x in final['confirmations'] if x.get('reportMonth')==MONTH]
    if jan!=[source]: die('POSTWRITE_JAN_VERIFY_FAIL')
    if sorted((x for x in final['confirmations'] if x.get('reportMonth')!=MONTH),key=lambda x:x.get('reportMonth',''))!=old_other:
        die('POSTWRITE_OTHER_CONFIG_CHANGED')
    print('BACKUP='+str(backup))
    print('JAN_CONFIRMATION_REPAIR=PASS; DATABASE_WRITES=NONE; SERVICE_RESTART=NONE')

if __name__=='__main__': main()
