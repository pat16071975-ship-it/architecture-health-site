"""Install the cases link on the AZMEDNSK homepage after the cases page is ready."""
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import tempfile


PAIR = (
    '<a class="az-home-half" href="/chto-my-lechim/" aria-label="Что мы лечим">Что мы лечим</a>'
    '<a class="az-home-half az-home-faq-link" href="/otvety-na-voprosy/" '
    'aria-label="Ответы на вопросы">Ответы на вопросы</a>'
)
LINK = '<a class="az-home-cases" href="/cases/">Наши кейсы<span aria-hidden="true">↗</span></a>'
STYLE = """<style id="az-home-cases-style">
.azh-home .nav>a.az-home-cases {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  padding: 8px 16px;
  border: 1px solid #9cc6dc;
  background: #f0f7fc;
  color: #166ca2;
  font-weight: 600;
}
.azh-home .nav>a.az-home-cases:hover,
.azh-home .nav>a.az-home-cases:focus-visible {
  background: #eaf5fc;
  border-color: #2080c4;
}
</style>"""


def install(root, backups):
    home = root / "index.html"
    if home.is_symlink():
        raise SystemExit("Проверьте путь к главной странице.")
    if not (root / "cases/index.html").is_file():
        raise SystemExit("Сначала установите утверждённую страницу /cases/.")
    before = home.read_bytes()
    page = before.decode("utf-8")
    if page.count(PAIR + LINK + "</nav>") == 1 and page.count(STYLE) == 1:
        return
    if (page.count(PAIR + "</nav>") != 1 or page.count("</head>") != 1
            or "az-home-cases" in page or 'href="/cases/"' in page):
        raise SystemExit("Разметка главной отличается. Проверьте место вставки.")
    page = page.replace(PAIR + "</nav>", PAIR + LINK + "</nav>")
    page = page.replace("</head>", STYLE + "</head>")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backups / ("home-cases-" + stamp)
    backup.mkdir(parents=True)
    (backup / "index.html").write_bytes(before)
    with tempfile.NamedTemporaryFile(dir=root, delete=False) as out:
        tmp = Path(out.name)
    try:
        tmp.write_bytes(page.encode("utf-8"))
        meta = home.stat()
        if os.geteuid() == 0:
            os.chown(tmp, meta.st_uid, meta.st_gid)
        shutil.copymode(home, tmp)
        if home.read_bytes() != before:
            raise SystemExit("Главная изменилась во время установки.")
        os.replace(tmp, home)
    finally:
        tmp.unlink(missing_ok=True)
    print("BACKUP=" + str(backup))


if __name__ == "__main__":
    install(Path("/var/www/azmednsk-preview"), Path("/var/backups/azmednsk"))
    print("HOME_CASES_TILE=PASS")
