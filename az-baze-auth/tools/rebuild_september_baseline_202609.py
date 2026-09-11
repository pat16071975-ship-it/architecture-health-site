#!/usr/bin/env python3
"""One-time controlled rebuild of AZ-BAZE September 2026 baseline (01-06).

Source aggregates were derived from the clinic's IDENT exports dated 2026-09-07:
- Выручка по направлениям (2026.09.07)
- Завершенные приемы (2026.09.07)

No patient-level data is stored in this script. The rebuild:
1) replaces calculated management-report fields for 01-06 with corrected cumulative values;
2) rebuilds 07-10 from the already saved daily_upload rows;
3) adds the historically omitted Алатарцева / Борисовская service-analytics quantities;
4) excludes the 1,150 ₽ retail item and two non-clinical admin appointments from medical KPIs.

The database is backed up before the transaction. All writes are atomic.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

DB_DEFAULT = "/var/lib/az-baze/auth.db"
BACKUP_DIR_DEFAULT = "/var/lib/az-baze/backups"
MARKER = "baseline_rebuild_2026-09-01_06_v1"
SOURCE = "IDENT baseline 2026-09-01..06; exports 2026-09-07; corrected clinical mapping"

DENTISTS = {
    "Чирков М. С.": "Чирков Максим Сергеевич",
    "Мовсесян Б. А.": "Мовсесян Бабкен Ашотович",
    "Румянцева М. П.": "Румянцева Мария Павловна",
    "Авдалие Э. И.": "Авдалие Эрик Ишханович",
    "Назыров У. Ж.": "Назыров Уктам Алиевич",
    "Голышенков В. А.": "Голышенков Владислав Александрович",
    "Зубачев Р. Н.": "Зубачев Роман Николаевич",
    "Босак Я. С.": "Босак Яна Сергеевна",
    "Филатова А. Д.": "Филатова А. Д.",
}
STRUCTURE = {
    "Старостенко В. А.": "Старостенко Вадим Анатольевич",
    "Димитренко А. Я.": "Димитренко Анна Яковлевна",
    "Лапина Н. С.": "Лапина Наталья Сергеевна",
    "Холодцева В. Е.": "Холодцева Валерия Евгеньевна",
    "Любаева А. Ю.": "Любаева Алёна Юрьевна",
    "Мамонтова Л. Г.": "Мамонтова Любава Геннадьевна",
    "Павлова А. В.": "Павлова Алла Викторовна",
    "Diers И.": "Diers И.",
    "Алатарцева П. В.": "Алатарцева П. В.",
    "Борисовская А. И.": "Борисовская А. И.",
}
LAB = {"Казанцев Л. Е.": "Казанцев Л. Е."}

DENT_FULL = list(DENTISTS.values())
STRUCT_FULL = list(STRUCTURE.values())

# Corrected DAILY clinical deltas for 01-06 from the saved IDENT exports.
# General primary/repeat deliberately exclude non-clinical administrator appointments.
BASELINE_DAILY = {
    "2026-09-01": {
        "factMedicine": 99860.0,
        "factLab": 0.0,
        "primary": 5,
        "repeat": 14,
        "dentPrimary": 2,
        "dentRepeat": 6,
        "clinicPrimary": 3,
        "clinicRepeat": 8,
        "labOrders": 0,
        "labRevenue": 0.0,
        "dentists": {
            "Мовсесян Бабкен Ашотович": 6600.0,
            "Румянцева Мария Павловна": 23760.0,
        },
        "clinicDocs": {
            "Старостенко Вадим Анатольевич": 67000.0,
            "Холодцева Валерия Евгеньевна": 2500.0,
        },
    },
    "2026-09-02": {
        "factMedicine": 259150.0,
        "factLab": 27350.0,
        "primary": 1,
        "repeat": 23,
        "dentPrimary": 0,
        "dentRepeat": 13,
        "clinicPrimary": 1,
        "clinicRepeat": 10,
        "labOrders": 1,
        "labRevenue": 27350.0,
        "dentists": {
            "Босак Яна Сергеевна": 8500.0,
            "Голышенков Владислав Александрович": 107700.0,
            "Румянцева Мария Павловна": 21700.0,
            "Чирков Максим Сергеевич": 38450.0,
        },
        "clinicDocs": {
            "Димитренко Анна Яковлевна": 12000.0,
            "Мамонтова Любава Геннадьевна": 6750.0,
            "Павлова Алла Викторовна": 21250.0,
            "Старостенко Вадим Анатольевич": 30000.0,
            "Холодцева Валерия Евгеньевна": 12800.0,
        },
    },
    "2026-09-03": {
        "factMedicine": 797000.0,
        "factLab": 70800.0,
        "primary": 0,
        "repeat": 13,
        "dentPrimary": 0,
        "dentRepeat": 12,
        "clinicPrimary": 0,
        "clinicRepeat": 1,
        "labOrders": 3,
        "labRevenue": 70800.0,
        "dentists": {
            "Босак Яна Сергеевна": 5000.0,
            "Назыров Уктам Алиевич": 528400.0,
            "Румянцева Мария Павловна": 23600.0,
            "Чирков Максим Сергеевич": 240000.0,
        },
        "clinicDocs": {},
    },
    "2026-09-04": {
        "factMedicine": 534760.0,
        "factLab": 0.0,
        "primary": 2,
        "repeat": 31,
        "dentPrimary": 2,
        "dentRepeat": 23,
        "clinicPrimary": 0,
        "clinicRepeat": 8,
        "labOrders": 0,
        "labRevenue": 0.0,
        "dentists": {
            "Голышенков Владислав Александрович": 6100.0,
            "Зубачев Роман Николаевич": 371000.0,
            "Назыров Уктам Алиевич": 67000.0,
            "Румянцева Мария Павловна": 32050.0,
            "Чирков Максим Сергеевич": 39760.0,
        },
        "clinicDocs": {
            "Лапина Наталья Сергеевна": 3500.0,
            "Любаева Алёна Юрьевна": 6350.0,
            "Мамонтова Любава Геннадьевна": 9000.0,
        },
    },
    "2026-09-05": {
        "factMedicine": 796550.0,
        "factLab": 0.0,
        "primary": 2,
        "repeat": 24,
        "dentPrimary": 2,
        "dentRepeat": 21,
        "clinicPrimary": 0,
        "clinicRepeat": 3,
        "labOrders": 0,
        "labRevenue": 0.0,
        "dentists": {
            "Босак Яна Сергеевна": 26000.0,
            "Зубачев Роман Николаевич": 487250.0,
            "Назыров Уктам Алиевич": 225700.0,
            "Румянцева Мария Павловна": 50100.0,
        },
        "clinicDocs": {
            "Алатарцева П. В.": 2500.0,
            "Лапина Наталья Сергеевна": 5000.0,
        },
    },
    "2026-09-06": {
        "factMedicine": 26600.0,
        "factLab": 0.0,
        "primary": 1,
        "repeat": 2,
        "dentPrimary": 1,
        "dentRepeat": 1,
        "clinicPrimary": 0,
        "clinicRepeat": 1,
        "labOrders": 0,
        "labRevenue": 0.0,
        "dentists": {
            "Мовсесян Бабкен Ашотович": 23100.0,
        },
        "clinicDocs": {
            "Холодцева Валерия Евгеньевна": 3500.0,
        },
    },
}

EXPECTED_OLD_06 = {
    "factMedicine": 2515070.0,
    "primary": 12,
    "repeat": 108,
}
EXPECTED_FINAL_06 = {
    "factMedicine": 2513920.0,
    "factLab": 98150.0,
    "primary": 11,
    "repeat": 107,
    "dentPrimary": 7,
    "dentRepeat": 76,
    "clinicPrimary": 4,
    "clinicRepeat": 31,
    "labOrders": 4,
    "labRevenue": 98150.0,
    "dentRevenue": 2331770.0,
    "clinicRevenue": 182150.0,
}
EXPECTED_FINAL_10 = {
    "factMedicine": 3354980.0,
    "factLab": 125250.0,
    "primary": 17,
    "repeat": 181,
    "dentPrimary": 9,
    "dentRepeat": 129,
    "clinicPrimary": 8,
    "clinicRepeat": 52,
    "labOrders": 5,
    "labRevenue": 125250.0,
    "dentRevenue": 3055200.0,
    "clinicRevenue": 299780.0,
}

SERVICE_BASELINE_TARGETS = {
    "Алатарцева П. В.": [4.0, 2500.0],
    "Борисовская А. И.": [2.0, 0.0],
}
SERVICE_CATEGORY = "Гипокситерапия / ИГГТ"
SERVICE_MONTH = "Сентябрь"


def die(message: str) -> None:
    raise RuntimeError(message)


def plain_number(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return 0


def load_report(conn: sqlite3.Connection, date_value: str) -> dict:
    row = conn.execute("SELECT payload FROM report_data WHERE date=?", (date_value,)).fetchone()
    if not row:
        die(f"report_data missing {date_value}")
    try:
        payload = json.loads(row["payload"])
    except Exception as exc:
        die(f"invalid report_data JSON for {date_value}: {exc}")
    if not isinstance(payload, dict):
        die(f"report_data payload is not object for {date_value}")
    return payload


def report_sums(record: dict) -> tuple[float, float]:
    dent = sum(float(v or 0) for v in (record.get("dentists") or {}).values())
    clinic = sum(float(v or 0) for v in (record.get("clinicDocs") or {}).values())
    return round(dent, 2), round(clinic, 2)


def check_fields(record: dict, expected: dict, label: str) -> None:
    dent, clinic = report_sums(record)
    values = dict(record)
    values["dentRevenue"] = dent
    values["clinicRevenue"] = clinic
    for key, expected_value in expected.items():
        actual = values.get(key)
        if isinstance(expected_value, float):
            if abs(float(actual or 0) - expected_value) > 0.01:
                die(f"{label}: {key}={actual!r}, expected {expected_value!r}")
        else:
            if int(actual or 0) != expected_value:
                die(f"{label}: {key}={actual!r}, expected {expected_value!r}")


def empty_state() -> dict:
    return {
        "factMedicine": 0.0,
        "factLab": 0.0,
        "primary": 0,
        "repeat": 0,
        "dentPrimary": 0,
        "dentRepeat": 0,
        "clinicPrimary": 0,
        "clinicRepeat": 0,
        "dentists": {name: 0.0 for name in DENT_FULL},
        "clinicDocs": {name: 0.0 for name in STRUCT_FULL},
        "labOrders": 0,
        "labRevenue": 0.0,
    }


def add_delta(state: dict, delta: dict) -> None:
    for key in (
        "factMedicine", "factLab", "primary", "repeat",
        "dentPrimary", "dentRepeat", "clinicPrimary", "clinicRepeat",
        "labOrders", "labRevenue",
    ):
        state[key] += plain_number(delta.get(key))
    for name, value in (delta.get("dentists") or {}).items():
        state["dentists"][name] = state["dentists"].get(name, 0.0) + float(value or 0)
    for name, value in (delta.get("clinicDocs") or {}).items():
        state["clinicDocs"][name] = state["clinicDocs"].get(name, 0.0) + float(value or 0)


def normalized_daily_delta(normalized: dict) -> dict:
    date_value = str(normalized.get("data_date") or "")
    if not date_value:
        die("daily_upload normalized_json missing data_date")

    staff_revenue: dict[str, float] = {}
    for item in normalized.get("items") or []:
        staff = str(item.get("staff") or "")
        amount = float(item.get("amount") or 0)
        staff_revenue[staff] = staff_revenue.get(staff, 0.0) + amount

    dentists = {
        full: round(staff_revenue.get(short, 0.0), 2)
        for short, full in DENTISTS.items()
    }
    clinic_docs = {
        full: round(staff_revenue.get(short, 0.0), 2)
        for short, full in STRUCTURE.items()
    }
    lab_revenue = round(sum(staff_revenue.get(short, 0.0) for short in LAB), 2)

    known_revenue = (
        sum(dentists.values())
        + sum(clinic_docs.values())
        + lab_revenue
    )
    total_items = round(sum(staff_revenue.values()), 2)
    if abs(total_items - known_revenue) > 0.01:
        unknown = {
            k: round(v, 2)
            for k, v in staff_revenue.items()
            if k not in DENTISTS and k not in STRUCTURE and k not in LAB and abs(v) > 0.01
        }
        die(f"{date_value}: unclassified service revenue {total_items-known_revenue:.2f}; staff={unknown}")

    doctors = (normalized.get("doctors") or {}).get(date_value) or {}
    dent_primary = clinic_primary = dent_repeat = clinic_repeat = 0

    for staff, value in (doctors.get("Первичные") or {}).items():
        if staff in DENTISTS:
            dent_primary += int(value or 0)
        elif staff in STRUCTURE:
            clinic_primary += int(value or 0)

    for kind in ("Повторные", "Отконсультированные"):
        for staff, value in (doctors.get(kind) or {}).items():
            if staff in DENTISTS:
                dent_repeat += int(value or 0)
            elif staff in STRUCTURE:
                clinic_repeat += int(value or 0)

    day_counts = (normalized.get("overall") or {}).get(date_value) or {}
    raw_primary = int(day_counts.get("Первичные", 0) or 0)
    raw_repeat = int(day_counts.get("Повторные", 0) or 0) + int(day_counts.get("Отконсультированные", 0) or 0)
    clinical_primary = dent_primary + clinic_primary
    clinical_repeat = dent_repeat + clinic_repeat
    if raw_primary != clinical_primary or raw_repeat != clinical_repeat:
        die(
            f"{date_value}: daily upload no longer reconciles: "
            f"raw={raw_primary}/{raw_repeat}, clinical={clinical_primary}/{clinical_repeat}"
        )

    lab_orders = len({
        (str(row[0]), str(row[1]))
        for row in (normalized.get("lab_invoices") or [])
        if isinstance(row, (list, tuple)) and len(row) >= 2 and str(row[1]) == date_value
    })

    return {
        "factMedicine": round(sum(dentists.values()) + sum(clinic_docs.values()), 2),
        "factLab": lab_revenue,
        "primary": clinical_primary,
        "repeat": clinical_repeat,
        "dentPrimary": dent_primary,
        "dentRepeat": dent_repeat,
        "clinicPrimary": clinic_primary,
        "clinicRepeat": clinic_repeat,
        "dentists": dentists,
        "clinicDocs": clinic_docs,
        "labOrders": lab_orders,
        "labRevenue": lab_revenue,
    }


def make_record(existing: dict, date_value: str, state: dict) -> dict:
    record = copy.deepcopy(existing)
    record.update({
        "date": date_value,
        "factMedicine": round(float(state["factMedicine"]), 2),
        "factLab": round(float(state["factLab"]), 2),
        "primary": int(state["primary"]),
        "repeat": int(state["repeat"]),
        "dentPrimary": int(state["dentPrimary"]),
        "dentRepeat": int(state["dentRepeat"]),
        "dentists": {k: round(float(v), 2) for k, v in state["dentists"].items()},
        "clinicPrimary": int(state["clinicPrimary"]),
        "clinicRepeat": int(state["clinicRepeat"]),
        "clinicDocs": {k: round(float(v), 2) for k, v in state["clinicDocs"].items()},
        "labOrders": int(state["labOrders"]),
        "labRevenue": round(float(state["labRevenue"]), 2),
        "_source": SOURCE,
        "_aggregation": "month_to_date",
    })
    return record


def build_records(conn: sqlite3.Connection) -> dict[str, dict]:
    state = empty_state()
    records: dict[str, dict] = {}

    for date_value in sorted(BASELINE_DAILY):
        add_delta(state, BASELINE_DAILY[date_value])
        records[date_value] = make_record(load_report(conn, date_value), date_value, state)

    rows = conn.execute(
        """
        SELECT data_date, normalized_json
        FROM daily_uploads
        WHERE data_date BETWEEN '2026-09-07' AND '2026-09-10'
        ORDER BY data_date
        """
    ).fetchall()
    dates = [row["data_date"] for row in rows]
    expected_dates = [f"2026-09-{day:02d}" for day in range(7, 11)]
    if dates != expected_dates:
        die(f"daily_upload rows 07-10 mismatch: {dates!r}")

    for row in rows:
        try:
            normalized = json.loads(row["normalized_json"])
        except Exception as exc:
            die(f"{row['data_date']}: invalid normalized_json: {exc}")
        add_delta(state, normalized_daily_delta(normalized))
        records[row["data_date"]] = make_record(
            load_report(conn, row["data_date"]),
            row["data_date"],
            state,
        )

    return records


def update_service_analytics(conn: sqlite3.Connection, now: str) -> None:
    row = conn.execute(
        "SELECT payload,updated_by FROM report_blobs WHERE key='az-service-analytics-v1'"
    ).fetchone()
    if not row:
        die("report_blobs az-service-analytics-v1 missing")
    data = json.loads(row["payload"])
    if not isinstance(data, dict):
        die("az-service-analytics-v1 payload invalid")

    months = data.get("months") or []
    if SERVICE_MONTH not in months:
        die(f"{SERVICE_MONTH} missing in service analytics")
    month_index = months.index(SERVICE_MONTH)

    directions = data.setdefault("directions", {})
    if "Клиника" in directions and "Отделение структуры" not in directions:
        directions["Отделение структуры"] = directions.pop("Клиника")
    direction = directions.get("Отделение структуры")
    if not isinstance(direction, dict):
        die("Отделение структуры missing in service analytics")

    categories = direction.get("categories") or []
    if SERVICE_CATEGORY not in categories:
        die(f"service category missing: {SERVICE_CATEGORY}")
    doctors = direction.setdefault("doctors", {})

    for doctor_name, target in SERVICE_BASELINE_TARGETS.items():
        doctor = doctors.get(doctor_name)
        if not isinstance(doctor, dict):
            doctor = {
                category: [[0, 0] for _ in months]
                for category in categories
            }
            doctors[doctor_name] = doctor
        series = doctor.setdefault(SERVICE_CATEGORY, [])
        while len(series) < len(months):
            series.append([0, 0])
        current = series[month_index]
        current_pair = [float(current[0] or 0), float(current[1] or 0)] if isinstance(current, list) and len(current) >= 2 else [0.0, 0.0]
        target_pair = [float(target[0]), float(target[1])]
        if current_pair not in ([0.0, 0.0], target_pair):
            die(
                f"service analytics precondition failed for {doctor_name}: "
                f"current={current_pair}, expected zero or {target_pair}"
            )
        series[month_index] = [target_pair[0], target_pair[1]]

    source = str(data.get("source") or "")
    if MARKER not in source:
        data["source"] = (source + "; " if source else "") + MARKER

    conn.execute(
        "UPDATE report_blobs SET payload=?,updated_at=? WHERE key='az-service-analytics-v1'",
        (json.dumps(data, ensure_ascii=False, separators=(",", ":")), now),
    )

    marker_payload = {
        "marker": MARKER,
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
            MARKER,
            json.dumps(marker_payload, ensure_ascii=False, separators=(",", ":")),
            row["updated_by"],
            now,
        ),
    )


def backup_database(db_path: str, backup_dir: str) -> str:
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = str(Path(backup_dir) / f"auth-before-{MARKER}-{stamp}.db")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    os.chmod(backup_path, 0o600)
    return backup_path


def already_applied(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT payload FROM report_blobs WHERE key=?", (MARKER,)).fetchone()
    if not row:
        return False
    record06 = load_report(conn, "2026-09-06")
    record10 = load_report(conn, "2026-09-10")
    try:
        check_fields(record06, EXPECTED_FINAL_06, "already-applied 06")
        check_fields(record10, EXPECTED_FINAL_10, "already-applied 10")
    except RuntimeError:
        return False
    return True


def precheck(conn: sqlite3.Connection) -> None:
    if already_applied(conn):
        return

    current06 = load_report(conn, "2026-09-06")
    for key, expected in EXPECTED_OLD_06.items():
        actual = current06.get(key)
        if isinstance(expected, float):
            if abs(float(actual or 0) - expected) > 0.01:
                die(f"precheck 06: {key}={actual!r}, expected {expected!r}")
        elif int(actual or 0) != expected:
            die(f"precheck 06: {key}={actual!r}, expected {expected!r}")

    current10 = load_report(conn, "2026-09-10")
    if abs(float(current10.get("factMedicine") or 0) - 3356130.0) > 0.01:
        die("precheck 10 factMedicine is not expected pre-rebuild value")
    if int(current10.get("primary") or 0) != 18 or int(current10.get("repeat") or 0) != 182:
        die("precheck 10 primary/repeat are not expected pre-rebuild values")

    rows = conn.execute(
        "SELECT data_date,revision FROM daily_uploads WHERE data_date BETWEEN '2026-09-07' AND '2026-09-10' ORDER BY data_date"
    ).fetchall()
    if [row["data_date"] for row in rows] != [f"2026-09-{d:02d}" for d in range(7, 11)]:
        die("precheck daily_upload dates 07-10 failed")


def verify(conn: sqlite3.Connection) -> None:
    record06 = load_report(conn, "2026-09-06")
    record10 = load_report(conn, "2026-09-10")
    check_fields(record06, EXPECTED_FINAL_06, "verify 06")
    check_fields(record10, EXPECTED_FINAL_10, "verify 10")

    for date_value in ("2026-09-06", "2026-09-10"):
        record = load_report(conn, date_value)
        dent, clinic = report_sums(record)
        if abs(float(record.get("factMedicine") or 0) - dent - clinic) > 0.01:
            die(f"{date_value}: factMedicine does not reconcile")
        if int(record.get("primary") or 0) != int(record.get("dentPrimary") or 0) + int(record.get("clinicPrimary") or 0):
            die(f"{date_value}: primary does not reconcile")
        if int(record.get("repeat") or 0) != int(record.get("dentRepeat") or 0) + int(record.get("clinicRepeat") or 0):
            die(f"{date_value}: repeat does not reconcile")

    marker = conn.execute("SELECT 1 FROM report_blobs WHERE key=?", (MARKER,)).fetchone()
    if not marker:
        die("migration marker missing")


def apply(db_path: str, backup_dir: str) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        precheck(conn)
        if already_applied(conn):
            verify(conn)
            print("BASELINE ALREADY APPLIED: OK")
            print("BASELINE 06: medicine=2513920; primary/repeat=11/107; dentistry/structure=2331770/182150")
            print("REPORT 10: medicine=3354980; primary/repeat=17/181; dentistry/structure=3055200/299780")
            return 0

        records = build_records(conn)
        check_fields(records["2026-09-06"], EXPECTED_FINAL_06, "candidate 06")
        check_fields(records["2026-09-10"], EXPECTED_FINAL_10, "candidate 10")
        print("PRECHECK SOURCE TOTALS: OK")

        backup_path = backup_database(db_path, backup_dir)
        print(f"BACKUP={backup_path}")

        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for date_value in sorted(records):
                payload = json.dumps(records[date_value], ensure_ascii=False, separators=(",", ":"))
                cur = conn.execute(
                    "UPDATE report_data SET payload=?,updated_at=? WHERE date=?",
                    (payload, now, date_value),
                )
                if cur.rowcount != 1:
                    die(f"report_data update affected {cur.rowcount} rows for {date_value}")

            update_service_analytics(conn, now)
            verify(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        verify(conn)
        print("BASELINE 01-06: REBUILT")
        print("REPORT 07-10: REBUILT FROM DAILY UPLOADS")
        print("SERVICE ANALYTICS BASELINE: CORRECTED")
        print("BASELINE 06: medicine=2513920; primary/repeat=11/107; dentistry/structure=2331770/182150")
        print("REPORT 10: medicine=3354980; lab=125250; primary/repeat=17/181; dentistry/structure=3055200/299780")
        print("RECONCILIATION: OK")
        return 0
    finally:
        conn.close()


def verify_only(db_path: str) -> int:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        verify(conn)
        print("VERIFY BASELINE: OK")
        return 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DB_DEFAULT)
    parser.add_argument("--backup-dir", default=BACKUP_DIR_DEFAULT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: DB not found: {args.db}", file=sys.stderr)
        return 2

    try:
        if args.apply:
            return apply(args.db, args.backup_dir)
        return verify_only(args.db)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
