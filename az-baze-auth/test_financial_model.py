import copy,json,os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('AZBAZE_SECRET_KEY','test-finmodel-key')
import app as auth
import financial_model as fm
class CalculationTests(unittest.TestCase):
 def state(self):
  c=fm.initial();return dict(config=c,results=fm.calculate(c),version=0,stale=[])
 def change(self,**kw):return dict(group='months',key='rent',value=900000,**kw)
 def test_calendar_and_totals(self):
  c=fm.initial();r=fm.calculate(c)
  self.assertEqual(r[0]['capacity'],8*31*12);self.assertEqual(r[1]['capacity'],8*28*12)
  c['year']=2028;self.assertEqual(fm.calculate(c)[1]['capacity'],8*29*12)
  for m in r:
   self.assertAlmostEqual(m['revenue'],m['dentistry']+m['structure']+m['other_revenue']);self.assertLessEqual(m['hours'],m['capacity']);self.assertIsNotNone(m['profit'])
 def test_costs_once(self):
  c=fm.initial();c['months']['payroll_tax']=[0]*12
  for r in fm.calculate(c):
   self.assertAlmostEqual(r['revenue']-r['total_cost'],r['profit_before']);self.assertAlmostEqual(r['profit_before']-r['tax'],r['profit'])
   self.assertAlmostEqual(r['total_cost'],sum(r[k] for k in ['wage','payroll_tax','medical','lab','rent','marketing','it','outsourcing','other_cost','reserve']))
 def test_zero_and_negative(self):
  c=fm.initial();c['months']['marketing_fixed']=[0]*12;c['months']['payroll_tax']=[0]*12;c['months']['target']=[0]*12
  r=fm.calculate(c)[0];self.assertEqual(r['marketing'],0);self.assertGreater(r['revenue'],0);c['months']['rent']=[1e9]*12;self.assertLess(fm.calculate(c)[0]['profit'],0)
 def test_ramp_and_unassigned(self):
  c=fm.initial();c['hires']=[dict(id='x',name='test',room='5',start='2027-02',count=1)];r=fm.calculate(c)
  self.assertEqual([m['hires'][0]['factor'] for m in r[:7]],[0,.13,.44,.76,.9,1,1]);self.assertEqual(r[1]['starts'],1)
  c['hires'][0]['room']=None;q=fm.calculate(c);self.assertLessEqual(q[1]['revenue'],r[1]['revenue']);self.assertTrue(any('Не назначен' in x for x in q[1]['warnings']))
 def test_capacity(self):
  c=fm.initial();c['hires'][0]['count']=20;c['months']['target']=[1e9]*12
  for m in fm.calculate(c):
   for r in m['rooms']:self.assertLessEqual(r['staffed'],r['capacity']);self.assertLessEqual(r['hours'],r['staffed'])
 def test_absence(self):
  c=fm.initial();c['months']['target']=[1e9]*12;a=fm.calculate(c)[0]['revenue'];c['rooms'][0]['absence_hours']=[10000]*12;self.assertLess(fm.calculate(c)[0]['revenue'],a)
 def test_keep_partial(self):
  old=self.state();n,_,_=fm.transition(old,self.change(**{'from':4,'scope':'future'}),'keep');self.assertEqual(n['results'],old['results']);self.assertEqual(n['stale'],list(range(4,13)))
  q,_,_=fm.transition(n,self.change(**{'from':6,'scope':'month'}),'recalculate');self.assertNotIn(6,q['stale']);self.assertIn(4,q['stale']);self.assertIn(7,q['stale']);self.assertEqual(q['results'][:5],old['results'][:5])
 def test_recalc_one(self):
  old=self.state();n,_,_=fm.transition(old,self.change(**{'from':6,'scope':'month'}),'recalculate');self.assertEqual(n['results'][:5],old['results'][:5]);self.assertEqual(n['results'][6:],old['results'][6:]);self.assertEqual(n['results'][5]['rent'],900000)
 def test_invalid(self):
  for v in ['NaN','Infinity',-1,True,1.1]:
   with self.assertRaises(ValueError):fm.candidate(self.state(),{'group':'months','key':'booking','value':v,'from':1,'scope':'future'})
  with self.assertRaises(ValueError):fm.month_index('2027-13')
