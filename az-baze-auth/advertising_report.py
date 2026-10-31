import calendar
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import metrika_auto

from flask import Response, abort, g, jsonify, request

from app import DB_PATH, SITE_ROOT, admin_required, audit, db, iso_now, permission_required, require_csrf


SCHEMA_VERSION = 1
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_TIMEZONE = "Asia/Novosibirsk"
METRIKA_EXPORT_SCHEMA = "az_analytics_export_v1"
METRIKA_COUNTER_ID = "113776005"
METRIKA_ARCHIVE_ROOT = Path(os.environ.get("AZBAZE_METRIKA_ARCHIVE", "/var/lib/az-baze/advertising-metrika-archive"))
METRIKA_GOALS = {
    "Открытие окна записи": "booking_open",
    "Переход к записи в Medflex": "medflex",
    "Нажатие на телефон": "phone",
    "Автоцель: клик по номеру телефона": "phone_auto",
    "Автоцель: переход в мессенджер": "messenger",
}
INSTAGRAM_CAMPAIGN_LABELS = {
    "starostenko": "Старостенко",
    "zubachev": "Зубачев",
    "clinic": "Клиника",
}
LEGACY_INSTAGRAM_SOURCE_KEYS = {
    "instagram_starostenko": "starostenko",
    "instagram_bio_unknown": "link_in_bio",
    "instagram_other": "",
}
YANDEX_CANONICAL_CAMPAIGNS = {
    "715444498": "АЗ | Бруксизм и скрежет зубами | Поиск | Новосибирск",
    "715560815": "АЗ | Ортодонт и гнатолог | Поиск | Новосибирск",
}
YANDEX_CAMPAIGN_ALIASES = {
    "bruksizm_search_nsk": "715444498",
    "аз | бруксизм и скрежет зубами | поиск | новосибирск": "715444498",
    "аз | ортодонт и гнатолог | поиск | новосибирск": "715560815",
}
METRIKA_CAMPAIGN_REPORT_SECTION = "campaigns_clean"
METRIKA_CAMPAIGN_REPORT_IGNORED = {"Чужие кампании", "Не определено"}
DATASET_CONFIG = {
    "direct": ("advertising_traffic_daily", "metric_date"),
    "metrika": ("advertising_traffic_daily", "metric_date"),
    "clinic": ("advertising_clinic_daily", "metric_date"),
    "cohort": ("advertising_cohort_daily", "cohort_date"),
}
METRIC_KEYS = (
    "spend", "clicks", "visits", "target_leads", "leads_with_primary",
    "primary_visits", "primary_paid", "primary_services", "repeat_visits",
    "repeat_paid", "new_patients", "new_patient_30d_paid",
    "booking_open_visits", "booking_open_reaches", "medflex_visits", "medflex_reaches",
    "phone_visits", "messenger_visits",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS advertising_traffic_daily (
    provider TEXT NOT NULL,
    metric_date TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_name TEXT NOT NULL,
    channel_name TEXT,
    campaign_id TEXT NOT NULL DEFAULT '',
    campaign_name TEXT NOT NULL DEFAULT '',
    spend REAL,
    clicks INTEGER,
    visits INTEGER,
    updated_at TEXT NOT NULL,
    import_id TEXT NOT NULL,
    PRIMARY KEY(provider, metric_date, source_key, campaign_id, campaign_name)
);
CREATE TABLE IF NOT EXISTS advertising_clinic_daily (
    provider TEXT NOT NULL,
    metric_date TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_name TEXT NOT NULL,
    channel_name TEXT,
    campaign_id TEXT NOT NULL DEFAULT '',
    campaign_name TEXT NOT NULL DEFAULT '',
    target_leads INTEGER,
    leads_with_primary INTEGER,
    primary_visits INTEGER,
    primary_paid REAL,
    primary_services REAL,
    repeat_visits INTEGER,
    repeat_paid REAL,
    updated_at TEXT NOT NULL,
    import_id TEXT NOT NULL,
    PRIMARY KEY(provider, metric_date, source_key, campaign_id, campaign_name)
);
CREATE TABLE IF NOT EXISTS advertising_cohort_daily (
    provider TEXT NOT NULL,
    cohort_date TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_name TEXT NOT NULL,
    channel_name TEXT,
    campaign_id TEXT NOT NULL DEFAULT '',
    campaign_name TEXT NOT NULL DEFAULT '',
    new_patients INTEGER,
    new_patient_30d_paid REAL,
    updated_at TEXT NOT NULL,
    import_id TEXT NOT NULL,
    PRIMARY KEY(provider, cohort_date, source_key, campaign_id, campaign_name)
);
CREATE TABLE IF NOT EXISTS advertising_report_imports (
    payload_sha256 TEXT NOT NULL,
    dataset TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    timezone TEXT NOT NULL,
    currency TEXT,
    vat_mode TEXT,
    attribution_model TEXT,
    imported_at TEXT NOT NULL,
    actor_user_id INTEGER,
    row_count INTEGER NOT NULL,
    PRIMARY KEY(payload_sha256, dataset)
);
CREATE INDEX IF NOT EXISTS idx_ad_traffic_date ON advertising_traffic_daily(metric_date);
CREATE INDEX IF NOT EXISTS idx_ad_clinic_date ON advertising_clinic_daily(metric_date);
CREATE INDEX IF NOT EXISTS idx_ad_cohort_date ON advertising_cohort_daily(cohort_date);
CREATE TABLE IF NOT EXISTS advertising_metrika_goal_daily (
    provider TEXT NOT NULL,
    metric_date TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_name TEXT NOT NULL,
    channel_name TEXT,
    campaign_id TEXT NOT NULL DEFAULT '',
    campaign_name TEXT NOT NULL DEFAULT '',
    goal_key TEXT NOT NULL,
    goal_name TEXT NOT NULL,
    goal_visits INTEGER NOT NULL,
    reaches INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    import_id TEXT NOT NULL,
    PRIMARY KEY(provider, metric_date, source_key, campaign_id, campaign_name, goal_key)
);
CREATE INDEX IF NOT EXISTS idx_ad_metrika_goal_date ON advertising_metrika_goal_daily(metric_date);
CREATE TABLE IF NOT EXISTS advertising_metrika_imports (
    payload_sha256 TEXT PRIMARY KEY,
    original_filename TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    completed_days_json TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    actor_user_id INTEGER,
    traffic_rows INTEGER NOT NULL,
    goal_rows INTEGER NOT NULL,
    archive_path TEXT NOT NULL
);
"""


def _ensure_schema(path=DB_PATH):
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _parse_date(value):
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise ValueError("invalid date")
    return date.fromisoformat(value)


def _iso(value):
    return value.isoformat()


def _month_last_day(year, month):
    return calendar.monthrange(year, month)[1]


def _shift_month(value, delta):
    month_index = value.year * 12 + (value.month - 1) + delta
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    return date(year, month, min(value.day, _month_last_day(year, month)))


def _shift_year(value, delta=-1):
    year = value.year + delta
    return date(year, value.month, min(value.day, _month_last_day(year, value.month)))


def _same_month(start, end):
    return start.year == end.year and start.month == end.month


def _comparison_ranges(mode, start, end):
    if start > end:
        raise ValueError("invalid range")
    if mode == "last_year":
        return [(_shift_year(start), _shift_year(end))]
    if mode == "previous_period":
        if _same_month(start, end):
            return [(_shift_month(start, -1), _shift_month(end, -1))]
        days = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        return [(prev_end - timedelta(days=days - 1), prev_end)]
    if mode == "previous_quarter_avg":
        if not _same_month(start, end):
            return []
        quarter_start_month = ((start.month - 1) // 3) * 3 + 1
        prev_q_end_month = quarter_start_month - 1
        prev_q_year = start.year
        if prev_q_end_month == 0:
            prev_q_end_month = 12
            prev_q_year -= 1
        result = []
        for offset in (2, 1, 0):
            month_index = prev_q_year * 12 + (prev_q_end_month - 1) - offset
            year, month0 = divmod(month_index, 12)
            month = month0 + 1
            last = _month_last_day(year, month)
            from_day = min(start.day, last)
            to_day = min(end.day, last)
            if from_day <= to_day:
                result.append((date(year, month, from_day), date(year, month, to_day)))
        return result
    if mode == "year_avg":
        if not _same_month(start, end):
            return []
        result = []
        for month in range(1, start.month):
            last = _month_last_day(start.year, month)
            from_day = min(start.day, last)
            to_day = min(end.day, last)
            if from_day <= to_day:
                result.append((date(start.year, month, from_day), date(start.year, month, to_day)))
        return result
    return []


def _comparison_label(mode):
    return {
        "previous_period": "Предыдущий сопоставимый период",
        "previous_quarter_avg": "Среднее предыдущего квартала",
        "year_avg": "Среднее текущего года",
        "last_year": "Тот же период прошлого года",
        "none": "Без сравнения",
    }.get(mode, "Без сравнения")


def _nullable_sum(rows, key):
    values = [row[key] for row in rows if row[key] is not None]
    return sum(values) if values else None


def _combine_money(primary, repeat):
    if primary is None and repeat is None:
        return None
    return (primary or 0) + (repeat or 0)


def _summary(conn, start, end):
    start_s, end_s = _iso(start), _iso(end)
    traffic = conn.execute(
        "SELECT spend,clicks,visits FROM advertising_traffic_daily WHERE metric_date BETWEEN ? AND ?",
        (start_s, end_s),
    ).fetchall()
    clinic = conn.execute(
        "SELECT target_leads,leads_with_primary,primary_visits,primary_paid,primary_services,repeat_visits,repeat_paid "
        "FROM advertising_clinic_daily WHERE metric_date BETWEEN ? AND ?",
        (start_s, end_s),
    ).fetchall()
    cohort = conn.execute(
        "SELECT new_patients,new_patient_30d_paid FROM advertising_cohort_daily WHERE cohort_date BETWEEN ? AND ?",
        (start_s, end_s),
    ).fetchall()
    result = {
        "spend": _nullable_sum(traffic, "spend"),
        "clicks": _nullable_sum(traffic, "clicks"),
        "visits": _nullable_sum(traffic, "visits"),
        "target_leads": _nullable_sum(clinic, "target_leads"),
        "leads_with_primary": _nullable_sum(clinic, "leads_with_primary"),
        "primary_visits": _nullable_sum(clinic, "primary_visits"),
        "primary_paid": _nullable_sum(clinic, "primary_paid"),
        "primary_services": _nullable_sum(clinic, "primary_services"),
        "repeat_visits": _nullable_sum(clinic, "repeat_visits"),
        "repeat_paid": _nullable_sum(clinic, "repeat_paid"),
        "new_patients": _nullable_sum(cohort, "new_patients"),
        "new_patient_30d_paid": _nullable_sum(cohort, "new_patient_30d_paid"),
    }
    result["total_visits"] = None if result["primary_visits"] is None and result["repeat_visits"] is None else (result["primary_visits"] or 0) + (result["repeat_visits"] or 0)
    result["total_paid"] = _combine_money(result["primary_paid"], result["repeat_paid"])
    result["lead_to_primary_pct"] = result["leads_with_primary"] * 100.0 / result["target_leads"] if result["target_leads"] and result["leads_with_primary"] is not None else None
    result.update(_metrika_goal_summary(conn, start, end))
    return result


def _average_summaries(summaries):
    if not summaries:
        return {key: None for key in (*METRIC_KEYS, "total_visits", "total_paid", "lead_to_primary_pct")}
    result = {}
    for key in summaries[0].keys():
        values = [item.get(key) for item in summaries]
        result[key] = sum(values) / len(values) if values and all(v is not None for v in values) else None
    return result


def _delta_pct(current, previous):
    if current is None or previous is None:
        return None
    if previous == 0:
        return 0.0 if current == 0 else None
    return (current - previous) * 100.0 / previous


def _metric_daily(conn, start, end, metric):
    start_s, end_s = _iso(start), _iso(end)
    if metric in {"spend", "visits"}:
        rows = conn.execute(
            f"SELECT metric_date AS day,SUM({metric}) AS value FROM advertising_traffic_daily "
            f"WHERE metric_date BETWEEN ? AND ? AND {metric} IS NOT NULL GROUP BY metric_date ORDER BY metric_date",
            (start_s, end_s),
        ).fetchall()
    elif metric in {"target_leads", "primary_visits"}:
        rows = conn.execute(
            f"SELECT metric_date AS day,SUM({metric}) AS value FROM advertising_clinic_daily "
            f"WHERE metric_date BETWEEN ? AND ? AND {metric} IS NOT NULL GROUP BY metric_date ORDER BY metric_date",
            (start_s, end_s),
        ).fetchall()
    elif metric == "total_paid":
        rows = conn.execute(
            "SELECT metric_date AS day,SUM(COALESCE(primary_paid,0)+COALESCE(repeat_paid,0)) AS value "
            "FROM advertising_clinic_daily WHERE metric_date BETWEEN ? AND ? "
            "AND (primary_paid IS NOT NULL OR repeat_paid IS NOT NULL) GROUP BY metric_date ORDER BY metric_date",
            (start_s, end_s),
        ).fetchall()
    else:
        return {}
    return {str(row["day"]): row["value"] for row in rows}


def _trend(conn, start, end, metric, comparison_ranges):
    days = (end - start).days + 1
    current_map = _metric_daily(conn, start, end, metric)
    current = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        current.append({"point": offset + 1, "date": _iso(day), "value": current_map.get(_iso(day))})
    compare = []
    if comparison_ranges:
        maps = [_metric_daily(conn, a, b, metric) for a, b in comparison_ranges]
        for offset in range(days):
            values, labels = [], []
            for (a, b), mapping in zip(comparison_ranges, maps):
                day = a + timedelta(days=offset)
                if day <= b:
                    labels.append(_iso(day))
                    value = mapping.get(_iso(day))
                    if value is not None:
                        values.append(value)
            compare.append({
                "point": offset + 1,
                "date": labels[0] if len(labels) == 1 else "",
                "value": (sum(values) / len(values)) if len(values) == len(comparison_ranges) and values else None,
            })
    return {"metric": metric, "current": current, "compare": compare}


def _group_breakdown(conn, start, end, level):
    start_s, end_s = _iso(start), _iso(end)
    traffic = conn.execute(
        "SELECT source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits "
        "FROM advertising_traffic_daily WHERE metric_date BETWEEN ? AND ?", (start_s, end_s)
    ).fetchall()
    clinic = conn.execute(
        "SELECT source_key,source_name,channel_name,campaign_id,campaign_name,target_leads,leads_with_primary,"
        "primary_visits,primary_paid,primary_services,repeat_visits,repeat_paid FROM advertising_clinic_daily "
        "WHERE metric_date BETWEEN ? AND ?", (start_s, end_s)
    ).fetchall()
    cohort = conn.execute(
        "SELECT source_key,source_name,channel_name,campaign_id,campaign_name,new_patients,new_patient_30d_paid "
        "FROM advertising_cohort_daily WHERE cohort_date BETWEEN ? AND ?", (start_s, end_s)
    ).fetchall()
    try:
        goals = conn.execute(
            "SELECT source_key,source_name,channel_name,campaign_id,campaign_name,goal_key,SUM(goal_visits) AS goal_visits "
            "FROM advertising_metrika_goal_daily WHERE provider='metrika' AND metric_date BETWEEN ? AND ? "
            "GROUP BY source_key,source_name,channel_name,campaign_id,campaign_name,goal_key",
            (start_s, end_s),
        ).fetchall()
    except sqlite3.OperationalError:
        goals = []
    groups = {}

    def identity_for(row):
        source_key, source_name, channel_name, campaign_id, campaign_name = _canonical_metrika_identity(
            row["source_key"], row["source_name"], row["channel_name"],
            row["campaign_id"], row["campaign_name"],
        )
        if source_key in {"yandex_ads", "instagram"} and not campaign_name:
            # Missing display name must not erase a factual non-empty campaign ID.
            campaign_name = "Не определено"
        return source_key, source_name, channel_name, campaign_id, campaign_name

    def key_for(identity):
        source_key, _source_name, _channel_name, campaign_id, campaign_name = identity
        if level == "campaign":
            return None if not campaign_id and not campaign_name else (source_key, campaign_id, campaign_name)
        return (source_key,)

    def ensure(identity, key):
        if key not in groups:
            source_key, source_name, channel_name, campaign_id, campaign_name = identity
            groups[key] = {
                "source_key": source_key, "source_name": source_name,
                "channel_name": channel_name,
                "campaign_id": campaign_id if level == "campaign" else "",
                "campaign_name": campaign_name if level == "campaign" else "",
                **{metric: None for metric in METRIC_KEYS},
            }
        return groups[key]

    def add(row, fields):
        identity = identity_for(row)
        key = key_for(identity)
        if key is None:
            return
        target = ensure(identity, key)
        for field in fields:
            value = row[field]
            if value is not None:
                target[field] = (target[field] or 0) + value
    for row in traffic:
        add(row, ("spend", "clicks", "visits"))
    for row in clinic:
        add(row, ("target_leads", "leads_with_primary", "primary_visits", "primary_paid", "primary_services", "repeat_visits", "repeat_paid"))
    for row in cohort:
        add(row, ("new_patients", "new_patient_30d_paid"))
    for row in goals:
        identity = identity_for(row)
        key = key_for(identity)
        if key is None:
            continue
        target = ensure(identity, key)
        field = {
            "booking_open": "booking_open_visits",
            "medflex": "medflex_visits",
            "phone": "phone_visits",
            "phone_auto": "phone_visits",
            "messenger": "messenger_visits",
        }.get(str(row["goal_key"]))
        if field:
            target[field] = (target[field] or 0) + int(row["goal_visits"] or 0)
    result = []
    for item in groups.values():
        item["total_paid"] = _combine_money(item["primary_paid"], item["repeat_paid"])
        item["total_visits"] = None if item["primary_visits"] is None and item["repeat_visits"] is None else (item["primary_visits"] or 0) + (item["repeat_visits"] or 0)
        item["lead_to_primary_pct"] = item["leads_with_primary"] * 100.0 / item["target_leads"] if item["target_leads"] and item["leads_with_primary"] is not None else None
        result.append(item)
    sort_key = "campaign_name" if level == "campaign" else "source_name"
    return sorted(result, key=lambda item: (-(item["spend"] or 0), item.get(sort_key) or ""))


def _freshness(conn):
    rows = conn.execute(
        "SELECT dataset,MAX(generated_at) AS generated_at,MAX(imported_at) AS imported_at "
        "FROM advertising_report_imports GROUP BY dataset ORDER BY dataset"
    ).fetchall()
    labels = {"direct": "Реклама", "metrika": "Посещения сайта", "clinic": "CRM и клиника", "cohort": "Новые пациенты за 30 дней"}
    return [{"dataset": row["dataset"], "label": labels.get(row["dataset"], row["dataset"]), "generated_at": row["generated_at"], "imported_at": row["imported_at"]} for row in rows]



def _metrika_source(row):
    traffic_id = str(row.get("traffic_source_id") or "").strip()
    traffic_name = str(row.get("traffic_source_name") or "").strip()
    detail = str(row.get("source_detail") or "").strip()
    detail_id = str(row.get("source_detail_id") or "").strip()
    utm_source = str(row.get("utm_source") or "").strip().lower()
    utm_campaign = str(row.get("utm_campaign") or "").strip().lower()
    utm_content = str(row.get("utm_content") or "").strip().lower()

    if traffic_id == "ad":
        if utm_source == "geoadv_direct":
            return "yandex_business", "Яндекс Бизнес", "Платная реклама"
        return "yandex_ads", "Яндекс-реклама", "Платная реклама"
    if traffic_id == "direct":
        return "direct", "Прямые заходы", "Прямые заходы"
    if traffic_id == "organic":
        probe = (detail_id + " " + detail).lower()
        if "google" in probe:
            return "google_organic", "Google органика", "Поиск"
        if "яндекс" in probe or "yandex" in probe:
            return "yandex_organic", "Яндекс органика", "Поиск"
        return "organic", detail or traffic_name or "Поиск", "Поиск"
    if traffic_id == "social":
        if "instagram" in (detail + " " + utm_source).lower() or utm_source == "ig":
            return "instagram", "Instagram", "Социальные сети"
        return "social", detail or traffic_name or "Социальные сети", "Социальные сети"
    if traffic_id == "internal":
        return "internal", "Внутренние переходы", "Другие источники"
    if traffic_id == "referral":
        probe = (detail + " " + detail_id + " " + utm_source).lower()
        if "2gis" in probe:
            return "2gis", "2ГИС", "Карты"
        if "taplink" in probe:
            return "taplink", "Taplink", "Другие источники"
        if "napopravku" in probe:
            return "napopravku", "НаПоправку", "Другие источники"
        if "prodoctorov" in probe:
            return "prodoctorov", "ПроДокторов", "Другие источники"
        return "referral", detail or traffic_name or "Переходы по ссылкам", "Другие источники"
    key = re.sub(r"[^a-z0-9а-я]+", "_", (traffic_id or detail or traffic_name).lower()).strip("_") or "other"
    return key[:120], detail or traffic_name or "Другие источники", "Другие источники"


def _instagram_campaign_name(row):
    raw = str(row.get("utm_campaign") or row.get("utm_content") or "").strip()
    if not raw:
        return ""
    return INSTAGRAM_CAMPAIGN_LABELS.get(raw.lower(), raw)


def _canonical_metrika_identity(source_key, source_name, channel_name, campaign_id, campaign_name):
    source_key = str(source_key or "").strip()
    source_name = str(source_name or "").strip()
    channel_name = str(channel_name or "").strip()
    campaign_id = str(campaign_id or "").strip()
    campaign_name = str(campaign_name or "").strip()

    legacy_instagram_tag = LEGACY_INSTAGRAM_SOURCE_KEYS.get(source_key)
    if source_key == "instagram" or source_key in LEGACY_INSTAGRAM_SOURCE_KEYS:
        source_key = "instagram"
        source_name = "Instagram"
        channel_name = "Социальные сети"
        if not campaign_name and legacy_instagram_tag:
            campaign_name = legacy_instagram_tag
        if campaign_name:
            campaign_name = INSTAGRAM_CAMPAIGN_LABELS.get(campaign_name.lower(), campaign_name)
        campaign_id = ""
        return source_key, source_name, channel_name, campaign_id, campaign_name

    if source_key == "yandex_ads":
        source_name = "Яндекс-реклама"
        channel_name = "Платная реклама"

        # A non-empty campaign ID is authoritative evidence.  Known IDs may be
        # canonicalized, but an unknown ID must never be replaced merely
        # because its text name matches a known campaign.
        if campaign_id:
            if campaign_id in YANDEX_CANONICAL_CAMPAIGNS:
                return (
                    source_key,
                    source_name,
                    channel_name,
                    campaign_id,
                    YANDEX_CANONICAL_CAMPAIGNS[campaign_id],
                )
            return source_key, source_name, channel_name, campaign_id, campaign_name

        # Name / UTM aliases are only safe when the factual campaign ID is
        # absent.
        canonical_id = YANDEX_CAMPAIGN_ALIASES.get(campaign_name.lower(), "") if campaign_name else ""
        if canonical_id:
            return (
                source_key,
                source_name,
                channel_name,
                canonical_id,
                YANDEX_CANONICAL_CAMPAIGNS[canonical_id],
            )

    return source_key, source_name, channel_name, campaign_id, campaign_name


def _metrika_campaign_name(row, source_key):
    if source_key == "instagram":
        return _instagram_campaign_name(row)
    return str(row.get("direct_campaign_name") or row.get("utm_campaign") or "").strip()


def _parse_report_campaigns(report_text, payload):
    if not report_text:
        return []
    lines = [line.rstrip("\r") for line in report_text.splitlines()]
    try:
        index = next(i for i, line in enumerate(lines) if line.strip() == METRIKA_CAMPAIGN_REPORT_SECTION)
    except StopIteration:
        return []
    if index + 1 >= len(lines):
        return []
    metrics_line = lines[index + 1].strip()
    if not metrics_line.startswith("Метрики:"):
        return []
    metrics = [item.strip() for item in metrics_line.split(":", 1)[1].split(",") if item.strip()]
    if "ym:s:visits" not in metrics:
        return []
    goal_names = {
        str(item.get("goal_id") or ""): str(item.get("goal_name") or "")
        for item in (payload.get("goal_registry") or [])
        if isinstance(item, dict)
    }
    result = []
    for raw_line in lines[index + 2:]:
        line = raw_line.strip()
        if not line:
            break
        if line.startswith("Итого:"):
            continue
        if " | " not in line:
            return []
        campaign_name, values_text = line.rsplit(" | ", 1)
        values_raw = [item.strip() for item in values_text.split(",")]
        if len(values_raw) != len(metrics):
            return []
        try:
            values = [float(item) for item in values_raw]
        except ValueError:
            return []
        row = {
            "campaign_name": campaign_name.strip(),
            "visits": int(round(values[metrics.index("ym:s:visits")])),
            "goals": {},
        }
        for metric, value in zip(metrics, values):
            match = re.fullmatch(r"ym:s:goal(\d+)(reaches|visits)", metric)
            if not match:
                continue
            goal_name = goal_names.get(match.group(1), "")
            goal_key = METRIKA_GOALS.get(goal_name)
            if not goal_key:
                continue
            pair = row["goals"].setdefault(
                (goal_key, goal_name), {"goal_visits": 0, "reaches": 0}
            )
            pair["goal_visits" if match.group(2) == "visits" else "reaches"] = int(round(value))
        result.append(row)
    return result


def _apply_yandex_campaign_report(traffic_rows, goal_rows, completed_days, campaign_rows):
    if len(completed_days) != 1 or not campaign_rows:
        return traffic_rows, goal_rows
    day = completed_days[0]
    yandex_traffic = [
        (key, values)
        for key, values in traffic_rows.items()
        if key[0] == day and key[1] == "yandex_ads"
    ]
    if not yandex_traffic:
        return traffic_rows, goal_rows

    # campaigns_clean carries campaign names but not reliable IDs.  If the
    # structured traffic already contains an unknown non-empty ID, rebuilding
    # all Yandex rows from names could erase that factual distinction.  Fail
    # closed and preserve the structured rows instead.
    if any(
        key[4] and key[4] not in YANDEX_CANONICAL_CAMPAIGNS
        for key, _values in yandex_traffic
    ):
        return traffic_rows, goal_rows

    parent_visits = sum(int(values.get("visits") or 0) for _, values in yandex_traffic)
    selected = [
        row for row in campaign_rows
        if row["campaign_name"] not in METRIKA_CAMPAIGN_REPORT_IGNORED
        and int(row.get("visits") or 0) > 0
    ]
    named_visits = sum(int(row["visits"]) for row in selected)
    if not selected or named_visits > parent_visits:
        return traffic_rows, goal_rows

    parent_goals = {}
    for key, values in goal_rows.items():
        if key[0] != day or key[1] != "yandex_ads":
            continue
        bucket = parent_goals.setdefault(
            (key[6], key[7]), {"goal_visits": 0, "reaches": 0}
        )
        bucket["goal_visits"] += int(values.get("goal_visits") or 0)
        bucket["reaches"] += int(values.get("reaches") or 0)

    selected_goals = {}
    for row in selected:
        for goal, values in row.get("goals", {}).items():
            bucket = selected_goals.setdefault(goal, {"goal_visits": 0, "reaches": 0})
            bucket["goal_visits"] += int(values.get("goal_visits") or 0)
            bucket["reaches"] += int(values.get("reaches") or 0)
    for goal, values in selected_goals.items():
        parent = parent_goals.get(goal, {"goal_visits": 0, "reaches": 0})
        if values["goal_visits"] > parent["goal_visits"] or values["reaches"] > parent["reaches"]:
            return traffic_rows, goal_rows

    rebuilt_traffic = {
        key: values for key, values in traffic_rows.items()
        if not (key[0] == day and key[1] == "yandex_ads")
    }
    for row in selected:
        source_key, source_name, channel_name, campaign_id, campaign_name = _canonical_metrika_identity(
            "yandex_ads", "Яндекс-реклама", "Платная реклама", "", row["campaign_name"]
        )
        key = (day, source_key, source_name, channel_name, campaign_id, campaign_name)
        rebuilt_traffic[key] = {"visits": int(row["visits"])}
    residual_visits = parent_visits - named_visits
    if residual_visits:
        key = (day, "yandex_ads", "Яндекс-реклама", "Платная реклама", "", "")
        rebuilt_traffic[key] = {"visits": residual_visits}

    rebuilt_goals = {
        key: values for key, values in goal_rows.items()
        if not (key[0] == day and key[1] == "yandex_ads")
    }
    for row in selected:
        source_key, source_name, channel_name, campaign_id, campaign_name = _canonical_metrika_identity(
            "yandex_ads", "Яндекс-реклама", "Платная реклама", "", row["campaign_name"]
        )
        for (goal_key, goal_name), values in row.get("goals", {}).items():
            # A zero present in campaigns_clean is evidence, not missing data.
            # Keep the row so the UI renders 0 instead of «Нет данных».
            key = (
                day, source_key, source_name, channel_name,
                campaign_id, campaign_name, goal_key, goal_name,
            )
            rebuilt_goals[key] = {
                "goal_visits": int(values["goal_visits"]),
                "reaches": int(values["reaches"]),
            }
    for (goal_key, goal_name), parent in parent_goals.items():
        selected_values = selected_goals.get(
            (goal_key, goal_name), {"goal_visits": 0, "reaches": 0}
        )
        residual_goal_visits = parent["goal_visits"] - selected_values["goal_visits"]
        residual_reaches = parent["reaches"] - selected_values["reaches"]
        # parent_goals means this metric is known for the parent.  After
        # subtracting the named campaigns, zero is therefore a proven residual
        # value and must remain 0 rather than becoming unknown.
        key = (
            day, "yandex_ads", "Яндекс-реклама", "Платная реклама",
            "", "", goal_key, goal_name,
        )
        rebuilt_goals[key] = {
            "goal_visits": residual_goal_visits,
            "reaches": residual_reaches,
        }
    return rebuilt_traffic, rebuilt_goals


def _parse_metrika_zip(raw, filename=""):
    if not raw:
        raise ValueError("Пустой ZIP Яндекс Метрики.")
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("ZIP Яндекс Метрики превышает 25 МБ.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if names.count("az_analytics.json") != 1:
                raise ValueError("В ZIP должен быть ровно один az_analytics.json.")
            if len(names) > 50:
                raise ValueError("В ZIP слишком много файлов.")
            payload = json.loads(archive.read("az_analytics.json").decode("utf-8"))
            report_names = [
                name for name in names
                if re.fullmatch(r"REPORT-\d{4}-\d{2}-\d{2}\.txt", Path(name).name)
            ]
            report_text = (
                archive.read(report_names[0]).decode("utf-8-sig")
                if len(report_names) == 1 else ""
            )
    except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Не удалось прочитать ZIP Яндекс Метрики.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Некорректный az_analytics.json.")
    meta = payload.get("metadata") or {}
    if meta.get("schema_version") != METRIKA_EXPORT_SCHEMA:
        raise ValueError("Неизвестная версия выгрузки Яндекс Метрики.")
    if str(meta.get("counter_id") or "") != METRIKA_COUNTER_ID:
        raise ValueError("Выгрузка относится к другому счётчику Метрики.")
    if str(meta.get("source_timezone") or "") != DEFAULT_TIMEZONE:
        raise ValueError("Неверный часовой пояс выгрузки Метрики.")
    if float(meta.get("sample_share") or 0) != 1.0:
        raise ValueError("Выгрузка Метрики содержит семплирование.")
    traffic = payload.get("metrica_traffic_day")
    goals = payload.get("metrica_goal_day")
    if not isinstance(traffic, list) or not isinstance(goals, list):
        raise ValueError("В выгрузке Метрики нет обязательных таблиц.")

    completed_days = sorted({
        str(row.get("date") or "")
        for row in traffic
        if str(row.get("day_status") or "").startswith("completed_calendar_day")
    })
    if not completed_days:
        raise ValueError("В ZIP нет завершённых календарных дней.")

    traffic_by_id = {}
    traffic_rows = {}
    for row in traffic:
        day = str(row.get("date") or "")
        if day not in completed_days:
            continue
        traffic_by_id[str(row.get("row_id") or "")] = row
        if bool(row.get("is_test_tag")):
            continue
        source_key, source_name, channel_name = _metrika_source(row)
        campaign_id = str(row.get("direct_campaign_id") or "")
        campaign_name = _metrika_campaign_name(row, source_key)
        source_key, source_name, channel_name, campaign_id, campaign_name = _canonical_metrika_identity(
            source_key, source_name, channel_name, campaign_id, campaign_name
        )
        key = (day, source_key, source_name, channel_name, campaign_id, campaign_name)
        target = traffic_rows.setdefault(key, {"visits": 0})
        target["visits"] += int(row.get("visits") or 0)

    goal_rows = {}
    for row in goals:
        day = str(row.get("date") or "")
        if day not in completed_days:
            continue
        traffic_row = traffic_by_id.get(str(row.get("traffic_row_id") or ""))
        if not traffic_row or bool(traffic_row.get("is_test_tag")):
            continue
        goal_name = str(row.get("goal_name") or "")
        goal_key = METRIKA_GOALS.get(goal_name)
        if not goal_key:
            continue
        source_key, source_name, channel_name = _metrika_source(traffic_row)
        campaign_id = str(traffic_row.get("direct_campaign_id") or "")
        campaign_name = _metrika_campaign_name(traffic_row, source_key)
        source_key, source_name, channel_name, campaign_id, campaign_name = _canonical_metrika_identity(
            source_key, source_name, channel_name, campaign_id, campaign_name
        )
        key = (day, source_key, source_name, channel_name, campaign_id, campaign_name, goal_key, goal_name)
        target = goal_rows.setdefault(key, {"goal_visits": 0, "reaches": 0})
        target["goal_visits"] += int(row.get("goal_visits") or 0)
        target["reaches"] += int(row.get("reaches") or 0)

    report_campaigns = _parse_report_campaigns(report_text, payload)
    traffic_rows, goal_rows = _apply_yandex_campaign_report(
        traffic_rows, goal_rows, completed_days, report_campaigns
    )

    return {
        "metadata": meta,
        "completed_days": completed_days,
        "traffic_rows": traffic_rows,
        "goal_rows": goal_rows,
        "payload_sha256": hashlib.sha256(raw).hexdigest(),
        "raw": raw,
        "filename": filename or "metrika.zip",
    }


def _archive_metrika(raw, payload_sha256):
    METRIKA_ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    final = METRIKA_ARCHIVE_ROOT / (payload_sha256 + ".zip")
    if not final.exists():
        tmp = METRIKA_ARCHIVE_ROOT / (payload_sha256 + ".zip.tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, final)
    return str(final)


def _stored_metrika_matches(conn, parsed):
    days = list(parsed.get("completed_days") or [])
    if not days:
        return False
    placeholders = ",".join("?" for _ in days)

    actual_traffic = {}
    rows = conn.execute(
        f"""
        SELECT metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,visits
        FROM advertising_traffic_daily
        WHERE provider='metrika' AND metric_date IN ({placeholders})
        """,
        days,
    ).fetchall()
    for row in rows:
        key = (
            str(row["metric_date"]), str(row["source_key"]), str(row["source_name"]),
            str(row["channel_name"] or ""), str(row["campaign_id"] or ""),
            str(row["campaign_name"] or ""),
        )
        actual_traffic[key] = actual_traffic.get(key, 0) + int(row["visits"] or 0)

    expected_traffic = {
        key: int(values["visits"])
        for key, values in (parsed.get("traffic_rows") or {}).items()
    }
    if actual_traffic != expected_traffic:
        return False

    actual_goals = {}
    rows = conn.execute(
        f"""
        SELECT metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,
               goal_key,goal_name,goal_visits,reaches
        FROM advertising_metrika_goal_daily
        WHERE provider='metrika' AND metric_date IN ({placeholders})
        """,
        days,
    ).fetchall()
    for row in rows:
        key = (
            str(row["metric_date"]), str(row["source_key"]), str(row["source_name"]),
            str(row["channel_name"] or ""), str(row["campaign_id"] or ""),
            str(row["campaign_name"] or ""), str(row["goal_key"]), str(row["goal_name"]),
        )
        actual_goals[key] = {
            "goal_visits": int(row["goal_visits"] or 0),
            "reaches": int(row["reaches"] or 0),
        }

    expected_goals = {
        key: {
            "goal_visits": int(values["goal_visits"]),
            "reaches": int(values["reaches"]),
        }
        for key, values in (parsed.get("goal_rows") or {}).items()
    }
    return actual_goals == expected_goals


def _store_metrika(conn, parsed, actor_user_id):
    sha = parsed["payload_sha256"]
    existing = conn.execute(
        "SELECT payload_sha256 FROM advertising_metrika_imports WHERE payload_sha256=?",
        (sha,),
    ).fetchone()
    if existing and _stored_metrika_matches(conn, parsed):
        return {
            "reused": True,
            "reclassified": False,
            "completed_days": parsed["completed_days"],
            "payload_sha256": sha,
        }

    archive_path = _archive_metrika(parsed["raw"], sha)
    imported_at = iso_now()
    import_id = "metrika-" + sha[:16]
    for day in parsed["completed_days"]:
        conn.execute("DELETE FROM advertising_traffic_daily WHERE provider='metrika' AND metric_date=?", (day,))
        conn.execute("DELETE FROM advertising_metrika_goal_daily WHERE provider='metrika' AND metric_date=?", (day,))

    for key, values in parsed["traffic_rows"].items():
        day, source_key, source_name, channel_name, campaign_id, campaign_name = key
        conn.execute(
            """
            INSERT INTO advertising_traffic_daily(
                provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,
                spend,clicks,visits,updated_at,import_id
            ) VALUES('metrika',?,?,?,?,?,?,NULL,NULL,?,?,?)
            """,
            (day, source_key, source_name, channel_name, campaign_id, campaign_name,
             int(values["visits"]), imported_at, import_id),
        )

    for key, values in parsed["goal_rows"].items():
        day, source_key, source_name, channel_name, campaign_id, campaign_name, goal_key, goal_name = key
        conn.execute(
            """
            INSERT INTO advertising_metrika_goal_daily(
                provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,
                goal_key,goal_name,goal_visits,reaches,updated_at,import_id
            ) VALUES('metrika',?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (day, source_key, source_name, channel_name, campaign_id, campaign_name,
             goal_key, goal_name, int(values["goal_visits"]), int(values["reaches"]), imported_at, import_id),
        )

    meta = parsed["metadata"]
    start, end = parsed["completed_days"][0], parsed["completed_days"][-1]
    if not existing:
        conn.execute(
            """
            INSERT INTO advertising_metrika_imports(
                payload_sha256,original_filename,period_start,period_end,completed_days_json,
                generated_at,imported_at,actor_user_id,traffic_rows,goal_rows,archive_path
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (sha, parsed["filename"], start, end, json.dumps(parsed["completed_days"]),
             str(meta.get("fetched_at_utc") or ""), imported_at, actor_user_id,
             len(parsed["traffic_rows"]), len(parsed["goal_rows"]), archive_path),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO advertising_report_imports(
                payload_sha256,dataset,period_start,period_end,generated_at,timezone,currency,
                vat_mode,attribution_model,imported_at,actor_user_id,row_count
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (sha, "metrika", start, end, str(meta.get("fetched_at_utc") or ""),
             str(meta.get("source_timezone") or DEFAULT_TIMEZONE), "RUB", "",
             str(meta.get("attribution") or ""), imported_at, actor_user_id, len(parsed["traffic_rows"])),
        )

    return {
        "reused": False,
        "reclassified": bool(existing),
        "completed_days": parsed["completed_days"],
        "payload_sha256": sha,
    }

def _metrika_goal_summary(conn, start, end):
    try:
        rows = conn.execute(
            """
            SELECT goal_key,SUM(goal_visits) AS goal_visits,SUM(reaches) AS reaches
            FROM advertising_metrika_goal_daily
            WHERE provider='metrika' AND metric_date BETWEEN ? AND ?
            GROUP BY goal_key
            """,
            (_iso(start), _iso(end)),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    result = {
        "booking_open_visits": None, "booking_open_reaches": None,
        "medflex_visits": None, "medflex_reaches": None,
        "phone_visits": None, "messenger_visits": None,
    }
    for row in rows:
        key = str(row["goal_key"])
        if key == "booking_open":
            result["booking_open_visits"] = int(row["goal_visits"] or 0)
            result["booking_open_reaches"] = int(row["reaches"] or 0)
        elif key == "medflex":
            result["medflex_visits"] = int(row["goal_visits"] or 0)
            result["medflex_reaches"] = int(row["reaches"] or 0)
        elif key in {"phone", "phone_auto"}:
            result["phone_visits"] = (result["phone_visits"] or 0) + int(row["goal_visits"] or 0)
        elif key == "messenger":
            result["messenger_visits"] = int(row["goal_visits"] or 0)
    return result


def _mis_revenue_daily(conn):
    try:
        rows = conn.execute(
            """
            SELECT period_start,period_end,paid_total,detailed_paid_total
            FROM advertising_mis_paid_ingests
            WHERE period_end>=?
            ORDER BY period_start,period_end
            """,
            ("2026-10-27",),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    grouped = {}
    for row in rows:
        grouped.setdefault(str(row["period_start"]), []).append(row)
    evidence = {}
    for period_start, items in grouped.items():
        previous = None
        previous_end = None
        for row in items:
            period_end = str(row["period_end"])
            candidate = None
            rank = None
            if period_start == period_end:
                candidate = (float(row["paid_total"] or 0), float(row["detailed_paid_total"] or 0))
                rank = 2
            elif previous is not None:
                expected = (date.fromisoformat(previous_end) + timedelta(days=1)).isoformat()
                if expected == period_end:
                    candidate = (
                        max(0.0, float(row["paid_total"] or 0) - float(previous["paid_total"] or 0)),
                        max(0.0, float(row["detailed_paid_total"] or 0) - float(previous["detailed_paid_total"] or 0)),
                    )
                    rank = 1
            if candidate is not None:
                score = (rank, -date.fromisoformat(period_start).toordinal())
                old = evidence.get(period_end)
                if old is None or score > old["score"]:
                    evidence[period_end] = {
                        "score": score,
                        "paid_total": round(candidate[0], 2),
                        "detailed_paid_total": round(candidate[1], 2),
                    }
            previous = row
            previous_end = period_end
    return evidence


def _provider_paid_daily(conn):
    """Daily paid evidence from provider-level totals in the same MIS report.

    Exact one-day reports are authoritative. For cumulative sequential reports,
    use provider-by-provider deltas. These totals can attribute money to a
    provider even when the export has no patient/service detail for that payment.
    """
    try:
        rows = conn.execute(
            """
            SELECT period_start,period_end,payload_json
            FROM service_payment_periods
            WHERE period_end>=?
            ORDER BY period_start,period_end
            """,
            ("2026-10-27",),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    grouped = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        grouped.setdefault(str(row["period_start"]), []).append(
            (str(row["period_end"]), payload)
        )

    evidence = {}
    for period_start, items in grouped.items():
        previous = None
        previous_end = None
        for period_end, payload in items:
            providers = {
                str(name): float((values or {}).get("paid") or 0)
                for name, values in (payload.get("providers") or {}).items()
            }
            residual = float((payload.get("provider_residual") or {}).get("paid") or 0)
            selected = None
            selected_residual = None
            rank = None

            if period_start == period_end:
                selected = {name: max(0.0, round(value, 2)) for name, value in providers.items()}
                selected_residual = max(0.0, round(residual, 2))
                rank = 2
            elif previous is not None:
                expected = (date.fromisoformat(previous_end) + timedelta(days=1)).isoformat()
                if expected == period_end:
                    names = set(previous["providers"]) | set(providers)
                    selected = {
                        name: max(
                            0.0,
                            round(
                                float(providers.get(name) or 0)
                                - float(previous["providers"].get(name) or 0),
                                2,
                            ),
                        )
                        for name in names
                    }
                    selected_residual = max(
                        0.0,
                        round(residual - float(previous["residual"] or 0), 2),
                    )
                    rank = 1

            if selected is not None:
                score = (rank, -date.fromisoformat(period_start).toordinal())
                old = evidence.get(period_end)
                if old is None or score > old["score"]:
                    evidence[period_end] = {
                        "score": score,
                        "providers": selected,
                        "provider_residual": selected_residual,
                    }

            previous = {"providers": providers, "residual": residual}
            previous_end = period_end

    return evidence


def _provider_registry(conn):
    try:
        rows = conn.execute(
            """
            SELECT source_name,display_name,direction
            FROM provider_registry
            ORDER BY source_name
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {
        str(row["source_name"]): {
            "display_name": str(row["display_name"] or row["source_name"]),
            "direction": str(row["direction"] or "pending"),
        }
        for row in rows
    }


def _provider_revenue_summary(conn, start, end):
    start_s, end_s = _iso(start), _iso(end)
    daily = _provider_paid_daily(conn)
    selected = [value for day, value in daily.items() if start_s <= day <= end_s]
    if not selected:
        return {
            "provider_paid_total": None,
            "provider_residual": None,
            "provider_balance_diff": None,
            "direction_paid": {"dent": None, "structure": None, "lab": None, "other": None},
            "providers": [],
        }

    paid_by_provider = {}
    provider_residual = 0.0
    for item in selected:
        provider_residual += float(item.get("provider_residual") or 0)
        for name, amount in (item.get("providers") or {}).items():
            paid_by_provider[name] = paid_by_provider.get(name, 0.0) + float(amount or 0)

    registry = _provider_registry(conn)
    direction_paid = {"dent": 0.0, "structure": 0.0, "lab": 0.0, "other": 0.0}
    provider_rows = []
    for source_name, amount in paid_by_provider.items():
        amount = round(amount, 2)
        meta = registry.get(source_name) or {}
        direction = str(meta.get("direction") or "pending")
        bucket = direction if direction in {"dent", "structure", "lab"} else "other"
        direction_paid[bucket] += amount
        provider_rows.append(
            {
                "source_name": source_name,
                "display_name": str(meta.get("display_name") or source_name),
                "direction": direction,
                "paid": amount,
            }
        )

    provider_rows.sort(key=lambda row: (-row["paid"], row["display_name"]))
    direction_paid = {key: round(value, 2) for key, value in direction_paid.items()}
    provider_paid_total = round(sum(paid_by_provider.values()), 2)
    provider_residual = round(provider_residual, 2)

    return {
        "provider_paid_total": provider_paid_total,
        "provider_residual": provider_residual,
        "provider_balance_diff": None,
        "direction_paid": direction_paid,
        "providers": provider_rows,
    }


def _revenue_summary(conn, start, end):
    start_s, end_s = _iso(start), _iso(end)
    daily = _mis_revenue_daily(conn)
    selected = [v for day, v in daily.items() if start_s <= day <= end_s]
    paid_total = round(sum(v["paid_total"] for v in selected), 2) if selected else None
    detailed_total = round(sum(v["detailed_paid_total"] for v in selected), 2) if selected else None

    provider = _provider_revenue_summary(conn, start, end)
    if paid_total is not None and provider["provider_paid_total"] is not None:
        provider["provider_balance_diff"] = round(
            paid_total
            - provider["provider_paid_total"]
            - float(provider["provider_residual"] or 0),
            2,
        )

    try:
        row = conn.execute(
            """
            SELECT SUM(primary_paid) AS primary_paid,SUM(repeat_paid) AS repeat_paid
            FROM advertising_clinic_daily
            WHERE provider='mis' AND metric_date BETWEEN ? AND ?
            """,
            (start_s, end_s),
        ).fetchone()
        primary_paid = row["primary_paid"] if row else None
        repeat_paid = row["repeat_paid"] if row else None
    except sqlite3.OperationalError:
        primary_paid = repeat_paid = None

    classified = (
        None
        if primary_paid is None and repeat_paid is None
        else round(float(primary_paid or 0) + float(repeat_paid or 0), 2)
    )
    unclassified_detailed = (
        None
        if detailed_total is None or classified is None
        else round(max(0.0, detailed_total - classified), 2)
    )
    provider_known_without_patient_detail = (
        None
        if paid_total is None or detailed_total is None
        else round(max(0.0, paid_total - detailed_total), 2)
    )

    cash = {"cash_total": None, "cash_ooo": None, "cash_ip": None, "cash_unallocated": None}
    try:
        rows = conn.execute(
            "SELECT payload_json FROM cash_receipts_daily WHERE data_date BETWEEN ? AND ?",
            (start_s, end_s),
        ).fetchall()
        if rows:
            totals = {k: 0.0 for k in cash}
            for row in rows:
                payload = json.loads(row["payload_json"])
                totals["cash_total"] += float(payload.get("cashTotal") or 0)
                totals["cash_ooo"] += float(payload.get("cashOOO") or 0)
                totals["cash_ip"] += float(payload.get("cashIP") or 0)
                totals["cash_unallocated"] += float(payload.get("cashUnallocated") or 0)
            cash = {k: round(v, 2) for k, v in totals.items()}
    except (sqlite3.OperationalError, TypeError, ValueError):
        pass

    return {
        "clinic_paid_total": paid_total,
        "detailed_paid_total": detailed_total,
        "provider_known_without_patient_detail": provider_known_without_patient_detail,
        # Backward-compatible alias. UI must use the clearer field above.
        "without_line_detail": provider_known_without_patient_detail,
        "primary_paid": None if primary_paid is None else round(float(primary_paid), 2),
        "repeat_paid": None if repeat_paid is None else round(float(repeat_paid), 2),
        "unclassified_detailed": unclassified_detailed,
        **provider,
        **cash,
        "days_with_mis_revenue": len(selected),
    }


def _crm_status(conn, start, end):
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM advertising_clinic_daily
            WHERE provider='crm' AND metric_date BETWEEN ? AND ?
              AND (target_leads IS NOT NULL OR leads_with_primary IS NOT NULL)
            """,
            (_iso(start), _iso(end)),
        ).fetchone()
        available = bool(row and row["n"])
    except sqlite3.OperationalError:
        available = False
    return {
        "available": available,
        "message": "" if available else "Не хватает данных CRM для построения отчёта",
    }


def _clean_text(value, required=False, max_len=240):
    if value is None:
        if required:
            raise ValueError("missing text")
        return ""
    value = str(value).strip()
    if required and not value:
        raise ValueError("missing text")
    if len(value) > max_len:
        raise ValueError("text too long")
    return value


def _number(value, integer=False):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid number")
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ValueError("invalid number")
    if not math.isfinite(number) or number < 0:
        raise ValueError("invalid number")
    if integer and float(value) != number:
        raise ValueError("invalid integer")
    return number


def _normalize_row(dataset, row, period_start, period_end):
    if not isinstance(row, dict):
        raise ValueError("invalid row")
    date_key = "cohort_date" if dataset == "cohort" else "date"
    day = _parse_date(row.get(date_key))
    if not period_start <= day <= period_end:
        raise ValueError("row outside period")
    base = {
        "date": _iso(day),
        "source_key": _clean_text(row.get("source_key"), required=True, max_len=120),
        "source_name": _clean_text(row.get("source_name"), required=True, max_len=240),
        "channel_name": _clean_text(row.get("channel_name"), max_len=240),
        "campaign_id": _clean_text(row.get("campaign_id"), max_len=160),
        "campaign_name": _clean_text(row.get("campaign_name"), max_len=300),
    }
    if dataset == "direct":
        base.update(spend=_number(row.get("spend")), clicks=_number(row.get("clicks"), integer=True))
    elif dataset == "metrika":
        base.update(visits=_number(row.get("visits"), integer=True))
    elif dataset == "clinic":
        base.update(
            target_leads=_number(row.get("target_leads"), integer=True),
            leads_with_primary=_number(row.get("leads_with_primary"), integer=True),
            primary_visits=_number(row.get("primary_visits"), integer=True),
            primary_paid=_number(row.get("primary_paid")), primary_services=_number(row.get("primary_services")),
            repeat_visits=_number(row.get("repeat_visits"), integer=True), repeat_paid=_number(row.get("repeat_paid")),
        )
        if base["leads_with_primary"] is not None and base["target_leads"] is not None and base["leads_with_primary"] > base["target_leads"]:
            raise ValueError("converted leads exceed target leads")
    elif dataset == "cohort":
        base.update(new_patients=_number(row.get("new_patients"), integer=True), new_patient_30d_paid=_number(row.get("new_patient_30d_paid")))
    return base


def _normalize_package(payload):
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid schema")
    generated_at = _clean_text(payload.get("generated_at"), required=True, max_len=80)
    timezone = _clean_text(payload.get("timezone") or DEFAULT_TIMEZONE, required=True, max_len=80)
    currency = _clean_text(payload.get("currency") or "RUB", max_len=16)
    vat_mode = _clean_text(payload.get("vat_mode"), max_len=120)
    attribution_model = _clean_text(payload.get("attribution_model"), max_len=160)
    datasets = payload.get("datasets")
    if not isinstance(datasets, dict) or not datasets:
        raise ValueError("missing datasets")
    normalized = {}
    for name, spec in datasets.items():
        if name not in DATASET_CONFIG or not isinstance(spec, dict):
            raise ValueError("invalid dataset")
        start = _parse_date(spec.get("period_start")); end = _parse_date(spec.get("period_end"))
        if start > end:
            raise ValueError("invalid period")
        rows = spec.get("rows")
        if not isinstance(rows, list) or len(rows) > 200000:
            raise ValueError("invalid rows")
        normalized[name] = {"period_start": _iso(start), "period_end": _iso(end), "rows": [_normalize_row(name, row, start, end) for row in rows]}
    return {"schema_version": SCHEMA_VERSION, "generated_at": generated_at, "timezone": timezone, "currency": currency, "vat_mode": vat_mode, "attribution_model": attribution_model, "datasets": normalized}


def _canonical_sha(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _apply_import(conn, normalized, actor_user_id):
    payload_sha = _canonical_sha(normalized)
    datasets = normalized["datasets"]
    if datasets and all(conn.execute("SELECT 1 FROM advertising_report_imports WHERE payload_sha256=? AND dataset=?", (payload_sha, name)).fetchone() for name in datasets):
        return {"reused": True, "payload_sha256": payload_sha, "datasets": sorted(datasets)}
    now = iso_now()
    for name, spec in datasets.items():
        table, date_col = DATASET_CONFIG[name]
        conn.execute(f"DELETE FROM {table} WHERE provider=? AND {date_col} BETWEEN ? AND ?", (name, spec["period_start"], spec["period_end"]))
        for row in spec["rows"]:
            common = (name, row["date"], row["source_key"], row["source_name"], row["channel_name"], row["campaign_id"], row["campaign_name"])
            if name == "direct":
                conn.execute("INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (*common, row["spend"], row["clicks"], None, now, payload_sha))
            elif name == "metrika":
                conn.execute("INSERT INTO advertising_traffic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (*common, None, None, row["visits"], now, payload_sha))
            elif name == "clinic":
                conn.execute("INSERT INTO advertising_clinic_daily(provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,target_leads,leads_with_primary,primary_visits,primary_paid,primary_services,repeat_visits,repeat_paid,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*common, row["target_leads"], row["leads_with_primary"], row["primary_visits"], row["primary_paid"], row["primary_services"], row["repeat_visits"], row["repeat_paid"], now, payload_sha))
            else:
                conn.execute("INSERT INTO advertising_cohort_daily(provider,cohort_date,source_key,source_name,channel_name,campaign_id,campaign_name,new_patients,new_patient_30d_paid,updated_at,import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (*common, row["new_patients"], row["new_patient_30d_paid"], now, payload_sha))
        conn.execute("INSERT OR REPLACE INTO advertising_report_imports(payload_sha256,dataset,period_start,period_end,generated_at,timezone,currency,vat_mode,attribution_model,imported_at,actor_user_id,row_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (payload_sha, name, spec["period_start"], spec["period_end"], normalized["generated_at"], normalized["timezone"], normalized["currency"], normalized["vat_mode"], normalized["attribution_model"], now, actor_user_id, len(spec["rows"])))
    return {"reused": False, "payload_sha256": payload_sha, "datasets": sorted(datasets)}


def _today():
    timezone = DEFAULT_TIMEZONE
    try:
        row = db().execute("SELECT value FROM app_settings WHERE key='clinic_timezone' LIMIT 1").fetchone()
        if row and row["value"]:
            timezone = str(row["value"])
    except sqlite3.OperationalError:
        pass
    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except Exception:
        return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()


def _coverage_summary(conn, start, end):
    """Visible source coverage for the selected advertising-report period."""
    start_s, end_s = _iso(start), _iso(end)
    tracking_start = date.fromisoformat("2026-10-27")
    expected_start = max(start, tracking_start)
    expected_days = max(0, (end - expected_start).days + 1) if end >= expected_start else 0

    def pack(days, complete=True):
        values = sorted({
            str(day)
            for day in days
            if isinstance(day, str) and start_s <= day <= end_s
        })
        expected = {
            (expected_start + timedelta(days=offset)).isoformat()
            for offset in range(expected_days)
        }
        return {
            "from": values[0] if values else None,
            "to": values[-1] if values else None,
            "days": len(values),
            "expectedFrom": _iso(expected_start) if expected_days else None,
            "expectedTo": end_s if expected_days else None,
            "expectedDays": expected_days,
            "complete": bool(expected_days and expected.issubset(set(values))) if complete else None,
        }

    metrika_days = set()
    try:
        rows = conn.execute(
            "SELECT completed_days_json FROM advertising_metrika_imports"
        ).fetchall()
        for row in rows:
            try:
                values = json.loads(row["completed_days_json"])
            except (TypeError, ValueError):
                continue
            if isinstance(values, list):
                metrika_days.update(str(value) for value in values)
    except sqlite3.OperationalError:
        pass
    if not metrika_days:
        try:
            rows = conn.execute(
                "SELECT DISTINCT metric_date FROM advertising_traffic_daily "
                "WHERE provider='metrika' AND metric_date BETWEEN ? AND ?",
                (start_s, end_s),
            ).fetchall()
            metrika_days.update(str(row["metric_date"]) for row in rows)
        except sqlite3.OperationalError:
            pass

    clinic_days = set()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT metric_date
            FROM advertising_clinic_daily
            WHERE provider='mis' AND metric_date BETWEEN ? AND ?
              AND (primary_visits IS NOT NULL OR repeat_visits IS NOT NULL)
            """,
            (start_s, end_s),
        ).fetchall()
        clinic_days.update(str(row["metric_date"]) for row in rows)
    except sqlite3.OperationalError:
        pass

    revenue_days = {
        day for day in _mis_revenue_daily(conn)
        if start_s <= day <= end_s
    }

    cash_days = set()
    try:
        rows = conn.execute(
            "SELECT data_date FROM cash_receipts_daily "
            "WHERE data_date BETWEEN ? AND ? ORDER BY data_date",
            (start_s, end_s),
        ).fetchall()
        cash_days.update(str(row["data_date"]) for row in rows)
    except sqlite3.OperationalError:
        pass

    return {
        "trackingStart": _iso(tracking_start),
        "metrika": pack(metrika_days),
        "clinic": pack(clinic_days),
        "misPaid": pack(revenue_days),
        # No-operation cash dates may be absent from cash_receipts_daily, so this
        # is deliberately shown as operation-day evidence, not completeness.
        "cash": pack(cash_days, complete=False),
    }


def _report_payload(conn, start, end, compare_mode, trend_metric):
    current = _summary(conn, start, end)
    ranges = _comparison_ranges(compare_mode, start, end)
    comparison = _average_summaries([_summary(conn, a, b) for a, b in ranges]) if ranges else {key: None for key in current}
    deltas = {key: _delta_pct(current.get(key), comparison.get(key)) for key in current if key != "new_patient_30d_paid"}
    return {
        "period": {"from": _iso(start), "to": _iso(end)},
        "compare": {"mode": compare_mode, "label": _comparison_label(compare_mode), "ranges": [{"from": _iso(a), "to": _iso(b)} for a, b in ranges], "summary": comparison, "delta_pct": deltas},
        "summary": current,
        "activity": {"primary_visits": current["primary_visits"], "primary_paid": current["primary_paid"], "repeat_visits": current["repeat_visits"], "repeat_paid": current["repeat_paid"], "total_visits": current["total_visits"], "total_paid": current["total_paid"]},
        "sources": _group_breakdown(conn, start, end, "source"),
        "campaigns": _group_breakdown(conn, start, end, "campaign"),
        "trend": _trend(conn, start, end, trend_metric, ranges),
        "freshness": _freshness(conn),
        "coverage": _coverage_summary(conn, start, end),
        "revenue": _revenue_summary(conn, start, end),
        "crm": _crm_status(conn, start, end),
    }


def register_advertising_report(app):
    _ensure_schema()

    @app.get("/reports/advertising/")
    @permission_required("reports")
    def advertising_report_page():
        path = SITE_ROOT / "reports" / "advertising.html"
        if not path.exists():
            abort(404)
        return Response(path.read_text(encoding="utf-8"), mimetype="text/html")

    @app.get("/api/reports/advertising")
    @permission_required("reports")
    def advertising_report_data():
        today = _today(); default_start = today.replace(day=1)
        try:
            start = _parse_date(request.args.get("from") or _iso(default_start)); end = _parse_date(request.args.get("to") or _iso(today))
        except ValueError:
            abort(400)
        if start > end or (end - start).days > 730:
            abort(400)
        compare_mode = request.args.get("compare", "previous_period")
        if compare_mode not in {"none", "previous_period", "previous_quarter_avg", "year_avg", "last_year"}:
            abort(400)
        trend_metric = request.args.get("trend", "target_leads")
        if trend_metric not in {"spend", "visits", "target_leads", "primary_visits", "total_paid"}:
            abort(400)
        payload = _report_payload(db(), start, end, compare_mode, trend_metric)
        payload["today"] = _iso(today)
        payload["availableComparisons"] = ["previous_period", *(["previous_quarter_avg", "year_avg"] if _same_month(start, end) else []), "last_year"]
        return jsonify(payload)

    @app.post("/api/reports/advertising/metrika/sync")
    @permission_required("section5")
    def advertising_metrika_sync():
        require_csrf()
        token = metrika_auto.token_from_env()
        if not token:
            return jsonify(
                status="error",
                message="Яндекс Метрика ещё не подключена к серверу. Нужна однократная авторизация.",
            ), 400

        conn = db()
        days = metrika_auto.missing_completed_days(conn)
        if not days:
            return jsonify(
                status="ok",
                message="Данные Яндекс Метрики уже актуальны. Новых завершённых дней нет.",
                completed_days=[],
                imported=0,
            )

        try:
            client = metrika_auto.MetrikaClient(token)
            prepared = []
            for day in days:
                raw, filename = client.fetch_day_archive(day)
                parsed = _parse_metrika_zip(raw, filename)
                if parsed["completed_days"] != [day.isoformat()]:
                    raise ValueError("API Метрики вернул неожиданный период.")
                prepared.append(parsed)

            conn.execute("BEGIN IMMEDIATE")
            results = []
            for parsed in prepared:
                results.append(_store_metrika(conn, parsed, g.user["id"]))
            conn.commit()
        except (metrika_auto.MetrikaApiError, ValueError, OSError, sqlite3.DatabaseError) as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            return jsonify(
                status="error",
                message=str(exc) or "Не удалось получить данные Яндекс Метрики.",
            ), 400

        completed_days = [
            day
            for result in results
            for day in result["completed_days"]
        ]
        audit(
            "advertising_metrika_auto_sync",
            target_user_id=g.user["id"],
            details=(
                "days=" + ",".join(completed_days)
                + "; imported=" + str(sum(0 if result["reused"] else 1 for result in results))
            ),
        )
        return jsonify(
            status="ok",
            message=(
                "Данные Яндекс Метрики загружены автоматически."
                if completed_days
                else "Данные Яндекс Метрики уже актуальны."
            ),
            completed_days=completed_days,
            imported=sum(0 if result["reused"] else 1 for result in results),
        )

    @app.post("/api/reports/advertising/metrika/import")
    @permission_required("section5")
    def advertising_metrika_import():
        require_csrf()
        uploaded = request.files.get("metrika_zip")
        if not uploaded or not uploaded.filename:
            return jsonify(status="error", message="Выберите ZIP Яндекс Метрики."), 400
        try:
            parsed = _parse_metrika_zip(uploaded.read(), uploaded.filename)
            conn = db()
            conn.execute("BEGIN IMMEDIATE")
            result = _store_metrika(conn, parsed, g.user["id"])
            conn.commit()
        except (ValueError, OSError, sqlite3.DatabaseError) as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            return jsonify(status="error", message=str(exc) or "Не удалось загрузить Метрику."), 400
        audit(
            "advertising_metrika_imported",
            target_user_id=g.user["id"],
            details=(
                "days=" + ",".join(result["completed_days"])
                + "; reused=" + str(result["reused"]).lower()
                + "; reclassified=" + str(result.get("reclassified", False)).lower()
            ),
        )
        return jsonify(
            status="ok",
            message=(
                "Метрика уже загружена."
                if result["reused"]
                else (
                    "Метрика пересобрана по обновлённой классификации источников."
                    if result.get("reclassified")
                    else "Метрика загружена. В отчёт включены только завершённые дни."
                )
            ),
            **result,
        )

    @app.post("/api/reports/advertising/import")
    @admin_required
    def advertising_report_import():
        require_csrf()
        conn = db()
        try:
            normalized = _normalize_package(request.get_json(silent=True))
            conn.execute("BEGIN IMMEDIATE")
            result = _apply_import(conn, normalized, g.user["id"])
            conn.commit()
        except (TypeError, ValueError, sqlite3.DatabaseError):
            conn.rollback()
            abort(400)
        audit("advertising_report_imported", target_user_id=g.user["id"], details="datasets=" + ",".join(result["datasets"]) + "; reused=" + str(result["reused"]).lower())
        return jsonify(ok=True, **result)
