#!/usr/bin/env python3
"""Private offline archival of a reviewed IDENT source pack. Never reimport."""
import argparse
import hashlib
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path
from zipfile import ZipFile, BadZipFile

import mis_source_archive as archive

MAX_BUNDLE = 100 * 1024 * 1024
MAX_FILES = 1000
TYPE_MAP = {
    "services_and_discounts": "services",
    "paid_and_debt": "paid_services",
    "cash_operations": "cash",
    "completed_visits": "completed",
}


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def read_bundle(path, expected_sha):
    """Check byte identity, sizes, aliases, and all ZIP member names.
    The manifest's coverage is catalog evidence, never an active selection.
    """
    if not archive.HEX.fullmatch(expected_sha):
        raise ValueError("Expected bundle SHA256 is required")
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BUNDLE:
        raise ValueError("Invalid bundle path or size")
    raw_bundle = path.read_bytes()
    if sha256(raw_bundle) != expected_sha:
        raise ValueError("Bundle SHA256 mismatch")
    from io import BytesIO
    with ZipFile(BytesIO(raw_bundle)) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if (len(names) != len(set(names)) or len(names) > MAX_FILES + 1
                or "manifest.json" not in names
                or sum(i.file_size for i in infos) > MAX_BUNDLE
                or any(i.is_dir() or stat.S_ISLNK(i.external_attr >> 16) for i in infos)):
            raise ValueError("Invalid bundle directory")
        raw_manifest = z.read("manifest.json")
        if len(raw_manifest) > archive.MAX_SOURCE_BYTES:
            raise ValueError("Manifest too large")
        m = json.loads(raw_manifest)
        if not isinstance(m, dict) or m.get("version") != 1 or not isinstance(m.get("files"), list):
            raise ValueError("Unknown catalog format")
        files = m["files"]
        if not files or len(files) > MAX_FILES or len(files) != m.get("unique_source_count"):
            raise ValueError("Invalid catalog file count")
        sources, expected_names, alias_count, bytes_count = [], {"manifest.json"}, 0, 0
        seen_sha = set()
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("Invalid catalog entry")
            sha = item.get("sha256")
            if not isinstance(sha, str) or not archive.HEX.fullmatch(sha) or sha in seen_sha:
                raise ValueError("Duplicate or invalid catalog SHA256")
            seen_sha.add(sha)
            member = "originals/" + sha + ".xlsx"
            if item.get("path") != member or member not in names:
                raise ValueError("Invalid original member")
            expected_names.add(member)
            size = item.get("size_bytes")
            if type(size) is not int or not 0 < size <= archive.MAX_SOURCE_BYTES:
                raise ValueError("Invalid original size")
            if z.getinfo(member).file_size != size:
                raise ValueError("ZIP original size mismatch")
            raw = z.read(member)
            if len(raw) != size or sha256(raw) != sha:
                raise ValueError("Original SHA256 mismatch")
            if not raw.startswith(b"PK"):
                raise ValueError("Expected an original XLSX container")
            with ZipFile(BytesIO(raw)) as workbook:
                if "xl/workbook.xml" not in workbook.namelist() or "[Content_Types].xml" not in workbook.namelist():
                    raise ValueError("Invalid workbook container")
            kind = TYPE_MAP.get(item.get("type"))
            if kind is None:
                raise ValueError("Unknown original source type")
            start = archive._day(item.get("observed_from"))
            end = archive._day(item.get("observed_to"))
            if start > end:
                raise ValueError("Invalid observed bounds")
            aliases = item.get("original_names")
            if not isinstance(aliases, list) or not aliases:
                raise ValueError("Missing original names")
            clean_aliases = set()
            for alias in aliases:
                if not isinstance(alias, dict):
                    raise ValueError("Invalid original alias")
                filename, original_zip = alias.get("filename"), alias.get("archive")
                if not isinstance(filename,str) or not filename or not isinstance(original_zip,str) or not original_zip:
                    raise ValueError("Invalid original alias")
                clean_aliases.add((original_zip,filename))
            if len(clean_aliases) != len(aliases):
                raise ValueError("Duplicate alias in catalog")
            alias_count += len(aliases)
            bytes_count += size
            sources.append((kind,raw,sha,start,end,aliases,item.get("selection")))
        if set(names) != expected_names or alias_count != m.get("source_count") or bytes_count != m.get("raw_unique_bytes"):
            raise ValueError("Bundle membership or catalog counts mismatch")
        if m.get("duplicate_occurrences") != alias_count-len(files):
            raise ValueError("Duplicate count mismatch")
        return raw_manifest, sources, {
            "originals":len(sources), "occurrences":alias_count, "raw_bytes":bytes_count,
            "bundle_sha256":expected_sha,
        }


def archive_bundle(conn, verified):
    """Only archive objects/events in one transaction; no report import."""
    raw_manifest, sources, info = verified
    if conn.in_transaction:
        raise RuntimeError("Historical archive requires clean transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for kind, raw, sha, start, end, aliases, selection in sources:
            for alias in aliases:
                archive.record(
                    conn, kind, raw, alias["filename"], expected_sha=sha,
                    source_from=start, source_to=end, origin="verified_historical_pack",
                    outcome="archive_only", decision="archive_only",
                    context={"bundle_sha256":info["bundle_sha256"],
                             "interval_basis":"observed_bounds",
                             "original_archive":alias["archive"],
                             "catalog_selection":selection},
                )
        archive.record(
            conn,"catalog",raw_manifest,"manifest.json",
            origin="verified_historical_pack",outcome="archive_only",
            decision="archive_only",context={"bundle_sha256":info["bundle_sha256"]},
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return info


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--sha256",required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify",action="store_true")
    mode.add_argument("--apply",action="store_true")
    p.add_argument("--database",type=Path)
    args = p.parse_args()
    os.umask(0o077)
    verified = read_bundle(args.bundle,args.sha256)
    print("MIS_ARCHIVE_BUNDLE=PASS; "+json.dumps(verified[2],sort_keys=True),flush=True)
    if args.verify:
        print("READ_ONLY=PASS; BUSINESS_WRITES=NONE",flush=True)
        return
    if args.database is None or args.database.is_symlink() or not args.database.is_file():
        raise ValueError("An existing SQLite database path is required")
    conn = sqlite3.connect(args.database.resolve().as_uri()+"?mode=rw",uri=True,timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Database integrity check failed")
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_data'").fetchone():
            raise ValueError("Expected AZ BAZE database")
        archive_bundle(conn,verified)
        # Read back exact archived originals without printing their contents.
        for _kind, raw, sha, *_rest in verified[1]:
            if archive.read_original(conn,sha) != raw:
                raise RuntimeError("Archive readback mismatch")
        print("MIS_HISTORY_ARCHIVE=PASS; BUSINESS_WRITES=NONE; ACTIVE_VERSIONS_UNCHANGED",flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, BadZipFile, OSError, sqlite3.Error) as exc:
        print("MIS_ARCHIVE_STOP="+type(exc).__name__+": "+str(exc),file=sys.stderr)
        sys.exit(1)
