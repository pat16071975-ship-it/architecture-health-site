import hashlib
import io
import json
import zipfile
from collections import Counter, defaultdict
from flask import abort, g, jsonify, render_template, request

import advertising_mis_bridge
import cash_payments
import daily_upload
import daily_upload_core as core
import paid_services
import upload_reconcile
from app import audit, csrf_token, db, iso_now, permission_required, require_csrf, user_permissions



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
        SELECT data_date,completed_sha256 AS completed_hash,normalized_json
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
        # Discounts/gross revenue are no longer sourced from the completed-visits
        # path. Finrez receives them from the economists' workbook. If that source
        # is absent, the UI must show no data rather than a synthetic zero.
        for record in management.values():
            record["discountDataComplete"] = False
            record["grossRevenue"] = 0
            record["discountAmount"] = 0
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
            details=f"source=completed; error={type(exc).__name__}",
        )
        return {"status": "error", "error": type(exc).__name__}



HISTORY_BUNDLE_SCHEMA = "az_completed_history_source_bundle_v1"
HISTORY_BUNDLE_MAX_FILES = 100
HISTORY_BUNDLE_MAX_MEMBER_BYTES = 25 * 1024 * 1024


def _history_bundle_from_request():
    file_storage = request.files.get("bundle")
    if not file_storage or not file_storage.filename:
        raise ValueError("Выберите ZIP-пакет исторических завершённых приёмов.")
    raw = file_storage.read()
    if not raw:
        raise ValueError("Выбран пустой ZIP-пакет.")
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("Размер ZIP-пакета превышает 25 МБ.")
    return file_storage.filename, raw


def _history_source_days(raw, filename):
    parsed, _sheet = daily_upload._parse_fixed_raw(raw, filename, "completed")
    overall, visits = parsed
    visits_by_date = defaultdict(list)
    for row in visits:
        data_date = str(row.get("date") or "")
        if data_date:
            visits_by_date[data_date].append(core._plain(row))

    days = {}
    for data_date, raw_counts in overall.items():
        counts = {
            str(kind): int(value or 0)
            for kind, value in (raw_counts or {}).items()
            if int(value or 0) > 0
        }
        # Large IDENT exports can keep old days only as collapsed daily summary
        # rows. Those rows have no type/detail layer and must never erase a more
        # complete source for the same date.
        if not counts:
            continue
        day_visits = visits_by_date.get(str(data_date), [])
        expected = sum(counts.values())
        if len(day_visits) != expected:
            raise ValueError(
                f"{filename}: за {data_date} найдено {len(day_visits)} строк приёмов "
                f"при контрольном итоге {expected}."
            )
        day_overall = {str(data_date): counts}
        completed_hash = daily_upload._payload_hash(
            {
                "data_date": str(data_date),
                "overall": day_overall,
                "visits": day_visits,
            }
        )
        days[str(data_date)] = {
            "data_date": str(data_date),
            "overall": day_overall,
            "visits": day_visits,
            "completed_hash": completed_hash,
            "source_filename": filename,
        }
    return days


def _history_kind_counts(day):
    overall = day.get("overall") or {}
    counts = overall.get(day.get("data_date")) or {}
    return {str(key): int(value or 0) for key, value in counts.items()}


def _history_visit_counter(day):
    return Counter(
        (
            str(row.get("patient") or ""),
            str(row.get("kind") or ""),
        )
        for row in (day.get("visits") or [])
    )


def _history_day_subset(left, right):
    left_counts = _history_kind_counts(left)
    right_counts = _history_kind_counts(right)
    if any(
        left_counts.get(kind, 0) > right_counts.get(kind, 0)
        for kind in set(left_counts) | set(right_counts)
    ):
        return False
    left_visits = _history_visit_counter(left)
    right_visits = _history_visit_counter(right)
    return all(
        count <= right_visits.get(key, 0)
        for key, count in left_visits.items()
    )


def merge_history_day(existing, candidate):
    same_counts = _history_kind_counts(existing) == _history_kind_counts(candidate)
    same_visits = _history_visit_counter(existing) == _history_visit_counter(candidate)
    if same_counts and same_visits:
        return existing, "duplicate"
    if _history_day_subset(candidate, existing):
        return existing, "subset_ignored"
    if _history_day_subset(existing, candidate):
        return candidate, "superset_replaced"
    raise ValueError(
        "Конфликтующие выгрузки «Завершённых приёмов» за "
        f"{candidate.get('data_date')}: "
        f"{existing.get('source_filename')} / {candidate.get('source_filename')}."
    )


