import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
import report_consistency_preflight as m


class ReadOnlyPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_hash_exact_and_no_change(self):
        p = self.root / 'source.py'; p.write_bytes(b'example\n')
        before = p.stat()
        self.assertEqual(m.fingerprint(p), hashlib.sha1(b'blob 8\0example\n').hexdigest())
        self.assertEqual(before.st_mtime_ns, p.stat().st_mtime_ns)

    def test_absent_does_not_create(self):
        p = self.root / 'new.py'
        self.assertIsNone(m.fingerprint(p)); self.assertFalse(p.exists())

    def test_symlink_refused(self):
        p = self.root / 'target'; p.write_bytes(b'code')
        link = self.root / 'link'; link.symlink_to(p)
        with self.assertRaises(ValueError): m.fingerprint(link)

    def test_directory_refused(self):
        with self.assertRaises(ValueError): m.fingerprint(self.root)

    def test_version_states(self):
        self.assertEqual(m.version_state(None, None, 'new'), 'BASE')
        self.assertEqual(m.version_state('old', 'old', 'new'), 'BASE')
        self.assertEqual(m.version_state('new', 'old', 'new'), 'TARGET')
        self.assertEqual(m.version_state('unknown', 'old', 'new'), 'OTHER')

    def test_schema_does_not_read_records_or_write(self):
        p = self.root / 'snapshot.db'
        c = sqlite3.connect(p)
        for table, columns in m.COLUMNS.items():
            c.execute('CREATE TABLE ' + table + '(' + ','.join('"' + x + '" TEXT' for x in columns) + ')')
        c.execute('ALTER TABLE users ADD COLUMN password_hash TEXT')
        c.execute("INSERT INTO users(id,full_name,password_hash) VALUES('1','private','secret')")
        c.commit(); c.close()
        before = p.read_bytes()
        self.assertTrue(all(m.inspect_schema(p).values()))
        self.assertEqual(before, p.read_bytes())
        self.assertEqual(list(self.root.iterdir()), [p])

    def test_missing_tables_no_initialization(self):
        p = self.root / 'snapshot.db'; sqlite3.connect(p).close()
        before = p.read_bytes()
        self.assertEqual(m.inspect_schema(p), {key:False for key in m.COLUMNS})
        self.assertEqual(before, p.read_bytes())

    def test_missing_database_not_created(self):
        p = self.root / 'absent.db'
        with self.assertRaises(ValueError): m.inspect_schema(p)
        self.assertFalse(p.exists())


if __name__ == '__main__': unittest.main(verbosity=2)
