import json
import re
from collections import defaultdict
from datetime import datetime

from flask import Response, abort, g, jsonify, request

from app import SITE_ROOT, admin_required, audit, csrf_token, db, iso_now
import report_storage


DENTISTS = {
    "Чирков М. С.": "Чирков Максим Сергеевич",
    "Мовсесян Б. А.": "Мовсесян Бабкен Ашотович",
    "Румянцева М. П.": "Румянцева Мария Павловна",
    "Авдалие Э. И.": "Авдалие Эрик Ишханович",
    "Назыров У. Ж.": "Назыров Уктам Алиевич",
    "Голышенков В. А.": "Голышенков Владислав Александрович",
    "Зубачев Р. Н.": "Зубачев Роман Николаевич",
    "Босак Я. С.": "Босак Яна Сергеевна",
}
STRUCTURE_DOCTORS = {
    "Старостенко В. А.": "Старостенко Вадим Анатольевич",
    "Димитренко А. Я.": "Димитренко Анна Яковлевна",
    "Лапина Н. С.": "Лапина Наталья Сергеевна",
    "Холодцева В. Е.": "Холодцева Валерия Евгеньевна",
    "Любаева А. Ю.": "Любаева Алёна Юрьевна",
    "Мамонтова Л. Г.": "Мамонтова Любава Геннадьевна",
    "Павлова А. В.": "Павлова Алла Викторовна",
}
LAB_DOCTORS = {"Казанцев Л. Е.": "Казанцев Л. Е."}
KNOWN_STAFF = set(DENTISTS) | set(STRUCTURE_DOCTORS) | set(LAB_DOCTORS) | {
    "Алатарцева П. В.",
    "Борисовская А. И.",
    "Администраторы -.",
    "Старшийадминистратор -.",
    "Старшийадминистратор ...",
    "Старшийадминист...",
}
MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
INVOICE_RE = re.compile(r"Счет №(\d+) от (\d{2}\.\d{2}\.\d{4})")
COUNT_RE = re.compile(r"^(\d+)\s*\((\d+)\)$")


def _decode_upload(file_storage):
    raw = file_storage.read()
    if not raw:
        abort(400)
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            abort(400)
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    abort(400)


def _money(value):
    value = str(value or "").replace("₽", "").replace("\xa0", " ").strip()
    value = value.replace(" ", "").replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def _iso(value):
    return datetime.strptime(value, "%d.%m.%Y").strftime("%Y-%m-%d")


def _parse_revenue(text):
    items = []
    lab_invoices = []
    current_staff = None
    current_date = None
    current_group = ""
    current_invoice = None

    for raw_line in text.splitlines()[2:]:
        cols = raw_line.split("\t")
        cols += [""] * max(0, 7 - len(cols))
        first = cols[0].strip()

        if first in KNOWN_STAFF:
            current_staff = first
            current_date = None
            current_group = ""
            current_invoice = None
            continue

        match = INVOICE_RE.match(first)
        if match:
            current_invoice = match.group(1)
            current_date = _iso(match.group(2))
            current_group = ""
            if current_staff in LAB_DOCTORS:
                lab_invoices.append((current_invoice, current_date))
            continue

        qty_text = cols[3].strip()
        amount = _money(cols[6] if len(cols) > 6 else "")
        if not current_staff or not current_date or not qty_text or amount is None:
            continue

        try:
            qty = float(qty_text.replace(",", "."))
        except ValueError:
            continue

        if first:
            current_group = first

        items.append({
            "staff": current_staff,
            "date": current_date,
            "group": current_group,
            "service": cols[2].strip(),
            "qty": qty,
            "amount": amount,
            "invoice": current_invoice,
        })

    if not items:
        abort(400)

    months = {(int(row["date"][0:4]), int(row["date"][5:7])) for row in items}
    if len(months) != 1:
        abort(400)

    return items, lab_invoices, next(iter(months))


def _parse_completed(text):
    overall = defaultdict(lambda: defaultdict(int))
    doctors = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    current_date = None
    current_kind = None
    kinds = {"Первичные", "Отконсультированные", "Повторные"}

    for raw_line in text.splitlines()[4:]:
        cols = raw_line.split("\t")
        cols += [""] * max(0, 13 - len(cols))
        first = cols[0].strip()
        second = cols[1].strip()
        count_text = cols[2].strip()

        if DATE_RE.match(first) and not second and COUNT_RE.match(count_text):
            current_date = _iso(first)
            current_kind = None
            continue

        if current_date and first in kinds:
            match = COUNT_RE.match(count_text)
            if match:
                overall[current_date][first] = int(match.group(1))
                current_kind = first
            continue

        if current_date and current_kind and first and not second:
            match = COUNT_RE.match(count_text)
            if match:
                doctors[current_date][current_kind][first] += int(match.group(1))

    if not overall:
        abort(400)
    return overall, doctors


