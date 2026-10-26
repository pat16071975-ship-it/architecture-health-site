import calendar
import hashlib
import json
import math
import re
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Response, abort, g, jsonify, request

from app import DB_PATH, SITE_ROOT, admin_required, audit, db, iso_now, permission_required, require_csrf


SCHEMA_VERSION = 1
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_TIMEZONE = "Asia/Novosibirsk"
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
    groups = {}
    def key_for(row):
        if level == "campaign":
            campaign_id, campaign_name = str(row["campaign_id"] or ""), str(row["campaign_name"] or "")
            return None if not campaign_id and not campaign_name else (str(row["source_key"]), campaign_id, campaign_name)
        return (str(row["source_key"]),)
    def ensure(row, key):
        if key not in groups:
            groups[key] = {
                "source_key": str(row["source_key"]), "source_name": str(row["source_name"]),
                "channel_name": str(row["channel_name"] or ""),
                "campaign_id": str(row["campaign_id"] or "") if level == "campaign" else "",
                "campaign_name": str(row["campaign_name"] or "") if level == "campaign" else "",
                **{metric: None for metric in METRIC_KEYS},
            }
        return groups[key]
    def add(row, fields):
        key = key_for(row)
        if key is None:
            return
        target = ensure(row, key)
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
