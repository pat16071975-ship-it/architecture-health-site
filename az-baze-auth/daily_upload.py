import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from flask import abort, g, render_template, request

import daily_upload_core as core
from app import csrf_token, permission_required, require_csrf

# server.py replaces this module variable with the upload-aware permission wrapper
# before register_daily_upload() is called.
user_permissions = core.user_permissions

# Providers present in the current IDENT exports but absent from the historical
# hard-coded directory. They are added centrally so management rebuilding and
# service analytics use the same department mapping.
EXTRA_DENTISTS = {
    "Филатова А. Д.": "Филатова А. Д.",
}
EXTRA_STRUCTURE_DOCTORS = {
    "Diers И.": "Diers И.",
    "Алатарцева П. В.": "Алатарцева П. В.",
    "Борисовская А. И.": "Борисовская А. И.",
}
core.ident_import.DENTISTS.update(EXTRA_DENTISTS)
core.ident_import.STRUCTURE_DOCTORS.update(EXTRA_STRUCTURE_DOCTORS)
core.ident_import.KNOWN_STAFF.update(EXTRA_DENTISTS)
core.ident_import.KNOWN_STAFF.update(EXTRA_STRUCTURE_DOCTORS)

STAFF_HEADER_RE = re.compile(
    r"^(?:[A-Za-zА-ЯЁа-яё-]+(?:\s+[A-Za-zА-ЯЁа-яё-]+)*)\s+[A-ZА-ЯЁ]\.\s*(?:[A-ZА-ЯЁ]\.)?$"
)
DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
COUNT_RE = re.compile(r"^(\d+)\s*\((\d+)\)$")
KINDS = {"Первичные", "Отконсультированные", "Повторные"}


