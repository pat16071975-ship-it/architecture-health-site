import base64
import hashlib
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from openpyxl import load_workbook


DATA_PATH = Path("/var/lib/az-baze/payroll-registry.json")
SOURCE_NAME = "ЗП_ЭКСПЕРСТОМ"
TIMEZONE = "Asia/Novosibirsk"
START_MONTH = "2026-09"
GOOGLE_SHEET_ID = os.environ.get(
    "AZ_PAYROLL_GOOGLE_SHEET_ID",
    "1DaDZGCHMX99vd47qpjAv70Gn6gsk5PVmbmK0t3PrS1Q",
)
GOOGLE_CREDENTIALS_PATH = Path(
    os.environ.get(
        "AZ_PAYROLL_GOOGLE_CREDENTIALS",
        "/etc/az-baze/payroll-google-service-account.json",
    )
)
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_GOOGLE_EXPORT_BYTES = 10 * 1024 * 1024

MONTH_NAMES = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}
GROUP_HEADERS = {
    "врачебный блок": "Врачи",
    "средний и младший персонал": "Вспомогательный персонал",
    "администраторы": "Администраторы",
    "административный персонал (упр)": "АУП",
    "маркетинговый блок": "Штатный маркетинг",
    "зуботехническая лаборатория": "Лаборатория",
}


def _b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _service_account_access_token(credentials_path=GOOGLE_CREDENTIALS_PATH, opener=None, now=None):
    opener = opener or urllib.request.urlopen
    path = Path(credentials_path)
    if not path.exists():
        raise ValueError(
            "Google Drive read-only доступ для зарплатного реестра не настроен"
        )

    try:
        credentials = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Не удалось прочитать Google service-account credentials") from exc

    client_email = str(credentials.get("client_email") or "").strip()
    private_key_pem = str(credentials.get("private_key") or "").encode("utf-8")
    if not client_email or not private_key_pem:
        raise ValueError("Google service-account credentials неполные")

    issued_at = int(time.time() if now is None else now)
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": client_email,
        "scope": GOOGLE_DRIVE_SCOPE,
        "aud": GOOGLE_TOKEN_URL,
        "iat": issued_at,
        "exp": issued_at + 3600,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    ).encode("ascii")

    try:
        private_key = serialization.load_pem_private_key(private_key_pem, password=None)
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    except Exception as exc:
        raise ValueError("Google service-account private key некорректен") from exc

    assertion = signing_input.decode("ascii") + "." + _b64url(signature)
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode("ascii")
    request = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with opener(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise ValueError("Не удалось получить Google Drive access token") from exc

    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise ValueError("Google Drive не вернул access token")
    return token


def fetch_google_sheet_xlsx(
    sheet_id=GOOGLE_SHEET_ID,
    credentials_path=GOOGLE_CREDENTIALS_PATH,
    opener=None,
    token_provider=None,
):
    opener = opener or urllib.request.urlopen
    sheet_id = str(sheet_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", sheet_id):
        raise ValueError("Некорректный Google Sheet ID")

    provider = token_provider or _service_account_access_token
    token = provider(credentials_path, opener=opener)
    query = urllib.parse.urlencode({"mimeType": GOOGLE_XLSX_MIME})
    url = (
        "https://www.googleapis.com/drive/v3/files/"
        + urllib.parse.quote(sheet_id, safe="")
        + "/export?"
        + query
    )
    request = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + token},
        method="GET",
    )
    try:
        with opener(request, timeout=60) as response:
            data = response.read(MAX_GOOGLE_EXPORT_BYTES + 1)
    except Exception as exc:
        raise ValueError("Не удалось экспортировать ЗП_ЭКСПЕРСТОМ из Google Drive") from exc

    if not data:
        raise ValueError("Google Drive вернул пустой зарплатный файл")
    if len(data) > MAX_GOOGLE_EXPORT_BYTES:
        raise ValueError("Экспорт ЗП_ЭКСПЕРСТОМ превышает допустимый размер")
    if not data.startswith(b"PK"):
        raise ValueError("Google Drive вернул не XLSX-файл")
    return data


