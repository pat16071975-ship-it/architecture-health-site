from flask import abort, g, redirect, render_template, request, url_for

import app as app_core
from clinic_timezone import (
    ClinicTimezoneError,
    get_clinic_timezone,
    set_clinic_timezone,
    timezone_options,
)


PERMISSION_OPTIONS = [
    ("structure_manage", "Структура клиник — настройка"),
]
PERMISSION_KEY = "structure_manage"

FOUNDATION_TABLES = {
    "holdings",
    "organizations",
    "clusters",
    "clinics",
    "directions",
    "clinic_directions",
}


def _foundation_ready(conn):
    tables = {
        row["name"]
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """
        )
    }
    return FOUNDATION_TABLES.issubset(tables)


def _clinic_rows(conn):
    if not _foundation_ready(conn):
        return []
    return conn.execute(
        """
        SELECT
            c.id,
            c.name,
            c.timezone,
            c.status,
            o.name AS organization_name,
            cl.name AS cluster_name
        FROM clinics c
        JOIN organizations o ON o.id=c.organization_id
        JOIN clusters cl ON cl.id=c.cluster_id
        ORDER BY o.name COLLATE NOCASE, cl.name COLLATE NOCASE,
                 c.name COLLATE NOCASE, c.id
        """
    ).fetchall()


def register(app):
    app_core.SECTION_KEYS.add(PERMISSION_KEY)
    app_core.EXPLICIT_PERMISSION_KEYS.add(PERMISSION_KEY)

    @app.context_processor
    def clinic_structure_permissions_context():
        return {"structure_permission_options": PERMISSION_OPTIONS}

    @app.get("/structure/clinics/")
    @app_core.permission_required(PERMISSION_KEY)
    def clinic_structure_index():
        conn = app_core.db()
        foundation_ready = _foundation_ready(conn)
        rows = _clinic_rows(conn) if foundation_ready else []
        return render_template(
            "clinic_structure.html",
            rows=rows,
            foundation_ready=foundation_ready,
        )

    @app.route("/structure/clinics/<int:clinic_id>/timezone", methods=["GET", "POST"])
    @app_core.permission_required(PERMISSION_KEY)
    def clinic_timezone_edit(clinic_id):
        conn = app_core.db()
        if not _foundation_ready(conn):
            abort(409)

        try:
            clinic = get_clinic_timezone(conn, clinic_id)
        except ClinicTimezoneError:
            abort(404)

        error = None
        selected_timezone = clinic["timezone"]

        if request.method == "POST":
            app_core.require_csrf()
            selected_timezone = request.form.get("timezone", "").strip()
            try:
                result = set_clinic_timezone(conn, clinic_id, selected_timezone)
                if result["changed"]:
                    app_core.audit(
                        "clinic_timezone_updated",
                        details=(
                            f"clinic_id={clinic_id}; "
                            f"old_timezone={result['old_timezone']}; "
                            f"new_timezone={result['timezone']}"
                        ),
                    )
                else:
                    conn.rollback()
                return redirect(url_for("clinic_structure_index"))
            except ClinicTimezoneError as exc:
                conn.rollback()
                error = str(exc)

        return render_template(
            "clinic_timezone_form.html",
            clinic=clinic,
            selected_timezone=selected_timezone,
            timezone_options=timezone_options(),
            error=error,
            csrf=app_core.csrf_token(),
        )

    @app.after_request
    def clinic_structure_home_link(response):
        if request.path != "/" or response.mimetype != "text/html":
            return response
        if not getattr(g, "user", None):
            return response
        if PERMISSION_KEY not in app_core.user_permissions(g.user):
            return response

        html = response.get_data(as_text=True)
        link = (
            '<a class="menu-btn active" href="/structure/clinics/" '
            'id="clinic-structure">Настройка клиник</a>'
        )
        if link in html:
            return response

        admin_link = '<a class="menu-btn active" href="/admin">Управление доступом</a>'
        knowledge_link = (
            '<a class="menu-btn active" href="/knowledge/" '
            'id="knowledge">База знаний</a>'
        )
        anchor = admin_link if admin_link in html else knowledge_link
        if anchor in html:
            html = html.replace(anchor, anchor + "\n" + link, 1)
            response.set_data(html)
        return response
