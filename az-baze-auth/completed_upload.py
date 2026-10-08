import hashlib
import json
from flask import abort, g, jsonify, request

import cash_payments
import daily_upload
import daily_upload_core as core
import paid_services
import upload_reconcile
from app import audit, db, iso_now, permission_required, require_csrf, user_permissions


SEPARATE_SERVICES_LABEL = "Новый отчёт МИС — отдельная загрузка"


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
        SELECT data_date,completed_hash,normalized_json
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

    incoming_dates = set(incoming)
    for data_date, old in existing.items():
        if old.get("has_completed") and data_date not in incoming_dates:
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


def _merge_normalized(existing_normalized, day=None, removed=False):
    value = dict(existing_normalized or {})
    value.setdefault("items", [])
    value.setdefault("retail_items", [])
    value.setdefault("lab_invoices", [])
    value["doctors"] = {}
    if removed:
        data_date = str(value.get("data_date") or "")
        value["overall"] = {data_date: {}} if data_date else {}
        value["visits"] = []
        return value

    value["data_date"] = day["data_date"]
    value["overall"] = day["overall"]
    value["visits"] = day["visits"]
    return value


def _preview():
    perms = user_permissions(g.user)
    if "upload_completed" not in perms:
        abort(403)

    filename, raw = _file_from_request()
    prepared = _parse(raw, filename)
    conn = db()
    cash_payments.init_schema(conn)
    paid_services.init_schema(conn)
    upload_reconcile.init_schema(conn)
    comparison = _comparison(conn, prepared)
    conflicts = sorted(set(comparison["conflict"]) | set(comparison["removed"]))
    paid_snapshot = paid_services.latest_snapshot_for_month(conn, prepared["month"])
    return {
        "status": "preview",
        "period": {"from": prepared["dates"][0], "to": prepared["dates"][-1]},
        "counts": _counts(comparison),
        "conflict_dates": conflicts,
        "requires_choice": bool(conflicts),
        "requires_provider_mapping": False,
        "can_replace": "upload_replace" in perms,
        "paid_source_ready": bool(paid_snapshot),
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
            SEPARATE_SERVICES_LABEL,
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


def _commit(decision=None):
    perms = user_permissions(g.user)
    if "upload_completed" not in perms:
        abort(403)

    filename, raw = _file_from_request()
    prepared = _parse(raw, filename)
    conn = db()
    cash_payments.init_schema(conn)
    paid_services.init_schema(conn)
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
    conn.execute("BEGIN")
    try:
        for data_date in sorted(apply_dates):
            _insert_or_update_day(conn, prepared, data_date, g.user["id"], now)
        for data_date in sorted(removed_dates):
            _clear_removed_day(conn, prepared, data_date, g.user["id"], now)

        source = f"AZ-BAZE completed visits: {prepared['filename']}"
        management = core._rebuild_management(prepared["month"], source)
        cash_payments.overlay_record_map(conn, prepared["month"], management)

        for data_date, record in management.items():
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

        cash_payments.overlay_stored_month(
            conn,
            prepared["month"],
            g.user["id"],
            now,
        )
        paid_services.overlay_stored_month(
            conn,
            prepared["month"],
            g.user["id"],
            now,
            core.ident_import.DENTISTS,
            core.ident_import.STRUCTURE_DOCTORS,
            core.ident_import.LAB_DOCTORS,
            ignored=(
                upload_reconcile.provider_names_by_direction(conn, "ignore")
                | (
                    set(core.ident_import.KNOWN_STAFF)
                    - set(core.ident_import.DENTISTS)
                    - set(core.ident_import.STRUCTURE_DOCTORS)
                    - set(core.ident_import.LAB_DOCTORS)
                )
            ),
        )
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
