"""Synthetic regressions: six counters, read-only consumers, expense coverage."""
import json
import unittest
from unittest.mock import patch
from copy import deepcopy
from test_completed_visit_totals import conn, insert_day
import completed_visit_totals as completed
import report_storage as storage
import daily_upload_core as upload
import finrez
import report_accounting
import terminology


class ReportConsistency(unittest.TestCase):
    def setUp(self):
        self.c = conn()
        self.addCleanup(self.c.close)
        for day, counts in [('10', (39,174,20,100,19,74)),
                            ('11', (3,15,2,10,1,5)), ('12', (2,22,1,13,1,9))]:
            date = '2026-11-' + day
            p,r,dp,dr,cp,cr = counts
            insert_day(self.c, date, p, r)
            data=json.loads(self.c.execute('SELECT normalized_json FROM daily_uploads WHERE data_date=?',(date,)).fetchone()[0])
            data['doctors']={date:{'Первичные':{'Чирков М. С.':dp,'Лапина Н. С.':cp},
                                  'Повторные':{'Чирков М. С.':dr,'Лапина Н. С.':cr}}}
            self.c.execute('UPDATE daily_uploads SET normalized_json=? WHERE data_date=?',(json.dumps(data),date))
            record={'date':date,'primary':p,'repeat':r,'dentPrimary':dp,'dentRepeat':dr,
                    'clinicPrimary':cp,'clinicRepeat':cr,'cashTotal':3583126,
                    'factMedicine':3000000,'dentists':{'Doctor':15000},'plan':10000000}
            self.c.execute('INSERT INTO report_data VALUES(?,?,?,?)',(date,json.dumps(record),1,'test'))
        self.c.commit()

    def test_all_read_consumers_share_cumulative_counters_without_writes(self):
        before=self.c.total_changes
        with patch.object(finrez,'db',return_value=self.c):
            data=finrez._management_months()['2026-11']
        self.assertEqual([data[k] for k in ('primary','repeat','dentPrimary','dentRepeat','clinicPrimary','clinicRepeat')], [44,211,23,123,21,88])
        self.assertEqual(data['cashTotal'],3583126)
        self.assertEqual(data['dentists'],{'Doctor':15000})
        self.assertEqual(self.c.total_changes,before)
        confirmed=storage._canonical_completed_months(self.c)
        self.assertEqual(confirmed['2026-11-11']['dentRepeat'],110)
        self.assertNotIn('2026-11-13',confirmed)

    def test_manual_unassigned_and_consulted_attribution(self):
        n={'overall':{'2026-11-01':{'Первичные':4,'Повторные':5}},
           'doctors':{'2026-11-01':{'Отконсультированные':{'Чирков М. С.':2},'Первичные':{'Unknown':2}}},
           'manual_direction_counts':{'structure':{'Первичные':1,'Повторные':3}}}
        self.assertEqual(completed.direction_day_counts(n,'2026-11-01'),
                         dict(dentPrimary=0,dentRepeat=2,clinicPrimary=1,clinicRepeat=3))
        n['overall']={}
        self.assertIsNone(completed.direction_day_counts(n,'2026-11-01'))

    def test_legacy_direction_baseline_and_restore_keep_money(self):
        self.c.execute('DELETE FROM daily_uploads WHERE data_date < ?',('2026-11-11',))
        expected=storage._canonical_completed_months(self.c)
        self.assertEqual(expected['2026-11-12']['dentPrimary'],23)
        records=storage._preserved_completed_records(self.c,expected)
        self.c.execute('UPDATE report_data SET payload=? WHERE date=?',(json.dumps({'cashTotal':777,'dentPrimary':1}),'2026-11-12'))
        storage._reapply_completed_after_restore(self.c,1,'test',confirmed_before=expected,preserved_records=records)
        row=json.loads(self.c.execute('SELECT payload FROM report_data WHERE date=?',('2026-11-12',)).fetchone()[0])
        self.assertEqual(row['dentPrimary'],23)
        self.assertEqual(row['cashTotal'],777)

    def test_rebuild_uses_identical_direction_series(self):
        with patch.object(upload,'db',return_value=self.c):
            rows=upload._rebuild_management('2026-11','TEST')
        expected=completed.management_month_to_date(self.c,'2026-11')
        for day, counters in expected.items():
            for key,value in counters.items(): self.assertEqual(rows[day][key],value)

    def test_forecast_terminology_keeps_clinic_total_label(self):
        self.assertTrue(terminology.skip_runtime_rename('/reports/forecast/'))
        self.assertFalse(terminology.skip_runtime_rename('/reports/management/'))


class AccountingCoverage(unittest.TestCase):
    def setUp(self):
        self.e={'available':True,'control':{'status':'OK'},'period':['2026-08'],
                'payroll':{'period':['2026-08','2026-09','2026-10'],
                           'partial_period':['2026-11'],
                           'total_by_month':{'2026-08':4366395,'2026-09':5458973.88,'2026-10':4197672.68}},
                'opu':{'expense_lines':[{'category':'Налоги и взносы на ФОТ','months':{'2026-08':476542}}]}}
        self.exp={'available':True,'period':{'from':'2026-08-01','to':'2026-08-31'},
                  'months':{'2026-08':{'operating':{'ФОТ':{'total':1},'Налоги и взносы на ФОТ':{'total':2},'Аренда':{'total':100}}}}}

    def test_accrual_replaces_paid_fot_without_double_count(self):
        original=deepcopy(self.exp)
        row=report_accounting.months(self.exp,self.e,{})['2026-08']
        self.assertEqual(row['knownExpenses'],4366395+476542+100)
        self.assertTrue(row['complete'])
        self.assertEqual(self.exp,original)

    def test_known_payroll_without_other_expenses_never_implies_complete_profit(self):
        rows=report_accounting.months({'available':False},self.e,{'2026-12':{}})
        self.assertEqual(rows['2026-10']['knownExpenses'],4197672.68)
        self.assertFalse(rows['2026-10']['complete'])
        for month in ('2026-11','2026-12'):
            self.assertIsNone(rows[month]['payroll'])
            self.assertIsNone(rows[month]['knownExpenses'])
            self.assertFalse(rows[month]['complete'])

    def test_explicit_zero_is_valid_but_missing_or_failed_control_is_not(self):
        self.e['payroll']['total_by_month']['2026-08']=0
        self.assertTrue(report_accounting.months(self.exp,self.e,{})['2026-08']['payrollComplete'])
        self.e['control']['status']='FAIL'
        row=report_accounting.months(self.exp,self.e,{})['2026-08']
        self.assertIsNone(row['payroll'])
        self.assertFalse(row['complete'])

    def test_partial_period_wins_over_closed_and_coverage_needs_both_bounds(self):
        self.e['payroll']['partial_period'].append('2026-08')
        self.assertFalse(report_accounting.months(self.exp,self.e,{})['2026-08']['complete'])
        self.e['payroll']['partial_period']=[]
        self.exp['period']['from']='2026-08-15'
        self.assertFalse(report_accounting.months(self.exp,self.e,{})['2026-08']['expensesComplete'])
