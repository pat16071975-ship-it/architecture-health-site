import io
import json
from collections import defaultdict
from datetime import date, datetime
from calendar import monthrange

from flask import jsonify, render_template, request, send_file
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment

import nn_normalize
import nn_upload
from app import csrf_token, db


MONTH_NAMES_RU = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}


REPORTS = (
    ("key_metrics", "Ключевые показатели"),
    ("monthly", "Помесячная сводка"),
    ("doctor_compare", "Врачи — сравнение"),
    ("doctors", "Врачи"),
    ("suspicious", "Подозрительные"),
    ("primaries", "Первичные"),
    ("treatment", "Лечение"),
    ("primary_repeat", "Первичные / повторные пациенты"),
)


def _safe_json(value):
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _batch_rows(conn, clinic_id):
    return conn.execute(
        """
        SELECT n.batch_id,n.period_start,n.period_end,n.payload_json,b.uploaded_at
        FROM nn_normalized_batches n
        JOIN nn_upload_batches b ON b.id=n.batch_id
        JOIN nn_clinics c ON c.clinic_id=n.clinic_id
        WHERE n.clinic_id=? AND b.status='ready' AND c.active=1
        ORDER BY b.uploaded_at,n.batch_id
        """,
        (clinic_id,),
    ).fetchall()


def _dedupe_rows(rows, fields):
    found = {}
    for row in rows:
        key = tuple(json.dumps(row.get(field), ensure_ascii=False, sort_keys=True) for field in fields)
        found[key] = row
    return list(found.values())


def _date_owned(batch_index, value, periods, merge_controls):
    if not value:
        return False

    current = merge_controls[batch_index]
    if current["modern"] and value in current["ignore_dates"]:
        return False

    for later in range(batch_index + 1, len(periods)):
        control = merge_controls[later]
        if control["modern"]:
            if value in control["replace_dates"]:
                return False
            continue

        # Preserve the historical rule for already accepted legacy batches:
        # a later legacy batch owns its whole declared period.
        start, end = periods[later]
        if start and end and start <= value <= end:
            return False
    return True


def _patient_owned(batch_index, patient, merge_controls):
    refs = nn_upload._patient_refs(patient)
    current = merge_controls[batch_index]
    if current["modern"] and refs & current["ignore_patient_refs"]:
        return False
    for later in range(batch_index + 1, len(merge_controls)):
        control = merge_controls[later]
        if control["modern"] and refs & control["replace_patient_refs"]:
            return False
    return True


def effective_payload(conn, clinic_id):
    rows = _batch_rows(conn, clinic_id)
    if not rows:
        return None
    payloads = [_safe_json(row["payload_json"]) for row in rows]
    periods = [(row["period_start"], row["period_end"]) for row in rows]
    merge_controls = []
    for payload in payloads:
        control = payload.get("merge_control")
        modern = isinstance(control, dict) and control.get("version") == 1
        merge_controls.append({
            "modern": modern,
            "active": bool(control.get("active", True)) if modern else True,
            "use_controls": bool(control.get("use_controls", True)) if modern else True,
            "ignore_dates": set(control.get("ignore_dates", [])) if modern else set(),
            "replace_dates": set(control.get("replace_dates", [])) if modern else set(),
            "ignore_patient_refs": set(control.get("ignore_patient_refs", [])) if modern else set(),
            "replace_patient_refs": set(control.get("replace_patient_refs", [])) if modern else set(),
        })

    control_indices = [
        index for index, control in enumerate(merge_controls)
        if control["active"] and control["use_controls"]
    ]
    newest_control_index = control_indices[-1] if control_indices else None

    master = []
    visits = []
    medical = []
    deleted = []
    control_services = []
    service_control_period = {}
    payment_control = []

    for index, payload in enumerate(payloads):
        control = merge_controls[index]
        if control["modern"] and not control["active"]:
            continue

        for patient in payload.get("patients", []):
            if not _patient_owned(index, patient, merge_controls):
                continue
            master.append({
                "name": patient.get("name", ""),
                "dob": patient.get("dob"),
                "gender": patient.get("gender", ""),
                "source": patient.get("source", ""),
                "visits_count": patient.get("visits_count", 0),
                "iin": patient.get("iin", ""),
                "chart": patient.get("chart", ""),
                "phone": patient.get("phone", ""),
                "note": patient.get("note", ""),
                "source_amount": 0,
            })
        for item in payload.get("visits", []):
            if _date_owned(index, item.get("date"), periods, merge_controls):
                visits.append(dict(item))
        for item in payload.get("medical_records", []):
            if _date_owned(index, item.get("date"), periods, merge_controls):
                medical.append(dict(item))
        for item in payload.get("deleted_appointments", []):
            if _date_owned(index, item.get("appointment_date"), periods, merge_controls):
                deleted.append(dict(item))

        # Aggregate service controls are useful for reconciliation only.
        # Keep only the newest batch's control rows when histories overlap.
        if index == newest_control_index:
            control_services = [dict(item) for item in payload.get("service_control_rows", [])]
            service_control_period = dict(
                payload.get("service_control_period") or payload.get("period") or {}
            )
            payment_control = [dict(item) for item in payload.get("payment_control_rows", [])]

    master = _dedupe_rows(master, ("chart", "iin", "phone", "name", "dob"))
    visits = _dedupe_rows(
        visits,
        ("date", "start", "end", "doctor", "patient", "chart", "phone", "services_text", "amount"),
    )
    medical = _dedupe_rows(
        medical,
        ("date", "doctor", "patient", "chart", "treatment", "recommendation", "assignment"),
    )
    deleted = _dedupe_rows(
        deleted,
        ("deleted_date", "deleted_time", "appointment_date", "appointment_time", "doctor", "patient"),
    )
    merged = nn_normalize.normalize_rows(master, visits, medical, control_services, deleted, len(deleted))
    merged["service_control_period"] = service_control_period
    merged["payment_control_rows"] = payment_control
    return merged


