"""Regression tests of the existing journal. Synthetic data only."""
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from flask import Flask, render_template_string
import upload_history

ROOT = Path(__file__).resolve().parent


class UploadHistoryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
          CREATE TABLE users(id INTEGER PRIMARY KEY,full_name TEXT,password_hash TEXT);
          INSERT INTO users VALUES(7,'Test actor','SECRET_HASH');
          CREATE TABLE upload_log(id INTEGER PRIMARY KEY,data_date TEXT,status TEXT,details TEXT,
            completed_filename TEXT,services_filename TEXT,uploaded_by INTEGER,created_at TEXT);
          CREATE TABLE audit_log(id INTEGER PRIMARY KEY,actor_user_id INTEGER,action TEXT,
            target_user_id INTEGER,details TEXT,created_at TEXT);
        ''')

    def event(self, action, details, when='2026-10-21T09:00:00+00:00', actor=7):
        self.conn.execute('INSERT INTO audit_log(actor_user_id,action,details,created_at) VALUES(?,?,?,?)',
                          (actor,action,details,when))
        self.conn.commit()

    def legacy(self):
        self.conn.execute("INSERT INTO upload_log VALUES(20,'2026-09-01','imported','legacy details',"
                          "'visits.xls','services.xlsx',7,'2026-10-20T09:00:00+00:00')")
        self.conn.commit()

    def completed(self, **kwargs):
        fields = dict(new=2, identical=1, conflict=0, removed=0, decision='append', month='2026-09', filename='visits.xls')
        fields.update(kwargs)
        return (f"file={fields['filename']}; sheet=Лист 1; month={fields['month']}; "
                f"decision={fields['decision']}; new={fields['new']}; identical={fields['identical']}; "
                f"conflict={fields['conflict']}; removed={fields['removed']}; "
                "backup=/PRIVATE/internal.sqlite; paid_source_separate=1")

    def cash(self, **kwargs):
        fields = dict(new=2, identical=1, conflict=0, removed=0, decision='append')
        fields.update(kwargs)
        return ("file=cash.xlsx; sheet=Лист; range=2026-09-02..2026-09-30; "
                f"decision={fields['decision']}; new={fields['new']}; identical={fields['identical']}; "
                f"conflict={fields['conflict']}; removed={fields['removed']}; "
                "months=2026-09; reports=1; backup=/PRIVATE/backup; rule=positive-receipts-only")

    def paid(self, *, old=False, status='imported'):
        dates = 'month=2026-09; as_of=2026-09-30; ' if old else (
            'period_start=2026-01-01; period_end=2026-06-30; source_sha256='+'a'*64+'; ')
        return 'file=paid.xlsx; '+dates+'decision=append; status='+status+'; paid=123.45; reports=0; analytics_updated=0; backup=/PRIVATE/backup'

    def read(self):
        before=list(self.conn.iterdump())
        changes=self.conn.total_changes
        self.conn.execute('PRAGMA query_only=ON')
        result=upload_history.recent_uploads(self.conn)
        self.assertEqual(before,list(self.conn.iterdump()))
        self.assertEqual(changes,self.conn.total_changes)
        self.conn.execute('PRAGMA query_only=OFF')
        return result

    def test_legacy_and_three_independent_events_in_one_existing_journal(self):
        self.legacy()
        self.event('completed_reports_reconciled',self.completed())
        self.event('cash_receipts_reconciled',self.cash())
        self.event('paid_services_stored',self.paid())
        rows=self.read()
        self.assertEqual(len(rows),4)
        self.assertEqual(rows[-1]['id'],20)
        self.assertEqual(rows[-1]['completed_filename'],'visits.xls')
        self.assertEqual({r['source_label'] for r in rows[:-1]},set(upload_history._ACTIONS.values()))
        self.assertTrue(all(r['user_name']=='Test actor' for r in rows))
        self.assertTrue(all(r['status']=='imported' for r in rows))

    def test_no_month_start_end_or_applied_days_invented_for_completed(self):
        self.event('completed_reports_reconciled',self.completed())
        row=self.read()[0]
        self.assertEqual(row['data_date'],'2026-09')
        self.assertIn('даты применения не записаны',row['period_label'])
        self.assertNotIn('2026-09-01',json.dumps(row))

    def test_cash_and_paid_keep_exact_file_period_not_new_daily_rows(self):
        self.event('cash_receipts_reconciled',self.cash())
        self.event('paid_services_stored',self.paid())
        rows=self.read()
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['data_date'],'2026-01-01 — 2026-06-30')
        self.assertEqual(rows[1]['data_date'],'2026-09-02 — 2026-09-30')
        self.assertEqual(rows[0]['period_label'],'Период файла')

    def test_old_paid_format_keeps_recorded_cutoff_not_guessed_range(self):
        self.event('paid_services_stored',self.paid(old=True,status='replaced'))
        row=self.read()[0]
        self.assertEqual((row['data_date'],row['period_label'],row['status']),('2026-09-30','Срез файла','replaced'))

    def test_keep_old_duplicate_partial_and_replaced_are_not_all_imported(self):
        cases=[({'new':0},'duplicate'),({'new':0,'conflict':1,'decision':'keep_old'},'kept_old'),
               ({'new':1,'conflict':1,'decision':'keep_old'},'partial'),
               ({'new':0,'removed':1,'decision':'use_new'},'replaced'),
               ({'new':0,'conflict':1,'decision':'append'},'recorded')]
        for fields,status in cases:
            with self.subTest(fields=fields):
                self.event('cash_receipts_reconciled',self.cash(**fields))
                self.assertEqual(self.read()[0]['status'],status)

    def test_admin_auth_and_other_project_events_never_exposed(self):
        for action in ('login','password_changed','user_permissions_changed','nn_files_uploaded',
                       'provider_classified','clinical_period_reconciled','paid_services_stored_extra'):
            self.event(action,'SECRET unrelated data')
        self.legacy()
        self.assertEqual(len(self.read()),1)
        self.assertNotIn('SECRET',json.dumps(self.read()))

    def test_raw_admin_details_paths_and_amounts_are_not_returned(self):
        self.event('paid_services_stored',self.paid())
        text=json.dumps(self.read())
        self.assertNotIn('/PRIVATE',text)
        self.assertNotIn('SECRET_HASH',text)
        self.assertNotIn('123.45',text)
        self.assertNotIn('source_sha256',text)

    def test_unknown_record_format_visible_but_not_interpreted_as_success(self):
        self.event('completed_reports_reconciled','file=unexpected.xls; SECRET old-format data')
        row=self.read()[0]
        self.assertEqual(row['status'],'recorded')
        self.assertIsNone(row['data_date'])
        self.assertFalse(row['completed_filename'])
        self.assertNotIn('SECRET',json.dumps(row))

    def test_no_upload_is_inferred_from_snapshot_or_patient_tables(self):
        self.conn.execute('CREATE TABLE service_payment_snapshots(payload_json TEXT)')
        self.conn.execute("INSERT INTO service_payment_snapshots VALUES('SECRET payload')")
        self.conn.commit()
        self.assertEqual(self.read(),[])

    def test_bad_dates_and_ambiguous_filenames_fail_closed(self):
        for detail in (self.completed(month='2026-99'),
                       self.completed(filename='fake; sheet=spoof'),
                       self.completed(new='9'*5000)):
            with self.subTest(size=len(detail)):
                self.event('completed_reports_reconciled',detail)
                row=self.read()[0]
                self.assertEqual(row['status'],'recorded')
                self.assertIsNone(row['data_date'])

    def test_reverse_period_or_mismatched_legacy_month_not_guessed(self):
        for detail in (self.paid().replace('period_end=2026-06-30','period_end=2025-06-30'),
                       self.paid(old=True).replace('as_of=2026-09-30','as_of=2026-08-31')):
            self.event('paid_services_stored',detail)
            self.assertEqual(self.read()[0]['status'],'recorded')

    def test_repeat_reads_never_duplicate_or_backfill_any_table(self):
        self.event('cash_receipts_reconciled',self.cash())
        first=self.read()
        self.assertEqual(self.read(),first)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM upload_log').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0],1)

    def test_missing_journal_tables_are_readonly_no_schema_creation(self):
        self.conn.execute('DROP TABLE upload_log');self.conn.execute('DROP TABLE audit_log')
        self.conn.commit()
        self.assertEqual(self.read(),[])

    def test_missing_audit_table_preserves_legacy_rows(self):
        self.legacy();self.conn.execute('DROP TABLE audit_log');self.conn.commit()
        self.assertEqual(len(self.read()),1)

    def test_deleted_actor_does_not_hide_event_or_pick_target_user(self):
        self.event('cash_receipts_reconciled',self.cash(),actor=999)
        self.conn.execute('UPDATE audit_log SET target_user_id=7');self.conn.commit()
        self.assertIsNone(self.read()[0]['user_name'])

    def test_bounded_latest_60_across_sources_sorted_with_timezones(self):
        for i in range(70):
            self.event('cash_receipts_reconciled',self.cash(),when=f'2026-10-21T09:{i//60:02}:{i%60:02}+00:00')
        self.legacy()
        rows=self.read()
        self.assertEqual(len(rows),60)
        self.assertEqual(rows[0]['id'],'audit:70')
        self.assertEqual(rows[-1]['id'],'audit:11')
        self.event('paid_services_stored',self.paid(),when='2026-10-21T16:02:00+07:00')
        self.assertEqual(self.read()[0]['id'],'audit:71')
        for invalid in (True,0,61,'60'):
            with self.assertRaises(ValueError):upload_history.recent_uploads(self.conn,invalid)

    def test_same_file_names_are_not_deduplicated_across_recorded_actions(self):
        self.event('cash_receipts_reconciled',self.cash())
        self.event('cash_receipts_reconciled',self.cash(new=0))
        rows=self.read();self.assertEqual(len(rows),2)
        self.assertEqual([r['status'] for r in rows],['duplicate','imported'])

    def test_existing_template_right_and_html_escaping(self):
        self.event('completed_reports_reconciled',self.completed(filename='<script>alert(1)</script>.xls'))
        rows=self.read()
        template=(ROOT/'templates/uploads.html').read_text()
        start=template.index("{% if 'upload_history' in perms %}")
        end=template.index("{% if 'reports' in perms %}",start)
        with Flask(__name__).app_context():
            allowed=render_template_string(template[start:end],history=rows,perms={'upload_history'})
            denied=render_template_string(template[start:end],history=rows,perms=set())
        self.assertIn('&lt;script&gt;',allowed)
        self.assertNotIn('<script>',allowed)
        self.assertNotIn('Журнал загрузок',denied)
        self.assertEqual(allowed.count('<th>'),5)
        self.assertIn('Месяц файла',allowed)

    def test_actual_core_hook_reads_same_projection(self):
        import daily_upload_core as core
        self.event('cash_receipts_reconciled',self.cash())
        with patch.object(core,'db',return_value=self.conn):
            self.assertEqual(core._history(),self.read())
        # Existing caller gate must not be replaced with generic admin access.
        source=(ROOT/'daily_upload.py').read_text()
        self.assertIn('history=core._history() if "upload_history" in perms else []',source)


if __name__=='__main__':unittest.main(verbosity=2)
