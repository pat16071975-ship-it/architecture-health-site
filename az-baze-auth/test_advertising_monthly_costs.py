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

    def discovered_channels(self):
        store = core._load_cost_store(self.c)
        for key, name in [('instagram', 'Instagram'), ('yandex_ads', 'Яндекс-реклама'), ('2gis', '2ГИС')]:
            store['sources'][key] = {'name': name, 'channel_name': 'Платная реклама', 'manual': False}
        for source, ident, name in [('instagram','clinic','Instagram клиники'), ('instagram','zubachev','Instagram Зубачёва'), ('instagram','starostenko','Instagram Старостенко'), ('yandex_ads','715444498','Ортодонтия')]:
            key = core._campaign_cost_key(source, ident, name)
            store['campaigns'][key] = {'source_key': source, 'campaign_id': ident, 'campaign_name': name}
        core._persist_cost_store(self.c, store); self.c.commit()
        return store

    def test_discovered_channels_and_instagram_children_visible_without_payments(self):
        self.discovered_channels()
        before = self.c.total_changes
        result = self.view()
        self.assertEqual(result['records'], [])
        channels = {r['source_key']: r for r in result['channels']}
        self.assertEqual(set(channels), {'instagram','yandex_ads','2gis'})
        self.assertEqual({c['campaign_name'] for c in channels['instagram']['campaigns']},
                         {'Instagram клиники','Instagram Зубачёва','Instagram Старостенко'})
        self.assertTrue(all(c['amount'] is None for c in channels['instagram']['campaigns']))
        self.assertEqual(self.c.total_changes, before)

    def test_direct_campaigns_show_observed_spend_even_when_coverage_partial(self):
        store = self.discovered_channels()
        key = next(k for k,v in store['campaigns'].items() if v['source_key']=='yandex_ads')
        with patch.object(core,'_direct_campaign_values',return_value={key:{'spend':87.65}}), patch.object(core,'_dataset_period_complete',return_value=False):
            result = self.view()
        yandex = next(r for r in result['channels'] if r['source_key']=='yandex_ads')
        self.assertEqual(yandex['direct'], 100)
        self.assertEqual(yandex['campaigns'][0]['direct'],87.65)
        self.assertFalse(result['direct_complete'])

    def test_new_instagram_campaign_payment_keeps_detail_and_can_be_edited(self):
        store=self.discovered_channels()
        key=next(k for k,v in store['campaigns'].items() if v['campaign_id']=='clinic')
        self.change(action='save',source='instagram',level='campaign',campaign_key=key,label='Instagram клиники',amount=200)
        entry=self.view()['records'][0]
        self.assertEqual((entry['level'],entry['key']),('campaign',key))
        self.assertEqual(self.view()['fixed'],200)
        self.change(action='save',source='instagram',label='Instagram',amount=500)
        self.assertEqual(self.view()['fixed'],500)
        self.change(action='save',id=entry['id'],source='instagram',label=entry['label'],amount=300)
        self.assertEqual(self.view()['fixed'],500)
        channels={r['source_key']:r for r in self.view()['channels']}
        child=next(c for c in channels['instagram']['campaigns'] if c['campaign_key']==key)
        self.assertEqual(child['amount'],300)

    def test_campaign_creation_rejects_direct_unknown_and_wrong_source(self):
        store=self.discovered_channels()
        ig=next(k for k,v in store['campaigns'].items() if v['source_key']=='instagram')
        direct=next(k for k,v in store['campaigns'].items() if v['source_key']=='yandex_ads')
        for source,key in [('yandex_ads',direct),('2gis',ig),('instagram','unknown')]:
            before=self.view()
            with self.assertRaises(ValueError):
                self.change(action='save',source=source,level='campaign',campaign_key=key,label='forged',amount=100)
            self.assertEqual(self.view(),before)

    def test_unknown_invalid_and_zero_values(self):
        for value in [None,True,False,'','NaN','Inf','-1','abc']:
            with self.subTest(value=value),self.assertRaises(ValueError):self.add(value)
        self.add(0);self.approve();self.assertEqual(self.view()['confirmed']['fixed'],0)

    def source_values(self, first=date(2026,11,1), last=date(2026,11,15)):
        return core._source_economics(self.c, first, last)['sources']

    def test_unconfirmed_channels_use_month_not_legacy_interval(self):
        store = core._load_cost_store(self.c)
        for key, value, end in [('2gis', 8601, '2026-11-15'),
                                ('yandex_business', 11400, '2026-11-30')]:
            store['sources'][key] = {'name': key, 'channel_name': 'Платная реклама', 'manual': True}
            store['periods']['sources'][key] = [dict(
                **{'from': '2026-11-01', 'to': end}, advertising_payment=value,
                contractors_payment=None, total_spend=value)]
        core._persist_cost_store(self.c, store); self.c.commit()
        before = self.c.total_changes
        values = self.source_values()
        self.assertEqual(values['2gis']['total_spend'], 4300.50)
        self.assertEqual(values['yandex_business']['total_spend'], 5700)
        self.assertIsNone(core._summary(self.c,date(2026,11,1),date(2026,11,15))['spend'])
        self.assertEqual(self.c.total_changes, before)
        self.approve()
        self.assertEqual(self.source_values()['yandex_business']['total_spend'], 5700)
        self.change(action='unconfirm')
        self.assertEqual(self.source_values()['2gis']['total_spend'], 4300.50)

    def test_new_payment_edit_delete_visible_without_confirmation(self):
        ident = self.add(11400)
        key = self.view()['records'][0]['source']
        self.assertEqual(self.source_values()[key]['total_spend'], 5700)
        self.approve()
        self.change(action='save', id=ident, source=key, label='Площадка', amount=12000)
        self.assertIsNone(self.view()['confirmed'])
        self.assertEqual(self.source_values()[key]['total_spend'], 6000)
        self.change(action='delete', id=ident, confirmation='delete')
        self.assertIsNone(self.source_values()[key]['total_spend'])

    def test_source_missing_month_is_unknown_but_explicit_zero_is_known(self):
        self.add(3100, '2026-10')
        key = self.view('2026-10')['records'][0]['source']
        self.assertIsNone(self.source_values()[key]['total_spend'])
        self.assertIsNone(self.source_values(date(2026,10,31),date(2026,11,15))[key]['total_spend'])
        self.change(action='save',source=key,label='Площадка',amount=0)
        self.assertEqual(self.source_values()[key]['total_spend'],0)
        self.assertEqual(self.source_values(date(2026,10,31),date(2026,11,15))[key]['total_spend'],100)

    def test_unconfirmed_campaign_detail_source_total_not_doubled(self):
        for parent in [False, True]:
            with self.subTest(parent=parent):
                self.campaign_fixture(parent)
                self.assertEqual(self.source_values()['paid']['total_spend'],250)

    def test_cross_month_legacy_payment_is_unknown_without_fallback(self):
        store = self.legacy()
        store['periods']['sources']['paid'][0]['from'] = '2026-10-31'
        core._persist_cost_store(self.c, store); self.c.commit()
        self.assertIsNone(self.source_values(date(2026,10,31),date(2026,11,15))['paid']['total_spend'])

    def test_moved_payment_does_not_reappear_in_original_month(self):
        ident = self.add(3000)
        key = self.view()['records'][0]['source']
        self.change(action='save',id=ident,target_month='2026-10',source=key,label='Площадка',amount=3100)
        self.assertIsNone(self.source_values()[key]['total_spend'])
        self.assertEqual(self.source_values(date(2026,10,1),date(2026,10,15))[key]['total_spend'],1500)

    def test_direct_and_global_confirmation_gates_unchanged(self):
        self.discovered_channels()
        self.change(action='save',source='2gis',label='2ГИС',amount=8601)
        self.change(action='save',source='yandex_ads',label='Подрядчик',amount=600)
        values = self.source_values()
        self.assertEqual(values['yandex_ads']['advertising_payment'],100)
        self.assertIsNone(values['yandex_ads']['contractors_payment'])
        self.assertIsNone(core._summary(self.c,date(2026,11,1),date(2026,11,15))['spend'])
        self.approve()
        values = self.source_values()
        self.assertEqual(values['yandex_ads']['advertising_payment'],100)
        self.assertEqual(values['yandex_ads']['contractors_payment'],300)
        with patch.object(core,'_dataset_period_complete',return_value=False):
            self.assertIsNone(core._summary(self.c,date(2026,11,1),date(2026,11,15))['spend'])

    def test_source_proration_and_no_direct_write(self):
        self.change(action='save',source='yandex_ads',label='Подрядчик',amount=600)
        self.approve()
        store=core._load_cost_store(self.c)
        self.assertEqual(costs.source_period(core,self.c,store,'yandex_ads',date(2026,11,1),date(2026,11,15)),300)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM advertising_traffic_daily').fetchone()[0],0)
        before=self.c.total_changes
        core._summary(self.c,date(2026,11,1),date(2026,11,15))
        self.assertEqual(self.c.total_changes,before)


    def instagram_owner_november_fixture(self):
        """Two original payments, including the exact old «Отделение структуры» identity."""
        store = core._load_cost_store(self.c)
        store['sources']['instagram'] = {
            'name': 'Instagram', 'channel_name': 'Социальные сети', 'manual': False
        }
        # Legacy cost editor knew two labels/keys that differ from Metrika's
        # factual Instagram campaign names «Зубачев» and «Клиника».
        old_z = core._campaign_cost_key('instagram', '', 'Зубачёв')
        old_clinic = core._campaign_cost_key('instagram', '', 'Отделение структуры')
        real_z = core._campaign_cost_key('instagram', '', 'Зубачев')
        real_clinic = core._campaign_cost_key('instagram', '', 'Клиника')
        store['campaigns'][old_z] = {
            'source_key': 'instagram', 'campaign_id': '', 'campaign_name': 'Зубачёв'
        }
        store['campaigns'][old_clinic] = {
            'source_key': 'instagram', 'campaign_id': '',
            'campaign_name': 'Отделение структуры'
        }
        core._persist_cost_store(self.c, store)
        entries = {
            'owner-instagram-z': {
                'id': 'owner-instagram-z', 'month': '2026-11',
                'source': 'instagram', 'label': 'Зубачёв',
                'amount': 150000.0, 'comment': '', 'level': 'campaign',
                'key': old_z, 'legacy': False,
            },
            'owner-instagram-clinic': {
                'id': 'owner-instagram-clinic', 'month': '2026-11',
                'source': 'instagram', 'label': 'Отделение структуры',
                'amount': 100000.0, 'comment': '', 'level': 'campaign',
                'key': old_clinic, 'legacy': False,
            },
        }
        state = {'version': 1, 'entries': entries, 'confirmed': {}, 'history': []}
        self.c.execute(
            "INSERT INTO report_blobs(key,payload,updated_by,updated_at) VALUES(?,?,1,'x')",
            (costs.KEY, costs.encode(state)),
        )
        for name, visits in [('Зубачев', 6), ('Клиника', 12)]:
            self.c.execute(
                "INSERT INTO advertising_traffic_daily("
                "provider,metric_date,source_key,source_name,channel_name,"
                "campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ('metrika','2026-11-03','instagram','Instagram','Социальные сети',
                 '',name,None,None,visits,'x','m'),
            )
        self.c.commit()
        return {
            'old_z': old_z, 'old_clinic': old_clinic,
            'real_z': real_z, 'real_clinic': real_clinic,
            'state_before': costs.load(self.c),
            'costs_before': core._load_cost_store(self.c),
        }

    def test_instagram_november_expenses_land_in_real_campaign_rows(self):
        old = self.instagram_owner_november_fixture()
        self.approve()
        before_state = costs.load(self.c)
        before_changes = self.c.total_changes
        first, last = date(2026,11,1), date(2026,11,23)
        # Original source/month money is 250,000, independently of labels.
        self.assertEqual(self.view()['fixed'], 250000)
        self.assertEqual(costs.source_period(
            core, self.c, core._load_cost_store(self.c), 'instagram', first, last
        ), 191666.67)
        source_rows = {row['source_key']: row for row in core._group_breakdown(
            self.c, first, last, 'source'
        )}
        child_rows = [r for r in core._group_breakdown(
            self.c, first, last, 'campaign'
        ) if r['source_key'] == 'instagram']
        by_name = {row['campaign_name']: row for row in child_rows}
        self.assertEqual(set(by_name), {'Зубачев','Клиника'})
        self.assertEqual(len(child_rows), 2)
        self.assertEqual(by_name['Зубачев']['visits'], 6)
        self.assertEqual(by_name['Клиника']['visits'], 12)
        self.assertEqual(by_name['Зубачев']['spend'], 115000.0)
        self.assertEqual(by_name['Клиника']['spend'], 76666.67)
        self.assertEqual(source_rows['instagram']['spend'], 191666.67)
        self.assertEqual(round(sum(r['spend'] for r in child_rows), 2), 191666.67)
        self.assertEqual(by_name['Зубачев']['cost_per_visit'], 115000/6)
        self.assertAlmostEqual(by_name['Клиника']['cost_per_visit'], 76666.67/12)
        # Reporting and editor GET do not migrate/modify the owner's raw records.
        self.assertEqual(self.c.total_changes, before_changes)
        self.assertEqual(costs.load(self.c), before_state)
        self.assertEqual(core._load_cost_store(self.c), old['costs_before'])

    def test_instagram_editor_names_clinic_and_keeps_old_payment_editable(self):
        old = self.instagram_owner_november_fixture()
        before_changes = self.c.total_changes
        data = self.view()
        instagram = next(row for row in data['channels'] if row['source_key']=='instagram')
        campaigns = {row['campaign_name']:row for row in instagram['campaigns']}
        self.assertEqual(set(campaigns), {'Зубачев','Клиника'})
        self.assertEqual(campaigns['Зубачев']['campaign_key'],old['real_z'])
        self.assertEqual(campaigns['Клиника']['campaign_key'],old['real_clinic'])
        self.assertEqual(campaigns['Зубачев']['amount'],150000)
        self.assertEqual(campaigns['Клиника']['amount'],100000)
        self.assertEqual(campaigns['Зубачев']['record_ids'],['owner-instagram-z'])
        self.assertEqual(campaigns['Клиника']['record_ids'],['owner-instagram-clinic'])
        self.assertEqual(self.c.total_changes, before_changes)
        # Editing through the canonical campaign must retain its original
        # persisted record ID, key, amount, and historical provenance.
        self.change(action='save', id='owner-instagram-clinic', source='instagram',
                    level='campaign', campaign_key=old['real_clinic'],
                    label='Клиника', amount='100000', comment='Уточнено название')
        updated = costs.load(self.c)['entries']['owner-instagram-clinic']
        self.assertEqual(updated['key'], old['old_clinic'])
        self.assertEqual(updated['amount'], 100000)
        self.assertEqual(updated['label'], 'Клиника')
        self.assertEqual(costs.load(self.c)['history'][-1]['before']['label'],
                         'Отделение структуры')
        self.assertEqual(self.view()['fixed'], 250000)

    def test_instagram_source_level_legacy_names_are_not_double_counted(self):
        self.instagram_owner_november_fixture()
        state = costs.load(self.c)
        # Source-level payment with a recognized name represents an older
        # version of the clinic's same 100k campaign, not a second charge.
        state['entries']['old-named-clinic'] = {
            'id':'old-named-clinic', 'month':'2026-11','source':'instagram',
            'label':'Отделение структуры','amount':100000,
            'comment':'','level':'source','legacy':False,
        }
        self.c.execute(
            "UPDATE report_blobs SET payload=? WHERE key=?",
            (costs.encode(state),costs.KEY),
        )
        self.c.commit()
        first,last=date(2026,11,1),date(2026,11,23)
        campaign = next(meta for meta in core._campaign_registry(self.c)
                        if meta['source_key']=='instagram' and meta['campaign_name']=='Клиника')
        self.assertEqual(costs.campaign_period(
            core,self.c,core._load_cost_store(self.c),campaign,first,last
        ),76666.67)
        # AZ-254-01: the parent MUST count Zubachev as well as the clinic
        # although one old clinic payment was stored at source level.
        self.assertEqual(self.view()['fixed'],250000)
        before_db=self.c.total_changes
        before_ledger=costs.load(self.c)
        before_store=core._load_cost_store(self.c)
        source=next(r for r in core._group_breakdown(
            self.c,first,last,'source') if r['source_key']=='instagram')
        children={r['campaign_name']:r for r in core._group_breakdown(
            self.c,first,last,'campaign') if r['source_key']=='instagram'}
        self.assertEqual(source['spend'],191666.67)
        self.assertEqual(children['Зубачев']['spend'],115000)
        self.assertEqual(children['Клиника']['spend'],76666.67)
        self.assertEqual(round(sum(r['spend'] for r in children.values()),2),source['spend'])
        self.assertEqual(costs.source_period(
            core,self.c,before_store,'instagram',first,last),191666.67)
        self.assertEqual(before_db,self.c.total_changes)
        self.assertEqual(costs.load(self.c),before_ledger)
        self.assertEqual(core._load_cost_store(self.c),before_store)
        self.approve()
        self.assertEqual(self.view()['confirmed']['fixed'],250000)
        self.assertEqual(costs.period_costs(
            core,self.c,first,last)['fixed'],191666.67)
        # No inference from an unrecognized label or from a different source.
        state['entries']['old-named-clinic']['label']='Отделение структуры дополнительный'
        self.c.execute(
            "UPDATE report_blobs SET payload=? WHERE key=?",
            (costs.encode(state),costs.KEY),
        );self.c.commit()
        self.assertEqual(costs.campaign_period(
            core,self.c,core._load_cost_store(self.c),campaign,first,last
        ),76666.67)

    def test_instagram_missing_campaign_is_unknown_not_zero(self):
        old=self.instagram_owner_november_fixture()
        state=costs.load(self.c)
        del state['entries']['owner-instagram-clinic']
        self.c.execute("UPDATE report_blobs SET payload=? WHERE key=?",
                       (costs.encode(state),costs.KEY));self.c.commit()
        first,last=date(2026,11,1),date(2026,11,23)
        kids={r['campaign_name']:r for r in core._group_breakdown(self.c,first,last,'campaign')
              if r['source_key']=='instagram'}
        self.assertIsNone(kids['Клиника']['spend'])
        self.assertEqual(kids['Зубачев']['spend'],115000)
        self.assertEqual(core._group_breakdown(self.c,first,last,'source')[0]['spend'],115000)
        # No accidental inference that "Клиника" has a zero advertising cost.



    def test_az254_01_only_source_level_named_clinic_plus_zubachev_is_250k(self):
        self.instagram_owner_november_fixture()
        state=costs.load(self.c)
        clinic=state['entries'].pop('owner-instagram-clinic')
        state['entries']['old-source-clinic']={
            **clinic,'id':'old-source-clinic','level':'source'
        }
        state['entries']['old-source-clinic'].pop('key',None)
        self.c.execute("UPDATE report_blobs SET payload=? WHERE key=?",
                       (costs.encode(state),costs.KEY));self.c.commit()
        before=costs.load(self.c)
        self.assertEqual(self.view()['fixed'],250000)
        a,b=date(2026,11,1),date(2026,11,23)
        source=next(x for x in core._group_breakdown(self.c,a,b,'source')
                    if x['source_key']=='instagram')
        rows={x['campaign_name']:x for x in core._group_breakdown(self.c,a,b,'campaign')
              if x['source_key']=='instagram'}
        self.assertEqual(source['spend'],191666.67)
        self.assertEqual(rows['Зубачев']['spend'],115000)
        self.assertEqual(rows['Клиника']['spend'],76666.67)
        self.assertEqual(costs.load(self.c),before)
        self.assertEqual(round(sum(x['spend'] for x in rows.values()),2),source['spend'])

    def test_az254_01_unknown_generic_source_parent_keeps_existing_precedence(self):
        self.instagram_owner_november_fixture()
        state=costs.load(self.c)
        state['entries']['instagram-budget']={
            'id':'instagram-budget','source':'instagram','month':'2026-11',
            'label':'Instagram общая реклама','amount':250000,
            'level':'source','legacy':False,'comment':''
        }
        self.c.execute("UPDATE report_blobs SET payload=? WHERE key=?",
                       (costs.encode(state),costs.KEY));self.c.commit()
        # An actual source-level budget keeps the existing A-COST-06
        # precedence, not the new per-campaign alias deduplication.
        self.assertEqual(self.view()['fixed'],250000)
        self.assertEqual(costs.fixed_total(
            list(costs.load(self.c)['entries'].values()),
            core._load_cost_store(self.c)),250000)

    def test_az254_02_historical_old_key_clinic_remains_visible_no_new_month_entry(self):
        keys=self.instagram_owner_november_fixture()
        state=costs.load(self.c)
        del state['entries']['owner-instagram-clinic']
        self.c.execute("UPDATE report_blobs SET payload=? WHERE key=?",
                       (costs.encode(state),costs.KEY))
        store=core._load_cost_store(self.c)
        legacy={'from':'2026-11-01','to':'2026-11-30',
                'advertising_payment':100000,'contractors_payment':None,
                'total_spend':100000}
        store['periods']['campaigns'][keys['old_clinic']]=[legacy]
        core._persist_cost_store(self.c,store);self.c.commit()
        before_store=core._load_cost_store(self.c)
        before_ledger=costs.load(self.c)
        changes=self.c.total_changes
        a,b=date(2026,11,1),date(2026,11,30)
        registry={meta['campaign_name']:meta for meta in core._campaign_registry(self.c)
                  if meta['source_key']=='instagram'}
        self.assertEqual(set(registry),{'Зубачев','Клиника'})
        self.assertEqual(registry['Клиника']['campaign_key'],keys['real_clinic'])
        self.assertIn(keys['old_clinic'],registry['Клиника']['cost_record_keys'])
        rows={r['campaign_name']:r for r in core._group_breakdown(self.c,a,b,'campaign')
              if r['source_key']=='instagram'}
        parent=next(r for r in core._group_breakdown(self.c,a,b,'source')
                    if r['source_key']=='instagram')
        self.assertEqual(rows['Зубачев']['spend'],150000)
        self.assertEqual(rows['Клиника']['spend'],100000)
        self.assertEqual(parent['spend'],250000)
        self.assertEqual(rows['Клиника']['campaign_key'],keys['real_clinic'])
        self.assertEqual(sum(x['spend'] for x in rows.values()),parent['spend'])
        self.assertEqual(changes,self.c.total_changes)
        self.assertEqual(core._load_cost_store(self.c),before_store)
        self.assertEqual(costs.load(self.c),before_ledger)
        self.assertEqual(self.view()['fixed'],250000)

    def test_az254_02_historical_old_key_works_with_no_other_monthly_entries(self):
        keys=self.instagram_owner_november_fixture()
        state=costs.load(self.c)
        state['entries'].clear()
        self.c.execute("UPDATE report_blobs SET payload=? WHERE key=?",
                       (costs.encode(state),costs.KEY))
        store=core._load_cost_store(self.c)
        store['periods']['campaigns'][keys['old_clinic']]=[{
            'from':'2026-11-01','to':'2026-11-30',
            'advertising_payment':100000,
            'contractors_payment':None,'total_spend':100000,
        }]
        core._persist_cost_store(self.c,store);self.c.commit()
        a,b=date(2026,11,1),date(2026,11,30)
        kids={r['campaign_name']:r for r in core._group_breakdown(self.c,a,b,'campaign')
              if r['source_key']=='instagram'}
        parent=next(r for r in core._group_breakdown(self.c,a,b,'source')
                    if r['source_key']=='instagram')
        self.assertEqual(kids['Клиника']['spend'],100000)
        self.assertIsNone(kids['Зубачев']['spend'])
        self.assertEqual(parent['spend'],100000)
        self.assertEqual(self.view()['fixed'],100000)

    def test_az254_02_conflicting_duplicate_history_is_unknown_not_double_counted(self):
        keys=self.instagram_owner_november_fixture()
        state=costs.load(self.c)
        state['entries'].clear()
        self.c.execute("UPDATE report_blobs SET payload=? WHERE key=?",
                       (costs.encode(state),costs.KEY))
        store=core._load_cost_store(self.c)
        old={'from':'2026-11-01','to':'2026-11-30',
             'advertising_payment':100000,'contractors_payment':None,
             'total_spend':100000}
        store['periods']['campaigns'][keys['old_clinic']]=[old]
        store['campaigns'][keys['real_clinic']] = {
            'source_key':'instagram', 'campaign_id':'', 'campaign_name':'Клиника'
        }
        store['periods']['campaigns'][keys['real_clinic']]=[dict(old)]
        # A matching duplicate interval is the same expense, not two.
        core._persist_cost_store(self.c,store);self.c.commit()
        a,b=date(2026,11,1),date(2026,11,30)
        kids={r['campaign_name']:r for r in core._group_breakdown(self.c,a,b,'campaign')
              if r['source_key']=='instagram'}
        self.assertEqual(kids['Клиника']['spend'],100000)
        store['periods']['campaigns'][keys['real_clinic']][0]['total_spend']=110000
        store['periods']['campaigns'][keys['real_clinic']][0]['advertising_payment']=110000
        core._persist_cost_store(self.c,store);self.c.commit()
        kids={r['campaign_name']:r for r in core._group_breakdown(self.c,a,b,'campaign')
              if r['source_key']=='instagram'}
        self.assertIsNone(kids['Клиника']['spend'])


if __name__=='__main__':unittest.main()
