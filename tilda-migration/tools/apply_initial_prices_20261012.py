#!/usr/bin/env python3
"""Only the five owner-approved initial-appointment prices. Python stdlib only."""
import argparse, hashlib, html, json, os, re, shutil, socket, subprocess, sys, tempfile
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path('/var/www/azmednsk-test/tilda-migration/public')
BACKUPS=Path('/var/backups/azmednsk')

def sha(data): return hashlib.sha256(data).hexdigest()
def require(ok,message):
    if not ok: raise RuntimeError(message)
def visible(text):
    text=re.sub(r'<(?:script|style)\b.*?</(?:script|style)>','',text,flags=re.S|re.I)
    return ' '.join(html.unescape(re.sub('<[^>]*>',' ',text)).split())
def read_package():
    return {'version': 1, 'approved_date': '2026-10-12', 'scope': 'Initial appointment only, five specialists; no discounts or promotions', 'pages': {'team/pavlova-anna/index.html': {'display_name': 'Павлова Алла Викторовна', 'price': '25 000 ₽', 'before': 'Первичный приём:<br>2 000 ₽, акция — 1 000 ₽', 'after': 'Первичный приём:<br>25&nbsp;000&nbsp;₽', 'before_sha256': 'f8aa1178fac19611594f0eb18272559af0291a8fbb0088eb244a733fe7a507d2', 'after_sha256': 'b88bdb4907299d0cdf12afb8820bf765413cb29bba878b19c7d0c70779fa1947'}, 'team/zubachev-roman/index.html': {'display_name': 'Зубачев Роман Николаевич', 'price': 'от 12 000 ₽', 'before': 'Приём от:<strong style="color:rgb(32, 128, 196)"> 10 000₽</strong>', 'after': 'Первичный приём:<br><strong style="color:rgb(32, 128, 196)">от&nbsp;12&nbsp;000&nbsp;₽</strong>', 'before_sha256': '26f494e8c760407c228b14339ace0fe20b98e61073ccdfdaa18a3dff5c61c6c8', 'after_sha256': '0a88eb2c10ae71720643e26a5e6b28983652fbd805bc078cd95c58a2d0c27b24'}, 'team/starostenko-vadim/index.html': {'display_name': 'Старостенко Вадим Анатольевич', 'price': '7 500 ₽', 'before': 'Приём от:<strong style="color:rgb(32, 128, 196)"> 7 000₽</strong>', 'after': 'Первичный приём:<br><strong style="color:rgb(32, 128, 196)">7&nbsp;500&nbsp;₽</strong>', 'before_sha256': 'e56de20a6f50c0d5f718b50538edc0a2e9a2c2785f061cf8829de3509fdf07e4', 'after_sha256': '4b88831bfff71bfab1302fd39fbbfdb5b7db709f2d4b00d0aa7961543eb6f697'}, 'team/dimitrenko-anna/index.html': {'display_name': 'Димитренко Анна Яковлевна', 'price': '6 000 ₽', 'before': 'Приём от:<strong style="color:rgb(32, 128, 196)"> 5 500₽</strong>', 'after': 'Первичный приём:<br><strong style="color:rgb(32, 128, 196)">6&nbsp;000&nbsp;₽</strong>', 'before_sha256': 'dff6185a23d80c8bc3d60044f439e29d9c5b372e91f0367c9d7d524e5f41552b', 'after_sha256': '14d727859edc8bb8af412a78b657cba5d83887107e836364fdac053ff566f59d'}, 'team/mamontova-lubava/index.html': {'display_name': 'Мамонтова Любава Геннадьевна', 'price': '4 500 ₽', 'before': 'Первичный приём:<br>2 000 ₽, акция — 1 000 ₽', 'after': 'Первичный приём:<br>4&nbsp;500&nbsp;₽', 'before_sha256': '3f8fef1f689e9828fbd1614c2bafc5a479579985294af5db1674b032e6029563', 'after_sha256': '2f747bd80dcc2cb4a95b25261cb2003a6309d03d4760078fe6187829e8940351'}}}
def no_promos(text):
    return not re.search(r'\b(?:скидк\w*|акци[яиюйе]\w*|бесплат\w*)\b',visible(text),re.I)
