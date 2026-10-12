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


def _provider_mappings(conn):
    # Read the registry without helper schema-init: this is also used inside
    # the transaction that stores the selected classifications and the report.
    maps = {
        "dent": dict(ident_import.DENTISTS),
        "structure": dict(ident_import.STRUCTURE_DOCTORS),
        "lab": dict(ident_import.LAB_DOCTORS),
    }
    ignored = set(ident_import.KNOWN_STAFF)
    for names in maps.values():
        ignored.difference_update(names)
    for source, display, direction in conn.execute(
        "SELECT source_name,display_name,direction FROM provider_registry "
        "WHERE direction<>'pending' ORDER BY source_name"
    ):
        for names in maps.values():
            names.pop(source, None)
        ignored.discard(source)
        if direction in maps:
            maps[direction][source] = display
        elif direction == "ignore":
            ignored.add(source)
    maps["ignore"] = ignored
    return maps


def _summary(report, conn):
    maps = _provider_mappings(conn)
    values = paid_services.direction_summary(
        report,
        maps["dent"],
        maps["structure"],
        maps["lab"],
        ignored=maps["ignore"],
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


def _state_and_revision(conn, report):
    row = paid_services.existing_report(conn, report)
    if not row:
        return "new", "new"
    revision = hashlib.sha256(str(row[2]).encode("utf-8")).hexdigest()
    if paid_services._same_payload(row[2], paid_services.compact_report(report)):
        return "identical", revision
    return "conflict", revision


def _existing_state(conn, report, source_sha):
    return _state_and_revision(conn, report)[0]


def _existing_revision(conn, report):
    return _state_and_revision(conn, report)[1]


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


def _save_provider_decisions(conn, decisions, required, actor_id, now):
    """Classify only the unresolved names in this file, in the caller's transaction."""
    if not required:
        return False
    missing = [name for name in required if name not in decisions]
    if missing:
        raise ValueError("Не выбрано направление для новых сотрудников: " + ", ".join(missing))
    clean = {}
    for source_name in required:
        source = str(source_name or "").strip()
        raw = decisions[source_name]
        if isinstance(raw, dict):
            direction = str(raw.get("direction") or "").strip()
            display = str(raw.get("display_name") or source).strip() or source
        else:
            direction = str(raw or "").strip()
            display = source
        if not source or direction not in {"dent", "structure", "lab", "ignore"}:
            raise ValueError(f"Для врача «{source}» не выбрано допустимое направление.")
        clean[source] = (direction, display)

    for source, (direction, display) in clean.items():
        row = conn.execute(
            "SELECT direction FROM provider_registry WHERE source_name=?", (source,),
        ).fetchone()
        if row:
            if row[0] != "pending":
                raise ValueError("Классификация сотрудника уже изменилась. Повторите проверку файла.")
            conn.execute(
                """
                UPDATE provider_registry
                SET display_name=?,direction=?,last_seen_at=?,confirmed_by=?,confirmed_at=?
                WHERE source_name=? AND direction='pending'
                """,
                (display, direction, now, actor_id, now, source),
            )
        else:
            conn.execute(
                """
                INSERT INTO provider_registry(
                    source_name,display_name,direction,source_filename,
                    first_seen_at,last_seen_at,confirmed_by,confirmed_at
                ) VALUES(?,?,?,NULL,?,?,?,?)
                """,
                (source, display, direction, now, now, actor_id, now),
            )
    return bool(clean)


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
        period_start=request.form.get("period_start"),
        period_end=request.form.get("period_end"),
    )
    source_sha = hashlib.sha256(raw).hexdigest()
    summary = _summary(report, conn)
    state = _existing_state(conn, report, source_sha)
    return perms, conn, filename, raw, report, source_sha, summary, state


def _preview():
    perms, conn, filename, _raw, report, _source_sha, summary, state = _prepare()
    state, revision = _state_and_revision(conn, report)
    unknown = summary["unknown_providers"]

    return {
        "status": "preview",
        "period": {
            "from": report["period_start"],
            "to": report["period_end"],
        },
        "month": report["month"],
        "revision": revision,
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
    initial_revision = _existing_revision(conn, report)
    expected_revision = request.form.get("expected_revision")
    decisions = _provider_decisions()
    if decision == "use_new" and "upload_replace" not in perms:
        abort(403)
    backup = None
    now = iso_now()
    providers_changed = False
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Recheck after taking the write lock, including the version the user
        # actually saw in preview. A concurrent import must never be overwritten.
        revision = _existing_revision(conn, report)
        if revision != (expected_revision or initial_revision):
            raise ValueError("Сохранённый отчёт уже изменился. Повторите проверку файла.")
        state = _existing_state(conn, report, source_sha)
        summary = _summary(report, conn)
        if state == "identical" or (state == "conflict" and decision == "keep_old"):
            conn.rollback()
            return {
                "status": "duplicate",
                "message": (
                    "Новый отчёт МИС совпадает с сохранённым; ничего не изменено."
                    if state == "identical" else
                    "Сохранена ранее загруженная версия нового отчёта МИС."
                ),
                "summary": summary,
            }
        if state == "conflict":
            if decision != "use_new":
                raise ValueError(
                    "За этот период уже сохранена другая версия нового отчёта МИС. "
                    "Выберите «Сохранить старые» или «Загрузить новые»."
                )
            if not expected_revision:
                raise ValueError("Перед заменой сохранённого отчёта повторите проверку файла.")
            # No writes have occurred yet. The separate read-only connection
            # backs up the version protected by our write lock.
            backup = upload_reconcile.create_db_backup("paid-services-reimport")
        if summary["unknown_providers"]:
            if "upload_replace" not in perms:
                abort(403)
            providers_changed = _save_provider_decisions(
                conn, decisions, summary["unknown_providers"], g.user["id"], now,
            )
            summary = _summary(report, conn)
            if summary["unknown_providers"]:
                raise ValueError("Не для всех новых сотрудников выбрано направление.")
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

    if providers_changed:
        upload_reconcile.refresh_runtime(conn, ident_import, cash_payments)

    audit(
        "paid_services_stored",
        target_user_id=g.user["id"],
        details=(
            f"file={filename}; period_start={report['period_start']}; period_end={report['period_end']}; "
            f"source_sha256={source_sha}; "
            f"decision={decision or 'append'}; status={status}; "
            f"paid={summary['paid']}; reports=0; analytics_updated=0; backup={backup or ''}"
        ),
    )
    return {
        "status": "replaced" if status == "replaced" else "imported",
        "message": (
            "Отчёт МИС за " + ".".join(reversed(report["period_start"].split("-"))) +
            " — " + ".".join(reversed(report["period_end"].split("-"))) +
            " сохранён. Суммы врачей взяты из графы «Оплачено»."
        ),
        "period": {"from": report["period_start"], "to": report["period_end"]},
        "summary": summary,
        "rows": 0,
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
