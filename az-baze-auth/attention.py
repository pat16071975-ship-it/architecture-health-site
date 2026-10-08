import calendar
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Response, g, jsonify, render_template

from app import db, permission_required, user_permissions
import upload_reconcile


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _fmt_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return str(value)


def _safe_json(value):
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def _clinic_today(conn):
    try:
        if _table_exists(conn, "clinics"):
            row = conn.execute(
                "SELECT timezone FROM clinics WHERE status='active' ORDER BY id LIMIT 1"
            ).fetchone()
            if row and row["timezone"]:
                return datetime.now(timezone.utc).astimezone(ZoneInfo(row["timezone"])).date()
    except Exception:
        pass
    return datetime.now(timezone.utc).date()


def _latest_date(conn, table):
    if not _table_exists(conn, table):
        return None
    row = conn.execute(f"SELECT MAX(data_date) AS data_date FROM {table}").fetchone()
    return str(row["data_date"]) if row and row["data_date"] else None


def _daily_dates(conn, year, month):
    if not _table_exists(conn, "daily_uploads"):
        return []
    prefix = f"{year:04d}-{month:02d}"
    rows = conn.execute(
        "SELECT data_date FROM daily_uploads WHERE substr(data_date,1,7)=? ORDER BY data_date",
        (prefix,),
    ).fetchall()
    return [str(row["data_date"]) for row in rows]


def _report_dates(conn, year, month):
    if not _table_exists(conn, "report_data"):
        return []
    prefix = f"{year:04d}-{month:02d}"
    rows = conn.execute(
        "SELECT date FROM report_data WHERE substr(date,1,7)=? ORDER BY date",
        (prefix,),
    ).fetchall()
    return [str(row["date"]) for row in rows]


def _report_for_date(conn, data_date):
    row = conn.execute(
        "SELECT payload FROM report_data WHERE date=?",
        (data_date,),
    ).fetchone()
    if row:
        return _safe_json(row["payload"])
    row = conn.execute(
        "SELECT payload FROM report_data WHERE date<=? AND substr(date,1,7)=substr(?,1,7) ORDER BY date DESC LIMIT 1",
        (data_date, data_date),
    ).fetchone()
    return _safe_json(row["payload"]) if row else {}


def _management_monthly_plan(conn, month):
    if not _table_exists(conn, "report_blobs"):
        return None
    row = conn.execute(
        "SELECT payload FROM report_blobs WHERE key=?",
        ("az-management-monthly-plan-v1",),
    ).fetchone()
    if not row:
        return None
    parsed = _safe_json(row["payload"])
    if parsed.get("version") != 1 or not isinstance(parsed.get("months"), dict):
        return None
    raw = parsed["months"].get(month)
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _direction_values(record):
    dentists = record.get("dentists") if isinstance(record.get("dentists"), dict) else {}
    clinic_docs = record.get("clinicDocs") if isinstance(record.get("clinicDocs"), dict) else {}
    return {
        "dentistry": sum(_num(value) for value in dentists.values()),
        "structure": sum(_num(value) for value in clinic_docs.values()),
        "lab": _num(record.get("labRevenue") or record.get("factLab")),
    }


def _comparison_average(conn, today, snapshot_date):
    values = {"dentistry": [], "structure": [], "lab": []}
    if not snapshot_date:
        return {key: None for key in values}

    try:
        target_day = int(str(snapshot_date)[8:10])
    except (TypeError, ValueError):
        return {key: None for key in values}

    for month in range(1, today.month):
        dates = _report_dates(conn, today.year, month)
        comparable_dates = [
            value for value in dates
            if int(str(value)[8:10]) <= target_day
        ]
        if not comparable_dates:
            continue
        record = _report_for_date(conn, comparable_dates[-1])
        if not record:
            continue
        directions = _direction_values(record)
        for key in values:
            values[key].append(directions[key])

    return {
        key: (sum(items) / len(items) if len(items) >= 2 else None)
        for key, items in values.items()
    }


def _deviation(current, average):
    if average is None or average <= 0:
        return None
    return (current - average) / average * 100.0


def _deviation_text(value):
    if value is None:
        return "Недостаточно данных для сравнения"
    rounded = round(abs(value))
    if value > 5:
        return f"На {rounded}% выше среднего темпа текущего года"
    if value < -5:
        return f"На {rounded}% ниже среднего темпа текущего года"
    return "На уровне среднего темпа текущего года"


