#!/usr/bin/env python3
"""AZ: install shared desktop scroll navigation with backups and rollback."""
from pathlib import Path
import os,sys,socket,re,datetime,tempfile,shutil,json,hashlib,fcntl
ROOT=Path('/var/www/azmednsk-preview')
ASSETS={'assets/az-sticky-nav-20261112.css': '/* Isolated from the legacy home, doctor and service navigation styles. */\n#az-sticky-navigation,#az-sticky-navigation *{box-sizing:border-box}\n#az-sticky-navigation[hidden]{display:none!important}\n#az-sticky-navigation{position:fixed;inset:0 0 auto;z-index:40;display:block;margin:0;padding:0;width:100%;max-width:none;height:auto;min-height:0;border:0;border-bottom:1px solid #dce8f2;border-radius:0;background:#fff;box-shadow:0 4px 16px rgb(32 80 112 / 8%);font-family:Montserrat,Arial,sans-serif;animation:az-nav-appear .16s ease-out}\n#az-sticky-navigation .az-sticky-inner{display:flex;align-items:center;justify-content:center;gap:4px;width:min(1160px,100%);margin:0 auto;padding:5px 20px;min-height:52px}\n#az-sticky-navigation a{display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto;min-height:40px;margin:0;padding:9px 13px;border:0;border-radius:5px;background:transparent;color:#426b86;font-size:14px;font-weight:400;line-height:22px;letter-spacing:normal;text-decoration:none;white-space:nowrap;transition:background-color .15s,color .15s}\n#az-sticky-navigation a:hover{background:#f0f7fc;color:#155b91}\n#az-sticky-navigation a[aria-current=page],#az-sticky-navigation a.az-section-active{background:#edf6fc;color:#155b91}\n#az-sticky-navigation a:focus-visible{outline:2px solid #2080c4;outline-offset:-2px;background:#edf6fc}\n@media(min-width:960px){html.az-sticky-nav-visible{scroll-padding-top:68px}}\n@media(max-width:959px){#az-sticky-navigation{display:none!important}}\n@media(prefers-reduced-motion:reduce){#az-sticky-navigation{animation:none}#az-sticky-navigation a{transition:none}}\n@keyframes az-nav-appear{from{opacity:0}to{opacity:1}}\n', 'assets/az-sticky-nav-20261112.js': "/* AZ desktop navigation, shared by all public page templates. */\n(() => {\n  'use strict';\n  if (document.getElementById('az-sticky-navigation')) return;\n  const home = document.querySelector('.azh-home .hero img') || document.querySelector('.azh-home .hero');\n  const original = home || Array.from(document.querySelectorAll('nav')).find(nav =>\n    Array.from(nav.querySelectorAll('a')).some(a => a.textContent.trim() === 'Направления')) || document.querySelector('header');\n  const links = [['Главная', '/'], ['Направления', '/uslygi/'], ['Команда', '/team/'],\n    ['О клинике', '/o_nas/'], ['Что мы лечим', '/chto-my-lechim/'],\n    ['Контакты', '/contacts/'], ['Родителям', '/detyam/']];\n  const nav = document.createElement('nav');\n  nav.id = 'az-sticky-navigation';\n  nav.setAttribute('aria-label', 'Основная навигация при прокрутке');\n  nav.hidden = true;\n  const inner = document.createElement('div');\n  inner.className = 'az-sticky-inner';\n  const path = location.pathname.replace(/\\/+$/, '') || '/';\n  links.forEach(([label, href]) => {\n    const a = document.createElement('a');\n    a.href = href;\n    a.textContent = label;\n    const base = href.replace(/\\/+$/, '') || '/';\n    if (path === base) a.setAttribute('aria-current', 'page');\n    else if ((base !== '/' && path.startsWith(base + '/')) ||\n      (base === '/uslygi' && path.startsWith('/uslugi/'))) a.className = 'az-section-active';\n    inner.appendChild(a);\n  });\n  nav.appendChild(inner);\n  document.body.appendChild(nav);\n  const desktop = window.matchMedia('(min-width: 960px)');\n  let pending = false;\n  function update() {\n    pending = false;\n    const show = desktop.matches && (original ? original.getBoundingClientRect().bottom <= 0 : window.scrollY > 180);\n    nav.hidden = !show;\n    document.documentElement.classList.toggle('az-sticky-nav-visible', show);\n  }\n  function schedule() {\n    if (!pending) { pending = true; requestAnimationFrame(update); }\n  }\n  window.addEventListener('scroll', schedule, { passive: true });\n  window.addEventListener('resize', schedule, { passive: true });\n  window.addEventListener('pageshow', schedule);\n  window.addEventListener('load', schedule, { once: true });\n  if (desktop.addEventListener) desktop.addEventListener('change', schedule);\n  if (original && 'ResizeObserver' in window) new ResizeObserver(schedule).observe(original);\n  update();\n})();\n"}
BLOCK='\n<!-- AZ desktop sticky navigation -->\n<link rel="stylesheet" href="/assets/az-sticky-nav-20261112.css">\n<script defer src="/assets/az-sticky-nav-20261112.js"></script>\n'
def prepare(root):
 changes={};existing=0
 for p in sorted(root.rglob('*.html')):
  raw=p.read_bytes()
  if b'/site.js' not in raw:continue
  if any(x.is_symlink() for x in [p,*p.parents]):raise RuntimeError('STOP: symlink '+str(p))
  s=raw.decode('utf-8')
  if 'az-sticky-nav-20261112' in s:
   assert s.count(BLOCK)==1 and s.index(BLOCK)<s.lower().index('</head'), 'STOP: existing navigation differs: '+str(p)
   existing+=1;continue
  matches=list(re.finditer(r'</head\s*>',s,re.I))
  assert len(matches)==1,'STOP: invalid HTML '+str(p)
  pos=matches[0].start();new=(s[:pos]+BLOCK+s[pos:]).encode()
  assert new.replace(BLOCK.encode(),b'',1)==raw
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
 if not changes:print('STICKY_NAV=ALREADY_INSTALLED');return
 lock=open('/var/lock/az-sticky-nav.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 backup=Path('/var/backups/azmednsk')/('sticky-nav-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
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
 (standard/'sticky-nav-20261112.txt').write_text('Every public HTML page must include the desktop navigation CSS and JS exactly once inside head. Preserve on future page generation. Desktop >=960px: on home show after hero photo leaves viewport; elsewhere after original navigation leaves viewport. Keep original menus, booking and analytics unchanged.\n'+BLOCK)
 print(f'STICKY_NAV=PASS; PAGES_UPDATED={pages}; STANDARD=SAVED')
 print('BACKUP='+str(backup));print('URL=https://azmednsk.ru/')
 print('External HTTPS verification required after installation.')
if __name__=='__main__':main()
