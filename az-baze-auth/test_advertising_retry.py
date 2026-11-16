import json,tempfile,unittest,fcntl
from pathlib import Path
from unittest.mock import Mock,patch
import advertising_retry as r
import metrika_auto as m
import direct_auto as d
class RetryTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name)/'state';self.path.write_text('{}');self.conn=Mock()
  for obj,key,value in [(r,'STATE',self.path),(r.time,'time',7200)]:
   p=patch.object(obj,key,return_value=value) if key=='time' else patch.object(obj,key,value)
   p.start();self.addCleanup(p.stop)
 def state(self):return json.loads(self.path.read_text())
 def test_automatic_status_survives_manual_success_and_skips(self):
  call=r.scheduled(Mock(return_value={'changed':False}))
  call(self.conn,'token',None,retry_mode='hourly')
  saved=self.state()['automatic']
  self.assertEqual(saved,{'outcome':'success','at':7200})
  self.assertIn('01.01.1970 в 09:00',r.public_status()['message'])
  self.assertIn('успешно завершена',r.public_status()['message'])
  call(self.conn,'token',1)
  self.assertEqual(self.state()['automatic'],saved)
  with self.assertRaises(r.RetrySkipped):call(self.conn,'token',None,retry_mode='hourly')
  self.assertEqual(self.state()['automatic'],saved)
 def test_status_error_retry_recovery_read_only(self):
  call=r.scheduled(Mock(side_effect=OSError('secret-token')))
  with self.assertRaises(OSError):call(self.conn,'token',None,retry_mode='hourly')
  before=self.path.read_bytes();message=r.public_status()['message']
  self.assertIn('завершилась ошибкой',message);self.assertIn('09:10',message)
  self.assertNotIn('secret',message);self.assertEqual(self.path.read_bytes(),before)
  with patch.object(r.time,'time',return_value=7800):
   self.assertIn('ближайшей минутной',r.public_status()['message'])
   r.scheduled(Mock(return_value='ok'))(self.conn,'token',None,retry_mode='retry')
  self.assertIn('успешно завершена',r.public_status()['message'])
  self.assertNotIn('Следующая попытка',r.public_status()['message'])
 def test_status_interrupted_blocked_legacy_unavailable(self):
  self.assertNotIn('успешно',r.public_status()['message'])
  with self.assertRaises(KeyboardInterrupt):r.scheduled(Mock(side_effect=KeyboardInterrupt))(self.conn,'token',None,retry_mode='hourly')
  self.assertIn('завершение пока не подтверждено',r.public_status()['message'])
  self.path.write_text('{"blocked":true,"automatic":{"outcome":"error","at":7200}}')
  self.assertIn('приостановлена',r.public_status()['message'])
  self.assertNotIn('Следующая попытка',r.public_status()['message'])
  self.path.write_text('broken');self.assertIn('Не удалось',r.public_status()['message'])
  self.path.unlink();self.assertIn('недоступен',r.public_status()['message']);self.assertFalse(self.path.exists())
 def test_failures_then_success(self):
  fn=Mock(side_effect=[OSError('secret'),OSError('secret'),{'ok':1}]);call=r.scheduled(fn)
  with self.assertRaises(OSError) as e:call(self.conn,'token',1)
  self.assertTrue(e.exception.az_retry_scheduled);self.assertEqual(self.state()['next_retry'],7800)
  self.assertNotIn('secret',self.path.read_text());self.assertNotIn('token',self.path.read_text())
  for mode in ('hourly','retry'):
   with self.assertRaises(r.RetrySkipped):call(self.conn,'token',None,retry_mode=mode)
  with patch.object(r.time,'time',return_value=7800):
   with self.assertRaises(OSError):call(self.conn,'token',None,retry_mode='retry')
  self.assertEqual(self.state()['next_retry'],8400)
  with patch.object(r.time,'time',return_value=8400):self.assertEqual(call(self.conn,'token',None,retry_mode='retry'),{'ok':1})
  self.assertIsNone(self.state()['next_retry']);self.assertEqual(fn.call_count,3)
 def test_manual_success_cancels_retry(self):
  self.path.write_text('{"next_retry":7800}');call=r.scheduled(Mock(return_value='ok'));call(self.conn,'token',1)
  for mode in ('hourly','retry'):
   with self.assertRaises(r.RetrySkipped):call(self.conn,'token',None,retry_mode=mode)
  with patch.object(r.time,'time',return_value=10800):self.assertEqual(call(self.conn,'token',None,retry_mode='hourly'),'ok')
 def test_busy_preserves_state(self):
  initial={'next_retry':7000};self.path.write_text(json.dumps(initial))
  with self.assertRaises(m.MetrikaBusyError):r.scheduled(Mock(side_effect=m.MetrikaBusyError('busy')))(self.conn,'token',1)
  self.assertEqual(self.state(),initial)
 def test_concurrent_gate(self):
  fn=Mock()
  with open(str(self.path)+'.lock','a') as f:
   fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
   with self.assertRaises(m.MetrikaBusyError):r.scheduled(fn)(self.conn,'token',1)
  fn.assert_not_called();self.assertEqual(self.state(),{})
 def test_auth_blocks_until_manual_recovery(self):
  for exc in [m.MetrikaAuthError('auth'),m.MetrikaAccessError('access'),d.DirectApiError('HTTP 403'),d.DirectApiError('HTTP 400; код=53')]:
   fn=Mock(side_effect=exc);call=r.scheduled(fn)
   with self.assertRaises(type(exc)):call(self.conn,'token',1)
   self.assertTrue(self.state()['blocked'])
   with self.assertRaises(r.RetrySkipped):call(self.conn,'token',None,retry_mode='hourly')
   fn.side_effect=None;call(self.conn,'new-token',1);self.assertFalse(self.state()['blocked'])
 def test_blocked_manual_failures_and_interruptions_require_success(self):
  for failure in [OSError('network'),m.MetrikaApiError('network'),KeyboardInterrupt(),SystemExit(1),m.MetrikaBusyError('busy')]:
   with self.subTest(failure=type(failure).__name__):
    fn=Mock(side_effect=m.MetrikaAccessError('access'));call=r.scheduled(fn)
    with self.assertRaises(m.MetrikaAccessError):call(self.conn,'token',1)
    fn.side_effect=failure
    with self.assertRaises(type(failure)) as caught:call(self.conn,'token',1)
    self.assertTrue(self.state()['blocked']);self.assertIsNone(self.state()['next_retry'])
    self.assertFalse(getattr(caught.exception,'az_retry_scheduled',False))
    for mode in ('hourly','retry'):
     with patch.object(r.time,'time',return_value=14400),self.assertRaises(r.RetrySkipped):call(self.conn,'token',None,retry_mode=mode)
    self.assertEqual(fn.call_count,2)
    fn.side_effect=None;fn.return_value='ok'
    self.assertEqual(call(self.conn,'token',1),'ok')
    self.assertFalse(self.state()['blocked']);self.assertIsNone(self.state()['next_retry'])
    with patch.object(r.time,'time',return_value=10800):self.assertEqual(call(self.conn,'token',None,retry_mode='hourly'),'ok')
 def test_block_remains_durable_during_manual_attempt(self):
  self.path.write_text('{"blocked":true,"next_retry":null}')
  def attempt(*args):
   self.assertTrue(self.state()['blocked']);self.assertIsNone(self.state()['next_retry'])
   return 'ok'
  self.assertEqual(r.scheduled(attempt)(self.conn,'token',1),'ok')
  self.assertFalse(self.state()['blocked'])
 def test_block_survives_actual_process_exit(self):
  import subprocess,sys
  self.path.write_text('{"blocked":true,"next_retry":null}')
  code = "import os,sys; from pathlib import Path; import advertising_retry as r; r.STATE=Path(sys.argv[1]); r.scheduled(lambda *args: os._exit(17))(None,'token',1)"
  result=subprocess.run([sys.executable,'-B','-c',code,str(self.path)],cwd=Path(r.__file__).parent,timeout=10)
  self.assertEqual(result.returncode,17)
  self.assertTrue(self.state()['blocked']);self.assertIsNone(self.state()['next_retry'])
  fn=Mock()
  for mode in ('hourly','retry'):
   with self.assertRaises(r.RetrySkipped):r.scheduled(fn)(self.conn,'token',None,retry_mode=mode)
  fn.assert_not_called()
 def test_kill_retains_intent(self):
  with self.assertRaises(KeyboardInterrupt):r.scheduled(Mock(side_effect=KeyboardInterrupt))(self.conn,'token',1)
  self.assertEqual(self.state()['next_retry'],7800)
 def test_disabled_no_writes(self):
  self.path.unlink();call=r.scheduled(Mock(return_value='ok'));self.assertEqual(call(self.conn,'token',1),'ok');self.assertFalse(self.path.exists())
  with self.assertRaises(r.RetrySkipped):call(self.conn,'token',None,retry_mode='retry')
 def test_corrupt_state(self):
  self.path.write_text('bad');fn=Mock()
  with self.assertRaises(ValueError):r.scheduled(fn)(self.conn,'token',1)
  fn.assert_not_called()
 def test_write_failure_prevents_network(self):
  fn=Mock()
  with patch.object(r,'write_state',side_effect=OSError):
   with self.assertRaises(OSError):r.scheduled(fn)(self.conn,'token',1)
  fn.assert_not_called()
if __name__=='__main__':unittest.main()
