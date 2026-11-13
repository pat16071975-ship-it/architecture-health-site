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
import re
import time
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


# Keep polling brief: the button uses a synchronous Gunicorn worker.
# A queued report with a long RetryIn is retried by a later button press
# or the separately approved (not yet enabled) hourly worker.
REPORT_MAX_ATTEMPTS = 4
REPORT_WAIT_BUDGET_SECONDS = 12
REPORT_HTTP_TIMEOUT_SECONDS = 20


def _safe_request_id(headers, body=b""):
    """Only a numeric/ASCII Yandex request ID may reach user-facing errors."""
    value = str(headers.get("RequestId") or headers.get("requestid") or "").strip()
    if not value and body:
        try:
            payload = json.loads(body[:8192].decode("utf-8"))
            value = str((payload.get("error") or {}).get("request_id") or "")
        except (ValueError, UnicodeDecodeError, AttributeError, TypeError):
            pass
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) else ""


def _safe_error_code(body):
    try:
        payload = json.loads(body[:8192].decode("utf-8"))
        code = (payload.get("error") or {}).get("error_code")
        if str(code).isdigit() and len(str(code)) <= 6:
            return str(code)
    except (ValueError, UnicodeDecodeError, AttributeError, TypeError):
        pass
    return ""


def _api_error_message(status, headers, body, *, reports):
    source = "Reports" if reports else "Campaigns"
    reference = _safe_request_id(headers, body)
    code = _safe_error_code(body)
    suffix = ("; код=" + code if code else "") + ("; RequestId=" + reference if reference else "")
    return "Ошибка API Директа / %s (HTTP %s%s)." % (source, status, suffix)


def _retry_after(headers):
    raw = str(headers.get("retryIn") or headers.get("RetryIn") or "2").strip()
    if not raw.isdigit():
        return 2
    return max(1, min(int(raw), 86400))


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
            "processingMode": "auto",
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
    attempts = REPORT_MAX_ATTEMPTS if reports else 2
    waited = 0
    server_retries = 0
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=REPORT_HTTP_TIMEOUT_SECONDS) as response:
                status = response.status
                response_headers = response.headers
                raw = response.read(REPORT_LIMIT + 1) if status == 200 else b""
        except urllib.error.HTTPError as exc:
            status = exc.code
            response_headers = exc.headers or {}
            raw = exc.read(8192) if status not in (201, 202) else b""
        except (urllib.error.URLError, TimeoutError, OSError):
            raise DirectApiError("Не удалось получить ответ API Яндекс Директа.") from None
        if status == 200:
            if len(raw) > REPORT_LIMIT:
                raise DirectApiError("Отчёт Директа превышает допустимый размер.")
            return raw
        if reports and status in (201, 202):
            wait = _retry_after(response_headers)
            request_id = _safe_request_id(response_headers)
            if attempt == attempts - 1 or waited + wait > REPORT_WAIT_BUDGET_SECONDS:
                reference = (" RequestId=" + request_id + "." if request_id else "")
                raise DirectApiError(
                    "Отчёт Директа формируется в очереди. Повторите обновление "
                    "не раньше чем через %s сек.%s" % (wait, reference)
                )
            time.sleep(wait)
            waited += wait
            continue
        if status in (500, 502) and server_retries == 0 and attempt < attempts - 1:
            # Yandex documents a single repeat for transient server errors;
            # repeating indefinitely risks abuse and delays Gunicorn workers.
            server_retries += 1
            time.sleep(1)
            waited += 1
            continue
        raise DirectApiError(
            _api_error_message(status, response_headers, raw, reports=reports)
        )
    raise DirectApiError("Не удалось завершить запрос Директа за допустимое время.")


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
