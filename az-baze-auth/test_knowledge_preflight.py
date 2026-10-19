"""Tests for the standalone read-only preflight (synthetic input only)."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('preflight',Path(__file__).parent/'deploy/knowledge-permanent-preflight.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PreflightTests(unittest.TestCase):
    def test_mount_output_never_includes_source_or_credentials(self):
        text = '10 9 0:2 / /mnt/qnap\\040test ro,relatime - cifs //server/private rw,username=SECRET,password=SECRET'
        result = p.mounts(text)
        self.assertEqual(result,[{'target':'/mnt/qnap test','fstype':'cifs','read_only':True}])
        self.assertNotIn('SECRET',str(result))

    def test_audio_fix_not_misclassified_as_video_transcode(self):
        rows=[{'status':'NEEDS_PROXY','video_codec':'h264','pix_fmt':'yuvj420p','audio_codec':'pcm_s16le','source_size_bytes':'500'},
              {'status':'NEEDS_PROXY','video_codec':'hevc','pix_fmt':'yuv420p10le','audio_codec':'aac','source_size_bytes':'700'},
              {'status':'TIMEOUT','source_size_bytes':'900'}]
        groups=p.summarize(rows)
        self.assertEqual(groups['audio_fix_candidates']['source_bytes'],500)
        self.assertEqual(groups['transcode_candidates']['files'],1)
        self.assertEqual(groups['unresolved']['files'],1)

    def test_invalid_size_rejected(self):
        with self.assertRaises(ValueError):
            p.summarize([{'source_size_bytes':'-1'}])

    def test_main_not_run_at_import(self):
        self.assertEqual(p.SOURCE,Path('/mnt/qnap-obuchenie'))


if __name__=='__main__':
    unittest.main(verbosity=2)
