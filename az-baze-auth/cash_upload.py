from flask import abort, g, redirect, request, url_for

import cash_payments
import ident_import
from app import audit, db, iso_now, permission_required, require_csrf, user_permissions


def register_cash_upload(app):
    cash_payments.configure_providers(
        ident_import.DENTISTS,
        ident_import.STRUCTURE_DOCTORS,
        ident_import.LAB_DOCTORS,
    )
    @app.post("/uploads/cash")
    @permission_required("section5")
    def cash_upload():
        require_csrf()
        perms = user_permissions(g.user)
        if "upload_services" not in perms:
            abort(403)

        file_storage = request.files.get("payments")
        if not file_storage or not file_storage.filename:
            return redirect(url_for("uploads_page", cash_error="Выберите файл «Счета и оплаты»."))

        raw = file_storage.read()
        try:
            daily, sheet_name = cash_payments.parse_file(raw, file_storage.filename)
            conn = db()
            cash_payments.init_schema(conn)
            now = iso_now()
            conn.execute("BEGIN")
            try:
                months = cash_payments.replace_range(
                    conn,
                    daily,
                    file_storage.filename,
                    cash_payments.sha256(raw),
                    g.user["id"],
                    now,
                )
                updated = sum(
                    cash_payments.overlay_stored_month(conn, month, g.user["id"], now)
                    for month in months
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        except ValueError as exc:
            return redirect(url_for("uploads_page", cash_error=str(exc)))

        audit(
            "cash_receipts_imported",
            target_user_id=g.user["id"],
            details=(
                f"file={file_storage.filename}; sheet={sheet_name}; "
                f"range={min(daily)}..{max(daily)}; months={','.join(months)}; reports={updated}; "
                "rule=positive-receipts-only; ooo=0531380019042729; ip=0463880019042725"
            ),
        )
        return redirect(
            url_for(
                "uploads_page",
                cash_ok="1",
                cash_months=str(len(months)),
                cash_rows=str(updated),
            )
        )
