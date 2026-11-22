"""Real server and temporary DB. Run separately from other environment fixtures."""
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from werkzeug.datastructures import MultiDict

BOOT = tempfile.TemporaryDirectory()
os.environ.setdefault('AZBAZE_SECRET_KEY', 'menu-order-tests-only')
os.environ['AZBAZE_DB'] = str(Path(BOOT.name) / 'seed.db')
os.environ['AZBAZE_CREDENTIALS_KEY'] = str(Path(BOOT.name) / 'credentials.key')
os.environ['AZBAZE_SITE_ROOT'] = str(Path(__file__).parents[1])
import app as core
core.init_db_file()
import server
import menu_order as ordering
import report_storage
import ztl_control

PATH = '/structure/clinics/menu-order/'
MENU_PAGES = {
    'home': '/', 'reports': '/reports/', 'accounting': '/accounting/',
    'ztl': '/accounting/ztl/?start=2026-08&end=2026-11',
    'medical': '/accounting/medical-supplies/?month=2026-11&tab=catalog',
    'access': '/access-contacts/', 'control': '/control/',
    'control_ztl': '/control/ztl/?start=2026-01-01&end=2026-11-17', 'nn': '/nn/',
}

class MenuFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        shutil.copyfile(Path(BOOT.name) / 'seed.db', root / 'auth.db')
        (root / 'index.html').write_bytes((Path(__file__).parents[1] / 'az-baze/index.html').read_bytes())
        shutil.copytree(Path(__file__).parents[1] / 'reports', root / 'reports')
        for obj, key, value in [(core, 'DB_PATH', root / 'auth.db'), (core, 'SITE_ROOT', root),
                                (core, 'OWNER_EMAIL', 'owner@example.test'), (report_storage, 'SITE_ROOT', root)]:
            p = patch.object(obj, key, value); p.start(); self.addCleanup(p.stop)
        self.web = server.app; self.web.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.conn = sqlite3.connect(core.DB_PATH); self.conn.row_factory = sqlite3.Row; self.addCleanup(self.conn.close)
        with self.conn:
            ordering.init_schema(self.conn)
            for uid, email, admin in [(1, 'owner@example.test', 1), (2, 'staff@example.test', 0),
                                      (3, 'admin@example.test', 1), (4, 'nn@example.test', 0)]:
                self.conn.execute('''INSERT INTO users(id,email,full_name,password_hash,is_admin,active,
                                     must_change_password,created_at,updated_at) VALUES(?,?,?,'x',?,1,0,'t','t')''',
                                  (uid, email, 'Тестовый пользователь', admin))
            self.conn.executemany('INSERT INTO permissions(user_id,section) VALUES(?,?)',
                [(1, 'structure_manage'), (1, 'surveys_create'), (2, 'accounting'), (2, 'reports'), (3, 'structure_manage'), (4, 'nn_reports')])
        self.owner = self.client(1)

    def client(self, uid=None):
        client = self.web.test_client()
        if uid:
            with client.session_transaction() as s: s['user_id'], s['csrf'] = uid, 'menu-test-token'
        return client

    def snapshot(self): return '\n'.join(self.conn.iterdump())

    def fields(self, menu, order=None):
        fields = MultiDict(dict(csrf='menu-test-token', version=str(ordering.state(self.conn, menu)['version']),
                                catalog=ordering.catalog_version(menu), action='save'))
        fields.setlist('item', order if order is not None else list(reversed(ordering.ids(menu))))
        return fields

    def post(self, menu, fields=None, client=None):
        return (client or self.owner).post(PATH + '?menu=' + menu, data=fields if fields is not None else self.fields(menu))

    def tiles(self, text, scope, path):
        parser = ordering.TileParser(text, path.split('?')[0]); parser.feed(text)
        return [t for g in parser.groups if g['menu'] == scope for t in g['tiles']]

