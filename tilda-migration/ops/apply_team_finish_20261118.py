"""Center the dental group photo, clean the main team's portrait links, and retire /team-2/."""
import datetime
import errno
import fcntl
import os
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path('/var/www/azmednsk-preview')
BACKUPS = Path('/var/backups/azmednsk')

TOP = '#dentistry .t2-follow > .t2-landscape { align-self: start; }'
CENTER = TOP + """
@media (min-width: 701px) {
  #dentistry .t2-follow { --portrait-gap: 24px; }
  #dentistry .t2-follow > .t2-people { column-gap: var(--portrait-gap); }
  .t2:not(.searching) #dentistry .t2-follow > .t2-landscape {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    align-items: center;
  }
  /* Reserve the same height as one of the two adjacent 2:3 portraits. */
  #dentistry .t2-follow > .t2-landscape::before {
    content: "";
    grid-area: 1 / 1;
    width: calc((100% - var(--portrait-gap)) / 2);
    aspect-ratio: 2 / 3;
    pointer-events: none;
  }
  #dentistry .t2-follow > .t2-landscape > .t2-frames { grid-area: 1 / 1; }
}
@media (min-width: 701px) and (max-width: 1000px) {
  #dentistry .t2-follow { --portrait-gap: 16px; }
}"""

LINK = '<a href="#help">Уточнить у администратора →</a>'
CARDS = re.compile(r'<article class="person"[^>]*>.*?</article>', re.S)


def need(ok, message):
    if not ok:
        raise ValueError(message)


def revise(html):
    need('<style id="az-dental-staff-row-v1">' in html and 'class="grid t2-people dental-staff-row"' in html,
         'Не найдена текущая раскладка основной команды')
    need(html.count(TOP) == 1, 'Не найден стиль общего фото')
    if CENTER not in html:
        html = html.replace(TOP, CENTER, 1)
    removed = sum(m.group().count(LINK) for m in CARDS.finditer(html))
    html = CARDS.sub(lambda m: m.group().replace(LINK, ''), html)
    need('уточнить у администратора' not in html.casefold(), 'Найдена дополнительная форма подписи; требуется проверка')
    return html, removed


def backup_page(page, content, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(page, destination)
    need(destination.read_bytes() == content, 'Не совпала резервная копия ' + str(page))


def write_atomic(page, original, data):
    st = page.stat()
    with tempfile.NamedTemporaryFile(dir=page.parent, delete=False) as f:
        tmp = Path(f.name)
    try:
        with tmp.open('wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if os.geteuid() == 0:
            os.chown(tmp, st.st_uid, st.st_gid)
        tmp.chmod(st.st_mode & 0o7777)
        need(page.read_bytes() == original, 'Страница изменилась во время установки')
        os.replace(tmp, page)
    finally:
        if tmp.exists():
            tmp.unlink()


def apply(root, backups):
    page = root / 'team/index.html'
    preview = root / 'team-2/index.html'
    need(page.resolve() == page and preview.resolve() == preview, 'Проверьте пути страниц')
    lock = os.open(page.parent, os.O_RDONLY)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = page.read_bytes()
        updated, count = revise(original.decode('utf-8'))
        after = updated.encode('utf-8')
        old_preview = preview.read_bytes() if preview.exists() else None
        if old_preview is not None:
            need(b'id="dentistry"' in old_preview and b'noindex' in old_preview
                 and b'https://azmednsk.ru/team/' in old_preview, 'Не опознана тренировочная страница')
        if original != after or old_preview is not None:
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = backups / ('team-finish-' + stamp)
            backup.mkdir(mode=0o700, parents=True)
            backup_page(page, original, backup / 'team/index.html')
            if old_preview is not None:
                backup_page(preview, old_preview, backup / 'team-2/index.html')
            print('BACKUP=' + str(backup))
            if original != after:
                write_atomic(page, original, after)
            if old_preview is not None:
                need(preview.read_bytes() == old_preview, 'Тренировочная страница изменилась во время установки')
                preview.unlink()
        try:
            preview.parent.rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            if exc.errno != errno.ENOTEMPTY:
                raise
        need(page.read_bytes() == after and not preview.exists(), 'Не прошла проверка страниц')
        print(f'TEAM_FINISH=PASS; PHOTO=PORTRAIT_CENTER; LINKS_REMOVED={count}; TEAM2_PAGE=REMOVED')
    finally:
        os.close(lock)


if __name__ == '__main__':
    apply(ROOT, BACKUPS)
    print('URL=https://azmednsk.ru/team/#dentistry')
