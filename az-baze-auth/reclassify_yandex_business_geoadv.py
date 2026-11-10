#!/usr/bin/env python3
import argparse
import json
import sqlite3
import sys
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import advertising_report as ar
from app import DB_PATH


CONFIRM_TOKEN = "RECLASSIFY-YANDEX-BUSINESS-GEOADV"
TARGET_SOURCE = "yandex_business"


def _legacy_metrika_source(row):
    """Exact pre-fix paid-source semantics for geoadv replay comparison."""
    traffic_id = str(row.get("traffic_source_id") or "").strip()
    if traffic_id != "ad":
        return ar._metrika_source(row)

    traffic_name = str(row.get("traffic_source_name") or "").strip()
    detail = str(row.get("source_detail") or "").strip()
    detail_id = str(row.get("source_detail_id") or "").strip()
    utm_source_raw = str(row.get("utm_source") or "").strip()
    utm_source = utm_source_raw.lower()
    direct_campaign_id = ar._meaningful_campaign_id(row.get("direct_campaign_id"))
    direct_campaign_name = ar._meaningful_campaign_name(row.get("direct_campaign_name"))

    if utm_source == "geoadv_direct":
        return "yandex_business", "Яндекс Бизнес", "Платная реклама"

    yandex_utm = utm_source in {
        "yandex", "yandex_direct", "yandex-direct", "yandexdirect", "ydirect",
    }
    if (
        direct_campaign_id
        or direct_campaign_name
        or any(
            ar._is_yandex_direct_label(value)
            for value in (detail_id, detail, traffic_name)
        )
        or yandex_utm
    ):
        return "yandex_ads", "Яндекс-реклама", "Платная реклама"
    if utm_source_raw:
        return ar._dynamic_source_identity(
            "paid", utm_source_raw, "Платная реклама", "Платная реклама"
        )
    if detail or detail_id:
        return ar._dynamic_source_identity(
            "paid",
            detail or detail_id,
            traffic_name or "Платная реклама",
            "Платная реклама",
        )
    return "paid_ads", traffic_name or "Платная реклама", "Платная реклама"


def _read_payload(raw):
    with zipfile.ZipFile(Path(raw)) as archive:
        return json.loads(archive.read("az_analytics.json").decode("utf-8"))


