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


MANAGEMENT_COMPACT_CSS = r"""
<style id="az-management-compact-v6">
@media (min-width:851px){
  #editView .grid{
    display:flex!important;
    flex-wrap:nowrap!important;
    align-items:stretch!important;
    gap:5px!important;
    padding:6px 8px!important;
  }
  #editView .metric{
    flex:1 1 0!important;
    width:auto!important;
    min-width:0!important;
    max-width:none!important;
    min-height:38px!important;
    padding:3px 6px!important;
    border-radius:7px!important;
  }
  #editView .metric label{
    margin:0 0 2px!important;
    font-size:8px!important;
    line-height:1.05!important;
    white-space:normal!important;
  }
  #editView .metric input{
    padding:1px 0!important;
    min-height:16px!important;
    font-size:11px!important;
    line-height:1.05!important;
  }
  #editView .section h2{
    padding:6px 12px!important;
  }
  #editView .data-table th,
  #editView .data-table td{
    padding:2px 8px!important;
    line-height:1.05!important;
    height:28px!important;
  }
  #editView .data-table input{
    width:108px!important;
    min-height:22px!important;
    padding:1px 6px!important;
    font-size:10.5px!important;
    line-height:1!important;
  }
}
</style>
"""

SERVICES_COMPACT_CSS = r"""
<style id="az-services-compact-v1">
@media (min-width:901px){
  .kpis{display:flex!important;flex-wrap:wrap!important;gap:5px!important;margin:8px 0!important}
  .kpi{flex:0 0 118px!important;width:118px!important;min-width:118px!important;max-width:118px!important;padding:5px 7px!important;border-radius:8px!important}
  .kpi span{margin-bottom:3px!important;font-size:9px!important;line-height:1.05!important}
  .kpi strong{font-size:17px!important;line-height:1!important}
  .toolbar{gap:6px!important;padding:9px!important}
  .toolbar .field{min-width:140px!important}
  .field input,.field select{padding:6px 8px!important;font-size:12px!important}
  .table th,.table td{padding:5px 7px!important}
}
</style>
"""

FORECAST_COMPACT_CSS = r"""
<style id="az-forecast-compact-v1">
@media (min-width:1001px){
  .summary{display:flex!important;flex-wrap:wrap!important;gap:5px!important;padding:7px!important}
  .summary .metric{flex:0 0 118px!important;width:118px!important;min-width:118px!important;max-width:118px!important;padding:5px 7px!important;border-radius:8px!important}
  .summary .metric span{font-size:8px!important;line-height:1.05!important}
  .summary .metric strong{margin-top:3px!important;font-size:17px!important;line-height:1!important}
  .bar{gap:6px!important;padding:8px 10px!important}
  .field input,.field select{padding:6px 7px!important}
  .scenario-body{padding:8px!important}
  .inputs{gap:5px!important}
}
</style>
"""

TRANSITIONS_COMPACT_CSS = r"""
<style id="az-transitions-compact-v1">
@media (min-width:1101px){
  .kpis{display:flex!important;flex-wrap:wrap!important;gap:5px!important;margin:8px 0!important}
  .kpi{flex:0 0 118px!important;width:118px!important;min-width:118px!important;max-width:118px!important;min-height:0!important;padding:5px 7px!important;border-radius:8px!important}
  .kpi span{margin-bottom:3px!important;font-size:9px!important;line-height:1.05!important}
  .kpi strong{font-size:17px!important;line-height:1!important}
  .kpi small{margin-top:2px!important;font-size:8px!important}
  .toolbar{gap:6px!important;padding:8px!important}
  .toolbar .field{min-width:150px!important}
  .toolbar .field.source{min-width:220px!important}
  .field input,.field select{padding:6px 8px!important;font-size:12px!important}
}
</style>
"""


def _inject_before_head_close(html, fragment):
    if fragment.split('id="', 1)[1].split('"', 1)[0] in html:
        return html
    return html.replace("</head>", fragment + "\n</head>", 1)


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
            html = _inject_before_head_close(html, SERVICES_COMPACT_CSS)
            response.set_data(html)

    if request.path == "/reports/management/" and response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        html = re.sub(
            r"mobile-date-fix\.css\?v=[0-9-]+",
            "mobile-date-fix.css?v=20260907-4",
            html,
        )
        # Use inline desktop overrides so the approved compact layout cannot be lost
        # because of a stale/missing external stylesheet.
        html = _inject_before_head_close(html, MANAGEMENT_COMPACT_CSS)
        if "management-mobile-layout.js" not in html:
            html = html.replace(
                "</body>",
                '<script src="./management-mobile-layout.js?v=20260907-1"></script>\n</body>',
                1,
            )
        response.set_data(html)

    if request.path == "/reports/forecast/" and response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        html = _inject_before_head_close(html, FORECAST_COMPACT_CSS)
        response.set_data(html)

    if request.path == "/reports/transitions/" and response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        html = _inject_before_head_close(html, TRANSITIONS_COMPACT_CSS)
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
