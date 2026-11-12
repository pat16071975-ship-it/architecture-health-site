"""Read-only Yandex Direct Reports API client for AZ BAZE.

Uses the SAME existing OAuth token as Metrika. Fetching does not write to
the Direct account or the AZ database; only the caller's guarded transaction
may commit the validated response. No secrets in errors or logs.
"""
import csv
import io
import json
import math
import os
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CAMPAIGNS_URL = "https://api.direct.yandex.com/json/v501/campaigns"
REPORTS_URL = "https://api.direct.yandex.com/json/v501/reports"
REPORT_LIMIT = 6 * 1024 * 1024
FIRST_SUPPORTED_DAY = date(2026, 10, 27)


class DirectApiError(ValueError):
    """Fail closed before any database write."""


def _amount(value, *, optional=False):
    text = str(value if value is not None else "").strip()
    if text in {"", "--", "—"}:
        if optional:
            return None
        raise DirectApiError("Директ не вернул обязательное числовое значение.")
    try:
        number = Decimal(text.replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise DirectApiError("Директ вернул некорректную статистику.") from exc
    if not number.is_finite() or number < 0 or number > Decimal("1000000000000"):
        raise DirectApiError("Директ вернул недопустимую статистику.")
    return number


def _request(url, token, body, *, reports=False):
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json; charset=utf-8",
        "Accept-Language": "ru",
    }
    client_login = str(os.environ.get("AZBAZE_DIRECT_CLIENT_LOGIN") or "").strip()
    if client_login:
        headers["Client-Login"] = client_login
    if reports:
        headers.update({
            "processingMode": "online",
            "returnMoneyInMicros": "false",
            "skipReportHeader": "true",
            "skipReportSummary": "true",
        })
    request = urllib.request.Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=50) as response:
            if response.status != 200:
                raise DirectApiError("Директ вернул неожиданный HTTP-статус.")
            raw = response.read(REPORT_LIMIT + 1)
    except urllib.error.HTTPError as exc:
        if reports and exc.code in {201, 202}:
            raise DirectApiError("Отчёт Директа ещё формируется. Повторите обновление позднее.") from None
        raise DirectApiError("Ошибка доступа к API Яндекс Директа (HTTP %s)." % exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise DirectApiError("Не удалось получить ответ API Яндекс Директа.") from None
    if len(raw) > REPORT_LIMIT:
        raise DirectApiError("Отчёт Директа превышает допустимый размер.")
    return raw


def fetch_daily_campaigns(token, start, end):
    """Return (rows, metadata) for exactly the requested calendar dates.

    Costs: account RUB, include VAT (YES), no money micro-units.
    Conversions: Yandex Direct's aggregate goal metric, not CRM patients.
    Neither missing conversions nor missing costs are silently treated as 0.
    """
    if not token or not isinstance(start, date) or not isinstance(end, date):
        raise DirectApiError("Не подтверждён токен или отчётный период.")
    if start < FIRST_SUPPORTED_DAY or end < start or (end - start).days > 730:
        raise DirectApiError("Недопустимый период статистики Директа.")

    campaign_request = {
        "method": "get",
        "params": {
            "SelectionCriteria": {},
            "FieldNames": ["Id", "Name", "Currency"],
            "Page": {"Limit": 10000},
        },
    }
    raw_campaigns = _request(CAMPAIGNS_URL, token, campaign_request)
    try:
        campaigns_payload = json.loads(raw_campaigns.decode("utf-8"))
        campaigns_result = campaigns_payload["result"]
        if campaigns_result.get("LimitedBy") is not None:
            raise DirectApiError("Директ вернул неполный список кампаний.")
        campaign_items = campaigns_result["Campaigns"]
        if not isinstance(campaign_items, list):
            raise ValueError("invalid Campaigns")
        campaigns = {
            str(item["Id"]): {
                "name": str(item.get("Name") or "").strip(),
                "currency": str(item.get("Currency") or "").strip(),
            }
            for item in campaign_items
        }
        if len(campaigns) != len(campaign_items):
            raise ValueError("duplicate campaign")
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise DirectApiError("Не удалось подтвердить кампании и валюту Директа.") from exc

    report_request = {
        "params": {
            "SelectionCriteria": {
                "DateFrom": start.isoformat(),
                "DateTo": end.isoformat(),
            },
            "FieldNames": [
                "Date", "CampaignId", "CampaignName", "Clicks", "Cost", "Conversions",
            ],
            "ReportName": "AZ_BAZE_Direct_Daily_ReadOnly",
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES",
        },
    }
    raw_report = _request(REPORTS_URL, token, report_request, reports=True)
    try:
        reader = csv.DictReader(io.StringIO(raw_report.decode("utf-8-sig")), delimiter="\t")
        required = {"Date", "CampaignId", "CampaignName", "Clicks", "Cost", "Conversions"}
        if not required.issubset(reader.fieldnames or []):
            raise DirectApiError("Директ вернул неполные колонки отчёта.")
        result = []
        seen = set()
        for row in reader:
            day = date.fromisoformat(str(row.get("Date") or ""))
            cid = str(row.get("CampaignId") or "").strip()
            if not start <= day <= end or not cid or cid not in campaigns:
                raise DirectApiError("В статистике Директа обнаружена чужая кампания или дата.")
            if campaigns[cid]["currency"] != "RUB":
                raise DirectApiError("Валюта кампании не RUB; автоматическое суммирование запрещено.")
            key = (day.isoformat(), cid)
            if key in seen:
                raise DirectApiError("Директ вернул повторную строку дня и кампании.")
            seen.add(key)
            cost = _amount(row.get("Cost"))
            clicks = _amount(row.get("Clicks"))
            conversions = _amount(row.get("Conversions"), optional=True)
            if clicks != clicks.to_integral_value():
                raise DirectApiError("Количество кликов нецелое.")
            result.append({
                "date": day.isoformat(),
                "source_key": "yandex_ads",
                "source_name": "Яндекс-реклама",
                "channel_name": "Платная реклама",
                "campaign_id": cid,
                "campaign_name": campaigns[cid]["name"] or str(row.get("CampaignName") or "").strip(),
                "spend": float(cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "clicks": int(clicks),
                "conversions": None if conversions is None else float(conversions),
            })
            if len(result) > 100000:
                raise DirectApiError("Отчёт Директа превышает допустимое число строк.")
    except (UnicodeDecodeError, ValueError, TypeError, KeyError, OverflowError) as exc:
        if isinstance(exc, DirectApiError):
            raise
        raise DirectApiError("Директ вернул некорректный TSV-отчёт.") from exc

    return sorted(result, key=lambda row: (row["date"], row["campaign_id"])), {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "campaigns": len({row["campaign_id"] for row in result}),
        "rows": len(result),
        "currency": "RUB",
        "include_vat": True,
        "conversion_definition": "Yandex Direct aggregate conversions",
    }
