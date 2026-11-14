from pathlib import Path
from datetime import datetime,timezone
import hashlib,os,shutil,socket,tempfile,re
ROOT=Path('/var/www/azmednsk-preview')
BEFORE='93a5390b094522f7cea38048b12a714a2b42da9b4406741dacc24c6250073655'
COPY='''<div class="t2-osteopathy-copy"><h3>Остеопатия: внимание к движению</h3><p>Остеопатия — это работа руками с мышцами, суставами и мягкими тканями. Врач оценивает, как движется тело, где есть напряжение и ограничения подвижности и как они могут быть связаны с жалобами пациента.</p><p>Приём начинается с беседы и осмотра. Затем специалист подбирает техники с учётом состояния здоровья и противопоказаний. Цель работы — уменьшить боль и помочь двигаться свободнее. При необходимости врач рекомендует дополнительные обследования или консультацию другого специалиста.</p></div>'''
STYLE='''<style id="az-osteopathy-copy-v1">.t2 .t2-osteopathy-copy{grid-column:1/-1;margin-top:10px;color:#35586c;font-size:16px;line-height:1.75}.t2 .t2-osteopathy-copy h3{font-size:24px;line-height:1.3;color:#2080c4;margin:0 0 16px}.t2 .t2-osteopathy-copy p{margin:0 0 14px}.t2.searching .t2-osteopathy-copy{display:none}@media(max-width:700px){.t2 .t2-osteopathy-copy{font-size:15px;margin-top:0}.t2 .t2-osteopathy-copy h3{font-size:21px}}</style>'''
def digest(b):return hashlib.sha256(b).hexdigest()
def transform(old):
 s=old.decode();assert digest(old)==BEFORE,'STOP: team changed'
 start=s.index('<section class="t2-section" id="osteopathy">');end=s.index('</section>',start)
 section=s[start:end];needle='</article></div><figure class="t2-photo '
 assert section.count(needle)==1
 section=section.replace(needle,'</article>'+COPY+'</div><figure class="t2-photo ',1)
 new=s[:start]+section+s[end:];assert new.count('</head>')==1
 new=new.replace('</head>',STYLE+'</head>',1)
 assert re.findall(r'<img\b[^>]*>',s)==re.findall(r'<img\b[^>]*>',new)
 return new.encode()
def main():
 assert socket.gethostname()=='vdska' and os.geteuid()==0,'STOP: host/user'
 p=ROOT/'team/index.html';assert not p.is_symlink()
 old=p.read_bytes()
 if b'az-osteopathy-copy-v1' in old:print('ALREADY_APPLIED=YES');return
 new=transform(old);preview=(ROOT/'team-2/index.html').read_bytes()
 print('PREFLIGHT=PASS; CHANGE=OSTEOPATHY_TEXT_ONLY')
 backup=Path('/var/backups/azmednsk')/('team-osteopathy-text-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True)
 shutil.copy2(p,backup/'index.html');assert (backup/'index.html').read_bytes()==old
 fd,tmp=tempfile.mkstemp(dir=p.parent,prefix='.az-text-')
 try:
  with os.fdopen(fd,'wb') as f:f.write(new);f.flush();os.fsync(f.fileno())
  shutil.copystat(p,tmp);st=p.stat();os.chown(tmp,st.st_uid,st.st_gid)
  assert p.read_bytes()==old,'STOP: concurrent edit';os.replace(tmp,p)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
 assert p.read_bytes()==new and (ROOT/'team-2/index.html').read_bytes()==preview
 print('TEAM_OSTEOPATHY_TEXT=PASS; PHOTOS_AND_CARDS=UNCHANGED; PREVIEW=UNCHANGED')
 print('BACKUP='+str(backup));print('URL=https://azmednsk.ru/team/#osteopathy')
if __name__=='__main__':
 try:main()
 except Exception as e:raise SystemExit(str(e))
