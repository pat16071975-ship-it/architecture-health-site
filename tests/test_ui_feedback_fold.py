"""Actual-template browser tests with synthetic responses; no live services/data."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts' / 'az-ui-feedback-fold'

class ActualUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()
        env = Environment(loader=FileSystemLoader(ROOT / 'az-baze-auth/templates'), autoescape=select_autoescape())
        cls.upload_html = env.get_template('uploads.html').render(
            perms=['upload_services', 'upload_completed'], can_daily=True,
            can_replace=True, latest=None, next_required_date='2026-10-28',
            pending_providers=[], history=[], csrf='synthetic-csrf', request=SimpleNamespace(args={}),
            paid_services_latest_date='2026-10-27')
        cls.view = (ROOT / 'reports/period-view.js').read_text()
        cls.helpers = cls.view[cls.view.index('  const fmtMoney='):cls.view.index('  const escapeHtml=')]
        ARTIFACTS.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def upload_case(self, kind, status='imported', needs=False, error=False, width=1280):
        page = self.browser.new_page(viewport={'width':width, 'height':800})
        self.addCleanup(page.close)
        calls = {'page':0, 'commit':0, 'preview':0}
        period = {'from':'2026-10-01', 'to':'2026-10-27'}
        preview = {'status':'preview', 'period':period, 'counts':{}, 'revision':'synthetic',
                   'overlap_revision':'synthetic', 'unknown_providers':[], 'can_replace':True,
                   'requires_choice':needs, 'requires_review':needs,
                   'summary':{'paid':100,'dent':70,'structure':20,'lab':10}}
        def route_handler(route):
            url = route.request.url
            if url == 'https://az.test/uploads/':
                calls['page'] += 1
                route.fulfill(status=200, content_type='text/html', body=self.upload_html)
            elif url.endswith('/preview'):
                calls['preview'] += 1
                route.fulfill(status=200, json=preview)
            elif url.endswith('/commit') or url.endswith('/metrika/import'):
                calls['commit'] += 1
                payload = {'status':status, 'period':period, 'message':'Сервер подтвердил результат.'}
                if kind=='metrika':
                    payload={'status':status,'completed_days':['2026-10-27'],'reused':status=='duplicate', 'message':'Метрика сохранена.'}
                if error: payload={'status':'error','message':'Синтетическая ошибка сервера'}
                route.fulfill(status=409 if error else 200,json=payload)
            else: route.abort()
        page.route('**/*',route_handler)
        page.goto('https://az.test/uploads/')
        selector={'clinical':'[data-az-upload-form]','cash':'[data-az-cash-upload-form]',
                  'paid':'[data-az-paid-upload-form]','metrika':'[data-az-metrika-form]'}[kind]
        form=page.locator(selector)
        for i, inp in enumerate(form.locator('input[type=file]').all()):
            inp.set_input_files({'name':f'synthetic-{kind}-{i}.xlsx' if kind!='metrika' else 'synthetic-metrika.zip',
                                'mimeType':'application/octet-stream','buffer':b'synthetic'})
        form.locator('button[type=submit]').click()
        if needs:
            page.locator('#reconcileOverlay:not(.hidden)').wait_for()
            self.assertEqual(calls['commit'],0)
            self.assertTrue(page.locator('#azUploadModal').is_hidden())
            page.locator('#reconcileNew').click()
        if error:
            page.get_by_text('Синтетическая ошибка сервера',exact=True).wait_for()
            self.assertTrue(page.locator('#azUploadModal').is_hidden())
            self.assertEqual(calls['page'],1)
            return
        page.locator('#azUploadModal:not([hidden])').wait_for()
        title=page.locator('#azUploadModalTitle').inner_text()
        self.assertIn('ДУБЛЬ' if status=='duplicate' else 'ЗАМЕНЕНЫ' if status=='replaced' else 'ЗАГРУЖЕНЫ',title)
        text=page.locator('#azUploadModalMessage').inner_text()
        self.assertIn('synthetic-'+kind,text)
        if kind=='metrika':
            self.assertIn('Завершённые дни: 27.10.2026',text)
            self.assertNotIn('28.10.2026',text)
        else:
            self.assertIn('01.10.2026 — 27.10.2026',text)
        page.wait_for_timeout(1200)
        self.assertTrue(page.locator('#azUploadModal').is_visible())
        self.assertEqual(calls['page'],1,'No timed reload before acknowledgement')
        self.assertEqual(calls['commit'],1)
        page.keyboard.press('Tab')
        self.assertEqual(page.evaluate('document.activeElement.id'),'azUploadModalOk')
        if kind=='paid' and not needs and status=='imported':
            page.screenshot(path=str(ARTIFACTS / f'upload-confirm-{width}.png'))
        with page.expect_navigation(wait_until='domcontentloaded'):
            page.locator('#azUploadModalOk').click()
        page.wait_for_function("document.querySelector('#azUploadModal').hidden")
        self.assertEqual(calls['page'],2,'One reload only after explicit OK')
        self.assertEqual(calls['commit'],1,'Acknowledgement must not import twice')

    def test_success_all_four_upload_flows(self):
        for kind in ['clinical','cash','paid','metrika']:
            with self.subTest(kind=kind): self.upload_case(kind)

    def test_confirmed_conflict_all_three_flows(self):
        for kind in ['clinical','cash','paid']:
            with self.subTest(kind=kind): self.upload_case(kind,status='replaced',needs=True)

    def test_duplicate_is_not_success_message(self):
        for kind in ['paid','metrika']:
            with self.subTest(kind=kind): self.upload_case(kind,status='duplicate')

    def test_server_error_never_shows_success(self):
        for kind in ['clinical','cash','paid','metrika']:
            with self.subTest(kind=kind): self.upload_case(kind,error=True)

    def test_mobile_acknowledgement(self):
        for width in [320,390]:
            with self.subTest(width=width): self.upload_case('paid',width=width)

    def fold_case(self, previous=True, start='2026-10-01', end='2026-10-27', width=1280):
        page=self.browser.new_page(viewport={'width':width,'height':800})
        self.addCleanup(page.close)
        previous_end='2025-12-31' if end.startswith('2026-01') else '2026-09-30'
        previous_start=previous_end[:7]+'-01'
        periods=[{'from':start,'to':end,'dentists':{'D-active':0,'D-zero':0,'D-negative':0},'clinicDocs':{'C-zero':0,'C-active':25}}]
        if previous: periods.insert(0,{'from':previous_start,'to':previous_end,'dentists':{'D-active':100,'D-zero':0,'D-negative':-10},'clinicDocs':{'C-zero':0,'C-active':0}})
        periods.insert(0,{'from':'2025-08-01','to':'2025-08-31','dentists':{'D-zero':200},'clinicDocs':{'C-zero':10}})
        rows=''.join(f'<tr class="{cls}"><td>{name}</td><td>{old}</td><td>{now}</td></tr>' for cls,name,old,now in [
            ('row-team-dent','D-active','100 ₽','0 ₽'),('row-team-dent','D-zero','—','0 ₽'),
            ('row-team-dent','D-negative','-10 ₽','0 ₽'),('row-team-clinic','C-zero','—','0 ₽'),
            ('row-team-clinic','C-active','0 ₽','25 ₽')])
        page.set_content('<style>[hidden]{display:none!important}td{padding:10px}.btn{min-height:44px}</style><table><tbody id="dateCompareBody">'+rows+'</tbody></table>')
        page.add_script_tag(content='const num=v=>Number(v)||0; const dentists=[];const clinicDocs=[];const DOCTOR_PAID_SNAPSHOTS={};const DOCTOR_PAID_PERIODS='+json.dumps(periods)+';window.queueDateStickyClone=()=>{};')
        page.add_script_tag(content=self.helpers)
        before=page.evaluate('JSON.stringify(DOCTOR_PAID_PERIODS)')
        snapshot=page.locator('tbody').inner_text()
        page.evaluate('date=>foldInactiveDoctors(document.querySelector("tbody"),[{target:date,r:{},paidParts:[]}],date)',end)
        return page,before,snapshot

    def test_fold_two_directions_and_expand_back(self):
        page,before,_=self.fold_case()
        buttons=page.locator('.az-doctor-fold-toggle button')
        self.assertEqual(buttons.count(),2)
        self.assertEqual(page.locator('tr[data-az-doctor-fold][hidden]').count(),2)
        self.assertTrue(page.get_by_text('D-active',exact=True).is_visible())
        self.assertTrue(page.get_by_text('D-negative',exact=True).is_visible())
        self.assertTrue(page.get_by_text('C-active',exact=True).is_visible())
        page.screenshot(path=str(ARTIFACTS/'doctors-collapsed.png'))
        buttons.nth(0).click()
        self.assertTrue(page.get_by_text('D-zero',exact=True).is_visible())
        self.assertFalse(page.get_by_text('C-zero',exact=True).is_visible())
        self.assertEqual(buttons.nth(0).get_attribute('aria-expanded'),'true')
        page.screenshot(path=str(ARTIFACTS/'doctors-expanded.png'))
        buttons.nth(0).press('Enter')
        self.assertFalse(page.get_by_text('D-zero',exact=True).is_visible())
        self.assertEqual(page.evaluate('JSON.stringify(DOCTOR_PAID_PERIODS)'),before)
        self.assertIn('—',page.get_by_text('D-zero',exact=True).locator('..').inner_text(),'Displayed values must not be rewritten')

    def test_missing_previous_month_never_becomes_zero(self):
        page,before,_=self.fold_case(previous=False)
        self.assertEqual(page.locator('.az-doctor-fold-toggle').count(),0)
        self.assertTrue(page.get_by_text('D-zero',exact=True).is_visible())
        self.assertEqual(page.evaluate('JSON.stringify(DOCTOR_PAID_PERIODS)'),before)

    def test_incomplete_current_month_never_becomes_zero(self):
        page,_,_=self.fold_case(start='2026-10-02')
        self.assertEqual(page.locator('.az-doctor-fold-toggle').count(),0)

    def test_year_boundary(self):
        page,_,_=self.fold_case(start='2026-01-01',end='2026-01-27')
        self.assertEqual(page.locator('tr[data-az-doctor-fold][hidden]').count(),2)

    def test_mobile_fold(self):
        page,_,_=self.fold_case(width=390)
        self.assertEqual(page.locator('.az-doctor-fold-toggle button').count(),2)
        page.locator('.az-doctor-fold-toggle button').nth(0).click()
        self.assertTrue(page.get_by_text('D-zero',exact=True).is_visible())
        page.screenshot(path=str(ARTIFACTS/'doctors-mobile.png'))

    def test_no_upload_reload_timer_and_hook_present(self):
        source=(ROOT/'az-baze-auth/templates/uploads.html').read_text()
        self.assertNotIn('setTimeout(()=>location.reload(),900)',source)
        self.assertEqual(source.count('finishUpload('),4)
        self.assertIn('foldInactiveDoctors(tbody,months,date);',self.view)
        for text in ['expected_revision','expected_overlap_revision','provider_decisions','visit_direction_decisions',"type!=='clinical'&&out.requires_review"]:
            self.assertIn(text,source)
        self.assertIn('paidPatients',self.view)

if __name__=='__main__':
    unittest.main(verbosity=2)
