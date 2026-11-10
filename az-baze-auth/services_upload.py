import json
from collections import defaultdict

from flask import abort, g, jsonify, request

import advertising_mis_bridge
import cash_payments
import daily_upload
import daily_upload_core as core
import upload_reconcile
from app import audit, db, iso_now, permission_required, require_csrf

user_permissions = core.user_permissions

DENT_CATEGORIES = [
    "Функциональная стоматология / ВНЧС / функциональные аппараты",
    "Зуботехнические / лабораторные этапы",
    "Рентген / КТ / ТРГ",
    "Профессиональная гигиена",
    "Отбеливание",
    "Пародонтология",
    "Эндодонтия — периодонтит",
    "Эндодонтия — пульпит",
    "Реставрации / эстетическая терапия",
    "Лечение кариеса / пломбирование",
    "Немедленная нагрузка / All-on-4 / All-on-6",
    "Костная пластика / синус-лифтинг",
    "Имплантация",
    "Хирургия — удаление зубов",
    "Хирургия — мягкие ткани / пародонтальная хирургия",
    "Ортопедия — диагностика / слепки / сканирование",
    "Ортопедия — протезирование на имплантах",
    "Ортопедия — съёмное протезирование",
    "Ортопедия — коронки / вкладки / виниры",
    "Анестезия / материалы / вспомогательные услуги",
    "Консультации и диагностика",
    "Прочие стоматологические услуги",
]
STRUCTURE_CATEGORIES = [
    "Миофасциальный массаж",
    "Остеопатическое лечение / коррекция",
    "Остеопатия — первичный / повторный приём",
    "Миофункциональная терапия — диагностика",
    "Миофункциональная терапия — лечение / курсы",
    "Нутрициология / метаболизм — первичный приём",
    "Нутрициология / метаболизм — повторный / сопровождение",
    "Гастроэнтерология — первичный приём",
    "Гастроэнтерология — повторный приём",
    "Гипокситерапия / ИГГТ",
    "DIERS / постуральная диагностика",
    "Подиатрия / ортопедические стельки",
    "Нейропсихология",
    "Терапия / врач-терапевт",
    "Прочие / междисциплинарные услуги",
]
LAB_CATEGORIES = [
    "Доставка",
    "Коррекция / переделка / починка",
    "WAX-UP / цифровое моделирование",
    "Элайнеры / сеты",
    "Каппы / сплинты / ретенционные",
    "3D-печать / модели",
    "Функциональные аппараты",
    "Срочное изготовление",
    "Хирургические шаблоны",
    "Перебазировка",
    "Диагностика / сравнение сканов",
]


def _file_from_request():
    file_storage = request.files.get("services")
    if not file_storage or not file_storage.filename:
        raise ValueError("Выберите файл «Выручка по направлениям».")
    raw = file_storage.read()
    if not raw:
        raise ValueError("Выбран пустой файл «Выручка по направлениям».")
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("Размер файла «Выручка по направлениям» превышает 25 МБ.")
    return file_storage.filename, raw


def _canonical_service_day(normalized):
    return {
        "data_date": str(normalized.get("data_date") or ""),
        "items": core._plain(normalized.get("items") or []),
        "retail_items": core._plain(normalized.get("retail_items") or []),
        "lab_invoices": core._plain(normalized.get("lab_invoices") or []),
    }


def _parse(raw, filename):
    parsed, sheet = daily_upload._parse_fixed_raw(raw, filename, "services")
    items, lab_invoices, (year, month) = parsed
    month_key = f"{year:04d}-{month:02d}"
    period = daily_upload._split_period({}, [], items, lab_invoices)
    if not period:
        raise ValueError("Файл «Выручка по направлениям» не содержит данных.")
    dates = [str(row["data_date"]) for row in period]
    if any(value[:7] != month_key for value in dates):
        raise ValueError("Файл «Выручка по направлениям» должен содержать один календарный месяц.")

    days = {}
    for row in period:
        data_date = str(row["data_date"])
        normalized = core._plain(row["normalized"])
        days[data_date] = {
            "data_date": data_date,
            "normalized": normalized,
            "services_hash": daily_upload._payload_hash(
                _canonical_service_day(normalized)
            ),
        }

    return {
        "filename": filename,
        "sheet": sheet,
        "year": year,
        "month": month,
        "month_key": month_key,
        "dates": sorted(days),
        "days": days,
        "period": [
            {"data_date": day, "normalized": days[day]["normalized"]}
            for day in sorted(days)
        ],
    }


