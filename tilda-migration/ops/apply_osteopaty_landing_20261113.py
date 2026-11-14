#!/usr/bin/env python3
import re,os,socket,sys,hashlib,urllib.request,datetime,json,shutil
from pathlib import Path
ROOT=Path('/var/www/azmednsk-preview')
ASSET='assets/osteopaty-collage-20261113.png'
SHA='7ce58c244f1de1f334df83994d112d66de8b25a9b97bd735c25303d4870db809'
URL='https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/0b39a55a2b7c0c6354d44cb2c357e7755288eb4c/tilda-migration/public/'+ASSET
MARK='az-osteopaty-landing-20261113'
def render(s):
 assert '<main>' in s and '113776005' in s and 'Старостенко' in s and 'Димитренко' in s,'Unexpected source page'
 s=s.replace('https://azmednsk.ru/uslugi/osteopatiya','https://azmednsk.ru/osteopaty')
 s=s.replace('Остеопатия в Новосибирске | Архитектура здоровья','Остеопаты Старостенко и Димитренко — Новосибирск | Архитектура здоровья')
 s=s.replace('<h1>Остеопатия в Новосибирске</h1>','<h1>Остеопатия в «Архитектуре здоровья»</h1>')
 pattern=r'<section id="layout-2788165601".*?</section>'
 old=re.search(pattern,s,re.S)
 assert old,'Intro block not found'
 intro='<section class="az-osteopaty-hero" aria-label="Наши остеопаты"><img src="/'+ASSET+'" width="2048" height="682" alt="Анна Димитренко и Вадим Старостенко — специалисты клиники Архитектура здоровья" fetchpriority="high"><div class="container content-section"><p>Оценка движения, подвижности тканей и индивидуальная работа с телом. Врачи-остеопаты Вадим Старостенко и Анна Димитренко принимают в Новосибирске.</p><a class="button" href="#az-direction-doctors">Выбрать врача и записаться</a></div></section>'
 s=s[:old.start()]+intro+s[old.end():]
 s=s.replace('href="#booking"', 'href="#az-direction-doctors"')
 # Keep the established clinical copy and shared site chrome from the source page.
 s=s.replace('</head>','<style id="'+MARK+'">.az-osteopaty-hero{margin:0 auto;max-width:1800px}.az-osteopaty-hero>img{display:block;width:100%;height:auto;object-fit:contain}.az-osteopaty-hero .content-section{text-align:center}#az-direction-doctors{scroll-margin-top:90px}@media(max-width:640px){main h1{font-size:28px;overflow-wrap:break-word}.az-osteopaty-hero .content-section{padding-top:20px}}</style></head>')
 for slug,employee in [('starostenko-vadim','202272'),('dimitrenko-anna','688767')]:
  pat=r'<article class="az-service-doctor" aria-labelledby="az-doctor-'+slug+r'-name">.*?</article>'
  m=re.search(pat,s,re.S);assert m,'Missing doctor '+slug
  block=m[0].replace('user=e0c452bf7c5bc897e8b8c0782bcb16cb"','user=e0c452bf7c5bc897e8b8c0782bcb16cb&amp;employeeId='+employee+'&amp;source=4"')
  block=re.sub(r'Откроется список Medflex\. Выберите специалиста: .*?\. Если нет', 'Откроется запись к этому специалисту в Medflex. Если нет',block)
  s=s[:m.start()]+block+s[m.end():]
 assert s.count(MARK)==1 and 'employeeId=202272' in s and 'employeeId=688767' in s
 return s

def main():
 dry=len(sys.argv)==3 and sys.argv[1]=='--check'
 assert dry or sys.argv[1:]==['--apply'],'Usage: --check ROOT or --apply'
 root=Path(sys.argv[2]) if dry else ROOT
 if not dry:assert os.geteuid()==0 and socket.gethostname().split('.')[0]=='vdska','Run as root on vdska'
 assert root.is_dir() and not root.is_symlink(),'Wrong root'
 source=root/'uslugi/osteopatiya/index.html';out=root/'osteopaty/index.html';asset=root/ASSET
 source_bytes=source.read_bytes()
 assert hashlib.sha256(source_bytes).hexdigest()=='38f81770605a43d4089da4facc2b7fc6a75834f3ec1987e8198144c1bf7b9d74','Source page changed; review before installation'
 html=render(source_bytes.decode())
 assert not out.exists() or MARK in out.read_text(),'Existing unrelated landing; no changes'
 for p in [out,asset]:
  assert not any(x.is_symlink() for x in [p,*p.parents]),'Symlink target'
 if dry:print('PREFLIGHT=PASS; LANDING=1; DOCTORS=2; SCHEDULE=DEFERRED');return
 data=urllib.request.urlopen(URL,timeout=90).read();assert hashlib.sha256(data).hexdigest()==SHA,'Asset hash mismatch'
 files={asset:data,out:html.encode()}
 changed={p:b for p,b in files.items() if not p.exists() or p.read_bytes()!=b}
 if not changed:print('OSTEOPATY_LANDING=ALREADY_APPLIED');return
 backup=Path('/var/backups/azmednsk')/('osteopaty-landing-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True)
 prior={p:p.read_bytes() if p.exists() else None for p in changed}
 for p,b in prior.items():
  if b is not None:
   q=backup/p.relative_to(root);q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(b)
 (backup/'manifest.json').write_text(json.dumps({str(p.relative_to(root)):b is not None for p,b in prior.items()}))
 try:
  for p,b in changed.items():
   p.parent.mkdir(parents=True,exist_ok=True);temp=p.with_name(p.name+'.aztmp');temp.write_bytes(b);temp.chmod(0o644);os.replace(temp,p)
  assert out.read_text()==html and hashlib.sha256(asset.read_bytes()).hexdigest()==SHA
 except Exception:
  for p,b in prior.items():
   if b is None:p.unlink(missing_ok=True)
   else:p.write_bytes(b)
  raise
 print('OSTEOPATY_LANDING=PASS; DOCTORS=2; COLLAGE=PASS; SCHEDULE=DEFERRED')
 print('BACKUP='+str(backup));print('URL=https://azmednsk.ru/osteopaty/')
if __name__=='__main__':
 try:main()
 except Exception as e:print('STOP='+str(e));sys.exit(1)
