"""Check exact scope, matching visible/JSON-LD paths and destination membership."""
import argparse,json,hashlib,re,subprocess
from pathlib import Path
from lxml import html
p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);a=p.parse_args()
tm=Path(__file__).resolve().parents[1];repo=tm.parent;m=json.loads((tm/'ops/breadcrumbs_20261021_manifest.json').read_text())
def cleaned(b):
 d=html.fromstring(b)
 for e in d.xpath('//nav[contains(@class,"breadcrumbs")] | //script[@id="az-breadcrumb-schema"]'):e.getparent().remove(e)
 return html.tostring(d)
for n,r in m['files'].items():
 before=(a.baseline/n).read_bytes();after=(a.candidate/n).read_bytes();d=html.fromstring(after)
 assert hashlib.sha256(before).hexdigest()==r['before']
 assert hashlib.sha256(after).hexdigest()==r['after']
 assert cleaned(before)==cleaned(after),n
 nav=d.xpath('//nav[contains(@class,"breadcrumbs")]');assert len(nav)==1,n;nav=nav[0]
 current=nav.xpath('./span[@aria-current="page"]');assert len(current)==1,n
 data=json.loads(d.xpath('string(//script[@id="az-breadcrumb-schema"])'));items=data['itemListElement']
 els=nav.xpath('./a|./span')
 assert [x['position'] for x in items]==list(range(1,len(items)+1))
 assert [x['name'] for x in items]==[x.text_content() for x in els]
 assert items[-1]['item']==d.xpath('string(//link[@rel="canonical"]/@href)'),n
 for el,item in zip(els[:-1],items[:-1]):
  assert item['item']=='https://azmednsk.ru'+el.get('href')
  dest=el.get('href').lstrip('/')+'index.html'
  assert (a.baseline/dest).exists(),(n,dest)
 # Source changes reverse to exact git base, preserving historical source/live differences.
 src=(tm/'public'/n).read_text()
 for old,new,count in reversed(r['replacements']):assert src.count(new)==count;src=src.replace(new,old)
 gitbase=subprocess.check_output(['git','show','HEAD:tilda-migration/public/'+n],cwd=repo).decode()
 assert src==gitbase,n
report={'status':'PASS','pages':len(m['files']),'checks':['before/after hashes','exact content preserved outside breadcrumb and new schema','one current-page label','matching visible/schema names and URLs','sequential positions','all parent targets exist','exact Git-base reversibility']}
(tm/'ops/breadcrumbs_20261021_validation.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