def _canonical(value):
    return json.dumps(
        core._plain(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _payload_hash(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _format_date(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return str(value or "")


def _iso(value):
    return datetime.strptime(value, "%d.%m.%Y").strftime("%Y-%m-%d")


def _money(value):
    value = str(value or "").replace("₽", "").replace("\xa0", " ").strip()
    value = value.replace(" ", "").replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def _is_staff_header(value):
    value = str(value or "").strip()
    return value in core.ident_import.KNOWN_STAFF or bool(STAFF_HEADER_RE.fullmatch(value))


def _person_key(value):
    text = str(value or "").lower().replace("ё", "е")
    parts = re.findall(r"[a-zа-я]+", text)
    if not parts:
        return ""
    surname = parts[0]
    initials = "".join(part[0] for part in parts[1:3])
    return surname + "_" + initials


def _is_retail_item(row):
    text = (str(row.get("group") or "") + " " + str(row.get("service") or "")).lower()
    return "сопутствующие товары" in text


def _parse_revenue_text(text):
    items = []
    lab_invoices = []
    current_staff = None
    current_patient = None
    current_date = None
    current_group = ""
    current_invoice = None

    for raw_line in text.splitlines()[2:]:
        cols = raw_line.split("\t")
        cols += [""] * max(0, 7 - len(cols))
        first = cols[0].strip()
        second = cols[1].strip()
        service = cols[2].strip()
        qty_text = cols[3].strip()

        if _is_staff_header(first):
            current_staff = first
            current_patient = None
            current_date = None
            current_group = ""
            current_invoice = None
            continue

        match = core.ident_import.INVOICE_RE.match(first)
        if match:
            current_invoice = match.group(1)
            current_date = _iso(match.group(2))
            current_group = ""
            if current_staff in core.ident_import.LAB_DOCTORS:
                lab_invoices.append((current_invoice, current_date))
            continue

        # Patient header between the staff header and invoice/service lines.
        if first and not second and not service and not qty_text:
            current_patient = first
            continue

        amount = _money(cols[6] if len(cols) > 6 else "")
        if not current_staff or not current_date or not qty_text or amount is None:
            continue

        try:
            qty = float(qty_text.replace(",", "."))
        except ValueError:
            continue

        if first:
            current_group = first

        items.append(
            {
                "staff": current_staff,
                "patient": current_patient or "",
                "date": current_date,
                "group": current_group,
                "service": service,
                "qty": qty,
                "amount": amount,
                "invoice": current_invoice,
            }
        )

    if not items:
        raise ValueError("Файл «Выполненные услуги» не содержит распознаваемых услуг.")

    months = {(int(row["date"][0:4]), int(row["date"][5:7])) for row in items}
    if len(months) != 1:
        raise ValueError("Файл «Выполненные услуги» должен содержать один календарный месяц.")

    return items, lab_invoices, next(iter(months))


def _parse_completed_text(text):
    overall = defaultdict(lambda: defaultdict(int))
    visits = []
    current_date = None
    current_kind = None

    for raw_line in text.splitlines()[4:]:
        cols = raw_line.split("\t")
        cols += [""] * max(0, 13 - len(cols))
        first = cols[0].strip()
        second = cols[1].strip()
        count_text = cols[2].strip()

        if DATE_RE.fullmatch(first) and not second and COUNT_RE.fullmatch(count_text):
            current_date = _iso(first)
            current_kind = None
            continue

        if current_date and first in KINDS:
            match = COUNT_RE.fullmatch(count_text)
            if match:
                overall[current_date][first] = int(match.group(1))
                current_kind = first
            continue

        # Current IDENT format: detail rows repeat the date in column A and
        # contain patient initials in column B; one row = one completed visit.
        if current_date and current_kind and DATE_RE.fullmatch(first) and second:
            visits.append(
                {
                    "date": _iso(first),
                    "patient": second,
                    "kind": current_kind,
                }
            )

    if not overall:
        raise ValueError("Файл «Завершённые приёмы» не содержит распознаваемых приёмов.")
    return overall, visits


def _parse_fixed_file(file_storage, kind):
    raw = file_storage.read()
    if not raw:
        raise ValueError("Выбран пустой файл.")
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("Размер файла превышает 25 МБ.")

    candidates = core._table_candidates(raw, file_storage.filename or "")
    last_error = None
    for sheet_name, text in candidates:
        try:
            parsed = _parse_completed_text(text) if kind == "completed" else _parse_revenue_text(text)
            return raw, parsed, sheet_name
        except Exception as exc:
            last_error = exc

    label = "«Завершённые приёмы»" if kind == "completed" else "«Выполненные услуги»"
    if isinstance(last_error, ValueError):
        raise last_error
    raise ValueError(f"Не удалось распознать структуру файла {label}.") from last_error


def _doctor_attribution(visits, items):
    # The completed-visits export contains patient/date rows but no doctor column.
    # Link them to the revenue export by patient + date. When a patient has visits
    # in both departments on the same date, allocate at least one visit to each.
    providers = defaultdict(list)
    for row in items:
        staff = row.get("staff")
        if staff in core.ident_import.DENTISTS:
            department = "dent"
        elif staff in core.ident_import.STRUCTURE_DOCTORS:
            department = "structure"
        else:
            continue
        key = (str(row.get("date") or ""), _person_key(row.get("patient")))
        if not key[0] or not key[1]:
            continue
        pair = (department, staff)
        if pair not in providers[key]:
            providers[key].append(pair)

    # If an employee is the patient and there is no revenue row linked to them,
    # their own staff identity still gives us a safe department fallback.
    self_provider = {}
    for short, full in core.ident_import.DENTISTS.items():
        self_provider[_person_key(short)] = ("dent", short)
        self_provider[_person_key(full)] = ("dent", short)
    for short, full in core.ident_import.STRUCTURE_DOCTORS.items():
        self_provider[_person_key(short)] = ("structure", short)
        self_provider[_person_key(full)] = ("structure", short)

    grouped_visits = defaultdict(list)
    for visit in visits:
        key = (str(visit.get("date") or ""), _person_key(visit.get("patient")))
        if key[0] and key[1]:
            grouped_visits[key].append(str(visit.get("kind") or ""))

    doctors = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for key, kinds in grouped_visits.items():
        candidates = list(providers.get(key, []))
        if not candidates and key[1] in self_provider:
            candidates = [self_provider[key[1]]]
        if not candidates:
            continue

        representatives = []
        seen_departments = set()
        for department, staff in candidates:
            if department in seen_departments:
                continue
            representatives.append((department, staff))
            seen_departments.add(department)

        assignments = representatives[: len(kinds)]
        while len(assignments) < len(kinds):
            assignments.append(candidates[0])

        data_date = key[0]
        for kind, (_department, staff) in zip(kinds, assignments):
            doctors[data_date][kind][staff] += 1

    return doctors


def _ensure_extra_service_doctors(data, year, month):
    # Make new providers visible to «Аналитика услуг» as well. Existing historical
    # doctor dictionaries are preserved; missing doctors start with zero series.
    core._ensure_month(data, year, month)
    month_count = len(data.get("months", []))
    for direction_name, doctor_names in (
        ("Стоматология", EXTRA_DENTISTS.values()),
        ("Отделение структуры", EXTRA_STRUCTURE_DOCTORS.values()),
    ):
        direction = data.get("directions", {}).get(direction_name)
        if not isinstance(direction, dict):
            continue
        categories = list(direction.get("categories", []))
        doctors = direction.setdefault("doctors", {})
        for doctor_name in doctor_names:
            if doctor_name in doctors:
                continue
            doctors[doctor_name] = {
                category: [[0, 0] for _ in range(month_count)]
                for category in categories
            }


def _split_period(overall, visits, items, lab_invoices):
    completed_dates = sorted(str(value) for value in overall)
    service_dates = sorted({str(row.get("date") or "") for row in items if row.get("date")})

    if not completed_dates:
        raise ValueError("Файл «Завершённые приёмы» не содержит дат.")
    if not service_dates:
        raise ValueError("Файл «Выполненные услуги» не содержит дат.")
    if completed_dates != service_dates:
        only_completed = sorted(set(completed_dates) - set(service_dates))
        only_services = sorted(set(service_dates) - set(completed_dates))
        details = []
        if only_completed:
            details.append("только в приёмах: " + ", ".join(_format_date(v) for v in only_completed))
        if only_services:
            details.append("только в услугах: " + ", ".join(_format_date(v) for v in only_services))
        suffix = "; ".join(details)
        raise ValueError("Даты в двух файлах не совпадают" + (f" ({suffix})." if suffix else "."))

    clinical_items = [row for row in items if not _is_retail_item(row)]
    doctors = _doctor_attribution(visits, clinical_items)
    result = []
    for data_date in completed_dates:
        day_overall = {data_date: core._plain(overall.get(data_date, {}))}
        day_doctors = {data_date: core._plain(doctors.get(data_date, {}))}
        day_visits = [core._plain(row) for row in visits if str(row.get("date") or "") == data_date]
        day_items = [
            core._plain(row)
            for row in clinical_items
            if str(row.get("date") or "") == data_date
        ]
        day_retail = [
            core._plain(row)
            for row in items
            if str(row.get("date") or "") == data_date and _is_retail_item(row)
        ]
        day_lab = [
            core._plain(row)
            for row in lab_invoices
            if len(row) >= 2 and str(row[1]) == data_date
        ]
        if not day_items:
            raise ValueError(f"За {_format_date(data_date)} в файле услуг нет медицинских строк для загрузки.")

        normalized = {
            "data_date": data_date,
            "items": day_items,
            "retail_items": day_retail,
            "lab_invoices": day_lab,
            "overall": day_overall,
            "doctors": day_doctors,
            "visits": day_visits,
        }
        completed_hash = _payload_hash(
            {
                "data_date": data_date,
                "overall": day_overall,
                "doctors": day_doctors,
                "visits": day_visits,
            }
        )
        services_hash = _payload_hash(
            {
                "data_date": data_date,
                "items": day_items,
                "retail_items": day_retail,
                "lab_invoices": day_lab,
            }
        )
        result.append(
            {
                "data_date": data_date,
                "normalized": normalized,
                "completed_hash": completed_hash,
                "services_hash": services_hash,
            }
        )
    return result


def _next_required_date(latest):
    basis = latest["data_date"] if latest else None
    if not basis:
        service_data = core.ident_import._load_blob("az-service-analytics-v1")
        if service_data:
            basis = core._service_through(service_data)
    if not basis:
        return None
    try:
        next_day = datetime.strptime(str(basis), "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        return None
    return next_day.strftime("%d.%m.%Y")


def _process_period_upload(completed_file, services_file):
    perms = user_permissions(g.user)
    if "upload_completed" not in perms or "upload_services" not in perms:
        abort(403)

    _completed_raw, completed_parsed, completed_sheet = _parse_fixed_file(completed_file, "completed")
    _services_raw, services_parsed, services_sheet = _parse_fixed_file(services_file, "services")
    overall, visits = completed_parsed
    items, lab_invoices, (year, month) = services_parsed

    period = _split_period(overall, visits, items, lab_invoices)
    dates = [row["data_date"] for row in period]
    month_key = f"{year:04d}-{month:02d}"
    if any(data_date[:7] != month_key for data_date in dates):
        raise ValueError("Месяц в двух файлах не совпадает.")

    completed_name = completed_file.filename or "Завершённые приёмы"
    services_name = services_file.filename or "Выполненные услуги"
    conn = core.db()

    service_data = core.ident_import._load_blob("az-service-analytics-v1")
    if not service_data:
        raise ValueError("База «Аналитики услуг» ещё не подготовлена.")
    through = core._service_through(service_data)

    # Full preflight first: no report data is changed until every date in the
    # uploaded period has passed duplicate/replacement checks.
    actions = []
    for row in period:
        data_date = row["data_date"]
        existing = conn.execute(
            "SELECT * FROM daily_uploads WHERE data_date=?",
            (data_date,),
        ).fetchone()
        old_normalized = None
        action = "imported"
        baseline_covered = False

        if existing:
            try:
                old_normalized = json.loads(existing["normalized_json"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Нельзя безопасно проверить данные за {_format_date(data_date)}: сохранённая версия повреждена."
                ) from exc

            if _canonical(old_normalized) == _canonical(row["normalized"]):
                action = "duplicate"
            else:
                action = "replaced"
                if "upload_replace" not in perms:
                    core._log(
                        conn,
                        "denied_replace",
                        data_date,
                        "Данные за дату уже существуют и отличаются; нет права на замену.",
                        completed_name,
                        services_name,
                    )
                    conn.commit()
                    raise ValueError(
                        f"За {_format_date(data_date)} данные уже загружены и отличаются. "
                        "Для замены требуется отдельное право."
                    )
        elif through and data_date <= through:
            action = "duplicate"
            baseline_covered = True

        actions.append(
            {
                **row,
                "existing": existing,
                "old_normalized": old_normalized,
                "action": action,
                "baseline_covered": baseline_covered,
            }
        )

    changed = [row for row in actions if row["action"] != "duplicate"]
    if not changed:
        for row in actions:
            detail = (
                "Дата уже входит в ранее загруженную сводную базу; пропущена без изменений."
                if row["baseline_covered"]
                else "Повторная загрузка идентичных данных за день; ничего не изменено."
            )
            core._log(
                conn,
                "duplicate",
                row["data_date"],
                detail,
                completed_name,
                services_name,
            )
        conn.commit()
        return {
            "status": "duplicate",
            "message": (
                f"Период {_format_date(dates[0])}–{_format_date(dates[-1])} уже покрыт загруженными данными. "
                "Повторно ничего не изменено."
            ),
            "data_date": dates[-1],
        }

    for row in changed:
        if row["action"] == "imported" and through and row["data_date"] <= through:
            raise ValueError(
                f"Дата {_format_date(row['data_date'])} уже входит в ранее загруженную сводную базу "
                f"по {_format_date(through)}. Начинайте выгрузку со следующего дня."
            )

    _ensure_extra_service_doctors(service_data, year, month)
    month_index = core._ensure_month(service_data, year, month)
    for row in changed:
        if row["old_normalized"]:
            core._apply_service_items(
                service_data,
                row["old_normalized"].get("items", []),
                year,
                month,
                -1,
            )
        month_index = core._apply_service_items(
            service_data,
            row["normalized"].get("items", []),
            year,
            month,
            +1,
        )

    old_through = core._service_through(service_data)
    candidates = [row["data_date"] for row in changed]
    if old_through:
        candidates.append(old_through)
    last_known = max(candidates)
    service_data["id"] = f"az-services-{year}-through-{last_known}-v1"
    service_data["source"] = f"Ежедневные загрузки AZ-BAZE through {last_known}"
    finance_blobs = core.ident_import._pad_financial_months(month_index)

    now = core.iso_now()
    source = f"AZ-BAZE period: {services_name} + {completed_name}"
    conn.execute("BEGIN")
    try:
        for row in actions:
            data_date = row["data_date"]
            if row["action"] == "duplicate":
                detail = (
                    "Дата уже входит в ранее загруженную сводную базу; пропущена без изменений."
                    if row["baseline_covered"]
                    else "Дата уже загружена идентично; данные пропущены без изменений."
                )
                core._log(
                    conn,
                    "duplicate",
                    data_date,
                    detail,
                    completed_name,
                    services_name,
                )
                continue

            normalized_json = json.dumps(
                row["normalized"],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if row["action"] == "replaced":
                revision = int(row["existing"]["revision"] or 1) + 1
                conn.execute(
                    "UPDATE daily_uploads SET completed_filename=?,services_filename=?,completed_sha256=?,services_sha256=?,normalized_json=?,revision=?,uploaded_by=?,uploaded_at=? WHERE data_date=?",
                    (
                        completed_name,
                        services_name,
                        row["completed_hash"],
                        row["services_hash"],
                        normalized_json,
                        revision,
                        g.user["id"],
                        now,
                        data_date,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO daily_uploads(data_date,completed_filename,services_filename,completed_sha256,services_sha256,normalized_json,revision,uploaded_by,uploaded_at) VALUES(?,?,?,?,?,?,1,?,?)",
                    (
                        data_date,
                        completed_name,
                        services_name,
                        row["completed_hash"],
                        row["services_hash"],
                        normalized_json,
                        g.user["id"],
                        now,
                    ),
                )

            core._log(
                conn,
                row["action"],
                data_date,
                f"Завершённые приёмы: лист {completed_sheet}; Выполненные услуги: лист {services_sheet}",
                completed_name,
                services_name,
            )

        management = core._rebuild_management(month_key, source)
        for day, record in management.items():
            conn.execute(
                "INSERT INTO report_data(date,payload,updated_by,updated_at) VALUES(?,?,?,?) ON CONFLICT(date) DO UPDATE SET payload=excluded.payload,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                (
                    day,
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                    g.user["id"],
                    now,
                ),
            )

        core._save_blob(conn, "az-service-analytics-v1", service_data, now)
        for key, value in finance_blobs.items():
            core._save_blob(conn, key, value, now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    counts = {
        "imported": sum(1 for row in actions if row["action"] == "imported"),
        "replaced": sum(1 for row in actions if row["action"] == "replaced"),
        "duplicate": sum(
            1 for row in actions if row["action"] == "duplicate" and not row["baseline_covered"]
        ),
        "covered": sum(1 for row in actions if row["baseline_covered"]),
    }
    core.audit(
        "period_reports_imported",
        target_user_id=g.user["id"],
        details=(
            f"period={dates[0]}..{dates[-1]}; imported={counts['imported']}; "
            f"replaced={counts['replaced']}; duplicate={counts['duplicate']}; "
            f"covered={counts['covered']}; completed={completed_name}; services={services_name}"
        ),
    )

    parts = [f"новых дней: {counts['imported']}"]
    if counts["replaced"]:
        parts.append(f"заменено: {counts['replaced']}")
    if counts["duplicate"]:
        parts.append(f"совпали и пропущены: {counts['duplicate']}")
    if counts["covered"]:
        parts.append(f"уже входили в сводную базу и пропущены: {counts['covered']}")
    return {
        "status": "imported" if counts["imported"] else "replaced",
        "message": (
            f"Готово. Период {_format_date(dates[0])}–{_format_date(dates[-1])} разнесён по дням; "
            + ", ".join(parts)
            + ". Управленческий отчёт и «Аналитика услуг» пересчитаны."
        ),
        "data_date": dates[-1],
    }


def register_daily_upload(app):
    core._init_schema()

    @app.route("/uploads/", methods=["GET", "POST"])
    @permission_required("section5")
    def uploads_page():
        perms = user_permissions(g.user)
        result = None
        error = None
        if request.method == "POST":
            require_csrf()
            completed = request.files.get("completed")
            services = request.files.get("services")
            if not completed or not completed.filename:
                error = "Выберите файл «Завершённые приёмы»."
            elif not services or not services.filename:
                error = "Выберите файл «Выполненные услуги»."
            else:
                try:
                    result = _process_period_upload(completed, services)
                except ValueError as exc:
                    error = str(exc)

        latest = core.db().execute(
            "SELECT data_date,revision,uploaded_at FROM daily_uploads ORDER BY data_date DESC LIMIT 1"
        ).fetchone()
        return render_template(
            "uploads.html",
            csrf=csrf_token(),
            perms=perms,
            result=result,
            error=error,
            latest=latest,
            next_required_date=_next_required_date(latest),
            history=core._history() if "upload_history" in perms else [],
            can_daily=("upload_completed" in perms and "upload_services" in perms),
            can_replace=("upload_replace" in perms),
        )
