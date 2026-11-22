"""Owner control: real HTTP, transactional evidence and read-only drilldowns."""
import json
import os
os.environ.setdefault("AZBAZE_SECRET_KEY","local-test-only")
import sqlite3
import unittest
from datetime import date
from unittest.mock import patch

import app as core
import control_center as oversight
import ztl
import ztl_control as zc
from test_ztl_control import ControlTests


class OwnerControlTests(unittest.TestCase):
    client=ControlTests.client
    grant=ControlTests.grant
    item=ControlTests.item
    receipt=ControlTests.receipt
    card=ControlTests.card
    order=ControlTests.order
    product_data=ControlTests.product_data
    product_operation=ControlTests.product_operation

    def setUp(self):
        ControlTests.setUp(self)
        owner=patch.object(core,'OWNER_EMAIL','1@example.test');owner.start();self.addCleanup(owner.stop)
        self.grant('ztl_catalog','ztl_orders','ztl_payroll','ztl_receipts_edit','ztl_inventory_manage','ztl_inventory_count')
        self.editor=self.client(2);self.owner=self.client(1)
        for c in (self.editor,self.owner):
            with c.session_transaction() as s:s['csrf']='token'
        with self.c:
            self.c.execute("UPDATE users SET full_name=CASE id WHEN 1 THEN 'Владелец' WHEN 2 THEN 'Начальник ЗТЛ' ELSE 'Администратор' END")
            self.c.execute('UPDATE users SET is_admin=1 WHERE id=3')
        self.path='/control/ztl/?start=2000-01-01&end=2090-12-31'

    def snapshot(self):
        return '\n'.join(self.c.iterdump())

    def post(self,action,data,reason=None):
        fields={'csrf':'token',**data}
        if reason is not None:fields['change_reason']=reason
        return self.editor.post('/accounting/ztl/control/'+action,data=fields)

    def card_edit(self,**kw):
        c=self.c.execute('SELECT * FROM ztl_cards WHERE work_id=?',(self.work,)).fetchone()
        data=dict(work_id=self.work,version=c['version'],technician_fee='30',no_materials='1')
        data.update(kw);return data

    def rows(self):
        return list(oversight.events(self.c,date(2000,1,1),date(2090,12,31)))

    def access(self,uid=2,mode='view',**kw):
        return self.owner.post('/control/access/',data=dict(csrf='token',user_id=uid,mode=mode,stamp=oversight.grant_stamp(self.c),**kw))

    def test_permissions_home_owner_only_delegation_and_no_get_writes(self):
        before=self.snapshot()
        self.assertIn('id="control"',self.owner.get('/').get_data(as_text=True))
        for uid in (2,3):
            c=self.client(uid)
            self.assertNotIn('id="control"',c.get('/').get_data(as_text=True))
            for url in ('/control/',self.path,'/control/access/','/control/ztl/record/product/'+str(self.work)):
                self.assertEqual(c.get(url).status_code,403)
            with c.session_transaction() as s:s['csrf']='token'
            self.assertEqual(c.post('/control/access/',data=dict(csrf='token',user_id=uid,mode='review',stamp=oversight.grant_stamp(self.c))).status_code,403)
        for url in ('/control/',self.path,self.path+'&view=work','/control/access/'):
            response=self.owner.get(url);self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.client().get('/control/').status_code,302)
        self.assertEqual(self.access().status_code,303)
        self.assertIn('id="control"',self.editor.get('/').get_data(as_text=True))
        self.assertEqual(self.editor.get(self.path).status_code,200)
        self.assertEqual(self.editor.post('/control/ztl/review',data=dict(csrf='token')).status_code,403)
        self.assertEqual(self.access(mode='none').status_code,303)
        self.assertEqual(self.editor.get('/control/').status_code,403)

    def test_access_csrf_version_and_audit_failure_rollback(self):
        before=self.snapshot()
        self.assertEqual(self.owner.post('/control/access/',data=dict(user_id=2,mode='review',stamp=oversight.grant_stamp(self.c))).status_code,400)
        self.assertEqual(self.owner.post('/control/access/',data=dict(csrf='token',user_id=2,mode='review',stamp='old')).status_code,409)
        self.assertEqual(self.snapshot(),before)
        with self.c:self.c.execute("CREATE TRIGGER fail_grant BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'grant audit failure'); END")
        before=self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):self.access()
        self.assertEqual(self.snapshot(),before)

    def test_normal_admin_cannot_take_over_granted_user_or_owner_identity(self):
        self.assertEqual(self.access().status_code,303)
        admin=self.client(3)
        with admin.session_transaction() as s:s['csrf']='token'
        before=self.snapshot()
        for uid in (1,2):
            for suffix in ('edit','reset-password'):
                self.assertEqual(admin.post('/admin/users/'+str(uid)+'/'+suffix,data=dict(csrf='token',email='stolen@example.test',full_name='X')).status_code,403)
        self.assertEqual(admin.post('/admin/users/3/edit',data=dict(csrf='token',email='1@example.test',full_name='X',active='1',is_admin='1')).status_code,403)
        self.assertEqual(admin.post('/admin/users/new',data=dict(csrf='token',email='1@example.test',full_name='X')).status_code,403)
        self.assertEqual(self.snapshot(),before)

    def test_norm_change_reason_immediate_diff_and_historical_order_unchanged(self):
        item=self.item();self.receipt(item);self.card(item,qty='0.5')
        oid=self.order(pieces='2');original=dict(self.c.execute('SELECT * FROM ztl_production WHERE order_id=?',(oid,)).fetchone())
        moves=list(self.c.execute('SELECT * FROM ztl_moves'))
        data=self.card_edit(**{'qty_'+str(item):'0.6'})
        before=self.snapshot()
        for reason in (None,'  ','x'*4001):
            response=self.post('card',data,reason);self.assertEqual(response.status_code,400,response.data);self.assertEqual(self.snapshot(),before)
        response=self.post('card',data,'Уточнена норма после пробного изготовления');self.assertEqual(response.status_code,303,response.data)
        self.assertEqual(json.loads(self.c.execute('SELECT recipe FROM ztl_cards WHERE work_id=?',(self.work,)).fetchone()[0])[0]['qty'],600)
        self.assertEqual(dict(self.c.execute('SELECT * FROM ztl_production WHERE order_id=?',(oid,)).fetchone()),original)
        self.assertEqual(list(self.c.execute('SELECT * FROM ztl_moves')),moves)
        event=next(r for r in self.rows() if r['entity']=='Технологическая карта')
        self.assertEqual(event['reason'],'Уточнена норма после пробного изготовления')
        d=next(d for d in event['diff'] if 'Расход на изделие' in d['field'])
        self.assertEqual((d['before'],d['after'],d['delta']),('0.5','0.6','+0.1 / 20%'))
        self.assertTrue(event['attention'])
        self.assertEqual(event['actor'],'Начальник ЗТЛ')
        # Saving identical data only increments the existing business version; no false edit in oversight.
        self.assertEqual(self.post('card',self.card_edit(**{'qty_'+str(item):'0.6'})).status_code,303)
        self.assertFalse(next(r for r in self.rows() if r['entity']=='Технологическая карта')['meaningful'])

    def test_context_failure_rolls_back_all_changes_and_new_material(self):
        before=self.snapshot()
        with patch.object(oversight,'capture',side_effect=sqlite3.IntegrityError('metadata failure')):
            self.assertEqual(self.post('card',self.product_data()).status_code,409)
        self.assertEqual(self.snapshot(),before)
        with self.c:self.c.execute("CREATE TRIGGER fail_context BEFORE INSERT ON ztl_event_context BEGIN SELECT RAISE(ABORT,'context failure'); END")
        before=self.snapshot()
        self.assertEqual(self.post('card',self.card_edit(technician_fee='45'),'Исправлена расценка').status_code,409)
        self.assertEqual(self.snapshot(),before)

    def test_rename_archive_delete_reasons_and_immutable_labels(self):
        data=self.product_data(no_materials='1',material_row=[])
        self.assertEqual(self.post('card',data).status_code,303)
        wid=self.c.execute("SELECT id FROM ztl_names WHERE name='Новое изделие'").fetchone()[0]
        original=next(r for r in self.rows() if r['entity']=='Технологическая карта' and r['entity_id']==wid)
        for operation in ('archive','restore','delete'):
            data=self.product_operation(wid,operation);before=self.snapshot()
            self.assertEqual(self.post('product-action',data).status_code,400)
            self.assertEqual(self.snapshot(),before)
            self.assertEqual(self.post('product-action',data,'Проверка ассортимента').status_code,303)
        self.assertEqual(next(r for r in self.rows() if r['id']==original['id'])['title'],original['title'])
        response=self.owner.get('/control/ztl/record/product/'+str(wid));self.assertEqual(response.status_code,200,response.data)
        self.assertIn('Запись удалена',response.get_data(as_text=True))

    def test_receipt_posting_is_not_manual_norm_change_and_remains_atomic(self):
        item=self.item();draft=self.receipt(item,paid='0')
        data=dict(id=draft,version=1,supplier='Поставщик',reference=ztl.row_by_id(self.c,'ztl_receipts',draft)['reference'],received_on='2026-11-01',item_0=item,qty_0='10',value_0='100',paid='1',confirmed='1')
        self.assertEqual(self.post('receipt',data).status_code,303)
        auto=next(r for r in self.rows() if r['entity']=='Закупочная стоимость')
        self.assertTrue(auto['automatic']);self.assertFalse(oversight.needs_reason(auto['entity'],auto['action'],auto['before'],auto['after']))
        draft=self.receipt(item,paid='0');data.update(id=draft,reference=ztl.row_by_id(self.c,'ztl_receipts',draft)['reference'],qty_0='11')
        before=self.snapshot();self.assertEqual(self.post('receipt',data).status_code,400);self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.post('receipt',data,'Исправлено количество по документу').status_code,303)

    def test_review_roles_csrf_conflicts_and_transaction_rollback(self):
        hid=self.c.execute('SELECT MAX(id) FROM ztl_history').fetchone()[0]
        self.assertEqual(self.access(mode='review').status_code,303)
        form=dict(history_id=hid,version=0,status='question',comment='Уточнить основание')
        before=self.snapshot()
        self.assertEqual(self.editor.post('/control/ztl/review',data=form).status_code,400)
        self.assertEqual(self.snapshot(),before)
        form['csrf']='token'
        self.assertEqual(self.editor.post('/control/ztl/review',data=form).status_code,303)
        self.assertEqual(self.editor.post('/control/ztl/review',data=form).status_code,409)
        form.update(version=1,status='checked',comment='Проверено по документу')
        with self.c:self.c.execute("CREATE TRIGGER fail_review BEFORE INSERT ON control_review_history BEGIN SELECT RAISE(ABORT,'review failure'); END")
        before=self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):self.editor.post('/control/ztl/review',data=form)
        self.assertEqual(self.snapshot(),before)
        with self.c:self.c.execute('DROP TRIGGER fail_review')
        self.assertEqual(self.editor.post('/control/ztl/review',data=form).status_code,303)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM control_review_history').fetchone()[0],2)
        response=self.owner.get(self.path+'&status=checked');self.assertEqual(response.status_code,200,response.data)
        self.assertIn('Проверено по документу',response.get_data(as_text=True))

    def test_workload_unique_records_missing_zero_and_all_drilldowns_read_only(self):
        for price in ('0',''):
            data=self.product_data(name='Карта '+price,no_materials='1',material_row=[],internal_price=price,external_price='',technician_fee='0')
            self.assertEqual(self.post('card',data).status_code,303)
        with self.c:ztl.catalog_entry(self.c,'work','Без карты',1)
        data=self.card_edit(technician_fee='30')
        self.assertEqual(self.post('card',data,'Уточнение').status_code,303)
        self.assertEqual(self.post('card',self.card_edit()).status_code,303)
        work,quality=oversight.workload(self.c,self.rows(),2)
        self.assertEqual(work['created_products']['count'],2)
        self.assertEqual(work['edited_products']['count'],1)
        self.assertEqual(quality['zero_fee']['count'],2)
        self.assertEqual(quality['no_price']['count'],1)
        all_work,all_quality=oversight.workload(self.c,self.rows(),None)
        self.assertEqual(all_quality['missing_card']['count'],1)
        before=self.snapshot()
        for key,metric in (all_work|all_quality).items():
            response=self.owner.get(self.path+'&view=work&metric='+key);self.assertEqual(response.status_code,200,response.data)
            for r in metric['records']:
                response=self.owner.get('/control/ztl/record/'+r['kind']+'/'+str(r['id']));self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(self.snapshot(),before)

    def test_legacy_events_no_fake_reason_timezone_boundary_and_xss(self):
        with self.c:
            self.c.execute('DELETE FROM ztl_event_context')
            self.c.execute("UPDATE ztl_history SET changed_at='2026-11-21T17:00:00+00:00'")
        response=self.owner.get('/control/ztl/?start=2026-11-22&end=2026-11-22')
        self.assertEqual(response.status_code,200,response.data)
        self.assertIn('22.11.2026 00:00:00',response.get_data(as_text=True));self.assertIn('Ранее не запрашивалась',response.get_data(as_text=True))
        self.assertEqual(len(list(oversight.events(self.c,date(2026,11,21),date(2026,11,21)))),0)
        self.assertEqual(self.post('card',self.card_edit(),'<script>alert(1)</script>').status_code,303)
        body=self.owner.get(self.path).get_data(as_text=True)
        self.assertNotIn('<script>alert(1)</script>',body);self.assertIn('&lt;script&gt;',body)
        for qs in ('start=bad','start=2026-12-01&end=2026-01-01','end=9999-12-31','actor=bad','category=bad','status=bad'):
            self.assertEqual(self.owner.get('/control/ztl/?'+qs).status_code,400)


if __name__=='__main__':unittest.main()
