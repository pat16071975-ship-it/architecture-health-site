import copy
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('AZBAZE_SECRET_KEY', 'test-finmodel-key')
import app as auth
import financial_model as v1
import financial_model_v2 as model
import ident_import


def memory():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    v1.init_schema(conn)
    return conn


def config():
    with memory() as conn:
        c = model.read_state(conn)['config']
    c['payer_checks'] = {k: [20000]*12 for k in model.DIRECTIONS}
    return c


def change(key, value, month=1, scope='future', group='months'):
    return {'group':group, 'key':key, 'value':value, 'from':month, 'scope':scope}


def signed(old, edit, mode='recalculate'):
    return dict(version=old['version'], state_digest=model.digest(old), change=edit, mode=mode)


def period(conn, start='2026-10-01', end='2026-10-31', paid=1000, tokens=('private-patient-a',),
           structure_paid=500, structure_tokens=('private-patient-a', 'private-patient-b')):
    conn.execute('CREATE TABLE IF NOT EXISTS service_payment_periods '
                 '(period_start TEXT,period_end TEXT,payload_json TEXT,source_sha256 TEXT,imported_at TEXT,PRIMARY KEY(period_start,period_end))')
    dent, structure = next(iter(ident_import.DENTISTS)), next(iter(ident_import.STRUCTURE_DOCTORS))
    payload = dict(version=3, period_start=start, period_end=end,
                   providers={dent:{'paid':paid}, structure:{'paid':structure_paid}},
                   paid_patient_evidence_complete=True,
                   paid_patients={dent:list(tokens),structure:list(structure_tokens)})
    conn.execute('INSERT OR REPLACE INTO service_payment_periods VALUES(?,?,?,?,?)',
                 (start,end,json.dumps(payload),'a'*64,'test-upload'))
    conn.commit()
    return payload


