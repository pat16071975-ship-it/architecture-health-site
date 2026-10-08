import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from flask import abort, g, jsonify, redirect, render_template, request, url_for

import daily_upload_core as core
import cash_payments
import paid_services
import upload_reconcile
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
        raise ValueError("Файл «Выручка по направлениям» не содержит распознаваемых услуг.")

    months = {(int(row["date"][0:4]), int(row["date"][5:7])) for row in items}
    if len(months) != 1:
        raise ValueError("Файл «Выручка по направлениям» должен содержать один календарный месяц.")

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


def _parse_fixed_raw(raw, filename, kind):
    if not raw:
        raise ValueError("Выбран пустой файл.")
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("Размер файла превышает 25 МБ.")

    if kind == "services":
        try:
            paid_report = paid_services.parse_bytes(raw, filename or "")
        except paid_services.NotPaidServicesReport:
            paid_report = None
        if paid_report is not None:
            year, month = [int(part) for part in paid_report["month"].split("-")]
            lab_invoices = [
                (row["invoice"], row["date"])
                for row in paid_report.get("invoices") or []
                if row.get("staff") in core.ident_import.LAB_DOCTORS
            ]
            parsed = (paid_report["items"], lab_invoices, (year, month))
            return parsed, paid_report["sheet"], paid_report

    candidates = core._table_candidates(raw, filename or "")
    last_error = None
    for sheet_name, text in candidates:
        try:
            parsed = _parse_completed_text(text) if kind == "completed" else _parse_revenue_text(text)
            return parsed, sheet_name, None
        except Exception as exc:
            last_error = exc

    label = "«Завершённые приёмы»" if kind == "completed" else "«Выручка по направлениям»"
    if isinstance(last_error, ValueError):
        raise last_error
    raise ValueError(f"Не удалось распознать структуру файла {label}.") from last_error


def _parse_fixed_file(file_storage, kind):
    raw = file_storage.read()
    parsed, sheet_name, paid_report = _parse_fixed_raw(
        raw, file_storage.filename or "", kind
    )
    return raw, parsed, sheet_name, paid_report

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
    # Keep «Аналитика услуг» aligned with the effective provider registry.
    # Existing historical doctor dictionaries are preserved; newly classified
    # providers start with zero series until their source rows are applied.
    core._ensure_month(data, year, month)
    month_count = len(data.get("months", []))
    for direction_name, doctor_names in (
        ("Стоматология", core.ident_import.DENTISTS.values()),
        ("Отделение структуры", core.ident_import.STRUCTURE_DOCTORS.values()),
        ("Лаборатория", core.ident_import.LAB_DOCTORS.values()),
    ):
        direction = data.get("directions", {}).get(direction_name)
        if not isinstance(direction, dict):
            continue
        categories = list(direction.get("categories", []))
        doctors = direction.setdefault("doctors", {})
        for doctor_name in sorted(set(doctor_names)):
            if doctor_name in doctors:
                continue
            doctors[doctor_name] = {
                category: [[0, 0] for _ in range(month_count)]
                for category in categories
            }


def _split_period(overall, visits, items, lab_invoices, paid_report=None):
    completed_dates = sorted(str(value) for value in overall)
    service_dates = sorted({str(row.get("date") or "") for row in items if row.get("date")})

    if not completed_dates:
        raise ValueError("Файл «Завершённые приёмы» не содержит дат.")
    if not service_dates:
        raise ValueError("Файл «Выручка по направлениям» не содержит дат.")

    # The two MIS exports describe different facts. A calendar day can legitimately
    # exist in only one source: e.g. revenue/service activity without a completed
    # visit row, or completed visits without revenue rows. Build one period from
    # the union of dates and treat the missing source for that day as zero.
    period_dates = sorted(set(completed_dates) | set(service_dates))

    clinical_items = [row for row in items if not _is_retail_item(row)]
    doctors = _doctor_attribution(visits, clinical_items)
    result = []
    for data_date in period_dates:
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
        normalized = {
            "data_date": data_date,
            "items": day_items,
            "retail_items": day_retail,
            "lab_invoices": day_lab,
            "overall": day_overall,
            "doctors": day_doctors,
            "visits": day_visits,
        }
        if paid_report and data_date == str(paid_report.get("period_end") or ""):
            normalized["paid_snapshot"] = paid_services.compact_report(paid_report)
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


