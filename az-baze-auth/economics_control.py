import hashlib
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from flask import Response, g, jsonify, request

from app import SITE_ROOT, admin_required, audit, csrf_token, db, iso_now, permission_required, require_csrf

DATA_PATH = Path("/var/lib/az-baze/economics-control.json")
PERIOD = [f"2026-{m:02d}" for m in range(1, 7)]
MONTHS_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь"]
OPU_COLS = [21, 23, 25, 27, 29, 31]
PAYROLL_COLS = [14, 15, 16, 17, 18, 19]

_XLSX_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class _Cell:
    __slots__ = ("value",)

    def __init__(self, value=None):
        self.value = value


class _Sheet:
    __slots__ = ("_rows", "max_row")

    def __init__(self, rows, max_row):
        self._rows = rows
        self.max_row = max_row

    def cell(self, row, col):
        return _Cell(self._rows.get(row, {}).get(col))


class _Workbook:
    __slots__ = ("_sheets", "sheetnames")

    def __init__(self, sheets):
        self._sheets = sheets
        self.sheetnames = list(sheets)

    def __getitem__(self, name):
        return self._sheets[name]


def _text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _dict_series(values):
    return {PERIOD[i]: _num(values[i]) for i in range(6)}


def _norm_name(value):
    value = re.sub(r"\(\s*[cс]\s*\)", "", _text(value), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def _norm_key(value):
    value = _text(value).lower().replace("ё", "е")
    value = re.sub(r"[«»\"'’`]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _employee_id(name):
    return hashlib.sha256(_norm_name(name).lower().encode("utf-8")).hexdigest()[:16]


def _col_number(cell_ref):
    match = re.match(r"([A-Z]+)", cell_ref or "")
    if not match:
        return 0
    value = 0
    for char in match.group(1):
        value = value * 26 + ord(char) - 64
    return value


def _xlsx_cell_value(cell, shared_strings):
    cell_type = cell.attrib.get("t")
    value_node = cell.find(f"{{{_XLSX_MAIN_NS}}}v")
    if cell_type == "s" and value_node is not None:
        try:
            index = int(value_node.text or "0")
            return shared_strings[index] if 0 <= index < len(shared_strings) else ""
        except (TypeError, ValueError):
            return ""
    if cell_type == "inlineStr":
        inline = cell.find(f"{{{_XLSX_MAIN_NS}}}is")
        if inline is None:
            return ""
        return "".join((node.text or "") for node in inline.iter(f"{{{_XLSX_MAIN_NS}}}t"))
    if value_node is None:
        return None
    raw = value_node.text
    if cell_type in ("str", "e"):
        return raw
    try:
        return float(raw)
    except (TypeError, ValueError):
        return raw


def _load_xlsx_subset(stream):
    """
    Read only the two source sheets needed for the economic model.

    The source workbook has dozens of sheets. Loading the whole workbook inside
    a web request caused the Gunicorn worker to exceed its request time. This
    reader parses only OPU and payroll using the Python standard library.
    """
    if hasattr(stream, "seek"):
        stream.seek(0)
    try:
        archive = zipfile.ZipFile(stream)
    except zipfile.BadZipFile as exc:
        raise ValueError("Файл не является корректным .xlsx") from exc

    with archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{{{_XLSX_MAIN_NS}}}si"):
                shared_strings.append(
                    "".join((node.text or "") for node in item.iter(f"{{{_XLSX_MAIN_NS}}}t"))
                )

        workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
        rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {node.attrib["Id"]: node.attrib["Target"] for node in rels_root}

        wanted = {"ОПУ", "Свод по ЗП new"}
        targets = {}
        sheets_node = workbook_root.find(f"{{{_XLSX_MAIN_NS}}}sheets")
        if sheets_node is None:
            raise ValueError("В файле не найдена структура листов")

        for sheet in sheets_node:
            name = sheet.attrib.get("name", "")
            if name not in wanted:
                continue
            rel_id = sheet.attrib.get(f"{{{_XLSX_REL_NS}}}id")
            target = rel_map.get(rel_id)
            if not target:
                continue
            path = ("xl/" + target.lstrip("/")).replace("xl/xl/", "xl/")
            targets[name] = path

        missing = wanted - set(targets)
        if missing:
            raise ValueError("В файле нет листов: " + ", ".join(sorted(missing)))

        result = {}
        for name, path in targets.items():
            rows = {}
            max_row = 0
            raw_sheet = archive.read(path)
            for _event, row_node in ET.iterparse(io.BytesIO(raw_sheet), events=("end",)):
                if row_node.tag != f"{{{_XLSX_MAIN_NS}}}row":
                    continue
                try:
                    row_index = int(row_node.attrib.get("r", "0"))
                except ValueError:
                    row_index = 0
                max_row = max(max_row, row_index)
                row_values = {}
                for cell in row_node.findall(f"{{{_XLSX_MAIN_NS}}}c"):
                    col = _col_number(cell.attrib.get("r"))
                    if not col or col > 31:
                        continue
                    row_values[col] = _xlsx_cell_value(cell, shared_strings)
                if row_values:
                    rows[row_index] = row_values
                row_node.clear()
            result[name] = _Sheet(rows, max_row)

        return _Workbook(result)


def _find_row(ws, label, start=1):
    target = _norm_key(label)
    max_row = ws.max_row or 500
    for row in range(start, max_row + 1):
        if _norm_key(ws.cell(row, 4).value) == target:
            return row
    return None


def _row_series(ws, label, start=1):
    row = _find_row(ws, label, start)
    if not row:
        return {month: 0.0 for month in PERIOD}
    return _dict_series([ws.cell(row, col).value for col in OPU_COLS])


_OPU_SUBTOTALS = {
    _norm_key(value)
    for value in (
        "ПРЯМЫЕ РАСХОДЫ",
        "ЗП вспомогательного персонала осн.напралений",
        "Материалы",
        "Доп. производственные расходы",
        "КОСВЕННЫЕ РАСХОДЫ",
        "ФОТ АУП и вспомогательного персонала + налоги на ЗП",
        "Содержание помещений, ремонт оборудования",
        "Информационные услуги",
        "Банковские услуги",
        "Маркетинг",
        "Расходы на содержание оборудования и ПО",
        "Прочие расходы",
        "Расходы на персонал",
        "ОПЕРАЦИОННАЯ ПРИБЫЛЬ",
    )
}


def _expense_category(label):
    s = _norm_key(label)

    if any(
        value in s
        for value in (
            "ассистент",
            "сотрудник цсо",
            "административный персонал",
            "администратор",
            "ст.мед.сестр",
            "старшая медицинская сестра",
            "уборщик",
            "заведующ",
            "координатор маркетингового блока",
            "smm-специалист",
        )
    ):
        return "ФОТ"
    if "ндфл" in s or "соц.взнос" in s:
        return "Налоги и взносы на ФОТ"
    if "аренд" in s:
        return "Аренда"
    if "коммун" in s:
        return "Коммунальные и содержание помещений"
    if any(
        value in s
        for value in (
            "услуги лаборатории",
            "стоматологические материалы",
            "материалы для зтл",
            "расходные материалы",
            "материалы (доп",
            "материалы (отд",
            "компьютерная томография",
            "утилизац",
            "спецодеж",
            "санитарно",
            "стоматологические принадлежности",
        )
    ):
        return "Медицинские расходы"
    if any(value in s for value in ("программное обеспечение", "обслуживание пк", "услуги связи", "интернет")):
        return "Связь, интернет, программы и IT"
    if "ремонт и обслуживание оборудования" in s or "услуги по демонтажу оборудования" in s:
        return "Ремонт и обслуживание оборудования"
    if "покупка малоценного оборудования" in s or "мебел" in s:
        return "Оборудование, мебель и обновление"
    if any(value in s for value in ("общехозяй", "услуги доставки", "подотчет")):
        return "Хозяйственные и офисные расходы"
    if any(value in s for value in ("обучен", "командиров")):
        return "Обучение и командировки"
    if any(
        value in s
        for value in (
            "управленческий учет",
            "консалт",
            "бухгалтерские услуги",
            "услуги юриста",
            "охрана труда",
            "поиск персонала",
            "мед.осмотр",
            "регистрация товарного знака",
        )
    ):
        return "Профессиональные и административные услуги"
    if any(value in s for value in ("комиссия банка", "комиссии банка", "услуги банка", "эквайр", "сбп")):
        return "Банковские услуги"
    if any(
        value in s
        for value in (
            "прочие расходы на маркетинг",
            "услуги маркетолога",
            "полиграф",
            "сео продвижение",
            "контекстная реклама",
            "реклама",
            "рекламный кабинет",
            "фотограф",
            "дизайнер",
        )
    ):
        return "Маркетинг и реклама"
    return "Прочие операционные / разовые расходы"


def _parse_opu(ws):
    revenue_net = _row_series(ws, "ВЫРУЧКА со скидкой, руб.", 38)
    operating_profit = _row_series(ws, "ОПЕРАЦИОННАЯ ПРИБЫЛЬ", 150)
    net_profit = _row_series(ws, "ЧИСТАЯ ПРИБЫЛЬ", 200)
    materials = _row_series(ws, "Материалы", 130)
    assistant_salary = _row_series(ws, "Ассистенты стоматологов", 130)
    hygiene_fot = _row_series(ws, "Гигиенист", 79)
    direct_expenses = _row_series(ws, "ПРЯМЫЕ РАСХОДЫ", 130)
    indirect_expenses = _row_series(ws, "КОСВЕННЫЕ РАСХОДЫ", 150)

    direct_start = _find_row(ws, "ПРЯМЫЕ РАСХОДЫ", 130)
    direct_end = _find_row(ws, "МАРЖИНАЛЬНАЯ ПРИБЫЛЬ 2", (direct_start or 130) + 1)
    indirect_start = _find_row(ws, "КОСВЕННЫЕ РАСХОДЫ", 150)
    indirect_end = _find_row(ws, "ОПЕРАЦИОННАЯ ПРИБЫЛЬ", (indirect_start or 150) + 1)
    if not all((direct_start, direct_end, indirect_start, indirect_end)):
        raise ValueError("Не удалось определить границы прямых/косвенных расходов в ОПУ")

    expense_lines = []
    expense_entries = []
    for section, start, end in (
        ("Прямые расходы", direct_start, direct_end),
        ("Косвенные расходы", indirect_start, indirect_end),
    ):
        for row in range(start + 1, end):
            source_name = _text(ws.cell(row, 4).value)
            if not source_name:
                continue
            normalized = _norm_key(source_name)
            if normalized in _OPU_SUBTOTALS:
                continue
            months = _dict_series([ws.cell(row, col).value for col in OPU_COLS])
            total = sum(months.values())
            if abs(total) < 0.001:
                continue
            category = _expense_category(source_name)
            line = {
                "source_name": source_name,
                "normalized_name": normalized,
                "category": category,
                "section": section,
                "source": "ОПУ",
                "months": months,
                "total": total,
            }
            expense_lines.append(line)
            for month, amount in months.items():
                if abs(amount) < 0.001:
                    continue
                expense_entries.append(
                    {
                        "source_name": source_name,
                        "normalized_name": normalized,
                        "category": category,
                        "section": section,
                        "source": "ОПУ",
                        "month": month,
                        "amount": amount,
                    }
                )

    return {
        "revenue_net": revenue_net,
        "operating_profit": operating_profit,
        "net_profit": net_profit,
        "materials": materials,
        "assistant_salary": assistant_salary,
        "hygiene_fot": hygiene_fot,
        "direct_expenses": direct_expenses,
        "indirect_expenses": indirect_expenses,
        "expense_lines": expense_lines,
        "expense_entries": expense_entries,
    }


_MAIN_GROUP_HEADERS = {
    _norm_key("Врачебный персонал"): "Врачи",
    _norm_key("Средний и младший персонал"): "Вспомогательный персонал",
    _norm_key("Административный персонал"): "АУП",
    _norm_key("Администраторы"): "Администраторы",
    _norm_key("Зуботехническая лаборатория"): "Лаборатория",
    _norm_key("Маркетинговый блок"): "Штатный маркетинг",
}

_PAYROLL_SUBTOTAL_NAMES = {
    _norm_key(value)
    for value in (
        "Врачебный персонал",
        "Функциональная стоматология (ортодонтия)",
        "Отделение терапии",
        "Отделение структуры",
        "Отделение ортопедии",
        "Отделение хирургии",
        "Миофункциональные терапевты",
        "Сторонний специалист",
        "Средний и младший персонал",
        "Ассистент стоматолога",
        "Старшая медицинская сестра",
        "ЦСО",
        "Уборщик служебных помещений",
        "Рентгенолаборант",
        "Оператор ИГГТ",
        "Административный персонал",
        "Администраторы",
        "Зуботехническая лаборатория",
        "Маркетинговый блок",
    )
}


def _function_for(role, group):
    r = _norm_key(role)
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


def _parse_payroll(ws):
    assignments = []
    payments = []
    seen_assignments = set()
    current_group = None

    for row in range(6, (ws.max_row or 300) + 1):
        role = _text(ws.cell(row, 4).value)
        name = _norm_name(ws.cell(row, 5).value)
        if not role and not name:
            continue

        role_key = _norm_key(role)
        name_key = _norm_key(name)
        header_group = _MAIN_GROUP_HEADERS.get(role_key) or _MAIN_GROUP_HEADERS.get(name_key)
        if header_group and (role_key in _MAIN_GROUP_HEADERS or name_key in _MAIN_GROUP_HEADERS):
            current_group = header_group
            continue

        amounts = [_num(ws.cell(row, col).value) for col in PAYROLL_COLS]
        if not any(abs(value) > 0.0001 for value in amounts):
            continue
        if not current_group:
            continue

        if name_key in _PAYROLL_SUBTOTAL_NAMES or name_key == role_key:
            continue

        employee_id = _employee_id(name)
        department = _text(ws.cell(row, 3).value)
        function = _function_for(role, current_group)
        months = _dict_series(amounts)
        assignment_key = (
            employee_id,
            role_key,
            _norm_key(department or function),
            tuple(round(value, 2) for value in amounts),
        )
        if assignment_key in seen_assignments:
            continue
        seen_assignments.add(assignment_key)

        assignment_id = hashlib.sha256(repr(assignment_key).encode("utf-8")).hexdigest()[:20]
        assignment = {
            "assignment_id": assignment_id,
            "employee_id": employee_id,
            "name": name,
            "role": role,
            "group": current_group,
            "function": function,
            "department": department or function,
            "months": months,
            "total": sum(amounts),
        }
        assignments.append(assignment)

        for month, amount in months.items():
            if abs(amount) < 0.0001:
                continue
            payment_id = hashlib.sha256(
                f"{assignment_id}|{month}|{amount:.2f}".encode("utf-8")
            ).hexdigest()[:20]
            payments.append(
                {
                    "payment_id": payment_id,
                    "assignment_id": assignment_id,
                    "employee_id": employee_id,
                    "name": name,
                    "role": role,
                    "department": department or function,
                    "group": current_group,
                    "function": function,
                    "month": month,
                    "amount": amount,
                    "source": "Свод по ЗП new",
                }
            )

    physical = {}
    for assignment in assignments:
        employee = physical.setdefault(
            assignment["employee_id"],
            {
                "employee_id": assignment["employee_id"],
                "name": assignment["name"],
                "months": {month: 0.0 for month in PERIOD},
                "total": 0.0,
                "roles": [],
            },
        )
        employee["total"] += assignment["total"]
        for month in PERIOD:
            employee["months"][month] += assignment["months"][month]
        employee["roles"].append(
            {
                "role": assignment["role"],
                "group": assignment["group"],
                "function": assignment["function"],
                "department": assignment["department"],
            }
        )

    people = list(physical.values())
    total_by_month = {
        month: sum(employee["months"][month] for employee in people)
        for month in PERIOD
    }
    groups = {}
    for group in (
        "Врачи",
        "АУП",
        "Администраторы",
        "Вспомогательный персонал",
        "Лаборатория",
        "Штатный маркетинг",
    ):
        group_assignments = [item for item in assignments if item["group"] == group]
        groups[group] = {
            "total": sum(item["total"] for item in group_assignments),
            "months": {
                month: sum(item["months"][month] for item in group_assignments)
                for month in PERIOD
            },
        }

    return {
        "employees": assignments,
        "people": people,
        "assignments": assignments,
        "payments": payments,
        "groups": groups,
        "total_by_month": total_by_month,
        "total": sum(total_by_month.values()),
    }


def _ident_revenue():
    result = {}
    for month in PERIOD:
        row = db().execute(
            "SELECT payload FROM report_data WHERE date LIKE ? ORDER BY date DESC LIMIT 1",
            (month + "-%",),
        ).fetchone()
        if not row:
            result[month] = None
            continue
        try:
            record = json.loads(row["payload"])
            result[month] = _num(record.get("factMedicine")) + _num(
                record.get("factLab", record.get("labRevenue"))
            )
        except Exception:
            result[month] = None
    return result


def _close(left, right, tolerance=1.0):
    return abs(float(left or 0) - float(right or 0)) <= tolerance


def _validate_payload(payload):
    payroll = payload["payroll"]
    opu = payload["opu"]
    checks = {}

    checks["payroll_people_total"] = _close(
        sum(item["total"] for item in payroll["people"]),
        payroll["total"],
    )
    checks["payroll_assignment_total"] = _close(
        sum(item["total"] for item in payroll["assignments"]),
        payroll["total"],
    )
    checks["payroll_group_total"] = _close(
        sum(item["total"] for item in payroll["groups"].values()),
        payroll["total"],
    )
    checks["payroll_payment_total"] = _close(
        sum(item["amount"] for item in payroll["payments"]),
        payroll["total"],
    )
    checks["payment_ids_unique"] = len(
        {item["payment_id"] for item in payroll["payments"]}
    ) == len(payroll["payments"])

    month_reconcile = {}
    for month in PERIOD:
        normalized = sum(item["months"][month] for item in opu["expense_lines"])
        source = opu["direct_expenses"][month] + opu["indirect_expenses"][month]
        month_reconcile[month] = {
            "normalized": normalized,
            "source": source,
            "difference": normalized - source,
            "ok": _close(normalized, source),
        }
    checks["opu_expenses_reconcile"] = all(item["ok"] for item in month_reconcile.values())
    checks["materials_present"] = sum(opu["materials"].values()) > 0
    checks["assistants_present"] = sum(opu["assistant_salary"].values()) > 0
    checks["revenue_present"] = sum(opu["revenue_net"].values()) > 0

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise ValueError(
            "Контроль исходных данных не пройден: " + ", ".join(failed)
        )

    return {
        "status": "OK",
        "checks": checks,
        "expense_month_reconcile": month_reconcile,
        "physical_employees": len(payroll["people"]),
        "assignments": len(payroll["assignments"]),
        "payments": len(payroll["payments"]),
    }


def _build_payload(wb):
    if not {"ОПУ", "Свод по ЗП new"}.issubset(set(wb.sheetnames)):
        raise ValueError("В файле нет листов ОПУ и/или Свод по ЗП new")

    opu = _parse_opu(wb["ОПУ"])
    payroll = _parse_payroll(wb["Свод по ЗП new"])
    ident = _ident_revenue()

    revenue_control = []
    for month in PERIOD:
        opu_value = opu["revenue_net"][month]
        ident_value = ident.get(month)
        if ident_value is None:
            difference = difference_pct = None
            status = "Нет данных IDENT"
        else:
            difference = ident_value - opu_value
            difference_pct = abs(difference) / opu_value if opu_value else None
            if difference_pct is not None and difference_pct <= 0.01:
                status = "ОК"
            elif difference_pct is not None and difference_pct <= 0.03:
                status = "Требует сверки"
            else:
                status = "Критическое расхождение"
        revenue_control.append(
            {
                "month": month,
                "opu_net": opu_value,
                "ident_net": ident_value,
                "difference": difference,
                "difference_pct": difference_pct,
                "status": status,
            }
        )

    hygiene_missing = [month for month in PERIOD if opu["hygiene_fot"][month] <= 0]
    data_quality = {
        "Гигиена": {
            "status": "missing_fot" if hygiene_missing else "complete",
            "message": (
                "ФОТ гигиенистов отсутствует/не подтверждён: "
                + ", ".join(hygiene_missing)
                + ". Маржинальность за эти месяцы не считается подтверждённой."
                if hygiene_missing
                else "Данные ФОТ подтверждены."
            ),
        },
        "2025_2026_patients": {
            "status": "partial",
            "message": "Методика учёта пациентов 2025 и 2026 различается. Межгодовой ряд требует предупреждения или нормализации.",
        },
        "marketing": {
            "status": "partial",
            "message": "Сквозная атрибуция источник → пациент → оплата пока отсутствует. CAC/ROMI не считаются подтверждёнными.",
        },
        "laboratory": {
            "status": "partial",
            "message": "Лаборатория должна оцениваться отдельным P&L; персональная выручка техника не используется как критерий эффективности.",
        },
    }

    payload = {
        "version": 3,
        "available": True,
        "source": "ОПУ + Свод по ЗП new + IDENT",
        "generated_at": iso_now(),
        "period": PERIOD,
        "opu": opu,
        "payroll": payroll,
        "revenue_control": revenue_control,
        "data_quality": data_quality,
        "notes": {
            "distributed_profit": "Показатель не является фактической прибылью врача. До появления прямых затрат врача используем только расчётно распределённую прибыль.",
            "potential_revenue": "Потерянные часы × фактическая выручка за проведённый час — только оценочная потенциальная выручка, не фактическая потеря.",
        },
        "layers": {
            "raw": ["ОПУ", "Свод по ЗП new", "IDENT"],
            "normalized": [
                "сотрудники / employee_id",
                "роли / assignment_id",
                "статьи расходов по нормализованному названию",
                "направления",
                "источники выручки",
            ],
            "calculated": [
                "ФОТ/выручка",
                "операционная и чистая рентабельность",
                "контроль источников",
                "качество данных",
            ],
        },
    }
    payload["control"] = _validate_payload(payload)
    payload["control"]["materials_jan_jun"] = sum(opu["materials"].values())
    payload["control"]["assistants_jan_jun"] = sum(opu["assistant_salary"].values())
    return payload


def _load_data():
    if not DATA_PATH.exists():
        return {"version": 3, "available": False}
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    data["available"] = True
    return data


def register_economics_control(app):
    @app.get("/reports/dashboard.html")
    @permission_required("reports")
    def economics_dashboard_page():
        return Response(
            (SITE_ROOT / "reports" / "dashboard.html").read_text(encoding="utf-8"),
            mimetype="text/html",
        )

    @app.route("/reports/economics-import/", methods=["GET", "POST"])
    @admin_required
    def economics_import():
        message, error = "", ""
        if request.method == "POST":
            try:
                require_csrf()
                upload = request.files.get("file")
                if not upload or not upload.filename.lower().endswith(".xlsx"):
                    raise ValueError("Выберите исходный файл .xlsx")

                workbook = _load_xlsx_subset(upload.stream)
                payload = _build_payload(workbook)

                DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
                temp_path = DATA_PATH.with_suffix(".json.tmp")
                temp_path.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                temp_path.chmod(0o640)
                temp_path.replace(DATA_PATH)

                audit(
                    "economics_source_imported",
                    target_user_id=g.user["id"],
                    details=(
                        f"file={upload.filename}; "
                        f"payroll={payload['payroll']['total']:.2f}; "
                        f"employees={payload['control']['physical_employees']}; "
                        f"control={payload['control']['status']}"
                    ),
                )
                message = (
                    "Экономические данные пересчитаны из первичного файла. "
                    "Контроль сумм: OK."
                )
            except Exception as exc:
                error = str(exc)

        html = (SITE_ROOT / "reports" / "economics-import.html").read_text(
            encoding="utf-8"
        )
        html = (
            html.replace(
                "{{MESSAGE}}",
                f'<div class="ok">{message}</div>' if message else "",
            )
            .replace("{{ERROR}}", error)
            .replace("{{CSRF}}", csrf_token())
        )
        return Response(html, mimetype="text/html")

    @app.get("/api/reports/economics-control")
    @permission_required("reports")
    def economics_control_data():
        try:
            return jsonify(data=_load_data(), error="", csrf=csrf_token())
        except Exception as exc:
            return jsonify(
                data={"version": 3, "available": False},
                error=str(exc),
                csrf=csrf_token(),
            )
