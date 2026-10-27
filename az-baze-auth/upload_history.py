"""Read the existing upload journal plus recorded independent-upload events.

No migration, backfill, inference from snapshots, or access to general admin
history. An audit event remains one recorded operation, not invented daily
imports. The displayed period is explicitly the FILE period/month; the old
audit messages do not enumerate every date actually applied by keep_old.
"""
import re
from datetime import date, datetime, timezone


_ACTIONS = {
    "completed_reports_reconciled": "Завершённые приёмы",
    "cash_receipts_reconciled": "Счета и оплаты",
    "paid_services_stored": "Выручка по направлениям (новый оплаты)",
}
_COUNT_FIELDS = (
    r"decision=(?P<decision>append|keep_old|use_new); new=(?P<new>\d{1,9}); "
    r"identical=(?P<identical>\d{1,9}); conflict=(?P<conflict>\d{1,9}); removed=(?P<removed>\d{1,9})"
)
_PATTERNS = {
    "completed_reports_reconciled": (re.compile(
        r"\Afile=(?P<filename>[^;\r\n]+); sheet=[^;\r\n]*; month=(?P<month>\d{4}-\d{2}); "
        + _COUNT_FIELDS + r"; backup=.*; paid_source_separate=1\Z", re.S),),
    "cash_receipts_reconciled": (re.compile(
        r"\Afile=(?P<filename>[^;\r\n]+); sheet=[^;\r\n]*; range=(?P<start>\d{4}-\d{2}-\d{2})"
        r"\.\.(?P<end>\d{4}-\d{2}-\d{2}); " + _COUNT_FIELDS
        + r"; months=[\d,\-]*; reports=\d+; backup=.*; rule=positive-receipts-only\Z", re.S),),
    "paid_services_stored": (
        re.compile(
            r"\Afile=(?P<filename>[^;\r\n]+); period_start=(?P<start>\d{4}-\d{2}-\d{2}); "
            r"period_end=(?P<end>\d{4}-\d{2}-\d{2}); source_sha256=[0-9a-f]{64}; "
            r"decision=(?P<decision>append|keep_old|use_new); status=(?P<status>imported|replaced|duplicate); "
            r"paid=[\d.\-+eE]+; reports=\d+; analytics_updated=[01]; backup=.*\Z", re.S),
        re.compile(
            r"\Afile=(?P<filename>[^;\r\n]+); month=(?P<month>\d{4}-\d{2}); "
            r"as_of=(?P<end>\d{4}-\d{2}-\d{2}); "
            r"decision=(?P<decision>append|keep_old|use_new); status=(?P<status>imported|replaced|duplicate); "
            r"paid=[\d.\-+eE]+; reports=\d+; analytics_updated=[01]; backup=.*\Z", re.S),
    ),
}
_STATUS_TEXT = {
    "imported": "Загружено", "replaced": "Заменено",
    "duplicate": "Дубль — без изменений", "kept_old": "Сохранены старые данные",
    "partial": "Новые добавлены, старые сохранены", "recorded": "Событие загрузчика",
}


def _rows(conn, sql, args=()):
    cursor = conn.execute(sql, args)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _status(fields):
    if "status" in fields:
        return fields["status"]
    new, conflict, removed = (int(fields[name]) for name in ("new", "conflict", "removed"))
    decision = fields["decision"]
    if conflict or removed:
        if decision == "use_new":
            return "replaced"
        if decision == "keep_old":
            return "partial" if new else "kept_old"
        # Unexpected historical text: do not invent a successful import.
        return "recorded"
    return "imported" if new else "duplicate"


def _event_row(event):
    action = event["action"]
    result = {
        "id": "audit:" + str(event["id"]), "created_at": event["created_at"],
        "uploaded_by": event["actor_user_id"], "user_name": event["user_name"],
        "source_label": _ACTIONS[action], "data_date": None, "period_label": "",
        "status": "recorded", "status_text": _STATUS_TEXT["recorded"],
        "completed_filename": "", "services_filename": "", "details": "",
    }
    for pattern in _PATTERNS[action]:
        match = pattern.fullmatch(event["details"] or "")
        if not match:
            continue
        fields = match.groupdict()
        try:
            if fields.get("start"):
                start, end = date.fromisoformat(fields["start"]), date.fromisoformat(fields["end"])
                if start > end:
                    continue
                result["data_date"] = start.isoformat() if start == end else f"{start.isoformat()} — {end.isoformat()}"
                result["period_label"] = "Период файла"
            else:
                date.fromisoformat(fields["month"] + "-01")
                if fields.get("end"):
                    end = date.fromisoformat(fields["end"])
                    if end.isoformat()[:7] != fields["month"]:
                        continue
                    result["data_date"] = end.isoformat()
                    result["period_label"] = "Срез файла"
                else:
                    result["data_date"] = fields["month"]
                    result["period_label"] = "Месяц файла; даты применения не записаны"
        except ValueError:
            continue
        status = _status(fields)
        result["status"], result["status_text"] = status, _STATUS_TEXT[status]
        key = "completed_filename" if action == "completed_reports_reconciled" else "services_filename"
        result[key] = fields["filename"]
        break
    return result


def _order(row):
    try:
        stamp = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.timestamp()
    except (TypeError, ValueError, AttributeError, OverflowError):
        stamp = float("-inf")
    identifier = str(row["id"])
    return stamp, int(identifier.split(":")[-1]), identifier.startswith("audit:")


def recent_uploads(conn, limit=60):
    """Only SELECTs. Caller retains the existing section5/upload_history gate."""
    if type(limit) is not int or not 1 <= limit <= 60:
        raise ValueError("Invalid upload history limit")
    rows = []
    if _table_exists(conn, "upload_log"):
        rows = _rows(conn,
            "SELECT l.*,u.full_name AS user_name FROM upload_log l "
            "LEFT JOIN users u ON u.id=l.uploaded_by ORDER BY l.id DESC LIMIT ?", (limit,))
    if _table_exists(conn, "audit_log"):
        events = _rows(conn,
            "SELECT a.id,a.actor_user_id,a.action,a.details,a.created_at,u.full_name AS user_name "
            "FROM audit_log a LEFT JOIN users u ON u.id=a.actor_user_id "
            "WHERE a.action IN (?,?,?) ORDER BY a.id DESC LIMIT ?", (*_ACTIONS, limit))
        rows.extend(_event_row(event) for event in events)
    return sorted(rows, key=_order, reverse=True)[:limit]
