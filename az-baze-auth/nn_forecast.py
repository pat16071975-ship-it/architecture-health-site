import calendar
from datetime import date

from flask import jsonify, render_template, request

import nn_normalize
import nn_reports
import nn_upload
from app import db


MONTH_NAMES = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)

PRESETS = {"3": 3, "6": 6, "12": 12}


def _money(value):
    return round(float(value or 0), 2)


def _month_key(value):
    text = str(value or "")
    return text[:7] if len(text) >= 7 else ""


def _month_label(value):
    try:
        year, month = [int(part) for part in str(value).split("-", 1)]
    except (TypeError, ValueError):
        return str(value or "")
    return f"{MONTH_NAMES[month - 1]} {year}"


def _next_month(value, offset=1):
    year, month = [int(part) for part in str(value).split("-", 1)]
    index = year * 12 + (month - 1) + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _available_months(payload):
    return sorted({
        _month_key(row.get("date"))
        for row in (payload or {}).get("visits", [])
        if _month_key(row.get("date"))
    })


def _full_months(payload, today=None):
    today = today or date.today()
    current = f"{today.year:04d}-{today.month:02d}"
    return [month for month in _available_months(payload) if month < current]


def _partial_month(payload, today=None):
    today = today or date.today()
    current = f"{today.year:04d}-{today.month:02d}"
    return current if current in _available_months(payload) else None


def _select_months(full_months, preset="3", month_from="", month_to=""):
    if preset == "custom":
        if not full_months:
            return []
        if month_from not in full_months or month_to not in full_months:
            return []
        start = min(full_months.index(month_from), full_months.index(month_to))
        end = max(full_months.index(month_from), full_months.index(month_to))
        return full_months[start:end + 1]
    count = PRESETS.get(str(preset), 3)
    return full_months[-count:]


def _treatment_visit(row):
    text = str(row.get("services_text") or "")
    return bool(
        nn_normalize.EVLK_RE.search(text)
        or nn_normalize.SCLERO_RE.search(text)
        or nn_normalize.MINI_RE.search(text)
    )


def _month_rows(payload, month):
    visits = [
        row for row in payload.get("visits", [])
        if _month_key(row.get("date")) == month
    ]
    primaries = [
        row for row in payload.get("primaries", [])
        if _month_key(row.get("date")) == month
    ]
    repeats = [
        row for row in payload.get("repeats", [])
        if _month_key(row.get("date")) == month
    ]
    treatments = [
        row for row in payload.get("treatments", [])
        if _month_key(row.get("first_date")) == month
    ]
    return visits, primaries, repeats, treatments


def _converted_primary_count(payload, primaries):
    treatment_by_patient = {}
    for treatment in payload.get("treatments", []):
        patient_key = treatment.get("patient_key")
        first_date = treatment.get("first_date") or ""
        if not patient_key or not first_date:
            continue
        current = treatment_by_patient.get(patient_key)
        if current is None or first_date < (current.get("first_date") or ""):
            treatment_by_patient[patient_key] = treatment

    converted = 0
    for primary in primaries:
        treatment = treatment_by_patient.get(primary.get("patient_key"))
        if treatment and (treatment.get("first_date") or "") >= (primary.get("date") or ""):
            converted += 1
    return converted


def _plan_map(conn, clinic_id):
    nn_upload._init_schema()
    rows = conn.execute(
        """
        SELECT year,month,amount
        FROM nn_monthly_plans
        WHERE clinic_id=?
        ORDER BY year,month
        """,
        (clinic_id,),
    ).fetchall()
    return {
        f"{int(row['year']):04d}-{int(row['month']):02d}": float(row["amount"])
        for row in rows
    }


def _summary_for_month(payload, month):
    visits, primaries, repeats, treatments = _month_rows(payload, month)
    revenue = sum(_money(row.get("amount")) for row in visits)
    primary_revenue = sum(_money(row.get("amount")) for row in primaries)
    treatment_revenue = sum(
        _money(row.get("amount")) for row in visits if _treatment_visit(row)
    )
    paid_primary = sum(1 for row in primaries if row.get("paid"))
    converted = _converted_primary_count(payload, primaries)
    treatment_people = len({
        row.get("patient_key")
        for row in treatments
        if row.get("patient_key")
    })
    residual = max(0.0, revenue - primary_revenue - treatment_revenue)
    return {
        "month": month,
        "label": _month_label(month),
        "revenue": round(revenue, 2),
        "visits": len(visits),
        "primaries": len(primaries),
        "primary_unpaid": len(primaries) - paid_primary,
        "primary_paid": paid_primary,
        "repeats": len(repeats),
        "treatment_people": treatment_people,
        "converted_primaries": converted,
        "primary_revenue": round(primary_revenue, 2),
        "treatment_revenue": round(treatment_revenue, 2),
        "residual_revenue": round(residual, 2),
    }


