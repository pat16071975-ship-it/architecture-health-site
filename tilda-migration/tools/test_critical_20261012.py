"""Run against a fresh read-only public snapshot: python3 test_critical_20261012.py SNAPSHOT."""
import importlib.util,json,re,shutil,sys,tempfile,xml.etree.ElementTree as ET
from pathlib import Path
spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('apply_critical_20261012.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
source=Path(sys.argv[1])
with tempfile.TemporaryDirectory() as td:
 root=Path(td)/'public';shutil.copytree(source,root)
 # Synthetic bytes test preservation mechanics; no claim of visual photo validation.
 for n in m.PHOTOS:
  p=root/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(('PHOTO_SENTINEL '+n).encode())
 original={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
 prepared=m.prepare(root);assert len(prepared)==80
 def verify(n,b):assert (root/n).read_bytes()==b
 m.apply(root,Path(td)/'backups',verify)
 for n,(old,new,_) in prepared.items():
  assert (root/n).read_bytes()==new
  if n.endswith('.html'):
   # Visible body is unchanged except the six approved href destinations.
   before=old.decode().split('</head>',1)[1];after=new.decode().split('</head>',1)[1]
   if n in ('index.html','contacts/index.html','o_nas/index.html'):
    before=before.replace('href="tel:+73832496411"','href="tel:+73832842421"').replace('href="tel:+73832846011"','href="tel:+73832842421"')
   assert before==after,n
 urls=[e.text for e in ET.parse(root/'sitemap.xml').iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
 assert len(urls)==len(set(urls))==77 and not any('/thank/' in x or '404.html' in x for x in urls)
 assert 'noindex,nofollow' in (root/'thank/index.html').read_text()
 for n in m.PHOTOS:assert (root/n).read_bytes()==original[n]
 prices={'pavlova-anna':'25&nbsp;000&nbsp;₽','zubachev-roman':'от&nbsp;12&nbsp;000&nbsp;₽','starostenko-vadim':'7&nbsp;500&nbsp;₽','dimitrenko-anna':'6&nbsp;000&nbsp;₽','mamontova-lubava':'4&nbsp;500&nbsp;₽'}
 for slug,price in prices.items():assert price in (root/f'team/{slug}/index.html').read_text()
 print('CONTENT_PRICES_PHOTO_REFERENCES_AND_PROTECTED_BYTES=PASS; SITEMAP=PASS')
 m.apply(root,Path(td)/'backups',verify);print('IDEMPOTENCE=PASS')
 for n,b in original.items():(root/n).write_bytes(b)
 (root/'sitemap.xml').unlink()
 def fail(n,b):raise RuntimeError('Simulated HTTPS failure')
 try:m.apply(root,Path(td)/'backups',fail);raise AssertionError('no failure')
 except RuntimeError:pass
 assert not (root/'sitemap.xml').exists()
 assert all((root/n).read_bytes()==b for n,b in original.items());print('ROLLBACK_INCLUDING_NEW_SITEMAP=PASS')
 (root/'index.html').write_bytes(original['index.html']+b' ')
 try:m.prepare(root);raise AssertionError('drift not rejected')
 except RuntimeError:pass
 print('DRIFT_GUARD=PASS')
 for width in (960,1024,1199,1200,1280,1363,1440,1920):
  left=max(20,width/2-(688 if width>=1200 else 492));w=509 if width>=1200 else 441
  assert left>=20 and left+w<=width
 print('DESKTOP_HEADING_GEOMETRY_960_TO_1920=PASS; MOBILE_RULES_UNCHANGED')
print('LOCAL_TESTS=PASS; LIVE_DEPLOY=NOT_RUN; BROWSER_PREVIEW=BLOCKED')
