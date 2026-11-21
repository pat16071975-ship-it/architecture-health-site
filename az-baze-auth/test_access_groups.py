"""HTTP regressions on an isolated database; no production data or secrets."""
import io
import sqlite3
import unittest
from unittest.mock import patch
from werkzeug.datastructures import MultiDict
import test_personal_credentials as fixture
import app as core
import access_groups as groups
import contact_excel_import as ci
from test_contact_excel_import import _xlsx_bytes


class WorkGroupsTests(unittest.TestCase):
    client = fixture.PersonalCredentialsTests.client
    csrf = fixture.PersonalCredentialsTests.csrf

    def setUp(self):
        fixture.PersonalCredentialsTests.setUp(self)
        self.admin = self.client(1)
        self.csrf(self.admin)
        with sqlite3.connect(core.DB_PATH) as c:
            c.executemany('INSERT INTO permissions(user_id,section) VALUES(?,?)',
                          [(2,'credentials_view'),(3,'credentials_view'),(3,'credentials_edit')])
        self.editor = self.client(3)
        self.csrf(self.editor)

    def post(self, path, client=None, **data):
        client = client or self.admin
        return client.post(path, data={**self.csrf(client), **data})

    def query(self, sql, args=()):
        with sqlite3.connect(core.DB_PATH) as c:
            return c.execute(sql, args).fetchall()

    def dump(self):
        with sqlite3.connect(core.DB_PATH) as c:
            return '\n'.join(c.iterdump())

    def contact(self, name='Contact', group=1):
        r = self.post('/contacts/new', group_id=str(group), full_name=name,
                      organization='Org', phone='+7 999 111-22-33', responsibility='Long responsibility')
        self.assertEqual(r.status_code,302)
        return self.query('SELECT MAX(id) FROM work_contacts')[0][0]

    def credential(self):
        r = self.post('/credentials/new', group_id='1', service_name='Service', login='Login', password='secret-not-in-html', note='Note')
        self.assertEqual(r.status_code,302)
        return self.query('SELECT MAX(id) FROM credentials')[0][0]

    def move(self, ids, group=2, kind='contacts', client=None):
        client = client or self.admin
        return client.post('/access-contacts/groups/'+kind+'/move', data=MultiDict([
            ('csrf','test-csrf'),('group_id',str(group)),*[('record',s) for s in ids]]))

    def test_required_group_and_invalid_ids_leave_no_writes(self):
        for raw in ('', '0', '99999', '-1', '1.0', '１', '9'*100):
            with self.subTest(raw=raw):
                before=self.dump()
                r=self.post('/contacts/new',group_id=raw,full_name='Must not save')
                self.assertEqual(r.status_code,400)
                self.assertEqual(self.dump(),before)
        self.assertEqual(self.post('/contacts/new',full_name='Missing').status_code,400)

    def test_inline_custom_group_and_subgroup_and_escaped_names(self):
        r=self.post('/contacts/new',client=self.editor,group_id='new',new_group_name='  Своя   группа  ',full_name='Person')
        self.assertEqual(r.status_code,302)
        ident=self.query("SELECT id FROM work_groups WHERE name='Своя группа'")[0][0]
        self.assertEqual(self.query('SELECT group_id FROM work_contact_groups'),[(ident,)])
        r=self.post('/contacts/new',group_id='new',new_group_name='<script>test</script>',new_group_parent=str(ident),full_name='Child')
        self.assertEqual(r.status_code,302)
        html=self.admin.get('/contacts/').get_data(as_text=True)
        self.assertIn('&lt;script&gt;test&lt;/script&gt;',html)
        self.assertNotIn('<script>test</script>',html)
        self.assertEqual(html.count('data-work-search='),2)
        child=self.query('SELECT id FROM work_groups WHERE parent_id=?',(ident,))[0][0]
        before=self.dump()
        self.assertEqual(self.post('/access-contacts/groups/',name='Third level',parent_id=str(child)).status_code,400)
        self.assertEqual(self.dump(),before)

    def test_group_duplicate_reserved_and_rename_version(self):
        self.contact()
        before=self.query('SELECT * FROM work_contact_groups')
        self.assertEqual(self.post('/access-contacts/groups/',action='rename',id='1',version='1',name='Моя IT').status_code,302)
        snapshot=self.dump()
        for data in [dict(action='rename',id='1',version='1',name='Stale'),dict(name='  МОЯ  IT '),dict(name='Без группы'),dict(name='x'*101)]:
            self.assertIn(self.post('/access-contacts/groups/',**data).status_code,(400,409))
            self.assertEqual(self.dump(),snapshot)
        self.assertEqual(self.query('SELECT * FROM work_contact_groups'),before)
        with sqlite3.connect(core.DB_PATH) as c: groups.init_schema(c)
        self.assertEqual(self.dump(),snapshot)

    def test_legacy_rows_visible_gets_do_not_write(self):
        ident=self.contact()
        with sqlite3.connect(core.DB_PATH) as c: c.execute('DELETE FROM work_contact_groups')
        before=self.dump(); raw=core.DB_PATH.read_bytes()
        for path in ['/contacts/','/contacts/new',f'/contacts/{ident}/edit','/credentials/','/credentials/new','/access-contacts/groups/','/contacts/import']:
            response=self.admin.get(path)
            self.assertEqual(response.status_code,200,path)
        html=self.admin.get('/contacts/').get_data(as_text=True)
        self.assertIn('data-group="0"',html)
        self.assertIn('Long responsibility',html)
        self.assertEqual(self.dump(),before)
        self.assertEqual(core.DB_PATH.read_bytes(),raw)

    def test_contact_edit_requires_group_and_rejects_stale_assignment(self):
        ident=self.contact()
        before=self.dump()
        self.assertEqual(self.post(f'/contacts/{ident}/edit',expected_group_id='1',full_name='Changed').status_code,400)
        self.assertEqual(self.dump(),before)
        self.assertEqual(self.move([f'{ident}:1']).status_code,302)
        after=self.dump()
        self.assertEqual(self.post(f'/contacts/{ident}/edit',expected_group_id='1',group_id='3',full_name='Stale').status_code,409)
        self.assertEqual(self.dump(),after)
        self.assertEqual(self.post(f'/contacts/{ident}/edit',expected_group_id='2',group_id='3',full_name='Allowed').status_code,302)
        self.assertEqual(self.query('SELECT group_id FROM work_contact_groups'),[(3,)])

    def test_bulk_move_only_changes_membership_preserves_full_records(self):
        a,b=self.contact('Alpha'),self.contact('Beta')
        originals=self.query('SELECT * FROM work_contacts')
        self.assertEqual(self.move([f'{a}:1',f'{b}:1'],client=self.editor).status_code,302)
        self.assertEqual(self.query('SELECT * FROM work_contacts'),originals)
        self.assertEqual(self.query('SELECT group_id FROM work_contact_groups'),[(2,),(2,)])
        for choices in [[f'{a}:2','99999:0'],[f'{a}:2',f'{b}:1'],[f'{a}:2',f'{a}:2'],[f'{a}:2',f'0{a}:2'],[],['bad']]:
            before=self.dump()
            self.assertIn(self.move(choices,3).status_code,(400,409))
            self.assertEqual(self.dump(),before)

    def test_credentials_preserve_secret_and_private_isolation(self):
        ident=self.credential()
        token=self.query('SELECT secret_token FROM credentials')[0][0]
        self.assertEqual(self.move([f'{ident}:1'],kind='credentials').status_code,302)
        self.assertEqual(self.post(f'/credentials/{ident}/edit',expected_group_id='2',group_id='3',service_name='Renamed',login='Login').status_code,302)
        self.assertEqual(self.query('SELECT secret_token FROM credentials')[0][0],token)
        html=self.admin.get('/credentials/').get_data(as_text=True)
        self.assertNotIn('secret-not-in-html',html)
        self.assertIn('data-group="3"',html)
        self.assertEqual(self.post(f'/credentials/{ident}/secret',action='show').json['password'],'secret-not-in-html')
        self.assertEqual(self.post(f'/credentials/{ident}/secret',client=self.editor,action='show').status_code,403)
        self.assertEqual(self.move([f'{ident}:3'],kind='personal-credentials').status_code,404)
        private=self.admin.get('/personal-credentials/new').get_data(as_text=True)
        self.assertNotIn('id="workGroup"',private)
        self.assertNotIn('id="workSearch"',self.admin.get('/personal-credentials/').get_data(as_text=True))
        with sqlite3.connect(core.DB_PATH) as c:
            c.execute('INSERT INTO credential_trash(credential_id,deleted_by,deleted_at) VALUES(?,1,?)',(ident,'t'))
        before=self.dump()
        self.assertEqual(self.move([f'{ident}:3'],kind='credentials').status_code,409)
        self.assertEqual(self.dump(),before)

    def test_permissions_csrf_and_view_only_no_actions(self):
        ident=self.contact()
        viewer=self.client(2); self.csrf(viewer)
        html=viewer.get('/contacts/').get_data(as_text=True)
        self.assertNotIn('id="groupMove"',html)
        self.assertNotIn('href="/access-contacts/groups/"',html)
        self.assertIn('id="workSearch"',html)
        before=self.dump()
        for client in (viewer,self.client()):
            for path in ('/access-contacts/groups/','/access-contacts/groups/contacts/move'):
                self.assertIn(client.post(path,data={'csrf':'test-csrf','name':'Denied','group_id':'1'}).status_code,(302,403))
        for path in ('/access-contacts/groups/','/access-contacts/groups/contacts/move','/contacts/new',f'/contacts/{ident}/edit'):
            self.assertEqual(self.admin.post(path,data={'group_id':'1'}).status_code,400)
        self.assertEqual(self.dump(),before)

    def test_audit_failure_rolls_back_contact_group_and_bulk(self):
        ident=self.contact()
        with sqlite3.connect(core.DB_PATH) as c:
            c.execute("CREATE TRIGGER audit_failure BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
        for path,data in [('/contacts/new',dict(group_id='new',new_group_name='Rollback',full_name='Name')),
                          ('/credentials/new',dict(group_id='new',new_group_name='Rollback',service_name='Name')),
                          ('/access-contacts/groups/',dict(name='Rollback'))]:
            before=self.dump()
            with self.assertRaises(sqlite3.IntegrityError): self.post(path,**data)
            self.assertEqual(self.dump(),before)
        before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError): self.move([f'{ident}:1'])
        self.assertEqual(self.dump(),before)

    def test_import_requires_group_and_does_not_move_duplicates(self):
        data=_xlsx_bytes([ci.EXPECTED_HEADERS,['1','IT','Support','Person','89991234567']])
        before=self.dump()
        r=self.admin.post('/contacts/import',data={'csrf':'test-csrf','file':(io.BytesIO(data),'contacts.xlsx')})
        self.assertIn('Выберите группу',r.get_data(as_text=True))
        self.assertEqual(self.dump(),before)
        for group in ('1','2'):
            with patch.object(ci,'backup_database',return_value='temporary backup'):
                r=self.admin.post('/contacts/import',data={'csrf':'test-csrf','group_id':group,'file':(io.BytesIO(data),'contacts.xlsx')})
            self.assertEqual(r.status_code,200)
        self.assertEqual(self.query('SELECT group_id FROM work_contact_groups'),[(1,)])
        self.assertEqual(len(self.query('SELECT * FROM work_contacts')),1)

    def test_import_rollback_includes_associations(self):
        contacts,_=ci.extract_contacts(_xlsx_bytes([ci.EXPECTED_HEADERS,['1','IT','Support','Person','89991234567']]))
        with sqlite3.connect(core.DB_PATH) as c:
            c.execute("CREATE TRIGGER audit_failure BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
        before=self.dump()
        with sqlite3.connect(core.DB_PATH) as c:
            c.row_factory=sqlite3.Row
            with self.assertRaises(sqlite3.IntegrityError): ci.import_contacts(c,contacts,1,'fixture',group_id=1)
        self.assertEqual(self.dump(),before)

    def test_missing_schema_get_fails_without_repair(self):
        with sqlite3.connect(core.DB_PATH) as c: c.execute('DROP TABLE work_contact_groups')
        before=self.dump()
        self.assertEqual(self.admin.get('/contacts/').status_code,503)
        self.assertEqual(self.dump(),before)


if __name__=='__main__': unittest.main()
