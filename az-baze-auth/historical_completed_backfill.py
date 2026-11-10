import hashlib
import html
import io
import json
import zipfile
from collections import Counter, defaultdict

from flask import abort, g, jsonify, request

import advertising_mis_bridge
import daily_upload
import daily_upload_core as core
import upload_reconcile
from app import audit, csrf_token, db, iso_now, permission_required, require_csrf, user_permissions


HISTORY_BUNDLE_SCHEMA = "az_completed_history_source_bundle_v1"
HISTORY_BUNDLE_MAX_FILES = 100
HISTORY_BUNDLE_MAX_MEMBER_BYTES = 25 * 1024 * 1024


def _require_history_access():
    perms = user_permissions(g.user)
    if (
        not g.user
        or not bool(g.user["is_admin"])
        or "upload_completed" not in perms
        or "upload_replace" not in perms
    ):
        abort(403)
    return perms


def _bundle_from_request():
    file_storage = request.files.get("bundle")
    if not file_storage or not file_storage.filename:
        raise ValueError("Выберите ZIP-пакет исторических завершённых приёмов.")
    raw = file_storage.read()
    if not raw:
        raise ValueError("Выбран пустой ZIP-пакет.")
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("Размер ZIP-пакета превышает 25 МБ.")
    return file_storage.filename, raw


def _source_days(raw, filename):
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
        # A long IDENT export may contain old days only as collapsed daily
        # summaries. Without type/detail rows those dates are not authoritative
        # and must not erase a more complete source.
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


def _kind_counts(day):
    overall = day.get("overall") or {}
    counts = overall.get(day.get("data_date")) or {}
    return {str(key): int(value or 0) for key, value in counts.items()}


def _visit_counter(day):
    return Counter(
        (
            str(row.get("patient") or ""),
            str(row.get("kind") or ""),
        )
        for row in (day.get("visits") or [])
    )


def _day_subset(left, right):
    left_counts = _kind_counts(left)
    right_counts = _kind_counts(right)
    if any(
        left_counts.get(kind, 0) > right_counts.get(kind, 0)
        for kind in set(left_counts) | set(right_counts)
    ):
        return False
    left_visits = _visit_counter(left)
    right_visits = _visit_counter(right)
    return all(
        count <= right_visits.get(key, 0)
        for key, count in left_visits.items()
    )


def merge_history_day(existing, candidate):
    if (
        _kind_counts(existing) == _kind_counts(candidate)
        and _visit_counter(existing) == _visit_counter(candidate)
    ):
        return existing, "duplicate"
    if _day_subset(candidate, existing):
        return existing, "subset_ignored"
    if _day_subset(existing, candidate):
        return candidate, "superset_replaced"
    raise ValueError(
        "Конфликтующие выгрузки «Завершённых приёмов» за "
        f"{candidate.get('data_date')}: "
        f"{existing.get('source_filename')} / {candidate.get('source_filename')}."
    )


def parse_history_bundle(raw, filename):
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
            name
            for name in names
            if name.lower().endswith(".xlsx") and not name.endswith("/")
        ]
        if not members or len(members) > HISTORY_BUNDLE_MAX_FILES:
            raise ValueError("Некорректное количество XLSX-файлов в пакете.")
        if len(members) != int(manifest.get("source_file_count") or 0):
            raise ValueError("Количество файлов в ZIP не совпадает с manifest.json.")

        canonical = {}
        duplicate_days = 0
        subset_days = 0
        verified_sources = 0
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
            verified_sources += 1

            for data_date, candidate in _source_days(member_raw, basename).items():
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
        type_counts.update(_kind_counts(day))

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
        "bundle_sha256": hashlib.sha256(raw).hexdigest(),
        "dates": dates,
        "months": sorted({value[:7] for value in dates}),
        "days": canonical,
        "canonical_visits": canonical_visits,
        "type_counts": dict(type_counts),
        "source_file_count": verified_sources,
        "duplicate_days": duplicate_days,
        "subset_days": subset_days,
    }


