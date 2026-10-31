import csv
import fcntl
import hashlib
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

COUNTER_ID = "113776005"
TIMEZONE = "Asia/Novosibirsk"
ATTRIBUTION = "cross_device_last_significant"
ROBOT_FILTER = "ym:s:isRobot=='No'"
TOKEN_ENV = "AZBAZE_YANDEX_METRIKA_TOKEN"
API_ROOT = "https://api-metrika.yandex.net"
SYNC_LOCK_PATH = os.environ.get("AZBAZE_METRIKA_SYNC_LOCK", "/var/lib/az-baze/metrika-sync.lock")
TRACKING_START = date(2026, 10, 27)
RECENT_COMPLETED_REFRESH_DAYS = 3

EXPORT_GOAL_NAMES = (
    "Открытие окна записи",
    "Переход к записи в Medflex",
    "Нажатие на телефон",
    "Автоцель: клик по номеру телефона",
    "Автоцель: переход в мессенджер",
    "Автоцель: переход в соц.сеть",
    "Автоцель: скачивание файла",
)
TECHNICAL_GOALS = {"Открытие окна записи"}

TRAFFIC_DIMENSIONS = (
    "ym:s:date",
    "ym:s:cross_device_last_significantTrafficSource",
    "ym:s:cross_device_last_significantSourceEngine",
    "ym:s:cross_device_last_significantUTMSource",
    "ym:s:cross_device_last_significantUTMMedium",
    "ym:s:cross_device_last_significantUTMCampaign",
    "ym:s:cross_device_last_significantUTMContent",
    "ym:s:cross_device_last_significantUTMTerm",
)
BASE_METRICS = (
    "ym:s:visits",
    "ym:s:users",
    "ym:s:pageviews",
    "ym:s:bounceRate",
    "ym:s:avgVisitDurationSeconds",
)
CAMPAIGN_DIMENSION = "ym:s:cross_device_last_significantDirectClickOrder"

MEDFLEX_COUNTERS = ("113892774", "113916673")
STANDARD_ARCHIVE_NAMES = {
    "README.txt",
    "SHA256.json",
    "age.csv", "age_raw.json",
    "az_analytics.json",
    "browsers.csv", "browsers_raw.json",
    "counter_raw.json",
    "daily.csv", "daily_raw.json",
    "devices.csv", "devices_raw.json",
    "gender.csv", "gender_raw.json",
    "geography.csv", "geography_raw.json",
    "goal_registry.csv",
    "goals_raw.json",
    "hours.csv", "hours_raw.json",
    "landing.csv", "landing_raw.json",
    "medflex_daily_raw.json",
    "metrica_api_raw.json",
    "metrica_goal_day.csv",
    "metrica_traffic_day.csv",
    "new_visitors.csv", "new_visitors_raw.json",
    "pages.csv", "pages_raw.json",
    "referrers.csv", "referrers_raw.json",
    "search_phrases.csv", "search_phrases_raw.json",
    "sources.csv", "sources_raw.json",
    "without_test.csv", "without_test_raw.json",
}
DIAGNOSTIC_SPECS = {
    "daily": (("ym:s:date",), "full", ROBOT_FILTER, "-ym:s:visits"),
    "sources": (
        ("ym:s:cross_device_last_significantTrafficSource", "ym:s:cross_device_last_significantSourceEngine"),
        "full", ROBOT_FILTER, "-ym:s:visits",
    ),
    "devices": (("ym:s:deviceCategory",), "full", ROBOT_FILTER, "-ym:s:visits"),
    "browsers": (("ym:s:browser",), "full", ROBOT_FILTER, "-ym:s:visits"),
    "hours": (("ym:s:hour",), "full", ROBOT_FILTER, "-ym:s:visits"),
    "new_visitors": (("ym:s:isNewUser",), "full", ROBOT_FILTER, "-ym:s:visits"),
    "search_phrases": (
        ("ym:s:cross_device_last_significantSearchEngineRoot", "ym:s:cross_device_last_significantSearchPhrase"),
        "full", ROBOT_FILTER, "-ym:s:visits",
    ),
    "without_test": (
        ("ym:s:date",),
        "full",
        ROBOT_FILTER + " AND ym:s:cross_device_last_significantUTMCampaign!='metrika_test'",
        "-ym:s:visits",
    ),
    "age": (("ym:s:ageInterval",), "base", ROBOT_FILTER, "-ym:s:visits"),
    "gender": (("ym:s:gender",), "base", ROBOT_FILTER, "-ym:s:visits"),
    "geography": (("ym:s:regionCountry", "ym:s:regionCity"), "base", ROBOT_FILTER, "-ym:s:visits"),
    "landing": (("ym:s:startURL",), "base", ROBOT_FILTER, "-ym:s:visits"),
    "referrers": (("ym:s:cross_device_last_significantReferer",), "base", ROBOT_FILTER, "-ym:s:visits"),
    "pages": (("ym:pv:URLPathFull",), "pages", "ym:pv:isRobot=='No'", "-ym:pv:pageviews"),
}


