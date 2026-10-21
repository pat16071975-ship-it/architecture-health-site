import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

import knowledge_link_ab_pilot as m


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'pilot.mp4'
        self.source.write_bytes(b'approved-synthetic-pilot' * 100)
        self.testdir = self.root / 'test'
        self.testdir.mkdir()
        self.target = self.testdir / ('a' * 32 + '.mp4')
        self.sha = m.checksum(self.source)
        self.size = self.source.stat().st_size

    def go(self, readable=lambda path: None, final_guard=lambda: None):
        return m.connect(self.source, self.target, self.sha, self.size, readable, final_guard)

    def test_new_link_same_bytes_inode_mode_owner(self):
        before = m.snapshot(self.source)
        self.assertFalse(self.go())
        self.assertEqual(m.snapshot(self.source), before)
        self.assertEqual(m.snapshot(self.target), before)
        self.assertTrue(os.path.samefile(self.source, self.target))
        self.assertEqual(set(self.testdir.iterdir()), {self.target})

    def test_idempotent_hardlink(self):
        self.go()
        nlink = self.source.stat().st_nlink
        self.assertTrue(self.go())
        self.assertEqual(self.source.stat().st_nlink, nlink)

    def test_identical_existing_file_reused_without_replacement(self):
        self.target.write_bytes(self.source.read_bytes())
        before = m.snapshot(self.target)
        self.assertTrue(self.go())
        self.assertEqual(m.snapshot(self.target), before)
        self.assertFalse(os.path.samefile(self.source, self.target))

    def test_conflicting_target_untouched(self):
        self.target.write_bytes(b'old test fragment')
        before = m.snapshot(self.target)
        with self.assertRaises(m.Stop): self.go()
        self.assertEqual(m.snapshot(self.target), before)
        self.assertEqual(self.target.read_bytes(), b'old test fragment')

    def test_symlink_target_not_replaced(self):
        self.target.symlink_to(self.source)
        with self.assertRaises(m.Stop): self.go()
        self.assertTrue(self.target.is_symlink())

    def test_symlink_source_not_published(self):
        actual = self.source.with_name('actual.mp4')
        self.source.rename(actual)
        self.source.symlink_to(actual)
        with self.assertRaises(m.Stop): self.go()
        self.assertFalse(self.target.exists())

    def test_missing_directory_not_created(self):
        self.testdir.rmdir()
        with self.assertRaises(m.Stop): self.go()
        self.assertFalse(self.testdir.exists())

    def test_bad_hash_stops_before_creation(self):
        self.sha = '0' * 64
        with self.assertRaises(m.Stop): self.go()
        self.assertFalse(self.target.exists())

    def test_wrong_size_stops(self):
        self.size += 1
        with self.assertRaises(m.Stop): self.go()
        self.assertFalse(self.target.exists())

    def test_permission_failure_removes_only_new_link(self):
        source_before = m.snapshot(self.source)
        other = self.testdir / 'unrelated.mp4'
        other.write_bytes(b'other')
        def reject(path): raise m.Stop('not readable')
        with self.assertRaises(m.Stop): self.go(readable=reject)
        self.assertFalse(self.target.exists())
        self.assertEqual(m.snapshot(self.source), source_before)
        self.assertEqual(other.read_bytes(), b'other')

    def test_guard_failure_rolls_back_new_link(self):
        def reject(): raise m.Stop('service changed')
        with self.assertRaises(m.Stop): self.go(final_guard=reject)
        self.assertFalse(self.target.exists())
        self.assertEqual(m.checksum(self.source), self.sha)

    def test_existing_target_survives_guard_failure(self):
        self.go()
        def reject(): raise m.Stop('service changed')
        with self.assertRaises(m.Stop): self.go(final_guard=reject)
        self.assertTrue(self.target.is_file())

    def test_parallel_directory_lock_refuses(self):
        fd = os.open(self.testdir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            m.fcntl.flock(fd, m.fcntl.LOCK_EX | m.fcntl.LOCK_NB)
            with self.assertRaises(m.Stop): self.go()
        finally: os.close(fd)
        self.assertFalse(self.target.exists())


class MetadataTests(unittest.TestCase):
    def record(self):
        relative = 'Course/day/example.MP4'
        eid = hashlib.sha256(relative.encode()).hexdigest()[:32]
        rec = {'input': 'example.mp4', 'original': relative, 'bytes': 25, 'sha256': 'a' * 64}
        return {'batch': 'AZ-VIDEO-BATCH-001', 'status': 'PILOT_EXPORT_ONLY', 'files': [rec]}, eid

    def test_exact_manifest_identity(self):
        manifest, eid = self.record()
        self.assertEqual(m.batch_record(manifest, 'example.mp4', eid, 25), manifest['files'][0])

    def test_manifest_id_mismatch(self):
        manifest, eid = self.record()
        with self.assertRaises(m.Stop): m.batch_record(manifest, 'example.mp4', '0' * 32, 25)

    def test_manifest_duplicate(self):
        manifest, eid = self.record()
        manifest['files'] *= 2
        with self.assertRaises(m.Stop): m.batch_record(manifest, 'example.mp4', eid, 25)

    def test_manifest_traversal(self):
        manifest, eid = self.record()
        manifest['files'][0]['original'] = '../example.MP4'
        with self.assertRaises(m.Stop): m.batch_record(manifest, 'example.mp4', eid, 25)

    def test_readonly_catalog_check(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'auth.db'
            c = sqlite3.connect(path)
            c.execute('CREATE TABLE knowledge_entries (id,relative_path,size_bytes,modified_at,hidden,entry_type,media_kind)')
            c.execute('CREATE TABLE knowledge_video_compat (entry_id,relative_path,source_size,source_mtime,status)')
            c.execute("INSERT INTO knowledge_entries VALUES ('a','f.MP4',25,'7',0,'file','video')")
            c.execute("INSERT INTO knowledge_video_compat VALUES ('a','f.MP4',25,'7','source_ok')")
            c.commit(); c.close()
            before = path.read_bytes()
            m.catalog_check(path, 'a', 'f.MP4', (1, 2, 25, 7000000000))
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaises(m.Stop): m.catalog_check(path, 'a', 'f.MP4', (1, 2, 26, 7000000000))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(name).iterdir()), [path])

if __name__ == '__main__':
    unittest.main(verbosity=2)