class MenuOrderTests(MenuFixture, unittest.TestCase):
    def test_ordinary_entry_and_owner_access(self):
        self.assertIn('Порядок плашек разделов', self.owner.get('/structure/clinics/').text)
        self.assertNotIn('Порядок плашек разделов', self.client(3).get('/structure/clinics/').text)
        for uid, expected in [(None, 302), (2, 403), (3, 403), (4, 403), (1, 200)]:
            self.assertEqual(self.client(uid).get(PATH).status_code, expected)
        before = self.snapshot(); self.assertEqual(self.post('home', client=self.client(3)).status_code, 403)
        self.assertEqual(before, self.snapshot())

    def test_all_actual_menu_tiles_and_raw_contents(self):
        for menu, path in MENU_PAGES.items():
            with self.subTest(menu=menu):
                response = self.owner.get(path); self.assertEqual(response.status_code, 200)
                before = response.text; tiles = self.tiles(before, menu, path)
                self.assertEqual([t['key'] for t in tiles], ordering.ids(menu))
                self.assertEqual(self.post(menu).status_code, 303)
                after = self.owner.get(path).text; actual = self.tiles(after, menu, path)
                self.assertEqual([t['key'] for t in actual], list(reversed(ordering.ids(menu))))
                self.assertCountEqual([before[t['start']:t['end']] for t in tiles], [after[t['start']:t['end']] for t in actual])
                self.assertEqual(self.owner.get(path).headers['Cache-Control'], 'no-store')

    def test_shared_order_keeps_staff_permissions(self):
        for menu in ('home', 'ztl', 'access'):
            path = MENU_PAGES[menu]; before = self.client(2).get(path).text
            visible = [t['key'] for t in self.tiles(before, menu, path)]
            self.assertEqual(self.post(menu).status_code, 303)
            text = self.client(2).get(path).text
            self.assertEqual([t['key'] for t in self.tiles(text, menu, path)], [k for k in reversed(ordering.ids(menu)) if k in visible])
            self.assertNotIn('id="control"', text)
            if menu == 'ztl':
                self.assertNotIn('/ztl/receipts/', text); self.assertNotIn('/ztl/inventory/', text)
        for path in ('/admin', '/control/', '/nn/', '/accounting/ztl/receipts/'):
            self.assertEqual(self.client(2).get(path).status_code, 403)

    def test_nn_only_home_keeps_isolation(self):
        self.post('home'); self.post('nn'); response = self.client(4).get('/')
        self.assertEqual([t['key'] for t in self.tiles(response.text, 'nn', '/')], [k for k in reversed(ordering.ids('nn')) if k != 'uploads'])
        self.assertNotIn('Настройка клиник', response.text); self.assertNotIn('Контроль', response.text)
        self.assertEqual(self.client(4).get(PATH + '?menu=nn').status_code, 403)

    def test_gets_do_not_write(self):
        self.post('home'); self.post('ztl'); before = self.snapshot()
        for menu, path in MENU_PAGES.items():
            self.assertEqual(self.owner.get(PATH + '?menu=' + menu).status_code, 200)
            self.assertEqual(self.owner.get(path).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_csrf_and_unknown_menu(self):
        before = self.snapshot()
        for csrf in ('', 'wrong'):
            fields = self.fields('home'); fields['csrf'] = csrf
            self.assertEqual(self.post('home', fields).status_code, 400)
        self.assertEqual(self.owner.post(PATH + '?menu=unknown', data=self.fields('home')).status_code, 404)
        self.assertEqual(before, self.snapshot())

    def test_duplicate_missing_cross_menu_unknown_items(self):
        good = ordering.ids('home')
        for bad in ([], good[:-1], good + [good[0]], good[:-1] + [good[0]], good[:-1] + ['payroll'], good[:-1] + ['<script>']):
            before = self.snapshot(); self.assertEqual(self.post('home', self.fields('home', bad)).status_code, 400)
            self.assertEqual(self.snapshot(), before)

    def test_stale_form_and_replay(self):
        fields = self.fields('home'); self.assertEqual(self.post('home', fields).status_code, 303)
        before = self.snapshot(); self.assertEqual(self.post('home', fields).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_reset_version_prevents_stale_overwrite(self):
        original = self.owner.get('/accounting/').text; self.post('accounting'); stale = self.fields('accounting')
        reset = self.fields('accounting'); reset['action'] = 'reset'
        self.assertEqual(self.post('accounting', reset).status_code, 303)
        self.assertEqual(ordering.state(self.conn, 'accounting'), dict(order=None, version=2, damaged=False))
        self.assertEqual(self.owner.get('/accounting/').text, original)
        before = self.snapshot(); self.assertEqual(self.post('accounting', stale).status_code, 409)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.post('accounting').status_code, 303)
        self.assertEqual(ordering.state(self.conn, 'accounting')['version'], 3)

    def test_noop_and_initial_reset_no_writes(self):
        fields = self.fields('ztl'); fields['action'] = 'reset'; before = self.snapshot()
        self.assertEqual(self.post('ztl', fields).status_code, 303); self.assertEqual(before, self.snapshot())
        self.post('ztl'); before = self.snapshot()
        self.assertEqual(self.post('ztl').status_code, 303); self.assertEqual(before, self.snapshot())

    def test_changed_catalog_invalid_version_and_action(self):
        for key, value, status in [('catalog', 'old', 409), ('version', '', 400), ('version', '-1', 409), ('action', 'delete', 400)]:
            before = self.snapshot(); fields = self.fields('reports'); fields[key] = value
            self.assertEqual(self.post('reports', fields).status_code, status); self.assertEqual(before, self.snapshot())

    def test_audit_failure_rolls_back_and_success_preserves_other_tables(self):
        self.conn.execute("CREATE TRIGGER deny_menu_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'audit offline'); END")
        self.conn.commit(); before = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError): self.post('home')
        self.assertEqual(self.snapshot(), before)
        self.conn.execute('DROP TRIGGER deny_menu_audit'); self.conn.commit()
        protected = {r[0]: list(self.conn.execute('SELECT * FROM "' + r[0] + '"')) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('navigation_order','audit_log','sqlite_sequence')")}
        self.post('home')
        for name, rows in protected.items(): self.assertEqual(list(self.conn.execute('SELECT * FROM "' + name + '"')), rows)
        row = self.conn.execute("SELECT * FROM audit_log WHERE action='nn_navigation_order_save'").fetchone()
        self.assertEqual(row['actor_user_id'], 1)
        self.assertEqual(json.loads(row['details']), dict(menu='home', before=None, after=list(reversed(ordering.ids('home')))))

    def test_missing_schema_does_not_break_menus_or_write(self):
        self.conn.execute('DROP TABLE navigation_order'); self.conn.commit(); before = self.snapshot()
        self.assertEqual(self.owner.get('/').status_code, 200)
        self.assertIn('ещё не подготовлена', self.owner.get(PATH).text)
        self.assertEqual(self.post('home').status_code, 409); self.assertEqual(before, self.snapshot())

    def test_damaged_order_fallback_and_repair(self):
        for malformed in ('{', '{}', '[1]', '["reports", "reports"]'):
            self.conn.execute("INSERT OR REPLACE INTO navigation_order VALUES('home',?,1,1,'t')", (malformed,)); self.conn.commit()
            before = self.snapshot(); self.assertEqual(self.owner.get('/').status_code, 200)
            self.assertIn('повреждён', self.owner.get(PATH).text); self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.post('home').status_code, 303); self.assertFalse(ordering.state(self.conn, 'home')['damaged'])

    def test_catalogue_matches_ztl_accounting(self):
        self.assertEqual([(k, label) for k, label, _ in ordering.CATALOG['ztl'][1]], ztl_control.TABS)
        self.assertEqual([(k, label) for k, label, _ in ordering.CATALOG['accounting'][1]], list(core.ACCOUNTING_SECTIONS.items()))

    def test_private_menu_audit_remains_owner_only(self):
        self.post('home'); self.post('nn')
        admin = self.client(3).get('/admin').text
        self.assertNotIn('nn_navigation_order', admin)
        self.assertIn('nn_navigation_order', self.owner.get('/admin').text)

    def test_reprepare_schema_idempotent_without_data_changes(self):
        self.post('ztl'); before = self.snapshot()
        with self.conn: ordering.init_schema(self.conn)
        self.assertEqual(self.snapshot(), before)

    def test_non_menu_pages_unchanged(self):
        paths = ['/accounting/ztl/orders/', '/accounting/ztl/products/', '/accounting/ztl/inventory/', '/structure/clinics/', '/login']
        before = {path: self.owner.get(path).text for path in paths}
        for menu in ordering.CATALOG: self.post(menu)
        for path, original in before.items(): self.assertEqual(self.owner.get(path).text, original)

