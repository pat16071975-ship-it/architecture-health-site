import json
import unittest
from unittest.mock import patch
from werkzeug.datastructures import MultiDict

import test_ztl_control as fixtures
import ztl
import ztl_control as control
import ztl_labor as labor


class LaborTests(unittest.TestCase):
    client = fixtures.ControlTests.client
    grant = fixtures.ControlTests.grant
    post = fixtures.ControlTests.post
    post_order = fixtures.ControlTests.post_order
    priced_order_data = fixtures.ControlTests.priced_order_data

    def setUp(self):
        fixtures.ControlTests.setUp(self)
        with self.c:
            self.c.execute('UPDATE ztl_cards SET technician_fee=600000 WHERE work_id=?', (self.work,))
            self.second = ztl.catalog_entry(self.c, 'employee', 'Техник Сидоров', 1)
            self.third = ztl.catalog_entry(self.c, 'employee', 'Техник Андреев', 1)
        self.ids = [self.technician, self.second, self.third]
        self.grant('ztl_orders', 'ztl_payroll')

    def snapshot(self):
        return '\n'.join(self.c.iterdump())

    def distribution(self, rules):
        data = dict(labor_present='1')
        for tid, mode, value in rules:
            data['labor_mode_' + str(tid)] = mode
            data['labor_value_' + str(tid)] = str(value)
        return data

    def create(self, rules=None, ids=None, **changes):
        d = self.priced_order_data(**dict(dict(technician_id=ids or self.ids, completed_on='2026-11-15', amount='24000'), **changes))
        if rules is not None:
            d.update(self.distribution(rules))
        response = self.post_order(d)
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        return self.c.execute('SELECT MAX(id) FROM ztl_orders').fetchone()[0]

    def state(self, oid):
        return labor.allocation(self.c, control.production(self.c, oid))

    def edit(self, oid, **changes):
        o = ztl.row_by_id(self.c, 'ztl_orders', oid)
        data = self.priced_order_data(number=o['number'], id=oid, version=o['version'], technician_id=self.ids, amount='24000', completed_on='2026-11-15')
        data.update(changes)
        return data

    def save_split(self, oid, rules, version=None):
        return self.post('labor', dict(order_id=oid, version=version or ztl.row_by_id(self.c, 'ztl_orders', oid)['version'], **self.distribution(rules)))

    def test_mixed_three_techs_uses_labor_not_customer_bill_and_filters_personal_share(self):
        oid = self.create([(self.technician, 'percent', 50), (self.second, 'rub', 2000), (self.third, 'rub', 1000)])
        state = self.state(oid)
        self.assertEqual((state['total'], state['assigned'], state['remaining'], state['complete']), (600000, 600000, 0, True))
        for tid, amount in zip(self.ids, [300000, 200000, 100000]):
            rows = control.payroll_orders(self.c, '2026-11', '2026-11', tid)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['labor']['selected_amount'], amount)
        self.assertEqual(sum(o['labor']['selected_amount'] for o in control.payroll_orders(self.c, '2026-11', '2026-11')), 600000)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_costs').fetchone()[0], 0)
        self.assertEqual(ztl.report(self.c, '2026-11', '2026-11')['payroll'], 0)

    def test_single_two_and_five_technicians(self):
        for count in (1, 2, 5):
            with self.subTest(count=count):
                ids=self.ids[:count]
                if count==5:
                    with self.c:
                        ids += [ztl.catalog_entry(self.c,'employee','Дополнительный '+str(i),1) for i in range(2)]
                oid=self.create([(i,'percent',100//count) for i in ids],ids=ids,number='COUNT-'+str(count))
                state=self.state(oid)
                self.assertEqual(state['assigned'],600000)
                self.assertTrue(state['complete'])

    def test_legacy_single_defaults_to_full_and_shared_remains_unallocated_without_writes(self):
        single=self.create(ids=[self.technician], number='SINGLE')
        shared=self.create(number='SHARED')
        before=self.snapshot()
        self.assertEqual(self.state(single)['assigned'],600000)
        self.assertTrue(self.state(single)['complete'])
        self.assertEqual(self.state(shared)['assigned'],0)
        self.assertFalse(self.state(shared)['complete'])
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_labor_splits').fetchone()[0],0)

    def test_partial_blank_and_explicit_zero_are_distinct(self):
        oid=self.create([(self.technician,'percent',50),(self.second,'rub',0)])
        state=self.state(oid)
        self.assertEqual((state['assigned'],state['remaining'],state['complete']),(300000,300000,False))
        self.assertEqual(len(state['entries']),2)
        self.assertTrue(control.payroll_orders(self.c,'2026-11','2026-11',self.second)[0]['labor']['selected_known'])
        self.assertFalse(control.payroll_orders(self.c,'2026-11','2026-11',self.third)[0]['labor']['selected_known'])

    def test_zero_total_still_requires_explicit_decisions_for_each_technician(self):
        with self.c:self.c.execute('UPDATE ztl_cards SET technician_fee=0 WHERE work_id=?',(self.work,))
        oid=self.create([(self.technician,'rub',0)])
        self.assertFalse(self.state(oid)['complete'])
        self.assertEqual(self.save_split(oid,[(i,'rub',0) for i in self.ids]).status_code,303)
        self.assertTrue(self.state(oid)['complete'])

    def test_kopecks_apportioned_deterministically_without_excess_or_loss(self):
        for total in (1,2,3,99,101,10001,987654321):
            for values in ((5000,5000),(3333,3333,3334),(1700,3300,5000)):
                rules=[dict(technician_id=i+1,mode='percent',value=v) for i,v in enumerate(values)]
                entries=labor.calculate(total,rules)
                self.assertEqual(sum(e['amount'] for e in entries),total)
                self.assertEqual(entries,labor.calculate(total,list(reversed(rules))))
        self.assertEqual([e['amount'] for e in labor.calculate(1,[dict(technician_id=i,mode='percent',value=5000) for i in (2,1)])],[1,0])

    def test_invalid_and_excessive_allocations_roll_back_entire_order(self):
        for rules in ([(self.technician,'percent',101)],[(self.technician,'rub',6000.01)],
                      [(self.technician,'percent',60),(self.second,'percent',50)],
                      [(self.technician,'rub',4000),(self.second,'percent',50)],
                      [(self.technician,'rub',-1)],[(self.technician,'rub','NaN')],
                      [(self.technician,'percent','0.001')],[(self.technician,'other',50)]):
            before=self.snapshot()
            r=self.post_order(self.priced_order_data(technician_id=self.ids,**self.distribution(rules)))
            self.assertEqual(r.status_code,400,r.get_data(as_text=True))
            self.assertEqual(self.snapshot(),before)

    def test_no_overallocation_hidden_by_rounding(self):
        with self.assertRaises(ValueError):
            labor.calculate(1,[dict(technician_id=1,mode='rub',value=1),dict(technician_id=2,mode='percent',value=1)])

    def test_unselected_and_repeated_fields_rejected(self):
        before=self.snapshot()
        data=self.priced_order_data(technician_id=[self.technician],**self.distribution([(self.second,'rub',1)]))
        self.assertEqual(self.post_order(data).status_code,400)
        self.assertEqual(self.snapshot(),before)
        data=MultiDict(self.priced_order_data(technician_id=[self.technician],**self.distribution([(self.technician,'rub',1)])))
        data.add('labor_value_'+str(self.technician),'6000')
        client=self.client(2)
        with client.session_transaction() as s:s['csrf']='token'
        data['csrf']='token'
        self.assertEqual(client.post('/accounting/ztl/save/order',data=data).status_code,400)
        self.assertEqual(self.snapshot(),before)

    def test_orders_right_does_not_grant_payroll_and_csrf_is_enforced(self):
        oid=self.create()
        self.grant('ztl_orders')
        before=self.snapshot()
        self.assertEqual(self.post_order(self.edit(oid,**self.distribution([(self.technician,'percent',100)]))).status_code,403)
        self.assertEqual(self.save_split(oid,[(self.technician,'percent',100)]).status_code,403)
        self.assertNotIn('name="labor_present"',self.client(2).get('/accounting/ztl/order?id='+str(oid)).get_data(as_text=True))
        self.grant('ztl_payroll')
        before=self.snapshot()
        self.assertEqual(self.client(2).post('/accounting/ztl/control/labor',data=dict(order_id=oid,version=1,labor_present=1)).status_code,400)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.save_split(oid,[(i,'percent',v) for i,v in zip(self.ids,[50,25,25])]).status_code,303)

    def test_stale_forms_in_both_directions_cannot_overwrite(self):
        oid=self.create()
        old_edit=self.edit(oid)
        self.assertEqual(self.save_split(oid,[(self.technician,'percent',100)]).status_code,303)
        before=self.snapshot()
        self.assertEqual(self.post_order(old_edit).status_code,409)
        self.assertEqual(self.snapshot(),before)
        version=ztl.row_by_id(self.c,'ztl_orders',oid)['version']
        self.assertEqual(self.post_order(self.edit(oid,description='Без изменения оплаты')).status_code,303)
        before=self.snapshot()
        self.assertEqual(self.save_split(oid,[],version).status_code,409)
        self.assertEqual(self.snapshot(),before)

    def test_legacy_form_cannot_change_technicians_or_quantity_behind_saved_split(self):
        oid=self.create([(self.technician,'percent',100)],completed_on='')
        for change in (dict(technician_id=[self.second]),dict(pieces='2')):
            before=self.snapshot()
            self.assertEqual(self.post_order(self.edit(oid,completed_on='',**change)).status_code,400)
            self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.post_order(self.edit(oid,completed_on='',pieces=2,**self.distribution([(self.technician,'percent',50),(self.second,'rub',6000),(self.third,'rub',0)]))).status_code,303)
        self.assertEqual((self.state(oid)['total'],self.state(oid)['assigned']),(1200000,1200000))

    def test_snapshot_fee_and_payments_do_not_reprice_allocations(self):
        oid=self.create([(self.technician,'percent',100)])
        with self.c:self.c.execute('UPDATE ztl_cards SET technician_fee=999999,version=version+1 WHERE work_id=?',(self.work,))
        before=self.state(oid)
        self.assertEqual(self.post_order(self.edit(oid,price_version='',amount='25000')).status_code,303)
        self.assertEqual(self.state(oid),before)

    def test_completion_protections_preserved_with_allocation_fields(self):
        oid=self.create([(self.technician,'percent',100)])
        for change in (dict(completed_on=''),dict(pieces=2)):
            before=self.snapshot()
            self.assertEqual(self.post_order(self.edit(oid,**change,**self.distribution([(self.technician,'percent',100)]))).status_code,400)
            self.assertEqual(self.snapshot(),before)

    def test_audit_failure_rolls_back_orders_stock_and_splits(self):
        item=control.save_item(self.c,dict(name='Материал',unit='м',kind='material'),1)
        fixtures.ControlTests.receipt(self,item)
        control.save_card(self.c,dict(work_id=self.work,version=1,technician_fee='6000',**{'qty_'+str(item):'1'}),1)
        data=self.priced_order_data(price_version=2,technician_id=self.ids,**self.distribution([(self.technician,'percent',100)]))
        before=self.snapshot();original=ztl.log
        def fail(*args,**kwargs):
            if args[1]=='Распределение оплаты ЗТЛ':raise RuntimeError('audit fail')
            return original(*args,**kwargs)
        with patch.object(ztl,'log',side_effect=fail),self.assertRaisesRegex(RuntimeError,'audit fail'):
            ztl.save_order(self.c,data,1,can_payroll=True)
        self.assertEqual(self.snapshot(),before)
        oid=self.create(price_version=2)
        before=self.snapshot()
        with patch.object(ztl,'log',side_effect=fail),self.assertRaisesRegex(RuntimeError,'audit fail'):
            labor.save_allocation(self.c,dict(order_id=oid,version=1,**self.distribution([(self.technician,'percent',100)])),1)
        self.assertEqual(self.snapshot(),before)

    def test_historical_unknown_fee_not_replaced_by_current_card(self):
        oid=self.create()
        with self.c:self.c.execute('UPDATE ztl_production SET card_version=0,technician_fee=0 WHERE order_id=?',(oid,))
        before=self.snapshot()
        self.assertIsNone(self.state(oid)['total'])
        self.assertEqual(self.save_split(oid,[(self.technician,'percent',100)]).status_code,400)
        self.assertEqual(self.snapshot(),before)

    def test_readonly_pages_show_all_five_seeded_technicians_and_do_not_write(self):
        seeded=labor.seed_technicians(self.c,1)
        oid=self.create([(self.technician,'percent',100)])
        before=self.snapshot()
        for url in ('/accounting/ztl/order','/accounting/ztl/orders/','/accounting/ztl/payroll/','/accounting/ztl/catalog/'):
            response=self.client(1).get(url)
            self.assertEqual(response.status_code,200,response.get_data(as_text=True))
            for tech in seeded:self.assertIn(tech['name'],response.get_data(as_text=True))
        self.assertEqual(self.snapshot(),before)

    def test_seed_is_explicit_idempotent_preserves_old_records_and_aliases(self):
        count=self.c.execute("SELECT COUNT(*) FROM ztl_names WHERE kind='employee'").fetchone()[0]
        labor.init_schema(self.c);self.c.commit()
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM ztl_names WHERE kind='employee'").fetchone()[0],count)
        old=list(self.c.execute('SELECT * FROM ztl_names'))
        seeded=labor.seed_technicians(self.c,1)
        self.assertEqual(sum(r['created'] for r in seeded),5)
        before=self.snapshot()
        self.assertEqual(sum(r['created'] for r in labor.seed_technicians(self.c,1)),0)
        self.assertEqual(self.snapshot(),before)
        for row in old:self.assertEqual(tuple(self.c.execute('SELECT * FROM ztl_names WHERE id=?',(row['id'],)).fetchone()),tuple(row))

    def test_seed_rolls_back_all_new_names_if_log_fails(self):
        before=self.snapshot();original=ztl.log
        def fail(*args,**kwargs):
            if args[1]=='ztl_names' and args[6].get('name')==labor.TECHNICIANS[2]:raise RuntimeError('audit fail')
            return original(*args,**kwargs)
        with patch.object(ztl,'log',side_effect=fail),self.assertRaisesRegex(RuntimeError,'audit fail'):
            labor.seed_technicians(self.c,1)
        self.assertEqual(self.snapshot(),before)

    def test_seed_detects_abbreviation_without_merging_or_duplicates(self):
        with self.c:ztl.catalog_entry(self.c,'employee','Палунин В. А.',1)
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'Палунин'):
            labor.seed_technicians(self.c,1)
        self.assertEqual(self.snapshot(),before)


if __name__=='__main__':unittest.main()