def _context(payload):
    dates = sorted({row.get("date") for row in payload.get("visits", []) if row.get("date")})
    years = sorted({int(value[:4]) for value in dates}, reverse=True)
    months_by_year = {}
    for year in years:
        months_by_year[str(year)] = sorted(
            {int(value[5:7]) for value in dates if value.startswith(f"{year:04d}-")}
        )
    doctors = sorted({row.get("doctor") for row in payload.get("visits", []) if row.get("doctor")})
    earliest = dates[0] if dates else None
    latest = dates[-1] if dates else None
    return {
        "years": years,
        "months_by_year": months_by_year,
        "doctors": doctors,
        "earliest_date": earliest,
        "latest_date": latest,
    }


def _valid_iso_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _bounds(payload):
    ctx = _context(payload)
    earliest = ctx["earliest_date"]
    latest = ctx["latest_date"]
    if not earliest or not latest:
        return None, None, ctx

    raw_from = _valid_iso_date(request.args.get("date_from", "").strip())
    raw_to = _valid_iso_date(request.args.get("date_to", "").strip())
    if raw_from or raw_to:
        start = raw_from or earliest
        end = raw_to or latest
        start = max(start, earliest)
        end = min(end, latest)
        if start > end:
            start, end = end, start
        return start, end, ctx

    try:
        year = int(request.args.get("year") or latest[:4])
    except ValueError:
        year = int(latest[:4])
    try:
        month = int(request.args.get("month") or latest[5:7])
    except ValueError:
        month = int(latest[5:7])
    month = min(12, max(1, month))
    month_start = f"{year:04d}-{month:02d}-01"
    month_end = f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"
    start = max(month_start, earliest)
    end = min(month_end, latest)
    return start, end, ctx


def _in_range(value, start, end):
    return bool(value and start and end and start <= value <= end)


def _patient_map(payload):
    return {row["key"]: row for row in payload.get("patients", []) if row.get("key")}


def _money(value):
    return round(float(value or 0), 2)


def _period_rows(payload, start, end):
    visits = [row for row in payload.get("visits", []) if _in_range(row.get("date"), start, end)]
    primaries = [row for row in payload.get("primaries", []) if _in_range(row.get("date"), start, end)]
    repeats = [row for row in payload.get("repeats", []) if _in_range(row.get("date"), start, end)]
    treatments = [row for row in payload.get("treatments", []) if _in_range(row.get("first_date"), start, end)]
    suspicious = [row for row in payload.get("suspicious", []) if _in_range(row.get("date"), start, end)]
    return visits, primaries, repeats, treatments, suspicious


