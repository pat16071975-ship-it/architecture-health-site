"""Read-only SQLite/API regressions. No real patient, host or file access."""
import json
import unittest
from unittest.mock import patch
import report_storage
from test_paid_history_reporting import HistoricalPaidReportingTests

class PaidExactAsOfTests(unittest.TestCase):
    def setUp(self):
        self.f = HistoricalPaidReportingTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def test_all_confirmed_ends_remain_available_without_raw_records(self):
        self.f.store('2026-01-01','2026-01-20')
        self.f.store('2026-01-01','2026-01-31')
        before='\n'.join(self.f.conn.iterdump())
        self.f.conn.execute('PRAGMA query_only=ON')
        response=self.f.client().get('/api/reports/data')
        self.assertEqual(response.status_code,200)
        p=response.get_json()
        self.assertEqual(p['data'],{})
        self.assertEqual(set(p['paidPatientsByDate']),{'2026-01-20','2026-01-31'})
        self.assertEqual(p['paidPatientsMonthly']['2026-01']['asOf'],'2026-01-31')
        self.assertEqual(before,'\n'.join(self.f.conn.iterdump()))

    def test_incremental_tail_extends_latest_month_without_double_count(self):
        self.f.store('2026-10-01','2026-10-27')
        self.f.store('2026-10-28','2026-10-28')
        self.f.conn.execute('PRAGMA query_only=ON')
        p=self.f.client().get('/api/reports/data').get_json()
        self.assertEqual(p['paidPatientsMonthly']['2026-10']['asOf'],'2026-10-28')
        self.assertEqual(p['paidPatientsByDate']['2026-10-28']['medicine'],2)
        self.assertEqual(p['paidPatientsByDate']['2026-10-27']['medicine'],2)

    def test_gap_is_not_filled_by_tail(self):
        self.f.store('2026-10-01','2026-10-26')
        self.f.store('2026-10-28','2026-10-28')
        p=self.f.client().get('/api/reports/data').get_json()
        self.assertEqual(p['paidPatientsMonthly']['2026-10']['asOf'],'2026-10-26')
        self.assertNotIn('2026-10-28',p['paidPatientsByDate'])
        self.assertNotIn('2026-10-27',p['paidPatientsByDate'])

    def test_full_month_does_not_invent_day27(self):
        self.f.store('2026-01-05','2026-01-31',confirmed=True)
        self.f.add_record('2026-01-27')
        p=self.f.client().get('/api/reports/data').get_json()
        self.assertNotIn('2026-01-27',p['paidPatientsByDate'])
        self.assertNotIn('paidPatients',p['data']['2026-01-27'])
        self.assertEqual(p['paidPatientsByDate']['2026-01-31']['medicine'],2)

    def test_new_aggregate_map_never_exposes_tokens(self):
        tokens=self.f.store('2026-01-01','2026-01-31')
        p=self.f.client().get('/api/reports/data').get_json()
        serialized=json.dumps(p['paidPatientsByDate'])
        for token in tokens:self.assertNotIn(token,serialized)
        self.assertEqual(set(p['paidPatientsByDate']['2026-01-31']),
            {'from','to','asOf','medicine','dent','structure','dentists','clinicDocs'})

    def test_rendered_page_loads_exact_endpoint_map_and_new_assets(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]/'reports'
        with patch.object(report_storage,'_read_report_file',side_effect=lambda name:(root/name).read_text()):
            html=report_storage._render_server_reports()
        self.assertIn('PAID_PATIENTS_BY_DATE={}',html)
        self.assertIn('PAID_PATIENTS_BY_DATE=payload.paidPatientsByDate||{};',html)
        self.assertIn('period-view.js?v=20261029-paid-cash-daily-1',html)
        self.assertIn('management-mobile-layout.js?v=20261029-paid-two-views-3',html)
        self.assertIn('function loadStore(){return SERVER_STORE}',html)
        self.assertNotIn('seed-data.js',html)

    def test_arbitrary_day_endpoints_are_preserved_independently(self):
        for end in ('2026-01-09','2026-01-15','2026-01-17'):
            self.f.store('2026-01-01',end)
        before='\n'.join(self.f.conn.iterdump())
        self.f.conn.execute('PRAGMA query_only=ON')
        p=self.f.client().get('/api/reports/data').get_json()
        self.assertEqual(set(p['paidPatientsByDate']),{'2026-01-09','2026-01-15','2026-01-17'})
        for end in ('2026-01-09','2026-01-15','2026-01-17'):
            self.assertEqual(p['paidPatientsByDate'][end]['from'],'2026-01-01')
            self.assertEqual(p['paidPatientsByDate'][end]['to'],end)
            self.assertEqual(p['paidPatientsByDate'][end]['asOf'],end)
        self.assertEqual(before,'\n'.join(self.f.conn.iterdump()))

    def test_month_end_never_backfills_arbitrary_earlier_day(self):
        self.f.store('2026-01-01','2026-01-31')
        p=self.f.client().get('/api/reports/data').get_json()
        for day in ('2026-01-09','2026-01-15','2026-01-17','2026-01-27'):
            self.assertNotIn(day,p['paidPatientsByDate'])

if __name__=='__main__':unittest.main()