def _prepare_period_raw(completed_raw, completed_name, services_raw, services_name):
    completed_parsed, completed_sheet, _completed_paid = _parse_fixed_raw(
        completed_raw, completed_name, "completed"
    )
    services_parsed, services_sheet, paid_report = _parse_fixed_raw(
        services_raw, services_name, "services"
    )
    overall, visits = completed_parsed
    items, lab_invoices, (year, month) = services_parsed
    completed_dates = {str(value) for value in overall}
    service_dates = {str(row.get("date") or "") for row in items if row.get("date")}
    source_gaps = {
        "completed_only": sorted(completed_dates - service_dates),
        "revenue_only": sorted(service_dates - completed_dates),
    }
    period = _split_period(overall, visits, items, lab_invoices, paid_report=paid_report)
    dates = [row["data_date"] for row in period]
    month_key = f"{year:04d}-{month:02d}"
    if any(data_date[:7] != month_key for data_date in dates):
        raise ValueError("Месяц в двух файлах не совпадает.")
    return {
        "period": period,
        "dates": dates,
        "month_key": month_key,
        "year": year,
        "month": month,
        "completed_sheet": completed_sheet,
        "services_sheet": services_sheet,
        "completed_name": completed_name or "Завершённые приёмы",
        "services_name": services_name or "Выручка по направлениям",
        "source_gaps": source_gaps,
        "paid_report": paid_report,
        "services_source_sha": hashlib.sha256(services_raw).hexdigest(),
    }


def _read_period_files(completed_file, services_file):
    completed_name = completed_file.filename or "Завершённые приёмы"
    services_name = services_file.filename or "Выручка по направлениям"
    completed_raw = completed_file.read()
    services_raw = services_file.read()
    if not completed_raw:
        raise ValueError("Выбран пустой файл «Завершённые приёмы».")
    if not services_raw:
        raise ValueError("Выбран пустой файл «Выручка по направлениям».")
    if len(completed_raw) > 25 * 1024 * 1024 or len(services_raw) > 25 * 1024 * 1024:
        raise ValueError("Размер файла превышает 25 МБ.")
    return completed_raw, completed_name, services_raw, services_name


def _comparison_has_conflict(comparison):
    return bool(
        comparison.get("conflict")
        or comparison.get("historical")
        or comparison.get("removed")
    )


def _comparison_counts(comparison):
    return {
        "new": len(comparison.get("new") or []),
        "identical": len(comparison.get("identical") or []),
        "conflict": len(comparison.get("conflict") or []),
        "historical": len(comparison.get("historical") or []),
        "removed": len(comparison.get("removed") or []),
    }


def _period_preview(completed_file, services_file):
    perms = user_permissions(g.user)
    if "upload_completed" not in perms or "upload_services" not in perms:
        abort(403)

    completed_raw, completed_name, services_raw, services_name = _read_period_files(
        completed_file, services_file
    )
    conn = core.db()
    upload_reconcile.init_schema(conn)
    upload_reconcile.refresh_runtime(conn, core.ident_import, cash_payments)
    prepared = _prepare_period_raw(
        completed_raw, completed_name, services_raw, services_name
    )

    service_data = core.ident_import._load_blob("az-service-analytics-v1")
    if not service_data:
        raise ValueError("База «Аналитики услуг» ещё не подготовлена.")
    through = core._service_through(service_data)
    comparison = upload_reconcile.compare_clinical(
        conn, prepared["period"], through
    )
    unknown = upload_reconcile.detect_unknown_providers(
        prepared["period"], core.ident_import, conn
    )
    if unknown:
        upload_reconcile.record_pending_providers(
            conn,
            unknown,
            services_name,
            actor_id=g.user["id"],
        )
        conn.commit()

    counts = _comparison_counts(comparison)
    return {
        "status": "preview",
        "period": {
            "from": prepared["dates"][0],
            "to": prepared["dates"][-1],
        },
        "counts": counts,
        "conflict_dates": sorted(
            set(comparison.get("conflict") or [])
            | set(comparison.get("historical") or [])
            | set(comparison.get("removed") or [])
        ),
        "unknown_providers": unknown,
        "source_gaps": prepared.get("source_gaps") or {"completed_only": [], "revenue_only": []},
        "requires_choice": _comparison_has_conflict(comparison),
        "requires_provider_mapping": bool(unknown),
        "can_replace": "upload_replace" in perms,
    }


