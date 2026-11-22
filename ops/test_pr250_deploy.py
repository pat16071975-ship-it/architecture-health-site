"""Guarded installer: real temporary SQLite, synthetic systemd/network, real app startup."""
import ast,contextlib,importlib.util,io,json,os,shutil,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
BASE='098ca183bab3f61c42326423f11e87554ae10464'

def constant(path,name='SCHEMA'):
    return next(ast.literal_eval(n.value) for n in ast.parse(path.read_bytes()).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets))

class InstallerTests(unittest.TestCase):

    def remember(self):
        self.original_dump=self.dump()
        with sqlite3.connect(self.m.DB) as c:
            self.before=self.m.schema_objects(c)
            self.tables=[n for n,r in self.before.items() if r[0]=='table']
            self.data=self.m.data_fingerprint(c,self.tables)

    def preserved(self,rolled_back=False):
        m=self.m
        with sqlite3.connect(m.DB) as c:
            self.assertEqual(m.data_fingerprint(c,self.tables),self.data)
            if rolled_back:self.assertEqual(m.schema_objects(c),self.before)
            else:
                source=m.migration_source(self.raw)
                self.assertEqual(m.schema_objects(c),{**self.before,**source['added']})
                for table in m.NEW_TABLES:self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        for name,old,new in m.ROWS:
            if rolled_back and old is None:self.assertFalse(m.live(name).exists())
            else:self.assertEqual(m.blob(m.live(name).read_bytes()),old if rolled_back else new)
        for name,data in self.dependencies.items():self.assertEqual(m.live(name).read_bytes(),data)
        for name,data in self.extra_files.items():self.assertEqual(m.live(name).read_bytes(),data)
        self.assertTrue(all(self.active.values()));self.assertEqual(m.STATE.read_bytes(),b'{"preserve":"retry"}')

    def test_success_four_empty_tables_full_backup_no_data_changes(self):
        m=self.m;m.main();self.preserved()
        backup,=m.BACKUP_ROOT.glob('pr250-*')
        with sqlite3.connect(backup/'auth.db') as c:self.assertEqual('\n'.join(c.iterdump()),self.original_dump)
        manifest=json.loads((backup/'manifest.json').read_text())
        self.assertEqual(manifest['data_writes'],'NONE');self.assertEqual(set(manifest['new_tables']),set(m.NEW_TABLES))
        for name,old,new in m.ROWS:self.assertEqual(m.live(name).stat().st_mode&0o777,0o640)
        self.assertIn('PR250_PRODUCTION_DEPLOY=PASS; FILES=11',self.output.getvalue())
        self.assertIn('DML_WRITES=0',self.output.getvalue())

    def inject(self,extra):
        original=self.m.prepare
        def prepare(c,source):
            extra(c)
            original(c,source)
        self.m.prepare=prepare

    def test_schema_failure_rolls_back_prior_ddl_and_dml(self):
        original=self.m.migration_source
        def source(data):
            result=original(data)
            result['statements']=[*result['statements'],"UPDATE reports SET value=99 WHERE id=2"]
            return result
        self.m.migration_source=source
        with self.assertRaisesRegex(RuntimeError,'existing data changed'):self.m.main()
        self.preserved(True)

    def test_unexpected_schema_rolls_back_everything(self):
        original=self.m.migration_source
        def source(data):
            result=original(data);result['statements']=[*result['statements'],'CREATE TABLE stray(x)'];return result
        self.m.migration_source=source
        with self.assertRaisesRegex(RuntimeError,'unexpected schema change'):self.m.main()
        self.preserved(True)

    def test_missing_or_disabled_owner_stops_before_file_mutation(self):
        self.m.configured_owner=lambda:'missing@invalid'
        with self.assertRaisesRegex(RuntimeError,'owner account unavailable'):self.m.main()
        self.assertNotIn(('systemctl','stop',self.m.SERVICE),self.calls);self.preserved(True)
        self.m.configured_owner=lambda:'a@invalid'
        with sqlite3.connect(self.m.DB) as c:c.execute('UPDATE users SET active=0 WHERE id=1')
        self.remember()
        with self.assertRaisesRegex(RuntimeError,'owner account unavailable'):self.m.main()
        self.preserved(True)

    def test_owner_setting_reads_only_running_service_identity(self):
        namespace={}
        tree=ast.parse((ROOT/'ops/apply_pr250_guarded.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='configured_owner')
        namespace.update(subprocess=subprocess,pathlib=__import__('pathlib'),SERVICE=self.m.SERVICE)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'owner-check','exec'),namespace)
        read=namespace['configured_owner']
        with patch.object(subprocess,'check_output',return_value='123\n') as command:
            for env,result in [(b'OTHER=not-logged\0AZBAZE_OWNER_EMAIL= A@INVALID \0','a@invalid'),
                               (b'OTHER=not-logged\0',None),(b'AZBAZE_OWNER_EMAIL= \0',None)]:
                with patch.object(Path,'read_bytes',return_value=env):
                    if result:self.assertEqual(read(),result)
                    else:
                        with self.assertRaisesRegex(RuntimeError,'not configured'):read()
            command.assert_called_with(['systemctl','show',self.m.SERVICE,'-p','MainPID','--value'],text=True)
        with patch.object(subprocess,'check_output',return_value='0\n'):
            with self.assertRaisesRegex(RuntimeError,'PID unavailable'):read()
        self.assertNotIn('not-logged',self.output.getvalue())

    def test_commit_releases_writer_lock_before_start(self):
        original=self.m.run
        def run(*args):
            if args==('systemctl','start',self.m.SERVICE):
                with sqlite3.connect(self.m.DB,timeout=0.01) as c:
                    c.execute('BEGIN IMMEDIATE')
                    for table in self.m.NEW_TABLES:self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
                    c.rollback()
            original(*args)
        self.m.run=run;self.m.main();self.preserved()

    def test_rollback_blocked_if_control_received_new_user_data(self):
        self.fail_once(lambda c:c.execute('INSERT INTO control_grants VALUES(1,0)'))
        with self.assertRaisesRegex(RuntimeError,'automatic rollback blocked'):self.m.main()
        with sqlite3.connect(self.m.DB) as c:
            self.assertEqual(c.execute('SELECT * FROM control_grants').fetchall(),[(1,0)])
            self.assertEqual(self.m.data_fingerprint(c,self.tables),self.data)
        for n,o,h in self.m.ROWS:self.assertEqual(self.m.blob(self.m.live(n).read_bytes()),h)
        self.assertTrue(all(self.active.values()));self.assertNotIn('PRODUCTION_DEPLOY=PASS',self.output.getvalue())

    def test_real_server_owner_control_pages_and_known_knowledge_registration(self):
        boot=self.real_server(knowledge=True)
        def verify():
            self.pages(boot)
            result=boot("\nwith c.session_transaction() as s:s['user_id']=1\nfor p in ('/control/','/control/access/','/control/ztl/','/control/ztl/?view=work','/synthetic-knowledge-check'):\n r=c.get(p); assert r.status_code==200,(p,r.status_code,r.get_data(as_text=True)[:300])\n")
            self.assertEqual(result.returncode,0,result.stderr)
        self.m.wait_health=verify;self.m.main();self.preserved()

    def setUp(self):
            spec=importlib.util.spec_from_file_location('pr250',ROOT/'ops/apply_pr250_guarded.py')
            self.m=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.m);m=self.m
            tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
            m.DB=self.root/'auth.db';m.BACKUP_ROOT=self.root/'backups';m.BACKUP_ROOT.mkdir()
            m.STATE=self.root/'retry.json';m.STATE.write_bytes(b'{"preserve":"retry"}')
            m.GATE=self.root/'retry.lock';m.GATE.touch();m.LOCK=self.root/'installer.lock'
            m.live=lambda name:self.root/'live'/name
            self.raw={n:(ROOT/n).read_bytes() for n,_,_ in m.ROWS};self.old={}
            for name,old,new in m.ROWS:
                self.assertEqual(m.blob(self.raw[name]),new)
                m.live(name).parent.mkdir(parents=True,exist_ok=True)
                if old:
                    data=subprocess.check_output(['git','show',BASE+':'+name],cwd=ROOT)
                    self.assertEqual(m.blob(data),old);self.old[name]=data
                    m.live(name).write_bytes(data);m.live(name).chmod(0o640)
            self.dependencies={};self.extra_files={}
            for name,expected in m.DEPENDENCIES.items():
                data=(ROOT/name).read_bytes();self.assertEqual(m.blob(data),expected)
                m.live(name).parent.mkdir(parents=True,exist_ok=True);m.live(name).write_bytes(data)
                self.dependencies[name]=data
            with sqlite3.connect(m.DB) as c:
                for path in ('app.py','ztl.py','ztl_control.py','ztl_labor.py'):c.executescript(constant(ROOT/'az-baze-auth'/path))
                c.execute("INSERT INTO users(id,email,full_name,password_hash,is_admin,active,must_change_password,created_at,updated_at) VALUES(1,'a@invalid','Fixture','hash',1,1,0,'t','t')")
                c.executescript("""
                    CREATE TABLE reports(id INTEGER PRIMARY KEY AUTOINCREMENT,value REAL,note TEXT,document BLOB);
                    INSERT INTO reports VALUES(1,NULL,'unknown',X'00ff'),(2,0,'zero',NULL),(3,-35.75,'negative',X'bb00');
                    INSERT INTO ztl_names VALUES(1,'employee','Прежний техник',4),(2,'work','Изделие',2);
                    INSERT INTO ztl_parties VALUES(1,'Плательщик','private',1);
                    INSERT INTO ztl_orders VALUES(1,'LAB-OLD','2026-10-01','2026-10-01','external',1,'Synthetic',NULL,NULL,2,'keep',2400000,5,7);
                    INSERT INTO ztl_cards VALUES(2,'[]',850000,4);
                    INSERT INTO ztl_product_prices VALUES(2,NULL,2400000);
                    INSERT INTO ztl_production VALUES(1,2,'[]',1,500000,'2026-10-02','[1]');
                    INSERT INTO ztl_items VALUES(1,'Проволока','м','material',3);
                    INSERT INTO ztl_moves VALUES(1,1,'2026-10-01',10000,20000,'receipt',1,'keep');
                    INSERT INTO ztl_costs VALUES(1,'2026-10','payroll',1,1000000,50000,6);
                    INSERT INTO ztl_history VALUES(1,'ztl_names',50,'Удаление',1,'old','{}','{}');
                    INSERT INTO ztl_history VALUES(2,'ztl_orders',1,'Создание',1,'old',NULL,'{}');
                """)
            self.remember()
            self.active={s:True for s in (m.SERVICE,*m.TIMERS)};self.calls=[]
            def run(*args):
                self.calls.append(args);self.assertEqual(args[0],'systemctl');unit=args[-1];self.assertIn(unit,self.active)
                if args[1]=='is-active':self.assertTrue(self.active[unit])
                elif args[1] in ('start','stop'):self.active[unit]=args[1]=='start'
                else:self.fail(str(args))
            m.configured_owner=lambda:'a@invalid'
            m.run=run;m.download=lambda n:self.raw[n];m.health=lambda:self.assertTrue(self.active[m.SERVICE]);m.wait_health=m.health
            stack=contextlib.ExitStack();self.addCleanup(stack.close)
            stack.enter_context(patch.object(sys,'argv',['installer','--apply']))
            stack.enter_context(patch.object(m.os,'geteuid',return_value=0));stack.enter_context(patch.object(m.os,'chown'))
            self.output=io.StringIO();stack.enter_context(contextlib.redirect_stdout(self.output))

    def dump(self):
            with sqlite3.connect(self.m.DB) as c:return '\n'.join(c.iterdump())

    def test_wrong_download_stops_before_app_stop(self):
            self.m.download=lambda n:b'wrong'
            with self.assertRaisesRegex(RuntimeError,'download hash mismatch'):self.m.main()
            self.preserved(True);self.assertNotIn(('systemctl','stop',self.m.SERVICE),self.calls)

    def test_live_and_dependency_mismatch_stop(self):
            for name in ('az-baze-auth/ztl.py','az-baze-auth/medical_supplies.py','az-baze-auth/server.py'):
                p=self.m.live(name);saved=p.read_bytes();p.write_bytes(b'newer file')
                with self.assertRaisesRegex(RuntimeError,'LIVE_BASE_MISMATCH'):self.m.main()
                self.assertEqual(p.read_bytes(),b'newer file');p.write_bytes(saved)
            self.preserved(True);self.assertFalse(self.calls)

    def test_reports_all_file_mismatches_without_stopping_services(self):
            paths=('az-baze-auth/server.py','az-baze-auth/medical_supplies.py')
            for name in paths:self.m.live(name).write_bytes(b'unrecognized version')
            try:
                with self.assertRaises(RuntimeError) as raised:self.m.main()
                message=str(raised.exception)
                for name in paths:self.assertIn(str(self.m.live(name)),message)
                self.assertEqual(message.count('actual='+self.m.blob(b'unrecognized version')),2)
                self.assertFalse(self.calls);self.assertEqual(self.dump(),self.original_dump)
            finally:
                for name in paths:self.m.live(name).write_bytes(self.dependencies[name])
            self.preserved(True)

    def test_existing_new_file_not_overwritten(self):
            p=self.m.live('az-baze-auth/control_center.py');p.write_bytes(b'unrelated')
            with self.assertRaisesRegex(RuntimeError,'already exists'):self.m.main()
            self.assertEqual(p.read_bytes(),b'unrelated');p.unlink();self.preserved(True)

    def test_symlink_rejected(self):
            p=self.m.live('az-baze-auth/ztl.py');saved=p.read_bytes();p.unlink();p.symlink_to(self.m.DB)
            with self.assertRaisesRegex(RuntimeError,'symlink'):self.m.main()
            p.unlink();p.write_bytes(saved);self.preserved(True)

    def test_schema_mismatch_stops_without_mutation(self):
            with sqlite3.connect(self.m.DB) as c:c.execute('ALTER TABLE ztl_production ADD COLUMN stray TEXT')
            self.remember()
            with self.assertRaisesRegex(RuntimeError,'legacy ZTL/control schema mismatch'):self.m.main()
            self.preserved(True);self.assertNotIn(('systemctl','stop',self.m.SERVICE),self.calls)

    def test_migration_source_is_pinned(self):
            bad=dict(self.raw);bad['az-baze-auth/control_center.py']+=b'\n# changed'
            with self.assertRaisesRegex(RuntimeError,'migration source hash mismatch'):self.m.migration_source(bad)
            self.preserved(True)

    def test_mid_file_failure_removes_new_files_and_restores_old(self):
            original=self.m.install;calls=0
            def fail(*args):
                nonlocal calls
                calls+=1;original(*args)
                if calls==3:raise RuntimeError('synthetic write failure')
            self.m.install=fail
            with self.assertRaisesRegex(RuntimeError,'synthetic write failure'):self.m.main()
            self.preserved(True)

    def test_commit_failure_rolls_back_schema_and_files(self):
            real_connect=sqlite3.connect
            class FailingCommit(sqlite3.Connection):
                def commit(self):raise RuntimeError('synthetic commit failure')
            def connect(database,*args,**kwargs):
                if str(database)==self.m.DB.as_uri()+'?mode=rw':kwargs['factory']=FailingCommit
                return real_connect(database,*args,**kwargs)
            with patch.object(self.m.sqlite3,'connect',side_effect=connect):
                with self.assertRaisesRegex(RuntimeError,'synthetic commit failure'):self.m.main()
            self.preserved(True)

    def fail_once(self,mutate=lambda c:None):
            calls=0
            def fail():
                nonlocal calls
                calls+=1
                if calls==1:
                    with sqlite3.connect(self.m.DB) as c:mutate(c)
                    raise RuntimeError('synthetic health failure')
                self.m.health()
            self.m.wait_health=fail

    def test_health_failure_restores_old_app_with_empty_control_tables(self):
            self.fail_once()
            with self.assertRaisesRegex(RuntimeError,'synthetic health failure'):self.m.main()
            self.preserved(True);self.assertNotIn('PRODUCTION_DEPLOY=PASS',self.output.getvalue())

    def test_rollback_preserves_unrelated_writes_after_restart(self):
            self.fail_once(lambda c:c.execute("UPDATE reports SET note='new user value' WHERE id=2"))
            with self.assertRaisesRegex(RuntimeError,'synthetic health failure'):self.m.main()
            with sqlite3.connect(self.m.DB) as c:
                self.assertEqual(c.execute('SELECT note FROM reports WHERE id=2').fetchone()[0],'new user value')
                c.execute("UPDATE reports SET note='zero' WHERE id=2")
            self.preserved(True)

    def test_repeat_installer_rejected_without_data_changes(self):
            self.m.main();before=self.dump()
            with self.assertRaisesRegex(RuntimeError,'LIVE_BASE_MISMATCH'):self.m.main()
            self.assertEqual(self.dump(),before);self.preserved()

    def test_wal_database_preserves_data(self):
            with sqlite3.connect(self.m.DB) as c:self.assertEqual(c.execute('PRAGMA journal_mode=WAL').fetchone()[0],'wal')
            self.m.main();self.preserved()

    def test_flags_and_root_required_before_any_mutation(self):
            for args in ([],['--add-technicians'],['--apply','unknown']):
                with patch.object(sys,'argv',['installer',*args]):
                    with self.assertRaisesRegex(RuntimeError,'root and --apply required'):self.m.main()
            with patch.object(self.m.os,'geteuid',return_value=1000):
                with self.assertRaisesRegex(RuntimeError,'root and --apply required'):self.m.main()
            self.assertFalse(self.calls);self.preserved(True)

    def use_knowledge_server(self):
            name='az-baze-auth/server.py'
            data=(ROOT/name).read_bytes()
            self.assertEqual(data.count(b'import upload_reconcile\n'),1)
            self.assertEqual(data.count(b'clinic_structure_settings.register(app)\n'),1)
            data=data.replace(b'import upload_reconcile\n',b'import upload_reconcile\nimport knowledge_catalog\n')
            data=data.replace(b'clinic_structure_settings.register(app)\n',b'clinic_structure_settings.register(app)\nknowledge_catalog.register(app)\n')
            self.assertEqual(self.m.blob(data),'8c9a1b45390e273a1e8f1bc8a12f331cf0fbdd47')
            self.m.live(name).write_bytes(data);self.dependencies[name]=data
            # The production module is outside the PR/repository. This test double
            # checks retention and register(app) execution, not its business logic.
            module=b"def register(app):\n    app.add_url_rule('/synthetic-knowledge-check', 'synthetic_knowledge', lambda: 'registered')\n"
            self.m.live('az-baze-auth/knowledge_catalog.py').write_bytes(module)
            self.extra_files['az-baze-auth/knowledge_catalog.py']=module

    def test_known_production_server_accepted_and_kept_byte_for_byte(self):
            self.use_knowledge_server();self.m.main();self.preserved()
            backup,=self.m.BACKUP_ROOT.glob('pr250-*')
            files=json.loads((backup/'manifest.json').read_text())['files']
            self.assertFalse(any('server.py' in n or 'knowledge_catalog' in n for n,old,new in files))

    def test_other_server_changes_remain_rejected(self):
            self.use_knowledge_server();p=self.m.live('az-baze-auth/server.py')
            p.write_bytes(p.read_bytes()+b'\n# unknown modification\n')
            with self.assertRaisesRegex(RuntimeError,'LIVE_BASE_MISMATCH'):self.m.main()
            self.assertFalse(self.calls);self.assertEqual(self.dump(),self.original_dump)
            p.write_bytes(self.dependencies['az-baze-auth/server.py']);self.preserved(True)

    def test_rollback_retains_knowledge_registration_and_module(self):
            self.use_knowledge_server();self.fail_once()
            with self.assertRaisesRegex(RuntimeError,'synthetic health failure'):self.m.main()
            self.preserved(True)

    def real_server(self,knowledge=False):
            appdir=self.m.live('az-baze-auth/app.py').parent
            for path in (ROOT/'az-baze-auth').glob('*.py'):shutil.copy2(path,appdir/path.name)
            shutil.copytree(ROOT/'az-baze-auth/templates',appdir/'templates',dirs_exist_ok=True)
            for name,old,new in self.m.ROWS:
                if old is None:self.m.live(name).unlink()
                else:self.m.live(name).write_bytes(self.old[name])
            if knowledge:self.use_knowledge_server()
            env=dict(os.environ,AZBAZE_SECRET_KEY='synthetic-startup-test',AZBAZE_DB=str(self.m.DB),AZBAZE_CREDENTIALS_KEY=str(self.root/'key'),AZBAZE_SITE_ROOT=str(self.root),AZBAZE_OWNER_EMAIL='a@invalid')
            startup="import server; c=server.app.test_client(); assert c.get('/health').status_code==200"
            def boot(extra=''):
                return subprocess.run([sys.executable,'-B','-c',startup+extra],cwd=appdir,env=env,capture_output=True,text=True,timeout=25)
            baseline=boot();self.assertEqual(baseline.returncode,0,baseline.stderr)
            self.remember();return boot

    def pages(self,boot):
            extra="\nwith c.session_transaction() as s:s['user_id']=1\nfor p in ('/accounting/ztl/','/accounting/ztl/order','/accounting/ztl/orders/','/accounting/ztl/payroll/','/accounting/ztl/products/'):\n r=c.get(p); assert r.status_code==200,(p,r.status_code,r.get_data(as_text=True)[:300])\n"
            result=boot(extra);self.assertEqual(result.returncode,0,result.stderr)

    def test_real_server_recovers_old_forms_after_health_failure(self):
            boot=self.real_server();calls=0
            def check():
                nonlocal calls
                calls+=1;self.pages(boot)
                if calls==1:raise RuntimeError('synthetic post-start failure')
            self.m.wait_health=check
            with self.assertRaisesRegex(RuntimeError,'synthetic post-start failure'):self.m.main()
            self.preserved(True);self.assertIn('PREVIOUS_SERVICE_HEALTH=PASS',self.output.getvalue())
