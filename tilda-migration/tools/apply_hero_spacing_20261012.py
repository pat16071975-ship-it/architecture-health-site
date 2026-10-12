#!/usr/bin/env python3
"""Approved home hero spacing correction. No network download or Git checkout during apply."""
import argparse,concurrent.futures,fcntl,hashlib,json,os,shutil,socket,subprocess,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
MANIFEST = {'version': 1, 'files': {'index.html': {'before': '3e8fe59b5359f6953359a423fe8ec98a6d26b0a7c14437c0194e90b431631e03', 'after': '83cb824e9e664aaac7aadec496cb1d785c20f222d73ef1d56515cf60bf9a38f7', 'replacements': [['</head>', '<style id="az-home-hero-spacing-20261012">\n/* Keep the description and both actions inside the home hero. */\n@media (min-width:1200px){\n #visual-2488705671-1784635266210000001{left:max(20px,calc(50% - 688px));width:min(571px,calc(50% - 40px))}\n #visual-2488705671-1784635438553000002{left:max(20px,calc(50% - 688px))}\n #visual-2488705671-1784635533568000003{left:calc(max(20px,calc(50% - 688px)) + 231px)}\n}\n@media (min-width:960px) and (max-width:1199px){\n #layout-2488705671{--az-hero-left:max(20px,calc(50% - 492px));--az-hero-button:min(200px,calc((50vw - 111px)/2))}\n #visual-2488705671-1784635266210000001{left:var(--az-hero-left);width:min(421px,calc(50% - 91px))}\n #visual-2488705671-1784635438553000002{left:var(--az-hero-left);width:var(--az-hero-button)}\n #visual-2488705671-1784635533568000003{left:calc(var(--az-hero-left) + var(--az-hero-button) + 20px);width:var(--az-hero-button)}\n}\n@media (max-width:479px){\n #visual-2488705671-1784635266210000001{width:min(310px,calc(50% + 140px))}\n}\n</style>\n</head>']]}}, 'protected': {'team/tproduct/338106745633-palunin-vladislav-andreevich/index.html': '7d659f2883a0a841cb81a44c2f9e1c92c2be74b8f4ad19e364e23f7edb7ff560', 'team/tproduct/392688125713-mozharova-ekaterina-sergeevna/index.html': 'acd1fbc01cd46949a126af6461bf0f3fd4de4ec9f4eeb1b8a2b3c20b2e9e6872', 'team/tproduct/834140634503-kazantsev-leonid-evgenevich/index.html': '82546b20061db470fb23d42dc539772e32b035d5e604d7bbf873c2ab6a7e5912', 'layouts.css': '66d78398182263a458236c439c1a098fc8390539c7d45511d9240bd54f1a3a71', 'robots.txt': '9a93da66b2f7b9bc05ad7429409692dff442b3bf8f988f2937ae21ad9cf6744f', 'sitemap.xml': '74ffc28b998df2bc8425c17f6b3369cf15484b9398a17dc48aef497dd87b8ba4'}}
ROOT=Path('/var/www/azmednsk-test/tilda-migration/public')
BACKUPS=Path('/var/backups/azmednsk')
PHOTOS=('assets/4ab8539d240e65119fbc.jpg','assets/594800af226e87bb24de.jpg','assets/560ccf811a455b6ce7b0.jpeg','assets/3a23cefb232c54a024e6.jpg')
def sha(b):return hashlib.sha256(b).hexdigest()
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def prepare(root):
 result={}
 for name,rule in MANIFEST['files'].items():
  p=root/name
  require(not p.is_symlink() and p.resolve().is_relative_to(root.resolve()),'Unsafe path: '+name)
  old=p.read_bytes() if p.exists() else None
  digest=sha(old) if old is not None else None
  require(digest in (rule['before'],rule['after']),'Изменён исходный файл: '+name)
  if digest==rule['after']:new=old
  elif 'content' in rule:new=rule['content'].encode()
  else:
   s=old.decode()
   for a,b in rule.get('replacements',[]):
    require(s.count(a)==1,'Неоднозначная замена: '+name);s=s.replace(a,b,1)
   s+=rule.get('append','');new=s.encode()
  require(sha(new)==rule['after'],'Неверный результат: '+name)
  result[name]=(old,new,p.stat() if old is not None else None)
 for name,digest in MANIFEST['protected'].items():require(sha((root/name).read_bytes())==digest,'Изменена служебная страница: '+name)
 return result

def atomic(p,data,st):
 fd,tmp=tempfile.mkstemp(prefix='.az-hero-',dir=p.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  owner=st or p.parent.stat()
  os.chmod(tmp,(st.st_mode & 0o7777) if st else 0o644);os.chown(tmp,owner.st_uid,owner.st_gid)
  os.replace(tmp,p)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def verify_http(name,expected):
 path=name.removesuffix('index.html') if name.endswith('index.html') else name
 with tempfile.TemporaryDirectory() as td:
  h=Path(td)/'headers';b=Path(td)/'body'
  r=subprocess.run(['curl','--noproxy','*','--resolve','azmednsk.ru:443:127.0.0.1','--connect-timeout','5','--max-time','20','-fsS','-D',str(h),'-o',str(b),'-H','Cache-Control: no-cache','https://azmednsk.ru/'+path],capture_output=True)
  require(r.returncode==0,'HTTPS failed: '+path)
  require(b.read_bytes()==expected,'HTTPS content mismatch: '+path)
  if name.endswith('.html'):
   for line in h.read_text().splitlines():
    if line.lower().startswith('x-robots-tag:'):require('noindex' not in line.lower() and 'none' not in line.lower(),'X-Robots-Tag blocks indexing: '+path)

def verify_all(prepared,verify):
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
  list(pool.map(lambda item:verify(item[0],item[1][1]),prepared.items()))

def apply(root,backups,verify=verify_http):
 prepared=prepare(root)
 protected={n:(root/n).read_bytes() for n in (*PHOTOS,*MANIFEST['protected'])}
 changes={n:v for n,v in prepared.items() if v[0]!=v[1]}
 if not changes:
  verify_all(prepared,verify);print('ALREADY_INSTALLED=PASS; HTTPS=PASS');return
 backup=backups/('hero-spacing-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700)
 for n,(old,new,st) in prepared.items():
  if old is not None:
   p=backup/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/n,p);require(p.read_bytes()==old,'Backup mismatch: '+n)
 (backup/'manifest.json').write_text(json.dumps(MANIFEST,ensure_ascii=False,indent=2))
 (backup/'absent-before.json').write_text(json.dumps([n for n,v in prepared.items() if v[0] is None]))
 for n,(old,_,_) in prepared.items():require(((root/n).read_bytes() if (root/n).exists() else None)==old,'Concurrent change: '+n)
 written=[]
 try:
  for n,(_,new,st) in changes.items():atomic(root/n,new,st);written.append(n)
  for n,(_,new,_) in prepared.items():require((root/n).read_bytes()==new,'Write mismatch: '+n)
  verify_all(prepared,verify)
  for n,b in protected.items():require((root/n).read_bytes()==b,'Protected content changed: '+n)
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
  print('BACKUP='+str(backup));print('ROLLBACK_FILES='+('PASS' if not failures else 'FAIL '+str(failures)));raise
 print('HOME_HERO_SPACING=PASS; HTTPS=PASS; PHOTOS=UNCHANGED')
 print('HTML_UPDATED=1; MOBILE_DESCRIPTION_GUTTER=10px')
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
