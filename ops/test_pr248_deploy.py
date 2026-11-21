"""Installer checks use only temporary files/DB and mocked network/systemd."""
import ast,contextlib,importlib.util,io,os,shutil,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
BASE='5ea04ec63d402d320fb9a54540883b3b48ceec21'

def constant(path,name='SCHEMA'):
    tree=ast.parse(path.read_bytes())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets))

class InstallerTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('pr248',ROOT/'ops/apply_pr248_guarded.py')
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
            for path,key in [('app.py','SCHEMA'),('credentials_store.py','SCHEMA'),('personal_credentials.py','SCHEMA'),('access_contacts_ext.py','CONTACTS_SCHEMA')]:
                c.executescript(constant(ROOT/'az-baze-auth'/path,key))
            c.execute("INSERT INTO users(id,email,full_name,password_hash,is_admin,active,must_change_password,created_at,updated_at) VALUES(1,'a@invalid','Fixture','hash',1,1,0,'t','t')")
            c.execute("INSERT INTO work_contacts(organization,organization_role,full_name,phone,responsibility,created_by,updated_by,created_at,updated_at) VALUES('Org','Role','Contact','+7 999 111-22-33','Full note',1,1,'t','t')")
            c.execute("INSERT INTO credentials(service_name,site_url,login,secret_token,note,created_by,updated_by,created_at,updated_at) VALUES('Service','','Login','encrypted-secret','Note',1,1,'t','t')")
            c.executescript("CREATE TABLE reports(id INTEGER PRIMARY KEY,value REAL,note TEXT); INSERT INTO reports VALUES(1,NULL,'unknown'),(2,0,'zero'),(3,-35.75,'negative');")
        with sqlite3.connect(m.DB) as c:
            c.executescript(m.LEGACY_SCHEMA)
            defaults=constant(ROOT/'az-baze-auth/access_groups.py','DEFAULTS')
            c.executemany('INSERT INTO work_groups(name,name_key) VALUES(?,?)',[(n,n.casefold()) for n in defaults])
            for ident,name,parent,version in [(9,'Своя группа',None,3),(10,'Подгруппа',9,4),(11,'Медицинские системы',None,2),(12,'Неиспользуемая',None,1)]:
                c.execute('INSERT INTO work_groups VALUES(?,?,?,?,?)',(ident,name,name.casefold(),parent,version))
            for ident in range(2,22):
                c.execute("INSERT INTO credentials(id,service_name,login,secret_token,note,created_by,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,1,1,'t','t')",(ident,'Service '+str(ident),'Login '+str(ident),bytes([ident,0,255]),'Full note'))
            c.execute("INSERT INTO credential_trash VALUES(2,1,'t')")
            c.execute("INSERT INTO personal_credentials(owner_id,service_name,login,secret_token,created_at,updated_at) VALUES(1,'Private','private',X'00ffaa','t','t')")
            c.execute('INSERT INTO work_contact_groups VALUES(1,10)')
            c.executemany('INSERT INTO work_credential_groups VALUES(?,?)',[(i,[1,9,10,11][i%4]) for i in range(1,21)])
            c.execute("UPDATE sqlite_sequence SET seq=50 WHERE name='work_groups'")
        with sqlite3.connect(m.DB) as c:
            self.before=m.schema_objects(c)
            self.tables=[n for n,r in self.before.items() if r[0]=='table' and n not in ('sqlite_sequence','work_groups','work_credential_groups')]
            self.data=m.data_fingerprint(c,self.tables)
            self.sequence=sorted(c.execute("SELECT name,seq FROM sqlite_sequence WHERE name<>'work_groups'").fetchall())
            self.original_dump='\n'.join(c.iterdump())
            self.old_groups=c.execute('SELECT * FROM work_groups ORDER BY id').fetchall()
            self.old_paths=c.execute('SELECT a.record_id,g.name,p.name,g.version FROM work_credential_groups a JOIN work_groups g ON g.id=a.group_id LEFT JOIN work_groups p ON p.id=g.parent_id ORDER BY a.record_id').fetchall()
        self.active={s:True for s in (m.SERVICE,*m.TIMERS)};self.calls=[]
        def run(*args):
            self.calls.append(args);self.assertEqual(args[0],'systemctl');unit=args[-1];self.assertIn(unit,self.active)
            if args[1]=='is-active':self.assertTrue(self.active[unit])
            elif args[1] in ('start','stop'):self.active[unit]=args[1]=='start'
            else:self.fail(str(args))
        m.run=run;m.download=lambda n:self.raw[n];m.health=lambda:self.assertTrue(self.active[m.SERVICE]);m.wait_health=m.health
        stack=contextlib.ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(sys,'argv',['installer','--apply']))
        stack.enter_context(patch.object(m.os,'geteuid',return_value=0));stack.enter_context(patch.object(m.os,'chown'))
        self.output=io.StringIO();stack.enter_context(contextlib.redirect_stdout(self.output))

    def dump(self):
        with sqlite3.connect(self.m.DB) as c:return '\n'.join(c.iterdump())

    def preserved(self,rolled_back=False):
        m=self.m
        with sqlite3.connect(m.DB) as c:
            self.assertEqual(m.data_fingerprint(c,self.tables),self.data)
            self.assertEqual(sorted(c.execute("SELECT name,seq FROM sqlite_sequence WHERE name<>'work_groups'").fetchall()),self.sequence)
            if rolled_back:
                # Rebuilding this table may reorder sqlite_sequence rows. Check
                # every schema object and every row value, not dump row order.
                with sqlite3.connect(':memory:') as expected:
                    expected.executescript(self.original_dump)
                    self.assertEqual(m.schema_objects(c),m.schema_objects(expected))
                    tables=[n for n,r in m.schema_objects(expected).items() if r[0]=='table']
                    self.assertEqual(m.data_fingerprint(c,tables),m.data_fingerprint(expected,tables))
            else:
                self.assertEqual(c.execute("SELECT id,name,name_key,parent_id,version FROM work_groups WHERE kind='contacts' ORDER BY id").fetchall(),self.old_groups)
                paths=c.execute('SELECT a.record_id,g.name,p.name,g.version FROM work_credential_groups a JOIN work_groups g ON g.id=a.group_id LEFT JOIN work_groups p ON p.id=g.parent_id ORDER BY a.record_id').fetchall()
                self.assertEqual(paths,self.old_paths)
                self.assertEqual(c.execute('SELECT count(*) FROM work_credential_groups WHERE record_id=21').fetchone()[0],0)
                self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        for name,old,new in m.ROWS:self.assertEqual(m.blob(m.live(name).read_bytes()),old if rolled_back else new)
        self.assertTrue(all(self.active.values()));self.assertEqual(m.STATE.read_bytes(),b'{"preserve":"retry"}')

    def test_success_preserves_twenty_paths_records_secrets_and_backup(self):
        m=self.m;m.main();self.preserved()
        with sqlite3.connect(m.DB) as c:
            roots=[r[0] for r in c.execute("SELECT name FROM work_groups WHERE kind='credentials' AND parent_id IS NULL")]
            approved=constant(ROOT/'az-baze-auth/access_groups.py','CREDENTIAL_DEFAULTS')
            self.assertTrue(set(approved)<=set(roots));self.assertEqual(roots.count('Медицинские системы'),1)
            self.assertNotIn('Неиспользуемая',roots)
        backup,=m.BACKUP_ROOT.glob('pr248-*')
        with sqlite3.connect(backup/'auth.db') as c:self.assertEqual('\n'.join(c.iterdump()),self.original_dump)
        for name,old,new in m.ROWS:self.assertEqual(m.live(name).stat().st_mode&0o777,0o640)
        self.assertIn('PR248_PRODUCTION_DEPLOY=PASS; FILES=4',self.output.getvalue())
        self.assertIn('PRESERVED_CREDENTIAL_ASSIGNMENTS=20',self.output.getvalue())

    def test_wrong_download_stops_before_app_stop(self):
        self.m.download=lambda n:b'wrong'
        with self.assertRaisesRegex(RuntimeError,'download hash mismatch'):self.m.main()
        self.preserved(True);self.assertNotIn(('systemctl','stop',self.m.SERVICE),self.calls)

    def test_live_and_dependency_mismatch_stop(self):
        for name in ('az-baze-auth/access_groups.py','az-baze-auth/personal_credentials.py'):
            p=self.m.live(name);saved=p.read_bytes();p.write_bytes(b'newer file')
            with self.assertRaisesRegex(RuntimeError,'LIVE_BASE_MISMATCH'):self.m.main()
            self.assertEqual(p.read_bytes(),b'newer file');p.write_bytes(saved)
        self.preserved(True);self.assertFalse(self.calls)

    def test_unprepared_schema_not_overwritten(self):
        with sqlite3.connect(self.m.DB) as c:c.execute('ALTER TABLE work_groups ADD COLUMN stray TEXT')
        self.original_dump=self.dump()
        with self.assertRaisesRegex(RuntimeError,'legacy group schema mismatch'):self.m.main()
        self.preserved(True);self.assertNotIn(('systemctl','stop',self.m.SERVICE),self.calls)

    def test_migration_source_is_pinned(self):
        bad=dict(self.raw);bad['az-baze-auth/access_groups.py']+=b'\n# changed'
        with self.assertRaisesRegex(RuntimeError,'migration source hash mismatch'):self.m.migration_source(bad)
        self.preserved(True)

    def test_trigger_failure_rolls_back_schema_and_assignments(self):
        with sqlite3.connect(self.m.DB) as c:c.execute("CREATE TRIGGER fail_move BEFORE UPDATE ON work_credential_groups BEGIN SELECT RAISE(ABORT,'synthetic move failure'); END")
        self.original_dump=self.dump()
        with self.assertRaisesRegex(sqlite3.IntegrityError,'synthetic move failure'):self.m.main()
        self.preserved(True)

    def inject_migration(self,extra):
        original=self.m.migration_source
        def source(data):
            scope=original(data);migrate=scope['init_schema']
            def changed(c):migrate(c);extra(c)
            scope['init_schema']=changed
            return scope
        self.m.migration_source=source

    def test_existing_record_mutation_is_detected_and_rolled_back(self):
        self.inject_migration(lambda c:c.execute('UPDATE reports SET value=99 WHERE id=2'))
        with self.assertRaisesRegex(RuntimeError,'existing records or secrets changed'):self.m.main()
        self.preserved(True)

    def test_extra_schema_change_is_detected_and_rolled_back(self):
        self.inject_migration(lambda c:c.execute('ALTER TABLE reports ADD COLUMN stray TEXT'))
        with self.assertRaisesRegex(RuntimeError,'unexpected schema change'):self.m.main()
        self.preserved(True)

    def test_wrong_service_path_is_detected_and_rolled_back(self):
        self.inject_migration(lambda c:c.execute("UPDATE work_credential_groups SET group_id=(SELECT id FROM work_groups WHERE kind='credentials' AND name='Реклама и аналитика') WHERE record_id=1"))
        with self.assertRaisesRegex(RuntimeError,'credential assignment changed'):self.m.main()
        self.preserved(True)

    def test_sequence_mutation_is_detected_and_rolled_back(self):
        self.inject_migration(lambda c:c.execute("UPDATE sqlite_sequence SET seq=999 WHERE name='credentials'"))
        with self.assertRaisesRegex(RuntimeError,'unrelated sequence changed'):self.m.main()
        self.preserved(True)

    def test_mid_file_failure_restores_files_and_old_schema(self):
        original=self.m.install;calls=0
        def fail(*args):
            nonlocal calls
            calls+=1;original(*args)
            if calls==2:raise RuntimeError('synthetic write failure')
        self.m.install=fail
        with self.assertRaisesRegex(RuntimeError,'synthetic write failure'):self.m.main()
        self.preserved(True);self.assertIn('MIGRATION_ROLLBACK=PASS',self.output.getvalue())

    def test_health_failure_restores_files_and_old_schema(self):
        calls=0
        def fail():
            nonlocal calls
            calls+=1
            if calls==1:raise RuntimeError('synthetic health failure')
            self.m.health()
        self.m.wait_health=fail
        with self.assertRaisesRegex(RuntimeError,'synthetic health failure'):self.m.main()
        self.preserved(True);self.assertNotIn('PRODUCTION_DEPLOY=PASS',self.output.getvalue())

    def test_migration_committed_and_writer_lock_released_before_startup(self):
        def check_health():
            self.m.health()
            with sqlite3.connect(self.m.DB,timeout=0.01) as c:
                self.assertIn('kind',{r[1] for r in c.execute('PRAGMA table_info(work_groups)')})
                c.execute('BEGIN IMMEDIATE');c.rollback()
        self.m.wait_health=check_health;self.m.main();self.preserved()

    def test_repeat_does_not_change_data(self):
        self.m.main();before=self.dump()
        with self.assertRaisesRegex(RuntimeError,'LIVE_BASE_MISMATCH'):self.m.main()
        self.assertEqual(self.dump(),before);self.preserved()

    def test_wal_database_preserves_data(self):
        with sqlite3.connect(self.m.DB) as c:self.assertEqual(c.execute('PRAGMA journal_mode=WAL').fetchone()[0],'wal')
        self.m.main();self.preserved()

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

    def test_empty_catalogue_initializes_both_sets(self):
        with sqlite3.connect(self.m.DB) as c:
            c.execute('DELETE FROM work_contact_groups');c.execute('DELETE FROM work_credential_groups')
            c.execute('DELETE FROM work_groups WHERE parent_id IS NOT NULL');c.execute('DELETE FROM work_groups')
        self.m.main()
        with sqlite3.connect(self.m.DB) as c:
            self.assertEqual(c.execute('SELECT kind,count(*) FROM work_groups GROUP BY kind ORDER BY kind').fetchall(),[('contacts',8),('credentials',9)])

    def test_rollback_preserves_unrelated_writes_after_restart(self):
        calls=0
        def fail_once():
            nonlocal calls
            calls+=1
            if calls==1:
                with sqlite3.connect(self.m.DB) as c:c.execute("UPDATE reports SET note='new user value' WHERE id=2")
                raise RuntimeError('synthetic health failure')
            self.m.health()
        self.m.wait_health=fail_once
        with self.assertRaisesRegex(RuntimeError,'synthetic health failure'):self.m.main()
        with sqlite3.connect(self.m.DB) as c:
            self.assertEqual(c.execute('SELECT note FROM reports WHERE id=2').fetchone()[0],'new user value')
            self.assertNotIn('kind',{r[1] for r in c.execute('PRAGMA table_info(work_groups)')})
            self.assertEqual(c.execute('SELECT * FROM work_groups ORDER BY id').fetchall(),self.old_groups)
        for name,old,new in self.m.ROWS:self.assertEqual(self.m.blob(self.m.live(name).read_bytes()),old)
        self.assertTrue(all(self.active.values()));self.assertIn('PREVIOUS_SERVICE_HEALTH=PASS',self.output.getvalue())

    def test_rollback_does_not_overwrite_groups_changed_after_restart(self):
        calls=0
        def fail_once():
            nonlocal calls
            calls+=1
            if calls==1:
                with sqlite3.connect(self.m.DB) as c:c.execute("UPDATE work_groups SET name='User renamed' WHERE kind='credentials' AND name='Реклама и аналитика'")
                raise RuntimeError('synthetic health failure')
            self.m.health()
        self.m.wait_health=fail_once
        with self.assertRaisesRegex(RuntimeError,'automatic rollback blocked'):self.m.main()
        with sqlite3.connect(self.m.DB) as c:
            self.assertIn('kind',{r[1] for r in c.execute('PRAGMA table_info(work_groups)')})
            self.assertEqual(c.execute("SELECT count(*) FROM work_groups WHERE name='User renamed'").fetchone()[0],1)
        for name,old,new in self.m.ROWS:self.assertEqual(self.m.blob(self.m.live(name).read_bytes()),new)
        self.assertTrue(all(self.active.values()));self.assertNotIn('PRODUCTION_DEPLOY=PASS',self.output.getvalue())

    def real_server(self):
        appdir=self.m.live('az-baze-auth/app.py').parent
        for path in (ROOT/'az-baze-auth').glob('*.py'):shutil.copy2(path,appdir/path.name)
        shutil.copytree(ROOT/'az-baze-auth/templates',appdir/'templates',dirs_exist_ok=True)
        for name,content in self.old.items():self.m.live(name).write_bytes(content)
        env=dict(os.environ,AZBAZE_SECRET_KEY='synthetic-startup-test',AZBAZE_DB=str(self.m.DB),AZBAZE_CREDENTIALS_KEY=str(self.root/'key'),AZBAZE_SITE_ROOT=str(self.root))
        startup="import server; c=server.app.test_client(); assert c.get('/health').status_code==200"
        def boot(extra=''):
            return subprocess.run([sys.executable,'-B','-c',startup+extra],cwd=appdir,env=env,capture_output=True,text=True,timeout=20)
        baseline=boot();self.assertEqual(baseline.returncode,0,baseline.stderr)
        return boot

    def test_real_server_bootstrap_and_group_pages_after_install(self):
        boot=self.real_server()
        # This is the original installer defect: actual bootstrap requires a
        # write lock, unlike the former mock of systemctl + /health.
        with sqlite3.connect(self.m.DB) as locked:
            locked.execute('BEGIN IMMEDIATE')
            blocked=boot();self.assertNotEqual(blocked.returncode,0)
            self.assertIn('database is locked',blocked.stderr);locked.rollback()
        def actual_health():
            extra="\nwith c.session_transaction() as s:s['user_id']=1\nfor p in ('/credentials/','/contacts/','/access-contacts/groups/credentials/','/access-contacts/groups/contacts/'):\n r=c.get(p); assert r.status_code==200,(p,r.status_code)\nprint('REAL_SERVER_HEALTH_AND_GROUP_PAGES=PASS')"
            result=boot(extra);self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('REAL_SERVER_HEALTH_AND_GROUP_PAGES=PASS',result.stdout)
        self.m.wait_health=actual_health
        self.m.main()
        self.assertIn('PR248_PRODUCTION_DEPLOY=PASS',self.output.getvalue())

    def test_real_server_recovers_old_pages_after_health_failure(self):
        boot=self.real_server();before=self.dump();calls=0
        def check_and_fail_once():
            nonlocal calls
            calls+=1
            path='/access-contacts/groups/credentials/' if calls==1 else '/access-contacts/groups/'
            result=boot("\nwith c.session_transaction() as s:s['user_id']=1\nfor p in ('/credentials/','/contacts/',"+repr(path)+"):\n r=c.get(p); assert r.status_code==200,(p,r.status_code)")
            self.assertEqual(result.returncode,0,result.stderr)
            if calls==1:raise RuntimeError('synthetic post-start health failure')
        self.m.wait_health=check_and_fail_once
        with self.assertRaisesRegex(RuntimeError,'synthetic post-start health failure'):self.m.main()
        with sqlite3.connect(':memory:') as expected,sqlite3.connect(self.m.DB) as actual:
            expected.executescript(before)
            self.assertEqual(self.m.schema_objects(actual),self.m.schema_objects(expected))
            tables=[n for n,r in self.m.schema_objects(expected).items() if r[0]=='table']
            self.assertEqual(self.m.data_fingerprint(actual,tables),self.m.data_fingerprint(expected,tables))
        self.assertIn('PREVIOUS_SERVICE_HEALTH=PASS',self.output.getvalue())

if __name__=='__main__':unittest.main()