def _classify_dent(group, service):
    group_l = group.lower()
    text = (group + " " + service).lower()
    service_l = service.lower()

    if "функциональная стоматология" in group_l:
        if "зуботехническая лаборатория" in group_l:
            return "Зуботехнические / лабораторные этапы"
        return "Функциональная стоматология / ВНЧС / функциональные аппараты"
    if "зуботехническая лаборатория" in group_l:
        return "Зуботехнические / лабораторные этапы"
    if any(x in text for x in ("рентген", "трг", "томограф")) or "кт " in text:
        return "Рентген / КТ / ТРГ"
    if "профессиональн" in text and "гигиен" in text:
        return "Профессиональная гигиена"
    if "отбел" in text:
        return "Отбеливание"
    if "пародонт" in text:
        return "Пародонтология"
    if "периодонтит" in text:
        return "Эндодонтия — периодонтит"
    if "эндодонт" in text or "пульпит" in text:
        return "Эндодонтия — пульпит"
    if "реставрац" in text or "художествен" in text:
        return "Реставрации / эстетическая терапия"
    if "лечение кариеса" in text or "леченние кариеса" in text or "кариес" in text:
        return "Лечение кариеса / пломбирование"
    if "одномоментн" in group_l or "немедленн" in group_l or "all-on" in text:
        return "Немедленная нагрузка / All-on-4 / All-on-6"
    if "костн" in text or "синус" in text:
        return "Костная пластика / синус-лифтинг"
    if "имплант" in text or "формировател" in text:
        if any(x in service_l for x in ("корон", "титанов", "сканмаркер")):
            return "Ортопедия — протезирование на имплантах"
        return "Имплантация"
    if "хирург" in group_l:
        if "удален" in service_l:
            return "Хирургия — удаление зубов"
        if any(x in text for x in ("шов", "мягк", "пародонтальн")):
            return "Хирургия — мягкие ткани / пародонтальная хирургия"
    if "ортопедическ" in group_l or any(
        x in service_l
        for x in (
            "корон", "винир", "вклад", "waxup", "wax-up", "сканирован",
            "слеп", "модель", "силиконового ключа", "фиксация коронки",
            "временной коронки",
        )
    ):
        if any(x in service_l for x in ("сканирован", "слеп", "waxup", "wax-up", "модел", "модель", "силиконового ключа")):
            return "Ортопедия — диагностика / слепки / сканирование"
        if any(x in service_l for x in ("имплант", "титанов", "сканмаркер")):
            return "Ортопедия — протезирование на имплантах"
        if any(x in service_l for x in ("съём", "съем", "протез")):
            return "Ортопедия — съёмное протезирование"
        return "Ортопедия — коронки / вкладки / виниры"
    if any(x in text for x in ("анестез", "коффердам", "optragayte", "ретракц", "матриц", "обезболив", "шовный материал")):
        return "Анестезия / материалы / вспомогательные услуги"
    if any(x in text for x in ("первичный осмотр", "повторный осмотр", "консультац", "осмотр")):
        return "Консультации и диагностика"
    return "Прочие стоматологические услуги"


def _classify_structure(group, service):
    text = (group + " " + service).lower()
    if "остеопат" in text:
        if "миофасциальн" in text or "массаж" in text:
            return "Миофасциальный массаж"
        if "лечение" in text or "коррекц" in text:
            return "Остеопатическое лечение / коррекция"
        return "Остеопатия — первичный / повторный приём"
    if "миофункцион" in text or "миотерап" in text:
        if "диагност" in text or "первичн" in text:
            return "Миофункциональная терапия — диагностика"
        return "Миофункциональная терапия — лечение / курсы"
    if "нутрици" in text or "метабол" in text:
        if "первич" in text:
            return "Нутрициология / метаболизм — первичный приём"
        return "Нутрициология / метаболизм — повторный / сопровождение"
    if "гастроэнтер" in text:
        if "первич" in text:
            return "Гастроэнтерология — первичный приём"
        return "Гастроэнтерология — повторный приём"
    if "гипокс" in text or "иггт" in text:
        return "Гипокситерапия / ИГГТ"
    if "diers" in text or "постурал" in text:
        return "DIERS / постуральная диагностика"
    if "подиатр" in text or "стельк" in text:
        return "Подиатрия / ортопедические стельки"
    if "нейропсих" in text:
        return "Нейропсихология"
    if "терапевт" in text:
        return "Терапия / врач-терапевт"
    return "Прочие / междисциплинарные услуги"


