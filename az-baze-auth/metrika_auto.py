import csv
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


class MetrikaApiError(RuntimeError):
    pass


def token_from_env():
    return str(os.environ.get(TOKEN_ENV) or "").strip()


def previous_completed_day(now=None):
    current = now or datetime.now(ZoneInfo(TIMEZONE))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo(TIMEZONE))
    return current.astimezone(ZoneInfo(TIMEZONE)).date() - timedelta(days=1)


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
                known_days.update(
                    date.fromisoformat(str(value))
                    for value in values
                    if value
                )
    except Exception:
        known_days = set()

    if not known_days:
        row = conn.execute(
            "SELECT MAX(metric_date) AS day FROM advertising_traffic_daily WHERE provider='metrika'"
        ).fetchone()
        if row and row["day"]:
            known_days.add(date.fromisoformat(str(row["day"])))

    latest = max(known_days) if known_days else None
    start = (latest + timedelta(days=1)) if latest else yesterday
    if start > yesterday:
        return []
    return [start + timedelta(days=offset) for offset in range((yesterday - start).days + 1)]


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
                raise MetrikaApiError("Нужно повторно подключить Яндекс Метрику.") from None
            if exc.code == 403:
                raise MetrikaApiError("Нет доступа к счётчику Яндекс Метрики.") from None
            if exc.code == 429:
                raise MetrikaApiError("Яндекс Метрика временно ограничила частоту запросов. Повторите позже.") from None
            raise MetrikaApiError("Яндекс Метрика вернула ошибку API.") from None
        except (urllib.error.URLError, TimeoutError):
            raise MetrikaApiError("Не удалось связаться с API Яндекс Метрики.") from None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MetrikaApiError("API Яндекс Метрики вернул некорректный ответ.") from None

    def counter(self):
        payload = self.request_json(f"/management/v1/counter/{COUNTER_ID}", {})
        counter = payload.get("counter") if isinstance(payload, dict) else None
        if not isinstance(counter, dict) or str(counter.get("id") or "") != COUNTER_ID:
            raise MetrikaApiError("Не удалось подтвердить доступ к счётчику Яндекс Метрики.")
        zone = str(counter.get("time_zone_name") or "")
        if zone and zone != TIMEZONE:
            raise MetrikaApiError("Часовой пояс счётчика Метрики не совпадает с Asia/Novosibirsk.")
        return payload

    def goals(self):
        payload = self.request_json(f"/management/v1/counter/{COUNTER_ID}/goals", {})
        goals = payload.get("goals") if isinstance(payload, dict) else None
        if not isinstance(goals, list):
            raise MetrikaApiError("Не удалось получить цели Яндекс Метрики.")
        selected = []
        by_name = {str(goal.get("name") or ""): goal for goal in goals if isinstance(goal, dict)}
        for name in EXPORT_GOAL_NAMES:
            goal = by_name.get(name)
            if goal and goal.get("id") is not None:
                selected.append(goal)
        if not selected:
            raise MetrikaApiError("В счётчике не найдены используемые цели Яндекс Метрики.")
        return payload, selected

    def table(self, *, day, dimensions, metrics, filters=ROBOT_FILTER, sort="-ym:s:visits"):
        params = {
            "ids": COUNTER_ID,
            "dimensions": ",".join(dimensions),
            "metrics": ",".join(metrics),
            "date1": day.isoformat(),
            "date2": day.isoformat(),
            "filters": filters,
            "limit": 100000,
            "offset": 1,
            "lang": "ru",
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
            if payload.get("sampled") is True or float(payload.get("sample_share") or 1.0) != 1.0:
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

    def fetch_day_archive(self, day):
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
                "day_status": "completed_calendar_day_revisable",
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
            "direct_campaign_day": None,
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

        files = {
            "az_analytics.json": json.dumps(az_payload, ensure_ascii=False, indent=2).encode("utf-8"),
            f"REPORT-{day.isoformat()}.txt": report_text.encode("utf-8"),
            "metrica_api_raw.json": json.dumps(traffic_raw, ensure_ascii=False, indent=2).encode("utf-8"),
            "campaigns_raw.json": json.dumps(campaign_raw, ensure_ascii=False, indent=2).encode("utf-8"),
            "goals_raw.json": json.dumps(goals_payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "counter_raw.json": json.dumps(counter_payload, ensure_ascii=False, indent=2).encode("utf-8"),
        }
        readme = (
            f"AZ-BAZE automatic Yandex Metrica import for {day.isoformat()}.\n"
            "Source: Yandex Metrica API, read-only OAuth.\n"
            "The archive is generated internally and is parsed by the existing az_analytics_export_v1 importer.\n"
            "Robots excluded. Attribution: cross_device_last_significant.\n"
        ).encode("utf-8")
        files["README.txt"] = readme
        checksums = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
        files["SHA256.json"] = json.dumps(checksums, ensure_ascii=False, indent=2).encode("utf-8")

        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(files):
                archive.writestr(name, files[name])
        return raw.getvalue(), f"AZ-BAZE-auto-metrika-{day.isoformat()}.zip"
