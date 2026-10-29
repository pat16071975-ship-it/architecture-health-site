"""Read-only reporting periods for explicitly confirmed historic iDent exports.

Service-row bounds are not a declaration of the export's calendar period.
Never infer a whole month from min/max dates, a filename, or upload time.
A separate, private confirmation binds a month to one exact stored upload.
Replacing that upload invalidates the confirmation automatically.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

BASENAME = "paid-period-confirmations-v1.json"
MONTH_RE = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
KINDS = {"snapshot", "period"}
MAX_CONFIG_BYTES = 1_000_000


def _date(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Invalid reporting date")
    date.fromisoformat(value)
    return value


def month_bounds(month: str) -> tuple[str, str]:
    if not isinstance(month, str) or not MONTH_RE.fullmatch(month):
        raise ValueError("Invalid reporting month")
    year, number = map(int, month.split("-"))
    last = calendar.monthrange(year, number)[1]
    return _date(month + "-01"), _date(f"{month}-{last:02d}")


def validate_config(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Invalid paid-period confirmation version")
    entries = value.get("confirmations")
    if not isinstance(entries, list) or len(entries) > 1200:
        raise ValueError("Invalid paid-period confirmations")
    result, seen_months, seen_sources = [], set(), set()
    fields = {"kind", "sourceFrom", "sourceTo", "sourceSha256", "payloadSha256", "importedAt", "reportMonth"}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ValueError("Invalid paid-period confirmation fields")
        if not all(isinstance(entry[k], str) for k in fields):
            raise ValueError("Invalid paid-period confirmation types")
        start, end = _date(entry["sourceFrom"]), _date(entry["sourceTo"])
        month = entry["reportMonth"]
        first, last = month_bounds(month)
        if entry["kind"] not in KINDS or not first <= start <= end <= last:
            raise ValueError("Confirmation must belong to one calendar month")
        if entry["kind"] == "snapshot" and start != first:
            raise ValueError("Snapshot source start must match its stored month")
        if not all(SHA_RE.fullmatch(entry[k]) for k in ("sourceSha256", "payloadSha256")):
            raise ValueError("Confirmation requires exact source and payload SHA256")
        if not entry["importedAt"] or len(entry["importedAt"]) > 100:
            raise ValueError("Confirmation requires an exact import stamp")
        key = (entry["kind"], start, end)
        if month in seen_months or key in seen_sources:
            raise ValueError("Ambiguous duplicate monthly confirmation")
        seen_months.add(month)
        seen_sources.add(key)
        result.append(dict(entry))
    return result


def load_config(db_path: str | Path) -> list[dict[str, str]]:
    """No file or directory creation; configuration is outside the public site."""
    path = Path(db_path).parent / BASENAME
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return []
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("Paid-period confirmation file is too large")
    return validate_config(json.loads(raw))


def source_identity(source: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    return (source["kind"], source["sourceFrom"], source["sourceTo"],
            source["sourceSha256"], source["payloadSha256"], source["importedAt"])


def read_sources(conn, confirmations=()) -> list[dict[str, Any]]:
    """SELECT only, even when the tables are absent. Never import app/schema code."""
    declarations = validate_config({"version": 1, "confirmations": list(confirmations)})
    approved = {
        (c["kind"], c["sourceFrom"], c["sourceTo"], c["sourceSha256"],
         c["payloadSha256"], c["importedAt"]): c["reportMonth"]
        for c in declarations
    }
    result = []
    queries = (
        ("snapshot", "service_payment_snapshots", "month || '-01'", "as_of_date"),
        ("period", "service_payment_periods", "period_start", "period_end"),
    )
    for kind, table, start_column, end_column in queries:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
        hash_column = 'source_sha256' if 'source_sha256' in columns else "''"
        stamp_column = 'imported_at' if 'imported_at' in columns else "''"
        rows = conn.execute(
            f'SELECT {start_column}, {end_column}, payload_json, {hash_column}, {stamp_column} '
            f'FROM "{table}" ORDER BY 1,2'
        )
        for row in rows:
            start, end, raw, source_sha, imported_at = tuple(row)
            try:
                start, end = _date(start), _date(end)
                if not isinstance(raw, str):
                    continue
                payload = json.loads(raw)
                if start > end or not isinstance(payload, dict):
                    continue
                if kind == "snapshot":
                    if payload.get("version") not in (1, 2) or start[:7] != end[:7]:
                        continue
                    if payload.get("month", start[:7]) != start[:7] or payload.get("as_of_date", end) != end:
                        continue
                elif (payload.get("version") != 3 or payload.get("period_start") != start
                      or payload.get("period_end") != end):
                    continue
                if not isinstance(payload.get("providers"), dict) or any(
                        not isinstance(v, dict) for v in payload["providers"].values()):
                    continue
            except (ValueError, TypeError):
                continue
            source = {
                "kind": kind, "sourceFrom": start, "sourceTo": end,
                "sourceSha256": str(source_sha or ""),
                "payloadSha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "importedAt": str(imported_at or ""),
                "from": start, "to": end, "payload": payload,
                "confirmedMonth": None,
            }
            month = approved.get(source_identity(source))
            if month:
                # The owner confirms the exported period, not imaginary rows/payments.
                source["from"], source["to"] = month_bounds(month)
                source["confirmedMonth"] = month
            result.append(source)
    # A genuine calendar-boundary upload outranks a normalized historic alias.
    # Do not sum both representations or replace a newer exact-period source.
    native = {(s["from"], s["to"]) for s in result if not s["confirmedMonth"]}
    result = [s for s in result if not s["confirmedMonth"] or (s["from"], s["to"]) not in native
              or (s["from"], s["to"]) == (s["sourceFrom"], s["sourceTo"])]
    return sorted(result, key=lambda s: (s["kind"] == "period", s["from"], s["to"]))


def make_confirmation(source: dict[str, Any], report_month: str) -> dict[str, str]:
    """Only for an explicitly selected/confirmed export; not auto-classification."""
    entry = {k: source[k] for k in (
        "kind", "sourceFrom", "sourceTo", "sourceSha256", "payloadSha256", "importedAt")}
    entry["reportMonth"] = report_month
    return validate_config({"version": 1, "confirmations": [entry]})[0]
