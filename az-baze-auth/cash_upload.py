import json
from flask import abort, g, jsonify, redirect, request, url_for

import cash_payments
import ident_import
import upload_reconcile
from app import audit, db, iso_now, permission_required, require_csrf, user_permissions


def _cash_file():
    file_storage = request.files.get("payments")
    if not file_storage or not file_storage.filename:
        raise ValueError("Выберите файл «Счета и оплаты».")
    raw = file_storage.read()
    if not raw:
        raise ValueError("Выбран пустой файл «Счета и оплаты».")
    return file_storage.filename, raw


def _cash_totals(daily):
    total = {
        "total": 0.0,
        "ooo": 0.0,
        "ip": 0.0,
        "dent": 0.0,
        "structure": 0.0,
        "lab": 0.0,
        "unallocated": 0.0,
    }
    for payload in (daily or {}).values():
        check = upload_reconcile.assert_cash_reconciliation(payload)
        total["total"] += float(check["total"] or 0)
        total["dent"] += float(check["dent"] or 0)
        total["structure"] += float(check["structure"] or 0)
        total["lab"] += float(check["lab"] or 0)
        total["unallocated"] += float(check["unallocated"] or 0)
        total["ooo"] += float((payload or {}).get("cashOOO") or 0)
        total["ip"] += float((payload or {}).get("cashIP") or 0)
    result = {key: round(value, 2) for key, value in total.items()}
    if abs(result["total"] - result["ooo"] - result["ip"]) > 0.02:
        raise ValueError("Факт не сходится с разбивкой ООО + ИП.")
    return result


def _cash_preview():
    perms = user_permissions(g.user)
    if "upload_services" not in perms:
        abort(403)

    filename, raw = _cash_file()
    conn = db()
    cash_payments.init_schema(conn)
    upload_reconcile.init_schema(conn)
    upload_reconcile.refresh_runtime(conn, ident_import, cash_payments)

    daily, sheet_name = cash_payments.parse_file(raw, filename)
    comparison = upload_reconcile.compare_cash(conn, daily)
    conflicts = sorted(
        set(comparison.get("conflict") or [])
        | set(comparison.get("removed") or [])
    )
    return {
        "status": "preview",
        "period": {"from": min(daily), "to": max(daily)},
        "counts": {key: len(value or []) for key, value in comparison.items()},
        "conflict_dates": conflicts,
        "requires_choice": bool(conflicts),
        "can_replace": "upload_replace" in perms,
        "sheet": sheet_name,
        "reconciliation": _cash_totals(daily),
    }


def _insert_cash_row(conn, data_date, payload, filename, source_sha, actor_id, now):
    conn.execute(
        """
        INSERT INTO cash_receipts_daily(
            data_date,payload_json,source_filename,source_sha256,
            imported_by,imported_at
        ) VALUES(?,?,?,?,?,?)
        """,
        (
            data_date,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            filename,
            source_sha,
            actor_id,
            now,
        ),
    )


