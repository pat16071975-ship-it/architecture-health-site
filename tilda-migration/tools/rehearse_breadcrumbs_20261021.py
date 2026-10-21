"""Isolated installer rehearsal; HTTPS callbacks are simulated, never production."""
import argparse,contextlib,hashlib,importlib.util,io,json,shutil,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--fixtures',type=Path,required=True);args=p.parse_args()
repo=Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('apply_breadcrumbs_20261021.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
results=[]
def snapshot(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
def fixture(td):
 root=Path(td)/'public';root.mkdir()
 for name in set(m.MANIFEST['files'])|set(m.MANIFEST['protected']):
  src=args.fixtures/name
  dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
 for name in m.PHOTOS:
  dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True);
  if not dst.exists():dst.write_bytes(('legacy photo fixture '+name).encode())
 return root,Path(td)/'backups'
for mode in ['success','file-drift','protected-drift','parent-symlink-drift','https-failure','redirect-failure','partial-write']:
 with tempfile.TemporaryDirectory() as td:
  root,backups=fixture(td);target=next(iter(m.MANIFEST['files']))
  if mode=='file-drift':(root/target).write_text('unexpected change')
  if mode=='protected-drift':(root/'site.css').write_text('unexpected change')
  if mode=='parent-symlink-drift':
   parent=(root/target).parent;moved=parent.with_name(parent.name+'-real');parent.rename(moved);parent.symlink_to(moved,target_is_directory=True)
  before=snapshot(root);calls=[]
  def verify(name,data):
   calls.append(name)
   digest=m.MANIFEST['files'].get(name,{}).get('after') or m.MANIFEST['protected'][name]
   assert m.sha(data)==digest
   if mode=='https-failure':raise RuntimeError('simulated HTTPS failure')
  def redirect(path):
   if mode=='redirect-failure':raise RuntimeError('simulated redirect failure')
  real_atomic=m.atomic;count=[0]
  def atomic(*args):
   count[0]+=1
   if mode=='partial-write' and count[0]==4:raise RuntimeError('simulated write failure')
   return real_atomic(*args)
  m.atomic=atomic
  with contextlib.redirect_stdout(io.StringIO()):
   try:m.apply(root,backups,verify,redirect)
   except RuntimeError:
    assert mode!='success';assert snapshot(root)==before
    if mode.endswith('drift'):assert not backups.exists() and not calls
   else:
    assert mode=='success'
    assert set(calls)==set(m.MANIFEST['files'])|set(m.MANIFEST['public_checks'])
    after=snapshot(root);assert {n for n in after if after[n]!=before[n]}==set(m.MANIFEST['files'])
    times={n:(root/n).stat().st_mtime_ns for n in after};m.apply(root,backups,verify,redirect)
    assert snapshot(root)==after and len(list(backups.iterdir()))==1
    assert all((root/n).stat().st_mtime_ns==v for n,v in times.items())
    rule=m.MANIFEST['files'][target];text=(root/target).read_text()
    for a,b,n in reversed(rule['replacements']):assert text.count(b)==n;text=text.replace(b,a)
    assert m.sha(text.encode())==rule['before']
  m.atomic=real_atomic
  results.append(mode+':PASS')

report={'checks':results,'limitations':['HTTPS callbacks simulated; real server HTTPS verified by installer.','Runtime photos use fixture placeholders.']}
(repo/'tilda-migration/ops/breadcrumbs_20261021_installer_validation.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
