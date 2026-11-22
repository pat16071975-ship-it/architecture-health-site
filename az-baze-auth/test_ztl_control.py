import io
import json
import re
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
        return client.post('/accounting/ztl/control/'+action,data={'change_reason':'Проверка изменения сохранённой записи',**(data or {}),'csrf':'token'})

    def product_data(self,**changes):
        data=dict(name='Новое изделие',version='0',materials_present='1',prices_present='1',internal_price='2500,50',external_price='4000',material_row=['0'],
                  material_0='new',material_name_0='Пластик',material_unit_0='г',material_qty_0='2,5',technician_fee='150')
        data.update(changes);return data

    def product_operation(self,wid,operation):
        work=ztl.row_by_id(self.c,'ztl_names',wid)
        card=self.c.execute('SELECT version FROM ztl_cards WHERE work_id=?',(wid,)).fetchone()
        return dict(work_id=wid,work_version=work['version'],version=card['version'] if card else 0,operation=operation,confirm='1')

    def priced_order_data(self, scope='external', **changes):
        data=dict(number='PRICE-HTTP',invoice_date='2026-11-01',scope=scope,party_id=self.party,
                  patient='Проверка прайса',doctor_id=self.doctor,amount='100',delay_days='0',
                  price_key=scope+':'+str(self.work),price_version='1',price_selection_present='1',
                  technician_id=str(self.technician),technicians_present='1',pieces='1')
        data.update(changes);return data

    def post_order(self,data):
        client=self.client(2)
        with client.session_transaction() as session:session['csrf']='token'
        return client.post('/accounting/ztl/save/order',data={'change_reason':'Проверка изменения сохранённой записи',**data,'csrf':'token'})

    def test_registry_price_rows_match_filled_prices_after_http_save_and_edit(self):
        self.grant('ztl_catalog')
        for internal,external,scopes in [('', '24000', ['external']), ('1250', '', ['internal']),
                                         ('100', '200', ['internal','external']), ('0', '', ['internal']),
                                         ('', '0', ['external']), ('0', '0', ['internal','external']),
                                         (' ', '', [])]:
            with self.subTest(internal=internal,external=external):
                data=self.product_data(work_id=self.work,work_version=1,version=self.c.execute(
                    'SELECT version FROM ztl_cards WHERE work_id=?',(self.work,)).fetchone()[0],
                    name='Работа',material_row=[],no_materials='1',internal_price=internal,external_price=external)
                self.assertEqual(self.post('card',data).status_code,303)
                before='\n'.join(self.c.iterdump())
                page=self.client(2).get('/accounting/ztl/products/').get_data(as_text=True)
                entry=page.split('data-product-entry="'+str(self.work)+'"',1)[1].split('</tbody>',1)[0]
                self.assertEqual(re.findall(r'data-price-type="([^"]+)"',entry),scopes)
                self.assertEqual(entry.count('data-unpriced-product'),int(not scopes))
                self.assertEqual(entry.count('data-edit-product='),len(scopes) or 1)
                self.assertEqual([p['scope'] for p in c.order_price_choices(self.c)],scopes)
                self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_external_only_new_product_uses_one_card_and_keeps_existing_orders(self):
        oid=self.order();before_order=ztl.row_by_id(self.c,'ztl_orders',oid)
        before_production=c.production(self.c,oid);stamp=c.stock_stamp(self.c)
        self.grant('ztl_catalog')
        self.assertEqual(self.post('card',self.product_data(internal_price='',external_price='24000',ident_code='001.02')).status_code,303)
        card=next(r for r in c.catalog(self.c) if r['name']=='Новое изделие')
        self.assertEqual((card['internal_price'],card['external_price'],card['ident_code']),(None,2400000,'001.02'))
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_cards WHERE work_id=?',(card['id'],)).fetchone()[0],1)
        self.assertEqual(ztl.row_by_id(self.c,'ztl_orders',oid),before_order)
        self.assertEqual(c.production(self.c,oid),before_production)
        self.assertEqual(c.stock_stamp(self.c),stamp)

    def test_ident_code_http_edit_legacy_omission_clear_and_audit(self):
        self.grant('ztl_catalog')
        self.assertEqual(self.post('card',self.product_data(ident_code='  0007.А-01  ')).status_code,303)
        card=next(r for r in c.catalog(self.c) if r['name']=='Новое изделие');wid=card['id']
        self.assertEqual(card['ident_code'],'0007.А-01')
        page=self.client(2).get('/accounting/ztl/products/').get_data(as_text=True)
        self.assertLess(page.index('name="ident_code"'),page.index('name="name"'))
        entry=page.split('data-product-entry="'+str(wid)+'"',1)[1].split('</tbody>',1)[0]
        self.assertEqual(entry.count('data-list-label="Код IDENT">0007.А-01</td>'),2)
        self.assertIn('name="ident_code" value="0007.А-01"',entry)
        data=self.product_data(work_id=wid,work_version=card['work_version'],version=card['version'],
                               material_0=str(card['parts'][0]['item_id']),ident_code='0008')
        self.assertEqual(self.post('card',data).status_code,303)
        history=self.c.execute("SELECT before_json,after_json FROM ztl_history WHERE entity='Технологическая карта' AND entity_id=? ORDER BY id DESC LIMIT 1",(wid,)).fetchone()
        self.assertEqual(json.loads(history['before_json'])['ident_code'],'0007.А-01')
        self.assertEqual(json.loads(history['after_json'])['ident_code'],'0008')
        before='\n'.join(self.c.iterdump())
        self.assertEqual(self.post('card',data).status_code,409)
        self.assertEqual('\n'.join(self.c.iterdump()),before)
        data['version']+=1;del data['ident_code']
        self.assertEqual(self.post('card',data).status_code,303)
        self.assertEqual(next(r for r in c.catalog(self.c) if r['id']==wid)['ident_code'],'0008')
        data.update(version=data['version']+1,ident_code='')
        self.assertEqual(self.post('card',data).status_code,303)
        self.assertEqual(next(r for r in c.catalog(self.c) if r['id']==wid)['ident_code'],'')
        self.assertIsNone(self.c.execute('SELECT * FROM ztl_product_codes WHERE work_id=?',(wid,)).fetchone())

    def test_ident_code_invalid_and_failed_save_leave_no_partial_records(self):
        self.grant('ztl_catalog');before='\n'.join(self.c.iterdump())
        for code in ('a'*101,'00\n17','00\x0017','00\x7f17'):
            with self.subTest(code=repr(code)):
                response=self.post('card',self.product_data(ident_code=code))
                self.assertEqual(response.status_code,400)
                self.assertIn('Код IDENT',response.get_data(as_text=True))
                self.assertEqual('\n'.join(self.c.iterdump()),before)
        # This fails after the code insert, and must roll it back with the material.
        self.assertEqual(self.post('card',self.product_data(ident_code='007',external_price='-1')).status_code,400)
        self.assertEqual('\n'.join(self.c.iterdump()),before)
        original=ztl.log
        def fail_card(*args,**kwargs):
            if args[1]=='Технологическая карта':raise RuntimeError('audit failed')
            return original(*args,**kwargs)
        with patch.object(ztl,'log',side_effect=fail_card),self.assertRaisesRegex(RuntimeError,'audit failed'):
            c.save_card(self.c,self.product_data(ident_code='007'),1)
        self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_ident_code_archive_restore_delete_and_permission(self):
        c.save_card(self.c,self.product_data(ident_code='00005'),1)
        wid=next(r['id'] for r in c.catalog(self.c) if r['name']=='Новое изделие')
        before='\n'.join(self.c.iterdump())
        self.assertEqual(self.post('card',self.product_data(ident_code='007')).status_code,403)
        self.assertEqual('\n'.join(self.c.iterdump()),before)
        self.grant('ztl_catalog')
        for action in ('archive','restore'):
            self.assertEqual(self.post('product-action',self.product_operation(wid,action)).status_code,303)
            self.assertEqual(next(r for r in c.catalog(self.c) if r['id']==wid)['ident_code'],'00005')
        self.assertEqual(self.post('product-action',self.product_operation(wid,'delete')).status_code,303)
        self.assertIsNone(self.c.execute('SELECT * FROM ztl_product_codes WHERE work_id=?',(wid,)).fetchone())
        history=self.c.execute("SELECT before_json FROM ztl_history WHERE entity='Изделие ЗТЛ' AND entity_id=? ORDER BY id DESC LIMIT 1",(wid,)).fetchone()
        self.assertEqual(json.loads(history[0])['ident_code'],'00005')

    def test_ident_code_html_escaped_and_optional_for_legacy_cards(self):
        c.save_card(self.c,self.product_data(ident_code='00<svg/onload=alert(1)>'),1)
        page=self.client(1).get('/accounting/ztl/products/').get_data(as_text=True)
        self.assertNotIn('00<svg/onload=alert(1)>',page)
        self.assertIn('00&lt;svg/onload=alert(1)&gt;',page)
        self.assertEqual(next(r for r in c.catalog(self.c) if r['id']==self.work)['ident_code'],'')

    def test_code_schema_init_is_idempotent_and_does_not_fill_existing_products(self):
        before='\n'.join(self.c.iterdump())
        self.c.execute('DROP TABLE ztl_product_codes');self.c.commit()
        c.init_schema(self.c);c.init_schema(self.c)
        self.assertEqual('\n'.join(self.c.iterdump()),before)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_product_codes').fetchone()[0],0)

    def test_order_price_positions_filter_missing_zero_and_archive(self):
        with self.c:self.c.execute('UPDATE ztl_product_prices SET internal_price=0,external_price=NULL WHERE work_id=?',(self.work,))
        choices=c.order_price_choices(self.c)
        self.assertEqual([(p['key'],p['price']) for p in choices],[('internal:'+str(self.work),0)])
        self.grant('ztl_orders')
        before='\n'.join(self.c.iterdump())
        page=self.client(2).get('/accounting/ztl/order').get_data(as_text=True)
        self.assertIn('value="internal:'+str(self.work)+'"',page)
        self.assertNotIn('value="external:'+str(self.work)+'"',page)
        self.assertEqual('\n'.join(self.c.iterdump()),before)
        self.assertEqual(self.post_order(self.priced_order_data()).status_code,400)
        self.assertEqual('\n'.join(self.c.iterdump()),before)
        self.assertEqual(self.post_order(self.priced_order_data('internal',amount='0')).status_code,303)
        c.product_action(self.c,self.product_operation(self.work,'archive'),1)
        self.assertEqual(c.order_price_choices(self.c),[])

    def test_http_wrong_price_scope_forged_work_and_missing_selection_are_atomic(self):
        self.grant('ztl_orders');before='\n'.join(self.c.iterdump())
        cases=[{'scope':'internal'}, {'price_key':'internal:1:extra'}, {'price_key':''},
               {'price_key':'external:0'}, {'price_key':'external:'+str(self.technician)},
               {'work_id':self.technician}, {'price_version':'999'}, {'price_version':''}]
        for change in cases:
            with self.subTest(change=change):
                data=self.priced_order_data();data.update(change)
                response=self.post_order(data)
                self.assertIn(response.status_code,(400,409))
                self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_http_both_price_positions_use_one_card_and_one_stock_issue_per_order(self):
        item=self.item();self.receipt(item);self.card(item)
        self.grant('ztl_orders')
        for scope in ('internal','external'):
            data=self.priced_order_data(scope,number='PRICE-'+scope,price_version='2',pieces='2')
            self.assertEqual(self.post_order(data).status_code,303)
        rows=ztl.orders(self.c)
        self.assertEqual({(r['scope'],r['work_id']) for r in rows},{('internal',self.work),('external',self.work)})
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_cards').fetchone()[0],1)
        self.assertEqual(c.stock(self.c)[0]['qty'],6000)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM ztl_moves WHERE kind='order'").fetchone()[0],2)

    def test_old_order_without_current_price_keeps_its_position_amount_and_production(self):
        oid=self.order(amount='123.45')
        with self.c:self.c.execute('DELETE FROM ztl_product_prices WHERE work_id=?',(self.work,))
        old=ztl.row_by_id(self.c,'ztl_orders',oid);production=c.production(self.c,oid)
        choices=c.order_price_choices(self.c,old)
        self.assertEqual([(p['key'],p['saved']) for p in choices],[('external:'+str(self.work),True)])
        self.grant('ztl_orders');before='\n'.join(self.c.iterdump())
        page=self.client(2).get('/accounting/ztl/order?id='+str(oid)).get_data(as_text=True)
        self.assertIn('сохранённая позиция заказа',page)
        self.assertIn('value="123.45"',page)
        self.assertEqual('\n'.join(self.c.iterdump()),before)
        data=self.priced_order_data(id=oid,version=old['version'],number=old['number'],amount='123.45',price_version='')
        self.assertEqual(self.post_order(data).status_code,303)
        self.assertEqual(ztl.row_by_id(self.c,'ztl_orders',oid)['amount'],12345)
        self.assertEqual(c.production(self.c,oid),production)
        before='\n'.join(self.c.iterdump())
        data.update(scope='internal',price_key='internal:'+str(self.work),version=old['version']+1)
        self.assertEqual(self.post_order(data).status_code,400)
        self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_old_client_cannot_create_order_with_missing_scope_price(self):
        with self.c:self.c.execute('UPDATE ztl_product_prices SET external_price=NULL WHERE work_id=?',(self.work,))
        before='\n'.join(self.c.iterdump())
        with self.assertRaisesRegex(ValueError,'внешний прайс'):self.order()
        self.assertEqual('\n'.join(self.c.iterdump()),before)
        self.order(scope='internal')

    def test_changed_price_version_and_failed_history_roll_back_entire_order(self):
        self.grant('ztl_orders')
        c.save_card(self.c,dict(work_id=self.work,version='1',no_materials='1',technician_fee='0',
                    prices_present='1',internal_price='150',external_price='250'),1)
        before='\n'.join(self.c.iterdump())
        self.assertEqual(self.post_order(self.priced_order_data()).status_code,409)
        self.assertEqual('\n'.join(self.c.iterdump()),before)
        with patch.object(ztl,'log',side_effect=RuntimeError('history unavailable')):
            with self.assertRaisesRegex(RuntimeError,'history unavailable'):
                self.post_order(self.priced_order_data(price_version='2'))
        self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_unified_http_creates_product_and_many_materials_atomically(self):
        wire=self.item();self.receipt(wire)
        data=self.product_data(material_row=['0','1','2'],material_0=str(wire),material_name_0='',material_qty_0='0,25',material_unit_0='м',
             material_1='new',material_name_1='Винт',material_qty_1='2',material_unit_1='шт.',
             material_2='new',material_name_2='Особый пластик',material_qty_2='1.5',material_unit_2='__custom__',material_custom_unit_2='порция')
        self.grant('ztl_catalog')
        response=self.post('card',data)
        self.assertEqual(response.status_code,303)
        self.assertIn('/ztl/products/',response.location)
        card=next(r for r in c.catalog(self.c) if r['name']==data['name'])
        self.assertEqual([p['qty'] for p in card['parts']],[250,2000,1500])
        self.assertEqual(card['technician_fee'],15000)
        self.assertEqual((card['internal_price'],card['external_price']),(250050,400000))
        self.assertIsNone(card['total_cost'])
        self.assertIn('порция',c.units(self.c))
        self.assertTrue(all(r['kind']=='material' for r in c.stock(self.c)))
        self.assertEqual(c.stock_stamp(self.c),1)  # Saving a recipe never posts stock.
        page=self.client(2).get('/accounting/ztl/products/').get_data(as_text=True)
        self.assertIn('<option value="порция"',page)

    def test_bad_composition_rolls_back_name_materials_and_history(self):
        self.grant('ztl_catalog')
        asset=self.item('Шкаф','asset','шт.')
        before='\n'.join(self.c.iterdump())
        cases=[{'technician_fee':'oops'}, {'material_qty_0':'0'}, {'material_qty_0':'-1'},
               {'internal_price':'-1'}, {'external_price':'abc'}, {'internal_price':'0.001'},
               {'external_price':'NaN'}, {'internal_price':'Infinity'}, {'external_price':'1000000001'},
               {'material_unit_0':'__custom__','material_custom_unit_0':''},
               {'material_row':['0','1'],'material_1':str(asset),'material_qty_1':'1'},
               {'material_row':['0','0']}, {'material_0':''}, {'no_materials':'1'},
               {'material_row':['0','1'],'material_1':'new','material_name_1':'Пластик','material_qty_1':'1','material_unit_1':'г'}]
        for change in cases:
            with self.subTest(change=change):
                response=self.post('card',self.product_data(**change))
                self.assertEqual(response.status_code,400,response.get_data(as_text=True))
                self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_composition_existing_units_and_duplicate_items_are_protected(self):
        iid=self.item();self.grant('ztl_catalog');before='\n'.join(self.c.iterdump())
        cases=[dict(material_unit_0='кг'),dict(material_row=['0','1'],material_1=str(iid),material_qty_1='2')]
        for change in cases:
            data=self.product_data(material_0=str(iid),material_unit_0='м',material_name_0='')
            data.update(change)
            self.assertEqual(self.post('card',data).status_code,400)
            self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_unified_edit_name_composition_fee_does_not_reprice_old_order(self):
        iid=self.item();self.receipt(iid);self.card(iid)
        oid=self.order();original=c.production(self.c,oid);stamp=c.stock_stamp(self.c)
        work=ztl.row_by_id(self.c,'ztl_names',self.work)
        data=self.product_data(work_id=self.work,work_version=work['version'],version=2,name='Исправленное изделие',
             material_0=str(iid),material_name_0='',material_unit_0='м',material_qty_0='2',technician_fee='99')
        self.grant('ztl_catalog')
        self.assertEqual(self.post('card',data).status_code,303)
        self.assertEqual(ztl.row_by_id(self.c,'ztl_names',self.work)['name'],'Исправленное изделие')
        self.assertEqual(c.production(self.c,oid),original)
        self.assertEqual(c.stock_stamp(self.c),stamp)
        self.assertEqual(ztl.row_by_id(self.c,'ztl_orders',oid)['amount'],10000)
        before='\n'.join(self.c.iterdump())
        self.assertEqual(self.post('card',data).status_code,409)
        self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_products_screen_has_full_title_one_creation_form_and_materials_only(self):
        self.item('Только материал');self.item('Скрытый шкаф','asset','шт.')
        before='\n'.join(self.c.iterdump())
        page=self.client(1).get('/accounting/ztl/products/').get_data(as_text=True)
        self.assertIn('Реестр изделий (технологические карты)',page)
        self.assertEqual(page.count('Добавить изделие</button>'),1)
        self.assertNotIn('action="/accounting/ztl/control/item"',page)
        self.assertNotIn('name="kind"',page)
        self.assertNotIn('Скрытый шкаф',page)
        self.assertIn('+ Добавить материал',page)
        self.assertIn('+ Своя единица',page)
        self.assertIn('Сохранённые материалы для изготовления',page)
        self.assertIn('name="material_qty_0"',page)
        self.assertIn('name="internal_price"',page)
        self.assertIn('name="external_price"',page)
        self.assertLess(page.index('class="card ztl-product-new"'),page.index('aria-label="Список созданных изделий"'))
        receipts=self.client(1).get('/accounting/ztl/receipts/').get_data(as_text=True)
        self.assertIn('Добавить позицию для прихода',receipts)
        self.assertIn('Имущество (например, шкаф)',receipts)
        self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_archive_preserves_orders_and_blocks_new_orders_until_restore(self):
        iid=self.item();self.receipt(iid);self.card(iid)
        oid=self.order(completed_on='2026-11-16');saved=c.production(self.c,oid);stamp=c.stock_stamp(self.c)
        self.grant('ztl_catalog')
        self.assertEqual(self.post('product-action',self.product_operation(self.work,'archive')).status_code,303)
        before='\n'.join(self.c.iterdump())
        with self.assertRaisesRegex(ValueError,'убрано из ассортимента'):self.order()
        self.assertEqual('\n'.join(self.c.iterdump()),before)
        self.grant('ztl_orders')
        new=self.client(2).get('/accounting/ztl/order').get_data(as_text=True)
        self.assertNotIn('value="'+str(self.work)+'" >Работа',new)
        edit=self.client(2).get('/accounting/ztl/order?id='+str(oid)).get_data(as_text=True)
        self.assertIn('Работа — сохранённая позиция заказа',edit)
        self.assertEqual(self.order_http(oid,patient='Исправлен пациент').status_code,303)
        self.assertEqual(c.production(self.c,oid),saved)
        self.assertEqual(c.stock_stamp(self.c),stamp)
        self.grant('ztl_catalog')
        self.assertEqual(self.post('product-action',self.product_operation(self.work,'restore')).status_code,303)
        self.order()
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_orders').fetchone()[0],2)

    def test_delete_unused_product_keeps_history_and_never_reuses_id(self):
        c.save_card(self.c,self.product_data(),1)
        wid=next(r['id'] for r in c.catalog(self.c) if r['name']=='Новое изделие')
        self.grant('ztl_catalog')
        self.assertEqual(self.post('product-action',self.product_operation(wid,'delete')).status_code,303)
        self.assertFalse(self.c.execute('SELECT 1 FROM ztl_cards WHERE work_id=?',(wid,)).fetchone())
        self.assertFalse(self.c.execute('SELECT 1 FROM ztl_product_prices WHERE work_id=?',(wid,)).fetchone())
        self.assertFalse(self.c.execute('SELECT 1 FROM ztl_names WHERE id=?',(wid,)).fetchone())
        self.assertTrue(self.c.execute("SELECT 1 FROM ztl_history WHERE entity='Изделие ЗТЛ' AND entity_id=? AND action='Удаление'",(wid,)).fetchone())
        with self.c:newid=ztl.catalog_entry(self.c,'work','Следующее изделие',1)
        self.assertGreater(newid,wid)

    def test_prices_zero_unknown_edit_history_and_legacy_preservation(self):
        self.grant('ztl_catalog')
        self.assertEqual(self.post('card',self.product_data(internal_price='0',external_price=' ')).status_code,303)
        card=next(r for r in c.catalog(self.c) if r['name']=='Новое изделие')
        wid=card['id'];iid=card['parts'][0]['item_id']
        self.assertEqual((card['internal_price'],card['external_price']),(0,None))
        self.assertIsNone(card['total_cost'])  # Prices never replace unknown material cost.
        data=self.product_data(work_id=wid,work_version=card['work_version'],version=card['version'],
             material_0=str(iid),material_name_0='',internal_price='',external_price='1234.56')
        self.assertEqual(self.post('card',data).status_code,303)
        saved=next(r for r in c.catalog(self.c) if r['id']==wid)
        self.assertEqual((saved['internal_price'],saved['external_price']),(None,123456))
        history=self.c.execute("SELECT before_json,after_json FROM ztl_history WHERE entity='Технологическая карта' AND entity_id=? ORDER BY id DESC LIMIT 1",(wid,)).fetchone()
        self.assertEqual(json.loads(history['before_json'])['internal_price'],0)
        self.assertIsNone(json.loads(history['after_json'])['internal_price'])
        self.assertEqual(json.loads(history['after_json'])['external_price'],123456)
        # Earlier clients that omit the price controls must not wipe saved prices.
        c.save_card(self.c,dict(work_id=wid,version=saved['version'],technician_fee='150',**{'qty_'+str(iid):'2.5'}),1)
        saved=next(r for r in c.catalog(self.c) if r['id']==wid)
        self.assertEqual((saved['internal_price'],saved['external_price']),(None,123456))
        for operation in ('archive','restore'):
            self.assertEqual(self.post('product-action',self.product_operation(wid,operation)).status_code,303)
            self.assertEqual(self.c.execute('SELECT external_price FROM ztl_product_prices WHERE work_id=?',(wid,)).fetchone()[0],123456)
        before='\n'.join(self.c.iterdump())
        self.assertEqual(self.post('card',data).status_code,409)
        self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_incomplete_prices_request_is_atomic_and_catalog_prices_are_read_only(self):
        self.grant('ztl_catalog');before='\n'.join(self.c.iterdump())
        data=self.product_data();del data['external_price']
        self.assertEqual(self.post('card',data).status_code,400)
        self.assertEqual('\n'.join(self.c.iterdump()),before)
        self.assertEqual(self.post('card',self.product_data()).status_code,303)
        self.grant()
        before='\n'.join(self.c.iterdump())
        page=self.client(2).get('/accounting/ztl/products/').get_data(as_text=True)
        self.assertIn('2500.5 ₽',page)
        self.assertIn('4000 ₽',page)
        self.assertNotIn('data-product-form data-inline-save',page)
        self.assertEqual(self.post('card',self.product_data()).status_code,403)
        self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_new_price_schema_init_does_not_rewrite_existing_cards_or_create_prices(self):
        with self.c:self.c.execute('DELETE FROM ztl_product_prices')
        before='\n'.join(self.c.iterdump())
        self.c.execute('DROP TABLE ztl_product_prices');self.c.commit()
        c.init_schema(self.c)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ztl_product_prices').fetchone()[0],0)
        self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_product_actions_used_delete_stale_version_and_confirm_guards(self):
        self.order();self.grant('ztl_catalog');before='\n'.join(self.c.iterdump())
        for op,change,status in [('delete',{},400),('archive',{'confirm':'0'},400),('archive',{'work_version':999},409),('archive',{'version':999},409)]:
            data=self.product_operation(self.work,op);data.update(change)
            self.assertEqual(self.post('product-action',data).status_code,status)
            self.assertEqual('\n'.join(self.c.iterdump()),before)

    def test_material_error_correction_delete_and_id_history(self):
        iid=self.item('ааа',unit='ууу');self.grant('ztl_catalog')
        self.assertEqual(self.post('material-action',dict(id=iid,version=1,operation='edit',name='Пластик',unit='__custom__',custom_unit='порция')).status_code,303)
        item=ztl.row_by_id(self.c,'ztl_items',iid)
        self.assertEqual((item['name'],item['unit']),('Пластик','порция'))
        self.assertEqual(self.post('material-action',dict(id=iid,version=2,operation='delete',confirm='1')).status_code,303)
        self.assertGreater(self.item('Новый материал'),iid)

    def test_material_used_in_card_or_receipt_cannot_be_deleted_or_change_unit(self):
        iid=self.item();self.card(iid);other=self.item('Для черновика');self.receipt(other,paid='0')
        self.grant('ztl_catalog');before='\n'.join(self.c.iterdump())
        for material in (iid,other):
            for data in (dict(operation='delete',confirm='1'),dict(operation='edit',name='Новое имя',unit='г')):
                self.assertEqual(self.post('material-action',dict(id=material,version=1,**data)).status_code,400)
                self.assertEqual('\n'.join(self.c.iterdump()),before)
        self.assertEqual(self.post('material-action',dict(id=iid,version=1,operation='edit',name='Проволока новая',unit='м')).status_code,303)

    def test_new_actions_require_catalog_accounting_csrf_and_rollback_failed_history(self):
        iid=self.item();actions=[('card',self.product_data()),
                ('product-action',self.product_operation(self.work,'archive')),
                ('material-action',dict(id=iid,version=1,operation='delete',confirm='1'))]
        for rights in ((),('ztl_orders',),('ztl_receipts_edit',)):
            self.grant(*rights)
            for action,data in actions:self.assertEqual(self.post(action,data).status_code,403)
        self.grant('ztl_catalog')
        before='\n'.join(self.c.iterdump())
        for action,data in actions:
            self.assertEqual(self.client(2).post('/accounting/ztl/control/'+action,data=data).status_code,400)
            self.assertEqual(self.post(action,data,uid=3).status_code,403)
        original=ztl.log
        def fail_last(conn,entity,*args):
            if entity in ('Технологическая карта','Изделие ЗТЛ') or (entity=='Номенклатура ЗТЛ' and args[1]=='Удаление'):
                raise RuntimeError('history unavailable')
            return original(conn,entity,*args)
        with patch.object(ztl,'log',side_effect=fail_last):
            for action,data in actions:
                with self.assertRaisesRegex(RuntimeError,'history unavailable'):self.post(action,data)
                self.assertEqual('\n'.join(self.c.iterdump()),before)

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
        values.update(change_reason='Проверка изменения сохранённой записи',csrf='token',amount=c.display(values['amount'],100))
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
