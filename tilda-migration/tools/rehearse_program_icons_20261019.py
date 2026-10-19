"""Isolated installer rehearsal; HTTPS callbacks are simulated, never production."""
import argparse,contextlib,hashlib,importlib.util,io,json,shutil,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--fixtures',type=Path,required=True);args=p.parse_args()
repo=Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('apply_program_icons_20261019.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
results=[]
def snapshot(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
def fixture(td):
 root=Path(td)/'public';root.mkdir()
 for name in set(m.MANIFEST['files'])|set(m.MANIFEST['protected']):
  src=args.fixtures/'verified-besedina/public'/name
  dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
 for name in m.PHOTOS:
  dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(('legacy photo fixture '+name).encode())
 return root,Path(td)/'backups'
for mode in ['success','file-drift','protected-drift','parent-symlink-drift','https-failure','redirect-failure']:
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
  results.append(mode+':PASS')
# Git source has historical differences from live; apply/reverse precisely there as well.
for name,rule in m.MANIFEST['files'].items():
 old=subprocess.check_output(['git','show','012e11294c04597a8b2a95412dba4c57b637a45c:tilda-migration/public/'+name],cwd=repo).decode()
 new=(repo/'tilda-migration/public'/name).read_text();restored=new
 for a,b,n in reversed(rule['replacements']):assert restored.count(b)==n;restored=restored.replace(b,a)
 assert restored==old
results.append('source-and-live-content-preservation:PASS')
from lxml import html
for name,rule in m.MANIFEST['files'].items():
 before=(args.fixtures/'verified-besedina/public'/name).read_text();after=before
 for a,b,n in rule['replacements']:after=after.replace(a,b)
 old=html.fromstring(before);new=html.fromstring(after)
 assert old.xpath('//a/@href')==new.xpath('//a/@href')
 assert old.xpath('//img/@src')==new.xpath('//img/@src')
 assert ' '.join(old.xpath('//body')[0].text_content().split())==' '.join(new.xpath('//body')[0].text_content().split())
 icons=new.xpath('//figure[@class="az-program-step-icon"]');assert len(icons)==4
 for e in icons:
  assert 'width:64px;' in e.get('style') and 'height:64px;' in e.get('style')
  im=e.xpath('./img')[0];assert im.get('width')==im.get('height')=='64' and 'object-fit:contain' in im.get('style')
results.append('all-12-icons-bounded-and-content-links-image-order-preserved:PASS')
report={'checks':results,'scope':{'changed_html':3,'icons':12,'icon_size_px':64,'protected':len(m.MANIFEST['protected']),'redirects':9},'limitations':['HTTPS callbacks are simulated here; installer checks real server HTTPS.','Four runtime-only legacy photos use fixture bytes here; installer snapshots actual server bytes.']}
(repo/'tilda-migration/ops/program_icons_20261019_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
