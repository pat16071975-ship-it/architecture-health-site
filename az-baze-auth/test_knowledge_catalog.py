import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import knowledge_catalog


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
