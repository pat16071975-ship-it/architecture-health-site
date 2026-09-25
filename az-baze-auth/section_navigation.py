import html
import re


SECTION_RULES = (
    ("/reports/", "Отчёты", "/reports/"),
    ("/access-contacts/", "Доступы и контакты", "/access-contacts/"),
    ("/contacts/", "Доступы и контакты", "/access-contacts/"),
    ("/credentials/", "Доступы и контакты", "/access-contacts/"),
    ("/surveys/", "Опросы", "/surveys/"),
    ("/uploads/", "Загрузка данных", "/uploads/"),
    ("/structure/clinics/", "Настройка клиник", "/structure/clinics/"),
    ("/admin", "Управление доступом", "/admin"),
    ("/knowledge/", "База знаний", "/knowledge/"),
)

EXCLUDED_PATHS = {
    "/login",
    "/logout",
    "/change-password",
    "/reports/services.html",
}

NAV_CSS = r"""
<style id="az-section-nav-style">
.az-section-nav{
  position:sticky;
  top:0;
  z-index:10000;
  width:min(1180px,calc(100% - 24px));
  margin:0 auto;
  padding:8px 0 6px;
  display:flex;
  justify-content:flex-end;
  gap:8px;
  background:#f7f3ec;
  box-shadow:0 0 0 100vmax #f7f3ec,0 1px 0 rgba(181,150,98,.22);
  clip-path:inset(0 -100vmax);
  pointer-events:none;
}
.az-section-nav__btn{
  min-height:38px;
  padding:8px 13px;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  border:1px solid rgba(181,150,98,.46);
  border-radius:9px;
  background:#efe8db;
  box-shadow:0 5px 18px rgba(71,58,38,.08);
  color:#354039;
  text-decoration:none;
  font:600 11px/1.15 Montserrat,Arial,sans-serif;
  white-space:nowrap;
  pointer-events:auto;
}
.az-section-nav__btn:hover,
.az-section-nav__btn:focus-visible{
  outline:0;
  border-color:rgba(68,99,79,.55);
  background:#fbf8f2;
  color:#44634f;
}
.az-section-nav__btn.current{
  background:#44634f;
  border-color:#44634f;
  color:#fff;
  cursor:default;
}
@media(min-width:761px){
  body .toolbar{top:52px!important}
}
@media(max-width:760px){
  body{padding-bottom:72px!important}
  .az-section-nav{
    position:fixed;
    left:0;
    right:0;
    bottom:0;
    top:auto;
    width:100%;
    margin:0;
    padding:8px max(10px,env(safe-area-inset-right)) calc(8px + env(safe-area-inset-bottom)) max(10px,env(safe-area-inset-left));
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:8px;
    background:#f7f3ec;
    border-top:1px solid rgba(181,150,98,.30);
    box-shadow:0 -8px 24px rgba(71,58,38,.08);
  }
  .az-section-nav__btn{
    width:100%;
    min-height:44px;
    padding:9px 8px;
    font-size:10px;
    text-align:center;
    white-space:normal;
  }
}
@media print{
  .az-section-nav{display:none!important}
  body{padding-bottom:0!important}
}
</style>
"""


def section_for_path(path):
    if not path or path in EXCLUDED_PATHS or path.startswith("/survey/"):
        return None
    for prefix, label, root in SECTION_RULES:
        if path == prefix.rstrip("/") or path.startswith(prefix):
            return {"label": label, "root": root}
    return None


def _is_root(path, root):
    return path.rstrip("/") == root.rstrip("/")


def render_navigation(path):
    section = section_for_path(path)
    if not section:
        return ""
    label = html.escape(section["label"])
    root = html.escape(section["root"], quote=True)
    if _is_root(path, section["root"]):
        section_button = (
            f'<span class="az-section-nav__btn current" aria-current="page">{label}</span>'
        )
    else:
        section_button = f'<a class="az-section-nav__btn" href="{root}">{label}</a>'
    return (
        '<nav class="az-section-nav" aria-label="Навигация по разделу">'
        + section_button
        + '<a class="az-section-nav__btn" href="/">На главную</a>'
        + "</nav>"
    )


def _remove_exact_anchor(text, href, labels):
    labels = {label.strip() for label in labels}
    pattern = re.compile(
        r'<a\b(?P<attrs>[^>]*)>(?P<body>[\s\S]*?)</a>',
        re.IGNORECASE,
    )

    def replace(match):
        attrs = match.group("attrs")
        href_match = re.search(r'\bhref\s*=\s*["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        if not href_match or href_match.group(1) != href:
            return match.group(0)
        body = re.sub(r'<[^>]+>', '', match.group("body"))
        label = html.unescape(body).strip()
        return "" if label in labels else match.group(0)

    return pattern.sub(replace, text)


def strip_duplicate_navigation(text, path):
    section = section_for_path(path)
    if not section:
        return text
    text = _remove_exact_anchor(text, "/", ("На главную", "← На главную"))
    if section["root"] == "/reports/":
        text = _remove_exact_anchor(text, "/reports/", ("К отчётам", "Отчёты"))
    elif section["root"] == "/access-contacts/":
        text = _remove_exact_anchor(text, "/access-contacts/", ("К разделу", "Доступы и контакты"))
    elif section["root"] == "/surveys/":
        text = _remove_exact_anchor(text, "/surveys/", ("К опросам", "Все опросы", "Опросы"))
    text = re.sub(
        r'<div\b[^>]*class=["\'][^"\']*\bactions\b[^"\']*["\'][^>]*>\s*</div>',
        '',
        text,
        flags=re.IGNORECASE,
    )
    return text


def inject_navigation(text, path):
    nav = render_navigation(path)
    if not nav or "az-section-nav" in text:
        return text
    if "<body" not in text.lower() or "</head>" not in text.lower():
        return text
    text = strip_duplicate_navigation(text, path)
    text = text.replace("</head>", NAV_CSS + "\n</head>", 1)
    text = re.sub(r"(<body\b[^>]*>)", r"\1\n" + nav, text, count=1, flags=re.IGNORECASE)
    return text
