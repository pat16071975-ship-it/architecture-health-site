#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import advertising_report as report
from app import DB_PATH

TARGET_DAYS = (
    "2026-10-27",
    "2026-10-28",
    "2026-10-29",
    "2026-10-30",
)


def _goal_totals_from_db(conn, day):
    rows = conn.execute(
        """
        SELECT goal_key,SUM(goal_visits) AS goal_visits,SUM(reaches) AS reaches
        FROM advertising_metrika_goal_daily
        WHERE provider='metrika' AND metric_date=?
        GROUP BY goal_key
        ORDER BY goal_key
        """,
        (day,),
    ).fetchall()
    return {
        str(row["goal_key"]): {
            "goal_visits": int(row["goal_visits"] or 0),
            "reaches": int(row["reaches"] or 0),
        }
        for row in rows
    }


def _goal_totals_from_parsed(parsed, day):
    result = {}
    for key, values in parsed["goal_rows"].items():
        if key[0] != day:
            continue
        goal_key = str(key[6])
        bucket = result.setdefault(goal_key, {"goal_visits": 0, "reaches": 0})
        bucket["goal_visits"] += int(values["goal_visits"])
        bucket["reaches"] += int(values["reaches"])
    return dict(sorted(result.items()))


def _current_archive_for_day(conn, day):
    import_ids = [
        str(row["import_id"])
        for row in conn.execute(
            """
            SELECT DISTINCT import_id
            FROM advertising_traffic_daily
            WHERE provider='metrika' AND metric_date=?
            ORDER BY import_id
            """,
            (day,),
        ).fetchall()
    ]
    if len(import_ids) != 1:
        raise RuntimeError(f"{day}: expected exactly one current Metrika import_id, got {import_ids!r}")
    import_id = import_ids[0]
    prefix = "metrika-"
    if not import_id.startswith(prefix) or len(import_id) <= len(prefix):
        raise RuntimeError(f"{day}: unexpected import_id {import_id!r}")
    sha_prefix = import_id[len(prefix):]

    candidates = conn.execute(
        """
        SELECT payload_sha256,original_filename,completed_days_json,archive_path
        FROM advertising_metrika_imports
        WHERE payload_sha256 LIKE ?
        ORDER BY payload_sha256
        """,
        (sha_prefix + "%",),
    ).fetchall()
    selected = []
    for row in candidates:
        try:
            completed_days = json.loads(row["completed_days_json"])
        except (TypeError, ValueError):
            continue
        if day in completed_days:
            selected.append((row, completed_days))
    if len(selected) != 1:
        raise RuntimeError(
            f"{day}: current import {import_id!r} does not resolve to one archived payload"
        )
    row, completed_days = selected[0]
    if completed_days != [day]:
        raise RuntimeError(
            f"{day}: archived payload covers {completed_days!r}; proposal refuses multi-day replay"
        )

    archive_path = Path(str(row["archive_path"]))
    if not archive_path.is_file() or archive_path.is_symlink():
        raise RuntimeError(f"{day}: archive path is unavailable or unsafe: {archive_path}")
    raw = archive_path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    expected_sha = str(row["payload_sha256"])
    if actual_sha != expected_sha:
        raise RuntimeError(f"{day}: archive SHA256 mismatch")
    return {
        "payload_sha256": expected_sha,
        "original_filename": str(row["original_filename"]),
        "archive_path": str(archive_path),
        "raw": raw,
    }


def build_plan(conn):
    plan = []
    for day in TARGET_DAYS:
        source = _current_archive_for_day(conn, day)
        parsed = report._parse_metrika_zip(source["raw"], source["original_filename"])
        if parsed["completed_days"] != [day]:
            raise RuntimeError(f"{day}: parsed completed days drifted: {parsed['completed_days']!r}")

        current_visits = conn.execute(
            """
            SELECT SUM(visits)
            FROM advertising_traffic_daily
            WHERE provider='metrika' AND metric_date=?
            """,
            (day,),
        ).fetchone()[0]
        current_visits = int(current_visits or 0)
        expected_visits = sum(
            int(values["visits"])
            for key, values in parsed["traffic_rows"].items()
            if key[0] == day
        )
        if current_visits != expected_visits:
            raise RuntimeError(
                f"{day}: visit invariant failed current={current_visits} expected={expected_visits}"
            )

        current_goals = _goal_totals_from_db(conn, day)
        expected_goals = _goal_totals_from_parsed(parsed, day)
        if current_goals != expected_goals:
            raise RuntimeError(
                f"{day}: goal invariant failed current={current_goals!r} expected={expected_goals!r}"
            )

        current_rows = conn.execute(
            """
            SELECT COUNT(*)
            FROM advertising_traffic_daily
            WHERE provider='metrika' AND metric_date=?
            """,
            (day,),
        ).fetchone()[0]
        expected_rows = sum(1 for key in parsed["traffic_rows"] if key[0] == day)
        plan.append(
            {
                "day": day,
                "payload_sha256": source["payload_sha256"],
                "original_filename": source["original_filename"],
                "archive_path": source["archive_path"],
                "visits": current_visits,
                "goal_totals": current_goals,
                "current_traffic_rows": int(current_rows or 0),
                "canonical_traffic_rows": expected_rows,
                "reclassification_required": not report._stored_metrika_matches(conn, parsed),
            }
        )
    return plan


def main():
    parser = argparse.ArgumentParser(
        description="READ-ONLY proposal for canonical replay of archived Metrika ZIPs, 27-30.10.2026."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    uri = "file:" + str(Path(args.db)) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        plan = build_plan(conn)
        for item in plan:
            print(json.dumps(item, ensure_ascii=False, sort_keys=True))
        print("METRIKA_HISTORY_RECLASS_PREFLIGHT=PASS; LIVE_WRITES=NONE")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
