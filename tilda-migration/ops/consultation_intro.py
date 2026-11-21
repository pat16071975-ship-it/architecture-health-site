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
def replace_once(html, before, after):
    if html.count(before) != 1:
        raise ValueError('Изменилась разметка страницы')
    return html.replace(before, after)

def prepare(url, html):
    if url == ORTHO:
        edits = [
            ('в Новосибирске</h1></section>', 'в Новосибирске</h1><div class="az-age-links" role="navigation" aria-label="Ортодонтия по возрасту"><a href="#ortho-child">Для ребёнка</a><a href="#ortho-adult">Для взрослого</a></div></section>'),
            ('<h3>Детям и подросткам</h3>', '<h3 id="ortho-child">Детям и подросткам</h3>'),
            ('<h3>Взрослым</h3>', '<h3 id="ortho-adult">Взрослым</h3>'),
            ('</head>', '<style>.az-age-links{display:flex;gap:8px 20px;flex-wrap:wrap;margin-top:12px}.az-age-links a{color:#1369a5;text-decoration:underline;text-underline-offset:3px;padding:6px 0}#ortho-child,#ortho-adult{scroll-margin-top:100px}</style></head>'),
        ]
    else:
        edits = [
            ('определяет лечение.</p></div>', 'определяет лечение.</p><p class="az-visit-intro">С жалобами на боль и щелчки в челюсти в клинике принимают Роман Зубачёв и Анна Филатова. На первом приёме врач уточнит, когда появилась боль, осмотрит зубы и оценит движения челюсти.</p><a class="az-visit-cost" href="#vnchs-doctors">Стоимость первичного приёма у каждого врача</a></div>'),
            ('<h2 class="az-care-heading">К кому обратиться</h2>', '<h2 class="az-care-heading" id="vnchs-doctors">К кому обратиться</h2>'),
            ('</head>', '<style>.az-visit-intro{max-width:68ch;line-height:1.5;margin:12px 0 6px}.az-visit-cost{color:#1369a5;text-decoration:underline;text-underline-offset:3px}#vnchs-doctors{scroll-margin-top:100px}</style></head>'),
        ]
    for before, after in edits:
        html = replace_once(html, before, after)
    return html

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

HASHES = {'/chto-my-lechim/vnchs/': ('cdc32e5248fb239d301c7a44fe5c06b957e1ed0f130af27c0ddb352f26658023', 'fb4c2fea49b4d8c19f77292a43ed1297784aaccb339432b77a6fbaf91ea9e770'), '/uslugi/orthodontics/': ('42ad770bd66f0ac2bc7bf8748c34eb00ba2abc6efedbe4f5f8d0868879b4f836', 'e1056f2a1baadfea85da9b661af5bb50278b14002cf4a459c581e422812e68b8')}

def install():
    originals, updates, paths = {}, {}, {}
    for url in (JOINT, ORTHO):
        path = ROOT / url.strip('/') / 'index.html'
        paths[url] = path
        originals[url] = path.read_bytes()
        digest = sha256(originals[url]).hexdigest()
        before, after = HASHES[url]
        if digest not in (before, after):
            raise ValueError('Страница изменилась: ' + url)
        updates[url] = originals[url] if digest == after else prepare(url, originals[url].decode()).encode()
        if sha256(updates[url]).hexdigest() != after:
            raise ValueError('Проверка результата: ' + url)
    if updates[ORTHO].count(b'id="orthodontics-first-visit"') != 1:
        raise ValueError('Отсутствует раздел консультации')
    changed = [u for u in paths if originals[u] != updates[u]]
    if changed:
        backup = Path(mkdtemp(prefix='consultation-intro-',dir=BACKUPS))
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
    print(f'CONSULTATION_INTRO=PASS; HTTPS_PAGES=2; CHANGED={len(changed)}')

if __name__ == '__main__':
    with open(BACKUPS/'.case-links.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        install()
