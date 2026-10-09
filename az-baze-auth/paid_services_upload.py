import hashlib
import json

from flask import abort, g, jsonify, request

import cash_payments
import ident_import
import paid_services
import upload_reconcile
from app import audit, db, iso_now, permission_required, require_csrf, user_permissions


def _file_from_request():
    file_storage = request.files.get("paid_services")
    if not file_storage or not file_storage.filename:
        raise ValueError("Выберите новый отчёт МИС «Выручка по направлениям».")
    raw = file_storage.read()
    if not raw:
        raise ValueError("Выбран пустой новый отчёт МИС.")
    return file_storage.filename, raw


def _ignored_provider_names(conn):
    clinical = (
        set(ident_import.DENTISTS)
        | set(ident_import.STRUCTURE_DOCTORS)
        | set(ident_import.LAB_DOCTORS)
    )
    nonclinical_known = set(ident_import.KNOWN_STAFF) - clinical
    return upload_reconcile.provider_names_by_direction(conn, "ignore") | nonclinical_known


def _summary(report, conn):
    values = paid_services.direction_summary(
        report,
        ident_import.DENTISTS,
        ident_import.STRUCTURE_DOCTORS,
        ident_import.LAB_DOCTORS,
        ignored=_ignored_provider_names(conn),
    )
    totals = report.get("totals") or {}
    return {
        "opening": round(float(totals.get("opening") or 0), 2),
        "billed": round(float(totals.get("billed") or 0), 2),
        "paid": round(float(totals.get("paid") or 0), 2),
        "closing": round(float(totals.get("closing") or 0), 2),
        "dent": round(float(values.get("dentPaid") or 0), 2),
        "structure": round(float(values.get("structurePaid") or 0), 2),
        "lab": round(float(values.get("labPaid") or 0), 2),
        "unknown_paid": round(float(values.get("unknownPaid") or 0), 2),
        "ignored_paid": round(float(values.get("ignoredPaid") or 0), 2),
        "non_provider_paid": round(float(values.get("nonProviderPaid") or 0), 2),
        "unknown_providers": sorted((values.get("unknownProviders") or {}).keys()),
    }


