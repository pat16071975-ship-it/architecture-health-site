#!/usr/bin/env python3
import argparse,fcntl,hashlib,os,socket,subprocess,tempfile,shutil,sys
from pathlib import Path
from datetime import datetime,timezone
BEFORE='a2d7a6f7ef4012e54185cb23ca2c04be4f208a0a483a9af2b4f51590141140e4'
AFTER='fc257c3be56e407cb95be57d6d94bc6055b239ad61feae05247073870308650f'
OLD='<dialog id="booking"><button class="dialog-close" type="button" aria-label="Закрыть">×</button><div class="booking-card"><h3>Запишитесь на консультацию</h3><p>Выберите удобное время онлайн или позвоните в клинику.</p><a class="button" href="https://booking.medflex.ru/?user=e0c452bf7c5bc897e8b8c0782bcb16cb" target="_blank" rel="noopener">Записаться на приём</a><a class="phone" href="tel:+73832842421">+7 (383) 284-24-21</a></div></dialog>'
NEW='<dialog id="booking"><button class="dialog-close" type="button" aria-label="Закрыть">×</button><div class="booking-card"><h3>Запишитесь к ортодонту</h3><p>Зубачев Роман Николаевич и Филатова Анна Дмитриевна принимают детей и взрослых. Выберите врача, а в онлайн-записи — нужный вид приёма и удобное время. Также можно позвонить в клинику.</p><p><a class="button" href="https://booking.medflex.ru/?user=e0c452bf7c5bc897e8b8c0782bcb16cb&amp;employeeId=203140&amp;source=4" target="_blank" rel="noopener" data-az-doctor-booking="203140">Записаться к Зубачеву</a></p><p><a class="button" href="https://booking.medflex.ru/?user=e0c452bf7c5bc897e8b8c0782bcb16cb&amp;employeeId=1316218&amp;source=4" target="_blank" rel="noopener" data-az-doctor-booking="1316218">Записаться к Филатовой</a></p><a class="phone" href="tel:+73832842421">+7 (383) 284-24-21</a></div></dialog>'
ROOT=Path('/var/www/azmednsk-test/tilda-migration/public')
REL=Path('uslugi/orthodontics/index.html')
def sha(b):return hashlib.sha256(b).hexdigest()
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def transform(data):
 require(sha(data) in (BEFORE,AFTER),'Source changed; no writes')
 if sha(data)==AFTER:return data
 s=data.decode('utf-8');require(s.count(OLD)==1,'Booking dialog mismatch')
 new=s.replace(OLD,NEW).encode('utf-8');require(sha(new)==AFTER,'Output mismatch');return new
def atomic(path,data,st):
 fd,tmp=tempfile.mkstemp(prefix='.az-ortho-',dir=path.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chown(tmp,st.st_uid,st.st_gid);os.chmod(tmp,st.st_mode&0o7777);os.replace(tmp,path)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def verify(data):
 r=subprocess.run(['curl','--noproxy','*','--resolve','azmednsk.ru:443:127.0.0.1','--connect-timeout','5','--max-time','25','-fsS','-H','Cache-Control: no-cache','https://azmednsk.ru/uslugi/orthodontics/'],capture_output=True)
 require(r.returncode==0 and r.stdout==data,'HTTPS content mismatch')
def main():
 ap=argparse.ArgumentParser();g=ap.add_mutually_exclusive_group(required=True);g.add_argument('--check',action='store_true');g.add_argument('--apply',action='store_true');a=ap.parse_args()
 require(socket.gethostname()=='az-server' and os.geteuid()==0,'Run as root on az-server')
 require(ROOT.is_dir(),'Missing public directory');target=ROOT/REL
 require(not any(x.is_symlink() for x in [target,*target.parents]),'Symlink not allowed')
 with open('/run/lock/azmednsk-critical.lock','w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  old=target.read_bytes();new=transform(old)
  for asset in ['site.js','assets/az-metrika-20261025.js']:require((ROOT/asset).is_file(),'Missing '+asset)
  if a.check:print('PREFLIGHT=PASS; LIVE_WRITES=NONE');return
  if old==new:verify(new);print('ALREADY_INSTALLED=PASS; HTTPS=PASS');return
  st=target.stat();backup=Path('/var/backups/azmednsk')/('ortho-booking-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
  backup.mkdir(parents=True,mode=0o700);shutil.copy2(target,backup/'index.html');require((backup/'index.html').read_bytes()==old,'Backup mismatch')
  require(target.read_bytes()==old,'Concurrent change')
  try:
   atomic(target,new,st);verify(new)
  except BaseException:
   require(target.read_bytes()==new,'Concurrent change blocks rollback; backup='+str(backup))
   atomic(target,old,st);os.utime(target,ns=(st.st_atime_ns,st.st_mtime_ns));print('ROLLBACK=PASS; BACKUP='+str(backup));raise
  print('ORTHODONT_BOOKING=PASS; HTML_UPDATED=1; HTTPS=PASS')
  print('ZUBACHEV=203140; FILATOVA=1316218; CHILDREN_AND_ADULTS=PASS')
  print('OTHER_CARDS_PRICES_PHOTOS_METRIKA_NGINX=UNCHANGED')
  print('BACKUP='+str(backup))
if __name__=='__main__':
 try:main()
 except Exception as e:print('STOP: '+str(e),file=sys.stderr);sys.exit(1)
