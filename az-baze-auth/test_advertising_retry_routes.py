import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import test_advertising_report as fixture
import advertising_retry as retry
import metrika_auto
import types
core=fixture.m
class RetryRoutesTests(unittest.TestCase):
 def test_manual_failure_message_then_success_cancels_pending(self):
  with tempfile.TemporaryDirectory() as td:
   state=Path(td)/'state';state.write_text('{}')
   class App:
    def __init__(self):self.routes={}
    def get(self,path):return self.post(path)
    def post(self,path):
     def register(fn):self.routes[path]=fn;return fn
     return register
   app=App();conn=fixture.conn()
   result={'changed':False,'payload':{}}
   call=retry.scheduled(Mock(side_effect=[metrika_auto.MetrikaApiError('API unavailable'),result]))
   with patch.object(retry,'STATE',state),patch.object(core,'_ensure_schema'),patch.object(core,'db',return_value=conn),patch.object(core,'run_metrika_direct_sync',call),patch.object(metrika_auto,'token_from_env',return_value='secret'),patch.object(core,'g',types.SimpleNamespace(user={'id':1})):
    core.register_advertising_report(app)
    before=state.read_bytes()
    body,code,headers=app.routes['/api/reports/advertising/metrika/sync-status']()
    self.assertEqual(code,200);self.assertEqual(headers['Cache-Control'],'no-store')
    self.assertEqual(set(body),{'message'});self.assertEqual(state.read_bytes(),before)
    response,status=app.routes['/api/reports/advertising/metrika/sync']()
    self.assertEqual(status,400);self.assertIn('10 минут',response['message']);self.assertNotIn('secret',response['message'])
    self.assertIsNotNone(__import__('json').loads(state.read_text())['next_retry'])
    self.assertEqual(app.routes['/api/reports/advertising/metrika/sync']()['status'],'ok')
    self.assertIsNone(__import__('json').loads(state.read_text())['next_retry'])
   conn.close()
if __name__=='__main__':unittest.main()
