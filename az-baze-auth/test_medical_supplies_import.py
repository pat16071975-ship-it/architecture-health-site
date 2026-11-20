import io
import json
import sqlite3
import unittest
from decimal import Decimal
from unittest.mock import patch
from openpyxl import Workbook
from test_medical_supplies import SuppliesTests
import medical_supplies_import as stock
import medical_supplies as med
import app as core


def fixture(rows=None, total=None):
    rows = rows or [('Материал А',100,'шт',14645),('Материал Б',2,'упак',100)]
    w=Workbook();s=w.active;s.title='По обороту и остаткам'
    for col,label in [('B','Начало'),('F','Приход'),('J','Расход'),('N','Конец')]:s[col+'1']=label
    s['A2']='Итого';s['N2']=sum(Decimal(str(r[3])) for r in rows) if total is None else total
    s['A3']='Терапия';s['N3']=s['N2'].value
    for n,(name,qty,unit,value) in enumerate(rows,4):
        s.cell(n,1,name);s.cell(n,14,qty);s.cell(n,15,unit);s.cell(n,16,value);s.cell(n,17,0)
    out=io.BytesIO();w.save(out);return out.getvalue()


class ParseTests(unittest.TestCase):
    def test_exact_division_and_no_subtotal_duplication(self):
        p=stock.parse_statement(fixture())
        self.assertEqual(len(p['rows']),2);self.assertEqual(Decimal(p['total']),14745)
        self.assertEqual(p['rows'][0]['piece_price'],'146.45')
        self.assertEqual(p['rows'][1]['unit_price'],'50')
        self.assertIsNone(p['rows'][1]['piece_price'])
        self.assertEqual(p['rows'][0]['issues'],[])
        self.assertEqual(p['issue_count'],1)

    def test_unknown_pack_never_guessed_from_strength_or_name(self):
        rows=[('Средство 1:100 000, 5 л, 20 шт',2,'упак',80)]
        r=stock.parse_statement(fixture(rows))['rows'][0]
        self.assertEqual(r['unit_price'],'40');self.assertIsNone(r['piece_price']);self.assertTrue(r['issues'])

    def test_zero_and_negative_values_preserved_with_questions(self):
        rows=[('Ноль',10,'шт',0),('Нет остатков',0,'шт',0),('Возврат',-2,'шт',-100)]
        p=stock.parse_statement(fixture(rows));a,b,c=p['rows']
        self.assertEqual(a['piece_price'],'0');self.assertTrue(a['issues'])
        self.assertIsNone(b['piece_price']);self.assertTrue(b['issues'])
        self.assertEqual(c['end_qty'],'-2');self.assertEqual(c['end_value'],'-100');self.assertTrue(c['issues'])

    def test_duplicate_names_stay_separate(self):
        p=stock.parse_statement(fixture([('Материал',12,'шт',450),('Материал',28,'шт',0)]))
        self.assertEqual(len(p['rows']),2)
        self.assertTrue(all(any('повторяется' in q for q in r['issues']) for r in p['rows']))

    def test_missing_unit_is_a_question_not_a_skipped_subtotal(self):
        p=stock.parse_statement(fixture([('Нет единицы',3,None,30)]))
        self.assertEqual(len(p['rows']),1);self.assertTrue(p['rows'][0]['issues'])
        self.assertIsNone(p['rows'][0]['piece_price'])

    def test_total_mismatch_fails(self):
        with self.assertRaisesRegex(ValueError,'не совпадает'):stock.parse_statement(fixture(total=1))

    def test_rounding_is_only_for_display(self):
        p=stock.parse_statement(fixture([('Дробный',3,'шт',10)]))
        self.assertTrue(p['rows'][0]['piece_price'].startswith('3.333333'))
        self.assertEqual(stock.money(p['rows'][0]['piece_price']),'3,33')
        self.assertEqual(p['total'],'10')

    def test_bad_files_and_formulas_rejected(self):
        with self.assertRaises(ValueError):stock.parse_statement(b'not xlsx')
        with self.assertRaises(ValueError):stock.parse_statement(b'a'*(stock.MAX_BYTES+1))
        data=fixture([('=HYPERLINK("https://example.test")',1,'шт',10)])
        with self.assertRaisesRegex(ValueError,'формула'):stock.parse_statement(data)

    def test_explicit_do_not_use_kept(self):
        r=stock.parse_statement(fixture([('Материал НЕ ИСПОЛЬЗОВАТЬ',2,'шт',20)]))['rows'][0]
        self.assertTrue(any('НЕ ИСПОЛЬЗОВАТЬ' in q for q in r['issues']))


