"""Focused offline safety checks. No service calls, privilege changes or live DB."""
import contextlib
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SOURCE = Path(os.environ.get("AZ_OCR_DIAGNOSTIC_SOURCE", str(Path(__file__).with_name("diagnose_medical_ocr_20261127.py"))))
spec = importlib.util.spec_from_file_location("az_ocr_readonly_diagnostic_under_test", SOURCE)
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)
SECRET = "PRIVATE_OCR_NAME_AMOUNT_TOKEN"


class ReadonlyDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="az-diag-test-")
        self.root = Path(self.tmp.name)
        self.db = self.root / "auth.db"
        self.raw = b"%PDF-1.4\n" + SECRET.encode() + b"\n%%EOF"
        self.sha = hashlib.sha256(self.raw).hexdigest()
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE med_supply_documents (id INTEGER PRIMARY KEY, sha256 TEXT, size_bytes INTEGER, content_type TEXT, original_bytes BLOB)")
            conn.execute("INSERT INTO med_supply_documents VALUES(1,?,?,?,?)", (self.sha, len(self.raw), "application/pdf", self.raw))
            conn.execute("INSERT INTO med_supply_documents VALUES(2,?,?,?,?)", (self.sha, len(self.raw), "application/pdf", self.raw))
        self.lock = self.root / ".med-supply-ocr.lock"
        self.lock.write_bytes(b"existing-lock-marker")
        self.lock.chmod(0o600)
        self.patches = [patch.object(diag, "DATABASE", self.db), patch.object(diag, "ORIGINAL_SHA256", self.sha)]
        for item in self.patches:
            item.start()
        self.addCleanup(self.tmp.cleanup)
        for item in self.patches:
            self.addCleanup(item.stop)

    def db_hash(self):
        return hashlib.sha256(self.db.read_bytes()).hexdigest()

    def test_sqlite_is_readonly_query_only_scoped_and_closed(self):
        before = self.db_hash()
        original_connect = sqlite3.connect
        opened, commands = [], []

        class ObservedConnection(sqlite3.Connection):
            def execute(conn, sql, parameters=(), /):
                commands.append((sql, parameters))
                if sql.startswith("SELECT size_bytes"):
                    self.assertEqual(super(ObservedConnection, conn).execute("PRAGMA query_only").fetchone()[0], 1)
                    with self.assertRaises(sqlite3.OperationalError):
                        super(ObservedConnection, conn).execute("DELETE FROM med_supply_documents")
                return super(ObservedConnection, conn).execute(sql, parameters)

        def connect(database_uri, **kwargs):
            self.assertEqual(database_uri, self.db.as_uri() + "?mode=ro")
            self.assertTrue(kwargs["uri"])
            conn = original_connect(database_uri, factory=ObservedConnection, **kwargs)
            opened.append(conn)
            return conn

        with patch.object(diag.sqlite3, "connect", side_effect=connect):
            self.assertEqual(diag.read_original(), self.raw)
        self.assertEqual(self.db_hash(), before)
        selected = [params for sql, params in commands if sql.startswith("SELECT size_bytes")]
        self.assertEqual(selected, [(1, self.sha, diag.MAX_BYTES)])
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")

    def test_wrong_document_sha_is_rejected_without_write(self):
        before = self.db_hash()
        with patch.object(diag, "ORIGINAL_SHA256", "0" * 64):
            with self.assertRaisesRegex(diag.Stop, "^DOCUMENT_SCOPE$"):
                diag.read_original()
        self.assertEqual(self.db_hash(), before)

    def test_corrupt_original_bytes_are_rejected_without_write(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE med_supply_documents SET original_bytes=? WHERE id=1", (self.raw[:-1] + b"X",))
        before = self.db_hash()
        with self.assertRaisesRegex(diag.Stop, "^ORIGINAL_INTEGRITY$"):
            diag.read_original()
        self.assertEqual(self.db_hash(), before)

    def test_lock_is_existing_readonly_and_exclusive(self):
        before = self.lock.stat()
        with diag.existing_ocr_lock():
            with self.assertRaisesRegex(diag.Stop, "^OCR_ALREADY_RUNNING$"):
                with diag.existing_ocr_lock():
                    self.fail("Concurrent OCR lock was admitted")
        self.assertEqual(self.lock.read_bytes(), b"existing-lock-marker")
        self.assertEqual(self.lock.stat().st_mtime_ns, before.st_mtime_ns)
        self.lock.unlink()
        with self.assertRaises(FileNotFoundError):
            with diag.existing_ocr_lock():
                self.fail("Missing live lock was created")
        self.assertFalse(self.lock.exists())

    def test_lock_rejects_symlink_and_unsafe_permissions(self):
        target = self.root / "other"
        target.write_bytes(b"do-not-touch")
        self.lock.unlink()
        self.lock.symlink_to(target)
        with self.assertRaises(OSError):
            with diag.existing_ocr_lock():
                self.fail("Symlink lock admitted")
        self.assertEqual(target.read_bytes(), b"do-not-touch")
        self.lock.unlink()
        self.lock.write_bytes(b"existing-lock-marker")
        self.lock.chmod(0o644)
        with self.assertRaisesRegex(diag.Stop, "^OCR_LOCK_METADATA$"):
            with diag.existing_ocr_lock():
                self.fail("World-readable lock admitted")

    def test_source_guard_rejects_any_mismatch_before_executing_modules(self):
        first, second = "az_diag_probe_first", "az_diag_probe_second"
        raw = b"raise AssertionError('must not execute before all pins pass')\n"
        (self.root / (first + ".py")).write_bytes(raw)
        second_path = self.root / (second + ".py")
        second_path.write_bytes(raw + b"# drift\n")
        pins = {first: diag.git_blob(raw), second: diag.git_blob(raw)}
        with patch.object(diag, "APP", self.root), patch.object(diag, "PINS", pins):
            with self.assertRaisesRegex(diag.Stop, "^RUNTIME_PIN$"):
                diag.load_pinned_modules()
            self.assertNotIn(first, sys.modules)
            self.assertNotIn(second, sys.modules)
            second_path.unlink()
            second_path.symlink_to(self.root / (first + ".py"))
            with self.assertRaisesRegex(diag.Stop, "^RUNTIME_PATH$"):
                diag.load_pinned_modules()
            self.assertNotIn(first, sys.modules)

    def test_exception_codes_preserve_cause_and_redact_private_text(self):
        cases = [
            (subprocess.TimeoutExpired([SECRET], 8, output=SECRET), b"", "TIMEOUT"),
            (subprocess.CalledProcessError(1, [SECRET]), SECRET.encode() + b" failed loading language", "OCR_LANGUAGE_MODEL"),
            (subprocess.CalledProcessError(1, [SECRET]), SECRET.encode() + b" cannot allocate memory", "MEMORY_ALLOCATION"),
            (subprocess.CalledProcessError(-24, [SECRET]), b"", "CPU_LIMIT"),
            (subprocess.CalledProcessError(-25, [SECRET]), b"", "FILE_SIZE_LIMIT"),
            (PermissionError(errno.EACCES, SECRET), b"", "OS_ERROR"),
        ]
        for cause, stderr, expected in cases:
            with self.subTest(expected=expected):
                error = ValueError(SECRET)
                error.__cause__ = cause
                details = diag.exception_details(error, stderr)
                self.assertEqual(details["reason"], expected)
                self.assertNotIn(SECRET, json.dumps(details))
                self.assertEqual(details["exceptions"][0], "ValueError")
        cycle = ValueError(SECRET)
        cycle.__cause__ = cycle
        self.assertEqual(diag.exception_details(cycle)["exceptions"], ["ValueError"])

    def test_traced_command_preserves_runner_parameters_and_redacts_stderr(self):
        events, seen = [], []
        output = self.root / "page.tsv"
        argv = ["tesseract", str(self.root / "page.png"), "stdout", "-l", "rus+eng", "--psm", "3", "tsv"]

        def fake_run(*args, **kwargs):
            seen.append((args, kwargs.copy()))
            kwargs["stderr"].write(SECRET.encode() + b" failed loading language")
            raise subprocess.CalledProcessError(1, [SECRET], stderr=SECRET)

        def original(actual_argv, directory, actual_output, seconds, maximum):
            self.assertEqual((actual_argv, directory, actual_output, seconds, maximum), (argv, self.root, output, 8, diag.MAX_BYTES))
            try:
                subprocess.run(actual_argv, cwd=directory, stderr=subprocess.DEVNULL, timeout=seconds)
            except subprocess.CalledProcessError as cause:
                raise ValueError(SECRET) from cause

        with patch.object(diag.subprocess, "run", side_effect=fake_run) as runner:
            traced = diag.traced_command(original, lambda event, **values: events.append({"event": event, **values}))
            with self.assertRaises(ValueError):
                traced(argv, self.root, output, 8, diag.MAX_BYTES)
            self.assertIs(diag.subprocess.run, runner)
        self.assertEqual(events[0]["stage"], "INITIAL_OCR")
        self.assertEqual(events[0]["reason"], "OCR_LANGUAGE_MODEL")
        self.assertEqual(events[0]["returncode"], 1)
        self.assertEqual(events[0]["limit_seconds"], 8)
        self.assertEqual(events[0]["configured_seconds"], 8)
        self.assertNotIn(SECRET, json.dumps(events))
        self.assertEqual(seen[0][1]["timeout"], 8)
        self.assertNotEqual(seen[0][1]["stderr"], subprocess.DEVNULL)

    def run_mock_diagnosis(self, fail=False):
        before, lock_before = self.db_hash(), self.lock.read_bytes()
        events, scratch_paths = [], []
        command = lambda *args: None

        def recognize(raw, page, scratch):
            self.assertEqual(raw, self.raw)
            self.assertEqual(page, 1)
            self.assertNotEqual(scratch, self.db.parent)
            self.assertEqual(Path(tempfile.gettempdir()), scratch)
            scratch_paths.append(scratch)
            (scratch / "private-output").write_text(SECRET)
            if fail:
                raise subprocess.TimeoutExpired([SECRET], 8)
            return 9, {"words": [SECRET], "candidates": [{"name": SECRET, "value": SECRET}], "recognition_issues": [SECRET]}

        core = types.SimpleNamespace(_command=command, recognize_pdf_page=recognize)
        old_temp = tempfile.tempdir
        with patch.object(diag, "service_identity_and_limits", return_value=(object(), object(), {"MemoryMax": "infinity"})), \
             patch.object(diag, "drop_privileges") as drop, \
             patch.object(diag, "load_pinned_modules", return_value=core):
            diag.diagnose(lambda event, **values: events.append({"event": event, **values}))
        drop.assert_called_once()
        self.assertEqual(self.db_hash(), before)
        self.assertEqual(self.lock.read_bytes(), lock_before)
        self.assertTrue(scratch_paths)
        self.assertTrue(all(not path.exists() for path in scratch_paths))
        self.assertIs(core._command, command)
        self.assertEqual(tempfile.tempdir, old_temp)
        self.assertNotIn(SECRET, json.dumps(events))
        self.assertEqual(events[-1]["event"], "READ_ONLY")
        return next(event for event in events if event["event"] == "OCR_RESULT")

    def test_diagnose_success_reads_only_doc1_page1_and_cleans_temporary_data(self):
        result = self.run_mock_diagnosis()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["page_count"], 9)

    def test_diagnose_failure_keeps_database_and_lock_and_cleans_temporary_data(self):
        result = self.run_mock_diagnosis(fail=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "TIMEOUT")

    def test_main_suppresses_python_native_stdout_and_stderr(self):
        program = '''import importlib.util, os, sys
spec=importlib.util.spec_from_file_location("private_diag",sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def fake(emit):
 print("PRIVATE_OCR_NAME_AMOUNT_TOKEN",flush=True)
 os.write(1,b"PRIVATE_OCR_NAME_AMOUNT_TOKEN\\n")
 os.write(2,b"PRIVATE_OCR_NAME_AMOUNT_TOKEN\\n")
 emit("SAFE_EVENT",status="PASS")
 raise RuntimeError("PRIVATE_OCR_NAME_AMOUNT_TOKEN")
m.diagnose=fake
raise SystemExit(m.main())
'''
        proc = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(SOURCE)], capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn(SECRET, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "")
        reports = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(reports[0], {"event": "SAFE_EVENT", "status": "PASS"})
        self.assertEqual(reports[1]["event"], "STOP")
        self.assertEqual(reports[1]["status"], "DIAGNOSTIC_FAILED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
