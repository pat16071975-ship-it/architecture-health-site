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
import advertising_mis_bridge

from flask import Response, abort, g, jsonify, request

from app import DB_PATH, SITE_ROOT, admin_required, audit, csrf_token, db, iso_now, permission_required, require_csrf, user_permissions


SCHEMA_VERSION = 1
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_TIMEZONE = "Asia/Novosibirsk"
METRIKA_EXPORT_SCHEMA = "az_analytics_export_v1"
METRIKA_COUNTER_ID = "113776005"
ADVERTISING_COSTS_KEY = "az-advertising-costs-v1"
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
CREATE TABLE IF NOT EXISTS advertising_campaign_identity_evidence (
    source_key TEXT NOT NULL,
    utm_campaign TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    campaign_name TEXT NOT NULL,
    first_seen_date TEXT NOT NULL,
    last_seen_date TEXT NOT NULL,
    evidence_rows INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(source_key,utm_campaign,campaign_id)
);
CREATE INDEX IF NOT EXISTS idx_ad_campaign_evidence_utm
    ON advertising_campaign_identity_evidence(source_key,utm_campaign);
CREATE TABLE IF NOT EXISTS advertising_metrika_import_formats (
    import_id TEXT PRIMARY KEY,
    campaign_identity_source TEXT NOT NULL
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


def _cost_number(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("invalid advertising cost")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid advertising cost") from exc
    if not math.isfinite(number) or number < 0 or number > 1_000_000_000_000:
        raise ValueError("invalid advertising cost")
    return round(number, 2)


def _manual_source_key(name):
    normalized = re.sub(r"[^a-z0-9а-я]+", "_", str(name or "").lower()).strip("_")
    digest = hashlib.sha256(str(name or "").strip().lower().encode("utf-8")).hexdigest()[:10]
    return ("manual_" + (normalized[:40] or "source") + "_" + digest)[:120]


def _empty_cost_store():
    # Legacy v1 shape retained for validation/backward compatibility.
    return {"version": 1, "sources": {}, "periods": []}


def _normalize_cost_store(payload):
    """Normalize the historical v1 advertising-cost payload."""
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("invalid advertising costs")
    if not isinstance(payload.get("sources"), dict) or not isinstance(payload.get("periods"), list):
        raise ValueError("invalid advertising costs")

    sources = {}
    for raw_key, raw_meta in payload["sources"].items():
        key = str(raw_key or "").strip()
        if not key or len(key) > 120 or not isinstance(raw_meta, dict):
            raise ValueError("invalid advertising source")
        name = str(raw_meta.get("name") or "").strip()
        channel = str(raw_meta.get("channel_name") or "").strip()
        if not name or len(name) > 240 or len(channel) > 240:
            raise ValueError("invalid advertising source")
        sources[key] = {
            "name": name,
            "channel_name": channel,
            "manual": bool(raw_meta.get("manual")),
        }

    periods = []
    for raw in payload["periods"]:
        if not isinstance(raw, dict):
            raise ValueError("invalid advertising cost period")
        start = str(raw.get("from") or "").strip()
        end = str(raw.get("to") or "").strip()
        if not DATE_RE.fullmatch(start) or not DATE_RE.fullmatch(end) or start > end:
            raise ValueError("invalid advertising cost period")
        source_spend = raw.get("source_spend") or {}
        if not isinstance(source_spend, dict):
            raise ValueError("invalid advertising source spend")
        clean_spend = {}
        for key, value in source_spend.items():
            key = str(key or "").strip()
            if key not in sources:
                raise ValueError("unknown advertising source")
            clean_spend[key] = _cost_number(value)
        advertising_payment = _cost_number(raw.get("advertising_payment"))
        contractors_payment = _cost_number(raw.get("contractors_payment"))
        total_spend = _cost_number(raw.get("total_spend"))
        totals = (advertising_payment, contractors_payment, total_spend)
        if any(value is not None for value in totals):
            if any(value is None for value in totals):
                raise ValueError("advertising totals incomplete")
            if abs(total_spend - advertising_payment - contractors_payment) > 0.01:
                raise ValueError("total spend must equal advertising plus contractors")
        periods.append({
            "from": start,
            "to": end,
            "advertising_payment": advertising_payment,
            "contractors_payment": contractors_payment,
            "total_spend": total_spend,
            "source_spend": clean_spend,
        })

    periods.sort(key=lambda item: (item["from"], item["to"]))
    for previous, current in zip(periods, periods[1:]):
        if current["from"] <= previous["to"]:
            raise ValueError("advertising cost periods overlap")
    return {"version": 1, "sources": sources, "periods": periods}


def _empty_hierarchical_cost_store():
    return {
        "version": 2,
        "sources": {},
        "campaigns": {},
        "periods": {
            "global": [],
            "sources": {},
            "campaigns": {},
        },
    }


def _cost_period_triplet(raw, *, allow_legacy_partial=False):
    if not isinstance(raw, dict):
        raise ValueError("invalid advertising cost period")
    start = str(raw.get("from") or "").strip()
    end = str(raw.get("to") or "").strip()
    if not DATE_RE.fullmatch(start) or not DATE_RE.fullmatch(end) or start > end:
        raise ValueError("invalid advertising cost period")

    advertising_payment = _cost_number(raw.get("advertising_payment"))
    contractors_payment = _cost_number(raw.get("contractors_payment"))
    total_spend = _cost_number(raw.get("total_spend"))
    legacy_partial = bool(raw.get("legacy_partial"))

    if legacy_partial:
        if not allow_legacy_partial:
            raise ValueError("legacy partial advertising cost is not writable")
        if advertising_payment is None or contractors_payment is not None or total_spend is not None:
            raise ValueError("invalid legacy partial advertising cost")
    else:
        totals = (advertising_payment, contractors_payment, total_spend)
        if any(value is not None for value in totals):
            if any(value is None for value in totals):
                raise ValueError("advertising totals incomplete")
            if abs(total_spend - advertising_payment - contractors_payment) > 0.01:
                raise ValueError("total spend must equal advertising plus contractors")

    result = {
        "from": start,
        "to": end,
        "advertising_payment": advertising_payment,
        "contractors_payment": contractors_payment,
        "total_spend": total_spend,
    }
    if legacy_partial:
        result["legacy_partial"] = True
    return result


def _normalize_cost_periods(raw_periods, *, allow_legacy_partial=False):
    if not isinstance(raw_periods, list):
        raise ValueError("invalid advertising cost periods")
    periods = [
        _cost_period_triplet(item, allow_legacy_partial=allow_legacy_partial)
        for item in raw_periods
    ]
    periods.sort(key=lambda item: (item["from"], item["to"]))
    for previous, current in zip(periods, periods[1:]):
        if current["from"] <= previous["to"]:
            raise ValueError("advertising cost periods overlap")
    return periods


def _migrate_cost_store_v1(payload):
    legacy = _normalize_cost_store(payload)
    store = _empty_hierarchical_cost_store()
    store["sources"] = dict(legacy["sources"])

    for period in legacy["periods"]:
        store["periods"]["global"].append({
            "from": period["from"],
            "to": period["to"],
            "advertising_payment": period["advertising_payment"],
            "contractors_payment": period["contractors_payment"],
            "total_spend": period["total_spend"],
        })
        for source_key, value in (period.get("source_spend") or {}).items():
            if value is None:
                continue
            store["periods"]["sources"].setdefault(source_key, []).append({
                "from": period["from"],
                "to": period["to"],
                "advertising_payment": value,
                "contractors_payment": None,
                "total_spend": None,
                # v1 only stored source advertising spend. Do not invent
                # contractor=0 or total=advertising while migrating.
                "legacy_partial": True,
            })

    store["periods"]["global"] = _normalize_cost_periods(store["periods"]["global"])
    for key, periods in list(store["periods"]["sources"].items()):
        store["periods"]["sources"][key] = _normalize_cost_periods(
            periods, allow_legacy_partial=True
        )
    return store


def _normalize_hierarchical_cost_store(payload):
    if isinstance(payload, dict) and payload.get("version") == 1:
        return _migrate_cost_store_v1(payload)
    if not isinstance(payload, dict) or payload.get("version") != 2:
        raise ValueError("invalid hierarchical advertising costs")

    raw_sources = payload.get("sources")
    raw_campaigns = payload.get("campaigns")
    raw_periods = payload.get("periods")
    if (
        not isinstance(raw_sources, dict)
        or not isinstance(raw_campaigns, dict)
        or not isinstance(raw_periods, dict)
    ):
        raise ValueError("invalid hierarchical advertising costs")

    sources = {}
    for raw_key, raw_meta in raw_sources.items():
        key = str(raw_key or "").strip()
        if not key or len(key) > 120 or not isinstance(raw_meta, dict):
            raise ValueError("invalid advertising source")
        name = str(raw_meta.get("name") or "").strip()
        channel = str(raw_meta.get("channel_name") or "").strip()
        if not name or len(name) > 240 or len(channel) > 240:
            raise ValueError("invalid advertising source")
        sources[key] = {
            "name": name,
            "channel_name": channel,
            "manual": bool(raw_meta.get("manual")),
        }

    campaigns = {}
    for raw_key, raw_meta in raw_campaigns.items():
        key = str(raw_key or "").strip()
        if not key or len(key) > 120 or not isinstance(raw_meta, dict):
            raise ValueError("invalid advertising campaign")
        source_key = str(raw_meta.get("source_key") or "").strip()
        campaign_id = str(raw_meta.get("campaign_id") or "").strip()
        campaign_name = str(raw_meta.get("campaign_name") or "").strip()
        if (
            source_key not in sources
            or (not campaign_id and not campaign_name)
            or len(campaign_id) > 240
            or len(campaign_name) > 500
        ):
            raise ValueError("invalid advertising campaign")
        campaigns[key] = {
            "source_key": source_key,
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
        }

    global_periods = _normalize_cost_periods(raw_periods.get("global") or [])
    source_periods = {}
    raw_source_periods = raw_periods.get("sources") or {}
    if not isinstance(raw_source_periods, dict):
        raise ValueError("invalid source cost periods")
    for source_key, periods in raw_source_periods.items():
        source_key = str(source_key or "").strip()
        if source_key not in sources:
            raise ValueError("unknown advertising source")
        source_periods[source_key] = _normalize_cost_periods(
            periods, allow_legacy_partial=True
        )

    campaign_periods = {}
    raw_campaign_periods = raw_periods.get("campaigns") or {}
    if not isinstance(raw_campaign_periods, dict):
        raise ValueError("invalid campaign cost periods")
    for campaign_key, periods in raw_campaign_periods.items():
        campaign_key = str(campaign_key or "").strip()
        if campaign_key not in campaigns:
            raise ValueError("unknown advertising campaign")
        campaign_periods[campaign_key] = _normalize_cost_periods(periods)

    return {
        "version": 2,
        "sources": sources,
        "campaigns": campaigns,
        "periods": {
            "global": global_periods,
            "sources": source_periods,
            "campaigns": campaign_periods,
        },
    }


def _load_cost_store(conn):
    """Return the new v2 in-memory model, migrating v1 without data loss."""
    try:
        row = conn.execute(
            "SELECT payload FROM report_blobs WHERE key=?",
            (ADVERTISING_COSTS_KEY,),
        ).fetchone()
    except sqlite3.OperationalError:
        return _empty_hierarchical_cost_store()
    if not row:
        return _empty_hierarchical_cost_store()
    try:
        return _normalize_hierarchical_cost_store(json.loads(row["payload"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return _empty_hierarchical_cost_store()


def _cost_entity_periods(store, level, entity_key=""):
    periods = store.get("periods") or {}
    if level == "global":
        return periods.get("global") or []
    if level == "source":
        return (periods.get("sources") or {}).get(entity_key) or []
    if level == "campaign":
        return (periods.get("campaigns") or {}).get(entity_key) or []
    raise ValueError("invalid advertising cost level")


def _cost_entity_summary(store, level, entity_key, start, end):
    start_s, end_s = _iso(start), _iso(end)
    overlapping = [
        item for item in _cost_entity_periods(store, level, entity_key)
        if item["from"] <= end_s and item["to"] >= start_s
    ]
    selected = [
        item for item in overlapping
        if item["from"] >= start_s and item["to"] <= end_s
    ]
    if not overlapping:
        return {
            "complete": False,
            "periods": [],
            "advertising_payment": None,
            "contractors_payment": None,
            "total_spend": None,
            "legacy_partial": False,
        }

    cursor = start
    complete = len(selected) == len(overlapping)
    for item in selected:
        item_start = date.fromisoformat(item["from"])
        item_end = date.fromisoformat(item["to"])
        if item_start != cursor:
            complete = False
            break
        cursor = item_end + timedelta(days=1)
    if cursor <= end:
        complete = False

    def sum_field(field):
        values = [item.get(field) for item in selected]
        if not values or any(value is None for value in values):
            return None
        return round(sum(values), 2)

    return {
        "complete": bool(complete),
        "periods": selected,
        "advertising_payment": sum_field("advertising_payment") if complete else None,
        "contractors_payment": sum_field("contractors_payment") if complete else None,
        "total_spend": sum_field("total_spend") if complete else None,
        "legacy_partial": bool(complete and any(item.get("legacy_partial") for item in selected)),
    }


def _cost_exact_entity_period(store, level, entity_key, start, end):
    start_s, end_s = _iso(start), _iso(end)
    return next(
        (
            item for item in _cost_entity_periods(store, level, entity_key)
            if item["from"] == start_s and item["to"] == end_s
        ),
        None,
    )


def _campaign_cost_key(source_key, campaign_id, campaign_name):
    source_key = str(source_key or "").strip()
    campaign_id = str(campaign_id or "").strip()
    campaign_name = str(campaign_name or "").strip()
    factual = campaign_id or campaign_name
    if not source_key or not factual:
        return ""
    if campaign_id:
        # Factual ID is the stable technical identity. Display-name changes
        # must never create another cost entity for the same campaign.
        identity = source_key + "\0id\0" + campaign_id
        label = "id_" + (_identity_slug(campaign_id) or "campaign")
    else:
        identity = source_key + "\0name\0" + campaign_name
        label = _identity_slug(campaign_name) or "campaign"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    room = 120 - len("campaign__") - len(digest) - 1
    return ("campaign_" + label[:room] + "_" + digest)[:120]


def _source_registry(conn, store=None):
    store = store or _load_cost_store(conn)
    registry = {}
    try:
        rows = conn.execute(
            """
            SELECT source_key,source_name,channel_name,campaign_id,campaign_name
            FROM advertising_traffic_daily
            WHERE provider='metrika'
            ORDER BY metric_date
            """
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for row in rows:
        key, name, channel, _campaign_id, _campaign_name = _canonical_metrika_identity(
            row["source_key"], row["source_name"], row["channel_name"], "", ""
        )
        if key and key not in registry:
            registry[key] = {
                "source_key": key,
                "source_name": name or key,
                "channel_name": channel or "",
                "manual": False,
            }
    for key, meta in (store.get("sources") or {}).items():
        registry[key] = {
            "source_key": key,
            "source_name": str(meta.get("name") or key),
            "channel_name": str(meta.get("channel_name") or ""),
            "manual": bool(meta.get("manual")),
        }
    return sorted(registry.values(), key=lambda item: item["source_name"].lower())


def _store_metrika_import_format(conn, import_id, parsed):
    source = str(
        (parsed.get("metadata") or {}).get("campaign_identity_source") or ""
    ).strip()
    # Empty means the legacy campaign representation. The marker is explicit
    # so the old Python alias dictionary can never become evidence for a new
    # traffic_direct_click_order import.
    source = source or "legacy"
    conn.execute(
        """
        INSERT INTO advertising_metrika_import_formats(
            import_id,campaign_identity_source
        ) VALUES(?,?)
        ON CONFLICT(import_id) DO UPDATE SET
          campaign_identity_source=excluded.campaign_identity_source
        WHERE campaign_identity_source<>excluded.campaign_identity_source
        """,
        (str(import_id or ""), source),
    )


def _native_campaign_import_ids(conn):
    try:
        rows = conn.execute(
            """
            SELECT import_id
            FROM advertising_metrika_import_formats
            WHERE campaign_identity_source='traffic_direct_click_order'
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row["import_id"]) for row in rows}


def _canonical_stored_campaign_identity(
    row,
    campaign_evidence,
    native_import_ids,
):
    source_key, source_name, channel_name, campaign_id, campaign_name = (
        _canonical_metrika_identity(
            row["source_key"],
            row["source_name"],
            row["channel_name"],
            row["campaign_id"],
            row["campaign_name"],
            allow_legacy_aliases=False,
        )
    )

    if source_key != "yandex_ads" or campaign_id or not campaign_name:
        return source_key, source_name, channel_name, campaign_id, campaign_name

    evidence = (campaign_evidence or {}).get((source_key, campaign_name))
    if evidence:
        return _canonical_metrika_identity(
            source_key,
            source_name,
            channel_name,
            evidence.get("campaign_id"),
            evidence.get("campaign_name") or campaign_name,
            allow_legacy_aliases=False,
        )

    provider = str(row["provider"] or "") if "provider" in row.keys() else ""
    import_id = str(row["import_id"] or "") if "import_id" in row.keys() else ""
    is_native = provider == "metrika" and import_id in (native_import_ids or set())
    if is_native:
        return source_key, source_name, channel_name, campaign_id, campaign_name

    # Only unmarked/legacy stored rows retain the historical alias dictionary.
    return _canonical_metrika_identity(
        source_key,
        source_name,
        channel_name,
        campaign_id,
        campaign_name,
        allow_legacy_aliases=True,
    )


def _campaign_registry(conn, store=None):
    store = store or _load_cost_store(conn)
    registry = {}
    campaign_evidence = _campaign_evidence_map(conn)
    native_import_ids = _native_campaign_import_ids(conn)
    try:
        rows = conn.execute(
            """
            SELECT provider,import_id,
                   source_key,source_name,channel_name,campaign_id,campaign_name
            FROM advertising_traffic_daily
            WHERE provider='metrika'
              AND (campaign_id<>'' OR campaign_name<>'')
            ORDER BY metric_date
            """
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for row in rows:
        source_key, _source_name, _channel, campaign_id, campaign_name = (
            _canonical_stored_campaign_identity(
                row, campaign_evidence, native_import_ids
            )
        )
        if not campaign_id and not campaign_name:
            continue
        key = _campaign_cost_key(source_key, campaign_id, campaign_name)
        if key and key not in registry:
            registry[key] = {
                "campaign_key": key,
                "source_key": source_key,
                "campaign_id": campaign_id,
                "campaign_name": campaign_name or ("Кампания " + campaign_id if campaign_id else "Не определено"),
            }
        elif (
            key
            and campaign_name
            and len(campaign_name) > len(str(registry[key].get("campaign_name") or ""))
        ):
            registry[key]["campaign_name"] = campaign_name
    for key, meta in (store.get("campaigns") or {}).items():
        registry[key] = {
            "campaign_key": key,
            "source_key": str(meta.get("source_key") or ""),
            "campaign_id": str(meta.get("campaign_id") or ""),
            "campaign_name": str(meta.get("campaign_name") or ""),
        }
    return sorted(
        registry.values(),
        key=lambda item: (
            item["source_key"],
            (item["campaign_name"] or item["campaign_id"]).lower(),
        ),
    )


def _dataset_period_complete(conn, dataset, start, end):
    try:
        rows = conn.execute(
            """
            SELECT period_start,period_end
            FROM advertising_report_imports
            WHERE dataset=? AND period_end>=? AND period_start<=?
            ORDER BY period_start,period_end
            """,
            (dataset, _iso(start), _iso(end)),
        ).fetchall()
    except sqlite3.OperationalError:
        return False
    if not rows:
        return False
    cursor = start
    for row in rows:
        period_start = max(date.fromisoformat(str(row["period_start"])), start)
        period_end = min(date.fromisoformat(str(row["period_end"])), end)
        if period_end < cursor:
            continue
        if period_start > cursor:
            return False
        cursor = max(cursor, period_end + timedelta(days=1))
        if cursor > end:
            return True
    return cursor > end


def _direct_source_values(conn, start, end):
    try:
        rows = conn.execute(
            """
            SELECT source_key,source_name,channel_name,campaign_id,campaign_name,
                   spend,clicks
            FROM advertising_traffic_daily
            WHERE provider='direct' AND metric_date BETWEEN ? AND ?
            """,
            (_iso(start), _iso(end)),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    values = {}
    for row in rows:
        key, name, channel, _cid, _cname = _canonical_metrika_identity(
            row["source_key"], row["source_name"], row["channel_name"], "", ""
        )
        target = values.setdefault(
            key,
            {
                "source_name": name,
                "channel_name": channel,
                "spend": 0.0,
                "clicks": 0,
                "spend_complete": True,
                "clicks_complete": True,
                "rows": 0,
            },
        )
        target["rows"] += 1
        if row["spend"] is None:
            target["spend_complete"] = False
        else:
            target["spend"] += float(row["spend"])
        if row["clicks"] is None:
            target["clicks_complete"] = False
        else:
            target["clicks"] += int(row["clicks"])
    for target in values.values():
        if not target["rows"] or not target["spend_complete"]:
            target["spend"] = None
        else:
            target["spend"] = round(float(target["spend"]), 2)
        if not target["rows"] or not target["clicks_complete"]:
            target["clicks"] = None
        else:
            target["clicks"] = int(target["clicks"])
    return values


def _direct_campaign_values(conn, start, end):
    try:
        rows = conn.execute(
            """
            SELECT source_key,source_name,channel_name,campaign_id,campaign_name,
                   spend,clicks
            FROM advertising_traffic_daily
            WHERE provider='direct' AND metric_date BETWEEN ? AND ?
              AND (campaign_id<>'' OR campaign_name<>'')
            """,
            (_iso(start), _iso(end)),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    values = {}
    for row in rows:
        source_key, _name, _channel, campaign_id, campaign_name = _canonical_metrika_identity(
            row["source_key"], row["source_name"], row["channel_name"],
            row["campaign_id"], row["campaign_name"],
        )
        key = _campaign_cost_key(source_key, campaign_id, campaign_name)
        if not key:
            continue
        target = values.setdefault(
            key,
            {
                "spend": 0.0,
                "clicks": 0,
                "spend_complete": True,
                "clicks_complete": True,
                "rows": 0,
            },
        )
        target["rows"] += 1
        if row["spend"] is None:
            target["spend_complete"] = False
        else:
            target["spend"] += float(row["spend"])
        if row["clicks"] is None:
            target["clicks_complete"] = False
        else:
            target["clicks"] += int(row["clicks"])
    for target in values.values():
        target["spend"] = (
            round(float(target["spend"]), 2)
            if target["rows"] and target["spend_complete"]
            else None
        )
        target["clicks"] = (
            int(target["clicks"])
            if target["rows"] and target["clicks_complete"]
            else None
        )
    return values


def _effective_cost_components(summary, direct_spend=None):
    manual_advertising = summary.get("advertising_payment") if summary.get("complete") else None
    contractors = summary.get("contractors_payment") if summary.get("complete") else None
    advertising = direct_spend if direct_spend is not None else manual_advertising
    total = (
        round(float(advertising) + float(contractors), 2)
        if advertising is not None and contractors is not None
        else (
            summary.get("total_spend")
            if direct_spend is None and summary.get("complete")
            else None
        )
    )
    return {
        "advertising_payment": advertising,
        "manual_advertising_payment": manual_advertising,
        "contractors_payment": contractors,
        "total_spend": total,
        "spend_provenance": (
            "direct_api"
            if direct_spend is not None
            else ("manual" if manual_advertising is not None else None)
        ),
        "period_complete": bool(summary.get("complete")),
    }


def _campaign_economics(conn, start, end, store=None):
    store = store or _load_cost_store(conn)
    direct_complete = _dataset_period_complete(conn, "direct", start, end)
    direct = _direct_campaign_values(conn, start, end) if direct_complete else {}
    result = {}
    for meta in _campaign_registry(conn, store):
        key = meta["campaign_key"]
        summary = _cost_entity_summary(store, "campaign", key, start, end)
        direct_value = direct.get(key) or {}
        components = _effective_cost_components(
            summary,
            direct_value.get("spend") if direct_complete else None,
        )
        result[key] = {
            **meta,
            **components,
            # Existing per-click economics intentionally continue to use the
            # advertising component only; contractor allocation is visible
            # separately and does not silently redefine CPC.
            "spend": components["advertising_payment"],
            "clicks": direct_value.get("clicks") if direct_complete else None,
        }
    return {
        "campaigns": result,
        "direct_period_complete": direct_complete,
    }


def _source_economics(conn, start, end, store=None):
    store = store or _load_cost_store(conn)
    budget = _cost_entity_summary(store, "global", "", start, end)
    direct_complete = _dataset_period_complete(conn, "direct", start, end)
    direct = _direct_source_values(conn, start, end) if direct_complete else {}
    campaigns = _campaign_economics(conn, start, end, store)["campaigns"]
    result = {}
    for meta in _source_registry(conn, store):
        key = meta["source_key"]
        summary = _cost_entity_summary(store, "source", key, start, end)
        direct_value = direct.get(key) or {}
        components = _effective_cost_components(
            summary,
            direct_value.get("spend") if direct_complete else None,
        )
        children = [item for item in campaigns.values() if item["source_key"] == key]
        child_totals = [item.get("total_spend") for item in children]
        distributed_total = (
            round(sum(float(value) for value in child_totals), 2)
            if children and all(value is not None for value in child_totals)
            else None
        )
        result[key] = {
            **meta,
            **components,
            "spend": components["advertising_payment"],
            "manual_spend": components["manual_advertising_payment"],
            "direct_spend": (
                None
                if not direct_complete or direct_value.get("spend") is None
                else round(float(direct_value["spend"]), 2)
            ),
            "clicks": direct_value.get("clicks") if direct_complete else None,
            "campaign_distributed_total": distributed_total,
            "campaign_reconciliation_delta": (
                round(float(components["total_spend"]) - distributed_total, 2)
                if components["total_spend"] is not None and distributed_total is not None
                else None
            ),
        }

    source_ad_values = [
        item["advertising_payment"]
        for item in result.values()
        if item["advertising_payment"] is not None
    ]
    source_total_values = [
        item["total_spend"]
        for item in result.values()
        if item["total_spend"] is not None
    ]
    source_spend_total = round(sum(source_ad_values), 2) if source_ad_values else None
    source_total_spend = round(sum(source_total_values), 2) if source_total_values else None

    cpc_scope = [
        item for item in result.values()
        if item.get("manual")
        or item.get("channel_name") == "Платная реклама"
        or item.get("spend") is not None
        or item.get("clicks") is not None
    ]
    click_scope_complete = bool(cpc_scope) and all(
        item.get("spend") is not None and item.get("clicks") is not None
        for item in cpc_scope
    )
    advertising_payment = budget.get("advertising_payment") if budget.get("complete") else None
    if (
        click_scope_complete
        and advertising_payment is not None
        and source_spend_total is not None
        and abs(source_spend_total - advertising_payment) <= 0.01
    ):
        clicks = sum(int(item.get("clicks") or 0) for item in cpc_scope)
        cost_per_click = advertising_payment / clicks if clicks else None
    else:
        cost_per_click = None

    return {
        "budget": budget,
        "sources": result,
        "campaigns": campaigns,
        "source_spend_total": source_spend_total,
        "source_total_spend": source_total_spend,
        "source_reconciliation_delta": (
            round(float(budget["total_spend"]) - source_total_spend, 2)
            if budget.get("complete")
            and budget.get("total_spend") is not None
            and source_total_spend is not None
            else None
        ),
        "cost_per_click": cost_per_click,
        "click_scope_complete": click_scope_complete,
        "direct_period_complete": direct_complete,
    }


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
    payment = advertising_mis_bridge.payment_breakdown(conn, start_s, end_s)
    economics = _source_economics(conn, start, end)
    budget = economics["budget"]

    primary_paid = payment["primary_paid"]
    repeat_paid = payment["repeat_paid"]
    if primary_paid is None:
        primary_paid = _nullable_sum(clinic, "primary_paid")
    if repeat_paid is None:
        repeat_paid = _nullable_sum(clinic, "repeat_paid")

    result = {
        # The top KPI is the owner's total advertising expense, including contractors.
        # Direct/API source spend is kept separately and never silently substituted.
        "spend": budget.get("total_spend"),
        "advertising_payment": budget.get("advertising_payment"),
        "contractors_payment": budget.get("contractors_payment"),
        "source_spend_total": economics.get("source_spend_total"),
        "source_total_spend": economics.get("source_total_spend"),
        "source_reconciliation_delta": economics.get("source_reconciliation_delta"),
        "cost_per_click": economics.get("cost_per_click"),
        "clicks": (
            sum(int(item.get("clicks") or 0) for item in economics["sources"].values() if item.get("clicks") is not None)
            if economics.get("direct_period_complete") and any(item.get("clicks") is not None for item in economics["sources"].values())
            else None
        ),
        "visits": _nullable_sum(traffic, "visits"),
        "target_leads": _nullable_sum(clinic, "target_leads"),
        "leads_with_primary": _nullable_sum(clinic, "leads_with_primary"),
        "primary_visits": _nullable_sum(clinic, "primary_visits"),
        "primary_paid": primary_paid,
        "primary_services": _nullable_sum(clinic, "primary_services"),
        "primary_payers": payment.get("primary_payers"),
        "repeat_visits": _nullable_sum(clinic, "repeat_visits"),
        "repeat_paid": repeat_paid,
        "repeat_payers": payment.get("repeat_payers"),
        "unclassified_clinical_paid": payment.get("unclassified_clinical"),
        "new_patients": _nullable_sum(cohort, "new_patients"),
        "new_patient_30d_paid": _nullable_sum(cohort, "new_patient_30d_paid"),
    }
    result["total_visits"] = None if result["primary_visits"] is None and result["repeat_visits"] is None else (result["primary_visits"] or 0) + (result["repeat_visits"] or 0)
    result["total_paid"] = _combine_money(result["primary_paid"], result["repeat_paid"])
    result["primary_avg_check"] = (
        result["primary_paid"] / result["primary_payers"]
        if result["primary_paid"] is not None and result["primary_payers"]
        else None
    )
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
        return {str(row["day"]): row["value"] for row in rows}
    if metric == "target_leads":
        rows = conn.execute(
            "SELECT metric_date AS day,SUM(target_leads) AS value FROM advertising_clinic_daily "
            "WHERE metric_date BETWEEN ? AND ? AND target_leads IS NOT NULL GROUP BY metric_date ORDER BY metric_date",
            (start_s, end_s),
        ).fetchall()
        return {str(row["day"]): row["value"] for row in rows}
    if metric in {"primary_visits", "primary_paid", "total_paid"}:
        result = {}
        cursor = start
        while cursor <= end:
            day_summary = _summary(conn, cursor, cursor)
            value = day_summary.get(metric)
            if value is not None:
                result[_iso(cursor)] = value
            cursor += timedelta(days=1)
        return result
    if metric == "actual_received_total":
        try:
            rows = conn.execute(
                "SELECT data_date,payload_json FROM cash_receipts_daily "
                "WHERE data_date BETWEEN ? AND ? ORDER BY data_date",
                (start_s, end_s),
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        result = {}
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                result[str(row["data_date"])] = float(payload.get("cashTotal") or 0)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return result
    return {}

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
    campaign_evidence = _campaign_evidence_map(conn)
    native_import_ids = _native_campaign_import_ids(conn)
    traffic = conn.execute(
        "SELECT provider,import_id,source_key,source_name,channel_name,campaign_id,campaign_name,spend,clicks,visits "
        "FROM advertising_traffic_daily WHERE metric_date BETWEEN ? AND ?", (start_s, end_s)
    ).fetchall()
    clinic = conn.execute(
        "SELECT provider,import_id,source_key,source_name,channel_name,campaign_id,campaign_name,target_leads,leads_with_primary,"
        "primary_visits,primary_paid,primary_services,repeat_visits,repeat_paid FROM advertising_clinic_daily "
        "WHERE metric_date BETWEEN ? AND ?", (start_s, end_s)
    ).fetchall()
    cohort = conn.execute(
        "SELECT provider,import_id,source_key,source_name,channel_name,campaign_id,campaign_name,new_patients,new_patient_30d_paid "
        "FROM advertising_cohort_daily WHERE cohort_date BETWEEN ? AND ?", (start_s, end_s)
    ).fetchall()
    try:
        goals = conn.execute(
            "SELECT provider,import_id,source_key,source_name,channel_name,campaign_id,campaign_name,"
            "goal_key,SUM(goal_visits) AS goal_visits "
            "FROM advertising_metrika_goal_daily WHERE provider='metrika' AND metric_date BETWEEN ? AND ? "
            "GROUP BY provider,import_id,source_key,source_name,channel_name,campaign_id,campaign_name,goal_key",
            (start_s, end_s),
        ).fetchall()
    except sqlite3.OperationalError:
        goals = []
    groups = {}

    def identity_for(row):
        source_key, source_name, channel_name, campaign_id, campaign_name = (
            _canonical_stored_campaign_identity(
                row, campaign_evidence, native_import_ids
            )
        )
        if campaign_id and not campaign_name:
            # A factual ID without a label is still a distinct campaign and
            # must be visible as such rather than rendered as generic unknown.
            campaign_name = "Кампания " + campaign_id
        elif source_key in {"yandex_ads", "instagram"} and not campaign_name:
            campaign_name = "Не определено"
        return source_key, source_name, channel_name, campaign_id, campaign_name

    def key_for(identity):
        source_key, _source_name, _channel_name, campaign_id, campaign_name = identity
        if level == "campaign":
            if campaign_id:
                return (source_key, "id", campaign_id)
            return None if not campaign_name else (source_key, "name", campaign_name)
        return (source_key,)

    def ensure(identity, key):
        source_key, source_name, channel_name, campaign_id, campaign_name = identity
        if key not in groups:
            groups[key] = {
                "source_key": source_key, "source_name": source_name,
                "channel_name": channel_name,
                "campaign_id": campaign_id if level == "campaign" else "",
                "campaign_name": campaign_name if level == "campaign" else "",
                **{metric: None for metric in METRIC_KEYS},
            }
        elif (
            level == "campaign"
            and campaign_name
            and len(campaign_name) > len(str(groups[key].get("campaign_name") or ""))
        ):
            # Same factual campaign ID with a better label remains one row.
            groups[key]["campaign_name"] = campaign_name
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
    economics = _source_economics(conn, start, end)
    if level == "source":
        for source_key, source_value in economics["sources"].items():
            key = (source_key,)
            if key not in groups:
                identity = (
                    source_key,
                    source_value["source_name"],
                    source_value["channel_name"],
                    "",
                    "",
                )
                ensure(identity, key)
    elif level == "campaign":
        for campaign_value in economics.get("campaigns", {}).values():
            identity = (
                campaign_value["source_key"],
                (
                    (economics["sources"].get(campaign_value["source_key"]) or {})
                    .get("source_name")
                    or campaign_value["source_key"]
                ),
                (
                    (economics["sources"].get(campaign_value["source_key"]) or {})
                    .get("channel_name")
                    or ""
                ),
                campaign_value.get("campaign_id") or "",
                campaign_value.get("campaign_name") or "",
            )
            key = key_for(identity)
            if key is not None and key not in groups:
                ensure(identity, key)

    result = []
    for item in groups.values():
        if level == "source":
            source_value = economics["sources"].get(item["source_key"]) or {}
            item["spend"] = source_value.get("spend")
            item["advertising_payment"] = source_value.get("advertising_payment")
            item["contractors_payment"] = source_value.get("contractors_payment")
            item["total_spend"] = source_value.get("total_spend")
            item["cost_period_complete"] = bool(source_value.get("period_complete"))
            item["campaign_distributed_total"] = source_value.get("campaign_distributed_total")
            item["campaign_reconciliation_delta"] = source_value.get("campaign_reconciliation_delta")
            if source_value.get("clicks") is not None:
                item["clicks"] = source_value.get("clicks")
            item["spend_provenance"] = source_value.get("spend_provenance")
            item["campaign_key"] = ""
        else:
            campaign_key = _campaign_cost_key(
                item["source_key"], item["campaign_id"], item["campaign_name"]
            )
            campaign_value = economics.get("campaigns", {}).get(campaign_key) or {}
            item["campaign_key"] = campaign_key
            item["spend"] = campaign_value.get("spend")
            item["advertising_payment"] = campaign_value.get("advertising_payment")
            item["contractors_payment"] = campaign_value.get("contractors_payment")
            item["total_spend"] = campaign_value.get("total_spend")
            item["cost_period_complete"] = bool(campaign_value.get("period_complete"))
            if campaign_value.get("clicks") is not None:
                item["clicks"] = campaign_value.get("clicks")
            item["spend_provenance"] = campaign_value.get("spend_provenance")

        item["total_paid"] = _combine_money(item["primary_paid"], item["repeat_paid"])
        item["total_visits"] = None if item["primary_visits"] is None and item["repeat_visits"] is None else (item["primary_visits"] or 0) + (item["repeat_visits"] or 0)
        item["lead_to_primary_pct"] = item["leads_with_primary"] * 100.0 / item["target_leads"] if item["target_leads"] and item["leads_with_primary"] is not None else None
        spend = item.get("spend")
        item["cost_per_click"] = spend / item["clicks"] if spend is not None and item.get("clicks") else None
        item["cost_per_visit"] = spend / item["visits"] if spend is not None and item.get("visits") else None
        item["cost_per_booking_open"] = spend / item["booking_open_visits"] if spend is not None and item.get("booking_open_visits") else None
        item["cost_per_medflex"] = spend / item["medflex_visits"] if spend is not None and item.get("medflex_visits") else None
        item["cost_per_target_lead"] = spend / item["target_leads"] if spend is not None and item.get("target_leads") else None
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



CAMPAIGN_ID_SENTINELS = {"", "other", "unknown", "undefined", "not_set", "not set", "(not set)"}
CAMPAIGN_NAME_SENTINELS = {
    "", "чужие кампании", "другие кампании", "прочие кампании",
    "не определено", "кампания не определена",
    "other", "unknown", "undefined", "not_set", "not set", "(not set)",
}


def _identity_slug(value):
    return re.sub(r"[^a-z0-9а-я]+", "_", str(value or "").strip().lower()).strip("_")


def _dynamic_source_identity(prefix, raw_name, fallback_name, channel_name):
    name = str(raw_name or "").strip()
    if not name:
        return prefix, fallback_name, channel_name

    # The costs store has an established 120-character source_key contract.
    # Keep a readable slug prefix, but put the complete SHA-256 digest of the
    # factual raw value into the key.  We never truncate the digest: long UTM
    # values therefore stay bounded while punctuation variants such as
    # future.network / future-network remain distinct.
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    slug = _identity_slug(name) or "source"
    room = 120 - len(prefix) - len(digest) - 2
    if room < 1:
        raise ValueError("dynamic source prefix too long")
    key = f"{prefix}_{slug[:room]}_{digest}"
    return key, name, channel_name


def _evidence_label(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _is_exact_or_domain(value, exact=(), domains=(), prefixes=()):
    raw = _evidence_label(value)
    if not raw:
        return False
    if raw in set(exact):
        return True

    candidate = raw.rstrip(".")
    for prefix in prefixes:
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):]
            break
    if candidate in set(exact):
        return True
    return any(candidate == domain or candidate.endswith("." + domain) for domain in domains)


def _is_yandex_direct_label(value):
    raw = _evidence_label(value)
    if not raw:
        return False
    return bool(
        re.fullmatch(
            r"(?:ad\.)?(?:яндекс|yandex)\s*:?\s*(?:директ|direct)(?:\s*:\s*.*)?",
            raw,
        )
    )


def _meaningful_campaign_id(value):
    raw = str(value or "").strip()
    return "" if raw.lower() in CAMPAIGN_ID_SENTINELS else raw


def _meaningful_campaign_name(value):
    raw = str(value or "").strip()
    return "" if raw.lower() in CAMPAIGN_NAME_SENTINELS else raw


def _metrika_source(row):
    traffic_id = str(row.get("traffic_source_id") or "").strip()
    traffic_name = str(row.get("traffic_source_name") or "").strip()
    detail = str(row.get("source_detail") or "").strip()
    detail_id = str(row.get("source_detail_id") or "").strip()
    utm_source_raw = str(row.get("utm_source") or "").strip()
    utm_source = utm_source_raw.lower()
    direct_campaign_id = _meaningful_campaign_id(row.get("direct_campaign_id"))
    direct_campaign_name = _meaningful_campaign_name(row.get("direct_campaign_name"))

    if traffic_id == "ad":
        if utm_source == "geoadv_direct":
            return "yandex_business", "Яндекс Бизнес", "Платная реклама"

        # Do not assume that every paid source is Yandex Direct.  Direct facts
        # (native campaign identity or Yandex/Direct source evidence) keep the
        # established yandex_ads bucket; otherwise a new UTM/source becomes a
        # new source automatically without a Python registry entry.
        yandex_utm = utm_source in {
            "yandex", "yandex_direct", "yandex-direct", "yandexdirect", "ydirect",
        }
        if (
            direct_campaign_id
            or direct_campaign_name
            or any(_is_yandex_direct_label(value) for value in (detail_id, detail, traffic_name))
            or yandex_utm
        ):
            return "yandex_ads", "Яндекс-реклама", "Платная реклама"
        if utm_source_raw:
            return _dynamic_source_identity(
                "paid", utm_source_raw, "Платная реклама", "Платная реклама"
            )
        if detail or detail_id:
            return _dynamic_source_identity(
                "paid", detail or detail_id, traffic_name or "Платная реклама", "Платная реклама"
            )
        return "paid_ads", traffic_name or "Платная реклама", "Платная реклама"

    if traffic_id == "direct":
        return "direct", "Прямые заходы", "Прямые заходы"

    if traffic_id == "organic":
        if any(
            _is_exact_or_domain(
                value,
                exact=("google",),
                domains=("google.com",),
                prefixes=("organic.",),
            )
            for value in (detail_id, detail)
        ):
            return "google_organic", "Google органика", "Поиск"
        if any(
            _is_exact_or_domain(
                value,
                exact=("яндекс", "yandex"),
                domains=("yandex.ru",),
                prefixes=("organic.",),
            )
            for value in (detail_id, detail)
        ):
            return "yandex_organic", "Яндекс органика", "Поиск"
        return _dynamic_source_identity(
            "organic", detail or detail_id, traffic_name or "Поиск", "Поиск"
        )

    if traffic_id == "social":
        if (
            utm_source in {"instagram", "ig"}
            or any(
                _is_exact_or_domain(
                    value,
                    exact=("instagram",),
                    domains=("instagram.com",),
                    prefixes=("social.",),
                )
                for value in (detail_id, detail)
            )
        ):
            return "instagram", "Instagram", "Социальные сети"
        return _dynamic_source_identity(
            "social",
            utm_source_raw or detail or detail_id,
            traffic_name or "Социальные сети",
            "Социальные сети",
        )

    if traffic_id == "internal":
        return "internal", "Внутренние переходы", "Другие источники"

    if traffic_id == "referral":
        referral_values = (utm_source_raw, detail, detail_id)
        if (
            utm_source == "2gis"
            or any(
                _is_exact_or_domain(value, domains=("2gis.ru",), prefixes=("referral.",))
                for value in referral_values
            )
        ):
            return "2gis", "2ГИС", "Карты"
        if (
            utm_source == "taplink"
            or any(
                _is_exact_or_domain(value, domains=("taplink.cc",), prefixes=("referral.",))
                for value in referral_values
            )
        ):
            return "taplink", "Taplink", "Другие источники"
        if (
            utm_source == "napopravku"
            or any(
                _is_exact_or_domain(value, domains=("napopravku.ru",), prefixes=("referral.",))
                for value in referral_values
            )
        ):
            return "napopravku", "НаПоправку", "Другие источники"
        if (
            utm_source == "prodoctorov"
            or any(
                _is_exact_or_domain(value, domains=("prodoctorov.ru",), prefixes=("referral.",))
                for value in referral_values
            )
        ):
            return "prodoctorov", "ПроДокторов", "Другие источники"
        return _dynamic_source_identity(
            "referral",
            utm_source_raw or detail or detail_id,
            traffic_name or "Переходы по ссылкам",
            "Другие источники",
        )

    raw_source = utm_source_raw or detail or traffic_name or traffic_id
    return _dynamic_source_identity(
        "source", raw_source, "Другие источники", "Другие источники"
    )


def _instagram_campaign_name(row):
    raw = str(row.get("utm_campaign") or row.get("utm_content") or "").strip()
    if not raw:
        return ""
    return INSTAGRAM_CAMPAIGN_LABELS.get(raw.lower(), raw)


def _canonical_metrika_identity(
    source_key,
    source_name,
    channel_name,
    campaign_id,
    campaign_name,
    *,
    allow_legacy_aliases=True,
):
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

        # Raw Metrika sentinels are removed before this function.  Keep legacy
        # stored identities untouched here so historical "other" rows are not
        # silently rewritten merely by rendering an old report.
        if campaign_id:
            if not campaign_name:
                campaign_name = YANDEX_CANONICAL_CAMPAIGNS.get(campaign_id, "")
            return source_key, source_name, channel_name, campaign_id, campaign_name

        # Legacy aliases remain migration compatibility only.  Any new UTM
        # that is absent from this map is still kept verbatim as its own
        # campaign, so future campaigns do not require Python edits.
        canonical_id = (
            YANDEX_CAMPAIGN_ALIASES.get(campaign_name.lower(), "")
            if allow_legacy_aliases and campaign_name
            else ""
        )
        if canonical_id:
            return (
                source_key,
                source_name,
                channel_name,
                canonical_id,
                YANDEX_CANONICAL_CAMPAIGNS[canonical_id],
            )

    return source_key, source_name, channel_name, campaign_id, campaign_name


def _campaign_evidence_rows(traffic, accepted_days):
    grouped = {}
    accepted = set(accepted_days or [])
    for row in traffic or []:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date") or "")
        if day not in accepted or bool(row.get("is_test_tag")):
            continue
        source_key, _source_name, _channel_name = _metrika_source(row)
        if source_key != "yandex_ads":
            continue
        utm_campaign = str(row.get("utm_campaign") or "").strip()
        campaign_id = _meaningful_campaign_id(row.get("direct_campaign_id"))
        campaign_name = _meaningful_campaign_name(row.get("direct_campaign_name"))
        if not utm_campaign or not campaign_id:
            continue
        key = (source_key, utm_campaign, campaign_id)
        target = grouped.setdefault(
            key,
            {
                "source_key": source_key,
                "utm_campaign": utm_campaign,
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "first_seen_date": day,
                "last_seen_date": day,
                "evidence_rows": 0,
            },
        )
        if campaign_name and (
            not target["campaign_name"]
            or len(campaign_name) > len(target["campaign_name"])
        ):
            target["campaign_name"] = campaign_name
        target["first_seen_date"] = min(target["first_seen_date"], day)
        target["last_seen_date"] = max(target["last_seen_date"], day)
        target["evidence_rows"] += 1
    return list(grouped.values())


def _campaign_evidence_map_from_rows(rows):
    grouped = {}
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        source_key = str(raw.get("source_key") or "").strip()
        utm_campaign = str(raw.get("utm_campaign") or "").strip()
        campaign_id = _meaningful_campaign_id(raw.get("campaign_id"))
        campaign_name = _meaningful_campaign_name(raw.get("campaign_name"))
        if not source_key or not utm_campaign or not campaign_id:
            continue
        key = (source_key, utm_campaign)
        target = grouped.setdefault(key, {})
        names = target.setdefault(campaign_id, set())
        if campaign_name:
            names.add(campaign_name)

    result = {}
    for key, by_id in grouped.items():
        if len(by_id) != 1:
            # One factual UTM linked to multiple factual campaign IDs is
            # ambiguous. Never guess or merge it.
            result[key] = None
            continue
        campaign_id, names = next(iter(by_id.items()))
        campaign_name = (
            max(names, key=lambda value: (len(value), value))
            if names else ""
        )
        result[key] = {
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
        }
    return result


def _campaign_evidence_map(conn):
    try:
        rows = conn.execute(
            """
            SELECT source_key,utm_campaign,campaign_id,campaign_name,
                   first_seen_date,last_seen_date,evidence_rows
            FROM advertising_campaign_identity_evidence
            ORDER BY source_key,utm_campaign,campaign_id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return _campaign_evidence_map_from_rows([dict(row) for row in rows])


def _merge_campaign_evidence_maps(*maps):
    result = {}
    keys = set()
    for mapping in maps:
        keys.update((mapping or {}).keys())
    for key in keys:
        values = [mapping.get(key) for mapping in maps if key in (mapping or {})]
        if not values or any(value is None for value in values):
            result[key] = None
            continue
        ids = {str(value.get("campaign_id") or "") for value in values}
        ids.discard("")
        if len(ids) != 1:
            result[key] = None
            continue
        names = {
            str(value.get("campaign_name") or "").strip()
            for value in values
            if str(value.get("campaign_name") or "").strip()
        }
        result[key] = {
            "campaign_id": next(iter(ids)),
            "campaign_name": (
                max(names, key=lambda value: (len(value), value))
                if names else ""
            ),
        }
    return result


def _store_campaign_evidence(conn, rows):
    for raw in rows or []:
        source_key = str(raw.get("source_key") or "").strip()
        utm_campaign = str(raw.get("utm_campaign") or "").strip()
        campaign_id = _meaningful_campaign_id(raw.get("campaign_id"))
        campaign_name = _meaningful_campaign_name(raw.get("campaign_name"))
        first_seen = str(raw.get("first_seen_date") or "").strip()
        last_seen = str(raw.get("last_seen_date") or "").strip()
        evidence_rows = max(1, int(raw.get("evidence_rows") or 1))
        if (
            not source_key
            or not utm_campaign
            or not campaign_id
            or not DATE_RE.fullmatch(first_seen)
            or not DATE_RE.fullmatch(last_seen)
        ):
            continue
        conn.execute(
            """
            INSERT INTO advertising_campaign_identity_evidence(
                source_key,utm_campaign,campaign_id,campaign_name,
                first_seen_date,last_seen_date,evidence_rows
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(source_key,utm_campaign,campaign_id) DO UPDATE SET
              campaign_name=CASE
                WHEN length(excluded.campaign_name)>length(campaign_name)
                THEN excluded.campaign_name ELSE campaign_name END,
              first_seen_date=MIN(first_seen_date,excluded.first_seen_date),
              last_seen_date=MAX(last_seen_date,excluded.last_seen_date),
              evidence_rows=MAX(evidence_rows,excluded.evidence_rows)
            WHERE
              length(excluded.campaign_name)>length(campaign_name)
              OR excluded.first_seen_date<first_seen_date
              OR excluded.last_seen_date>last_seen_date
              OR excluded.evidence_rows>evidence_rows
            """,
            (
                source_key, utm_campaign, campaign_id, campaign_name,
                first_seen, last_seen, evidence_rows,
            ),
        )


def _metrika_campaign_name(row, source_key):
    if source_key == "instagram":
        return _instagram_campaign_name(row)
    return str(row.get("direct_campaign_name") or row.get("utm_campaign") or "").strip()


def _metrika_campaign_identity(
    row,
    source_key,
    source_name,
    channel_name,
    campaign_evidence=None,
):
    # DirectClickOrder is authoritative only when it contains a factual,
    # non-sentinel ID. When the ID is blank/sentinel, factual UTM remains
    # visible unless a unique factual UTM->Direct-ID relation was observed.
    raw_campaign_id = str(row.get("direct_campaign_id") or "").strip()
    campaign_id = _meaningful_campaign_id(raw_campaign_id)
    direct_name = _meaningful_campaign_name(row.get("direct_campaign_name"))
    utm_campaign = str(row.get("utm_campaign") or "").strip()

    if source_key == "instagram":
        campaign_name = _instagram_campaign_name(row)
    elif campaign_id:
        campaign_name = direct_name or utm_campaign
    elif (
        source_key == "yandex_ads"
        and utm_campaign
        and campaign_evidence
        and campaign_evidence.get((source_key, utm_campaign))
    ):
        evidence = campaign_evidence[(source_key, utm_campaign)]
        campaign_id = str(evidence.get("campaign_id") or "").strip()
        campaign_name = (
            str(evidence.get("campaign_name") or "").strip()
            or utm_campaign
        )
    elif utm_campaign:
        campaign_name = utm_campaign
    elif raw_campaign_id and not campaign_id:
        campaign_name = ""
    else:
        campaign_name = direct_name

    return _canonical_metrika_identity(
        source_key,
        source_name,
        channel_name,
        campaign_id,
        campaign_name,
        allow_legacy_aliases=False,
    )


def _parse_structured_campaigns(payload):
    raw_rows = payload.get("direct_campaign_day")
    if raw_rows is None:
        return None
    if not isinstance(raw_rows, list):
        raise ValueError("Некорректная структурированная разбивка кампаний Метрики.")

    goal_registry = {
        str(item.get("goal_id") or ""): str(item.get("goal_name") or "")
        for item in (payload.get("goal_registry") or [])
        if isinstance(item, dict)
    }
    grouped = {}
    for item in raw_rows:
        if not isinstance(item, dict):
            raise ValueError("Некорректная строка кампании Метрики.")
        campaign_id = str(item.get("campaign_id") or "").strip()
        campaign_name = str(item.get("campaign_name") or "").strip()
        try:
            visits = int(item.get("visits") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректное число визитов кампании Метрики.") from exc
        if visits < 0:
            raise ValueError("Некорректное число визитов кампании Метрики.")

        key = (campaign_id, campaign_name)
        row = grouped.setdefault(
            key,
            {
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "visits": 0,
                "goals": {},
            },
        )
        row["visits"] += visits

        goals = item.get("goals") or []
        if not isinstance(goals, list):
            raise ValueError("Некорректные цели кампании Метрики.")
        for goal in goals:
            if not isinstance(goal, dict):
                raise ValueError("Некорректная цель кампании Метрики.")
            goal_id = str(goal.get("goal_id") or "")
            goal_name = str(goal.get("goal_name") or goal_registry.get(goal_id, "")).strip()
            goal_key = METRIKA_GOALS.get(goal_name)
            if not goal_key:
                continue
            try:
                goal_visits = int(goal.get("goal_visits") or 0)
                reaches = int(goal.get("reaches") or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError("Некорректные значения цели кампании Метрики.") from exc
            if goal_visits < 0 or reaches < 0:
                raise ValueError("Некорректные значения цели кампании Метрики.")
            pair = row["goals"].setdefault(
                (goal_key, goal_name),
                {"goal_visits": 0, "reaches": 0},
            )
            pair["goal_visits"] += goal_visits
            pair["reaches"] += reaches

    return list(grouped.values())


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
        if row["campaign_name"] != "Чужие кампании"
        and not (
            row["campaign_name"] == "Не определено"
            and not str(row.get("campaign_id") or "").strip()
        )
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
            "yandex_ads", "Яндекс-реклама", "Платная реклама",
            str(row.get("campaign_id") or ""), row["campaign_name"],
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
            "yandex_ads", "Яндекс-реклама", "Платная реклама",
            str(row.get("campaign_id") or ""), row["campaign_name"],
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


def _parse_metrika_zip(raw, filename="", allow_provisional=False, now=None, campaign_evidence=None):
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

    completed_days = {
        str(row.get("date") or "")
        for row in traffic
        if str(row.get("day_status") or "").startswith("completed_calendar_day")
    }
    provisional_days = {
        str(row.get("date") or "")
        for row in traffic
        if allow_provisional and str(row.get("day_status") or "") == "provisional_current_day"
    }

    meta_from = str(meta.get("period_from") or "")
    meta_to = str(meta.get("period_to") or "")
    meta_status = str(meta.get("day_status") or "")
    if meta_from and meta_from == meta_to and DATE_RE.fullmatch(meta_from):
        if meta_status.startswith("completed_calendar_day"):
            if meta_from in provisional_days:
                raise ValueError("Статус дня в выгрузке Метрики противоречив.")
            completed_days.add(meta_from)
        elif allow_provisional and meta_status == "provisional_current_day":
            if meta_from in completed_days:
                raise ValueError("Статус дня в выгрузке Метрики противоречив.")
            provisional_days.add(meta_from)

    completed_days = sorted(day for day in completed_days if day)
    provisional_days = sorted(day for day in provisional_days if day)

    current = now or datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    local_today = current.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()
    for day in completed_days:
        if date.fromisoformat(day) >= local_today:
            raise ValueError("Незавершённый календарный день нельзя импортировать как завершённый.")

    accepted_days = sorted(set(completed_days) | set(provisional_days))
    native_campaign_format = (
        str(meta.get("campaign_identity_source") or "").strip()
        == "traffic_direct_click_order"
    )
    if not accepted_days:
        raise ValueError(
            "В ZIP нет завершённых календарных дней."
            if not allow_provisional
            else "В ZIP нет допустимых данных Яндекс Метрики."
        )

    local_campaign_evidence_rows = _campaign_evidence_rows(traffic, accepted_days)
    local_campaign_evidence = _campaign_evidence_map_from_rows(local_campaign_evidence_rows)
    effective_campaign_evidence = _merge_campaign_evidence_maps(
        campaign_evidence or {},
        local_campaign_evidence,
    )

    traffic_by_id = {}
    traffic_rows = {}
    for row in traffic:
        day = str(row.get("date") or "")
        if day not in accepted_days:
            continue
        traffic_by_id[str(row.get("row_id") or "")] = row
        if bool(row.get("is_test_tag")):
            continue
        source_key, source_name, channel_name = _metrika_source(row)
        source_key, source_name, channel_name, campaign_id, campaign_name = _metrika_campaign_identity(
            row, source_key, source_name, channel_name, effective_campaign_evidence
        )
        key = (day, source_key, source_name, channel_name, campaign_id, campaign_name)
        target = traffic_rows.setdefault(key, {"visits": 0})
        target["visits"] += int(row.get("visits") or 0)

    goal_rows = {}
    for row in goals:
        day = str(row.get("date") or "")
        if day not in accepted_days:
            continue
        traffic_row = traffic_by_id.get(str(row.get("traffic_row_id") or ""))
        if not traffic_row or bool(traffic_row.get("is_test_tag")):
            continue
        goal_name = str(row.get("goal_name") or "")
        goal_key = METRIKA_GOALS.get(goal_name)
        if not goal_key:
            continue
        source_key, source_name, channel_name = _metrika_source(traffic_row)
        source_key, source_name, channel_name, campaign_id, campaign_name = _metrika_campaign_identity(
            traffic_row, source_key, source_name, channel_name, effective_campaign_evidence
        )
        key = (day, source_key, source_name, channel_name, campaign_id, campaign_name, goal_key, goal_name)
        target = goal_rows.setdefault(key, {"goal_visits": 0, "reaches": 0})
        target["goal_visits"] += int(row.get("goal_visits") or 0)
        target["reaches"] += int(row.get("reaches") or 0)

    structured_campaigns = _parse_structured_campaigns(payload)
    campaign_rows = (
        []
        if native_campaign_format
        else (
            structured_campaigns
            if structured_campaigns is not None
            else _parse_report_campaigns(report_text, payload)
        )
    )
    traffic_rows, goal_rows = _apply_yandex_campaign_report(
        traffic_rows, goal_rows, accepted_days, campaign_rows
    )

    return {
        "metadata": meta,
        "completed_days": completed_days,
        "provisional_days": provisional_days,
        "traffic_rows": traffic_rows,
        "goal_rows": goal_rows,
        "campaign_evidence": local_campaign_evidence_rows,
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
    days = sorted(set(parsed.get("completed_days") or []) | set(parsed.get("provisional_days") or []))
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


def _metrika_day_is_completed(conn, day):
    rows = conn.execute(
        "SELECT completed_days_json FROM advertising_metrika_imports"
    ).fetchall()
    for row in rows:
        try:
            values = json.loads(row["completed_days_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(values, list) and day in {str(value) for value in values}:
            return True
    return False


def _store_metrika_provisional(conn, parsed, actor_user_id):
    provisional_days = list(parsed.get("provisional_days") or [])
    if parsed.get("completed_days") or len(provisional_days) != 1:
        raise ValueError("Оперативный срез Метрики должен содержать ровно текущий день.")
    day = provisional_days[0]
    if _metrika_day_is_completed(conn, day):
        raise ValueError("Этот день уже сохранён как завершённый и не может быть заменён оперативным срезом.")
    import_id = "metrika-live-" + day
    _store_campaign_evidence(conn, parsed.get("campaign_evidence"))
    _store_metrika_import_format(conn, import_id, parsed)
    if _stored_metrika_matches(conn, parsed):
        return {
            "reused": True,
            "updated": False,
            "provisional_days": provisional_days,
            "payload_sha256": parsed["payload_sha256"],
        }

    imported_at = iso_now()
    conn.execute(
        "DELETE FROM advertising_traffic_daily WHERE provider='metrika' AND metric_date=?",
        (day,),
    )
    conn.execute(
        "DELETE FROM advertising_metrika_goal_daily WHERE provider='metrika' AND metric_date=?",
        (day,),
    )
    for key, values in parsed["traffic_rows"].items():
        row_day, source_key, source_name, channel_name, campaign_id, campaign_name = key
        if row_day != day:
            continue
        conn.execute(
            """
            INSERT INTO advertising_traffic_daily(
                provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,
                spend,clicks,visits,updated_at,import_id
            ) VALUES('metrika',?,?,?,?,?,?,NULL,NULL,?,?,?)
            """,
            (row_day, source_key, source_name, channel_name, campaign_id, campaign_name,
             int(values["visits"]), imported_at, import_id),
        )
    for key, values in parsed["goal_rows"].items():
        row_day, source_key, source_name, channel_name, campaign_id, campaign_name, goal_key, goal_name = key
        if row_day != day:
            continue
        conn.execute(
            """
            INSERT INTO advertising_metrika_goal_daily(
                provider,metric_date,source_key,source_name,channel_name,campaign_id,campaign_name,
                goal_key,goal_name,goal_visits,reaches,updated_at,import_id
            ) VALUES('metrika',?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (row_day, source_key, source_name, channel_name, campaign_id, campaign_name,
             goal_key, goal_name, int(values["goal_visits"]), int(values["reaches"]), imported_at, import_id),
        )
    return {
        "reused": False,
        "updated": True,
        "provisional_days": provisional_days,
        "payload_sha256": parsed["payload_sha256"],
    }


def _completed_metrika_matches_registered(conn, parsed):
    days = list(parsed.get("completed_days") or [])
    return (
        len(days) == 1
        and _metrika_day_is_completed(conn, days[0])
        and _stored_metrika_matches(conn, parsed)
    )


def _store_metrika(conn, parsed, actor_user_id):
    sha = parsed["payload_sha256"]
    import_id = "metrika-" + sha[:16]
    existing = conn.execute(
        "SELECT payload_sha256 FROM advertising_metrika_imports WHERE payload_sha256=?",
        (sha,),
    ).fetchone()
    _store_campaign_evidence(conn, parsed.get("campaign_evidence"))
    _store_metrika_import_format(conn, import_id, parsed)
    if existing and _stored_metrika_matches(conn, parsed):
        return {
            "reused": True,
            "reclassified": False,
            "completed_days": parsed["completed_days"],
            "payload_sha256": sha,
        }

    archive_path = _archive_metrika(parsed["raw"], sha)
    imported_at = iso_now()
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

    payment_scope = advertising_mis_bridge.payment_breakdown(conn, start_s, end_s)
    if payment_scope.get("primary_paid") is not None:
        primary_paid = payment_scope["primary_paid"]
    if payment_scope.get("repeat_paid") is not None:
        repeat_paid = payment_scope["repeat_paid"]

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
        "actual_received_total": cash["cash_total"],
        "primary_payers": payment_scope.get("primary_payers"),
        "repeat_payers": payment_scope.get("repeat_payers"),
        "unclassified_clinical": payment_scope.get("unclassified_clinical"),
        "excluded_lab": payment_scope.get("excluded_lab"),
        "excluded_sales": payment_scope.get("excluded_sales"),
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
    revenue = _revenue_summary(conn, start, end)
    crm = _crm_status(conn, start, end)
    budget = _cost_entity_summary(_load_cost_store(conn), "global", "", start, end)
    return {
        "period": {"from": _iso(start), "to": _iso(end)},
        "compare": {"mode": compare_mode, "label": _comparison_label(compare_mode), "ranges": [{"from": _iso(a), "to": _iso(b)} for a, b in ranges], "summary": comparison, "delta_pct": deltas},
        "summary": current,
        "activity": {"primary_visits": current["primary_visits"], "primary_paid": current["primary_paid"], "repeat_visits": current["repeat_visits"], "repeat_paid": current["repeat_paid"], "total_visits": current["total_visits"], "total_paid": current["total_paid"]},
        "economics": {
            "cost_period_complete": budget.get("complete", False),
            "primary_payers": current.get("primary_payers"),
            "primary_avg_check": current.get("primary_avg_check"),
            # Advertising attribution is intentionally absent until CRM links a
            # source to the same primary patients and their factual payments.
            "advertising_primary_cost": None,
            "primary_roas": None,
        },
        "sources": _group_breakdown(conn, start, end, "source"),
        "campaigns": _group_breakdown(conn, start, end, "campaign"),
        "trend": _trend(conn, start, end, trend_metric, ranges),
        "freshness": _freshness(conn),
        "coverage": _coverage_summary(conn, start, end),
        "revenue": revenue,
        "crm": crm,
    }


def _cost_values_payload(store, level, entity_key, start, end):
    exact = _cost_exact_entity_period(store, level, entity_key, start, end)
    summary = _cost_entity_summary(store, level, entity_key, start, end)
    return {
        "period": {"from": _iso(start), "to": _iso(end)},
        "exact": {
            "advertising_payment": (exact or {}).get("advertising_payment"),
            "contractors_payment": (exact or {}).get("contractors_payment"),
            "total_spend": (exact or {}).get("total_spend"),
            "legacy_partial": bool((exact or {}).get("legacy_partial")),
        },
        "summary": {
            "complete": bool(summary.get("complete")),
            "advertising_payment": summary.get("advertising_payment"),
            "contractors_payment": summary.get("contractors_payment"),
            "total_spend": summary.get("total_spend"),
            "legacy_partial": bool(summary.get("legacy_partial")),
        },
    }


def _cost_hierarchy_payload(conn, start, end):
    store = _load_cost_store(conn)
    economics = _source_economics(conn, start, end, store)
    campaign_economics = economics.get("campaigns") or {}
    campaign_registry = _campaign_registry(conn, store)
    campaigns_by_source = {}
    for meta in campaign_registry:
        key = meta["campaign_key"]
        econ = campaign_economics.get(key) or {}
        row = {
            **meta,
            **_cost_values_payload(store, "campaign", key, start, end),
            "effective": {
                "advertising_payment": econ.get("advertising_payment"),
                "contractors_payment": econ.get("contractors_payment"),
                "total_spend": econ.get("total_spend"),
                "spend_provenance": econ.get("spend_provenance"),
                "period_complete": bool(econ.get("period_complete")),
            },
        }
        campaigns_by_source.setdefault(meta["source_key"], []).append(row)

    sources = []
    for meta in _source_registry(conn, store):
        key = meta["source_key"]
        econ = (economics.get("sources") or {}).get(key) or {}
        children = campaigns_by_source.get(key, [])
        sources.append({
            **meta,
            **_cost_values_payload(store, "source", key, start, end),
            "effective": {
                "advertising_payment": econ.get("advertising_payment"),
                "contractors_payment": econ.get("contractors_payment"),
                "total_spend": econ.get("total_spend"),
                "spend_provenance": econ.get("spend_provenance"),
                "period_complete": bool(econ.get("period_complete")),
            },
            "reconciliation": {
                "distributed_total": econ.get("campaign_distributed_total"),
                "parent_total": econ.get("total_spend"),
                "delta": econ.get("campaign_reconciliation_delta"),
                "complete": (
                    econ.get("campaign_distributed_total") is not None
                    and econ.get("total_spend") is not None
                ),
            },
            "campaigns": children,
        })

    global_values = _cost_values_payload(store, "global", "", start, end)
    budget = economics.get("budget") or {}
    global_effective = {
        "advertising_payment": budget.get("advertising_payment") if budget.get("complete") else None,
        "contractors_payment": budget.get("contractors_payment") if budget.get("complete") else None,
        "total_spend": budget.get("total_spend") if budget.get("complete") else None,
        "spend_provenance": "manual" if budget.get("complete") else None,
        "period_complete": bool(budget.get("complete")),
    }
    global_row = {
        "entity_type": "global",
        "entity_key": "",
        **global_values,
        "effective": global_effective,
        "reconciliation": {
            "distributed_total": economics.get("source_total_spend"),
            "parent_total": global_effective.get("total_spend"),
            "delta": economics.get("source_reconciliation_delta"),
            "complete": (
                economics.get("source_total_spend") is not None
                and global_effective.get("total_spend") is not None
            ),
        },
    }
    return {
        "period": {"from": _iso(start), "to": _iso(end)},
        "global": global_row,
        "sources": sources,
    }


def _replace_cost_entity_period(store, level, entity_key, period):
    periods = store["periods"]
    if level == "global":
        target = list(periods.get("global") or [])
        container = None
    elif level == "source":
        target = list((periods.get("sources") or {}).get(entity_key) or [])
        container = periods.setdefault("sources", {})
    elif level == "campaign":
        target = list((periods.get("campaigns") or {}).get(entity_key) or [])
        container = periods.setdefault("campaigns", {})
    else:
        raise ValueError("invalid advertising cost level")

    target = [
        item for item in target
        if not (item["from"] == period["from"] and item["to"] == period["to"])
    ]
    if any(
        period.get(field) is not None
        for field in ("advertising_payment", "contractors_payment", "total_spend")
    ):
        target.append(period)

    if level == "global":
        periods["global"] = target
    else:
        if target:
            container[entity_key] = target
        else:
            container.pop(entity_key, None)


def _persist_cost_store(conn, store):
    normalized = _normalize_hierarchical_cost_store(store)
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    conn.execute(
        """
        INSERT INTO report_blobs(key,payload,updated_by,updated_at)
        VALUES(?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET
          payload=excluded.payload,
          updated_by=excluded.updated_by,
          updated_at=excluded.updated_at
        """,
        (ADVERTISING_COSTS_KEY, payload, g.user["id"], iso_now()),
    )
    return normalized


def _metrika_sync_has_changes(completed_updated, current_result):
    return bool(completed_updated or current_result.get("updated"))


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
        trend_metric = request.args.get("trend", "visits")
        if trend_metric not in {"spend", "visits", "target_leads", "primary_visits", "primary_paid", "total_paid", "actual_received_total"}:
            abort(400)
        payload = _report_payload(db(), start, end, compare_mode, trend_metric)
        payload["today"] = _iso(today)
        payload["availableComparisons"] = ["previous_period", *(["previous_quarter_avg", "year_avg"] if _same_month(start, end) else []), "last_year"]
        perms = user_permissions(g.user)
        payload["costEditor"] = {
            "canEdit": "upload_services" in perms,
            "csrf": csrf_token() if "upload_services" in perms else "",
        }
        return jsonify(payload)

    @app.get("/api/reports/advertising/costs")
    @permission_required("upload_services")
    def advertising_costs_get():
        today = _today()
        default_start = today.replace(day=1)
        try:
            start = _parse_date(request.args.get("from") or _iso(default_start))
            end = _parse_date(request.args.get("to") or _iso(today))
        except ValueError:
            abort(400)
        if start > end or (end - start).days > 730:
            abort(400)
        return jsonify(**_cost_hierarchy_payload(db(), start, end))

    @app.post("/api/reports/advertising/costs")
    @permission_required("upload_services")
    def advertising_costs_save():
        require_csrf()
        try:
            level = str(request.form.get("entity_type") or "").strip()
            entity_key = str(request.form.get("entity_key") or "").strip()
            start = _parse_date(str(request.form.get("from") or ""))
            end = _parse_date(str(request.form.get("to") or ""))
            if level not in {"global", "source", "campaign"}:
                raise ValueError("invalid advertising cost level")
            if start > end or (end - start).days > 730:
                raise ValueError("invalid period")
            period = _cost_period_triplet({
                "from": _iso(start),
                "to": _iso(end),
                "advertising_payment": request.form.get("advertising_payment"),
                "contractors_payment": request.form.get("contractors_payment"),
                "total_spend": request.form.get("total_spend"),
            })
        except (ValueError, TypeError):
            return jsonify(
                status="error",
                message=(
                    "Проверьте период. Если вводите расходы, заполните "
                    "«Оплата рекламы», «Подрядчики» и «Всего»; "
                    "«Всего» должно равняться рекламе плюс подрядчики."
                ),
            ), 400

        conn = db()
        store = _load_cost_store(conn)
        source_registry = {
            item["source_key"]: item
            for item in _source_registry(conn, store)
        }
        campaign_registry = {
            item["campaign_key"]: item
            for item in _campaign_registry(conn, store)
        }

        if level == "global":
            entity_key = ""

        elif level == "source":
            source_name = str(request.form.get("source_name") or "").strip()
            channel_name = str(request.form.get("channel_name") or "").strip()
            if not entity_key:
                if not source_name:
                    return jsonify(status="error", message="Укажите название источника."), 400
                existing = next(
                    (
                        item for item in source_registry.values()
                        if item["source_name"].strip().lower() == source_name.lower()
                    ),
                    None,
                )
                entity_key = existing["source_key"] if existing else _manual_source_key(source_name)
                if not existing:
                    source_registry[entity_key] = {
                        "source_key": entity_key,
                        "source_name": source_name,
                        "channel_name": channel_name or "Другие источники",
                        "manual": True,
                    }
            if entity_key not in source_registry:
                return jsonify(status="error", message="Неизвестный рекламный источник."), 400
            meta = source_registry[entity_key]
            store["sources"][entity_key] = {
                "name": meta["source_name"],
                "channel_name": meta["channel_name"],
                "manual": bool(meta["manual"]),
            }

        else:
            if entity_key not in campaign_registry:
                return jsonify(status="error", message="Неизвестная рекламная кампания."), 400
            meta = campaign_registry[entity_key]
            source_key = meta["source_key"]
            if source_key not in source_registry:
                return jsonify(status="error", message="Неизвестный источник кампании."), 400
            source_meta = source_registry[source_key]
            store["sources"][source_key] = {
                "name": source_meta["source_name"],
                "channel_name": source_meta["channel_name"],
                "manual": bool(source_meta["manual"]),
            }
            store["campaigns"][entity_key] = {
                "source_key": source_key,
                "campaign_id": meta["campaign_id"],
                "campaign_name": meta["campaign_name"],
            }

        try:
            _replace_cost_entity_period(store, level, entity_key, period)
            normalized = _persist_cost_store(conn, store)
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            message = (
                "Периоды расходов одной строки не должны пересекаться. "
                "Измените существующий период или выберите непересекающийся диапазон."
                if "overlap" in str(exc)
                else "Не удалось сохранить рекламные расходы."
            )
            return jsonify(status="error", message=message), 409
        except sqlite3.DatabaseError:
            conn.rollback()
            return jsonify(status="error", message="Не удалось сохранить рекламные расходы."), 500

        audit(
            "advertising_costs_saved",
            target_user_id=g.user["id"],
            details=(
                f"entity={level}:{entity_key};"
                f"period={period['from']}..{period['to']}"
            ),
        )
        return jsonify(
            status="ok",
            message="Расходы сохранены.",
            entity_type=level,
            entity_key=entity_key,
            hierarchy=_cost_hierarchy_payload(conn, start, end),
            version=normalized["version"],
        )

    @app.post("/api/reports/advertising/metrika/sync")
    @permission_required("upload_services")
    def advertising_metrika_sync():
        require_csrf()
        token = metrika_auto.token_from_env()
        if not token:
            return jsonify(
                status="error",
                message="Яндекс Метрика ещё не подключена к серверу.",
            ), 400

        conn = db()
        try:
            with metrika_auto.MetrikaSyncLock():
                sync_started = datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
                current_day = metrika_auto.current_local_day(sync_started)
                completed_targets = metrika_auto.completed_sync_days(conn, sync_started)

                client = metrika_auto.MetrikaClient(token)
                campaign_evidence = _campaign_evidence_map(conn)
                prepared_completed = []
                for day in completed_targets:
                    core_raw, core_filename = client.fetch_day_archive(
                        day,
                        completed=True,
                        diagnostics=False,
                    )
                    core_parsed = _parse_metrika_zip(
                        core_raw,
                        core_filename,
                        now=sync_started,
                        campaign_evidence=campaign_evidence,
                    )
                    campaign_evidence = _merge_campaign_evidence_maps(
                        campaign_evidence,
                        _campaign_evidence_map_from_rows(core_parsed.get("campaign_evidence")),
                    )
                    if core_parsed["completed_days"] != [day.isoformat()] or core_parsed.get("provisional_days"):
                        raise ValueError("API Метрики вернул неожиданный завершённый период.")

                    full_parsed = None
                    if not _completed_metrika_matches_registered(conn, core_parsed):
                        full_raw, full_filename = client.fetch_day_archive(
                            day,
                            completed=True,
                            diagnostics=True,
                        )
                        full_parsed = _parse_metrika_zip(
                            full_raw,
                            full_filename,
                            now=sync_started,
                            campaign_evidence=campaign_evidence,
                        )
                        campaign_evidence = _merge_campaign_evidence_maps(
                            campaign_evidence,
                            _campaign_evidence_map_from_rows(full_parsed.get("campaign_evidence")),
                        )
                        if (
                            full_parsed["completed_days"] != [day.isoformat()]
                            or full_parsed.get("provisional_days")
                            or full_parsed["traffic_rows"] != core_parsed["traffic_rows"]
                            or full_parsed["goal_rows"] != core_parsed["goal_rows"]
                        ):
                            raise ValueError("Полная и контрольная выгрузки Метрики не совпали.")
                    prepared_completed.append((core_parsed, full_parsed))

                current_raw, current_filename = client.fetch_day_archive(
                    current_day,
                    completed=False,
                    diagnostics=False,
                )
                current_parsed = _parse_metrika_zip(
                    current_raw,
                    current_filename,
                    allow_provisional=True,
                    now=sync_started,
                    campaign_evidence=campaign_evidence,
                )
                if (
                    current_parsed.get("completed_days")
                    or current_parsed.get("provisional_days") != [current_day.isoformat()]
                ):
                    raise ValueError("API Метрики вернул неожиданный текущий период.")

                # The API fetch happens outside the DB write transaction.  A
                # non-blocking process lock prevents parallel refresh jobs.
                # Re-check the day after the network phase so a midnight
                # rollover cannot store yesterday as a provisional snapshot.
                if metrika_auto.current_local_day() != current_day:
                    raise ValueError("Во время обновления сменились календарные сутки. Нажмите кнопку ещё раз.")

                conn.execute("BEGIN IMMEDIATE")
                still_targets = {
                    day.isoformat()
                    for day in metrika_auto.completed_sync_days(conn, sync_started)
                }
                completed_updated = []
                completed_unchanged = []
                for core_parsed, full_parsed in prepared_completed:
                    parsed_day = str((core_parsed.get("completed_days") or [""])[0])
                    if parsed_day not in still_targets:
                        continue
                    if _completed_metrika_matches_registered(conn, core_parsed):
                        completed_unchanged.append(parsed_day)
                        continue
                    if full_parsed is None:
                        raise ValueError("Не подготовлен полный архив изменившегося завершённого дня.")
                    _store_metrika(conn, full_parsed, g.user["id"])
                    completed_updated.append(parsed_day)

                current_result = _store_metrika_provisional(
                    conn,
                    current_parsed,
                    g.user["id"],
                )
                conn.commit()
        except metrika_auto.MetrikaBusyError as exc:
            return jsonify(status="busy", message=str(exc)), 409
        except (metrika_auto.MetrikaApiError, ValueError, OSError, sqlite3.DatabaseError) as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            return jsonify(
                status="error",
                message=str(exc) or "Не удалось получить данные Яндекс Метрики.",
            ), 400

        if _metrika_sync_has_changes(completed_updated, current_result):
            audit(
                "advertising_metrika_auto_sync",
                target_user_id=g.user["id"],
                details=(
                    "completed_updated=" + ",".join(completed_updated)
                    + "; completed_unchanged=" + ",".join(completed_unchanged)
                    + "; current_day=" + current_day.isoformat()
                    + "; current_updated=" + ("1" if current_result["updated"] else "0")
                ),
            )
        return jsonify(
            status="ok",
            message="Данные Яндекс Метрики обновлены.",
            completed_days=completed_updated,
            checked_completed_days=completed_unchanged,
            current_day=current_day.isoformat(),
            current_updated=bool(current_result["updated"]),
            current_reused=bool(current_result["reused"]),
        )

    @app.post("/api/reports/advertising/metrika/import")
    @permission_required("upload_services")
    def advertising_metrika_import():
        require_csrf()
        uploaded = request.files.get("metrika_zip")
        if not uploaded or not uploaded.filename:
            return jsonify(status="error", message="Выберите ZIP Яндекс Метрики."), 400
        try:
            conn = db()
            parsed = _parse_metrika_zip(
                uploaded.read(),
                uploaded.filename,
                campaign_evidence=_campaign_evidence_map(conn),
            )
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
