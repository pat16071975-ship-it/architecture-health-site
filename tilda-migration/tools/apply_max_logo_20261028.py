#!/usr/bin/env python3
"""Myofunctional specialist routing on existing pages. Exact preflight, backup and rollback."""
import argparse,base64,fcntl,hashlib,json,os,shutil,socket,subprocess,sys,tempfile,zlib
from pathlib import Path
from datetime import datetime,timezone
import re
M={'files': {'assets/az-chat-20261026.css': {'before': '986d6679a8e1c99d71b90506027c32755ab7b8cddff85cb646fabbbd4bff32c9', 'after': 'ed679ae6f44987d86a218b2055717ce4c0aa8af9bbae5616b8585c13ed8b8e0d', 'data': 'eNp1VcFy4zYMvecr1M10ZrcTJpSiOA51aT+gX9DpgZIgmRuJVEkqcVaTfy9I0xIlOwd7bJAAHoCHx5t7/otUB26nUh2JEb+EbFmpdA2aoKXo+ZG8i9oeWJpmdHAG3QrJsnw4Jny0qhh4XTundI8Wf6Xk1Wur1ShrdtvkDW+a4hSRpXjFqE7UyW1dwb7JwgHRvBajYRiiaJS0pOG96D7Y3/jbgNbc3v2lBe/uDJeGoEU0RaU6pdltlub5ExRVB1wjcHv4vDmXlPxxvajP+cIhm3w6vAIsyzB7JySQA4j2YFl6/zhnoXta5efiaUKT1N0OtZMOGrzuLCGLNzxG1UJZpS9zYtJqUU+1MEPHP5j7U7gvYqFHiwWCacdeGpY2OsFP0fLBx18iVFwvEZoOjgXvRCuJwBiGVSAt6OLnaKxoPjAc/pWWmYFXQEqw7wDyFDRfyvAZknS3HeHV+ZUv9Qs0m/k5117IwBi6oMW2vs5ohfRd/gq0h+V4FHqfZrs9f/5N9IPSlktbLCPzWP3f99PInindjDAvLBwtqaFSSCOhJJNKwhJtjZEd1BvoaeuCfQDt4n7p16hqNORNGFF2cDcTzIw9cuZjfTyp0bpglwQpwglRTWPAsjyeuMApbnvoqYMGnOqqh6cBPLr2hE7431e2DYdAzEG7Guhq7IHyYQiOBOtVWYChRiRrlFEcq3FlB64RVzLq7vu3B26wNPOADk5csFJR4WqTjGa7lGb7e/PWfvuRnAp5cMzlQiZSEQ0DcLupgQaAUZoIZ8TA//S0rtXP2NMvNC2+ex7c3G9HmtUF0bfzYdmp6jUiaBBMiuOLrGdKbsyLwq6JWajyJ1QohcKy0IWVV1Aip8GRfbO48clphemFaW7lNXJjpUOkkWm6wr5Ry0t87pVAtYwOAp+esl35XC95pLIwBSe3/M4rXvTHC21+2oS6+bOHWvDvSz9RDIbjj2l+4c7hd07lslj38mD56mVYHj58P6xVvce4kfMvFdwr2v5CvD0d3zWeua8FDD3Du3DzKho15SksMEs3KhHWn0brv8J7heCew0U1aoM9HZTwMrJS4O04zoDdu5+vo/+jBpD/Tuc9oL9frE44yq+vyNq8YUkEw43s8+Z/0acyRw=='}, 'assets/az-max-official-20261028.svg': {'before': None, 'after': '5caa61a4b0731d0d89421b4fb24f41433025e5aea59155a14cf3a3fada6c9174', 'data': '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'}}, 'protected': {}}
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
 backup=backups/('max-logo-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700)
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
 print('MAX_LOGO=PASS; HTTPS=PASS; CSS_UPDATED=1; OFFICIAL_LOGO=1')
 print('TEXTS_LINKS_QR_AND_BOOKING=UNCHANGED; METRIKA=UNCHANGED; NGINX=UNCHANGED')
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