def _metric_items(payload, start, end):
    visits, primaries, repeats, treatments, suspicious = _period_rows(payload, start, end)
    people = {row.get("patient_key") for row in visits if row.get("patient_key")}
    turnover = sum(_money(row.get("amount")) for row in visits)
    return [
        ("Уникальных пациентов после нормализации", len(people), "count"),
        ("Закрытых приёмов", len(visits), "count"),
        ("Оборот за период", turnover, "money"),
        ("Первичных пациентов", len(primaries), "count"),
        ("Первичных с оплатой первичного визита", sum(1 for row in primaries if row.get("paid")), "count"),
        ("Первичных с нулевой оплатой", sum(1 for row in primaries if not row.get("paid")), "count"),
        ("Пациентов с подтверждённым лечением", len(treatments), "count"),
        ("Из них найдена оплата лечения", sum(1 for row in treatments if row.get("payment_found")), "count"),
        ("Клиническое лечение/коррекция без оплаты лечения в периоде", sum(1 for row in treatments if not row.get("payment_found")), "count"),
        ("Строгих подозрительных", sum(1 for row in suspicious if row.get("strict")), "count"),
    ]


def _monthly_items(payload, start, end):
    month = start[:7]
    primaries = [row for row in payload.get("primaries", []) if row.get("date", "").startswith(month)]
    treatments = [row for row in payload.get("treatments", []) if row.get("first_date", "").startswith(month)]
    visits = [row for row in payload.get("visits", []) if row.get("date", "").startswith(month)]
    turnover = sum(_money(row.get("amount")) for row in visits)
    paid_primary = sum(1 for row in primaries if row.get("paid"))
    paid_treatment = sum(1 for row in treatments if row.get("payment_found"))
    return [
        ("Оборот", turnover, "money"),
        ("Первичных", len(primaries), "count"),
        ("Первичных с оплатой", paid_primary, "count"),
        ("Первичных без оплаты", len(primaries) - paid_primary, "count"),
        ("Средний чек / всех первичных", turnover / len(primaries) if primaries else None, "money"),
        ("Средний чек / оплативших первичных", turnover / paid_primary if paid_primary else None, "money"),
        ("Пациентов с лечением", len(treatments), "count"),
        ("С оплатой лечения", paid_treatment, "count"),
        ("Без оплаты лечения в периоде", len(treatments) - paid_treatment, "count"),
    ]


def _doctor_rows(payload, start, end, detailed):
    visits, primaries, repeats, treatments, suspicious = _period_rows(payload, start, end)
    selected = request.args.get("doctor", "").strip()
    doctors = sorted({row.get("doctor") for row in visits if row.get("doctor")})
    interval_minutes, invalid = nn_normalize._merge_intervals(visits)
    result = []
    for doctor in doctors:
        if selected and selected != doctor:
            continue
        d_visits = [row for row in visits if row.get("doctor") == doctor]
        d_primary = [row for row in primaries if row.get("doctor") == doctor]
        d_repeats = [row for row in repeats if row.get("doctor") == doctor]
        turnover = sum(_money(row.get("amount")) for row in d_visits)
        paid_primary = sum(1 for row in d_primary if row.get("paid"))
        hours = interval_minutes.get(doctor, 0) / 60.0
        treatment_counts = {
            kind: sum(1 for row in treatments if doctor in row.get("doctors", {}).get(kind, []))
            for kind in ("evlk", "sclero", "mini")
        }
        row = {
            "doctor": doctor,
            "primary": len(d_primary),
            "primary_paid": paid_primary,
            "repeat_visits": len(d_repeats),
            "treatments": treatment_counts,
            "turnover": turnover,
            "occupied_hours": round(hours, 2),
            "turnover_per_hour": turnover / hours if hours else None,
        }
        if detailed:
            row.update({
                "repeat_people": len({item.get("patient_key") for item in d_repeats}),
                "indications": {
                    kind: sum(1 for item in d_primary if item.get("indications", {}).get(kind))
                    for kind in ("evlk", "sclero", "mini")
                },
                "hosiery_people": len({
                    item.get("patient_key") for item in d_visits
                    if "трикотаж" in (item.get("services_text", "") + " " + item.get("note", "")).lower()
                }),
                "avg_check_all_primary": turnover / len(d_primary) if d_primary else None,
                "avg_check_paid_primary": turnover / paid_primary if paid_primary else None,
                "invalid_intervals": invalid.get(doctor, 0),
                "strict_suspicious": sum(1 for item in suspicious if item.get("doctor") == doctor and item.get("strict")),
                "manual_review": sum(1 for item in suspicious if item.get("doctor") == doctor and not item.get("strict")),
            })
        result.append(row)
    return result


