#!/usr/bin/env python3
import argparse
import json
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DECISION = "period-boundary-repair-2026"


def _valid_date(value):
    text = str(value or "")
    if not DATE_RE.fullmatch(text):
        return None
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


def _payload(raw):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def payload_event_dates(payload):
    """Return source dates still preserved in compact v1/v2 payloads."""
    dates = set()
    links = payload.get("visit_links")
    if isinstance(links, dict):
        for key in links:
            value = _valid_date(str(key).split("|", 1)[0])
            if value:
                dates.add(value)
    invoices = payload.get("invoices")
    if isinstance(invoices, list):
        for row in invoices:
            if isinstance(row, dict):
                value = _valid_date(row.get("date"))
                if value:
                    dates.add(value)
    return sorted(dates)


def _next_day(value):
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def analyze_snapshots(conn, start_date="2026-01-01"):
    rows = conn.execute(
        """
        SELECT as_of_date,month,payload_json,source_filename,source_sha256,
               imported_by,imported_at
        FROM service_payment_snapshots
        WHERE as_of_date>=?
        ORDER BY month,as_of_date
        """,
        (start_date,),
    ).fetchall()
    previous = {}
    result = []
    for row in rows:
        as_of = str(row["as_of_date"])
        month = str(row["month"])
        payload = _payload(row["payload_json"])
        entry = {
            "as_of": as_of,
            "month": month,
            "source_filename": str(row["source_filename"] or ""),
            "source_sha256": str(row["source_sha256"] or ""),
            "imported_by": row["imported_by"],
            "imported_at": str(row["imported_at"] or ""),
            "payload": payload,
            "event_start": None,
            "event_end": None,
            "previous_as_of": previous.get(month),
            "status": "",
            "reason": "",
        }
        if not _valid_date(as_of) or month != as_of[:7]:
            entry["status"] = "invalid"
            entry["reason"] = "snapshot date/month mismatch"
            result.append(entry)
            previous[month] = as_of
            continue
        if not payload or payload.get("version") not in (1, 2):
            entry["status"] = "invalid"
            entry["reason"] = "unsupported compact payload"
            result.append(entry)
            previous[month] = as_of
            continue

        dates = [value for value in payload_event_dates(payload) if value <= as_of]
        if not dates:
            entry["status"] = "ambiguous_no_dates"
            entry["reason"] = "compact payload has no preserved event dates"
            result.append(entry)
            previous[month] = as_of
            continue

        first, last = dates[0], dates[-1]
        entry["event_start"] = first
        entry["event_end"] = last
        month_start = month + "-01"
        prev = previous.get(month)

        if first <= month_start:
            entry["status"] = "keep_snapshot"
            entry["reason"] = "source starts at calendar-month boundary"
        elif prev and first <= prev:
            entry["status"] = "keep_snapshot"
            entry["reason"] = "source overlaps earlier snapshot and behaves cumulatively"
        elif prev and first == _next_day(prev):
            entry["status"] = "convert_incremental"
            entry["reason"] = "source begins exactly after previous snapshot"
        elif prev and first > prev:
            entry["status"] = "ambiguous_gap"
            entry["reason"] = "later source begins after previous snapshot with a gap"
        else:
            entry["status"] = "ambiguous_first_partial"
            entry["reason"] = "first snapshot in month begins after day 1"

        result.append(entry)
        previous[month] = as_of
    return result


