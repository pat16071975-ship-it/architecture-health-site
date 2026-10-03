import calendar
from datetime import date

from flask import g, jsonify, render_template, request

import nn_reports
import nn_upload
from app import csrf_token, db, iso_now, require_csrf


MONTH_NAMES = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)

RANGES = {
    "q1": (1, 3),
    "q2": (4, 6),
    "q3": (7, 9),
    "q4": (10, 12),
    "h1": (1, 6),
    "h2": (7, 12),
    "year": (1, 12),
}


def _money(value):
    return round(float(value or 0), 2)


def _safe_date(value):
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def _short_doctor(value):
    parts = [part for part in str(value or "").strip().split() if part]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0]
    initials = "".join(f"{part[0].upper()}." for part in parts[1:] if part)
    return f"{parts[0]} {initials}".strip()


def _plan_map(conn, clinic_id, year):
    nn_upload._init_schema()
    rows = conn.execute(
        """
        SELECT month,amount
        FROM nn_monthly_plans
        WHERE clinic_id=? AND year=?
        ORDER BY month
        """,
        (clinic_id, year),
    ).fetchall()
    return {int(row["month"]): float(row["amount"]) for row in rows}


def _range_months(range_key, selected_month):
    if range_key == "ytd":
        return list(range(1, selected_month + 1))
    start, end = RANGES.get(range_key, (1, selected_month))
    return list(range(start, end + 1))


def _clamped_target(year, month, day):
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def _month_snapshot(payload, year, month, selected_day):
    target = _clamped_target(year, month, selected_day)
    start = date(year, month, 1).isoformat()
    end = target.isoformat()
    visits = []
    primaries = []
    repeats = []
    if payload:
        visits = [
            row for row in payload.get("visits", [])
            if start <= (row.get("date") or "") <= end
        ]
        primaries = [
            row for row in payload.get("primaries", [])
            if start <= (row.get("date") or "") <= end
        ]
        repeats = [
            row for row in payload.get("repeats", [])
            if start <= (row.get("date") or "") <= end
        ]

    has_data = bool(visits or primaries or repeats)
    if not has_data:
        return {
            "target": target.isoformat(),
            "has_data": False,
            "fact": None,
            "primary": None,
            "primary_unpaid": None,
            "repeat": None,
            "pp_day": None,
            "avg_paid": None,
            "avg_all": None,
            "doctors": {},
        }

    fact = round(sum(_money(row.get("amount")) for row in visits), 2)
    paid_primary = sum(1 for row in primaries if row.get("paid"))
    primary_total = len(primaries)
    doctors = {}
    for row in visits:
        doctor = str(row.get("doctor") or "").strip()
        if not doctor:
            continue
        doctors[doctor] = round(doctors.get(doctor, 0.0) + _money(row.get("amount")), 2)

    return {
        "target": target.isoformat(),
        "has_data": True,
        "fact": fact,
        "primary": primary_total,
        "primary_unpaid": primary_total - paid_primary,
        "repeat": len(repeats),
        "pp_day": round(primary_total / target.day, 2) if target.day else None,
        "avg_paid": round(fact / paid_primary, 2) if paid_primary else None,
        "avg_all": round(fact / primary_total, 2) if primary_total else None,
        "doctors": doctors,
    }


def management_compare_data(conn, clinic_id, selected_date=None, range_key="year", view="compare"):
    clinic = nn_upload._active_clinic(conn, clinic_id)
    if not clinic:
        raise PermissionError("clinic")

    payload = nn_reports.effective_payload(conn, clinic_id)
    context = nn_reports._context(payload) if payload else {
        "earliest_date": None,
        "latest_date": None,
        "years": [],
        "months_by_year": {},
        "doctors": [],
    }

    selected = _safe_date(selected_date)
    if not selected:
        selected = _safe_date(context.get("latest_date")) or date.today()

    year = selected.year
    if view == "single":
        month_numbers = [selected.month]
    else:
        month_numbers = _range_months(range_key, selected.month)

    plans = _plan_map(conn, clinic_id, year)
    months = []
    doctor_names = set()

    for month in month_numbers:
        snap = _month_snapshot(payload, year, month, selected.day)
        plan = plans.get(month)
        due = None
        execution = None
        if plan is not None:
            target_day = date.fromisoformat(snap["target"]).day
            due = round(plan * target_day / calendar.monthrange(year, month)[1], 2)
            if snap["fact"] is not None and plan:
                execution = round(snap["fact"] / plan * 100, 1)
        doctor_names.update(snap["doctors"].keys())
        months.append({
            "month": month,
            "label": MONTH_NAMES[month - 1],
            "target": snap["target"],
            "has_data": snap["has_data"],
            "plan": plan,
            "due": due,
            "fact": snap["fact"],
            "execution": execution,
            "primary": snap["primary"],
            "primary_unpaid": snap["primary_unpaid"],
            "repeat": snap["repeat"],
            "pp_day": snap["pp_day"],
            "avg_paid": snap["avg_paid"],
            "avg_all": snap["avg_all"],
            "doctors": snap["doctors"],
        })

    doctor_rows = []
    for name in sorted(doctor_names, key=lambda value: value.casefold()):
        doctor_rows.append({
            "name": name,
            "label": _short_doctor(name),
            "values": [month["doctors"].get(name) if month["has_data"] else None for month in months],
        })

    return {
        "empty": not payload,
        "clinic_id": clinic_id,
        "clinic_name": clinic["name"],
        "selected_date": selected.isoformat(),
        "year": year,
        "view": view,
        "range": range_key,
        "months": months,
        "doctors": doctor_rows,
        "context": context,
    }