class ImportTests(SuppliesTests):
    def upload(self,client,data=None,day='2026-11-19',csrf=True):
        with client.session_transaction() as s:s['csrf']='test-token'
        return client.post('/accounting/medical-supplies/import',data={
            **({'csrf':'test-token'} if csrf else {}),'snapshot_date':day,
            'file':(io.BytesIO(data or fixture()),'По обороту (2026.11.19).xlsx')})

    def test_http_import_and_no_writes_to_existing_ledger(self):
        before='\n'.join(line for line in self.c.iterdump() if 'med_supply_stock_imports' not in line)
        content=fixture();r=self.upload(self.client(2),content);self.assertEqual(r.status_code,303)
        after='\n'.join(line for line in self.c.iterdump() if 'med_supply_stock_imports' not in line)
        self.assertEqual(before,after)
        page=self.client(2).get(r.location).get_data(as_text=True)
        for expected in ['146,45','19.11.2026','Требует решения','Материал А','Материал Б']:self.assertIn(expected,page)
        self.assertEqual(self.c.execute('SELECT source_xlsx FROM med_supply_stock_imports').fetchone()[0],content)
        self.assertEqual(self.client(2).get('/accounting/medical-supplies/').status_code,200)
        self.assertIn('Остатки на',self.client(2).get('/accounting/medical-supplies/').get_data(as_text=True))

    def test_retry_idempotent_and_date_conflict(self):
        content=fixture()
        for _ in range(2):self.assertEqual(self.upload(self.client(2),content).status_code,303)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM med_supply_stock_imports').fetchone()[0],1)
        self.assertEqual(self.upload(self.client(2),content,day='2026-11-18').status_code,400)
        with self.assertRaisesRegex(ValueError,'другой датой'):
            stock.save_import(self.c,content,'other.xlsx','2026-11-20',2)

    def test_dates_and_invalid_data_never_write(self):
        before='\n'.join(self.c.iterdump())
        for day in ('','bad','2026-02-30','20261119','2026-11-18'):
            self.assertEqual(self.upload(self.client(2),day=day).status_code,400)
        self.assertEqual(self.upload(self.client(2),fixture(total=1)).status_code,400)
        self.assertEqual(before,'\n'.join(self.c.iterdump()))

    def test_permissions_and_csrf(self):
        self.assertEqual(self.upload(self.client(3)).status_code,403)
        self.assertEqual(self.upload(self.client(2),csrf=False).status_code,400)
        with self.c:
            self.c.execute("DELETE FROM permissions WHERE user_id=2 AND section='medical_supplies_edit'")
            self.c.execute("INSERT INTO permissions VALUES(2,'medical_supplies_catalog')")
        self.assertEqual(self.upload(self.client(2)).status_code,403)
        self.assertEqual(self.client(2).get('/accounting/medical-supplies/?tab=stock').status_code,200)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM med_supply_stock_imports').fetchone()[0],0)

    def test_filters_readonly_and_old_monthly_operations_unchanged(self):
        iid=self.item();self.period(iid,qty_in='2',qty_out='1')
        old=med.snapshot(self.c,'2026-11')
        self.upload(self.client(2))
        before=core.DB_PATH.read_bytes()
        page=self.client(2).get('/accounting/medical-supplies/?tab=stock&issues=1').get_data(as_text=True)
        self.assertNotIn('data-source-row="4"',page);self.assertIn('data-source-row="5"',page)
        page=self.client(2).get('/accounting/medical-supplies/?tab=stock&q=Материал+А').get_data(as_text=True)
        self.assertIn('data-source-row="4"',page);self.assertNotIn('data-source-row="5"',page)
        self.assertEqual(core.DB_PATH.read_bytes(),before)
        self.assertEqual(med.snapshot(self.c,'2026-11'),old)
        for tab in ('movement','catalog','order','history'):
            self.assertEqual(self.client(2).get('/accounting/medical-supplies/?tab='+tab).status_code,200)

    def test_failed_database_insert_rolls_back(self):
        self.c.execute("CREATE TRIGGER reject_import BEFORE INSERT ON med_supply_stock_imports BEGIN SELECT RAISE(ABORT,'test fail'); END")
        self.c.commit();before='\n'.join(self.c.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):stock.save_import(self.c,fixture(),'stock.xlsx','2026-11-19',2)
        self.assertEqual(before,'\n'.join(self.c.iterdump()))

    def test_html_escape_and_foreign_import_id(self):
        self.upload(self.client(2),fixture([('<img src=x onerror=alert(1)>',1,'шт',1)]))
        html=self.client(2).get('/accounting/medical-supplies/?tab=stock').get_data(as_text=True)
        self.assertIn('&lt;img',html);self.assertNotIn('<img src=x',html)
        self.assertEqual(self.client(2).get('/accounting/medical-supplies/?tab=stock&import_id=999').status_code,404)

    def test_schema_missing_read_does_not_initialize(self):
        self.c.execute('DROP TABLE med_supply_stock_imports');self.c.commit()
        before=core.DB_PATH.read_bytes()
        self.assertEqual(self.client(2).get('/accounting/medical-supplies/?tab=stock').status_code,200)
        self.assertEqual(self.upload(self.client(2)).status_code,400)
        self.assertEqual(core.DB_PATH.read_bytes(),before)