def _primary_values(record):
    return {
        "total": _num(record.get("primary")),
        "dentistry": _num(record.get("dentPrimary")),
        "structure": _num(record.get("clinicPrimary")),
        "lab_orders": _num(record.get("labOrders")),
    }


def _primary_forecast_multipliers(conn, today, snapshot_date):
    keys = ("total", "dentistry", "structure", "lab_orders")
    factors = {key: [] for key in keys}
    if not snapshot_date:
        return {key: {"multiplier": None, "months": 0} for key in keys}

    try:
        target_day = int(str(snapshot_date)[8:10])
    except (TypeError, ValueError):
        return {key: {"multiplier": None, "months": 0} for key in keys}

    for month in range(1, today.month):
        dates = _report_dates(conn, today.year, month)
        if not dates:
            continue

        comparable_dates = [
            value for value in dates
            if int(str(value)[8:10]) <= target_day
        ]
        if not comparable_dates:
            continue

        comparable_record = _report_for_date(conn, comparable_dates[-1])
        final_record = _report_for_date(conn, dates[-1])
        if not comparable_record or not final_record:
            continue

        comparable_values = _primary_values(comparable_record)
        final_values = _primary_values(final_record)

        for key in keys:
            base_value = comparable_values[key]
            final_value = final_values[key]
            if base_value > 0 and final_value >= base_value:
                factors[key].append(final_value / base_value)

    result = {}
    for key in keys:
        items = factors[key]
        result[key] = {
            "multiplier": (sum(items) / len(items)) if len(items) >= 2 else None,
            "months": len(items),
        }
    return result


def _forecast_from_history(current_value, multiplier):
    if multiplier is None:
        return None
    return round(current_value * multiplier)


def _primary_values(record):
    return {
        "total": _num(record.get("primary")),
        "dentistry": _num(record.get("dentPrimary")),
        "structure": _num(record.get("clinicPrimary")),
        "lab_orders": _num(record.get("labOrders")),
    }


def _primary_comparable_averages(conn, today, snapshot_date):
    keys = ("total", "dentistry", "structure", "lab_orders")
    values = {key: [] for key in keys}
    if not snapshot_date:
        return {key: {"average": None, "months": 0} for key in keys}

    try:
        target_day = int(str(snapshot_date)[8:10])
    except (TypeError, ValueError):
        return {key: {"average": None, "months": 0} for key in keys}

    for month in range(1, today.month):
        dates = _report_dates(conn, today.year, month)
        comparable_dates = [
            value for value in dates
            if int(str(value)[8:10]) <= target_day
        ]
        if not comparable_dates:
            continue
        record = _report_for_date(conn, comparable_dates[-1])
        if not record:
            continue
        month_values = _primary_values(record)
        for key in keys:
            values[key].append(month_values[key])

    result = {}
    for key in keys:
        items = values[key]
        result[key] = {
            "average": (sum(items) / len(items)) if len(items) >= 2 else None,
            "months": len(items),
        }
    return result


def _primary_detail_rows(conn, today, snapshot_date, current_values):
    comparable = _primary_comparable_averages(conn, today, snapshot_date)
    forecasts = _primary_forecast_multipliers(conn, today, snapshot_date)

    rows = []
    for key, label, forecast_label in (
        ("total", "Первичные всего", "первичных"),
        ("dentistry", "Стоматология", "первичных"),
        ("structure", "Отделение структуры", "первичных"),
        ("lab_orders", "Заказы лаборатории", "заказов"),
    ):
        current = current_values[key]
        average = comparable[key]["average"]
        deviation = _deviation(current, average)
        rows.append(
            {
                "key": key,
                "label": label,
                "current": round(current),
                "average": round(average, 1) if average is not None else None,
                "comparison_months": comparable[key]["months"],
                "deviation": round(deviation, 1) if deviation is not None else None,
                "comment": _deviation_text(deviation),
                "forecast": _forecast_from_history(current, forecasts[key]["multiplier"]),
                "forecast_months": forecasts[key]["months"],
                "forecast_label": forecast_label,
            }
        )
    return rows