def _commit_cash(decision=None):
    perms = user_permissions(g.user)
    if "upload_services" not in perms:
        abort(403)

    filename, raw = _cash_file()
    conn = db()
    cash_payments.init_schema(conn)
    upload_reconcile.init_schema(conn)
    upload_reconcile.refresh_runtime(conn, ident_import, cash_payments)

    daily, sheet_name = cash_payments.parse_file(raw, filename)
    for payload in daily.values():
        upload_reconcile.assert_cash_reconciliation(payload)

    comparison = upload_reconcile.compare_cash(conn, daily)
    conflict_dates = set(comparison.get("conflict") or [])
    removed_dates = set(comparison.get("removed") or [])
    has_conflict = bool(conflict_dates or removed_dates)

    if has_conflict and decision not in {"keep_old", "use_new"}:
        raise ValueError(
            "В денежной выгрузке есть отличия. Выберите «Сохранить старые» "
            "или «Загрузить новые»."
        )
    if decision == "use_new" and "upload_replace" not in perms:
        abort(403)

    use_new = decision == "use_new"
    new_dates = set(comparison.get("new") or [])
    identical_dates = set(comparison.get("identical") or [])
    apply_dates = set(new_dates)
    if use_new:
        apply_dates |= conflict_dates

    backup_path = None
    if use_new and has_conflict:
        backup_path = upload_reconcile.create_db_backup("cash-reimport")

    now = iso_now()
    source_sha = cash_payments.sha256(raw)
    affected_months = {
        value[:7]
        for value in (apply_dates | (removed_dates if use_new else set()))
    }
    updated = 0

    conn.execute("BEGIN")
    try:
        if use_new:
            for data_date in sorted(removed_dates):
                existing = conn.execute(
                    "SELECT * FROM cash_receipts_daily WHERE data_date=?",
                    (data_date,),
                ).fetchone()
                if existing:
                    upload_reconcile.archive_cash_row(
                        conn, existing, g.user["id"], "use_new_removed"
                    )
                    conn.execute(
                        "DELETE FROM cash_receipts_daily WHERE data_date=?",
                        (data_date,),
                    )

        for data_date in sorted(apply_dates):
            existing = conn.execute(
                "SELECT * FROM cash_receipts_daily WHERE data_date=?",
                (data_date,),
            ).fetchone()
            if existing:
                upload_reconcile.archive_cash_row(
                    conn, existing, g.user["id"], "use_new"
                )
                conn.execute(
                    "DELETE FROM cash_receipts_daily WHERE data_date=?",
                    (data_date,),
                )
            _insert_cash_row(
                conn,
                data_date,
                daily[data_date],
                filename,
                source_sha,
                g.user["id"],
                now,
            )

        for month in sorted(affected_months):
            updated += cash_payments.overlay_stored_month(
                conn, month, g.user["id"], now
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    audit(
        "cash_receipts_reconciled",
        target_user_id=g.user["id"],
        details=(
            f"file={filename}; sheet={sheet_name}; range={min(daily)}..{max(daily)}; "
            f"decision={decision or 'append'}; new={len(new_dates)}; "
            f"identical={len(identical_dates)}; conflict={len(conflict_dates)}; "
            f"removed={len(removed_dates)}; months={','.join(sorted(affected_months))}; "
            f"reports={updated}; backup={backup_path or ''}; "
            "rule=positive-receipts-only"
        ),
    )

    if not apply_dates and not (use_new and removed_dates):
        message = (
            "Денежные данные совпадают; ничего не изменено."
            if not has_conflict
            else "Выбраны старые денежные данные; существующие значения сохранены."
        )
        status = "duplicate"
    else:
        parts = [f"новых дней: {len(new_dates)}"]
        if conflict_dates:
            parts.append(
                f"отличались: {len(conflict_dates)} "
                f"({'загружены новые' if use_new else 'сохранены старые'})"
            )
        if removed_dates:
            parts.append(
                f"отсутствуют в новой версии: {len(removed_dates)} "
                f"({'удалены' if use_new else 'сохранены'})"
            )
        if identical_dates:
            parts.append(f"совпали: {len(identical_dates)}")
        message = "Денежная выгрузка обработана: " + ", ".join(parts) + "."
        status = "replaced" if use_new and has_conflict else "imported"

    return {
        "status": status,
        "message": message,
        "months": len(affected_months),
        "rows": updated,
        "backup": backup_path,
        "reconciliation": _cash_totals(daily),
    }


def register_cash_upload(app):
    cash_payments.configure_providers(
        ident_import.DENTISTS,
        ident_import.STRUCTURE_DOCTORS,
        ident_import.LAB_DOCTORS,
    )

    @app.post("/api/uploads/cash/preview")
    @permission_required("section5")
    def cash_upload_preview():
        require_csrf()
        try:
            return jsonify(_cash_preview())
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    @app.post("/api/uploads/cash/commit")
    @permission_required("section5")
    def cash_upload_commit():
        require_csrf()
        try:
            result = _commit_cash(request.form.get("decision") or None)
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409

    @app.post("/uploads/cash")
    @permission_required("section5")
    def cash_upload():
        require_csrf()
        try:
            preview = _cash_preview()
            if preview["requires_choice"]:
                return redirect(
                    url_for(
                        "uploads_page",
                        cash_error=(
                            "В файле есть отличия от сохранённых денежных данных. "
                            "Подтвердите выбор «Сохранить старые» или «Загрузить новые» на странице."
                        ),
                    )
                )
            result = _commit_cash("append")
        except ValueError as exc:
            return redirect(url_for("uploads_page", cash_error=str(exc)))

        return redirect(
            url_for(
                "uploads_page",
                cash_ok="1",
                cash_months=str(result.get("months", 0)),
                cash_rows=str(result.get("rows", 0)),
            )
        )
