"""Rearrange the existing cards and landscape photograph on /team/."""
import datetime
import fcntl
import os
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path('/var/www/azmednsk-preview')
BACKUPS = Path('/var/backups/azmednsk')
DUO = '<div class="t2-duo t2-follow">'
GRID = '<div class="grid t2-people">'
ROW = '<div class="grid t2-people dental-staff-row">'
FIGURE = '<figure class="t2-photo t2-landscape">'
GROUP = '/assets/team2-v1/team2-assistants-1.webp'
STAFF = ('person-cherevko', 'person-alatartseva', 'person-borisovskaya', 'person-abdurakhimov')
CSS = """<style id="az-dental-staff-row-v1">
#dentistry .t2-follow > .t2-landscape { align-self: start; }
#dentistry > .dental-staff-row { margin-top: 24px; grid-template-columns: repeat(4, minmax(0, 1fr)); }
@media (max-width: 1000px) {
  #dentistry > .dental-staff-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>"""


def require(ok, message):
    if not ok:
        raise ValueError(message)


def card_ids(markup):
    return re.findall(r'<article class="person"[^>]*\bid="([^"]+)"', markup)


def rearrange(html):
    sections = list(re.finditer(r'<section\b[^>]*\bid="dentistry"[^>]*>.*?</section>', html, re.S))
    require(len(sections) == 1, 'Ожидался один блок стоматологии')
    section = sections[0]
    original = section.group()
    head, delimiter, tail = original.partition(DUO + GRID)
    require(delimiter and DUO not in tail, 'Не найден ряд с Бесединой и Голышенковым')
    cards, delimiter, rest = tail.partition('</div>' + FIGURE)
    require(delimiter and rest.count('src="' + GROUP + '"') == 1,
            'Проверьте положение общего фото')
    figure, delimiter, footer = rest.partition('</figure></div>')
    require(delimiter and '<figcaption' not in figure, 'Изменилась разметка общего фото')
    before = card_ids(cards)
    if footer != '</section>':
        require(before == ['person-9', 'person-11']
                and footer.startswith(ROW) and footer.endswith('</div></section>')
                and card_ids(footer) == list(STAFF) and html.count(CSS) == 1,
                'В блоке стоматологии уже есть другая раскладка')
        return html
    require(before == ['person-9', 'person-11', *STAFF] and len(card_ids(original)) == 10,
            'Изменился порядок карточек')
    cut = cards.rindex('<article', 0, cards.index('id="person-cherevko"'))
    doctors, staff = cards[:cut], cards[cut:]
    require(card_ids(doctors) == before[:2] and card_ids(staff) == list(STAFF),
            'Не удалось разделить карточки')
    require('<style id="az-dental-staff-row-v1">' not in html and html.count('</head>') == 1,
            'Проверьте стили страницы')
    updated = (head + DUO + GRID + doctors + '</div>' + FIGURE + figure + delimiter
               + ROW + staff + '</div></section>')
    return (html[:section.start()] + updated + html[section.end():]).replace('</head>', CSS + '</head>', 1)


def install(root, backups):
    page = root / 'team/index.html'
    require(page.resolve() == page, 'Проверьте путь страницы')
    lock = os.open(page.parent, os.O_RDONLY)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = page.read_bytes()
        updated = rearrange(original.decode('utf-8')).encode('utf-8')
        if original == updated:
            print('DENTAL_LAYOUT=ALREADY_APPLIED')
            return
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = backups / ('team-dental-layout-' + stamp)
        backup.mkdir(mode=0o700, parents=True)
        shutil.copy2(page, backup / 'index.html')
        require((backup / 'index.html').read_bytes() == original, 'Страница изменилась при копировании')
        st = page.stat()
        with tempfile.NamedTemporaryFile(dir=page.parent, delete=False) as f:
            tmp = Path(f.name)
        try:
            with tmp.open('wb') as f:
                f.write(updated)
                f.flush()
                os.fsync(f.fileno())
            if os.geteuid() == 0:
                os.chown(tmp, st.st_uid, st.st_gid)
            tmp.chmod(st.st_mode & 0o7777)
            require(page.read_bytes() == original, 'Страница изменилась, повторите запуск')
            os.replace(tmp, page)
            require(page.read_bytes() == updated, 'Проверка опубликованной страницы не прошла')
        finally:
            if tmp.exists():
                tmp.unlink()
        print('DENTAL_LAYOUT=PASS; GROUP_PHOTO=TOP_RIGHT; STAFF_ROW=4')
        print('BACKUP=' + str(backup))
    finally:
        os.close(lock)


if __name__ == '__main__':
    install(ROOT, BACKUPS)
    print('URL=https://azmednsk.ru/team/#dentistry')
