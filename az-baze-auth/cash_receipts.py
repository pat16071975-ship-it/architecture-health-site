import re
from collections import defaultdict
from datetime import datetime

OOO_KKM = "0531380019042729"
IP_KKM = "0463880019042725"
APPROVED_KKM = {OOO_KKM: "ooo", IP_KKM: "ip"}

RU_MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4,
    "май": 5, "июн": 6, "июл": 7, "авг": 8,
    "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}

INVOICE_RE = re.compile(r"^№(\d+)")
DEBT_RE = re.compile(r"^Задолженность по счету №(\d+)", re.IGNORECASE)


def _money(value):
    text = str(value or "").replace("₽", "").replace("\xa0", " ").strip()
    text = text.replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _date(value):
    text = str(value or "").strip()
    if not text or text.lower() == "итого":
        return None
    first = text.splitlines()[0].strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(first, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    match = re.match(r"^(\d{1,2})\s+([А-ЯЁа-яё]{3})\s+(20\d{2})", first)
    if not match:
        return None
    month = RU_MONTHS.get(match.group(2).lower())
    if not month:
        return None
    return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(1)):02d}"


def _staff_from_operation(operation, staff_names):
    text = str(operation or "")
    candidates = [name for name in staff_names if name and str(name) in text]
    if not candidates:
        return None
    return max(candidates, key=len)


def parse_payments_text(text, staff_names):
    lines = text.splitlines()
    if not lines:
        raise ValueError("Файл «Счета и оплаты» пуст.")

    header_index = None
    header = None
    for index, raw_line in enumerate(lines[:20]):
        cols = raw_line.split("\t")
        names = [str(value or "").strip() for value in cols]
        if "Операция" in names and "Сумма к оплате (₽)" in names and "Движение ДС (₽)" in names and "ККМ" in names:
            header_index = index
            header = names
            break
    if header_index is None:
        raise ValueError("Файл «Счета и оплаты» не содержит ожидаемый заголовок.")

    def col(name):
        try:
            return header.index(name)
        except ValueError as exc:
            raise ValueError(f"В файле «Счета и оплаты» отсутствует колонка «{name}».") from exc

    date_col = col("Дата и время")
    operation_col = col("Операция")
    due_col = col("Сумма к оплате (₽)")
    movement_col = col("Движение ДС (₽)")
    kkm_col = col("ККМ")

    parsed_rows = []
    invoice_staff = {}
    source_dates = set()

    for raw_line in lines[header_index + 1:]:
        cols = raw_line.split("\t")
        cols += [""] * max(0, len(header) - len(cols))
        data_date = _date(cols[date_col] if date_col < len(cols) else "")
        if not data_date:
            continue
        source_dates.add(data_date)
        operation = str(cols[operation_col] if operation_col < len(cols) else "").strip()
        due = _money(cols[due_col] if due_col < len(cols) else "")
        movement = _money(cols[movement_col] if movement_col < len(cols) else "")
        kkm = str(cols[kkm_col] if kkm_col < len(cols) else "").strip()
        staff = _staff_from_operation(operation, staff_names)
        invoice_match = INVOICE_RE.match(operation)
        if invoice_match and staff:
            invoice_staff[invoice_match.group(1)] = staff
        parsed_rows.append({
            "date": data_date,
            "operation": operation,
            "due": due,
            "movement": movement,
            "kkm": kkm,
            "staff": staff,
        })

    if not parsed_rows:
        raise ValueError("Файл «Счета и оплаты» не содержит распознаваемых операций.")

    daily = defaultdict(lambda: {
        "billed": 0.0,
        "cashOoo": 0.0,
        "cashIp": 0.0,
        "staff": defaultdict(lambda: {"ooo": 0.0, "ip": 0.0}),
    })

    for row in parsed_rows:
        operation = row["operation"]
        due = row["due"]
        movement = row["movement"]
        data_date = row["date"]

        invoice_match = INVOICE_RE.match(operation)
        debt_match = DEBT_RE.match(operation)
        if due is not None and due > 0 and (invoice_match or debt_match):
            daily[data_date]["billed"] += due

        entity = APPROVED_KKM.get(row["kkm"])
        if not entity or movement is None or movement <= 0:
            continue

        if entity == "ooo":
            daily[data_date]["cashOoo"] += movement
        else:
            daily[data_date]["cashIp"] += movement

        staff = row["staff"]
        if not staff and debt_match:
            staff = invoice_staff.get(debt_match.group(1))
        if staff:
            daily[data_date]["staff"][staff][entity] += movement

    result_daily = {}
    for data_date, value in daily.items():
        staff = {}
        for name, parts in value["staff"].items():
            ooo = round(float(parts["ooo"] or 0), 2)
            ip = round(float(parts["ip"] or 0), 2)
            staff[name] = {"ooo": ooo, "ip": ip, "total": round(ooo + ip, 2)}
        result_daily[data_date] = {
            "billed": round(float(value["billed"] or 0), 2),
            "cashOoo": round(float(value["cashOoo"] or 0), 2),
            "cashIp": round(float(value["cashIp"] or 0), 2),
            "cashFact": round(float(value["cashOoo"] or 0) + float(value["cashIp"] or 0), 2),
            "staff": staff,
        }

    ordered_dates = sorted(source_dates)
    return {
        "version": 1,
        "sourceStart": ordered_dates[0],
        "sourceEnd": ordered_dates[-1],
        "daily": result_daily,
    }


def cumulative_for_date(parsed, data_date):
    month = str(data_date)[:7]
    totals = {
        "billed": 0.0,
        "cashOoo": 0.0,
        "cashIp": 0.0,
        "staff": defaultdict(lambda: {"ooo": 0.0, "ip": 0.0}),
    }
    for day in sorted(parsed.get("daily", {})):
        if day[:7] != month or day > data_date:
            continue
        row = parsed["daily"][day]
        totals["billed"] += float(row.get("billed") or 0)
        totals["cashOoo"] += float(row.get("cashOoo") or 0)
        totals["cashIp"] += float(row.get("cashIp") or 0)
        for staff_name, parts in (row.get("staff") or {}).items():
            totals["staff"][staff_name]["ooo"] += float(parts.get("ooo") or 0)
            totals["staff"][staff_name]["ip"] += float(parts.get("ip") or 0)

    staff = {}
    for name, parts in totals["staff"].items():
        ooo = round(parts["ooo"], 2)
        ip = round(parts["ip"], 2)
        staff[name] = {"ooo": ooo, "ip": ip, "total": round(ooo + ip, 2)}

    cash_ooo = round(totals["cashOoo"], 2)
    cash_ip = round(totals["cashIp"], 2)
    return {
        "billed": round(totals["billed"], 2),
        "cashOoo": cash_ooo,
        "cashIp": cash_ip,
        "cashFact": round(cash_ooo + cash_ip, 2),
        "staff": staff,
    }
