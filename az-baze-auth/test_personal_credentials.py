import sqlite3
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from cryptography.fernet import Fernet
import test_accounting_section as fixture
import app as core
import credentials_store as shared
import access_contacts_ext


class PersonalCredentialsTests(unittest.TestCase):
    client = fixture.AccountingSectionTests.client
    def setUp(self):
        fixture.AccountingSectionTests.setUp(self)
        self.key = core.DB_PATH.parent/'key'
        self.key.write_bytes(Fernet.generate_key())
        self.key.chmod(0o600)
        p = patch.object(shared, 'KEY_PATH', self.key)
        p.start(); self.addCleanup(p.stop)
        with self.web.app_context():
            shared.register_credentials(self.web)
            access_contacts_ext.register(self.web)
            core.db().execute('UPDATE users SET password_hash=?', (generate_password_hash('local-password'),))
            core.db().execute('DELETE FROM permissions WHERE user_id=3')
            core.db().commit()
        self.owner = self.client(3)
        self.csrf(self.owner)

    def csrf(self, client):
        with client.session_transaction() as s:
            s['csrf'] = 'test-csrf'
        return {'csrf': 'test-csrf'}

    def post(self, client, path, **data):
        return client.post('/personal-credentials/'+path, data={**self.csrf(client), **data})

    def create(self):
        result = self.post(self.owner, 'new', service_name='Private service', login='private-login', password='never-in-page', note='private-note', owner_id=1)
        self.assertEqual(result.status_code, 302)
        with sqlite3.connect(core.DB_PATH) as db:
            row = db.execute('SELECT id,owner_id,secret_token FROM personal_credentials ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual(row[1], 3)
        self.assertNotIn('never-in-page', row[2])
        return row[0]

    def test_default_access_and_shared_separation(self):
        home = self.owner.get('/').get_data(as_text=True)
        self.assertIn('href="/access-contacts/"', home)
        hub = self.owner.get('/access-contacts/').get_data(as_text=True)
        self.assertIn('/personal-credentials/', hub)
        self.assertNotIn('href="/credentials/"', hub)
        self.assertNotIn('href="/contacts/"', hub)
        for path in ['/credentials/', '/credentials/new', '/credentials/trash', '/contacts/']:
            self.assertEqual(self.owner.get(path).status_code,403)
        adminhub=self.client(1).get('/access-contacts/').get_data(as_text=True)
        self.assertIn('href="/credentials/"',adminhub)
        self.assertIn('href="/contacts/"',adminhub)
        self.assertIn('checked disabled',self.client(1).get('/admin/users/3/edit').get_data(as_text=True))

    def test_foreign_ids_are_denied_including_admin(self):
        ident=self.create()
        for uid in [1,2]:
            client=self.client(uid)
            for path in ['', 'trash']:
                page=client.get('/personal-credentials/'+path).get_data(as_text=True)
                self.assertNotIn('Private service',page)
                self.assertNotIn('private-login',page)
            for action in ['edit','delete']:
                self.assertEqual(client.get(f'/personal-credentials/{ident}/{action}').status_code,404)
                self.assertEqual(self.post(client,f'{ident}/{action}',version=1).status_code,404)
            for action in ['secret','restore']:
                self.assertEqual(self.post(client,f'{ident}/{action}',version=1,action='show').status_code,404)
        with sqlite3.connect(core.DB_PATH) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM credentials').fetchone()[0],0)

    def test_lifecycle_encryption_csrf_and_versions(self):
        ident=self.create()
        page=self.owner.get('/personal-credentials/').get_data(as_text=True)
        self.assertNotIn('never-in-page',page)
        self.assertIn('fetch(`/personal-credentials/',page)
        secret=self.post(self.owner,f'{ident}/secret',action='show')
        self.assertEqual(secret.json['password'],'never-in-page')
        self.assertEqual(secret.headers['Cache-Control'],'no-store')
        for path in ['new',f'{ident}/edit',f'{ident}/delete',f'{ident}/secret',f'{ident}/restore']:
            self.assertEqual(self.owner.post('/personal-credentials/'+path).status_code,400)
        self.assertEqual(self.post(self.owner,f'{ident}/edit',version=0).status_code,409)
        self.assertEqual(self.post(self.owner,f'{ident}/edit',version=1,service_name='New',login='me').status_code,302)
        self.assertEqual(self.post(self.owner,f'{ident}/secret',action='show').json['password'],'never-in-page')
        self.assertEqual(self.post(self.owner,f'{ident}/delete',version=2,current_password='local-password',service_confirmation='New').status_code,302)
        self.assertEqual(self.post(self.owner,f'{ident}/secret',action='show').status_code,404)
        self.assertNotIn('never-in-page',self.owner.get('/personal-credentials/trash').get_data(as_text=True))
        self.assertEqual(self.post(self.owner,f'{ident}/restore',version=2).status_code,409)
        self.assertEqual(self.post(self.owner,f'{ident}/restore',version=3).status_code,302)
        self.assertEqual(self.post(self.owner,f'{ident}/edit',version=4,clear_password='1').status_code,302)
        self.assertEqual(self.post(self.owner,f'{ident}/secret',action='show').json['password'],'')

    def test_auth_missing_schema_and_read_only_get(self):
        for path in ['', 'new','trash','1/edit','1/delete']:
            self.assertEqual(self.client().get('/personal-credentials/'+path).status_code,302)
        with sqlite3.connect(core.DB_PATH) as db:
            db.execute('UPDATE users SET active=0 WHERE id=2')
        self.assertEqual(self.client(2).get('/personal-credentials/').status_code,302)
        self.create()
        before=core.DB_PATH.read_bytes()
        for path in ['', 'new','trash','1/edit','1/delete']:
            self.assertEqual(self.owner.get('/personal-credentials/'+path).status_code,200)
        self.assertEqual(before,core.DB_PATH.read_bytes())
        with sqlite3.connect(core.DB_PATH) as db:
            db.execute('DROP TABLE personal_credentials')
        self.assertEqual(self.owner.get('/personal-credentials/').status_code,503)
        with sqlite3.connect(core.DB_PATH) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='personal_credentials'").fetchone())

    def test_existing_shared_records_unchanged_and_not_personal(self):
        with sqlite3.connect(core.DB_PATH) as db:
            db.execute("INSERT INTO credentials(service_name,secret_token,created_at,updated_at) VALUES('Shared only','','t','t')")
            before=db.execute('SELECT * FROM credentials').fetchall()
        self.create()
        self.assertNotIn('Shared only',self.owner.get('/personal-credentials/').get_data(as_text=True))
        sharedpage=self.client(1).get('/credentials/').get_data(as_text=True)
        self.assertIn('Shared only',sharedpage)
        self.assertNotIn('Private service',sharedpage)
        self.assertIn('fetch(`/credentials/',sharedpage)
        with sqlite3.connect(core.DB_PATH) as db:
            self.assertEqual(before,db.execute('SELECT * FROM credentials').fetchall())

    def test_nn_only_keeps_isolation_and_personal_entry(self):
        with sqlite3.connect(core.DB_PATH) as db:
            db.execute("INSERT INTO permissions(user_id,section) VALUES(3,'nn_reports')")
        page=self.owner.get('/').get_data(as_text=True)
        self.assertIn('href="/personal-credentials/"',page)
        self.assertNotIn('href="/reports/"',page)
        self.assertEqual(self.owner.get('/personal-credentials/').status_code,200)

    def test_foreign_trash_stays_private_and_owner_cannot_be_changed(self):
        ident=self.create()
        self.assertEqual(self.post(self.owner,f'{ident}/edit',version=1,service_name='Private service',owner_id=1).status_code,302)
        self.assertEqual(self.post(self.owner,f'{ident}/delete',version=2,current_password='local-password',service_confirmation='Private service').status_code,302)
        for uid in (1,2):
            client=self.client(uid)
            self.assertNotIn('Private service',client.get('/personal-credentials/trash').get_data(as_text=True))
            self.assertEqual(self.post(client,f'{ident}/restore',version=3).status_code,404)
            self.assertEqual(self.post(client,f'{ident}/secret',action='show').status_code,404)
        self.assertEqual(self.post(self.owner,f'{ident}/restore',version=3).status_code,302)

    def test_delete_confirmation_and_user_deletion(self):
        ident=self.create()
        for password,title in [('wrong','Private service'),('local-password','wrong')]:
            self.post(self.owner,f'{ident}/delete',version=1,current_password=password,service_confirmation=title)
            self.assertEqual(self.post(self.owner,f'{ident}/secret',action='show').status_code,200)
        with self.web.app_context():
            core.db().execute('DELETE FROM users WHERE id=3')
            core.db().commit()
            self.assertEqual(core.db().execute('SELECT COUNT(*) FROM personal_credentials').fetchone()[0],0)
        self.assertEqual(self.owner.get('/personal-credentials/').status_code,302)

    def test_anonymous_mutations_cannot_write(self):
        ident=self.create()
        for path in ['new',f'{ident}/edit',f'{ident}/delete',f'{ident}/restore',f'{ident}/secret']:
            self.assertEqual(self.post(self.client(),path,version=1,action='show').status_code,302)