def _classify_lab(group, service):
    text = (group + " " + service).lower()
    if "доставк" in text:
        return "Доставка"
    if "коррекц" in text or "передел" in text or "почин" in text:
        return "Коррекция / переделка / починка"
    if "wax" in text:
        return "WAX-UP / цифровое моделирование"
    if "элайн" in text:
        return "Элайнеры / сеты"
    if "капп" in text or "сплинт" in text or "ретенц" in text:
        return "Каппы / сплинты / ретенционные"
    if "модел" in text or "печать" in text or "3d" in text:
        return "3D-печать / модели"
    if "функциональн" in text or "аппарат" in text:
        return "Функциональные аппараты"
    if "сроч" in text:
        return "Срочное изготовление"
    if "шаблон" in text:
        return "Хирургические шаблоны"
    if "перебаз" in text:
        return "Перебазировка"
    return "Диагностика / сравнение сканов"


def _existing_report(date):
    row = db().execute("SELECT payload FROM report_data WHERE date=?", (date,)).fetchone()
    if not row:
        return {}
    try:
        value = json.loads(row["payload"])
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _build_management(items, lab_invoices, overall, doctors, source):
    dates = sorted({row["date"] for row in items} | set(overall))
    daily_staff = defaultdict(lambda: defaultdict(float))
    for row in items:
        daily_staff[row["date"]][row["staff"]] += row["amount"]

    daily_lab_orders = defaultdict(int)
    for invoice, date in set(lab_invoices):
        daily_lab_orders[date] += 1

    cumulative_staff = defaultdict(float)
    primary_total = repeat_total = 0
    dent_primary = dent_repeat = 0
    structure_primary = structure_repeat = 0
    lab_orders = 0
    result = {}

    for date in dates:
        for staff, value in daily_staff[date].items():
            cumulative_staff[staff] += value

        primary_total += overall[date].get("Первичные", 0)
        repeat_total += overall[date].get("Повторные", 0) + overall[date].get("Отконсультированные", 0)

        for staff, value in doctors[date].get("Первичные", {}).items():
            if staff in DENTISTS:
                dent_primary += value
            elif staff in STRUCTURE_DOCTORS:
                structure_primary += value

        for kind in ("Повторные", "Отконсультированные"):
            for staff, value in doctors[date].get(kind, {}).items():
                if staff in DENTISTS:
                    dent_repeat += value
                elif staff in STRUCTURE_DOCTORS:
                    structure_repeat += value

        lab_orders += daily_lab_orders[date]
        lab_revenue = cumulative_staff.get("Казанцев Л. Е.", 0.0)
        total_revenue = sum(cumulative_staff.values())
        fact_medicine = total_revenue - lab_revenue

        existing = _existing_report(date)
        record = {
            "date": date,
            "plan": existing.get("plan", ""),
            "factMedicine": round(fact_medicine, 2),
            "factLab": round(lab_revenue, 2),
            "pp25": existing.get("pp25", ""),
            "avg25": existing.get("avg25", ""),
            "primary": primary_total,
            "repeat": repeat_total,
            "dentPrimary": dent_primary,
            "dentRepeat": dent_repeat,
            "dentists": {
                full: round(cumulative_staff.get(short, 0.0), 2)
                for short, full in DENTISTS.items()
            },
            "clinicPrimary": structure_primary,
            "clinicRepeat": structure_repeat,
            "clinicDocs": {
                full: round(cumulative_staff.get(short, 0.0), 2)
                for short, full in STRUCTURE_DOCTORS.items()
            },
            "labOrders": lab_orders,
            "labRevenue": round(lab_revenue, 2),
            "leadsDent": existing.get("leadsDent", ""),
            "leadsDentLost": existing.get("leadsDentLost", ""),
            "leadsClinic": existing.get("leadsClinic", ""),
            "leadsReserve": existing.get("leadsReserve", ""),
            "_source": source,
            "_aggregation": "month_to_date",
        }
        result[date] = record

    return result


def _load_blob(key):
    row = db().execute("SELECT payload FROM report_blobs WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row["payload"])
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError):
        return None


def _rename_structure_direction(container):
    if not isinstance(container, dict):
        return
    if "Клиника" in container and "Отделение структуры" not in container:
        container["Отделение структуры"] = container.pop("Клиника")


def _pad_months(service_data, month_index):
    for direction in service_data.get("directions", {}).values():
        for doctor in direction.get("doctors", {}).values():
            for series in doctor.values():
                if not isinstance(series, list):
                    continue
                while len(series) <= month_index:
                    series.append([0, 0])
                series[month_index] = [0, 0]


