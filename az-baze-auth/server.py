from app import app
from report_storage import register_report_storage

register_report_storage(app)


@app.after_request
def allow_same_origin_report_frame(response):
    if response.request.path == "/reports/services-base.html" if hasattr(response, "request") else False:
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response