def _existing_state(conn, report, source_sha):
    paid_services.init_schema(conn)
    row = conn.execute(
        """
        SELECT payload_json,source_sha256
        FROM service_payment_snapshots
        WHERE as_of_date=?
        """,
        (str(report.get("period_end") or ""),),
    ).fetchone()
    if not row:
        return "new"

    incoming = json.dumps(
        paid_services.compact_report(report),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    existing_payload = str(row["payload_json"] if hasattr(row, "keys") else row[0])
    if json.dumps(
        json.loads(existing_payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) == incoming:
        return "identical"
    return "conflict"


def _provider_decisions():
    raw = request.form.get("provider_decisions", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Не удалось прочитать классификацию новых сотрудников.") from exc
    if not isinstance(value, dict):
        raise ValueError("Некорректная классификация новых сотрудников.")
    return value


def _prepare():
    perms = user_permissions(g.user)
    if "upload_services" not in perms:
        abort(403)

    filename, raw = _file_from_request()
    conn = db()
    paid_services.init_schema(conn)
    upload_reconcile.init_schema(conn)
    upload_reconcile.refresh_runtime(conn, ident_import, cash_payments)

    report = paid_services.parse_bytes(
        raw,
        filename,
        known_staff=ident_import.KNOWN_STAFF,
    )
    source_sha = hashlib.sha256(raw).hexdigest()
    summary = _summary(report, conn)
    state = _existing_state(conn, report, source_sha)
    return perms, conn, filename, raw, report, source_sha, summary, state


def _preview():
    perms, conn, filename, _raw, report, _source_sha, summary, state = _prepare()
    unknown = summary["unknown_providers"]
    if unknown:
        upload_reconcile.record_pending_providers(
            conn,
            unknown,
            filename,
            actor_id=g.user["id"],
        )
        conn.commit()

    return {
        "status": "preview",
        "period": {
            "from": f"{report['month']}-01",
            "to": report["period_end"],
        },
        "month": report["month"],
        "counts": {
            "new": 1 if state == "new" else 0,
            "identical": 1 if state == "identical" else 0,
            "conflict": 1 if state == "conflict" else 0,
            "historical": 0,
            "removed": 0,
        },
        "requires_choice": state == "conflict",
        "requires_provider_mapping": bool(unknown),
        "unknown_providers": unknown,
        "can_replace": "upload_replace" in perms,
        "summary": summary,
    }


def _commit(decision=None):
    perms, conn, filename, _raw, report, source_sha, summary, state = _prepare()

    decisions = _provider_decisions()
    if summary["unknown_providers"]:
        if not decisions:
            raise ValueError("Сначала классифицируйте новых сотрудников из нового отчёта МИС.")
        if "upload_replace" not in perms:
            abort(403)
        missing = [name for name in summary["unknown_providers"] if name not in decisions]
        if missing:
            raise ValueError(
                "Не выбрано направление для новых сотрудников: " + ", ".join(missing)
            )
        upload_reconcile.resolve_providers(conn, decisions, g.user["id"])
        conn.commit()
        upload_reconcile.refresh_runtime(conn, ident_import, cash_payments)
        summary = _summary(report, conn)
        if summary["unknown_providers"]:
            raise ValueError("Не для всех новых сотрудников выбрано направление.")

    if state == "conflict" and decision not in {"keep_old", "use_new"}:
        raise ValueError(
            "За эту дату уже сохранена другая версия нового отчёта МИС. "
            "Выберите «Сохранить старые» или «Загрузить новые»."
        )
    if decision == "use_new" and "upload_replace" not in perms:
        abort(403)

    if state == "identical":
        return {
            "status": "duplicate",
            "message": "Новый отчёт МИС совпадает с сохранённым; ничего не изменено.",
            "summary": summary,
        }
    if state == "conflict" and decision == "keep_old":
        return {
            "status": "duplicate",
            "message": "Сохранена ранее загруженная версия нового отчёта МИС.",
            "summary": summary,
        }

    backup = None
    if state == "conflict" and decision == "use_new":
        backup = upload_reconcile.create_db_backup("paid-services-reimport")

    now = iso_now()
    # Transitional storage-only mode: new MIS reports cannot replace the approved
    # clinical/service analytics or any management/visit/laboratory figures.
    # Activation requires a separate owner decision and a separately reviewed PR.
    analytics_updated = False
    updated = 0
    conn.execute("BEGIN")
    try:
        status = paid_services.store_snapshot(
            conn,
            report,
            filename,
            source_sha,
            g.user["id"],
            now,
            decision="use_new" if decision == "use_new" else "append",
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    audit(
        "paid_services_stored",
        target_user_id=g.user["id"],
        details=(
            f"file={filename}; month={report['month']}; as_of={report['period_end']}; "
            f"decision={decision or 'append'}; status={status}; "
            f"paid={summary['paid']}; reports={updated}; "
            f"analytics_updated={int(analytics_updated)}; backup={backup or ''}"
        ),
    )
    return {
        "status": "replaced" if status == "replaced" else "imported",
        "message": (
            "Новый отчёт МИС сохранён отдельно для проверки. "
            "Действующие управленческие показатели, приёмы, лаборатория, "
            "«Аналитика услуг», кассовый Факт и ООО/ИП не изменены."
        ),
        "summary": summary,
        "rows": updated,
        "backup": backup,
    }


def register_paid_services_upload(app):
    @app.post("/api/uploads/paid-services/preview")
    @permission_required("section5")
    def paid_services_preview():
        require_csrf()
        try:
            return jsonify(_preview())
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    @app.post("/api/uploads/paid-services/commit")
    @permission_required("section5")
    def paid_services_commit():
        require_csrf()
        try:
            return jsonify(_commit(request.form.get("decision") or None))
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409
