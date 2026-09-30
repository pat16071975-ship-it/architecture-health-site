import html
import io
import json
import math
import re
import sqlite3
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import xlrd
from pypdf import PdfReader

from app import DB_PATH, iso_now


NORMALIZED_VERSION = 1
NORMALIZED_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS nn_normalized_batches (
    batch_id INTEGER PRIMARY KEY,
    clinic_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    period_start TEXT,
    period_end TEXT,
    payload_json TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES nn_upload_batches(id) ON DELETE CASCADE,
    FOREIGN KEY (clinic_id) REFERENCES clinics(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_nn_normalized_clinic_period
    ON nn_normalized_batches(clinic_id, period_start, period_end);
"""

SS_NS = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}
SS_INDEX = "{urn:schemas-microsoft-com:office:spreadsheet}Index"
SS_NAME = "{urn:schemas-microsoft-com:office:spreadsheet}Name"

REPEAT_RE = re.compile(
    r"\b(повтор|повторн|контроль|контрольн|после\s+операц|перевяз|коррекц|повторка)\w*",
    re.I,
)
TREATMENT_RE = re.compile(
    r"(эвлк|эвкл|лазерн\w*\s+лечен|эндоваскуляр|склер|минифлеб|\bмф\b)",
    re.I,
)
ANALYSIS_RE = re.compile(r"\b(анализ|лабораторн)\w*", re.I)
HOSIERY_RE = re.compile(r"(чулк|трикотаж|компрессионн)", re.I)
EVLK_RE = re.compile(r"(эвлк|эвкл|лазерн\w*\s+лечен|эндоваскуляр)", re.I)
SCLERO_RE = re.compile(r"(склер|склт)", re.I)
MINI_RE = re.compile(r"(минифлеб|\bмф\b)", re.I)
NOT_INDICATED_RE = re.compile(r"(не\s+показан|не\s+показано|показаний\s+нет)", re.I)
PERFORMED_RE = re.compile(r"(выполнен|проведен|проведён|протокол\s+манипуляц)", re.I)
PREPAY_RE = re.compile(r"(предоплат|аванс)", re.I)

SERVICE_ALIASES = {
    "patient": ("пациент", "фио пациента", "ф.и.о. пациента"),
    "chart": ("номер амбулаторной карты", "амбулаторная карта", "номер карты", "карта"),
    "phone": ("телефон", "номер телефона"),
    "date": ("дата оказания", "дата услуги", "дата приема", "дата приёма", "дата"),
    "doctor": ("врач", "исполнитель", "сотрудник", "специалист"),
    "service": ("наименование услуги", "услуга", "наименование"),
    "group": ("группа", "раздел", "категория"),
    "qty": ("количество", "кол-во", "кол."),
    "amount": ("сумма", "стоимость", "итого", "сумма оплаты", "оплачено"),
    "comment": ("примечание", "комментарий"),
}


def _init_schema(conn=None):
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(NORMALIZED_SCHEMA)
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def _clean_text(value):
    if value is None:
        return ""
    text = html.unescape(str(value)).replace("\x00", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _norm_name(value):
    text = _clean_text(value).lower().replace("ё", "е")
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\+?\d[\d\s()\-]{6,}", " ", text)
    text = re.sub(r"[^a-zа-яәіңғүұқөһё\s-]", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _norm_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def _norm_id(value):
    text = re.sub(r"\D", "", str(value or ""))
    return text


def _parse_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        try:
            return xlrd.xldate_as_datetime(float(value), 0).strftime("%Y-%m-%d")
        except Exception:
            return None
    text = _clean_text(value)
    for pattern in (
        r"(\d{2}\.\d{2}\.\d{4})",
        r"(\d{4}-\d{2}-\d{2})",
    ):
        match = re.search(pattern, text)
        if match:
            token = match.group(1)
            for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(token, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    pass
    return None


def _parse_clock_range(value):
    text = _clean_text(value)
    match = re.search(r"(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})", text)
    return (match.group(1), match.group(2)) if match else (None, None)


def _num(value):
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", " ").replace("₽", "").strip()
    text = text.replace(" ", "").replace(",", ".")
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        return float(text)
    except ValueError:
        return 0.0


def _spreadsheetml_rows(path):
    root = ET.parse(path).getroot()
    worksheet = root.find(".//ss:Worksheet", SS_NS)
    if worksheet is None:
        return []
    rows = []
    for row in worksheet.findall(".//ss:Table/ss:Row", SS_NS):
        values = []
        col = 1
        for cell in row.findall("ss:Cell", SS_NS):
            index = cell.attrib.get(SS_INDEX)
            if index:
                target = int(index)
                while col < target:
                    values.append("")
                    col += 1
            data = cell.find("ss:Data", SS_NS)
            values.append(data.text if data is not None and data.text is not None else "")
            col += 1
        rows.append(values)
    return rows


def _xls_rows(path):
    prefix = Path(path).read_bytes()[:64].lstrip()
    if prefix.startswith(b"<?xml") or prefix.startswith(b"<Workbook"):
        return _spreadsheetml_rows(path)
    book = xlrd.open_workbook(filename=str(path), on_demand=True)
    try:
        sheet = book.sheet_by_index(0)
        return [sheet.row_values(i) for i in range(sheet.nrows)]
    finally:
        book.release_resources()


def _dict_rows(matrix):
    if not matrix:
        return []
    headers = [_clean_text(v) for v in matrix[0]]
    result = []
    for raw in matrix[1:]:
        values = list(raw) + [""] * max(0, len(headers) - len(raw))
        result.append({headers[i]: values[i] for i in range(len(headers)) if headers[i]})
    return result


def _patient_master(path):
    rows = _dict_rows(_xls_rows(path))
    result = []
    for row in rows:
        result.append({
            "name": _clean_text(row.get("Пациент")),
            "dob": _parse_date(row.get("Дата рождения")),
            "iin": _norm_id(row.get("ИИН")),
            "chart": _norm_id(row.get("Номер амбулаторной карты")),
            "phone": _norm_phone(row.get("Телефоны")),
            "note": _clean_text(row.get("Примечание")),
            "source_amount": _num(row.get("Сумма")),
        })
    return [r for r in result if r["name"] or r["chart"] or r["phone"]]


def _registry_rows(path):
    rows = _dict_rows(_spreadsheetml_rows(path))
    result = []
    for row in rows:
        date = _parse_date(row.get("Время приема"))
        if not date:
            continue
        start, end = _parse_clock_range(row.get("Время приема"))
        result.append({
            "date": date,
            "start": start,
            "end": end,
            "doctor": _clean_text(row.get("Врач")),
            "patient": _clean_text(row.get("Пациент")),
            "iin": _norm_id(row.get("ИИН")),
            "chart": _norm_id(row.get("Номер амбулаторной карты")),
            "phone": _norm_phone(row.get("Номер телефона")),
            "repeat_field": _clean_text(row.get("Повторность")),
            "note": _clean_text(row.get("Примечание")),
            "visit_type": _clean_text(row.get("Тип приема")),
            "help_type": _clean_text(row.get("Вид помощи")),
            "services_text": _clean_text(row.get("Перечень услуг")),
            "service_prices_text": _clean_text(row.get("Стоимость услуг")),
            "amount": _num(row.get("Сумма")),
        })
    return result


def _medical_rows(path):
    rows = _dict_rows(_spreadsheetml_rows(path))
    result = []
    for row in rows:
        date = _parse_date(row.get("Время приема"))
        if not date:
            continue
        patient_raw = _clean_text(row.get("Пациент"))
        dob = _parse_date(patient_raw)
        patient = re.sub(r"\s*\(\d{2}\.\d{2}\.\d{4}\)\s*$", "", patient_raw).strip()
        result.append({
            "date": date,
            "doctor": _clean_text(row.get("Врач")),
            "patient": patient,
            "dob": dob,
            "chart": _norm_id(row.get("Номер амбулаторной карты")),
            "complaint": _clean_text(row.get("Жалоба")),
            "objective": _clean_text(row.get("Данные объективного осмотра")),
            "direction": _clean_text(row.get("Направление")),
            "assignment": _clean_text(row.get("Назначение")),
            "recommendation": _clean_text(row.get("Рекомендация")),
            "treatment": _clean_text(row.get("Лечение / Манипуляция")),
        })
    return result


def _normalize_header(value):
    text = _clean_text(value).lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9]+", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _header_key(header, aliases):
    if not header:
        return None
    for key, options in aliases.items():
        if header in options:
            return key
    rules = (
        ("patient", ("пациент",)),
        ("chart", ("амбулатор", "карт")),
        ("phone", ("телефон",)),
        ("date", ("дата",)),
        ("doctor", ("врач", "исполнитель", "сотрудник", "специалист")),
        ("service", ("услуг", "номенклатур")),
        ("group", ("групп", "раздел", "категори")),
        ("qty", ("количество", "кол во", "кол.")),
        ("amount", ("сумм", "стоим", "итого", "оплачен")),
        ("comment", ("примеч", "коммент")),
    )
    for key, tokens in rules:
        if key == "chart":
            if all(token in header for token in tokens):
                return key
        elif any(token in header for token in tokens):
            return key
    if header == "наименование":
        return "service"
    return None


def _header_map(matrix):
    aliases = {
        key: {_normalize_header(v) for v in values}
        for key, values in SERVICE_ALIASES.items()
    }
    best = None
    for row_index, row in enumerate(matrix[:30]):
        normalized = [_normalize_header(v) for v in row]
        mapping = {}
        for col, header in enumerate(normalized):
            key = _header_key(header, aliases)
            if key and key not in mapping:
                mapping[key] = col
        score = len(mapping)
        if best is None or score > best[0]:
            best = (score, row_index, mapping)
    if not best or best[0] < 3 or "service" not in best[2]:
        raise ValueError("Не удалось определить колонки подробного отчёта по услугам.")
    return best[1], best[2]


def _service_rows_from_matrix(matrix):
    # Current MedElement «Подробный отчет по услугам» is an aggregate table:
    # Наименование / Количество / Стоимость / Сумма / Скидка / Итого.
    # In that shape it is source-control only, not patient-level financial data.
    normalized_headers = [_normalize_header(v) for v in (matrix[0] if matrix else [])]
    required_aggregate = {
        "наименование",
        "количество",
        "стоимость",
        "сумма",
        "скидка",
        "итого",
    }
    if required_aggregate.issubset(set(normalized_headers)):
        positions = {header: normalized_headers.index(header) for header in required_aggregate}
        department_col = normalized_headers.index("отделение") if "отделение" in normalized_headers else None
        item_type_col = normalized_headers.index("тип номенклатуры") if "тип номенклатуры" in normalized_headers else None
        rows = []
        for raw in matrix[1:]:
            values = list(raw)
            def at(index):
                return values[index] if index is not None and index < len(values) else ""
            service = _clean_text(at(positions["наименование"]))
            if not service:
                continue
            rows.append({
                "patient": "",
                "chart": "",
                "phone": "",
                "date": None,
                "doctor": "",
                "group": _clean_text(at(item_type_col)),
                "department": _clean_text(at(department_col)),
                "service": service,
                "qty": _num(at(positions["количество"])),
                "cost": _num(at(positions["стоимость"])),
                "gross_amount": _num(at(positions["сумма"])),
                "discount": _num(at(positions["скидка"])),
                "amount": _num(at(positions["итого"])),
                "comment": "",
                "aggregate_control": True,
            })
        return rows

    header_row, mapping = _header_map(matrix)
    rows = []
    carry = {key: "" for key in ("patient", "chart", "phone", "date", "doctor", "group", "comment")}
    for raw in matrix[header_row + 1:]:
        values = list(raw)
        def get(key):
            col = mapping.get(key)
            return values[col] if col is not None and col < len(values) else ""

        patient = _clean_text(get("patient")) or carry["patient"]
        chart = _norm_id(get("chart")) or carry["chart"]
        phone = _norm_phone(get("phone")) or carry["phone"]
        date = _parse_date(get("date")) or carry["date"]
        doctor = _clean_text(get("doctor")) or carry["doctor"]
        group = _clean_text(get("group")) or carry["group"]
        comment = _clean_text(get("comment")) or carry["comment"]
        service = _clean_text(get("service"))
        qty = _num(get("qty")) if "qty" in mapping else 1.0
        amount = _num(get("amount")) if "amount" in mapping else 0.0

        for key, value in (
            ("patient", patient), ("chart", chart), ("phone", phone),
            ("date", date), ("doctor", doctor), ("group", group), ("comment", comment),
        ):
            if value:
                carry[key] = value

        if not service and amount == 0:
            continue
        rows.append({
            "patient": patient,
            "chart": chart,
            "phone": phone,
            "date": date,
            "doctor": doctor,
            "group": group,
            "service": service,
            "qty": qty,
            "amount": amount,
            "comment": comment,
        })
    return rows


def _service_rows(path):
    return _service_rows_from_matrix(_xls_rows(path))


def _deleted_rows(path, doctors):
    reader = PdfReader(str(path))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    total_match = re.search(r"всего\s+удалено\s+(\d+)\s*(?:$|\n)", text, re.I)
    total = int(total_match.group(1)) if total_match else None

    doctor_prefixes = sorted(
        {" ".join(_clean_text(d).split()[:2]) for d in doctors if d},
        key=len,
        reverse=True,
    )
    rows = []
    line_re = re.compile(
        r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2}).*?"
        r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})(.*)$"
    )
    for line in text.splitlines():
        match = line_re.search(line)
        if not match:
            continue
        tail = _clean_text(match.group(5))
        doctor = ""
        patient = tail
        for prefix in doctor_prefixes:
            pos = tail.lower().find(prefix.lower())
            if pos >= 0:
                doctor = prefix
                patient = tail[pos + len(prefix):].strip()
                break
        rows.append({
            "deleted_date": _parse_date(match.group(1)),
            "deleted_time": match.group(2),
            "appointment_date": _parse_date(match.group(3)),
            "appointment_time": match.group(4),
            "doctor": doctor,
            "patient": patient,
            "patient_name": _norm_name(patient),
            "phone": _norm_phone(patient),
        })
    return rows, total


class PatientResolver:
    def __init__(self, master_rows):
        self.patients = {}
        self.by_chart = {}
        self.by_iin = {}
        self.by_phone = {}
        self.by_name_dob = {}
        self.by_name = defaultdict(list)

        rows = [dict(row) for row in master_rows]
        parent = list(range(len(rows)))

        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        indexes = {"chart": {}, "iin": {}, "phone": {}, "name_dob": {}}
        for index, row in enumerate(rows):
            keys = []
            if row.get("chart"):
                keys.append(("chart", row["chart"]))
            if row.get("iin"):
                keys.append(("iin", row["iin"]))
            if row.get("phone"):
                keys.append(("phone", row["phone"]))
            name = _norm_name(row.get("name"))
            if name and row.get("dob"):
                keys.append(("name_dob", (name, row["dob"])))
            for bucket, value in keys:
                if value in indexes[bucket]:
                    union(index, indexes[bucket][value])
                else:
                    indexes[bucket][value] = index

        groups = defaultdict(list)
        for index in range(len(rows)):
            groups[find(index)].append(index)

        for group_index, members in enumerate(groups.values(), start=1):
            group_rows = [rows[i] for i in members]
            charts = sorted({r.get("chart") for r in group_rows if r.get("chart")})
            iins = sorted({r.get("iin") for r in group_rows if r.get("iin")})
            phones = sorted({r.get("phone") for r in group_rows if r.get("phone")})
            names = [r.get("name") for r in group_rows if r.get("name")]
            dobs = [r.get("dob") for r in group_rows if r.get("dob")]
            notes = [r.get("note") for r in group_rows if r.get("note")]
            key = (
                "chart:" + charts[0] if charts
                else "iin:" + iins[0] if iins
                else "phone:" + phones[0] if phones
                else f"master:{group_index}:{_norm_name(names[0] if names else '')}:{dobs[0] if dobs else ''}"
            )
            patient = {
                "key": key,
                "name": names[0] if names else "",
                "dob": dobs[0] if dobs else None,
                "iin": iins[0] if iins else "",
                "chart": charts[0] if charts else "",
                "charts": charts,
                "phone": phones[0] if phones else "",
                "phones": phones,
                "note": " | ".join(dict.fromkeys(notes)),
                "source_amount": round(sum(float(r.get("source_amount") or 0) for r in group_rows), 2),
            }
            self.patients[key] = patient

            for row in group_rows:
                if row.get("chart"):
                    self.by_chart[row["chart"]] = key
                if row.get("iin"):
                    self.by_iin[row["iin"]] = key
                if row.get("phone"):
                    self.by_phone[row["phone"]] = key
                name = _norm_name(row.get("name"))
                if name and row.get("dob"):
                    self.by_name_dob[(name, row["dob"])] = key
                if name and key not in self.by_name[name]:
                    self.by_name[name].append(key)

    def resolve(self, row):
        chart = _norm_id(row.get("chart"))
        iin = _norm_id(row.get("iin"))
        phone = _norm_phone(row.get("phone"))
        name = _norm_name(row.get("patient") or row.get("name"))
        dob = row.get("dob") or _parse_date(row.get("patient"))

        if chart and chart in self.by_chart:
            return self.by_chart[chart]
        if iin and iin in self.by_iin:
            return self.by_iin[iin]
        if phone and phone in self.by_phone:
            return self.by_phone[phone]
        if name and dob and (name, dob) in self.by_name_dob:
            return self.by_name_dob[(name, dob)]
        if name and len(self.by_name.get(name, [])) == 1:
            return self.by_name[name][0]

        base = chart or iin or phone or name
        key = "unmatched:" + (base or "unknown")
        if key not in self.patients:
            self.patients[key] = {
                "key": key,
                "name": _clean_text(row.get("patient") or row.get("name")),
                "dob": dob,
                "iin": iin,
                "chart": chart,
                "charts": [chart] if chart else [],
                "phone": phone,
                "phones": [phone] if phone else [],
                "note": "",
                "source_amount": 0.0,
            }
            if name:
                self.by_name[name].append(key)
        return key


def _is_deleted_visit(visit, deleted_rows):
    visit_doctor = _norm_name(" ".join(_clean_text(visit.get("doctor")).split()[:2]))
    visit_name = _norm_name(visit.get("patient"))
    visit_phone = _norm_phone(visit.get("phone"))
    for deleted in deleted_rows:
        if deleted.get("appointment_date") != visit.get("date"):
            continue
        if deleted.get("appointment_time") != visit.get("start"):
            continue
        deleted_doctor = _norm_name(deleted.get("doctor"))
        if deleted_doctor and deleted_doctor != visit_doctor:
            continue
        deleted_phone = _norm_phone(deleted.get("phone"))
        deleted_name = deleted.get("patient_name") or ""
        if deleted_phone and visit_phone and deleted_phone == visit_phone:
            return True
        if deleted_name and visit_name and deleted_name == visit_name:
            return True
    return False


def _is_nonclinical(text):
    text = _clean_text(text)
    return bool(TREATMENT_RE.search(text) or ANALYSIS_RE.search(text) or HOSIERY_RE.search(text))


def _service_class(row):
    text = _clean_text(" ".join((row.get("group", ""), row.get("service", ""), row.get("comment", ""))))
    service = _clean_text(row.get("service"))
    if EVLK_RE.search(text):
        return "evlk"
    if SCLERO_RE.search(text):
        return "sclero"
    if MINI_RE.search(text):
        return "mini"
    if HOSIERY_RE.search(text):
        return "hosiery"
    if ANALYSIS_RE.search(text):
        return "analysis"
    if re.search(r"(прием|приём|консультац|узи)", service, re.I):
        return "consultation"
    return "other"


def _indications(text):
    text = _clean_text(text)
    if not text or NOT_INDICATED_RE.search(text):
        return {"evlk": False, "sclero": False, "mini": False}
    return {
        "evlk": bool(EVLK_RE.search(text)),
        "sclero": bool(SCLERO_RE.search(text)),
        "mini": bool(MINI_RE.search(text)),
    }


def _merge_intervals(visits):
    by_doctor_date = defaultdict(list)
    invalid = defaultdict(int)
    for row in visits:
        doctor = row.get("doctor")
        start = row.get("start")
        end = row.get("end")
        if not doctor or not start or not end:
            invalid[doctor] += 1
            continue
        try:
            start_m = int(start[:2]) * 60 + int(start[3:])
            end_m = int(end[:2]) * 60 + int(end[3:])
        except Exception:
            invalid[doctor] += 1
            continue
        if end_m <= start_m:
            invalid[doctor] += 1
            continue
        by_doctor_date[(doctor, row["date"])].append((start_m, end_m))

    minutes = defaultdict(int)
    for (doctor, _date), intervals in by_doctor_date.items():
        intervals.sort()
        cur_s = cur_e = None
        for start, end in intervals:
            if cur_s is None:
                cur_s, cur_e = start, end
            elif start <= cur_e:
                cur_e = max(cur_e, end)
            else:
                minutes[doctor] += cur_e - cur_s
                cur_s, cur_e = start, end
        if cur_s is not None:
            minutes[doctor] += cur_e - cur_s
    return minutes, invalid


def normalize_rows(master, registry, medical, services, deleted_rows, deleted_total=None):
    resolver = PatientResolver(master)
    for rows in (registry, medical):
        for row in rows:
            row["patient_key"] = resolver.resolve(row)
    patient_services = []
    for row in services:
        if row.get("patient") or row.get("chart") or row.get("phone"):
            row["patient_key"] = resolver.resolve(row)
            patient_services.append(row)

    # «Реестр приемов» is the closed/completed-visit source used by the approved
    # analysis. The separate deleted-appointments PDF is control evidence only;
    # subtracting it here would remove valid closed visits that were later edited.
    kept_visits = list(registry)

    services_by_patient_date = defaultdict(list)
    for row in patient_services:
        if row.get("date"):
            row["class"] = _service_class(row)
            services_by_patient_date[(row["patient_key"], row["date"])].append(row)

    medical_by_patient = defaultdict(list)
    for row in medical:
        medical_by_patient[row["patient_key"]].append(row)

    visits_by_patient = defaultdict(list)
    for row in kept_visits:
        visits_by_patient[row["patient_key"]].append(row)

    prior_nonclinical_dates = defaultdict(list)
    for row in kept_visits:
        service_class = _service_class({
            "group": "",
            "service": row.get("services_text", ""),
            "comment": row.get("note", ""),
        })
        row["service_class"] = service_class
        if service_class in {"evlk", "sclero", "mini", "analysis", "hosiery"}:
            prior_nonclinical_dates[row["patient_key"]].append(row["date"])
    for row in patient_services:
        if row.get("date") and row.get("class") in {"evlk", "sclero", "mini", "analysis", "hosiery"}:
            prior_nonclinical_dates[row["patient_key"]].append(row["date"])

    primaries = []
    repeats = []
    primary_by_patient = {}
    for patient_key, rows in visits_by_patient.items():
        rows.sort(key=lambda r: (r["date"], r.get("start") or ""))
        has_primary = False
        for row in rows:
            text = " ".join((
                row.get("repeat_field", ""),
                row.get("note", ""),
                row.get("visit_type", ""),
                row.get("help_type", ""),
                row.get("services_text", ""),
            ))
            explicit_repeat = bool(REPEAT_RE.search(text))
            nonclinical = _is_nonclinical(text)
            has_prior_nonclinical = any(
                date < row["date"] for date in prior_nonclinical_dates.get(patient_key, [])
            )
            if not has_primary and not explicit_repeat and not nonclinical and not has_prior_nonclinical:
                primary_amount = round(float(row.get("amount") or 0), 2)
                evidence = text + " " + resolver.patients.get(patient_key, {}).get("note", "")
                for med in medical_by_patient.get(patient_key, []):
                    if med["date"] == row["date"]:
                        evidence += " " + " ".join((
                            med.get("direction", ""), med.get("assignment", ""), med.get("recommendation", ""), med.get("treatment", "")
                        ))
                indication = _indications(evidence)
                rec = {
                    "patient_key": patient_key,
                    "date": row["date"],
                    "doctor": row.get("doctor"),
                    "amount": primary_amount,
                    "paid": primary_amount > 0,
                    "indications": indication,
                    "note": row.get("note", ""),
                }
                primaries.append(rec)
                primary_by_patient[patient_key] = rec
                has_primary = True
                continue

            if explicit_repeat or (has_primary and not nonclinical):
                repeats.append({
                    "patient_key": patient_key,
                    "date": row["date"],
                    "doctor": row.get("doctor"),
                    "note": row.get("note", ""),
                })

    treatment_events = defaultdict(lambda: {
        "types": {"evlk": False, "sclero": False, "mini": False},
        "doctors": {"evlk": set(), "sclero": set(), "mini": set()},
        "dates": [],
        "basis": [],
        "payment": 0.0,
        "financial_evidence": False,
        "clinical_evidence": False,
    })

    for row in kept_visits:
        service_text = row.get("services_text", "")
        if not service_text:
            continue
        kinds = []
        if EVLK_RE.search(service_text):
            kinds.append("evlk")
        if SCLERO_RE.search(service_text):
            kinds.append("sclero")
        if MINI_RE.search(service_text):
            kinds.append("mini")
        if not kinds:
            continue
        event = treatment_events[row["patient_key"]]
        for kind in kinds:
            event["types"][kind] = True
            if row.get("doctor"):
                event["doctors"][kind].add(row["doctor"])
        event["dates"].append(row["date"])
        amount = float(row.get("amount") or 0)
        if amount > 0:
            event["payment"] += amount
            event["financial_evidence"] = True
        basis = " | ".join(value for value in (service_text, row.get("note", "")) if value)
        event["basis"].append(
            f"{'/'.join(k.upper() for k in kinds)} {row['date']} [registry_financial_service]: {basis}"
        )

    # If a future detailed-service export contains patient/date columns, retain it
    # as supplementary evidence without using aggregate rows for turnover.
    for row in patient_services:
        kind = row.get("class") or _service_class(row)
        if kind not in {"evlk", "sclero", "mini"} or not row.get("date"):
            continue
        patient_key = row["patient_key"]
        event = treatment_events[patient_key]
        event["types"][kind] = True
        if row.get("doctor"):
            event["doctors"][kind].add(row["doctor"])
        event["dates"].append(row["date"])
        event["basis"].append(
            f"{kind.upper()} {row.get('date') or ''} [detailed_service_control]: "
            f"{row.get('service','')} | {row.get('comment','')}"
        )

    for row in medical:
        text = " ".join((row.get("treatment", ""), row.get("assignment", ""), row.get("recommendation", "")))
        patient_key = row["patient_key"]
        kinds = []
        if EVLK_RE.search(text):
            kinds.append("evlk")
        if SCLERO_RE.search(text):
            kinds.append("sclero")
        if MINI_RE.search(text):
            kinds.append("mini")
        if not kinds:
            continue
        if not (PERFORMED_RE.search(text) or row.get("treatment")):
            continue
        event = treatment_events[patient_key]
        event["clinical_evidence"] = True
        for kind in kinds:
            event["types"][kind] = True
            if row.get("doctor"):
                event["doctors"][kind].add(row["doctor"])
        event["dates"].append(row["date"])
        event["basis"].append(
            f"{'/'.join(k.upper() for k in kinds)} {row['date']} [medical_protocol]: {row.get('treatment') or text}"
        )

    for row in kept_visits:
        text = row.get("note", "")
        if not SCLERO_RE.search(text):
            continue
        if not REPEAT_RE.search(text):
            continue
        event = treatment_events[row["patient_key"]]
        event["types"]["sclero"] = True
        event["clinical_evidence"] = True
        if row.get("doctor"):
            event["doctors"]["sclero"].add(row["doctor"])
        event["dates"].append(row["date"])
        event["basis"].append(f"SCLERO {row['date']} [registry_free_stage]: {text}")

    treatments = []
    for patient_key, event in treatment_events.items():
        event_dates = sorted({d for d in event["dates"] if d})
        if not event_dates:
            continue
        first_date = event_dates[0]
        basis_text = " | ".join(event["basis"])
        prepay_only = (
            event["financial_evidence"]
            and not event["clinical_evidence"]
            and PREPAY_RE.search(basis_text)
            and not PERFORMED_RE.search(basis_text)
        )
        if prepay_only:
            continue

        if event["types"]["evlk"] and MINI_RE.search(basis_text):
            event["types"]["mini"] = True
            for doctor in event["doctors"]["evlk"]:
                event["doctors"]["mini"].add(doctor)

        subsequent = [
            r for r in repeats
            if r["patient_key"] == patient_key and r["date"] >= first_date
        ]
        treatments.append({
            "patient_key": patient_key,
            "first_date": first_date,
            "types": dict(event["types"]),
            "doctors": {k: sorted(v) for k, v in event["doctors"].items()},
            "payment_found": event["payment"] > 0,
            "payment": round(event["payment"], 2),
            "subsequent_repeat_visits": len(subsequent),
            "basis": basis_text,
        })

    treatment_by_patient = {row["patient_key"]: row for row in treatments}
    suspicious = []
    for patient_key, rows in visits_by_patient.items():
        primary = primary_by_patient.get(patient_key)
        treatment = treatment_by_patient.get(patient_key)
        repeat_rows = [r for r in repeats if r["patient_key"] == patient_key]
        postop = [r for r in rows if REPEAT_RE.search(r.get("note", ""))]
        if treatment and not treatment["payment_found"]:
            strict = bool(primary and treatment["subsequent_repeat_visits"] > 0)
            suspicious.append({
                "patient_key": patient_key,
                "date": treatment["first_date"],
                "doctor": (primary or {}).get("doctor") or (repeat_rows[0].get("doctor") if repeat_rows else ""),
                "signal": "Лечение/коррекция без оплаты лечения в периоде" if not repeat_rows else "Манипуляция/коррекция + повтор без оплаты лечения в периоде",
                "primary_in_period": bool(primary),
                "treatment": treatment["basis"],
                "repeat": repeat_rows[0]["date"] + ": " + repeat_rows[0].get("note", "") if repeat_rows else "",
                "payment": 0,
                "confidence": "Высокая" if strict else "Низкая/недостаточно данных",
                "comment": "" if strict else "Первая запись пациента в периоде могла быть повторной/контрольной либо коррекцией; исходное лечение могло быть до границы периода.",
                "strict": strict,
            })
        elif not treatment and postop and not primary:
            first = postop[0]
            suspicious.append({
                "patient_key": patient_key,
                "date": first["date"],
                "doctor": first.get("doctor"),
                "signal": "Повтор/контроль после лечения без оплаты лечения в периоде",
                "primary_in_period": False,
                "treatment": "",
                "repeat": first["date"] + ": " + first.get("note", ""),
                "payment": 0,
                "confidence": "Низкая/недостаточно данных",
                "comment": "Первая запись пациента в периоде уже является повторной/контрольной либо коррекцией; исходное лечение могло быть до границы периода.",
                "strict": False,
            })

    interval_minutes, invalid_intervals = _merge_intervals(kept_visits)
    doctor_names = sorted({
        r.get("doctor") for r in kept_visits if r.get("doctor")
    })

    turnover_by_doctor = defaultdict(float)
    for row in kept_visits:
        if row.get("doctor"):
            turnover_by_doctor[row["doctor"]] += float(row.get("amount") or 0)

    doctor_metrics = []
    for doctor in doctor_names:
        doctor_primaries = [p for p in primaries if p["doctor"] == doctor]
        doctor_repeats = [r for r in repeats if r["doctor"] == doctor]
        repeat_people = {r["patient_key"] for r in doctor_repeats}
        indications = {
            kind: sum(1 for p in doctor_primaries if p["indications"][kind])
            for kind in ("evlk", "sclero", "mini")
        }
        treatment_counts = {}
        for kind in ("evlk", "sclero", "mini"):
            treatment_counts[kind] = sum(
                1 for t in treatments if doctor in t["doctors"][kind]
            )
        hosiery_people = {
            row["patient_key"]
            for row in kept_visits
            if row.get("doctor") == doctor
            and HOSIERY_RE.search(row.get("services_text", ""))
            and float(row.get("amount") or 0) > 0
        }
        turnover = round(turnover_by_doctor[doctor], 2)
        hours = interval_minutes.get(doctor, 0) / 60.0
        paid_primary = sum(1 for p in doctor_primaries if p["paid"])
        doctor_metrics.append({
            "doctor": doctor,
            "primary": len(doctor_primaries),
            "primary_paid": paid_primary,
            "repeat_visits": len(doctor_repeats),
            "repeat_people": len(repeat_people),
            "indications": indications,
            "treatments": treatment_counts,
            "hosiery_people": len(hosiery_people),
            "turnover": turnover,
            "avg_check_all_primary": turnover / len(doctor_primaries) if doctor_primaries else None,
            "avg_check_paid_primary": turnover / paid_primary if paid_primary else None,
            "occupied_hours": hours,
            "turnover_per_hour": turnover / hours if hours > 0 else None,
            "invalid_intervals": invalid_intervals.get(doctor, 0),
            "strict_suspicious": sum(1 for s in suspicious if s["doctor"] == doctor and s["strict"]),
            "manual_review": sum(1 for s in suspicious if s["doctor"] == doctor and not s["strict"]),
        })

    months = sorted({
        row["date"][:7] for row in kept_visits if row.get("date")
    })
    monthly = []
    for month in months:
        month_visits = [v for v in kept_visits if v.get("date", "").startswith(month)]
        month_primaries = [p for p in primaries if p["date"].startswith(month)]
        month_treatments = [t for t in treatments if t["first_date"].startswith(month)]
        turnover = round(sum(float(v.get("amount") or 0) for v in month_visits), 2)
        paid_primary = sum(1 for p in month_primaries if p["paid"])
        treatment_paid = sum(1 for t in month_treatments if t["payment_found"])
        monthly.append({
            "month": month,
            "turnover": turnover,
            "primary_total": len(month_primaries),
            "primary_paid": paid_primary,
            "primary_zero": len(month_primaries) - paid_primary,
            "avg_check_all_primary": turnover / len(month_primaries) if month_primaries else None,
            "avg_check_paid_primary": turnover / paid_primary if paid_primary else None,
            "treatment_people": len(month_treatments),
            "treatment_paid": treatment_paid,
            "treatment_unpaid": len(month_treatments) - treatment_paid,
        })

    dates = [r["date"] for r in kept_visits if r.get("date")]
    period_start = min(dates) if dates else None
    period_end = max(dates) if dates else None
    unique_repeat_people = {r["patient_key"] for r in repeats}

    registry_turnover = round(sum(float(v.get("amount") or 0) for v in kept_visits), 2)
    service_control_amount = round(sum(float(s.get("amount") or 0) for s in services), 2)
    service_control_quantity = round(sum(float(s.get("qty") or 0) for s in services), 2)

    payload = {
        "version": NORMALIZED_VERSION,
        "period": {"start": period_start, "end": period_end},
        "source_control": {
            "closed_appointments": len(kept_visits),
            "deleted_report_rows_parsed": len(deleted_rows),
            "deleted_report_total": deleted_total,
            "service_control_rows": len(services),
            "service_control_quantity": service_control_quantity,
            "service_control_amount": service_control_amount,
            "registry_turnover": registry_turnover,
            "turnover_delta": round(registry_turnover - service_control_amount, 2),
        },
        "patients": list(resolver.patients.values()),
        "visits": kept_visits,
        "medical_records": medical,
        "service_control_rows": services,
        "deleted_appointments": deleted_rows,
        "primaries": primaries,
        "repeats": repeats,
        "treatments": treatments,
        "suspicious": suspicious,
        "monthly": monthly,
        "doctor_metrics": doctor_metrics,
        "key_metrics": {
            "unique_patients": len([p for p in resolver.patients.values() if not p["key"].startswith("unmatched:")]),
            "closed_appointments": len(kept_visits),
            "turnover": registry_turnover,
            "primary_total": len(primaries),
            "primary_paid": sum(1 for p in primaries if p["paid"]),
            "primary_zero": sum(1 for p in primaries if not p["paid"]),
            "treatment_people": len(treatments),
            "treatment_paid": sum(1 for t in treatments if t["payment_found"]),
            "treatment_unpaid": sum(1 for t in treatments if not t["payment_found"]),
            "strict_suspicious": sum(1 for s in suspicious if s["strict"]),
            "repeat_people": len(unique_repeat_people),
        },
    }
    return payload


def normalize_source_files(paths):
    master = _patient_master(paths["patients_general"])
    registry = _registry_rows(paths["appointments_registry"])
    medical = _medical_rows(paths["medical_records"])
    services = _service_rows(paths["services_detailed"])
    deleted_rows, deleted_total = _deleted_rows(
        paths["deleted_appointments"],
        [row.get("doctor") for row in registry],
    )
    return normalize_rows(master, registry, medical, services, deleted_rows, deleted_total)


def normalize_batch(conn, batch_id, upload_root):
    _init_schema(conn)
    batch = conn.execute(
        "SELECT id,clinic_id FROM nn_upload_batches WHERE id=?",
        (batch_id,),
    ).fetchone()
    if not batch:
        raise ValueError("Набор файлов не найден.")

    file_rows = conn.execute(
        "SELECT source_key,stored_filename FROM nn_upload_files WHERE batch_id=?",
        (batch_id,),
    ).fetchall()
    names = {row["source_key"]: row["stored_filename"] for row in file_rows}
    required = {"appointments_registry", "medical_records", "services_detailed", "patients_general", "deleted_appointments"}
    if set(names) != required:
        raise ValueError("Набор файлов неполный.")

    base = Path(upload_root) / str(batch["clinic_id"]) / str(batch_id)
    paths = {key: base / filename for key, filename in names.items()}
    for path in paths.values():
        if not path.is_file():
            raise ValueError("Файл набора отсутствует на сервере.")

    payload = normalize_source_files(paths)
    now = iso_now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO nn_normalized_batches(
                batch_id,clinic_id,version,period_start,period_end,payload_json,normalized_at
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(batch_id) DO UPDATE SET
                version=excluded.version,
                period_start=excluded.period_start,
                period_end=excluded.period_end,
                payload_json=excluded.payload_json,
                normalized_at=excluded.normalized_at
            """,
            (
                batch_id,
                batch["clinic_id"],
                NORMALIZED_VERSION,
                payload["period"]["start"],
                payload["period"]["end"],
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
            ),
        )
        conn.execute(
            "UPDATE nn_upload_batches SET status='ready' WHERE id=?",
            (batch_id,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return payload
