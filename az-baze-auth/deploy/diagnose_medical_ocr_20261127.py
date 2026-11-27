#!/usr/bin/env python3
"""Read-only, one-page diagnosis of the exact installed PR274 OCR runtime.

No app/server import, business writes, route calls, service changes or OCR save.
The existing runner and its resource/time limits are retained. Child stderr is
captured privately only to emit fixed reason codes; its contents never print.
"""
import contextlib
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import types

APP = Path("/opt/az-baze-auth")
DATABASE = Path("/var/lib/az-baze/auth.db")
DOCUMENT_ID = 1
PAGE = 1
ORIGINAL_SHA256 = "c089a63d21e791e412562087086021fce82b507f915c6589a8fa97da79a50ec7"
MAX_BYTES = 20 * 1024 * 1024
PINS = {
    "medical_supplies_documents": "61887feb048e4b6dee23eae403f404370f479d20",
    "medical_supplies_ocr_enrichment": "7304d9db500725951fe9dc75a221e66e729fc53c",
    "medical_supplies_ocr_quality": "c8d0b7b2df97f974d28b408be7c0dc43d1f95d22",
    "medical_supplies_ocr_image": "0fb65eaf55ec9f6d69e13cbd8d527c5a51abcadc",
    "medical_supplies_ocr": "2b9ae9103df06a077f8476d71c368d3dbc03e83d",
}
STAGES = {
    "page": ("INITIAL_OCR", 8),
    "clean-page": ("CLEANED_PAGE_OCR", 8),
    "table-cells": ("TABLE_CELLS_OCR", 8),
    "table-numbers": ("TABLE_NUMBERS_OCR", 8),
    "numeric-retry": ("NUMERIC_RETRY_OCR", 4),
}
LIMIT_KEYS = ("MemoryMax", "CPUQuotaPerSecUSec", "LimitAS", "LimitCPU", "TasksMax")


class Stop(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Stop(code)


def git_blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def service_identity_and_limits():
    require(os.geteuid() == 0 and socket.gethostname().split(".")[0] == "vdska", "HOST_OR_ROOT")
    result = subprocess.run(
        ["/usr/bin/systemctl", "show", "az-baze-auth", "--no-pager",
         "--property=" + ",".join(("User", "Group", *LIMIT_KEYS))],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5, check=True)
    require(len(result.stdout) < 8192, "SERVICE_PROPERTIES")
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    require(props.get("User") == "azadmin" and props.get("Group") == "www-data", "SERVICE_IDENTITY")
    account, group = pwd.getpwnam("azadmin"), grp.getgrnam("www-data")
    require(account.pw_uid > 0 and group.gr_gid > 0, "SERVICE_IDENTITY")
    limits = {key: value if re.fullmatch(r"(?:infinity|[0-9]+(?:\.[0-9]+)?(?:us|ms|s|min|h|%)?)", value)
              else "UNRECOGNIZED" for key in LIMIT_KEYS for value in [props.get(key, "")]}
    return account, group, limits


def drop_privileges(account, group):
    os.umask(0o077)
    os.initgroups(account.pw_name, group.gr_gid)
    os.setgid(group.gr_gid)
    os.setuid(account.pw_uid)
    require(os.getuid() == os.geteuid() == account.pw_uid
            and os.getgid() == os.getegid() == group.gr_gid, "DROP_PRIVILEGES")


@contextlib.contextmanager
def existing_ocr_lock():
    """Take the live OCR mutex through a read-only FD; never create or modify it."""
    path = DATABASE.parent / ".med-supply-ocr.lock"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and not info.st_mode & 0o077, "OCR_LOCK_METADATA")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop("OCR_ALREADY_RUNNING") from None
        yield
    finally:
        os.close(fd)


