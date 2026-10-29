import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import paid_period_confirmation as periods


class PeriodConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.executescript('''
        CREATE TABLE service_payment_periods(period_start TEXT,period_end TEXT,payload_json TEXT,source_sha256 TEXT,imported_at TEXT);
        CREATE TABLE service_payment_snapshots(month TEXT,as_of_date TEXT,payload_json TEXT,source_sha256 TEXT,imported_at TEXT);
        ''')

    def tearDown(self):
        self.conn.close()

    def source(self, start='2026-01-05', end='2026-01-31', kind='period'):
        payload = {'version': 3 if kind == 'period' else 2, 'providers': {},
                   'period_start': start, 'period_end': end, 'month': start[:7],
                   'as_of_date': end, 'paid_patient_evidence_complete': True,
                   'paid_patients': {}}
        data = (start if kind == 'period' else start[:7], end, json.dumps(payload),
                hashlib.sha256((start + end).encode()).hexdigest(), '2026-10-28T15:00:00Z')
        table = 'service_payment_periods' if kind == 'period' else 'service_payment_snapshots'
        self.conn.execute(f'INSERT INTO {table} VALUES(?,?,?,?,?)', data)
        return next(s for s in periods.read_sources(self.conn) if s['sourceFrom'] == start and s['sourceTo'] == end)

    def test_unconfirmed_service_bounds_are_never_rounded(self):
        self.source()
        row = periods.read_sources(self.conn)[0]
        self.assertEqual((row['from'], row['to']), ('2026-01-05', '2026-01-31'))
        self.assertIsNone(row['confirmedMonth'])

    def test_confirmed_month_is_a_read_only_projection(self):
        source = self.source()
        entry = periods.make_confirmation(source, '2026-01')
        before = '\n'.join(self.conn.iterdump())
        self.conn.execute('PRAGMA query_only=ON')
        row = periods.read_sources(self.conn, [entry])[0]
        self.assertEqual((row['from'], row['to']), ('2026-01-01', '2026-01-31'))
        self.assertEqual(row['sourceFrom'], '2026-01-05')
        self.assertEqual(before, '\n'.join(self.conn.iterdump()))

    def test_february_march_and_short_service_end_may(self):
        for start, end in [('2026-02-02','2026-02-28'),('2026-03-02','2026-03-31'),('2026-05-01','2026-05-30')]:
            with self.subTest(start=start):
                source = self.source(start, end, 'snapshot' if start.endswith('-01') else 'period')
                entry = periods.make_confirmation(source, start[:7])
                row = next(s for s in periods.read_sources(self.conn, [entry]) if s['sourceFrom'] == start)
                self.assertEqual((row['from'], row['to']), periods.month_bounds(start[:7]))

    def test_october_increment_is_not_a_month(self):
        self.source('2026-10-12','2026-10-13')
        row = periods.read_sources(self.conn)[0]
        self.assertEqual((row['from'],row['to']),('2026-10-12','2026-10-13'))

    def test_source_replacement_invalidates_confirmation(self):
        entry = periods.make_confirmation(self.source(), '2026-01')
        self.conn.execute("UPDATE service_payment_periods SET source_sha256=?", ('f'*64,))
        self.assertIsNone(periods.read_sources(self.conn,[entry])[0]['confirmedMonth'])

    def test_payload_change_invalidates_confirmation(self):
        source = self.source()
        entry = periods.make_confirmation(source,'2026-01')
        changed = {**source['payload'],'paid_patients':{'Dent':['synthetic-token']}}
        self.conn.execute('UPDATE service_payment_periods SET payload_json=?',(json.dumps(changed),))
        self.assertIsNone(periods.read_sources(self.conn,[entry])[0]['confirmedMonth'])

    def test_reimport_stamp_change_invalidates_confirmation(self):
        entry = periods.make_confirmation(self.source(),'2026-01')
        self.conn.execute("UPDATE service_payment_periods SET imported_at='later'")
        self.assertIsNone(periods.read_sources(self.conn,[entry])[0]['confirmedMonth'])

    def test_confirmation_does_not_apply_to_other_month_or_source(self):
        entry = periods.make_confirmation(self.source(),'2026-01')
        self.source('2026-02-02','2026-02-28')
        rows=periods.read_sources(self.conn,[entry])
        self.assertIsNone(next(s for s in rows if s['from'].startswith('2026-02'))['confirmedMonth'])

    def test_long_period_cannot_be_certified_as_one_month(self):
        source=self.source('2026-01-05','2026-06-30')
        with self.assertRaises(ValueError): periods.make_confirmation(source,'2026-01')

    def test_duplicate_month_is_rejected(self):
        entry=periods.make_confirmation(self.source(),'2026-01')
        with self.assertRaises(ValueError):
            periods.validate_config({'version':1,'confirmations':[entry,entry]})

    def test_invalid_fingerprint_is_rejected(self):
        entry=periods.make_confirmation(self.source(),'2026-01')
        for key in ['sourceSha256','payloadSha256']:
            with self.subTest(key=key),self.assertRaises(ValueError):
                periods.validate_config({'version':1,'confirmations':[{**entry,key:'not-a-hash'}]})

    def test_native_exact_period_outranks_historical_alias(self):
        entry=periods.make_confirmation(self.source(),'2026-01')
        self.source('2026-01-01','2026-01-31','snapshot')
        rows=periods.read_sources(self.conn,[entry])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['kind'],'snapshot')
        self.assertIsNone(rows[0]['confirmedMonth'])

    def test_empty_database_is_read_only(self):
        with sqlite3.connect(':memory:') as conn:
            conn.execute('PRAGMA query_only=ON')
            self.assertEqual(periods.read_sources(conn),[])
            self.assertEqual(conn.execute('SELECT count(*) FROM sqlite_master').fetchone()[0],0)

    def test_corrupt_payload_and_invalid_calendar_date_are_ignored(self):
        self.source()
        self.conn.execute("UPDATE service_payment_periods SET payload_json='broken'")
        self.assertEqual(periods.read_sources(self.conn),[])
        self.conn.execute('DELETE FROM service_payment_periods')
        payload={'version':3,'providers':{},'period_start':'2026-02-30','period_end':'2026-02-31'}
        self.conn.execute('INSERT INTO service_payment_periods VALUES(?,?,?,?,?)',
                          ('2026-02-30','2026-02-31',json.dumps(payload),'a'*64,'stamp'))
        self.assertEqual(periods.read_sources(self.conn),[])

    def test_missing_config_creates_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            self.assertEqual(periods.load_config(path/'auth.db'),[])
            self.assertEqual(list(path.iterdir()),[])

    def test_private_config_roundtrip_and_validation(self):
        entry=periods.make_confirmation(self.source(),'2026-01')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/periods.BASENAME
            path.write_text(json.dumps({'version':1,'confirmations':[entry]}))
            self.assertEqual(periods.load_config(Path(directory)/'auth.db'),[entry])
            path.write_text('{"version":999,"confirmations":[]}')
            with self.assertRaises(ValueError): periods.load_config(Path(directory)/'auth.db')

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


if __name__ == '__main__': unittest.main()
