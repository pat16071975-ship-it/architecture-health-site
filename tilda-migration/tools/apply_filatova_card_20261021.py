#!/usr/bin/env python3
"""Update approved doctor prices using exact guards, backups, local HTTPS and rollback."""
import argparse,concurrent.futures,fcntl,hashlib,json,os,shutil,socket,subprocess,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
MANIFEST = {'version': 1, 'purpose': 'Fix Filatova listing photo height only', 'files': {'index.html': {'before': '64599f8317dddfebed330e58117146408691d9be153dd4408632d355d9cb7547', 'after': '244554501dec04213ca0b69344c715397d52a5c963ee3bc8f4a8c658a325d819', 'replacements': [['loading="lazy" width="853" height="1280" style="object-fit:contain;object-position:center"', 'loading="lazy" style="object-fit:contain;object-position:center top"', 1]]}, 'team/index.html': {'before': 'c3a14495519bc45bf94a4c7130b2204de283e52abd1125574d4b3e78341315ac', 'after': '178eadd7aecd866406693e6ceade49356842dc101c9d483b89c2d89e75f9ff6f', 'replacements': [['loading="lazy" width="853" height="1280" style="object-fit:contain;object-position:center"', 'loading="lazy" style="object-fit:contain;object-position:center top"', 1]]}, 'o_nas/index.html': {'before': '6c9d29c029e6ac7304b9c25f030d014df2a6c672d9cb61517f49eecc00a16614', 'after': '87c3ec83e8c1eeeb40dd9bde0a3e1c524d56d79f26d991d861d4fcde887dad62', 'replacements': [['loading="lazy" width="853" height="1280" style="object-fit:contain;object-position:center"', 'loading="lazy" style="object-fit:contain;object-position:center top"', 1]]}}, 'protected': {'site.css': '8b2adf1b395ce968f1f116fac27b9a559d7d42265d1686737ec21dc4590406c9', 'layouts.css': '66d78398182263a458236c439c1a098fc8390539c7d45511d9240bd54f1a3a71', 'site.js': '190dba2936bc2def347a43e94b81e6fe226ee77ae38220db9e4790a17c355361', 'team/lapina-natalia/index.html': '77623e3b8f77d85d195bf9ca43d50c15011a22f55568b81a89640dd04de2d9fb', 'team/nazirov-uktamjon/index.html': 'dbaf792f12217ab22dfcaf2d06751e4778c28fd772e99e7e8b637182789b62d7', 'team/filatova-anna/index.html': '1710645feac7cc184b89622591a76c087b1f50fd2ebd8672fd8fb7ca7ca0da52'}, 'absent': ['team/bosak-yana/index.html'], 'redirects': [], 'public_checks': []}
ROOT=Path('/var/www/azmednsk-test/tilda-migration/public')
BACKUPS=Path('/var/backups/azmednsk')
PHOTOS=()
def sha(b):return hashlib.sha256(b).hexdigest()
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def safe(root,name):
 p=root/name
 require(p.resolve().is_relative_to(root.resolve()),'Unsafe path: '+name)
 require(all(not q.is_symlink() for q in [p,*p.parents]),'Symlink: '+name)
 return p
def prepare(root):
 result={}
 for name,rule in MANIFEST['files'].items():
  p=safe(root,name)
  require(not p.is_symlink() and p.resolve().is_relative_to(root.resolve()),'Unsafe path: '+name)
  require(all(not q.is_symlink() for q in [p,*p.parents] if q.is_relative_to(root)),'Symlink in path: '+name)
  old=p.read_bytes() if p.exists() else None
  digest=sha(old) if old is not None else None
  require(digest in (rule['before'],rule['after']),'Изменён исходный файл: '+name)
  if digest==rule['after']:new=old
  elif 'content' in rule:new=rule['content'].encode()
  else:
   s=old.decode()
   for a,b,count in rule.get('replacements',[]):
    require(count>0 and s.count(a)==count,'Неоднозначная замена: '+name);s=s.replace(a,b)
   s+=rule.get('append','');new=s.encode()
  require(sha(new)==rule['after'],'Неверный результат: '+name)
  result[name]=(old,new,p.stat() if old is not None else None)
 for name,digest in MANIFEST['protected'].items():require(sha(safe(root,name).read_bytes())==digest,'Изменена служебная страница: '+name)
 for name in MANIFEST['absent']:
  require(not (root/name).exists() and not (root/name).is_symlink(),'Удалённый профиль снова появился: '+name)
 return result
