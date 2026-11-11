#!/usr/bin/env python3
"""AZ: install the owner-provided UIS script inside head."""
from pathlib import Path
import os,sys,socket,re,datetime,tempfile,shutil,json,hashlib
ROOT=Path('/var/www/azmednsk-preview')
EMBED='<script type="text/javascript" async src="https://app.uiscom.ru/static/cs.min.js?k=W6B0HUCUVgIuXfyKAXTsbHnqEslvRH09"></script>'
MARK='UIS'
BLOCK='\n<!-- UIS -->\n    '+EMBED+'\n<!-- UIS -->\n'
def prepare(root):
 changes={};existing=0
 for p in sorted(root.rglob('*.html')):
  raw=p.read_bytes()
  if b'/site.js' not in raw:continue
  if any(x.is_symlink() for x in [p,*p.parents]):raise RuntimeError('STOP: symlink '+str(p))
  s=raw.decode('utf-8')
  if 'app.uiscom.ru/static/cs.min.js' in s:
   heads=re.findall(r'<head\b[^>]*>(.*?)</head\s*>',s,re.I|re.S)
   assert len(heads)==1 and s.count('app.uiscom.ru/static/cs.min.js')==1 and EMBED in heads[0], 'STOP: existing UIS differs or outside head: '+str(p)
   existing+=1;continue
  matches=list(re.finditer(r'</head\s*>',s,re.I))
  assert len(matches)==1,'STOP: invalid HTML '+str(p)
  pos=matches[0].start();new=(s[:pos]+BLOCK+s[pos:]).encode()
  assert new.replace(BLOCK.encode(),b'',1)==raw
  changes[p.relative_to(root)]=(raw,new)
 assert changes or existing,'STOP: no site pages'
 return changes,existing

def atomic(p,data,mode):
 fd,tmp=tempfile.mkstemp(prefix='.az-uis-',dir=p.parent)
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
 if not changes:print('UIS_HEAD=ALREADY_INSTALLED');return
 backup=Path('/var/backups/azmednsk')/('uis-head-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
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
 (standard/'uis-head-20261111.txt').write_text('Every public HTML page must include this UIS script exactly once inside head. Preserve on future page generation.\n'+BLOCK)
 print(f'UIS_HEAD=PASS; PAGES_UPDATED={len(changes)}; STANDARD=SAVED')
 print('BACKUP='+str(backup));print('URL=https://azmednsk.ru/')
 print('External HTTPS verification required after installation.')
if __name__=='__main__':main()
