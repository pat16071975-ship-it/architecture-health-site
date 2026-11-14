import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('AZBAZE_SECRET_KEY', 'local-test-only')
import app as m


class AccountingSectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for key, value in [('DB_PATH', root/'auth.db'), ('SITE_ROOT', root)]:
            patcher = patch.object(m, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        (root/'index.html').write_text((Path(__file__).parents[1]/'az-baze/index.html').read_text())
        m.init_db_file(m.DB_PATH)
        with sqlite3.connect(m.DB_PATH) as c:
            for uid, admin in [(1,1),(2,0),(3,0)]:
                c.execute('INSERT INTO users(id,email,full_name,password_hash,is_admin,active,must_change_password,created_at,updated_at) VALUES(?,?,?,?,?,1,0,?,?)', (uid,f'{uid}@example.test','User','x',admin,'t','t'))
            c.executemany('INSERT INTO permissions(user_id,section) VALUES(?,?)',[(2,'accounting'),(3,'reports')])
        self.web = m.create_app()
        self.web.config['TESTING'] = True

    def client(self, uid=None):
        client = self.web.test_client()
        if uid:
            with client.session_transaction() as session: session['user_id']=uid
        return client

    def test_menu_and_routes_require_accounting_access(self):
        paths = ['/accounting/', '/accounting/ztl/', '/accounting/medical-supplies/', '/accounting/bookkeeping/']
        for uid in (1,2):
            client=self.client(uid)
            self.assertIn('id="accounting"', client.get('/').get_data(as_text=True))
            for path in paths: self.assertEqual(client.get(path).status_code,200)
        denied=self.client(3)
        self.assertNotIn('id="accounting"',denied.get('/').get_data(as_text=True))
        for path in paths:
            self.assertEqual(denied.get(path).status_code,403)
            self.assertEqual(self.client().get(path).status_code,302)
        self.assertEqual(self.client(2).get('/accounting/unknown/').status_code,404)

    def test_three_sections_empty_states_and_no_writes(self):
        client=self.client(2)
        before=m.DB_PATH.read_bytes()
        hub=client.get('/accounting/').get_data(as_text=True)
        for slug,label in m.ACCOUNTING_SECTIONS.items():
            self.assertIn(f'/accounting/{slug}/',hub)
            self.assertIn(label,hub)
            page=client.get(f'/accounting/{slug}/').get_data(as_text=True)
            self.assertIn('Данные пока не добавлены.',page)
            self.assertIn('href="/accounting/"',page)
        self.assertEqual(m.DB_PATH.read_bytes(),before)
        self.assertIn(('accounting','УЧЕТ'),m.SECTIONS)
        self.assertEqual(client.get('/reports/').status_code,403)
