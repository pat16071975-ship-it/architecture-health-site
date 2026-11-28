"""Durable raw receipt is independent of real IDENT parsing/import results.

Synthetic originals only. Reuse the existing Flask/SQLite fixture, without
inheriting or rerunning its separate business-application test suite.
"""
import hashlib
import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from flask import g
from werkzeug.datastructures import FileStorage, MultiDict

import test_mis_archive_routes as fixtures
import mis_source_archive as archive


class UploadReceiptTests(unittest.TestCase):
    setUp = fixtures.ArchiveRouteTests.setUp
    post = fixtures.ArchiveRouteTests.post
    service_file = fixtures.ArchiveRouteTests.service_file
    completed_file = fixtures.ArchiveRouteTests.completed_file
    cash_file = fixtures.ArchiveRouteTests.cash_file
    assert_ok = fixtures.ArchiveRouteTests.assert_ok

    def business_snapshot(self):
        tables = [row[0] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT IN ('mis_source_objects','mis_source_events') ORDER BY name"
        )]
        return {name: sorted((tuple(row) for row in self.conn.execute(
            'SELECT * FROM "' + name.replace('"', '""') + '"'
        )), key=repr) for name in tables}

    def assert_no_archive(self):
        self.assertIsNone(self.conn.execute(
            "SELECT name FROM sqlite_master WHERE name='mis_source_objects'"
        ).fetchone())

    def received(self, raw, kind, path=None):
        sha = hashlib.sha256(raw).hexdigest()
        # Another connection proves that the receipt was durably committed,
        # rather than left in the business transaction being inspected.
        with sqlite3.connect(self.dbpath) as stored:
            self.assertEqual(archive.read_original(stored, sha), raw)
        rows = self.conn.execute(
            "SELECT * FROM mis_source_events WHERE sha256=? AND source_kind=? "
            "AND origin='ident_upload_received' ORDER BY saved_at,event_key",
            (sha, kind),
        ).fetchall()
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["outcome"], "archive_only")
            self.assertEqual(row["decision"], "received")
            self.assertEqual(row["actor_id"], 7)
            for field in ("source_from", "source_to", "observed_dates_json",
                          "applied_dates_json", "removed_dates_json"):
                self.assertIsNone(row[field])
            context = json.loads(row["context_json"])
            self.assertEqual(context["validation"], "not_performed")
        if path is not None:
            self.assertEqual(json.loads(rows[-1]["context_json"])["request_path"], path)
        return rows

    def no_application(self, raw):
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM mis_source_events WHERE sha256=? AND outcome='applied'",
            (hashlib.sha256(raw).hexdigest(),),
        ).fetchone()[0], 0)

    def valid_files(self):
        return {
            "services": (self.service_file([fixtures.FIRST]), "services.xlsx"),
            "completed": (self.completed_file([fixtures.FIRST]), "completed.xlsx"),
            "payments": (self.cash_file([fixtures.FIRST]), "cash.xlsx"),
            "paid_services": (fixtures.report_bytes(
                ("01.11.2026",), provider=fixtures.STAFF,
                period=("01.11.2026", "01.11.2026")), "paid.xlsx"),
        }

    def legacy_files(self):
        return {
            "revenue": (fixtures.text_rows(fixtures.service_rows([fixtures.FIRST])), "services.tsv"),
            "completed": (fixtures.text_rows(fixtures.completed_rows([fixtures.FIRST])), "completed.tsv"),
        }

    def test_all_five_previews_archive_without_business_changes(self):
        files = self.valid_files()
        cases = (
            ("services", (("services", "services"),)),
            ("completed", (("completed", "completed"),)),
            ("cash", (("cash", "payments"),)),
            ("paid-services", (("paid_services", "paid_services"),)),
            ("clinical", (("completed", "completed"), ("services", "services"))),
        )
        for route, sources in cases:
            with self.subTest(route=route):
                before = self.business_snapshot()
                path = "/api/uploads/" + route + "/preview"
                self.assert_ok(self.post(path, {field: files[field] for _, field in sources}))
                self.assertEqual(self.business_snapshot(), before)
                for kind, field in sources:
                    self.received(files[field][0], kind, path)
                    self.no_application(files[field][0])

    def test_invalid_originals_survive_all_six_import_paths(self):
        for route, field, kind in (
            ("services", "services", "services"),
            ("completed", "completed", "completed"),
            ("cash", "payments", "cash"),
            ("paid-services", "paid_services", "paid_services"),
        ):
            for action in ("preview", "commit"):
                with self.subTest(route=route, action=action):
                    raw = ("Malformed " + route + action).encode()
                    path = "/api/uploads/" + route + "/" + action
                    before = self.business_snapshot()
                    result = self.post(path, {field: (raw, "broken.txt")})
                    self.assertIn(result.status_code, (400, 409))
                    self.received(raw, kind, path)
                    self.no_application(raw)
                    self.assertEqual(self.business_snapshot(), before)
        for path, service_field in (("/api/uploads/clinical/commit", "services"),
                                    ("/api/reports/import-ident", "revenue")):
            with self.subTest(path=path):
                raw = ("Malformed pair " + path).encode()
                completed = self.legacy_files()["completed"]
                before = self.business_snapshot()
                result = self.post(path, {service_field: (raw, "broken.txt"), "completed": completed})
                self.assertIn(result.status_code, (400, 409))
                self.received(raw, "services", path)
                self.received(completed[0], "completed", path)
                self.no_application(raw)
                self.assertEqual(self.business_snapshot(), before)

    def test_missing_pair_counterpart_still_preserves_received_original(self):
        raw = self.service_file([fixtures.FIRST])
        for path, field in (("/api/uploads/clinical/preview", "services"),
                            ("/api/uploads/clinical/commit", "services"),
                            ("/api/reports/import-ident", "revenue")):
            with self.subTest(path=path):
                before = self.business_snapshot()
                response = self.post(path, {field: (raw, "single.xlsx")})
                self.assertEqual(response.status_code, 400)
                self.received(raw, "services", path)
                self.no_application(raw)
                self.assertEqual(self.business_snapshot(), before)

    def test_malformed_decisions_do_not_prevent_original_preservation(self):
        files = self.valid_files()
        for route, keys, field in (
            ("services", ("services",), "provider_decisions"),
            ("clinical", ("services", "completed"), "provider_decisions"),
            ("clinical", ("services", "completed"), "visit_direction_decisions"),
        ):
            with self.subTest(route=route, field=field):
                before = self.business_snapshot()
                path = "/api/uploads/" + route + "/commit"
                response = self.post(path, {key: files[key] for key in keys}, **{field: "{"})
                self.assertEqual(response.status_code, 409)
                for key in keys:
                    self.received(files[key][0], key, path)
                    self.no_application(files[key][0])
                self.assertEqual(self.business_snapshot(), before)

    def assert_business_failure_preserves(self, path, files):
        before = self.business_snapshot()
        original = archive.record

        def fail_after_business_write(conn, kind, *args, **kwargs):
            result = original(conn, kind, *args, **kwargs)
            if kwargs.get("outcome") == "applied":
                raise sqlite3.IntegrityError("synthetic business transaction failure")
            return result

        with patch.object(archive, "record", side_effect=fail_after_business_write):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "business transaction failure"):
                self.post(path, files)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.business_snapshot(), before)
        kinds = {"payments": "cash", "revenue": "services"}
        for field, (raw, _) in files.items():
            self.received(raw, kinds.get(field, field), path)
            self.no_application(raw)

    def test_services_rollback_keeps_original(self):
        self.assert_business_failure_preserves("/api/uploads/services/commit",
            {"services": self.valid_files()["services"]})

    def test_completed_rollback_keeps_original(self):
        self.assert_business_failure_preserves("/api/uploads/completed/commit",
            {"completed": self.valid_files()["completed"]})

    def test_cash_rollback_keeps_original(self):
        self.assert_business_failure_preserves("/api/uploads/cash/commit",
            {"payments": self.valid_files()["payments"]})

    def test_paid_rollback_keeps_original(self):
        self.assert_business_failure_preserves("/api/uploads/paid-services/commit",
            {"paid_services": self.valid_files()["paid_services"]})

    def test_clinical_rollback_keeps_both_originals(self):
        files = self.valid_files()
        self.assert_business_failure_preserves("/api/uploads/clinical/commit",
            {key: files[key] for key in ("completed", "services")})

    def test_legacy_rollback_keeps_both_originals(self):
        self.assert_business_failure_preserves("/api/reports/import-ident", self.legacy_files())

    def test_conflict_and_keep_old_preserve_both_versions(self):
        path = "/api/uploads/services/commit"
        old = self.service_file([fixtures.FIRST], amount=100)
        changed = self.service_file([fixtures.FIRST], amount=200)
        self.assert_ok(self.post(path, {"services": (old, "same.xlsx")}))
        before = self.business_snapshot()
        self.assertEqual(self.post(path, {"services": (changed, "same.xlsx")}).status_code, 409)
        self.received(changed, "services", path)
        self.no_application(changed)
        self.assertEqual(self.business_snapshot(), before)
        self.assert_ok(self.post(path, {"services": (changed, "same.xlsx")}, "keep_old"))
        self.assertEqual(self.business_snapshot(), before)
        self.no_application(changed)
        self.assertEqual(archive.read_original(self.conn, hashlib.sha256(old).hexdigest()), old)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM mis_source_objects").fetchone()[0], 2)

    def test_replays_and_aliases_keep_receipts_but_deduplicate_bytes(self):
        raw = self.service_file([fixtures.FIRST])
        path = "/api/uploads/services/preview"
        for filename in ("first.xlsx", "first.xlsx", "renamed.xlsx"):
            self.assert_ok(self.post(path, {"services": (raw, filename)}))
        rows = self.received(raw, "services", path)
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["original_filename"] for row in rows}, {"first.xlsx", "renamed.xlsx"})
        self.assertEqual(self.conn.execute("SELECT count(*) FROM mis_source_objects").fetchone()[0], 1)
        self.no_application(raw)

    def test_all_parts_of_recognized_field_are_preserved(self):
        first, second = b"invalid source one", b"invalid source two"
        data = MultiDict([("csrf", "csrf"),
            ("services", (io.BytesIO(first), "first.txt")),
            ("services", (io.BytesIO(second), "second.txt")),
            ("services", (io.BytesIO(first), "first.txt")),
        ])
        response = self.client.post("/api/uploads/services/preview", data=data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.received(first, "services")), 2)
        self.received(second, "services")
        self.no_application(first)
        self.no_application(second)

    def test_upload_permission_gates_precede_archiving(self):
        files = self.valid_files()
        before = self.business_snapshot()
        for route, sources, granted in (
            ("services", ("services",), {"section5", "upload_completed"}),
            ("completed", ("completed",), {"section5", "upload_services"}),
            ("cash", ("payments",), {"section5", "upload_completed"}),
            ("paid-services", ("paid_services",), {"section5", "upload_completed"}),
            ("clinical", ("services", "completed"), {"section5", "upload_services"}),
            ("clinical", ("services", "completed"), {"section5", "upload_completed"}),
        ):
            for action in ("preview", "commit"):
                with self.subTest(route=route, action=action, granted=granted):
                    self.perms = granted
                    response = self.post("/api/uploads/" + route + "/" + action,
                                         {key: files[key] for key in sources})
                    self.assertEqual(response.status_code, 403)
                    self.assert_no_archive()
        self.is_admin = False
        self.assertEqual(self.post("/api/reports/import-ident", self.legacy_files()).status_code, 403)
        self.assert_no_archive()
        self.assertEqual(self.business_snapshot(), before)

    def test_section_permission_and_unauthenticated_requests_do_not_archive(self):
        self.app.add_url_rule("/login", endpoint="login", view_func=lambda: "login")
        files = {"services": self.valid_files()["services"]}
        self.perms = {"upload_services"}
        self.assertEqual(self.post("/api/uploads/services/preview", files).status_code, 403)
        self.assert_no_archive()
        self.app.before_request_funcs[None].append(lambda: setattr(g, "user", None))
        self.assertEqual(self.post("/api/uploads/services/preview", files).status_code, 302)
        self.assert_no_archive()

    def test_csrf_failure_precedes_archiving(self):
        files = self.valid_files()
        for path, field in (
            ("/api/uploads/services/preview", "services"),
            ("/api/uploads/completed/commit", "completed"),
            ("/api/uploads/cash/preview", "payments"),
            ("/api/uploads/paid-services/commit", "paid_services"),
            ("/api/uploads/clinical/commit", "services"),
            ("/uploads/cash", "payments"),
            ("/api/reports/import-ident", "services"),
        ):
            with self.subTest(path=path):
                raw, name = files[field]
                response = self.client.post(path,
                    data={field: (io.BytesIO(raw), name), "csrf": "wrong"},
                    headers={"X-CSRF-Token": "wrong"})
                self.assertEqual(response.status_code, 400)
                self.assert_no_archive()

    def test_oversized_part_is_not_stored_and_valid_counterpart_is_preserved(self):
        small = b"valid size original, invalid report"
        oversized = b"X" * 65
        before = self.business_snapshot()
        with patch.object(archive, "MAX_SOURCE_BYTES", 64):
            response = self.post("/api/uploads/clinical/commit", {
                "completed": (oversized, "large.txt"),
                "services": (small, "small.txt"),
            })
        self.assertEqual(response.status_code, 409)
        self.received(small, "services")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM mis_source_objects").fetchone()[0], 1)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM mis_source_objects WHERE sha256=?",
            (hashlib.sha256(oversized).hexdigest(),)).fetchone())
        self.assertEqual(self.business_snapshot(), before)

    def test_request_size_gate_and_empty_files_do_not_archive(self):
        self.app.config["MAX_CONTENT_LENGTH"] = 128
        response = self.post("/api/uploads/services/preview", {"services": (b"x" * 200, "large.txt")})
        self.assertEqual(response.status_code, 413)
        self.assert_no_archive()
        self.app.config["MAX_CONTENT_LENGTH"] = None
        self.assertEqual(self.post("/api/uploads/services/preview",
                                  {"services": (b"", "empty.txt")}).status_code, 400)
        self.assert_no_archive()

    def test_archive_failure_stops_import_and_rolls_back_receipt_batch(self):
        files = self.valid_files()
        before = list(self.conn.iterdump())
        original = archive.record

        def fail_second(conn, kind, *args, **kwargs):
            result = original(conn, kind, *args, **kwargs)
            if kind == "services" and kwargs.get("origin") == "ident_upload_received":
                raise sqlite3.OperationalError("synthetic archive unavailable")
            return result

        with patch.object(archive, "record", side_effect=fail_second):
            with self.assertRaisesRegex(sqlite3.OperationalError, "archive unavailable"):
                self.post("/api/uploads/clinical/commit",
                    {key: files[key] for key in ("completed", "services")})
        self.assertEqual(list(self.conn.iterdump()), before)
        self.assertFalse(self.conn.in_transaction)

    def test_helper_refuses_to_commit_an_existing_business_transaction(self):
        raw = b"bounded original"
        files = MultiDict([("services", FileStorage(io.BytesIO(raw), filename="s.txt"))])
        before = self.business_snapshot()
        self.conn.execute("BEGIN")
        self.conn.execute("DELETE FROM report_blobs")
        with self.assertRaisesRegex(RuntimeError, "clean transaction"):
            archive.receive_uploads(self.conn, files, (("services", "services"),),
                                   actor_id=7, request_path="/test")
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.business_snapshot(), before)
        self.assert_no_archive()

    def test_helper_restores_stream_and_never_reads_without_a_bound(self):
        raw = b"original exact bytes"

        class BoundedStream(io.BytesIO):
            def read(self, size=-1):
                if not 0 <= size <= archive.MAX_SOURCE_BYTES + 1:
                    raise AssertionError("unbounded read")
                return super().read(size)

        stream = BoundedStream(raw)
        stream.seek(3)
        files = MultiDict([("services", FileStorage(stream, filename="s.txt"))])
        archive.receive_uploads(self.conn, files, (("services", "services"),),
                               actor_id=7, request_path="/test")
        self.assertEqual(stream.tell(), 3)
        self.received(raw, "services", "/test")

    def test_legacy_cash_post_preserves_rejected_original(self):
        raw = b"unrecognized cash report"
        before = self.business_snapshot()
        response = self.post("/uploads/cash", {"payments": (raw, "cash.txt")})
        self.assertEqual(response.status_code, 302)
        self.received(raw, "cash", "/uploads/cash")
        self.no_application(raw)
        self.assertEqual(self.business_snapshot(), before)

    def test_get_and_display_only_post_do_not_create_archive(self):
        for route in ("services", "completed", "cash", "paid-services", "clinical"):
            self.assertEqual(self.client.get("/api/uploads/" + route + "/preview").status_code, 405)
        self.assertEqual(self.client.get("/reports/import-ident/").status_code, 404)
        self.assertEqual(self.post("/uploads/", {"services": (b"ignored", "old.txt")}).status_code, 303)
        self.assert_no_archive()


if __name__ == "__main__":
    unittest.main()