def management_page():
    conn = db()
    nn_upload._init_schema()
    clinics = nn_upload._clinic_rows(conn)
    return render_template(
        "nn_management.html",
        clinics=clinics,
    )


def api_compare():
    try:
        clinic_id = int(request.args.get("clinic_id", ""))
    except (TypeError, ValueError):
        return jsonify(error="Укажите клинику."), 400

    view = request.args.get("view", "compare").strip()
    if view not in {"compare", "single"}:
        view = "compare"
    range_key = request.args.get("range", "year").strip()
    if range_key not in {"ytd", *RANGES.keys()}:
        range_key = "year"

    try:
        data = management_compare_data(
            db(),
            clinic_id,
            selected_date=request.args.get("date", "").strip(),
            range_key=range_key,
            view=view,
        )
        return jsonify(data)
    except PermissionError:
        return jsonify(error="Клиника недоступна."), 403


def _parse_plan_amount(value):
    text = str(value or "").replace("\xa0", " ").strip()
    if not text:
        return None
    text = text.replace(" ", "").replace(",", ".")
    try:
        amount = float(text)
    except ValueError as exc:
        raise ValueError("План должен быть числом.") from exc
    if amount < 0:
        raise ValueError("План не может быть отрицательным.")
    return round(amount, 2)


def plan_page():
    nn_upload._init_schema()
    conn = db()
    clinics = nn_upload._clinic_rows(conn)
    error = None
    result = None

    raw_clinic = request.form.get("clinic_id") if request.method == "POST" else request.args.get("clinic_id")
    try:
        clinic_id = int(raw_clinic) if raw_clinic else (int(clinics[0]["id"]) if clinics else None)
    except (TypeError, ValueError):
        clinic_id = None
    clinic = nn_upload._active_clinic(conn, clinic_id) if clinic_id else None

    raw_year = request.form.get("year") if request.method == "POST" else request.args.get("year")
    try:
        year = int(raw_year) if raw_year else date.today().year
    except (TypeError, ValueError):
        year = date.today().year
    if year < 2000 or year > 2100:
        year = date.today().year

    if clinic_id and not clinic:
        error = "Выбранная клиника не найдена или отключена."

    if request.method == "POST":
        require_csrf()
        if not clinic:
            error = error or "Сначала выберите клинику."
        else:
            try:
                values = {
                    month: _parse_plan_amount(request.form.get(f"plan_{month}", ""))
                    for month in range(1, 13)
                }
                now = iso_now()
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for month, amount in values.items():
                        if amount is None:
                            conn.execute(
                                "DELETE FROM nn_monthly_plans WHERE clinic_id=? AND year=? AND month=?",
                                (clinic_id, year, month),
                            )
                        else:
                            conn.execute(
                                """
                                INSERT INTO nn_monthly_plans(
                                    clinic_id,year,month,amount,updated_by,updated_at
                                ) VALUES(?,?,?,?,?,?)
                                ON CONFLICT(clinic_id,year,month) DO UPDATE SET
                                    amount=excluded.amount,
                                    updated_by=excluded.updated_by,
                                    updated_at=excluded.updated_at
                                """,
                                (clinic_id, year, month, amount, int(g.user["id"]), now),
                            )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                result = "План сохранён."
            except ValueError as exc:
                error = str(exc)

    plans = _plan_map(conn, clinic_id, year) if clinic else {}
    return render_template(
        "nn_plan.html",
        clinics=clinics,
        selected_clinic=clinic,
        year=year,
        month_names=MONTH_NAMES,
        plans=plans,
        error=error,
        result=result,
        csrf=csrf_token(),
    )
