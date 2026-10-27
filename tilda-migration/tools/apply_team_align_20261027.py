#!/usr/bin/env python3
"""Myofunctional specialist routing on existing pages. Exact preflight, backup and rollback."""
import argparse,base64,fcntl,hashlib,json,os,shutil,socket,subprocess,sys,tempfile,zlib
from pathlib import Path
from datetime import datetime,timezone
import re
M={'files': {'team/index.html': {'before': 'a177b12de31bc222411adf680c663e634a2dee6a1b32907822aca44f833a86ca', 'after': '84567c284af091ac9d9e7f4c0e11fab411969042862080feb19b4f883f5c447c', 'data': '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'}}, 'protected': {}}
ROOT=Path('/var/www/azmednsk-test/tilda-migration/public')
BACKUPS=Path('/var/backups/azmednsk')
def sha(data):return hashlib.sha256(data).hexdigest() if data is not None else None
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def safe(root,n):
 p=root/n
 require(p.resolve().is_relative_to(root.resolve()),'Unsafe path '+n)
 require(not any(q.is_symlink() for q in [p,*p.parents]),'Symlink '+n)
 return p
def read(p):return p.read_bytes() if p.exists() else None
def prepare(root):
 result={}
 for n,r in M['files'].items():
  p=safe(root,n);old=read(p);require(sha(old) in (r['before'],r['after']),'Изменён исходный файл: '+n);new=zlib.decompress(base64.b64decode(r['data']))
  require(sha(new)==r['after'],'Payload mismatch '+n)
  require(sha(old) in (r['before'],r['after']),'Изменён исходный файл: '+n)
  result[n]=(old,new,p.stat() if old is not None else None)
 for n,h in M['protected'].items():require(sha(safe(root,n).read_bytes())==h,'Protected file changed: '+n)
 return result
def atomic(p,data,st):
 fd,tmp=tempfile.mkstemp(prefix='.az-facts-',dir=p.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  owner=st or p.parent.stat();os.chown(tmp,owner.st_uid,owner.st_gid);os.chmod(tmp,(st.st_mode&0o7777) if st else 0o644);os.replace(tmp,p)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def verify_http(n,expected):
 path=n.removesuffix('index.html') if n.endswith('index.html') else n
 with tempfile.TemporaryDirectory() as td:
  body=Path(td)/'body';headers=Path(td)/'headers'
  r=subprocess.run(['curl','--noproxy','*','--resolve','azmednsk.ru:443:127.0.0.1','--connect-timeout','5','--max-time','25','-sS','-w','%{http_code}','-D',str(headers),'-o',str(body),'-H','Cache-Control: no-cache','https://azmednsk.ru/'+path],capture_output=True)
  require(r.returncode==0,'HTTPS request failed '+n)
  if expected is None:
   require(r.stdout.strip() in (b'404',b'410'),'Removed profile must return404/410: '+n);return
  require(r.stdout.strip()==b'200' and body.read_bytes()==expected,'HTTPS content mismatch '+n)
  if n.endswith('.jpg'):require('content-type: image/jpeg' in headers.read_text().lower(),'JPEG MIME mismatch '+n)
def verify_all(prepared,verify):
 for n,(_,new,_) in prepared.items():verify(n,new)
def apply(root,backups,verify=verify_http):
 prepared=prepare(root);changes={n:r for n,r in prepared.items() if r[0]!=r[1]}
 if not changes:verify_all(prepared,verify);print('ALREADY_INSTALLED=PASS; HTTPS=PASS');return
 backup=backups/('team-align-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700)
 for n,(old,_,st) in changes.items():
  if old is not None:
   p=backup/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/n,p);require(p.read_bytes()==old,'Backup mismatch '+n)
 (backup/'manifest.json').write_text(json.dumps({n:{'before':r['before'],'after':r['after']} for n,r in M['files'].items()},indent=2))
 created=[];written=[];removed_dirs={}
 try:
  prepare(root)
  for n,(old,new,st) in changes.items():
   p=safe(root,n);require(read(p)==old,'Concurrent change '+n)
   if new is None:
    parent_st=p.parent.stat();p.unlink();written.append(n);p.parent.rmdir();removed_dirs[n]=parent_st;continue
   missing=[];parent=p.parent
   while not parent.exists():missing.append(parent);parent=parent.parent
   for d in reversed(missing):
    owner=d.parent.stat();d.mkdir(mode=0o755);os.chown(d,owner.st_uid,owner.st_gid);created.append(d)
   atomic(p,new,st);written.append(n)
  for n,(_,new,_) in prepared.items():require(read(root/n)==new,'Write mismatch '+n)
  verify_all(prepared,verify);prepare(root)
 except BaseException:
  failures=[]
  for n in reversed(written):
   old,new,st=changes[n];p=root/n
   try:
    require(read(p)==new,'Concurrent change blocks rollback '+n)
    if old is None:p.unlink()
    else:
     if n in removed_dirs:
      ds=removed_dirs[n];p.parent.mkdir(mode=ds.st_mode&0o7777);os.chown(p.parent,ds.st_uid,ds.st_gid)
     atomic(p,old,st);os.utime(p,ns=(st.st_atime_ns,st.st_mtime_ns))
    require(read(p)==old,'Rollback mismatch '+n)
   except Exception as e:failures.append(str(e))
  for d in reversed(created):
   try:d.rmdir()
   except OSError as e:failures.append(str(e))
  print('BACKUP='+str(backup));print('ROLLBACK='+('PASS' if not failures else str(failures)));raise
 print('TEAM_ALIGNMENT=PASS; HTTPS=PASS; HTML_UPDATED=1')
 print('PHOTOS_TEXTS_PRICES_AND_BOOKING_LINKS=UNCHANGED; METRIKA=UNCHANGED; NGINX=UNCHANGED')
 print('BACKUP='+str(backup));print('Следующий шаг: пришлите вывод для внешней проверки сайта.')
def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--check',action='store_true');g.add_argument('--apply',action='store_true');a=p.parse_args()
 require(os.geteuid()==0 and socket.gethostname()=='az-server','Нужен root на az-server');require(ROOT.is_dir(),'Missing site root')
 with open('/run/lock/azmednsk-critical.lock','w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if a.check:prepare(ROOT);print('PREFLIGHT=PASS; LIVE_WRITES=NONE')
  else:apply(ROOT,BACKUPS)
if __name__=='__main__':
 try:main()
 except Exception as e:print('STOP:',e,file=sys.stderr);sys.exit(1)
