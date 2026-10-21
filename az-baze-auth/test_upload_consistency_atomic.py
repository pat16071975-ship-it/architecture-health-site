"""Prove PR80 already commits provider classifications with the paid snapshot.

Faults happen inside actual SQLite writes, not only before store_snapshot. No
new business logic is introduced to re-fix an already corrected defect.
"""
import json
import sqlite3
import unittest
from unittest.mock import patch

import app as app_core
import paid_services_upload as uploads
import upload_history
import test_paid_services_periods as fixtures
from test_paid_services_periods import report_bytes


class AtomicClassificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PaidPeriodUploadTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.conn = self.fixture.conn

    def args(self, monthly):
        if monthly:
            return dict(period_start='2026-07-01',period_end='2026-07-31')
        return dict(period_start='2026-01-01',period_end='2026-06-30')

    def raw(self, monthly, provider='Unknown U.'):
        return report_bytes(('05.07.2026','31.07.2026') if monthly else ('05.01.2026','27.06.2026'),provider=provider)

    def configure_failure(self, table, event):
        self.conn.execute(f"CREATE TRIGGER stop_storage AFTER {event} ON {table} "
                          "BEGIN SELECT RAISE(FAIL,'synthetic storage fault'); END")
        self.conn.commit()

    def test_real_after_insert_failure_rolls_back_classification_and_snapshot(self):
        for monthly, table in [(False,'service_payment_periods'),(True,'service_payment_snapshots')]:
            with self.subTest(monthly=monthly):
                self.configure_failure(table,'INSERT')
                before=list(self.conn.iterdump())
                with self.assertRaisesRegex(sqlite3.IntegrityError,'synthetic storage fault'):
                    self.fixture.post('commit',self.raw(monthly),expected_revision='new',
                        provider_decisions=json.dumps({'Unknown U.':'dent'}),**self.args(monthly))
                self.assertEqual(before,list(self.conn.iterdump()))
                self.assertFalse(self.conn.in_transaction)
                self.assertNotIn('Unknown U.',uploads.ident_import.DENTISTS)
                self.assertNotIn('Unknown U.',uploads.ident_import.STRUCTURE_DOCTORS)
                self.conn.execute('DROP TRIGGER stop_storage');self.conn.commit()

    def test_real_replacement_fault_preserves_previous_snapshot_and_history(self):
        for monthly,table in [(False,'service_payment_periods'),(True,'service_payment_snapshots')]:
            with self.subTest(monthly=monthly):
                first=self.fixture.post('commit',self.raw(monthly,'Dent D.'),**self.args(monthly))
                self.assertEqual(first.status_code,200)
                raw=self.raw(monthly)
                preview=self.fixture.post('preview',raw,**self.args(monthly)).get_json()
                self.configure_failure(table,'UPDATE')
                before=list(self.conn.iterdump())
                with self.assertRaisesRegex(sqlite3.IntegrityError,'synthetic storage fault'):
                    self.fixture.post('commit',raw,decision='use_new',expected_revision=preview['revision'],
                        provider_decisions=json.dumps({'Unknown U.':'dent'}),**self.args(monthly))
                self.assertEqual(before,list(self.conn.iterdump()))
                self.assertFalse(self.conn.in_transaction)
                self.conn.execute('DROP TRIGGER stop_storage');self.conn.commit()

    def test_existing_pending_classification_is_restored_on_sql_failure(self):
        self.conn.execute("INSERT INTO provider_registry VALUES(?,?,?,NULL,?,?,NULL,NULL)",
                          ('Unknown U.','Unknown U.','pending','before','before'))
        self.conn.commit()
        self.configure_failure('service_payment_periods','INSERT')
        before=list(self.conn.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            self.fixture.post('commit',self.raw(False),expected_revision='new',
                provider_decisions=json.dumps({'Unknown U.':'structure'}))
        self.assertEqual(before,list(self.conn.iterdump()))
        self.assertEqual(self.conn.execute("SELECT direction FROM provider_registry WHERE source_name='Unknown U.'").fetchone()[0],'pending')

    def test_successful_native_event_is_displayed_without_exposing_audit_details(self):
        self.conn.executescript('''
          CREATE TABLE users(id INTEGER PRIMARY KEY,full_name TEXT);
          INSERT INTO users VALUES(7,'Synthetic operator');
          CREATE TABLE audit_log(id INTEGER PRIMARY KEY,actor_user_id INTEGER,action TEXT,
              target_user_id INTEGER,details TEXT,created_at TEXT);
        ''')
        with patch.object(uploads,'audit',app_core.audit),patch.object(app_core,'db',return_value=self.conn):
            response=self.fixture.post('commit',self.raw(False),expected_revision='new',
                 provider_decisions=json.dumps({'Unknown U.':'dent'}))
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM service_payment_periods').fetchone()[0],1)
        self.assertEqual(self.conn.execute("SELECT direction FROM provider_registry WHERE source_name='Unknown U.'").fetchone()[0],'dent')
        rows=upload_history.recent_uploads(self.conn)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['status'],'imported')
        self.assertEqual(rows[0]['services_filename'],'period.xlsx')
        self.assertEqual(rows[0]['data_date'],'2026-01-01 — 2026-06-30')
        self.assertEqual(rows[0]['user_name'],'Synthetic operator')
        self.assertNotIn('source_sha256',json.dumps(rows))
        self.assertNotIn('Unknown U.',json.dumps(rows))
        self.assertEqual(self.fixture.protected_state(),self.fixture.protected)

    def test_error_creates_neither_successful_audit_event_nor_journal_entry(self):
        self.conn.executescript('''
          CREATE TABLE users(id INTEGER PRIMARY KEY,full_name TEXT);
          CREATE TABLE audit_log(id INTEGER PRIMARY KEY,actor_user_id INTEGER,action TEXT,
              target_user_id INTEGER,details TEXT,created_at TEXT);
        ''')
        self.configure_failure('service_payment_periods','INSERT')
        with patch.object(uploads,'audit',app_core.audit),patch.object(app_core,'db',return_value=self.conn):
            with self.assertRaises(sqlite3.IntegrityError):
                self.fixture.post('commit',self.raw(False),expected_revision='new',
                    provider_decisions=json.dumps({'Unknown U.':'dent'}))
        self.assertEqual(upload_history.recent_uploads(self.conn),[])


if __name__=='__main__':unittest.main(verbosity=2)
