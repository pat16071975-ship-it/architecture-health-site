from pathlib import Path
from datetime import datetime, timezone
import hashlib, io, json, os, re, shutil, socket, sys, tempfile, urllib.request, zipfile
SOURCE_SHA='25c6ff6c24d4489da1cbe114e416c1f15fcf665fa08c81dc97b460aaba1f8cbc'
BUNDLE_SHA='ae189ef9ccda291aa0e4974534174238e8ff625031cc4e289dbb16717012001f'
def sha(data): return hashlib.sha256(data).hexdigest()
def check(ok,message):
    if not ok: raise SystemExit('STOP='+message)
def install(root,backup_root,data):
    check(sha(data)==BUNDLE_SHA,'bundle hash')
    check(root.is_dir() and not root.is_symlink(),'site root')
    for p in [root/'team',root/'team/index.html',root/'assets']:
        check(p.exists() and not p.is_symlink(),'source path '+str(p))
    source=root/'team/index.html'; before=source.read_bytes()
    check(sha(before)==SOURCE_SHA,'main team changed; recheck before installation')
    page=root/'team-2'; assets=root/'assets/team2-v1'
    check(not page.exists() and not page.is_symlink(),'team-2 already exists')
    check(not assets.exists() and not assets.is_symlink(),'team2-v1 assets already exist')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names=z.namelist();check(len(names)==len(set(names)),'duplicate entries')
        check(sum(i.file_size for i in z.infolist())<10000000,'unpacked size')
        check(all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in names),'archive paths')
        manifest=json.loads(z.read('manifest.json'))
        check(set(names)==set(manifest)|{'manifest.json'},'manifest coverage')
        check(all(n=='team-2/index.html' or n=='photo-map.json' or (n.startswith('assets/') and len(Path(n).parts)==2) for n in manifest),'allowed files')
        content={n:z.read(n) for n in manifest}
        check(all(sha(content[n])==v for n,v in manifest.items()),'file hashes')
    check(b'noindex,nofollow,noarchive' in content['team-2/index.html'],'noindex')
    check(content['team-2/index.html'].count(b'<article class="person"')==16,'16 cards')
    print('PREFLIGHT=PASS; MAIN_TEAM_MATCH=PASS; ASSET_HASHES=PASS')
    backup=backup_root/('team2-preview-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True,exist_ok=False)
    (backup/'team-before.html').write_bytes(before)
    (backup/'created-paths.json').write_text(json.dumps([str(page),str(assets)]))
    check((backup/'team-before.html').read_bytes()==before,'backup verification')
    stage=Path(tempfile.mkdtemp(prefix='.team2-stage-',dir=root)); published_assets=False;published_page=False
    try:
        (stage/'assets').mkdir();(stage/'page').mkdir()
        for n,b in content.items():
            if n.startswith('assets/'):(stage/'assets'/Path(n).name).write_bytes(b)
        (stage/'page/index.html').write_bytes(content['team-2/index.html'])
        for p in stage.rglob('*'):p.chmod(0o755 if p.is_dir() else 0o644)
        check(source.read_bytes()==before,'concurrent main team change')
        check(not page.exists() and not page.is_symlink() and not assets.exists() and not assets.is_symlink(),'concurrent preview creation')
        os.rename(stage/'assets',assets);published_assets=True
        os.rename(stage/'page',page);published_page=True
        check(sha((page/'index.html').read_bytes())==manifest['team-2/index.html'],'installed page')
        check(all(sha((assets/Path(n).name).read_bytes())==manifest[n] for n in manifest if n.startswith('assets/')),'installed assets')
        check(source.read_bytes()==before,'main team unchanged')
    except BaseException:
        if published_page:shutil.rmtree(page)
        if published_assets:shutil.rmtree(assets)
        raise
    finally:shutil.rmtree(stage)
    print('TEAM2_PREVIEW=PASS; CARDS=16; MAIN_TEAM=UNCHANGED; NOINDEX=PASS')
    print('BACKUP='+str(backup));print('URL=https://azmednsk.ru/team-2/')
if __name__=='__main__':
    check(socket.gethostname()=='vdska' and os.geteuid()==0,'server/user')
    check(len(sys.argv)==2 and re.fullmatch('[0-9a-f]{40}',sys.argv[1]),'exact commit argument')
    url='https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/'+sys.argv[1]+'/tilda-migration/previews/team2-v1.zip'
    data=urllib.request.urlopen(url,timeout=90).read(10000000)
    install(Path('/var/www/azmednsk-preview'),Path('/var/backups/azmednsk'),data)
