from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from receiver import Identity, Rejected, ingest, init_store, query_sha256, validate, ROOT

SOURCE = '11111111-1111-4111-8111-111111111111'
STREAM = '22222222-2222-4222-8222-222222222222'
OTHER = '33333333-3333-4333-8333-333333333333'
TZ = 'Asia/Novosibirsk'
IDENTITY = Identity(SOURCE, STREAM, TZ)


def row(d, count=2, people=1, pos='120.0500000000', neg='-10.0500000000', net='110.0000000000'):
    return dict(date=d, records_count=count, paying_patients=people, positive_amount=pos, negative_amount=neg, net_amount=net)


def zero(d):
    return row(d, 0, 0, '0', '0', '0')


def payload(seq=1, start='2026-01-01', rows=None):
    rows = rows if rows is not None else [row(start), zero((date.fromisoformat(start)+timedelta(days=1)).isoformat())]
    return dict(contract_version=1, dataset='payments_daily_v1', scope='whole_source_database',
                source_id=SOURCE, stream_id=STREAM, batch_id=str(UUID(int=seq+100)), sequence=seq,
                source_timezone=TZ, date_from=start,
                date_to_exclusive=(date.fromisoformat(start)+timedelta(days=len(rows))).isoformat(),
                query_started_at='2026-02-01T01:00:00.000001Z', observed_at='2026-02-01T01:00:01.000001Z',
                coverage='full_query_window_as_observed', query_sha256=query_sha256(),
                schema={'payment_precision': 28, 'payment_scale': 10}, rows=rows)


