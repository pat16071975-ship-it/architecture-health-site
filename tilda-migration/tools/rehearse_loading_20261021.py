"""Exercise the real installer against downloaded live fixtures; HTTP is simulated."""
import argparse,contextlib,importlib.util,io,json,shutil,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--assets',type=Path,required=True);a=p.parse_args()
spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('apply_loading_20261021.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def snapshot(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
checks=[]
for mode in ['success','file-drift','protected-drift','parent-symlink','asset-drift','bad-download','https-failure','redirect-failure','partial-write']:
 with tempfile.TemporaryDirectory() as td:
  root=Path(td)/'public';shutil.copytree(a.baseline,root);backups=Path(td)/'backups'
  for n in m.PHOTOS:
   p=root/n
   if not p.exists():p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'runtime-photo-fixture')
  target=next(iter(m.MANIFEST['files']));asset=next(iter(m.MANIFEST['assets']))
  if mode=='file-drift':(root/target).write_text('drift')
  if mode=='protected-drift':(root/'site.css').write_text('drift')
  if mode=='asset-drift':
   p=root/asset;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'drift')
  if mode=='parent-symlink':
   p=root/'assets';p.rename(root/'assets-real');p.symlink_to(root/'assets-real',target_is_directory=True)
  before=snapshot(root);calls=[]
  def fetch(n,r):return b'bad' if mode=='bad-download' else (a.assets/n).read_bytes()
  def verify(n,b):
   calls.append(n)
   expected=m.MANIFEST['files'].get(n,{}).get('after') or m.MANIFEST['assets'].get(n,{}).get('sha256') or m.MANIFEST['protected'][n]
   assert m.sha(b)==expected
   if mode=='https-failure':raise RuntimeError('simulated HTTPS failure')
  def redirect(n):
   if mode=='redirect-failure':raise RuntimeError('simulated redirect failure')
  real_atomic=m.atomic;count=[0]
  def atomic(*args):
   count[0]+=1
   if mode=='partial-write' and count[0]==220:raise RuntimeError('simulated write failure')
   return real_atomic(*args)
  m.atomic=atomic
  with contextlib.redirect_stdout(io.StringIO()):
   try:m.apply(root,backups,verify,redirect,fetch)
   except RuntimeError:
    assert mode!='success',mode
    assert snapshot(root)==before,mode
    if mode in ['file-drift','protected-drift','parent-symlink','asset-drift','bad-download']:assert not backups.exists()
   else:
    assert mode=='success'
    after=snapshot(root)
    assert set(calls)==set(m.MANIFEST['files'])|set(m.MANIFEST['assets'])|set(m.MANIFEST['public_checks'])
    assert {n for n in after if before.get(n)!=after[n]}==set(m.MANIFEST['files'])|set(m.MANIFEST['assets'])
    times={n:(root/n).stat().st_mtime_ns for n in after}
    m.apply(root,backups,verify,redirect,fetch)
    assert snapshot(root)==after and len(list(backups.iterdir()))==1
    assert all((root/n).stat().st_mtime_ns==v for n,v in times.items())
  m.atomic=real_atomic;checks.append(mode+':PASS')
report={'checks':checks,'html':len(m.MANIFEST['files']),'assets':len(m.MANIFEST['assets']),'limitations':['HTTPS and downloads are injected local callbacks; real server HTTPS runs during installation.','Runtime-only photos use placeholders only when absent from baseline.']}
out=Path(__file__).parents[1]/'ops/loading_20261021_installer_validation.json';out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
