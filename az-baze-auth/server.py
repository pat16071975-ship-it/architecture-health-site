import re

from flask import request

from app import app
import ident_import
import report_storage
import terminology

report_storage.register_report_storage(app)
terminology.install(app, report_storage)
ident_import.register_ident_import(app)


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
            "mobile-date-fix.css?v=20260907-3",
            html,
        )
        response.set_data(html)

    return response