def encoded(p):
    return json.dumps(p, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def query(db, sql):
    with sqlite3.connect(db) as conn:
        return conn.execute(sql).fetchall()


@pytest.fixture
def store(tmp_path):
    db = tmp_path/'reference.sqlite3'
    assert init_store(db, IDENTITY) == 'STORE_READY'
    return db


def test_first_batch_full_window_and_exact_bytes(store):
    raw = encoded(payload())
    assert ingest(store, raw) == 'ACCEPTED'
    assert query(store, 'select date,net_amount,sequence from daily order by date') == [('2026-01-01','110.0000000000',1),('2026-01-02','0',1)]
    assert query(store, 'select payload,payload_sha256 from batches') == [(raw,sha256(raw).hexdigest())]


def test_retry_is_idempotent(store):
    raw=encoded(payload())
    assert ingest(store, raw) == 'ACCEPTED'
    assert ingest(store, raw) == 'ALREADY_ACCEPTED'
    assert query(store,'select count(*) from batches') == [(1,)]


def test_correction_replaces_not_adds(store):
    ingest(store,encoded(payload()))
    new=row('2026-01-01',1,1,'15.25','0','15.25')
    assert ingest(store,encoded(payload(2,rows=[new])))=='ACCEPTED'
    assert query(store,'select date,net_amount,sequence from daily order by date')==[('2026-01-01','15.25',2),('2026-01-02','0',1)]
    assert query(store,'select count(*) from batches')==[(2,)]


def test_deletion_and_zero_replacement(store):
    ingest(store,encoded(payload()))
    ingest(store,encoded(payload(2,rows=[zero('2026-01-01')])))
    assert query(store,'select records_count,net_amount from daily where date="2026-01-01"')==[(0,'0')]


def test_retry_old_after_new_does_not_roll_back(store):
    raw=encoded(payload())
    ingest(store,raw)
    ingest(store,encoded(payload(2,rows=[zero('2026-01-01')])))
    assert ingest(store,raw)=='ALREADY_ACCEPTED'
    assert query(store,'select sequence from daily where date="2026-01-01"')==[(2,)]


def test_stale_overlap_rejects_entire_batch(store):
    ingest(store,encoded(payload(4,'2026-01-02',rows=[zero('2026-01-02')])))
    with pytest.raises(Rejected,match='STALE_OVERLAPPING_WINDOW'):
        ingest(store,encoded(payload(3)))
    assert query(store,'select date,sequence from daily')==[('2026-01-02',4)]
    assert query(store,'select sequence from batches')==[(4,)]


def test_disjoint_older_window_allowed(store):
    ingest(store,encoded(payload(4,'2026-01-03')))
    assert ingest(store,encoded(payload(3)))=='ACCEPTED'
    assert query(store,'select count(*) from daily')==[(4,)]


def test_sequence_collision_even_if_json_only_reformatted(store):
    p=payload();ingest(store,encoded(p))
    with pytest.raises(Rejected,match='SEQUENCE_CONTENT_CONFLICT'):
        ingest(store,json.dumps(p,indent=2).encode())


def test_batch_id_collision(store):
    p=payload();ingest(store,encoded(p));q=payload(2);q['batch_id']=p['batch_id']
    with pytest.raises(Rejected,match='BATCH_ID_CONFLICT'):
        ingest(store,encoded(q))


def test_wrong_source_or_stream(store):
    for key in ['source_id','stream_id']:
        p=payload();p[key]=OTHER
        with pytest.raises(Rejected,match='SOURCE_OR_STREAM_MISMATCH'):
            ingest(store,encoded(p))
    assert query(store,'select count(*) from batches')==[(0,)]


def test_store_never_rebinds(store):
    with pytest.raises(Rejected,match='STORE_ALREADY_BOUND'):
        init_store(store,Identity(OTHER,STREAM,TZ))
    assert init_store(store,IDENTITY)=='STORE_READY'


def test_unrelated_database_never_modified(tmp_path):
    db=tmp_path/'other.sqlite3'
    with sqlite3.connect(db) as c:c.execute('create table real_report(amount text)')
    before=db.read_bytes()
    with pytest.raises(Rejected,match='NOT_IDENT004_REFERENCE_STORE'):init_store(db,IDENTITY)
    with pytest.raises(Rejected,match='NOT_IDENT004_REFERENCE_STORE'):ingest(db,encoded(payload()))
    assert before==db.read_bytes()


def test_ingest_never_creates_db(tmp_path):
    db=tmp_path/'nonexistent.sqlite3'
    with pytest.raises(sqlite3.Error):ingest(db,encoded(payload()))
    assert not db.exists()


def test_sql_scope_and_read_only_profile():
    for f in (ROOT/'sql').glob('*.sql'):
        text=f.read_text().upper().strip()
        assert all(part.strip().startswith('SELECT') for part in text.split(';') if part.strip())
        assert text.count(';') == (3 if f.name == 'preflight_readonly.sql' else 1)
        assert not any(x in text for x in ['NOLOCK','SELECT *','INSERT ','UPDATE ','DELETE ','DROP ','ALTER ','EXEC '])
    script=(ROOT/'collector.ps1').read_text()
    assert '$builder.Encrypt = $true' in script and '$builder.TrustServerCertificate = $false' in script
    assert 'Read-Host' in script and 'AsSecureString' in script


@pytest.mark.parametrize('mutate,reason',[
    (lambda p:p['rows'].pop(),'INCOMPLETE_DATE_COVERAGE'),
    (lambda p:p['rows'].__setitem__(1,deepcopy(p['rows'][0])),'UNORDERED_OR_DUPLICATE_DAY'),
    (lambda p:p['rows'][0].update(net_amount='999'),'MONEY_BALANCE_MISMATCH'),
    (lambda p:p['rows'][0].update(positive_amount=120.05),'INVALID_DECIMAL_STRING'),
    (lambda p:p['rows'][0].update(positive_amount='120,05'),'INVALID_DECIMAL_STRING'),
    (lambda p:p['rows'][0].update(positive_amount='1e2'),'INVALID_DECIMAL_STRING'),
    (lambda p:p['rows'][0].update(paying_patients=3),'INVALID_PATIENT_COUNT'),
    (lambda p:p['rows'][0].update(records_count=True),'INVALID_INTEGER'),
    (lambda p:p['rows'][0].update(paying_patients=0),'PATIENT_PAYMENT_MISMATCH'),
    (lambda p:p['rows'][0].update(patient_name='Example'),'INVALID_ROW_FIELDS'),
    (lambda p:p.update(password='example'),'INVALID_ENVELOPE_FIELDS'),
    (lambda p:p.update(query_sha256='0'*64),'UNKNOWN_QUERY_FINGERPRINT'),
    (lambda p:p.update(source_timezone='UTC'),'SOURCE_TIMEZONE_MISMATCH'),
    (lambda p:p.update(contract_version=True),'UNKNOWN_CONTRACT'),
    (lambda p:p.update(coverage='partial'),'UNSUPPORTED_COVERAGE'),
    (lambda p:p.update(date_to_exclusive='2026-01-01'),'INVALID_DATE_WINDOW'),
    (lambda p:p.update(date_to_exclusive='2026-03-01'),'INVALID_DATE_WINDOW'),
    (lambda p:p.update(observed_at='2026-01-01T00:00:00Z'),'INVALID_OBSERVATION_ORDER'),
    (lambda p:p.update(query_started_at='2026-01-01T00:00:00Z',observed_at='2026-01-01T00:00:00Z'),'FUTURE_DATE_WINDOW'),
    (lambda p:p['schema'].update(payment_scale=2),'DECIMAL_PRECISION_OR_SCALE_OVERFLOW'),
])
def test_invalid_payload_stops_without_partial_write(store,mutate,reason):
    p=payload();mutate(p)
    with pytest.raises(Rejected,match=reason):ingest(store,encoded(p))
    assert query(store,'select count(*) from daily')==[(0,)]
    assert query(store,'select count(*) from batches')==[(0,)]


def test_schema_change_after_first_success_rejected(store):
    ingest(store,encoded(payload()))
    p=payload(2);p['schema']['payment_precision']=29
    with pytest.raises(Rejected,match='SOURCE_SCHEMA_CHANGED'):ingest(store,encoded(p))
    assert query(store,'select count(*) from batches')==[(1,)]


def test_duplicate_json_key_rejected(store):
    raw=encoded(payload()).replace(b'{"contract_version":1,',b'{"contract_version":1,"contract_version":1,',1)
    with pytest.raises(Rejected,match='DUPLICATE_JSON_KEY'):ingest(store,raw)


def test_large_decimals_exact_and_negative_only(store):
    p=payload(rows=[row('2026-01-01',2,1,'9999999999999999999999999999.1234567890','-9999999999999999999999999999.1234567889','0.0000000001'),row('2026-01-02',1,0,'0','-12.34','-12.34')])
    assert ingest(store,encoded(p))=='ACCEPTED'
    assert query(store,'select net_amount from daily order by date')==[('0.0000000001',),('-12.34',)]


def test_atomic_rollback_if_storage_fails(store):
    ingest(store,encoded(payload()))
    # Inject a failure after first day update and before committing second day.
    with sqlite3.connect(store) as c:
        c.execute("create trigger test_fail before insert on daily when NEW.date='2026-01-02' begin select raise(abort,'test'); end")
    q=payload(2,rows=[zero('2026-01-01'),row('2026-01-02')])
    with pytest.raises(sqlite3.Error):ingest(store,encoded(q))
    assert query(store,'select date,net_amount,sequence from daily order by date')==[('2026-01-01','110.0000000000',1),('2026-01-02','0',1)]
    assert query(store,'select count(*) from batches')==[(1,)]
