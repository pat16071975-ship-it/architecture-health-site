#!/usr/bin/env python3
"""Controlled September baseline rebuild v3.

v3 uses absolute service-analytics targets for the two historically omitted
structure doctors, derived directly from the saved IDENT exports:
- 01-06.09 baseline: Алатарцева 4 / 2500 ₽; Борисовская 2 / 0 ₽
- 07-10.09 confirmed source: Алатарцева 2 / 0 ₽; Борисовская 2 / 0 ₽
- final 01-10.09: Алатарцева 6 / 2500 ₽; Борисовская 4 / 0 ₽

The base v1 migration still performs DB backup, report_data rebuild, atomic
transaction and reconciliation. v3 only replaces service-analytics handling and
verification so zero-priced service rows are not inferred from normalized_json.
"""

from __future__ import annotations

import json

import rebuild_september_baseline_202609 as base

FINAL_SERVICE_TARGETS = {
    "Алатарцева П. В.": [6.0, 2500.0],
    "Борисовская А. И.": [4.0, 0.0],
}
CURRENT_PRE_MIGRATION_ALLOWED = {
    "Алатарцева П. В.": ([2.0, 0.0], [6.0, 2500.0]),
    "Борисовская А. И.": ([2.0, 0.0], [4.0, 0.0]),
}

_original_verify = base.verify


def _pair(value):
    if not isinstance(value, list) or len(value) < 2:
        return [0.0, 0.0]
    return [float(value[0] or 0), float(value[1] or 0)]


def _same_pair(left, right):
    return abs(float(left[0]) - float(right[0])) <= 0.001 and abs(float(left[1]) - float(right[1])) <= 0.01


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


def fixed_update_service_analytics(conn, now):
    row, data, months, month_index, categories, doctors = _service_context(conn)

    for doctor_name, target in FINAL_SERVICE_TARGETS.items():
        doctor = doctors.get(doctor_name)
        if not isinstance(doctor, dict):
            doctor = {category: [[0, 0] for _ in months] for category in categories}
            doctors[doctor_name] = doctor
        series = doctor.setdefault(base.SERVICE_CATEGORY, [])
        while len(series) < len(months):
            series.append([0, 0])

        current = _pair(series[month_index])
        allowed = CURRENT_PRE_MIGRATION_ALLOWED[doctor_name]
        if not any(_same_pair(current, candidate) for candidate in allowed):
            base.die(
                f"service analytics precondition failed for {doctor_name}: "
                f"current={current}, allowed={list(allowed)}"
            )
        series[month_index] = [float(target[0]), float(target[1])]

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
            "Выручка по направлениям (2026.09.11) 06-10",
        ],
        "baseline_through": "2026-09-06",
        "retail_excluded": 1150.0,
        "admin_visits_excluded": {"primary": 1, "repeat": 1},
        "structure_reclassified": {
            "Алатарцева П. В.": {
                "baseline_01_06_qty": 4,
                "baseline_01_06_revenue": 2500.0,
                "post_07_10_qty": 2,
                "post_07_10_revenue": 0.0,
                "final_qty": 6,
                "final_revenue": 2500.0,
                "repeat_visits_baseline": 4,
            },
            "Борисовская А. И.": {
                "baseline_01_06_qty": 2,
                "baseline_01_06_revenue": 0.0,
                "post_07_10_qty": 2,
                "post_07_10_revenue": 0.0,
                "final_qty": 4,
                "final_revenue": 0.0,
                "repeat_visits_baseline": 2,
            },
        },
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
    for doctor_name, expected in FINAL_SERVICE_TARGETS.items():
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
