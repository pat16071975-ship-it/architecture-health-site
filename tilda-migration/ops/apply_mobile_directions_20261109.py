#!/usr/bin/env python3
"""AZ scoped SEO/navigation changes. No full-site deployment."""
from pathlib import Path
import sys,os,socket,re,json,hashlib,datetime,tempfile,shutil,urllib.request
ROOT=Path('/var/www/azmednsk-preview')
PAYLOAD={'uslugi/functional_dentistry/index.html': {'before': '844f43badda8bff927e283a7d22c7f8006ffc6a8cdc835210fb73089f828de07', 'after': '4e0e7449670dd5b18109cea61dbd5a68f86e84b9205bd9c36283e50c120a5c43', 'ops': [['</head>', '<style id="az-mobile-directions-20261109">@media(max-width:360px){.az-doctor-price strong{white-space:normal!important;overflow-wrap:anywhere}.az-doctor-booking{min-width:0;max-width:100%}}</style></head>']]}, 'uslugi/miofunkcionalnaya-terapiya/index.html': {'before': 'a119aef83a793fd73e6bda74c38ab04f589957c34c0762d5b49074f41c5f3882', 'after': 'ceaac2d96e3ac0e44a8622fd0e99677ded6bd8ceccecb37a0fcfa623a3b5938e', 'ops': [['</head>', '<style id="az-mobile-directions-20261109">@media(max-width:360px){main h1,main h2{overflow-wrap:anywhere}}</style></head>']]}}
def sha(b):return hashlib.sha256(b).hexdigest()
def safe(p):
 assert not any(x.is_symlink() for x in [p,*p.parents]),'STOP: symlink '+str(p)
def prepare(root):
 safe(root);changes={};existing=0
 for rel,item in PAYLOAD.items():
  p=root/rel;safe(p);assert p.is_file(),'STOP: missing '+rel
  old=p.read_bytes()
  if sha(old)==item['after']:existing+=1;continue
  assert sha(old)==item['before'],'STOP: page changed since review: '+rel
  s=old.decode()
  for a,b in item['ops']:
   assert s.count(a)==1,'STOP: anchor '+rel
   s=s.replace(a,b,1)
  new=s.encode();assert sha(new)==item['after'],'STOP: invalid result '+rel
  changes[rel]=(old,new)
 return changes,existing
def atomic(p,data,st):
 fd,tmp=tempfile.mkstemp(prefix='.az-seo-',dir=p.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chmod(tmp,st.st_mode&0o777)
  if os.geteuid()==0:os.chown(tmp,st.st_uid,st.st_gid)
  os.replace(tmp,p)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def apply(root,backup,verify=None):
 changes,existing=prepare(root)
 if not changes:return 0,existing
 backup.mkdir(parents=True,mode=0o700);stats={};done=[]
 for rel,(old,new) in changes.items():
  p=root/rel;assert p.read_bytes()==old,'STOP: concurrent change'
  target=backup/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target);stats[rel]=p.stat()
 (backup/'manifest.json').write_text(json.dumps({r:{'before':sha(v[0]),'after':sha(v[1])} for r,v in changes.items()},indent=2))
 try:
  for rel,(old,new) in changes.items():
   p=root/rel;assert p.read_bytes()==old,'STOP: concurrent change';done.append(rel);atomic(p,new,stats[rel])
  for rel,(old,new) in changes.items():assert (root/rel).read_bytes()==new
  if verify:verify(changes)
 except BaseException:
  for rel in reversed(done):atomic(root/rel,changes[rel][0],stats[rel])
  print('AUTO_ROLLBACK=PASS');raise
 return len(changes),existing
def main():
 if len(sys.argv)==3 and sys.argv[1]=='--check-root':
  changes,existing=prepare(Path(sys.argv[2]));print(f'PREFLIGHT=PASS; PAGES_TO_UPDATE={len(changes)}; ALREADY_PRESENT={existing}; LIVE_WRITES=NONE');return
 assert len(sys.argv)==1,'STOP: unsupported arguments'
 assert socket.gethostname()=='vdska' and os.geteuid()==0,'STOP: root on vdska required'
 conf=Path('/etc/nginx/sites-available/azmednsk-preview').read_text()
 assert re.search(r'server_name\s+[^;]*\bazmednsk\.ru\b',conf),'STOP: wrong nginx server'
 assert set(re.findall(r'^\s*root\s+([^;]+);',conf,re.M))=={str(ROOT)},'STOP: wrong document root'
 changes,existing=prepare(ROOT);print(f'PREFLIGHT=PASS; PAGES_TO_UPDATE={len(changes)}; ALREADY_PRESENT={existing}')
 if not changes:print('MOBILE_DIRECTIONS=ALREADY_INSTALLED');return
 backup=Path('/var/backups/azmednsk')/('mobile-directions-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
 def verify(items):
  for rel in ['uslugi/functional_dentistry/index.html', 'uslugi/miofunkcionalnaya-terapiya/index.html']:
   if rel not in items:continue
   url='https://azmednsk.ru/'+rel.removesuffix('index.html')+'?seo-check=20261109'
   with urllib.request.urlopen(url,timeout=30) as response:body=response.read();assert response.status==200
   assert sha(body)==sha(items[rel][1]),'STOP: HTTPS response differs '+rel
 n,e=apply(ROOT,backup,verify)
 print(f'MOBILE_DIRECTIONS=PASS; PAGES_UPDATED={n}; ALREADY_PRESENT={e}; HTTPS=PASS')
 print('PRICES_PHOTOS_BOOKING_METRIKA_NGINX=UNCHANGED');print('BACKUP='+str(backup))
 print('URL=https://azmednsk.ru/uslugi/functional_dentistry/');print('External browser verification required after installation.')
if __name__=='__main__':main()
