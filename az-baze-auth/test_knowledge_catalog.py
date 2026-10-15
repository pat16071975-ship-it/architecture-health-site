import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

import knowledge_catalog
import app as app_core


class KnowledgeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="az-knowledge-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "Обучение"
        self.root.mkdir()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)

    def write(self, relative, data=b"x"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_reindex_filters_technical_files_without_touching_source(self):
        video = self.write("Для ассистентов/ОБУЧЕНИЕ DIERS/1.mp4", b"video")
        lrf = self.write("Для ассистентов/ОБУЧЕНИЕ DIERS/1.LRF", b"proxy")
        dot = self.write("Для ассистентов/ОБУЧЕНИЕ DIERS/._1.MP4", b"meta")
        recycle = self.write("@Recycle/deleted.mp4", b"deleted")
        pdf = self.write("Общая медицина/SEST/Методичка.pdf", b"%PDF")
        dicom = self.write("Стоматология и миотерапия/КТ/scan.dcm", b"dicom")
        stl = self.write("Стоматология и миотерапия/Модели/model.stl", b"stl")
        dentalcad = self.write("Стоматология и миотерапия/Модели/case.dentalcad", b"cad")
        dll = self.write("Стоматология и миотерапия/ОБУЧЕНИЯ/runtime/tool.dll", b"dll")
        jar = self.write("Стоматология и миотерапия/ОБУЧЕНИЯ/runtime/tool.jar", b"jar")
        noext = self.write("Стоматология и миотерапия/ОБУЧЕНИЯ/runtime/LICENSE", b"text")
        mts = self.write("Стоматология и миотерапия/ОБУЧЕНИЯ/lecture.MTS", b"video")
        gif = self.write("Стоматология и миотерапия/ОБУЧЕНИЯ/slides.gif", b"image")

        with patch.object(knowledge_catalog, "KNOWLEDGE_ROOT", self.root):
            count = knowledge_catalog.reindex(self.conn)

        self.assertGreaterEqual(count, 5)
        visible = {
            row["relative_path"]
            for row in self.conn.execute(
                "SELECT relative_path FROM knowledge_entries WHERE hidden=0 AND entry_type='file'"
            )
        }
        self.assertIn("Для ассистентов/ОБУЧЕНИЕ DIERS/1.mp4", visible)
        self.assertIn("Общая медицина/SEST/Методичка.pdf", visible)
        self.assertNotIn("Для ассистентов/ОБУЧЕНИЕ DIERS/1.LRF", visible)
        self.assertNotIn("Для ассистентов/ОБУЧЕНИЕ DIERS/._1.MP4", visible)
        self.assertNotIn("@Recycle/deleted.mp4", visible)
        self.assertNotIn("Стоматология и миотерапия/КТ/scan.dcm", visible)
        self.assertNotIn("Стоматология и миотерапия/Модели/model.stl", visible)
        self.assertNotIn("Стоматология и миотерапия/Модели/case.dentalcad", visible)
        self.assertNotIn("Стоматология и миотерапия/ОБУЧЕНИЯ/runtime/tool.dll", visible)
        self.assertNotIn("Стоматология и миотерапия/ОБУЧЕНИЯ/runtime/tool.jar", visible)
        self.assertNotIn("Стоматология и миотерапия/ОБУЧЕНИЯ/runtime/LICENSE", visible)
        self.assertIn("Стоматология и миотерапия/ОБУЧЕНИЯ/lecture.MTS", visible)
        self.assertIn("Стоматология и миотерапия/ОБУЧЕНИЯ/slides.gif", visible)

        visible_dirs = {
            row["relative_path"]
            for row in self.conn.execute(
                "SELECT relative_path FROM knowledge_entries WHERE hidden=0 AND entry_type='directory'"
            )
        }
        self.assertNotIn("Стоматология и миотерапия/ОБУЧЕНИЯ/runtime", visible_dirs)

        self.assertTrue(video.exists())
        self.assertTrue(lrf.exists())
        self.assertTrue(dot.exists())
        self.assertTrue(recycle.exists())
        self.assertTrue(pdf.exists())
        self.assertTrue(dicom.exists())
        self.assertTrue(stl.exists())
        self.assertTrue(dentalcad.exists())
        self.assertTrue(dll.exists())
        self.assertTrue(jar.exists())
        self.assertTrue(noext.exists())
        self.assertTrue(mts.exists())
        self.assertTrue(gif.exists())

    def test_root_sklyarov_is_hidden_but_management_sklyarov_is_visible(self):
        root_legacy = self.write("Скляров/2 день 3 часть.MP4", b"legacy")
        canonical = self.write("Управление и сервис/Скляров Управление клиникой/2 день 3 часть.MP4", b"canonical")

        with patch.object(knowledge_catalog, "KNOWLEDGE_ROOT", self.root):
            knowledge_catalog.reindex(self.conn)

        visible = {
            row["relative_path"]
            for row in self.conn.execute(
                "SELECT relative_path FROM knowledge_entries WHERE hidden=0 AND entry_type='file'"
            )
        }
        self.assertNotIn("Скляров/2 день 3 часть.MP4", visible)
        self.assertIn("Управление и сервис/Скляров Управление клиникой/2 день 3 часть.MP4", visible)
        self.assertTrue(root_legacy.exists())
        self.assertTrue(canonical.exists())

    def test_static_knowledge_html_is_readable_by_shared_after_request(self):
        site = Path(self.tmp.name) / "site"
        (site / "knowledge" / "doctor").mkdir(parents=True)
        (site / "knowledge" / "index.html").write_text(
            "<!doctype html><html><head></head><body>ROOT</body></html>",
            encoding="utf-8",
        )
        (site / "knowledge" / "doctor" / "index.html").write_text(
            "<!doctype html><html><head></head><body>DOCTOR</body></html>",
            encoding="utf-8",
        )

        with patch.object(app_core, "SITE_ROOT", site):
            with app_core.app.test_request_context("/knowledge/"):
                response = app_core.app.view_functions["knowledge_index"].__wrapped__()
                self.assertFalse(response.direct_passthrough)
                self.assertIn("ROOT", response.get_data(as_text=True))

            with app_core.app.test_request_context("/knowledge/doctor/"):
                response = app_core.app.view_functions["knowledge_file"].__wrapped__("doctor/")
                self.assertFalse(response.direct_passthrough)
                self.assertIn("DOCTOR", response.get_data(as_text=True))

    def test_sklyarov_static_pages_show_ten_clickable_videos(self):
        repo_root = Path(__file__).resolve().parent.parent
        pages = [
            repo_root / "knowledge/manager/management-service/index.html",
            repo_root / "knowledge/administrator/management-service/index.html",
            repo_root / "knowledge/manager/management-service/sklyarov/index.html",
            repo_root / "knowledge/administrator/management-service/sklyarov/index.html",
        ]

        for page in pages[:2]:
            text = page.read_text(encoding="utf-8")
            self.assertIn('<span class="count">10 видео</span>', text)
            self.assertNotIn('<span class="count">2 видео</span>', text)

        expected_ids = {
            "dabc14c01621ee1cf5eafc309c3704c3",
            "6eb3d2d8406c340d8cfe272c8c47d1fb",
            "b87063b8fbc19c9fd00fd977d7f7f155",
            "6930d5d05c83b55433830ea3ccc4e6b2",
            "510362fa60f1f97e47d09a3c38a3a27b",
            "43877ab18b7b492982b59060e3a04df9",
            "54ca70018990d388a39bd56bbc3c9860",
            "86ef0b211df409c3561fd9c4bb470d97",
            "75b3a5d317f5e04b756e63763a71b6ff",
            "bda2085e71f82e2bcc7e69ce59e888fd",
        }
        for page in pages[2:]:
            text = page.read_text(encoding="utf-8")
            for entry_id in expected_ids:
                self.assertIn(f'href="/knowledge-view/{entry_id}"', text)
            self.assertEqual(text.count('href="/knowledge-view/'), 10)

    def test_primary_role_pages_open_live_indexed_materials(self):
        repo_root = Path(__file__).resolve().parent.parent

        doctor = (repo_root / "knowledge/doctor/index.html").read_text(encoding="utf-8")
        assistant = (repo_root / "knowledge/assistant/index.html").read_text(encoding="utf-8")
        administrator = (repo_root / "knowledge/administrator/index.html").read_text(encoding="utf-8")
        manager = (repo_root / "knowledge/manager/index.html").read_text(encoding="utf-8")
        root = (repo_root / "knowledge/index.html").read_text(encoding="utf-8")

        self.assertIn("/knowledge-library/?path=", doctor)
        self.assertEqual(doctor.count("/knowledge-library/?path="), 3)
        self.assertIn("/knowledge-library/?path=", assistant)
        self.assertIn("/knowledge-library/?path=", administrator)
        self.assertIn("/knowledge-library/?path=", manager)
        self.assertEqual(root.count("/knowledge-library/?path="), 2)

        self.assertNotIn('href="/knowledge/assistant/diers/"', assistant)
        self.assertNotIn('href="/knowledge/administrator/management-service/"', administrator)
        self.assertNotIn('href="/knowledge/manager/management-service/"', manager)

    def test_video_media_route_supports_range_playback(self):
        video = self.write(
            "Управление и сервис/Скляров Управление клиникой/1 день 1 часть.MP4",
            b"0123456789" * 20,
        )
        with patch.object(knowledge_catalog, "KNOWLEDGE_ROOT", self.root):
            knowledge_catalog.reindex(self.conn)
            row = self.conn.execute(
                "SELECT id FROM knowledge_entries WHERE relative_path=?",
                ("Управление и сервис/Скляров Управление клиникой/1 день 1 часть.MP4",),
            ).fetchone()

            test_app = Flask("knowledge-media-test")
            knowledge_catalog.register(test_app)

            with patch.object(knowledge_catalog, "db", lambda: self.conn):
                with test_app.test_request_context(
                    f"/knowledge-media/{row['id']}",
                    headers={"Range": "bytes=0-9"},
                ):
                    response = test_app.view_functions["knowledge_media"].__wrapped__(row["id"])
                    self.assertEqual(response.status_code, 206)
                    self.assertEqual(response.get_data(), b"0123456789")

        self.assertTrue(video.exists())

    def test_ids_are_stable_and_default_policy_forbids_download(self):
        self.write("Круглые столы клиники/Круглый стол 04.2026.MP4", b"video")
        with patch.object(knowledge_catalog, "KNOWLEDGE_ROOT", self.root):
            knowledge_catalog.reindex(self.conn)
            first = self.conn.execute(
                "SELECT id,relative_path FROM knowledge_entries WHERE entry_type='file' AND hidden=0"
            ).fetchone()
            knowledge_catalog.reindex(self.conn)
            second = self.conn.execute(
                "SELECT id,relative_path FROM knowledge_entries WHERE entry_type='file' AND hidden=0"
            ).fetchone()

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["relative_path"], second["relative_path"])
        self.assertFalse(knowledge_catalog._policy(self.conn, first["relative_path"]))

    def test_safe_root_rejects_escape(self):
        self.write("safe.mp4", b"video")
        with patch.object(knowledge_catalog, "KNOWLEDGE_ROOT", self.root):
            self.assertEqual(knowledge_catalog._safe_root_path("safe.mp4"), self.root / "safe.mp4")
            with self.assertRaises(Exception):
                knowledge_catalog._safe_root_path("../outside.mp4")


if __name__ == "__main__":
    unittest.main()