SUSPICIOUS_LEVELS = (
    ("high", "Высокая вероятность несоответствия"),
    ("review", "Требует проверки"),
    ("insufficient", "Недостаточно данных"),
)


def _suspicious_level(row):
    if row.get("treatment"):
        if row.get("repeat"):
            return "high", "Высокая вероятность несоответствия"
        return "review", "Требует проверки"
    return "insufficient", "Недостаточно данных"


def _group_suspicious(rows):
    months = {}
    for row in rows:
        day = row.get("date")
        if not day:
            continue
        month = day[:7]
        month_item = months.setdefault(
            month,
            {"month": month, "count": 0, "days": {}},
        )
        month_item["count"] += 1
        day_item = month_item["days"].setdefault(
            day,
            {"date": day, "count": 0, "rows": []},
        )
        day_item["count"] += 1
        day_item["rows"].append(row)
    result = []
    for month in sorted(months):
        item = months[month]
        item["days"] = [item["days"][day] for day in sorted(item["days"])]
        result.append(item)
    return result


def _suspicious_sections(rows):
    buckets = {key: [] for key, _label in SUSPICIOUS_LEVELS}
    for row in rows:
        key, label = _suspicious_level(row)
        item = dict(row)
        item["review_level"] = key
        item["review_label"] = label
        buckets[key].append(item)
    return [
        {
            "key": key,
            "label": label,
            "count": len(buckets[key]),
            "groups": _group_suspicious(buckets[key]),
        }
        for key, label in SUSPICIOUS_LEVELS
        if buckets[key]
    ]


def _group_count_sum(rows, date_field, amount_field):
    months = {}
    for row in rows:
        day = row.get(date_field)
        if not day:
            continue
        month = day[:7]
        months.setdefault(month, {"month": month, "count": 0, "sum": 0.0, "days": {}})
        months[month]["count"] += 1
        months[month]["sum"] += _money(row.get(amount_field))
        d = months[month]["days"].setdefault(day, {"date": day, "count": 0, "sum": 0.0})
        d["count"] += 1
        d["sum"] += _money(row.get(amount_field))
    result = []
    for month in sorted(months):
        item = months[month]
        item["sum"] = round(item["sum"], 2)
        item["days"] = [item["days"][key] for key in sorted(item["days"])]
        for day in item["days"]:
            day["sum"] = round(day["sum"], 2)
        result.append(item)
    return result


def _primary_repeat_detail(payload, start, end):
    patients = _patient_map(payload)
    unpaid_primaries = [
        row for row in payload.get("primaries", [])
        if _in_range(row.get("date"), start, end)
        and not row.get("paid")
        and row.get("patient_key")
    ]
    primary_by_patient = {
        row["patient_key"]: row
        for row in unpaid_primaries
    }

    repeat_by_patient = defaultdict(list)
    for row in payload.get("repeats", []):
        patient_key = row.get("patient_key")
        if patient_key in primary_by_patient and row.get("date"):
            primary_date = primary_by_patient[patient_key].get("date")
            if primary_date and row["date"] > primary_date:
                repeat_by_patient[patient_key].append(row)

    detail = []
    for patient_key, primary in primary_by_patient.items():
        later = sorted(
            repeat_by_patient.get(patient_key, []),
            key=lambda row: (row.get("date") or "", row.get("doctor") or ""),
        )
        if not later:
            continue
        first_repeat = later[0]
        patient = patients.get(patient_key, {})
        detail.append({
            "patient_key": patient_key,
            "patient_name": patient.get("name", ""),
            "chart": patient.get("chart", ""),
            "phone": patient.get("phone", ""),
            "primary_date": primary.get("date", ""),
            "primary_doctor": primary.get("doctor", ""),
            "first_repeat_date": first_repeat.get("date", ""),
            "first_repeat_doctor": first_repeat.get("doctor", ""),
            "repeat_count": len(later),
        })

    detail.sort(
        key=lambda row: (
            row.get("primary_date") or "",
            row.get("patient_name") or "",
        )
    )
    unpaid_people = len(primary_by_patient)
    continued_count = len(detail)
    return {
        "unpaid_primary_people": unpaid_people,
        "continued_people": continued_count,
        "no_repeat_people": max(0, unpaid_people - continued_count),
        "continued_share": (
            round(continued_count / unpaid_people * 100, 1)
            if unpaid_people else None
        ),
        "rows": detail,
    }


