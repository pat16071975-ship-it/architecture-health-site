import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import knowledge_video_compat as compat


class KnowledgeVideoCompatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="az-video-compat-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "qnap"
        self.cache = base / "cache"
        self.root.mkdir()
        self.cache.mkdir()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        compat.init_schema(self.conn)
        self.addCleanup(self.conn.close)

    def entry(self, relative="Видео/test.mp4", size=100, mtime="123"):
        return {
            "id": compat_hash(relative),
            "relative_path": relative,
            "size_bytes": size,
            "modified_at": mtime,
        }

    def test_request_video_queues_and_resets_changed_source(self):
        entry = self.entry()
        with patch.object(compat, "COMPAT_ROOT", self.cache):
            state = compat.request_video(self.conn, entry)
            self.assertEqual(state["status"], "pending")

            path = compat.cache_path(entry["id"])
            path.write_bytes(b"cached")
            self.conn.execute(
                "UPDATE knowledge_video_compat SET status='ready' WHERE entry_id=?",
                (entry["id"],),
            )
            self.conn.commit()

            changed = dict(entry)
            changed["size_bytes"] = 101
            state = compat.request_video(self.conn, changed)
            self.assertEqual(state["status"], "pending")
            self.assertFalse(path.exists())

    def test_browser_compatible_rule_is_strict(self):
        h264 = {
            "video": {"codec_name": "h264", "pix_fmt": "yuv420p"},
            "audio": {"codec_name": "aac"},
        }
        hevc = {
            "video": {"codec_name": "hevc", "pix_fmt": "yuv420p10le"},
            "audio": {"codec_name": "aac"},
        }
        self.assertTrue(compat.source_is_browser_compatible(Path("x.mp4"), h264))
        self.assertFalse(compat.source_is_browser_compatible(Path("x.mp4"), hevc))
        self.assertFalse(compat.source_is_browser_compatible(Path("x.mkv"), h264))

    def test_worker_marks_browser_compatible_source_without_transcoding(self):
        relative = "Видео/ready.mp4"
        source = self.root / relative
        source.parent.mkdir(parents=True)
        source.write_bytes(b"source")
        entry = self.entry(relative=relative, size=source.stat().st_size, mtime="1")

        with patch.object(compat, "KNOWLEDGE_ROOT", self.root), patch.object(compat, "COMPAT_ROOT", self.cache):
            compat.request_video(self.conn, entry)
            with patch.object(
                compat,
                "probe",
                return_value={
                    "video": {"codec_name": "h264", "pix_fmt": "yuv420p"},
                    "audio": {"codec_name": "aac"},
                    "duration": 60.0,
                },
            ):
                self.assertTrue(compat.process_one(self.conn))

        state = self.conn.execute(
            "SELECT * FROM knowledge_video_compat WHERE entry_id=?",
            (entry["id"],),
        ).fetchone()
        self.assertEqual(state["status"], "source_ok")
        self.assertFalse((self.cache / f"{entry['id']}.mp4").exists())

    def test_worker_transcodes_incompatible_source_once(self):
        relative = "Видео/hevc.mp4"
        source = self.root / relative
        source.parent.mkdir(parents=True)
        source.write_bytes(b"hevc-source")
        entry = self.entry(relative=relative, size=source.stat().st_size, mtime="1")

        def fake_run(args, **_kwargs):
            Path(args[-1]).write_bytes(b"h264-cache")
            return SimpleNamespace(returncode=0, stderr="")

        probe_results = [
            {
                "video": {"codec_name": "hevc", "pix_fmt": "yuv420p10le"},
                "audio": {"codec_name": "aac"},
                "duration": 120.0,
            },
            {
                "video": {"codec_name": "h264", "pix_fmt": "yuv420p"},
                "audio": {"codec_name": "aac"},
                "duration": 120.0,
            },
        ]

        with patch.object(compat, "KNOWLEDGE_ROOT", self.root), \
             patch.object(compat, "COMPAT_ROOT", self.cache), \
             patch.object(compat, "CACHE_LIMIT_BYTES", 1024**3), \
             patch.object(compat, "MIN_FREE_BYTES", 0), \
             patch.object(compat, "probe", side_effect=probe_results), \
             patch.object(compat.subprocess, "run", side_effect=fake_run):
            compat.request_video(self.conn, entry)
            self.assertTrue(compat.process_one(self.conn))

        state = self.conn.execute(
            "SELECT * FROM knowledge_video_compat WHERE entry_id=?",
            (entry["id"],),
        ).fetchone()
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["codec_name"], "hevc")
        self.assertTrue((self.cache / f"{entry['id']}.mp4").exists())


def compat_hash(relative):
    import hashlib
    return hashlib.sha256(relative.encode("utf-8")).hexdigest()[:32]


if __name__ == "__main__":
    unittest.main()
