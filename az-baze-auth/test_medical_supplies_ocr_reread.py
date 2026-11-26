"""Read-only automatic preview and explicit, recoverable replacement of one page.

The fixtures are synthetic. These tests exercise Flask/SQLite routes and never
invoke OCR, post stock, or require a private document.
"""
import copy
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

from flask import template_rendered

import medical_supplies_ocr as ocr
import test_medical_supplies_ocr as fixtures


APPLY = fixtures.PREFIX + "/automatic/apply"
EVENT = "ocr_automatic_apply"


class AutomaticRereadRouteTests(unittest.TestCase):
    client = fixtures.OCRRouteTests.client
    upload = fixtures.OCRRouteTests.upload
    get_doc = fixtures.OCRRouteTests.get_doc
    initial = fixtures.OCRRouteTests.initial
    state = fixtures.OCRRouteTests.state
    snapshot_stock = fixtures.OCRRouteTests.snapshot_stock
    review_snapshot = fixtures.OCRRouteTests.review_snapshot

    def setUp(self):
        fixtures.OCRRouteTests.setUp(self)
        self.folder_id = self.conn.execute(
            "INSERT INTO med_supply_folders(name) VALUES('Synthetic goods')"
        ).lastrowid
        self.conn.commit()

    def automatic(self):
        page = fixtures.data_fixture()
        page["candidates"][0].update(
            name="Автоматически прочитанный товар", quantity="2", value="289.20",
            value_basis="printed_gross", action="new", folder_id=self.folder_id,
            item_id=None, note="",
        )
        page.update(
            recognition_version=ocr.RECOGNITION_VERSION,
            extractor_version=ocr.EXTRACTOR_VERSION,
            printed_total="289.20", total_basis="printed_gross",
            header_candidate={
                "document_type": "act", "counterparty": "Автоматический поставщик",
                "number": "SYNTHETIC-A", "date": "2026-11-20", "part": 1,
                "note": "", "issues": [], "pagination": {"page": 1, "of": 1},
            },
        )
        return page

    def manual_page(self):
        page = fixtures.data_fixture()
        row = dict(page["candidates"][0])
        row.update(
            name="Ручная строка до обновления", quantity="17", value="91.01",
            action="new", folder_id=self.folder_id, item_id=None,
            note="Сохранить в истории", value_basis="printed_gross",
        )
        page.update(
            rows=[row], checked=True, no_items=False, review_scope="goods",
            header={"document_type": "act", "counterparty": "Ручной поставщик",
                    "number": "MANUAL-8", "date": "2026-11-21", "part": 8,
                    "note": "Реквизиты не заменять"},
            automatic=self.automatic(),
        )
        return page

    def seed(self, page=None, *, other_page=None, extra_fields=None):
        if self.get_doc() is None:
            self.initial()
        pages = {"1": copy.deepcopy(self.manual_page() if page is None else page)}
        if other_page is not None:
            pages["2"] = copy.deepcopy(other_page)
        fields = {
            ocr.KEY: {"page_count": len(pages), "pages": pages},
            "outside_ocr": {"keep": ["original field", 7]},
        }
        if extra_fields:
            fields.update(extra_fields)
        self.conn.execute(
            "UPDATE med_supply_documents SET fields_json=?,version=2,status='reviewed' WHERE id=1",
            (json.dumps(fields, ensure_ascii=False),),
        )
        self.conn.commit()

    def context(self, *, page=1, view="current", uid=2):
        captured = []

        def capture(sender, template, context, **extra):
            if template.name == "medical_supplies_ocr.html":
                captured.append(context)

        url = fixtures.PREFIX + "?p=" + str(page) + "&view=" + view
        with template_rendered.connected_to(capture, self.app):
            response = self.client(uid).get(url)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(len(captured), 1)
        captured[0]["_rendered_html"] = response.get_data(as_text=True)
        return captured[0]

    def apply(self, *, version=2, page=1, uid=2, token=None, **overrides):
        if token is None:
            token = self.context(page=page, view="automatic")["automatic_token"]
        form = {
            "csrf": "token", "version": str(version), "page": str(page),
            "automatic_token": token,
        }
        form.update(overrides)
        return self.client(uid).post(APPLY, data=form)

    def events(self):
        return list(self.conn.execute(
            "SELECT * FROM med_supply_document_events WHERE action=? ORDER BY id", (EVENT,)
        ))

    def test_preview_is_read_only_and_does_not_hide_or_mutate_saved_manual_drafts(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                page = self.manual_page()
                if empty:
                    page.update(rows=[], header={}, no_items=True)
                self.seed(page)
                before = list(self.conn.iterdump())
                with patch.object(ocr, "recognize_pdf_page", side_effect=AssertionError("GET must not OCR")):
                    saved = self.context()
                    preview = self.context(view="automatic")
                    back = self.context()
                self.assertFalse(saved["view_automatic"])
                self.assertTrue(preview["view_automatic"])
                self.assertTrue(preview["has_automatic"])
                self.assertTrue(preview["can_apply_automatic"])
                self.assertEqual(preview["rows"][0]["name"], "Автоматически прочитанный товар")
                self.assertEqual(preview["rows"][0]["unit_cost"], "144.60")
                self.assertEqual(preview["header"], page["header"])
                self.assertEqual(saved["rows"], back["rows"])
                self.assertEqual([r["name"] for r in back["rows"]], [r["name"] for r in page["rows"]])
                self.assertEqual(self.state()["pages"]["1"], page)
                self.assertEqual(list(self.conn.iterdump()), before)

    def test_apply_matches_the_preview_and_archives_the_full_previous_page(self):
        other = self.manual_page()
        other["rows"][0]["name"] = "Другая страница"
        other["header"]["part"] = 9
        self.seed(other_page=other)
        before_page = copy.deepcopy(self.state()["pages"]["1"])
        before_doc = dict(self.get_doc())
        before_stock = self.snapshot_stock()
        preview = self.context(view="automatic")
        with patch.object(ocr, "recognize_pdf_page", side_effect=AssertionError("Apply must not OCR")):
            response = self.apply(token=preview["automatic_token"])
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        self.assertIn("p=1", response.location)
        self.assertIn("applied=1", response.location)
        applied = self.state()["pages"]["1"]
        displayed = self.context()
        self.assertEqual(displayed["rows"], preview["rows"])
        self.assertEqual(displayed["supplier"], preview["supplier"])
        self.assertEqual(applied["header"], before_page["header"])
        self.assertFalse(applied["checked"])
        self.assertFalse(applied["no_items"])
        self.assertEqual(applied["review_scope"], "goods")
        self.assertEqual(self.state()["pages"]["2"], other)
        self.assertEqual(self.get_doc()["version"], 3)
        self.assertEqual(self.get_doc()["status"], "review")
        for key in ("sha256", "size_bytes", "original_bytes", "document_type", "counterparty",
                    "document_number", "document_date", "note"):
            self.assertEqual(self.get_doc()[key], before_doc[key], key)
        self.assertEqual(json.loads(self.get_doc()["fields_json"])["outside_ocr"],
                         {"keep": ["original field", 7]})
        self.assertEqual(self.snapshot_stock(), before_stock)
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["actor_id"], 2)
        self.assertEqual(json.loads(events[0]["before_json"]),
                         {"version": 2, "page": 1, "page_data": before_page})
        after = json.loads(events[0]["after_json"])
        self.assertFalse(after["stock_posted"])
        self.assertEqual(after["version"], 3)
        self.assertNotIn("Ручная строка до обновления", json.dumps(applied, ensure_ascii=False))

    def test_empty_or_absent_manual_header_and_deliberately_empty_rows_require_explicit_apply(self):
        for header_present in (False, True):
            with self.subTest(header_present=header_present):
                page = self.manual_page()
                page.update(rows=[], no_items=True, checked=True)
                if header_present:
                    page["header"] = {}
                else:
                    page.pop("header")
                self.seed(page)
                preview = self.context(view="automatic")
                self.assertEqual(self.state()["pages"]["1"], page)
                response = self.apply(token=preview["automatic_token"])
                self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
                applied = self.state()["pages"]["1"]
                self.assertEqual("header" in applied, header_present)
                if header_present:
                    self.assertEqual(applied["header"], {})
                self.assertEqual(self.context()["rows"], preview["rows"])
                self.assertFalse(applied["no_items"])
                self.assertFalse(applied["checked"])

    def test_apply_uses_only_server_candidates_and_ignores_forged_posted_rows_or_headers(self):
        self.seed()
        preview = self.context(view="automatic")
        response = self.apply(
            token=preview["automatic_token"], name="Подмена", quantity="999", value="0.00",
            value_basis="printed_gross", unit_cost="0.00", checked="1", no_items="1",
            rows='[{"name":"Injected"}]', automatic='{"candidates":[]}',
            header='{"part":99}', part="99", counterparty="Подмена поставщика",
        )
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        self.assertEqual(self.context()["rows"], preview["rows"])
        self.assertEqual(self.state()["pages"]["1"]["header"]["part"], 8)
        self.assertFalse(self.state()["pages"]["1"]["checked"])
        self.assertFalse(self.state()["pages"]["1"]["no_items"])

    def test_unknown_net_and_zero_amounts_are_preserved_without_inference_or_posting(self):
        page = self.manual_page()
        candidates = []
        specs = (("printed_gross", "0.00", "2", "упак", "0.00"),
                 ("printed_net", "120.50", "2", "упак", None),
                 ("unknown", "289.20", "2", "упак", None),
                 ("", "289.20", "2", "упак", None),
                 ("printed_gross", None, "2", "упак", None),
                 ("printed_gross", "289.20", None, "упак", None),
                 ("printed_gross", "289.20", "2", "", None),
                 ("printed_gross", "289.20", "2", None, None))
        for index, (basis, value, quantity, unit, expected_cost) in enumerate(specs, 1):
            row = copy.deepcopy(page["automatic"]["candidates"][0])
            row.update(source_line=index, name="Товар " + str(index), value_basis=basis,
                       value=value, quantity=quantity, unit=unit)
            candidates.append(row)
        page["automatic"]["candidates"] = candidates
        page["automatic"]["lines"] = [
            {"id": index, "text": "Синтетическая строка " + str(index), "words": []}
            for index in range(1, len(candidates) + 1)
        ]
        self.seed(page)
        stock = self.snapshot_stock()
        preview = self.context(view="automatic")
        response = self.apply(token=preview["automatic_token"])
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        self.assertEqual(self.context()["rows"], preview["rows"])
        rows = self.context()["rows"]
        for row, (basis, value, quantity, unit, expected_cost) in zip(rows, specs):
            self.assertEqual((row["value_basis"], row["value"], row["quantity"], row["unit"], row["unit_cost"]),
                             (basis, value, quantity, unit, expected_cost))
        self.assertFalse(self.state()["pages"]["1"]["checked"])
        self.assertEqual(self.snapshot_stock(), stock)

    def test_nonretryable_warning_remains_visible_and_does_not_block_an_unchecked_draft(self):
        page = self.manual_page()
        warning = "Сетка таблицы не найдена; использован текстовый разбор."
        page["automatic"]["recognition_issues"] = [warning]
        self.seed(page)
        preview = self.context(view="automatic")
        self.assertTrue(preview["can_apply_automatic"])
        self.assertFalse(preview["automatic_unavailable_reason"])
        self.assertIn(warning, preview["page_issues"])
        response = self.apply(token=preview["automatic_token"])
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        applied = self.state()["pages"]["1"]
        self.assertEqual(applied["recognition_issues"], [warning])
        self.assertFalse(applied["checked"])
        displayed = self.context()
        self.assertEqual(displayed["rows"], preview["rows"])
        self.assertIn(warning, displayed["page_issues"])

    def test_missing_failed_and_empty_automatic_results_never_replace_manual_work(self):
        cases = {
            "missing": None,
            "malformed": {},
            "empty": {**self.automatic(), "candidates": []},
            "retryable": {**self.automatic(), "recognition_retryable": True},
            "failed": {**self.automatic(), "error": "Synthetic failure"},
            "malformed_candidates": {**self.automatic(), "candidates": "invalid"},
        }
        for name, automatic in cases.items():
            with self.subTest(case=name):
                page = self.manual_page()
                if automatic is None:
                    page.pop("automatic")
                else:
                    page["automatic"] = copy.deepcopy(automatic)
                self.seed(page)
                before = list(self.conn.iterdump())
                preview = self.context(view="automatic")
                self.assertFalse(preview["can_apply_automatic"])
                self.assertTrue(preview["automatic_unavailable_reason"])
                self.assertNotIn('id="ocr-apply-automatic"', preview["_rendered_html"])
                if automatic is None:
                    self.assertFalse(preview["view_automatic"])
                    self.assertEqual(preview["view_mode"], "current")
                    self.assertEqual([row["name"] for row in preview["rows"]],
                                     [row["name"] for row in page["rows"]])
                    self.assertIn(preview["automatic_unavailable_reason"], preview["_rendered_html"])
                # Submit the actual token displayed for this source: rejection
                # must protect an empty/failed result, not just a bad token.
                response = self.apply(token=preview["automatic_token"])
                self.assertIn(response.status_code, (400, 409), response.get_data(as_text=True))
                self.assertEqual(list(self.conn.iterdump()), before)

    def test_read_permissions_post_only_csrf_and_source_token_are_required(self):
        self.seed()
        token = self.context(view="automatic")["automatic_token"]
        before = list(self.conn.iterdump())
        viewer = self.context(view="automatic", uid=3)
        self.assertTrue(viewer["view_automatic"])
        self.assertFalse(viewer["can_manage"])
        self.assertNotIn('id="ocr-apply-automatic"', viewer["_rendered_html"])
        self.assertEqual(self.client(4).get(fixtures.PREFIX + "?p=1&view=automatic").status_code, 403)
        self.assertEqual(self.client(2).get(APPLY).status_code, 405)
        for uid in (3, 4):
            self.assertEqual(self.apply(uid=uid, token=token).status_code, 403)
        self.assertIn(self.apply(uid=None, token=token).status_code, (302, 303))
        self.assertEqual(self.apply(token=token, csrf="wrong").status_code, 400)
        self.assertEqual(self.apply(token="").status_code, 400)
        self.assertIn(self.apply(token="0" * 64).status_code, (400, 409))
        self.assertEqual(list(self.conn.iterdump()), before)

    def test_apply_rejects_stale_page_or_source_and_does_not_reuse_a_token_for_another_page(self):
        other = self.manual_page()
        other["automatic"]["candidates"][0]["value"] = "10.00"
        self.seed(other_page=other)
        token = self.context(view="automatic")["automatic_token"]
        before = list(self.conn.iterdump())
        self.assertEqual(self.apply(version=1, token=token).status_code, 409)
        self.assertIn(self.apply(page=2, token=token).status_code, (400, 409))
        self.assertIn(self.apply(page=3, token=token).status_code, (400, 404))
        self.assertEqual(list(self.conn.iterdump()), before)
        fields = json.loads(self.get_doc()["fields_json"])
        fields[ocr.KEY]["pages"]["1"]["automatic"]["candidates"][0]["value"] = "10.01"
        self.conn.execute("UPDATE med_supply_documents SET fields_json=? WHERE id=1",
                          (json.dumps(fields, ensure_ascii=False),))
        self.conn.commit()
        changed = list(self.conn.iterdump())
        self.assertIn(self.apply(token=token).status_code, (400, 409))
        self.assertEqual(list(self.conn.iterdump()), changed)

    def test_archive_event_failure_rolls_back_replacement_version_and_status(self):
        self.seed()
        token = self.context(view="automatic")["automatic_token"]
        before = list(self.conn.iterdump())
        with patch.object(ocr.inbox, "_event", side_effect=sqlite3.IntegrityError("synthetic event failure")):
            response = self.apply(token=token)
        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        self.assertEqual(list(self.conn.iterdump()), before)

    def test_archive_and_document_size_limits_both_fail_without_partial_changes(self):
        for large_archive in (True, False):
            with self.subTest(large_archive=large_archive):
                page = self.manual_page()
                fields = None
                if large_archive:
                    page["rows"][0]["note"] = "x" * 30000
                else:
                    fields = {"outside_ocr": {"large": "x" * 30000}}
                self.seed(page, extra_fields=fields)
                token = self.context(view="automatic")["automatic_token"]
                before = list(self.conn.iterdump())
                with patch.object(ocr, "MAX_JSON", 20000):
                    response = self.apply(token=token)
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
                self.assertEqual(list(self.conn.iterdump()), before)

    def test_concurrent_version_or_original_sha_change_before_transaction_wins(self):
        actual_store = ocr.store
        for changed_field in ("version", "sha256"):
            with self.subTest(changed_field=changed_field):
                self.seed()
                token = self.context(view="automatic")["automatic_token"]
                expected = []

                def changed_then_store(*args, **kwargs):
                    if changed_field == "version":
                        self.conn.execute("UPDATE med_supply_documents SET version=3,note='Concurrent edit' WHERE id=1")
                    else:
                        raw = bytes(self.get_doc()["original_bytes"]) + b"\n"
                        self.conn.execute(
                            "UPDATE med_supply_documents SET original_bytes=?,size_bytes=?,sha256=? WHERE id=1",
                            (raw, len(raw), hashlib.sha256(raw).hexdigest()),
                        )
                    self.conn.commit()
                    expected[:] = list(self.conn.iterdump())
                    return actual_store(*args, **kwargs)

                with patch.object(ocr, "store", side_effect=changed_then_store):
                    response = self.apply(token=token)
                self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
                self.assertTrue(expected)
                self.assertEqual(list(self.conn.iterdump()), expected)

    def test_original_bytes_are_verified_before_applying_automatic_data(self):
        self.seed()
        token = self.context(view="automatic")["automatic_token"]
        self.conn.execute("UPDATE med_supply_documents SET original_bytes=? WHERE id=1", (b"corrupted",))
        self.conn.commit()
        before = list(self.conn.iterdump())
        self.assertEqual(self.apply(token=token).status_code, 400)
        self.assertEqual(list(self.conn.iterdump()), before)

    def test_retry_does_not_duplicate_archive_and_later_edits_make_the_old_form_stale(self):
        self.seed()
        token = self.context(view="automatic")["automatic_token"]
        self.assertEqual(self.apply(token=token).status_code, 303)
        applied = list(self.conn.iterdump())
        self.assertEqual(self.apply(token=token).status_code, 303)
        self.assertEqual(list(self.conn.iterdump()), applied)
        self.assertEqual(len(self.events()), 1)
        self.conn.execute("UPDATE med_supply_documents SET version=4,note='Later edit' WHERE id=1")
        self.conn.commit()
        edited = list(self.conn.iterdump())
        self.assertEqual(self.apply(token=token).status_code, 409)
        self.assertEqual(list(self.conn.iterdump()), edited)

    def test_repeated_application_keeps_history_outside_the_current_page(self):
        self.seed()
        first = copy.deepcopy(self.state()["pages"]["1"])
        self.assertEqual(self.apply().status_code, 303)
        fields = json.loads(self.get_doc()["fields_json"])
        current = fields[ocr.KEY]["pages"]["1"]
        newer = self.automatic()
        newer["candidates"][0]["name"] = "Новый результат повторного чтения"
        newer["candidates"][0]["value"] = "150.00"
        current["automatic"] = newer
        second = copy.deepcopy(current)
        self.conn.execute("UPDATE med_supply_documents SET fields_json=?,version=4 WHERE id=1",
                          (json.dumps(fields, ensure_ascii=False),))
        self.conn.commit()
        self.assertEqual(self.apply(version=4).status_code, 303)
        events = self.events()
        self.assertEqual(len(events), 2)
        self.assertEqual(json.loads(events[0]["before_json"])["page_data"], first)
        self.assertEqual(json.loads(events[1]["before_json"])["page_data"], second)
        self.assertNotIn("Ручная строка до обновления", json.dumps(second, ensure_ascii=False))
        applied = self.state()["pages"]["1"]
        self.assertNotIn("Ручная строка до обновления", json.dumps(applied, ensure_ascii=False))
        self.assertNotIn("automatic", applied)
        self.assertEqual(applied["rows"][0]["name"], "Новый результат повторного чтения")

    def test_history_listing_does_not_read_archived_page_json(self):
        self.seed()
        self.assertEqual(self.apply().status_code, 303)
        archived = self.events()[0]["before_json"]
        self.assertIn("page_data", json.loads(archived))
        actual_connect = sqlite3.connect
        read_columns = []

        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and table == "med_supply_document_events":
                read_columns.append(column)
                if column in ("before_json", "after_json"):
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        def guarded_connect(*args, **kwargs):
            conn = actual_connect(*args, **kwargs)
            conn.set_authorizer(authorize)
            return conn

        with patch.object(sqlite3, "connect", side_effect=guarded_connect):
            response = self.client(2).get("/accounting/medical-supplies/incoming/?id=1")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn("Выбран новый разбор страницы; прежний сохранён в истории",
                      response.get_data(as_text=True))
        self.assertTrue({"action", "created_at", "actor_id"}.issubset(read_columns), read_columns)
        self.assertNotIn("before_json", read_columns)
        self.assertNotIn("after_json", read_columns)
        self.assertEqual(self.events()[0]["before_json"], archived)


if __name__ == "__main__":
    unittest.main()
