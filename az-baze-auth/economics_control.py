import hashlib
import json
import re
from pathlib import Path

from flask import Response, g, jsonify, request

from app import SITE_ROOT, admin_required, audit, csrf_token, db, iso_now, permission_required, require_csrf

DATA_PATH = Path("/var/lib/az-baze/economics-control.json")
PERIOD = [f"2026-{m:02d}" for m in range(1, 7)]
MONTHS_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь"]
OPU_COLS = [21, 23, 25, 27, 29, 31]
PAYROLL_COLS = [14, 15, 16, 17, 18, 19]


def _text(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _dict_series(values):
    return {PERIOD[i]: _num(values[i]) for i in range(6)}


def _norm_name(v):
    return re.sub(r"\s+", " ", _text(v).replace("(с)", "").replace("(c)", "")).strip()


def _employee_id(name):
    return hashlib.sha256(_norm_name(name).lower().encode("utf-8")).hexdigest()[:16]


def _find_row(ws, label, start=1):
    target = _text(label).lower()
    max_row = ws.max_row or 500
    for row in range(start, max_row + 1):
        if _text(ws.cell(row, 4).value).lower() == target:
            return row
    return None


def _row_series(ws, label, start=1):
    row = _find_row(ws, label, start)
    if not row:
        return {m: 0 for m in PERIOD}
    return _dict_series([ws.cell(row, c).value for c in OPU_COLS])


def _expense_category(label):
    s = label.lower()
    if "ассист" in s or "цсо" in s or "администра" in s or "мед.сестр" in s or "убор" in s or "заведующ" in s:
        return "ФОТ"
    if "ндфл" in s or "соц.взнос" in s:
        return "Налоги и взносы на ФОТ"
    if "аренд" in s:
        return "Аренда"
    if "коммун" in s or "содержание помещений" in s:
        return "Коммунальные и содержание помещений"
    if any(x in s for x in ("материал", "лаборатор", "утилизац", "спецодеж", "санитар", "томограф", "стоматологические принадлежности")):
        return "Медицинские расходы"
    if any(x in s for x in ("маркет", "smm", "полиграф", "реклам", "фотограф", "дизайнер")):
        return "Маркетинг и реклама"
    if any(x in s for x in ("программ", "обслуживание пк", "связ", "интернет")):
        return "Связь, интернет, программы и IT"
    if any(x in s for x in ("общехозяй", "достав", "малоценного", "мебел", "подотчет")):
        return "Хозяйственные и офисные расходы"
    if "ремонт" in s or "оборудован" in s:
        return "Ремонт и обслуживание оборудования"
    if any(x in s for x in ("обучен", "командиров")):
        return "Обучение и командировки"
    if any(x in s for x in ("управленческий учет", "консалт", "бухгалтер", "юрист", "охрана труда", "поиск персонала", "мед.осмотр")):
        return "Профессиональные и административные услуги"
    if "банк" in s or "эквайр" in s or "сбп" in s:
        return "Банковские услуги"
    return "Прочие операционные / разовые расходы"


def _parse_opu(ws):
    revenue_net = _row_series(ws, "ВЫРУЧКА со скидкой, руб.", 38)
    operating_profit = _row_series(ws, "ОПЕРАЦИОННАЯ ПРИБЫЛЬ", 200)
    net_profit = _row_series(ws, "ЧИСТАЯ ПРИБЫЛЬ", 200)
    materials = _row_series(ws, "Материалы", 137)
    assistant_salary = _row_series(ws, "Ассистенты стоматологов", 137)
    hygiene_fot = _row_series(ws, "Гигиенист", 79)
    direct_expenses = _row_series(ws, "ПРЯМЫЕ РАСХОДЫ", 137)
    indirect_expenses = _row_series(ws, "КОСВЕННЫЕ РАСХОДЫ", 157)

    # Only leaf rows: subtotal rows are intentionally excluded to prevent double counting.
    leaf_rows = list(range(139, 156)) + list(range(159, 165)) + list(range(166, 206))
    skip = {141, 157, 158, 165, 169, 174, 179, 190, 194, 200}
    expense_lines = []
    for row in leaf_rows:
        if row in skip:
            continue
        name = _text(ws.cell(row, 4).value)
        if not name:
            continue
        months = _dict_series([ws.cell(row, c).value for c in OPU_COLS])
        total = sum(months.values())
        if abs(total) < 0.001:
            continue
        expense_lines.append({"source_name": name, "normalized_name": name.lower(), "category": _expense_category(name), "source": "ОПиУ", "months": months, "total": total})

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
    }