def _baseline(payload, selected_months):
    if not selected_months:
        return None

    rows = [_summary_for_month(payload, month) for month in selected_months]
    n = len(rows)

    total_revenue = sum(row["revenue"] for row in rows)
    total_visits = sum(row["visits"] for row in rows)
    total_primary = sum(row["primaries"] for row in rows)
    total_unpaid = sum(row["primary_unpaid"] for row in rows)
    total_paid = sum(row["primary_paid"] for row in rows)
    total_repeats = sum(row["repeats"] for row in rows)
    total_treatments = sum(row["treatment_people"] for row in rows)
    converted = sum(row["converted_primaries"] for row in rows)
    primary_revenue = sum(row["primary_revenue"] for row in rows)
    treatment_revenue = sum(row["treatment_revenue"] for row in rows)
    residual_revenue = sum(row["residual_revenue"] for row in rows)

    conversion = converted / total_primary if total_primary else 0.0
    repeat_ratio = total_repeats / total_primary if total_primary else 0.0

    return {
        "months": selected_months,
        "period_label": (
            f"{_month_label(selected_months[0])} — {_month_label(selected_months[-1])}"
        ),
        "month_count": n,
        "revenue": round(total_revenue / n, 2),
        "visits": round(total_visits / n, 2),
        "primaries": round(total_primary / n, 2),
        "primary_unpaid": round(total_unpaid / n, 2),
        "repeats": round(total_repeats / n, 2),
        "treatments": round(total_treatments / n, 2),
        "conversion": round(conversion, 6),
        "avg_check_paid": round(total_revenue / total_paid, 2) if total_paid else None,
        "avg_check_all": round(total_revenue / total_primary, 2) if total_primary else None,
        "coefficients": {
            "repeat_per_primary": round(repeat_ratio, 6),
            "primary_revenue": round(primary_revenue / total_primary, 2) if total_primary else 0.0,
            "treatment_revenue": round(treatment_revenue / total_treatments, 2) if total_treatments else 0.0,
            "repeat_revenue": round(residual_revenue / total_repeats, 2) if total_repeats else 0.0,
        },
        "rows": rows,
    }


def forecast_base_data(conn, clinic_id, preset="3", month_from="", month_to="", today=None):
    clinic = nn_upload._active_clinic(conn, clinic_id)
    if not clinic:
        raise PermissionError("clinic")

    payload = nn_reports.effective_payload(conn, clinic_id)
    full_months = _full_months(payload, today=today) if payload else []
    selected = _select_months(
        full_months,
        preset=str(preset or "3"),
        month_from=str(month_from or ""),
        month_to=str(month_to or ""),
    )
    baseline = _baseline(payload, selected) if payload else None

    partial_key = _partial_month(payload, today=today) if payload else None
    partial = _summary_for_month(payload, partial_key) if partial_key else None

    forecast_start = None
    if selected:
        forecast_start = _next_month(selected[-1], 1)

    return {
        "empty": not bool(payload),
        "clinic_id": clinic_id,
        "clinic_name": clinic["name"],
        "preset": str(preset or "3"),
        "available_full_months": [
            {"month": month, "label": _month_label(month)}
            for month in full_months
        ],
        "selected_months": selected,
        "baseline": baseline,
        "partial": partial,
        "forecast_start": forecast_start,
        "plans": _plan_map(conn, clinic_id),
    }


def forecast_page():
    conn = db()
    nn_upload._init_schema()
    return render_template(
        "nn_forecast.html",
        clinics=nn_upload._clinic_rows(conn),
    )


def api_base():
    try:
        clinic_id = int(request.args.get("clinic_id", ""))
    except (TypeError, ValueError):
        return jsonify(error="Укажите клинику."), 400

    preset = request.args.get("preset", "3").strip()
    if preset not in {"3", "6", "12", "custom"}:
        preset = "3"

    try:
        data = forecast_base_data(
            db(),
            clinic_id,
            preset=preset,
            month_from=request.args.get("from", "").strip(),
            month_to=request.args.get("to", "").strip(),
        )
        return jsonify(data)
    except PermissionError:
        return jsonify(error="Клиника недоступна."), 403
