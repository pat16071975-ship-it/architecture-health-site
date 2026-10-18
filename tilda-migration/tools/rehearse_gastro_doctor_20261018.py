"""Isolated installer rehearsal; HTTPS callbacks are simulated, never production."""
import argparse,contextlib,hashlib,importlib.util,io,json,shutil,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--fixtures',type=Path,required=True);args=p.parse_args()
repo=Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('apply_gastro_doctor_20261018.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
results=[]
def snapshot(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
def fixture(td):
 root=Path(td)/'public';root.mkdir()
 for name in set(m.MANIFEST['files'])|set(m.MANIFEST['protected']):
  src=args.fixtures/'verified-gastro-before/public'/name
  if name=='assets/a77a625fc72b6b568fd8.png':src=args.fixtures/'gastro-lyubaeva-photo.png'
  dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
 for name in m.PHOTOS:
  dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(('legacy photo fixture '+name).encode())
 return root,Path(td)/'backups'
for mode in ['success','file-drift','protected-drift','https-failure','redirect-failure']:
 with tempfile.TemporaryDirectory() as td:
  root,backups=fixture(td);target=next(iter(m.MANIFEST['files']))
  if mode=='file-drift':(root/target).write_text('unexpected change')
  if mode=='protected-drift':(root/'index.html').write_text('unexpected change')
  before=snapshot(root);calls=[]
  def verify(name,data):
   calls.append(name)
   digest=m.MANIFEST['files'].get(name,{}).get('after') or m.MANIFEST['protected'][name]
   assert m.sha(data)==digest
   if mode=='https-failure':raise RuntimeError('simulated HTTPS failure')
  def redirect(path):
   if mode=='redirect-failure':raise RuntimeError('simulated redirect failure')
  with contextlib.redirect_stdout(io.StringIO()):
   try:m.apply(root,backups,verify,redirect)
   except RuntimeError:
    assert mode!='success';assert snapshot(root)==before
    if mode.endswith('drift'):assert not backups.exists() and not calls
   else:
    assert mode=='success'
    assert set(calls)==set(m.MANIFEST['files'])|set(m.MANIFEST['public_checks'])
    after=snapshot(root);assert {n for n in after if after[n]!=before[n]}=={target}
    times={n:(root/n).stat().st_mtime_ns for n in after};m.apply(root,backups,verify,redirect)
    assert snapshot(root)==after and len(list(backups.iterdir()))==1
    assert all((root/n).stat().st_mtime_ns==v for n,v in times.items())
    rule=m.MANIFEST['files'][target];text=(root/target).read_text()
    for a,b,n in reversed(rule['replacements']):assert text.count(b)==n;text=text.replace(b,a)
    assert m.sha(text.encode())==rule['before']
  results.append(mode+':PASS')
# Git source has historical differences from live; apply/reverse precisely there as well.
for name,rule in m.MANIFEST['files'].items():
 old=subprocess.check_output(['git','show','fffdb07c2599b4280deb9eafebca1493c216eddb:tilda-migration/public/'+name],cwd=repo).decode()
 new=(repo/'tilda-migration/public'/name).read_text();restored=new
 for a,b,n in reversed(rule['replacements']):assert restored.count(b)==n;restored=restored.replace(b,a)
 assert restored==old
results.append('source-and-live-content-preservation:PASS')
report={'checks':results,'scope':{'changed_html':1,'protected':len(m.MANIFEST['protected']),'redirects':9},'limitations':['HTTPS callbacks are simulated here; installer checks real server HTTPS.','Four runtime-only legacy photos use fixture bytes here; installer snapshots actual server bytes.']}
(repo/'tilda-migration/ops/gastro_doctor_20261018_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