def report_payload(conn, report_key, clinic_id):
    if report_key not in {key for key, _label in REPORTS}:
        raise KeyError(report_key)
    if not nn_upload._active_clinic(conn, clinic_id):
        raise PermissionError("clinic")
    payload = effective_payload(conn, clinic_id)
    if not payload:
        return {"report": report_key, "empty": True, "context": {"years": [], "months_by_year": {}, "doctors": []}}

    start, end, ctx = _bounds(payload)
    doctor = request.args.get("doctor", "").strip()
    result = {
        "report": report_key,
        "empty": False,
        "period": {"start": start, "end": end},
        "context": ctx,
        "selected_doctor": doctor,
    }

    if report_key == "key_metrics":
        result["items"] = _metric_items(payload, start, end)
    elif report_key == "monthly":
        result["items"] = _monthly_items(payload, start, end)
    elif report_key == "doctor_compare":
        result["doctors"] = _doctor_rows(payload, start, end, False)
    elif report_key == "doctors":
        result["doctors"] = _doctor_rows(payload, start, end, True)
    elif report_key == "suspicious":
        patients = _patient_map(payload)
        source_rows = [
            row for row in payload.get("suspicious", [])
            if _in_range(row.get("date"), start, end)
        ]
        if doctor:
            source_rows = [row for row in source_rows if row.get("doctor") == doctor]
        rows = []
        for source in source_rows:
            row = dict(source)
            row["patient_name"] = patients.get(row.get("patient_key"), {}).get("name", "")
            level, label = _suspicious_level(row)
            row["review_level"] = level
            row["review_label"] = label
            rows.append(row)
        result["levels"] = _suspicious_sections(rows)
        result["groups"] = _group_suspicious(rows)
    elif report_key == "primaries":
        tab = request.args.get("tab", "paid")
        rows = [row for row in payload.get("primaries", []) if _in_range(row.get("date"), start, end)]
        rows = [row for row in rows if bool(row.get("paid")) == (tab == "paid")]
        result["tab"] = tab
        result["groups"] = _group_count_sum(rows, "date", "amount")
        result["total"] = {"count": len(rows), "sum": round(sum(_money(row.get("amount")) for row in rows), 2)}
    elif report_key == "treatment":
        rows = [row for row in payload.get("treatments", []) if _in_range(row.get("first_date"), start, end)]
        result["groups"] = _group_count_sum(rows, "first_date", "payment")
        result["total"] = {"count": len(rows), "sum": round(sum(_money(row.get("payment")) for row in rows), 2)}
    elif report_key == "primary_repeat":
        result.update(_primary_repeat_detail(payload, start, end))
    return result


def reports_page():
    conn = db()
    clinics = nn_upload._clinic_rows(conn)
    return render_template(
        "nn_reports.html",
        reports=REPORTS,
        report_labels=dict(REPORTS),
        clinics=clinics,
        csrf=csrf_token(),
    )


def api_report(report_key):
    raw = request.args.get("clinic_id", "").strip()
    try:
        clinic_id = int(raw)
    except ValueError:
        return jsonify(error="Выберите клинику."), 400
    try:
        return jsonify(report_payload(db(), report_key, clinic_id))
    except KeyError:
        return jsonify(error="Неизвестный отчёт."), 404
    except PermissionError:
        return jsonify(error="Клиника недоступна."), 403