def _update_service_analytics(items, year, month):
    data = _load_blob("az-service-analytics-v1")
    if not data:
        abort(409)

    directions = data.setdefault("directions", {})
    _rename_structure_direction(directions)

    if int(data.get("year") or year) != year:
        abort(409)

    label = MONTH_NAMES[month - 1]
    months = data.setdefault("months", [])
    if label in months:
        month_index = months.index(label)
    else:
        expected_index = month - 1
        if len(months) != expected_index:
            abort(409)
        months.append(label)
        month_index = len(months) - 1

    _pad_months(data, month_index)

    for row in items:
        if row["staff"] in DENTISTS:
            direction_name = "Стоматология"
            doctor_name = DENTISTS[row["staff"]]
            category = _classify_dent(row["group"], row["service"])
        elif row["staff"] in STRUCTURE_DOCTORS:
            direction_name = "Отделение структуры"
            doctor_name = STRUCTURE_DOCTORS[row["staff"]]
            category = _classify_structure(row["group"], row["service"])
        elif row["staff"] in LAB_DOCTORS:
            direction_name = "Лаборатория"
            doctor_name = LAB_DOCTORS[row["staff"]]
            category = _classify_lab(row["group"], row["service"])
        else:
            continue

        direction = directions.get(direction_name)
        if not direction:
            continue
        doctor = direction.get("doctors", {}).get(doctor_name)
        if not doctor or category not in direction.get("categories", []):
            continue

        series = doctor.setdefault(category, [])
        while len(series) <= month_index:
            series.append([0, 0])
        series[month_index][0] += row["qty"]
        series[month_index][1] += row["amount"]

    last_date = max(row["date"] for row in items)
    data["id"] = f"az-services-{year}-through-{last_date}-v1"
    data["source"] = f"IDENT revenue export through {last_date}; unified price classifier"
    return data, month_index


def _pad_financial_months(month_index):
    updated = {}
    for key, empty in (("az-service-salary-v1", None), ("az-service-extra-payments-v1", 0)):
        data = _load_blob(key)
        if not data:
            continue
        _rename_structure_direction(data)
        for direction in data.values():
            if not isinstance(direction, dict):
                continue
            for series in direction.values():
                if not isinstance(series, list):
                    continue
                while len(series) <= month_index:
                    series.append(empty)
        updated[key] = data
    return updated


def _save_import(management, service_data, finance_blobs, source):
    conn = db()
    now = iso_now()
    conn.execute("BEGIN")
    try:
        for date, record in management.items():
            payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                """
                INSERT INTO report_data(date,payload,updated_by,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(date) DO UPDATE SET
                  payload=excluded.payload,
                  updated_by=excluded.updated_by,
                  updated_at=excluded.updated_at
                """,
                (date, payload, g.user["id"], now),
            )

        blobs = {"az-service-analytics-v1": service_data, **finance_blobs}
        for key, value in blobs.items():
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                """
                INSERT INTO report_blobs(key,payload,updated_by,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                  payload=excluded.payload,
                  updated_by=excluded.updated_by,
                  updated_at=excluded.updated_at
                """,
                (key, payload, g.user["id"], now),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    audit(
        "ident_reports_imported",
        target_user_id=g.user["id"],
        details=f"{source}; management={len(management)}",
    )


def register_ident_import(app):
    @app.get("/reports/import-ident/")
    @admin_required
    def ident_import_page():
        path = SITE_ROOT / "reports" / "import-ident.html"
        if not path.exists():
            abort(404)
        return Response(path.read_text(encoding="utf-8"), mimetype="text/html")

    @app.post("/api/reports/import-ident")
    @admin_required
    def ident_import_api():
        report_storage._require_api_csrf()
        revenue_file = request.files.get("revenue")
        completed_file = request.files.get("completed")
        if not revenue_file or not completed_file:
            abort(400)

        revenue_text = _decode_upload(revenue_file)
        completed_text = _decode_upload(completed_file)
        items, lab_invoices, (year, month) = _parse_revenue(revenue_text)
        overall, doctors = _parse_completed(completed_text)

        completed_dates = set(overall)
        if any(date[:7] != f"{year:04d}-{month:02d}" for date in completed_dates):
            abort(400)

        source = f"IDENT {revenue_file.filename} + {completed_file.filename}"
        management = _build_management(items, lab_invoices, overall, doctors, source)
        service_data, month_index = _update_service_analytics(items, year, month)
        finance_blobs = _pad_financial_months(month_index)
        _save_import(management, service_data, finance_blobs, source)

        final_date = max(management)
        final = management[final_date]
        return jsonify(
            ok=True,
            through=final_date,
            managementRecords=len(management),
            factMedicine=final["factMedicine"],
            factLab=final["factLab"],
            primary=final["primary"],
            repeat=final["repeat"],
            serviceMonth=MONTH_NAMES[month - 1],
            transitionsUpdated=False,
            csrf=csrf_token(),
        )
