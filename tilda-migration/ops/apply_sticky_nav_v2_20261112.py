#!/usr/bin/env python3
"""AZ: install shared desktop scroll navigation with backups and rollback."""
from pathlib import Path
import os,sys,socket,re,datetime,tempfile,shutil,json,hashlib,fcntl
ROOT=Path('/var/www/azmednsk-preview')
ASSETS={'assets/az-sticky-nav-20261112-v2.css': '/* Native sticky retains the real menu, its links, dimensions and both rules. */\n@media(min-width:960px){\n  nav.az-original-sticky-nav{position:sticky!important;top:0;z-index:40;background-color:#fff;}\n  html{scroll-padding-top:64px}\n}\n#az-home-sticky-nav[hidden]{display:none!important}\n#az-home-sticky-nav{box-sizing:border-box;position:fixed;top:0;left:0;width:100%;z-index:40;display:flex;justify-content:center;align-items:center;gap:28px;padding:10px;margin:0;border-top:1px solid #dce8f2;border-bottom:1px solid #dce8f2;border-left:0;border-right:0;border-radius:0;background:#fff;box-shadow:none;font-family:Montserrat,Arial,sans-serif;font-size:15px;line-height:normal;}\n#az-home-sticky-nav a{margin:0;padding:0;border:0;border-radius:0;background:transparent;color:#476573;font:inherit;text-decoration:none;white-space:nowrap;}\n#az-home-sticky-nav a:hover{text-decoration:underline}\n#az-home-sticky-nav a:focus-visible{outline:2px solid #2080c4;outline-offset:3px}\n@media(max-width:959px){#az-home-sticky-nav{display:none!important}}\n', 'assets/az-sticky-nav-20261112-v2.js': "/* Keep the original inner-page navigation; home uses the same ruled strip. */\n(() => {\n  'use strict';\n  const home = document.querySelector('.azh-home .hero img') || document.querySelector('.azh-home .hero');\n  const original = [...document.querySelectorAll('nav')].find(n => [...n.querySelectorAll('a')].some(a => a.textContent.trim() === 'Направления'));\n  if (!home) {\n    if (original) original.classList.add('az-original-sticky-nav');\n    return;\n  }\n  if (document.getElementById('az-home-sticky-nav')) return;\n  const nav = document.createElement('nav');\n  nav.id = 'az-home-sticky-nav';\n  nav.setAttribute('aria-label', 'Основная навигация');\n  nav.hidden = true;\n  [['Главная','/'],['Направления','/uslygi/'],['Команда','/team/'],['О клинике','/o_nas/'],['Что мы лечим','/chto-my-lechim/'],['Контакты','/contacts/'],['Родителям','/detyam/']].forEach(([title,href]) => {\n    const a=document.createElement('a'); a.textContent=title; a.href=href; nav.appendChild(a);\n  });\n  document.body.appendChild(nav);\n  const desktop=matchMedia('(min-width:960px)');\n  let pending=false;\n  function update(){pending=false;nav.hidden=!(desktop.matches && home.getBoundingClientRect().bottom<=0);}\n  function schedule(){if(!pending){pending=true;requestAnimationFrame(update);}}\n  addEventListener('scroll',schedule,{passive:true});\n  addEventListener('resize',schedule,{passive:true});\n  addEventListener('pageshow',schedule);\n  addEventListener('load',schedule,{once:true});\n  if(desktop.addEventListener)desktop.addEventListener('change',schedule);\n  if('ResizeObserver' in window)new ResizeObserver(schedule).observe(home);\n  update();\n})();\n"}
OLD_BLOCK='\n<!-- AZ desktop sticky navigation -->\n<link rel="stylesheet" href="/assets/az-sticky-nav-20261112.css">\n<script defer src="/assets/az-sticky-nav-20261112.js"></script>\n'
BLOCK='\n<!-- AZ desktop sticky navigation -->\n<link rel="stylesheet" href="/assets/az-sticky-nav-20261112-v2.css">\n<script defer src="/assets/az-sticky-nav-20261112-v2.js"></script>\n'
def prepare(root):
 changes={};existing=0
 for p in sorted(root.rglob('*.html')):
  raw=p.read_bytes()
  if b'/site.js' not in raw:continue
  if any(x.is_symlink() for x in [p,*p.parents]):raise RuntimeError('STOP: symlink '+str(p))
  s=raw.decode('utf-8')
  if 'az-sticky-nav-20261112-v2' in s:
   assert s.count(BLOCK)==1 and s.index(BLOCK)<s.lower().index('</head'), 'STOP: existing navigation differs: '+str(p)
   existing+=1;continue
  assert s.count(OLD_BLOCK)==1, 'STOP: expected installed v1 block: '+str(p)
  s=s.replace(OLD_BLOCK,'',1)
  matches=list(re.finditer(r'</head\s*>',s,re.I))
  assert len(matches)==1,'STOP: invalid HTML '+str(p)
  pos=matches[0].start();new=(s[:pos]+BLOCK+s[pos:]).encode()
  assert new.replace(BLOCK.encode(),b'',1)==raw.replace(OLD_BLOCK.encode(),b'',1)
  changes[p.relative_to(root)]=(raw,new)
 assert changes or existing,'STOP: no site pages'
 pages=len(changes)
 for rel,content in ASSETS.items():
  p=root/rel
  assert not any(x.is_symlink() for x in [p,*p.parents]), 'STOP: symlink asset'
  old=p.read_bytes() if p.exists() else None
  new=content.encode()
  if old!=new:
   assert old is None, 'STOP: conflicting navigation asset '+str(p)
   changes[Path(rel)]=(old,new)
 return changes,existing,pages