def read_original():
    require(DATABASE.resolve(strict=True) == DATABASE, "DATABASE_PATH")
    with contextlib.closing(sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True,
                                           isolation_level=None, timeout=5)) as conn:
        conn.execute("PRAGMA query_only=ON")
        require(conn.execute("PRAGMA query_only").fetchone()[0] == 1, "QUERY_ONLY")
        row = conn.execute("SELECT size_bytes, content_type, original_bytes FROM med_supply_documents "
                           "WHERE id=? AND sha256=? AND typeof(original_bytes)='blob' "
                           "AND length(original_bytes) BETWEEN 1 AND ?",
                           (DOCUMENT_ID, ORIGINAL_SHA256, MAX_BYTES)).fetchone()
    require(row is not None, "DOCUMENT_SCOPE")
    size, kind, raw = row
    require(isinstance(raw, bytes) and len(raw) == size and kind == "application/pdf"
            and raw.startswith(b"%PDF-") and hashlib.sha256(raw).hexdigest() == ORIGINAL_SHA256,
            "ORIGINAL_INTEGRITY")
    return raw


def load_pinned_modules():
    """Compile the verified snapshots themselves, avoiding a second unchecked read."""
    sources = {}
    for name, expected in PINS.items():
        path = APP / (name + ".py")
        require(path.resolve(strict=True) == path and name not in sys.modules, "RUNTIME_PATH")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            require(stat.S_ISREG(os.fstat(source.fileno()).st_mode), "RUNTIME_FILE")
            raw = source.read(2 * 1024 * 1024)
        require(len(raw) < 2 * 1024 * 1024 and git_blob(raw) == expected, "RUNTIME_PIN")
        sources[name] = (path, raw)
    for name, (path, raw) in sources.items():
        module = types.ModuleType(name)
        module.__file__ = str(path)
        sys.modules[name] = module
        exec(compile(raw, str(path), "exec"), module.__dict__)
    require("app" not in sys.modules and "server" not in sys.modules, "UNEXPECTED_APP_IMPORT")
    return sys.modules["medical_supplies_ocr"]


def exception_details(error, stderr=b""):
    chain, seen = [], set()
    while error is not None and id(error) not in seen and len(chain) < 8:
        chain.append(error)
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    names = {"OCRUnavailable", "TimeoutExpired", "CalledProcessError", "OSError",
             "FileNotFoundError", "PermissionError", "MemoryError", "ValueError", "RuntimeError"}
    details = {"exceptions": [type(exc).__name__ if type(exc).__name__ in names else "OtherError"
                              for exc in chain]}
    for exc in chain:
        if isinstance(exc, subprocess.CalledProcessError):
            details["returncode"] = int(exc.returncode)
        if isinstance(exc, OSError) and exc.errno is not None:
            details["errno"] = int(exc.errno)
    evidence = stderr[:65536].lower()
    if any(isinstance(exc, subprocess.TimeoutExpired) for exc in chain):
        reason = "TIMEOUT"
    elif any(x in evidence for x in (b"failed loading language", b"error opening data file")):
        reason = "OCR_LANGUAGE_MODEL"
    elif any(x in evidence for x in (b"bad_alloc", b"out of memory", b"cannot allocate memory")):
        reason = "MEMORY_ALLOCATION"
    elif b"prlimit:" in evidence:
        reason = "RESOURCE_LIMIT_SETUP"
    elif b"incorrect password" in evidence:
        reason = "PDF_PASSWORD"
    elif any(x in evidence for x in (b"may not be a pdf file", b"couldn't read xref table",
                                    b"couldn't find trailer dictionary")):
        reason = "INVALID_PDF"
    elif details.get("returncode", 0) < 0:
        reason = {-9: "SIGNAL_KILL", -11: "SIGNAL_SEGFAULT", -24: "CPU_LIMIT",
                  -25: "FILE_SIZE_LIMIT"}.get(details["returncode"], "SIGNAL")
    elif "errno" in details:
        reason = "OS_ERROR"
    elif "returncode" in details:
        reason = "TOOL_EXIT"
    else:
        reason = "OCR_ERROR"
    return {"reason": reason, **details}


