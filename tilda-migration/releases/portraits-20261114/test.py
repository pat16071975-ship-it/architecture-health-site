import importlib.util,json,shutil,tempfile
from pathlib import Path
r=Path('/tmp/portrait-update');spec=importlib.util.spec_from_file_location('installer',r/'apply_random_portraits.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as t:
 root=Path(t)/'site';shutil.copytree('/tmp/site-photos/pages',root);(root/'team-2').mkdir();(root/'team-2/index.html').write_text('preview guard')
 before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
 target=root/'team/zubachev-roman/index.html';original=target.read_bytes();target.write_bytes(original+b' ')
 try:m.apply(root,Path(t)/'backups',False);raise AssertionError('missed mismatch')
 except AssertionError as e:assert 'page changed' in str(e)
 assert not (root/'assets/portraits-20261114-v1').exists()
 target.write_bytes(original);m.apply(root,Path(t)/'backups',False);m.apply(root,Path(t)/'backups',False)
 payload=json.loads((r/'payload.json').read_text());changed=set(payload['changes'])
 for rel,b in before.items():
  if rel not in changed:assert (root/rel).read_bytes()==b,rel
 for rel,item in payload['changes'].items():assert m.sha((root/rel).read_bytes())==item['after']
 print('TEST=PASS; wrong-base stops before writes; 64 pages exact; untouched pages preserved; rerun safe')
