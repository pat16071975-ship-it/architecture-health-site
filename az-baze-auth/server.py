import re

from flask import request

from app import app
from report_storage import register_report_storage
from terminology import apply_report_terminology

register_report_storage(app)
apply_report_terminology()


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
    return response