def _existing_completed_dates(conn, months):
    result = {}
    for month in months:
        rows = conn.execute(
            """
            SELECT data_date,normalized_json
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
            except (TypeError, ValueError, json.JSONDecodeError):
                normalized = {}
            overall = normalized.get("overall") if isinstance(normalized, dict) else {}
            visits = normalized.get("visits") if isinstance(normalized, dict) else []
            canonical_hash = daily_upload._payload_hash(
                {
                    "data_date": data_date,
                    "overall": overall or {},
                    "visits": visits or [],
                }
            )
            result[data_date] = {
                "completed_hash": canonical_hash,
                "has_completed": bool(
                    any((value or {}) for value in (overall or {}).values())
                    or visits
                ),
            }
    return result


def compare_history(conn, prepared):
    existing = _existing_completed_dates(conn, prepared.get("months") or [])
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


def _read_normalized(row):
    if not row:
        return {}
    try:
        value = json.loads(row["normalized_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _merged_normalized(existing_normalized, day):
    value = dict(existing_normalized or {})
    value.setdefault("items", [])
    value.setdefault("retail_items", [])
    value.setdefault("lab_invoices", [])
    value["doctors"] = {}
    value["data_date"] = day["data_date"]
    value["overall"] = day["overall"]
    value["visits"] = day["visits"]
    return value


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
            "historical_completed_update",
        )
        normalized = _merged_normalized(_read_normalized(existing), day)
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

    normalized = _merged_normalized(
        {
            "data_date": data_date,
            "items": [],
            "retail_items": [],
            "lab_invoices": [],
            "doctors": {},
        },
        day,
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


def merge_completed_management(existing, rebuilt, source):
    result = dict(existing or {})
    result["date"] = rebuilt["date"]
    result["primary"] = int(rebuilt.get("primary") or 0)
    result["repeat"] = int(rebuilt.get("repeat") or 0)
    result["_completed_source"] = source
    result["_completed_aggregation"] = "month_to_date"
    return result


def _preview():
    _require_history_access()
    filename, raw = _bundle_from_request()
    prepared = parse_history_bundle(raw, filename)
    comparison = compare_history(db(), prepared)
    return {
        "status": "preview",
        "period": {"from": prepared["dates"][0], "to": prepared["dates"][-1]},
        "source_file_count": prepared["source_file_count"],
        "days": len(prepared["dates"]),
        "visits": prepared["canonical_visits"],
        "type_counts": prepared["type_counts"],
        "duplicates_collapsed": prepared["duplicate_days"],
        "subsets_collapsed": prepared["subset_days"],
        "bundle_sha256": prepared["bundle_sha256"],
        "counts": {
            "new": len(comparison["new"]),
            "identical": len(comparison["identical"]),
            "conflict": len(comparison["conflict"]),
        },
        "conflict_dates": comparison["conflict"],
    }


def _commit(decision=None):
    _require_history_access()
    filename, raw = _bundle_from_request()
    prepared = parse_history_bundle(raw, filename)
    conn = db()
    comparison = compare_history(conn, prepared)

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

    backup = upload_reconcile.create_db_backup("historical-completed")
    now = iso_now()
    source = (
        f"AZ-BAZE migration completed visits: {prepared['dates'][0]}.."
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
                (
                    "historical_completed_migration=1; "
                    f"bundle_sha256={prepared['bundle_sha256']}; "
                    "services_preserved=1"
                ),
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
        "historical_completed_migration",
        target_user_id=g.user["id"],
        details=(
            f"bundle_sha256={prepared['bundle_sha256']}; "
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



def inject_uploads_page(page_html):
    try:
        _require_history_access()
    except Exception:
        return page_html

    marker = '<form method="post" action="/uploads/cash"'
    if marker not in page_html or 'data-az-completed-history-form' in page_html:
        return page_html

    csrf = html.escape(csrf_token(), quote=True)
    block = f"""
<style id="az-completed-history-inline-style">
.az-history-box{{margin-top:12px;border:1px solid #ded5c8;border-left:4px solid #44634f;border-radius:11px;background:#fff;padding:14px}}
.az-history-box h3{{margin:0 0 5px;font-size:14px}}
.az-history-box p{{margin:0 0 10px;color:#6c746d;font-size:11px;line-height:1.45}}
.az-history-box input[type=file]{{width:100%;padding:9px;border:1px solid #d8cdbb;border-radius:8px;background:#fffdf8;font:500 11px Montserrat,Arial,sans-serif}}
.az-history-box .result{{margin-top:12px}}
</style>
<form method="post" enctype="multipart/form-data" class="az-history-box" data-az-completed-history-form>
  <input type="hidden" name="csrf" value="{csrf}">
  <h3>История завершённых приёмов</h3>
  <p>Восстановление истории после переезда. Здесь нужен только один ZIP; файл «Выручка по направлениям» не требуется.</p>
  <input type="file" name="bundle" accept=".zip,application/zip" required>
  <button class="btn primary" type="submit" style="margin-top:12px">Восстановить историю</button>
  <div class="hint">Сначала пакет будет проверен. Перед записью автоматически создаётся резервная копия базы.</div>
  <div id="completedHistoryClientResult" class="result hidden"></div>
</form>
<script id="az-completed-history-inline-js">
(()=>{
  const form=document.querySelector('[data-az-completed-history-form]');
  if(!form)return;
  const result=document.getElementById('completedHistoryClientResult');
  const button=form.querySelector('button[type="submit"]');
  const show=(text,ok=true)=>{
    if(!result)return;
    result.textContent=text||'';
    result.className='result '+(ok?'ok':'error');
  };
  const send=async(url,extra={})=>{
    const data=new FormData(form);
    Object.entries(extra).forEach(([key,value])=>data.set(key,value));
    const response=await fetch(url,{method:'POST',body:data,credentials:'same-origin'});
    let payload={};
    try{payload=await response.json()}catch{}
    if(!response.ok)throw new Error(payload.message||'Сервер не принял исторический пакет.');
    return payload;
  };
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    if(button)button.disabled=true;
    show('Проверяю исторические завершённые приёмы…',true);
    try{
      const preview=await send('/api/uploads/completed-history/preview');
      const counts=preview.counts||{};
      const types=preview.type_counts||{};
      const summary=
        'Проверено: '+(preview.visits||0)+' приёмов, '+
        (preview.days||0)+' дней, период '+preview.period.from+' — '+preview.period.to+
        '. Первичные '+(types['Первичные']||0)+
        ', повторные '+(types['Повторные']||0)+
        ', отконсультированные '+(types['Отконсультированные']||0)+
        '. Новые дни '+(counts.new||0)+
        ', совпали '+(counts.identical||0)+
        ', отличаются '+(counts.conflict||0)+'.';
      show(summary,true);
      if(!window.confirm(summary+'\n\nЗаписать историю в AZ BAZE? Перед записью будет создан backup.')){
        show('Проверка завершена. Запись не выполнялась.',true);
        return;
      }
      show('Создаю backup и восстанавливаю историю…',true);
      const decision=(counts.conflict||0)?'use_new':'append';
      const out=await send('/api/uploads/completed-history/commit',{decision});
      show((out.message||'История восстановлена.')+(out.backup?' Backup: '+out.backup:''),true);
      setTimeout(()=>location.reload(),1200);
    }catch(error){
      show(error.message||'Ошибка восстановления истории.',false);
    }finally{
      if(button)button.disabled=false;
    }
  });
})();
</script>
"""
    return page_html.replace(marker, block + "\n" + marker, 1)

def register(app):
    @app.post("/api/uploads/completed-history/preview")
    @permission_required("section5")
    def historical_completed_preview():
        require_csrf()
        try:
            return jsonify(_preview())
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    @app.post("/api/uploads/completed-history/commit")
    @permission_required("section5")
    def historical_completed_commit():
        require_csrf()
        try:
            return jsonify(_commit(request.form.get("decision") or None))
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409
