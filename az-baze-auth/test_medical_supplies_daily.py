import io
import json
import sqlite3
import unittest
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch
from openpyxl import load_workbook
from test_medical_supplies import SuppliesTests
from test_medical_supplies_import import fixture
import medical_supplies as med
import medical_supplies_import as stock
import medical_supplies_daily as daily


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.case=SuppliesTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.c,self.web=self.case.c,self.case.web
        self.client,self.item,self.period=self.case.client,self.case.item,self.case.period
        self.clock=patch.object(daily,'today',return_value=date(2026,12,31))
        self.clock.start();self.addCleanup(self.clock.stop)
        self.import_id,_=stock.save_import(self.c,fixture(),'stock.xlsx','2026-11-19',2)
        self.root=self.c.execute("SELECT id FROM med_supply_folders WHERE name='Аптека'").fetchone()[0]
        self.sub=self.c.execute("SELECT id FROM med_supply_folders WHERE name='Анестезия'").fetchone()[0]

    def classify(self,row=4,**changes):
        data=dict(import_id=str(self.import_id),source_row=str(row),folder=str(self.root),subfolder=str(self.sub),version='0')
        data.update(changes)
        return daily.assign(self.c,data,2)

    def move(self,iid,day='2026-11-20',qty='10',kind='out',value='',version='0',allowed=None):
        daily.save_day(self.c,dict(item_id=str(iid),day=day,qty=qty,kind=kind,value=value,version=version),2,allowed or {'in','out'})

    def http(self,action,data,user=2,csrf=True):
        c=self.client(user)
        with c.session_transaction() as s:s['csrf']='t'
        return c.post('/accounting/medical-supplies/daily/'+action,data={**data,'month':'2026-11',**({'csrf':'t'} if csrf else {})})

    def test_classify_enters_catalog_once_and_keeps_source(self):
        source=tuple(self.c.execute('SELECT * FROM med_supply_stock_imports').fetchone())
        iid=self.classify()
        item=self.c.execute('SELECT * FROM med_supply_items WHERE id=?',(iid,)).fetchone()
        self.assertEqual(item['name'],'Материал А')
        r=daily.snapshot(self.c,'2026-11')[0]
        self.assertEqual((r['end'],r['end_value'],r['piece_price']),(D(100),D(14645),D('146.45')))
        self.assertIsNone(r['start']);self.assertTrue(r['history_gap'])
        self.assertEqual(source,tuple(self.c.execute('SELECT * FROM med_supply_stock_imports').fetchone()))
        self.assertEqual(med.snapshot(self.c,'2026-11'),[])
        with self.assertRaises(daily.Conflict):self.classify()
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM med_supply_items').fetchone()[0],1)

    def test_no_auto_assignment_and_zero_both_sorts_bottom(self):
        iid,_=stock.save_import(self.c,fixture([('Цена0',5,'шт',0),('Есть',2,'шт',10),('Остаток0',0,'шт',0),('Ещё',1,'шт',5)]),'other.xlsx','2026-11-19',2)
        rows=[r for r in daily.import_rows(self.c) if r['import_id']==iid]
        self.assertEqual([r['name'] for r in rows],['Есть','Ещё','Цена0','Остаток0'])
        self.assertTrue(all(r['assigned'] is None for r in rows))
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM med_supply_items').fetchone()[0],0)

    def test_parent_child_validation_and_reclassification_preserves_stock(self):
        wrong=self.c.execute("SELECT id FROM med_supply_folders WHERE name='Терапия'").fetchone()[0]
        before='\n'.join(self.c.iterdump())
        with self.assertRaisesRegex(ValueError,'не относится'):self.classify(subfolder=str(wrong))
        self.assertEqual(before,'\n'.join(self.c.iterdump()))
        iid=self.classify();self.move(iid)
        root=self.c.execute("SELECT id FROM med_supply_folders WHERE name='Расходные материалы'").fetchone()[0]
        old=daily.snapshot(self.c,'2026-11')[0]
        self.classify(folder=str(root),subfolder=str(wrong),version='1')
        new=daily.snapshot(self.c,'2026-11')[0]
        self.assertEqual((old['end'],old['end_value']),(new['end'],new['end_value']))
        self.assertEqual(new['folder_id'],wrong);self.assertEqual(new['version'],2)

    def test_source_date_not_first_of_month_or_incoming(self):
        iid=self.classify()
        for day in ('2026-11-01','2026-11-19'):
            with self.assertRaisesRegex(ValueError,'следующего'):self.move(iid,day)
        r=daily.snapshot(self.c,'2026-11')[0]
        self.assertEqual(r['in_total'],0);self.assertIsNone(r['start'])
        self.move(iid)
        r=daily.snapshot(self.c,'2026-12')[0]
        self.assertEqual(r['start'],90);self.assertFalse(r['history_gap'])

    def test_spending_balance_and_replacing_not_adding(self):
        iid=self.classify();self.move(iid)
        r=daily.snapshot(self.c,'2026-11')[0]
        self.assertEqual((r['spent'],r['end'],r['end_value']),(D('1464.50'),D(90),D('13180.50')))
        self.move(iid,qty='2',version='1')
        r=daily.snapshot(self.c,'2026-11')[0]
        self.assertEqual((r['spent'],r['end']),(D('292.90'),D(98)))
        with self.assertRaises(daily.Conflict):self.move(iid,qty='9',version='1')

    def test_income_cost_chronological_and_same_day_receipt_before_out(self):
        iid=self.classify()
        self.move(iid,qty='100',kind='in',value='20000')
        self.move(iid,qty='10',version='1')
        r=daily.snapshot(self.c,'2026-11')[0]
        self.assertEqual(r['spent'],D('1732.25'))
        self.assertEqual(r['end_value'],D('32912.75'))
        self.assertEqual(r['end'],190)

    def test_unknown_receipt_price_does_not_become_zero(self):
        iid=self.classify();self.move(iid,qty='10',kind='in')
        self.move(iid,qty='2',version='1')
        r=daily.snapshot(self.c,'2026-11')[0]
        self.assertIsNone(r['end_value']);self.assertIsNone(r['spent']);self.assertEqual(r['end'],108)
        self.move(iid,qty='10',kind='in',value='1000',version='2')
        self.assertIsNotNone(daily.snapshot(self.c,'2026-11')[0]['spent'])

    def test_future_day_and_future_month_no_fabricated_stock(self):
        iid=self.classify()
        with self.assertRaises(ValueError):self.move(iid,day='2027-01-01')
        r=daily.snapshot(self.c,'2027-01')[0]
        self.assertIsNone(r['end']);self.assertIsNone(r['spent'])
        self.assertIsNone(daily.snapshot(self.c,'2026-10')[0]['end'])

    def test_negative_stock_not_clamped(self):
        iid=self.classify();self.move(iid,qty='101')
        r=daily.snapshot(self.c,'2026-11')[0]
        self.assertEqual(r['end'],-1);self.assertIsNone(r['end_value'])

    def test_unknown_pack_piece_price_and_manual_conversion(self):
        iid=self.classify(row=5)
        self.assertIsNone(daily.snapshot(self.c,'2026-11')[0]['piece_price'])
        daily.save_pieces(self.c,dict(item_id=str(iid),version='1',pieces='10'),2)
        r=daily.snapshot(self.c,'2026-11')[0]
        self.assertEqual(r['piece_price'],5)
        self.assertEqual((r['end'],r['end_value']),(2,100))
        with self.assertRaises(daily.Conflict):daily.save_pieces(self.c,dict(item_id=str(iid),version='1',pieces='20'),2)

    def test_root_and_subfolder_totals_not_double_counted(self):
        a=self.classify();b=self.classify(row=5,subfolder='')
        self.move(a,qty='1');self.move(b,qty='1')
        rows=daily.snapshot(self.c,'2026-11');tree=daily.tree(self.c,rows)
        root=next(r for r in tree if r['id']==self.root)
        self.assertEqual(root['total']['spent'],D('196.45'))
        self.assertEqual(root['total']['end_value'],D('14548.55'))
        self.assertEqual(daily.totals(rows),root['total'])

    def test_no_monthly_double_count_or_destroyed_legacy(self):
        old=self.item();self.period(old,qty_in='10',qty_out='2')
        baseline=med.snapshot(self.c,'2026-11')
        iid=self.classify();self.move(iid)
        self.assertEqual(med.snapshot(self.c,'2026-11'),baseline)
        with self.assertRaisesRegex(ValueError,'по дням'):self.period(iid,qty_in='100')
        page=self.client(2).get('/accounting/medical-supplies/?month=2026-11').get_data(as_text=True)
        self.assertIn('Ранее внесённые месячные итоги',page)
        self.assertEqual(self.client(2).get('/accounting/medical-supplies/?tab=monthly&month=2026-11').status_code,200)

    def test_permissions_csrf_and_write_rollback(self):
        data=dict(import_id=str(self.import_id),source_row='4',folder=str(self.root),subfolder=str(self.sub),version='0')
        self.assertEqual(self.http('assign',data,user=3).status_code,403)
        self.assertEqual(self.http('assign',data,csrf=False).status_code,400)
        before='\n'.join(self.c.iterdump())
        with patch.object(med,'history',side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):self.classify()
        self.assertEqual(before,'\n'.join(self.c.iterdump()))
        response=self.http('assign',data);self.assertEqual(response.status_code,303)
        self.assertEqual(self.http('assign',data).status_code,409)
        iid=self.c.execute('SELECT item_id FROM med_supply_daily_items').fetchone()[0]
        with self.assertRaises(PermissionError):self.move(iid,allowed={'in'})
        before='\n'.join(self.c.iterdump())
        with patch.object(med,'history',side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):self.move(iid)
        self.assertEqual(before,'\n'.join(self.c.iterdump()))

    def test_folder_create_rename_depth_and_no_assignment_side_effects(self):
        fid=daily.save_folder(self.c,{'name':'Новая','parent':str(self.root)},2)
        with self.assertRaises(ValueError):daily.save_folder(self.c,{'name':'Третий уровень','parent':str(fid)},2)
        daily.save_folder(self.c,dict(id=str(fid),version='1',name='Другое название'),2)
        with self.assertRaises(daily.Conflict):daily.save_folder(self.c,dict(id=str(fid),version='1',name='Старая форма'),2)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM med_supply_daily_items').fetchone()[0],0)

    def test_all_gets_readonly_catalog_has_exact_anchor_and_export(self):
        iid=self.classify();self.move(iid)
        before='\n'.join(self.c.iterdump())
        for tab in ('movement','stock','catalog','history','order','monthly'):
            response=self.client(2).get('/accounting/medical-supplies/?month=2026-11&tab='+tab)
            self.assertEqual(response.status_code,200)
            if tab=='catalog':self.assertIn('2026-11-19',response.get_data(as_text=True))
        response=self.client(2).get('/accounting/medical-supplies/daily/export?month=2026-11')
        self.assertEqual(response.status_code,200)
        wb=load_workbook(io.BytesIO(response.data),data_only=False)
        self.assertEqual(wb.active['M2'].value,13180.5);wb.close()
        self.assertEqual(before,'\n'.join(self.c.iterdump()))

    def test_real_day_http_change_and_other_flow_preserved(self):
        iid=self.classify()
        data=dict(item_id=str(iid),day='2026-11-20',kind='out',qty='3',version='0')
        self.assertEqual(self.http('day',data).status_code,303)
        data.update(kind='in',qty='5',value='500',version='1')
        self.assertEqual(self.http('day',data).status_code,303)
        r=self.c.execute('SELECT * FROM med_supply_days').fetchone()
        self.assertEqual((r['qty_out'],r['qty_in']),('3','5'))

    def test_bad_inputs_and_mismatched_sources_no_writes(self):
        before='\n'.join(self.c.iterdump())
        for changes in ({'folder':'9999'}, {'source_row':'9999'}, {'import_id':'99999'}, {'version':'bad'}):
            with self.assertRaises(ValueError):self.classify(**changes)
        self.assertEqual(before,'\n'.join(self.c.iterdump()))

    def test_same_named_rows_stay_distinct_after_assignment(self):
        self.import_id,_=stock.save_import(self.c,fixture([('Одинаковые',1,'шт',10),('Одинаковые',2,'шт',30)]),'duplicate.xlsx','2026-11-19',2)
        a=self.classify();b=self.classify(row=5)
        self.assertNotEqual(a,b)
        self.assertEqual(daily.totals(daily.snapshot(self.c,'2026-11'))['end_value'],40)
