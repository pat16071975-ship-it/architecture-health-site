import re

from flask import request, send_from_directory

from app import SITE_ROOT, app
import economics_control
import finrez
import ident_import
import report_storage
import terminology

# Economic source workbooks are ~9 MB. Keep the application limit aligned
# with the Nginx upload limit so valid admin imports are not rejected.
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

report_storage.register_report_storage(app)
terminology.install(app, report_storage)
ident_import.register_ident_import(app)
finrez.register_finrez(app)
economics_control.register_economics_control(app)


@app.get("/favicon.svg")
def favicon_svg():
    return send_from_directory(SITE_ROOT / "assets", "favicon.svg", mimetype="image/svg+xml")


@app.after_request
def tune_report_response(response):
    if request.path == "/reports/services-base.html":
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        if response.mimetype == "text/html":
            html = response.get_data(as_text=True)
            html = html.replace('<a class="btn" href="./">Управленческий отчёт</a>', '')
            html = re.sub(
                r'<a class="btn" target="_blank" rel="noopener" href="[^"]+">Прайс-классификатор</a>',
                '',
                html,
            )
            html = html.replace('<a class="btn" href="/reports/">К отчётам</a>', '')
            html = html.replace('<a class="btn" href="/">На главную</a>', '')
            response.set_data(html)

    if request.path == "/reports/management/" and response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        html = re.sub(
            r"mobile-date-fix\.css\?v=[0-9-]+",
            "mobile-date-fix.css?v=20260907-4",
            html,
        )
        if "management-compact-metrics.css" not in html:
            html = html.replace(
                "</head>",
                '<link rel="stylesheet" href="./management-compact-metrics.css?v=20260909-2">\n</head>',
                1,
            )
        else:
            html = re.sub(
                r"management-compact-metrics\.css\?v=[0-9-]+",
                "management-compact-metrics.css?v=20260909-2",
                html,
            )
        if "management-mobile-layout.js" not in html:
            html = html.replace(
                "</body>",
                '<script src="./management-mobile-layout.js?v=20260907-1"></script>\n</body>',
                1,
            )
        response.set_data(html)

    if response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        if 'rel="icon"' not in html:
            html = html.replace(
                "</head>",
                '<link rel="icon" type="image/svg+xml" href="/favicon.svg?v=20260908-1">\n</head>',
                1,
            )
            response.set_data(html)

    return response
