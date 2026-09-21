from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones


class ClinicTimezoneError(ValueError):
    pass


EXCLUDED_TIMEZONE_PREFIXES = ("posix/", "right/", "SystemV/")
EXCLUDED_TIMEZONE_NAMES = {"Factory", "localtime"}


def normalize_timezone_name(value):
    name = str(value or "").strip()
    if not name:
        raise ClinicTimezoneError("Укажите часовой пояс клиники.")
    if name in EXCLUDED_TIMEZONE_NAMES or name.startswith(EXCLUDED_TIMEZONE_PREFIXES):
        raise ClinicTimezoneError("Выберите стандартный часовой пояс IANA.")
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ClinicTimezoneError("Неизвестный часовой пояс IANA.") from exc
    return name


def _utc_offset_label(zone, now_utc=None):
    current = now_utc or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    local = current.astimezone(zone)
    offset = local.utcoffset()
    if offset is None:
        return "UTC"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def timezone_options(now_utc=None):
    options = []
    for name in sorted(available_timezones()):
        if name in EXCLUDED_TIMEZONE_NAMES or name.startswith(EXCLUDED_TIMEZONE_PREFIXES):
            continue
        try:
            zone = ZoneInfo(name)
        except ZoneInfoNotFoundError:
            continue
        offset = _utc_offset_label(zone, now_utc)
        options.append(
            {
                "name": name,
                "offset": offset,
                "label": f"{name} ({offset})",
            }
        )
    options.sort(key=lambda item: (item["offset"], item["name"]))
    return options


def get_clinic_timezone(conn, clinic_id):
    row = conn.execute(
        """
        SELECT id,organization_id,cluster_id,name,timezone,status
        FROM clinics
        WHERE id=?
        """,
        (int(clinic_id),),
    ).fetchone()
    if row is None:
        raise ClinicTimezoneError("Клиника не найдена.")
    return {
        "id": row["id"],
        "organization_id": row["organization_id"],
        "cluster_id": row["cluster_id"],
        "name": row["name"],
        "timezone": row["timezone"],
        "status": row["status"],
    }


def set_clinic_timezone(conn, clinic_id, timezone_name):
    timezone_name = normalize_timezone_name(timezone_name)
    current = get_clinic_timezone(conn, clinic_id)
    if current["timezone"] == timezone_name:
        return {
            **current,
            "old_timezone": current["timezone"],
            "changed": False,
        }

    conn.execute(
        "UPDATE clinics SET timezone=? WHERE id=?",
        (timezone_name, int(clinic_id)),
    )
    updated = get_clinic_timezone(conn, clinic_id)
    return {
        **updated,
        "old_timezone": current["timezone"],
        "changed": True,
    }


def clinic_local_now(conn, clinic_id, now_utc=None):
    clinic = get_clinic_timezone(conn, clinic_id)
    zone = ZoneInfo(normalize_timezone_name(clinic["timezone"]))
    current = now_utc or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    return current.astimezone(zone)