def _parse_history_bundle(raw, filename):
    stream = io.BytesIO(raw)
    if not zipfile.is_zipfile(stream):
        raise ValueError("Исторический пакет должен быть ZIP-файлом.")

    stream.seek(0)
    try:
        archive = zipfile.ZipFile(stream)
    except zipfile.BadZipFile as exc:
        raise ValueError("ZIP-пакет повреждён.") from exc

    with archive:
        names = archive.namelist()
        if "manifest.json" not in names:
            raise ValueError("В ZIP-пакете отсутствует manifest.json.")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("manifest.json повреждён.") from exc

        if manifest.get("schema") != HISTORY_BUNDLE_SCHEMA:
            raise ValueError("Неподдерживаемый формат исторического пакета.")

        manifest_sources = {
            str(item.get("name") or ""): item
            for item in (manifest.get("source_files") or [])
            if isinstance(item, dict) and str(item.get("name") or "")
        }
        members = [
            name for name in names
            if name.lower().endswith(".xlsx") and not name.endswith("/")
        ]
        if not members or len(members) > HISTORY_BUNDLE_MAX_FILES:
            raise ValueError("Некорректное количество XLSX-файлов в пакете.")
        if len(members) != int(manifest.get("source_file_count") or 0):
            raise ValueError("Количество файлов в ZIP не совпадает с manifest.json.")

        canonical = {}
        duplicate_days = 0
        subset_days = 0
        source_sha256 = {}
        for member in members:
            basename = member.rsplit("/", 1)[-1]
            spec = manifest_sources.get(basename)
            if not spec:
                raise ValueError(f"{basename}: файл отсутствует в manifest.json.")
            info = archive.getinfo(member)
            if info.file_size > HISTORY_BUNDLE_MAX_MEMBER_BYTES:
                raise ValueError(f"{basename}: размер файла превышает 25 МБ.")
            member_raw = archive.read(member)
            digest = hashlib.sha256(member_raw).hexdigest()
            if digest != str(spec.get("sha256") or ""):
                raise ValueError(f"{basename}: SHA256 не совпадает с manifest.json.")
            source_sha256[basename] = digest
            for data_date, candidate in _history_source_days(member_raw, basename).items():
                old = canonical.get(data_date)
                if old is None:
                    canonical[data_date] = candidate
                    continue
                merged, action = merge_history_day(old, candidate)
                canonical[data_date] = merged
                if action == "duplicate":
                    duplicate_days += 1
                elif action in {"subset_ignored", "superset_replaced"}:
                    subset_days += 1

    if not canonical:
        raise ValueError("ZIP-пакет не содержит детальных завершённых приёмов.")

    dates = sorted(canonical)
    canonical_visits = sum(
        len(item.get("visits") or [])
        for item in canonical.values()
    )
    type_counts = Counter()
    for day in canonical.values():
        type_counts.update(_history_kind_counts(day))

    expected_visits = int(manifest.get("canonical_visits") or 0)
    expected_days = int(manifest.get("canonical_days") or 0)
    expected_from = str(manifest.get("period_from") or "")
    expected_to = str(manifest.get("period_to") or "")
    if expected_visits and canonical_visits != expected_visits:
        raise ValueError(
            f"Контрольный итог приёмов не совпал: {canonical_visits} вместо {expected_visits}."
        )
    if expected_days and len(dates) != expected_days:
        raise ValueError(
            f"Контрольный итог дней не совпал: {len(dates)} вместо {expected_days}."
        )
    if expected_from and dates[0] != expected_from:
        raise ValueError("Начальная дата пакета не совпала с manifest.json.")
    if expected_to and dates[-1] != expected_to:
        raise ValueError("Конечная дата пакета не совпала с manifest.json.")

    return {
        "filename": filename,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "dates": dates,
        "months": sorted({value[:7] for value in dates}),
        "days": canonical,
        "canonical_visits": canonical_visits,
        "type_counts": dict(type_counts),
        "source_file_count": len(source_sha256),
        "duplicate_days": duplicate_days,
        "subset_days": subset_days,
    }


def compare_history_completed(conn, prepared):
    existing = {}
    for month in prepared.get("months") or []:
        existing.update(_existing_completed_dates(conn, month))
    comparison = {"new": [], "identical": [], "conflict": []}
    for data_date, day in (prepared.get("days") or {}).items():
        old = existing.get(data_date)
        if not old:
            comparison["new"].append(data_date)
        elif str(old.get("completed_hash") or "") == str(day.get("completed_hash") or ""):
            comparison["identical"].append(data_date)
        else:
            comparison["conflict"].append(data_date)
    for key in comparison:
        comparison[key] = sorted(comparison[key])
    return comparison


def merge_completed_management(existing, rebuilt, source):
    result = dict(existing or {})
    result["date"] = rebuilt["date"]
    result["primary"] = int(rebuilt.get("primary") or 0)
    result["repeat"] = int(rebuilt.get("repeat") or 0)
    result["_completed_source"] = source
    result["_completed_aggregation"] = "month_to_date"
    return result


