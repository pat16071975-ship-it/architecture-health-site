"""Center the dental team photo between the doctors' portrait tops and booking button bottoms."""
import datetime
import fcntl
import os
from pathlib import Path
import shutil
import tempfile

OPEN = '<style id="az-dental-staff-row-v1">'
OLD = """#dentistry .t2-follow > .t2-landscape { align-self: start; }
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
NEW = '#dentistry .t2-follow > .t2-landscape { align-self: center; }'


def need(ok, message):
    if not ok:
        raise ValueError(message)


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


def align(html):
    need(html.count(OPEN) == 1, 'Не найден блок стилей стоматологии')
    start = html.index(OPEN) + len(OPEN)
    end = html.index('</style>', start)
    css = html[start:end]
    if NEW in css and OLD not in css:
        return html
    need(css.count(OLD) == 1 and NEW not in css, 'Стиль общего фото изменился')
    return html[:start] + css.replace(OLD, NEW, 1) + html[end:]


def install(root, backups):
    page = root / 'team/index.html'
    need(page.resolve() == page, 'Проверьте путь страницы')
    lock = os.open(page.parent, os.O_RDONLY)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = page.read_bytes()
        after = align(before.decode('utf-8')).encode('utf-8')
        if before == after:
            print('TEAM_GROUP_FULL_CARD_CENTER=ALREADY_APPLIED')
            return
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = backups / ('team-full-card-center-' + stamp)
        backup.mkdir(mode=0o700, parents=True)
        backup_page(page, before, backup / 'index.html')
        write_atomic(page, before, after)
        need(page.read_bytes() == after, 'Не прошла проверка файла')
        print('TEAM_GROUP_FULL_CARD_CENTER=PASS')
        print('BACKUP=' + str(backup))
    finally:
        os.close(lock)


if __name__ == '__main__':
    install(Path('/var/www/azmednsk-preview'), Path('/var/backups/azmednsk'))
    print('URL=https://azmednsk.ru/team/#person-11')
