import json
from collections import defaultdict


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _clean_marketing_sources(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for label, raw in value.items():
        try:
            count = int(raw or 0)
        except (TypeError, ValueError):
            continue
        if str(label).strip() and count >= 0:
            result[str(label)] = count
    return result


def _payload(row):
    if not row:
        return {}
    try:
        raw = row["payload"]
    except (KeyError, TypeError, IndexError):
        try:
            raw = row[1]
        except (TypeError, IndexError):
            return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def daily_delta(core, normalized):
    """Return one-day management deltas using only attributed clinical visits.

    The IDENT completed-visits file can contain an overall count that cannot be
    attributed to Dentistry or the Structure department. Such visits are kept
    in control metadata instead of silently inflating management totals.
    """
    items = normalized.get("items", [])
    overall = normalized.get("overall", {})
    doctors = normalized.get("doctors", {})
    lab_invoices = normalized.get("lab_invoices", [])
    data_date = normalized["data_date"]

    staff = defaultdict(float)
    for row in items:
        staff[str(row.get("staff") or "")] += _float(row.get("amount"))

    total_revenue = sum(staff.values())
    lab_revenue = staff.get("Казанцев Л. Е.", 0.0)
    day_counts = overall.get(data_date, {}) or {}
    source_primary = _int(day_counts.get("Первичные"))
    source_repeat = _int(day_counts.get("Повторные")) + _int(day_counts.get("Отконсультированные"))

    dent_primary = structure_primary = dent_repeat = structure_repeat = 0
    day_doctors = doctors.get(data_date, {}) or {}
    for staff_name, value in (day_doctors.get("Первичные", {}) or {}).items():
        if staff_name in core.ident_import.DENTISTS:
            dent_primary += _int(value)
        elif staff_name in core.ident_import.STRUCTURE_DOCTORS:
            structure_primary += _int(value)

    for kind in ("Повторные", "Отконсультированные"):
        for staff_name, value in (day_doctors.get(kind, {}) or {}).items():
            if staff_name in core.ident_import.DENTISTS:
                dent_repeat += _int(value)
            elif staff_name in core.ident_import.STRUCTURE_DOCTORS:
                structure_repeat += _int(value)

    primary = dent_primary + structure_primary
    repeat = dent_repeat + structure_repeat
    lab_orders = len({
        (str(invoice), str(day))
        for invoice, day in lab_invoices
        if str(day) == data_date
    })

    return {
        "factMedicine": round(total_revenue - lab_revenue, 2),
        "factLab": round(lab_revenue, 2),
        "primary": primary,
        "repeat": repeat,
        "dentPrimary": dent_primary,
        "dentRepeat": dent_repeat,
        "clinicPrimary": structure_primary,
        "clinicRepeat": structure_repeat,
        "dentists": {
            full: round(staff.get(short, 0.0), 2)
            for short, full in core.ident_import.DENTISTS.items()
        },
        "clinicDocs": {
            full: round(staff.get(short, 0.0), 2)
            for short, full in core.ident_import.STRUCTURE_DOCTORS.items()
        },
        "labOrders": lab_orders,
        "labRevenue": round(lab_revenue, 2),
        "sourcePrimary": source_primary,
        "sourceRepeat": source_repeat,
        "unassignedPrimary": max(0, source_primary - primary),
        "unassignedRepeat": max(0, source_repeat - repeat),
    }


def _latest_marketing_snapshot(core, month, before_date):
    rows = core.db().execute(
        "SELECT date,payload FROM report_data "
        "WHERE substr(date,1,7)=? AND date < ? ORDER BY date DESC",
        (month, before_date),
    ).fetchall()
    for row in rows:
        value = _payload(row)
        sources = _clean_marketing_sources(value.get("marketingSources"))
        if sources is not None:
            try:
                row_date = row["date"]
            except (KeyError, TypeError, IndexError):
                row_date = row[0]
            return sources, str(value.get("marketingSourcesAsOf") or row_date)
    return None, None


def merge_payload(existing, managed, marketing_sources=None, marketing_as_of=None):
    """Preserve unrelated/manual payload fields while replacing managed fields."""
    record = dict(existing or {})
    record.update(managed)

    own_sources = _clean_marketing_sources(record.get("marketingSources"))
    if own_sources is not None:
        record["marketingSources"] = own_sources
        record["marketingSourcesAsOf"] = str(
            record.get("marketingSourcesAsOf") or managed.get("date") or ""
        )
    elif marketing_sources is not None:
        record["marketingSources"] = dict(marketing_sources)
        record["marketingSourcesAsOf"] = str(marketing_as_of or "")

    return record


def rebuild_management(core, month, source):
    rows = core._active_month_rows(month)
    if not rows:
        return {}

    first_date = rows[0][0]
    baseline = core._latest_prior_report(first_date)
    baseline_dent_primary = _int(baseline.get("dentPrimary"))
    baseline_structure_primary = _int(baseline.get("clinicPrimary"))
    baseline_dent_repeat = _int(baseline.get("dentRepeat"))
    baseline_structure_repeat = _int(baseline.get("clinicRepeat"))
    baseline_primary = baseline_dent_primary + baseline_structure_primary
    baseline_repeat = baseline_dent_repeat + baseline_structure_repeat

    # Old records can pre-date department breakdown. Preserve their total only
    # when there is no department information at all.
    if baseline_primary == 0 and _int(baseline.get("primary")):
        baseline_primary = _int(baseline.get("primary"))
    if baseline_repeat == 0 and _int(baseline.get("repeat")):
        baseline_repeat = _int(baseline.get("repeat"))

    baseline_control = baseline.get("_uploadControl")
    if not isinstance(baseline_control, dict):
        baseline_control = {}

    current = {
        "factMedicine": _float(baseline.get("factMedicine")),
        "factLab": _float(baseline.get("factLab")),
        "primary": baseline_primary,
        "repeat": baseline_repeat,
        "dentPrimary": baseline_dent_primary,
        "dentRepeat": baseline_dent_repeat,
        "clinicPrimary": baseline_structure_primary,
        "clinicRepeat": baseline_structure_repeat,
        "dentists": {
            name: _float((baseline.get("dentists") or {}).get(name))
            for name in core.ident_import.DENTISTS.values()
        },
        "clinicDocs": {
            name: _float((baseline.get("clinicDocs") or {}).get(name))
            for name in core.ident_import.STRUCTURE_DOCTORS.values()
        },
        "labOrders": _int(baseline.get("labOrders")),
        "labRevenue": _float(baseline.get("labRevenue") or baseline.get("factLab")),
        "sourcePrimary": _int(baseline_control.get("sourcePrimary", baseline.get("primary"))),
        "sourceRepeat": _int(baseline_control.get("sourceRepeat", baseline.get("repeat"))),
        "unassignedPrimary": _int(baseline_control.get("unassignedPrimary")),
        "unassignedRepeat": _int(baseline_control.get("unassignedRepeat")),
    }

    marketing_sources, marketing_as_of = _latest_marketing_snapshot(
        core, month, first_date
    )
    rebuilt = {}

    for data_date, normalized in rows:
        delta = daily_delta(core, normalized)
        for key in (
            "factMedicine",
            "factLab",
            "dentPrimary",
            "dentRepeat",
            "clinicPrimary",
            "clinicRepeat",
            "labOrders",
            "labRevenue",
            "sourcePrimary",
            "sourceRepeat",
            "unassignedPrimary",
            "unassignedRepeat",
        ):
            current[key] += delta[key]

        current["primary"] = current["dentPrimary"] + current["clinicPrimary"]
        current["repeat"] = current["dentRepeat"] + current["clinicRepeat"]

        for name, value in delta["dentists"].items():
            current["dentists"][name] = current["dentists"].get(name, 0) + value
        for name, value in delta["clinicDocs"].items():
            current["clinicDocs"][name] = current["clinicDocs"].get(name, 0) + value

        existing = core._load_report(data_date)
        existing_sources = _clean_marketing_sources(existing.get("marketingSources"))
        if existing_sources is not None:
            marketing_sources = existing_sources
            marketing_as_of = str(
                existing.get("marketingSourcesAsOf") or data_date
            )

        managed = {
            "date": data_date,
            "plan": existing.get("plan", ""),
            "factMedicine": round(current["factMedicine"], 2),
            "factLab": round(current["factLab"], 2),
            "pp25": existing.get("pp25", ""),
            "avg25": existing.get("avg25", ""),
            "primary": current["primary"],
            "repeat": current["repeat"],
            "dentPrimary": current["dentPrimary"],
            "dentRepeat": current["dentRepeat"],
            "dentists": {k: round(v, 2) for k, v in current["dentists"].items()},
            "clinicPrimary": current["clinicPrimary"],
            "clinicRepeat": current["clinicRepeat"],
            "clinicDocs": {k: round(v, 2) for k, v in current["clinicDocs"].items()},
            "labOrders": current["labOrders"],
            "labRevenue": round(current["labRevenue"], 2),
            "leadsDent": existing.get("leadsDent", ""),
            "leadsDentLost": existing.get("leadsDentLost", ""),
            "leadsClinic": existing.get("leadsClinic", ""),
            "leadsReserve": existing.get("leadsReserve", ""),
            "_source": source,
            "_aggregation": "month_to_date",
            "_uploadControl": {
                "sourcePrimary": current["sourcePrimary"],
                "sourceRepeat": current["sourceRepeat"],
                "reportedPrimary": current["primary"],
                "reportedRepeat": current["repeat"],
                "unassignedPrimary": current["unassignedPrimary"],
                "unassignedRepeat": current["unassignedRepeat"],
            },
        }
        rebuilt[data_date] = merge_payload(
            existing,
            managed,
            marketing_sources=marketing_sources,
            marketing_as_of=marketing_as_of,
        )

        current_sources = _clean_marketing_sources(
            rebuilt[data_date].get("marketingSources")
        )
        if current_sources is not None:
            marketing_sources = current_sources
            marketing_as_of = str(
                rebuilt[data_date].get("marketingSourcesAsOf") or data_date
            )

    return rebuilt


def install(core):
    """Install the common upload-integrity contract before routes are registered."""
    if getattr(core, "_az_upload_integrity_installed", False):
        return
    core._daily_delta = lambda normalized: daily_delta(core, normalized)
    core._rebuild_management = lambda month, source: rebuild_management(
        core, month, source
    )
    core._az_upload_integrity_installed = True