def _history_preview():
    perms = user_permissions(g.user)
    if "upload_completed" not in perms or "upload_replace" not in perms:
        abort(403)
    filename, raw = _history_bundle_from_request()
    prepared = _parse_history_bundle(raw, filename)
    conn = db()
    comparison = compare_history_completed(conn, prepared)
    return {
        "status": "preview",
        "period": {"from": prepared["dates"][0], "to": prepared["dates"][-1]},
        "source_file_count": prepared["source_file_count"],
        "days": len(prepared["dates"]),
        "visits": prepared["canonical_visits"],
        "type_counts": prepared["type_counts"],
        "duplicates_collapsed": prepared["duplicate_days"],
        "subsets_collapsed": prepared["subset_days"],
        "counts": {
            "new": len(comparison["new"]),
            "identical": len(comparison["identical"]),
            "conflict": len(comparison["conflict"]),
        },
        "conflict_dates": comparison["conflict"],
        "can_replace": True,
    }


def _history_commit(decision=None):
    perms = user_permissions(g.user)
    if "upload_completed" not in perms or "upload_replace" not in perms:
        abort(403)

    filename, raw = _history_bundle_from_request()
    prepared = _parse_history_bundle(raw, filename)
    conn = db()
    comparison = compare_history_completed(conn, prepared)
    if comparison["conflict"] and decision != "use_new":
        raise ValueError(
            "В историческом периоде есть отличия от уже сохранённых завершённых приёмов. "
            "Для замены подтвердите «Загрузить новые»."
        )

    apply_dates = set(comparison["new"])
    if decision == "use_new":
        apply_dates.update(comparison["conflict"])
    if not apply_dates:
        return {
            "status": "duplicate",
            "message": "Исторические завершённые приёмы уже совпадают; ничего не изменено.",
            "backup": None,
        }

    backup = upload_reconcile.create_db_backup("completed-history")
    now = iso_now()
    source = (
        f"AZ-BAZE historical completed visits: {prepared['dates'][0]}.."
        f"{prepared['dates'][-1]}"
    )
    conflict_set = set(comparison["conflict"])
    conn.execute("BEGIN")
    try:
        for data_date in sorted(apply_dates):
            _insert_or_update_day(conn, prepared, data_date, g.user["id"], now)
            core._log(
                conn,
                "replaced" if data_date in conflict_set else "imported",
                data_date,
                "historical_completed_zip=1; services_preserved=1",
                prepared["filename"],
                "",
            )

        for month in prepared["months"]:
            rebuilt = core._rebuild_management(month, source)
            for data_date, record in rebuilt.items():
                row = conn.execute(
                    "SELECT payload FROM report_data WHERE date=?",
                    (data_date,),
                ).fetchone()
                existing = {}
                if row:
                    try:
                        existing = json.loads(row["payload"])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        existing = {}
                merged = merge_completed_management(existing, record, source)
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
                        json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
                        g.user["id"],
                        now,
                    ),
                )

        advertising_sync = advertising_mis_bridge.rebuild(conn, now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    audit(
        "completed_history_bundle_reconciled",
        target_user_id=g.user["id"],
        details=(
            f"file={prepared['filename']}; "
            f"period={prepared['dates'][0]}..{prepared['dates'][-1]}; "
            f"files={prepared['source_file_count']}; days={len(prepared['dates'])}; "
            f"visits={prepared['canonical_visits']}; "
            f"new={len(comparison['new'])}; identical={len(comparison['identical'])}; "
            f"conflict={len(comparison['conflict'])}; decision={decision or 'append'}; "
            f"backup={backup}"
        ),
    )
    return {
        "status": "replaced" if comparison["conflict"] else "imported",
        "message": (
            f"Исторические завершённые приёмы восстановлены: "
            f"{prepared['canonical_visits']} приёмов за "
            f"{prepared['dates'][0]}–{prepared['dates'][-1]}."
        ),
        "backup": backup,
        "advertising_sync": advertising_sync,
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
            result = _commit(request.form.get("decision") or None)
            result["advertising_sync"] = _refresh_advertising_mis()
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409


    @app.get("/uploads/completed-history/")
    @permission_required("section5")
    def completed_history_page():
        perms = user_permissions(g.user)
        if "upload_completed" not in perms or "upload_replace" not in perms:
            abort(403)
        return render_template("completed_history_upload.html", csrf=csrf_token())

    @app.post("/api/uploads/completed-history/preview")
    @permission_required("section5")
    def completed_history_preview():
        require_csrf()
        try:
            return jsonify(_history_preview())
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    @app.post("/api/uploads/completed-history/commit")
    @permission_required("section5")
    def completed_history_commit():
        require_csrf()
        try:
            return jsonify(_history_commit(request.form.get("decision") or None))
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409
