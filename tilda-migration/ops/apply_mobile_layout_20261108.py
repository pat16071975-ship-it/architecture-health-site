#!/usr/bin/env python3
"""AZ: two scoped layout corrections; content, booking and analytics unchanged."""
from pathlib import Path
import os,sys,socket,re,datetime,tempfile,shutil,json,hashlib
ROOT=Path('/var/www/azmednsk-preview')
MARK='az-mobile-layout-20261108'
CSS={'chto-my-lechim/protezirovanie-zubov/index.html': '#dentists .az-dental-head{min-width:0;max-width:100%}\n#dentists .az-dental-head h3{min-width:0;overflow-wrap:anywhere}\n#dentists .az-dental-head h3 span{display:block;overflow-wrap:anywhere}', 'otvety-na-voprosy/index.html': '@media(max-width:760px){\n#faq-page .faq-direction{max-width:100%;gap:12px;flex-wrap:wrap}\n#faq-page .faq-direction>span:first-child{min-width:0;overflow-wrap:anywhere}\n#faq-page .faq-direction .count{flex:0 0 auto;white-space:nowrap}\n#faq-page .faq-panel>summary{flex-wrap:wrap;gap:8px 15px}\n#faq-page .faq-panel>summary h2{min-width:0;max-width:100%;overflow-wrap:anywhere}\n#faq-page .faq-section-count{flex:0 0 auto;white-space:nowrap}\n}'}
EXPECTED={'chto-my-lechim/protezirovanie-zubov/index.html': 'c896786a4c8442efd46146148df597bd384ea5b4b812d4deeb2c0671ef680a9b', 'otvety-na-voprosy/index.html': '8c975947c032fe3c23be76e95a8bd8dbb6b44b19092e6249ef70d2d5de9e1420'}

def prepare(root):
 changes={};existing=0
 for name,css in CSS.items():
  p=root/name
  assert p.is_file(), 'STOP: missing '+name
  assert not any(x.is_symlink() for x in [p,*p.parents]), 'STOP: symlink'
  raw=p.read_bytes();s=raw.decode('utf-8')
  block='\n<style id="'+MARK+'">\n'+css+'\n</style>\n'
  original=s
  if MARK in s:
   assert s.count(block)==1,'STOP: changed correction block'
   original=s.replace(block,'',1)
  assert hashlib.sha256(original.encode()).hexdigest()==EXPECTED[name], 'STOP: page changed since review: '+name
  if MARK in s:
   existing+=1;continue
  assert s.count('</head>')==1,'STOP: invalid head'
  new=s.replace('</head>',block+'</head>',1).encode()
  assert new.replace(block.encode(),b'',1)==raw
  changes[Path(name)]=(raw,new)
 return changes,existing

def atomic(p,data,mode):
 fd,tmp=tempfile.mkstemp(prefix='.az-mobile-',dir=p.parent)
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
 changes,existing=prepare(root)
 print(f'PREFLIGHT=PASS; PAGES_TO_UPDATE={len(changes)}; ALREADY_PRESENT={existing}')
 if check:print('LIVE_WRITES=NONE');return
 if not changes:print('MOBILE_LAYOUT=ALREADY_INSTALLED');return
 backup=Path('/var/backups/azmednsk')/('mobile-layout-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
 backup.mkdir(parents=True,mode=0o700)
 modes={};done=[]
 for rel,(old,new) in changes.items():
  p=root/rel;assert p.read_bytes()==old,'STOP: concurrent modification'
  dest=backup/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest);modes[rel]=p.stat().st_mode&0o777
 manifest={str(r):{'before':hashlib.sha256(a).hexdigest(),'after':hashlib.sha256(b).hexdigest()} for r,(a,b) in changes.items()}
 (backup/'manifest.json').write_text(json.dumps(manifest,indent=2))
 try:
  for rel,(old,new) in changes.items():
   assert (root/rel).read_bytes()==old,'STOP: concurrent modification'
   done.append(rel);atomic(root/rel,new,modes[rel])
  for rel,(_,new) in changes.items():assert (root/rel).read_bytes()==new
 except BaseException:
  for rel in reversed(done):atomic(root/rel,changes[rel][0],modes[rel])
  print('ROLLBACK=PASS');raise
 print(f'MOBILE_LAYOUT=PASS; PAGES_UPDATED={len(changes)}; CONTENT_AND_BOOKING=UNCHANGED')
 print('BACKUP='+str(backup));print('URL=https://azmednsk.ru/chto-my-lechim/protezirovanie-zubov/');print('URL=https://azmednsk.ru/otvety-na-voprosy/')
 print('External mobile verification required after installation.')
if __name__=='__main__':main()
