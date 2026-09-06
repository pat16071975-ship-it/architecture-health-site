import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AZBAZE_DB", "/var/lib/az-baze/auth.db"))
SITE_ROOT = Path(os.environ.get("AZBAZE_SITE_ROOT", "/var/www/az-baze.ru"))
SECTIONS = [
    ("reports", "Отчёты"),
    ("knowledge", "База знаний"),
    ("section3", "Раздел 3"),
    ("section4", "Раздел 4"),
    ("section5", "Раздел 5"),
]
SECTION_KEYS = {key for key, _ in SECTIONS}

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS permissions (
    user_id INTEGER NOT NULL,
    section TEXT NOT NULL,
    PRIMARY KEY (user_id, section),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER,
    action TEXT NOT NULL,
    target_user_id INTEGER,
    details TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE SET NULL
);
"""


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def create_app():
    app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
    secret = os.environ.get("AZBAZE_SECRET_KEY")
    if not secret:
        raise RuntimeError("AZBAZE_SECRET_KEY is not set")

    app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )

    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("user_id")
        if uid:
            row = db().execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
            if row:
                g.user = row
            else:
                session.clear()

        if g.user and g.user["must_change_password"]:
            allowed = {"change_password", "logout", "static", "health"}
            if request.endpoint not in allowed:
                return redirect(url_for("change_password"))

    @app.after_request
    def secure_headers(response):
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.get("/health")
    def health():
        return "ok", 200

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("home"))

        error = None
        if request.method == "POST":
            require_csrf()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = db().execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone()

            if user and user["active"]:
                locked_until = parse_dt(user["locked_until"])
                if locked_until and locked_until > utc_now():
                    error = "Слишком много неудачных попыток. Попробуйте позже."
                elif check_password_hash(user["password_hash"], password):
                    db().execute(
                        "UPDATE users SET failed_attempts=0, locked_until=NULL, updated_at=? WHERE id=?",
                        (iso_now(), user["id"]),
                    )
                    db().commit()
                    session.clear()
                    session.permanent = True
                    session["user_id"] = user["id"]
                    session["csrf"] = secrets.token_urlsafe(24)
                    audit("login", target_user_id=user["id"])
                    if user["must_change_password"]:
                        return redirect(url_for("change_password"))
                    next_url = safe_next(request.args.get("next"))
                    return redirect(next_url or url_for("home"))
                else:
                    attempts = int(user["failed_attempts"] or 0) + 1
                    locked = None
                    if attempts >= 5:
                        locked = (utc_now() + timedelta(minutes=15)).isoformat()
                        attempts = 0
                    db().execute(
                        "UPDATE users SET failed_attempts=?, locked_until=?, updated_at=? WHERE id=?",
                        (attempts, locked, iso_now(), user["id"]),
                    )
                    db().commit()
                    error = "Неверный e-mail или пароль."
            else:
                error = "Неверный e-mail или пароль."

        return render_template("login.html", error=error, csrf=csrf_token())

    @app.post("/logout")
    @login_required
    def logout():
        require_csrf()
        audit("logout", target_user_id=g.user["id"])
        session.clear()
        return redirect(url_for("login"))

    @app.route("/change-password", methods=["GET", "POST"])
    @login_required
    def change_password():
        error = None
        if request.method == "POST":
            require_csrf()
            password = request.form.get("password", "")
            password2 = request.form.get("password2", "")
            if len(password) < 10:
                error = "Пароль должен содержать не менее 10 символов."
            elif password != password2:
                error = "Пароли не совпадают."
            else:
                db().execute(
                    "UPDATE users SET password_hash=?, must_change_password=0, updated_at=? WHERE id=?",
                    (generate_password_hash(password), iso_now(), g.user["id"]),
                )
                db().commit()
                audit("password_changed", target_user_id=g.user["id"])
                return redirect(url_for("home"))
        return render_template("change_password.html", error=error, csrf=csrf_token())

    @app.get("/")
    @login_required
    def home():
        return render_home_for_user()

    @app.get("/api/me")
    @login_required
    def api_me():
        return jsonify(
            email=g.user["email"],
            full_name=g.user["full_name"],
            is_admin=bool(g.user["is_admin"]),
            permissions=sorted(user_permissions(g.user)),
        )

    @app.get("/assets/<path:filename>")
    @login_required
    def assets(filename):
        return send_from_directory(SITE_ROOT / "assets", filename)

    @app.get("/reports/")
    @permission_required("reports")
    def reports_index():
        return send_from_directory(SITE_ROOT / "reports", "index.html")

    @app.get("/reports/<path:filename>")
    @permission_required("reports")
    def reports_file(filename):
        return send_from_directory(SITE_ROOT / "reports", filename)

    @app.get("/knowledge/")
    @permission_required("knowledge")
    def knowledge_index():
        root = SITE_ROOT / "knowledge"
        if not (root / "index.html").exists():
            return "Раздел «База знаний» пока готовится.", 200
        return send_from_directory(root, "index.html")

    @app.get("/knowledge/<path:filename>")
    @permission_required("knowledge")
    def knowledge_file(filename):
        return send_from_directory(SITE_ROOT / "knowledge", filename)

    @app.get("/section3/")
    @permission_required("section3")
    def section3():
        return "Раздел пока не создан.", 200

    @app.get("/section4/")
    @permission_required("section4")
    def section4():
        return "Раздел пока не создан.", 200

    @app.get("/section5/")
    @permission_required("section5")
    def section5():
        return "Раздел пока не создан.", 200

    @app.get("/admin")
    @admin_required
    def admin():
        users = db().execute("SELECT * FROM users ORDER BY full_name COLLATE NOCASE").fetchall()
        perms = {
            row["id"]: sorted(user_permissions(row))
            for row in users
        }
        logs = db().execute(
            """
            SELECT a.*, au.full_name AS actor_name, tu.full_name AS target_name
            FROM audit_log a
            LEFT JOIN users au ON au.id=a.actor_user_id
            LEFT JOIN users tu ON tu.id=a.target_user_id
            ORDER BY a.id DESC LIMIT 30
            """
        ).fetchall()
        return render_template(
            "admin.html",
            users=users,
            perms=perms,
            sections=SECTIONS,
            logs=logs,
            csrf=csrf_token(),
        )

    @app.route("/admin/users/new", methods=["GET", "POST"])
    @admin_required
    def admin_new_user():
        error = None
        if request.method == "POST":
            require_csrf()
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            is_admin = 1 if request.form.get("is_admin") == "1" else 0
            permissions = selected_permissions()
            if not full_name or not valid_email(email):
                error = "Укажите ФИО и корректный e-mail."
            elif db().execute("SELECT 1 FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone():
                error = "Пользователь с таким e-mail уже существует."
            else:
                temp_password = secrets.token_urlsafe(10)
                now = iso_now()
                cur = db().execute(
                    """
                    INSERT INTO users(email, full_name, password_hash, is_admin, active, must_change_password, created_at, updated_at)
                    VALUES(?,?,?,?,1,1,?,?)
                    """,
                    (email, full_name, generate_password_hash(temp_password), is_admin, now, now),
                )
                user_id = cur.lastrowid
                set_permissions(user_id, permissions)
                db().commit()
                audit("user_created", target_user_id=user_id, details=f"admin={is_admin}; permissions={','.join(sorted(permissions))}")
                return render_template(
                    "temp_password.html",
                    full_name=full_name,
                    email=email,
                    temp_password=temp_password,
                    login_url=request.url_root.rstrip("/") + url_for("login"),
                )
        return render_template(
            "user_form.html",
            title="Новый пользователь",
            user=None,
            current_permissions=set(),
            sections=SECTIONS,
            error=error,
            csrf=csrf_token(),
        )

    @app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_edit_user(user_id):
        user = db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            abort(404)
        error = None
        if request.method == "POST":
            require_csrf()
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            is_admin = 1 if request.form.get("is_admin") == "1" else 0
            active = 1 if request.form.get("active") == "1" else 0
            permissions = selected_permissions()

            if user_id == g.user["id"] and (not is_admin or not active):
                error = "Нельзя снять права администратора или отключить собственную учётную запись."
            elif not full_name or not valid_email(email):
                error = "Укажите ФИО и корректный e-mail."
            elif db().execute(
                "SELECT 1 FROM users WHERE email=? COLLATE NOCASE AND id<>?",
                (email, user_id),
            ).fetchone():
                error = "Пользователь с таким e-mail уже существует."
            else:
                db().execute(
                    "UPDATE users SET email=?, full_name=?, is_admin=?, active=?, updated_at=? WHERE id=?",
                    (email, full_name, is_admin, active, iso_now(), user_id),
                )
                set_permissions(user_id, permissions)
                db().commit()
                audit("user_updated", target_user_id=user_id, details=f"admin={is_admin}; active={active}; permissions={','.join(sorted(permissions))}")
                return redirect(url_for("admin"))

        return render_template(
            "user_form.html",
            title="Права пользователя",
            user=user,
            current_permissions=user_permissions(user),
            sections=SECTIONS,
            error=error,
            csrf=csrf_token(),
        )

    @app.post("/admin/users/<int:user_id>/reset-password")
    @admin_required
    def admin_reset_password(user_id):
        require_csrf()
        user = db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            abort(404)
        temp_password = secrets.token_urlsafe(10)
        db().execute(
            "UPDATE users SET password_hash=?, must_change_password=1, failed_attempts=0, locked_until=NULL, updated_at=? WHERE id=?",
            (generate_password_hash(temp_password), iso_now(), user_id),
        )
        db().commit()
        audit("password_reset", target_user_id=user_id)
        return render_template(
            "temp_password.html",
            full_name=user["full_name"],
            email=user["email"],
            temp_password=temp_password,
            login_url=request.url_root.rstrip("/") + url_for("login"),
        )

    return app


def db():
    if "db" not in g:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        g.db = conn
    return g.db


def init_db_file(path=None):
    target = Path(path or DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def csrf_token():
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        session["csrf"] = token
    return token


def require_csrf():
    sent = request.form.get("csrf", "")
    expected = session.get("csrf", "")
    if not expected or not secrets.compare_digest(sent, expected):
        abort(400)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        if not g.user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def permission_required(section):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.user:
                return redirect(url_for("login", next=request.path))
            if section not in user_permissions(g.user):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def user_permissions(user):
    if bool(user["is_admin"]):
        return set(SECTION_KEYS)
    rows = db().execute("SELECT section FROM permissions WHERE user_id=?", (user["id"],)).fetchall()
    return {row["section"] for row in rows if row["section"] in SECTION_KEYS}


def set_permissions(user_id, permissions):
    clean = set(permissions) & SECTION_KEYS
    db().execute("DELETE FROM permissions WHERE user_id=?", (user_id,))
    db().executemany(
        "INSERT INTO permissions(user_id, section) VALUES(?,?)",
        [(user_id, section) for section in sorted(clean)],
    )


def selected_permissions():
    return {key for key in SECTION_KEYS if request.form.get(f"perm_{key}") == "1"}


def audit(action, target_user_id=None, details=None):
    actor = g.user["id"] if getattr(g, "user", None) else None
    db().execute(
        "INSERT INTO audit_log(actor_user_id, action, target_user_id, details, created_at) VALUES(?,?,?,?,?)",
        (actor, action, target_user_id, details, iso_now()),
    )
    db().commit()


def valid_email(email):
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email or ""))


def safe_next(value):
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return None


def render_home_for_user():
    index_path = SITE_ROOT / "index.html"
    if not index_path.exists():
        abort(404)
    html = index_path.read_text(encoding="utf-8")
    perms = user_permissions(g.user)

    mapping = {
        "reports": r'<a class="menu-btn active" href="/reports/">Отчёты</a>',
        "knowledge": r'<a class="menu-btn active" href="/knowledge/" id="knowledge">База знаний</a>',
        "section3": r'<div class="menu-btn placeholder" aria-disabled="true">Раздел в разработке</div>',
        "section4": r'<div class="menu-btn placeholder" aria-disabled="true">Раздел в разработке</div>',
        "section5": r'<div class="menu-btn placeholder" aria-disabled="true">Раздел в разработке</div>',
    }

    for key, fragment in mapping.items():
        if key not in perms:
            html = html.replace(fragment, "")

    admin_link = ""
    if g.user["is_admin"]:
        admin_link = '<a href="/admin" style="position:fixed;right:18px;bottom:18px;z-index:50;padding:9px 13px;border:1px solid rgba(181,150,98,.42);border-radius:9px;background:#faf7f1;color:#354039;text-decoration:none;font:600 12px Montserrat,Arial,sans-serif">Управление доступом</a>'
    logout_form = f'<form method="post" action="/logout" style="position:fixed;left:18px;bottom:18px;z-index:50"><input type="hidden" name="csrf" value="{csrf_token()}"><button type="submit" style="padding:9px 13px;border:1px solid rgba(181,150,98,.42);border-radius:9px;background:#faf7f1;color:#354039;font:600 12px Montserrat,Arial,sans-serif;cursor:pointer">Выйти</button></form>'
    html = html.replace("</body>", admin_link + logout_form + "</body>")
    html = html.replace('href="../reports/"', 'href="/reports/"')
    html = html.replace('href="#knowledge"', 'href="/knowledge/"')
    html = html.replace('src="../assets/', 'src="/assets/')
    return html


app = create_app()
