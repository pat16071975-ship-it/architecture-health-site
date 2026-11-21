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

    def credential_groups(self):
        return [r[0] for r in self.query("SELECT id FROM work_groups WHERE kind='credentials' ORDER BY id")]

    def credential(self):
        r = self.post('/credentials/new', group_id=str(self.credential_groups()[0]), service_name='Service', login='Login', password='secret-not-in-html', note='Note')
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
        self.assertEqual(self.post('/access-contacts/groups/contacts/',name='Third level',parent_id=str(child)).status_code,400)
        self.assertEqual(self.dump(),before)

    def test_group_duplicate_reserved_and_rename_version(self):
        self.contact()
        before=self.query('SELECT * FROM work_contact_groups')
        self.assertEqual(self.post('/access-contacts/groups/contacts/',action='rename',id='1',version='1',name='Моя IT').status_code,302)
        snapshot=self.dump()
        for data in [dict(action='rename',id='1',version='1',name='Stale'),dict(name='  МОЯ  IT '),dict(name='Без группы'),dict(name='x'*101)]:
            self.assertIn(self.post('/access-contacts/groups/contacts/',**data).status_code,(400,409))
            self.assertEqual(self.dump(),snapshot)
        self.assertEqual(self.query('SELECT * FROM work_contact_groups'),before)
        with sqlite3.connect(core.DB_PATH) as c: groups.init_schema(c)
        self.assertEqual(self.dump(),snapshot)

    def test_legacy_rows_visible_gets_do_not_write(self):
        ident=self.contact()
        with sqlite3.connect(core.DB_PATH) as c: c.execute('DELETE FROM work_contact_groups')
        before=self.dump(); raw=core.DB_PATH.read_bytes()
        for path in ['/contacts/','/contacts/new',f'/contacts/{ident}/edit','/credentials/','/credentials/new','/access-contacts/groups/contacts/','/contacts/import']:
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
        first, second, third = self.credential_groups()[:3]
        token=self.query('SELECT secret_token FROM credentials')[0][0]
        self.assertEqual(self.move([f'{ident}:{first}'],group=second,kind='credentials').status_code,302)
        self.assertEqual(self.post(f'/credentials/{ident}/edit',expected_group_id=str(second),group_id=str(third),service_name='Renamed',login='Login').status_code,302)
        self.assertEqual(self.query('SELECT secret_token FROM credentials')[0][0],token)
        html=self.admin.get('/credentials/').get_data(as_text=True)
        self.assertNotIn('secret-not-in-html',html)
        self.assertIn(f'data-group="{third}"',html)
        self.assertEqual(self.post(f'/credentials/{ident}/secret',action='show').json['password'],'secret-not-in-html')
        self.assertEqual(self.post(f'/credentials/{ident}/secret',client=self.editor,action='show').status_code,403)
        self.assertEqual(self.move([f'{ident}:{third}'],kind='personal-credentials').status_code,404)
        private=self.admin.get('/personal-credentials/new').get_data(as_text=True)
        self.assertNotIn('id="workGroup"',private)
        self.assertNotIn('id="workSearch"',self.admin.get('/personal-credentials/').get_data(as_text=True))
        with sqlite3.connect(core.DB_PATH) as c:
            c.execute('INSERT INTO credential_trash(credential_id,deleted_by,deleted_at) VALUES(?,1,?)',(ident,'t'))
        before=self.dump()
        self.assertEqual(self.move([f'{ident}:{third}'],group=second,kind='credentials').status_code,409)
        self.assertEqual(self.dump(),before)

    def test_permissions_csrf_and_view_only_no_actions(self):
        ident=self.contact()
        viewer=self.client(2); self.csrf(viewer)
        html=viewer.get('/contacts/').get_data(as_text=True)
        self.assertNotIn('id="groupMove"',html)
        self.assertNotIn('href="/access-contacts/groups/contacts/"',html)
        self.assertIn('id="workSearch"',html)
        before=self.dump()
        for client in (viewer,self.client()):
            for path in ('/access-contacts/groups/contacts/','/access-contacts/groups/contacts/move'):
                self.assertIn(client.post(path,data={'csrf':'test-csrf','name':'Denied','group_id':'1'}).status_code,(302,403))
        for path in ('/access-contacts/groups/contacts/','/access-contacts/groups/contacts/move','/contacts/new',f'/contacts/{ident}/edit'):
            self.assertEqual(self.admin.post(path,data={'group_id':'1'}).status_code,400)
        self.assertEqual(self.dump(),before)

    def test_audit_failure_rolls_back_contact_group_and_bulk(self):
        ident=self.contact()
        with sqlite3.connect(core.DB_PATH) as c:
            c.execute("CREATE TRIGGER audit_failure BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
        for path,data in [('/contacts/new',dict(group_id='new',new_group_name='Rollback',full_name='Name')),
                          ('/credentials/new',dict(group_id='new',new_group_name='Rollback',service_name='Name')),
                          ('/access-contacts/groups/contacts/',dict(name='Rollback'))]:
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

    def test_catalogues_and_inline_custom_groups_are_independent(self):
        for kind,name in [('contacts','Только контакты'),('credentials','Только доступы')]:
            url='/contacts/new' if kind=='contacts' else '/credentials/new'
            r=self.post(url,group_id='new',new_group_name=name,full_name='Person',service_name='Service')
            self.assertEqual(r.status_code,302)
        contacts=self.admin.get('/contacts/new').get_data(as_text=True)
        creds=self.admin.get('/credentials/new').get_data(as_text=True)
        self.assertIn('Только контакты',contacts);self.assertNotIn('Только доступы',contacts)
        self.assertIn('Только доступы',creds);self.assertNotIn('Только контакты',creds)
        for name in groups.CREDENTIAL_DEFAULTS:self.assertIn(name,creds)
        for name in groups.DEFAULTS:self.assertIn(name,contacts)
        for kind in ('contacts','credentials'):
            self.assertEqual(self.post('/access-contacts/groups/'+kind+'/',name='Одинаковое имя').status_code,302)
        before=self.query("SELECT * FROM work_groups WHERE kind='contacts'")
        ident=self.query("SELECT id FROM work_groups WHERE kind='credentials' AND name='Одинаковое имя'")[0][0]
        self.assertEqual(self.post('/access-contacts/groups/credentials/',action='rename',id=str(ident),version='1',name='Новое имя').status_code,302)
        self.assertEqual(self.query("SELECT * FROM work_groups WHERE kind='contacts'"),before)

    def test_cross_catalogue_create_edit_move_and_parent_are_rejected(self):
        contact=self.contact();credential=self.credential();cg=self.credential_groups()[0]
        before=self.dump()
        bad=[('/contacts/new',dict(group_id=str(cg),full_name='Wrong')),
             ('/credentials/new',dict(group_id='1',service_name='Wrong')),
             (f'/contacts/{contact}/edit',dict(group_id=str(cg),expected_group_id='1',full_name='Wrong')),
             (f'/credentials/{credential}/edit',dict(group_id='1',expected_group_id=str(cg),service_name='Wrong',password='replacement')),
             ('/contacts/new',dict(group_id='new',new_group_name='Wrong',new_group_parent=str(cg))),
             ('/credentials/new',dict(group_id='new',new_group_name='Wrong',new_group_parent='1')),
             ('/access-contacts/groups/contacts/',dict(action='rename',id=str(cg),version='1',name='Wrong')),
             ('/access-contacts/groups/credentials/',dict(action='rename',id='1',version='1',name='Wrong')),
             ('/access-contacts/groups/contacts/',dict(name='Wrong',parent_id=str(cg))),
             ('/access-contacts/groups/credentials/',dict(name='Wrong',parent_id='1'))]
        for url,data in bad:
            with self.subTest(url=url,data=data):
                self.assertEqual(self.post(url,**data).status_code,400)
                self.assertEqual(self.dump(),before)
        self.assertEqual(self.move([f'{contact}:1'],group=cg).status_code,400)
        self.assertEqual(self.move([f'{credential}:{cg}'],group=1,kind='credentials').status_code,400)
        self.assertEqual(self.dump(),before)
        data=_xlsx_bytes([ci.EXPECTED_HEADERS,['1','IT','Support','New person','89991234567']])
        response=self.admin.post('/contacts/import',data={'csrf':'test-csrf','group_id':str(cg),'file':(io.BytesIO(data),'contacts.xlsx')})
        self.assertIn('Группа не найдена в этом разделе',response.get_data(as_text=True))
        self.assertEqual(self.dump(),before)

    def test_scoped_pages_read_only_and_old_form_is_rejected(self):
        before=self.dump();raw=core.DB_PATH.read_bytes()
        for kind in ('contacts','credentials'):
            r=self.admin.get('/access-contacts/groups/'+kind+'/')
            self.assertEqual(r.status_code,200);self.assertIn(groups.LABELS[kind],r.get_data(as_text=True))
            self.assertEqual(self.client(2).get('/access-contacts/groups/'+kind+'/').status_code,403)
            self.assertEqual(self.admin.post('/access-contacts/groups/'+kind+'/',data={'name':'No csrf'}).status_code,400)
        self.assertEqual(self.post('/access-contacts/groups/',action='rename',id='1',version='1',name='Stale').status_code,409)
        self.assertEqual(self.admin.get('/access-contacts/groups/').location,'/access-contacts/groups/contacts/')
        self.assertEqual(self.admin.get('/access-contacts/groups/personal-credentials/').status_code,404)
        self.assertEqual(self.dump(),before);self.assertEqual(core.DB_PATH.read_bytes(),raw)

    def test_service_group_can_be_cleared_without_touching_secret(self):
        ident=self.credential();cg=self.credential_groups()[0]
        token=self.query('SELECT secret_token FROM credentials')[0][0]
        self.assertEqual(self.post(f'/credentials/{ident}/edit',expected_group_id=str(cg),group_id='',service_name='Ungrouped').status_code,302)
        self.assertEqual(self.query('SELECT * FROM work_credential_groups'),[])
        self.assertEqual(self.query('SELECT secret_token FROM credentials')[0][0],token)

    def test_legacy_get_does_not_migrate(self):
        with sqlite3.connect(core.DB_PATH) as c:
            c.executescript('DROP TABLE work_contact_groups; DROP TABLE work_credential_groups; DROP TABLE work_groups;')
            c.executescript(LEGACY_SCHEMA)
            c.execute("INSERT INTO work_groups(name,name_key) VALUES('Old','old')")
        before=self.dump()
        for path in ('/contacts/','/credentials/','/access-contacts/groups/contacts/','/access-contacts/groups/credentials/'):
            self.assertEqual(self.admin.get(path).status_code,503)
        self.assertEqual(self.dump(),before)


LEGACY_SCHEMA='''
CREATE TABLE work_groups(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,name_key TEXT NOT NULL,
 parent_id INTEGER REFERENCES work_groups(id) ON DELETE RESTRICT,version INTEGER NOT NULL DEFAULT 1);
CREATE UNIQUE INDEX work_groups_unique_name ON work_groups(COALESCE(parent_id,0),name_key);
CREATE TABLE work_contact_groups(record_id INTEGER PRIMARY KEY REFERENCES work_contacts(id) ON DELETE CASCADE,
 group_id INTEGER NOT NULL REFERENCES work_groups(id) ON DELETE RESTRICT);
CREATE TABLE work_credential_groups(record_id INTEGER PRIMARY KEY REFERENCES credentials(id) ON DELETE CASCADE,
 group_id INTEGER NOT NULL REFERENCES work_groups(id) ON DELETE RESTRICT);
'''

class GroupSeparationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:');self.addCleanup(self.c.close)
        self.c.execute('PRAGMA foreign_keys=ON')
        self.c.executescript('''
            CREATE TABLE work_contacts(id INTEGER PRIMARY KEY,full_name TEXT,phone TEXT);
            CREATE TABLE credentials(id INTEGER PRIMARY KEY,login TEXT,secret_token BLOB);
            CREATE TABLE credential_trash(credential_id INTEGER PRIMARY KEY REFERENCES credentials(id));
            CREATE TABLE personal_credentials(id INTEGER PRIMARY KEY,secret_token BLOB);
            INSERT INTO work_contacts VALUES(1,'Contact','+7 999 111-22-33'),(2,'Unassigned','');
            INSERT INTO credentials VALUES(1,'active',X'001122'),(2,'trash',X'334455'),(3,'unassigned',X'667788'),(4,'same name',X'99aabb');
            INSERT INTO credential_trash VALUES(2);
            INSERT INTO personal_credentials VALUES(1,X'ccdd');
        ''')
        self.c.executescript(LEGACY_SCHEMA)
        for ident,name,parent,version in [(1,'Старая группа',None,2),(2,'Подгруппа',1,4),(3,'Только контакты',None,3),(4,'Медицинские системы',None,5),(5,'Неиспользуемая своя',None,1)]:
            self.c.execute('INSERT INTO work_groups(id,name,name_key,parent_id,version) VALUES(?,?,?,?,?)',(ident,name,name.casefold(),parent,version))
        self.c.execute('INSERT INTO work_contact_groups VALUES(1,2)')
        self.c.executemany('INSERT INTO work_credential_groups VALUES(?,?)',[(1,2),(2,1),(4,4)])
        self.c.commit()

    def dump(self):return '\n'.join(self.c.iterdump())

    def test_preserves_records_contact_ids_paths_trash_and_custom_groups(self):
        c=self.c
        tables=('work_contacts','credentials','personal_credentials','credential_trash','work_contact_groups')
        before={t:c.execute('SELECT * FROM '+t).fetchall() for t in tables}
        old=c.execute('SELECT * FROM work_groups ORDER BY id').fetchall()
        groups.init_schema(c)
        self.assertEqual({t:c.execute('SELECT * FROM '+t).fetchall() for t in tables},before)
        self.assertEqual(c.execute("SELECT id,name,name_key,parent_id,version FROM work_groups WHERE kind='contacts' ORDER BY id").fetchall(),old)
        rows=c.execute("SELECT r.record_id,g.name,p.name,g.version,g.kind FROM work_credential_groups r JOIN work_groups g ON g.id=r.group_id LEFT JOIN work_groups p ON p.id=g.parent_id ORDER BY r.record_id").fetchall()
        self.assertEqual(rows,[(1,'Подгруппа','Старая группа',4,'credentials'),(2,'Старая группа',None,2,'credentials'),(4,'Медицинские системы',None,5,'credentials')])
        self.assertEqual(c.execute('SELECT count(*) FROM work_credential_groups WHERE record_id=3').fetchone()[0],0)
        roots=[r[0] for r in c.execute("SELECT name FROM work_groups WHERE kind='credentials' AND parent_id IS NULL")]
        self.assertTrue(set(groups.CREDENTIAL_DEFAULTS)<=set(roots));self.assertEqual(roots.count('Медицинские системы'),1)
        self.assertNotIn('Только контакты',roots);self.assertNotIn('Неиспользуемая своя',roots)
        self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_repeat_does_not_duplicate_or_reset_renamed_defaults(self):
        groups.init_schema(self.c)
        self.c.execute("UPDATE work_groups SET name='Моя медицина',name_key='моя медицина',version=version+1 WHERE kind='credentials' AND name='Медицинские системы'")
        self.c.commit();before=self.dump()
        groups.init_schema(self.c)
        self.assertEqual(self.dump(),before)

    def test_failure_during_membership_migration_rolls_back_schema_and_rows(self):
        self.c.execute("CREATE TRIGGER fail_move BEFORE UPDATE ON work_credential_groups BEGIN SELECT RAISE(ABORT,'simulated failure'); END")
        before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError):groups.init_schema(self.c)
        self.assertEqual(self.dump(),before)

    def test_caller_transaction_can_roll_back_preparation(self):
        before=self.dump();self.c.execute('BEGIN IMMEDIATE')
        groups.init_schema(self.c);self.c.rollback()
        self.assertEqual(self.dump(),before)

    def test_empty_legacy_catalogue_seeds_two_distinct_sets(self):
        self.c.execute('DELETE FROM work_contact_groups');self.c.execute('DELETE FROM work_credential_groups')
        self.c.execute('DELETE FROM work_groups WHERE parent_id IS NOT NULL');self.c.execute('DELETE FROM work_groups');self.c.commit()
        groups.init_schema(self.c)
        self.assertEqual(self.c.execute("SELECT name FROM work_groups WHERE kind='contacts' ORDER BY id").fetchall(),[(n,) for n in groups.DEFAULTS])
        self.assertEqual(self.c.execute("SELECT name FROM work_groups WHERE kind='credentials' ORDER BY id").fetchall(),[(n,) for n in groups.CREDENTIAL_DEFAULTS])

    def test_invalid_legacy_link_stops_atomically(self):
        self.c.execute('PRAGMA foreign_keys=OFF');self.c.execute('UPDATE work_credential_groups SET group_id=999 WHERE record_id=1');self.c.commit()
        before=self.dump()
        with self.assertRaises(ValueError):groups.init_schema(self.c)
        self.assertEqual(self.dump(),before)


if __name__=='__main__': unittest.main()