def build_summary(conn=None):
    conn = conn or db()
    today = _clinic_today(conn)
    yesterday = today - timedelta(days=1)

    latest_clinical = _latest_date(conn, "daily_uploads")
    latest_cash = _latest_date(conn, "cash_receipts_daily")

    current_month = f"{today.year:04d}-{today.month:02d}"
    current_dates = _daily_dates(conn, today.year, today.month)
    current_dates = [value for value in current_dates if value <= today.isoformat()]
    snapshot_date = current_dates[-1] if current_dates else None
    record = _report_for_date(conn, snapshot_date) if snapshot_date else {}

    stored_plan = _management_monthly_plan(conn, current_month)
    plan = stored_plan if stored_plan is not None else _num(record.get("plan"))
    fact = _num(record.get("cashTotal"))
    snapshot_day = int(snapshot_date[-2:]) if snapshot_date and len(snapshot_date) >= 10 else today.day
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    due = plan * snapshot_day / days_in_month if plan else 0
    plan_execution = fact / plan * 100 if plan > 0 else None
    due_delta = (fact - due) / due * 100 if due > 0 else None

    directions = _direction_values(record)
    comparable = _comparison_average(conn, today, snapshot_date)
    revenue_rows = []
    for key, label in (
        ("dentistry", "Стоматология"),
        ("structure", "Отделение структуры"),
        ("lab", "Лаборатория"),
    ):
        deviation = _deviation(directions[key], comparable[key])
        revenue_rows.append(
            {
                "key": key,
                "label": label,
                "amount": round(directions[key]),
                "deviation": round(deviation, 1) if deviation is not None else None,
                "comment": _deviation_text(deviation),
            }
        )

    primary_values = _primary_values(record)
    primary_total = round(primary_values["total"])
    primary = {
        "total": primary_total,
        "dentistry": round(primary_values["dentistry"]),
        "structure": round(primary_values["structure"]),
        "lab_orders": round(primary_values["lab_orders"]),
        "forecast_total": _forecast_from_history(
            primary_total,
            _primary_forecast_multipliers(conn, today, snapshot_date)["total"]["multiplier"],
        ),
    }
    primary_details = _primary_detail_rows(
        conn,
        today,
        snapshot_date,
        primary_values,
    )

    clinical_stale = not latest_clinical or latest_clinical < yesterday.isoformat()
    cash_stale = not latest_cash or latest_cash < yesterday.isoformat()
    pending_providers = upload_reconcile.pending_provider_rows(conn)

    return {
        "today": today.isoformat(),
        "snapshot_date": snapshot_date,
        "snapshot_date_label": _fmt_date(snapshot_date),
        "latest_clinical": latest_clinical,
        "latest_clinical_label": _fmt_date(latest_clinical),
        "latest_cash": latest_cash,
        "latest_cash_label": _fmt_date(latest_cash),
        "clinical_stale": clinical_stale,
        "cash_stale": cash_stale,
        "pending_providers": pending_providers,
        "elapsed_loaded_days": len(current_dates),
        "plan": {
            "fact": round(fact),
            "due": round(due),
            "execution": round(plan_execution, 1) if plan_execution is not None else None,
            "delta": round(due_delta, 1) if due_delta is not None else None,
        },
        "revenue": revenue_rows,
        "primary": primary,
        "primary_details": primary_details,
    }


def _page_context(summary):
    plan = summary["plan"]
    delta = plan["delta"]
    if delta is None:
        plan_comment = "Недостаточно данных для расчёта"
        plan_state = "neutral"
    elif delta < -5:
        plan_comment = f"Отставание от текущего плана на {round(abs(delta))}%"
        plan_state = "negative"
    elif delta > 5:
        plan_comment = f"Опережение текущего плана на {round(delta)}%"
        plan_state = "positive"
    else:
        plan_comment = "В пределах ±5% от текущего плана"
        plan_state = "neutral"
    return {
        "summary": summary,
        "plan_comment": plan_comment,
        "plan_state": plan_state,
    }


def register_attention(app):
    @app.get("/api/reports/attention")
    @permission_required("reports")
    def attention_api():
        return jsonify(build_summary())

    @app.get("/reports/attention/")
    @permission_required("reports")
    def attention_page():
        return render_template("attention.html", **_page_context(build_summary()))


