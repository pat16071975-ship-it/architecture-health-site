#!/usr/bin/env python3
import io
import json
import sqlite3
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import advertising_report as ar
import metrika_auto
from app import DB_PATH
from reclassify_yandex_business_geoadv import (
    _affected_old_keys,
    _goal_day,
    _legacy_metrika_source,
    _safe_diff,
    _totals,
    _traffic_day,
)


def _parse_bytes(raw, filename, evidence, now, legacy=False):
    original = ar._metrika_source
    if legacy:
        ar._metrika_source = _legacy_metrika_source
    try:
        return ar._parse_metrika_zip(
            raw,
            filename,
            allow_provisional=True,
            now=now,
            campaign_evidence=evidence,
        )
    finally:
        ar._metrika_source = original


def main():
    token = metrika_auto.token_from_env()
    if not token:
        raise SystemExit("STOP=METRIKA_TOKEN_MISSING")

    now = datetime.now(ZoneInfo(ar.DEFAULT_TIMEZONE))
    day = metrika_auto.current_local_day(now)

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        evidence = ar._campaign_evidence_map(conn)
    finally:
        conn.close()

    client = metrika_auto.MetrikaClient(token)
    raw, filename = client.fetch_day_archive(
        day,
        completed=False,
        diagnostics=False,
    )

    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        payload = json.loads(archive.read("az_analytics.json").decode("utf-8"))

    old = _parse_bytes(raw, filename, evidence, now, legacy=True)
    new = _parse_bytes(raw, filename, evidence, now, legacy=False)

    day_s = day.isoformat()
    if old.get("provisional_days") != [day_s] or new.get("provisional_days") != [day_s]:
        raise SystemExit("STOP=UNEXPECTED_CURRENT_DAY_SCOPE")

    old_traffic = _traffic_day(old, day_s)
    old_goals = _goal_day(old, day_s)
    new_traffic = _traffic_day(new, day_s)
    new_goals = _goal_day(new, day_s)

    affected_traffic, affected_goals = _affected_old_keys(
        payload,
        {day_s},
        evidence,
    )
    safe, reason = _safe_diff(
        old_traffic,
        new_traffic,
        old_goals,
        new_goals,
        affected_traffic,
        affected_goals,
    )
    if not safe:
        raise SystemExit("STOP=UNSAFE_DIFF:" + reason)

    geoadv_rows = []
    for row in payload.get("metrica_traffic_day") or []:
        if str(row.get("date") or "") != day_s or bool(row.get("is_test_tag")):
            continue
        source = str(row.get("utm_source") or "").strip()
        if not ar._is_yandex_business_utm_source(source):
            continue
        geoadv_rows.append(
            {
                "utm_source": source,
                "visits": int(row.get("visits") or 0),
                "direct_campaign_id": str(row.get("direct_campaign_id") or ""),
                "direct_campaign_name": str(row.get("direct_campaign_name") or ""),
            }
        )

    before = _totals(old_traffic, old_goals)
    after = _totals(new_traffic, new_goals)

    print("=== CURRENT DAY GEOADV READ-ONLY ===")
    print("DATE=" + day_s)
    print("COUNTER_ID=" + ar.METRIKA_COUNTER_ID)
    print("TIMEZONE=" + ar.DEFAULT_TIMEZONE)
    print("RAW_FILENAME=" + filename)
    print("RAW_BYTES=" + str(len(raw)))
    print("GEOADV_ROWS=" + str(len(geoadv_rows)))
    print("GEOADV_VISITS=" + str(sum(row["visits"] for row in geoadv_rows)))
    print(
        "GEOADV_SEARCH_YABS_VISITS="
        + str(
            sum(
                row["visits"]
                for row in geoadv_rows
                if row["utm_source"].strip().lower() == "geoadv_search_yabs"
            )
        )
    )
    campaign_ids = sorted(
        {
            row["direct_campaign_id"]
            for row in geoadv_rows
            if row["direct_campaign_id"]
        }
    )
    print("GEOADV_CAMPAIGN_IDS=" + ",".join(campaign_ids))

    print("=== BEFORE / AFTER ===")
    print(f"YANDEX_ADS_VISITS={before['yandex_ads']} -> {after['yandex_ads']}")
    print(
        "YANDEX_ADS_UNDEFINED_VISITS="
        f"{before['yandex_ads_undefined']} -> {after['yandex_ads_undefined']}"
    )
    print(
        "YANDEX_BUSINESS_VISITS="
        f"{before['yandex_business']} -> {after['yandex_business']}"
    )
    print(f"ALL_VISITS={before['visits']} -> {after['visits']}")

    for key in sorted(set(before["goals"]) | set(after["goals"])):
        b = before["goals"].get(key, {"goal_visits": 0, "reaches": 0})
        a = after["goals"].get(key, {"goal_visits": 0, "reaches": 0})
        print(
            f"GOAL_{key.upper()}_VISITS="
            f"{b['goal_visits']} -> {a['goal_visits']}"
        )
        print(
            f"GOAL_{key.upper()}_REACHES="
            f"{b['reaches']} -> {a['reaches']}"
        )

    if before["visits"] != after["visits"]:
        raise SystemExit("STOP=VISIT_TOTAL_CHANGED")
    if before["goals"] != after["goals"]:
        raise SystemExit("STOP=GOAL_TOTAL_CHANGED")

    print("SAFE_DIFF=PASS")
    print("MUTATION=NONE")
    print("CURRENT_GEOADV_READONLY_COMPARE=PASS")


if __name__ == "__main__":
    main()