def _wb_bytes(title, headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    for column in ws.columns:
        width = min(55, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
        ws.column_dimensions[column[0].column_letter].width = width
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def export_report(report_key):
    if report_key not in {"primaries", "treatment", "primary_repeat"}:
        return jsonify(error="Выгрузка недоступна."), 404
    raw = request.args.get("clinic_id", "").strip()
    try:
        clinic_id = int(raw)
    except ValueError:
        return jsonify(error="Выберите клинику."), 400
    conn = db()
    if not nn_upload._active_clinic(conn, clinic_id):
        return jsonify(error="Клиника недоступна."), 403
    payload = effective_payload(conn, clinic_id)
    if not payload:
        return jsonify(error="Нет обработанных данных."), 404
    start, end, _ctx = _bounds(payload)
    patients = _patient_map(payload)

    if report_key == "primaries":
        tab = request.args.get("tab", "paid")
        rows = [row for row in payload.get("primaries", []) if _in_range(row.get("date"), start, end)]
        rows = [row for row in rows if bool(row.get("paid")) == (tab == "paid")]
        data = []
        for row in rows:
            patient = patients.get(row.get("patient_key"), {})
            indications = row.get("indications", {})
            month_number = int(str(row.get("date"))[5:7]) if row.get("date") else 0
            data.append([
                row.get("date"), MONTH_NAMES_RU.get(month_number, ""), row.get("doctor"), patient.get("name"), patient.get("chart"),
                patient.get("phone"), _money(row.get("amount")), "Да" if row.get("paid") else "Нет",
                "Да" if indications.get("evlk") else "Нет",
                "Да" if indications.get("sclero") else "Нет",
                "Да" if indications.get("mini") else "Нет",
                row.get("note", ""), patient.get("note", ""),
            ])
        headers = ["Дата","Месяц","Врач","Пациент","Амбулаторная карта","Телефон","Оплата первичного","Оплатил","Показания ЭВЛК","Показания склеро","Показания минифлеб","Примечание приёма","Примечание пациента"]
        filename = f"Первичные_{'с_оплатой' if tab == 'paid' else 'без_оплаты'}_{start}_{end}.xlsx"
        buffer = _wb_bytes("Первичные", headers, data)
    elif report_key == "primary_repeat":
        detail = _primary_repeat_detail(payload, start, end)
        data = [
            [
                row.get("patient_name", ""),
                row.get("chart", ""),
                row.get("phone", ""),
                row.get("primary_doctor", ""),
                row.get("primary_date", ""),
                row.get("first_repeat_date", ""),
                row.get("first_repeat_doctor", ""),
                row.get("repeat_count", 0),
            ]
            for row in detail["rows"]
        ]
        headers = [
            "Пациент",
            "Амбулаторная карта",
            "Телефон",
            "Врач первичного",
            "Дата первичного",
            "Первый последующий повторный",
            "Врач повторного",
            "Всего последующих повторных",
        ]
        filename = f"Неоплаченный_первичный_с_повторными_{start}_{end}.xlsx"
        buffer = _wb_bytes("Первичный + повторные", headers, data)
    else:
        rows = [row for row in payload.get("treatments", []) if _in_range(row.get("first_date"), start, end)]
        primary_by_patient = {
            row.get("patient_key"): row
            for row in payload.get("primaries", [])
            if row.get("patient_key")
        }
        data = []
        for row in rows:
            patient = patients.get(row.get("patient_key"), {})
            primary = primary_by_patient.get(row.get("patient_key"), {})
            types = row.get("types", {})
            doctors = row.get("doctors", {})
            data.append([
                patient.get("name"), patient.get("chart"),
                primary.get("date", ""), primary.get("doctor", ""),
                row.get("first_date"),
                "Да" if types.get("evlk") else "Нет",
                "Да" if types.get("sclero") else "Нет",
                "Да" if types.get("mini") else "Нет",
                ", ".join(doctors.get("evlk", [])),
                ", ".join(doctors.get("sclero", [])),
                ", ".join(doctors.get("mini", [])),
                "Да" if row.get("payment_found") else "Нет",
                _money(row.get("payment")),
                row.get("subsequent_repeat_visits", 0),
                row.get("basis", ""),
            ])
        headers = ["Пациент","Амбулаторная карта","Первичный приём","Врач первичного","Первое лечение","ЭВЛК","Склеро","Минифлеб","Врач ЭВЛК","Врач склеро","Врач минифлеб","Оплата лечения найдена","Сумма строк с лечением","Последующих клинических визитов","Основание"]
        filename = f"Лечение_{start}_{end}.xlsx"
        buffer = _wb_bytes("Лечение", headers, data)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
