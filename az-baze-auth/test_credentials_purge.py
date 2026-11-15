import os
import sqlite3
import unittest
from unittest.mock import patch
import test_personal_credentials as fixture
import app as core


class PurgeTests(unittest.TestCase):
    setUp = fixture.PersonalCredentialsTests.setUp
    client = fixture.PersonalCredentialsTests.client
    csrf = fixture.PersonalCredentialsTests.csrf
    post = fixture.PersonalCredentialsTests.post
    create = fixture.PersonalCredentialsTests.create

    def personal_trash(self):
        ident = self.create()
        self.assertEqual(self.post(self.owner,f'{ident}/delete',version=1,current_password='local-password',service_confirmation='Private service').status_code,302)
        return ident

    def shared_trash(self):
        with sqlite3.connect(core.DB_PATH) as c:
            ident=c.execute("INSERT INTO credentials(service_name,secret_token,created_at,updated_at) VALUES('Shared','encrypted','t','u')").lastrowid
            c.execute('INSERT INTO credential_trash(credential_id,deleted_by,deleted_at) VALUES(?,1,?)',(ident,'d'))
        return ident

    def grants(self,uid,*keys):
        with sqlite3.connect(core.DB_PATH) as c:
            c.execute('DELETE FROM permissions WHERE user_id=?',(uid,))
            c.executemany('INSERT INTO permissions(user_id,section) VALUES(?,?)',[(uid,k) for k in keys])

    def shared_post(self,client,ident,**changes):
        return client.post(f'/credentials/{ident}/purge',data={**self.csrf(client),'updated_at':'u','deleted_at':'d','current_password':'local-password','purge_confirmation':'УДАЛИТЬ НАВСЕГДА Shared',**changes})

    def personal_post(self,ident,client=None,**changes):
        return self.post(client or self.owner,f'{ident}/purge',**{'version':2,'current_password':'local-password','purge_confirmation':'УДАЛИТЬ НАВСЕГДА Private service',**changes})

    def exists(self,table,ident):
        with sqlite3.connect(core.DB_PATH) as c:return c.execute(f'SELECT 1 FROM {table} WHERE id=?',(ident,)).fetchone() is not None

    def test_personal_owner_can_purge_with_no_shared_permissions(self):
        ident=self.personal_trash()
        self.assertIn(f'/personal-credentials/{ident}/purge',self.owner.get('/personal-credentials/trash').get_data(as_text=True))
        page=self.owner.get(f'/personal-credentials/{ident}/purge').get_data(as_text=True)
        self.assertNotIn('never-in-page',page)
        self.assertIn('/personal-credentials/trash',page)
        self.assertEqual(self.personal_post(ident).status_code,302)
        self.assertFalse(self.exists('personal_credentials',ident))
        self.assertEqual(self.post(self.owner,f'{ident}/restore',version=2).status_code,404)
        with sqlite3.connect(core.DB_PATH) as c:
            log=c.execute("SELECT actor_user_id,details FROM audit_log WHERE action='personal_credential_purged'").fetchone()
            self.assertEqual(log,(3,f'credential_id={ident}'))

    def test_personal_other_users_including_admin_denied(self):
        ident=self.personal_trash()
        for uid in (1,2):
            client=self.client(uid)
            self.assertEqual(client.get(f'/personal-credentials/{ident}/purge').status_code,404)
            self.assertEqual(self.personal_post(ident,client).status_code,404)
        self.assertTrue(self.exists('personal_credentials',ident))

    def test_personal_confirmations_csrf_version_and_active_guard(self):
        ident=self.create()
        self.assertEqual(self.owner.get(f'/personal-credentials/{ident}/purge').status_code,404)
        self.post(self.owner,f'{ident}/delete',version=1,current_password='local-password',service_confirmation='Private service')
        for changes in ({'current_password':'wrong'},{'purge_confirmation':'wrong'}):
            self.assertEqual(self.personal_post(ident,**changes).status_code,200)
            self.assertTrue(self.exists('personal_credentials',ident))
        self.assertEqual(self.personal_post(ident,csrf='wrong').status_code,400)
        self.assertEqual(self.personal_post(ident,version=1).status_code,409)
        self.assertEqual(self.personal_post(ident,self.client()).status_code,302)
        self.post(self.owner,f'{ident}/restore',version=2)
        self.assertEqual(self.personal_post(ident).status_code,404)
        self.assertTrue(self.exists('personal_credentials',ident))

    def test_shared_requires_explicit_flag_even_admin_and_legacy_allowlist(self):
        ident=self.shared_trash()
        with patch.dict(os.environ,{'AZBAZE_CREDENTIALS_PURGE_EMAILS':'1@example.test,2@example.test'}):
            for uid in (1,2):
                self.grants(uid,'credentials_view','credentials_delete')
                client=self.client(uid)
                self.assertNotIn(f'/credentials/{ident}/purge',client.get('/credentials/trash').get_data(as_text=True))
                self.assertEqual(self.shared_post(client,ident).status_code,403)
        for keys in [('credentials_purge',),('credentials_view','credentials_purge'),('credentials_delete','credentials_purge')]:
            self.grants(2,*keys)
            self.assertEqual(self.shared_post(self.client(2),ident).status_code,403)
        self.assertTrue(self.exists('credentials',ident))

    def test_shared_permission_form_save_and_success(self):
        with self.web.test_request_context(method='POST',data={'perm_credentials_purge':'1','perm_credentials_view':'1','perm_credentials_delete':'1'}):
            core.set_permissions(2,core.selected_permissions())
            core.db().commit()
        ident=self.shared_trash();client=self.client(2)
        self.assertIn(f'/credentials/{ident}/purge',client.get('/credentials/trash').get_data(as_text=True))
        self.assertIn('name="perm_credentials_purge"',self.client(1).get('/admin/users/2/edit').get_data(as_text=True))
        self.assertEqual(self.shared_post(client,ident).status_code,302)
        self.assertFalse(self.exists('credentials',ident))
        with sqlite3.connect(core.DB_PATH) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM credential_trash').fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit_log WHERE action='credential_purged'").fetchone()[0],1)

    def test_shared_confirmations_and_stale_restore(self):
        self.grants(2,'credentials_view','credentials_delete','credentials_purge')
        ident=self.shared_trash();client=self.client(2)
        for changes in ({'current_password':'wrong'},{'purge_confirmation':'wrong'}):
            self.assertEqual(self.shared_post(client,ident,**changes).status_code,200)
            self.assertTrue(self.exists('credentials',ident))
        self.assertEqual(self.shared_post(client,ident,csrf='wrong').status_code,400)
        self.assertEqual(self.shared_post(client,ident,deleted_at='old').status_code,409)
        with sqlite3.connect(core.DB_PATH) as c:c.execute('DELETE FROM credential_trash WHERE credential_id=?',(ident,))
        self.assertEqual(self.shared_post(client,ident).status_code,404)
        with sqlite3.connect(core.DB_PATH) as c:c.execute("INSERT INTO credential_trash VALUES(?,1,'new')",(ident,))
        self.assertEqual(self.shared_post(client,ident).status_code,409)
        self.assertTrue(self.exists('credentials',ident))

    def test_audit_failure_rolls_back_both_deletions(self):
        personal=self.personal_trash();shared=self.shared_trash()
        self.grants(2,'credentials_view','credentials_delete','credentials_purge')
        with sqlite3.connect(core.DB_PATH) as c:
            c.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON audit_log WHEN NEW.action IN ('credential_purged','personal_credential_purged') BEGIN SELECT RAISE(ABORT,'test audit failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.personal_post(personal)
        with self.assertRaises(sqlite3.IntegrityError):self.shared_post(self.client(2),shared)
        self.assertTrue(self.exists('personal_credentials',personal))
        self.assertTrue(self.exists('credentials',shared))
