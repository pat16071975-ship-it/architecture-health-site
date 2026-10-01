import collections, hashlib, json, re, urllib.parse
from pathlib import Path
from lxml import html
ROOT=Path(__file__).resolve().parents[1];PUBLIC=ROOT/'public'
missing=[];external_assets=[];broken=[];runtime=[];count=0
for file in PUBLIC.rglob('*.html'):
    count+=1;doc=html.parse(str(file))
    for el in doc.xpath('//*[@src or @href]'):
        url=el.get('src') or el.get('href');parsed=urllib.parse.urlparse(url)
        if parsed.scheme or parsed.netloc:
            if el.get('src') and 'tildacdn' in url: external_assets.append(url)
            if el.tag=='script' and 'tilda' in url: runtime.append(url)
            continue
        if not parsed.path: continue
        target=PUBLIC/parsed.path.lstrip('/') if parsed.path.startswith('/') else file.parent/parsed.path
        if el.get('src') or el.tag=='link':
            if not target.exists(): missing.append({'page':str(file.relative_to(PUBLIC)),'asset':url})
        elif el.tag=='a' and not target.exists() and not (target/'index.html').exists():
            broken.append({'page':str(file.relative_to(PUBLIC)),'link':url})
    robots=doc.xpath('//meta[@name="robots"]/@content')
    if file.name!='404.html' and 'noindex,nofollow' not in robots:raise AssertionError('Test page indexed: '+str(file))
items=json.loads((ROOT/'media-manifest.json').read_text())
for item in items:
    data=(PUBLIC/item['file'].lstrip('/')).read_bytes()
    if hashlib.sha256(data).hexdigest()!=item['sha256']: raise AssertionError('Media checksum mismatch: '+item['file'])
report={'html_pages':count,'media_files':len(items),'missing_assets':missing,'external_tilda_assets':external_assets,'tilda_runtime':runtime,'broken_links':broken,'visual_browser_check':'pending test HTTP host','forms':'no local submission; telephone and external Medflex link only'}
(ROOT/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('HTML',count,'MEDIA',len(items),'MISSING',len(missing),'TILDA_RUNTIME',len(runtime),'TILDA_ASSETS',len(external_assets),'BROKEN_LINKS',len(broken))
print('Unique missing links:',sorted(set(x['link'] for x in broken)))
if missing or external_assets or runtime or broken: raise SystemExit(1)
