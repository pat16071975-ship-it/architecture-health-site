"""Isolated AZ-BAZE administrators timesheet and payroll (no financial integrations)."""
import calendar
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from flask import abort, g, redirect, render_template, request, session

import app as core

PERMISSIONS = (
    ("admins_view", "Администраторы — просмотр"),
    ("admins_hours", "Администраторы — изменение табеля"),
    ("admins_payroll", "Администраторы — изменение премий"),
    ("admins_settings", "Администраторы — настройка сотрудников и ставок"),
    ("admins_close", "Администраторы — закрыть месяц"),
    ("admins_reopen", "Администраторы — повторно открыть месяц"),
)
KEYS = {key for key, _ in PERMISSIONS}
TABLES = ("az_admin_staff", "az_admin_rates", "az_admin_days",
          "az_admin_bonuses", "az_admin_months", "az_admin_events")
BONUS_FIELDS = ("standards", "conversion", "category", "plan", "extras")
HOLIDAYS = {
    (1, 1): "Новогодние каникулы", (1, 2): "Новогодние каникулы",
    (1, 3): "Новогодние каникулы", (1, 4): "Новогодние каникулы",
    (1, 5): "Новогодние каникулы", (1, 6): "Новогодние каникулы",
    (1, 7): "Рождество", (1, 8): "Новогодние каникулы",
    (2, 23): "День защитника Отечества",
    (3, 8): "Международный женский день",
    (5, 1): "Праздник Весны и Труда", (5, 9): "День Победы",
    (6, 12): "День России", (11, 4): "День народного единства",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS az_admin_staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL CHECK(length(trim(full_name)) > 0),
    created_month TEXT NOT NULL,
    inactive_month TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS az_admin_rates (
    staff_id INTEGER NOT NULL REFERENCES az_admin_staff(id) ON DELETE RESTRICT,
    valid_from TEXT NOT NULL,
    kopecks INTEGER NOT NULL CHECK(kopecks >= 0),
    PRIMARY KEY(staff_id, valid_from)
);
CREATE TABLE IF NOT EXISTS az_admin_days (
    staff_id INTEGER NOT NULL REFERENCES az_admin_staff(id) ON DELETE RESTRICT,
    work_date TEXT NOT NULL,
    minutes INTEGER NOT NULL CHECK(minutes > 0 AND minutes <= 1440),
    PRIMARY KEY(staff_id, work_date)
);
CREATE TABLE IF NOT EXISTS az_admin_bonuses (
    staff_id INTEGER NOT NULL REFERENCES az_admin_staff(id) ON DELETE RESTRICT,
    month TEXT NOT NULL,
    standards INTEGER NOT NULL DEFAULT 0 CHECK(standards >= 0),
    conversion INTEGER NOT NULL DEFAULT 0 CHECK(conversion >= 0),
    category INTEGER NOT NULL DEFAULT 0 CHECK(category >= 0),
    plan INTEGER NOT NULL DEFAULT 0 CHECK(plan >= 0),
    extras INTEGER NOT NULL DEFAULT 0 CHECK(extras >= 0),
    PRIMARY KEY(staff_id, month)
);
CREATE TABLE IF NOT EXISTS az_admin_months (
    month TEXT PRIMARY KEY,
    closed INTEGER NOT NULL DEFAULT 0 CHECK(closed IN (0,1)),
    version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
    snapshot TEXT,
    closed_by INTEGER,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS az_admin_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER NOT NULL,
    month TEXT NOT NULL,
    staff_id INTEGER,
    action TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_az_admin_days_date ON az_admin_days(work_date);
CREATE INDEX IF NOT EXISTS ix_az_admin_events_month ON az_admin_events(month,id);
"""


def init_schema(conn):
    """Separate, explicit migration. Never called by GET or module registration."""
    conn.executescript(SCHEMA)


def ready(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name LIKE 'az_admin_%'").fetchall()
    return {row[0] for row in rows} >= set(TABLES)


def now():
    return datetime.now(ZoneInfo("Asia/Novosibirsk")).isoformat()


def current_month():
    return now()[:7]


def month_value(value=None):
    value = value or current_month()
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", value):
        abort(400, description="Укажите корректный месяц.")
    return value


def month_shift(value, offset):
    year, month = map(int, value.split("-"))
    month += offset
    if month < 1:
        year -= 1
        month = 12
    if month > 12:
        year += 1
        month = 1
    return f"{year:04d}-{month:02d}"


def can(user, key):
    return bool(user and (core.is_owner(user) or key in core.user_permissions(user)))


def can_view(user):
    return bool(user and (core.is_owner(user) or bool(core.user_permissions(user) & KEYS)))


def require(key):
    if not can(g.user, key):
        abort(403)


def require_ready(conn):
    if not ready(conn):
        abort(409, description="Раздел ещё не подготовлен к работе.")


def form_version():
    raw = request.form.get("version")
    if not raw or not re.fullmatch(r"(0|[1-9][0-9]*)", raw) or len(raw) > 18:
        abort(400, description="Не указана корректная версия месяца. Обновите страницу.")
    return int(raw)


def form_view_revision():
    """The immutable form token binds the POST to all values actually displayed."""
    value = request.form.get("view_revision")
    if not value or not re.fullmatch(r"[0-9a-f]{64}", value):
        abort(400, description="Не указана корректная версия данных. Обновите страницу.")
    return value


def require_month_version(conn, month, expected, expected_view_revision, allow_closed=False):
    """Compare both local and inherited state INSIDE BEGIN IMMEDIATE."""
    state = month_state(conn, month)
    if (state["version"] != expected
            or month_view_revision(conn, month) != expected_view_revision):
        abort(409, description="Данные месяца изменились в другом окне. Обновите страницу.")
    if state["closed"] and not allow_closed:
        abort(409, description="Месяц закрыт. Для изменения его нужно повторно открыть.")
    return state


def advance_month_version(conn, month):
    """Bump only after a real edit and in the same transaction as its audit."""
    conn.execute(
        "INSERT INTO az_admin_months(month,version) VALUES(?,1) "
        "ON CONFLICT(month) DO UPDATE SET version=az_admin_months.version+1",
        (month,),
    )


def money_to_kopecks(value):
    try:
        v = Decimal(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        abort(400, description="Неверная сумма.")
    if not v.is_finite() or v < 0 or v > 100000000 or v.as_tuple().exponent < -2:
        abort(400, description="Сумма должна быть неотрицательной, не больше 100 млн ₽ и с двумя знаками после запятой.")
    return int((v * 100).to_integral_exact(rounding=ROUND_HALF_UP))


def money_text(kopecks):
    value = Decimal(int(kopecks)) / 100
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def hours_text(minutes):
    """Lossless, human-editable hours for every integer minute.

    Decimal hours terminate precisely only when minutes is divisible by 3.
    Otherwise use H:MM instead of producing an infinite decimal such as
    0.116666666666... for seven minutes. The display is always accepted
    unchanged by parse_hours_entry(), including at 24 hours.
    """
    minutes = int(minutes)
    if minutes % 3:
        return f"{minutes // 60}:{minutes % 60:02d}"
    amount = Decimal(minutes) / 60
    return f"{amount:f}".rstrip("0").rstrip(".") if minutes % 60 else str(minutes // 60)


def parse_hours(value):
    if value == "off":
        return None
    try:
        amount = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        abort(400, description="Укажите часы или «вых».")
    minutes = amount * 60
    if not amount.is_finite() or amount <= 0 or amount > 24 or minutes != minutes.to_integral_value():
        abort(400, description="Часы должны быть больше нуля и не превышать 24 (шаг — одна минута).")
    return int(minutes)


def parse_hours_entry(value):
    """Only whole hours 0..24. Old fractional DB records are preserved elsewhere.

    No decimal, no H:MM, no rounding and no 'вых'. A blank/zero means
    an empty day for a new or integral-hour cell.
    """
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"[0-9]{1,2}", text):
        if text == "":
            return None
        abort(400, description="Вводите только целые часы от 0 до 24.")
    hours = int(text)
    if hours > 24:
        abort(400, description="Не более 24 целых часов за день.")
    return hours * 60 if hours else None


def short_staff_name(full_name):
    """Presentation only: never alter stored legal names, rates or closed snapshots."""
    parts = str(full_name or "").split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return parts[0] + " " + " ".join(part[0].upper() + "." for part in parts[1:])


MONTH_NAMES = (
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


def month_title(value):
    year, month = map(int, value.split("-"))
    return f"{MONTH_NAMES[month]} {year} года"


def format_event(event):
    """Human-readable audit summary. Original JSON remains immutable in DB."""
    try:
        before = json.loads(event["before_json"] or "{}")
        after = json.loads(event["after_json"] or "{}")
    except (TypeError, ValueError):
        return "Историческое изменение"
    action = event["action"]
    if action == "hours":
        day = str(after.get("date") or before.get("date") or "")[-2:]
        previous = before.get("minutes")
        current = after.get("minutes")
        left = hours_text(previous) if previous is not None else "—"
        right = hours_text(current) if current is not None else "—"
        return f"{day} число: {left} → {right} ч"
    if action == "staff_created":
        return (f"Добавлен администратор: {after.get('name', '')}; "
                f"ставка {money_text(after.get('rate', 0))} ₽/ч")
    if action == "staff_updated":
        parts = []
        if before.get("name") != after.get("name"):
            parts.append(f"ФИО: {before.get('name', '')} → {after.get('name', '')}")
        if before.get("rate") != after.get("rate"):
            parts.append(
                f"Ставка: {money_text(before.get('rate', 0))} → "
                f"{money_text(after.get('rate', 0))} ₽/ч"
            )
        if before.get("active") != after.get("active"):
            parts.append("Статус: " + ("активен" if after.get("active") else "неактивен"))
        if before.get("inactive_month") != after.get("inactive_month"):
            parts.append("Снятие с работы с: " +
                         (after.get("inactive_month") or "не запланировано"))
        return "; ".join(parts) or "Настройки администратора"
    if action == "premiums":
        labels = dict(standards="Стандарты", conversion="Конверсия",
                      category="Категория", plan="План", extras="Допначисления")
        return "; ".join(
            f"{labels[k]}: {money_text(before.get(k, 0))} → "
            f"{money_text(after.get(k, 0))} ₽"
            for k in BONUS_FIELDS if before.get(k, 0) != after.get(k, 0)
        ) or "Премии"
    if action == "month_closed":
        return "Месяц закрыт, расчёт ФОТ зафиксирован"
    if action == "month_reopened":
        return "Месяц повторно открыт"
    return "Изменение: " + str(action)


def history_page(conn, number, per_page=100):
    """Paginate existing AZ admin events, across months, read-only."""
    count = conn.execute("SELECT COUNT(*) FROM az_admin_events").fetchone()[0]
    rows = conn.execute(
        "SELECT e.id,e.actor_id,e.month,e.staff_id,e.action,e.before_json,"
        "e.after_json,e.created_at,u.full_name AS actor_name,"
        "s.full_name AS staff_name "
        "FROM az_admin_events e LEFT JOIN users u ON u.id=e.actor_id "
        "LEFT JOIN az_admin_staff s ON s.id=e.staff_id "
        "ORDER BY e.id DESC LIMIT ? OFFSET ?",
        (per_page, (number - 1) * per_page),
    ).fetchall()
    result = []
    for entry in rows:
        stamp = entry["created_at"]
        try:
            stamp = datetime.fromisoformat(stamp).strftime("%d.%m.%Y %H:%M")
        except (TypeError, ValueError):
            pass
        result.append(dict(
            id=entry["id"], month=entry["month"],
            actor=entry["actor_name"] or f"Пользователь №{entry['actor_id']}",
            staff=short_staff_name(entry["staff_name"]) if entry["staff_name"]
                  else "Месяц / все сотрудники",
            when=stamp, summary=format_event(entry),
        ))
    return result, count


def proportional(kopecks, minutes, standard_minutes=10800):
    return int((Decimal(kopecks) * Decimal(minutes) /
                Decimal(standard_minutes)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def hourly_pay(kopecks, minutes):
    return int((Decimal(kopecks) * Decimal(minutes) /
                Decimal(60)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def month_state(conn, month):
    row = conn.execute("SELECT * FROM az_admin_months WHERE month=?", (month,)).fetchone()
    return dict(row) if row else dict(month=month, closed=0, version=0, snapshot=None,
                                      closed_by=None, closed_at=None)


def audit(conn, month, action, before, after, staff_id=None):
    conn.execute(
        "INSERT INTO az_admin_events(actor_id,month,staff_id,action,before_json,after_json,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (g.user["id"], month, staff_id, action,
         json.dumps(before, ensure_ascii=False, sort_keys=True),
         json.dumps(after, ensure_ascii=False, sort_keys=True), now()),
    )


def read_rows(conn, month):
    first_day = month + "-01"
    last_day = f"{month}-{calendar.monthrange(*map(int, month.split('-')))[1]:02d}"
    staff_rows = conn.execute(
        "SELECT * FROM az_admin_staff WHERE created_month<=? ORDER BY full_name COLLATE NOCASE,id",
        (month,),
    ).fetchall()
    saved = conn.execute(
        "SELECT staff_id,work_date,minutes FROM az_admin_days WHERE work_date BETWEEN ? AND ?",
        (first_day, last_day),
    ).fetchall()
    days_by_staff = {}
    for saved_day in saved:
        days_by_staff.setdefault(saved_day["staff_id"], {})[int(saved_day["work_date"][-2:])] = saved_day["minutes"]
    bonus_rows = {
        row["staff_id"]: dict(row)
        for row in conn.execute("SELECT * FROM az_admin_bonuses WHERE month=?", (month,))
    }
    rows = []
    for staff in staff_rows:
        sid = staff["id"]
        inactive = staff["inactive_month"]
        daily = days_by_staff.get(sid, {})
        bonus = {field: int(bonus_rows.get(sid, {}).get(field, 0)) for field in BONUS_FIELDS}
        # Keep inactive staff visible for reactivation and historic review.
        rate_row = conn.execute(
            "SELECT kopecks FROM az_admin_rates WHERE staff_id=? AND valid_from<=? "
            "ORDER BY valid_from DESC LIMIT 1", (sid, month),
        ).fetchone()
        rate = int(rate_row[0]) if rate_row else 0
        minutes = sum(daily.values())
        calculated = dict(
            base=hourly_pay(rate, minutes),
            standards=proportional(bonus["standards"], minutes),
            conversion=proportional(bonus["conversion"], minutes),
            category=bonus["category"], plan=bonus["plan"], extras=bonus["extras"],
        )
        total = sum(calculated.values())
        rows.append(dict(id=sid, name=staff["full_name"], active=not bool(inactive and inactive <= month),
                         rate=rate, bonus=bonus, minutes=minutes, hours=hours_text(minutes),
                         days={day: hours_text(amount) for day, amount in daily.items()},
                         legacy_days={day: amount for day, amount in daily.items()
                                      if amount % 60 != 0},
                         calculated=calculated, total=total))
    return rows


def model(conn, month):
    state = month_state(conn, month)
    if state["closed"] and state["snapshot"]:
        rows = json.loads(state["snapshot"])
        for row in rows:
            # JSON object keys are strings, while the table uses numeric dates.
            row["days"] = {int(day): hours for day, hours in row["days"].items()}
        return state, rows
    return state, read_rows(conn, month)


def month_view_revision_from_model(month, state, rows):
    """Bind the form to the EXACT model used to render its visible figures.

    No database reads: the same state/rows objects feed both the HTML and token.
    """
    payload = {
        "month": month,
        "version": state["version"],
        "closed": state["closed"],
        "rows": rows,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def month_view_revision(conn, month):
    """Recompute current token under the caller's write transaction on POST."""
    state, rows = model(conn, month)
    return month_view_revision_from_model(month, state, rows)


def month_page_snapshot(conn, month):
    """Read-only SQLite snapshot: all SELECTs and the form token agree.

    BEGIN establishes a consistent DB snapshot on the first read; subsequent
    SELECTs in model() see that same snapshot even when another request commits
    an inherited rate in WAL mode. The token is computed solely from those
    materialized rows, never by starting a second read after the first one.
    """
    conn.execute("BEGIN")
    try:
        state, rows = model(conn, month)
        view_revision = month_view_revision_from_model(month, state, rows)
        return state, rows, view_revision
    finally:
        # Do not leave an open SQLite read transaction during template rendering;
        # GET must not modify any table.
        conn.rollback()


def checked_staff(conn, staff_id):
    try:
        sid = int(staff_id)
    except (ValueError, TypeError):
        abort(400)
    row = conn.execute("SELECT * FROM az_admin_staff WHERE id=?", (sid,)).fetchone()
    if row is None:
        abort(404)
    return row


def redirect_tab(month, tab):
    return redirect(f"/administrators/?tab={tab}&month={month}", code=303)


def transaction(conn, action):
    """Commit only the scoped administrators data and its audit; rollback on any error."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = action()
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


def register(app):
    core.SECTION_KEYS.update(KEYS)
    core.EXPLICIT_PERMISSION_KEYS.update(KEYS)

    @app.context_processor
    def administrators_permissions():
        return {"administrators_permission_options": tuple(
            (key, label) for key, label in PERMISSIONS
            if key != "admins_reopen" or core.is_owner(getattr(g, "user", None))
        )}

    @app.template_filter("az_admin_money")
    def admin_money(value):
        return money_text(value)

    @app.template_filter("az_admin_short")
    def admin_short(value):
        return short_staff_name(value)

    @app.template_filter("az_admin_month_title")
    def admin_month_title(value):
        return month_title(value)

    @app.get("/administrators/")
    @core.login_required
    def administrators_index():
        if not can_view(g.user):
            abort(403)
        month = month_value(request.args.get("month"))
        tab = request.args.get("tab", "table")
        if tab not in ("table", "payroll", "settings"):
            abort(404)
        conn = core.db()
        available = ready(conn)
        if available:
            state, rows, view_revision = month_page_snapshot(conn, month)
        else:
            state, rows, view_revision = dict(closed=0), [], ""
        days = [
            dict(day=d, weekend=calendar.weekday(*map(int, month.split('-')), d) >= 5,
                 holiday=HOLIDAYS.get((int(month[-2:]), d)))
            for d in range(1, calendar.monthrange(*map(int, month.split('-')))[1] + 1)
        ]
        history = []
        history_count = 0
        page_number = 1
        if available and tab == "settings":
            raw_page = request.args.get("history_page", "1")
            if not re.fullmatch(r"[1-9][0-9]{0,4}", raw_page):
                abort(400, description="Некорректная страница журнала.")
            page_number = int(raw_page)
            history, history_count = history_page(conn, page_number)
        saved_hours_month = session.pop("az_admin_hours_saved_month", None) if tab == "table" else None
        return render_template(
            "administrators.html", title="Администраторы — AZ BAZE", tab=tab,
            month=month, prev_month=month_shift(month, -1), next_month=month_shift(month, 1),
            days=days, rows=rows, state=state, ready=available,
            can_hours=can(g.user, "admins_hours"), can_payroll=can(g.user, "admins_payroll"),
            can_settings=can(g.user, "admins_settings"),
            can_close=can(g.user, "admins_close"), can_reopen=can(g.user, "admins_reopen"),
            history=history, history_count=history_count, history_page=page_number,
            saved_hours_month=month if saved_hours_month == month else None,
            total_fot=sum(row["total"] for row in rows),
            csrf=core.csrf_token(), view_revision=view_revision,
        )

    @app.post("/administrators/hours")
    @core.login_required
    def administrators_hours():
        require("admins_hours")
        core.require_csrf()
        month = month_value(request.form.get("month"))
        version = form_version()
        expected_view_revision = form_view_revision()
        conn = core.db()
        require_ready(conn)
        work_date = str(request.form.get("work_date", ""))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", work_date) or not work_date.startswith(month + "-"):
            abort(400)
        try:
            datetime.strptime(work_date, "%Y-%m-%d")
        except ValueError:
            abort(400)
        staff = checked_staff(conn, request.form.get("staff_id"))
        if staff["created_month"] > month:
            abort(400)
        minutes = parse_hours(request.form.get("value"))
        def save():
            require_month_version(conn, month, version, expected_view_revision)
            before = conn.execute(
                "SELECT minutes FROM az_admin_days WHERE staff_id=? AND work_date=?",
                (staff["id"], work_date),
            ).fetchone()
            previous = before["minutes"] if before else None
            if minutes == previous:
                return
            if minutes is None:
                conn.execute("DELETE FROM az_admin_days WHERE staff_id=? AND work_date=?",
                             (staff["id"], work_date))
            else:
                conn.execute(
                    "INSERT INTO az_admin_days(staff_id,work_date,minutes) VALUES(?,?,?) "
                    "ON CONFLICT(staff_id,work_date) DO UPDATE SET minutes=excluded.minutes",
                    (staff["id"], work_date, minutes),
                )
            audit(conn, month, "hours", {"date": work_date, "minutes": previous},
                  {"date": work_date, "minutes": minutes}, staff["id"])
            advance_month_version(conn, month)
        transaction(conn, save)
        return redirect_tab(month, "table")

    @app.post("/administrators/hours/batch")
    @core.login_required
    def administrators_hours_batch():
        """One explicit save for the entire visible month; all-or-nothing.

        Revalidate the original view fingerprint under BEGIN IMMEDIATE before
        even reading the incoming hours. An incomplete or forged matrix cannot
        silently clear unseen days or post to another employee.
        """
        require("admins_hours")
        core.require_csrf()
        month = month_value(request.form.get("month"))
        version = form_version()
        expected_view_revision = form_view_revision()
        conn = core.db()
        require_ready(conn)
        day_count = calendar.monthrange(*map(int, month.split("-")))[1]

        def save():
            require_month_version(conn, month, version, expected_view_revision)
            rows = read_rows(conn, month)
            old = {
                (entry["staff_id"], int(entry["work_date"][-2:])): entry["minutes"]
                for entry in conn.execute(
                    "SELECT staff_id,work_date,minutes FROM az_admin_days "
                    "WHERE work_date BETWEEN ? AND ?",
                    (month + "-01", f"{month}-{day_count:02d}"),
                )
            }
            expected = {
                f"hours_{row['id']}_{day:02d}": (row["id"], day)
                for row in rows for day in range(1, day_count + 1)
            }
            submitted = {key for key in request.form if key.startswith("hours_")}
            if submitted != set(expected):
                abort(400, description="Неполный или посторонний табель. Обновите страницу.")
            proposed = {}
            for key, identity in expected.items():
                values = request.form.getlist(key)
                if len(values) != 1:
                    abort(400, description="Повторяющиеся значения часов. Обновите страницу.")
                previous = old.get(identity)
                raw = values[0].strip()
                if raw == "" and previous is not None and previous % 60 != 0:
                    # A legacy fractional entry is shown as an empty input
                    # labelled 'Ранее'. An untouched cell means preserve
                    # its EXACT minutes; 0 explicitly removes it and a new
                    # integer replaces it. Never silently round, erase or
                    # rewrite old FOT evidence.
                    proposed[identity] = previous
                else:
                    proposed[identity] = parse_hours_entry(values[0])
            changed = 0
            for (sid, day), minutes in proposed.items():
                previous = old.get((sid, day))
                if minutes == previous:
                    continue
                work_date = f"{month}-{day:02d}"
                if minutes is None:
                    conn.execute(
                        "DELETE FROM az_admin_days WHERE staff_id=? AND work_date=?",
                        (sid, work_date),
                    )
                else:
                    conn.execute(
                        "INSERT INTO az_admin_days(staff_id,work_date,minutes) "
                        "VALUES(?,?,?) ON CONFLICT(staff_id,work_date) "
                        "DO UPDATE SET minutes=excluded.minutes",
                        (sid, work_date, minutes),
                    )
                audit(conn, month, "hours",
                      {"date": work_date, "minutes": previous},
                      {"date": work_date, "minutes": minutes}, sid)
                changed += 1
            if changed:
                advance_month_version(conn, month)
            return changed
        transaction(conn, save)
        # Flash proof is created only after COMMIT, not by a GET query string.
        session["az_admin_hours_saved_month"] = month
        return redirect_tab(month, "table")

    @app.post("/administrators/payroll")
    @core.login_required
    def administrators_payroll():
        require("admins_payroll")
        core.require_csrf()
        month = month_value(request.form.get("month"))
        version = form_version()
        expected_view_revision = form_view_revision()
        conn = core.db()
        require_ready(conn)
        staff = checked_staff(conn, request.form.get("staff_id"))
        if staff["created_month"] > month:
            abort(400)
        amounts = {key: money_to_kopecks(request.form.get(key, "")) for key in BONUS_FIELDS}
        def save():
            require_month_version(conn, month, version, expected_view_revision)
            old = conn.execute("SELECT * FROM az_admin_bonuses WHERE staff_id=? AND month=?",
                               (staff["id"], month)).fetchone()
            previous = {key: old[key] if old else 0 for key in BONUS_FIELDS}
            if previous == amounts:
                return
            conn.execute(
                "INSERT INTO az_admin_bonuses(staff_id,month,standards,conversion,category,plan,extras)"
                " VALUES(?,?,?,?,?,?,?) ON CONFLICT(staff_id,month) DO UPDATE SET "
                "standards=excluded.standards,conversion=excluded.conversion,"
                "category=excluded.category,plan=excluded.plan,extras=excluded.extras",
                (staff["id"], month, *(amounts[k] for k in BONUS_FIELDS)),
            )
            audit(conn, month, "premiums", previous, amounts, staff["id"])
            advance_month_version(conn, month)
        transaction(conn, save)
        return redirect_tab(month, "payroll")

    @app.post("/administrators/settings")
    @core.login_required
    def administrators_settings():
        require("admins_settings")
        core.require_csrf()
        month = month_value(request.form.get("month"))
        version = form_version()
        expected_view_revision = form_view_revision()
        conn = core.db()
        require_ready(conn)
        name = str(request.form.get("full_name", "")).strip()
        if not name or len(name) > 160:
            abort(400, description="Укажите ФИО администратора (до 160 символов).")
        rate = money_to_kopecks(request.form.get("rate", ""))
        active = request.form.get("active") == "1"
        raw_id = request.form.get("staff_id", "")
        def save():
            require_month_version(conn, month, version, expected_view_revision)
            if not raw_id:
                if not active:
                    abort(400)
                created = conn.execute(
                    "INSERT INTO az_admin_staff(full_name,created_month,inactive_month,created_at,updated_at)"
                    " VALUES(?,?,NULL,?,?)",
                    (name, month, now(), now()),
                )
                sid = created.lastrowid
                conn.execute("INSERT INTO az_admin_rates(staff_id,valid_from,kopecks) VALUES(?,?,?)",
                             (sid, month, rate))
                audit(conn, month, "staff_created", {}, {"name": name, "rate": rate}, sid)
            else:
                old = checked_staff(conn, raw_id)
                if old["created_month"] > month:
                    abort(400)
                sid = old["id"]
                previous_rate = conn.execute(
                    "SELECT kopecks FROM az_admin_rates WHERE staff_id=? AND valid_from<=? "
                    "ORDER BY valid_from DESC LIMIT 1", (sid, month),
                ).fetchone()
                old_rate = int(previous_rate[0]) if previous_rate else 0
                was_active = not bool(old["inactive_month"] and old["inactive_month"] <= month)
                if old["full_name"] == name and old_rate == rate and was_active == active:
                    return
                # Editing an earlier month's rate must preserve a scheduled future deactivation.
                inactive_month = (
                    old["inactive_month"]
                    if active and old["inactive_month"] and old["inactive_month"] > month
                    else (None if active else month)
                )
                conn.execute("UPDATE az_admin_staff SET full_name=?,inactive_month=?,updated_at=? "
                             "WHERE id=?", (name, inactive_month, now(), sid))
                if old_rate != rate:
                    conn.execute(
                        "INSERT INTO az_admin_rates(staff_id,valid_from,kopecks) VALUES(?,?,?) "
                        "ON CONFLICT(staff_id,valid_from) DO UPDATE SET kopecks=excluded.kopecks",
                        (sid, month, rate),
                    )
                audit(conn, month, "staff_updated",
                      {"name": old["full_name"], "rate": old_rate,
                       "active": was_active, "inactive_month": old["inactive_month"]},
                      {"name": name, "rate": rate, "active": active,
                       "inactive_month": inactive_month}, sid)
            advance_month_version(conn, month)
        transaction(conn, save)
        return redirect_tab(month, "settings")

    @app.post("/administrators/month")
    @core.login_required
    def administrators_month():
        core.require_csrf()
        month = month_value(request.form.get("month"))
        version = form_version()
        expected_view_revision = form_view_revision()
        action = request.form.get("action")
        if action not in ("close", "reopen"):
            abort(400)
        require("admins_close" if action == "close" else "admins_reopen")
        conn = core.db()
        require_ready(conn)
        def save():
            old = require_month_version(conn, month, version, expected_view_revision,
                                        allow_closed=True)
            if action == "close":
                if old["closed"]:
                    abort(409, description="Месяц уже закрыт.")
                snapshot = json.dumps(read_rows(conn, month), ensure_ascii=False, sort_keys=True)
                conn.execute(
                    "INSERT INTO az_admin_months(month,closed,snapshot,closed_by,closed_at)"
                    " VALUES(?,1,?,?,?) ON CONFLICT(month) DO UPDATE SET "
                    "closed=1,snapshot=excluded.snapshot,closed_by=excluded.closed_by,"
                    "closed_at=excluded.closed_at",
                    (month, snapshot, g.user["id"], now()),
                )
                audit(conn, month, "month_closed", {"closed": False},
                      {"closed": True, "snapshot": snapshot})
                advance_month_version(conn, month)
            else:
                if not old["closed"]:
                    abort(409, description="Месяц уже открыт.")
                conn.execute("UPDATE az_admin_months SET closed=0 WHERE month=?", (month,))
                audit(conn, month, "month_reopened",
                      {"closed": True, "snapshot": old["snapshot"]}, {"closed": False})
                advance_month_version(conn, month)
        transaction(conn, save)
        return redirect_tab(month, "payroll")

    @app.after_request
    def administrators_home_link(response):
        if request.path != "/" or response.mimetype != "text/html" or not can_view(getattr(g, "user", None)):
            return response
        html = response.get_data(as_text=True)
        link = '<a class="menu-btn active" href="/administrators/" id="administrators">Администраторы</a>'
        # Keep the canonical default tile order used by menu_order.CATALOG.
        accounting = '<a class="menu-btn active" href="/accounting/" id="accounting">УЧЕТ</a>'
        anchor = re.search(r'<nav\b[^>]*\bclass="menu-column"[^>]*>', html)
        if link not in html and accounting in html:
            html = html.replace(accounting, accounting + "\n" + link, 1)
            response.set_data(html)
        elif link not in html and anchor:
            # A user with only administrators permissions may have no accounting tile.
            html = html.replace(anchor.group(0), anchor.group(0) + "\n" + link, 1)
            response.set_data(html)
        return response