class AuditRegressionTests(unittest.TestCase):
 def state(self):
  c=fm.initial();return dict(config=c,results=fm.calculate(c),version=0,stale=[])
 def test_225_01_direction_mapping_and_reconciliation(self):
  c=fm.initial();self.assertEqual({r['id'] for r in c['rooms'] if r['direction']=='dentistry'},{'5','7','8','9'})
  r=fm.calculate(c);self.assertAlmostEqual(r[0]['dentistry'],7634834.64,places=2)
  for m in r:
   self.assertAlmostEqual(m['dentistry'],sum(x['revenue'] for x in m['rooms'] if x['id'] in {'5','7','8','9'}))
   self.assertAlmostEqual(m['structure'],sum(x['revenue'] for x in m['rooms'] if x['id'] in {'1','3','4','6'}))
   self.assertAlmostEqual(m['revenue'],m['dentistry']+m['structure']+m['other_revenue'])
  c['rooms'][0]['direction']='typo'
  with self.assertRaises(ValueError):fm.calculate(c)
 def test_225_02_frozen_recommendations_and_partial_recalculation(self):
  old=self.state();before=fm.hiring_needs(old['results']);self.assertTrue(before)
  change=dict(group='months',key='search_months',value=3,scope='future',**{'from':1})
  kept,_,_=fm.transition(old,change,'keep');self.assertEqual(fm.hiring_needs(kept['results'],kept['config']),before)
  ramp=dict(group='ramp',key='4',value=.95,scope='future',**{'from':1})
  kept,_,_=fm.transition(kept,ramp,'keep');self.assertEqual(fm.hiring_needs(kept['results'],kept['config']),before)
  fresh,_,_=fm.transition(kept,dict(group='months',key='rent',value=10,scope='month',**{'from':6}),'recalculate')
  self.assertEqual(fresh['results'][:5],old['results'][:5]);self.assertEqual(fresh['results'][6:],old['results'][6:])
  self.assertIsNone(fresh['results'][5]['hiring_context']['lag']);self.assertEqual(fresh['results'][5]['hiring_context']['search'],3)
  full=fm.calculate(kept['config']);after=fm.hiring_needs(full);self.assertNotEqual(after,before);self.assertTrue(all(x['start']=='Профиль не достигает полной загрузки' for x in after))
 def test_225_03_expansion_is_not_new_hire(self):
  c=fm.initial();c['hires']=[next(h for h in c['hires'] if h['id']=='besedina')];c['rooms']=[next(r for r in c['rooms'] if r['id']=='5')]
  c['rooms'][0]['staffed_hours']=[0]*12;r=fm.calculate(c)
  self.assertEqual([m['hires'][0]['factor'] for m in r[:6]],[0,0,1,1,1,1])
  self.assertEqual([m['hires'][0]['schedule_fraction'] for m in r[:6]],[0,0,.5,1,1,1])
  self.assertEqual(r[2]['staffed'],66);self.assertEqual(r[3]['staffed'],132);self.assertTrue(all(m['ramping']==0 for m in r))
  c['ramp']=[.01,.02,.03,.04,.05];q=fm.calculate(c)
  for a,b in zip(r,q):self.assertEqual(a['revenue'],b['revenue']);self.assertEqual(a['hours'],b['hours'])
  c['hires'][0]['start']='2027-05';q=fm.calculate(c);self.assertEqual(q[3]['staffed'],0);self.assertEqual(q[4]['staffed'],66);self.assertEqual(q[5]['staffed'],132)
  c['rooms'][0]['staffed_hours']=[350]*12;q=fm.calculate(c)
  for m in q:self.assertLessEqual(m['staffed'],m['capacity'])