def _group_for_row(row):
    if row <= 44: return "Врачи"
    if row <= 90: return "Вспомогательный персонал"
    if row <= 103: return "АУП"
    if row <= 113: return "Администраторы"
    if row <= 118: return "Лаборатория"
    return "Штатный маркетинг"


def _function_for(role, group):
    r = role.lower()
    if group == "АУП":
        if "главн" in r and "врач" in r: return "Главный врач"
        if "hr" in r or "кадр" in r: return "HR / кадры"
        if "бухгалтер" in r or "платеж" in r: return "Бухгалтерия / финансы"
        if "маркет" in r or "smm" in r: return "Маркетинг / SMM"
        if "ассистент" in r: return "Бизнес-ассистенты"
        return "Руководство / управление"
    if group == "Администраторы": return "Администраторы / клиентский сервис"
    if group == "Лаборатория": return "Зуботехническая лаборатория"
    if group == "Штатный маркетинг": return "Маркетинг / SMM"
    if group == "Вспомогательный персонал":
        if "ассист" in r or "стаж" in r: return "Ассистенты врачей"
        if "цсо" in r: return "ЦСО"
        if "убор" in r: return "Уборка"
        if "рентген" in r or "diers" in r or "иггт" in r: return "Диагностика"
        if "мед" in r or "сестр" in r: return "Медсёстры"
        return "Вспомогательный персонал"
    return role or group


def _parse_payroll(ws):
    subtotal_names = {"врачебный персонал", "отделение терапии", "отделение структуры", "отделение ортопедии", "отделение хирургии", "миофункциональные терапевты", "средний и младший персонал", "цсо", "административный персонал", "администраторы", "зуботехническая лаборатория", "маркетинговый блок", "рентгенолаборант", "оператор иггт", "ассистент стоматолога", "уборщик служебных помещений"}
    employees, payments, seen = [], [], set()
    max_row = ws.max_row or 300
    for row in range(6, max_row + 1):
        name, role = _norm_name(ws.cell(row, 5).value), _text(ws.cell(row, 4).value)
        if not name or not role or name.lower() in subtotal_names or name.lower() == role.lower():
            continue
        vals = [_num(ws.cell(row, c).value) for c in PAYROLL_COLS]
        if not any(abs(v) > 0.0001 for v in vals):
            continue
        eid, group, function = _employee_id(name), _group_for_row(row), _function_for(role, _group_for_row(row))
        months = _dict_series(vals)
        key = (eid, role, tuple(round(v, 2) for v in vals))
        if key in seen:
            continue
        seen.add(key)
        employee = {"employee_id": eid, "name": name, "role": role, "group": group, "function": function, "department": _text(ws.cell(row, 3).value) or function, "months": months, "total": sum(vals)}
        employees.append(employee)
        for month, amount in months.items():
            if abs(amount) < 0.0001: continue
            payment_id = hashlib.sha256(f"{eid}|{role}|{month}|{amount:.2f}|{row}".encode()).hexdigest()[:20]
            payments.append({"payment_id": payment_id, "employee_id": eid, "role": role, "department": employee["department"], "month": month, "amount": amount, "source": "Свод по ЗП new"})

    total_by_month = {m: sum(e["months"][m] for e in employees) for m in PERIOD}
    groups = {}
    for group in ["Врачи", "АУП", "Администраторы", "Вспомогательный персонал", "Лаборатория", "Штатный маркетинг"]:
        arr = [e for e in employees if e["group"] == group]
        groups[group] = {"total": sum(e["total"] for e in arr), "months": {m: sum(e["months"][m] for e in arr) for m in PERIOD}}
    return {"employees": employees, "payments": payments, "groups": groups, "total_by_month": total_by_month, "total": sum(total_by_month.values())}


def _ident_revenue():
    result = {}
    for month in PERIOD:
        row = db().execute("SELECT payload FROM report_data WHERE date LIKE ? ORDER BY date DESC LIMIT 1", (month + "-%",)).fetchone()
        if not row:
            result[month] = None
            continue
        try:
            rec = json.loads(row["payload"])
            result[month] = _num(rec.get("factMedicine")) + _num(rec.get("factLab", rec.get("labRevenue")))
        except Exception:
            result[month] = None
    return result


