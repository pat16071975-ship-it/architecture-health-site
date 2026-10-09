import hashlib
import json
import copy
from flask import abort, g, jsonify, request

import cash_payments
import daily_upload
import daily_upload_core as core
import upload_reconcile
import upload_integrity
from app import audit, db, iso_now, permission_required, require_csrf, user_permissions



def _file_from_request():
    file_storage = request.files.get("completed")
    if not file_storage or not file_storage.filename:
        raise ValueError("Выберите файл «Завершённые приёмы».")
    raw = file_storage.read()
    if not raw:
        raise ValueError("Выбран пустой файл «Завершённые приёмы».")
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("Размер файла «Завершённые приёмы» превышает 25 МБ.")
    return file_storage.filename, raw


def _parse(raw, filename):
    parsed, sheet = daily_upload._parse_fixed_raw(raw, filename, "completed")
    overall, visits = parsed
    dates = sorted(str(value) for value in overall)
    if not dates:
        raise ValueError("Файл «Завершённые приёмы» не содержит дат.")
    months = {value[:7] for value in dates}
    if len(months) != 1:
        raise ValueError("Файл «Завершённые приёмы» должен содержать один календарный месяц.")
    month = next(iter(months))

    by_date = {}
    for data_date in dates:
        day_overall = {data_date: core._plain(overall.get(data_date, {}))}
        day_visits = [
            core._plain(row)
            for row in visits
            if str(row.get("date") or "") == data_date
        ]
        completed_hash = daily_upload._payload_hash(
            {
                "data_date": data_date,
                "overall": day_overall,
                "visits": day_visits,
            }
        )
        by_date[data_date] = {
            "data_date": data_date,
            "overall": day_overall,
            "visits": day_visits,
            "completed_hash": completed_hash,
        }
    return {
        "filename": filename,
        "sheet": sheet,
        "month": month,
        "dates": dates,
        "days": by_date,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _existing_completed_dates(conn, month):
    result = {}
    rows = conn.execute(
        """
        SELECT data_date,completed_sha256,normalized_json
        FROM daily_uploads
        WHERE substr(data_date,1,7)=?
        ORDER BY data_date
        """,
        (month,),
    ).fetchall()
    for row in rows:
        data_date = str(row["data_date"])
        try:
            normalized = json.loads(row["normalized_json"])
        except (TypeError, ValueError):
            normalized = {}
        overall = normalized.get("overall") if isinstance(normalized, dict) else {}
        visits = normalized.get("visits") if isinstance(normalized, dict) else []
        has_completed = bool(
            any((value or {}) for value in (overall or {}).values())
            or visits
        )
        canonical_hash = daily_upload._payload_hash(
            {
                "data_date": data_date,
                "overall": overall or {},
                "visits": visits or [],
            }
        )
        result[data_date] = {
            "completed_hash": canonical_hash,
            "has_completed": has_completed,
        }
    return result


def compare_completed(existing, prepared):
    incoming = prepared["days"]
    result = {"new": [], "identical": [], "conflict": [], "removed": []}
    for data_date, day in incoming.items():
        old = existing.get(data_date)
        if not old:
            result["new"].append(data_date)
        elif str(old.get("completed_hash") or "") == str(day["completed_hash"]):
            result["identical"].append(data_date)
        else:
            result["conflict"].append(data_date)

    incoming_dates = sorted(incoming)
    incoming_set = set(incoming_dates)
    if incoming_dates:
        start_date, end_date = incoming_dates[0], incoming_dates[-1]
        for data_date, old in existing.items():
            if (
                start_date <= data_date <= end_date
                and old.get("has_completed")
                and data_date not in incoming_set
            ):
                result["removed"].append(data_date)

    for key in result:
        result[key] = sorted(result[key])
    return result


def _comparison(conn, prepared):
    return compare_completed(
        _existing_completed_dates(conn, prepared["month"]),
        prepared,
    )


def _counts(comparison):
    return {
        "new": len(comparison["new"]),
        "identical": len(comparison["identical"]),
        "conflict": len(comparison["conflict"]),
        "historical": 0,
        "removed": len(comparison["removed"]),
    }



def _clinical_history_present(record):
    """Cash-only snapshots can precede a completed upload; legacy clinical data cannot."""
    if not isinstance(record, dict):
        return True
    clinical_keys = (
        "_source", "_aggregation", "_uploadControl", "primary", "repeat",
        "dentPrimary", "dentRepeat", "clinicPrimary", "clinicRepeat", "labOrders",
    )
    if any(key in record for key in clinical_keys):
        return True
    if record.get("_cash_rule") == "positive-receipts-only-v1":
        return False
    return any(key in record for key in (
        "factMedicine", "factLab", "dentists", "clinicDocs", "labRevenue",
        "grossRevenue", "discountAmount",
    ))


def _guard_historical_scope(conn, prepared):
    """Fail closed before a completed-only rebuild can touch legacy clinical history.

    A cash-only report row is not evidence of a completed clinical upload.
    However, previously imported clinical report_data and the legacy services
    through-date are authoritative until a separately approved cutover.
    """
    month = prepared["month"]
    registered = {
        str(row[0])
        for row in conn.execute(
            "SELECT data_date FROM daily_uploads WHERE substr(data_date,1,7)=?",
            (month,),
        ).fetchall()
    }
    # Existing daily rows are used to rebuild month-to-date totals but they
    # are NOT new imports. Only incoming dates may cross the legacy boundary.
    first_rebuilt_date = min(set(prepared["dates"]) | registered)

    source = conn.execute(
        "SELECT payload FROM report_blobs WHERE key=?",
        ("az-service-analytics-v1",),
    ).fetchone()
    if source:
        try:
            data = json.loads(source[0])
        except (TypeError, ValueError) as exc:
            raise ValueError("Нельзя проверить исторический источник услуг: данные повреждены.") from exc
        if not isinstance(data, dict):
            raise ValueError("Нельзя проверить исторический источник услуг: неверный формат.")
        through = core._service_through(data)
        if through and any(str(day) <= through for day in prepared["dates"]):
            raise ValueError(
                "Отдельная загрузка «Завершённых приёмов» пересекает ранее "
                "сохранённую сводную базу услуг. Изменения остановлены; "
                "старые клинические показатели не заменены."
            )

    rows = conn.execute(
        "SELECT date,payload FROM report_data WHERE substr(date,1,7)=? AND date>=? ORDER BY date",
        (month, first_rebuilt_date),
    ).fetchall()
    for row in rows:
        data_date = str(row["date"])
        if data_date in registered:
            continue
        try:
            previous = json.loads(row["payload"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Нельзя безопасно проверить исторические данные за {data_date}."
            ) from exc
        if _clinical_history_present(previous):
            raise ValueError(
                f"За {data_date} уже есть исторические клинические данные, "
                "не представленные в ежедневном журнале. Отдельная загрузка "
                "остановлена без их замены."
            )


def _preserve_completed_totals(management):
    """The completed-visits file is authoritative for overall primary/repeat."""
    for record in management.values():
        control = record.get("_uploadControl")
        if not isinstance(control, dict) or not {"sourcePrimary", "sourceRepeat"} <= control.keys():
            raise ValueError(
                "Нет контрольных общих чисел приёмов; перезапись отчёта остановлена."
            )
        record["primary"] = int(control["sourcePrimary"])
        record["repeat"] = int(control["sourceRepeat"])
    return management


def _same_financial_value(before, after):
    if isinstance(before, dict) and isinstance(after, dict):
        return before.keys() == after.keys() and all(
            _same_financial_value(value, after[key]) for key, value in before.items()
        )
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        if isinstance(before, bool) or isinstance(after, bool):
            return before is after
        return abs(float(before) - float(after)) < 0.005
    return before == after


def _guard_existing_financial_values(conn, management):
    """A completed-only import cannot silently replace money or service counts."""
    protected = (
        "factMedicine", "factLab", "dentists", "clinicDocs", "labRevenue",
        "labOrders", "grossRevenue", "discountAmount", "discountDataComplete",
        "billedMedicine", "billedLab", "billedDentists", "billedClinicDocs",
        "billedLabRevenue", "billedTotal", "cashTotal", "cashOOO", "cashIP",
        "cashUnallocated", "dentCashOOO", "dentCashIP", "clinicCashOOO",
        "clinicCashIP", "labCashOOO", "labCashIP",
    )
    for data_date, record in management.items():
        row = conn.execute(
            "SELECT payload FROM report_data WHERE date=?", (data_date,)
        ).fetchone()
        if not row:
            continue
        try:
            existing = json.loads(row["payload"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Нельзя проверить сохранённые суммы за {data_date}."
            ) from exc
        if not isinstance(existing, dict):
            raise ValueError(f"Неверный формат сохранённых сумм за {data_date}.")
        for field in protected:
            if field in existing and (
                field not in record
                or not _same_financial_value(existing[field], record[field])
            ):
                raise ValueError(
                    f"Отдельная загрузка приёмов изменила бы сохранённый "
                    f"показатель {field} за {data_date}. Операция остановлена."
                )


def _merge_normalized(existing_normalized, day=None, removed=False):
    value = dict(existing_normalized or {})
    value.setdefault("items", [])
    value.setdefault("retail_items", [])
    value.setdefault("lab_invoices", [])
    if removed:
        value["doctors"] = {}
        data_date = str(value.get("data_date") or "")
        value["overall"] = {data_date: {}} if data_date else {}
        value["visits"] = []
        return value

    value["data_date"] = day["data_date"]
    value["overall"] = day["overall"]
    value["visits"] = day["visits"]
    # Preserve the approved clinical attribution rule when completed visits change.
    clinical_items = [row for row in value["items"] if not daily_upload._is_retail_item(row)]
    value["doctors"] = core._plain(daily_upload._doctor_attribution(value["visits"], clinical_items))
    return value


def _preview():
    perms = user_permissions(g.user)
    if "upload_completed" not in perms:
        abort(403)

    filename, raw = _file_from_request()
    prepared = _parse(raw, filename)
    conn = db()
    # All required tables already exist; preview must not create or modify them.
    _guard_historical_scope(conn, prepared)
    comparison = _comparison(conn, prepared)
    conflicts = sorted(set(comparison["conflict"]) | set(comparison["removed"]))
    return {
        "status": "preview",
        "period": {"from": prepared["dates"][0], "to": prepared["dates"][-1]},
        "counts": _counts(comparison),
        "conflict_dates": conflicts,
        "requires_choice": bool(conflicts),
        "requires_provider_mapping": False,
        "can_replace": "upload_replace" in perms,
    }


def _read_normalized(row):
    if not row:
        return {}
    try:
        value = json.loads(row["normalized_json"])
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _insert_or_update_day(conn, prepared, data_date, actor_id, now):
    day = prepared["days"][data_date]
    existing = conn.execute(
        "SELECT * FROM daily_uploads WHERE data_date=?",
        (data_date,),
    ).fetchone()

    if existing:
        upload_reconcile.archive_daily_row(
            conn,
            existing,
            actor_id,
            "completed_source_update",
        )
        normalized = _merge_normalized(_read_normalized(existing), day=day)
        revision = int(existing["revision"] or 1) + 1
        conn.execute(
            """
            UPDATE daily_uploads
            SET completed_filename=?,completed_sha256=?,normalized_json=?,
                revision=?,uploaded_by=?,uploaded_at=?
            WHERE data_date=?
            """,
            (
                prepared["filename"],
                day["completed_hash"],
                json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
                revision,
                actor_id,
                now,
                data_date,
            ),
        )
        return "replaced"

    normalized = _merge_normalized(
        {
            "data_date": data_date,
            "items": [],
            "retail_items": [],
            "lab_invoices": [],
            "doctors": {},
        },
        day=day,
    )
    conn.execute(
        """
        INSERT INTO daily_uploads(
            data_date,completed_filename,services_filename,
            completed_sha256,services_sha256,normalized_json,
            revision,uploaded_by,uploaded_at
        ) VALUES(?,?,?,?,?,?,1,?,?)
        """,
        (
            data_date,
            prepared["filename"],
            "",
            day["completed_hash"],
            "",
            json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
            actor_id,
            now,
        ),
    )
    return "imported"


def _clear_removed_day(conn, prepared, data_date, actor_id, now):
    existing = conn.execute(
        "SELECT * FROM daily_uploads WHERE data_date=?",
        (data_date,),
    ).fetchone()
    if not existing:
        return
    upload_reconcile.archive_daily_row(
        conn,
        existing,
        actor_id,
        "completed_source_removed",
    )
    normalized = _merge_normalized(_read_normalized(existing), removed=True)
    empty_hash = daily_upload._payload_hash(
        {
            "data_date": data_date,
            "overall": normalized.get("overall") or {},
            "visits": [],
        }
    )
    conn.execute(
        """
        UPDATE daily_uploads
        SET completed_filename=?,completed_sha256=?,normalized_json=?,
            revision=?,uploaded_by=?,uploaded_at=?
        WHERE data_date=?
        """,
        (
            prepared["filename"],
            empty_hash,
            json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
            int(existing["revision"] or 1) + 1,
            actor_id,
            now,
            data_date,
        ),
    )



def _read_previous_clinical_checkpoint(conn, data_date):
    """Latest previously SAVED clinical MTD state, not partial normalized history."""
    rows = conn.execute(
        "SELECT date,payload FROM report_data "
        "WHERE substr(date,1,7)=? AND date<? ORDER BY date DESC",
        (data_date[:7], data_date),
    ).fetchall()
    for row in rows:
        try:
            saved = json.loads(row["payload"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Не удалось проверить предыдущий отчёт за {row['date']}."
            ) from exc
        if _clinical_history_present(saved):
            return str(row["date"]), saved
    return None, {}


def _guard_future_clinical_dates(conn, changed_dates):
    """Do not silently leave a later MTD clinical snapshot stale after backfill."""
    first_date = min(changed_dates)
    last_date = first_date[:7] + "-31"
    rows = conn.execute(
        "SELECT date,payload FROM report_data "
        "WHERE date>? AND date<=? ORDER BY date",
        (first_date, last_date),
    ).fetchall()
    for row in rows:
        other_date = str(row["date"])
        if other_date in changed_dates:
            continue
        try:
            record = json.loads(row["payload"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Нельзя безопасно проверить отчёт за {other_date}."
            ) from exc
        if _clinical_history_present(record):
            raise ValueError(
                "В месяце уже есть более поздний клинический срез за "
                f"{other_date}. Частичная замена приёмов остановлена, "
                "чтобы не оставить старые накопительные значения."
            )


def _completed_source_day(conn, data_date, source):
    """Append one completed-only day onto the last authoritative clinical MTD.

    Only visits are new in this independent source. Earlier approved money,
    laboratory counts and discount figures must never be reconstructed from
    an incomplete set of daily_uploads rows.
    """
    row = conn.execute(
        "SELECT normalized_json FROM daily_uploads WHERE data_date=?",
        (data_date,),
    ).fetchone()
    if not row:
        raise ValueError(f"Не найден сохранённый день приёмов {data_date}.")
    try:
        normalized = json.loads(row["normalized_json"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Нельзя прочитать день приёмов {data_date}.") from exc
    if not isinstance(normalized, dict) or normalized.get("data_date") != data_date:
        raise ValueError(f"Некорректные данные дня приёмов {data_date}.")
    # Services have their own authoritative upload. Completed-only must not
    # infer service revenue from a row whose provenance is unclear.
    if normalized.get("items") or normalized.get("lab_invoices"):
        raise ValueError(
            f"За {data_date} обнаружены услуги, которые нельзя безопасно "
            "пересчитывать из отдельного файла приёмов."
        )

    earlier_date, earlier = _read_previous_clinical_checkpoint(conn, data_date)
    current_row = conn.execute(
        "SELECT payload FROM report_data WHERE date=?", (data_date,)
    ).fetchone()
    current = {}
    if current_row:
        try:
            current = json.loads(current_row["payload"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Нельзя проверить отчёт за {data_date}.") from exc
        if not isinstance(current, dict):
            raise ValueError(f"Некорректный формат отчёта за {data_date}.")

    base = copy.deepcopy(earlier)
    base.update(copy.deepcopy(current))
    delta = upload_integrity.daily_delta(core, normalized)
    control = earlier.get("_uploadControl")
    if not isinstance(control, dict):
        control = {}

    original_primary = int(control.get("sourcePrimary", earlier.get("primary", 0)) or 0)
    original_repeat = int(control.get("sourceRepeat", earlier.get("repeat", 0)) or 0)
    primary = original_primary + delta["sourcePrimary"]
    repeat = original_repeat + delta["sourceRepeat"]

    for key in ("dentPrimary", "dentRepeat", "clinicPrimary", "clinicRepeat"):
        base[key] = int(earlier.get(key, 0) or 0) + int(delta[key])
    base["primary"] = primary
    base["repeat"] = repeat
    base["_uploadControl"] = {
        "sourcePrimary": primary,
        "sourceRepeat": repeat,
        "reportedPrimary": primary,
        "reportedRepeat": repeat,
        "unassignedPrimary": max(0, primary - base["dentPrimary"] - base["clinicPrimary"]),
        "unassignedRepeat": max(0, repeat - base["dentRepeat"] - base["clinicRepeat"]),
    }

    # No independent services were uploaded with this file. Carry the exact
    # last approved clinic/service MTD checkpoint rather than summing old
    # service invoices from an incomplete journal (B04).
    for key, fallback in (
        ("factMedicine", 0), ("factLab", 0), ("labRevenue", 0),
        ("labOrders", 0), ("dentists", {}), ("clinicDocs", {}),
        ("grossRevenue", 0), ("discountAmount", 0),
        ("discountDataComplete", False),
    ):
        if key not in base:
            base[key] = copy.deepcopy(fallback)

    base["date"] = data_date
    base["_source"] = source
    base["_aggregation"] = "month_to_date"
    base["_clinicalAsOf"] = data_date
    base["_serviceAsOf"] = (
        str(earlier.get("_serviceAsOf") or earlier_date or "")
        if earlier_date else str(current.get("_serviceAsOf") or "")
    )
    if base.get("_cash_rule") == "positive-receipts-only-v1":
        base["_cashAsOf"] = str(
            current.get("_cashAsOf") or
            (data_date if current.get("_cash_rule") == "positive-receipts-only-v1" else
             earlier.get("_cashAsOf") or earlier_date or "")
        )
    return base


def _commit(decision=None):
    perms = user_permissions(g.user)
    if "upload_completed" not in perms:
        abort(403)

    filename, raw = _file_from_request()
    prepared = _parse(raw, filename)
    conn = db()
    _guard_historical_scope(conn, prepared)
    cash_payments.init_schema(conn)
    upload_reconcile.init_schema(conn)
    comparison = _comparison(conn, prepared)
    has_conflict = bool(comparison["conflict"] or comparison["removed"])

    if has_conflict and decision not in {"keep_old", "use_new"}:
        raise ValueError(
            "В «Завершённых приёмах» есть отличия. Выберите "
            "«Сохранить старые» или «Загрузить новые»."
        )
    if decision == "use_new" and "upload_replace" not in perms:
        abort(403)

    if has_conflict and decision == "keep_old":
        apply_dates = set(comparison["new"])
        removed_dates = set()
    else:
        apply_dates = set(comparison["new"])
        if decision == "use_new":
            apply_dates.update(comparison["conflict"])
        removed_dates = set(comparison["removed"]) if decision == "use_new" else set()

    if not apply_dates and not removed_dates:
        return {
            "status": "duplicate",
            "message": "«Завершённые приёмы» совпадают; ничего не изменено.",
        }

    backup = None
    if decision == "use_new" and has_conflict:
        backup = upload_reconcile.create_db_backup("completed-reimport")

    now = iso_now()
    changed_dates = set(apply_dates) | set(removed_dates)
    # Fail closed for backward edits that would leave a future clinical MTD
    # stale; cash-only future snapshots are not clinical history.
    _guard_future_clinical_dates(conn, changed_dates)

    conn.execute("BEGIN")
    try:
        for data_date in sorted(apply_dates):
            _insert_or_update_day(conn, prepared, data_date, g.user["id"], now)
        for data_date in sorted(removed_dates):
            _clear_removed_day(conn, prepared, data_date, g.user["id"], now)

        source = f"AZ-BAZE completed visits: {prepared['filename']}"
        # Build each written date from the PREVIOUS APPROVED report checkpoint,
        # not an incomplete full-month daily_uploads service ledger.
        for data_date in sorted(changed_dates):
            record = _completed_source_day(conn, data_date, source)
            records = {data_date: record}
            cash_payments.overlay_record_map(conn, prepared["month"], records)
            record = records[data_date]
            _guard_existing_financial_values(conn, {data_date: record})
            conn.execute(
                """
                INSERT INTO report_data(date,payload,updated_by,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(date) DO UPDATE SET
                payload=excluded.payload,
                updated_by=excluded.updated_by,
                updated_at=excluded.updated_at
                """,
                (
                    data_date,
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                    g.user["id"],
                    now,
                ),
            )
        # Cash source snapshots and untouched historical clinical rows are not
        # rewritten from a completed-only upload.
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    audit(
        "completed_reports_reconciled",
        target_user_id=g.user["id"],
        details=(
            f"file={prepared['filename']}; sheet={prepared['sheet']}; "
            f"month={prepared['month']}; decision={decision or 'append'}; "
            f"new={len(comparison['new'])}; identical={len(comparison['identical'])}; "
            f"conflict={len(comparison['conflict'])}; removed={len(comparison['removed'])}; "
            f"backup={backup or ''}; paid_source_separate=1"
        ),
    )

    return {
        "status": "replaced" if decision == "use_new" and has_conflict else "imported",
        "message": (
            "«Завершённые приёмы» загружены отдельно. "
            "Денежный Факт, ООО/ИП и новый MIS-источник «Оплачено» сохранены независимо."
        ),
        "backup": backup,
    }


def register_completed_upload(app):
    @app.post("/api/uploads/completed/preview")
    @permission_required("section5")
    def completed_upload_preview():
        require_csrf()
        try:
            return jsonify(_preview())
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    @app.post("/api/uploads/completed/commit")
    @permission_required("section5")
    def completed_upload_commit():
        require_csrf()
        try:
            return jsonify(_commit(request.form.get("decision") or None))
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409
