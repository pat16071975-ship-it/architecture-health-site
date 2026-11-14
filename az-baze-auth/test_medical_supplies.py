import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from openpyxl import load_workbook
import medical_supplies as med
from test_accounting_section import AccountingSectionTests
import app as core


class SuppliesTests(AccountingSectionTests):
    def setUp(self):
        super().setUp()
        with sqlite3.connect(core.DB_PATH) as c:
            c.execute("INSERT INTO permissions VALUES(2,'medical_supplies_edit')")
        self.web = core.create_app()
        self.web.config['TESTING'] = True
        self.c = sqlite3.connect(core.DB_PATH)
        self.c.row_factory = sqlite3.Row
        self.c.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(self.c.close)

    def item(self, **changes):
        data = dict(name='Тестовый материал',unit='мл',opening_month='2026-11',opening_qty='10.125',
                    pack_qty='5',pack_price='',min_stock='12',excluded='0')
        data.update(changes)
        return med.save_item(self.c, data, 2)

    def period(self, iid, month='2026-11', qty_in='0', qty_out='0', version='0'):
        med.save_period(self.c, dict(item_id=str(iid),month=month,qty_in=qty_in,qty_out=qty_out,version=version), 2)

    def post(self, client, action, data):
        with client.session_transaction() as sess:
            sess['csrf']='test-token'
        return client.post('/accounting/medical-supplies/save/'+action, data={**data,'csrf':'test-token'})

    def test_empty_schema_and_reads_do_not_write(self):
        self.assertEqual(med.snapshot(self.c,'2026-11'),[])
        before=core.DB_PATH.read_bytes()
        client=self.client(2)
        for tab in ['movement','order','catalog','history']:
            res=client.get('/accounting/medical-supplies/?month=2026-11&tab='+tab)
            self.assertEqual(res.status_code,200)
        self.assertEqual(client.get('/accounting/medical-supplies/export?month=2026-11').status_code,200)
        self.assertEqual(core.DB_PATH.read_bytes(),before)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM med_supply_items').fetchone()[0],0)

    def test_decimal_rollover_and_period_replacement(self):
        iid=self.item()
        self.period(iid,qty_in='5.375',qty_out='3.5')
        nov=med.snapshot(self.c,'2026-11')[0]
        self.assertEqual((nov['start'],nov['end'],nov['complete']),(10125,12000,True))
        self.period(iid,'2026-12',qty_out='2')
        dec=med.snapshot(self.c,'2026-12')[0]
        self.assertEqual((dec['start'],dec['end']),(12000,10000))
        self.period(iid,qty_in='5.375',qty_out='1.5',version='1')
        self.assertEqual(med.snapshot(self.c,'2026-12')[0]['end'],12000)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM med_supply_periods').fetchone()[0],2)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM med_supply_history WHERE action='month_total'").fetchone()[0],3)

    def test_missing_month_is_not_zero_or_safe_order(self):
        iid=self.item()
        self.period(iid,'2026-12')
        r=med.snapshot(self.c,'2026-12')[0]
        self.assertFalse(r['complete']);self.assertIsNone(r['packs'])
        self.assertFalse(r['start_complete']);self.assertEqual(r['missing_months'],1)
        self.period(iid)
        r=med.snapshot(self.c,'2026-12')[0]
        self.assertTrue(r['complete']);self.assertEqual(r['packs'],1)

    def test_order_rounds_packages_and_keeps_unknown_price(self):
        iid=self.item(opening_qty='0',min_stock='11',pack_qty='5')
        self.period(iid)
        r=med.snapshot(self.c,'2026-11')[0]
        self.assertEqual(r['packs'],3);self.assertIsNone(r['order_cost'])
        row=dict(self.c.execute('SELECT * FROM med_supply_items').fetchone())
        med.save_item(self.c,dict(id=str(iid),version='1',name=row['name'],unit='мл',pack_qty='5',pack_price='0',min_stock='11'),2)
        self.assertEqual(med.snapshot(self.c,'2026-11')[0]['order_cost'],0)

    def test_future_period_not_in_selected_balance_and_unlimited_year(self):
        iid=self.item()
        self.period(iid)
        self.period(iid,'2028-01',qty_in='100')
        self.assertEqual(med.snapshot(self.c,'2026-11')[0]['end'],10125)
        self.assertEqual(med.snapshot(self.c,'2026-10'),[])
        self.assertEqual(med.month('2030-02'),'2030-02')

    def test_conflict_never_overwrites_saved_data(self):
        iid=self.item();self.period(iid,qty_out='1')
        before=self.c.execute('SELECT COUNT(*) FROM med_supply_history').fetchone()[0]
        with self.assertRaises(med.Conflict):self.period(iid,qty_out='9')
        self.assertEqual(med.snapshot(self.c,'2026-11')[0]['end'],9125)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM med_supply_history').fetchone()[0],before)

    def test_invalid_values_and_before_start_rejected(self):
        iid=self.item()
        for bad in ['',None,True,'-1','NaN','Infinity','1.0001','abc','1000000001','1e999999999']:
            with self.subTest(bad=bad),self.assertRaises(ValueError):med.number(bad)
        with self.assertRaises(ValueError):self.period(iid,'2026-10')
        with self.assertRaises(ValueError):self.period(99999)
        for bad in ['2026-13','2026-1','oops']:
            with self.assertRaises(ValueError):med.snapshot(self.c,bad)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM med_supply_periods').fetchone()[0],0)

    def test_history_is_atomic_with_write(self):
        iid=self.item()
        with patch.object(med,'history',side_effect=RuntimeError('failed audit')):
            with self.assertRaises(RuntimeError):self.period(iid)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM med_supply_periods').fetchone()[0],0)

    def test_view_edit_csrf_and_grants(self):
        iid=self.item()
        with self.c:
            self.c.execute("DELETE FROM permissions WHERE user_id=2 AND section='medical_supplies_edit'")
        reader=self.client(2)
        page=reader.get('/accounting/medical-supplies/').get_data(as_text=True)
        self.assertIn('Доступен просмотр',page);self.assertNotIn('id="period-',page)
        data=dict(month='2026-11',item_id=str(iid),qty_in='0',qty_out='0',version='0')
        self.assertEqual(self.post(reader,'period',data).status_code,403)
        admin=self.client(1)
        self.assertEqual(admin.post('/accounting/medical-supplies/save/period',data=data).status_code,400)
        self.assertEqual(self.post(admin,'period',data).status_code,303)
        self.assertEqual(self.post(admin,'period',data).status_code,409)
        for path in ['/accounting/medical-supplies/','/accounting/medical-supplies/export?month=2026-11']:
            self.assertEqual(self.client(3).get(path).status_code,403)
            self.assertEqual(self.client().get(path).status_code,302)
        with self.c:self.c.execute("INSERT INTO permissions VALUES(3,'medical_supplies_edit')")
        self.assertEqual(self.post(self.client(3),'period',data).status_code,403)

    def test_price_edits_do_not_change_quantities_opening_or_unit(self):
        iid=self.item();self.period(iid,qty_out='2')
        data=dict(id=str(iid),version='1',name='Новое имя',unit='мл',pack_qty='10',pack_price='100',min_stock='2',opening_qty='999',opening_month='2025-01')
        med.save_item(self.c,data,2)
        r=med.snapshot(self.c,'2026-11')[0]
        self.assertEqual((r['start'],r['end'],r['opening_month']),(10125,8125,'2026-11'))
        with self.assertRaises(ValueError):med.save_item(self.c,{**data,'version':'2','unit':'шт'},2)
        with self.assertRaises(med.Conflict):med.save_item(self.c,data,2)

    def test_negative_and_excluded_material_stays_in_history(self):
        iid=self.item(excluded='1');self.period(iid,qty_out='20')
        r=med.snapshot(self.c,'2026-11')[0]
        self.assertEqual(r['end'],-9875);self.assertIsNone(r['packs'])
        self.assertEqual(r['current']['qty_out'],20000)

    def test_excel_and_html_keep_names_as_text_and_export_both_flows(self):
        iid=self.item(name='=1+1<script>');self.period(iid,qty_in='2',qty_out='3')
        book=load_workbook(med.workbook(med.snapshot(self.c,'2026-11'),'2026-11'))
        self.assertEqual(book['Движение']['B2'].data_type,'s')
        self.assertEqual(book['Движение']['E2'].value,2)
        self.assertEqual(book['Движение']['F2'].value,3)
        page=self.client(2).get('/accounting/medical-supplies/?month=2026-11').get_data(as_text=True)
        self.assertIn('&lt;script&gt;',page);self.assertNotIn('=1+1<script>',page)

    def test_browser_forms_and_populated_tabs(self):
        client=self.client(2)
        data=dict(month='2026-11',name='Материал',unit='шт',opening_month='2026-11',opening_qty='10',pack_qty='2',pack_price='4.50',min_stock='12')
        self.assertEqual(self.post(client,'item',data).status_code,303)
        iid=self.c.execute('SELECT id FROM med_supply_items').fetchone()[0]
        self.assertEqual(self.post(client,'period',dict(month='2026-11',item_id=str(iid),qty_in='',qty_out='0',version='0')).status_code,400)
        self.period(iid)
        for tab in ['movement','order','catalog','history']:
            response=client.get('/accounting/medical-supplies/?month=2026-11&tab='+tab)
            self.assertEqual(response.status_code,200)
            html=response.get_data(as_text=True)
            self.assertIn('Материал',html)
            self.assertNotIn('Traceback',html)
        self.assertEqual(client.get(f'/accounting/medical-supplies/?month=2026-11&tab=catalog&edit={iid}').status_code,200)
        self.assertEqual(client.get('/accounting/medical-supplies/?month=2026-13').status_code,400)
        self.assertEqual(client.get('/accounting/medical-supplies/?tab=invalid').status_code,400)

    def test_init_schema_idempotent_without_seed(self):
        iid=self.item();self.period(iid)
        med.init_schema(self.c);med.init_schema(self.c)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM med_supply_items').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM med_supply_periods').fetchone()[0],1)


if __name__=='__main__':unittest.main()
