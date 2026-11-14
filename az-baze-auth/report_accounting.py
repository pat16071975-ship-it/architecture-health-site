"""Read-only accounting coverage shared by Finrez, dashboard and forecast.

Known expenses are useful even when incomplete. Profit requires full expense
coverage and a closed payroll month; missing payroll must never become zero.
"""
import calendar
import math
import re


def number(value):
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def months(expenses, economics, management):
    trusted = economics.get("available") and economics.get("control", {}).get("status") == "OK"
    payroll = economics.get("payroll", {}) if trusted else {}
    closed = payroll.get("period", economics.get("payroll_period", economics.get("period", []))) if trusted else []
    partial = payroll.get("partial_period", economics.get("payroll_partial_period", []))
    keys = set(management) | set(expenses.get("months", {})) | set(closed) | set(partial)
    result = {}
    for month in sorted(keys):
        if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", month):
            continue
        year, mo = map(int, month.split("-"))
        end = f"{month}-{calendar.monthrange(year, mo)[1]:02}"
        month_expenses = expenses.get("months", {}).get(month)
        operating = month_expenses.get("operating") if isinstance(month_expenses, dict) else None
        # Validate the source month BEFORE accrual overlays. A global date range
        # or known payroll cannot establish coverage for missing other costs.
        source_categories = {
            name: number(row.get("total")) if isinstance(row, dict) else None
            for name, row in (operating.items() if isinstance(operating, dict) else [])
        }
        month_complete = bool(
            source_categories
            and any(name not in ("ФОТ", "Налоги и взносы на ФОТ",
                                 "Оборудование, мебель и обновление")
                    for name in source_categories)
            and all(value is not None for value in source_categories.values())
        )
        categories = dict(source_categories)
        fot = number(payroll.get("total_by_month", {}).get(month))
        payroll_complete = month in closed and month not in partial and fot is not None
        categories["ФОТ"] = fot if payroll_complete else None
        if trusted and month in economics.get("period", []):
            taxes = [line for line in economics.get("opu", {}).get("expense_lines", [])
                     if line.get("category") == "Налоги и взносы на ФОТ"]
            if taxes:
                categories["Налоги и взносы на ФОТ"] = sum(number(line.get("months", {}).get(month)) or 0 for line in taxes)
        period = expenses.get("period", {})
        expenses_complete = bool(expenses.get("available") and month_complete and
                                 period.get("from", "9999") <= month + "-01" and
                                 period.get("to", "") >= end)
        known = [value for name, value in categories.items()
                 if value is not None and name != "Оборудование, мебель и обновление"]
        result[month] = {
            "categories": categories, "payroll": fot if payroll_complete else None,
            "payrollComplete": payroll_complete, "expensesComplete": expenses_complete,
            "complete": payroll_complete and expenses_complete,
            "knownExpenses": sum(known) if known else None, "through": end,
        }
    return result