class OfficialPayrollTests(unittest.TestCase):
 def config(self):
  c=fm.initial();c['months'].update(official_base_pct=[30]*12,official_burden_pct=[30.2]*12,payroll_tax=[.09]*12);return c
 def test_accumulation_and_base_unchanged(self):
  c=self.config();r=fm.calculate(c);zero=copy.deepcopy(c);zero['months']['official_step_pct']=[0]*12;q=fm.calculate(zero)
  for i,(a,b) in enumerate(zip(r,q)):
   self.assertAlmostEqual(a['official_share'],.30+(i+1)*.01)
   self.assertAlmostEqual(a['official_extra_wage'],a['wage']*(i+1)*.01)
   self.assertAlmostEqual(a['official_extra_tax'],a['official_extra_wage']*.302)
   self.assertEqual({k:v for k,v in a.items() if not k.startswith('official_')},{k:v for k,v in b.items() if not k.startswith('official_')})
   before=a['profit_before']-a['official_extra_tax'];tax=max(max(before,0)*c['months']['tax_rate'][i],a['revenue']*c['months']['minimum_tax'][i])
   self.assertAlmostEqual(a['official_adjusted_profit'],before-tax)
 def test_cap_zero_and_unknown(self):
  c=self.config();c['months']['official_base_pct']=[98]*12;c['months']['official_step_pct']=[3]*12
  for r in fm.calculate(c):self.assertEqual(r['official_share'],1);self.assertAlmostEqual(r['official_extra_wage'],r['wage']*.02)
  c['months']['official_base_pct']=[100]*12;c['months']['official_burden_pct']=[None]*12
  for r in fm.calculate(c):self.assertEqual(r['official_extra_tax'],0);self.assertEqual(r['official_adjusted_profit'],r['profit'])
  c['months']['official_base_pct']=[None]*12
  for r in fm.calculate(c):self.assertIsNone(r['official_extra_tax']);self.assertIsNone(r['official_adjusted_profit'])
  c=self.config();c['months']['official_burden_pct']=[0]*12
  self.assertEqual(fm.calculate(c)[0]['official_extra_tax'],0)
 def test_step_change_propagates_without_changing_future_inputs(self):
  c=self.config();old=dict(config=c,results=fm.calculate(c),version=0,stale=[])
  ch=dict(group='months',key='official_step_pct',value=3,scope='month',**{'from':6})
  new,_,_=fm.transition(old,ch,'recalculate');self.assertEqual(new['results'][:5],old['results'][:5])
  self.assertEqual(new['config']['months']['official_step_pct'],[1]*5+[3]+[1]*6)
  for i in range(5,12):self.assertAlmostEqual(new['results'][i]['official_share']-old['results'][i]['official_share'],.02)
  kept,_,_=fm.transition(old,ch,'keep');self.assertEqual(kept['results'],old['results']);self.assertEqual(kept['stale'],list(range(6,13)))
 def test_bounds_and_unknown_profit(self):
  c=self.config();c['months']['payroll_tax']=[None]*12;r=fm.calculate(c)[0];self.assertGreater(r['official_extra_tax'],0);self.assertLess(r['official_adjusted_profit'],r['profit'])
  old=dict(config=c,results=fm.calculate(c),version=0,stale=[])
  for val in [-1,101,'NaN',True]:
   with self.assertRaises(ValueError):fm.candidate(old,dict(group='months',key='official_step_pct',value=val,scope='month',**{'from':1}))

class ReservePolicyTests(unittest.TestCase):
 def test_source_policy_and_explicit_extra(self):
  c=fm.initial();r=fm.calculate(c)[0]
  self.assertEqual(r['payroll_tax'],0);self.assertTrue(r['base_contributions_in_reserve'])
  self.assertAlmostEqual(r['reserve'],r['revenue']*.04)
  for k,v in [('total_cost',7574631.08),('profit_before',1105210.09),('tax',165781.51),('profit',939428.58)]:self.assertAlmostEqual(r[k],v,places=2)
  self.assertIsNone(r['official_extra_tax']);self.assertIsNone(r['official_adjusted_profit'])
  c['months']['payroll_tax']=[.1]*12;q=fm.calculate(c)[0]
  self.assertAlmostEqual(q['total_cost']-r['total_cost'],r['wage']*.1);self.assertEqual(q['reserve'],r['reserve']);self.assertFalse(q['base_contributions_in_reserve'])
  c['months']['payroll_tax']=[0]*12;q=fm.calculate(c)[0];self.assertEqual(q['profit'],r['profit']);self.assertFalse(q['base_contributions_in_reserve'])
 def test_saved_legacy_readonly_keep_and_refresh(self):
  with sqlite3.connect(':memory:') as db:
   db.row_factory=sqlite3.Row;fm.init_schema(db);c=fm.initial();results=fm.calculate(c)
   for m in results:
    m.pop('reserve_contribution_policy');m.pop('base_contributions_in_reserve')
    for k in ['payroll_tax','total_cost','profit_before','tax','profit','margin']:m[k]=None
   c['months']['rent'][5]=999999
   db.execute('INSERT INTO financial_model_plan VALUES(2027,1,?,?,?)',(json.dumps(c),json.dumps(results),'[]'));db.commit()
   persisted=db.execute('SELECT * FROM financial_model_plan').fetchone();db.execute('PRAGMA query_only=ON');old=fm.read_state(db)
   self.assertEqual(old['results'],results);self.assertEqual(old['config'],c);self.assertEqual(old['stale'],list(range(1,13)));self.assertEqual(tuple(db.execute('SELECT * FROM financial_model_plan').fetchone()),tuple(persisted))
   db.execute('PRAGMA query_only=OFF')
   kept=fm.commit_change(db,1,dict(group='months',key='rent',value=12,scope='month',**{'from':6}),'keep',1)
   self.assertEqual(kept['results'],results);self.assertEqual(fm.read_state(db)['results'],results)
   fm.commit_change(db,2,{'group':'refresh'},'recalculate',1);new=fm.read_state(db)
   self.assertEqual(new['stale'],[]);self.assertEqual(new['outdated_policy'],[]);self.assertEqual(new['config']['months']['rent'][5],12);self.assertIsNotNone(new['results'][0]['profit']);self.assertEqual(db.execute('SELECT count(*) FROM financial_model_history').fetchone()[0],2)

