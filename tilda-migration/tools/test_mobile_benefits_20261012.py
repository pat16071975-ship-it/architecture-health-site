#!/usr/bin/env python3
import contextlib,importlib.util,io,sys,tempfile,shutil,re,xml.etree.ElementTree as E
from pathlib import Path
spec=importlib.util.spec_from_file_location('m',Path(__file__).with_name('apply_mobile_benefits_20261012.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as td:
 root=Path(td)/'public';shutil.copytree(sys.argv[1],root);(root/'index.html').write_bytes(Path(sys.argv[2]).read_bytes())
 for n in m.PHOTOS:
  p=root/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'preservation fixture')
 old={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
 def verify(n,b):assert m.sha(b)==m.MANIFEST['files'][n]['after']
 with contextlib.redirect_stdout(io.StringIO()):
  m.apply(root,Path(td)/'backup',verify);m.apply(root,Path(td)/'backup',verify)
 html=(root/'index.html').read_text()
 assert html.count('class="az-home-benefit"')==4
 assert len(re.findall(r'id="visual-2488705671-17846414728200000\d+"',html))==12
 # Existing content nodes and attributes must be byte-for-byte identical, except the header logo source.
 def normalize(s):
  s=re.sub(r'<style id="az-home-mobile-benefits-20261012">.*?</style>\n','',s,flags=re.S)
  s=s.replace('/assets/az-logo-black-clean-20261012.svg','/assets/c3372b2cd448410e4453.svg')
  return sorted(re.findall(r'<div id="visual-[^"]+".*?</div></div>',s))
 assert normalize(html)==normalize(old['index.html'].decode())
 for n,b in old.items():
  if n!='index.html':assert (root/n).read_bytes()==b
 for n in m.MANIFEST['files']:
  if n.endswith('.svg'):
   tree=E.fromstring((root/n).read_bytes());assert not any(x.tag.endswith('mask') for x in tree.iter())
   assert not any(x.tag.endswith('line') and x.get('y1')=='627.5' for x in tree.iter())
   (root/n).unlink()
 (root/'index.html').write_bytes(old['index.html'])
 def fail(n,b):raise RuntimeError('simulated HTTPS failure')
 with contextlib.redirect_stdout(io.StringIO()):
  try:m.apply(root,Path(td)/'rollback',fail)
  except RuntimeError:pass
  else:raise AssertionError('Expected rollback')
 assert {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}==old
 (root/'index.html').write_bytes(old['index.html']+b'drift')
 try:m.prepare(root)
 except RuntimeError:pass
 else:raise AssertionError('Expected drift rejection')
for w in range(320,640):
 grid=min(w-32,520);cell=(grid-40-16)/2;assert cell>=116
 photo_bottom=502 if w<480 else 556;grid_top=522 if w<480 else 576
 assert grid_top-photo_bottom==20
print('PASS: four complete benefit items; existing nodes preserved; clean SVG; apply/repeat/rollback/drift; mobile gap20px and two columns320–639px')
print('Rendered browser mobile QA remains pending.')
