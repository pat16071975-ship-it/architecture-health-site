"""Installer checks use only temporary files/DB and mocked network/systemd."""
import ast,contextlib,importlib.util,io,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
BASE='6c50bf5b53c87fea0c74da0c71605155757c93db'

def constant(path,name='SCHEMA'):
    tree=ast.parse(path.read_bytes())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets))

class InstallerTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('pr247',ROOT/'ops/apply_pr247_guarded.py')
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
            self.before=m.schema_objects(c)
            self.tables=[n for n,r in self.before.items() if r[0]=='table' and n!='sqlite_sequence']
            self.data=m.data_fingerprint(c,self.tables)
            self.sequence=sorted(c.execute('SELECT name,seq FROM sqlite_sequence').fetchall())
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

    def preserved(self,schema_same=False,files_old=False):
        m=self.m
        with sqlite3.connect(m.DB) as c:
            self.assertEqual(m.data_fingerprint(c,self.tables),self.data)
            objects=m.schema_objects(c);self.assertEqual({n:objects[n] for n in self.before},self.before)
            sequence=sorted(c.execute("SELECT name,seq FROM sqlite_sequence WHERE name<>'work_groups'").fetchall())
            self.assertEqual(sequence,self.sequence)
            if schema_same:self.assertEqual(objects,self.before)
        if files_old:
            for name,old,new in m.ROWS:
                if old:self.assertEqual(m.live(name).read_bytes(),self.old[name])
                else:self.assertFalse(m.live(name).exists())
        self.assertTrue(all(self.active.values()));self.assertEqual(m.STATE.read_bytes(),b'{"preserve":"retry"}')

    def test_success_metadata_only_and_verified_backup(self):
        m=self.m;m.main();self.preserved()
        for name,old,new in m.ROWS:
            self.assertEqual(m.blob(m.live(name).read_bytes()),new)
            self.assertEqual(m.live(name).stat().st_mode&0o777,0o640)
        with sqlite3.connect(m.DB) as c:
            self.assertEqual(set(m.schema_objects(c))-set(self.before),set((*m.TABLES,m.INDEX)))
            self.assertEqual(c.execute('SELECT name FROM work_groups ORDER BY id').fetchall(),[(n,) for n in m.DEFAULTS])
            for name in m.TABLES[1:]:self.assertEqual(c.execute('SELECT count(*) FROM '+name).fetchone()[0],0)
        backup,=m.BACKUP_ROOT.glob('pr247-*')
        with sqlite3.connect(backup/'auth.db') as c:
            self.assertEqual(m.schema_objects(c),self.before);self.assertEqual(m.data_fingerprint(c,self.tables),self.data)
            self.assertEqual(sorted(c.execute('SELECT name,seq FROM sqlite_sequence').fetchall()),self.sequence)
        self.assertIn('PR247_PRODUCTION_DEPLOY=PASS; FILES=16',self.output.getvalue())
        self.assertIn('DB_DATA_WRITES_BY_INSTALLER=GROUP_METADATA_ONLY',self.output.getvalue())

    def test_download_mismatch_stops_before_service_stop(self):
        self.m.download=lambda _:b'wrong'
        with self.assertRaisesRegex(RuntimeError,'download hash mismatch'):self.m.main()
        self.preserved(schema_same=True,files_old=True)
        self.assertNotIn(('systemctl','stop',self.m.SERVICE),self.calls)

    def test_live_mismatch_not_overwritten(self):
        p=self.m.live('az-baze-auth/app.py');p.write_bytes(b'newer source')
        with self.assertRaisesRegex(RuntimeError,'LIVE_BASE_MISMATCH'):self.m.main()
        self.assertEqual(p.read_bytes(),b'newer source');self.preserved(schema_same=True);self.assertFalse(self.calls)

    def test_dependency_mismatch_not_overwritten(self):
        p=self.m.live('az-baze-auth/personal_credentials.py');p.write_bytes(b'newer private source')
        with self.assertRaisesRegex(RuntimeError,'LIVE_BASE_MISMATCH'):self.m.main()
        self.assertEqual(p.read_bytes(),b'newer private source');self.preserved(schema_same=True,files_old=True)

    def test_existing_group_schema_not_overwritten(self):
        with sqlite3.connect(self.m.DB) as c:
            c.execute('CREATE TABLE work_groups(id INTEGER PRIMARY KEY,name TEXT)');c.execute("INSERT INTO work_groups VALUES(1,'Existing')")
        before=self.m.DB.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'already exists'):self.m.main()
        self.assertEqual(self.m.DB.read_bytes(),before);self.preserved(files_old=True)
        self.assertNotIn(('systemctl','stop',self.m.SERVICE),self.calls)

    def test_schema_source_and_defaults_are_pinned(self):
        key='az-baze-auth/access_groups.py';m=self.m
        self.assertEqual(len(m.schema_statements(self.raw)),4)
        for old,new in [(b'CREATE TABLE IF NOT EXISTS work_groups',b'CREATE TABLE IF NOT EXISTS other_groups'),("'Программы и IT'".encode(),b"'IT'"),(b'version INTEGER NOT NULL DEFAULT 1',b'version INTEGER DEFAULT 2')]:
            bad=dict(self.raw);bad[key]=bad[key].replace(old,new,1)
            with self.assertRaisesRegex(RuntimeError,'schema source mismatch'):m.schema_statements(bad)
        self.preserved(schema_same=True,files_old=True)

    def test_mutation_of_existing_records_rolls_back(self):
        m=self.m;original=m.schema_objects;calls=0
        def changed(c):
            nonlocal calls
            calls+=1
            if calls==3:c.execute('UPDATE reports SET value=99 WHERE id=2')
            return original(c)
        m.schema_objects=changed
        with self.assertRaisesRegex(RuntimeError,'unexpected DML'):m.initialize_groups(m.schema_statements(self.raw))
        self.preserved(schema_same=True,files_old=True)

    def test_extra_schema_change_rolls_back(self):
        m=self.m;original=m.schema_objects;calls=0
        def changed(c):
            nonlocal calls
            calls+=1
            if calls==3:c.execute('ALTER TABLE reports ADD COLUMN stray TEXT')
            return original(c)
        m.schema_objects=changed
        with self.assertRaisesRegex(RuntimeError,'unexpected schema change'):m.initialize_groups(m.schema_statements(self.raw))
        self.preserved(schema_same=True,files_old=True)

    def test_old_sequence_change_rolls_back(self):
        m=self.m;original=m.schema_objects;calls=0
        def changed(c):
            nonlocal calls
            calls+=1
            if calls==3:c.execute("UPDATE sqlite_sequence SET seq=999 WHERE name='work_contacts'")
            return original(c)
        m.schema_objects=changed
        with self.assertRaisesRegex(RuntimeError,'unexpected DML|sequences changed'):m.initialize_groups(m.schema_statements(self.raw))
        self.preserved(schema_same=True,files_old=True)

    def test_mid_install_failure_restores_old_files_removes_new(self):
        m=self.m;original=m.install;calls=0
        def fail_once(*args):
            nonlocal calls
            calls+=1;original(*args)
            if calls==4:raise RuntimeError('synthetic write failure')
        m.install=fail_once
        with self.assertRaisesRegex(RuntimeError,'synthetic write failure'):m.main()
        self.preserved(schema_same=True,files_old=True)
        self.assertNotIn('PRODUCTION_DEPLOY=PASS',self.output.getvalue())

    def test_health_failure_restores_files_keeps_committed_metadata(self):
        m=self.m;calls=0
        def fail_once():
            nonlocal calls
            calls+=1
            if calls==1:raise RuntimeError('synthetic health failure')
            m.health()
        m.wait_health=fail_once
        with self.assertRaisesRegex(RuntimeError,'synthetic health failure'):m.main()
        self.preserved(files_old=True)
        self.assertIn('DATABASE_RESTORE=NOT_RUN',self.output.getvalue())
        self.assertNotIn('PRODUCTION_DEPLOY=PASS',self.output.getvalue())
        with sqlite3.connect(m.DB) as c:self.assertEqual(c.execute('SELECT count(*) FROM work_groups').fetchone()[0],8)

    def test_repeat_does_not_change_records_or_duplicate_groups(self):
        m=self.m;m.main()
        with sqlite3.connect(m.DB) as c:before='\n'.join(c.iterdump())
        with self.assertRaisesRegex(RuntimeError,'LIVE_BASE_MISMATCH|already exists'):m.main()
        with sqlite3.connect(m.DB) as c:self.assertEqual('\n'.join(c.iterdump()),before)
        self.preserved()

if __name__=='__main__':unittest.main()