def compact_period_payload(snapshot, start, end):
    value = dict(snapshot)
    value["version"] = 3
    value.pop("month", None)
    value.pop("as_of_date", None)
    value["period_start"] = start
    value["period_end"] = end
    return value


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def backup_database(conn, backup_dir):
    root = Path(backup_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    target = root / f"paid-period-boundary-repair-{stamp}.db"
    destination = sqlite3.connect(target)
    try:
        conn.backup(destination)
    finally:
        destination.close()
    return target


def apply_repairs(conn, plan, backup_dir):
    ambiguous = [
        row for row in plan
        if row["status"] in {"invalid", "ambiguous_no_dates", "ambiguous_gap"}
    ]
    if ambiguous:
        labels = ", ".join(f"{row['as_of']}:{row['status']}" for row in ambiguous)
        raise RuntimeError("Refusing apply while unsafe rows exist: " + labels)

    candidates = [row for row in plan if row["status"] == "convert_incremental"]
    conn.execute("BEGIN IMMEDIATE")
    try:
        locked_plan = analyze_snapshots(conn)
        locked_keys = {
            (row["as_of"], row["source_sha256"], row["event_start"], row["status"])
            for row in locked_plan if row["status"] == "convert_incremental"
        }
        expected_keys = {
            (row["as_of"], row["source_sha256"], row["event_start"], row["status"])
            for row in candidates
        }
        if locked_keys != expected_keys:
            raise RuntimeError("Paid-services state changed after preflight; rerun preflight.")

        backup = backup_database(conn, backup_dir)
        converted = []
        for row in candidates:
            start, end = row["event_start"], row["as_of"]
            payload = compact_period_payload(row["payload"], start, end)
            payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            existing = conn.execute(
                """
                SELECT payload_json,source_sha256
                FROM service_payment_periods
                WHERE period_start=? AND period_end=?
                """,
                (start, end),
            ).fetchone()
            if existing:
                existing_payload = _payload(existing["payload_json"])
                if not existing_payload or _canonical(existing_payload) != _canonical(payload):
                    raise RuntimeError(f"Conflicting saved period {start} — {end}.")
            else:
                conn.execute(
                    """
                    INSERT INTO service_payment_periods(
                        period_start,period_end,payload_json,source_filename,source_sha256,
                        imported_by,imported_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        start, end, payload_json, row["source_filename"], row["source_sha256"],
                        row["imported_by"], row["imported_at"],
                    ),
                )

            source = conn.execute(
                """
                SELECT as_of_date,month,payload_json,source_filename,source_sha256,
                       imported_by,imported_at
                FROM service_payment_snapshots
                WHERE as_of_date=?
                """,
                (row["as_of"],),
            ).fetchone()
            if not source or str(source["source_sha256"]) != row["source_sha256"]:
                raise RuntimeError(f"Snapshot {row['as_of']} changed during repair.")

            conn.execute(
                """
                INSERT INTO service_payment_versions(
                    as_of_date,month,payload_json,source_filename,source_sha256,
                    imported_by,imported_at,archived_by,archived_at,decision
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    source["as_of_date"], source["month"], source["payload_json"],
                    source["source_filename"], source["source_sha256"], source["imported_by"],
                    source["imported_at"], None, datetime.now().isoformat(timespec="seconds"), DECISION,
                ),
            )
            conn.execute("DELETE FROM service_payment_snapshots WHERE as_of_date=?", (row["as_of"],))
            converted.append((start, end, row["source_filename"]))

        conn.commit()
        return str(backup), converted
    except Exception:
        conn.rollback()
        raise


def print_plan(plan):
    counts = defaultdict(int)
    print("=== PAID PERIOD BOUNDARY PREFLIGHT ===")
    for row in plan:
        counts[row["status"]] += 1
        print(
            f"{row['as_of']} | {row['status']} | prev={row['previous_as_of'] or '-'} | "
            f"events={row['event_start'] or '-'}..{row['event_end'] or '-'} | "
            f"file={row['source_filename']}"
        )
    print("=== COUNTS ===")
    for key in sorted(counts):
        print(f"{key}={counts[key]}")
    print("WRITES=NONE")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/var/lib/az-baze/auth.db")
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--backup-dir", default="/var/lib/az-baze/backups")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.apply:
        conn = sqlite3.connect(args.db)
    else:
        db_path = Path(args.db).resolve()
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        plan = analyze_snapshots(conn, args.start_date)
        print_plan(plan)
        if not args.apply:
            return
        backup, converted = apply_repairs(conn, plan, args.backup_dir)
        print("=== APPLY ===")
        print(f"BACKUP={backup}")
        for start, end, filename in converted:
            print(f"CONVERTED={start}..{end} | {filename}")
        print(f"CONVERTED_COUNT={len(converted)}")
        print("PAID_PERIOD_BOUNDARY_REPAIR=PASS")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
