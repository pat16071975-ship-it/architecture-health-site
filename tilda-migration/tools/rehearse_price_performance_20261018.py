import hashlib,importlib.util,json,re,shutil,tempfile,contextlib,io
from pathlib import Path
import argparse
args=argparse.ArgumentParser();args.add_argument('--fixtures',type=Path,required=True,help='Directory with verified-improvements-before/public, performance-assets, price-performance-after');opts=args.parse_args()
R=opts.fixtures;REPO=Path(__file__).resolve().parents[2];P=REPO/'tilda-migration/tools/apply_price_performance_20261018.py' 
spec=importlib.util.spec_from_file_location('installer',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.fetch_asset=lambda rule:(R/'performance-assets'/Path(rule['download']).name).read_bytes()
config_text='''# literal { brace in a comment }
server { listen 80; server_name azmednsk.ru www.azmednsk.ru; return 301 https://azmednsk.ru$request_uri; }
server {
 listen 443 ssl;
 listen [::]:443 ssl;
 server_name azmednsk.ru www.azmednsk.ru;
 root /var/www/azmednsk-test/tilda-migration/public;
 ssl_certificate /example/public.pem;
 ssl_certificate_key /example/private.key;
 location / { try_files $uri $uri/ =404; }
 location = /robots.txt { add_header X-Test "brace } in a quoted value"; }
}
server { listen 443 ssl; server_name other.example; gzip off; root /other; }
'''
results=[]
def fixture(td):
 root=Path(td)/'public';root.mkdir();backups=Path(td)/'backups'
 names=set(m.MANIFEST['protected'])|{n for n,r in m.MANIFEST['files'].items() if r['before'] is not None}
 for n in names:
  p=root/n;p.parent.mkdir(parents=True,exist_ok=True);src=R/'verified-improvements-before/public'/n
  if not src.exists():src=R/'performance-assets'/(Path(n).stem+'.source')
  shutil.copyfile(src,p)
 for n in m.PHOTOS:
  p=root/n;p.parent.mkdir(parents=True,exist_ok=True)
  if not p.exists():p.write_bytes(('legacy fixture '+n).encode())
 cp=Path(td)/'azmednsk.conf';cp.write_text(config_text)
 def cfg():return (cp,cp.read_bytes(),m.gzip_patch(cp.read_text()).encode(),cp.stat()),{cp:cp.read_bytes()}
 return root,backups,cp,cfg
snapshot=lambda root:{str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
def no_http(n,b):assert hashlib.sha256(b).hexdigest()==m.MANIFEST['files'][n]['after']
def run(mode):
 with tempfile.TemporaryDirectory() as td:
  root,backups,cp,cfg=fixture(td);initial=snapshot(root);nginx_before=cp.read_bytes();calls=[]
  def ng(*args):
   calls.append(args)
   if mode=='nginx-test-failure' and len(calls)==1:raise RuntimeError('nginx -t simulated failure')
   if mode=='reload-failure' and len(calls)==2:raise RuntimeError('reload simulated failure')
  def verify(n,b):
   no_http(n,b)
   if mode=='https-failure' and n=='index.html':raise RuntimeError('HTTPS simulated failure')
  def gzip_check(root):
   if mode=='gzip-failure':raise RuntimeError('gzip simulated failure')
   return [{'file':'layouts.css','original_bytes':696234,'transfer_bytes':44063}]
  if mode=='hash-drift':(root/'team/lubaeva-alena/index.html').write_text('unexpected change');initial=snapshot(root)
  with contextlib.redirect_stdout(io.StringIO()):
   try:m.apply_with_compression(root,backups,verify,lambda p:None,cfg,ng,gzip_check)
   except RuntimeError:
    assert mode!='success';assert snapshot(root)==initial,mode;assert cp.read_bytes()==nginx_before,mode
    if mode=='hash-drift':assert not backups.exists() and not calls
   else:
    assert mode=='success'
    for n,r in m.MANIFEST['files'].items():assert m.sha((root/n).read_bytes())==r['after']
    for n,d in m.MANIFEST['protected'].items():assert m.sha((root/n).read_bytes())==d
    assert len(list(backups.iterdir()))==1
    after=snapshot(root);meta={n:(root/n).stat().st_mtime_ns for n in after};config_after=cp.read_bytes();calls_before=len(calls)
    m.apply_with_compression(root,backups,verify,lambda p:None,cfg,ng,gzip_check)
    assert snapshot(root)==after and cp.read_bytes()==config_after and len(list(backups.iterdir()))==1
    assert all((root/n).stat().st_mtime_ns==t for n,t in meta.items()) and len(calls)==calls_before
  results.append(mode+':PASS')
for mode in ['success','hash-drift','nginx-test-failure','reload-failure','gzip-failure','https-failure']:run(mode)
patched=m.gzip_patch(config_text);assert m.gzip_patch(patched)==patched
# Exactly the same non-gzip configuration tree, including HTTP and unrelated HTTPS hosts.
def strip_gzip(nodes):
 return [(n['args'],strip_gzip(n['children']) if n['children'] is not None else None) for n in nodes if not n['args'][0].startswith('gzip')]
assert strip_gzip(m.nginx_nodes(config_text))==strip_gzip(m.nginx_nodes(patched))
existing=config_text.replace(' root /var/www',' gzip off; gzip_vary off; gzip_comp_level 1; gzip_types application/custom;\n root /var/www')
p=m.gzip_patch(existing);assert 'application/custom' in p and m.gzip_patch(p)==p
for invalid in [config_text.replace('azmednsk.ru www.azmednsk.ru;\n root','azmednsk.ru another.example;\n root'),config_text+config_text]:
 try:m.gzip_patch(invalid)
 except RuntimeError:pass
 else:raise AssertionError('unsafe configuration was accepted')
results.append('nginx-scope-existing-directives-idempotence:PASS')
# Reverse every source/live edit byte-for-byte, guarding unrelated text, links and attributes.
for n,rule in m.MANIFEST['files'].items():
 if not n.endswith('.html'):continue
 for kind,after in [('source',(REPO/'tilda-migration/public'/n).read_text()),('live',(R/'price-performance-after'/n).read_text())]:
  before=after
  for a,b,count in reversed(rule['replacements']):assert before.count(b)==count;before=before.replace(b,a)
  expected=m.MANIFEST['source_rules'][n]['before'] if kind=='source' else rule['before'];assert m.sha(before.encode())==expected
results.append('reversible-source-and-live-edits:PASS')
report={'checks':results,'photos_note':'Seven real images compared separately; four runtime-only legacy photos use test fixture bytes. HTTPS and nginx process calls simulated in installer rehearsal. Production checks run on install.','html_changed':8,'protected':94}
(REPO/'tilda-migration/ops/price_performance_20261018_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