def home_modal_fragment(user_id):
    return f"""
<style id="az-attention-modal-style">
.az-attention-overlay{{position:fixed;inset:0;z-index:20000;display:grid;place-items:center;padding:22px;background:rgba(45,52,47,.28);backdrop-filter:blur(3px)}}
.az-attention-overlay[hidden]{{display:none}}
.az-attention-modal{{width:min(900px,94vw);max-height:88vh;overflow:auto;background:#f7f3ec;border:1px solid rgba(181,150,98,.42);border-radius:18px;box-shadow:0 24px 70px rgba(54,45,31,.20);padding:22px}}
.az-attention-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:14px}}
.az-attention-title{{margin:0;font:600 28px/1.05 Montserrat,Arial,sans-serif;color:#2d342f}}
.az-attention-date{{margin-top:6px;color:#687067;font:500 11px/1.35 Montserrat,Arial,sans-serif}}
.az-attention-close{{border:0;background:transparent;color:#60685f;font-size:26px;line-height:1;cursor:pointer;padding:0 2px}}
.az-attention-warning{{margin:0 0 12px;padding:10px 12px;border-left:4px solid #a14b45;border-radius:8px;background:#f7e8e6;color:#7d3b36;font:600 12px/1.4 Montserrat,Arial,sans-serif}}
.az-attention-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.az-attention-card{{background:#fffdf8;border:1px solid #ded5c8;border-radius:13px;padding:15px}}
.az-attention-card.plan{{grid-column:1/-1}}
.az-attention-kicker{{font:600 11px/1.2 Montserrat,Arial,sans-serif;color:#687067;text-transform:uppercase;letter-spacing:.04em}}
.az-attention-main{{margin-top:7px;font:600 30px/1 Montserrat,Arial,sans-serif;color:#2d342f}}
.az-attention-sub{{margin-top:5px;font:500 12px/1.4 Montserrat,Arial,sans-serif;color:#5f685f}}
.az-attention-value-strong{{font-weight:700;color:#2d342f}}
.az-attention-plan-line{{display:inline}}
.az-attention-plan-line + .az-attention-plan-line::before{{content:" · "}}
.az-attention-row{{display:grid;grid-template-columns:minmax(130px,1fr) auto;gap:10px;padding:9px 0;border-top:1px solid #e7dfd3}}
.az-attention-row:first-of-type{{margin-top:7px}}
.az-attention-row strong{{font:600 14px/1.2 Montserrat,Arial,sans-serif}}
.az-attention-row span{{font:500 11px/1.35 Montserrat,Arial,sans-serif;color:#687067;grid-column:1/-1}}
.az-attention-positive{{color:#315a40!important}} .az-attention-negative{{color:#9a4b48!important}}
@media(max-width:700px){{
  .az-attention-overlay{{padding:8px;align-items:end}}
  .az-attention-modal{{width:100%;max-height:92vh;border-radius:16px 16px 0 0;padding:16px 14px 20px}}
  .az-attention-title{{font-size:22px}}
  .az-attention-grid{{grid-template-columns:1fr}}
  .az-attention-card.plan{{grid-column:auto}}
  .az-attention-main{{font-size:26px}}
  .az-attention-row{{grid-template-columns:1fr}}
  .az-attention-row strong{{font-size:15px}}
  .az-attention-plan-line{{display:block}}
  .az-attention-plan-line + .az-attention-plan-line{{margin-top:4px}}
  .az-attention-plan-line + .az-attention-plan-line::before{{content:""}}
}}
</style>
<div class="az-attention-overlay" id="azAttentionOverlay" hidden>
  <section class="az-attention-modal" role="dialog" aria-modal="true" aria-labelledby="azAttentionTitle">
    <div class="az-attention-head">
      <div><h2 class="az-attention-title" id="azAttentionTitle">Требует внимания</h2><div class="az-attention-date" id="azAttentionDate">Загрузка…</div></div>
      <button class="az-attention-close" type="button" id="azAttentionClose" aria-label="Закрыть">×</button>
    </div>
    <div id="azAttentionWarning"></div>
    <div class="az-attention-grid" id="azAttentionBody"></div>
  </section>
</div>
<script id="az-attention-modal-script">
(()=>{{
  const overlay=document.getElementById('azAttentionOverlay');
  const close=document.getElementById('azAttentionClose');
  const dateEl=document.getElementById('azAttentionDate');
  const warn=document.getElementById('azAttentionWarning');
  const body=document.getElementById('azAttentionBody');
  const money=v=>new Intl.NumberFormat('ru-RU',{{maximumFractionDigits:0}}).format(Number(v||0))+' ₽';
  const n=v=>new Intl.NumberFormat('ru-RU',{{maximumFractionDigits:0}}).format(Number(v||0));
  const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]));
  const state=v=>v>5?'az-attention-positive':v<-5?'az-attention-negative':'';
  function closeModal(){{overlay.hidden=true}}
  close.addEventListener('click',closeModal);
  overlay.addEventListener('click',e=>{{if(e.target===overlay)closeModal()}});
  fetch('/api/reports/attention',{{credentials:'same-origin',cache:'no-store'}}).then(r=>{{if(!r.ok)throw Error('attention');return r.json()}}).then(d=>{{
    const seenKey='az-attention-seen:{int(user_id)}:'+d.today;
    if(localStorage.getItem(seenKey)==='1')return;
    localStorage.setItem(seenKey,'1');
    dateEl.textContent=d.snapshot_date_label?'Данные по состоянию на '+d.snapshot_date_label:'Актуальные данные ещё не загружены';
    const warnings=[];
    if(d.clinical_stale)warnings.push('Нет актуальных клинических данных. Последние данные — '+(d.latest_clinical_label||'не определены')+'.');
    if(d.cash_stale)warnings.push('Денежные данные неактуальны. Последние «Счета и оплаты» — '+(d.latest_cash_label||'не определены')+'.');
    if((d.pending_providers||[]).length)warnings.push('Новые врачи требуют классификации: '+d.pending_providers.map(x=>x.source_name).join(', ')+'. Откройте «Загрузка данных».');
    warn.innerHTML=warnings.map(x=>'<div class="az-attention-warning">'+esc(x)+'</div>').join('');
    const p=d.plan||{{}}, pr=d.primary||{{}};
    let planComment='Недостаточно данных для расчёта';
    if(p.delta!=null) planComment=p.delta<-5?'Отставание от текущего плана на '+Math.round(Math.abs(p.delta))+'%':p.delta>5?'Опережение текущего плана на '+Math.round(p.delta)+'%':'В пределах ±5% от текущего плана';
    const rev=(d.revenue||[]).map(x=>'<div class="az-attention-row"><strong>'+esc(x.label)+' — '+(x.amount==null?'Нет данных':money(x.amount))+'</strong><span class="'+state(x.deviation)+'">'+esc(x.comment)+'</span></div>').join('');
    body.innerHTML=
      '<div class="az-attention-card plan"><div class="az-attention-kicker">Текущее выполнение плана</div><div class="az-attention-main">'+(p.execution==null?'—':esc(p.execution)+'%')+'</div><div class="az-attention-sub"><span class="az-attention-plan-line">Факт месяца — <span class="az-attention-value-strong">'+money(p.fact)+'</span></span><span class="az-attention-plan-line">Должно быть — <span class="az-attention-value-strong">'+money(p.due)+'</span></span></div><div class="az-attention-sub '+state(p.delta)+'">'+esc(planComment)+'</div></div>'+
      '<div class="az-attention-card"><div class="az-attention-kicker">Выручка по направлениям</div>'+rev+'</div>'+
      '<div class="az-attention-card"><div class="az-attention-kicker">Первичные пациенты — с начала месяца</div><div class="az-attention-main">'+n(pr.total)+'</div><div class="az-attention-sub">Стоматология — '+n(pr.dentistry)+'<br>Отделение структуры — '+n(pr.structure)+'<br>Заказы лаборатории — '+n(pr.lab_orders)+(pr.forecast_total==null?'':'<br><strong>Прогноз на конец месяца — '+n(pr.forecast_total)+' первичных</strong>')+'</div></div>';
    overlay.hidden=false;
  }}).catch(err=>console.error(err));
}})();
</script>
"""


def inject_home_modal(html, user):
    if not user or "reports" not in user_permissions(user):
        return html
    if "az-attention-modal-style" in html:
        return html
    return html.replace("</body>", home_modal_fragment(user["id"]) + "\n</body>", 1)
