from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp
from urllib.request import urlopen
from hashlib import sha256
from time import sleep
import os
import shutil
import fcntl

ROOT = Path('/var/www/azmednsk-preview')
BACKUPS = Path('/var/backups/azmednsk')
ORTHO = '/uslugi/orthodontics/'
JOINT = '/chto-my-lechim/vnchs/'
CLICK = '/cases/shchelchki-v-chelyusti/'
BITE = '/cases/mezialnyj-prikus-u-rebenka/'
TRAVEL = '/cases/korrekciya-prikusa-u-rebenka-iz-drugogo-goroda/'

def link(path, title):
    return f'<a href="{path}">{title}</a>'

def prepare(url, html):
    if url == CLICK:
        if html.count(link(JOINT, 'Лечение дисфункции ВНЧС')) != 1:
            raise ValueError('Проверьте обратную ссылку в истории ВНЧС')
        return html
    if url == JOINT:
        anchor = '<h2>Связанные материалы</h2><div class="related">'
        replacement = anchor + link(CLICK, 'Как проходило лечение, когда щёлкала челюсть и было трудно открыть рот')
    elif url == ORTHO:
        anchor = 'сразу установят брекеты.</p><h3>Взрослым</h3>'
        stories = '<ul><li>' + link(BITE, 'Ранняя коррекция мезиального прикуса у ребёнка') + '</li><li>' + link(TRAVEL, 'Как проходило лечение ребёнка из другого города') + '</li></ul>'
        replacement = anchor.replace('<h3>', '<p><strong>Истории лечения детей</strong></p>' + stories + '<h3>')
    else:
        anchor = link('/detyam/detskij-ortodont/', 'Детская ортодонтия') + '</div></main>'
        replacement = anchor.replace('</div>', link(ORTHO + '#orthodontics-first-visit', 'Как проходит консультация ортодонта') + '</div>')
    if html.count(replacement) == 1:
        return html
    if html.count(anchor) != 1:
        raise ValueError('Изменилась разметка ' + url)
    return html.replace(anchor, replacement)

def write(path, data):
    with NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(data)
    try:
        shutil.copystat(path, tmp.name)
        os.replace(tmp.name, path)
    finally:
        Path(tmp.name).unlink(missing_ok=True)

def verify(url, data):
    for attempt in range(3):
        try:
            with urlopen('https://azmednsk.ru' + url + '?v=' + sha256(data).hexdigest()[:12], timeout=20) as r:
                if r.read() != data:
                    raise ValueError('HTTPS отличается: ' + url)
            return
        except (OSError, ValueError):
            if attempt == 2:
                raise
            sleep(2)

def install():
    originals, updates, paths = {}, {}, {}
    for url in (JOINT, ORTHO, CLICK, BITE, TRAVEL):
        path = ROOT / url.strip('/') / 'index.html'
        paths[url] = path
        originals[url] = path.read_bytes()
        updates[url] = prepare(url, originals[url].decode()).encode()
    if updates[ORTHO].count(b'id="orthodontics-first-visit"') != 1:
        raise ValueError('Отсутствует раздел консультации')
    changed = [u for u in paths if originals[u] != updates[u]]
    if changed:
        backup = Path(mkdtemp(prefix='case-links-',dir=BACKUPS))
        for u in changed:
            dest = backup / paths[u].relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(paths[u], dest)
        print('BACKUP=' + str(backup), flush=True)
    written = []
    try:
        for u in changed:
            if paths[u].read_bytes() != originals[u]:
                raise RuntimeError('Страница изменилась: ' + u)
            write(paths[u], updates[u])
            written.append(u)
        for u in paths:
            verify(u, updates[u])
    except BaseException:
        for u in reversed(written):
            if paths[u].read_bytes() == updates[u]:
                write(paths[u], originals[u])
        raise
    print(f'CASE_LINKS=PASS; LINKS=6; HTTPS_PAGES=5; CHANGED={len(changed)}')

if __name__ == '__main__':
    with open(BACKUPS/'.case-links.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        install()