def google_source_status(credentials_path=GOOGLE_CREDENTIALS_PATH):
    path = Path(credentials_path)
    if not path.exists():
        return {
            "configured": False,
            "sheet_id": GOOGLE_SHEET_ID,
            "service_account": "",
        }
    try:
        credentials = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "configured": False,
            "sheet_id": GOOGLE_SHEET_ID,
            "service_account": "",
        }
    return {
        "configured": bool(credentials.get("client_email") and credentials.get("private_key")),
        "sheet_id": GOOGLE_SHEET_ID,
        "service_account": str(credentials.get("client_email") or ""),
    }


def _text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value):
    value = _text(value).lower().replace("ё", "е")
    value = re.sub(r"\(\s*[cс]\s*\)", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[«»\"'’\x60]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _norm_name(value):
    value = re.sub(r"\(\s*[cс]\s*\)", "", _text(value), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def _employee_id(name):
    # Must stay byte-for-byte compatible with economics_control._employee_id().
    key = _norm_name(name).lower()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _norm_role(value):
    return _norm(value).replace("–", "-").replace("—", "-")


def _canonical_department(value, group=""):
    department = _norm(value).replace("–", "-").replace("—", "-")
    if group == "Врачи":
        aliases = {
            "отделение терапии": "терапия",
            "терапевтическое отделение": "терапия",
            "отделение ортопедии": "ортопедия",
            "ортопедическое отделение": "ортопедия",
            "отделение хирургии": "хирургия",
            "хирургическое отделение": "хирургия",
            "функциональная стоматология (ортодонтия)": "ортодонтия",
            "функциональная стоматология ортодонтия": "ортодонтия",
            "отделение ортодонтии": "ортодонтия",
            "миофункциональные терапевты": "миофункциональная терапия",
        }
        department = aliases.get(department, department)
        if department.startswith("отделение "):
            department = department[len("отделение "):]
    return department


def _canonical_assignment_scope(item):
    group = str(item.get("group") or "")
    role = _norm_role(item.get("role"))
    department = _canonical_department(
        item.get("department") or item.get("function"),
        group,
    )

    # Group-level functions are more stable than historical department labels.
    if group == "Администраторы":
        return "администраторы / клиентский сервис"
    if group == "Лаборатория":
        return "зуботехническая лаборатория"
    if group == "Штатный маркетинг":
        return "маркетинг / smm"
    if group in {"АУП", "Вспомогательный персонал"}:
        return _norm(_function_for(item.get("role"), group))

    if group == "Врачи":
        # When the medical role itself defines a specialty, prefer that stable
        # identity over source-specific department wording.
        role_scopes = (
            (r"ортодонт", "ортодонтия"),
            (r"ортопед", "ортопедия"),
            (r"хирург", "хирургия"),
            (r"гигиен", "гигиена"),
            (r"терапевт|стоматолог[- ]терап|заведующ.*терапев", "терапия"),
            (
                r"остеопат|нутрициолог|подиатр|гастроэнтеролог|нейропсихолог|"
                r"массаж|миофункцион|логопед|медицинская сестра по массажу",
                "отделение структуры",
            ),
        )
        for pattern, scope in role_scopes:
            if re.search(pattern, role):
                return scope

    # Generic roles can legitimately exist in different departments; keep the
    # canonical department in the key so truly distinct assignments stay apart.
    return department or _norm(item.get("function")) or role


def _assignment_merge_key(item):
    group = str(item.get("group") or "")
    return (
        str(item.get("employee_id") or ""),
        group,
        _norm_role(item.get("role")),
        _canonical_assignment_scope(item),
    )


def _function_for(role, group):
    r = _norm(role)
    if group == "АУП":
        if any(value in r for value in ("собственник", "исполнительный директор", "управляющ")):
            return "Руководство"
        if "главн" in r and "врач" in r:
            return "Главный врач"
        if "hr" in r or "кадр" in r:
            return "HR / кадры"
        if "бухгалтер" in r or "платеж" in r:
            return "Бухгалтерия / финансы"
        if "маркет" in r or "smm" in r:
            return "Маркетинг / SMM"
        if "бизнес-ассистент" in r:
            return "Бизнес-ассистенты"
        return "Руководство / управление"
    if group == "Администраторы":
        return "Администраторы / клиентский сервис"
    if group == "Лаборатория":
        return "Зуботехническая лаборатория"
    if group == "Штатный маркетинг":
        return "Маркетинг / SMM"
    if group == "Вспомогательный персонал":
        if "ассист" in r or "стаж" in r:
            return "Ассистенты врачей"
        if "цсо" in r:
            return "ЦСО"
        if "убор" in r:
            return "Уборка"
        if "рентген" in r or "diers" in r or "иггт" in r:
            return "Диагностика"
        if "мед" in r or "сестр" in r:
            return "Медсёстры"
        return "Вспомогательный персонал"
    return _text(role) or group


def _month_from_title(title):
    normalized = _norm(title).replace("_", " ")
    match = re.search(
        r"\bрасчет\s+зп\b.*?\b("
        + "|".join(MONTH_NAMES)
        + r")\b.*?\b(\d{2}|\d{4})\b",
        normalized,
    )
    if not match:
        return None

    month_name, raw_year = match.groups()
    year = int(raw_year)
    if len(raw_year) == 2:
        year += 2000
    if year < 2000 or year > 2099:
        return None
    return f"{year:04d}-{MONTH_NAMES[month_name]:02d}"


def _month_sequence(start, end):
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    result = []
    year, month = sy, sm
    while (year, month) <= (ey, em):
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result


def _number(value):
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        value = float(value)
        return value if math.isfinite(value) else 0.0
    return 0.0


def _formula_error(value):
    return isinstance(value, str) and value.startswith("#")


def _row_accrued_amount(ws, row):
    # Экономический ФОТ: расчёт + KPI/премии/руководство/рефералы
    # − удержания + отпускные + больничные. Авансы и финальная выдача
    # не участвуют: это график выплаты уже начисленной зарплаты.
    values = [ws.cell(row, col).value for col in range(15, 26)]
    errors = [str(value) for value in values if _formula_error(value)]
    if errors:
        return None, errors
    additions = sum(_number(ws.cell(row, col).value) for col in range(15, 20))
    deductions = sum(_number(ws.cell(row, col).value) for col in range(20, 23))
    vacation = sum(_number(ws.cell(row, col).value) for col in (23, 24))
    sick = _number(ws.cell(row, 25).value)
    return additions - deductions + vacation + sick, []


def _sheet_records(ws):
    records, errors = [], []
    current_group = None
    current_department = ""
    current_name = ""

    for row in range(4, min(ws.max_row or 0, 400) + 1):
        raw_a = _text(ws.cell(row, 1).value)
        key_a = _norm(raw_a)
        if key_a in GROUP_HEADERS:
            current_group = GROUP_HEADERS[key_a]
            current_department = ""
            current_name = ""
            continue
        if current_group is None:
            continue
        if key_a.startswith("оборот за месяц") or key_a.startswith("итого выплачено"):
            break
        if raw_a:
            current_department = raw_a

        raw_name = _norm_name(ws.cell(row, 4).value)
        role = _text(ws.cell(row, 5).value)
        amount, row_errors = _row_accrued_amount(ws, row)
        if row_errors:
            errors.append({
                "row": row,
                "name": raw_name or current_name,
                "role": role,
                "errors": row_errors,
            })

        if raw_name:
            current_name = raw_name
        elif role and current_name and amount is not None and abs(amount) > 0.0001:
            raw_name = current_name

        if not raw_name or not role or amount is None:
            continue

        component_values = [ws.cell(row, col).value for col in range(15, 26)]
        if abs(amount) < 0.0001 and not any(value not in (None, "") for value in component_values):
            continue

        group = current_group
        employee_id = _employee_id(raw_name)
        department = current_department or _function_for(role, group)
        record = {
            "employee_id": employee_id,
            "name": raw_name,
            "role": role,
            "group": group,
            "function": _function_for(role, group),
            "department": department,
        }
        assignment_key = (
            employee_id,
            group,
            _norm_role(role),
            _canonical_assignment_scope(record),
        )
        assignment_id = hashlib.sha256(repr(assignment_key).encode("utf-8")).hexdigest()[:20]
        records.append({
            "assignment_id": assignment_id,
            **record,
            "amount": amount,
        })
    return records, errors


def _combine_assignments(month_records):
    combined = {}
    for month, records in month_records.items():
        for item in records:
            target = combined.setdefault(item["assignment_id"], {
                key: item[key]
                for key in ("assignment_id", "employee_id", "name", "role", "group", "function", "department")
            } | {"months": {}})
            target["months"][month] = target["months"].get(month, 0.0) + float(item["amount"])
    for item in combined.values():
        item["total"] = sum(float(value or 0) for value in item["months"].values())
    return list(combined.values())


def _people(assignments):
    physical = {}
    for assignment in assignments:
        employee = physical.setdefault(assignment["employee_id"], {
            "employee_id": assignment["employee_id"],
            "name": assignment["name"],
            "months": {},
            "total": 0.0,
            "roles": [],
        })
        for month, amount in (assignment.get("months") or {}).items():
            employee["months"][month] = employee["months"].get(month, 0.0) + float(amount or 0)
            employee["total"] += float(amount or 0)
        employee["roles"].append({
            "role": assignment["role"],
            "group": assignment["group"],
            "function": assignment["function"],
            "department": assignment["department"],
        })
    return list(physical.values())


def _group_totals(assignments, period):
    result = {}
    for group in ("Врачи", "АУП", "Администраторы", "Вспомогательный персонал", "Лаборатория", "Штатный маркетинг"):
        selected = [item for item in assignments if item.get("group") == group]
        months = {
            month: sum(float(item.get("months", {}).get(month) or 0) for item in selected)
            for month in period
        }
        result[group] = {"total": sum(months.values()), "months": months}
    return result


def _payments(assignments, source=SOURCE_NAME):
    result = []
    for assignment in assignments:
        for month, amount in sorted((assignment.get("months") or {}).items()):
            if abs(float(amount or 0)) < 0.0001:
                continue
            payment_id = hashlib.sha256(
                f"{assignment['assignment_id']}|{month}|{float(amount):.2f}".encode("utf-8")
            ).hexdigest()[:20]
            result.append({
                "payment_id": payment_id,
                "assignment_id": assignment["assignment_id"],
                "employee_id": assignment["employee_id"],
                "name": assignment["name"],
                "role": assignment["role"],
                "department": assignment["department"],
                "group": assignment["group"],
                "function": assignment["function"],
                "month": month,
                "amount": float(amount),
                "source": source,
            })
    return result


def parse_registry(stream, now=None, start_month=START_MONTH):
    if hasattr(stream, "seek"):
        stream.seek(0)
    try:
        wb = load_workbook(stream, data_only=True, read_only=False)
    except Exception as exc:
        raise ValueError("Не удалось прочитать зарплатный файл .xlsx") from exc

    current = now or datetime.now(ZoneInfo(TIMEZONE))
    current_month = current.astimezone(ZoneInfo(TIMEZONE)).strftime("%Y-%m")
    discovered = {}
    month_records, month_status, month_errors = {}, {}, {}
    ignored_future_period = []

    try:
        for title in wb.sheetnames:
            month = _month_from_title(title)
            if month is None or month < start_month:
                continue
            if month in discovered:
                raise ValueError(
                    f"Найдено несколько расчётных листов за {month}: "
                    f"{discovered[month]!r} и {title!r}"
                )
            discovered[month] = title

        relevant = sorted(month for month in discovered if month <= current_month)
        ignored_future_period = sorted(month for month in discovered if month > current_month)

        if not relevant:
            raise ValueError(
                f"В зарплатном файле нет расчётных листов начиная с {start_month}"
            )

        expected = _month_sequence(start_month, relevant[-1])
        missing = [month for month in expected if month not in discovered]
        if missing:
            raise ValueError(
                "В зарплатном файле пропущены расчётные месяцы: "
                + ", ".join(missing)
            )

        for month in relevant:
            records, errors = _sheet_records(wb[discovered[month]])
            status = "partial" if month == current_month or errors else "complete"
            month_status[month] = status
            month_errors[month] = errors
            if status == "complete":
                month_records[month] = records
    finally:
        wb.close()

    period = sorted(month_records)
    partial_period = sorted(
        month for month, status in month_status.items() if status != "complete"
    )
    assignments = _combine_assignments(month_records)
    total_by_month = {
        month: sum(float(item.get("months", {}).get(month) or 0) for item in assignments)
        for month in period
    }
    payroll = {
        "source": SOURCE_NAME,
        "period": period,
        "partial_period": partial_period,
        "start_month": start_month,
        "source_months": sorted(month_status),
        "ignored_future_period": ignored_future_period,
        "month_status": month_status,
        "month_errors": month_errors,
        "assignments": assignments,
        "employees": assignments,
        "people": _people(assignments),
        "payments": _payments(assignments),
        "groups": _group_totals(assignments, period),
        "total_by_month": total_by_month,
        "total": sum(total_by_month.values()),
    }
    return {
        "version": 1,
        "source": SOURCE_NAME,
        "generated_at": current.isoformat(),
        "payroll": payroll,
    }


def save_registry(payload, path=DATA_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temp.chmod(0o640)
    temp.replace(path)


def load_registry(path=DATA_PATH):
    if not path.exists():
        return None
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict) or parsed.get("version") != 1:
        raise ValueError("Некорректный зарплатный реестр")
    return parsed


def _merge_assignment_lists(base_assignments, overlay_assignments):
    result, order = {}, []

    for item in list(base_assignments or []) + list(overlay_assignments or []):
        item = deepcopy(item)
        # Normalize legacy/new employee IDs through the shared name contract.
        # This also repairs already-materialized 20-char overlay IDs during merge.
        item["employee_id"] = _employee_id(item.get("name") or "")
        k = _assignment_merge_key(item)
        if k not in result:
            result[k] = item
            result[k]["months"] = dict(item.get("months") or {})
            order.append(k)
        else:
            target = result[k]
            target["months"].update(item.get("months") or {})
            # Preserve the first (legacy) stable assignment/employee identity.
            # Newer display metadata may refine labels without splitting history.
            for field in ("name", "role", "group", "function", "department"):
                if item.get(field):
                    target[field] = item[field]
            if not target.get("assignment_id") and item.get("assignment_id"):
                target["assignment_id"] = item["assignment_id"]
            if not target.get("employee_id") and item.get("employee_id"):
                target["employee_id"] = item["employee_id"]

    merged = [result[k] for k in order]
    for item in merged:
        item["employee_id"] = _employee_id(item.get("name") or "")
        item["total"] = sum(float(value or 0) for value in (item.get("months") or {}).values())
    return merged


def merge_economics_payload(base_payload, registry_payload):
    if not registry_payload:
        return base_payload
    result = deepcopy(base_payload)
    overlay = registry_payload.get("payroll") or {}
    base = result.get("payroll") or {}
    base_period = list(result.get("period") or [])
    payroll_period = sorted(set(base_period) | set(base.get("period") or []) | set(overlay.get("period") or []))
    assignments = _merge_assignment_lists(
        base.get("assignments") or base.get("employees") or [],
        overlay.get("assignments") or [],
    )
    total_by_month = {
        month: sum(float(item.get("months", {}).get(month) or 0) for item in assignments)
        for month in payroll_period
    }
    merged_payroll = {
        **base,
        "source": SOURCE_NAME,
        "period": payroll_period,
        "partial_period": list(overlay.get("partial_period") or []),
        "month_status": {
            **{month: "complete" for month in base_period},
            **dict(overlay.get("month_status") or {}),
        },
        "month_errors": dict(overlay.get("month_errors") or {}),
        "assignments": assignments,
        "employees": assignments,
        "people": _people(assignments),
        "payments": _payments(assignments),
        "groups": _group_totals(assignments, payroll_period),
        "total_by_month": total_by_month,
        "total": sum(total_by_month.values()),
    }
    result["payroll"] = merged_payroll
    result["payroll_period"] = payroll_period
    result["payroll_partial_period"] = list(merged_payroll["partial_period"])
    result["payroll_source"] = SOURCE_NAME
    result["source"] = (str(result.get("source") or "") + " + " + SOURCE_NAME).strip(" +")
    result.setdefault("control", {})["payroll_registry"] = {
        "status": "OK",
        "complete_months": list(overlay.get("period") or []),
        "partial_months": list(overlay.get("partial_period") or []),
        "source": SOURCE_NAME,
    }
    return result