class FirstUploadTests(unittest.TestCase):
    def setUp(self):
        self.case=SuppliesTests();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.c=self.case.c

    def test_first_upload_form_reachable_from_normal_entry_and_all_four_tabs(self):
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM med_supply_stock_imports').fetchone()[0],0)
        before='\n'.join(self.c.iterdump())
        for uid in (1,2):
            client=self.case.client(uid)
            self.assertIn('href="/accounting/medical-supplies/"',client.get('/accounting/').get_data(as_text=True))
            for query in ('','?tab=movement','?tab=order','?tab=catalog','?tab=history'):
                with self.subTest(uid=uid,query=query):
                    response=client.get('/accounting/medical-supplies/'+query)
                    self.assertEqual(response.status_code,200)
                    html=response.get_data(as_text=True)
                    self.assertEqual(html.count('id="stock-file"'),1)
                    self.assertIn('action="/accounting/medical-supplies/import"',html)
                    self.assertIn('enctype="multipart/form-data"',html)
                    self.assertIn('name="csrf"',html)
                    self.assertIn('name="snapshot_date"',html)
        self.assertEqual(before,'\n'.join(self.c.iterdump()))

    def test_upload_entry_keeps_existing_permission_boundary(self):
        with self.c:self.c.execute("DELETE FROM permissions WHERE user_id=2 AND section='medical_supplies_edit'")
        for grant in (None,'medical_supplies_catalog','medical_supplies_purchase','medical_supplies_in','medical_supplies_out'):
            with self.c:
                self.c.execute("DELETE FROM permissions WHERE user_id=2 AND section LIKE 'medical_supplies_%'")
                if grant:self.c.execute('INSERT INTO permissions VALUES(2,?)',(grant,))
            client=self.case.client(2)
            before='\n'.join(self.c.iterdump())
            for query in ('','?tab=movement','?tab=order','?tab=catalog','?tab=history'):
                response=client.get('/accounting/medical-supplies/'+query)
                self.assertEqual(response.status_code,200)
                self.assertNotIn('id="stock-file"',response.get_data(as_text=True))
            self.assertEqual(client.post('/accounting/medical-supplies/import').status_code,403)
            self.assertEqual(before,'\n'.join(self.c.iterdump()))

    def test_first_upload_entry_preserves_existing_monthly_screen(self):
        iid=self.case.item();self.case.period(iid,qty_in='4',qty_out='2')
        before='\n'.join(self.c.iterdump())
        response=self.case.client(2).get('/accounting/medical-supplies/?month=2026-11')
        self.assertEqual(response.status_code,200)
        html=response.get_data(as_text=True)
        self.assertIn('id="stock-file"',html)
        self.assertIn('Тестовый материал',html)
        self.assertIn('id="period-'+str(iid)+'"',html)
        self.assertEqual(before,'\n'.join(self.c.iterdump()))


if __name__=='__main__':unittest.main()
