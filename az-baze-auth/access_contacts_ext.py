import re
import sqlite3
from flask import abort, g, redirect, render_template, request, url_for

import app as app_core
import credentials_store

PERMISSION_OPTIONS = [
    ("credentials_view", "Просматривать «Доступы и контакты»"),
    ("credentials_reveal", "Показывать и копировать пароли"),
    ("credentials_edit", "Добавлять и изменять записи"),
    ("credentials_delete", "Удалять записи"),
]

CONTACTS_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS work_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization TEXT NOT NULL DEFAULT '',
    organization_role TEXT NOT NULL DEFAULT '',
    full_name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    responsibility TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    updated_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_work_contacts_name ON work_contacts(full_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_work_contacts_org ON work_contacts(organization COLLATE NOCASE);
"""


def _init_contacts_schema():
    con = sqlite3.connect(app_core.DB_PATH)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.executescript(CONTACTS_SCHEMA)
        con.commit()
    finally:
        con.close()


def _credential_values():
    service = request.form.get("service_name", "").strip()
    login = request.form.get("login", "").strip()
    password = request.form.get("password", "")
    note = request.form.get("note", "").strip()
    if len(service) > 160: raise ValueError("Название сервиса слишком длинное.")
    if len(login) > 320: raise ValueError("Логин слишком длинный.")
    if len(password) > 1024: raise ValueError("Пароль слишком длинный.")
    if len(note) > 2000: raise ValueError("Комментарий слишком длинный.")
    return service, login, password, note


def _contact_values():
    values = tuple(request.form.get(k, "").strip() for k in (
        "organization", "organization_role", "full_name", "phone", "responsibility"
    ))
    limits = (240, 240, 240, 120, 2000)
    messages = (
        "Название организации слишком длинное.", "Роль в организации слишком длинная.",
        "ФИО слишком длинное.", "Телефон слишком длинный.",
        "Описание курируемых вопросов слишком длинное.",
    )
    for value, limit, message in zip(values, limits, messages):
        if len(value) > limit: raise ValueError(message)
    return values


def _contact(contact_id):
    row = app_core.db().execute("SELECT * FROM work_contacts WHERE id=?", (contact_id,)).fetchone()
    if not row: abort(404)
    return row


def _credential_label(row):
    return (row["service_name"] or "").strip() or "Доступ без названия"


def register(app):
    _init_contacts_schema()

    @app.context_processor
    def access_contacts_permissions_context():
        return {"credential_permission_options": PERMISSION_OPTIONS}

    @app.after_request
    def access_contacts_home_links(response):
        if request.path != "/" or response.mimetype != "text/html": return response
        html = response.get_data(as_text=True)
        old = '<a class="menu-btn active" href="/credentials/">Доступы и пароли</a>'
        access = '<a class="menu-btn active" href="/access-contacts/">Доступы и контакты</a>'
        admin = '<a class="menu-btn active" href="/admin">Управление доступом</a>'
        knowledge = '<a class="menu-btn active" href="/knowledge/" id="knowledge">База знаний</a>'
        html = html.replace(old, access, 1)
        if g.user and "credentials_view" in app_core.user_permissions(g.user) and access not in html and knowledge in html:
            html = html.replace(knowledge, knowledge + "\n" + access, 1)
        if g.user and g.user["is_admin"] and admin not in html:
            anchor = access if access in html else knowledge
            if anchor in html: html = html.replace(anchor, anchor + "\n" + admin, 1)
        html = re.sub(r'<a href="/admin" style="position:fixed;right:18px;bottom:18px;[^"]*">Управление доступом</a>', "", html, count=1)
        response.set_data(html)
        return response

    @app.get("/access-contacts/")
    @app_core.permission_required("credentials_view")
    def access_contacts_index():
        return render_template("access_contacts.html")

    @app_core.permission_required("credentials_view")
    def credentials_index_override():
        rows = app_core.db().execute("""
            SELECT c.id,c.service_name,c.login,c.secret_token,c.note,c.created_at,c.updated_at
            FROM credentials c LEFT JOIN credential_trash t ON t.credential_id=c.id
            WHERE t.credential_id IS NULL
            ORDER BY CASE WHEN TRIM(c.service_name)='' THEN 1 ELSE 0 END,c.service_name COLLATE NOCASE,c.id
        """).fetchall()
        trash_count = app_core.db().execute("SELECT COUNT(*) AS n FROM credential_trash").fetchone()["n"]
        perms = app_core.user_permissions(g.user)
        return render_template("credentials.html", rows=rows,
            can_reveal="credentials_reveal" in perms, can_edit="credentials_edit" in perms,
            can_delete="credentials_delete" in perms, trash_count=trash_count, csrf=app_core.csrf_token())

    @app_core.permission_required("credentials_view")
    @app_core.permission_required("credentials_edit")
    def credentials_new_override():
        error = None
        values = {"service_name":"", "login":"", "note":""}
        if request.method == "POST":
            app_core.require_csrf()
            try:
                service, login, password, note = _credential_values()
                values = {"service_name":service, "login":login, "note":note}
                now = app_core.iso_now()
                token = credentials_store._encrypt_secret(password) if password else ""
                cur = app_core.db().execute("""
                    INSERT INTO credentials(service_name,site_url,login,secret_token,note,created_by,updated_by,created_at,updated_at)
                    VALUES(?,'',?,?,?,?,?,?,?)
                """, (service,login,token,note,g.user["id"],g.user["id"],now,now))
                app_core.db().commit(); app_core.audit("credential_created", details=f"credential_id={cur.lastrowid}")
                return redirect(url_for("credentials_index"))
            except ValueError as exc: error = str(exc)
        return render_template("credential_form.html", title="Новый служебный доступ", values=values,
            editing=False, has_password=False, error=error, csrf=app_core.csrf_token())

    @app_core.permission_required("credentials_view")
    @app_core.permission_required("credentials_edit")
    def credentials_edit_override(credential_id):
        row = credentials_store._get_credential(credential_id)
        error = None
        values = {"service_name":row["service_name"], "login":row["login"], "note":row["note"]}
        if request.method == "POST":
            app_core.require_csrf()
            try:
                service, login, password, note = _credential_values()
                values = {"service_name":service, "login":login, "note":note}
                clear = request.form.get("clear_password") == "1"; now = app_core.iso_now()
                if clear:
                    app_core.db().execute("UPDATE credentials SET service_name=?,login=?,secret_token='',note=?,updated_by=?,updated_at=? WHERE id=?",
                        (service,login,note,g.user["id"],now,credential_id))
                elif password:
                    app_core.db().execute("UPDATE credentials SET service_name=?,login=?,secret_token=?,note=?,updated_by=?,updated_at=? WHERE id=?",
                        (service,login,credentials_store._encrypt_secret(password),note,g.user["id"],now,credential_id))
                else:
                    app_core.db().execute("UPDATE credentials SET service_name=?,login=?,note=?,updated_by=?,updated_at=? WHERE id=?",
                        (service,login,note,g.user["id"],now,credential_id))
                app_core.db().commit(); app_core.audit("credential_updated", details=f"credential_id={credential_id}; password_changed={bool(password) or clear}")
                return redirect(url_for("credentials_index"))
            except ValueError as exc: error = str(exc)
        return render_template("credential_form.html", title="Изменить служебный доступ", values=values,
            editing=True, has_password=bool(row["secret_token"]), error=error, csrf=app_core.csrf_token())

    @app_core.permission_required("credentials_view")
    @app_core.permission_required("credentials_delete")
    def credentials_delete_override(credential_id):
        row = credentials_store._get_credential(credential_id); error = None
        if request.method == "POST":
            app_core.require_csrf()
            password = request.form.get("current_password", "")
            confirmation = request.form.get("service_confirmation", "").strip()
            if not credentials_store._current_password_ok(password):
                credentials_store._audit_failed_confirmation("credential_trash_failed", credential_id, "password"); error = "Пароль AZ-BAZE неверен."
            elif row["service_name"] and confirmation != row["service_name"]:
                credentials_store._audit_failed_confirmation("credential_trash_failed", credential_id, "service_confirmation"); error = "Название сервиса введено не точно."
            else:
                app_core.db().execute("INSERT INTO credential_trash(credential_id,deleted_by,deleted_at) VALUES(?,?,?)",
                    (credential_id,g.user["id"],app_core.iso_now()))
                app_core.db().commit(); app_core.audit("credential_trashed", details=f"credential_id={credential_id}")
                return redirect(url_for("credentials_index"))
        return render_template("credential_delete_confirm.html", row=row, display_title=_credential_label(row),
            error=error, csrf=app_core.csrf_token())

    @app_core.permission_required("credentials_view")
    @app_core.permission_required("credentials_delete")
    def credentials_purge_override(credential_id):
        if not credentials_store._can_purge(g.user): abort(403)
        row = credentials_store._get_credential(credential_id, trashed=True)
        label = (row["service_name"] or "").strip() or "ДОСТУП БЕЗ НАЗВАНИЯ"
        phrase = f"УДАЛИТЬ НАВСЕГДА {label}"; error = None
        if request.method == "POST":
            app_core.require_csrf(); password = request.form.get("current_password", ""); confirmation = request.form.get("purge_confirmation", "").strip()
            if not credentials_store._current_password_ok(password):
                credentials_store._audit_failed_confirmation("credential_purge_failed", credential_id, "password"); error = "Пароль AZ-BAZE неверен."
            elif confirmation != phrase:
                credentials_store._audit_failed_confirmation("credential_purge_failed", credential_id, "phrase_confirmation"); error = "Подтверждающая фраза введена не точно."
            else:
                app_core.db().execute("DELETE FROM credentials WHERE id=?", (credential_id,)); app_core.db().commit()
                app_core.audit("credential_purged", details=f"credential_id={credential_id}"); return redirect(url_for("credentials_trash"))
        return render_template("credential_purge_confirm.html", row=row, display_title=_credential_label(row), phrase=phrase,
            error=error, csrf=app_core.csrf_token())

    app.view_functions["credentials_index"] = credentials_index_override
    app.view_functions["credentials_new"] = credentials_new_override
    app.view_functions["credentials_edit"] = credentials_edit_override
    app.view_functions["credentials_delete"] = credentials_delete_override
    app.view_functions["credentials_purge"] = credentials_purge_override

    @app.get("/contacts/")
    @app_core.permission_required("credentials_view")
    def work_contacts_index():
        rows = app_core.db().execute("""
            SELECT * FROM work_contacts
            ORDER BY CASE WHEN TRIM(full_name)='' THEN 1 ELSE 0 END,full_name COLLATE NOCASE,organization COLLATE NOCASE,id
        """).fetchall()
        perms = app_core.user_permissions(g.user)
        return render_template("contacts.html", rows=rows, can_edit="credentials_edit" in perms, can_delete="credentials_delete" in perms)

    @app.route("/contacts/new", methods=["GET","POST"])
    @app_core.permission_required("credentials_view")
    @app_core.permission_required("credentials_edit")
    def work_contacts_new():
        values = {k:"" for k in ("organization","organization_role","full_name","phone","responsibility")}; error = None
        if request.method == "POST":
            app_core.require_csrf()
            try:
                vals = _contact_values(); values = dict(zip(values, vals)); now = app_core.iso_now()
                cur = app_core.db().execute("""
                    INSERT INTO work_contacts(organization,organization_role,full_name,phone,responsibility,created_by,updated_by,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?)
                """, (*vals,g.user["id"],g.user["id"],now,now))
                app_core.db().commit(); app_core.audit("work_contact_created", details=f"contact_id={cur.lastrowid}")
                return redirect(url_for("work_contacts_index"))
            except ValueError as exc: error = str(exc)
        return render_template("contact_form.html", title="Новый рабочий контакт", values=values, error=error, csrf=app_core.csrf_token())

    @app.route("/contacts/<int:contact_id>/edit", methods=["GET","POST"])
    @app_core.permission_required("credentials_view")
    @app_core.permission_required("credentials_edit")
    def work_contacts_edit(contact_id):
        row = _contact(contact_id); keys=("organization","organization_role","full_name","phone","responsibility")
        values = {k:row[k] for k in keys}; error = None
        if request.method == "POST":
            app_core.require_csrf()
            try:
                vals = _contact_values(); values = dict(zip(keys, vals)); now = app_core.iso_now()
                app_core.db().execute("""
                    UPDATE work_contacts SET organization=?,organization_role=?,full_name=?,phone=?,responsibility=?,updated_by=?,updated_at=? WHERE id=?
                """, (*vals,g.user["id"],now,contact_id))
                app_core.db().commit(); app_core.audit("work_contact_updated", details=f"contact_id={contact_id}")
                return redirect(url_for("work_contacts_index"))
            except ValueError as exc: error = str(exc)
        return render_template("contact_form.html", title="Изменить рабочий контакт", values=values, error=error, csrf=app_core.csrf_token())

    @app.route("/contacts/<int:contact_id>/delete", methods=["GET","POST"])
    @app_core.permission_required("credentials_view")
    @app_core.permission_required("credentials_delete")
    def work_contacts_delete(contact_id):
        row = _contact(contact_id)
        if request.method == "POST":
            app_core.require_csrf(); app_core.db().execute("DELETE FROM work_contacts WHERE id=?", (contact_id,)); app_core.db().commit()
            app_core.audit("work_contact_deleted", details=f"contact_id={contact_id}"); return redirect(url_for("work_contacts_index"))
        return render_template("contact_delete_confirm.html", row=row, csrf=app_core.csrf_token())

    @app.route("/admin/users/<int:user_id>/delete", methods=["GET","POST"])
    @app_core.admin_required
    def admin_delete_user(user_id):
        user = app_core.db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user: abort(404)
        if user_id == g.user["id"]: abort(400)
        if request.method == "POST":
            app_core.require_csrf(); details=f"deleted_user_id={user_id}; email={user['email']}; full_name={user['full_name']}"
            app_core.db().execute("DELETE FROM users WHERE id=?", (user_id,)); app_core.db().commit(); app_core.audit("user_deleted", details=details)
            return redirect(url_for("admin"))
        return render_template("user_delete_confirm.html", user=user, csrf=app_core.csrf_token())