def _archive_has_affected_geoadv(path, selected_date=None):
    try:
        payload = _read_payload(path)
    except (OSError, zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    for row in payload.get("metrica_traffic_day") or []:
        day = str(row.get("date") or "")
        if selected_date and day != selected_date:
            continue
        if bool(row.get("is_test_tag")):
            continue
        if str(row.get("traffic_source_id") or "").strip() != "ad":
            continue
        source = str(row.get("utm_source") or "").strip()
        if ar._is_yandex_business_utm_source(source) and source.lower() != "geoadv_direct":
            return True
    return False


def _parse(raw, filename, campaign_evidence, legacy=False):
    original = ar._metrika_source
    if legacy:
        ar._metrika_source = _legacy_metrika_source
    try:
        return ar._parse_metrika_zip(
            Path(raw).read_bytes(),
            filename,
            now=datetime.now(ZoneInfo(ar.DEFAULT_TIMEZONE)),
            campaign_evidence=campaign_evidence,
        )
    finally:
        ar._metrika_source = original


def _traffic_day(parsed, day):
    return {
        key: int(values.get("visits") or 0)
        for key, values in (parsed.get("traffic_rows") or {}).items()
        if key[0] == day
    }


def _goal_day(parsed, day):
    return {
        key: {
            "goal_visits": int(values.get("goal_visits") or 0),
            "reaches": int(values.get("reaches") or 0),
        }
        for key, values in (parsed.get("goal_rows") or {}).items()
        if key[0] == day
    }


def _db_traffic_day(conn, day):
    result = {}
    for row in conn.execute(
        """
        SELECT metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,visits
        FROM advertising_traffic_daily
        WHERE provider='metrika' AND metric_date=?
        """,
        (day,),
    ).fetchall():
        key = (
            str(row["metric_date"]),
            str(row["source_key"]),
            str(row["source_name"]),
            str(row["channel_name"] or ""),
            str(row["campaign_id"] or ""),
            str(row["campaign_name"] or ""),
        )
        result[key] = result.get(key, 0) + int(row["visits"] or 0)
    return result


def _db_goal_day(conn, day):
    result = {}
    for row in conn.execute(
        """
        SELECT metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,
               goal_key,goal_name,goal_visits,reaches
        FROM advertising_metrika_goal_daily
        WHERE provider='metrika' AND metric_date=?
        """,
        (day,),
    ).fetchall():
        key = (
            str(row["metric_date"]),
            str(row["source_key"]),
            str(row["source_name"]),
            str(row["channel_name"] or ""),
            str(row["campaign_id"] or ""),
            str(row["campaign_name"] or ""),
            str(row["goal_key"]),
            str(row["goal_name"]),
        )
        values = result.setdefault(key, {"goal_visits": 0, "reaches": 0})
        values["goal_visits"] += int(row["goal_visits"] or 0)
        values["reaches"] += int(row["reaches"] or 0)
    return result


def _totals(traffic, goals):
    goal_totals = {}
    for key, values in goals.items():
        target = goal_totals.setdefault(
            key[6],
            {"goal_visits": 0, "reaches": 0},
        )
        target["goal_visits"] += int(values["goal_visits"])
        target["reaches"] += int(values["reaches"])

    return {
        "visits": sum(int(value) for value in traffic.values()),
        "yandex_ads": sum(
            int(value) for key, value in traffic.items()
            if key[1] == "yandex_ads"
        ),
        "yandex_ads_undefined": sum(
            int(value) for key, value in traffic.items()
            if key[1] == "yandex_ads" and not key[4] and not key[5]
        ),
        "yandex_business": sum(
            int(value) for key, value in traffic.items()
            if key[1] == TARGET_SOURCE
        ),
        "goals": goal_totals,
    }


def _affected_old_keys(payload, accepted_days, campaign_evidence):
    traffic = set()
    goals = set()
    traffic_rows = {}
    for row in payload.get("metrica_traffic_day") or []:
        day = str(row.get("date") or "")
        row_id = str(row.get("row_id") or "")
        traffic_rows[row_id] = row
        if day not in accepted_days or bool(row.get("is_test_tag")):
            continue
        if str(row.get("traffic_source_id") or "").strip() != "ad":
            continue
        source = str(row.get("utm_source") or "").strip()
        if (
            not ar._is_yandex_business_utm_source(source)
            or source.lower() == "geoadv_direct"
        ):
            continue
        source_key, source_name, channel = _legacy_metrika_source(row)
        identity = ar._metrika_campaign_identity(
            row,
            source_key,
            source_name,
            channel,
            campaign_evidence,
        )
        traffic.add((day, *identity))

    for row in payload.get("metrica_goal_day") or []:
        day = str(row.get("date") or "")
        if day not in accepted_days:
            continue
        source_row = traffic_rows.get(str(row.get("traffic_row_id") or ""))
        if not source_row or bool(source_row.get("is_test_tag")):
            continue
        source = str(source_row.get("utm_source") or "").strip()
        if (
            not ar._is_yandex_business_utm_source(source)
            or source.lower() == "geoadv_direct"
        ):
            continue
        goal_name = str(row.get("goal_name") or "")
        goal_key = ar.METRIKA_GOALS.get(goal_name)
        if not goal_key:
            continue
        source_key, source_name, channel = _legacy_metrika_source(source_row)
        identity = ar._metrika_campaign_identity(
            source_row,
            source_key,
            source_name,
            channel,
            campaign_evidence,
        )
        goals.add((day, *identity, goal_key, goal_name))
    return traffic, goals


def _safe_diff(old_traffic, new_traffic, old_goals, new_goals, affected_traffic, affected_goals):
    if _totals(old_traffic, old_goals)["visits"] != _totals(new_traffic, new_goals)["visits"]:
        return False, "visit total changed"
    if _totals(old_traffic, old_goals)["goals"] != _totals(new_traffic, new_goals)["goals"]:
        return False, "goal totals changed"

    traffic_keys = set(old_traffic) | set(new_traffic)
    for key in traffic_keys:
        if old_traffic.get(key, 0) == new_traffic.get(key, 0):
            continue
        if key[1] == TARGET_SOURCE:
            continue
        if key not in affected_traffic:
            return False, "non-geoadv traffic row changed: " + repr(key)

    goal_keys = set(old_goals) | set(new_goals)
    for key in goal_keys:
        if old_goals.get(key) == new_goals.get(key):
            continue
        if key[1] == TARGET_SOURCE:
            continue
        if key not in affected_goals:
            return False, "non-geoadv goal row changed: " + repr(key)

    return True, ""


def _insert_day(conn, day, traffic, goals, import_id):
    now = ar.iso_now()
    conn.execute(
        "DELETE FROM advertising_traffic_daily WHERE provider='metrika' AND metric_date=?",
        (day,),
    )
    conn.execute(
        "DELETE FROM advertising_metrika_goal_daily WHERE provider='metrika' AND metric_date=?",
        (day,),
    )
    for key, visits in traffic.items():
        (
            row_day,
            source_key,
            source_name,
            channel_name,
            campaign_id,
            campaign_name,
        ) = key
        conn.execute(
            """
            INSERT INTO advertising_traffic_daily(
                provider,metric_date,source_key,source_name,channel_name,
                campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id
            ) VALUES('metrika',?,?,?,?,?,?,NULL,NULL,?,?,?)
            """,
            (
                row_day,
                source_key,
                source_name,
                channel_name,
                campaign_id,
                campaign_name,
                int(visits),
                now,
                import_id,
            ),
        )
    for key, values in goals.items():
        (
            row_day,
            source_key,
            source_name,
            channel_name,
            campaign_id,
            campaign_name,
            goal_key,
            goal_name,
        ) = key
        conn.execute(
            """
            INSERT INTO advertising_metrika_goal_daily(
                provider,metric_date,source_key,source_name,channel_name,
                campaign_id,campaign_name,goal_key,goal_name,goal_visits,reaches,
                updated_at,import_id
            ) VALUES('metrika',?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row_day,
                source_key,
                source_name,
                channel_name,
                campaign_id,
                campaign_name,
                goal_key,
                goal_name,
                int(values["goal_visits"]),
                int(values["reaches"]),
                now,
                import_id,
            ),
        )


def _merge_totals(items):
    result = {
        "visits": 0,
        "yandex_ads": 0,
        "yandex_ads_undefined": 0,
        "yandex_business": 0,
        "goals": {},
    }
    for item in items:
        result["visits"] += item["visits"]
        result["yandex_ads"] += item["yandex_ads"]
        result["yandex_ads_undefined"] += item["yandex_ads_undefined"]
        result["yandex_business"] += item["yandex_business"]
        for key, values in item["goals"].items():
            target = result["goals"].setdefault(
                key,
                {"goal_visits": 0, "reaches": 0},
            )
            target["goal_visits"] += values["goal_visits"]
            target["reaches"] += values["reaches"]
    return result


def _print_comparison(before, after):
    print("=== BEFORE / AFTER ===")
    print(f"Yandex Ads visits: {before['yandex_ads']} -> {after['yandex_ads']}")
    print(
        "Yandex Ads undefined visits: "
        f"{before['yandex_ads_undefined']} -> {after['yandex_ads_undefined']}"
    )
    print(
        "Yandex Business visits: "
        f"{before['yandex_business']} -> {after['yandex_business']}"
    )
    print(f"All visits: {before['visits']} -> {after['visits']}")
    goal_keys = sorted(set(before["goals"]) | set(after["goals"]))
    for key in goal_keys:
        b = before["goals"].get(key, {"goal_visits": 0, "reaches": 0})
        a = after["goals"].get(key, {"goal_visits": 0, "reaches": 0})
        print(
            f"Goal {key}: visits {b['goal_visits']} -> {a['goal_visits']}; "
            f"reaches {b['reaches']} -> {a['reaches']}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--date", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    if args.apply and args.confirm != CONFIRM_TOKEN:
        raise SystemExit("STOP=BAD_CONFIRM_TOKEN")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        evidence = ar._campaign_evidence_map(conn)
        imports = conn.execute(
            """
            SELECT payload_sha256,original_filename,completed_days_json,archive_path,imported_at
            FROM advertising_metrika_imports
            ORDER BY imported_at,payload_sha256
            """
        ).fetchall()

        candidates = {}
        conflicts = []
        already = []
        archives_scanned = 0
        affected_archives = 0

        for record in imports:
            archives_scanned += 1
            archive_path = Path(str(record["archive_path"] or ""))
            if not archive_path.is_file():
                continue
            if not _archive_has_affected_geoadv(archive_path, args.date or None):
                continue
            affected_archives += 1

            raw_payload = _read_payload(archive_path)
            try:
                old_parsed = _parse(
                    archive_path,
                    str(record["original_filename"] or archive_path.name),
                    evidence,
                    legacy=True,
                )
                new_parsed = _parse(
                    archive_path,
                    str(record["original_filename"] or archive_path.name),
                    evidence,
                    legacy=False,
                )
            except ValueError as exc:
                conflicts.append((str(archive_path), "parse: " + str(exc)))
                continue

            accepted_days = set(old_parsed.get("completed_days") or [])
            affected_traffic, affected_goals = _affected_old_keys(
                raw_payload,
                accepted_days,
                evidence,
            )

            for day in sorted(accepted_days):
                if args.date and day != args.date:
                    continue
                if not any(key[0] == day for key in affected_traffic):
                    continue

                current_traffic = _db_traffic_day(conn, day)
                current_goals = _db_goal_day(conn, day)
                old_traffic = _traffic_day(old_parsed, day)
                old_goals = _goal_day(old_parsed, day)
                new_traffic = _traffic_day(new_parsed, day)
                new_goals = _goal_day(new_parsed, day)

                safe, reason = _safe_diff(
                    old_traffic,
                    new_traffic,
                    old_goals,
                    new_goals,
                    {key for key in affected_traffic if key[0] == day},
                    {key for key in affected_goals if key[0] == day},
                )
                if not safe:
                    conflicts.append((day, reason))
                    continue

                if current_traffic == new_traffic and current_goals == new_goals:
                    already.append(day)
                    continue
                if current_traffic != old_traffic or current_goals != old_goals:
                    conflicts.append(
                        (
                            day,
                            "stored rows do not match either exact pre-fix replay or new replay",
                        )
                    )
                    continue

                candidate = {
                    "day": day,
                    "sha": str(record["payload_sha256"]),
                    "import_id": "metrika-" + str(record["payload_sha256"])[:16],
                    "before_traffic": current_traffic,
                    "before_goals": current_goals,
                    "after_traffic": new_traffic,
                    "after_goals": new_goals,
                    "archive": str(archive_path),
                }
                previous = candidates.get(day)
                if previous and (
                    previous["after_traffic"] != candidate["after_traffic"]
                    or previous["after_goals"] != candidate["after_goals"]
                ):
                    conflicts.append((day, "multiple saved archives disagree for the same day"))
                    candidates.pop(day, None)
                    continue
                candidates[day] = candidate

        ready = [candidates[day] for day in sorted(candidates)]
        before = _merge_totals(
            [_totals(item["before_traffic"], item["before_goals"]) for item in ready]
        )
        after = _merge_totals(
            [_totals(item["after_traffic"], item["after_goals"]) for item in ready]
        )

        print(f"ARCHIVES_SCANNED={archives_scanned}")
        print(f"AFFECTED_ARCHIVES={affected_archives}")
        print("READY_DAYS=" + ",".join(item["day"] for item in ready))
        print("ALREADY_DAYS=" + ",".join(sorted(set(already))))
        for scope, reason in conflicts:
            print(f"CONFLICT={scope}: {reason}")
        _print_comparison(before, after)

        if before["visits"] != after["visits"]:
            raise SystemExit("STOP=VISIT_TOTAL_CHANGED")
        if before["goals"] != after["goals"]:
            raise SystemExit("STOP=GOAL_TOTAL_CHANGED")
        if conflicts:
            raise SystemExit("STOP=CONFLICTS_PRESENT")

        if not args.apply:
            print("MUTATION=NONE")
            print("GEOADV_RECLASSIFY_DRY_RUN=PASS")
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            for item in ready:
                _insert_day(
                    conn,
                    item["day"],
                    item["after_traffic"],
                    item["after_goals"],
                    item["import_id"],
                )
            for item in ready:
                if (
                    _db_traffic_day(conn, item["day"]) != item["after_traffic"]
                    or _db_goal_day(conn, item["day"]) != item["after_goals"]
                ):
                    raise RuntimeError("post-write verification failed: " + item["day"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        print("GEOADV_RECLASSIFY_APPLY=PASS")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