class ParserTests(unittest.TestCase):
    def test_raw_markup_and_hidden_tiles_preserved(self):
        a = '<a href="/accounting/ztl/" data-x="&quot;" hidden><span>ЗТЛ &amp; всё</span><br></a>'
        b = '<a href="/accounting/medical-supplies/" id="x">Медрасходка</a>'
        text = '<nav aria-label="Разделы учёта">'+a+'<!--keep-->\n'+b+'</nav><script>const x="<a href=/fake>";</script>'
        self.assertEqual(ordering.reorder(text, '/accounting/', lambda _: ['medical-supplies', 'ztl']), text.replace(a+'<!--keep-->\n'+b, b+'<!--keep-->\n'+a))

    def test_unknown_tiles_not_removed_new_known_tiles_append(self):
        text = '<a href="/accounting/ztl/">Outside</a><nav aria-label="Разделы учёта"><a href="/accounting/ztl/">Z</a><a href="/future/">Future</a><a href="/accounting/medical-supplies/">M</a><a href="/accounting/bookkeeping/">B</a></nav>'
        out = ordering.reorder(text, '/accounting/', lambda _: ['medical-supplies'])
        self.assertEqual(out.count('Outside'), 1); self.assertEqual(out.count('Future'), 1)
        p = ordering.TileParser(out, '/accounting/'); p.feed(out)
        self.assertEqual([t['key'] for t in p.groups[0]['tiles']], ['medical-supplies', 'ztl', 'bookkeeping'])

    def test_hidden_birthdays_placeholder_moves_without_becoming_visible(self):
        a = '<div class="menu-btn placeholder" data-section="section4" hidden aria-disabled="true">Дни рождения сотрудников</div>'
        b = '<a class="menu-btn" href="/reports/">Reports</a>'
        text = '<nav class="menu-column">' + b + a + '</nav>'
        result = ordering.reorder(text, '/', lambda _: ['birthdays', 'reports'])
        self.assertEqual(result, '<nav class="menu-column">' + a + b + '</nav>')

    def test_default_and_other_pages_byte_identical(self):
        text = '<nav aria-label="Разделы учёта"><a href="/accounting/ztl/">Z</a><a href="/accounting/bookkeeping/">B</a></nav>'
        self.assertEqual(ordering.reorder(text, '/accounting/', lambda _: None), text)
        self.assertEqual(ordering.reorder(text, '/elsewhere/', lambda _: ['bookkeeping']), text)

    def test_duplicate_unclosed_anchors_leave_original(self):
        for text in ['<nav aria-label="Разделы учёта"><a href="/accounting/ztl/">Z</a><a href="/accounting/ztl/">Z2</a></nav>', '<nav aria-label="Разделы учёта"><a href="/accounting/ztl/">Z</a><a href="/accounting/bookkeeping/">B']:
            self.assertEqual(ordering.reorder(text, '/accounting/', lambda _: ['bookkeeping']), text)

    def test_legacy_ztl_query_and_periods_preserved(self):
        text = '<nav aria-label="Разделы ЗТЛ"><a href="/accounting/ztl/?tab=orders&amp;start=2026-08">Orders</a><a href="/accounting/ztl/payroll/?start=2026-08&amp;end=2026-11">Payroll</a></nav>'
        out = ordering.reorder(text, '/accounting/ztl/', lambda _: ['payroll', 'orders'])
        self.assertLess(out.index('Payroll'), out.index('Orders')); self.assertIn('start=2026-08&amp;end=2026-11', out)

if __name__ == '__main__': unittest.main()
