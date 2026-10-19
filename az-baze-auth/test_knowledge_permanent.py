"""Isolated tests. Synthetic media only, no QNAP and no production database."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import knowledge_permanent as k


def row(path, root):
    rel = path.relative_to(root).as_posix()
    return {"id": hashlib.sha256(rel.encode()).hexdigest()[:32], "relative_path": rel,
            "name": path.name, "size_bytes": path.stat().st_size,
            "modified_at": str(int(path.stat().st_mtime))}


class BasicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.source = self.base / "source"
        self.dest = self.base / "store"
        self.source.mkdir()
        self.dest.mkdir()
        self.store = k.Store(self.source, self.dest, reserve=0, enforce_external=False)
        self.store.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def material(self, name="Направление/Курс/a.MOV", data=b"original"):
        f = self.source / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        return row(f, self.source)

    def test_path_traversal(self):
        for bad in ["/etc/passwd", "../a", "a/../b", "a//b", "a\\b", ".", "", "a\x00b"]:
            with self.subTest(bad=bad), self.assertRaises(k.NotReady):
                k.relative(bad)

    def test_symlink_rejected(self):
        (self.source / "escape").symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(k.NotReady):
            k.safe_path(self.source, "escape/file")

    def test_overlapping_roots_rejected(self):
        for source, dest in [(self.source, self.source), (self.base, self.dest)]:
            with self.assertRaises(k.NotReady):
                k.Store(source, dest, enforce_external=False)

    def test_vps_system_volume_rejected(self):
        with self.assertRaises(k.NotReady):
            k.Store(self.source, self.dest, enforce_external=True)

    def test_nonempty_store_cannot_be_initialized(self):
        other = self.base / "other"
        other.mkdir()
        (other / "keep.txt").write_text("keep")
        with self.assertRaises(k.NotReady):
            k.Store(self.source, other, enforce_external=False).initialize()
        self.assertEqual((other / "keep.txt").read_text(), "keep")

    def test_same_archive_reused_without_overwrite(self):
        r = self.material()
        archives = self.store.archive([r])
        p = self.dest / archives[0]["path"]
        before = k.snapshot(p), k.digest(p)
        again = self.store.archive([r])
        self.assertEqual(archives, again)
        self.assertEqual(before, (k.snapshot(p), k.digest(p)))
        self.assertEqual((self.source / r["relative_path"]).read_bytes(), b"original")

    def test_independent_archive_parts_restore_exact_paths(self):
        rows = [self.material(f"Направление/Курс/{i}.MOV", bytes([i]) * 64) for i in range(3)]
        archives = self.store.archive(rows, target_bytes=65)
        self.assertEqual(len(archives), 3)
        for ar in archives:
            with k.zipfile.ZipFile(self.dest / ar["path"]) as z:
                self.assertEqual(z.namelist(), [ar["members"][0]["relative_path"]])
                name = z.namelist()[0]
                self.assertEqual(z.read(name), (self.source / name).read_bytes())

    def test_mixed_folders_rejected(self):
        a, b = self.material("A/a.MOV"), self.material("B/b.MOV")
        with self.assertRaises(k.NotReady):
            self.store.archive([a, b])

    def test_corrupted_archive_not_accepted(self):
        ar = self.store.archive([self.material()])[0]
        path = self.dest / ar["path"]
        with path.open("r+b") as f:
            f.seek(42)
            f.write(b"BROKEN")
        with self.assertRaises(k.NotReady):
            self.store.verify_archive(ar)

    def test_low_space_retains_original_and_existing_outputs(self):
        r = self.material()
        keep = self.dest / "keep.mp4"
        keep.write_bytes(b"old-ready")
        usage = shutil._ntuple_diskusage(100, 99, 1)
        with patch.object(k.shutil, "disk_usage", return_value=usage), self.assertRaises(k.NotReady):
            self.store.archive([r])
        self.assertEqual(keep.read_bytes(), b"old-ready")
        self.assertEqual((self.source / r["relative_path"]).read_bytes(), b"original")

    def test_marker_mismatch_fail_closed(self):
        marker = k.json_read(self.store.marker)
        marker["source"] = "/other"
        k.json_atomic(self.store.marker, marker)
        with self.assertRaises(k.NotReady):
            self.store.space()

    def test_single_preparer_lock(self):
        with self.store.exclusive():
            with self.assertRaises(k.NotReady):
                with self.store.exclusive():
                    pass

    def test_empty_and_incomplete_publication_rejected(self):
        for rows in [[], [self.material()]]:
            with self.assertRaises(k.NotReady):
                self.store.publish(rows)
        self.assertFalse((self.dest / "active.json").exists())

    def test_casefold_output_collision_rejected(self):
        a = self.material("A/file.MOV")
        b = self.material("A/file.mp4")
        with self.assertRaises(k.NotReady):
            k.validate_rows([a, b])

    def test_sqlite_read_only_catalog(self):
        db = self.base / "catalog.db"
        with contextlib.closing(sqlite3.connect(db)) as c:
            c.execute("CREATE TABLE knowledge_entries(id TEXT, relative_path TEXT, name TEXT, size_bytes INT, modified_at TEXT, hidden INT,entry_type TEXT,media_kind TEXT)")
            c.execute("INSERT INTO knowledge_entries VALUES('x','a.mp4','a.mp4',1,'0',0,'file','video')")
            c.commit()
        before = k.digest(db)
        self.assertEqual(len(k.catalog_rows(db)), 1)
        self.assertEqual(k.digest(db), before)

    def test_unknown_video_not_assumed_compatible(self):
        with self.assertRaises(k.NotReady):
            k.classify(Path("a.mpeg"), {"video": {"codec_name": "unknown"}, "audio": None})

    def test_command_does_not_pick_attached_picture(self):
        meta = {"video": {"index": 5, "codec_name": "hevc"}, "audio": {"index": 1, "codec_name": "aac"}}
        cmd = k.conversion_command(Path("a.mp4"), Path("new.mp4"), meta, "video_transcode")
        self.assertIn("0:5", cmd)
        self.assertNotIn("0:v:0", cmd)
        self.assertIn("passthrough", cmd)
        self.assertNotIn("-r", cmd)
        self.assertIn("-n", cmd)
        self.assertNotIn("-y", cmd)

    def test_faststart_box_reader(self):
        p = self.base / "boxes.mp4"
        def box(kind):
            return struct.pack(">I4s", 8, kind)
        p.write_bytes(box(b"ftyp") + box(b"moov") + box(b"mdat"))
        self.assertTrue(k.faststart(p))
        p.write_bytes(box(b"ftyp") + box(b"mdat") + box(b"moov"))
        self.assertFalse(k.faststart(p))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.source = cls.base / "source"
        cls.source.mkdir()
        specs = [
            ("direct.mp4", "libx264", "aac", "yuv420p"),
            ("remux.MOV", "libx264", "aac", "yuv420p"),
            ("pcm.MOV", "libx264", "pcm_s16le", "yuv420p"),
            ("ac3.MTS", "libx264", "ac3", "yuv420p"),
            ("hevc10.mp4", "libx265", "aac", "yuv420p10le"),
            ("hevc8.mp4", "libx265", "aac", "yuv420p"),
            ("mpeg4.avi", "mpeg4", "libmp3lame", "yuv420p"),
        ]
        for name, vcodec, acodec, pix in specs:
            path = cls.source / name
            cmd = ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x96:rate=24",
                   "-f", "lavfi", "-i", "sine=frequency=600:sample_rate=48000", "-t", "1.5",
                   "-c:v", vcodec, "-pix_fmt", pix, "-threads", "1", "-filter_threads", "1",
                   "-c:a", acodec, "-ac", "2"]
            if vcodec == "libx265":
                cmd += ["-preset", "ultrafast", "-x265-params", "pools=1:frame-threads=1:log-level=error",
                        "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709"]
            if name.endswith("MTS"):
                cmd += ["-f", "mpegts"]
            elif name.endswith("mp4"):
                cmd += ["-movflags", "+faststart"]
            subprocess.run(cmd + [str(path)], capture_output=True, check=True, timeout=30)
        cls.rows = [row(f, cls.source) for f in sorted(cls.source.iterdir())]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.dst_tmp = tempfile.TemporaryDirectory(dir=self.base)
        self.dest = Path(self.dst_tmp.name)
        self.store = k.Store(self.source, self.dest, reserve=0, enforce_external=False)
        self.store.initialize()

    def tearDown(self):
        self.dst_tmp.cleanup()

    def prepare(self, names=None):
        rows = [r for r in self.rows if names is None or r["name"] in names]
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.store.prepare(rows)
        self.assertEqual(result["errors"], [], result["errors"])
        return rows

    def test_real_all_supported_media_classes(self):
        rows = self.prepare()
        modes = {r["name"]: self.store.valid_receipt(r)["mode"] for r in rows}
        self.assertEqual(modes, {"direct.mp4":"direct", "remux.MOV":"remux", "pcm.MOV":"audio_fix",
                                "ac3.MTS":"audio_fix", "hevc10.mp4":"video_transcode",
                                "hevc8.mp4":"video_transcode", "mpeg4.avi":"video_transcode"})
        for r in rows:
            rec = self.store.valid_receipt(r)
            self.assertEqual(rec["source_frames"]["frames"], rec["output_frames"]["frames"])
            if rec["mode"] != "video_transcode":
                self.assertEqual(rec["source_frames"]["decoded_sha256"], rec["output_frames"]["decoded_sha256"])
        with self.assertRaises(k.NotReady):
            self.store.publish(rows)
        self.assertEqual(self.store.publish(rows, visual_approved=True)["published"], 7)
        reader = k.Reader(self.dest)
        with patch.object(k, "run_media", side_effect=AssertionError("Must not process at view time")):
            for r in rows:
                path, rec = reader.resolve(r)
                self.assertTrue(path.is_file())

    def test_resume_does_not_reconvert_or_overwrite(self):
        rows = self.prepare({"pcm.MOV"})
        ready = self.store.valid_receipt(rows[0])
        with patch.object(k, "run_media", side_effect=AssertionError("Reconversion forbidden")):
            meta = k.probe(self.source / "pcm.MOV")
            self.assertEqual(self.store.prepare_one(rows[0], meta, "audio_fix", []), ready)

    def test_full_pipeline_originals_untouched(self):
        original = {f.name: (k.snapshot(f), k.digest(f)) for f in self.source.iterdir()}
        rows = self.prepare({"remux.MOV", "pcm.MOV"})
        self.store.publish(rows)
        self.assertEqual(original, {f.name:(k.snapshot(f),k.digest(f)) for f in self.source.iterdir()})
        with patch.object(k.subprocess, "Popen", side_effect=AssertionError("No on-demand job")):
            for _ in range(3):
                k.Reader(self.dest).resolve(rows[0])

    def test_missing_output_no_fallback_no_conversion(self):
        rows = self.prepare({"remux.MOV"})
        self.store.publish(rows)
        path, rec = k.Reader(self.dest).resolve(rows[0])
        path.unlink()
        with patch.object(k.subprocess, "Popen", side_effect=AssertionError("No fallback")), self.assertRaises((k.NotReady, OSError)):
            k.Reader(self.dest).resolve(rows[0])

    def test_failed_ffmpeg_not_published(self):
        rows = [r for r in self.rows if r["name"] == "pcm.MOV"]
        def fail(cmd, *a, **kw):
            if "-c:v" in cmd and cmd[cmd.index("-c:v")+1] == "copy":
                raise k.NotReady("injected encoder failure")
            return original(cmd, *a, **kw)
        original = k.run_media
        with patch.object(k, "run_media", side_effect=fail), contextlib.redirect_stdout(io.StringIO()):
            result = self.store.prepare(rows)
        self.assertEqual(len(result["errors"]), 1)
        self.assertFalse(self.store.receipt_path(rows[0]).exists())
        self.assertEqual(list(self.dest.rglob("*.partial.mp4")), [])
        self.assertFalse((self.dest / "active.json").exists())
        self.assertTrue((self.source / "pcm.MOV").exists())

    def test_source_tamper_blocks_receipt_reuse(self):
        # Own copy to avoid changing class fixtures.
        source = self.base / ("isolated-" + next(tempfile._get_candidate_names()))
        source.mkdir()
        shutil.copy2(self.source / "direct.mp4", source / "direct.mp4")
        store = k.Store(source, self.dest, reserve=0, enforce_external=False)
        # Reset only this test's empty marker before initialization.
        store.marker.unlink()
        store.initialize()
        r = row(source / "direct.mp4", source)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(store.prepare([r])["errors"])
        f = source / "direct.mp4"
        s = f.stat()
        with f.open("r+b") as out:
            out.seek(50); out.write(b"changed")
        os.utime(f, ns=(s.st_atime_ns, s.st_mtime_ns))
        with self.assertRaises(k.NotReady):
            store.valid_receipt(r)
        shutil.rmtree(source)


try:
    import flask
except ImportError:
    flask = None


@unittest.skipIf(flask is None, "Flask not installed in local runner; CI must run these tests")
class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.source, self.dest = self.base / "source", self.base / "store"
        self.source.mkdir(); self.dest.mkdir()
        source = self.source / "a.mp4"
        # HTTP byte serving fixtures; media validity is covered by MediaTests.
        source.write_bytes(b"0123456789abcdef")
        self.row = row(source, self.source)
        self.store = k.Store(self.source, self.dest, reserve=0, enforce_external=False)
        self.store.initialize()
        rec = {"schema":1,"profile":k.PROFILE,"verified":True,"id":self.row["id"],
               "relative_path":"a.mp4","mode":"direct","source_snapshot":k.snapshot(source),
               "source_sha256":k.digest(source),"output_snapshot":k.snapshot(source),
               "output_sha256":k.digest(source),"visual_review_required":False,"archives":[]}
        k.json_atomic(self.store.receipt_path(self.row),rec)
        self.store.publish([self.row])
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE knowledge_entries(id TEXT,relative_path TEXT,name TEXT,hidden INT,entry_type TEXT,media_kind TEXT)")
        self.db.execute("CREATE TABLE knowledge_policies(relative_path TEXT,allow_download INT)")
        self.db.execute("INSERT INTO knowledge_entries VALUES(?,?,?,0,'file','video')",(self.row["id"],"a.mp4","a.mp4"))
        self.db.commit()
        self.app = flask.Flask(__name__)
        self.app.secret_key = "isolated-test-key"
        from jinja2 import DictLoader
        self.app.jinja_loader = DictLoader({"knowledge_view.html":
            '<video src="{{ media_url }}"></video><span>{{ entry.name }}</span>'})
        def permission_required(permission):
            def decorate(fn):
                @k.wraps(fn)
                def wrapped(*a,**kw):
                    if flask.session.get("user") not in {"employee-a","employee-b"}:
                        flask.abort(403)
                    return fn(*a,**kw)
                return wrapped
            return decorate
        self.stub = types.SimpleNamespace(db=lambda: self.db,permission_required=permission_required)
        self.patch_app = patch.dict(sys.modules,{"app":self.stub})
        self.patch_app.start()
        self.patch_env = patch.dict(os.environ,{"AZBAZE_KNOWLEDGE_PERMANENT_ROOT":str(self.dest)})
        self.patch_env.start()
        for name in ["knowledge_view","knowledge_media","knowledge_compatible_media","knowledge_video_status","knowledge_video_retry"]:
            self.app.add_url_rule("/"+name+"/<entry_id>",name,lambda entry_id: (_ for _ in ()).throw(AssertionError("legacy called")),methods=["GET","POST"])
        k.install(self.app)
        self.client = self.app.test_client()

    def tearDown(self):
        self.patch_env.stop(); self.patch_app.stop(); self.db.close(); self.tmp.cleanup()

    def login(self,user):
        with self.client.session_transaction() as s:
            s["user"] = user

    def test_two_users_same_bytes_without_prepare_or_db_writes(self):
        before = self.db.total_changes
        with patch.object(k.subprocess,"Popen",side_effect=AssertionError("on-demand conversion")):
            for user in ["employee-a","employee-b"]:
                self.login(user)
                response=self.client.get("/knowledge_compatible_media/"+self.row["id"])
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.data,b"0123456789abcdef")
                self.assertEqual(response.headers["Cache-Control"],"private, no-store")
                response.close()
        self.assertEqual(before,self.db.total_changes)

    def test_permission_and_range(self):
        url="/knowledge_compatible_media/"+self.row["id"]
        self.assertEqual(self.client.get(url).status_code,403)
        self.login("employee-a")
        with self.client.get(url,headers={"Range":"bytes=3-7"}) as r:
            self.assertEqual(r.status_code,206)
            self.assertEqual(r.data,b"34567")
        self.assertEqual(self.client.get(url,headers={"Range":"bytes=100-200"}).status_code,416)

    def test_ready_status_and_retry_does_not_enqueue(self):
        self.login("employee-b")
        result=self.client.get("/knowledge_video_status/"+self.row["id"]).json
        self.assertTrue(result["ready"])
        self.assertEqual(result["status"],"ready")
        self.assertEqual(self.client.post("/knowledge_video_retry/"+self.row["id"]).status_code,409)

    def test_broken_manifest_503_not_legacy(self):
        (self.dest/"active.json").write_text("broken")
        self.login("employee-a")
        self.assertEqual(self.client.get("/knowledge_compatible_media/"+self.row["id"]).status_code,503)

    def test_view_uses_ready_media_and_preserves_download_policy(self):
        self.login("employee-a")
        before = self.db.total_changes
        with patch.object(k.subprocess, "Popen", side_effect=AssertionError("conversion")):
            r = self.client.get("/knowledge_view/" + self.row["id"])
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"/knowledge-compatible-media/", r.data)
        self.assertNotIn("Готовим".encode(), r.data)
        self.assertEqual(before, self.db.total_changes)

    def test_unconfigured_does_not_change_routes(self):
        app=flask.Flask("unset")
        old=dict(app.view_functions)
        with patch.dict(os.environ,{"AZBAZE_KNOWLEDGE_PERMANENT_ROOT":""}):
            k.install(app)
        self.assertEqual(app.view_functions,old)


if __name__ == "__main__":
    unittest.main(verbosity=2)
