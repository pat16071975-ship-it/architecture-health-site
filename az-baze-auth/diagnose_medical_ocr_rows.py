#!/usr/bin/env python3
"""Read one saved medical OCR review, without invoking the application or OCR.

Only the approved PR271 parser is executed, from verified bytes. The single
database query cannot read original uploads, other documents or catalogues.
"""
import sys
sys.dont_write_bytecode = True

import argparse
import base64
import gzip
import hashlib
import json
import sqlite3
import types
from contextlib import closing
from pathlib import Path


APPROVED = {
    "medical_supplies_documents": "0e269427d75989a455a7a347346b933df665c776",
    "medical_supplies_ocr": "31a023ce834de537ab284042754c8e2e5a1e7e8c",
}
COLUMNS = ("id", "original_name", "sha256", "version", "fields_json")
ROW_FIELDS = ("source_line", "source_lines", "name", "unit", "quantity", "price",
              "value", "value_basis", "action", "folder_id", "manual")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def approved_parser(code_root):
    # Verify both modules before executing either, and execute those same bytes.
    # This does not add the live application directory to sys.path.
    sources = {}
    for name, expected in APPROVED.items():
        path = code_root / (name + ".py")
        require(path.is_file() and not path.is_symlink(), "APPROVED_MODULE_NOT_REGULAR")
        sources[name] = path.read_bytes()
        require(git_blob(sources[name]) == expected, "APPROVED_MODULE_HASH_MISMATCH: " + name)
    sys.dont_write_bytecode = True
    modules = {}
    for name, source in sources.items():
        module = types.ModuleType(name)
        module.__file__ = str(code_root / (name + ".py"))
        exec(compile(source, module.__file__, "exec"), module.__dict__)
        modules[name] = module
        if name == "medical_supplies_documents":
            sys.modules[name] = module
    return modules["medical_supplies_ocr"]


def read_document(database, document_id):
    require(database.is_file(), "DATABASE_NOT_FOUND")
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=10)) as conn:
        conn.execute("PRAGMA query_only=ON")
        require(conn.execute("PRAGMA query_only").fetchone()[0] == 1, "QUERY_ONLY_NOT_ENABLED")

        def authorizer(action, table, column, database_name, trigger):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if (action == sqlite3.SQLITE_READ and database_name == "main"
                    and table == "med_supply_documents" and column in COLUMNS):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        conn.set_authorizer(authorizer)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id,original_name,sha256,version,fields_json "
                           "FROM med_supply_documents WHERE id=?", (document_id,)).fetchone()
        require(row is not None, "DOCUMENT_NOT_FOUND")
        return dict(row)


def count(value):
    return len(value) if isinstance(value, list) else None


def rows(value):
    if value is None:
        return None
    require(isinstance(value, list) and all(isinstance(row, dict) for row in value),
            "INVALID_SAVED_ROWS")
    return [{key: row[key] for key in ROW_FIELDS if key in row} for row in value]


