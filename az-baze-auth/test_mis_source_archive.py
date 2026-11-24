import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import mis_source_archive as a
import mis_archive_bundle as b


def wb(value):
    # Synthetic, no patient information, a real tiny XLSX.
    from openpyxl import Workbook
    w = Workbook()
    w.active.append(["Group","Payment"])
    w.active.append(["Lab",value])
    f = io.BytesIO()
    w.save(f)
    w.close()
    return f.getvalue()


def make_pack(path, raw, *, change=None):
    sha = hashlib.sha256(raw).hexdigest()
    member = "originals/"+sha+".xlsx"
    manifest = {"version":1,"source_count":2,"unique_source_count":1,
                "duplicate_occurrences":1,"raw_unique_bytes":len(raw),
                "files":[{"sha256":sha,"path":member,"size_bytes":len(raw),
                          "type":"paid_and_debt","observed_from":"2026-01-05",
                          "observed_to":"2026-01-31","selection":"monthly_candidate",
                          "original_names":[{"archive":"a.zip","filename":"a.xlsx"},
                                            {"archive":"b.zip","filename":"b.xlsx"}]}]}
    if change:
        change(manifest)
    with ZipFile(path,"w") as z:
        z.writestr(member,raw)
        z.writestr("manifest.json",json.dumps(manifest).encode())
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SourceArchiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root/"auth.db"
        self.conn = sqlite3.connect(self.path)
        self.addCleanup(self.conn.close)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("CREATE TABLE report_data(date TEXT,payload TEXT);"
                                "INSERT INTO report_data VALUES('2026-01-31','approved');")
        self.raw = wb(100)
        self.sha = hashlib.sha256(self.raw).hexdigest()

    def snapshot(self):
        return list(self.conn.iterdump())

    def save(self, raw=None, kind="paid_services", filename="same.xlsx", **kw):
        return a.record(self.conn,kind,raw or self.raw,filename,
                        source_from="2026-01-01",source_to="2026-01-31",**kw)

    def test_exact_bytes_dedup_alias_and_versions(self):
        self.conn.execute("BEGIN")
        self.save(outcome="applied",saved_at="2026-02-01T00:00:00Z")
        self.save(outcome="duplicate",filename="renamed.xlsx")
        self.save(outcome="duplicate",filename="renamed.xlsx")
        changed = wb(110)
        self.save(raw=changed,outcome="applied",decision="use_new")
        self.conn.commit()
        self.assertEqual(self.conn.execute("SELECT count(*) FROM mis_source_objects").fetchone()[0],2)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM mis_source_events").fetchone()[0],3)
        self.assertEqual(a.read_original(self.conn,self.sha),self.raw)
        self.assertEqual(a.read_original(self.conn,hashlib.sha256(changed).hexdigest()),changed)
        self.assertEqual(self.conn.execute("SELECT payload FROM report_data").fetchone()[0],"approved")

    def test_archive_and_business_rollback_on_first_import(self):
        before = self.snapshot()
        self.conn.execute("BEGIN")
        self.conn.execute("UPDATE report_data SET payload='new'")
        self.save()
        self.conn.rollback()
        self.assertEqual(before,self.snapshot())

    def test_archive_error_rolls_back_business_update(self):
        before = self.snapshot()
        self.conn.execute("BEGIN")
        try:
            self.conn.execute("UPDATE report_data SET payload='new'")
            with self.assertRaisesRegex(ValueError,"SHA256 mismatch"):
                self.save(expected_sha="0"*64)
        finally:
            self.conn.rollback()
        self.assertEqual(before,self.snapshot())

    def test_transaction_boundary_no_implicit_commit(self):
        with self.assertRaises(RuntimeError):
            self.save()
        self.conn.execute("BEGIN")
        self.conn.execute("UPDATE report_data SET payload='new'")
        with self.assertRaises(RuntimeError):
            a.record_noop(self.conn,[])
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT payload FROM report_data").fetchone()[0],"approved")

    def test_noop_two_files_is_atomic(self):
        before = self.snapshot()
        specs=[a.specs("services",self.raw,"a.xlsx",["2026-01-01"]),
               a.specs("completed",b"","b.xlsx",["2026-01-01"])]
        with self.assertRaises(ValueError):
            a.record_noop(self.conn,specs)
        self.assertEqual(before,self.snapshot())

    def test_missing_dates_are_not_invented(self):
        s=a.specs("services",self.raw,"a.xlsx",["2026-01-05","2026-01-31"],
                  applied=["2026-01-05"])
        self.conn.execute("BEGIN")
        a.record(self.conn,*s[0],**s[1])
        self.conn.commit()
        row=self.conn.execute("SELECT source_from,source_to,observed_dates_json,applied_dates_json FROM mis_source_events").fetchone()
        self.assertEqual(row[:2],("2026-01-05","2026-01-31"))
        self.assertEqual(json.loads(row[2]),["2026-01-05","2026-01-31"])
        self.assertEqual(json.loads(row[3]),["2026-01-05"])

    def test_backup_round_trip_and_read_only_restore(self):
        self.conn.execute("BEGIN")
        self.save()
        self.conn.commit()
        dst=sqlite3.connect(self.root/"backup.db")
        self.conn.backup(dst)
        dst.close()
        ro=sqlite3.connect((self.root/"backup.db").as_uri()+"?mode=ro",uri=True)
        try:
            self.assertEqual(a.read_original(ro,self.sha),self.raw)
            self.assertEqual(ro.total_changes,0)
        finally:
            ro.close()

    def test_corruption_detected_on_read_and_repeat(self):
        self.conn.execute("BEGIN")
        self.save()
        self.conn.commit()
        self.conn.execute("UPDATE mis_source_objects SET raw=? WHERE sha256=?",(b"!"*len(self.raw),self.sha))
        self.conn.commit()
        with self.assertRaises(ValueError):
            a.read_original(self.conn,self.sha)
        self.conn.execute("BEGIN")
        with self.assertRaises(ValueError):
            self.save()
        self.conn.rollback()

    def test_historical_bundle_is_archive_only_and_idempotent(self):
        path=self.root/"bundle.zip"
        sha=make_pack(path,self.raw)
        pack=b.read_bundle(path,sha)
        b.archive_bundle(self.conn,pack)
        b.archive_bundle(self.conn,pack)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM mis_source_objects").fetchone()[0],2)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM mis_source_events WHERE outcome='archive_only'").fetchone()[0],3)
        self.assertEqual(self.conn.execute("SELECT payload FROM report_data").fetchone()[0],"approved")
        self.assertEqual(a.read_original(self.conn,self.sha),self.raw)

    def test_bad_bundle_hash_catalog_and_extra_member(self):
        path=self.root/"bundle.zip"
        sha=make_pack(path,self.raw)
        with self.assertRaises(ValueError):
            b.read_bundle(path,"0"*64)
        sha=make_pack(path,self.raw,change=lambda m:m.update(source_count=3))
        with self.assertRaises(ValueError):
            b.read_bundle(path,sha)
        make_pack(path,self.raw)
        with ZipFile(path,"a") as z:
            z.writestr("../bad",b"x")
        with self.assertRaises(ValueError):
            b.read_bundle(path,hashlib.sha256(path.read_bytes()).hexdigest())

    def test_historical_batch_failure_rolls_back_all(self):
        path=self.root/"bundle.zip"
        pack=b.read_bundle(path,make_pack(path,self.raw))
        before=self.snapshot()
        original=a.record
        def fail_manifest(conn,kind,*args,**kw):
            if kind=="catalog":
                raise sqlite3.IntegrityError("synthetic failure")
            return original(conn,kind,*args,**kw)
        with patch.object(a,"record",side_effect=fail_manifest):
            with self.assertRaises(sqlite3.IntegrityError):
                b.archive_bundle(self.conn,pack)
        self.assertEqual(before,self.snapshot())


if __name__ == "__main__":
    unittest.main()
