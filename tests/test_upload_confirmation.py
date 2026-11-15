"""Synthetic browser tests of the actual client source. No live server/data."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
UPLOAD=(ROOT/'az-baze-auth/templates/uploads.html').read_text()
UPLOAD_SCRIPT=[s for s in re.findall(r'<script\b[^>]*>([\s\S]*?)</script>',UPLOAD) if 'const completedForm=' in s][0]
HELPER=UPLOAD_SCRIPT[UPLOAD_SCRIPT.index('  function uploadResultDetails('):UPLOAD_SCRIPT.index('  async function send(')]
DIALOG=re.search(r'<dialog id="uploadResultDialog"[\s\S]*?</dialog>',UPLOAD).group()
CSS=re.search(r'<style>([\s\S]*?)</style>',UPLOAD).group(1)
RECONCILE=UPLOAD[UPLOAD.index('<div id="reconcileOverlay"'):UPLOAD.index("{% if 'upload_history' in perms %}")]

class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        args={'headless':True}
        if os.environ.get('AZ_TEST_CHROMIUM'):args['executable_path']=os.environ['AZ_TEST_CHROMIUM']
        cls.browser=cls.pw.chromium.launch(**args)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()

    def setUp(self):
        self.page=self.browser.new_page(viewport={'width':1280,'height':800})
        self.errors=[]
        self.page.on('pageerror',lambda e:self.errors.append(str(e)))

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors,[])

    def modal_fixture(self,actual=False):
        forms=''.join('<form id="'+kind+'" '+attr+'><input type="file" name="'+name+'"><button type="submit">Загрузить</button></form><div id="'+kind+'ClientResult"></div>' for kind,attr,name in [
            ('completed','data-az-completed-upload-form','completed'),('services','data-az-services-upload-form','services'),('cash','data-az-cash-upload-form','cash'),('paid','data-az-paid-upload-form','paid_services'),('metrika','data-az-metrika-form','metrika_zip')])
        mock="window.requests=[];window.responses=[];window.fetch=async(url,options)=>{requests.push({url,body:Object.fromEntries(options.body.entries())});const r=responses.shift();if(!r)throw new Error('Unexpected request');return {ok:r.http!==500,json:async()=>{if(r.invalid)throw new Error('Invalid JSON');return r.payload}}};"
        self.page.set_content('<html lang="ru"><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:Arial;background:#f7f3ec}.btn{padding:10px;background:#44634f;color:white;border-radius:8px}'+CSS+'</style></head><body>'+forms+'<button id="metrikaAutoLoad">Обновить</button>'+DIALOG+RECONCILE+'<script>'+mock+(UPLOAD_SCRIPT if actual else HELPER)+'</script></body></html>')

    def show(self,payload=None,files=None,fallback=None):
        if payload is None:payload={'status':'imported','message':'Сохранено.','period':{'from':'2026-10-01','to':'2026-10-27'}}
        self.page.evaluate('(args)=>showUploadResult(...args)',[payload,files or ['Синтетический отчёт.xlsx'],fallback])

    def submit(self,kind,responses):
        self.modal_fixture(actual=True)
        self.page.evaluate('(x)=>{window.responses=x}',responses)
        self.page.locator('#'+kind+' input').set_input_files({'name':'Синтетический.xlsx','mimeType':'application/octet-stream','buffer':b'synthetic fixture only'})
        self.page.locator('#'+kind+' button').click()

    def preview(self,**changes):
        return dict({'status':'preview','period':{'from':'2026-10-01','to':'2026-10-27'},'revision':'new','overlap_revision':'synthetic-revision','counts':{},'requires_choice':False,'requires_review':False,'requires_provider_mapping':False,'unknown_providers':[],'can_replace':True},**changes)

    def test_modal_persists_until_ok_and_reloads_once(self):
        self.modal_fixture();self.show()
        self.page.wait_for_timeout(1200)
        self.assertTrue(self.page.locator('#uploadResultDialog').evaluate('(e)=>e.open'))
        self.assertEqual(self.page.locator('#uploadResultTitle').inner_text(),'Данные загружены')
        self.assertEqual(self.page.locator('#uploadResultPeriod').inner_text(),'Период: 01.10.2026 — 27.10.2026')
        navigations=[]
        self.page.on('framenavigated',lambda frame:navigations.append(frame.url) if frame==self.page.main_frame else None)
        with self.page.expect_navigation():self.page.locator('#uploadResultOk').click()
        self.assertEqual(len(navigations),1)

    def test_escape_backdrop_focus(self):
        self.modal_fixture();self.show()
        self.assertEqual(self.page.evaluate('document.activeElement.id'),'uploadResultOk')
        self.page.keyboard.press('Escape');self.page.mouse.click(1,1)
        self.assertTrue(self.page.locator('#uploadResultDialog').evaluate('(e)=>e.open'))

    def test_duplicates_reused_and_error_confirmation(self):
        self.modal_fixture()
        for payload in ({'status':'duplicate'},{'status':'identical'},{'status':'ok','reused':True}):
            self.show(payload)
            self.assertEqual(self.page.locator('#uploadResultTitle').inner_text(),'Данные не изменены')
        self.page.locator('#uploadResultDialog').evaluate('(e)=>e.close()')
        for payload in ({},[],{'status':'error'},{'status':'pending'},{'ok':False}):
            self.assertTrue(self.page.evaluate('(x)=>{try{showUploadResult(x,[]);return false}catch{return !document.getElementById("uploadResultDialog").open}}',payload))

    def test_files_dates_injection_and_advertising_warning(self):
        self.modal_fixture()
        payload={'status':'imported','message':'<img src=x onerror="window.injected=1">','period':{'from':'2026-10-01','to':'2026-10-27'},'advertising_sync':{'status':'error'}}
        self.show(payload,['Файл 1.xlsx','<img src=x>.xlsx'],{'from':'2026-09-01','to':'2026-09-30'})
        self.assertEqual(self.page.locator('#uploadResultFiles').inner_text(),'Файл 1.xlsx\n<img src=x>.xlsx')
        self.assertEqual(self.page.locator('#uploadResultDialog img').count(),0)
        self.assertIn('завершилось с ошибкой',self.page.locator('#uploadResultMessage').inner_text())
        self.assertIn('01.10.2026',self.page.locator('#uploadResultPeriod').inner_text())

    def test_partial_days_not_invented(self):
        self.modal_fixture();self.show({'status':'ok','completed_days':['2026-10-23','2026-10-27']})
        self.assertEqual(self.page.locator('#uploadResultPeriod').inner_text(),'Загруженные дни: 23.10.2026, 27.10.2026')
        self.show({'status':'ok'})
        self.assertEqual(self.page.locator('#uploadResultPeriod').inner_text(),'Период не указан в ответе сервера.')

    def test_all_direct_upload_forms_actual_script(self):
        for kind in ('completed','services','cash','paid'):
            self.submit(kind,[{'payload':self.preview()},{'payload':{'status':'imported','message':'Сохранено.'}}])
            self.page.locator('#uploadResultDialog').wait_for(state='visible')
            self.assertEqual(self.page.locator('#uploadResultFiles').inner_text(),'Синтетический.xlsx')
            self.assertEqual(self.page.evaluate('requests.length'),2)
            self.assertIn('/commit',self.page.evaluate('requests[1].url'))
            self.assertEqual(self.page.evaluate('requests[1].body.decision'),'append')
            self.assertIn('01.10.2026',self.page.locator('#uploadResultPeriod').inner_text())
            self.page.wait_for_timeout(1000)
            self.assertTrue(self.page.locator('#uploadResultDialog').evaluate('(e)=>e.open'))

    def test_overlap_confirmation_actual_script(self):
        self.submit('paid',[{'payload':self.preview(requires_review=True,overlaps=[{'from':'2026-10-26','to':'2026-10-26','relation':'new_contains_saved'}],summary={'paid':100})},{'payload':{'status':'imported'}}])
        self.page.locator('#reconcileContinue').wait_for(state='visible')
        self.assertEqual(self.page.evaluate('requests.length'),1)
        self.page.locator('#reconcileContinue').click()
        self.page.locator('#uploadResultDialog').wait_for(state='visible')
        self.assertEqual(self.page.evaluate('requests[1].body.expected_overlap_revision'),'synthetic-revision')

    def test_keep_old_is_not_success_actual_script(self):
        self.submit('paid',[{'payload':self.preview(requires_choice=True)},{'payload':{'status':'duplicate','message':'Старая версия сохранена.'}}])
        self.page.locator('#reconcileKeep').click()
        self.page.locator('#uploadResultDialog').wait_for(state='visible')
        self.assertEqual(self.page.locator('#uploadResultTitle').inner_text(),'Данные не изменены')
        self.assertEqual(self.page.evaluate('requests[1].body.decision'),'keep_old')

    def test_metrika_actual_script(self):
        self.submit('metrika',[{'payload':{'status':'ok','completed_days':['2026-10-27']}}])
        self.page.locator('#uploadResultDialog').wait_for(state='visible')
        self.assertEqual(self.page.evaluate('requests.length'),1)
        self.assertEqual(self.page.locator('#uploadResultPeriod').inner_text(),'Загруженные дни: 27.10.2026')

    def test_invalid_and_failed_server_actual_script(self):
        for response in ({'invalid':True},{'http':500,'payload':{'message':'Ошибка сервера.'}},{'payload':{}}):
            self.submit('paid',[{'payload':self.preview()},response])
            self.page.locator('#paidClientResult.error').wait_for(state='visible')
            self.assertFalse(self.page.locator('#uploadResultDialog').evaluate('(e)=>e.open'))

    def test_manual_replacement_and_duplicate_on_each_split_form(self):
        for kind in ('completed','services','cash'):
            self.submit(kind,[{'payload':self.preview(requires_choice=True)}, {'payload':{'status':'replaced'}}])
            self.page.locator('#reconcileNew').click()
            self.page.locator('#uploadResultDialog').wait_for(state='visible')
            self.assertEqual(self.page.evaluate('requests[1].body.decision'),'use_new')
            self.assertIn('01.10.2026',self.page.locator('#uploadResultPeriod').inner_text())
            self.submit(kind,[{'payload':self.preview()}, {'payload':{'status':'duplicate'}}])
            self.page.locator('#uploadResultDialog').wait_for(state='visible')
            self.assertEqual(self.page.locator('#uploadResultTitle').inner_text(),'Данные не изменены')

    def test_auto_metrika_direct_result_and_unchanged(self):
        for changed in (False,True):
            self.modal_fixture(actual=True)
            payload={'status':'ok','completed_days':['2026-11-12'] if changed else [],
                     'current_day':'2026-11-13','current_updated':changed,
                     'current_reused':not changed,'direct_reused':not changed,
                     'direct_period':{'from':'2026-11-01','to':'2026-11-13'},
                     'direct_rows':12,'direct_campaigns':2}
            self.page.evaluate('(x)=>{responses=[{payload:x}]}',payload)
            self.page.locator('#metrikaAutoLoad').click()
            self.page.locator('#uploadResultDialog').wait_for(state='visible')
            self.assertEqual(self.page.evaluate('requests.length'),1)
            self.assertIn('13.11.2026',self.page.locator('#uploadResultPeriod').inner_text())
            self.assertIn('Директ:',self.page.locator('#uploadResultPeriod').inner_text())
            self.assertEqual(self.page.locator('#uploadResultTitle').inner_text(),
                             'Данные загружены' if changed else 'Данные не изменены')

    def defer_requests(self):
        self.page.evaluate("""() => {
          requests=[]; pending=[];
          window.fetch=(url,options)=>new Promise(resolve=>{
            requests.push({url,body:Object.fromEntries(options.body.entries())});
            pending.push(payload=>resolve({ok:true,json:async()=>payload}));
          });
        }""")

    def resolve_request(self, index, payload):
        self.page.evaluate('(x)=>pending[x.index](x.payload)',{'index':index,'payload':payload})

    def set_file(self, kind, name):
        self.page.locator('#'+kind+' input').set_input_files(
            {'name':name,'mimeType':'application/octet-stream','buffer':name.encode()})

    def fire_submit(self, kind):
        self.page.evaluate('(kind)=>document.getElementById(kind).dispatchEvent(new Event("submit",{cancelable:true}))',kind)

    def test_file_snapshot_survives_changes_during_preview_and_commit(self):
        for kind in ('completed','services','cash','paid','metrika'):
            self.modal_fixture(actual=True);self.defer_requests();self.set_file(kind,'A.xlsx')
            self.fire_submit(kind)
            self.page.wait_for_function('requests.length===1')
            self.set_file(kind,'B.xlsx')
            if kind!='metrika':
                self.resolve_request(0,self.preview())
                self.page.wait_for_function('requests.length===2')
                self.set_file(kind,'C.xlsx')
            self.resolve_request(0 if kind=='metrika' else 1,{'status':'imported'})
            self.page.locator('#uploadResultDialog').wait_for(state='visible')
            self.assertEqual(self.page.locator('#uploadResultFiles').inner_text(),'A.xlsx')
            self.assertEqual(self.page.evaluate('requests.map(r=>Object.values(r.body).find(v=>v instanceof File).name)'),
                             ['A.xlsx'] if kind=='metrika' else ['A.xlsx','A.xlsx'])

    def test_global_lock_rejects_all_starts_until_acknowledgement(self):
        for first in ('completed','services','cash','paid','metrika','auto'):
            self.modal_fixture(actual=True);self.defer_requests()
            if first=='auto':self.page.locator('#metrikaAutoLoad').click()
            else:self.set_file(first,'A.xlsx');self.fire_submit(first)
            self.page.wait_for_function('requests.length===1')
            for phase in ('pending','result'):
                if phase=='result':
                    if first not in ('metrika','auto'):
                        self.resolve_request(0,self.preview());self.page.wait_for_function('requests.length===2')
                    self.resolve_request(0 if first in ('metrika','auto') else 1,{'status':'ok','message':'FIRST'})
                    self.page.locator('#uploadResultDialog').wait_for(state='visible')
                before=self.page.evaluate('requests.length')
                for other in ('completed','services','cash','paid','metrika'):self.fire_submit(other)
                self.page.evaluate('document.getElementById("metrikaAutoLoad").dispatchEvent(new Event("click"))')
                self.assertEqual(self.page.evaluate('requests.length'),before)
                if phase=='result':self.assertEqual(self.page.locator('#uploadResultMessage').inner_text(),'FIRST')

    def test_review_keeps_snapshot_and_cannot_cancel_or_double_commit_in_flight(self):
        self.modal_fixture(actual=True);self.defer_requests();self.set_file('paid','A.xlsx');self.fire_submit('paid')
        self.resolve_request(0,self.preview(requires_choice=True))
        self.page.locator('#reconcileNew').wait_for(state='visible')
        self.set_file('paid','B.xlsx');self.page.locator('#reconcileNew').click()
        self.page.wait_for_function('requests.length===2')
        self.page.evaluate("""()=>{
          document.getElementById('reconcileNew').dispatchEvent(new Event('click'));
          document.getElementById('reconcileCancel').dispatchEvent(new Event('click'));
        }""")
        self.fire_submit('cash')
        self.assertEqual(self.page.evaluate('requests.length'),2)
        self.resolve_request(1,{'status':'replaced'})
        self.page.locator('#uploadResultDialog').wait_for(state='visible')
        self.assertEqual(self.page.locator('#uploadResultFiles').inner_text(),'A.xlsx')
        self.assertEqual(self.page.evaluate('requests[1].body.paid_services.name'),'A.xlsx')

    def test_error_and_review_cancel_release_global_lock(self):
        self.modal_fixture(actual=True);self.defer_requests();self.set_file('paid','A.xlsx');self.fire_submit('paid')
        self.resolve_request(0,{'status':'error','message':'ERROR'})
        self.page.locator('#paidClientResult.error').wait_for(state='visible')
        self.fire_submit('paid');self.page.wait_for_function('requests.length===2')
        self.resolve_request(1,self.preview(requires_choice=True))
        self.page.locator('#reconcileCancel').click()
        self.fire_submit('cash');self.page.wait_for_function('requests.length===3')

    def test_mobile_modal_layout(self):
        self.modal_fixture()
        for width,height in ((320,568),(360,640),(390,844),(430,932),(768,1024),(1440,900)):
            self.page.set_viewport_size({'width':width,'height':height});self.show(files=['Длинное_имя_'*20+'.xlsx'])
            rect=self.page.locator('#uploadResultDialog').bounding_box()
            self.assertGreaterEqual(rect['x'],0);self.assertLessEqual(rect['x']+rect['width'],width)
            self.assertLessEqual(rect['height'],height)
            self.page.locator('#uploadResultOk').scroll_into_view_if_needed()
            self.assertTrue(self.page.locator('#uploadResultOk').is_visible())
            self.page.locator('#uploadResultDialog').evaluate('(e)=>e.close()')

if __name__ == "__main__":
    unittest.main()