def _read_normalized(row):
    if not row:
        return {}
    try:
        value = json.loads(row["normalized_json"])
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _existing_services(conn, month_key):
    result = {}
    rows = conn.execute(
        """
        SELECT data_date,normalized_json
        FROM daily_uploads
        WHERE substr(data_date,1,7)=?
        ORDER BY data_date
        """,
        (month_key,),
    ).fetchall()
    for row in rows:
        data_date = str(row["data_date"])
        normalized = _read_normalized(row)
        canonical = _canonical_service_day(normalized)
        has_services = bool(
            canonical["items"]
            or canonical["retail_items"]
            or canonical["lab_invoices"]
        )
        result[data_date] = {
            "has_services": has_services,
            "services_hash": daily_upload._payload_hash(canonical),
        }
    return result


def compare_services(existing, prepared):
    incoming = prepared["days"]
    result = {"new": [], "identical": [], "conflict": [], "removed": []}
    for data_date, day in incoming.items():
        old = existing.get(data_date)
        if not old or not old.get("has_services"):
            result["new"].append(data_date)
        elif str(old.get("services_hash") or "") == str(day["services_hash"]):
            result["identical"].append(data_date)
        else:
            result["conflict"].append(data_date)

    incoming_set = set(incoming)
    for data_date, old in existing.items():
        if old.get("has_services") and data_date not in incoming_set:
            result["removed"].append(data_date)

    for key in result:
        result[key] = sorted(result[key])
    return result


