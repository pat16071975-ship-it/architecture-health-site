import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import knowledge_export_batch as b

class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.inp, self.out = root / 'in', root / 'out'
        self.inp.mkdir(); self.out.mkdir()
        (self.inp / 'pilot.mp4').write_bytes(b'not-video-for-packaging-unit-test' * 19)
        self.plan = [{'input': 'pilot.mp4', 'original': 'Курс/День 1/Лекция.MOV',
                      'bytes': (self.inp / 'pilot.mp4').stat().st_size, 'duration_sec': 3.5,
                      'note': 'Pilot, not publication approval'}]
        self.meta = {'duration_sec': 3.5, 'video': {'codec_name': 'h264'}, 'audio': {'codec_name': 'aac'}}
        self.mock = patch.object(b, 'probe', return_value=self.meta).start()
        self.addCleanup(patch.stopall)

    def run_build(self, plan=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return b.build(self.plan if plan is None else plan, self.inp, self.out,
                           'AZ-VIDEO-BATCH-001', reserve=0)

    def test_roundtrip_names_and_original_untouched(self):
        before = (self.inp / 'pilot.mp4').read_bytes()
        res = self.run_build()
        self.assertFalse(res['reused'])
        self.assertEqual(before, (self.inp / 'pilot.mp4').read_bytes())
        zpath = Path(res['path'])
        self.assertEqual(hashlib.sha256(zpath.read_bytes()).hexdigest(), res['sha256'])
        with zipfile.ZipFile(zpath) as z:
            self.assertEqual(z.read('video/Курс/День 1/Лекция.mp4'), before)
            m = json.loads(z.read('MANIFEST.json'))
            self.assertEqual(m['status'], 'PILOT_EXPORT_ONLY')
            self.assertFalse(m['original_backup'])
            self.assertFalse(m['catalog_published'])
        self.assertEqual(zpath.stat().st_mode & 0o777, 0o600)
        self.assertEqual(zpath.parent.stat().st_mode & 0o777, 0o700)

    def test_existing_batch_reused_not_overwritten(self):
        a = self.run_build()
        before = b.snapshot(Path(a['path']))
        c = self.run_build()
        self.assertTrue(c['reused'])
        self.assertEqual(before, b.snapshot(Path(a['path'])))
        self.assertEqual(a['sha256'], c['sha256'])

    def test_changed_existing_input_blocks_reuse(self):
        res = self.run_build()
        original = Path(res['path']).read_bytes()
        (self.inp / 'pilot.mp4').write_bytes(b'x' * self.plan[0]['bytes'])
        with self.assertRaises(b.Stop): self.run_build()
        self.assertEqual(original, Path(res['path']).read_bytes())

    def test_corrupt_existing_zip_blocks_reuse(self):
        res = self.run_build()
        Path(res['path']).write_bytes(b'broken')
        with self.assertRaises((b.Stop, zipfile.BadZipFile)): self.run_build()

    def test_different_plan_blocks_reuse(self):
        self.run_build()
        self.plan[0]['original'] = 'Другая папка/Лекция.MOV'
        with self.assertRaises(b.Stop): self.run_build()

    def test_size_mismatch(self):
        self.plan[0]['bytes'] += 1
        with self.assertRaises(b.Stop): self.run_build()
        self.assertFalse(list(self.out.glob('*.building-*')))

    def test_duration_mismatch(self):
        self.plan[0]['duration_sec'] = 9
        with self.assertRaises(b.Stop): self.run_build()

    def test_missing_input(self):
        self.plan[0]['input'] = 'missing.mp4'
        with self.assertRaises(FileNotFoundError): self.run_build()

    def test_symlink_input(self):
        (self.inp / 'link.mp4').symlink_to(self.inp / 'pilot.mp4')
        self.plan[0]['input'] = 'link.mp4'
        with self.assertRaises(b.Stop): self.run_build()

    def test_symlinked_final(self):
        (self.out / 'AZ-VIDEO-BATCH-001').symlink_to(self.inp)
        with self.assertRaises(b.Stop): self.run_build()

    def test_unsafe_paths(self):
        for path in ('../x.MOV', '/x.MOV', 'x//y.MOV', 'x/../y.MOV', 'x\\y.MOV', 'x:y.MOV', 'x\ny.MOV'):
            with self.subTest(path=path):
                self.plan[0]['original'] = path
                with self.assertRaises(b.Stop): self.run_build()

    def test_case_insensitive_collision(self):
        row = {**self.plan[0], 'input': 'other.mp4', 'original': 'Курс/День 1/лекция.MTS'}
        with self.assertRaises(b.Stop): self.run_build(self.plan + [row])

    def test_keep_mp4_extension_case_and_review_note(self):
        self.plan[0]['original'] = 'Курс/Лекция.MP4'
        res = self.run_build()
        with zipfile.ZipFile(res['path']) as z:
            self.assertIn('video/Курс/Лекция.MP4', z.namelist())
            self.assertEqual(json.loads(z.read('MANIFEST.json'))['files'][0]['note'], self.plan[0]['note'])

    def test_low_space_no_input_deletion_no_work(self):
        free = shutil._ntuple_diskusage(10000, 10000, 0)
        with patch.object(b.shutil, 'disk_usage', return_value=free):
            with self.assertRaises(b.Stop): self.run_build()
        self.assertTrue((self.inp / 'pilot.mp4').exists())
        self.assertFalse(list(self.out.glob('.*.building-*')))

    def test_aborted_work_is_preserved_no_new_work(self):
        work = self.out / '.AZ-VIDEO-BATCH-001.building-old'
        work.mkdir(); (work / 'partial').write_bytes(b'partial')
        with self.assertRaises(b.Stop): self.run_build()
        self.assertEqual((work / 'partial').read_bytes(), b'partial')
        self.assertEqual(len(list(self.out.glob('.*.building-*'))), 1)

    def test_batch_limit(self):
        with self.assertRaises(b.Stop):
            b.build(self.plan, self.inp, self.out, 'AZ-VIDEO-BATCH-001', reserve=0, limit=1)

    def test_empty_and_duplicate_input(self):
        for plan in ([], self.plan + self.plan):
            with self.assertRaises(b.Stop): self.run_build(plan)

    def test_no_ffmpeg_converter_called(self):
        with patch.object(b.subprocess, 'Popen', side_effect=AssertionError('No converter')):
            self.run_build()

    def test_lock_exclusion(self):
        with (self.out / '.export.lock').open('w') as f:
            b.fcntl.flock(f, b.fcntl.LOCK_EX | b.fcntl.LOCK_NB)
            with self.assertRaises(b.Stop): self.run_build()

class RealMediaTests(unittest.TestCase):
    def test_real_h264_aac_probe_package_extract(self):
        # Do not silently skip missing FFmpeg; a missing dependency is a failed test.
        self.assertIsNotNone(shutil.which('ffmpeg'))
        self.assertIsNotNone(shutil.which('ffprobe'))
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); inp = root / 'in'; out = root / 'out'
            inp.mkdir(); out.mkdir(); video = inp / 'sample.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
                            'testsrc2=size=64x48:rate=25', '-f', 'lavfi', '-i',
                            'sine=sample_rate=48000', '-t', '0.4', '-c:v', 'libx264',
                            '-threads:v', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                            '-movflags', '+faststart', str(video)], check=True, timeout=30)
            meta = b.probe(video)
            plan = [{'input': video.name, 'original': 'Курс/Короткое видео.MP4',
                     'bytes': video.stat().st_size, 'duration_sec': meta['duration_sec']}]
            with contextlib.redirect_stdout(io.StringIO()):
                result = b.build(plan, inp, out, 'AZ-VIDEO-BATCH-001', reserve=0)
            with zipfile.ZipFile(result['path']) as z:
                self.assertEqual(z.read('video/Курс/Короткое видео.MP4'), video.read_bytes())

if __name__ == '__main__': unittest.main(verbosity=2)
