#!/usr/bin/env python3
"""Myofunctional specialist routing on existing pages. Exact preflight, backup and rollback."""
import argparse,base64,fcntl,hashlib,json,os,shutil,socket,subprocess,sys,tempfile,zlib
from pathlib import Path
from datetime import datetime,timezone
import re
M={'files': {'team/pavlova-anna/index.html': {'before': '50af455c0ce24c2e4cc46f2253151b5e58c46d0446ec4e2a2c5020820ea2230f', 'after': 'a7c06de0077f3b397c315afa4f9f3c252d8bead26424691025e42198a280b900', 'data': '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'}}, 'protected': {}}
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
 backup=backups/('pavlova-photo-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700)
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
 print('PAVLOVA_PHOTO_SIZE=PASS; HTTPS=PASS; HTML_UPDATED=1')
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
