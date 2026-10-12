#!/usr/bin/env python3
"""Exercise deployment against a downloaded post-PR82 live snapshot."""
import argparse,contextlib,importlib.util,io,tempfile,shutil
from pathlib import Path
spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('apply_css_version_20261012.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
p=argparse.ArgumentParser();p.add_argument('snapshot',type=Path);a=p.parse_args()
with tempfile.TemporaryDirectory() as td:
 root=Path(td)/'public';shutil.copytree(a.snapshot,root)
 for name in m.PHOTOS:
  f=root/name;f.parent.mkdir(parents=True,exist_ok=True)
  if not f.exists():f.write_bytes(b'photo preservation test fixture '+name.encode())
 before={f.relative_to(root).as_posix():f.read_bytes() for f in root.rglob('*') if f.is_file()}
 checked=[]
 def verify(name,expected):
  if name.startswith('layouts.css?'):assert m.sha(expected)==m.MANIFEST['protected']['layouts.css']
  else:assert m.sha(expected)==m.MANIFEST['files'][name]['after']
  checked.append(name)
 with contextlib.redirect_stdout(io.StringIO()):
  m.apply(root,Path(td)/'backup',verify)
  m.apply(root,Path(td)/'backup',verify)
 assert len(checked)==152
 for name,old in before.items():
  now=(root/name).read_bytes()
  if name in m.MANIFEST['files']:
   assert now.replace(b'/layouts.css?v=66d783981822',b'/layouts.css')==old
  else:assert now==old
 # Restore and force an HTTPS failure after writes: every byte must roll back.
 for name,old in before.items():(root/name).write_bytes(old)
 def fail(name,expected):
  if name=='index.html':raise RuntimeError('Simulated HTTPS failure')
 with contextlib.redirect_stdout(io.StringIO()):
  try:m.apply(root,Path(td)/'rollback',fail)
  except RuntimeError as e:assert str(e)=='Simulated HTTPS failure'
  else:raise AssertionError('Failure was not propagated')
 assert all((root/n).read_bytes()==b for n,b in before.items())
 (root/'index.html').write_bytes(before['index.html']+b'concurrent edit')
 try:m.prepare(root)
 except RuntimeError:pass
 else:raise AssertionError('Source drift was accepted')
print('PASS: 75 exact URL changes; protected bytes; idempotence; HTTPS-failure rollback; drift rejection')