def _build_payload(wb):
    if not {"ОПУ", "Свод по ЗП new"}.issubset(set(wb.sheetnames)):
        raise ValueError("В файле нет листов ОПУ и/или Свод по ЗП new")
    opu, payroll = _parse_opu(wb["ОПУ"]), _parse_payroll(wb["Свод по ЗП new"])
    ident = _ident_revenue()
    revenue_control = []
    for month in PERIOD:
        opu_v, ident_v = opu["revenue_net"][month], ident.get(month)
        if ident_v is None:
            diff = diff_pct = None
            status = "Нет данных IDENT"
        else:
            diff = ident_v - opu_v
            diff_pct = abs(diff) / opu_v if opu_v else None
            status = "ОК" if diff_pct is not None and diff_pct <= 0.01 else ("Требует сверки" if diff_pct is not None and diff_pct <= 0.03 else "Критическое расхождение")
        revenue_control.append({"month": month, "opu_net": opu_v, "ident_net": ident_v, "difference": diff, "difference_pct": diff_pct, "status": status})

    hygiene_missing = [m for m in PERIOD if opu["hygiene_fot"][m] <= 0]
    data_quality = {
        "Гигиена": {"status": "missing_fot" if hygiene_missing else "complete", "message": "ФОТ гигиенистов отсутствует/не подтверждён: " + ", ".join(hygiene_missing) + ". Маржинальность за эти месяцы не считается подтверждённой." if hygiene_missing else "Данные ФОТ подтверждены."},
        "2025_2026_patients": {"status": "partial", "message": "Методика учёта пациентов 2025 и 2026 различается. Межгодовой ряд требует предупреждения или нормализации."},
        "marketing": {"status": "partial", "message": "Сквозная атрибуция источник → пациент → оплата пока отсутствует. CAC/ROMI не считаются подтверждёнными."},
        "laboratory": {"status": "partial", "message": "Лаборатория должна оцениваться отдельным P&L; персональная выручка техника не используется как критерий эффективности."},
    }
    return {
        "version": 2, "available": True, "source": "ОПУ + Свод по ЗП new + IDENT", "generated_at": iso_now(), "period": PERIOD,
        "opu": opu, "payroll": payroll, "revenue_control": revenue_control, "data_quality": data_quality,
        "notes": {"distributed_profit": "Показатель не является фактической прибылью врача. До появления прямых затрат врача используем только расчётно распределённую прибыль.", "potential_revenue": "Потерянные часы × фактическая выручка за проведённый час — только оценочная потенциальная выручка, не фактическая потеря."},
        "layers": {"raw": ["ОПУ", "Свод по ЗП new", "IDENT"], "normalized": ["сотрудники / employee_id", "роли", "статьи расходов по названию", "направления", "источники выручки"], "calculated": ["ФОТ/выручка", "операционная и чистая рентабельность", "контроль источников", "качество данных"]},
        "control": {"payroll_balanced": abs(payroll["total"] - sum(v["total"] for v in payroll["groups"].values())) < 1, "materials_jan_jun": sum(opu["materials"].values()), "assistants_jan_jun": sum(opu["assistant_salary"].values())},
    }


def _load_data():
    if not DATA_PATH.exists():
        return {"version": 2, "available": False}
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    data["available"] = True
    return data


def register_economics_control(app):
    @app.get("/reports/dashboard.html")
    @permission_required("reports")
    def economics_dashboard_page():
        return Response((SITE_ROOT / "reports" / "dashboard.html").read_text(encoding="utf-8"), mimetype="text/html")

    @app.route("/reports/economics-import/", methods=["GET", "POST"])
    @admin_required
    def economics_import():
        message, error = "", ""
        if request.method == "POST":
            try:
                require_csrf()
                from openpyxl import load_workbook
                f = request.files.get("file")
                if not f or not f.filename.lower().endswith(".xlsx"):
                    raise ValueError("Выберите исходный файл .xlsx")
                payload = _build_payload(load_workbook(f.stream, data_only=True, read_only=True))
                DATA_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                DATA_PATH.chmod(0o640)
                audit("economics_source_imported", target_user_id=g.user["id"], details=f"file={f.filename}; payroll={payload['payroll']['total']:.2f}")
                message = "Экономические данные пересчитаны из первичного файла."
            except Exception as exc:
                error = str(exc)
        html = (SITE_ROOT / "reports" / "economics-import.html").read_text(encoding="utf-8")
        html = html.replace("{{MESSAGE}}", (f'<div class="ok">{message}</div>' if message else "")).replace("{{ERROR}}", error).replace("{{CSRF}}", csrf_token())
        return Response(html, mimetype="text/html")

    @app.get("/api/reports/economics-control")
    @permission_required("reports")
    def economics_control_data():
        try:
            return jsonify(data=_load_data(), error="", csrf=csrf_token())
        except Exception as exc:
            return jsonify(data={"version": 2, "available": False}, error=str(exc), csrf=csrf_token())