def atomic(p,data,st):
 fd,tmp=tempfile.mkstemp(prefix='.az-filatova-card-',dir=p.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  owner=st or p.parent.stat()
  os.chmod(tmp,(st.st_mode & 0o7777) if st else 0o644);os.chown(tmp,owner.st_uid,owner.st_gid)
  os.replace(tmp,p)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def verify_http(name,expected):
 path=MANIFEST['files'].get(name,{}).get('url',name.removesuffix('index.html') if name.endswith('index.html') else name).lstrip('/')
 with tempfile.TemporaryDirectory() as td:
  h=Path(td)/'headers';b=Path(td)/'body'
  r=subprocess.run(['curl','--noproxy','*','--resolve','azmednsk.ru:443:127.0.0.1','--connect-timeout','5','--max-time','20','-fsS','-w','%{http_code}','-D',str(h),'-o',str(b),'-H','Cache-Control: no-cache','https://azmednsk.ru/'+path],capture_output=True)
  require(r.returncode==0 and r.stdout.strip()==b'200','HTTPS failed: '+path)
  require(b.read_bytes()==expected,'HTTPS content mismatch: '+path)
  if name.endswith('.png'):
   require('content-type: image/png' in h.read_text().lower(),'Incorrect PNG Content-Type: '+path)
  if name.endswith('.pdf'):
   types=[line.split(':',1)[1].strip().split(';')[0].lower() for line in h.read_text().splitlines() if line.lower().startswith('content-type:')]
   require(types==['application/pdf'],'Incorrect PDF Content-Type: '+path)
  if name.endswith('.html') and name!='thank/index.html':
   for line in h.read_text().splitlines():
    if line.lower().startswith('x-robots-tag:'):require('noindex' not in line.lower() and 'none' not in line.lower(),'X-Robots-Tag blocks indexing: '+path)
def verify_redirect(path):
 with tempfile.TemporaryDirectory() as td:
  h=Path(td)/'headers';b=Path(td)/'body'
  r=subprocess.run(['curl','--noproxy','*','--resolve','azmednsk.ru:443:127.0.0.1','--connect-timeout','5','--max-time','20','-sS','-w','%{http_code}','-D',str(h),'-o',str(b),'https://azmednsk.ru'+path],capture_output=True)
  require(r.returncode==0 and r.stdout.strip()==b'301','Прежнее перенаправление изменилось: '+path)
  locations=[line.split(':',1)[1].strip() for line in h.read_text().splitlines() if line.lower().startswith('location:')]
  require(locations==['https://azmednsk.ru/team/'],'Неверный адрес перенаправления: '+path)
def verify_all(prepared,verify,redirect_verify,root):
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
  list(pool.map(lambda item:verify(item[0],item[1][1]),prepared.items()))
  list(pool.map(lambda name:verify(name,root.joinpath(name).read_bytes()),MANIFEST['public_checks']))
  list(pool.map(redirect_verify,MANIFEST['redirects']))
def apply(root,backups,verify=verify_http,redirect_verify=verify_redirect):
 prepared=prepare(root)
 protected={n:safe(root,n).read_bytes() for n in (*PHOTOS,*MANIFEST['protected'])}
 changes={n:v for n,v in prepared.items() if v[0]!=v[1]}
 if not changes:
  verify_all(prepared,verify,redirect_verify,root);print('ALREADY_INSTALLED=PASS; HTTPS=PASS');return
 backup=backups/('filatova-card-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700)
 for n,(old,new,st) in prepared.items():
  if old is not None:
   p=backup/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/n,p);require(p.read_bytes()==old,'Backup mismatch: '+n)
 (backup/'manifest.json').write_text(json.dumps(MANIFEST,ensure_ascii=False,indent=2))
 (backup/'absent-before.json').write_text(json.dumps([n for n,v in prepared.items() if v[0] is None]))
 for n,(old,_,_) in prepared.items():require(((root/n).read_bytes() if (root/n).exists() else None)==old,'Concurrent change: '+n)
 prepare(root)
 for n,b in protected.items():require(safe(root,n).read_bytes()==b,'Concurrent protected change: '+n)
 written=[];created_dirs=[]
 try:
  for n,(_,new,st) in changes.items():
   parent=(root/n).parent;missing=[]
   while not parent.exists():missing.append(parent);parent=parent.parent
   for directory in reversed(missing):
    owner=directory.parent.stat();directory.mkdir(mode=0o755);os.chown(directory,owner.st_uid,owner.st_gid);created_dirs.append(directory)
   require(safe(root,n).read_bytes()==changes[n][0],'Concurrent write: '+n)
   atomic(root/n,new,st);written.append(n)
  for n,(_,new,_) in prepared.items():require((root/n).read_bytes()==new,'Write mismatch: '+n)
  verify_all(prepared,verify,redirect_verify,root)
  for n,b in protected.items():require((root/n).read_bytes()==b,'Protected content changed: '+n)
  for n in MANIFEST['absent']:require(not (root/n).exists() and not (root/n).is_symlink(),'Removed profile reappeared: '+n)
 except BaseException:
  failures=[]
  for n in reversed(written):
   old,new,st=changes[n]
   try:
    require((root/n).read_bytes()==new,'Concurrent change blocks rollback')
    if old is None:(root/n).unlink()
    else:atomic(root/n,old,st);os.utime(root/n,ns=(st.st_atime_ns,st.st_mtime_ns))
    require(((root/n).read_bytes() if (root/n).exists() else None)==old,'Rollback mismatch')
   except Exception as e:failures.append(n+': '+str(e))
  for directory in reversed(created_dirs):
   try:directory.rmdir()
   except OSError as e:failures.append(str(directory)+': '+str(e))
  print('BACKUP='+str(backup));print('ROLLBACK_FILES='+('PASS' if not failures else 'FAIL '+str(failures)));raise
 print('FILATOVA_CARD=PASS; HTTPS=PASS; HTML_UPDATED=3')
 print('PHOTOS_OTHER_CONTENT_AND_BOOKING=UNCHANGED; NGINX=UNCHANGED')
 print('BACKUP='+str(backup))
 print('Следующий шаг: пришлите вывод для внешней проверки сайта.')
def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--check',action='store_true');g.add_argument('--apply',action='store_true');a=p.parse_args()
 require(socket.gethostname()=='az-server','Only az-server');require(os.geteuid()==0,'Use sudo python3');require(ROOT.is_dir(),'Missing site root')
 if a.check:prepare(ROOT);print('PREFLIGHT=PASS; LIVE_WRITES=NONE');return
 with open('/run/lock/azmednsk-critical.lock','w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);apply(ROOT,BACKUPS)
if __name__=='__main__':
 try:main()
 except Exception as e:print('STOP:',e,file=sys.stderr);sys.exit(1)