def _csv_bytes(headers, rows):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _table_csv(payload):
    query = payload.get("query") or {}
    dimensions = list(query.get("dimensions") or [])
    metrics = list(query.get("metrics") or [])
    rows = []
    for item in payload.get("data") or []:
        dim_values = [_dimension_value(value) or "" for value in (item.get("dimensions") or [])]
        metric_values = list(item.get("metrics") or [])
        rows.append([*dim_values, *metric_values])
    return _csv_bytes([*dimensions, *metrics], rows)


def _dict_csv(rows, headers):
    return _csv_bytes(
        headers,
        [
            [
                "" if row.get(header) is None else row.get(header)
                for header in headers
            ]
            for row in rows
        ],
    )


def _validate_additive_totals(payload, metrics, label):
    totals = payload.get("totals")
    if not isinstance(totals, list) or len(totals) != len(metrics):
        raise MetrikaApiError(f"Яндекс Метрика не вернула контрольные итоги: {label}.")
    additive = [
        index
        for index, metric in enumerate(metrics)
        if metric == "ym:s:visits"
        or (
            metric.startswith("ym:s:goal")
            and (metric.endswith("reaches") or metric.endswith("visits"))
        )
    ]
    for index in additive:
        actual = sum(
            float((row.get("metrics") or [])[index] or 0)
            for row in (payload.get("data") or [])
            if len(row.get("metrics") or []) == len(metrics)
        )
        expected = float(totals[index] or 0)
        if abs(actual - expected) > 1e-7:
            raise MetrikaApiError(
                f"Яндекс Метрика скрыла или не вернула часть строк: {label}. Импорт остановлен."
            )


class MetrikaApiError(RuntimeError):
    pass


class MetrikaAuthError(MetrikaApiError):
    pass


class MetrikaAccessError(MetrikaApiError):
    pass


class MetrikaRateLimitError(MetrikaApiError):
    pass


class MetrikaBusyError(MetrikaApiError):
    pass


class MetrikaSyncLock:
    def __init__(self, path=None):
        self.path = str(path or SYNC_LOCK_PATH)
        self.handle = None

    def __enter__(self):
        self.handle = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            raise MetrikaBusyError("Обновление Яндекс Метрики уже выполняется.") from None
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def token_from_env():
    return str(os.environ.get(TOKEN_ENV) or "").strip()


def current_local_day(now=None):
    current = now or datetime.now(ZoneInfo(TIMEZONE))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo(TIMEZONE))
    return current.astimezone(ZoneInfo(TIMEZONE)).date()


def previous_completed_day(now=None):
    return current_local_day(now) - timedelta(days=1)


def recent_completed_days(now=None, count=RECENT_COMPLETED_REFRESH_DAYS):
    yesterday = previous_completed_day(now)
    start = max(TRACKING_START, yesterday - timedelta(days=max(0, int(count) - 1)))
    if start > yesterday:
        return []
    return [
        start + timedelta(days=offset)
        for offset in range((yesterday - start).days + 1)
    ]


