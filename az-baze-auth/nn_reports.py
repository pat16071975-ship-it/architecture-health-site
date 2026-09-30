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
        JOIN clinics cl ON cl.id=n.clinic_id
        WHERE n.clinic_id=? AND b.status='ready' AND cl.status='active'
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


def _date_owned(batch_index, value, periods):
    if not value:
        return False
    for later in range(batch_index + 1, len(periods)):
        start, end = periods[later]
        if start and end and start <= value <= end:
            return False
    return True


def effective_payload(conn, clinic_id):
    rows = _batch_rows(conn, clinic_id)
    if not rows:
        return None
    payloads = [_safe_json(row["payload_json"]) for row in rows]
    periods = [(row["period_start"], row["period_end"]) for row in rows]

    master = []
    visits = []
    medical = []
    deleted = []
    control_services = []

    for index, payload in enumerate(payloads):
        for patient in payload.get("patients", []):
            master.append({
                "name": patient.get("name", ""),
                "dob": patient.get("dob"),
                "iin": patient.get("iin", ""),
                "chart": patient.get("chart", ""),
                "phone": patient.get("phone", ""),
                "note": patient.get("note", ""),
                "source_amount": 0,
            })
        for item in payload.get("visits", []):
            if _date_owned(index, item.get("date"), periods):
                visits.append(dict(item))
        for item in payload.get("medical_records", []):
            if _date_owned(index, item.get("date"), periods):
                medical.append(dict(item))
        for item in payload.get("deleted_appointments", []):
            if _date_owned(index, item.get("appointment_date"), periods):
                deleted.append(dict(item))

        # Aggregate service controls are useful for reconciliation only.
        # Keep only the newest batch's control rows when histories overlap.
        if index == len(payloads) - 1:
            control_services = [dict(item) for item in payload.get("service_control_rows", [])]

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
    return nn_normalize.normalize_rows(master, visits, medical, control_services, deleted, len(deleted))


def _context(payload):
    dates = sorted({row.get("date") for row in payload.get("visits", []) if row.get("date")})
    years = sorted({int(value[:4]) for value in dates}, reverse=True)
    months_by_year = {}
    for year in years:
        months_by_year[str(year)] = sorted(
            {int(value[5:7]) for value in dates if value.startswith(f"{year:04d}-")}
        )
    doctors = sorted({row.get("doctor") for row in payload.get("visits", []) if row.get("doctor")})
    latest = dates[-1] if dates else None
    return {
        "years": years,
        "months_by_year": months_by_year,
        "doctors": doctors,
        "latest_date": latest,
    }


def _bounds(payload):
    ctx = _context(payload)
    latest = ctx["latest_date"]
    if not latest:
        return None, None, ctx

    raw_from = request.args.get("date_from", "").strip()
    raw_to = request.args.get("date_to", "").strip()
    if raw_from or raw_to:
        return raw_from or payload["period"]["start"], raw_to or payload["period"]["end"], ctx

    try:
        year = int(request.args.get("year") or latest[:4])
    except ValueError:
        year = int(latest[:4])
    try:
        month = int(request.args.get("month") or latest[5:7])
    except ValueError:
        month = int(latest[5:7])
    month = min(12, max(1, month))
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"
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
    repeat_people = {row.get("patient_key") for row in repeats if row.get("patient_key")}
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
        ("Уникальных повторных пациентов", len(repeat_people), "count"),
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
        rows = [row for row in payload.get("suspicious", []) if _in_range(row.get("date"), start, end)]
        if doctor:
            rows = [row for row in rows if row.get("doctor") == doctor]
        for row in rows:
            row["patient_name"] = patients.get(row.get("patient_key"), {}).get("name", "")
        result["rows"] = rows
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
        primaries = [row for row in payload.get("primaries", []) if _in_range(row.get("date"), start, end)]
        repeats = [row for row in payload.get("repeats", []) if _in_range(row.get("date"), start, end)]
        result["primary_people"] = len({row.get("patient_key") for row in primaries})
        result["repeat_people"] = len({row.get("patient_key") for row in repeats})
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
    if report_key not in {"primaries", "treatment"}:
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
            data.append([
                row.get("date"), row.get("doctor"), patient.get("name"), patient.get("chart"),
                patient.get("phone"), _money(row.get("amount")), "Да" if row.get("paid") else "Нет",
                "Да" if indications.get("evlk") else "Нет",
                "Да" if indications.get("sclero") else "Нет",
                "Да" if indications.get("mini") else "Нет",
                row.get("note", ""), patient.get("note", ""),
            ])
        headers = ["Дата","Врач","Пациент","Амбулаторная карта","Телефон","Оплата первичного","Оплатил","Показания ЭВЛК","Показания склеро","Показания минифлеб","Примечание приёма","Примечание пациента"]
        filename = f"Первичные_{'с_оплатой' if tab == 'paid' else 'без_оплаты'}_{start}_{end}.xlsx"
        buffer = _wb_bytes("Первичные", headers, data)
    else:
        rows = [row for row in payload.get("treatments", []) if _in_range(row.get("first_date"), start, end)]
        data = []
        for row in rows:
            patient = patients.get(row.get("patient_key"), {})
            types = row.get("types", {})
            doctors = row.get("doctors", {})
            data.append([
                patient.get("name"), patient.get("chart"), row.get("first_date"),
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
        headers = ["Пациент","Амбулаторная карта","Первое лечение","ЭВЛК","Склеро","Минифлеб","Врач ЭВЛК","Врач склеро","Врач минифлеб","Оплата лечения найдена","Сумма строк с лечением","Последующих клинических визитов","Основание"]
        filename = f"Лечение_{start}_{end}.xlsx"
        buffer = _wb_bytes("Лечение", headers, data)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
