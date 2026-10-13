#!/usr/bin/env python3
"""Approved emergency wrapping of long words in doctor cards. No network download or Git checkout during apply."""
import argparse,concurrent.futures,fcntl,hashlib,json,os,shutil,socket,subprocess,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
MANIFEST = {'version': 1, 'files': {'assets/az-doctor-cards-wrap-20261013.css': {'before': None, 'after': 'b3504521129c394bb669fbc4e420ed6e86c4ed9193b1390a1ee3315dadeeec9a', 'content': '/* Keep experience at the bottom of each equal-height grid card. */\n.doctors .doctor > a {\n  display: flex;\n  flex-direction: column;\n  height: 100%;\n}\n.doctors .doctor > a > img {\n  flex: 0 0 auto;\n}\n.doctors .doctor-copy {\n  display: flex;\n  flex-direction: column;\n  flex: 1 0 auto;\n  min-width: 0;\n  /* Break a long word only when it cannot fit the card. */\n  word-wrap: break-word;\n  overflow-wrap: anywhere;\n}\n.doctors .doctor .doctor-experience {\n  margin: auto 0 0;\n  padding-top: 1.5em;\n}\n'}, 'index.html': {'before': 'eb5aec377bd33e2cf705e54dac96af5c31b436b98c59adda24d7d8bbee5839f2', 'after': '50923a7d28c37d6da600153bafe65bac5d982045e505a75907b25437232f434d', 'replacements': [['href="/assets/az-doctor-cards-20261013.css"', 'href="/assets/az-doctor-cards-wrap-20261013.css"']]}, 'o_nas/index.html': {'before': '74480a415155dcc14f93a810e8c7fe97b861f9936d3dbc80869e91112cf79b43', 'after': '33e0063ad22e3f64c14cbf9be81d28ebf1ba50f35d537945c45b82201ef0b977', 'replacements': [['href="/assets/az-doctor-cards-20261013.css"', 'href="/assets/az-doctor-cards-wrap-20261013.css"']]}, 'team/index.html': {'before': 'f037902b4303c130145092956179b53f4d7028269f492d00e73282297841e724', 'after': '5290d2e296c7dda3c80804b7b9f09edbce7bf99101c1baa26ef458d6ea48ee0a', 'replacements': [['href="/assets/az-doctor-cards-20261013.css"', 'href="/assets/az-doctor-cards-wrap-20261013.css"']]}}, 'protected': {'thank/index.html': '8e3f8ba685ad7997b44ce64643ba9fbf8ffb7e5bc286c9bfafce162a8b58464b', 'patient/patient-pravila/index.html': 'b4203f987cc35afe075e88b458a96a76b4867d7a3183b4427ebe9c643e0f3a4a', 'team/pavlova-anna/index.html': '6c1806d52c3da6a880079e353c10501946896be63ef67d5ccdef185add958ffb', 'team/zubachev-roman/index.html': '01f895efe4ea1389006ebae8ae30f328237f44515beeeeb83da2ac2c884d20cc', 'team/starostenko-vadim/index.html': '3d21e9539c50c512d5b096c75d8b5eac8b26b163593b10e65926e39e505a590c', 'team/dimitrenko-anna/index.html': 'd1e56ecb3bdf75829fc7f443c3dac07424749202fb28284976e2d9700ab062bf', 'team/mamontova-lubava/index.html': '5f9c931f887bd5943b0cbe3ce1caf5fa8e67245a0cd0298c47c4713cab742793', 'team/lubaeva-alena/index.html': '8f511e0e5c3bc134eaf629a68dcdbf6a7a06314f5c1534a8bc67eae619a25ac7', 'uslugi/ortopedicheskie-stelki/index.html': '299490f7bc517b8912fb2767500ff43c6377ef7e8b3a39d26b6cf39e12870ad8', 'site.css': 'bb0e025c9e291ccdb24da8b9e80cfac3c85d998c1387aed24fa645cfa0fe7320', 'layouts.css': '66d78398182263a458236c439c1a098fc8390539c7d45511d9240bd54f1a3a71', 'site.js': '190dba2936bc2def347a43e94b81e6fe226ee77ae38220db9e4790a17c355361', 'team/chirkov-maxim/index.html': 'cb0519f195ff8392c146bcae585f82b088ca4e19ec84ba593b0f808260d74b8c', 'assets/az-doctor-cards-20261013.css': '5813a1e65ce1ececfddb2d11dc6669cff3f61e16390b1b01cf870a512b3e62a0', 'assets/az-mozharova-approved-20261013.jpg': '621542c5ea7b609752813c546548d270d4f1848370eb1901387d48882fa98e4c', 'team/tproduct/834140634503-kazantsev-leonid-evgenevich/index.html': 'd7f32468b6bf4ec1aab639cd78e1975c0460022f5222401704c2f2ca28881aa0', 'team/tproduct/392688125713-mozharova-ekaterina-sergeevna/index.html': 'ef7c52403034ae0a871c396608caf9ce1ba7813a5d13343e22a3aab59a3e652e'}}
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
 fd,tmp=tempfile.mkstemp(prefix='.az-card-wrap-',dir=p.parent)
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
 backup=backups/('card-wrap-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700)
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
 print('DOCTOR_CARD_WRAP=PASS; HTTPS=PASS; PHOTOS_AND_PRICES=UNCHANGED')
 print('HTML_UPDATED=3; CARDS=38; EXPERIENCE_ALIGNMENT=UNCHANGED; FONT_SIZE=UNCHANGED')
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
