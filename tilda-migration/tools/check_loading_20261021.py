"""Static/media validation only. Does not establish browser or live acceptance."""
import hashlib,json,subprocess,io,tarfile
from pathlib import Path
from PIL import Image
from bs4 import BeautifulSoup
R=Path(__file__).resolve().parents[1]
sha=lambda b:hashlib.sha256(b).hexdigest()
m=json.loads((R/'ops/loading_20261021_manifest.json').read_text())
p=json.loads((R/'ops/loading_20261021_media_validation.json').read_text())
for name,rule in m['assets'].items():
 b=(R/'public'/name).read_bytes()
 assert len(b)==rule['bytes'] and sha(b)==rule['sha256'],name
for name,rule in m['files'].items():
 new=(R/'public'/name).read_text();old=new
 for a,b,n in reversed(rule['replacements']):
  assert old.count(b)==n,name
  old=old.replace(b,a)
 expected=subprocess.check_output(['git','show',m['base_commit']+':tilda-migration/public/'+name],cwd=R).decode()
 assert old==expected,name
 a=BeautifulSoup(old,'html.parser');b=BeautifulSoup(new,'html.parser')
 assert a.get_text()==b.get_text(),name
 assert [t.get('href') for t in a.find_all('a')]==[t.get('href') for t in b.find_all('a')],name
 assert [t.get('src') for t in a.find_all('img')]==[t.get('src') for t in b.find_all('img')],name
 assert [str(t) for t in a.find_all('script')]==[str(t) for t in b.find_all('script')],name
for item in p['images']:
 for variant in item['variants']:
  if variant['path'] not in m['assets']:continue
  with Image.open(R/'public'/variant['path']) as im:
   assert im.size==(variant['width'],variant['height'])
   assert sha(im.convert('RGBA').tobytes())==variant['rgba_sha256']
report={'status':'PASS STATIC/MEDIA','html_checked':len(m['files']),'assets_checked':len(m['assets']),'text_links_scripts_original_img_sources':'unchanged','source_delta':'exact reversible','image_decoding':'dimensions and RGBA checksums verified','browser_visual':'NOT CHECKED','live_deployed':False,'cache':'Existing downloaded response headers have no explicit Cache-Control for photo assets. Server cache configuration not changed.'}
(R/'ops/loading_20261021_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
