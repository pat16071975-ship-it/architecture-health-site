"""Monthly cost contracts against the real report code and SQLite transactions."""
import calendar
import json
import sqlite3
import unittest
from datetime import date, timedelta
from unittest.mock import patch
import test_advertising_report as fixture
import advertising_monthly_costs as costs

core = fixture.m

class MonthlyCostsTests(unittest.TestCase):
    def setUp(self):
        self.c = fixture.conn()
        self.addCleanup(self.c.close)
        self.c.executescript('CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT); CREATE TABLE audit_log(actor_user_id INTEGER, action TEXT,target_user_id INTEGER,details TEXT,created_at TEXT);')
        self.clock = patch.object(core, '_today', return_value=date(2026,11,15))
        self.clock.start();self.addCleanup(self.clock.stop)
        self.direct = patch.object(core, '_direct_source_values', return_value={'yandex_ads':{'spend':100,'clicks':10,'conversions':1}})
        self.direct.start();self.addCleanup(self.direct.stop)
        self.coverage = patch.object(core, '_dataset_period_complete', return_value=True)
        self.coverage.start();self.addCleanup(self.coverage.stop)

    def view(self, month='2026-11'):
        return costs.view(core,self.c,month)

    def change(self, month='2026-11', **fields):
        costs.mutate(core,self.c,{'month':month,'revision':self.view(month)['revision'],**fields},1,'2026-11-15T12:00:00Z')

    def add(self, amount=3000, month='2026-11', **fields):
        self.change(month,action='save',source='other',label='Площадка',amount=amount,**fields)
        return self.view(month)['records'][-1]['id']

    def approve(self,month='2026-11',**fields):
        self.change(month,action='confirm',mode='auto',**fields)

    def test_fixed_days_plus_actual_direct_not_full_month_or_vat_twice(self):
        self.add();self.approve()
        r=costs.period_costs(core,self.c,date(2026,11,1),date(2026,11,15))
        self.assertEqual(r,dict(fixed=1500,direct=100,complete=True,total=1600))
        self.assertEqual(core._summary(self.c,date(2026,11,1),date(2026,11,15))['spend'],1600)
        self.assertEqual(self.view()['automatic_total'],3100)
        self.assertEqual(self.view()['confirmed_total'],3100)

    def test_adjustment_is_fixed_and_direct_refresh_does_not_freeze_total(self):
        self.add();self.change(action='confirm',mode='manual',total='3400')
        self.assertEqual(self.view()['confirmed']['fixed'],3300)
        with patch.object(core,'_direct_source_values',return_value={'yandex_ads':{'spend':200}}):
            self.assertEqual(self.view()['confirmed_total'],3500)
            r=costs.period_costs(core,self.c,date(2026,11,1),date(2026,11,15))
            self.assertEqual(r['total'],1850)

    def test_missing_month_vs_explicit_zero_and_missing_direct(self):
        self.assertIsNone(costs.period_costs(core,self.c,date(2026,11,1),date(2026,11,15))['total'])
        self.approve()
        self.assertEqual(self.view()['confirmed']['fixed'],0)
        self.assertEqual(costs.period_costs(core,self.c,date(2026,11,1),date(2026,11,15))['total'],100)
        with patch.object(core,'_dataset_period_complete',return_value=False):
            r=costs.period_costs(core,self.c,date(2026,11,1),date(2026,11,15))
            self.assertEqual(r['direct'],100);self.assertIsNone(r['total'])
        with patch.object(core,'_direct_source_values',return_value={}):
            with self.assertRaises(ValueError):self.approve()

    def test_calendar_rounding_is_additive_including_leap_year(self):
        for y,m in [(2026,1),(2026,2),(2028,2),(2026,4)]:
            days=calendar.monthrange(y,m)[1]
            values=[costs.apportioned(100,date(y,m,d),date(y,m,d)) for d in range(1,days+1)]
            self.assertEqual(sum(values),100)
            self.assertEqual(costs.apportioned(100,date(y,m,5),date(y,m,15)),sum(values[4:15]))

    def test_cross_months_independent_confirmation(self):
        self.add(3100,'2026-10');self.approve('2026-10')
        self.assertIsNone(costs.period_costs(core,self.c,date(2026,10,31),date(2026,11,2))['total'])
        self.add();self.approve()
        self.assertEqual(costs.period_costs(core,self.c,date(2026,10,31),date(2026,11,2))['total'],400)

    def test_move_edit_delete_history_and_both_approvals_invalidated(self):
        ident=self.add();self.approve();self.approve('2026-10')
        self.change(action='save',id=ident,target_month='2026-10',source='other',label='Исправлено',amount='2000')
        self.assertIsNone(self.view()['confirmed']);self.assertIsNone(self.view('2026-10')['confirmed'])
        self.assertEqual(len(self.view()['records']),0)
        self.assertEqual(self.view('2026-10')['records'][0]['amount'],2000)
        self.assertEqual(len(costs.load(self.c)['history'][-1]['invalidated_confirmations']),2)
        with self.assertRaises(ValueError):self.change('2026-10',action='delete',id=ident)
        self.change('2026-10',action='delete',id=ident,confirmation='delete')
        self.assertEqual(len(self.view('2026-10')['records']),0)
        self.assertEqual(costs.load(self.c)['history'][-1]['before']['amount'],2000)

    def test_stale_revision_and_replay_do_not_duplicate(self):
        form={'month':'2026-11','revision':self.view()['revision'],'action':'save','source':'other','label':'Платёж','amount':'0'}
        costs.mutate(core,self.c,form,1,'now')
        with self.assertRaises(ValueError):costs.mutate(core,self.c,form,1,'now')
        self.assertEqual(len(self.view()['records']),1)

    def test_direct_change_after_preview_requires_review(self):
        revision=self.view()['revision']
        with patch.object(core,'_direct_source_values',return_value={'yandex_ads':{'spend':200}}):
            with self.assertRaises(ValueError):costs.mutate(core,self.c,{'month':'2026-11','revision':revision,'action':'confirm','mode':'manual','total':'1000'},1,'now')

    def test_audit_failure_rolls_back_payment_and_catalog(self):
        self.c.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'audit failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.add()
        self.assertEqual(costs.load(self.c)['entries'],{})
        self.assertEqual(core._load_cost_store(self.c)['sources'],{})

    def legacy(self):
        store=core._load_cost_store(self.c)
        store['sources']['paid']={'name':'Площадка','channel_name':'Платная реклама','manual':True}
        store['periods']['sources']['paid']=[{'from':'2026-11-01','to':'2026-11-15','advertising_payment':3000,'contractors_payment':None,'total_spend':3000}]
        core._persist_cost_store(self.c,store);self.c.commit()
        return store

    def test_legacy_read_only_then_edit_wrong_month(self):
        self.legacy();before=self.c.total_changes
        row=self.view()['records'][0]
        self.assertEqual(self.c.total_changes,before);self.assertEqual(row['amount'],3000)
        self.change(action='save',id=row['id'],target_month='2026-10',source='paid',label='Исправление даты',amount='3000')
        self.assertEqual(self.view()['records'],[])
        self.assertEqual(self.view('2026-10')['fixed'],3000)
        self.assertEqual(core._load_cost_store(self.c)['periods']['sources'].get('paid',[]),[])

    def test_legacy_endpoint_change_invalidates_confirmation_fingerprint(self):
        store=self.legacy();self.approve()
        store['periods']['sources']['paid'][0].update(advertising_payment=4000,total_spend=4000)
        core._persist_cost_store(self.c,store);self.c.commit()
        self.assertIsNone(self.view()['confirmed'])
        self.assertIsNone(costs.period_costs(core,self.c,date(2026,11,1),date(2026,11,15))['total'])

    def test_campaign_detail_is_not_double_counted(self):
        rows=[dict(source='a',month='2026-11',amount=300,level='source'),dict(source='a',month='2026-11',amount=200,level='campaign')]
        self.assertEqual(costs.fixed_total(rows),300)
        self.assertEqual(costs.fixed_total(rows[1:]),200)

    def campaign_fixture(self, with_parent):
        store = core._load_cost_store(self.c)
        store['sources']['paid'] = {'name': 'Площадка', 'channel_name': 'Платная реклама', 'manual': True}
        def period(value):
            return {'from': '2026-11-01', 'to': '2026-11-30',
                    'advertising_payment': value, 'contractors_payment': None, 'total_spend': value}
        if with_parent:
            store['periods']['sources']['paid'] = [period(500)]
        for name, value in [('A', 200), ('B', 300)]:
            key = core._campaign_cost_key('paid', name, name)
            store['campaigns'][key] = {'source_key': 'paid', 'campaign_id': name, 'campaign_name': name}
            store['periods']['campaigns'][key] = [period(value)]
        core._persist_cost_store(self.c, store)
        self.c.commit()
        return next(r for r in self.view()['records'] if r['level'] == 'campaign' and r['amount'] == 200)

    def assert_comment_edit_preserves_campaign_total(self, with_parent):
        original = self.campaign_fixture(with_parent)
        self.assertEqual(self.view()['fixed'], 500)
        self.approve()
        current = original
        # First edit converts legacy storage, second edits the new monthly record.
        for comment in ['Первый комментарий', 'Повторная правка']:
            self.change(action='save', id=current['id'], source='paid', label=current['label'],
                        amount=200, comment=comment, level='source')
            self.assertIsNone(self.view()['confirmed'])
            self.assertEqual(self.view()['fixed'], 500)
            current = next(r for r in self.view()['records'] if r.get('comment') == comment)
            self.assertEqual((current['level'], current['key'], current['source']),
                             ('campaign', original['key'], 'paid'))
            self.assertFalse(current['legacy'])
            event = costs.load(self.c)['history'][-1]
            self.assertEqual(event['before']['level'], 'campaign')
            self.assertEqual(event['after']['level'], 'campaign')
            self.approve()
            self.assertEqual(self.view()['confirmed_total'], 600)  # 500 fixed + 100 Direct.
            self.assertEqual(core._summary(self.c, date(2026,11,1), date(2026,11,15))['spend'], 350)
        return current

    def test_az217_01_comment_edit_with_parent_keeps_500(self):
        self.assert_comment_edit_preserves_campaign_total(True)

    def test_az217_01_comment_edit_campaigns_only_keeps_500(self):
        self.assert_comment_edit_preserves_campaign_total(False)

    def test_campaign_move_delete_keeps_role_and_month_totals(self):
        row = self.assert_comment_edit_preserves_campaign_total(False)
        self.change(action='save', id=row['id'], target_month='2026-10', source='paid',
                    label=row['label'], amount=250, comment='Перенос')
        self.assertEqual(self.view()['fixed'], 300)
        self.assertEqual(self.view('2026-10')['fixed'], 250)
        moved = self.view('2026-10')['records'][0]
        self.assertEqual(moved['level'], 'campaign')
        self.change('2026-10', action='delete', id=moved['id'], confirmation='delete')
        self.assertEqual(self.view('2026-10')['fixed'], 0)
        self.assertEqual(self.view()['fixed'], 300)

    def test_campaign_source_reclassification_rejected_atomically(self):
        row = self.campaign_fixture(True)
        before = self.view()
        with self.assertRaisesRegex(ValueError, 'Источник детализации'):
            self.change(action='save', id=row['id'], source='other', label='Новый источник', amount=200)
        self.assertEqual(self.view(), before)

    def test_unknown_invalid_and_zero_values(self):
        for value in [None,True,False,'','NaN','Inf','-1','abc']:
            with self.subTest(value=value),self.assertRaises(ValueError):self.add(value)
        self.add(0);self.approve();self.assertEqual(self.view()['confirmed']['fixed'],0)

    def test_source_proration_and_no_direct_write(self):
        self.change(action='save',source='yandex_ads',label='Подрядчик',amount=600)
        self.approve()
        store=core._load_cost_store(self.c)
        self.assertEqual(costs.source_period(core,self.c,store,'yandex_ads',date(2026,11,1),date(2026,11,15)),300)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM advertising_traffic_daily').fetchone()[0],0)
        before=self.c.total_changes
        core._summary(self.c,date(2026,11,1),date(2026,11,15))
        self.assertEqual(self.c.total_changes,before)

if __name__=='__main__':unittest.main()