def report(document, parser, requested_page, all_pages=False):
    raw = document.pop("fields_json")
    require(isinstance(raw, str) and len(raw.encode("utf-8")) <= parser.MAX_JSON,
            "INVALID_SAVED_FIELDS")
    fields = json.loads(raw)
    require(isinstance(fields, dict), "INVALID_SAVED_FIELDS")
    state = fields.get(parser.KEY, {"page_count": None, "pages": {}})
    require(isinstance(state, dict) and isinstance(state.get("pages"), dict),
            "INVALID_OCR_STATE")
    pages = state["pages"]
    require(len(pages) <= parser.MAX_PAGES, "INVALID_PAGE_COUNT")
    for key, page in pages.items():
        require(isinstance(key, str) and key.isdigit() and 1 <= int(key) <= parser.MAX_PAGES
                and str(int(key)) == key and isinstance(page, dict), "INVALID_SAVED_PAGE")
    page_count = state.get("page_count")
    require(page_count is None or (type(page_count) is int and 1 <= page_count <= parser.MAX_PAGES),
            "INVALID_PAGE_COUNT")
    require(not requested_page or not page_count or requested_page <= page_count,
            "REQUESTED_PAGE_OUT_OF_RANGE")
    summaries = [{"page": int(key), "extractor_version": page.get("extractor_version"),
                  "words": count(page.get("words")), "lines": count(page.get("lines")),
                  "candidates": count(page.get("candidates")), "rows_present": "rows" in page,
                  "rows_count": count(page.get("rows")), "checked": page.get("checked", False),
                  "no_items": page.get("no_items", False)}
                 for key, page in sorted(pages.items(), key=lambda item: int(item[0]))]
    selected = requested_page or 1
    error = None
    # Match PR271's GET selection exactly, including an explicit empty rows list.
    if not requested_page:
        for number in range(1, (page_count or 0) + 1):
            saved = pages.get(str(number))
            if saved is None:
                continue
            try:
                proposal = parser.derive_page(saved)
                if proposal.get("rows", proposal["candidates"]):
                    selected = number
                    break
            except ValueError as exc:
                selected, error = number, str(exc)
                break
    def page_details(number, saved, error=None):
        derived = None
        if error is None:
            try:
                derived = parser.derive_page(saved)
            except ValueError as exc:
                error = str(exc)
        return {
            "page": number,
            "saved": {
                "extractor_version": saved.get("extractor_version"),
                "words": saved.get("words"), "lines": saved.get("lines"),
                "candidates": rows(saved.get("candidates")),
                "rows_present": "rows" in saved, "rows": rows(saved.get("rows")),
                "checked": saved.get("checked", False),
                "no_items": saved.get("no_items", False),
            },
            "derived_candidates": rows(derived["candidates"]) if derived is not None else None,
            "displayed_rows": rows(derived.get("rows", derived["candidates"])) if derived is not None else None,
            "display_source": "saved_rows" if "rows" in saved else "derived_candidates",
            "derive_error": error,
        }
    selected_saved = pages.get(str(selected))
    details = page_details(selected, selected_saved, error) if selected_saved is not None else None
    result = {"metadata": {**document, "page_count": page_count, "selected_page": selected,
                           "selection": "explicit" if requested_page else "route_default",
                           "approved_code_blobs": APPROVED},
              "pages": summaries, "selected_page": details}
    if all_pages:
        result["all_pages"] = [page_details(int(key), saved)
                               for key, saved in sorted(pages.items(), key=lambda item: int(item[0]))]
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--database", type=Path, default=Path("/var/lib/az-baze/auth.db"))
    cli.add_argument("--code-root", type=Path, default=Path("/opt/az-baze-auth"))
    cli.add_argument("--document", type=int, default=1)
    cli.add_argument("--page", type=int, default=0, help="0 follows the installed GET route")
    cli.add_argument("--all-pages", action="store_true", help="Include details of every saved page")
    cli.add_argument("--compact", action="store_true", help="Print gzip+base64 JSON to stdout")
    args = cli.parse_args()
    require(1 <= args.document <= 10**12 and 0 <= args.page <= 20, "INVALID_DOCUMENT_OR_PAGE")
    parser = approved_parser(args.code_root)
    result = report(read_document(args.database, args.document), parser, args.page, args.all_pages)
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    print("MEDICAL_OCR_DIAGNOSTIC_BEGIN")
    if args.compact:
        print("MEDICAL_OCR_ENCODING=GZIP_BASE64")
        print(base64.b64encode(gzip.compress(payload.encode("utf-8"), mtime=0)).decode("ascii"))
    else:
        print(payload)
    print("MEDICAL_OCR_DIAGNOSTIC_END")
    print("READ_ONLY=PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print("MEDICAL_OCR_DIAGNOSTIC_STOP=" + message, file=sys.stderr)
        sys.exit(1)
