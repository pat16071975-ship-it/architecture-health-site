#!/usr/bin/env python3
"""Deployment safety checks using the captured live site; geometry checks are not visual QA."""
import importlib.util,contextlib,io,tempfile,shutil,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('m',Path(__file__).with_name('apply_hero_spacing_20261012.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as td:
 root=Path(td)/'public';shutil.copytree(sys.argv[1],root);(root/'index.html').write_bytes(Path(sys.argv[2]).read_bytes())
 for n in m.PHOTOS:
  p=root/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'preservation fixture '+n.encode())
 old=(root/'index.html').read_bytes()
 def verify(n,b):assert m.sha(b)==m.MANIFEST['files'][n]['after']
 with contextlib.redirect_stdout(io.StringIO()):
  m.apply(root,Path(td)/'backups',verify);m.apply(root,Path(td)/'backups',verify)
 new=(root/'index.html').read_bytes();a,b=m.MANIFEST['files']['index.html']['replacements'][0]
 assert new.replace(b.encode(),a.encode())==old
 (root/'index.html').write_bytes(old)
 def fail(n,b):raise RuntimeError('simulated HTTPS failure')
 with contextlib.redirect_stdout(io.StringIO()):
  try:m.apply(root,Path(td)/'rollback',fail)
  except RuntimeError:pass
  else:raise AssertionError('Expected rollback')
 assert (root/'index.html').read_bytes()==old
 (root/'index.html').write_bytes(old+b'concurrent edit')
 try:m.prepare(root)
 except RuntimeError:pass
 else:raise AssertionError('Expected drift rejection')
# Check screen edges, button separation and photo separation at all integer widths.
for w in range(320,2561):
 if w>=1200:
  x=max(20,w/2-688);dw=min(571,w/2-40);bw=200;bx=x+231;photo=w/2+4
 elif w>=960:
  x=max(20,w/2-492);dw=min(421,w/2-91);bw=min(200,(w/2-111)/2);bx=x+bw+20;photo=w/2-51
 elif w>=640:x=w/2-310;dw=583;bw=200;bx=x+231;photo=None
 elif w>=480:x=w/2-230;dw=457;bw=200;bx=x+231;photo=None
 else:x=w/2-150;dw=min(310,w/2+140);bw=177;bx=x;photo=None
 assert x>=10 and x+dw<=w-10,(w,'description')
 assert x>=10 and bx+bw<=w-10,(w,'buttons')
 if w>=480:assert bx>=x+bw+20,(w,'button overlap')
 if photo is not None:assert max(x+dw,bx+bw)<=photo-20,(w,'photo overlap')
print('PASS: one guarded HTML insertion, repeat apply, rollback, drift guard; horizontal geometry 320–2560px')
print('Mobile font sizes, vertical positions and stacked buttons unchanged; visual QA remains required after deployment.')
