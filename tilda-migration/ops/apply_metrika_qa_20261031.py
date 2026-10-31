#!/usr/bin/env python3
import gzip,argparse,base64,fcntl,hashlib,json,os,shutil,socket,subprocess,tempfile
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path('/var/www/azmednsk-test/tilda-migration/public')
EXPECTED={'assets/az-metrika-20261025.js': '143db9ca40faa4bb4c051c917bc7051d981c045619ed0eec8ee2e003d4566621'}
PAYLOAD={'assets/az-metrika-20261025.js': 'H4sIAAAAAAACA3VWUW/bNhB+76+4YkMlpSodY9gK2BOCBA2GYGmQrR2GLU0DWjrZjClSJSk7Tuz/viMl2Y6XPlgSj3ffHe++O3pwBP9wVeADfERnRM5hOPzp/ftfjo9/HkHdTKTIYYkTKxzCQtDLAqlDrpXjuQP6Ca0saCVXDI4Gr+KyUUEGcQJPrwCixiJYD+2iMa1FCfHrwdd4uVx+YckJf6ywUHb+hZnmx4FgDq2Lpc65x2AzbZ3iFSawXsNSqEIvGX8Mkc75hbKOS4lFAgZdY5SHHwzg/KGmqIWDGs27idFLiwb+OAVdu3e6cWNQuCCJUCU9CaIsoTS6gotr0AYar82nqBwjuAU38I3/jivIIOKPd1Xr+q4Qlk/I9d1iGE7V6n3UBZKiwiX89eflJ+Qmn11zwyu7O5IN0oRN0cV7iFGyg/nQgRNUj5mRe12WwZczq5DZNpeHGgl4V/KT04ZOQe7chcMqDodIIRq2jgBQUl3+B6AO7Q1WeoE7iM76IMjtgsr0zHy67z5pnbQZ2wAlJJ9BLKZKG+wMku5kVMa/hZtReYIYGsUXXEjvBNwMAfsaU55JSUgZKGm0tLQvLNTe+3eTBG/e9HQiM6slOe4+2JIbFUen/xJlRi/5H3uKwK5yGQES/8GTagWev63zNtEb+h0S98XU9b2xk+zT+rvcJwxnGvQ629aLqxRTl5pUpPOUJ21Gqxtxm/nHer3VS55iL2E8a1/r9c1twurGzmJupk1FTWCTzXhrz2Q2PPLs/sAdxh0VSm1iz9v77Hh8/2uh82DHbG5E7SyTqKZuNr5/+zZ5EmV8uH9zf8usyakyJumOuwmw8wxZbpAcnUv0FrFLxjwLlOok9mz1mU+vaD7Q3s3x7XjOuF2pPBvSlwc1Y85qbkj1isrPhKLedmdIEWPsMxMKlMRtctM+tDRqY4vSaOZcbUeDQZWzVZiSNKUGXd0Hjk/ZvT0RRbYdmWSyqtrCdxVbVfF2N42EEgTbFsRaM/KlS3Ni8rzidbuiYUtzVptRyalD06BqkEaVQTPaZq+XpI2Ro924JHFrwfO8MZS7z4bn8zNNBccW3nnBpVBzG9YhAd3cvNK+lhUNIdmgTYnFTpAvEAU9RSnQkLAQnPrVhg6jwYaOpH5sTrSeCzWlLqpqieECoIagGvkm3IbNi+LcSy+FdajQxFE4fJRuKRmMOsp6VkkKNQtC5oiT6Hzr7q9ZLrX1TXfyophm7I1Pyy0NthGoRspxNxXi1x472fVY69ArZ37HE+3UUaknDZE98vIo3ENRlE41l9kzsMFXh3LUX2BeOUmCVlTPtMK79pzPRm/QIuJHP3TJizqLbnmna1T7Jm1W9i+APmhiQdbdOgE1fUaJrlG7SEmX1UY7nWvpvbccDyPRb/VXrt/qAmF0RZcykP8wxG4n6j1swlDvZnry1Lbypk+St01ebAxq9Hz2G223ue0iJlJeG6SmXSAoOg+9fGXavyD4QCTylOupR9SUegoz2pTESk+8TepJTmjU5fT8D++1GobtCAAA'}
FILES={k:gzip.decompress(base64.b64decode(v)) for k,v in PAYLOAD.items()}
def sha(v):return hashlib.sha256(v).hexdigest()
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def guard(p):require(not any(x.is_symlink() for x in [p,*p.parents]),'Symlink rejected')
def write(p,data):
 st=p.stat() if p.exists() else None
 p.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix='.az-metrika-qa-',dir=p.parent)
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
   backup=a.rollback.resolve();require(backup.parent==Path('/var/backups/azmednsk') and backup.name.startswith('metrika-qa-'),'Unexpected backup path');restore(backup);return
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
  backup=Path('/var/backups/azmednsk')/('metrika-qa-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700);state={}
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
  print('METRIKA_QA=PASS; FILES_UPDATED=1; HTTPS=PASS')
  print('PAGES_PHOTOS_PRICES_BOOKING_NGINX=UNCHANGED; COUNTER_SETTINGS=UNCHANGED')
  print('BACKUP='+str(backup))
if __name__=='__main__':
 try:main()
 except Exception as e:raise SystemExit('STOP: '+str(e))
