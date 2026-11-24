"""Incoming document inbox: SQLite originals, permissions, no stock side effects."""
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

os.environ.setdefault("AZBAZE_SECRET_KEY", "local-test-only")
import app as appcore
import medical_supplies_documents as inbox


PDF = b"%PDF-1.7\n1 0 obj\n<</Type/Catalog>>\nendobj\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic image bytes"
XML = '<?xml version="1.0"?><УПД><Номер>123</Номер></УПД>'.encode("utf-8")


class IncomingDocumentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dbpath = self.root / "auth.db"
        for field, value in (
            ("DB_PATH", self.dbpath), ("SITE_ROOT", self.root),
        ):
            patcher = patch.object(appcore, field, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.root / "index.html").write_text(
            (Path(__file__).parents[1] / "az-baze" / "index.html").read_text(),
            encoding="utf-8",
        )
        appcore.init_db_file(self.dbpath)
        with sqlite3.connect(self.dbpath) as c:
            for uid in range(1, 6):
                c.execute(
                    "INSERT INTO users(id,email,full_name,password_hash,is_admin,"
                    "active,must_change_password,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,1,0,?,?)",
                    (uid, f"u{uid}@example.test", f"User {uid}", "hash", int(uid == 1),
                     "now", "now"),
                )
            c.executemany("INSERT INTO permissions(user_id,section) VALUES(?,?)", (
                (2, "accounting"), (2, "medical_supplies_edit"),
                (3, "accounting"), (4, "reports"),
                (5, "accounting"), (5, "medical_supplies_documents_manage"),
            ))
        self.app = appcore.create_app()
        self.app.config.update(TESTING=True, MAX_CONTENT_LENGTH=25*1024*1024)
        self.conn = sqlite3.connect(self.dbpath)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.conn.close)

    def client(self, uid=None, csrf=True):
        client = self.app.test_client()
        if uid:
            with client.session_transaction() as session:
                session["user_id"] = uid
                if csrf:
                    session["csrf"] = "token"
        return client

    def upload(self, raw=PDF, filename="Комус УПД.pdf", uid=2, **overrides):
        client = self.client(uid)
        form = {
            "csrf": "token",
            "file": (io.BytesIO(raw), filename),
            "document_type": "upd",
            "counterparty": "ООО Комус",
            "document_number": "S269000100839695",
            "document_date": "2026-11-20",
            "note": "Проверить позиции и НДС",
        }
        form.update(overrides)
        response = client.post(
            "/accounting/medical-supplies/incoming/upload",
            data=form, content_type="multipart/form-data",
        )
        return response

    def get_doc(self, doc_id=1):
        return self.conn.execute(
            "SELECT * FROM med_supply_documents WHERE id=?", (doc_id,)
        ).fetchone()

    def review(self, doc_id=1, uid=2, **values):
        data = {
            "csrf": "token", "version": "1", "document_type": "upd",
            "status": "review", "counterparty": "Комус",
            "document_number": "S269000100839695",
            "document_date": "2026-11-20", "note": "Проверка",
        }
        data.update(values)
        return self.client(uid).post(
            f"/accounting/medical-supplies/incoming/{doc_id}/review",
            data=data,
        )

    def test_permission_is_registered_once_by_medical_supplies_extension(self):
        key="medical_supplies_documents_manage"
        self.assertIn(key,appcore.SECTION_KEYS)
        self.assertEqual(sum(s==key for s,_ in appcore.SECTIONS),1)
        # A second app instance does not duplicate a role option.
        appcore.create_app()
        self.assertEqual(sum(s==key for s,_ in appcore.SECTIONS),1)
        with self.app.app_context():
            self.assertIn(key,appcore.user_permissions(
                self.conn.execute("SELECT * FROM users WHERE id=5").fetchone()
            ))

    def test_nav_from_all_three_med_supplies_views_and_empty_state(self):
        c = self.client(2)
        self.assertTrue(inbox.ready(self.conn))
        for suffix in ("?tab=movement", "?tab=stock", "?tab=history"):
            page = c.get("/accounting/medical-supplies/" + suffix)
            self.assertEqual(page.status_code, 200)
            self.assertIn("Входящие документы", page.get_data(as_text=True))
            self.assertIn("/accounting/medical-supplies/incoming/", page.get_data(as_text=True))
        result = c.get("/accounting/medical-supplies/incoming/")
        self.assertEqual(result.status_code, 200)
        html = result.get_data(as_text=True)
        self.assertIn("Загрузить документ", html)
        self.assertIn("На разбор", html)
        self.assertIn("Документов по выбранным условиям пока нет", html)
        self.assertNotIn("0 ₽", html)

    def test_upload_preserves_original_pdf_and_metadata_with_audit(self):
        resp = self.upload()
        self.assertEqual(resp.status_code, 303)
        self.assertIn("uploaded=1", resp.location)
        r = self.get_doc()
        self.assertIsNotNone(r)
        self.assertEqual(r["sha256"], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(r["original_bytes"], PDF)
        self.assertEqual((r["document_type"], r["status"]), ("upd", "pending"))
        self.assertEqual((r["document_number"], r["counterparty"]),
                         ("S269000100839695", "ООО Комус"))
        self.assertEqual(r["document_date"], "2026-11-20")
        self.assertEqual(r["size_bytes"], len(PDF))
        self.assertEqual(r["version"], 1)
        events = list(self.conn.execute(
            "SELECT action,actor_id,after_json FROM med_supply_document_events"
        ))
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["action"], events[0]["actor_id"]), ("upload", 2))
        self.assertNotIn("original_bytes", events[0]["after_json"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM med_supply_periods")
                         .fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM med_supply_days")
                         .fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM med_supply_stock_imports")
                         .fetchone()[0], 0)

    def test_upload_always_starts_pending_not_preapproved(self):
        response=self.upload(status="reviewed")
        self.assertEqual(response.status_code,303)
        self.assertEqual(self.get_doc()["status"],"pending")
        saved=json.loads(self.conn.execute(
            "SELECT after_json FROM med_supply_document_events").fetchone()[0])
        self.assertEqual(saved["status"],"pending")

    def test_duplicate_sha_returns_existing_without_second_history(self):
        self.assertEqual(self.upload().status_code, 303)
        again = self.upload(filename="копия документа.pdf",
                            counterparty="Иной поставщик", document_number="2")
        self.assertEqual(again.status_code, 303)
        self.assertIn("duplicate=1", again.location)
        self.assertIn("id=1", again.location)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM med_supply_documents")
                         .fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM med_supply_document_events")
                         .fetchone()[0], 1)
        self.assertEqual(self.get_doc()["counterparty"], "ООО Комус")

    def test_accounting_reader_cannot_upload_or_modify_original(self):
        self.upload()
        reader = self.client(3)
        page = reader.get("/accounting/medical-supplies/incoming/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Доступен просмотр документов", page.get_data(as_text=True))
        self.assertNotIn('id="inbox-file"', page.get_data(as_text=True))
        self.assertEqual(self.upload(uid=3).status_code, 403)
        self.assertEqual(self.review(uid=3).status_code, 403)
        self.assertEqual(self.get_doc()["version"], 1)
        manager = self.upload(PDF + b"another", uid=5)
        self.assertEqual(manager.status_code, 303)
        self.assertEqual(self.upload(PDF + b"owner", uid=1).status_code, 303)
        nonaccountant = self.client(4)
        self.assertEqual(nonaccountant.get("/accounting/medical-supplies/incoming/").status_code, 403)
        self.assertEqual(self.upload(uid=4).status_code, 403)
        self.assertEqual(self.client().get("/accounting/medical-supplies/incoming/").status_code, 302)

    def test_csrf_and_file_validation_fail_closed_without_write(self):
        client = self.client(2, csrf=False)
        self.assertEqual(client.post(
            "/accounting/medical-supplies/incoming/upload",
            data={"file": (io.BytesIO(PDF), "file.pdf")},
        ).status_code, 400)
        invalid = [
            (b"", "empty.pdf"),
            (b"<script>alert(1)</script>", "fake.pdf"),
            (PDF, "trojan.exe"),
            (PDF, "fake.jpg"),
            (b"\x00\x01\x02", "text.txt"),
            (b"PK\x03\x04garbage", "broken.xlsx"),
            (PDF, "inbox.html"),
        ]
        for raw, filename in invalid:
            with self.subTest(filename=filename):
                response = self.upload(raw, filename)
                self.assertEqual(response.status_code, 400)
        with patch.object(inbox, "MAX_BYTES", 15):
            self.assertEqual(self.upload(PDF, "oversize.pdf").status_code, 400)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM med_supply_documents")
                         .fetchone()[0], 0)

    def test_secure_original_download_and_passive_preview(self):
        self.upload()
        client = self.client(3)
        response = client.get("/accounting/medical-supplies/incoming/1/download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, PDF)
        self.assertEqual(response.headers["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        preview = client.get("/accounting/medical-supplies/incoming/1/view")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.data, PDF)
        self.assertIn("sandbox", preview.headers["Content-Security-Policy"])
        self.assertEqual(self.client(4).get(
            "/accounting/medical-supplies/incoming/1/download").status_code, 403)
        self.assertEqual(self.client().get(
            "/accounting/medical-supplies/incoming/1/download").status_code, 302)
        self.assertEqual(client.get(
            "/accounting/medical-supplies/incoming/9999/download").status_code, 404)

    def test_multiline_review_comment_and_html_stay_plain_text(self):
        self.upload()
        note="Сверить счёт и УПД\nПроверить сумму <script>alert(1)</script>"
        res=self.review(note=note)
        self.assertEqual(res.status_code,303)
        self.assertEqual(self.get_doc()["note"],note)
        html=self.client(3).get(
            "/accounting/medical-supplies/incoming/?id=1"
        ).get_data(as_text=True)
        self.assertIn("&lt;script&gt;",html)
        self.assertNotIn("<script>alert(1)</script>",html)

    def test_edit_metadata_requires_version_and_keeps_binary_immutable(self):
        self.upload()
        original = self.get_doc()
        response = self.review(status="reviewed", note="Проверили, без прихода",
                               counterparty="ООО Комус", version="1")
        self.assertEqual(response.status_code, 303)
        after = self.get_doc()
        self.assertEqual(after["original_bytes"], original["original_bytes"])
        self.assertEqual(after["sha256"], original["sha256"])
        self.assertEqual(after["version"], 2)
        self.assertEqual(after["status"], "reviewed")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM med_supply_document_events").fetchone()[0], 2)
        stale = self.review(status="review", version="1")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.get_doc()["status"], "reviewed")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM med_supply_document_events").fetchone()[0], 2)

    def test_reject_bad_meta_and_unknown_documents_without_side_effects(self):
        self.upload()
        for change in (
            {"document_type": "malware"},
            {"status": "accounted", "version": "1"},
            {"document_date": "2026-11-31"},
            {"counterparty": "a" * 201},
            {"version": "NaN"},
        ):
            with self.subTest(change=change):
                self.assertEqual(self.review(**change).status_code, 400)
        self.assertEqual(self.review(doc_id=9999).status_code, 404)
        self.assertEqual(self.get_doc()["version"], 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM med_supply_document_events").fetchone()[0], 1)

    def test_russian_search_type_and_review_filters_without_writing(self):
        self.upload(filename="Комус УПД.pdf")
        self.upload(PDF + b"\n2", filename="Акт компании.pdf",
                    document_type="act", counterparty="Другая организация")
        self.review(status="reviewed")
        client = self.client(3)
        before = self.dbpath.read_bytes()
        response = client.get("/accounting/medical-supplies/incoming/?q=КОМУС")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Комус УПД.pdf", response.get_data(as_text=True))
        self.assertNotIn("Акт компании.pdf", response.get_data(as_text=True))
        response = client.get("/accounting/medical-supplies/incoming/?status=pending&type=act")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Акт компании.pdf", response.get_data(as_text=True))
        self.assertNotIn("Комус УПД.pdf", response.get_data(as_text=True))
        self.assertEqual(self.dbpath.read_bytes(), before)
        self.assertEqual(client.get("/accounting/medical-supplies/incoming/?status=bad").status_code, 400)
        self.assertEqual(client.get("/accounting/medical-supplies/incoming/?id=9999").status_code, 404)

    def test_pdf_without_extension_and_cyrillic_basename(self):
        res = self.upload(filename="../../УПД Комус")
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.get_doc()["original_name"], "УПД Комус")
        self.assertEqual(self.get_doc()["content_type"], "application/pdf")
        self.assertEqual(inbox._filename("folder\\invoice.pdf"), "invoice.pdf")

    @staticmethod
    def make_zip(files, compression=zipfile.ZIP_STORED):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=compression) as z:
            for name, data in files:
                z.writestr(name, data)
        return stream.getvalue()

    @classmethod
    def valid_docx(cls):
        types = ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                 '</Types>')
        rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                '</Relationships>')
        body = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p/></w:body></w:document>')
        return cls.make_zip([
            ("[Content_Types].xml", types),
            ("_rels/.rels", rels),
            ("word/document.xml", body),
        ])

    def test_az256_01_reject_windows_traversal_drive_unc_root_and_ads_paths(self):
        bad_paths = [
            r"..\secret.txt", r"docs\..\secret.txt", r"a/b\..\secret.txt",
            r"C:\Users\secret.txt", r"C:secret.txt", r"E:/data.txt",
            r"\\server\share\document.xml", r"\Windows\temp.txt",
            r"//server/share/doc.txt", "/etc/passwd", r"a\b\..\..\x.txt",
            r"folder\file.txt:evil", "a/./file.txt", "a//file.txt",
            r"dir\CON.txt", "COM1.txt", "folder/../../s.txt",
        ]
        for path in bad_paths:
            with self.subTest(path=path):
                payload = self.make_zip([(path, "data")])
                with self.assertRaises(ValueError):
                    inbox.validate_file(payload, "supplier.zip")
        for safe in (r"docs\sample.txt", "docs/report.pdf", "Единицы/документ.pdf"):
            # Ordinary Windows separators are normalized but never extracted.
            self.assertEqual(inbox.validate_file(
                self.make_zip([(safe, "example")]), "supplier.zip"
            )[1], "application/zip")
        with self.assertRaises(ValueError):
            inbox.validate_file(self.make_zip([
                ("docs/a.txt", "a"), (r"docs\a.txt", "b"),
            ]), "invoice.zip")

    def test_az256_02_invalid_crc_rejected_in_zip_docx_and_upload(self):
        zip_source = self.make_zip([("invoice.txt", b"UNIQUE_CRC_PAYLOAD_123")])
        damaged = zip_source.replace(b"UNIQUE_CRC_PAYLOAD_123",
                                     b"UNIQUE_CRC_PAYLOAD_124", 1)
        self.assertNotEqual(damaged, zip_source)
        with self.assertRaises(ValueError):
            inbox.validate_file(damaged, "invoice.zip")
        self.assertEqual(self.upload(damaged, "invoice.zip").status_code, 400)
        docx = self.valid_docx()
        needle = b"<w:p/>"
        self.assertIn(needle, docx)
        bad_docx = docx.replace(needle, b"<w:x/>", 1)
        with self.assertRaises(ValueError):
            inbox.validate_file(bad_docx, "invoice.docx")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM med_supply_documents").fetchone()[0], 0)

    def test_az256_02_invalid_office_structure_names_only_rejected(self):
        types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>'
        rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>'
        bogus = [
            [("[Content_Types].xml", types), ("word/document.xml", "<document/>")],
            [("[Content_Types].xml", types), ("_rels/.rels", rels),
             ("word/document.xml", "<document/>")],
            [("[Content_Types].xml", "<malformed"), ("_rels/.rels", rels),
             ("word/document.xml", "<document/>")],
        ]
        for items in bogus:
            with self.subTest(items=items):
                with self.assertRaises(ValueError):
                    inbox.validate_file(self.make_zip(items), "fake.docx")
        bad_rels = self.valid_docx().replace(b'Target="word/document.xml"',
                                            b'Target="word/unknown.xml"', 1)
        with self.assertRaises(ValueError):
            inbox.validate_file(bad_rels, "broken.docx")
        docx = self.valid_docx()
        with self.assertRaises(ValueError):
            inbox.validate_file(docx, "fake.xlsx")  # wrong OOXML package kind
        with self.assertRaises(ValueError):
            inbox.validate_file(self.make_zip([
                ("mimetype", "application/vnd.oasis.opendocument.text"),
                ("content.xml", "bad"), ("META-INF/manifest.xml", "<bad/>"),
            ]), "not-a-real.odt")
        with self.assertRaises(ValueError):
            inbox.validate_file(self.make_zip([
                ("mimetype", "application/vnd.oasis.opendocument.spreadsheet"),
                ("content.xml", "<a/>"), ("META-INF/manifest.xml", "<bad/>"),
            ]), "fake.ods")

    def test_az256_02_zip_resource_limits_and_truncation(self):
        archive = self.make_zip([("first.txt", "a"), ("second.txt", "b")])
        with patch.object(inbox, "ZIP_MAX_MEMBERS", 1):
            with self.assertRaises(ValueError):
                inbox.validate_file(archive, "two.zip")
        with patch.object(inbox, "ZIP_MAX_UNCOMPRESSED", 1):
            with self.assertRaises(ValueError):
                inbox.validate_file(archive, "two.zip")
        with patch.object(inbox, "ZIP_MAX_MEMBER", 1):
            with self.assertRaises(ValueError):
                inbox.validate_file(self.make_zip([("large.txt", b"xx")]), "large.zip")
        with self.assertRaises(ValueError):
            inbox.validate_file(archive[:-12], "truncated.zip")
        # The entire validation must fail atomically; no document or event.
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM med_supply_documents").fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM med_supply_document_events").fetchone()[0], 0)

    def test_multiple_supported_formats_and_zip_path_checks(self):
        workbook = Workbook()
        workbook.active["A1"] = "УПД"
        output = io.BytesIO()
        workbook.save(output)
        samples = [
            (PNG, "photo.png", "image/png"),
            (XML, "УПД.xml", "application/xml"),
            ("Наименование;Сумма\nКомус;100".encode(), "invoice.csv", "text/csv"),
            (output.getvalue(), "requisites.xlsx",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            (b"{\\rtf1 text}", "letter.rtf", "application/rtf"),
        ]
        for raw, filename, mime in samples:
            with self.subTest(filename=filename):
                name, actual = inbox.validate_file(raw, filename)
                self.assertEqual(name, filename)
                self.assertEqual(actual, mime)
        docx = self.valid_docx()
        self.assertEqual(inbox.validate_file(docx, "letter.docx")[1],
                         "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual(self.upload(raw=XML, filename="УПД.xml").status_code, 303)
        self.assertEqual(self.client(2).get(
            "/accounting/medical-supplies/incoming/1/view").status_code, 415)
        with io.BytesIO() as raw:
            with zipfile.ZipFile(raw, "w") as z:
                z.writestr("../unsafe.txt", "data")
            self.assertRaises(ValueError, inbox.validate_file,
                              raw.getvalue(), "files.zip")

    def test_schema_is_explicit_readonly_and_history_atomic(self):
        self.assertTrue(inbox.ready(self.conn))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM med_supply_documents").fetchone()[0], 0)
        before = self.dbpath.read_bytes()
        self.client(2).get("/accounting/medical-supplies/incoming/")
        self.assertEqual(self.dbpath.read_bytes(), before)
        with self.conn:
            self.conn.execute(
                "CREATE TRIGGER fail_doc_event BEFORE INSERT ON med_supply_document_events "
                "BEGIN SELECT RAISE(ABORT,'audit unavailable'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            inbox.save_upload(self.conn, PDF, "doc.pdf", {"document_type": "upd"}, 2)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM med_supply_documents").fetchone()[0], 0)

    def test_missing_schema_is_not_silently_initialized_on_get(self):
        with self.conn:
            self.conn.execute("DROP TABLE med_supply_document_events")
            self.conn.execute("DROP TABLE med_supply_documents")
        before = self.dbpath.read_bytes()
        r = self.client(2).get("/accounting/medical-supplies/incoming/")
        self.assertEqual(r.status_code, 503)
        self.assertIn("пока не установлено", r.get_data(as_text=True))
        self.assertFalse(inbox.ready(self.conn))
        self.assertEqual(before, self.dbpath.read_bytes())


if __name__ == "__main__":
    unittest.main()
