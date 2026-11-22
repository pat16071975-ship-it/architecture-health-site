"""Guarded installer: real temporary SQLite, synthetic systemd/network, real app startup."""
import ast,contextlib,importlib.util,io,json,os,shutil,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
BASE='0892b78b49086edff3c7d81b63b1a7c55faaa760'

def constant(path,name='SCHEMA'):
    return next(ast.literal_eval(n.value) for n in ast.parse(path.read_bytes()).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets))

class InstallerTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('pr249',ROOT/'ops/apply_pr249_guarded.py')
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
        for name,expected in m.DEPENDENCIES.items():
            data=(ROOT/name).read_bytes();self.assertEqual(m.blob(data),expected)
            m.live(name).parent.mkdir(parents=True,exist_ok=True);m.live(name).write_bytes(data)
        with sqlite3.connect(m.DB) as c:
            for path in ('app.py','ztl.py','ztl_control.py'):c.executescript(constant(ROOT/'az-baze-auth'/path))
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
        m.run=run;m.download=lambda n:self.raw[n];m.health=lambda:self.assertTrue(self.active[m.SERVICE]);m.wait_health=m.health
        stack=contextlib.ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(sys,'argv',['installer','--apply','--add-technicians']))
        stack.enter_context(patch.object(m.os,'geteuid',return_value=0));stack.enter_context(patch.object(m.os,'chown'))
        self.output=io.StringIO();stack.enter_context(contextlib.redirect_stdout(self.output))

    def dump(self):
        with sqlite3.connect(self.m.DB) as c:return '\n'.join(c.iterdump())

    def remember(self):
        self.original_dump=self.dump()
        with sqlite3.connect(self.m.DB) as c:
            self.before=self.m.schema_objects(c)
            self.tables=[n for n,r in self.before.items() if r[0]=='table' and n not in ('ztl_names','ztl_history')]
            self.data=self.m.data_fingerprint(c,self.tables)
            self.names=dict((r[0],r) for r in c.execute('SELECT * FROM ztl_names'))
            self.history=dict((r[0],r) for r in c.execute('SELECT * FROM ztl_history'))

    def preserved(self,rolled_back=False):
        m=self.m
        with sqlite3.connect(m.DB) as c:
            self.assertEqual(m.data_fingerprint(c,self.tables),self.data)
            if rolled_back:
                with sqlite3.connect(':memory:') as expected:
                    expected.executescript(self.original_dump)
                    self.assertEqual(m.schema_objects(c),m.schema_objects(expected))
                    tables=[n for n,r in m.schema_objects(expected).items() if r[0]=='table']
                    self.assertEqual(m.data_fingerprint(c,tables),m.data_fingerprint(expected,tables))
            else:
                names=dict((r[0],r) for r in c.execute('SELECT * FROM ztl_names'))
                history=dict((r[0],r) for r in c.execute('SELECT * FROM ztl_history'))
                for i,r in self.names.items():self.assertEqual(names[i],r)
                for i,r in self.history.items():self.assertEqual(history[i],r)
                self.assertEqual(c.execute('SELECT COUNT(*) FROM ztl_labor_splits').fetchone()[0],0)
                self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        for name,old,new in m.ROWS:
            if rolled_back and old is None:self.assertFalse(m.live(name).exists())
            else:self.assertEqual(m.blob(m.live(name).read_bytes()),old if rolled_back else new)
        self.assertTrue(all(self.active.values()));self.assertEqual(m.STATE.read_bytes(),b'{"preserve":"retry"}')

    def test_success_adds_five_keeps_old_orders_fees_stock_payroll_and_backup(self):
        m=self.m;m.main();self.preserved()
        with sqlite3.connect(m.DB) as c:
            expected=constant(ROOT/'az-baze-auth/ztl_labor.py','TECHNICIANS')
            self.assertEqual(c.execute("SELECT id,name FROM ztl_names WHERE id>50 ORDER BY id").fetchall(),list(zip(range(51,56),expected)))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ztl_history').fetchone()[0],7)
        backup,=m.BACKUP_ROOT.glob('pr249-*')
        with sqlite3.connect(backup/'auth.db') as c:self.assertEqual('\n'.join(c.iterdump()),self.original_dump)
        manifest=json.loads((backup/'manifest.json').read_text());self.assertEqual(len(manifest['changes']['added_ids']),5)
        for name,old,new in m.ROWS:self.assertEqual(m.live(name).stat().st_mode&0o777,0o640)
        self.assertIn('PR249_PRODUCTION_DEPLOY=PASS; FILES=8',self.output.getvalue())
        self.assertIn('TECHNICIANS=5; ADDED=5; REUSED=0',self.output.getvalue())

    def test_schema_only_does_not_seed_employees(self):
        with patch.object(sys,'argv',['installer','--apply']):self.m.main()
        self.preserved()
        with sqlite3.connect(self.m.DB) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ztl_names').fetchone()[0],2)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ztl_history').fetchone()[0],2)
        self.assertIn('TECHNICIAN_IMPORT=NOT_RUN',self.output.getvalue())

    def test_reuses_normalized_full_names_without_changing_versions(self):
        names=constant(ROOT/'az-baze-auth/ztl_labor.py','TECHNICIANS')
        with sqlite3.connect(self.m.DB) as c:
            c.executemany("INSERT INTO ztl_names VALUES(?,'employee',?,9)",[(10+i,'  '+n.upper()+'  ') for i,n in enumerate(names[:2])])
        self.remember();self.m.main();self.preserved()
        self.assertIn('TECHNICIANS=5; ADDED=3; REUSED=2',self.output.getvalue())

    def test_seed_repeat_is_idempotent_and_preserves_outer_transaction(self):
        self.m.main();before=self.dump();source=self.m.migration_source(self.raw)
        with sqlite3.connect(self.m.DB) as c:
            c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE');result=source['seed'](c)
            self.assertTrue(c.in_transaction);self.assertTrue(all(not r['created'] for r in result))
        self.assertEqual(self.dump(),before)

    def test_abbreviated_name_rolls_back_all_prior_additions_and_schema(self):
        with sqlite3.connect(self.m.DB) as c:c.execute("INSERT INTO ztl_names VALUES(3,'employee','Мосина А. П.',1)")
        self.remember()
        with self.assertRaisesRegex(ValueError,'Уточните существующую'):self.m.main()
        self.preserved(True)

    def test_duplicate_normalized_names_rejected(self):
        with sqlite3.connect(self.m.DB) as c:
            c.executemany("INSERT INTO ztl_names VALUES(?,'employee',?,1)",[(3,'Казанцев Леонид Евгеньевич'),(4,'казанцев леонид евгеньевич')])
        self.remember()
        with self.assertRaisesRegex(ValueError,'несколько записей'):self.m.main()
        self.preserved(True)

    def test_history_failure_rolls_back_schema_employees_and_files(self):
        original=self.m.migration_source
        def source(data):
            scope=original(data);seed=scope['seed']
            def fail(c):
                c.execute("CREATE TEMP TRIGGER fail_history BEFORE INSERT ON ztl_history WHEN NEW.entity_id>=53 BEGIN SELECT RAISE(ABORT,'synthetic history failure'); END")
                return seed(c)
            scope['seed']=fail;return scope
        self.m.migration_source=source
        with self.assertRaisesRegex(sqlite3.IntegrityError,'synthetic history failure'):self.m.main()
        self.preserved(True)

    def test_wrong_download_stops_before_app_stop(self):
        self.m.download=lambda n:b'wrong'
        with self.assertRaisesRegex(RuntimeError,'download hash mismatch'):self.m.main()
        self.preserved(True);self.assertNotIn(('systemctl','stop',self.m.SERVICE),self.calls)

    def test_live_and_dependency_mismatch_stop(self):
        for name in ('az-baze-auth/ztl.py','az-baze-auth/medical_supplies.py'):
            p=self.m.live(name);saved=p.read_bytes();p.write_bytes(b'newer file')
            with self.assertRaisesRegex(RuntimeError,'LIVE_BASE_MISMATCH'):self.m.main()
            self.assertEqual(p.read_bytes(),b'newer file');p.write_bytes(saved)
        self.preserved(True);self.assertFalse(self.calls)

    def test_existing_new_file_not_overwritten(self):
        p=self.m.live('az-baze-auth/ztl_labor.py');p.write_bytes(b'unrelated')
        with self.assertRaisesRegex(RuntimeError,'already exists'):self.m.main()
        self.assertEqual(p.read_bytes(),b'unrelated');p.unlink();self.preserved(True)

    def test_symlink_rejected(self):
        p=self.m.live('az-baze-auth/ztl.py');saved=p.read_bytes();p.unlink();p.symlink_to(self.m.DB)
        with self.assertRaisesRegex(RuntimeError,'symlink'):self.m.main()
        p.unlink();p.write_bytes(saved);self.preserved(True)

    def test_schema_mismatch_stops_without_mutation(self):
        with sqlite3.connect(self.m.DB) as c:c.execute('ALTER TABLE ztl_production ADD COLUMN stray TEXT')
        self.remember()
        with self.assertRaisesRegex(RuntimeError,'legacy ZTL schema mismatch'):self.m.main()
        self.preserved(True);self.assertNotIn(('systemctl','stop',self.m.SERVICE),self.calls)

    def test_migration_source_is_pinned(self):
        bad=dict(self.raw);bad['az-baze-auth/ztl_labor.py']+=b'\n# changed'
        with self.assertRaisesRegex(RuntimeError,'migration source hash mismatch'):self.m.migration_source(bad)
        self.preserved(True)

    def inject(self,extra):
        original=self.m.migration_source
        def source(data):
            scope=original(data);seed=scope['seed']
            def changed(c):result=seed(c);extra(c);return result
            scope['seed']=changed;return scope
        self.m.migration_source=source

    def test_unrelated_mutation_detected_and_rolled_back(self):
        self.inject(lambda c:c.execute('UPDATE reports SET value=99 WHERE id=2'))
        with self.assertRaisesRegex(RuntimeError,'existing data changed'):self.m.main()
        self.preserved(True)

    def test_old_employee_mutation_detected_and_rolled_back(self):
        self.inject(lambda c:c.execute("UPDATE ztl_names SET name='Changed' WHERE id=1"))
        with self.assertRaisesRegex(RuntimeError,'unexpected employee change'):self.m.main()
        self.preserved(True)

    def test_unexpected_schema_detected_and_rolled_back(self):
        self.inject(lambda c:c.execute('ALTER TABLE reports ADD COLUMN stray TEXT'))
        with self.assertRaisesRegex(RuntimeError,'unexpected schema change'):self.m.main()
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

    def test_health_failure_restores_old_app_without_employees(self):
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

    def test_rollback_blocked_when_new_ztl_data_exists(self):
        self.fail_once(lambda c:c.execute("INSERT INTO ztl_labor_splits VALUES(1,1000000,'[]')"))
        with self.assertRaisesRegex(RuntimeError,'automatic rollback blocked'):self.m.main()
        with sqlite3.connect(self.m.DB) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ztl_labor_splits').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ztl_names').fetchone()[0],7)
        for n,o,h in self.m.ROWS:self.assertEqual(self.m.blob(self.m.live(n).read_bytes()),h)
        self.assertTrue(all(self.active.values()));self.assertNotIn('PRODUCTION_DEPLOY=PASS',self.output.getvalue())

    def test_commit_and_writer_lock_released_before_start(self):
        original=self.m.run
        def run(*args):
            if args==('systemctl','start',self.m.SERVICE):
                with sqlite3.connect(self.m.DB,timeout=0.01) as c:
                    c.execute('BEGIN IMMEDIATE')
                    self.assertEqual(c.execute('SELECT COUNT(*) FROM ztl_labor_splits').fetchone()[0],0)
                    self.assertEqual(c.execute('SELECT COUNT(*) FROM ztl_names').fetchone()[0],7)
                    c.rollback()
            original(*args)
        self.m.run=run;self.m.main();self.preserved()

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

    def real_server(self):
        appdir=self.m.live('az-baze-auth/app.py').parent
        for path in (ROOT/'az-baze-auth').glob('*.py'):shutil.copy2(path,appdir/path.name)
        shutil.copytree(ROOT/'az-baze-auth/templates',appdir/'templates',dirs_exist_ok=True)
        for name,old,new in self.m.ROWS:
            if old is None:self.m.live(name).unlink()
            else:self.m.live(name).write_bytes(self.old[name])
        env=dict(os.environ,AZBAZE_SECRET_KEY='synthetic-startup-test',AZBAZE_DB=str(self.m.DB),AZBAZE_CREDENTIALS_KEY=str(self.root/'key'),AZBAZE_SITE_ROOT=str(self.root))
        startup="import server; c=server.app.test_client(); assert c.get('/health').status_code==200"
        def boot(extra=''):
            return subprocess.run([sys.executable,'-B','-c',startup+extra],cwd=appdir,env=env,capture_output=True,text=True,timeout=25)
        baseline=boot();self.assertEqual(baseline.returncode,0,baseline.stderr)
        self.remember();return boot

    def pages(self,boot):
        extra="\nwith c.session_transaction() as s:s['user_id']=1\nfor p in ('/accounting/ztl/','/accounting/ztl/order','/accounting/ztl/orders/','/accounting/ztl/payroll/','/accounting/ztl/products/'):\n r=c.get(p); assert r.status_code==200,(p,r.status_code,r.get_data(as_text=True)[:300])\n"
        result=boot(extra);self.assertEqual(result.returncode,0,result.stderr)

    def test_real_server_starts_and_renders_new_forms(self):
        boot=self.real_server()
        self.m.wait_health=lambda:self.pages(boot)
        self.m.main();self.preserved()
        names=constant(ROOT/'az-baze-auth/ztl_labor.py','TECHNICIANS')
        result=boot("\nwith c.session_transaction() as s:s['user_id']=1\nfor p in ('/accounting/ztl/order','/accounting/ztl/orders/','/accounting/ztl/payroll/'):\n html=c.get(p).get_data(as_text=True)\n for n in "+repr(names)+":assert n in html,(p,n)\n")
        self.assertEqual(result.returncode,0,result.stderr)

    def test_real_server_recovers_old_forms_after_health_failure(self):
        boot=self.real_server();calls=0
        def check():
            nonlocal calls
            calls+=1;self.pages(boot)
            if calls==1:raise RuntimeError('synthetic post-start failure')
        self.m.wait_health=check
        with self.assertRaisesRegex(RuntimeError,'synthetic post-start failure'):self.m.main()
        self.preserved(True);self.assertIn('PREVIOUS_SERVICE_HEALTH=PASS',self.output.getvalue())

if __name__=='__main__':unittest.main()
