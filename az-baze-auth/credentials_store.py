import os
import re
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from flask import abort, g, jsonify, redirect, render_template, request, url_for

import app as app_core


PERMISSION_OPTIONS = [
    ("credentials_view", "Просматривать список доступов"),
    ("credentials_reveal", "Показывать и копировать пароли"),
    ("credentials_edit", "Добавлять и изменять записи"),
    ("credentials_delete", "Удалять записи"),
]

KEY_PATH = Path(
    os.environ.get("AZBAZE_CREDENTIALS_KEY", "/var/lib/az-baze/credentials.key")
)

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name TEXT NOT NULL,
    site_url TEXT NOT NULL DEFAULT '',
    login TEXT NOT NULL DEFAULT '',
    secret_token TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    updated_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_credentials_service_name
    ON credentials(service_name COLLATE NOCASE);
"""


def _init_schema():
    app_core.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(app_core.DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _fernet():
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        fd = os.open(
            KEY_PATH,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as handle:
            handle.write(Fernet.generate_key() + b"\n")

    try:
        mode = KEY_PATH.stat().st_mode & 0o777
        if mode & 0o077:
            raise RuntimeError(
                f"AZ-BAZE credentials key has unsafe permissions: {oct(mode)}"
            )
        key = KEY_PATH.read_bytes().strip()
        return Fernet(key)
    except (OSError, ValueError) as exc:
        raise RuntimeError("AZ-BAZE credentials key is unavailable or invalid") from exc


def _encrypt_secret(secret):
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def _decrypt_secret(token):
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("Credential secret cannot be decrypted") from exc


def _normalize_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    if not re.match(r"^https?://", value, re.IGNORECASE):
        value = "https://" + value
    if not re.match(r"^https?://[^\s]+$", value, re.IGNORECASE):
        raise ValueError("Укажите корректную ссылку сайта.")
    return value


def _form_values():
    service_name = request.form.get("service_name", "").strip()
    site_url = request.form.get("site_url", "").strip()
    login = request.form.get("login", "").strip()
    password = request.form.get("password", "")
    note = request.form.get("note", "").strip()

    if not service_name:
        raise ValueError("Укажите название сервиса.")
    if len(service_name) > 160:
        raise ValueError("Название сервиса слишком длинное.")
    if len(site_url) > 500:
        raise ValueError("Ссылка слишком длинная.")
    if len(login) > 320:
        raise ValueError("Логин слишком длинный.")
    if len(password) > 1024:
        raise ValueError("Пароль слишком длинный.")
    if len(note) > 2000:
        raise ValueError("Комментарий слишком длинный.")

    site_url = _normalize_url(site_url)
    return service_name, site_url, login, password, note


def _get_credential(credential_id):
    row = app_core.db().execute(
        "SELECT * FROM credentials WHERE id=?",
        (credential_id,),
    ).fetchone()
    if not row:
        abort(404)
    return row


def _require_csrf():
    app_core.require_csrf()


def _permissions():
    return app_core.user_permissions(g.user)


def register_credentials(app):
    app_core.SECTION_KEYS.update(key for key, _label in PERMISSION_OPTIONS)
    _init_schema()
    _fernet()

    @app.context_processor
    def credentials_permissions_context():
        return {"credential_permission_options": PERMISSION_OPTIONS}

    @app.get("/credentials/")
    @app_core.permission_required("credentials_view")
    def credentials_index():
        rows = app_core.db().execute(
            """
            SELECT id, service_name, site_url, login, note, created_at, updated_at
            FROM credentials
            ORDER BY service_name COLLATE NOCASE, id
            """
        ).fetchall()
        perms = _permissions()
        return render_template(
            "credentials.html",
            rows=rows,
            can_reveal="credentials_reveal" in perms,
            can_edit="credentials_edit" in perms,
            can_delete="credentials_delete" in perms,
            csrf=app_core.csrf_token(),
        )

    @app.route("/credentials/new", methods=["GET", "POST"])
    @app_core.permission_required("credentials_view")
    @app_core.permission_required("credentials_edit")
    def credentials_new():
        error = None
        values = {
            "service_name": "",
            "site_url": "",
            "login": "",
            "note": "",
        }
        if request.method == "POST":
            _require_csrf()
            try:
                service_name, site_url, login, password, note = _form_values()
                values = {
                    "service_name": service_name,
                    "site_url": site_url,
                    "login": login,
                    "note": note,
                }
                if not password:
                    raise ValueError("Укажите пароль.")
                now = app_core.iso_now()
                cur = app_core.db().execute(
                    """
                    INSERT INTO credentials(
                        service_name, site_url, login, secret_token, note,
                        created_by, updated_by, created_at, updated_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        service_name,
                        site_url,
                        login,
                        _encrypt_secret(password),
                        note,
                        g.user["id"],
                        g.user["id"],
                        now,
                        now,
                    ),
                )
                app_core.db().commit()
                app_core.audit(
                    "credential_created",
                    details=f"credential_id={cur.lastrowid}",
                )
                return redirect(url_for("credentials_index"))
            except ValueError as exc:
                error = str(exc)

        return render_template(
            "credential_form.html",
            title="Новый доступ",
            values=values,
            password_required=True,
            error=error,
            csrf=app_core.csrf_token(),
        )

    @app.route("/credentials/<int:credential_id>/edit", methods=["GET", "POST"])
    @app_core.permission_required("credentials_view")
    @app_core.permission_required("credentials_edit")
    def credentials_edit(credential_id):
        row = _get_credential(credential_id)
        error = None
        values = {
            "service_name": row["service_name"],
            "site_url": row["site_url"],
            "login": row["login"],
            "note": row["note"],
        }

        if request.method == "POST":
            _require_csrf()
            try:
                service_name, site_url, login, password, note = _form_values()
                values = {
                    "service_name": service_name,
                    "site_url": site_url,
                    "login": login,
                    "note": note,
                }
                now = app_core.iso_now()
                if password:
                    app_core.db().execute(
                        """
                        UPDATE credentials
                        SET service_name=?, site_url=?, login=?, secret_token=?,
                            note=?, updated_by=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            service_name,
                            site_url,
                            login,
                            _encrypt_secret(password),
                            note,
                            g.user["id"],
                            now,
                            credential_id,
                        ),
                    )
                else:
                    app_core.db().execute(
                        """
                        UPDATE credentials
                        SET service_name=?, site_url=?, login=?, note=?,
                            updated_by=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            service_name,
                            site_url,
                            login,
                            note,
                            g.user["id"],
                            now,
                            credential_id,
                        ),
                    )
                app_core.db().commit()
                app_core.audit(
                    "credential_updated",
                    details=f"credential_id={credential_id}; password_changed={bool(password)}",
                )
                return redirect(url_for("credentials_index"))
            except ValueError as exc:
                error = str(exc)

        return render_template(
            "credential_form.html",
            title="Изменить доступ",
            values=values,
            password_required=False,
            error=error,
            csrf=app_core.csrf_token(),
        )

    @app.post("/credentials/<int:credential_id>/delete")
    @app_core.permission_required("credentials_view")
    @app_core.permission_required("credentials_delete")
    def credentials_delete(credential_id):
        _require_csrf()
        _get_credential(credential_id)
        app_core.db().execute(
            "DELETE FROM credentials WHERE id=?",
            (credential_id,),
        )
        app_core.db().commit()
        app_core.audit(
            "credential_deleted",
            details=f"credential_id={credential_id}",
        )
        return redirect(url_for("credentials_index"))

    @app.post("/credentials/<int:credential_id>/secret")
    @app_core.permission_required("credentials_view")
    @app_core.permission_required("credentials_reveal")
    def credentials_secret(credential_id):
        _require_csrf()
        row = _get_credential(credential_id)
        action = request.form.get("action", "show").strip()
        if action not in {"show", "copy_password", "copy_all"}:
            abort(400)

        try:
            secret = _decrypt_secret(row["secret_token"])
        except RuntimeError:
            app.logger.exception(
                "Cannot decrypt credential id=%s",
                credential_id,
            )
            abort(500)

        app_core.audit(
            "credential_secret_access",
            details=f"credential_id={credential_id}; action={action}",
        )
        return jsonify(
            password=secret,
            service_name=row["service_name"],
            site_url=row["site_url"],
            login=row["login"],
            note=row["note"],
        )
