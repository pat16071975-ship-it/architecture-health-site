"""Isolated rehearsal and exact content checks; never touches the server."""
import argparse,contextlib,importlib.util,io,json,shutil,subprocess,tempfile
from pathlib import Path
from lxml import html
p=argparse.ArgumentParser();p.add_argument('--fixtures',type=Path,required=True);args=p.parse_args()
repo=Path(__file__).resolve().parents[2];pub=repo/'tilda-migration/public';base=args.fixtures/'verified-lab-document-icons/public'
spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('apply_besedina_20261019.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
results=[]
def snap(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
def fixture(td):
 root=Path(td)/'public';root.mkdir()
 for name in set(m.MANIFEST['files'])|set(m.MANIFEST['protected']):
  src=base/name
  if not src.exists():continue
  dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
 for name in m.PHOTOS:
  dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(('legacy photo fixture '+name).encode())
 return root,Path(td)/'backups'
for mode in ['success','file-drift','protected-drift','new-file-drift','https-failure','redirect-failure','symlink-parent']:
 with tempfile.TemporaryDirectory() as td:
  root,backups=fixture(td)
  if mode=='file-drift':(root/'team/index.html').write_text('unexpected change')
  if mode=='protected-drift':(root/'lab/index.html').write_text('unexpected change')
  if mode=='new-file-drift':(root/'assets/az-besedina-approved-20261019.png').write_text('unexpected new file')
  if mode=='symlink-parent':(root/'team/besedina-maria').symlink_to(root/'contacts',target_is_directory=True)
  before=snap(root);calls=[]
  def verify(name,data):
   calls.append(name);digest=m.MANIFEST['files'].get(name,{}).get('after') or m.MANIFEST['protected'][name];assert m.sha(data)==digest
   if mode=='https-failure':raise RuntimeError('simulated HTTPS failure')
  def redirect(path):
   if mode=='redirect-failure':raise RuntimeError('simulated redirect failure')
  with contextlib.redirect_stdout(io.StringIO()):
   try:m.apply(root,backups,verify,redirect)
   except RuntimeError:
    assert mode!='success';assert snap(root)==before
    if mode.endswith('drift') or mode=='symlink-parent':assert not backups.exists() and not calls
    if mode.endswith('failure'):assert not (root/'team/besedina-maria').exists()
   else:
    assert mode=='success';after=snap(root)
    assert {n for n in after if after[n]!=before.get(n)}==set(m.MANIFEST['files'])
    assert set(calls)==set(m.MANIFEST['files'])|set(m.MANIFEST['public_checks'])
    times={n:(root/n).stat().st_mtime_ns for n in after};m.apply(root,backups,verify,redirect)
    assert snap(root)==after and len(list(backups.iterdir()))==1
    assert all((root/n).stat().st_mtime_ns==v for n,v in times.items())
    assert (root/'team/besedina-maria').stat().st_mode&0o777==0o755
  results.append(mode+':PASS')
# Every scoped edit reverses to the exact prior source/live bytes.
for path,rule in m.MANIFEST['files'].items():
 if 'replacements' not in rule:continue
 for source,new in [('live',m.prepare(base)[path][1]),('source',(pub/path).read_bytes())]:
  s=new.decode()
  for a,b,count in reversed(rule['replacements']):assert s.count(b)==count;s=s.replace(b,a)
  previous=(base/path).read_bytes() if source=='live' else subprocess.check_output(['git','show','58b3dab3cfdf3d5f7c25b51a9875622b260edad0:tilda-migration/public/'+path],cwd=repo)
  assert s.encode()==previous,(path,source)
results.append('exact-source-and-live-replacements:PASS')
prepared=m.prepare(base)
# Full approved photo bytes; no crop or recompression.
original=args.fixtures/'generated_images/exec-f6905528-994b-43e1-a12a-431723d66966.png'
assert prepared['assets/az-besedina-approved-20261019.png'][1]==original.read_bytes()==(pub/'assets/az-besedina-approved-20261019.png').read_bytes()
results.append('approved-photo-exact-bytes:PASS')
# One named identity, known role and experience, no predecessor biography/prices.
new=html.fromstring(prepared['team/besedina-maria/index.html'][1]);txt=' '.join(new.xpath('//main//text()'))
for term in ['Беседина','Мария Юрьевна','Врач-стоматолог-терапевт','Стаж 15 лет']:assert term in txt
for term in ['Румянцев','Павловна','14 лет','детский','Интернатура','Образование','Первичный приём','1 000','2 000','университет']:assert term not in txt,term
assert new.xpath('//link[@rel="canonical"]/@href')==['https://azmednsk.ru/team/besedina-maria/']
assert len(new.xpath('//h1'))==1
for script in new.xpath('//script[@type="application/ld+json"]/text()'):json.loads(script)
assert len(new.xpath('//img[contains(@src,"besedina")]'))==1
# Footer/header/contact shell remains exact vs the live predecessor.
s=prepared['team/besedina-maria/index.html'][1].decode();old=(base/'team/rumyantseva-maria/index.html').read_text()
assert s[s.index('<section id="layout-2489767221"'):]==old[old.index('<section id="layout-2489767221"'):]
assert s[s.index('<body'):s.index('<main>')]==old[old.index('<body'):old.index('<main>')]
results.append('profile-approved-facts-and-existing-shell:PASS')
for path in ['team/index.html','o_nas/index.html']:
 d=html.fromstring(prepared[path][1]);before=html.fromstring((base/path).read_bytes());newcards=d.xpath('//article[@class="doctor"]');oldcards=before.xpath('//article[@class="doctor"]');assert len(newcards)==len(oldcards)==17
 for a,b in zip(oldcards,newcards):
  at=html.tostring(a);bt=html.tostring(b)
  if b'rumyantseva' in at:
   assert b'besedina' in bt;assert a.get('data-parts')==b.get('data-parts');assert 'object-fit:contain' in b.xpath('.//img/@style')[0]
  else:assert at==bt
results.append('team-order-filters-other-16-specialists:PASS')
for p in base.rglob('*.html'):
 path=str(p.relative_to(base));data=prepared[path][1] if path in prepared else p.read_bytes()
 assert 'Румянцев' not in data.decode() and b'rumyantseva' not in data,(path,'stale identity')
results.append('no-stale-public-rumyantseva-content:PASS')
for p in ['kompleks-uslugi/arhitektura-zdorovya/index.html','kompleks-uslugi/korrekciya-vnimaniya-deti/index.html','kompleks-uslugi/zdorovaya-spina/index.html']:
 assert p not in m.MANIFEST['files'];assert m.sha((base/p).read_bytes())==m.MANIFEST['protected'][p]
results.append('all-12-program-icons-unchanged:PASS')
retired=html.fromstring(prepared['team/rumyantseva-maria/index.html'][1]);assert retired.xpath('//meta[@name="robots"]/@content')==['noindex,follow'];assert retired.xpath('//meta[@http-equiv="refresh"]/@content')==['0;url=/team/']
assert b'/team/rumyantseva-maria/' not in prepared['sitemap.xml'][1]
results.append('old-profile-retired-and-sitemap-replaced:PASS')
report={'checks':results,'scope':{'files':len(prepared),'html':6,'protected':len(m.MANIFEST['protected']),'old_redirects':9,'new_profile':1,'approved_photo':1},'limitations':['HTTPS callbacks simulated; installer performs real HTTPS checks on server.','Runtime-only four legacy photos represented by fixture bytes; actual originals snapshotted on server.','Live visual acceptance follows owner installation; no claim of a rendered predeployment browser preview.']}
(repo/'tilda-migration/ops/besedina_20261019_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False))
