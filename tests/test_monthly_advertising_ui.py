"""Run the shipped monthly expense fragment with synthetic network responses."""
import json
import os
import re
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'az-baze-auth/templates/advertising_costs_fragment.html').read_text()
HTML=re.sub(r'{%.*?%}','',HTML).replace('{{ csrf }}','test-csrf')
STATE=dict(month='2026-11',records=[],sources=[dict(key='yandex_ads',name='Яндекс-реклама')],fixed=3000,direct=100,automatic_total=3100,confirmed=None,confirmed_total=None,direct_complete=False,direct_through='2026-11-15',revision='rev1',history=[],channels=[dict(source_key='instagram',source_name='Instagram',channel_name='Социальные сети',record_ids=[],amount=None,fixed_total=None,direct=None,campaigns=[dict(campaign_key=k,source_key='instagram',campaign_id=k,campaign_name=n,record_ids=[],amount=None,direct=None) for k,n in [('clinic','Instagram клиники'),('zubachev','Instagram Зубачёва'),('starostenko','Instagram Старостенко')]]),dict(source_key='yandex_ads',source_name='Яндекс-реклама',channel_name='Платная реклама',record_ids=[],amount=None,fixed_total=None,direct=100,campaigns=[dict(campaign_key='direct1',source_key='yandex_ads',campaign_id='715444498',campaign_name='Ортодонтия',record_ids=[],amount=None,direct=100)])])

class MonthlyBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        args={'headless':True}
        if os.environ.get('CHROMIUM_PATH'):args['executable_path']=os.environ['CHROMIUM_PATH']
        cls.browser=cls.pw.chromium.launch(**args)
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.pw.stop()
    def setUp(self):
        self.page=self.browser.new_page(viewport={'width':1280,'height':850})
        self.errors=[];self.page.on('pageerror',lambda e:self.errors.append(str(e)))
        mock='''window.fixture=STATE;window.posts=[];window.hold=false;window.reject=false;
window.fetch=async(url,options={})=>{if(options.method==='POST'){posts.push(Object.fromEntries(options.body.entries()));if(hold)await new Promise(r=>window.release=r);if(window.reject)return {ok:false,json:async()=>({message:'Данные изменились'})};return {ok:true,json:async()=>({status:'ok'})};}return {ok:true,json:async()=>fixture};};'''.replace('STATE',json.dumps(STATE))
        self.page.set_content('<html lang="ru"><body><script>'+mock+'</script>'+HTML+'</body></html>')
        self.page.evaluate("document.getElementById('adMonth').value='11';document.getElementById('adYear').value='2026'")
        self.page.locator('#adCostToggle').click()
        self.page.wait_for_function("document.getElementById('adAutomatic').textContent.includes('3')")
    def tearDown(self):self.page.close();self.assertEqual(self.errors,[])
    def test_accept_month_and_partial_direct_warning(self):
        self.assertIn('неполное',self.page.locator('#adDirectCoverage').inner_text())
        self.assertEqual(self.page.locator('input[type=date]').count(),0)
        self.page.locator('#adAccept').click()
        post=self.page.evaluate('posts[0]')
        self.assertEqual((post['mode'],post['csrf'],post['revision'],post['month']),('auto','test-csrf','rev1','2026-11'))
    def test_new_payment_edit_month_and_comment(self):
        self.page.locator('#adNew').click()
        self.page.locator('#adEntryMonth').select_option('10')
        self.page.locator('#adLabel').fill('Площадка')
        self.page.locator('#adAmount').fill('8 600,50')
        self.page.locator('#adComment').fill('Предоплата октября')
        self.page.locator('[data-save]').click()
        post=self.page.evaluate('posts[0]')
        self.assertEqual(post['target_month'],'2026-10');self.assertEqual(post['amount'],'8 600,50')
    def test_pending_operation_blocks_double_click_and_month_change(self):
        self.page.evaluate('hold=true');self.page.locator('#adAccept').click()
        self.assertTrue(self.page.locator('#adAccept').is_disabled())
        self.assertTrue(self.page.locator('#adMonth').is_disabled())
        self.page.evaluate("document.getElementById('adAccept').dispatchEvent(new Event('click'))")
        self.assertEqual(self.page.evaluate('posts.length'),1)
        self.page.evaluate('release()');self.page.wait_for_function("!document.getElementById('adAccept').disabled")
    def test_stale_edit_keeps_user_inputs(self):
        self.page.evaluate('window.reject=true');self.page.locator('#adNew').click()
        self.page.locator('#adLabel').fill('Не потерять');self.page.locator('#adAmount').fill('100')
        self.page.locator('[data-save]').click()
        self.page.wait_for_function("document.getElementById('adCostResult').textContent.includes('Данные изменились')")
        self.assertEqual(self.page.locator('#adLabel').input_value(),'Не потерять')
        self.assertTrue(self.page.locator('#adEditor').is_visible())
    def test_safe_text_render_edit_delete_confirmation(self):
        self.page.evaluate("fixture.records=[{id:'row1',month:'2026-11',source:'yandex_ads',label:'<img src=x onerror=alert(1)>',amount:500,comment:'test'}]")
        self.page.locator('#adRefresh').click()
        self.assertEqual(self.page.locator('#adPayments img').count(),0)
        self.page.locator('#adPaymentDetails').evaluate('(e)=>e.open=true')
        self.page.get_by_role('button',name='Изменить',exact=True).click()
        self.assertEqual(self.page.locator('#adAmount').input_value(),'500')
        self.page.locator('#adCancel').click()
        self.page.once('dialog',lambda d:d.dismiss());self.page.get_by_role('button',name='Удалить',exact=True).click()
        self.assertEqual(self.page.evaluate('posts.length'),0)
        self.page.once('dialog',lambda d:d.accept());self.page.get_by_role('button',name='Удалить',exact=True).click()
        self.assertEqual(self.page.evaluate('posts[0].confirmation'),'delete')
    def test_all_channels_and_instagram_children_are_expandable_without_payments(self):
        self.assertEqual(self.page.locator('#adCostRows .source').count(),2)
        self.page.locator('[data-expand-source="instagram"]').click()
        rows=self.page.locator('#adCostRows .campaign')
        self.assertEqual(rows.count(),3)
        for name in ['Instagram клиники','Instagram Зубачёва','Instagram Старостенко']:
            self.assertIn(name,self.page.locator('#adCostRows').inner_text())
        self.page.locator('[data-entity-key="clinic"] input').fill('200')
        self.page.locator('[data-entity-key="clinic"]').get_by_role('button',name='Сохранить',exact=True).click()
        post=self.page.evaluate('posts[0]')
        self.assertEqual((post['source'],post['level'],post['campaign_key'],post['amount']),('instagram','campaign','clinic','200'))
        self.assertEqual(self.page.locator('#adCostRows .campaign').count(),3)

    def test_instagram_existing_spend_shows_clinic_not_old_structure_name(self):
        self.page.evaluate("""() => {
          fixture.fixed=250000;
          fixture.automatic_total=250100;
          fixture.records=[
            {id:'z-old',month:'2026-11',source:'instagram',level:'campaign',
             key:'old-z',label:'Зубачёв',amount:150000,comment:''},
            {id:'clinic-old',month:'2026-11',source:'instagram',level:'campaign',
             key:'old-clinic',label:'Отделение структуры',amount:100000,comment:''}
          ];
          const ig=fixture.channels.find(x=>x.source_key==='instagram');
          ig.fixed_total=250000;
          ig.campaigns=[
            {campaign_key:'zubachev',source_key:'instagram',campaign_id:'',
             campaign_name:'Зубачев',record_ids:['z-old'],amount:150000,direct:null},
            {campaign_key:'clinic',source_key:'instagram',campaign_id:'',
             campaign_name:'Клиника',record_ids:['clinic-old'],amount:100000,direct:null}
          ];
        }""")
        self.page.locator('#adRefresh').click()
        self.page.locator('[data-expand-source="instagram"]').click()
        names=self.page.locator('#adCostRows .campaign').all_inner_texts()
        self.assertEqual(len(names),2)
        self.assertTrue(any('Клиника' in n for n in names))
        self.assertTrue(any('Зубачев' in n for n in names))
        self.assertFalse(any('Отделение структуры' in n for n in names))
        self.assertEqual(self.page.locator(
            '[data-entity-key="zubachev"] input').input_value(), '150000')
        self.assertEqual(self.page.locator(
            '[data-entity-key="clinic"] input').input_value(), '100000')
        self.page.locator('[data-entity-key="clinic"]').get_by_role(
            'button',name='Сохранить',exact=True).click()
        post=self.page.evaluate('posts[0]')
        self.assertEqual((post['id'],post['source'],post['campaign_key'],post['amount']),
                         ('clinic-old','instagram','clinic','100000'))

    def test_reapproval_notice_preserves_old_manual_adjustment_without_auto_applying(self):
        self.page.evaluate("""() => {
          fixture.fixed=250000;
          fixture.direct=100;
          fixture.automatic_total=250100;
          fixture.confirmed=null;
          fixture.confirmed_total=null;
          fixture.stale_confirmation={fixed:130000,adjustment:30000};
        }""")
        self.page.locator('#adRefresh').click()
        text=self.page.locator('#adAdjustment').inner_text()
        self.assertIn('Ранее подтверждено',text)
        self.assertIn('130',text)
        self.assertIn('30',text)
        self.assertIn('повторная проверка',text)
        self.assertEqual(self.page.locator('#adApproved').inner_text(),'—')
        self.assertEqual(self.page.evaluate('posts.length'),0)
        # Reapproval only after pressing a confirmation button.
        self.page.locator('#adAccept').click()
        self.assertEqual(self.page.evaluate('posts[0].action'),'confirm')
        self.assertEqual(self.page.evaluate('posts[0].mode'),'auto')

    def test_direct_breakdown_is_readonly_and_old_table_columns_preserved(self):
        self.page.locator('[data-expand-source="yandex_ads"]').click()
        campaign=self.page.locator('[data-entity-key="direct1"]')
        self.assertEqual(campaign.locator('input').input_value(),'100')
        self.assertTrue(campaign.locator('input').evaluate('(e)=>e.readOnly'))
        self.assertEqual(campaign.get_by_role('button').count(),0)
        headers=self.page.locator('.ad-cost-head').inner_text()
        for label in ['Уровень','Месяц','Реклама','Подрядчик','Всего']:
            self.assertIn(label,headers)
        self.assertEqual(self.page.locator('input[type=date]').count(),0)

    def test_mobile_compact_editor_and_scrollable_table(self):
        self.page.set_viewport_size({'width':390,'height':844});self.page.locator('#adNew').click()
        self.assertEqual(self.page.locator('.ad-cost-editor').evaluate('(e)=>getComputedStyle(e).gridTemplateColumns.split(" ").length'),2)
        self.assertTrue(self.page.locator('.ad-cost-table').first.evaluate('(e)=>e.scrollWidth>e.clientWidth'))
        self.assertLessEqual(self.page.evaluate('document.body.scrollWidth'),390)

if __name__=='__main__':unittest.main()
