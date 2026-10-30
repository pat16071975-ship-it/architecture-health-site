#!/usr/bin/env python3
import argparse,base64,fcntl,hashlib,json,os,shutil,socket,subprocess,tempfile
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path('/var/www/azmednsk-test/tilda-migration/public')
EXPECTED={'yandex_104ae2750b962a51.html': None}
PAYLOAD={'yandex_104ae2750b962a51.html': 'PGh0bWw+CiAgICA8aGVhZD4KICAgICAgICA8bWV0YSBodHRwLWVxdWl2PSJDb250ZW50LVR5cGUiIGNvbnRlbnQ9InRleHQvaHRtbDsgY2hhcnNldD1VVEYtOCI+CiAgICA8L2hlYWQ+CiAgICA8Ym9keT5WZXJpZmljYXRpb246IDEwNGFlMjc1MGI5NjJhNTE8L2JvZHk+CjwvaHRtbD4K'}
FILES={k:base64.b64decode(v) for k,v in PAYLOAD.items()}
def sha(v):return hashlib.sha256(v).hexdigest()
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def guard(p):require(not any(x.is_symlink() for x in [p,*p.parents]),'Symlink rejected')
def write(p,data):
 st=p.stat() if p.exists() else None
 p.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix='.az-webmaster-',dir=p.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chmod(tmp,(st.st_mode&0o7777) if st else 0o644)
  owner=st or p.parent.stat()
  os.chown(tmp,owner.st_uid,owner.st_gid)
  os.replace(tmp,p)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def verify():
 for rel in FILES:
  url='/'+(rel[:-10] if rel.endswith('index.html') else rel)
  r=subprocess.run(['curl','--noproxy','*','--resolve','azmednsk.ru:443:127.0.0.1','--connect-timeout','5','--max-time','25','-fsS','-H','Cache-Control: no-cache','https://azmednsk.ru'+url],capture_output=True)
  require(r.returncode==0 and r.stdout==FILES[rel],'HTTPS mismatch '+rel)
def restore(backup):
 state=json.loads((backup/'state.json').read_text())
 require(set(state)==set(FILES),'Backup manifest mismatch')
 for rel in FILES:
  p=ROOT/rel;guard(p);require(p.is_file() and p.read_bytes()==FILES[rel],'Rollback guard mismatch '+rel)
 for rel,old in state.items():
  p=ROOT/rel
  if old is None:p.unlink()
  else:
   b=backup/rel;require(sha(b.read_bytes())==old['sha'],'Backup hash mismatch');write(p,b.read_bytes());os.chmod(p,old['mode']);os.chown(p,old['uid'],old['gid'])
 print('ROLLBACK=PASS; BACKUP='+str(backup))
def main():
 ap=argparse.ArgumentParser();g=ap.add_mutually_exclusive_group(required=True);g.add_argument('--check',action='store_true');g.add_argument('--apply',action='store_true');g.add_argument('--rollback',type=Path);a=ap.parse_args()
 require(socket.gethostname()=='az-server' and os.geteuid()==0,'Run as root on az-server');require(ROOT.is_dir(),'Missing public directory')
 with open('/run/lock/azmednsk-critical.lock','w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if a.rollback:
   backup=a.rollback.resolve();require(backup.parent==Path('/var/backups/azmednsk') and backup.name.startswith('webmaster-verification-'),'Unexpected backup path');restore(backup);return
  current={}
  for rel,new in FILES.items():
   p=ROOT/rel;guard(p);old=p.read_bytes() if p.exists() else None;current[rel]=old
   require((old is None and EXPECTED[rel] is None) or (old is not None and sha(old) in [EXPECTED[rel],sha(new)]),'Source drift: '+rel)
  installed=all(current[k]==v for k,v in FILES.items())
  baseline=all((current[k] is None if EXPECTED[k] is None else current[k] is not None and sha(current[k])==EXPECTED[k]) for k in FILES)
  require(installed or baseline,'Partial installation; stop')
  for rel in ['site.js','assets/az-metrika-20261025.js','assets/az-conditions-system-20261024.css','assets/az-site-unify-20261024.css']:require((ROOT/rel).is_file(),'Missing shared asset '+rel)
  if a.check:print('PREFLIGHT=PASS; LIVE_WRITES=NONE');return
  if installed:verify();print('ALREADY_INSTALLED=PASS; HTTPS=PASS');return
  backup=Path('/var/backups/azmednsk')/('webmaster-verification-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700);state={}
  for rel,old in current.items():
   if old is None:state[rel]=None
   else:
    p=ROOT/rel;st=p.stat();q=backup/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q);require(q.read_bytes()==old,'Backup mismatch');state[rel]=dict(sha=sha(old),mode=st.st_mode&0o7777,uid=st.st_uid,gid=st.st_gid)
  (backup/'state.json').write_text(json.dumps(state));written=[]
  try:
   for rel,new in FILES.items():
    p=ROOT/rel;require((p.read_bytes() if p.exists() else None)==current[rel],'Concurrent change '+rel);write(p,new);written.append(rel)
   verify()
  except BaseException:
   for rel in reversed(written):
    p=ROOT/rel;require(p.read_bytes()==FILES[rel],'Concurrent edit blocks rollback; '+str(backup))
    if current[rel] is None:p.unlink()
    else:
     write(p,current[rel]);st=state[rel];os.chmod(p,st['mode']);os.chown(p,st['uid'],st['gid'])
   print('ROLLBACK=PASS; BACKUP='+str(backup));raise
  print('WEBMASTER_FILE=PASS; FILES_ADDED=1; HTTPS=PASS')
  print('PAGES_METRIKA_NGINX=UNCHANGED')
  print('BACKUP='+str(backup))
if __name__=='__main__':
 try:main()
 except Exception as e:raise SystemExit('STOP: '+str(e))