def preflight(root,manifest):
    result={}
    for name,m in manifest['pages'].items():
        p=root/name
        require(not p.is_symlink() and p.is_file(),f'Нет обычного файла: {name}')
        require(p.resolve().is_relative_to(root.resolve()),f'Путь вне сайта: {name}')
        old=p.read_bytes(); require(sha(old) in (m['before_sha256'],m['after_sha256']),f'Страница изменилась после подготовки пакета: {name}. Ничего не применено.')
        if sha(old)==m['after_sha256']: new=old
        else:
            text=old.decode('utf-8');require(text.count(m['before'])==1,f'Неоднозначная цена: {name}')
            new=text.replace(m['before'],m['after'],1).encode('utf-8')
        require(sha(new)==m['after_sha256'],f'Не совпала контрольная сумма результата: {name}')
        require(no_promos(new.decode('utf-8')),f'Осталась акция на странице: {name}')
        result[name]=(old,new,p.stat())
    # Detect extra priced mentions that were not present in the audited public HTML.
    names=r'\b(?:Павлова\s+Алла|Павловой\s+А\.?\s*В|Зубач[её]в\w*|Старостенко|Димитренко|Мамонтов[аыуе]\b)'
    for p in root.rglob('*.html'):
        if p.is_symlink(): continue
        rel=p.relative_to(root).as_posix()
        if rel in result: continue
        text=visible(p.read_text(encoding='utf-8'))
        if re.search(names,text,re.I) and re.search(r'₽|\bруб(?:лей|ля|ль)?\b|\bскидк\w*|\bакци[яиюйе]\w*|\bбесплат\w*',text,re.I):
            require(False,f'Найдено дополнительное упоминание с ценой/акцией: {rel}. Требуется отдельная проверка; ничего не применено.')
    return result

def atomic(path,data,stat,restore_times=False):
    fd,tmp=tempfile.mkstemp(prefix='.az-price-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as out: out.write(data);out.flush();os.fsync(out.fileno())
        os.chmod(tmp,stat.st_mode & 0o7777);os.chown(tmp,stat.st_uid,stat.st_gid)
        if restore_times: os.utime(tmp,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def check_https(name,expected):
    url='https://azmednsk.ru/'+name.removesuffix('index.html')
    r=subprocess.run(['curl','--noproxy','*','--resolve','azmednsk.ru:443:127.0.0.1','--connect-timeout','5','--max-time','20','-fsS','-H','Cache-Control: no-cache',url],capture_output=True)
    require(r.returncode==0,f'HTTPS не прошёл: {url}')
    require(sha(r.stdout)==sha(expected),f'HTTPS отдаёт другое содержимое: {url}')

def apply(root,backup_base,manifest,verify=check_https):
    prepared=preflight(root,manifest)
    changes={n:v for n,v in prepared.items() if v[0]!=v[1]}
    if not changes:
        for name,(_,new,_) in prepared.items():verify(name,new)
        print('УЖЕ УСТАНОВЛЕНО: цены пяти специалистов. HTTPS=PASS');return
    backup=backup_base/('initial-prices-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True,mode=0o700)
    for name,(old,new,st) in prepared.items():
        dst=backup/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/name,dst)
        require(dst.read_bytes()==old,f'Не проверена резервная копия: {name}')
    (backup/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    # Recheck before entering write/rollback phase; do not overwrite concurrent edits.
    for name,(old,_,_) in prepared.items():require((root/name).read_bytes()==old,f'Параллельное изменение: {name}')
    written=[]
    try:
        for name,(_,new,st) in changes.items():
            atomic(root/name,new,st);written.append(name)
        for name,(_,new,_) in prepared.items():
            require((root/name).read_bytes()==new,f'Не записан файл: {name}');verify(name,new)
    except BaseException:
        failures=[]
        for name in written:
            old,_,st=changes[name]
            try: atomic(root/name,old,st,True);require((root/name).read_bytes()==old,'Контрольная сумма отката не совпала')
            except Exception as e:failures.append(f'{name}: {e}')
        print('Резервная копия:',backup)
        print('ОТКАТ ФАЙЛОВ: '+('PASS' if not failures else 'FAIL: '+'; '.join(failures)))
        raise
    print('ГОТОВО: цены пяти специалистов установлены без скидок и акций; HTTPS=PASS')
    for name,m in manifest['pages'].items():print(m['display_name']+' — '+m['price'])
    print('Резервная копия:',backup)
    print('Другие критические исправления этим пакетом не выполнялись.')
    print('Следующий шаг: пришлите этот вывод для внешней проверки сайта.')

def main():
    parser=argparse.ArgumentParser(description='Цены первичного приёма: 5 специалистов, без акций')
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check',action='store_true',help='Только проверка, без записи')
    mode.add_argument('--apply',action='store_true',help='Применить утверждённые пять цен')
    args=parser.parse_args()
    require(socket.gethostname()=='az-server','Запускать только на az-server')
    require(os.geteuid()==0,'Запустите через sudo python3')
    require(ROOT.is_dir(),'Не найден корень сайта')
    manifest=read_package()
    if args.check:
        prepared=preflight(ROOT,manifest);print('PREFLIGHT=PASS; изменений нет; страниц:',len(prepared))
    else:apply(ROOT,BACKUPS,manifest)
if __name__=='__main__':
    try:main()
    except Exception as e:print('СТОП:',e,file=sys.stderr);sys.exit(1)