def _provider_decisions_from_form():
    raw = request.form.get("provider_decisions", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Не удалось прочитать выбранные направления новых врачей.") from exc
    if not isinstance(value, dict):
        raise ValueError("Некорректный список новых врачей.")
    return value


def _reset_service_month(data, month_index):
    """Clear one service-analytics month before authoritative historical rebuild."""
    for direction in (data.get("directions") or {}).values():
        doctors = direction.get("doctors") if isinstance(direction, dict) else {}
        if not isinstance(doctors, dict):
            continue
        for doctor in doctors.values():
            if not isinstance(doctor, dict):
                continue
            for series in doctor.values():
                if not isinstance(series, list):
                    continue
                while len(series) <= month_index:
                    series.append([0, 0])
                series[month_index] = [0, 0]


def _validate_historical_replacement(conn, month_key, dates, through):
    row = conn.execute(
        """
        SELECT MIN(date) AS first_date, MAX(date) AS last_date
        FROM report_data
        WHERE substr(date,1,7)=?
        """,
        (month_key,),
    ).fetchone()
    first_old = row["first_date"] if row and row["first_date"] else None
    last_old = row["last_date"] if row and row["last_date"] else None
    required_end = min(str(through), str(last_old)) if through and last_old else (str(through) if through else last_old)
    if first_old and dates[0] > str(first_old):
        raise ValueError(
            "Для безопасной замены старой сводной базы загрузите полный исходный период "
            f"не позднее {_format_date(str(first_old))}."
        )
    if required_end and dates[-1] < required_end:
        raise ValueError(
            "Для безопасной замены старой сводной базы загрузите данные как минимум по "
            f"{_format_date(required_end)}."
        )


def _archive_and_remove_missing_daily(conn, comparison, actor_id):
    for data_date in comparison.get("removed") or []:
        existing = conn.execute(
            "SELECT * FROM daily_uploads WHERE data_date=?",
            (data_date,),
        ).fetchone()
        if not existing:
            continue
        upload_reconcile.archive_daily_row(
            conn, existing, actor_id, "use_new_removed"
        )
        conn.execute("DELETE FROM daily_uploads WHERE data_date=?", (data_date,))


def _process_period_upload(completed_file, services_file, decision=None, provider_decisions=None):
    perms = user_permissions(g.user)
    if "upload_completed" not in perms or "upload_services" not in perms:
        abort(403)

    completed_raw, completed_name, services_raw, services_name = _read_period_files(
        completed_file, services_file
    )
    conn = core.db()
    upload_reconcile.init_schema(conn)
    upload_reconcile.refresh_runtime(conn, core.ident_import, cash_payments)

    initial = _prepare_period_raw(
        completed_raw, completed_name, services_raw, services_name
    )
    unknown = upload_reconcile.detect_unknown_providers(
        initial["period"], core.ident_import, conn
    )

    provider_decisions = provider_decisions or {}
    if provider_decisions:
        if "upload_replace" not in perms:
            abort(403)
        missing = [name for name in unknown if name not in provider_decisions]
        if missing:
            raise ValueError(
                "Не выбрано направление для новых врачей: " + ", ".join(missing)
            )
        upload_reconcile.resolve_providers(
            conn, provider_decisions, g.user["id"]
        )
        conn.commit()
        upload_reconcile.refresh_runtime(conn, core.ident_import, cash_payments)
        prepared = _prepare_period_raw(
            completed_raw, completed_name, services_raw, services_name
        )
        unknown = upload_reconcile.detect_unknown_providers(
            prepared["period"], core.ident_import, conn
        )
    else:
        prepared = initial

    if unknown:
        upload_reconcile.record_pending_providers(
            conn, unknown, services_name, actor_id=g.user["id"]
        )
        conn.commit()
        raise ValueError(
            "Обнаружены новые врачи. Сначала определите направление: "
            + ", ".join(unknown)
        )

    service_data = core.ident_import._load_blob("az-service-analytics-v1")
    if not service_data:
        raise ValueError("База «Аналитики услуг» ещё не подготовлена.")
    through = core._service_through(service_data)
    comparison = upload_reconcile.compare_clinical(
        conn, prepared["period"], through
    )
    has_conflict = _comparison_has_conflict(comparison)

    if has_conflict and decision not in {"keep_old", "use_new"}:
        raise ValueError(
            "В загружаемом периоде есть отличия. Выберите «Сохранить старые» "
            "или «Загрузить новые»."
        )
    if decision == "use_new" and "upload_replace" not in perms:
        abort(403)

    dates = prepared["dates"]
    year = prepared["year"]
    month = prepared["month"]
    month_key = prepared["month_key"]
    completed_sheet = prepared["completed_sheet"]
    services_sheet = prepared["services_sheet"]

    if comparison.get("historical") and decision == "use_new":
        _validate_historical_replacement(conn, month_key, dates, through)

    apply_new = decision == "use_new"
    changed_rows = []
    for row in prepared["period"]:
        data_date = row["data_date"]
        if data_date in set(comparison.get("new") or []):
            changed_rows.append((row, "imported"))
        elif apply_new and data_date in set(comparison.get("conflict") or []):
            changed_rows.append((row, "replaced"))
        elif apply_new and data_date in set(comparison.get("historical") or []):
            changed_rows.append((row, "historical_replaced"))

    remove_dates = list(comparison.get("removed") or []) if apply_new else []
    if not changed_rows and not remove_dates:
        return {
            "status": "duplicate",
            "message": (
                f"Период {_format_date(dates[0])}–{_format_date(dates[-1])}: "
                + (
                    "выбраны старые данные; существующие значения сохранены без изменений."
                    if has_conflict and decision == "keep_old"
                    else "данные совпадают; повторно ничего не изменено."
                )
            ),
            "data_date": dates[-1],
        }

    backup_path = None
    if apply_new and has_conflict:
        backup_path = upload_reconcile.create_db_backup("clinical-reimport")

    historical_replace = bool(comparison.get("historical")) and apply_new
    _ensure_extra_service_doctors(service_data, year, month)
    month_index = core._ensure_month(service_data, year, month)

    if historical_replace:
        core.ident_import._pad_months(service_data, month_index)
        _reset_service_month(service_data, month_index)
        # Full historical replacement makes the uploaded source authoritative
        # for the covered interval; the old monthly service totals are cleared
        # before all incoming rows are re-applied.
        service_rows = list(prepared["period"])
    else:
        service_rows = [row for row, _action in changed_rows]
        for data_date in remove_dates:
            existing = conn.execute(
                "SELECT normalized_json FROM daily_uploads WHERE data_date=?",
                (data_date,),
            ).fetchone()
            if existing:
                old_normalized = json.loads(existing["normalized_json"])
                core._apply_service_items(
                    service_data,
                    old_normalized.get("items", []),
                    year,
                    month,
                    -1,
                )
        for row, action in changed_rows:
            if action == "replaced":
                existing = conn.execute(
                    "SELECT normalized_json FROM daily_uploads WHERE data_date=?",
                    (row["data_date"],),
                ).fetchone()
                if existing:
                    old_normalized = json.loads(existing["normalized_json"])
                    core._apply_service_items(
                        service_data,
                        old_normalized.get("items", []),
                        year,
                        month,
                        -1,
                    )

    for row in service_rows:
        core._apply_service_items(
            service_data,
            row["normalized"].get("items", []),
            year,
            month,
            +1,
        )

    old_through = core._service_through(service_data)
    candidates = [row["data_date"] for row in prepared["period"]]
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
        if historical_replace:
            old_rows = conn.execute(
                """
                SELECT * FROM daily_uploads
                WHERE data_date>=? AND data_date<=?
                ORDER BY data_date
                """,
                (dates[0], dates[-1]),
            ).fetchall()
            old_by_date = {str(row["data_date"]): row for row in old_rows}
            for old_row in old_rows:
                upload_reconcile.archive_daily_row(
                    conn, old_row, g.user["id"], "use_new_historical"
                )
            conn.execute(
                "DELETE FROM daily_uploads WHERE data_date>=? AND data_date<=?",
                (dates[0], dates[-1]),
            )
            for row in prepared["period"]:
                previous = old_by_date.get(row["data_date"])
                revision = int(previous["revision"] or 1) + 1 if previous else 1
                conn.execute(
                    """
                    INSERT INTO daily_uploads(
                        data_date,completed_filename,services_filename,
                        completed_sha256,services_sha256,normalized_json,
                        revision,uploaded_by,uploaded_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row["data_date"],
                        completed_name,
                        services_name,
                        row["completed_hash"],
                        row["services_hash"],
                        json.dumps(row["normalized"], ensure_ascii=False, separators=(",", ":")),
                        revision,
                        g.user["id"],
                        now,
                    ),
                )
        else:
            _archive_and_remove_missing_daily(
                conn, {"removed": remove_dates}, g.user["id"]
            )
            for row, action in changed_rows:
                data_date = row["data_date"]
                normalized_json = json.dumps(
                    row["normalized"], ensure_ascii=False, separators=(",", ":")
                )
                existing = conn.execute(
                    "SELECT * FROM daily_uploads WHERE data_date=?",
                    (data_date,),
                ).fetchone()
                if existing:
                    upload_reconcile.archive_daily_row(
                        conn, existing, g.user["id"], "use_new"
                    )
                    revision = int(existing["revision"] or 1) + 1
                    conn.execute(
                        """
                        UPDATE daily_uploads
                        SET completed_filename=?,services_filename=?,
                            completed_sha256=?,services_sha256=?,
                            normalized_json=?,revision=?,uploaded_by=?,uploaded_at=?
                        WHERE data_date=?
                        """,
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
                        """
                        INSERT INTO daily_uploads(
                            data_date,completed_filename,services_filename,
                            completed_sha256,services_sha256,normalized_json,
                            revision,uploaded_by,uploaded_at
                        ) VALUES(?,?,?,?,?,?,1,?,?)
                        """,
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

        management = core._rebuild_management(month_key, source)
        cash_payments.overlay_record_map(conn, month_key, management)

        if apply_new:
            for removed_date in remove_dates:
                conn.execute("DELETE FROM report_data WHERE date=?", (removed_date,))
            if historical_replace:
                incoming_dates = set(dates)
                old_report_rows = conn.execute(
                    """
                    SELECT date FROM report_data
                    WHERE date>=? AND date<=?
                    """,
                    (dates[0], dates[-1]),
                ).fetchall()
                for old_row in old_report_rows:
                    old_date = str(old_row["date"])
                    if old_date not in management and old_date not in incoming_dates:
                        conn.execute("DELETE FROM report_data WHERE date=?", (old_date,))

        for day, record in management.items():
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
                    day,
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                    g.user["id"],
                    now,
                ),
            )

        # Re-overlay the independent money source after every clinical rebuild.
        cash_payments.overlay_stored_month(conn, month_key, g.user["id"], now)

        core._save_blob(conn, "az-service-analytics-v1", service_data, now)
        for key, value in finance_blobs.items():
            core._save_blob(conn, key, value, now)

        for row, action in changed_rows:
            core._log(
                conn,
                "replaced" if action in {"replaced", "historical_replaced"} else "imported",
                row["data_date"],
                (
                    f"global_decision={decision or 'append'}; "
                    f"Завершённые приёмы: лист {completed_sheet}; "
                    f"Выручка по направлениям: лист {services_sheet}"
                ),
                completed_name,
                services_name,
            )
        for removed_date in remove_dates:
            core._log(
                conn,
                "replaced",
                removed_date,
                "global_decision=use_new; дата отсутствует в новой версии периода и удалена из клинического слоя.",
                completed_name,
                services_name,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    counts = _comparison_counts(comparison)
    core.audit(
        "period_reports_reconciled",
        target_user_id=g.user["id"],
        details=(
            f"period={dates[0]}..{dates[-1]}; decision={decision or 'append'}; "
            f"new={counts['new']}; identical={counts['identical']}; "
            f"conflict={counts['conflict']}; historical={counts['historical']}; "
            f"removed={counts['removed']}; backup={backup_path or ''}; "
            f"completed={completed_name}; services={services_name}"
        ),
    )

    parts = [f"новых дней: {counts['new']}"]
    if counts["conflict"]:
        parts.append(
            f"отличались: {counts['conflict']} ({'загружены новые' if apply_new else 'сохранены старые'})"
        )
    if counts["historical"]:
        parts.append(
            f"старых сводных дат: {counts['historical']} ({'заменены' if apply_new else 'сохранены'})"
        )
    if counts["removed"]:
        parts.append(
            f"дат отсутствуют в новой версии: {counts['removed']} ({'удалены из клинического слоя' if apply_new else 'сохранены'})"
        )
    if counts["identical"]:
        parts.append(f"совпали: {counts['identical']}")
    return {
        "status": "replaced" if apply_new and has_conflict else "imported",
        "message": (
            f"Готово. Период {_format_date(dates[0])}–{_format_date(dates[-1])}; "
            + ", ".join(parts)
            + ". Все зависимые клинические отчёты пересчитаны из одной выбранной версии."
        ),
        "data_date": dates[-1],
        "backup": backup_path,
    }

def register_daily_upload(app):
    core._init_schema()

    @app.post("/api/uploads/providers/resolve")
    @permission_required("section5")
    def provider_resolve_api():
        require_csrf()
        perms = user_permissions(g.user)
        if "upload_replace" not in perms:
            abort(403)
        try:
            decisions = _provider_decisions_from_form()
            if not decisions:
                raise ValueError("Не выбрано ни одного врача для классификации.")
            conn = core.db()
            upload_reconcile.resolve_providers(conn, decisions, g.user["id"])
            conn.commit()
            upload_reconcile.refresh_runtime(conn, core.ident_import, cash_payments)
            core.audit(
                "provider_registry_updated",
                target_user_id=g.user["id"],
                details="providers=" + ",".join(sorted(decisions)),
            )
            return jsonify(
                {
                    "status": "ok",
                    "message": (
                        "Классификация сохранена. Для пересчёта уже загруженного периода "
                        "повторно загрузите исходные файлы и подтвердите выбранную версию."
                    ),
                }
            )
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    @app.post("/api/uploads/clinical/preview")
    @permission_required("section5")
    def clinical_upload_preview():
        require_csrf()
        completed = request.files.get("completed")
        services = request.files.get("services")
        if not completed or not completed.filename:
            return jsonify({"status": "error", "message": "Выберите файл «Завершённые приёмы»."}), 400
        if not services or not services.filename:
            return jsonify({"status": "error", "message": "Выберите файл «Выручка по направлениям»."}), 400
        try:
            return jsonify(_period_preview(completed, services))
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    @app.post("/api/uploads/clinical/commit")
    @permission_required("section5")
    def clinical_upload_commit():
        require_csrf()
        completed = request.files.get("completed")
        services = request.files.get("services")
        if not completed or not completed.filename:
            return jsonify({"status": "error", "message": "Выберите файл «Завершённые приёмы»."}), 400
        if not services or not services.filename:
            return jsonify({"status": "error", "message": "Выберите файл «Выручка по направлениям»."}), 400
        try:
            result = _process_period_upload(
                completed,
                services,
                decision=request.form.get("decision") or None,
                provider_decisions=_provider_decisions_from_form(),
            )
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409

    @app.route("/uploads/", methods=["GET", "POST"])
    @permission_required("section5")
    def uploads_page():
        # The page is display-only. All real uploads use the preview/commit API.
        # A direct POST can only be a legacy/browser replay of an old form submit;
        # always collapse it to a clean GET so refresh/Ctrl+F5 can never replay
        # clinical data processing.
        if request.method == "POST":
            return redirect(url_for("uploads_page"), code=303)

        perms = user_permissions(g.user)
        result = None
        error = None

        conn = core.db()
        latest = conn.execute(
            "SELECT data_date,revision,uploaded_at FROM daily_uploads ORDER BY data_date DESC LIMIT 1"
        ).fetchone()
        cash_latest = cash_payments.latest_loaded_date(conn)
        cash_next_required = cash_payments.next_required_date(conn)
        return render_template(
            "uploads.html",
            csrf=csrf_token(),
            perms=perms,
            result=result,
            error=error,
            latest=latest,
            next_required_date=_next_required_date(latest),
            cash_latest_date=_format_date(cash_latest) if cash_latest else None,
            cash_next_required_date=_format_date(cash_next_required) if cash_next_required else None,
            history=core._history() if "upload_history" in perms else [],
            pending_providers=upload_reconcile.pending_provider_rows(conn),
            can_daily=("upload_completed" in perms and "upload_services" in perms),
            can_replace=("upload_replace" in perms),
        )