class TargetCalculationTests(unittest.TestCase):
    def test_goal_not_clipped_by_supply_and_v1_unchanged(self):
        c=config(); frozen=copy.deepcopy(c); expected=v1.calculate(c)
        result=model.calculate(c)
        self.assertEqual(c,frozen)
        self.assertEqual(v1.calculate(c),expected)
        for i,r in enumerate(result):
            self.assertEqual(r['revenue'],c['months']['target'][i])
            self.assertAlmostEqual(r['revenue'],r['dentistry']+r['structure']+r['other_revenue'])
            self.assertAlmostEqual(r['required_hours'],sum(x['revenue']/c['rooms'][n]['rate'][i] for n,x in enumerate(r['rooms'])))
            self.assertEqual(r['available_revenue'],expected[i]['revenue'])
        c['hires']=[]
        for room in c['rooms']:room['staffed_hours']=[0]*12
        for r in model.calculate(c):
            self.assertEqual(r['available_revenue'],0)
            self.assertEqual(r['revenue'],r['target'])
            self.assertEqual(r['missing_hours'],r['required_hours'])
            self.assertGreater(r['missing_fte'],0)

    def test_payer_requirement_parallel_to_visits_and_rounding(self):
        c=config();c['months']['target']=[1000001]*12;c['months']['other_revenue_rate']=[0]*12
        c['payer_checks']['medicine']=[1000]*12
        r=model.calculate(c);self.assertEqual(r[0]['paid_patients'],1001)
        c['payer_checks']['medicine']=[2000]*12
        q=model.calculate(c);self.assertEqual(q[0]['paid_patients'],501)
        for a,b in zip(r,q):
            for key in ('visits','new','leads','profit','required_hours'):self.assertEqual(a[key],b[key])

    def test_unknown_and_zero_check_never_fabricate_people(self):
        c=config()
        for check in (None,0):
            c['payer_checks']['medicine']=[check]*12
            self.assertIsNone(model.calculate(c)[0]['paid_patients'])
        c['months']['target']=[0]*12
        for r in model.calculate(c):
            for k in ('revenue','paid_patients','required_hours','visits','new','leads','needed_marketing'):
                self.assertEqual(r[k],0)

    def test_marketing_cost_once_uses_need_not_existing_budget(self):
        c=config();c['months']['marketing_fixed']=[1]*12
        for r in model.calculate(c):
            self.assertAlmostEqual(r['needed_marketing'],r['new']/(.35*.78)*1000)
            self.assertEqual(r['marketing'],r['needed_marketing'])
            self.assertEqual(r['marketing_to_add'],max(0,r['needed_marketing']-1))
            self.assertAlmostEqual(r['base_total_cost'],sum(r[k] for k in
                ('wage','payroll_tax','medical','lab','rent','marketing','it','outsourcing','other_cost','reserve')))

    def test_capacity_gap_explicit_with_high_target(self):
        c=config();c['months']['target']=[1e9]*12
        for r in model.calculate(c):
            self.assertEqual(r['revenue'],1e9)
            self.assertGreater(r['capacity_gap'],0)
            self.assertGreater(r['load'],1)
            self.assertTrue(any('ёмкости' in w for w in r['warnings']))
            for room in r['rooms']:
                self.assertAlmostEqual(room['required_schedule'],room['hours']/(.67*.78))

    def test_absence_and_ramp_change_available_not_goal(self):
        c=config();before=model.calculate(c)
        for room in c['rooms']:room['absence_hours']=[10000]*12
        c['ramp']=[0]*5;after=model.calculate(c)
        self.assertEqual([r['revenue'] for r in before],[r['revenue'] for r in after])
        self.assertLess(after[0]['available_hours'],before[0]['available_hours'])
        self.assertGreater(after[0]['missing_fte'],before[0]['missing_fte'])

    def test_zero_denominators_block_only_dependents(self):
        for key in ('booking','attendance','visit_minutes','lead_conversion','staff_hours'):
            c=config();c['months'][key]=[0]*12;r=model.calculate(c)[0]
            self.assertEqual(r['revenue'],r['target'])
            self.assertIsNotNone(r['paid_patients'])
            if key in ('attendance','visit_minutes','lead_conversion'):self.assertIsNone(r['profit'])
            if key in ('booking','attendance','staff_hours'):self.assertIsNone(r['missing_fte'])
        c=config()
        for room in c['rooms']:room['weight']=[0]*12
        r=model.calculate(c)[0]
        self.assertIsNone(r['required_hours']);self.assertIsNone(r['profit'])
        self.assertIsNotNone(r['paid_patients'])

    def test_calendar_preserved(self):
        c=config();c['year']=2028
        self.assertEqual(model.calculate(c)[1]['capacity'],8*29*12)


