#!/usr/bin/env python3
"""Controlled September baseline rebuild v2.

This wrapper fixes only the service-analytics merge precheck from v1.
The production service-analytics blob already contains contributions from the
corrected daily uploads for 07-10.09. v2 calculates those contributions directly
from daily_uploads and adds only the missing baseline 01-06 contribution.
"""

from __future__ import annotations

import json

import rebuild_september_baseline_202609 as base

BASELINE_INCREMENT = {
    "Алатарцева П. В.": [4.0, 2500.0],
    "Борисовская А. И.": [2.0, 0.0],
}

_original_verify = base.verify


def _pair(value):
    if not isinstance(value, list) or len(value) < 2:
        return [0.0, 0.0]
    return [float(value[0] or 0), float(value[1] or 0)]


def _same_pair(left, right):
    return abs(float(left[0]) - float(right[0])) <= 0.001 and abs(float(left[1]) - float(right[1])) <= 0.01


def _post_baseline_from_daily_uploads(conn):
    totals = {name: [0.0, 0.0] for name in BASELINE_INCREMENT}
    rows = conn.execute(
        """
        SELECT data_date,normalized_json
        FROM daily_uploads
        WHERE data_date BETWEEN '2026-09-07' AND '2026-09-10'
        ORDER BY data_date
        """
    ).fetchall()
    expected_dates = [f"2026-09-{day:02d}" for day in range(7, 11)]
    actual_dates = [row["data_date"] for row in rows]
    if actual_dates != expected_dates:
        base.die(f"service analytics daily source mismatch: {actual_dates!r}")

    for row in rows:
        try:
            normalized = json.loads(row["normalized_json"])
        except Exception as exc:
            base.die(f"{row['data_date']}: invalid normalized_json: {exc}")
        for item in normalized.get("items") or []:
            staff = str(item.get("staff") or "")
            if staff not in totals:
                continue
            text = (str(item.get("group") or "") + " " + str(item.get("service") or "")).lower()
            if "гипокс" not in text and "иггт" not in text:
                continue
            totals[staff][0] += float(item.get("qty") or 0)
            totals[staff][1] += float(item.get("amount") or 0)

    for values in totals.values():
        values[0] = round(values[0], 4)
        values[1] = round(values[1], 2)
    return totals


def _service_context(conn):
    row = conn.execute(
        "SELECT payload,updated_by FROM report_blobs WHERE key='az-service-analytics-v1'"
    ).fetchone()
    if not row:
        base.die("report_blobs az-service-analytics-v1 missing")
    try:
        data = json.loads(row["payload"])
    except Exception as exc:
        base.die(f"az-service-analytics-v1 invalid JSON: {exc}")
    if not isinstance(data, dict):
        base.die("az-service-analytics-v1 payload invalid")

    months = data.get("months") or []
    if base.SERVICE_MONTH not in months:
        base.die(f"{base.SERVICE_MONTH} missing in service analytics")
    month_index = months.index(base.SERVICE_MONTH)

    directions = data.setdefault("directions", {})
    if "Клиника" in directions and "Отделение структуры" not in directions:
        directions["Отделение структуры"] = directions.pop("Клиника")
    direction = directions.get("Отделение структуры")
    if not isinstance(direction, dict):
        base.die("Отделение структуры missing in service analytics")
    categories = direction.get("categories") or []
    if base.SERVICE_CATEGORY not in categories:
        base.die(f"service category missing: {base.SERVICE_CATEGORY}")
    doctors = direction.setdefault("doctors", {})
    return row, data, months, month_index, categories, doctors


def _expected_final(conn):
    post = _post_baseline_from_daily_uploads(conn)
    final = {}
    for doctor_name, increment in BASELINE_INCREMENT.items():
        final[doctor_name] = [
            round(post[doctor_name][0] + increment[0], 4),
            round(post[doctor_name][1] + increment[1], 2),
        ]
    return post, final


def fixed_update_service_analytics(conn, now):
    row, data, months, month_index, categories, doctors = _service_context(conn)
    post, final = _expected_final(conn)

    for doctor_name in BASELINE_INCREMENT:
        doctor = doctors.get(doctor_name)
        if not isinstance(doctor, dict):
            doctor = {category: [[0, 0] for _ in months] for category in categories}
            doctors[doctor_name] = doctor
        series = doctor.setdefault(base.SERVICE_CATEGORY, [])
        while len(series) < len(months):
            series.append([0, 0])

        current = _pair(series[month_index])
        if _same_pair(current, post[doctor_name]):
            series[month_index] = final[doctor_name]
        elif _same_pair(current, final[doctor_name]):
            series[month_index] = final[doctor_name]
        else:
            base.die(
                f"service analytics precondition failed for {doctor_name}: "
                f"current={current}, expected post-07-10={post[doctor_name]} "
                f"or final={final[doctor_name]}"
            )

    source = str(data.get("source") or "")
    if base.MARKER not in source:
        data["source"] = (source + "; " if source else "") + base.MARKER

    conn.execute(
        "UPDATE report_blobs SET payload=?,updated_at=? WHERE key='az-service-analytics-v1'",
        (json.dumps(data, ensure_ascii=False, separators=(",", ":")), now),
    )

    marker_payload = {
        "marker": base.MARKER,
        "applied_at": now,
        "source_files": [
            "Выручка по направлениям (2026.09.07)",
            "Завершенные приемы (2026.09.07)",
        ],
        "baseline_through": "2026-09-06",
        "retail_excluded": 1150.0,
        "admin_visits_excluded": {"primary": 1, "repeat": 1},
        "structure_reclassified": {
            "Алатарцева П. В.": {"service_qty": 4, "service_revenue": 2500.0, "repeat_visits": 4},
            "Борисовская А. И.": {"service_qty": 2, "service_revenue": 0.0, "repeat_visits": 2},
        },
        "post_baseline_07_10": post,
        "service_analytics_final": final,
    }
    conn.execute(
        """
        INSERT INTO report_blobs(key,payload,updated_by,updated_at)
        VALUES(?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET
          payload=excluded.payload,
          updated_by=excluded.updated_by,
          updated_at=excluded.updated_at
        """,
        (
            base.MARKER,
            json.dumps(marker_payload, ensure_ascii=False, separators=(",", ":")),
            row["updated_by"],
            now,
        ),
    )


def fixed_verify(conn):
    _original_verify(conn)
    _row, _data, _months, month_index, _categories, doctors = _service_context(conn)
    _post, final = _expected_final(conn)
    for doctor_name, expected in final.items():
        doctor = doctors.get(doctor_name) or {}
        series = doctor.get(base.SERVICE_CATEGORY) or []
        if len(series) <= month_index:
            base.die(f"verify service analytics missing September for {doctor_name}")
        current = _pair(series[month_index])
        if not _same_pair(current, expected):
            base.die(
                f"verify service analytics failed for {doctor_name}: "
                f"current={current}, expected={expected}"
            )


base.update_service_analytics = fixed_update_service_analytics
base.verify = fixed_verify


if __name__ == "__main__":
    raise SystemExit(base.main())
