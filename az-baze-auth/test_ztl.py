import io
import json
import re
import sqlite3
import unittest
from unittest.mock import patch
from werkzeug.datastructures import FileStorage

from test_accounting_section import AccountingSectionTests
import app as core
import ztl
import medical_supplies as med
from test_accounting_section import AccountingSectionTests


class ZtlTests(unittest.TestCase):
    client = AccountingSectionTests.client
    def setUp(self):
        AccountingSectionTests.setUp(self)
        self.c=sqlite3.connect(core.DB_PATH)
        self.c.row_factory=sqlite3.Row
        self.c.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(self.c.close)
        self.clock=patch.object(ztl,'today',return_value='2026-11-16');self.clock.start();self.addCleanup(self.clock.stop)
        with self.c:
            self.party=ztl.catalog_entry(self.c,'party','Тестовый плательщик',1,'private')
            self.other=ztl.catalog_entry(self.c,'party','Другой плательщик',1,'legal')
            self.work=ztl.catalog_entry(self.c,'work','Работа',1)
            self.doctor=ztl.catalog_entry(self.c,'doctor','Врач Иванов',1)
        self.seq=0

    def order(self,**change):
        self.seq+=1
        d=dict(number=str(self.seq),invoice_date='2026-11-01',scope='external',party_id=str(self.party),patient='Тестовый пациент',
               work_id=str(self.work),doctor_id=str(self.doctor),amount='100',delay_days='30')
        d.update(change)
        return ztl.save_order(self.c,d,1)

    def values(self,**change):
        d=dict(party_id=str(self.party),scope='external',amount='100',paid_date='2026-11-15',method='bank',comment='Проверено по оплате',paid_patient='Тестовый пациент')
        d.update(change)
        return ztl.payment_values(self.c,d)

    def payment(self,**change):
        values=self.values(**change)
        return ztl.save_payment(self.c,values,(None,None,None),1,ztl.ledger_stamp(self.c),'token-'+str(ztl.ledger_stamp(self.c)))

    def grant(self,*rights):
        with self.c:
            self.c.execute("DELETE FROM permissions WHERE user_id=2 AND section!='accounting'")
            self.c.executemany('INSERT INTO permissions VALUES(2,?)',[(r,) for r in rights])

    def post(self,action,data=None,client=None):
        client=client or self.client(2)
        with client.session_transaction() as s:s['csrf']='token'
        return client.post('/accounting/ztl/save/'+action,data={**(data or {}),'csrf':'token'})

    def cost(self,kind,period='2026-11',**kw):
        ztl.save_cost(self.c,dict(month=period,amount='10',taxes='0',version='0',**kw),1,kind)

    def close_costs(self,period='2026-11'):
        self.cost('lab_costs',period);self.cost('clinic_costs',period)
        ztl.save_cost(self.c,dict(month=period,amount='0',taxes='0',version='0',employee_id='0'),1,'payroll')

    def test_fifo_partial_overpayment_and_no_duplicate_cash(self):
        a=self.order(amount='100',invoice_date='2026-10-01');b=self.order(amount='150');c=self.order(amount='200')
        p=self.payment(amount='300')
        self.assertEqual([o['paid'] for o in ztl.orders(self.c)],[10000,15000,5000])
        self.assertEqual(ztl.available(self.c,p),0)
        p2=self.payment(amount='200')
        self.assertEqual(ztl.available(self.c,p2),5000)
        self.order(amount='80')
        ztl.use_advance(self.c,p2,1,ztl.ledger_stamp(self.c))
        self.assertEqual(ztl.orders(self.c)[-1]['paid'],5000)
        self.assertEqual(ztl.report(self.c,'2026-11','2026-11')['cash'],50000)
        self.assertEqual(ztl.available(self.c,p2),0)

    def test_future_payment_updates_past_orders_not_past_cash(self):
        self.order(invoice_date='2026-10-01')
        self.payment(paid_date='2026-11-15')
        r=ztl.report(self.c,'2026-10','2026-10')
        self.assertEqual((r['paid'],r['debt'],r['cash']),(10000,0,0))

    def test_internal_only_lab_share_and_doctor_report_independent(self):
        self.order(scope='internal',amount='36500')
        self.order(scope='external',amount='90000')
        before=ztl.report(self.c,'2026-11','2026-11')['doctors']
        p=self.payment(scope='internal',amount='36500',service='Брекеты',service_total='100000')
        r=ztl.report(self.c,'2026-11','2026-11')
        self.assertEqual((r['cash'],r['paid']),(3650000,3650000))
        self.assertEqual((r['doctors'][0]['amount'],r['doctors'][0]['count']),(3650000,1))
        self.assertEqual(before[0]['amount'],r['doctors'][0]['amount'])
        self.assertEqual(ztl.row_by_id(self.c,'ztl_payments',p)['service_total'],10000000)

    def test_payment_scope_and_party_never_cross(self):
        self.order(scope='internal');self.order(party_id=str(self.other))
        self.payment(amount='300')
        self.assertEqual([o['paid'] for o in ztl.orders(self.c)],[0,0])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_allocations').fetchone()[0],0)

    def test_specific_order_does_not_pay_older_order(self):
        self.order();b=self.order()
        self.payment(amount='70',order_id=str(b))
        self.assertEqual([o['paid'] for o in ztl.orders(self.c)],[0,7000])
        with self.assertRaises(ValueError):self.values(order_id=str(b),party_id=str(self.other))

    def test_advance_before_any_order_can_be_used_without_cash_duplication(self):
        p=self.payment(amount='500')
        self.assertEqual(ztl.available(self.c,p),50000)
        self.order(amount='200')
        ztl.use_advance(self.c,p,1,ztl.ledger_stamp(self.c))
        self.assertEqual((ztl.available(self.c,p),ztl.report(self.c,'2026-11','2026-11')['cash']),(30000,50000))

    def test_preview_stale_or_replay_cannot_double_save(self):
        v=self.values();stamp=ztl.ledger_stamp(self.c)
        self.order()
        with self.assertRaises(med.Conflict):ztl.save_payment(self.c,v,(None,None,None),1,stamp,'x')
        ztl.save_payment(self.c,v,(None,None,None),1,ztl.ledger_stamp(self.c),'x')
        with self.assertRaises(med.Conflict):ztl.save_payment(self.c,v,(None,None,None),1,ztl.ledger_stamp(self.c),'x')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_payments').fetchone()[0],1)

    def test_payment_evidence_required_and_document_types_bounded(self):
        with self.assertRaises(ValueError):self.payment(comment='')
        v=self.values(comment='')
        proof=ztl.document(FileStorage(stream=io.BytesIO(b'%PDF-1.4 test'),filename='../proof.pdf'))
        p=ztl.save_payment(self.c,v,proof,1,ztl.ledger_stamp(self.c),'doc')
        self.assertEqual(ztl.row_by_id(self.c,'ztl_payments',p)['document_name'],'proof.pdf')
        for content in (b'<script>bad</script>',b'%PDF-'+b'x'*(1024*1024)):
            with self.assertRaises(ValueError):ztl.document(FileStorage(stream=io.BytesIO(content),filename='bad.pdf'))

    def test_internal_fields_validated(self):
        for delta in [dict(service='',service_total='100'),dict(service='Лечение',service_total='99'),dict(service='Лечение',service_total='NaN')]:
            with self.assertRaises(ValueError):self.values(scope='internal',**delta)
        for delta in [dict(amount='0'),dict(amount='-1'),dict(amount='NaN'),dict(method='x'),dict(paid_date='2026-12-01')]:
            with self.assertRaises(ValueError):self.values(**delta)

    def test_void_releases_allocations_and_retains_proof_history(self):
        self.order();p=self.payment(amount='150')
        ztl.void_payment(self.c,dict(id=str(p),version='1',reason='Неверная сумма'),1)
        self.assertEqual(ztl.orders(self.c)[0]['paid'],0)
        self.assertEqual(ztl.available(self.c,p),0)
        self.assertEqual(ztl.report(self.c,'2026-11','2026-11')['cash'],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_allocations').fetchone()[0],1)
        with self.assertRaises(med.Conflict):ztl.void_payment(self.c,dict(id=str(p),version='1',reason='Повтор'),1)

    def test_order_paid_amount_guard_and_created_date_preserved(self):
        oid=self.order(amount='100');self.payment(amount='60')
        old=ztl.row_by_id(self.c,'ztl_orders',oid)
        data={**old,'amount':'50','delay_days':'1','version':'1'}
        with self.assertRaises(ValueError):ztl.save_order(self.c,data,1)
        data.update(amount='100',invoice_date='2026-10-02',delay_days='10',created_date='1999-01-01')
        ztl.save_order(self.c,data,1)
        o=ztl.row_by_id(self.c,'ztl_orders',oid)
        self.assertEqual(o['created_date'],'2026-11-16')
        self.assertEqual((o['delay_days'],o['version']),(10,2))
        with self.assertRaises(med.Conflict):ztl.save_order(self.c,data,1)
        with self.assertRaises(ValueError):ztl.save_order(self.c,{**data,'version':'2','party_id':self.other},1)

    def test_history_failure_rolls_back_order_payment_cost(self):
        self.order()
        before=self.c.execute('SELECT COUNT(*) FROM ztl_orders').fetchone()[0]
        with patch.object(ztl,'log',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):self.order()
            with self.assertRaises(RuntimeError):self.payment()
            with self.assertRaises(RuntimeError):self.cost('lab_costs')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_orders').fetchone()[0],before)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_payments').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_allocations').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_costs').fetchone()[0],0)

    def test_month_costs_missing_zero_and_formula(self):
        self.order(amount='100');self.payment(amount='70')
        self.assertIsNone(ztl.report(self.c,'2026-11','2026-11')['actual'])
        self.close_costs()
        r=ztl.report(self.c,'2026-11','2026-11')
        self.assertEqual((r['theory'],r['debt'],r['actual']),(8000,3000,5000))
        self.assertAlmostEqual(r['margin'],5000/7000*100)
        self.assertFalse(ztl.report(self.c,'2026-10','2026-11')['complete'])
        self.close_costs('2026-10')
        self.assertTrue(ztl.report(self.c,'2026-10','2026-11')['complete'])

    def test_payroll_employee_taxes_and_month_completion(self):
        self.close_costs()
        ztl.save_cost(self.c,dict(month='2026-11',employee_new='Техник',amount='100',taxes='30',version='0'),1,'payroll')
        r=ztl.report(self.c,'2026-11','2026-11')
        self.assertEqual(r['payroll'],13000);self.assertFalse(r['complete'])
        ztl.save_cost(self.c,dict(month='2026-11',employee_id='0',amount='0',taxes='0',version='0'),1,'payroll')
        self.assertTrue(ztl.report(self.c,'2026-11','2026-11')['complete'])
        with self.assertRaises(ValueError):ztl.save_cost(self.c,dict(month='2026-11',employee_id='0',amount='100',taxes='0',version='1'),1,'payroll')

    def test_all_gets_do_not_write_and_escape_names(self):
        self.order(patient='<script>alert(1)</script>')
        client=self.client(1)
        count=ztl.ledger_stamp(self.c)
        for tab in ('orders','doctors','payments','costs','catalog','history'):
            response=client.get('/accounting/ztl/?start=2026-11&end=2026-11&tab='+tab)
            self.assertEqual(response.status_code,200,tab)
            self.assertNotIn('<script>alert(1)</script>',response.get_data(as_text=True))
        for path in ('order','payment','payment?party_id='+str(self.party)):
            self.assertEqual(client.get('/accounting/ztl/'+path).status_code,200)
        self.assertEqual(ztl.ledger_stamp(self.c),count)

    def test_granular_permission_matrix_and_section_gate(self):
        actions={'order':'orders','catalog':'catalog','payment':'payments','advance':'payments','void':'payments','payroll':'payroll','lab_costs':'lab_costs','clinic_costs':'clinic_costs'}
        for grant in ('',*ztl.RIGHTS):
            self.grant(*(['ztl_'+grant] if grant else []))
            for action,need in actions.items():
                response=self.post(action)
                if grant!=need:self.assertEqual(response.status_code,403,(grant,action))
                else:self.assertNotEqual(response.status_code,403,(grant,action))
        with self.c:self.c.execute("DELETE FROM permissions WHERE user_id=2 AND section='accounting'")
        self.assertEqual(self.post('clinic_costs').status_code,403)
        self.assertEqual(self.client(2).get('/accounting/ztl/').status_code,403)
        self.assertEqual(self.client().get('/accounting/ztl/').status_code,302)

    def test_csrf_preview_document_and_user_bound_confirmation(self):
        self.grant('ztl_payments')
        client=self.client(2)
        self.assertEqual(client.post('/accounting/ztl/payment-preview',data={}).status_code,400)
        with client.session_transaction() as s:s['csrf']='token'
        data=dict(party_id=str(self.party),scope='external',amount='100',paid_date='2026-11-15',method='cash',csrf='token')
        preview=client.post('/accounting/ztl/payment-preview',data=data)
        self.assertEqual(preview.status_code,200)
        import html
        signed=html.unescape(re.search(r'name="signed" value="([^"]+)"',preview.get_data(as_text=True))[1])
        self.assertEqual(self.post('payment',dict(signed=signed,comment='Подтверждено'),client).status_code,303)
        self.assertEqual(self.post('payment',dict(signed=signed,comment='Дубль'),client).status_code,409)
        self.assertEqual(self.client(3).get('/accounting/ztl/document/1').status_code,403)
        self.assertEqual(self.post('payment',dict(signed=signed+'x',comment='bad'),client).status_code,400)

    def test_catalog_inline_permission_and_dedup(self):
        with self.assertRaises(PermissionError):self.order(work_id_new='Новая работа')
        count=self.c.execute('SELECT COUNT(*) FROM ztl_names').fetchone()[0]
        self.assertEqual(count,2)
        with self.c:self.assertEqual(ztl.catalog_entry(self.c,'work','Работа',1),self.work)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_names').fetchone()[0],count)

    def test_cost_edit_conflict(self):
        self.cost('lab_costs')
        with self.assertRaises(med.Conflict):self.cost('lab_costs')
        self.assertEqual(self.c.execute('SELECT amount FROM ztl_costs').fetchone()[0],1000)


    def test_doc_download_and_multipart_real_route(self):
        self.grant('ztl_payments')
        client=self.client(2)
        with client.session_transaction() as sess:sess['csrf']='token'
        preview=client.post('/accounting/ztl/payment-preview',data=dict(csrf='token',party_id=self.party,scope='external',amount='50',paid_date='2026-11-15',method='bank'))
        import html
        signed=html.unescape(re.search(r'name="signed" value="([^"]+)"',preview.get_data(as_text=True))[1])
        response=client.post('/accounting/ztl/save/payment',data=dict(csrf='token',signed=signed,document=(io.BytesIO(b'%PDF-1.4 proof'),'proof.pdf')),content_type='multipart/form-data')
        self.assertEqual(response.status_code,303)
        pid=self.c.execute('SELECT id FROM ztl_payments').fetchone()[0]
        self.grant()  # reader may view documents but cannot change them
        response=client.get('/accounting/ztl/document/'+str(pid))
        self.assertEqual(response.data,b'%PDF-1.4 proof')
        self.assertIn('attachment',response.headers['Content-Disposition'])
        self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
        self.assertIn('no-store',response.headers['Cache-Control'])

    def test_financial_tables_untouched(self):
        # Sentinel registers use the same connection to catch unintended side effects.
        with self.c:
            self.c.execute('CREATE TABLE IF NOT EXISTS ztl_test_finance_sentinel(k TEXT PRIMARY KEY,v TEXT)')
            self.c.execute("INSERT INTO ztl_test_finance_sentinel VALUES('baseline','unchanged')")
        protected=[r[0] for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='table'") if not r[0].startswith('ztl_')]
        before={name:[tuple(r) for r in self.c.execute('SELECT * FROM "'+name+'"')] for name in protected}
        self.order();p=self.payment(amount='120');self.close_costs()
        self.order();ztl.use_advance(self.c,p,1,ztl.ledger_stamp(self.c))
        ztl.void_payment(self.c,dict(id=p,version='1',reason='Исправление'),1)
        self.assertEqual(before,{name:[tuple(r) for r in self.c.execute('SELECT * FROM "'+name+'"')] for name in protected})
        self.assertEqual(self.c.execute('SELECT v FROM ztl_test_finance_sentinel').fetchone()[0],'unchanged')

    def test_actual_order_and_cost_write_routes_and_catalog_rename(self):
        self.grant('ztl_catalog')
        self.assertEqual(self.post('catalog',dict(kind='doctor',id=self.doctor,version='1',name='Врач Петров')).status_code,303)
        self.assertEqual(ztl.row_by_id(self.c,'ztl_names',self.doctor)['name'],'Врач Петров')
        self.grant('ztl_orders')
        data=dict(number='42',invoice_date='2026-11-01',scope='internal',party_id=self.party,patient='Пациент',doctor_id=self.doctor,work_id=self.work,amount='36500',delay_days='15')
        self.assertEqual(self.post('order',data).status_code,303)
        self.assertEqual(self.post('order',data).status_code,409)
        self.grant('ztl_payroll')
        self.assertEqual(self.post('payroll',dict(month='2026-11',employee_new='Техник',amount='200',taxes='50',version='0')).status_code,303)
        self.assertEqual(self.post('lab_costs',dict(month='2026-11',amount='10',version='0')).status_code,403)

    def test_creation_transaction_rolls_back_inline_dictionary(self):
        self.seq+=1
        data=dict(number='new',invoice_date='2026-11-01',scope='internal',party_id=self.party,patient='Пациент',doctor_id=self.doctor,work_id_new='Несохранённая работа',amount='-1',delay_days='0')
        with self.assertRaises(ValueError):ztl.save_order(self.c,data,1,can_catalog=True)
        self.assertIsNone(self.c.execute("SELECT id FROM ztl_names WHERE name='Несохранённая работа'").fetchone())

    def test_no_orders_zero_costs_no_margin_and_report_filters(self):
        self.close_costs()
        r=ztl.report(self.c,'2026-11','2026-11')
        self.assertEqual(r['actual'],-2000);self.assertIsNone(r['margin'])
        self.order(scope='internal',patient='Только внутренний')
        self.order(scope='external',party_id=self.other,patient='Только внешний')
        client=self.client(1)
        page=client.get('/accounting/ztl/?start=2026-11&end=2026-11&mode=legal').get_data(as_text=True)
        self.assertIn('Только внешний',page);self.assertNotIn('Только внутренний',page)
        self.payment(scope='internal',paid_patient='Только внутренний',service='Лечение',service_total='500')
        page=client.get('/accounting/ztl/?start=2026-11&end=2026-11&unpaid=1').get_data(as_text=True)
        self.assertIn('Только внешний',page);self.assertNotIn('Только внутренний',page)


# Separate base fixtures without importing all ZTL test methods.
class SuppliesPermissionsTests(unittest.TestCase):
    client = AccountingSectionTests.client
    def setUp(self):
        AccountingSectionTests.setUp(self)
        self.c=sqlite3.connect(core.DB_PATH);self.c.row_factory=sqlite3.Row;self.addCleanup(self.c.close)
        self.item=med.save_item(self.c,dict(name='Материал',unit='шт',pack_qty='5',pack_price='10',min_stock='2',opening_month='2026-11',opening_qty='10'),1)

    def grant(self,*keys):
        with self.c:
            self.c.execute("DELETE FROM permissions WHERE user_id=2 AND section!='accounting'")
            self.c.executemany('INSERT INTO permissions VALUES(2,?)',[(k,) for k in keys])

    def post(self,action,data):
        client=self.client(2)
        with client.session_transaction() as s:s['csrf']='token'
        return client.post('/accounting/medical-supplies/save/'+action,data={**data,'csrf':'token'})

    def test_in_only_cannot_write_out_and_missing_is_not_zero(self):
        self.grant('medical_supplies_in')
        data=dict(month='2026-11',item_id=self.item,qty_in='3',qty_out='999',version='0')
        self.assertEqual(self.post('period',data).status_code,303)
        r=med.snapshot(self.c,'2026-11')[0]
        self.assertFalse(r['complete']);self.assertEqual(r['end'],13000);self.assertFalse(r['current']['out_known'])
        self.grant('medical_supplies_out')
        self.assertEqual(self.post('period',{**data,'qty_in':'999','qty_out':'2','version':'1'}).status_code,303)
        r=med.snapshot(self.c,'2026-11')[0]
        self.assertTrue(r['complete']);self.assertEqual(r['end'],11000)
        self.assertEqual(r['current']['qty_in'],3000)

    def test_catalog_cannot_change_purchase_and_purchase_cannot_change_name(self):
        data=dict(id=self.item,version='1',month='2026-11',name='Новое имя',unit='шт',pack_qty='99',pack_price='900',min_stock='99',excluded='1')
        self.grant('medical_supplies_catalog')
        self.assertEqual(self.post('item',data).status_code,303)
        r=med.snapshot(self.c,'2026-11')[0]
        self.assertEqual((r['name'],r['pack_qty'],r['pack_price']),('Новое имя',5000,1000))
        self.grant('medical_supplies_purchase')
        self.assertEqual(self.post('item',{**data,'name':'Запрещённое имя','version':'2'}).status_code,303)
        r=med.snapshot(self.c,'2026-11')[0]
        self.assertEqual((r['name'],r['pack_qty'],r['pack_price']),('Новое имя',99000,90000))
        self.assertEqual(self.post('item',dict(month='2026-11',name='Новый материал')).status_code,403)

    def test_legacy_full_edit_and_partial_ui(self):
        self.grant('medical_supplies_in')
        page=self.client(2).get('/accounting/medical-supplies/?month=2026-11').get_data(as_text=True)
        self.assertIn('name="qty_in"',page);self.assertNotIn('name="qty_out"',page)
        self.grant('medical_supplies_edit')
        page=self.client(2).get('/accounting/medical-supplies/?month=2026-11').get_data(as_text=True)
        self.assertIn('name="qty_in"',page);self.assertIn('name="qty_out"',page)

    def test_legacy_rows_without_coverage_stay_complete(self):
        with self.c:self.c.execute('INSERT INTO med_supply_periods VALUES(?,?,?,?,?)',(self.item,'2026-11',0,0,1))
        self.assertTrue(med.snapshot(self.c,'2026-11')[0]['complete'])


if __name__=='__main__':unittest.main()