def traced_command(original, emit):
    def execute(argv, directory, output, seconds, max_size):
        if argv[0] == "pdfinfo":
            stage, configured = "PDF_INFO", 3
        elif argv[0] == "pdftoppm":
            stage, configured = "PDF_RENDER", 6
        else:
            require(argv[0] == "tesseract" and Path(argv[1]).stem in STAGES, "UNEXPECTED_STAGE")
            stage, configured = STAGES[Path(argv[1]).stem]
        started = time.monotonic()
        real_run = subprocess.run
        with tempfile.TemporaryFile(dir=directory) as private_stderr:
            def capture(*args, **kwargs):
                require(kwargs.get("stderr") == subprocess.DEVNULL, "UNEXPECTED_STDERR")
                return real_run(*args, **{**kwargs, "stderr": private_stderr})
            subprocess.run = capture
            try:
                original(argv, directory, output, seconds, max_size)
            except Exception as exc:
                private_stderr.seek(0)
                emit("STAGE", stage=stage, status="FAIL", limit_seconds=seconds,
                     configured_seconds=configured, elapsed_seconds=round(time.monotonic()-started, 3),
                     **exception_details(exc, private_stderr.read(65536)))
                raise
            else:
                emit("STAGE", stage=stage, status="PASS", limit_seconds=seconds,
                     configured_seconds=configured, elapsed_seconds=round(time.monotonic()-started, 3))
            finally:
                subprocess.run = real_run
    return execute


def diagnose(emit):
    account, group, limits = service_identity_and_limits()
    emit("SERVICE_LIMITS", **limits)
    drop_privileges(account, group)
    emit("IDENTITY", user="azadmin", group="www-data", service_cgroup="NOT_INHERITED")
    with existing_ocr_lock(), tempfile.TemporaryDirectory(prefix="az-ocr-diagnostic-", dir="/tmp") as scratch:
        raw = read_original()
        emit("ORIGINAL", status="PASS", document=DOCUMENT_ID, page=PAGE, bytes=len(raw))
        core = load_pinned_modules()
        emit("RUNTIME_PINS", status="PASS", files=len(PINS))
        original = core._command
        core._command = traced_command(original, emit)
        old_tempdir = tempfile.tempdir
        tempfile.tempdir = scratch
        started = time.monotonic()
        try:
            count, parsed = core.recognize_pdf_page(raw, PAGE, Path(scratch))
            emit("OCR_RESULT", status="PASS", page_count=count,
                 words=len(parsed.get("words", [])), rows=len(parsed.get("candidates", [])),
                 retryable=bool(parsed.get("recognition_retryable")),
                 issues=len(parsed.get("recognition_issues", [])),
                 elapsed_seconds=round(time.monotonic()-started, 3))
        except Exception as exc:
            emit("OCR_RESULT", status="FAIL", elapsed_seconds=round(time.monotonic()-started, 3),
                 **exception_details(exc))
        finally:
            core._command = original
            tempfile.tempdir = old_tempdir
    emit("READ_ONLY", status="PASS", database_writes="NONE", service_changes="NONE", code_changes="NONE")


def main():
    # All incidental Python/native-library output is suppressed. Only allowlisted
    # structured observations use the separately retained original stdout.
    with os.fdopen(os.dup(1), "w", buffering=1) as report, open(os.devnull, "w") as silent:
        def emit(event, **values):
            report.write(json.dumps({"event": event, **values}, ensure_ascii=True) + "\n")
        os.dup2(silent.fileno(), 1)
        os.dup2(silent.fileno(), 2)
        try:
            diagnose(emit)
        except Stop as exc:
            emit("STOP", reason=str(exc))
            return 1
        except Exception as exc:
            emit("STOP", status="DIAGNOSTIC_FAILED", **exception_details(exc))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