def atomic(p,data,mode):
 fd,tmp=tempfile.mkstemp(prefix='.az-nav-',dir=p.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chmod(tmp,mode);os.replace(tmp,p)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def main():
 check=len(sys.argv)==3 and sys.argv[1]=='--check-root'
 root=Path(sys.argv[2]) if check else ROOT
 if not check:
  assert len(sys.argv)==1 and socket.gethostname()=='vdska' and os.geteuid()==0,'STOP: root on vdska required'
  conf=Path('/etc/nginx/sites-available/azmednsk-preview').read_text()
  assert re.search(r'server_name\s+[^;]*\bazmednsk\.ru\b',conf),'STOP: wrong nginx server'
  assert set(re.findall(r'^\s*root\s+([^;]+);',conf,re.M))=={str(ROOT)},'STOP: wrong document root'
 changes,existing,pages=prepare(root)
 print(f'PREFLIGHT=PASS; PAGES_TO_UPDATE={pages}; ALREADY_PRESENT={existing}')
 if check:print('LIVE_WRITES=NONE');return
 if not changes:print('STICKY_NAV_V2=ALREADY_INSTALLED');return
 lock=open('/var/lock/az-sticky-nav.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 backup=Path('/var/backups/azmednsk')/('sticky-nav-v2-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
 backup.mkdir(parents=True,mode=0o700)
 modes={};done=[]
 for rel,(old,new) in changes.items():
  p=root/rel;assert (p.read_bytes() if p.exists() else None)==old,'STOP: concurrent modification'
  dest=backup/rel;dest.parent.mkdir(parents=True,exist_ok=True);modes[rel]=(p.stat().st_mode&0o777) if p.exists() else 0o644
  if old is not None:shutil.copy2(p,dest)
 manifest={str(r):{'before':hashlib.sha256(a).hexdigest() if a is not None else None,'after':hashlib.sha256(b).hexdigest()} for r,(a,b) in changes.items()}
 (backup/'manifest.json').write_text(json.dumps(manifest,indent=2))
 try:
  for rel,(old,new) in sorted(changes.items(),key=lambda item: item[0].suffix == '.html'):
   assert ((root/rel).read_bytes() if (root/rel).exists() else None)==old,'STOP: concurrent modification'
   (root/rel).parent.mkdir(parents=True,exist_ok=True)
   done.append(rel);atomic(root/rel,new,modes[rel])
  for rel,(_,new) in changes.items():assert (root/rel).read_bytes()==new
 except BaseException:
  for rel in reversed(done):
   if changes[rel][0] is None:(root/rel).unlink(missing_ok=True)
   else:atomic(root/rel,changes[rel][0],modes[rel])
  print('ROLLBACK=PASS');raise
 standard=Path('/var/lib/azmednsk/standards');standard.mkdir(parents=True,exist_ok=True)
 (standard/'sticky-nav-v2-20261112.txt').write_text('Every public HTML page must include the desktop navigation CSS and JS exactly once inside head. Preserve on future page generation. Desktop >=960px: on home show after hero photo leaves viewport; elsewhere use the original navigation with native position:sticky at top:0; preserve original lines, spacing and links. Keep original menus, booking and analytics unchanged.\n'+BLOCK)
 print(f'STICKY_NAV_V2=PASS; PAGES_UPDATED={pages}; STANDARD=SAVED')
 print('BACKUP='+str(backup));print('URL=https://azmednsk.ru/')
 print('External HTTPS verification required after installation.')
if __name__=='__main__':main()
