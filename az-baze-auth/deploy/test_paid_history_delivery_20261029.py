"""Isolated delivery regressions. Actual SQLite/files/AST, mocked host/network/systemd."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('delivery',HERE/'paid-history-production-20261029.py')
d=importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
# CI points these at exact Git trees. Local source comes from verified CI artifact.
SOURCE=Path(os.environ.get('PR164_SOURCE',str(HERE.parent/'source')))
BASE=Path(os.environ.get('PR164_BASE',str(HERE.parent/'base')))

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.app=root/'app';self.app.mkdir()
        self.reports=root/'reports';self.reports.mkdir()
        self.db=root/'auth.db'
        self.backups=root/'backups';self.backups.mkdir()
        self.stage=root/'stage';self.stage.mkdir()
        self.events=[]
        for key,value in {'APP':self.app,'REPORTS':self.reports,'DB':self.db,'BACKUPS':self.backups,'LOCK':root/'lock'}.items():
            p=patch.object(d,key,value);p.start();self.addCleanup(p.stop)
        for relative,old,new in d.FILES:
            target=self.stage/relative;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((SOURCE/relative).read_bytes())
            self.assertEqual(d.blob(target.read_bytes()),new)
            if old:
                live=d.live_path(relative);live.write_bytes((BASE/relative).read_bytes());live.chmod(0o640)
                self.assertEqual(d.blob(live.read_bytes()),old)
        for name in ('ident_import.py','paid_services.py'):
            shutil.copyfile(SOURCE/'az-baze-auth'/name,self.app/name)
        self.helper=d.load_helper(self.stage)
        self.selections=[]
        c=sqlite3.connect(self.db)
        c.executescript('''
         CREATE TABLE service_payment_snapshots(month TEXT,as_of_date TEXT,payload_json TEXT,source_sha256 TEXT,imported_at TEXT);
         CREATE TABLE service_payment_periods(period_start TEXT,period_end TEXT,payload_json TEXT,source_sha256 TEXT,imported_at TEXT);
         CREATE TABLE provider_registry(source_name TEXT,display_name TEXT,direction TEXT);
         CREATE TABLE report_data(date TEXT,payload TEXT);
         INSERT INTO report_data VALUES('2026-01-31','{"cashTotal":999,"primary":7}');
         CREATE TABLE later_events(id INTEGER PRIMARY KEY,note TEXT);
        ''')
        for i in range(1,11):
            month=f'2026-{i:02d}'
            first,last=self.helper.month_bounds(month)
            if i==10:last='2026-10-27'
            kind,start,end=d.PERIODS.get(month,('snapshot',first,last))
            stamp='2026-10-28T12:00:00Z'
            payload={'version':3 if kind=='period' else 2,
                'providers':{'Чирков М. С.':{'paid':100,'billed':100},'Старостенко В. А.':{'paid':25,'billed':25}},
                'paid_patient_evidence_complete':True,
                'paid_patients':{'Чирков М. С.':['synthetic-shared'],'Старостенко В. А.':['synthetic-shared','synthetic-other']},
                'totals':{'paid':125},'provider_residual':{'paid':0}}
            if kind=='period':payload.update(period_start=start,period_end=end)
            else:payload.update(month=month,as_of_date=end)
            table='service_payment_periods' if kind=='period' else 'service_payment_snapshots'
            c.execute('INSERT INTO '+table+' VALUES(?,?,?,?,?)',(start if kind=='period' else month,end,json.dumps(payload),hashlib.sha256(month.encode()).hexdigest(),stamp))
            if month in d.PERIODS:
                self.selections.append(dict(kind=kind,sourceFrom=start,sourceTo=end,importedAt=stamp,reportMonth=month))
        c.commit();c.close()
        self.rawdb=self.db.read_bytes()
        for name,new in [('health',lambda:True),('stage_targets',lambda stage:None),
                         ('service',lambda action:self.events.append(action)),('host_guard',lambda:None),('verify_config_readable',lambda config:None)]:
            p=patch.object(d,name,new);p.start();self.addCleanup(p.stop)
        self.output=io.StringIO();p=contextlib.redirect_stdout(self.output);p.__enter__();self.addCleanup(p.__exit__,None,None,None)

    def prepare(self):
        return d.preflight(self.stage,self.selections)

    def install(self):
        state,protected,config=self.prepare()
        self.assertEqual(state,'BASE')
        d.apply(self.stage,self.selections,protected,config)
        return next(self.backups.iterdir()),config

    def assert_base(self):
        self.assertEqual(d.live_state(),'BASE')
        self.assertFalse(d.config_path().exists())

    def test_preflight_has_no_live_or_service_writes(self):
        state,protected,config=self.prepare()
        self.assertEqual(state,'BASE');self.assertEqual(self.db.read_bytes(),self.rawdb)
        self.assertFalse(d.config_path().exists());self.assertEqual(self.events,[])
        self.assertEqual(list(self.backups.iterdir()),[])
        self.assertEqual(len(json.loads(config)['confirmations']),4)
        self.assertNotIn('synthetic-shared',self.output.getvalue())
        self.assertNotIn('Чирков',self.output.getvalue())

    def test_actual_read_projection_four_months_and_october(self):
        _,_,config=self.prepare()
        with d.read_db(self.db) as c:
            report=d.reporter(self.stage,config,self.helper)
            months=report['_management_paid_patients_monthly'](c)
            self.assertEqual(len(months),10)
            self.assertEqual(months['2026-01']['medicine'],2)
            self.assertEqual(months['2026-05']['asOf'],'2026-05-31')
            self.assertEqual(months['2026-10']['asOf'],'2026-10-27')
            self.assertIsNone(report['_paid_patient_counts'](c,'2026-01-01','2026-01-27'))
            with self.assertRaises(sqlite3.OperationalError): c.execute('DELETE FROM report_data')

    def test_success_preserves_database_and_file_metadata(self):
        backup,config=self.install()
        self.assertEqual(d.live_state(),'TARGET');self.assertEqual(self.db.read_bytes(),self.rawdb)
        self.assertEqual(d.config_path().read_bytes(),config)
        self.assertEqual(stat.S_IMODE(d.config_path().stat().st_mode),0o640)
        self.assertEqual(d.config_path().stat().st_uid,0)
        for r,old,_ in d.FILES:
            self.assertEqual(stat.S_IMODE(d.live_path(r).stat().st_mode),0o640)
        d.validate_backup(backup)
        self.assertEqual(self.events,['stop','start'])

    def test_identical_preflight_after_install_is_readonly(self):
        self.install();self.events.clear();self.prepare()
        self.assertEqual(self.events,[]);self.assertEqual(len(list(self.backups.iterdir())),1)

    def test_unknown_runtime_blocks_before_service_stop(self):
        (self.reports/'index.html').write_text('newer unrelated version')
        with self.assertRaisesRegex(d.Stop,'UNKNOWN_RUNTIME'):self.prepare()
        self.assertEqual(self.events,[])

    def test_mixed_runtime_blocks(self):
        shutil.copyfile(self.stage/'reports/index.html',self.reports/'index.html')
        with self.assertRaisesRegex(d.Stop,'MIXED_RUNTIME'):self.prepare()

    def test_symlink_target_blocks(self):
        original=self.reports/'index.html';other=self.reports/'other';original.rename(other);original.symlink_to(other)
        with self.assertRaisesRegex(d.Stop,'UNSAFE_PATH'):self.prepare()

    def test_existing_config_is_not_overwritten(self):
        d.config_path().write_text('unrelated')
        with self.assertRaisesRegex(d.Stop,'CONFIG_RUNTIME_MISMATCH'):self.prepare()
        self.assertEqual(d.config_path().read_text(),'unrelated')

    def test_stale_import_stamp_blocks(self):
        self.selections[0]['importedAt']='different'
        with self.assertRaisesRegex(d.Stop,'SOURCE_CHANGED'):self.prepare()
        self.assertEqual(self.events,[])

    def test_unknown_or_duplicate_month_blocks(self):
        self.selections[3]=self.selections[0].copy()
        with self.assertRaisesRegex(d.Stop,'UNAPPROVED_MONTH'):self.prepare()

    def test_missing_patient_evidence_blocks(self):
        c=sqlite3.connect(self.db);raw=c.execute('SELECT payload_json FROM service_payment_snapshots WHERE month="2026-05"').fetchone()[0]
        p=json.loads(raw);p['paid_patient_evidence_complete']=False
        c.execute('UPDATE service_payment_snapshots SET payload_json=? WHERE month="2026-05"',(json.dumps(p),));c.commit();c.close()
        with self.assertRaisesRegex(d.Stop,'MISSING_PATIENT_EVIDENCE'):self.prepare()

    def test_changed_payload_between_preflight_and_apply_blocks(self):
        _,protected,config=self.prepare()
        c=sqlite3.connect(self.db);c.execute("UPDATE service_payment_periods SET payload_json=payload_json||' '");c.commit();c.close()
        with self.assertRaisesRegex(d.Stop,'STALE_PREFLIGHT_CONFIG'):d.apply(self.stage,self.selections,protected,config)
        self.assert_base();self.assertEqual(list(self.backups.iterdir()),[])

    def test_partial_install_rolls_back_files_and_new_helper(self):
        _,protected,config=self.prepare();original=d.atomic_write;calls=[]
        def fail(raw,destination,meta,expected):
            calls.append(str(destination))
            if len(calls)==3:raise OSError('synthetic write failure')
            original(raw,destination,meta,expected)
        with patch.object(d,'atomic_write',fail):
            with self.assertRaises(OSError):d.apply(self.stage,self.selections,protected,config)
        self.assert_base();self.assertEqual(self.db.read_bytes(),self.rawdb)

    def test_health_failure_rolls_back_configuration_too(self):
        _,protected,config=self.prepare();starts=[]
        def service(action):
            self.events.append(action)
            if action=='start':
                starts.append(1)
                if len(starts)==1:raise d.Stop('synthetic health failure')
        with patch.object(d,'service',service):
            with self.assertRaisesRegex(d.Stop,'synthetic health'):d.apply(self.stage,self.selections,protected,config)
        self.assert_base();self.assertEqual(self.db.read_bytes(),self.rawdb)

    def test_backup_failure_never_installs(self):
        _,protected,config=self.prepare()
        with patch.object(d,'make_backup',side_effect=OSError('synthetic backup failure')):
            with self.assertRaises(OSError):d.apply(self.stage,self.selections,protected,config)
        self.assert_base();self.assertEqual(self.events,['stop','start'])

    def test_external_database_write_is_preserved_during_recovery(self):
        _,protected,config=self.prepare();written=[]
        def service(action):
            self.events.append(action)
            if action=='start' and not written:
                c=sqlite3.connect(self.db);c.execute("INSERT INTO later_events(note) VALUES('real new upload simulation')");c.commit();c.close();written.append(1)
        with patch.object(d,'service',service):
            with self.assertRaisesRegex(d.Stop,'DB_CHANGED_AFTER_START'):d.apply(self.stage,self.selections,protected,config)
        self.assert_base()
        with d.read_db(self.db) as c:self.assertEqual(c.execute('SELECT count(*) FROM later_events').fetchone()[0],1)

    def test_manual_rollback_does_not_restore_database(self):
        backup,_=self.install()
        c=sqlite3.connect(self.db);c.execute("INSERT INTO later_events(note) VALUES('later')");c.commit();c.close();before=self.db.read_bytes()
        d.rollback(backup);self.assert_base();self.assertEqual(self.db.read_bytes(),before)

    def test_rollback_refuses_newer_runtime(self):
        backup,_=self.install();self.events.clear();(self.reports/'index.html').write_text('a later release')
        with self.assertRaisesRegex(d.Stop,'ROLLBACK_NEWER_RUNTIME'):d.rollback(backup)
        self.assertEqual(self.events,[])

    def test_rollback_refuses_newer_configuration(self):
        backup,_=self.install();self.events.clear();d.config_path().write_text('{}')
        with self.assertRaisesRegex(d.Stop,'ROLLBACK_NEWER_CONFIG'):d.rollback(backup)
        self.assertEqual(self.events,[])

    def test_corrupted_backup_blocks_rollback(self):
        backup,_=self.install();(backup/'files/reports/index.html').write_text('corruption')
        with self.assertRaisesRegex(d.Stop,'CORRUPT_FILE_BACKUP'):d.rollback(backup)

    def test_wrong_apply_token_cannot_read_or_stop_server(self):
        with self.assertRaisesRegex(d.Stop,'WRONG_APPLY_AUTHORIZATION'):
            d.main(['--apply','incorrect','a'*64])
        self.assertEqual(self.events,[])

    def test_protected_runtime_drift_after_stop_blocks_install(self):
        _,protected,config=self.prepare()
        def service(action):
            self.events.append(action)
            if action=='stop':(self.app/'unrelated.py').write_text('changed = True')
        with patch.object(d,'service',service):
            with self.assertRaisesRegex(d.Stop,'DRIFT_AFTER_STOP'):d.apply(self.stage,self.selections,protected,config)
        self.assert_base();self.assertTrue((self.app/'unrelated.py').exists())

    def test_missing_backup_space_stops_before_any_service_action(self):
        from types import SimpleNamespace
        with patch.object(d.shutil,'disk_usage',return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(d.Stop,'INSUFFICIENT_BACKUP_SPACE'):self.prepare()
        self.assertEqual(self.events,[])

    def test_service_config_read_failure_restores_all_files(self):
        _,protected,config=self.prepare()
        with patch.object(d,'verify_config_readable',side_effect=d.Stop('SERVICE_CANNOT_READ_CONFIG')):
            with self.assertRaisesRegex(d.Stop,'SERVICE_CANNOT_READ_CONFIG'):d.apply(self.stage,self.selections,protected,config)
        self.assert_base();self.assertEqual(self.db.read_bytes(),self.rawdb)

    def test_stale_reviewed_hash_blocks_cli_apply(self):
        def stage_targets(stage):
            for relative,_,_ in d.FILES:
                dest=stage/relative;dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes((SOURCE/relative).read_bytes())
        with patch.object(d,'stage_targets',stage_targets),patch.object(d.sys,'stdin',io.StringIO(json.dumps(self.selections))):
            with self.assertRaisesRegex(d.Stop,'PREFLIGHT_CONFIG_HASH_CHANGED'):
                d.main(['--apply',d.TOKEN,'0'*64])
        self.assertEqual(self.events,[]);self.assert_base()

if __name__=='__main__':unittest.main(verbosity=2)