def missing_completed_days(conn, now=None):
    yesterday = previous_completed_day(now)
    known_days = set()
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
                for value in values:
                    if value:
                        known_days.add(date.fromisoformat(str(value)))
    except Exception:
        known_days = set()

    if not known_days:
        rows = conn.execute(
            "SELECT DISTINCT metric_date FROM advertising_traffic_daily "
            "WHERE provider='metrika' ORDER BY metric_date"
        ).fetchall()
        known_days.update(
            date.fromisoformat(str(row["metric_date"]))
            for row in rows
            if row["metric_date"]
        )

    start = TRACKING_START
    if start > yesterday:
        return []
    return [
        start + timedelta(days=offset)
        for offset in range((yesterday - start).days + 1)
        if start + timedelta(days=offset) not in known_days
    ]


def completed_sync_days(conn, now=None):
    return sorted(set(missing_completed_days(conn, now)) | set(recent_completed_days(now)))


def _dimension_value(item):
    if not isinstance(item, dict):
        return None
    value = item.get("name")
    return None if value is None else str(value)


def _dimension_id(item):
    if not isinstance(item, dict):
        return None
    value = item.get("id")
    return None if value is None else str(value)


class MetrikaClient:
    def __init__(self, token, request_json=None):
        self.token = str(token or "").strip()
        self.request_json = request_json or self._request_json
        self._counter_cache = None
        self._goals_cache = None
        if not self.token:
            raise MetrikaApiError("Нужно подключить Яндекс Метрику.")

    def _request_json(self, path, params=None):
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = API_ROOT + path + (("?" + query) if query else "")
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": "OAuth " + self.token,
                "Accept": "application/json",
                "User-Agent": "AZ-BAZE-Metrika/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise MetrikaAuthError("Нужно повторно подключить Яндекс Метрику.") from None
            if exc.code == 403:
                raise MetrikaAccessError("Нет доступа к счётчику Яндекс Метрики.") from None
            if exc.code in {420, 429}:
                raise MetrikaRateLimitError("Яндекс Метрика временно ограничила частоту запросов. Повторите позже.") from None
            raise MetrikaApiError("Яндекс Метрика вернула ошибку API.") from None
        except (urllib.error.URLError, TimeoutError):
            raise MetrikaApiError("Не удалось связаться с API Яндекс Метрики.") from None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MetrikaApiError("API Яндекс Метрики вернул некорректный ответ.") from None

    def counter(self):
        if self._counter_cache is not None:
            return self._counter_cache
        payload = self.request_json(f"/management/v1/counter/{COUNTER_ID}", {})
        counter = payload.get("counter") if isinstance(payload, dict) else None
        if not isinstance(counter, dict) or str(counter.get("id") or "") != COUNTER_ID:
            raise MetrikaApiError("Не удалось подтвердить доступ к счётчику Яндекс Метрики.")
        if str(counter.get("status") or "Active") != "Active":
            raise MetrikaApiError("Счётчик Яндекс Метрики не активен.")
        zone = str(counter.get("time_zone_name") or "")
        if zone and zone != TIMEZONE:
            raise MetrikaApiError("Часовой пояс счётчика Метрики не совпадает с Asia/Novosibirsk.")
        self._counter_cache = payload
        return payload

    def goals(self):
        if self._goals_cache is not None:
            return self._goals_cache
        payload = self.request_json(f"/management/v1/counter/{COUNTER_ID}/goals", {})
        goals = payload.get("goals") if isinstance(payload, dict) else None
        if not isinstance(goals, list):
            raise MetrikaApiError("Не удалось получить цели Яндекс Метрики.")
        by_name = {
            str(goal.get("name") or ""): goal
            for goal in goals
            if isinstance(goal, dict) and str(goal.get("status") or "Active") == "Active"
        }
        missing = [
            name
            for name in EXPORT_GOAL_NAMES
            if not by_name.get(name) or by_name[name].get("id") is None
        ]
        if missing:
            raise MetrikaApiError(
                "В счётчике отсутствуют используемые цели Яндекс Метрики: "
                + ", ".join(missing)
            )
        self._goals_cache = (payload, [by_name[name] for name in EXPORT_GOAL_NAMES])
        return self._goals_cache

    def table(self, *, day, dimensions, metrics, filters=ROBOT_FILTER, sort="-ym:s:visits", counter_id=COUNTER_ID):
        params = {
            "ids": str(counter_id),
            "dimensions": ",".join(dimensions),
            "metrics": ",".join(metrics),
            "date1": day.isoformat(),
            "date2": day.isoformat(),
            "filters": filters,
            "limit": 100000,
            "offset": 1,
            "lang": "ru",
            "attribution": ATTRIBUTION,
        }
        if sort:
            params["sort"] = sort
        pages = []
        offset = 1
        total_rows = None
        while True:
            params["offset"] = offset
            payload = self.request_json("/stat/v1/data", dict(params))
            if not isinstance(payload, dict):
                raise MetrikaApiError("API Яндекс Метрики вернул некорректный отчёт.")
            sample_share = payload.get("sample_share")
            if payload.get("sampled") is True or (
                sample_share is not None and float(sample_share) != 1.0
            ):
                raise MetrikaApiError("Яндекс Метрика вернула семплированные данные. Импорт остановлен.")
            if payload.get("total_rows_rounded") is True:
                raise MetrikaApiError("Яндекс Метрика вернула округлённое число строк. Импорт остановлен.")
            data = payload.get("data")
            if not isinstance(data, list):
                raise MetrikaApiError("В ответе Яндекс Метрики отсутствуют строки отчёта.")
            if total_rows is None:
                total_rows = int(payload.get("total_rows") or len(data))
            pages.extend(data)
            if len(pages) >= total_rows:
                payload = dict(payload)
                payload["data"] = pages
                payload["total_rows"] = total_rows
                return payload
            if not data:
                raise MetrikaApiError("Не удалось получить все страницы отчёта Яндекс Метрики.")
            offset += len(data)

    def fetch_day_archive(self, day, *, completed=True, diagnostics=True):
        counter_payload = self.counter()
        goals_payload, goals = self.goals()
        goal_registry = [
            {
                "goal_id": str(goal["id"]),
                "goal_name": str(goal.get("name") or ""),
                "lead_class": "technical" if str(goal.get("name") or "") in TECHNICAL_GOALS else "contact_intent",
                "included_in_confirmed_leads": False,
            }
            for goal in goals
        ]
        metrics = list(BASE_METRICS)
        for goal in goals:
            gid = str(goal["id"])
            metrics.extend((f"ym:s:goal{gid}reaches", f"ym:s:goal{gid}visits"))
        if len(metrics) > 20:
            raise MetrikaApiError("Слишком много целей для одного совместимого отчёта Метрики.")

        traffic_raw = self.table(
            day=day,
            dimensions=TRAFFIC_DIMENSIONS,
            metrics=metrics,
        )
        campaign_raw = self.table(
            day=day,
            dimensions=(CAMPAIGN_DIMENSION,),
            metrics=metrics,
        )
        _validate_additive_totals(traffic_raw, metrics, "основной срез")
        _validate_additive_totals(campaign_raw, metrics, "кампании")

        diagnostic_raw = {}
        if diagnostics:
            for name, (dimensions, metric_kind, filters, sort) in DIAGNOSTIC_SPECS.items():
                report_metrics = (
                    tuple(metrics)
                    if metric_kind == "full"
                    else tuple(BASE_METRICS)
                    if metric_kind == "base"
                    else ("ym:pv:pageviews", "ym:pv:users")
                )
                diagnostic_raw[name] = self.table(
                    day=day,
                    dimensions=dimensions,
                    metrics=report_metrics,
                    filters=filters,
                    sort=sort,
                )

        direct_campaign_rows = []
        for api_row in campaign_raw.get("data") or []:
            dims = list(api_row.get("dimensions") or [])
            values = list(api_row.get("metrics") or [])
            if len(dims) != 1 or len(values) != len(metrics):
                raise MetrikaApiError("Структура разбивки кампаний Метрики изменилась. Импорт остановлен.")
            campaign_id = _dimension_id(dims[0]) or ""
            campaign_name = _dimension_value(dims[0]) or ""
            direct_row = {
                "date": day.isoformat(),
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "visits": int(round(values[0] or 0)),
                "goals": [],
            }
            cursor = 5
            for goal in goal_registry:
                reaches = int(round(values[cursor] or 0))
                goal_visits = int(round(values[cursor + 1] or 0))
                cursor += 2
                direct_row["goals"].append(
                    {
                        "goal_id": goal["goal_id"],
                        "goal_name": goal["goal_name"],
                        "reaches": reaches,
                        "goal_visits": goal_visits,
                    }
                )
            direct_campaign_rows.append(direct_row)

        medflex_status = {counter_id: "not_checked" for counter_id in MEDFLEX_COUNTERS}
        medflex_primary_raw = {
            "counter_id": MEDFLEX_COUNTERS[0],
            "status": "not_checked",
            "data": [],
        }
        if diagnostics:
            medflex_status = {}
            medflex_primary_raw = None
            for counter_id in MEDFLEX_COUNTERS:
                try:
                    raw = self.table(
                        day=day,
                        dimensions=("ym:s:date",),
                        metrics=("ym:s:visits", "ym:s:users", "ym:s:pageviews"),
                        filters=ROBOT_FILTER,
                        sort="-ym:s:visits",
                        counter_id=counter_id,
                    )
                    medflex_status[counter_id] = (
                        "no_data" if int(raw.get("total_rows") or 0) == 0 else "ok"
                    )
                    if counter_id == MEDFLEX_COUNTERS[0]:
                        medflex_primary_raw = raw
                except MetrikaAccessError:
                    medflex_status[counter_id] = "no_access"
                except MetrikaApiError:
                    medflex_status[counter_id] = "error"

            if medflex_primary_raw is None:
                medflex_primary_raw = {
                    "counter_id": MEDFLEX_COUNTERS[0],
                    "status": medflex_status[MEDFLEX_COUNTERS[0]],
                    "data": [],
                }

        fetched_at = datetime.now(timezone.utc).isoformat()
        batch_id = "az-metrika-auto-" + fetched_at
        traffic_rows = []
        goal_rows = []
        for index, api_row in enumerate(traffic_raw["data"]):
            dims = list(api_row.get("dimensions") or [])
            values = list(api_row.get("metrics") or [])
            if len(dims) != len(TRAFFIC_DIMENSIONS) or len(values) != len(metrics):
                raise MetrikaApiError("Структура отчёта Метрики изменилась. Импорт остановлен.")
            dim_payload = {
                "date": _dimension_value(dims[0]),
                "traffic_source_id": _dimension_id(dims[1]),
                "traffic_source_name": _dimension_value(dims[1]),
                "source_detail_id": _dimension_id(dims[2]),
                "source_detail": _dimension_value(dims[2]),
                "utm_source": _dimension_value(dims[3]),
                "utm_medium": _dimension_value(dims[4]),
                "utm_campaign": _dimension_value(dims[5]),
                "utm_content": _dimension_value(dims[6]),
                "utm_term": _dimension_value(dims[7]),
            }
            row_key = json.dumps([day.isoformat(), index, dims], ensure_ascii=False, sort_keys=True).encode("utf-8")
            row_id = hashlib.sha256(row_key).hexdigest()[:24]
            traffic = {
                "row_id": row_id,
                "batch_id": batch_id,
                "counter_id": COUNTER_ID,
                "date": dim_payload["date"] or day.isoformat(),
                "day_status": (
                    "completed_calendar_day_revisable"
                    if completed else "provisional_current_day"
                ),
                **{key: dim_payload[key] for key in (
                    "traffic_source_id", "traffic_source_name", "source_detail_id", "source_detail",
                    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
                )},
                "direct_campaign_id": None,
                "direct_campaign_name": None,
                "campaign_match_status": "unresolved" if dim_payload["traffic_source_id"] == "ad" else "not_applicable",
                "visits": int(round(values[0] or 0)),
                "users_nonadditive": int(round(values[1] or 0)),
                "pageviews": int(round(values[2] or 0)),
                "bounce_rate": float(values[3] or 0),
                "avg_visit_duration_seconds": float(values[4] or 0),
                "confirmed_leads": None,
                "is_test_tag": str(dim_payload["utm_campaign"] or "").strip().lower() == "metrika_test",
                "attribution": ATTRIBUTION,
                "robot_filter": "exclude_robots",
            }
            traffic_rows.append(traffic)
            cursor = 5
            for goal in goal_registry:
                reaches = int(round(values[cursor] or 0))
                goal_visits = int(round(values[cursor + 1] or 0))
                cursor += 2
                goal_rows.append(
                    {
                        "traffic_row_id": row_id,
                        "date": day.isoformat(),
                        "goal_id": goal["goal_id"],
                        "goal_name": goal["goal_name"],
                        "reaches": reaches,
                        "goal_visits": goal_visits,
                        "lead_class": goal["lead_class"],
                        "included_in_confirmed_leads": False,
                    }
                )

        summary = {
            "visits": sum(row["visits"] for row in traffic_rows),
            "test_tag_visits": sum(row["visits"] for row in traffic_rows if row["is_test_tag"]),
        }
        for goal in goal_registry:
            summary[goal["goal_id"] + "_reaches"] = sum(
                row["reaches"] for row in goal_rows if row["goal_id"] == goal["goal_id"]
            )

        metadata = {
            "schema_version": "az_analytics_export_v1",
            "batch_id": batch_id,
            "fetched_at_utc": fetched_at,
            "period_from": day.isoformat(),
            "period_to": day.isoformat(),
            "day_status": (
                "completed_calendar_day_revisable"
                if completed else "provisional_current_day"
            ),
            "source_timezone": TIMEZONE,
            "counter_id": COUNTER_ID,
            "extraction_method": "api",
            "attribution": ATTRIBUTION,
            "robot_filter": ROBOT_FILTER,
            "sample_share": 1.0,
            "total_rows": len(traffic_rows),
            "api_query": traffic_raw.get("query") or {},
        }
        az_payload = {
            "metadata": metadata,
            "metrica_traffic_day": traffic_rows,
            "metrica_goal_day": goal_rows,
            "goal_registry": goal_registry,
            "direct_campaign_day": direct_campaign_rows,
            "daily_summary": {day.isoformat(): summary},
        }

        campaign_metrics = list(metrics)
        report_lines = [
            "campaigns_clean",
            "Метрики: " + ", ".join(campaign_metrics),
        ]
        totals = list(campaign_raw.get("totals") or [])
        if len(totals) == len(campaign_metrics):
            report_lines.append("Итого: [" + ", ".join(str(float(value or 0)) for value in totals) + "]")
        for api_row in campaign_raw.get("data") or []:
            dims = list(api_row.get("dimensions") or [])
            vals = list(api_row.get("metrics") or [])
            if len(dims) != 1 or len(vals) != len(campaign_metrics):
                raise MetrikaApiError("Структура разбивки кампаний Метрики изменилась. Импорт остановлен.")
            name = _dimension_value(dims[0]) or "Не определено"
            report_lines.append(name + " | " + ", ".join(str(float(value or 0)) for value in vals))
        report_text = "\n".join(report_lines) + "\n\n"

        if not diagnostics:
            files = {
                "az_analytics.json": json.dumps(
                    az_payload, ensure_ascii=False, indent=2
                ).encode("utf-8"),
                f"REPORT-{day.isoformat()}.txt": report_text.encode("utf-8"),
            }
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
                for name in sorted(files):
                    archive.writestr(name, files[name])
            return (
                raw.getvalue(),
                f"AZ-BAZE-auto-metrika-core-{day.isoformat()}.zip",
            )

        traffic_headers = [
            "row_id", "batch_id", "counter_id", "date", "day_status",
            "traffic_source_id", "traffic_source_name", "source_detail_id", "source_detail",
            "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
            "direct_campaign_id", "direct_campaign_name", "campaign_match_status",
            "visits", "users_nonadditive", "pageviews", "bounce_rate",
            "avg_visit_duration_seconds", "confirmed_leads", "is_test_tag",
            "attribution", "robot_filter",
        ]
        goal_headers = [
            "traffic_row_id", "date", "goal_id", "goal_name", "reaches",
            "goal_visits", "lead_class", "included_in_confirmed_leads",
        ]
        registry_headers = [
            "goal_id", "goal_name", "lead_class", "included_in_confirmed_leads",
        ]

        files = {
            "az_analytics.json": json.dumps(az_payload, ensure_ascii=False, indent=2).encode("utf-8"),
            f"REPORT-{day.isoformat()}.txt": report_text.encode("utf-8"),
            "metrica_api_raw.json": json.dumps(traffic_raw, ensure_ascii=False, indent=2).encode("utf-8"),
            "goals_raw.json": json.dumps(goals_payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "counter_raw.json": json.dumps(counter_payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "medflex_daily_raw.json": json.dumps(medflex_primary_raw, ensure_ascii=False, indent=2).encode("utf-8"),
            "metrica_traffic_day.csv": _dict_csv(traffic_rows, traffic_headers),
            "metrica_goal_day.csv": _dict_csv(goal_rows, goal_headers),
            "goal_registry.csv": _dict_csv(goal_registry, registry_headers),
        }
        for name, payload in diagnostic_raw.items():
            files[name + "_raw.json"] = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            files[name + ".csv"] = _table_csv(payload)

        readme = (
            f"АЗ — автоматическая выгрузка Яндекс Метрики за {day.isoformat()}\n"
            "Формат az_analytics_export_v1. Основной файл az_analytics.json.\n"
            "Источник: API Яндекс Метрики, OAuth только с правом чтения.\n"
            "Роботы исключены. Атрибуция: последний значимый переход, кросс-девайс.\n"
            f"Medflex {MEDFLEX_COUNTERS[0]}: {medflex_status[MEDFLEX_COUNTERS[0]]}; "
            f"{MEDFLEX_COUNTERS[1]}: {medflex_status[MEDFLEX_COUNTERS[1]]}.\n"
            + (
                "Статус дня: завершённый календарный день.\n"
                if completed
                else "Статус дня: текущий оперативный срез; повторное обновление заменяет предыдущий срез.\n"
            )
            + "Автоматический импорт выполняется существующим загрузчиком AZ-BAZE.\n"
        ).encode("utf-8")
        files["README.txt"] = readme

        expected_before_hash = set(STANDARD_ARCHIVE_NAMES)
        expected_before_hash.remove("SHA256.json")
        expected_before_hash.add(f"REPORT-{day.isoformat()}.txt")
        if set(files) != expected_before_hash:
            raise MetrikaApiError("Внутренний архив Метрики не совпал с утверждённым составом.")
        checksums = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
        files["SHA256.json"] = json.dumps(checksums, ensure_ascii=False, indent=2).encode("utf-8")
        expected_full = set(expected_before_hash) | {"SHA256.json"}
        if set(files) != expected_full or len(files) != 39:
            raise MetrikaApiError("Внутренний архив Метрики не совпал с утверждённым составом.")

        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(files):
                archive.writestr(name, files[name])
        return raw.getvalue(), f"AZ-BAZE-auto-metrika-{day.isoformat()}.zip"
