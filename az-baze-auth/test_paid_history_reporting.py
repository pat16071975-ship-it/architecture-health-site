"""Real storage -> reporting API regressions; synthetic patients and SQLite only."""
import calendar
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask, g
import app as app_core
import ident_import
import paid_services
import paid_period_confirmation as confirmation
import report_storage
import upload_reconcile


class HistoricalPaidReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        paid_services.init_schema(self.conn)
        upload_reconcile.init_schema(self.conn)
        self.conn.executescript(report_storage.REPORT_SCHEMA)
        self.conn.execute('CREATE TABLE permissions(user_id INTEGER,section TEXT)')
        self.patches = [
            patch.object(ident_import,'DENTISTS',{'Dent A.':'Dent Doctor'}),
            patch.object(ident_import,'STRUCTURE_DOCTORS',{'Clinic B.':'Clinic Doctor'}),
            patch.object(ident_import,'LAB_DOCTORS',{}),
            patch.object(ident_import,'KNOWN_STAFF',{'Dent A.','Clinic B.'}),
            patch.object(report_storage,'DB_PATH',Path(self.temp.name)/'auth.db'),
            patch.object(report_storage,'db',return_value=self.conn),
            patch.object(app_core,'db',return_value=self.conn),
        ]
        for p in self.patches: p.start()
        self.declarations=[]

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.conn.close()
        self.temp.cleanup()

    def store(self,start,end,confirmed=False,complete=True):
        shared=paid_services._patient_token('Synthetic Shared Patient')
        other=paid_services._patient_token('Synthetic Other Patient')
        report={
            'month':end[:7] if start==end[:7]+'-01' else '',
            'period_start':start,'period_end':end,
            'totals':{'opening':0,'billed':125,'paid':125,'closing':0},
            'providers':{'Dent A.':{'opening':0,'billed':100,'paid':100,'closing':0},
                         'Clinic B.':{'opening':0,'billed':25,'paid':25,'closing':0}},
            'provider_residual':{'opening':0,'billed':0,'paid':0,'closing':0},
        }
        if complete:
            report['patients']=[
                {'staff':'Dent A.','patient_token':shared,'paid':100},
                {'staff':'Dent A.','patient_token':shared,'paid':10},
                {'staff':'Clinic B.','patient_token':shared,'paid':20},
                {'staff':'Clinic B.','patient_token':other,'paid':5},
                {'staff':'Clinic B.','patient_token':'unpaid','paid':0},
            ]
        paid_services.store_snapshot(self.conn,report,'synthetic.xlsx',
            hashlib.sha256((start+end).encode()).hexdigest(),1,'2026-10-28T15:00:00Z')
        self.conn.commit()
        if confirmed:
            source=next(s for s in confirmation.read_sources(self.conn) if s['sourceFrom']==start and s['sourceTo']==end)
            self.declarations.append(confirmation.make_confirmation(source,start[:7]))
            (Path(self.temp.name)/confirmation.BASENAME).write_text(json.dumps({'version':1,'confirmations':self.declarations}))
        return shared,other

    def add_record(self,day,**extra):
        record={'date':day,'factMedicine':205,'cashTotal':300,'cashOOO':200,'cashIP':100,
                'primary':7,'repeat':11,'labOrders':2,'labRevenue':95,
                'dentists':{'Dent Doctor':150},'clinicDocs':{'Clinic Doctor':55},**extra}
        self.conn.execute('INSERT INTO report_data VALUES(?,?,?,?)',(day,json.dumps(record),1,'original'))
        self.conn.commit()
        return record

    def client(self):
        app=Flask(__name__)
        app.secret_key='synthetic-test-only'
        @app.before_request
        def actor(): g.user={'id':1,'is_admin':True}
        with patch.object(report_storage,'_ensure_schema'):
            report_storage.register_report_storage(app)
        return app.test_client()

    def test_january_february_march_and_may_are_available_without_reimport(self):
        for first,last in [('2026-01-05','2026-01-31'),('2026-02-02','2026-02-28'),
                           ('2026-03-02','2026-03-31'),('2026-05-01','2026-05-30')]:
            self.store(first,last,confirmed=True)
        before='\n'.join(self.conn.iterdump())
        self.conn.execute('PRAGMA query_only=ON')
        monthly=report_storage._management_paid_patients_monthly(self.conn)
        self.assertEqual(set(monthly),{'2026-01','2026-02','2026-03','2026-05'})
        for month,counts in monthly.items():
            self.assertEqual(counts['asOf'],confirmation.month_bounds(month)[1])
            self.assertEqual((counts['medicine'],counts['dent'],counts['structure']),(2,1,2))
        self.assertEqual(before,'\n'.join(self.conn.iterdump()))

    def test_all_ten_months_end_to_end_api_no_changes_to_other_values(self):
        originals={}
        for month in range(1,11):
            prefix=f'2026-{month:02d}'
            first=prefix+('-05' if month==1 else '-02' if month in (2,3) else '-01')
            last=prefix+('-27' if month==10 else '-30' if month==5 else f'-{calendar.monthrange(2026,month)[1]:02d}')
            self.store(first,last,confirmed=month in (1,2,3,5))
            record_day=last if month==10 else confirmation.month_bounds(prefix)[1]
            originals[record_day]=self.add_record(record_day)
        before='\n'.join(self.conn.iterdump())
        self.conn.execute('PRAGMA query_only=ON')
        response=self.client().get('/api/reports/data')
        self.assertEqual(response.status_code,200)
        payload=response.get_json()
        self.assertEqual(len(payload['paidPatientsMonthly']),10)
        for day,original in originals.items():
            row=payload['data'][day]
            for key,value in original.items(): self.assertEqual(row[key],value,(day,key))
            self.assertEqual(row['paidPatients'],2)
            self.assertEqual(row['paidDentPatients'],1)
            self.assertEqual(row['paidClinicPatients'],2)
            self.assertEqual(row['paidPatientPeriodTo'],day)
        self.assertEqual(before,'\n'.join(self.conn.iterdump()))
        self.assertEqual(payload['paidPatientsMonthly']['2026-10']['asOf'],'2026-10-27')

    def test_month_end_does_not_fill_day_27(self):
        self.store('2026-01-05','2026-01-31',confirmed=True)
        self.add_record('2026-01-27')
        self.add_record('2026-01-31')
        data=self.client().get('/api/reports/data').get_json()['data']
        self.assertNotIn('paidPatients',data['2026-01-27'])
        self.assertEqual(data['2026-01-31']['paidPatients'],2)
        self.assertIsNone(report_storage._paid_patient_counts(self.conn,'2026-01-01','2026-01-27'))

    def test_unconfirmed_short_report_is_not_a_whole_month(self):
        self.store('2026-10-12','2026-10-13')
        self.assertEqual(report_storage._management_paid_patients_monthly(self.conn),{})
        self.assertIsNone(report_storage._paid_patient_counts(self.conn,'2026-10-01','2026-10-13'))
        self.assertEqual(report_storage._paid_patient_counts(self.conn,'2026-10-12','2026-10-13')['medicine'],2)

    def test_october_1_27_is_preserved_and_not_expanded(self):
        self.store('2026-10-01','2026-10-27')
        monthly=report_storage._management_paid_patients_monthly(self.conn)
        self.assertEqual(monthly['2026-10']['asOf'],'2026-10-27')
        self.assertIsNone(report_storage._paid_patient_counts(self.conn,'2026-10-01','2026-10-31'))

    def test_may_money_and_payers_use_same_confirmed_calendar_end(self):
        self.store('2026-05-01','2026-05-30',confirmed=True)
        paid=report_storage._management_doctor_paid_snapshots(self.conn)['2026-05'][0]
        payers=report_storage._management_paid_patients_monthly(self.conn)['2026-05']
        self.assertEqual(paid['asOf'],payers['asOf'])
        self.assertEqual(paid['asOf'],'2026-05-31')
        self.assertEqual(paid['dentists'],{'Dent Doctor':100})
        self.assertIsNone(report_storage._paid_patient_counts(self.conn,'2026-05-01','2026-05-30'))

    def test_january_money_and_counts_share_one_reporting_period(self):
        self.store('2026-01-05','2026-01-31',confirmed=True)
        money=report_storage._management_doctor_paid_periods(self.conn)[0]
        self.assertEqual((money['from'],money['to']),('2026-01-01','2026-01-31'))
        self.assertEqual(money['dentists'],{'Dent Doctor':100})
        self.assertEqual(money['clinicDocs'],{'Clinic Doctor':25})
        self.assertEqual(report_storage._paid_patient_counts(self.conn,money['from'],money['to'])['medicine'],2)

    def test_no_complete_patient_evidence_never_falls_back_to_visits(self):
        self.store('2026-01-05','2026-01-31',confirmed=True,complete=False)
        self.add_record('2026-01-31',primary=100,repeat=200)
        data=self.client().get('/api/reports/data').get_json()
        self.assertNotIn('paidPatients',data['data']['2026-01-31'])
        self.assertEqual(data['paidPatientsMonthly'],{})

    def test_overlap_and_repeated_person_not_summed_twice(self):
        self.store('2026-10-01','2026-10-11')
        self.store('2026-10-12','2026-10-13')
        self.store('2026-10-01','2026-10-13')
        self.assertEqual(report_storage._paid_patient_counts(self.conn,'2026-10-01','2026-10-13')['medicine'],2)

    def test_gap_is_not_invented_as_a_zero_day(self):
        self.store('2026-10-01','2026-10-11')
        self.store('2026-10-13','2026-10-14')
        self.assertIsNone(report_storage._paid_patient_counts(self.conn,'2026-10-01','2026-10-14'))

    def test_replacement_revokes_month_confirmation(self):
        self.store('2026-01-05','2026-01-31',confirmed=True)
        self.conn.execute("UPDATE service_payment_periods SET imported_at='different-import'")
        self.assertEqual(report_storage._management_paid_patients_monthly(self.conn),{})
        self.assertEqual(report_storage._management_doctor_paid_periods(self.conn)[0]['from'],'2026-01-05')

    def test_unknown_provider_does_not_get_assigned(self):
        self.store('2026-01-05','2026-01-31',confirmed=True)
        with patch.object(ident_import,'DENTISTS',{}),patch.object(ident_import,'KNOWN_STAFF',{'Clinic B.'}):
            self.assertEqual(report_storage._management_paid_patients_monthly(self.conn),{})
            self.assertEqual(report_storage._management_doctor_paid_periods(self.conn),[])

    def test_patient_tokens_and_confirmation_fingerprints_are_not_exposed(self):
        tokens=self.store('2026-01-05','2026-01-31',confirmed=True)
        self.add_record('2026-01-31')
        text=self.client().get('/api/reports/data').get_data(as_text=True)
        for token in tokens: self.assertNotIn(token,text)
        for field in ('sourceSha256','payloadSha256','importedAt','paid_patients'):
            self.assertNotIn(field,text)

    def test_stale_stored_derived_counts_cannot_bypass_missing_evidence(self):
        self.add_record('2026-01-31',paidPatientEvidenceComplete=True,paidPatients=999)
        before='\n'.join(self.conn.iterdump())
        data=self.client().get('/api/reports/data').get_json()['data']['2026-01-31']
        self.assertNotIn('paidPatients',data)
        self.assertEqual(before,'\n'.join(self.conn.iterdump()))

    def test_rendered_page_loads_monthly_dataset_and_keeps_raw_backups(self):
        app=Flask(__name__);app.secret_key='test'
        with app.test_request_context('/reports/management/'),patch.object(report_storage,'SITE_ROOT',Path(__file__).resolve().parent.parent):
            g.user={'id':1,'is_admin':True}
            html=report_storage._render_server_reports()
        self.assertIn('PAID_PATIENTS_MONTHLY={}',html)
        self.assertIn('payload.paidPatientsMonthly',html)
        self.assertIn('function loadStore(){return SERVER_STORE}',html)
        self.assertIn('20261029-paid-two-views-3',html)

if __name__=='__main__': unittest.main()
