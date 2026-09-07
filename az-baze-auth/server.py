from flask import request

from app import app
import report_storage
from terminology import install as install_report_terminology

install_report_terminology(app, report_storage)
report_storage.register_report_storage(app)


@app.after_request
def allow_same_origin_report_frame(response):
    if request.path == "/reports/services-base.html":
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response
