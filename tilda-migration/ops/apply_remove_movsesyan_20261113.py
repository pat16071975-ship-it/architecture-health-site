#!/usr/bin/env python3
"""Remove the departed specialist from azmednsk; reversible guarded installation."""
import re,os,sys,json,shutil,datetime,socket,hashlib
from pathlib import Path
TOKEN=re.compile(r'мовсесян|мовсепян|movseyan|movsesyan|1183045',re.I)
PROFILE='team/movseyan-babken'
ROOT=Path('/var/www/azmednsk-preview')
TEXT={'.html','.htm','.xml','.js','.json','.txt','.css','.svg'}
def transform(s,suffix):
 if suffix in ('.html','.htm'):
  for tag in ('article','li'):
   s=re.sub(r'<'+tag+r'\b[^>]*>.*?</'+tag+r'\s*>',lambda m:'' if TOKEN.search(m[0]) else m[0],s,flags=re.S|re.I)
 elif suffix=='.xml':
  s=re.sub(r'<url\b[^>]*>.*?</url\s*>',lambda m:'' if TOKEN.search(m[0]) else m[0],s,flags=re.S|re.I)
 return s

def plan(root):
 changes={};deletes=[];unknown=[]
 for f in sorted(root.rglob('*')):
  if not f.is_file():continue
  rel=f.relative_to(root).as_posix()
  if f.is_symlink():
   if PROFILE in rel:raise RuntimeError('Symlink in target: '+rel)
   continue
  if rel.startswith(PROFILE+'/') or rel==PROFILE+'.html':deletes.append(f);continue
  if f.suffix.lower() not in TEXT:continue
  try:s=f.read_text(encoding='utf-8')
  except UnicodeDecodeError:continue
  if not TOKEN.search(s):continue
  new=transform(s,f.suffix.lower())
  if TOKEN.search(new):unknown.append(rel)
  elif new!=s:changes[f]=new.encode()
 if unknown:raise RuntimeError('Unrecognized references; no files changed: '+', '.join(unknown))
 return changes,deletes

def main():
 dry=len(sys.argv)==3 and sys.argv[1]=='--check'
 root=Path(sys.argv[2]) if dry else ROOT
 if not dry:
  if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='vdska':raise RuntimeError('Run as root on vdska')
  if root.is_symlink() or not (root/'index.html').is_file() or 'azmednsk.ru' not in (root/'index.html').read_text():raise RuntimeError('Wrong site root')
 changes,deletes=plan(root)
 print(f'PREFLIGHT=PASS; FILES_TO_UPDATE={len(changes)}; FILES_TO_REMOVE={len(deletes)}',flush=True)
 for f in changes:print('UPDATE='+str(f.relative_to(root)))
 for f in deletes:print('REMOVE='+str(f.relative_to(root)))
 if dry:return
 if not changes and not deletes:print('REMOVE_MOVSESYAN=ALREADY_APPLIED');return
 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 backup=Path('/var/backups/azmednsk')/('remove-movsesyan-'+stamp);backup.mkdir(parents=True)
 targets=list(changes)+deletes
 manifest={}
 for f in targets:
  dest=backup/f.relative_to(root);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,dest)
  manifest[str(f.relative_to(root))]=hashlib.sha256(f.read_bytes()).hexdigest()
 (backup/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
 try:
  for f,data in changes.items():
   tmp=f.with_name(f.name+'.az-remove-tmp');tmp.write_bytes(data);shutil.copystat(f,tmp);os.chown(tmp,f.stat().st_uid,f.stat().st_gid);os.replace(tmp,f)
  for f in deletes:f.unlink()
  remaining_changes,remaining_deletes=plan(root)
  if remaining_changes or remaining_deletes:raise RuntimeError('Post-check failed')
 except Exception:
  for f in targets:shutil.copy2(backup/f.relative_to(root),f)
  print('ROLLBACK=PASS');raise
 standard=Path('/var/lib/azmednsk/standards/removed-movsesyan-20261113.txt');standard.parent.mkdir(parents=True,exist_ok=True)
 standard.write_text('Мовсесян Бабкен Ашотович удалён с сайта по поручению владельца. Не восстанавливать карточки, страницу team/movseyan-babken, ссылки и персональную запись employeeId=1183045 при последующих обновлениях.\n')
 print(f'REMOVE_MOVSESYAN=PASS; FILES_UPDATED={len(changes)}; FILES_REMOVED={len(deletes)}; REFERENCES=0; STANDARD=SAVED')
 print('BACKUP='+str(backup));print('URL=https://azmednsk.ru/team/');print('External HTTPS verification required.')
if __name__=='__main__':
 try:main()
 except Exception as e:print('STOP='+str(e));sys.exit(1)
