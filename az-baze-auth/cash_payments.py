import hashlib
import json
import math
import re
import sqlite3
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta

from flask import abort, g

import daily_upload_core as core

OOO_KKM = "0531380019042729"
IP_KKM = "0463880019042725"
KKM_ENTITY = {OOO_KKM: "ooo", IP_KKM: "ip"}

CASH_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS cash_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    source_rows INTEGER NOT NULL,
    accepted_rows INTEGER NOT NULL,
    cash_ooo REAL NOT NULL,
    cash_ip REAL NOT NULL,
    cash_fact REAL NOT NULL,
    billed_total REAL NOT NULL,
    uploaded_by INTEGER,
    uploaded_at TEXT NOT NULL,
    FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_cash_imports_uploaded_at ON cash_imports(uploaded_at DESC);
"""

REQUIRED_HEADERS = [
    "Дата и время",
    "Пациент/Компания",
    "Операция",
    "Сумма к оплате (₽)",
    "Движение ДС (₽)",
    "Касса",
    "Дата и время чека",
    "ККМ",
]

CASH_FIELDS = {
    "cashFact",
    "cashOOO",
    "cashIP",
    "cashAllocated",
    "cashUnallocated",
    "cashDentists",
    "cashDentistsOOO",
    "cashDentistsIP",
    "cashClinicDocs",
    "cashClinicDocsOOO",
    "cashClinicDocsIP",
    "cashLabRevenue",
    "cashLabOOO",
    "cashLabIP",
    "cashDataComplete",
    "billedInvoices",
}


def init_schema():
    core.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(core.DB_PATH)
    try:
        conn.executescript(CASH_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _money(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = float(value)
        return value if math.isfinite(value) else None
    text = str(value).replace("₽", "").replace("\xa0", " ").strip()
    if not text:
        return None
    text = text.replace(" ", "").replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _date_only(value):
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("\n", " ")
    month_map = {
        "янв": "01", "фев": "02", "мар": "03", "апр": "04",
        "май": "05", "июн": "06", "июл": "07", "авг": "08",
        "сен": "09", "окт": "10", "ноя": "11", "дек": "12",
    }
    match = re.match(r"^(\d{1,2})\s+([А-Яа-яЁё]{3})\s+(20\d{2})(?:\s+\d{1,2}:\d{2})?$", text)
    if match:
        month = month_map.get(match.group(2).lower())
        if month:
            return f"{match.group(3)}-{month}-{int(match.group(1)):02d}"
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _staff_match(operation):
    text = str(operation or "")
    if not text.startswith("№"):
        return None
    matches = []
    for short, full in core.ident_import.DENTISTS.items():
        if short in text:
            matches.append(("dent", short, full))
    for short, full in core.ident_import.STRUCTURE_DOCTORS.items():
        if short in text:
            matches.append(("structure", short, full))
    for short, full in core.ident_import.LAB_DOCTORS.items():
        if short in text:
            matches.append(("lab", short, full))
    unique = []
    seen = set()
    for item in matches:
        key = (item[0], item[2])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[0] if len(unique) == 1 else None


def parse_upload(file_storage):
    raw = file_storage.read()
    if not raw:
        raise ValueError("Выбран пустой файл «Счета и оплаты».")
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("Размер файла «Счета и оплаты» превышает 25 МБ.")

    candidates = core._table_candidates(raw, file_storage.filename or "")
    last_error = None
    for sheet_name, text in candidates:
        try:
            lines = text.splitlines()
            if not lines:
                raise ValueError("Файл «Счета и оплаты» пуст.")
            headers = lines[0].split("\t")
            headers += [""] * max(0, len(REQUIRED_HEADERS) - len(headers))
            if headers[: len(REQUIRED_HEADERS)] != REQUIRED_HEADERS:
                raise ValueError("Файл «Счета и оплаты» имеет неожиданную структуру колонок.")

            rows = []
            billed_rows = []
            unknown_kkm = set()
            source_rows = 0
            for raw_line in lines[1:]:
                cols = raw_line.split("\t")
                cols += [""] * max(0, 8 - len(cols))
                if str(cols[0]).strip() == "Итого":
                    continue
                operation = str(cols[2] or "").strip()
                billed = _money(cols[3])
                movement = _money(cols[4])
                kkm = str(cols[7] or "").strip()
                if billed is None and movement is None:
                    continue
                source_rows += 1

                row_date = _date_only(cols[0])
                cash_only_operation = (
                    operation in {"Внесение ДС", "Изъятие ДС"}
                    or operation.startswith("Перевод ДС внутри семьи")
                )
                if billed is not None and billed > 0 and not cash_only_operation:
                    if not row_date:
                        raise ValueError("В строке выставленного счёта не удалось определить дату.")
                    billed_rows.append({"date": row_date, "amount": round(float(billed), 2)})

                if movement is None:
                    continue

                # Internal family transfers are never new clinic income.
                if operation.startswith("Перевод ДС внутри семьи"):
                    continue

                # Current business rule: only positive receipts are fact.
                if movement <= 0:
                    continue

                if kkm and kkm not in KKM_ENTITY:
                    unknown_kkm.add(kkm)
                    continue
                if kkm not in KKM_ENTITY:
                    continue

                data_date = _date_only(cols[6]) or _date_only(cols[0])
                if not data_date:
                    raise ValueError("В строке положительного прихода не удалось определить дату чека.")

                entity = KKM_ENTITY[kkm]
                staff = _staff_match(operation)
                rows.append(
                    {
                        "date": data_date,
                        "amount": round(float(movement), 2),
                        "entity": entity,
                        "department": staff[0] if staff else None,
                        "staff": staff[1] if staff else None,
                        "staff_full": staff[2] if staff else None,
                    }
                )

            if unknown_kkm:
                raise ValueError(
                    "В файле найдены положительные приходы по неизвестной ККМ: "
                    + ", ".join(sorted(unknown_kkm))
                    + ". Импорт остановлен, чтобы не потерять деньги."
                )
            if not rows:
                raise ValueError("В файле «Счета и оплаты» нет положительных приходов по утверждённым ККМ.")

            return raw, rows, billed_rows, sheet_name, source_rows
        except Exception as exc:
            last_error = exc

    if isinstance(last_error, ValueError):
        raise last_error
    raise ValueError("Не удалось распознать файл «Счета и оплаты».") from last_error


def _empty_snapshot():
    return {
        "cashFact": 0.0,
        "cashOOO": 0.0,
        "cashIP": 0.0,
        "cashAllocated": 0.0,
        "cashUnallocated": 0.0,
        "cashDentists": {},
        "cashDentistsOOO": {},
        "cashDentistsIP": {},
        "cashClinicDocs": {},
        "cashClinicDocsOOO": {},
        "cashClinicDocsIP": {},
        "cashLabRevenue": 0.0,
        "cashLabOOO": 0.0,
        "cashLabIP": 0.0,
        "cashDataComplete": True,
        "billedInvoices": 0.0,
    }


def _add_map(container, key, amount):
    container[key] = round(float(container.get(key, 0.0)) + float(amount), 2)


def _apply_row(snapshot, row):
    amount = float(row["amount"])
    entity = row["entity"]
    snapshot["cashFact"] += amount
    snapshot["cashOOO" if entity == "ooo" else "cashIP"] += amount

    department = row.get("department")
    full = row.get("staff_full")
    if not department or not full:
        snapshot["cashUnallocated"] += amount
        return

    snapshot["cashAllocated"] += amount
    if department == "dent":
        _add_map(snapshot["cashDentists"], full, amount)
        _add_map(snapshot["cashDentistsOOO" if entity == "ooo" else "cashDentistsIP"], full, amount)
    elif department == "structure":
        _add_map(snapshot["cashClinicDocs"], full, amount)
        _add_map(snapshot["cashClinicDocsOOO" if entity == "ooo" else "cashClinicDocsIP"], full, amount)
    elif department == "lab":
        snapshot["cashLabRevenue"] += amount
        snapshot["cashLabOOO" if entity == "ooo" else "cashLabIP"] += amount
    else:
        snapshot["cashAllocated"] -= amount
        snapshot["cashUnallocated"] += amount


def _rounded_snapshot(value):
    result = deepcopy(value)
    for key in (
        "cashFact", "cashOOO", "cashIP", "cashAllocated", "cashUnallocated",
        "cashLabRevenue", "cashLabOOO", "cashLabIP", "billedInvoices",
    ):
        result[key] = round(float(result.get(key, 0.0)), 2)
    for key in (
        "cashDentists", "cashDentistsOOO", "cashDentistsIP",
        "cashClinicDocs", "cashClinicDocsOOO", "cashClinicDocsIP",
    ):
        result[key] = {name: round(float(amount), 2) for name, amount in sorted(result.get(key, {}).items())}
    return result


def build_daily_snapshots(rows, billed_rows=None):
    billed_rows = list(billed_rows or [])
    by_date = defaultdict(list)
    billed_by_date = defaultdict(float)
    for row in rows:
        by_date[str(row["date"])].append(row)
    for row in billed_rows:
        billed_by_date[str(row["date"])] += float(row.get("amount") or 0)

    dates = sorted(set(by_date) | set(billed_by_date))
    start = datetime.strptime(dates[0], "%Y-%m-%d").date()
    end = datetime.strptime(dates[-1], "%Y-%m-%d").date()
    snapshots = {}
    current_month = None
    cumulative = None
    cursor = start
    while cursor <= end:
        data_date = cursor.isoformat()
        month = data_date[:7]
        if month != current_month:
            current_month = month
            cumulative = _empty_snapshot()
        for row in by_date.get(data_date, []):
            _apply_row(cumulative, row)
        cumulative["billedInvoices"] += billed_by_date.get(data_date, 0.0)
        snapshots[data_date] = _rounded_snapshot(cumulative)
        cursor += timedelta(days=1)
    return snapshots, dates[0], dates[-1]


def _load_record(row):
    if not row:
        return {}
    try:
        value = json.loads(row["payload"])
    except (TypeError, ValueError, KeyError):
        return {}
    return value if isinstance(value, dict) else {}


def _cash_equal(existing, incoming):
    for key in CASH_FIELDS:
        left = existing.get(key)
        right = incoming.get(key)
        if isinstance(left, dict) or isinstance(right, dict):
            if (left or {}) != (right or {}):
                return False
        elif isinstance(left, bool) or isinstance(right, bool):
            if bool(left) != bool(right):
                return False
        else:
            try:
                if abs(float(left or 0) - float(right or 0)) > 0.01:
                    return False
            except (TypeError, ValueError):
                if left != right:
                    return False
    return True


def import_cash_file(file_storage, can_replace=False):
    raw, rows, billed_rows, sheet_name, source_rows = parse_upload(file_storage)
    snapshots, period_start, period_end = build_daily_snapshots(rows, billed_rows)
    filename = file_storage.filename or "Счета и оплаты"
    digest = _sha(raw)
    conn = core.db()

    duplicate = conn.execute("SELECT id FROM cash_imports WHERE sha256=?", (digest,)).fetchone()
    is_duplicate = bool(duplicate)

    existing_rows = conn.execute(
        "SELECT date,payload FROM report_data WHERE date BETWEEN ? AND ? ORDER BY date",
        (period_start, period_end),
    ).fetchall()
    existing_map = {str(row["date"]): _load_record(row) for row in existing_rows}

    cash_dates = {str(row["date"]) for row in rows}
    billed_dates = {str(row["date"]) for row in billed_rows}
    target_dates = sorted(set(existing_map) | cash_dates | billed_dates)
    prepared = []
    last_record_by_month = {}

    for data_date in target_dates:
        month = data_date[:7]
        exact = existing_map.get(data_date)
        if exact:
            base = deepcopy(exact)
            last_record_by_month[month] = deepcopy(exact)
        else:
            base = deepcopy(last_record_by_month.get(month, {}))

        snapshot = snapshots.get(data_date)
        if snapshot is None:
            continue

        incoming_cash = dict(snapshot)
        incoming_cash["cashCoverageStart"] = period_start
        incoming_cash["cashCoverageEnd"] = period_end
        incoming_cash["cashSourceFilename"] = filename
        incoming_cash["cashSourceSheet"] = sheet_name

        if exact and exact.get("cashDataComplete") is True and not _cash_equal(exact, incoming_cash):
            if not can_replace:
                raise ValueError(
                    "Файл меняет уже загруженные денежные данные. "
                    "Для замены требуется право «Заменять ранее загруженные данные за дату»."
                )

        base["date"] = data_date
        base.update(incoming_cash)
        prepared.append((data_date, base))
        last_record_by_month[month] = deepcopy(base)

    if not prepared:
        raise ValueError("Не найдено дат для обновления Управленческого отчёта.")

    ooo = round(sum(row["amount"] for row in rows if row["entity"] == "ooo"), 2)
    ip = round(sum(row["amount"] for row in rows if row["entity"] == "ip"), 2)
    fact = round(ooo + ip, 2)
    billed_total = round(sum(row["amount"] for row in billed_rows), 2)
    now = core.iso_now()

    conn.execute("BEGIN")
    try:
        for data_date, record in prepared:
            conn.execute(
                """
                INSERT INTO report_data(date,payload,updated_by,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(date) DO UPDATE SET
                    payload=excluded.payload,
                    updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at
                """,
                (
                    data_date,
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                    g.user["id"],
                    now,
                ),
            )
        if not is_duplicate:
            conn.execute(
                """
                INSERT INTO cash_imports(
                    filename,sha256,period_start,period_end,source_rows,accepted_rows,
                    cash_ooo,cash_ip,cash_fact,billed_total,uploaded_by,uploaded_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    filename,
                    digest,
                    period_start,
                    period_end,
                    int(source_rows),
                    len(rows),
                    ooo,
                    ip,
                    fact,
                    billed_total,
                    g.user["id"],
                    now,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    core.audit(
        "cash_payments_imported",
        target_user_id=g.user["id"],
        details=(
            f"period={period_start}..{period_end}; file={filename}; "
            f"accepted={len(rows)}; billed={billed_total:.2f}; ooo={ooo:.2f}; ip={ip:.2f}; fact={fact:.2f}"
        ),
    )
    return {
        "status": "duplicate" if is_duplicate else "imported",
        "message": (
            f"Готово. «Счета и оплаты» {'повторно проверены и восстановлены' if is_duplicate else 'загружены'} "
            f"за {period_start}–{period_end}. Факт рассчитан только по положительным приходам двух утверждённых ККМ."
        ),
        "data_date": period_end,
        "cash_fact": fact,
        "cash_ooo": ooo,
        "cash_ip": ip,
        "billed_total": billed_total,
    }


def latest_import():
    return core.db().execute(
        """
        SELECT filename,period_start,period_end,cash_ooo,cash_ip,cash_fact,billed_total,uploaded_at
        FROM cash_imports ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
