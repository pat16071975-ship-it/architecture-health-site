#!/usr/bin/env python3
"""Approved text corrections for Lyubaeva and the insole service. No network download or Git checkout during apply."""
import argparse,concurrent.futures,fcntl,hashlib,html,json,os,re,shutil,socket,subprocess,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
MANIFEST = {'version': 1, 'files': {'o_nas/index.html': {'before': 'dc36731be8469ab181ad2efc7f59dfdbd20f96f7ff3d5922eb37d6bc9b923e18', 'after': 'b0f623deef819cc9378a32572f36ff3a94660acd57eef2aac95359b07fd953a5', 'replacements': [('антивозраствной', 'антивозрастной', 1)]}, 'team/index.html': {'before': '8269b166b69c4e68a0001cb7e2ae6199a83e35caa329ddfb2ef2447ae7049c58', 'after': '29235b58f14028b0d3bd556d0c6f9bdab8875a9b3047bc8513d4e5300d7ab6ac', 'replacements': [('антивозраствной', 'антивозрастной', 1)]}, 'team/lubaeva-alena/index.html': {'before': '828ca92a5e855983b0950110a22062b44c68968a6d639be65967a83b17fce520', 'after': '8f511e0e5c3bc134eaf629a68dcdbf6a7a06314f5c1534a8bc67eae619a25ac7', 'replacements': [('антивозраствной', 'антивозрастной', 3)]}, 'uslugi/ortopedicheskie-stelki/index.html': {'before': 'd596fa88dd286a5483ab2fa929af5b6bd276eaa49ade907d49a8a52fdae849d1', 'after': '299490f7bc517b8912fb2767500ff43c6377ef7e8b3a39d26b6cf39e12870ad8', 'replacements': [('Коррекция и доработка стелек — бесплатно в течение первого месяца.', 'Коррекция и доработка стелек в течение первого месяца.', 1)]}}, 'protected': {'index.html': 'd022f1a321b0946a73f9a4ab17627506c26d68435cf0203a519ffef2f6dc28bf', 'thank/index.html': '8e3f8ba685ad7997b44ce64643ba9fbf8ffb7e5bc286c9bfafce162a8b58464b', 'patient/patient-pravila/index.html': 'b4203f987cc35afe075e88b458a96a76b4867d7a3183b4427ebe9c643e0f3a4a', 'team/pavlova-anna/index.html': '6c1806d52c3da6a880079e353c10501946896be63ef67d5ccdef185add958ffb', 'team/zubachev-roman/index.html': '01f895efe4ea1389006ebae8ae30f328237f44515beeeeb83da2ac2c884d20cc', 'team/starostenko-vadim/index.html': '3d21e9539c50c512d5b096c75d8b5eac8b26b163593b10e65926e39e505a590c', 'team/dimitrenko-anna/index.html': 'd1e56ecb3bdf75829fc7f443c3dac07424749202fb28284976e2d9700ab062bf', 'team/mamontova-lubava/index.html': '5f9c931f887bd5943b0cbe3ce1caf5fa8e67245a0cd0298c47c4713cab742793'}}
ROOT=Path('/var/www/azmednsk-test/tilda-migration/public')
BACKUPS=Path('/var/backups/azmednsk')
PHOTOS=('assets/4ab8539d240e65119fbc.jpg','assets/594800af226e87bb24de.jpg','assets/560ccf811a455b6ce7b0.jpeg','assets/3a23cefb232c54a024e6.jpg')
def sha(b):return hashlib.sha256(b).hexdigest()
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
LEGAL_PAGE='patient/patient-pravila/index.html'
FREE_RE=re.compile(r'бесплат\w*|безвозмезд\w*|без\s+(?:взимания\s+)?платы|без\s+оплат\w*|(?<![\d.,])0\s*(?:₽|руб(?:лей|ля|ль)?\b)',re.I)
def audit_content(root,prepared):
 for p in root.rglob('*.html'):
  name=p.relative_to(root).as_posix()
  require(not p.is_symlink() and p.resolve().is_relative_to(root.resolve()),'Unsafe HTML path: '+name)
  text=(prepared[name][1] if name in prepared else p.read_bytes()).decode('utf-8')
  text=html.unescape(text)
  require('антивозраств' not in text.lower(),'Найдена дополнительная опечатка: '+name)
  if name!=LEGAL_PAGE:
   require(not FREE_RE.search(text),'Найдено дополнительное упоминание бесплатности: '+name)

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
   for a,b,count in rule.get('replacements',[]):
    require(s.count(a)==count,'Неоднозначная замена: '+name);s=s.replace(a,b)
   s+=rule.get('append','');new=s.encode()
  require(sha(new)==rule['after'],'Неверный результат: '+name)
  result[name]=(old,new,p.stat() if old is not None else None)
 for name,digest in MANIFEST['protected'].items():require(sha((root/name).read_bytes())==digest,'Изменена служебная страница: '+name)
 audit_content(root,result)
 return result

def atomic(p,data,st):
 fd,tmp=tempfile.mkstemp(prefix='.az-content-',dir=p.parent)
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
 backup=backups/('content-corrections-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700)
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
 print('CONTENT_CORRECTIONS=PASS; HTTPS=PASS; PHOTOS_AND_PRICES=UNCHANGED')
 print('HTML_UPDATED=4; TYPO_REPLACEMENTS=5; FREE_SERVICE_CLAIMS=0; LEGAL_TEXT=UNCHANGED')
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
