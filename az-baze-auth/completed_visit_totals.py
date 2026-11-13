"""Authoritative completed-visit counters shared by AZ management and advertising.

Only the completed-visits layer may determine primary/repeat counts.
Payment/cash/services records are NEVER read here. Missing evidence stays
missing; an unassigned doctor does not erase an overall completed visit.
"""
import json
import re
import sqlite3

DATE = re.compile(r"^20\d\d-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$")
PRIMARY = "Первичные"
REPEAT = ("Повторные", "Отконсультированные")


def _count(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    text = str(value).strip() if value is not None else ""
    return int(text) if text.isdecimal() else None


def day_counts(normalized, day):
    """Return verified (primary, repeat), or None for services-only days.

    The completed XLSX overall counts are authoritative when present.
    Detailed visits are a fallback for historical completed-only records,
    not a substitute for an overall total with unassigned doctor/patient.
    """
    if not isinstance(normalized, dict) or not DATE.fullmatch(str(day)):
        return None
    overall = normalized.get("overall")
    raw = overall.get(day) if isinstance(overall, dict) else None
    fields = (PRIMARY, *REPEAT)
    if isinstance(raw, dict) and any(name in raw for name in fields):
        counts = [_count(raw.get(name, 0)) for name in fields]
        return tuple((counts[0], counts[1] + counts[2])) if all(
            count is not None for count in counts
        ) else None
    visits = normalized.get("visits")
    if not isinstance(visits, list):
        return None
    relevant = [
        str(row.get("kind") or "")
        for row in visits if isinstance(row, dict)
        and str(row.get("date") or day) == day
        and str(row.get("kind") or "") in fields
    ]
    if not relevant:
        return None
    return (
        sum(kind == PRIMARY for kind in relevant),
        sum(kind in REPEAT for kind in relevant),
    )


def month_to_date(conn, month):
    """Confirmed cumulative counts keyed by completed evidence day.

    Skips services-only dates and malformed JSON. Never reads or trusts
    report_data and never guesses missing days or patient classifications.
    Does not commit, insert, update or run schema migrations.
    """
    if not re.fullmatch(r"20\d\d-(?:0[1-9]|1[0-2])", str(month)):
        raise ValueError("invalid completed visit month")
    try:
        rows = conn.execute(
            "SELECT data_date,normalized_json FROM daily_uploads "
            "WHERE substr(data_date,1,7)=? ORDER BY data_date",
            (month,),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return {}
        raise
    totals = [0, 0]
    confirmed = {}
    for row in rows:
        day = str(row["data_date"])
        if not DATE.fullmatch(day) or day[:7] != month:
            continue
        try:
            normalized = json.loads(row["normalized_json"])
        except (TypeError, ValueError):
            continue
        counts = day_counts(normalized, day)
        if counts is None:
            continue
        totals[0] += counts[0]
        totals[1] += counts[1]
        confirmed[day] = tuple(totals)
    return confirmed


def has_pre_source_legacy_counts(conn, month, first_completed_day):
    """Protect a historical MTD baseline older than available daily sources.

    Example: September 6 baseline and only September 7+ daily uploads.
    The source-only sum MUST NOT replace that already-approved baseline.
    Return only a boolean; do not seed unknown visits from a snapshot.
    """
    if not first_completed_day or first_completed_day[:7] != month:
        return False
    try:
        rows = conn.execute(
            "SELECT payload FROM report_data WHERE substr(date,1,7)=? "
            "AND date<? ORDER BY date DESC",
            (month, first_completed_day),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return False
        raise
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        primary = _count(payload.get("primary"))
        repeat = _count(payload.get("repeat"))
        if primary is not None and repeat is not None and (primary or repeat):
            return True
    return False


def overlay_counts(record, date_key, monthly):
    """Change only two canonical completed counters; preserve all money."""
    expected = monthly.get(str(date_key))
    if expected is None:
        return record
    clean = dict(record) if isinstance(record, dict) else {}
    clean["date"] = str(date_key)
    clean["primary"], clean["repeat"] = expected
    return clean
