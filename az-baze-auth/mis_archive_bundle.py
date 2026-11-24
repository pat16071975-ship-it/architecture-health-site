#!/usr/bin/env python3
"""Private offline archival of a reviewed IDENT source pack. Never reimport."""
import argparse
import hashlib
import json
import os
import posixpath
import sqlite3
import stat
import sys
from pathlib import Path
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

import mis_source_archive as archive

MAX_BUNDLE = 100 * 1024 * 1024
MAX_FILES = 1000
MAX_XLSX_MEMBERS = 2048
MAX_XLSX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_XLSX_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_EXPANDED_XLSX_BYTES = 512 * 1024 * 1024
XML_NODE_LIMIT = 500_000
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
WORKSHEET_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
TYPE_MAP = {
    "service_prices_discounts": "services",
    "paid_and_debt": "paid_services",
    "cash_operations": "cash",
    "completed_visits": "completed",
}


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()



def _tag(namespace, local):
    return "{" + namespace + "}" + local


class _SafeXML(ET.TreeBuilder):
    """Bound node count/depth and forbid DTDs, including UTF-16 XML."""
    def __init__(self):
        super().__init__()
        self.nodes = 0
        self.depth = 0

    def start(self, tag, attrs):
        self.nodes += 1
        self.depth += 1
        if self.nodes > XML_NODE_LIMIT or self.depth > 128:
            raise ValueError("XLSX XML structure limit exceeded")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)

    def doctype(self, name, pubid, system):
        raise ValueError("XLSX XML DTD forbidden")


def _relationships(root):
    if root.tag != _tag(PACKAGE_REL_NS, "Relationships"):
        raise ValueError("Invalid XLSX relationships XML")
    links = {}
    for row in root:
        if row.tag != _tag(PACKAGE_REL_NS, "Relationship"):
            raise ValueError("Invalid XLSX relationship")
        key, target, kind = row.get("Id"), row.get("Target"), row.get("Type")
        if not key or key in links or not target or not kind:
            raise ValueError("Invalid XLSX relationship attributes")
        links[key] = row
    return links


def _internal_target(link, directory):
    target = link.get("Target", "")
    if (link.get("TargetMode", "Internal") != "Internal" or not target
            or "\\" in target or ":" in target or "?" in target or "#" in target
            or "%" in target):
        raise ValueError("Invalid XLSX internal relationship target")
    path = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                              else posixpath.join(directory, target))
    if path in (".", "..") or path.startswith("../"):
        raise ValueError("Invalid XLSX internal relationship path")
    return path


def validate_xlsx(raw):
    """Read every inner member to EOF (CRC), parse all XML, verify workbook
    and worksheet structure. Bounded, no extraction or formula evaluation.
    This is a structural check, NOT a clinical/financial content audit.
    Return actual expanded byte count for aggregate pack limits.
    """
    if not raw.startswith(b"PK"):
        raise ValueError("Expected an original XLSX container")
    try:
        with ZipFile(BytesIO(raw)) as workbook:
            infos = workbook.infolist()
            if not infos or len(infos) > MAX_XLSX_MEMBERS:
                raise ValueError("XLSX member count limit exceeded")
            names = set()
            expanded = 0
            core_names = {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                          "xl/_rels/workbook.xml.rels"}
            roots, worksheet_roots = {}, {}
            for info in infos:
                name = info.filename
                parts = name.rstrip("/").split("/")
                key = name.casefold()
                if (not name or key in names or name.startswith("/") or "\\" in name
                        or ":" in name or any(part in ("", ".", "..") for part in parts)
                        or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1
                        or info.file_size > MAX_XLSX_MEMBER_BYTES):
                    raise ValueError("Invalid XLSX member or size")
                names.add(key)
                if info.is_dir():
                    if info.file_size:
                        raise ValueError("Invalid XLSX directory")
                    continue
                expanded += info.file_size
                if expanded > MAX_XLSX_BYTES:
                    raise ValueError("XLSX expanded size limit exceeded")
                parser = (ET.XMLParser(target=_SafeXML())
                          if name.lower().endswith((".xml", ".rels")) else None)
                actual = 0
                with workbook.open(info) as stream:
                    while True:
                        chunk = stream.read(64 * 1024)
                        if not chunk:
                            break
                        actual += len(chunk)
                        if actual > info.file_size or actual > MAX_XLSX_MEMBER_BYTES:
                            raise ValueError("XLSX expanded member size mismatch")
                        if parser is not None:
                            parser.feed(chunk)
                if actual != info.file_size:
                    raise ValueError("XLSX expanded member size mismatch")
                if parser is not None:
                    root = parser.close()
                    if name in core_names:
                        roots[name] = root
                    # Keep only the structural result for a potential sheet.
                    worksheet_roots[name] = (root.tag == _tag(SHEET_NS, "worksheet")
                                             and root.find(_tag(SHEET_NS, "sheetData")) is not None)
            if set(roots) != core_names:
                raise ValueError("Missing XLSX core parts")
            types = roots["[Content_Types].xml"]
            if types.tag != _tag(CT_NS, "Types"):
                raise ValueError("Invalid XLSX content types")
            overrides = {}
            for row in types.findall(_tag(CT_NS, "Override")):
                name, content_type = row.get("PartName", ""), row.get("ContentType")
                if not name.startswith("/") or name in overrides or not content_type:
                    raise ValueError("Invalid XLSX part type")
                overrides[name] = content_type
            if overrides.get("/xl/workbook.xml") != XLSX_CT:
                raise ValueError("Invalid XLSX workbook content type")
            package_links = _relationships(roots["_rels/.rels"])
            office = [row for row in package_links.values()
                      if row.get("Type") == OFFICE_REL_NS + "/officeDocument"]
            if len(office) != 1 or _internal_target(office[0], "") != "xl/workbook.xml":
                raise ValueError("Invalid XLSX package workbook relationship")
            document = roots["xl/workbook.xml"]
            if document.tag != _tag(SHEET_NS, "workbook"):
                raise ValueError("Invalid XLSX workbook XML")
            sheets = document.find(_tag(SHEET_NS, "sheets"))
            links = _relationships(roots["xl/_rels/workbook.xml.rels"])
            if sheets is None or not len(sheets):
                raise ValueError("XLSX has no worksheets")
            seen_ids, seen_targets = set(), set()
            for sheet in sheets:
                key = sheet.get(_tag(OFFICE_REL_NS, "id"))
                if (sheet.tag != _tag(SHEET_NS, "sheet") or not sheet.get("name")
                        or not key or key in seen_ids or key not in links):
                    raise ValueError("Invalid XLSX sheet declaration")
                seen_ids.add(key)
                link = links[key]
                target = _internal_target(link, "xl")
                if (link.get("Type") != OFFICE_REL_NS + "/worksheet"
                        or target in seen_targets
                        or overrides.get("/" + target) != WORKSHEET_CT
                        or not worksheet_roots.get(target)):
                    raise ValueError("Invalid XLSX worksheet relationship or content")
                seen_targets.add(target)
            return expanded
    except (BadZipFile, ET.ParseError, RuntimeError, NotImplementedError, OSError, EOFError) as exc:
        raise ValueError("Invalid XLSX inner ZIP/XML") from exc


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
        expanded_xlsx = 0
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
            expanded_xlsx += validate_xlsx(raw)
            if expanded_xlsx > MAX_BUNDLE_EXPANDED_XLSX_BYTES:
                raise ValueError("Bundle expanded XLSX size limit exceeded")
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
