#!/usr/bin/env python3
"""Reorder two role phrases in Pavlova team card. Exact preflight, backup and rollback."""
import argparse,base64,fcntl,hashlib,json,os,shutil,socket,subprocess,sys,tempfile,zlib
from pathlib import Path
from datetime import datetime,timezone
import re
M={'files': {'team/index.html': {'before': '3b266235b61cd937e192889d243fef7af0f48e93795b82047bbc8fcd42fe64a5', 'after': 'ad04066f8e510e9e6eac11d6b63311d4a708fd9003252fe739e40f8db0f27fbf', 'data': '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'}}, 'protected': {}}
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
 backup=backups/('pavlova-role-order-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700)
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
 print('PAVLOVA_ROLE_ORDER=PASS; HTTPS=PASS; HTML_UPDATED=1')
 print('PHOTOS_PRICES_AND_BOOKING_LINKS=UNCHANGED; METRIKA=UNCHANGED; NGINX=UNCHANGED')
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
