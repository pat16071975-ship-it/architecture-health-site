#!/usr/bin/env python3
"""Publish one noindex diagnostic page; never modify the home page or assets."""
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import tempfile

ROOT = Path('/var/www/azmednsk-test/tilda-migration/public')
NAME = 'az-layout-check-20261012.html'
EXPECTED_HOME = 'd022f1a321b0946a73f9a4ab17627506c26d68435cf0203a519ffef2f6dc28bf'
PAGE = '''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>АЗ: проверка мобильной вёрстки</title>
<style>body{margin:16px;font:15px Arial,sans-serif;background:#eee;color:#111}button{padding:10px;margin:4px}pre{white-space:pre-wrap;background:white;padding:12px}iframe{display:block;width:390px;height:1800px;background:white;border:1px solid #aaa;max-width:none}h1{font-size:20px}</style></head>
<body><h1>Проверка главной на узком экране</h1><p>Техническая страница. Главная открывается внутри рамки заданной ширины.</p>
<nav aria-label="Ширина"><button data-width="320">320 px</button><button data-width="390">390 px</button><button data-width="430">430 px</button><button data-width="640">640 px</button><button data-width="1363">1363 px</button></nav>
<button id="top">Верх страницы</button><button id="footer">Подвал</button><button id="reload">Перезагрузить главную</button><pre id="report">Загрузка…</pre>
<iframe id="preview" title="Главная сайта" src="/?az-layout-check=20261012"></iframe>
<script>
const frame=document.getElementById('preview');
function report(){
 const w=frame.contentWindow,d=frame.contentDocument;
 const grid=d.querySelector('.az-home-benefits');
 const footer=d.querySelector('#visual-2489767221-1648032312285 > .visual-inner');
 const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
 const data={viewport:w.innerWidth,grid:grid?{display:w.getComputedStyle(grid).display,columns:w.getComputedStyle(grid).gridTemplateColumns,rect:rect(grid)}:null,cards:[...d.querySelectorAll('.az-home-benefit')].map(rect),logo:footer?w.getComputedStyle(footer).backgroundImage:null,styles:[...d.querySelectorAll('link[rel="stylesheet"]')].map(e=>e.href)};
 document.getElementById('report').textContent=JSON.stringify(data,null,2);
}
frame.addEventListener('load',report);
document.querySelectorAll('[data-width]').forEach(b=>b.addEventListener('click',()=>{frame.style.width=b.dataset.width+'px';requestAnimationFrame(report)}));
document.getElementById('top').onclick=()=>{frame.contentWindow.scrollTo(0,0);report()};
document.getElementById('footer').onclick=()=>{frame.contentDocument.getElementById('layout-2489767221').scrollIntoView();report()};
document.getElementById('reload').onclick=()=>frame.contentWindow.location.reload();
</script></body></html>
'''.encode()

def main():
    assert socket.gethostname() == 'az-server', 'Нужен az-server'
    assert os.geteuid() == 0, 'Запустите через sudo'
    home = ROOT / 'index.html'
    before = home.read_bytes()
    assert hashlib.sha256(before).hexdigest() == EXPECTED_HOME, 'Главная изменилась — остановка'
    target = ROOT / NAME
    created = False
    try:
        if target.exists():
            assert target.read_bytes() == PAGE, 'Диагностический путь уже занят другим файлом'
        else:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            created = True
            with os.fdopen(fd, 'wb') as f:
                f.write(PAGE)
                f.flush()
                os.fsync(f.fileno())
            st = home.stat()
            os.chown(target, st.st_uid, st.st_gid)
        with tempfile.TemporaryDirectory() as td:
            body = Path(td) / 'body'
            subprocess.run(['curl', '--noproxy', '*', '--resolve', 'azmednsk.ru:443:127.0.0.1', '--connect-timeout', '5', '--max-time', '20', '-fsS', 'https://azmednsk.ru/' + NAME, '-o', str(body)], check=True)
            assert body.read_bytes() == PAGE, 'Ответ HTTPS отличается'
        assert home.read_bytes() == before, 'Главная изменилась параллельно'
    except BaseException:
        if created and target.exists() and target.read_bytes() == PAGE:
            target.unlink()
        raise
    print('MOBILE_LAYOUT_CHECK=READY; HOME=UNCHANGED; ASSETS=UNCHANGED')
    print('URL=https://azmednsk.ru/' + NAME)
    print('Следующий шаг: пришлите вывод — Codex проверит мобильную вёрстку самостоятельно.')

if __name__ == '__main__':
    main()
