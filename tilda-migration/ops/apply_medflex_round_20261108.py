#!/usr/bin/env python3
"""AZ: restore the owner's exact Medflex round widget; no counters changed."""
from pathlib import Path
import os,sys,socket,re,datetime,tempfile,shutil,json,hashlib
ROOT=Path('/var/www/azmednsk-preview')
BOOK='https://booking.medflex.ru/?user=e0c452bf7c5bc897e8b8c0782bcb16cb&source=3'
EMBED='<div id="medflexRoundWidgetData" data-src="https://booking.medflex.ru/?user=e0c452bf7c5bc897e8b8c0782bcb16cb&isRoundWidget=true"></div> <script defer src="https://booking.medflex.ru/components/round/round_widget_button.js" charset="utf-8"></script>'
MARK='az-medflex-round-20261108'
BLOCK='\n<!-- '+MARK+' -->\n'+EMBED+'\n<!-- /'+MARK+' -->\n'
def prepare(root):
 changes={};existing=0
 for p in sorted(root.rglob('*.html')):
  raw=p.read_bytes()
  if b'/site.js' not in raw:continue
  if any(x.is_symlink() for x in [p,*p.parents]):raise RuntimeError('STOP: symlink '+str(p))
  s=raw.decode('utf-8')
  if MARK in s:
   assert s.count(BLOCK)==1 and s.count('id="medflexRoundWidgetData"')==1,'STOP: altered installation'
   existing+=1;continue
  assert 'medflexRoundWidgetData' not in s and 'round_widget_button.js' not in s,'STOP: existing widget needs review: '+str(p)
  matches=list(re.finditer(r'</body\s*>',s,re.I))
  assert len(matches)==1,'STOP: invalid HTML '+str(p)
  pos=matches[0].start();new=(s[:pos]+BLOCK+s[pos:]).encode()
  assert new.replace(BLOCK.encode(),b'',1)==raw
  changes[p.relative_to(root)]=(raw,new)
 assert changes or existing,'STOP: no site pages'
 return changes,existing

def atomic(p,data,mode):
 fd,tmp=tempfile.mkstemp(prefix='.az-medflex-',dir=p.parent)
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
 if not changes:print('MEDFLEX_ROUND=ALREADY_INSTALLED');return
 backup=Path('/var/backups/azmednsk')/('medflex-round-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
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
 standard=Path('/var/lib/azmednsk/standards');standard.mkdir(parents=True,exist_ok=True)
 (standard/'medflex-round-20261108.txt').write_text('All public site pages must preserve this exact round widget once, before </body>.\n'+EMBED+'\nAll-doctors link: '+BOOK+'\nRound counter: 113916673. All-doctors counter: 113892774. Do not create duplicate counters.\nDo not replace individual doctor links. Include widget in future page templates.\n')
 print(f'MEDFLEX_ROUND=PASS; PAGES_UPDATED={len(changes)}; STANDARD=SAVED')
 print('BACKUP='+str(backup));print('URL=https://azmednsk.ru/')
 print('Browser appearance and opening require external verification.')
if __name__=='__main__':main()