def _provider_decisions_from_form():
    raw = request.form.get("provider_decisions", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Не удалось прочитать выбранные направления новых врачей.") from exc
    if not isinstance(value, dict):
        raise ValueError("Некорректный список новых врачей.")
    return value


def _empty_service_data(year):
    directions = {}
    for name, categories, doctors in (
        ("Стоматология", DENT_CATEGORIES, core.ident_import.DENTISTS.values()),
        ("Отделение структуры", STRUCTURE_CATEGORIES, core.ident_import.STRUCTURE_DOCTORS.values()),
        ("Лаборатория", LAB_CATEGORIES, core.ident_import.LAB_DOCTORS.values()),
    ):
        directions[name] = {
            "categories": list(categories),
            "doctors": {
                doctor: {category: [] for category in categories}
                for doctor in sorted(set(doctors))
            },
        }
    return {
        "id": f"az-services-{year}-empty-v1",
        "source": "AZ-BAZE initialized from split «Выручка по направлениям» upload",
        "year": year,
        "months": [],
        "directions": directions,
    }


def _ensure_months_through(data, year, month):
    if int(data.get("year") or year) != year:
        raise ValueError("Год файла не совпадает с годом базы «Аналитики услуг».")
    while len(data.setdefault("months", [])) < month:
        next_month = len(data["months"]) + 1
        core._ensure_month(data, year, next_month)
    return month - 1


def _merged_normalized(existing, incoming):
    value = dict(existing or {})
    value.setdefault("overall", {})
    value.setdefault("visits", [])
    value.setdefault("manual_direction_counts", value.get("manual_direction_counts") or {})
    value["data_date"] = incoming["data_date"]
    value["items"] = core._plain(incoming.get("items") or [])
    value["retail_items"] = core._plain(incoming.get("retail_items") or [])
    value["lab_invoices"] = core._plain(incoming.get("lab_invoices") or [])
    value["doctors"] = core._plain(
        daily_upload._doctor_attribution(value.get("visits") or [], value["items"])
    )
    return value


def _clear_services(existing):
    value = dict(existing or {})
    data_date = str(value.get("data_date") or "")
    value["data_date"] = data_date
    value["items"] = []
    value["retail_items"] = []
    value["lab_invoices"] = []
    value["doctors"] = {}
    return value


def _preview():
    perms = user_permissions(g.user)
    if "upload_services" not in perms:
        abort(403)

    filename, raw = _file_from_request()
    prepared = _parse(raw, filename)
    conn = db()
    upload_reconcile.init_schema(conn)
    upload_reconcile.refresh_runtime(conn, core.ident_import, cash_payments)

    unknown = upload_reconcile.detect_unknown_providers(
        prepared["period"], core.ident_import, conn
    )
    comparison = compare_services(
        _existing_services(conn, prepared["month_key"]),
        prepared,
    )
    conflicts = sorted(
        set(comparison["conflict"]) | set(comparison["removed"])
    )
    return {
        "status": "preview",
        "period": {
            "from": prepared["dates"][0],
            "to": prepared["dates"][-1],
        },
        "counts": {key: len(value) for key, value in comparison.items()},
        "conflict_dates": conflicts,
        "unknown_providers": unknown,
        "requires_choice": bool(conflicts),
        "requires_provider_mapping": bool(unknown),
        "can_replace": "upload_replace" in perms,
        "analytics_will_initialize": (
            core.ident_import._load_blob("az-service-analytics-v1") is None
        ),
    }


def _refresh_advertising_mis():
    conn = db()
    now = iso_now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = advertising_mis_bridge.rebuild(conn, now)
        conn.commit()
        return {"status": "ok", "sync": result}
    except Exception as exc:
        conn.rollback()
        audit(
            "advertising_mis_sync_failed",
            target_user_id=g.user["id"],
            details=f"source=services; error={type(exc).__name__}",
        )
        return {"status": "error", "error": type(exc).__name__}


def _commit(decision=None, provider_decisions=None):
    perms = user_permissions(g.user)
    if "upload_services" not in perms:
        abort(403)

    filename, raw = _file_from_request()
    conn = db()
    upload_reconcile.init_schema(conn)
    upload_reconcile.refresh_runtime(conn, core.ident_import, cash_payments)

    prepared = _parse(raw, filename)
    unknown = upload_reconcile.detect_unknown_providers(
        prepared["period"], core.ident_import, conn
    )

    provider_decisions = provider_decisions or {}
    if provider_decisions:
        if "upload_replace" not in perms:
            abort(403)
        missing = [name for name in unknown if name not in provider_decisions]
        if missing:
            raise ValueError(
                "Не выбрано направление для новых врачей: " + ", ".join(missing)
            )
        upload_reconcile.resolve_providers(
            conn, provider_decisions, g.user["id"]
        )
        conn.commit()
        upload_reconcile.refresh_runtime(
            conn, core.ident_import, cash_payments
        )
        prepared = _parse(raw, filename)
        unknown = upload_reconcile.detect_unknown_providers(
            prepared["period"], core.ident_import, conn
        )

    if unknown:
        upload_reconcile.record_pending_providers(
            conn, unknown, filename, actor_id=g.user["id"]
        )
        conn.commit()
        raise ValueError(
            "Обнаружены новые врачи. Сначала определите направление: "
            + ", ".join(unknown)
        )

    comparison = compare_services(
        _existing_services(conn, prepared["month_key"]),
        prepared,
    )
    has_conflict = bool(comparison["conflict"] or comparison["removed"])
    if has_conflict and decision not in {"keep_old", "use_new"}:
        raise ValueError(
            "В «Выручке по направлениям» есть отличия. Выберите "
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
        removed_dates = (
            set(comparison["removed"]) if decision == "use_new" else set()
        )

    if not apply_dates and not removed_dates:
        return {
            "status": "duplicate",
            "message": "«Выручка по направлениям» совпадает; ничего не изменено.",
            "backup": None,
        }

    backup = upload_reconcile.create_db_backup("services-split-upload")
    service_data = core.ident_import._load_blob("az-service-analytics-v1")
    if not service_data:
        service_data = _empty_service_data(prepared["year"])
    month_index = _ensure_months_through(
        service_data, prepared["year"], prepared["month"]
    )
    daily_upload._ensure_extra_service_doctors(
        service_data, prepared["year"], prepared["month"]
    )

    old_through = core._service_through(service_data)
    candidates = list(prepared["dates"])
    if old_through:
        candidates.append(old_through)
    last_known = max(candidates)
    service_data["id"] = (
        f"az-services-{prepared['year']}-through-{last_known}-v1"
    )
    service_data["source"] = (
        f"AZ-BAZE split «Выручка по направлениям» through {last_known}"
    )
    finance_blobs = core.ident_import._pad_financial_months(month_index)

    now = iso_now()
    source = f"AZ-BAZE services-only: {filename}"
    conflict_set = set(comparison["conflict"])

    conn.execute("BEGIN")
    try:
        for data_date in sorted(apply_dates):
            incoming = prepared["days"][data_date]["normalized"]
            existing = conn.execute(
                "SELECT * FROM daily_uploads WHERE data_date=?",
                (data_date,),
            ).fetchone()
            if existing:
                upload_reconcile.archive_daily_row(
                    conn, existing, g.user["id"], "services_source_update"
                )
                normalized = _merged_normalized(
                    _read_normalized(existing), incoming
                )
                conn.execute(
                    """
                    UPDATE daily_uploads
                    SET services_filename=?,services_sha256=?,normalized_json=?,
                        revision=?,uploaded_by=?,uploaded_at=?
                    WHERE data_date=?
                    """,
                    (
                        filename,
                        prepared["days"][data_date]["services_hash"],
                        json.dumps(
                            normalized,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        int(existing["revision"] or 1) + 1,
                        g.user["id"],
                        now,
                        data_date,
                    ),
                )
            else:
                normalized = _merged_normalized({}, incoming)
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
                        "",
                        filename,
                        "",
                        prepared["days"][data_date]["services_hash"],
                        json.dumps(
                            normalized,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        g.user["id"],
                        now,
                    ),
                )

            core._log(
                conn,
                "replaced" if data_date in conflict_set else "imported",
                data_date,
                (
                    f"split_services=1; decision={decision or 'append'}; "
                    f"Выручка по направлениям: лист {prepared['sheet']}"
                ),
                "",
                filename,
            )

        for data_date in sorted(removed_dates):
            existing = conn.execute(
                "SELECT * FROM daily_uploads WHERE data_date=?",
                (data_date,),
            ).fetchone()
            if not existing:
                continue
            upload_reconcile.archive_daily_row(
                conn, existing, g.user["id"], "services_source_removed"
            )
            normalized = _clear_services(_read_normalized(existing))
            conn.execute(
                """
                UPDATE daily_uploads
                SET services_filename=?,services_sha256=?,normalized_json=?,
                    revision=?,uploaded_by=?,uploaded_at=?
                WHERE data_date=?
                """,
                (
                    filename,
                    daily_upload._payload_hash(
                        _canonical_service_day(normalized)
                    ),
                    json.dumps(
                        normalized,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    int(existing["revision"] or 1) + 1,
                    g.user["id"],
                    now,
                    data_date,
                ),
            )

        # Rebuild the service-analytics month from the final merged DB state.
        # This makes append / keep_old / use_new all converge on the same truth
        # and prevents a partial source decision from silently erasing old data.
        daily_upload._reset_service_month(service_data, month_index)
        month_rows = conn.execute(
            """
            SELECT normalized_json
            FROM daily_uploads
            WHERE substr(data_date,1,7)=?
            ORDER BY data_date
            """,
            (prepared["month_key"],),
        ).fetchall()
        for month_row in month_rows:
            try:
                month_normalized = json.loads(month_row["normalized_json"])
            except (TypeError, ValueError):
                continue
            core._apply_service_items(
                service_data,
                month_normalized.get("items") or [],
                prepared["year"],
                prepared["month"],
                +1,
            )

        management = core._rebuild_management(
            prepared["month_key"], source
        )
        cash_payments.overlay_record_map(
            conn, prepared["month_key"], management
        )
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
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    g.user["id"],
                    now,
                ),
            )

        cash_payments.overlay_stored_month(
            conn, prepared["month_key"], g.user["id"], now
        )
        core._save_blob(
            conn, "az-service-analytics-v1", service_data, now
        )
        for key, value in finance_blobs.items():
            core._save_blob(conn, key, value, now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    audit(
        "services_reports_reconciled",
        target_user_id=g.user["id"],
        details=(
            f"file={filename}; sheet={prepared['sheet']}; "
            f"month={prepared['month_key']}; decision={decision or 'append'}; "
            f"new={len(comparison['new'])}; "
            f"identical={len(comparison['identical'])}; "
            f"conflict={len(comparison['conflict'])}; "
            f"removed={len(comparison['removed'])}; backup={backup}"
        ),
    )
    return {
        "status": "replaced" if decision == "use_new" and has_conflict else "imported",
        "message": (
            "«Выручка по направлениям» загружена отдельно. "
            "Данные услуг, врачей и аналитики услуг пересчитаны; "
            "завершённые приёмы и денежный Факт сохранены независимо."
        ),
        "backup": backup,
    }


def register_services_upload(app):
    @app.post("/api/uploads/services/preview")
    @permission_required("section5")
    def services_upload_preview():
        require_csrf()
        try:
            return jsonify(_preview())
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    @app.post("/api/uploads/services/commit")
    @permission_required("section5")
    def services_upload_commit():
        require_csrf()
        try:
            result = _commit(
                request.form.get("decision") or None,
                _provider_decisions_from_form(),
            )
            result["advertising_sync"] = _refresh_advertising_mis()
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409
