from pathlib import Path
from bs4 import BeautifulSoup
import importlib.util,tempfile,shutil,urllib.parse,xml.etree.ElementTree as E
B=Path(__file__).parent;spec=importlib.util.spec_from_file_location('deploy',B/'apply_parents_seo_20261028.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
assert 'children.html' not in m.M['files']
existing=set()
for loc in E.fromstring((B/'live/sitemap.xml').read_bytes()).iter():
 if loc.tag.endswith('loc'):
  path=loc.text.removeprefix('https://azmednsk.ru/').strip('/')
  existing.add((path+'/' if path else '')+'index.html')
assert all(n in existing for n,r in m.M['files'].items() if r['before'] and n.endswith('.html'))
with tempfile.TemporaryDirectory() as td:
 root=Path(td)/'site';root.mkdir();backup=Path(td)/'backups'
 for n,r in m.M['files'].items():
  if r['before']:
   p=root/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(B/'live'/n,p)
 m.prepare(root);m.apply(root,backup,lambda n,b:None);m.apply(root,backup,lambda n,b:None)
 for n,r in m.M['files'].items():
  if r['before']:shutil.copy2(B/'live'/n,root/n)
  else:(root/n).unlink()
 try:m.apply(root,backup,lambda n,b:(_ for _ in ()).throw(RuntimeError('SIMULATED_HTTP_FAILURE')))
 except RuntimeError as e:assert str(e)=='SIMULATED_HTTP_FAILURE'
 for n,r in m.M['files'].items():assert m.sha(m.read(root/n))==r['before'],n
 (root/'index.html').write_text('unexpected')
 try:m.prepare(root);raise AssertionError('Guard failed')
 except RuntimeError:pass
base=BeautifulSoup((B/'live/detyam/index.html').read_text(),'html.parser')
allfiles=set(m.M['files'])|{p.relative_to(B/'live').as_posix() for p in (B/'live').rglob('*') if p.is_file()}
canon=[]
for p in (B/'out').rglob('*.html'):
 n=p.relative_to(B/'out').as_posix();s=BeautifulSoup(p.read_text(),'html.parser')
 old=BeautifulSoup((B/'live'/n).read_text(),'html.parser') if (B/'live'/n).exists() else base
 assert [str(x) for x in s.select('script')]==[str(x) for x in old.select('script')],n
 assert str(s.select_one('#booking'))==str(old.select_one('#booking')),n
 for a in s.select('a[href="/detyam/"]'):assert a.text.strip()!='Детям',n
 if n.startswith('detyam/'):
  assert len(s.select('h1'))==1,n
  canonical=s.select_one('link[rel="canonical"]')['href'];canon.append(canonical)
  assert canonical=='https://azmednsk.ru/'+n.removesuffix('index.html'),n
  ids=[a['id'] for a in s.select('[id]')];assert len(ids)==len(set(ids)),n
  for a in s.select('a[href^="#"]'):assert a['href']=='#' or s.find(id=a['href'][1:]),(n,a)
  for a in s.select('a[href^="/detyam/"]'):
   target=a['href'].split('#')[0].strip('/')+'/index.html';assert target in allfiles,target
  if n!='detyam/index.html':assert len(s.select('.kt-person'))>=2,n
assert len(canon)==8 and len(set(canon))==8
E.fromstring((B/'out/sitemap.xml').read_bytes())
print('PASS: 8 children pages; navigation; internal links; canonical; booking and tracking unchanged; guarded apply; idempotency; simulated-failure rollback')
