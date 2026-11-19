import io
import json
import sqlite3
import unittest
from unittest.mock import patch
from werkzeug.datastructures import FileStorage

import ztl
import ztl_control as c
from medical_supplies import Conflict
from test_ztl import ZtlTests


class ControlTests(unittest.TestCase):
    setUp=ZtlTests.setUp
    client=ZtlTests.client
    order=ZtlTests.order
    grant=ZtlTests.grant
    close_costs=ZtlTests.close_costs
    cost=ZtlTests.cost

    def item(self,name='Проволока',kind='material',unit='м'):
        return c.save_item(self.c,dict(name=name,kind=kind,unit=unit),1)

    def receipt(self,iid,qty='10',value='100',**kw):
        data=dict(supplier='Поставщик',reference='D-'+str(c.stock_stamp(self.c))+'-'+str(self.c.execute('SELECT COUNT(*) FROM ztl_receipts').fetchone()[0]),
                  received_on='2026-11-01',item_0=str(iid),qty_0=qty,value_0=value,confirmed='1',paid='1')
        data.update(kw);return c.save_receipt(self.c,data,1)

    def card(self,iid,qty='1',fee='30',version='1'):
        c.save_card(self.c,dict(work_id=self.work,version=version,technician_fee=fee,**{'qty_'+str(iid):qty}),1)

    def inv(self,action,iid=None,**kw):
        data=kw
        if iid:data=dict(id=iid,version=ztl.row_by_id(self.c,'ztl_inventories',iid)['version'],**kw)
        return c.inventory_action(self.c,action,data,1)

    def post(self,action,data=None,uid=2):
        client=self.client(uid)
        with client.session_transaction() as session:session['csrf']='token'
        return client.post('/accounting/ztl/control/'+action,data={**(data or {}),'csrf':'token'})

    def test_dynamic_card_average_and_frozen_order_fee(self):
        item=self.item();self.receipt(item);self.card(item,qty='2')
        self.assertEqual(c.catalog(self.c)[0]['material_cost'],2000)
        oid=self.order(pieces='2',completed_on='2026-11-16')
        self.assertEqual((c.stock(self.c)[0]['qty'],c.stock(self.c)[0]['value']),(6000,6000))
        self.receipt(item,qty='6',value='180')
        self.assertEqual(c.catalog(self.c)[0]['material_cost'],4000)
        self.card(item,qty='3',fee='99',version='2')
        saved=c.production(self.c,oid)
        self.assertEqual((saved['technician_fee'],saved['pieces'],json.loads(saved['recipe'])[0]['qty']),(3000,2,2000))
        self.assertEqual(c.payroll_orders(self.c,'2026-11','2026-11')[0]['fee_total'],6000)

    def test_order_correction_reverses_once_and_shared_no_double(self):
        item=self.item();self.receipt(item);self.card(item)
        with self.c:second=ztl.catalog_entry(self.c,'employee','Второй техник',1)
        oid=self.order(technician_id=[self.technician,second],completed_on='2026-11-16')
        rows=c.payroll_orders(self.c,'2026-11','2026-11')
        self.assertEqual(len(rows),1);self.assertTrue(rows[0]['shared'])
        for technician in (self.technician,second):
            self.assertEqual(len(c.payroll_orders(self.c,'2026-11','2026-11',technician)),1)
        values=ztl.row_by_id(self.c,'ztl_orders',oid);values.update(amount='100',patient='Изменено')
        ztl.save_order(self.c,values,1)
        self.assertEqual(c.stock(self.c)[0]['qty'],9000)
        values=ztl.row_by_id(self.c,'ztl_orders',oid);values.update(amount='100',pieces='2')
        with self.assertRaises(ValueError):ztl.save_order(self.c,values,1)

    def test_insufficient_stock_rolls_back_entire_order(self):
        item=self.item();self.receipt(item,qty='1');self.card(item,qty='2')
        stamp=c.stock_stamp(self.c)
        with self.assertRaises(ValueError):self.order()
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_orders').fetchone()[0],0)
        self.assertEqual(c.stock_stamp(self.c),stamp)

    def test_change_quantity_returns_at_original_cost_then_reissues(self):
        item=self.item();self.receipt(item);self.card(item)
        oid=self.order(pieces='2')
        values=ztl.row_by_id(self.c,'ztl_orders',oid);values.update(amount='100',pieces='3')
        ztl.save_order(self.c,values,1)
        self.assertEqual((c.stock(self.c)[0]['qty'],c.stock(self.c)[0]['value']),(7000,7000))

    def order_http(self, oid, **changes):
        client=self.client(2)
        with client.session_transaction() as session:session['csrf']='token'
        values=ztl.row_by_id(self.c,'ztl_orders',oid)
        values.update(csrf='token',amount=c.display(values['amount'],100))
        values.update(changes)
        return client.post('/accounting/ztl/save/order',data=values)

    def test_http_completed_order_cannot_be_reopened_then_reissued(self):
        item=self.item();self.receipt(item);self.card(item)
        oid=self.order(pieces='2',completed_on='2026-11-16')
        self.grant('ztl_orders')
        before='\n'.join(self.c.iterdump())
        response=self.order_http(oid,completed_on='',patient='Must roll back',amount='999',
                                 technicians_present='1',technician_id=str(self.technician))
        self.assertEqual(response.status_code,400)
        self.assertIn('Снять отметку изготовления',response.get_data(as_text=True))
        self.assertEqual('\n'.join(self.c.iterdump()),before)
        # The second request cannot exploit an empty date left by the first one.
        for changes in ({'pieces':'3'}, {'pieces':'3','completed_on':''}):
            response=self.order_http(oid,**changes)
            self.assertEqual(response.status_code,400)
            self.assertEqual('\n'.join(self.c.iterdump()),before)
        self.assertEqual(c.stock(self.c)[0]['qty'],8000)
        rows=c.payroll_orders(self.c,'2026-11','2026-11')
        self.assertEqual([(r['order_id'],r['pieces'],r['fee_total']) for r in rows],[(oid,2,6000)])

    def test_http_empty_technician_selection_rejected_without_any_writes(self):
        item=self.item();self.receipt(item);self.card(item)
        with self.c:second=ztl.catalog_entry(self.c,'employee','Второй техник',1)
        oid=self.order(technician_id=[self.technician,second],pieces='2',completed_on='2026-11-16')
        self.grant('ztl_orders')
        form=self.client(2).get('/accounting/ztl/order?id='+str(oid)).get_data(as_text=True)
        self.assertIn('name="technicians_present" value="1"',form)
        before='\n'.join(self.c.iterdump())
        for empty in ({'technicians_present':'1'}, {'technicians_present':''}, {'technician_id':''}):
            response=self.order_http(oid,patient='Must roll back',**empty)
            self.assertEqual(response.status_code,400)
            self.assertIn('Выберите хотя бы одного техника.',response.get_data(as_text=True))
            self.assertEqual('\n'.join(self.c.iterdump()),before)
        self.assertEqual(json.loads(c.production(self.c,oid)['technicians']),[self.technician,second])

    def test_http_omitted_production_fields_preserve_state_and_valid_selection_saves(self):
        item=self.item();self.receipt(item);self.card(item)
        oid=self.order(pieces='2',completed_on='2026-11-16')
        with self.c:second=ztl.catalog_entry(self.c,'employee','Второй техник',1)
        self.grant('ztl_orders')
        original=c.production(self.c,oid);stamp=c.stock_stamp(self.c)
        response=self.order_http(oid,patient='Исправленное ФИО')
        self.assertEqual(response.status_code,303)
        self.assertEqual(c.production(self.c,oid),original)
        response=self.order_http(oid,technicians_present='1',technician_id=[str(self.technician),str(second)])
        self.assertEqual(response.status_code,303)
        self.assertEqual(json.loads(c.production(self.c,oid)['technicians']),[self.technician,second])
        response=self.order_http(oid,technicians_present='1',technician_id=str(second))
        self.assertEqual(response.status_code,303)
        self.assertEqual(json.loads(c.production(self.c,oid)['technicians']),[second])
        self.assertEqual(c.production(self.c,oid)['completed_on'],'2026-11-16')
        self.assertEqual(c.stock_stamp(self.c),stamp)
        self.assertEqual(c.stock(self.c)[0]['qty'],8000)

    def test_http_legacy_order_omission_is_preserved_but_submitted_empty_choice_rejected(self):
        oid=self.order()
        with self.c:self.c.execute('DELETE FROM ztl_production WHERE order_id=?',(oid,))
        self.grant('ztl_orders')
        self.assertEqual(self.order_http(oid,patient='Исправленный старый заказ').status_code,303)
        self.assertIsNone(c.production(self.c,oid))
        before='\n'.join(self.c.iterdump())
        response=self.order_http(oid,technicians_present='1')
        self.assertEqual(response.status_code,400)
        self.assertIn('Выберите хотя бы одного техника.',response.get_data(as_text=True))
        self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_old_order_tracking_does_not_backfill_expenses(self):
        oid=self.order()
        with self.c:self.c.execute('DELETE FROM ztl_production WHERE order_id=?',(oid,))
        item=self.item();self.receipt(item);self.card(item)
        values=ztl.row_by_id(self.c,'ztl_orders',oid);values.update(amount='100',technician_id=[self.technician],completed_on='2026-11-16')
        ztl.save_order(self.c,values,1)
        self.assertEqual(c.stock(self.c)[0]['qty'],10000)
        self.assertEqual(c.production(self.c,oid)['card_version'],0)

    def test_receipt_two_flags_duplicates_and_revision(self):
        item=self.item();rid=self.receipt(item,paid='0')
        self.assertEqual(c.stock(self.c)[0]['qty'],0)
        old=ztl.row_by_id(self.c,'ztl_receipts',rid)
        data=dict(id=rid,version='1',supplier=old['supplier'],reference=old['reference'],received_on=old['received_on'],confirmed='1',paid='1')
        c.save_receipt(self.c,data,1)
        self.assertEqual(c.stock(self.c)[0]['qty'],10000)
        with self.assertRaises(Conflict):c.save_receipt(self.c,data,1)
        with self.assertRaises(sqlite3.IntegrityError):self.receipt(item,reference=old['reference'])
        self.assertEqual(c.stock(self.c)[0]['qty'],10000)

    def test_xml_draft_mapping_all_lines_and_protected_document(self):
        raw='<Файл><СведТов НаимТов="Проволока" КолТов="10" СтТовУчНал="100"/><СведТов НаимТов="Пластик" КолТов="2" СтТовУчНал="40"/></Файл>'.encode()
        file=lambda:FileStorage(io.BytesIO(raw),filename='upd.xml')
        rid=c.save_receipt(self.c,dict(supplier='S',reference='1',received_on='2026-11-01'),1,file())
        self.assertEqual(len(json.loads(ztl.row_by_id(self.c,'ztl_receipts',rid)['lines'])),2)
        item=self.item();data=dict(id=rid,version=1,supplier='S',reference='1',received_on='2026-11-01',confirmed='1',paid='1',item_0=item,qty_0='10',value_0='100')
        with self.assertRaises(ValueError):c.save_receipt(self.c,data,1)
        self.assertEqual(self.client(2).get('/accounting/ztl/receipt-document/'+str(rid)).status_code,403)
        self.grant('ztl_receipts_view');res=self.client(2).get('/accounting/ztl/receipt-document/'+str(rid))
        self.assertEqual(res.data,raw);self.assertIn('no-store',res.headers['Cache-Control'])
        bad=FileStorage(io.BytesIO(b'<!DOCTYPE a [<!ENTITY a "x">]><a/>'),filename='bad.xml')
        with self.assertRaises(ValueError):c.upload(bad)

    def test_inventory_two_attempts_explanation_and_accept(self):
        item=self.item();self.receipt(item)
        iid=self.inv('start');self.inv('count',iid,**{'count_'+str(item):'9'})
        with self.assertRaises(Conflict):self.inv('accept',iid)
        self.inv('recheck',iid);self.inv('count',iid,**{'count_'+str(item):'8'})
        with self.assertRaises(Conflict):self.inv('accept',iid)
        self.inv('explain',iid,person='Казанцева',explanation='Расход не внесён вовремя')
        self.inv('accept',iid)
        self.assertEqual((c.stock(self.c)[0]['qty'],c.stock(self.c)[0]['value']),(8000,8000))
        with self.assertRaises(Conflict):self.inv('accept',iid)
        printed=self.client(1).get('/accounting/ztl/inventory/'+str(iid)+'/print')
        self.assertEqual(printed.status_code,200);self.assertIn('Казанцева',printed.get_data(as_text=True))

    def test_receipt_route_accepts_document_limit_and_error_names_are_plain_text(self):
        self.grant('ztl_receipts_edit')
        data=dict(supplier='S',reference='HTTP-document',received_on='2026-11-01',document=(io.BytesIO(b'%PDF-'+b'0'*(3*1024*1024)),'test.pdf'))
        response=self.post('receipt',data)
        self.assertEqual(response.status_code,303)
        data.update(reference='HTTP-too-large',document=(io.BytesIO(b'%PDF-'+b'0'*(5*1024*1024)),'large.pdf'))
        self.assertEqual(self.post('receipt',data).status_code,400)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_receipts').fetchone()[0],1)
        item=self.item(name='<img src=x onerror=alert(1)>');self.card(item)
        self.grant('ztl_orders')
        client=self.client(2)
        with client.session_transaction() as session:session['csrf']='token'
        response=client.post('/accounting/ztl/save/order',data=dict(csrf='token',number='ERR',invoice_date='2026-11-16',scope='external',party_id=self.party,patient='test',work_id=self.work,amount='1',delay_days='0',technician_id=self.technician))
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.mimetype,'text/plain')
        self.assertIn('<img',response.get_data(as_text=True))

    def test_inventory_match_zero_and_stock_concurrency(self):
        item=self.item();self.receipt(item)
        iid=self.inv('start');self.inv('count',iid,**{'count_'+str(item):'10'})
        self.inv('accept',iid);self.assertEqual(c.stock(self.c)[0]['qty'],10000)
        iid=self.inv('start');self.receipt(item)
        with self.assertRaises(Conflict):self.inv('count',iid,**{'count_'+str(item):'20'})
        self.inv('cancel',iid,reason='Изменился склад')

    def test_inventory_count_permission_does_not_leak_expected_or_history(self):
        item=self.item();self.receipt(item);iid=self.inv('start')
        self.grant('ztl_inventory_count');client=self.client(2)
        page=client.get('/accounting/ztl/?tab=inventory').get_data(as_text=True)
        self.assertIn('Фактический остаток',page)
        for secret in ('Расчётный остаток','Ушло по картам','stock_stamp','"qty":','"value":'):self.assertNotIn(secret,page)
        self.assertEqual(self.post('inventory-accept',dict(id=iid,version=1)).status_code,403)
        self.assertEqual(client.get('/accounting/ztl/inventory/'+str(iid)+'/print').status_code,403)
        self.assertNotIn('"snapshot"',client.get('/accounting/ztl/?tab=history').get_data(as_text=True))

    def test_receipts_read_write_and_no_implicit_grants(self):
        for tab in ('receipts','inventory'):self.assertEqual(self.client(2).get('/accounting/ztl/?tab='+tab).status_code,403)
        self.grant('ztl_receipts_view')
        self.assertEqual(self.client(2).get('/accounting/ztl/?tab=receipts').status_code,200)
        self.assertEqual(self.post('receipt').status_code,403)
        self.grant('ztl_receipts_edit')
        self.assertEqual(self.client(2).get('/accounting/ztl/?tab=receipts').status_code,200)
        self.assertEqual(self.post('inventory-start').status_code,403)
        self.assertEqual(self.post('item').status_code,403)

    def test_economics_no_purchase_and_usage_double_count_or_payroll_duplication(self):
        item=self.item();self.receipt(item,value='100');self.card(item,qty='2',fee='30');self.order()
        self.close_costs()
        r=ztl.report(self.c,'2026-11','2026-11');econ=c.economic_summary(self.c,r)
        self.assertFalse(econ['complete']);self.assertEqual(econ['months'][0]['materials'],2000)
        self.grant('ztl_lab_costs');cur=econ['months'][0]
        self.assertEqual(self.post('reconcile',dict(month='2026-11',mode='additional',stamp=cur['stamp'],version=cur['version'])).status_code,303)
        econ=c.economic_summary(self.c,r)
        self.assertTrue(econ['complete']);self.assertEqual(econ['total_cost'],r['total_cost']+2000)
        self.receipt(item,value='100');self.assertFalse(c.economic_summary(self.c,r)['complete'])

    def test_asset_expense_once_and_replayed_writeoff_rejected(self):
        item=self.item('Шкаф','asset','шт.');self.receipt(item,qty='1',value='5000')
        data=dict(item_id=item,qty='1',reason='Сломан',stamp=c.stock_stamp(self.c))
        c.write_off(self.c,data,1)
        self.assertEqual(c.month_costs(self.c,'2026-11')['total'],500000)
        with self.assertRaises(Conflict):c.write_off(self.c,data,1)
        self.assertEqual(c.stock(self.c)[0]['qty'],0)

    def test_all_seven_tabs_and_filter(self):
        self.order(patient='Заказ техника')
        for tab,_ in c.TABS:
            res=self.client(1).get('/accounting/ztl/?tab='+tab+'&start=2026-11&end=2026-11')
            self.assertEqual(res.status_code,200,tab)
        res=self.client(1).get('/accounting/ztl/?tab=orders&start=2026-11&technician='+str(self.technician))
        self.assertIn('Заказ техника',res.get_data(as_text=True))
        res=self.client(1).get('/accounting/ztl/?tab=orders&start=2026-11&technician=99999')
        self.assertNotIn('Заказ техника',res.get_data(as_text=True))

    def test_tiles_are_an_entrance_without_loading_ledgers(self):
        self.order(patient='Только внутри заказов')
        before='\n'.join(self.c.iterdump())
        with patch.object(ztl,'report',side_effect=AssertionError('menu must not calculate report')), \
             patch.object(c,'stock',side_effect=AssertionError('menu must not read stock')), \
             patch.object(c,'payroll_orders',side_effect=AssertionError('menu must not calculate payroll')):
            response=self.client(1).get('/accounting/ztl/?start=2026-10&end=2026-11')
        self.assertEqual(response.status_code,200)
        body=response.get_data(as_text=True)
        self.assertIn('<h1 class="title">ЗТЛ</h1>',body)
        self.assertIn('aria-label="Разделы ЗТЛ"',body)
        self.assertEqual(body.count('class="btn active"'),7)
        for key,label in c.TABS:
            self.assertIn('/accounting/ztl/'+key+'/?start=2026-10&amp;end=2026-11',body)
            self.assertIn(label,body)
        for absent in ('<table','<form','Только внутри заказов','ztl-filter','ztl-toolbar'):
            self.assertNotIn(absent,body)
        self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_separate_sections_legacy_links_and_back_preserve_records(self):
        self.order(patient='Заказ на отдельной странице')
        before='\n'.join(self.c.iterdump());client=self.client(1)
        for key,label in c.TABS:
            with self.subTest(section=key):
                path='/accounting/ztl/'+key+'/?start=2026-10&end=2026-11'
                response=client.get(path);self.assertEqual(response.status_code,200)
                body=response.get_data(as_text=True)
                self.assertIn('<h2 class="ztl-title">'+label+'</h2>',body)
                self.assertIn('href="/accounting/ztl/?start=2026-10&amp;end=2026-11"',body)
                self.assertNotIn('aria-label="Разделы ЗТЛ"',body)
                self.assertNotIn('class="menu"',body)
                if key not in ('orders','economics'):
                    self.assertNotIn('Заказ на отдельной странице',body)
                legacy=client.get('/accounting/ztl/?tab='+key+'&start=2026-10&end=2026-11')
                self.assertEqual(legacy.status_code,200)
                self.assertEqual(legacy.get_data(as_text=True),body)
        self.assertEqual('\n'.join(self.c.iterdump()),before)
        self.assertEqual(client.get('/accounting/ztl/unknown/').status_code,400)

    def test_tiles_and_direct_sections_keep_permissions(self):
        for uid in (None,3):
            for path in ('/accounting/ztl/',*(('/accounting/ztl/'+key+'/') for key,_ in c.TABS)):
                self.assertEqual(self.client(uid).get(path).status_code,302 if uid is None else 403)
        for rights,allowed in [((),()),(('ztl_receipts_view',),('receipts',)),
                               (('ztl_receipts_edit',),('receipts',)),
                               (('ztl_inventory_count',),('inventory',)),
                               (('ztl_inventory_manage',),('inventory',))]:
            self.grant(*rights);client=self.client(2)
            body=client.get('/accounting/ztl/').get_data(as_text=True)
            for key in ('receipts','inventory'):
                self.assertEqual(('/accounting/ztl/'+key+'/') in body,key in allowed)
                self.assertEqual(client.get('/accounting/ztl/'+key+'/').status_code,200 if key in allowed else 403)

    def test_reference_sections_do_not_calculate_unselected_report(self):
        client=self.client(1)
        with patch.object(ztl,'report',side_effect=AssertionError('unselected report')), \
             patch.object(c,'payroll_orders',side_effect=AssertionError('unselected payroll')):
            for key in ('products','receipts','inventory','payments','catalog','history'):
                self.assertEqual(client.get('/accounting/ztl/'+key+'/').status_code,200,key)

    def test_audit_failure_rolls_back_and_csrf(self):
        item=self.item()
        with patch.object(ztl,'log',side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):self.receipt(item)
        self.assertEqual(c.stock(self.c)[0]['qty'],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_receipts').fetchone()[0],0)
        self.grant('ztl_inventory_manage')
        self.assertEqual(self.client(2).post('/accounting/ztl/control/inventory-start',data={}).status_code,400)


if __name__=='__main__':unittest.main()
