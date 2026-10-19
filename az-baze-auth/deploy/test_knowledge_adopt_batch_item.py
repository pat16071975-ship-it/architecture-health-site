import contextlib
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import knowledge_adopt_batch_item as k

class AdoptTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        self.src = self.root/'source'; self.src.mkdir()
        self.backups = self.root/'backups'; self.backups.mkdir()
        self.media = self.src/'Course'/'part.MP4'; self.media.parent.mkdir()
        self.media.write_bytes(b'new-verified-pilot' * 50)
        import os
        os.utime(self.media, (315511200, 315511200))
        self.rel = 'Course/part.MP4'
        self.id = hashlib.sha256(self.rel.encode()).hexdigest()[:32]
        snap = k.snapshot(self.media)
        self.item = dict(id=self.id,relative_path=self.rel,path=self.media,snapshot=snap,
                         size=snap[2],mtime=str(snap[3]//10**9),sha256=k.sha256(self.media),pix_fmt='yuv420p')
        self.db = self.root/'auth.db'
        with contextlib.closing(sqlite3.connect(self.db, isolation_level=None)) as c:
            c.executescript('''
              CREATE TABLE knowledge_entries (
                id TEXT PRIMARY KEY, relative_path TEXT NOT NULL UNIQUE,parent_path TEXT NOT NULL,
                name TEXT NOT NULL,entry_type TEXT NOT NULL,extension TEXT NOT NULL,
                media_kind TEXT NOT NULL,size_bytes INTEGER NOT NULL,modified_at TEXT,
                hidden INTEGER NOT NULL,indexed_at TEXT NOT NULL);
              CREATE TABLE knowledge_video_compat (
                entry_id TEXT PRIMARY KEY,relative_path TEXT NOT NULL,source_size INTEGER NOT NULL,
                source_mtime TEXT,status TEXT NOT NULL,requested_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                last_accessed_at TEXT,codec_name TEXT,pix_fmt TEXT,output_size INTEGER,error TEXT);
              CREATE TABLE knowledge_policies (relative_path TEXT PRIMARY KEY,allow_download INTEGER);
              CREATE TABLE financial_fixture (id INTEGER,value INTEGER);
            ''')
            for identity, rel in [(self.id,self.rel),('neighbor','Other/part.MP4')]:
                c.execute('INSERT INTO knowledge_entries VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (identity,rel,str(Path(rel).parent),Path(rel).name,'file','.mp4','video',744324097,'1791886731',0,'old'))
                c.execute('INSERT INTO knowledge_video_compat VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                          (identity,rel,744324097,'1791886731','ready','old','old','access','hevc','yuv420p10le',61901188,None))
                c.execute('INSERT INTO knowledge_policies VALUES(?,?)',(rel,0))
            c.execute('INSERT INTO financial_fixture VALUES(1,123456)')
        self.manifest = self.root/'MANIFEST.json'
        self.record = dict(input='pilot.mp4',original=self.rel,archive_name='video/'+self.rel,
                           bytes=snap[2],duration_sec=2.0,sha256=self.item['sha256'])
        self.save_manifest()

    def save_manifest(self):
        self.manifest.write_text(json.dumps(dict(batch='AZ-VIDEO-BATCH-001',status='PILOT_EXPORT_ONLY',files=[self.record])))

    def dump(self):
        with contextlib.closing(sqlite3.connect(self.db, isolation_level=None)) as c:
            return list(c.iterdump())

    def run_sync(self):
        return k.sync_one(self.db,self.item,self.backups,744324097,'1791886731',61901188)

    def verify(self):
        with patch.object(k,'read_media',return_value=dict(video={'pix_fmt':'yuv420p'},duration_sec=2.0)):
            return k.verify_item(self.src,self.manifest,'AZ-VIDEO-BATCH-001','pilot.mp4',self.item['size'])

    def test_exact_two_rows_identity_and_neighbors_preserved(self):
        with contextlib.closing(sqlite3.connect(self.db, isolation_level=None)) as c:
            c.row_factory=sqlite3.Row
            old=k.read_rows(c,self.id); neighbor=k.read_rows(c,'neighbor')
        result=self.run_sync()
        self.assertEqual(result['changes'],2)
        with contextlib.closing(sqlite3.connect(self.db, isolation_level=None)) as c:
            c.row_factory=sqlite3.Row
            entry,state=k.read_rows(c,self.id)
            self.assertEqual(k.read_rows(c,'neighbor'),neighbor)
            self.assertEqual(c.execute('SELECT value FROM financial_fixture').fetchone()[0],123456)
            self.assertEqual([r[0] for r in c.execute('SELECT allow_download FROM knowledge_policies')],[0,0])
        for col in set(old[0]) - {'size_bytes','modified_at','indexed_at'}:
            self.assertEqual(entry[col],old[0][col])
        for col in set(old[1]) - {'source_size','source_mtime','status','codec_name','pix_fmt','output_size','error','updated_at'}:
            self.assertEqual(state[col],old[1][col])
        self.assertEqual(state['status'],'source_ok')
        self.assertEqual((entry['size_bytes'],entry['modified_at']),(state['source_size'],state['source_mtime']))
        backup=json.loads(Path(result['backup']).read_text())
        self.assertEqual((backup['entry'],backup['state']),old)
        self.assertEqual(Path(result['backup']).stat().st_mode & 0o777,0o600)

    def test_idempotent_no_new_backup_or_writes(self):
        self.run_sync(); before=self.dump()
        res=self.run_sync()
        self.assertTrue(res['reused']); self.assertEqual(res['changes'],0)
        self.assertEqual(before,self.dump()); self.assertEqual(len(list(self.backups.iterdir())),1)

    def test_busy_status_refused_without_writes(self):
        for status in ('pending','probing','converting','error','evicted'):
            with contextlib.closing(sqlite3.connect(self.db, isolation_level=None)) as c:
                c.execute('UPDATE knowledge_video_compat SET status=? WHERE entry_id=?',(status,self.id))
            before=self.dump()
            with self.assertRaises(k.Stop): self.run_sync()
            self.assertEqual(before,self.dump())

    def test_changed_old_signature_refused(self):
        with contextlib.closing(sqlite3.connect(self.db, isolation_level=None)) as c: c.execute('UPDATE knowledge_entries SET size_bytes=999 WHERE id=?',(self.id,))
        before=self.dump()
        with self.assertRaises(k.Stop): self.run_sync()
        self.assertEqual(before,self.dump())

    def test_source_changed_before_transaction_refused(self):
        self.media.write_bytes(b'changed')
        before=self.dump()
        with self.assertRaises(k.Stop): self.run_sync()
        self.assertEqual(before,self.dump())

    def test_backup_failure_rolls_back(self):
        before=self.dump()
        with patch.object(k,'write_json',side_effect=OSError('Disk full')):
            with self.assertRaises(OSError): self.run_sync()
        self.assertEqual(before,self.dump())

    def test_after_updates_verification_failure_rolls_back_both(self):
        before=self.dump(); real=k.read_rows; count=0
        def invalid_after(c,identity):
            nonlocal count
            count+=1
            return real(c,identity) if count==1 else ({},{})
        with patch.object(k,'read_rows',side_effect=invalid_after):
            with self.assertRaises(k.Stop): self.run_sync()
        self.assertEqual(before,self.dump())

    def test_trigger_blocks_any_writes(self):
        with contextlib.closing(sqlite3.connect(self.db, isolation_level=None)) as c:
            c.execute('CREATE TRIGGER bad AFTER UPDATE ON knowledge_entries BEGIN UPDATE financial_fixture SET value=0; END')
        before=self.dump()
        with self.assertRaises(k.Stop): self.run_sync()
        self.assertEqual(before,self.dump())

    def test_no_conversion_or_cache_deletion(self):
        cache=self.root/'old-cache.mp4'; cache.write_bytes(b'old-cache-keep')
        with patch.object(k.subprocess,'run',side_effect=AssertionError('No conversion in sync')):
            self.run_sync()
        self.assertEqual(cache.read_bytes(),b'old-cache-keep')
        self.assertEqual(k.sha256(self.media),self.item['sha256'])

    def test_historical_zip_mtime_kept(self):
        item=self.verify(); self.assertEqual(item['mtime'],'315511200')
        self.run_sync(); self.assertEqual(k.snapshot(self.media),self.item['snapshot'])

    def test_manifest_path_traversal_refused(self):
        for rel in ('../secret.mp4','/etc/test.mp4','Course/../test.MP4'):
            self.record['original']=rel; self.save_manifest()
            with self.assertRaises(k.Stop): self.verify()

    def test_wrong_hash_refused(self):
        self.record['sha256']='0'*64; self.save_manifest()
        with self.assertRaises(k.Stop): self.verify()

    def test_wrong_duration_refused(self):
        self.record['duration_sec']=9.0; self.save_manifest()
        with self.assertRaises(k.Stop): self.verify()

    def test_renamed_mov_not_silently_migrated(self):
        self.record['original']='Course/part.MOV'; self.save_manifest()
        with self.assertRaises(k.Stop): self.verify()

    def test_wrong_identity_refused(self):
        with contextlib.closing(sqlite3.connect(self.db, isolation_level=None)) as c: c.execute("UPDATE knowledge_entries SET hidden=1 WHERE id=?",(self.id,))
        before=self.dump()
        with self.assertRaises(k.Stop): self.run_sync()
        self.assertEqual(before,self.dump())

    def test_low_backup_space_refused(self):
        from collections import namedtuple
        before=self.dump()
        with patch.object(k.shutil,'disk_usage',return_value=namedtuple('Usage','total used free')(1,1,0)):
            with self.assertRaises(k.Stop): self.run_sync()
        self.assertEqual(before,self.dump())

class RealProbeTest(unittest.TestCase):
    def test_real_h264_aac_file_verified_against_batch(self):
        import subprocess
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); source=root/'source'; source.mkdir()
            video=source/'part.MP4'
            subprocess.run(['ffmpeg','-v','error','-nostdin','-f','lavfi','-i',
                            'testsrc2=size=64x48:rate=25','-f','lavfi','-i','sine=sample_rate=48000',
                            '-t','0.4','-c:v','libx264','-threads:v','1','-c:a','aac',
                            '-movflags','+faststart',str(video)],check=True,timeout=30)
            meta=k.read_media(video)
            manifest=root/'manifest.json'
            manifest.write_text(json.dumps(dict(batch='AZ-VIDEO-BATCH-001',status='PILOT_EXPORT_ONLY',
              files=[dict(input='pilot.mp4',original='part.MP4',archive_name='video/part.MP4',
                          bytes=video.stat().st_size,duration_sec=meta['duration_sec'],sha256=k.sha256(video))])))
            before=k.snapshot(video)
            result=k.verify_item(source,manifest,'AZ-VIDEO-BATCH-001','pilot.mp4',video.stat().st_size)
            self.assertEqual(result['sha256'],k.sha256(video))
            self.assertEqual(before,k.snapshot(video))
            self.assertEqual(result['pix_fmt'],'yuv420p')

if __name__ == '__main__': unittest.main(verbosity=2)
