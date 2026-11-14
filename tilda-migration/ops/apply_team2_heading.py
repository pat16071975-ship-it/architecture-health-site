from pathlib import Path
from datetime import datetime, timezone
import base64, hashlib, json, os, shutil, socket, tempfile
PAYLOAD={'assets': {}, 'changes': [['<div class="t2-together">', '<div class="t2-together"><h2 style="color:#329bd2;font-size:clamp(32px,5vw,64px);font-weight:700;line-height:1.15;text-align:center;margin:0 0 28px">МЫ КОМАНДА!!!</h2>']]}
BEFORE='108ec5eae34454b3d531172c6b3d1142b309cfd9fce746995f9275fc41a934f6'
def sha(b):return hashlib.sha256(b).hexdigest()
def check(ok,msg):
    if not ok:raise SystemExit('STOP='+msg)
def apply(root,backups):
    page=root/'team-2/index.html';main=root/'team/index.html';folder=root/'assets/team2-v1'
    for p in [root,root/'team-2',page,root/'team',main,root/'assets',folder]:check(p.exists() and not p.is_symlink(),'path '+str(p))
    before=page.read_bytes();original=main.read_bytes();text=before.decode()
    check(sha(before)==BEFORE,'team-2 has changed')
    assets={n:base64.b64decode(v['data'],validate=True) for n,v in PAYLOAD['assets'].items()}
    for n,b in assets.items():
        check(sha(b)==PAYLOAD['assets'][n]['sha'],'asset hash')
        check(not (folder/n).exists() and not (folder/n).is_symlink(),'asset already exists')
    for old,new in PAYLOAD['changes']:
        check(text.count(old)==1,'image reference mismatch');text=text.replace(old,new,1)
    after=text.encode()
    check(text.count('<article class="person"')==16,'card count')
    print('PREFLIGHT=PASS; CHANGE=TEAM_HEADING_ONLY')
    backup=backups/('team2-title-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True,exist_ok=False);shutil.copy2(page,backup/'index.html')
    check((backup/'index.html').read_bytes()==before,'backup')
    created=[];tmp=None
    try:
        for n,b in assets.items():
            p=folder/n
            with p.open('xb') as f:f.write(b)
            created.append(p);p.chmod(0o644)
        fd,name=tempfile.mkstemp(prefix='.lab-photos-',dir=page.parent);tmp=Path(name)
        with os.fdopen(fd,'wb') as f:f.write(after)
        shutil.copystat(page,tmp)
        check(page.read_bytes()==before,'concurrent page change')
        check(main.read_bytes()==original,'main page changed')
        os.replace(tmp,page);tmp=None
        check(page.read_bytes()==after,'page readback')
        check(all((folder/n).read_bytes()==b for n,b in assets.items()),'assets readback')
    except BaseException:
        if page.read_bytes()==after:shutil.copy2(backup/'index.html',page)
        for p in created:p.unlink(missing_ok=True)
        raise
    finally:
        if tmp:tmp.unlink(missing_ok=True)
    check(main.read_bytes()==original,'main unchanged')
    print('TEAM2_HEADING=PASS; MAIN_TEAM=UNCHANGED')
    print('BACKUP='+str(backup));print('URL=https://azmednsk.ru/team-2/#administrators')
if __name__=='__main__':
    check(socket.gethostname()=='vdska' and os.geteuid()==0,'server/user')
    apply(Path('/var/www/azmednsk-preview'),Path('/var/backups/azmednsk'))