class WhiteningTests(unittest.TestCase):
    def test_main_profit_includes_extra_once_and_tax_recalculated(self):
        c=config();c['months']['official_base_pct']=[26.5]*12
        for i,r in enumerate(model.calculate(c)):
            self.assertAlmostEqual(r['official_share'],(.265+.01*(i+1)))
            self.assertAlmostEqual(r['official_extra_wage'],r['wage']*.01*(i+1))
            self.assertAlmostEqual(r['official_extra_tax'],r['wage']*.01*(i+1)*.32)
            self.assertAlmostEqual(r['total_cost'],r['base_total_cost']+r['official_extra_tax'])
            self.assertAlmostEqual(r['profit_before'],r['revenue']-r['total_cost'])
            self.assertAlmostEqual(r['tax'],max(max(r['profit_before'],0)*.15,r['revenue']*.01))
            self.assertAlmostEqual(r['profit'],r['profit_before']-r['tax'])
            self.assertEqual(r['official_adjusted_profit'],r['profit'])
            self.assertEqual(r['payroll_tax'],0)
            self.assertAlmostEqual(r['reserve'],r['revenue']*.04)

    def test_cap_at_100_unknown_and_real_zero(self):
        c=config();c['months']['official_base_pct']=[98]*12;c['months']['official_step_pct']=[3]*12
        for r in model.calculate(c):
            self.assertEqual(r['official_share'],1)
            self.assertAlmostEqual(r['official_extra_tax'],r['wage']*.02*.32)
        c['months']['official_base_pct']=[100]*12;c['months']['official_burden_pct']=[None]*12
        self.assertEqual(model.calculate(c)[0]['official_extra_tax'],0)
        c['months']['official_base_pct']=[None]*12
        r=model.calculate(c)[0];self.assertIsNone(r['profit']);self.assertIsNone(r['official_extra_tax'])
        self.assertIsNotNone(r['base_profit'])
        c['months']['official_base_pct']=[0]*12;c['months']['official_burden_pct']=[0]*12
        self.assertEqual(model.calculate(c)[0]['official_extra_tax'],0)

    def test_later_start_and_step_propagation_and_keep(self):
        c=config();c['months']['official_step_pct']=[0]*5+[1]*7
        old=dict(version=0,config=c,results=model.calculate(c),stale=[])
        self.assertTrue(all(r['official_extra_tax']==0 for r in old['results'][:5]))
        edit=change('official_step_pct',3,6,'month')
        new,_=model.transition(old,edit,'recalculate')
        self.assertEqual(new['results'][:5],old['results'][:5])
        self.assertEqual(new['config']['months']['official_step_pct'],[0]*5+[3]+[1]*6)
        for i in range(5,12):self.assertAlmostEqual(new['results'][i]['official_share']-old['results'][i]['official_share'],.02)
        kept,_=model.transition(old,edit,'keep')
        self.assertEqual(kept['results'],old['results']);self.assertEqual(kept['stale'],list(range(6,13)))

    def test_base_fot_not_increased_negative_profit_retained(self):
        c=config();c['months']['rent']=[1e9]*12
        old=model.calculate(c)
        c['months']['official_step_pct']=[0]*12
        for a,b in zip(old,model.calculate(c)):
            self.assertEqual(a['wage'],b['wage']);self.assertEqual(a['reserve'],b['reserve'])
            self.assertLess(a['profit'],0);self.assertLess(a['profit'],b['profit'])


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.conn=memory();self.addCleanup(self.conn.close)
    def refs(self):return model.references(self.conn,today=date(2026,11,20))
    def test_full_month_common_person_and_no_pii(self):
        period(self.conn);r=self.refs()[0]
        self.assertEqual(r['values']['medicine'],dict(revenue=1500,patients=2,check=750))
        self.assertEqual(r['values']['dentistry']['check'],1000)
        self.assertEqual(r['values']['structure']['check'],250)
        self.assertNotIn('private-patient',json.dumps(r))
        before=self.conn.total_changes;self.refs();self.assertEqual(self.conn.total_changes,before)
    def test_split_intervals_union_and_no_overlapping_double_count(self):
        period(self.conn,end='2026-10-15');period(self.conn,start='2026-10-16')
        self.assertEqual(self.refs()[0]['values']['medicine'],dict(revenue=3000,patients=2,check=1500))
        period(self.conn,paid=123,structure_paid=77)
        self.assertEqual(self.refs()[0]['values']['medicine'],dict(revenue=200,patients=2,check=100))
    def test_gap_partial_and_future_month_unavailable(self):
        period(self.conn,end='2026-10-15');period(self.conn,start='2026-10-17')
        self.assertEqual(self.refs(),[])
        period(self.conn,start='2026-11-01',end='2026-11-30')
        self.assertEqual(self.refs(),[])
        period(self.conn,start='2026-09-05',end='2026-09-30')
        self.assertEqual(self.refs(),[])
    def test_missing_or_invalid_evidence_not_zero(self):
        payload=period(self.conn)
        for value in (None,'NaN',True):
            p=copy.deepcopy(payload);p['providers'][next(iter(ident_import.DENTISTS))]['paid']=value
            self.conn.execute('UPDATE service_payment_periods SET payload_json=?',(json.dumps(p),));self.conn.commit()
            self.assertEqual(self.refs(),[])
        payload['paid_patient_evidence_complete']=False
        self.conn.execute('UPDATE service_payment_periods SET payload_json=?',(json.dumps(payload),));self.conn.commit()
        self.assertEqual(self.refs(),[])
    def test_zero_negative_and_empty_patients_leave_check_unknown(self):
        for amount in (0,-100):
            period(self.conn,paid=amount)
            self.assertIsNone(self.refs()[0]['values']['dentistry']['check'])
        period(self.conn,tokens=())
        self.assertIsNone(self.refs()[0]['values']['dentistry']['check'])
    def test_unknown_provider_blocks_and_lab_excluded(self):
        payload=period(self.conn);payload['providers']['Unclassified']={'paid':100}
        self.conn.execute('UPDATE service_payment_periods SET payload_json=?',(json.dumps(payload),));self.conn.commit()
        self.assertEqual(self.refs(),[])
        payload=period(self.conn);lab=next(iter(ident_import.LAB_DOCTORS))
        payload['providers'][lab]={'paid':99999};payload['paid_patients'][lab]=['private-lab']
        self.conn.execute('UPDATE service_payment_periods SET payload_json=?',(json.dumps(payload),));self.conn.commit()
        self.assertEqual(self.refs()[0]['values']['medicine']['revenue'],1500)
        self.assertEqual(self.refs()[0]['values']['medicine']['patients'],2)
    def test_unused_zero_provider_headers_do_not_discard_paid_month(self):
        payload=period(self.conn)
        for name,value in [('Unused A',0),('Unused B',0.0),('Unused C','0.00')]:
            payload['providers'][name]={'opening':0,'billed':500,'paid':value,'closing':500}
            payload['paid_patients'][name]=[]
        self.conn.execute('UPDATE service_payment_periods SET payload_json=?',(json.dumps(payload),));self.conn.commit()
        before=list(self.conn.iterdump());self.conn.execute('PRAGMA query_only=ON')
        refs=self.refs()
        self.assertEqual(len(refs),1)
        self.assertEqual(refs[0]['values']['medicine'],dict(revenue=1500,patients=2,check=750))
        self.assertEqual(refs[0]['values']['dentistry']['check'],1000)
        self.assertEqual(refs[0]['values']['structure']['check'],250)
        self.assertEqual(list(self.conn.iterdump()),before)
    def test_snapshot_zero_headers_have_same_result_as_period(self):
        payload=period(self.conn)
        payload.update(version=2,month='2026-10',as_of_date='2026-10-31')
        payload['providers']['Unused snapshot provider']={'paid':0}
        self.conn.execute('DROP TABLE service_payment_periods')
        self.conn.execute('CREATE TABLE service_payment_snapshots(month TEXT,as_of_date TEXT,payload_json TEXT)')
        self.conn.execute('INSERT INTO service_payment_snapshots VALUES(?,?,?)',('2026-10','2026-10-31',json.dumps(payload)))
        self.conn.commit()
        self.assertEqual(self.refs()[0]['values']['medicine'],dict(revenue=1500,patients=2,check=750))
    def test_unknown_nonzero_or_invalid_paid_is_not_treated_as_unused(self):
        for value in (1,-1,0.001,None,True,False,'','NaN','Infinity','bad'):
            with self.subTest(value=value):
                payload=period(self.conn);payload['providers']['Unclassified']={'paid':value}
                self.conn.execute('UPDATE service_payment_periods SET payload_json=?',(json.dumps(payload),));self.conn.commit()
                self.assertEqual(self.refs(),[])
        payload=period(self.conn);payload['providers']['Unclassified']={}
        self.conn.execute('UPDATE service_payment_periods SET payload_json=?',(json.dumps(payload),));self.conn.commit()
        self.assertEqual(self.refs(),[])
    def test_zero_unknown_provider_with_payer_evidence_still_blocks(self):
        payload=period(self.conn);payload['providers']['Unclassified']={'paid':0}
        payload['paid_patients']['Unclassified']=['private-unallocated-payer']
        self.conn.execute('UPDATE service_payment_periods SET payload_json=?',(json.dumps(payload),));self.conn.commit()
        self.assertEqual(self.refs(),[])
    def test_source_snapshot_manual_override_and_changed_source_refused(self):
        period(self.conn);refs=self.refs();old=model.read_state(self.conn)
        edit=dict(group='payer_source',month=refs[0]['month'],digest=refs[0]['digest'],scope='future',**{'from':3})
        new,_=model.transition(old,edit,'recalculate',refs)
        self.assertEqual(new['config']['payer_checks']['medicine'],[750]*12)
        self.assertEqual(new['results'][:2],old['results'][:2])
        manual=change('medicine',999,4,'month','payer_check')
        modified,_=model.transition(new,manual,'recalculate')
        self.assertEqual(modified['config']['payer_sources']['medicine'][3],{'kind':'manual'})
        period(self.conn,paid=2000)
        with self.assertRaises(ValueError):model.transition(old,edit,'recalculate',self.refs())


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.conn=memory();self.addCleanup(self.conn.close)
        self.conn.execute('CREATE TABLE sentinel(raw BLOB, amount REAL)')
        self.conn.execute("INSERT INTO sentinel VALUES(X'000102ff',-10)");self.conn.commit()
    def test_copy_actual_saved_variant1_not_seed_and_no_read_writes(self):
        v1.commit_change(self.conn,0,change('target',87654321),'recalculate',1)
        prior=list(self.conn.iterdump());self.conn.execute('PRAGMA query_only=ON')
        old=model.read_state(self.conn)
        self.assertEqual(old['config']['months']['target'],[87654321]*12)
        self.assertEqual(list(self.conn.iterdump()),prior)
    def test_independent_storage_and_history_in_both_directions(self):
        v1.commit_change(self.conn,0,change('target',12345678),'recalculate',1)
        first=v1.read_state(self.conn)
        old=model.read_state(self.conn);new=model.commit_change(self.conn,signed(old,change('target',23456789)),1)
        self.assertEqual(v1.read_state(self.conn),first)
        v1.commit_change(self.conn,1,change('rent',42),'recalculate',1)
        self.assertEqual(model.read_state(self.conn),new)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM financial_model_v2_history').fetchone()[0],1)
        self.assertEqual(tuple(self.conn.execute('SELECT * FROM sentinel').fetchone()),(b'\x00\x01\x02\xff',-10))
    def test_v1_changed_during_initial_preview_refused(self):
        old=model.read_state(self.conn);payload=signed(old,change('target',123))
        v1.commit_change(self.conn,0,change('rent',4),'recalculate',1)
        with self.assertRaises(RuntimeError):model.commit_change(self.conn,payload,1)
        self.assertEqual(model.read_state(self.conn)['version'],0)
    def test_replay_and_journal_error_atomic(self):
        old=model.read_state(self.conn);payload=signed(old,change('rent',1))
        model.commit_change(self.conn,payload,1)
        with self.assertRaises(RuntimeError):model.commit_change(self.conn,payload,1)
        before=list(self.conn.iterdump())
        self.conn.execute("CREATE TRIGGER failv2 BEFORE INSERT ON financial_model_v2_history BEGIN SELECT RAISE(ABORT,'failed');END")
        with self.assertRaises(sqlite3.IntegrityError):
            model.commit_change(self.conn,signed(model.read_state(self.conn),change('rent',2)),1)
        self.conn.execute('DROP TRIGGER failv2')
        self.assertEqual(list(self.conn.iterdump()),before)
    def test_keep_and_partial_recalc_preserves_history(self):
        old=model.read_state(self.conn)
        kept,_=model.transition(old,change('rent',1,6),'keep')
        self.assertEqual(kept['results'],old['results']);self.assertEqual(kept['stale'],list(range(6,13)))
        new,_=model.transition(kept,change('rent',2,7,'month'),'recalculate')
        self.assertEqual(new['results'][:6],old['results'][:6]);self.assertEqual(new['results'][7:],old['results'][7:])
        self.assertNotIn(7,new['stale'])
    def test_batch_atomic_and_invalid_values(self):
        old=model.read_state(self.conn)
        edit=dict(group='batch',changes=[change('official_step_pct',2,6,'month'),change('medicine',1000,3,'month','payer_check')])
        new,_=model.transition(old,edit,'recalculate')
        self.assertEqual(new['results'][:2],old['results'][:2]);self.assertEqual(new['results'][3:5],old['results'][3:5])
        for value in (-1,'NaN',True,1e12):
            edit['changes'][-1]['value']=value
            with self.assertRaises(ValueError):model.transition(old,edit,'recalculate')
        self.assertIsNone(old['config']['payer_checks']['medicine'][2])


class InitialPayerDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.conn=memory();self.addCleanup(self.conn.close)
        original=model.references
        clock=patch.object(model,'references',side_effect=lambda conn: original(conn,today=date(2026,11,20)))
        clock.start();self.addCleanup(clock.stop)

    def test_latest_month_with_unused_headers_initializes_and_new_money_invalidates_preview(self):
        period(self.conn,start='2026-09-01',end='2026-09-30',paid=300,structure_paid=100)
        payload=period(self.conn)
        payload['providers']['Unused A']={'paid':0}
        payload['providers']['Unused B']={'paid':0.0}
        payload['providers']['Unused C']={'paid':0}
        self.conn.execute('UPDATE service_payment_periods SET payload_json=? WHERE period_start=?',
                          (json.dumps(payload),'2026-10-01'));self.conn.commit()
        state=model.read_state(self.conn)
        self.assertEqual(state['payer_initialization'],dict(status='source',month='2026-10'))
        self.assertEqual(state['config']['payer_checks']['medicine'],[750]*12)
        self.assertTrue(all(row['paid_patients']>0 for row in state['results']))
        token=signed(state,dict(group='refresh'))
        payload['providers']['Unused B']['paid']=10
        self.conn.execute('UPDATE service_payment_periods SET payload_json=? WHERE period_start=?',
                          (json.dumps(payload),'2026-10-01'));self.conn.commit()
        before=list(self.conn.iterdump())
        with self.assertRaisesRegex(RuntimeError,'Модель изменилась'):
            model.commit_change(self.conn,token,1)
        self.assertEqual(list(self.conn.iterdump()),before)

    def test_latest_complete_month_populates_all_months_readonly(self):
        period(self.conn,start='2026-09-01',end='2026-09-30',paid=500)
        period(self.conn)
        period(self.conn,start='2026-11-01',end='2026-11-17',paid=999999)
        period(self.conn,start='2026-12-01',end='2026-12-31',paid=888888)
        before=list(self.conn.iterdump());first=v1.read_state(self.conn)
        self.conn.execute('PRAGMA query_only=ON')
        state=model.read_state(self.conn)
        self.assertEqual(state['payer_initialization'],dict(status='source',month='2026-10'))
        for key,check in [('medicine',750),('dentistry',1000),('structure',250)]:
            self.assertEqual(state['config']['payer_checks'][key],[check]*12)
            self.assertTrue(all(s['month']=='2026-10' for s in state['config']['payer_sources'][key]))
        self.assertTrue(all(r['paid_patients']>0 and r['paid_dentistry']>0 and r['paid_structure']>0 for r in state['results']))
        self.assertEqual(model.digest(model.read_state(self.conn)),model.digest(state))
        self.assertEqual(v1.read_state(self.conn),first)
        self.assertEqual(list(self.conn.iterdump()),before)

    def test_gap_in_latest_month_uses_last_fully_covered_month(self):
        period(self.conn,start='2026-09-01',end='2026-09-30',paid=300,structure_paid=100)
        period(self.conn,end='2026-10-15')
        period(self.conn,start='2026-10-17')
        state=model.read_state(self.conn)
        self.assertEqual(state['payer_initialization']['month'],'2026-09')
        self.assertEqual(state['config']['payer_checks']['medicine'],[200]*12)

    def test_latest_source_does_not_mix_directions_or_fabricate_zero_check(self):
        period(self.conn,start='2026-09-01',end='2026-09-30')
        period(self.conn,paid=0,tokens=())
        state=model.read_state(self.conn)
        self.assertEqual(state['payer_initialization']['month'],'2026-10')
        self.assertEqual(state['config']['payer_checks']['dentistry'],[None]*12)
        self.assertTrue(all(r['paid_dentistry'] is None for r in state['results']))
        self.assertEqual(state['config']['payer_checks']['structure'],[250]*12)

    def test_absent_and_failed_source_have_explicit_status_without_writes(self):
        before=list(self.conn.iterdump())
        self.assertEqual(model.read_state(self.conn)['payer_initialization']['status'],'missing')
        for error in (ValueError('bad confirmation'),sqlite3.DatabaseError('unavailable'),OSError('unreadable')):
            with patch.object(model,'references',side_effect=error):
                state=model.read_state(self.conn)
            self.assertEqual(state['payer_initialization']['status'],'error')
            self.assertTrue(all(r['paid_patients'] is None for r in state['results']))
        self.assertEqual(list(self.conn.iterdump()),before)

    def test_first_save_freezes_source_and_new_uploads_do_not_change_plan(self):
        period(self.conn);first=v1.read_state(self.conn)
        old=model.read_state(self.conn)
        saved=model.commit_change(self.conn,signed(old,dict(group='refresh')),1)
        period(self.conn,paid=100000)
        with patch.object(model,'references',side_effect=AssertionError('saved plan must not reload facts')):
            self.assertEqual(model.read_state(self.conn),saved)
        self.assertEqual(v1.read_state(self.conn),first)
        journal=json.loads(self.conn.execute('SELECT after_json FROM financial_model_v2_history').fetchone()[0])
        self.assertEqual(journal['config']['payer_sources'],saved['config']['payer_sources'])
        self.assertEqual(saved['config']['payer_checks']['medicine'],[750]*12)

    def test_changed_auto_source_rejects_first_save_and_no_partial_records(self):
        period(self.conn);old=model.read_state(self.conn)
        preview=signed(old,dict(group='refresh'))
        period(self.conn,paid=3000);before=list(self.conn.iterdump())
        with self.assertRaisesRegex(RuntimeError,'Модель изменилась'):
            model.commit_change(self.conn,preview,1)
        self.assertEqual(list(self.conn.iterdump()),before)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM financial_model_v2_plan').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM financial_model_v2_history').fetchone()[0],0)

    def test_saved_manual_checks_including_empty_and_zero_are_never_replaced(self):
        period(self.conn);old=model.read_state(self.conn)
        edits=dict(group='batch',changes=[change('medicine',None,1,'month','payer_check'),
            change('dentistry',0,1,'month','payer_check'),change('structure',999,1,'month','payer_check')])
        saved=model.commit_change(self.conn,signed(old,edits),1)
        period(self.conn,paid=50000)
        self.assertEqual(model.read_state(self.conn),saved)
        self.assertIsNone(saved['config']['payer_checks']['medicine'][0])
        self.assertEqual(saved['config']['payer_checks']['dentistry'][0],0)
        self.assertEqual(saved['config']['payer_checks']['structure'][0],999)


class RoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        patcher=patch.object(auth,'DB_PATH',Path(self.tmp.name)/'test.db');patcher.start();self.addCleanup(patcher.stop)
        auth.init_db_file();self.web=auth.create_app();self.web.config['TESTING']=True
        with sqlite3.connect(auth.DB_PATH) as c:
            for uid,admin in ((1,1),(2,0),(3,0),(4,0)):
                c.execute('INSERT INTO users(id,email,full_name,password_hash,is_admin,active,must_change_password,created_at,updated_at) VALUES(?,?,?,?,?,1,0,?,?)',(uid,f'{uid}@example.test','User','x',admin,'t','t'))
            c.executemany('INSERT INTO permissions VALUES(?,?)',[(2,'reports'),(3,'financial_model_edit'),(4,'reports'),(4,'financial_model_edit')])
    def client(self,uid=None):
        c=self.web.test_client()
        if uid:
            with c.session_transaction() as s:s['user_id']=uid;s['csrf']='test'
        return c
    def preview(self,c,edit=None):
        return c.post('/reports/financial-model/v2/preview',data=dict(csrf='test',version=0,mode='recalculate',change=json.dumps(edit or change('rent',1))))
    def test_permissions_readonly_and_no_lazy_schema(self):
        path='/reports/financial-model/v2/'
        self.assertEqual(self.client().get(path).status_code,302)
        self.assertEqual(self.client(3).get(path).status_code,403)
        before=auth.DB_PATH.read_bytes()
        for uid in (1,2,4):
            self.assertEqual(self.client(uid).get(path).status_code,200)
            self.assertEqual(self.client(uid).get(path+'references').status_code,200)
        self.assertEqual(auth.DB_PATH.read_bytes(),before)
        self.assertEqual(self.preview(self.client(2)).status_code,403)
        self.assertEqual(self.preview(self.client(3)).status_code,403)
    def test_csrf_variant_tokens_actor_and_replay(self):
        c=self.client(1);before=auth.DB_PATH.read_bytes();response=self.preview(c)
        self.assertEqual(response.status_code,200,response.data)
        token=response.get_json()['token'];self.assertEqual(auth.DB_PATH.read_bytes(),before)
        path='/reports/financial-model/v2/save'
        self.assertEqual(c.post(path,data=dict(token=token)).status_code,400)
        self.assertEqual(self.client(4).post(path,data=dict(csrf='test',token=token)).status_code,403)
        self.assertEqual(c.post('/reports/financial-model/save',data=dict(csrf='test',token=token)).status_code,400)
        self.assertEqual(c.post(path,data=dict(csrf='test',token=token)).status_code,200)
        self.assertEqual(c.post(path,data=dict(csrf='test',token=token)).status_code,409)
        with sqlite3.connect(auth.DB_PATH) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM financial_model_history').fetchone()[0],0)
    def test_reference_update_between_preview_and_save_rolls_back(self):
        with sqlite3.connect(auth.DB_PATH) as conn:period(conn)
        c=self.client(1);source=c.get('/reports/financial-model/v2/references').get_json()['references'][0]
        edit=dict(group='payer_source',month=source['month'],digest=source['digest'],scope='future',**{'from':1})
        token=self.preview(c,edit).get_json()['token']
        with sqlite3.connect(auth.DB_PATH) as conn:period(conn,paid=4000)
        self.assertEqual(c.post('/reports/financial-model/v2/save',data=dict(csrf='test',token=token)).status_code,409)
        with sqlite3.connect(auth.DB_PATH) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM financial_model_v2_plan').fetchone()[0],0)
    def test_both_variants_navigable_and_missing_checks_explicit(self):
        c=self.client(1)
        for url in ('/reports/financial-model/','/reports/financial-model/v2/'):
            html=c.get(url).get_data(as_text=True)
            self.assertIn('id="modelVariant"',html)
            self.assertIn('Вариант 1',html);self.assertIn('Вариант 2',html)
        html=c.get('/reports/financial-model/v2/').get_data(as_text=True)
        self.assertIn('выбеление',html);self.assertIn('payer_checks',html)


if __name__=='__main__':
    unittest.main()