class StoreTests(unittest.TestCase):
 def setUp(self):
  self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row;fm.init_schema(self.c);self.c.execute('CREATE TABLE report_data(value TEXT)');self.c.execute("INSERT INTO report_data VALUES('sentinel')");self.c.commit()
 def test_readonly(self):self.c.execute('PRAGMA query_only=ON');fm.read_state(self.c)
 def test_replay(self):
  ch={'group':'months','key':'rent','value':5,'from':1,'scope':'future'};fm.commit_change(self.c,0,ch,'keep',1)
  with self.assertRaises(RuntimeError):fm.commit_change(self.c,0,ch,'keep',1)
  self.assertEqual(self.c.execute('SELECT count(*) FROM financial_model_history').fetchone()[0],1);self.assertEqual(self.c.execute('SELECT value FROM report_data').fetchone()[0],'sentinel')
 def test_rollback(self):
  self.c.execute("CREATE TRIGGER failhistory BEFORE INSERT ON financial_model_history BEGIN SELECT RAISE(ABORT,'fail'); END")
  with self.assertRaises(sqlite3.IntegrityError):fm.commit_change(self.c,0,{'group':'months','key':'rent','value':5,'from':1,'scope':'future'},'keep',1)
  self.assertEqual(fm.read_state(self.c)['version'],0)
class RoutesTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);p=patch.object(auth,'DB_PATH',Path(self.tmp.name)/'test.db');p.start();self.addCleanup(p.stop);auth.init_db_file();self.web=auth.create_app();self.web.config['TESTING']=True
  with sqlite3.connect(auth.DB_PATH) as c:
   for uid,admin in [(1,1),(2,0),(3,0),(4,0)]:c.execute('INSERT INTO users(id,email,full_name,password_hash,is_admin,active,must_change_password,created_at,updated_at) VALUES(?,?,?,?,?,1,0,?,?)',(uid,f'{uid}@example.test','User','x',admin,'t','t'))
   c.executemany('INSERT INTO permissions VALUES(?,?)',[(2,'reports'),(3,'financial_model_edit'),(4,'reports'),(4,'financial_model_edit')])
 def client(self,uid=None):
  c=self.web.test_client()
  if uid:
   with c.session_transaction() as s:s['user_id']=uid;s['csrf']='test'
  return c
 def preview(self,c):return c.post('/reports/financial-model/preview',data={'csrf':'test','version':0,'mode':'keep','change':json.dumps({'group':'months','key':'rent','value':1,'from':1,'scope':'future'})})
 def test_permissions(self):
  path='/reports/financial-model/';self.assertEqual(self.client().get(path).status_code,302);self.assertEqual(self.client(3).get(path).status_code,403);c=self.client(2);before=auth.DB_PATH.read_bytes();self.assertEqual(c.get(path).status_code,200);self.assertEqual(auth.DB_PATH.read_bytes(),before);self.assertEqual(self.preview(c).status_code,403);self.assertEqual(self.preview(self.client(3)).status_code,403);self.assertEqual(self.preview(self.client(4)).status_code,200)
 def test_token_csrf_replay(self):
  c=self.client(1);before=auth.DB_PATH.read_bytes();j=self.preview(c).get_json();self.assertEqual(auth.DB_PATH.read_bytes(),before);path='/reports/financial-model/save'
  self.assertEqual(c.post(path,data={'token':j['token']}).status_code,400);self.assertEqual(self.client(4).post(path,data={'csrf':'test','token':j['token']}).status_code,403);self.assertEqual(c.post(path,data={'csrf':'test','token':j['token']+'x'}).status_code,400);self.assertEqual(c.post(path,data={'csrf':'test','token':j['token']}).status_code,200);self.assertEqual(c.post(path,data={'csrf':'test','token':j['token']}).status_code,409)
if __name__=='__main__':unittest.main()
