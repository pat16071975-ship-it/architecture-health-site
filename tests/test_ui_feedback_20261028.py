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
PERIOD=(ROOT/'reports/period-view.js').read_text()
UPLOAD_SCRIPT=[s for s in re.findall(r'<script\b[^>]*>([\s\S]*?)</script>',UPLOAD) if 'const clinicalForm=' in s][0]
HELPER=UPLOAD_SCRIPT[UPLOAD_SCRIPT.index('  function uploadResultDetails('):UPLOAD_SCRIPT.index('  async function send(')]
DIALOG=re.search(r'<dialog id="uploadResultDialog"[\s\S]*?</dialog>',UPLOAD).group()
CSS=re.search(r'<style>([\s\S]*?)</style>',UPLOAD).group(1)
GROUP=PERIOD[PERIOD.index('  function groupInactiveDoctorRows('):PERIOD.index('  function renderDateComparison(')]
GROUP_CSS=PERIOD[PERIOD.index('    .date-compare-table tr.az-inactive-doctor'):PERIOD.index('    .cash-split{')]
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
            ('clinical','data-az-upload-form','completed'),('cash','data-az-cash-upload-form','cash'),('paid','data-az-paid-upload-form','paid_services'),('metrika','data-az-metrika-form','metrika_zip')])
        mock="window.requests=[];window.responses=[];window.fetch=async(url,options)=>{requests.push({url,body:Object.fromEntries(options.body.entries())});const r=responses.shift();if(!r)throw new Error('Unexpected request');return {ok:r.http!==500,json:async()=>{if(r.invalid)throw new Error('Invalid JSON');return r.payload}}};"
        self.page.set_content('<html lang="ru"><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:Arial;background:#f7f3ec}.btn{padding:10px;background:#44634f;color:white;border-radius:8px}'+CSS+'</style></head><body>'+forms+DIALOG+RECONCILE+'<script>'+mock+(UPLOAD_SCRIPT if actual else HELPER)+'</script></body></html>')

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
        for kind in ('clinical','cash','paid'):
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

    def group_fixture(self,rows=None,targets=None,selected='2026-10-27'):
        rows=rows or [
            {'name':'Нулевой А','kind':'dentists','values':[200,0,0]},
            {'name':'Активный Б','kind':'dentists','values':[0,50,0]},
            {'name':'Нет данных В','kind':'dentists','values':[70,None,0]},
            {'name':'Активный Г','kind':'dentists','values':[0,0,75]},
            {'name':'Нет данных Д','kind':'clinicDocs','values':[30,None,None]},
            {'name':'Активный Е','kind':'clinicDocs','values':[0,0,90]},
        ]
        targets=targets or ['2026-08-27','2026-09-27','2026-10-27']
        setup='''window.stickyCalls=0;function queueDateStickyClone(){stickyCalls++}function rawOrNull(v){return v===''||v==null||!isFinite(+v)?null:+v}function doctorPaidValue(date,type,name,original){return original}'''
        self.page.set_content('<html><head><style>body{font-family:Arial}.date-compare-table table{border-collapse:collapse}td{padding:8px;border:1px solid #ddd}'+GROUP_CSS+'</style></head><body><div class="date-compare-table"><table><tbody id="rows"></tbody></table></div><script>'+setup+GROUP+'</script></body></html>')
        self.page.evaluate('''({rows,targets,selected})=>{
            const body=document.getElementById('rows');
            window.months=targets.map(target=>({target,r:{dentists:{},clinicDocs:{}}}));
            for(const kind of ['dentists','clinicDocs']){
                const header=document.createElement('tr');header.className='section-heading';header.innerHTML='<td>'+kind+'</td>';body.appendChild(header);
                for(const row of rows.filter(x=>x.kind===kind)){
                    const tr=document.createElement('tr');tr.className=kind==='dentists'?'row-team-dent':'row-team-clinic';
                    const name=document.createElement('td');name.textContent=row.name;tr.appendChild(name);
                    row.values.forEach((value,index)=>{months[index].r[kind][row.name]=value;const cell=document.createElement('td');cell.textContent=value===null?'—':value+' ₽';tr.appendChild(cell)});
                    body.appendChild(tr);
                }
            }
            const total=document.createElement('tr');total.id='total';total.innerHTML='<td>Итог</td><td>777 ₽</td>';body.appendChild(total);
            window.originalRows=Array.from(body.querySelectorAll('.row-team-dent,.row-team-clinic')).map(row=>({node:row,html:row.innerHTML}));
            groupInactiveDoctorRows(body,months,selected);
        }''',{'rows':rows,'targets':targets,'selected':selected})

    def test_rows_move_to_bottom_of_each_section(self):
        self.group_fixture()
        labels=self.page.locator('#rows > tr > td:first-child').all_text_contents()
        self.assertEqual(labels[:6],['dentists','Активный Б','Активный Г','+ Нули / нет данных за 2 месяца (2)','Нулевой А','Нет данных В'])
        self.assertEqual(labels[6:],['clinicDocs','Активный Е','+ Нули / нет данных за 2 месяца (1)','Нет данных Д','Итог'])
        self.assertEqual(self.page.locator('.az-inactive-doctor[hidden]').count(),3)

    def test_history_nodes_values_and_totals_preserved(self):
        self.group_fixture()
        self.assertTrue(self.page.evaluate('originalRows.every(({node,html})=>node.isConnected&&node.innerHTML===html)'))
        self.assertEqual(self.page.locator('#total').inner_text(),'Итог\t777 ₽')
        self.assertTrue(self.page.evaluate('document.querySelector("#az-inactive-dentists-0").textContent.includes("200 ₽")'))

    def test_group_toggles_are_independent_and_keyboard_works(self):
        self.group_fixture()
        first=self.page.locator('.az-inactive-doctors-button').first
        first.focus();self.page.keyboard.press('Enter')
        self.assertEqual(first.get_attribute('aria-expanded'),'true')
        self.assertEqual(self.page.locator('.az-inactive-doctor[hidden]').count(),1)
        first.click();self.assertEqual(self.page.locator('.az-inactive-doctor[hidden]').count(),3)
        self.assertEqual(self.page.evaluate('stickyCalls'),2)

    def test_zero_unknown_combinations_and_negative_remain_visible(self):
        rows=[{'name':str(i),'kind':'dentists','values':values} for i,values in enumerate(([0,0],[None,0],[0,None],[None,None],[-10,0],[0,-1],[0,1],[1,0]))]
        self.group_fixture(rows,['2026-09-27','2026-10-27'])
        self.assertEqual(self.page.locator('.az-inactive-doctor').count(),4)
        self.assertEqual(self.page.locator('.row-team-dent:not([hidden]) td:first-child').all_text_contents(),['4','5','6','7'])

    def test_future_months_do_not_hide_active_doctor(self):
        self.group_fixture([{'name':'Активный','kind':'dentists','values':[50,0,None,None]}],['2026-09-27','2026-10-27','2026-11-27','2026-12-27'])
        self.assertEqual(self.page.locator('.az-inactive-doctors-toggle').count(),0)

    def test_fewer_than_two_months_no_guess(self):
        self.group_fixture([{'name':'Нулевой','kind':'dentists','values':[0]}],['2026-10-27'])
        self.assertEqual(self.page.locator('.az-inactive-doctors-toggle').count(),0)

    def test_all_inactive_stays_in_own_section(self):
        self.group_fixture([{'name':'А','kind':'dentists','values':[0,0]},{'name':'Б','kind':'clinicDocs','values':[None,0]}],['2026-09-27','2026-10-27'])
        self.assertEqual(self.page.locator('.az-inactive-doctors-toggle').count(),2)
        self.assertEqual(self.page.locator('.az-inactive-doctor[hidden]').count(),2)

    def test_scope_and_render_hook(self):
        self.assertIn('groupInactiveDoctorRows(tbody,months,date);',PERIOD)
        self.assertEqual(PERIOD.count('groupInactiveDoctorRows(tbody,months,date);'),1)
        self.assertNotIn('setTimeout(()=>location.reload(),900)',UPLOAD)
        self.assertIn('20261028-upload-doctor-groups-1',(ROOT/'reports/index.html').read_text())
        self.assertIn('const needs=out.requires_choice||out.requires_provider_mapping||(type!==\'clinical\'&&out.requires_review);',UPLOAD)
        for marker in ('expected_revision','expected_overlap_revision','provider_decisions','visit_direction_decisions'):
            self.assertIn(marker,UPLOAD)

if __name__=='__main__':unittest.main(verbosity=2)
