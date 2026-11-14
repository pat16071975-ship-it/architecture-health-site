from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit
from PIL import Image,ImageOps
import re,json,hashlib,base64,zlib
root=Path('/tmp/portrait-update'); out=root/'payload';out.mkdir(exist_ok=True)
source=Path('/tmp/az-team2'); pages=Path('/tmp/site-photos/pages')
def sha(b):return hashlib.sha256(b).hexdigest()
def norm(s):return s.casefold().replace('ё','е').strip()
people=[('zubachev','Зубачев Роман Николаевич','зубачев'),('chirkov','Чирков Максим Сергеевич','чирков'),('starostenko','Старостенко Вадим Анатольевич','старостенко'),('dimitrenko','Димитренко Анна Яковлевна','димитренко'),('filatova','Филатова Анна Дмитриевна','филатова'),('nazyrov','Назыров Уктамжон Жумавоевич','назыров'),('besedina','Беседина Мария Юрьевна','беседина'),('lyubaeva','Любаева Алена Юрьевна','любаева'),('pavlova','Павлова Алла Викторовна','павлова'),('lapina','Лапина Наталья Сергеевна','лапина'),('holodtseva','Холодцева Валерия Евгеньевна','холодцева')]
folder='assets/portraits-20261114-v1';assets={}; mapping={};provenance={}
for key,name,prefix in people:
 files=sorted([p for p in source.glob('*.jpg') if norm(p.stem).split()[0]==prefix],key=lambda p:int(re.search(r'\d+',p.stem)[0]) if re.search(r'\d+',p.stem) else 0)
 assert files
 urls=[]
 for i,p in enumerate(files,1):
  im=ImageOps.exif_transpose(Image.open(p)).convert('RGB');im.thumbnail((640,960),Image.Resampling.LANCZOS)
  dst=out/f'{key}-{i}.webp';im.save(dst,'WEBP',quality=82,method=6)
  rel=f'{folder}/{dst.name}';assets[rel]=dst.read_bytes();urls.append('/'+rel)
  provenance[rel]={'name':name,'source':p.name,'source_sha256':sha(p.read_bytes()),'size':im.size,'bytes':dst.stat().st_size}
 mapping[norm(name)]={'key':key,'urls':urls,'name':name}
class Parser(HTMLParser):
 def handle_starttag(self,t,a):
  if t=='img':self.attrs=dict(a)
def attrs(tag):
 p=Parser();p.feed(tag);return p.attrs
# Exact full-name match only. No surname guessing or certificate replacement.
changes={}; report=[];counts={}
for f in sorted(pages.rglob('*.html')):
 rel=str(f.relative_to(pages))
 if rel in ('team/index.html','team-2/index.html') or '/certificates/' in rel:continue
 old=f.read_bytes();s=old.decode();matches=[]
 def replace(m):
  tag=m[0];a=attrs(tag);p=mapping.get(norm(a.get('alt','')))
  if not p:return tag
  assert '<picture' not in s[max(0,m.start()-300):m.start()]
  # Keep existing geometry and all non-image behavior.
  new=re.sub(r'\s+(?:src|srcset|sizes)\s*=\s*(?:"[^"]*"|\x27[^\x27]*\x27)', '',tag,flags=re.I)
  end='/>' if new.endswith('/>') else '>'
  new=new[:-len(end)]+f' src="{p["urls"][0]}" data-az-person="{p["key"]}"'+end
  matches.append({'name':p['name'],'old_src':a.get('src'),'new_src':p['urls'][0]});counts[p['key']]=counts.get(p['key'],0)+1
  return new
 s=re.sub(r'<img\b[^>]*>',replace,s,flags=re.I)
 if matches:
  assert s.count('</body>')==1
  s=s.replace('</body>',f'<script defer src="/{folder}/portraits.js"></script></body>')
  changes[rel]={'before':sha(old),'after':sha(s.encode()),'content':s};report.append({'path':rel,'portraits':matches})
js='''(() => {
'use strict';
if (/^\\/team(?:-2)?\\/?$/.test(location.pathname)) return;
const pools = POOLS;
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const states = [];
const nextIndex = (length, current) => { const n = Math.floor(Math.random() * (length - 1)); return n >= current ? n + 1 : n; };
const observer = new IntersectionObserver(entries => entries.forEach(e => { e.target._azVisible = e.isIntersecting; }), {rootMargin:'0px'});
for (const img of document.querySelectorAll('img[data-az-person]')) {
 const pool = pools[img.dataset.azPerson];
 if (!pool || pool.length < 2) continue;
 const state = {img,pool,index:0,busy:false,first:true,due:0}; states.push(state); observer.observe(img);
}
async function swap(s) {
 s.busy = true;
 const index = s.first ? Math.floor(Math.random()*s.pool.length) : nextIndex(s.pool.length,s.index);
 s.first = false;
 try {
  if (index === s.index) return;
  const preload = new Image(); preload.src = s.pool[index]; await preload.decode();
  if (!s.img.isConnected || document.hidden || !s.img._azVisible) return;
  // Fade only after a decoded image is available; keep the original layout.
  const duration = reduced.matches ? 0 : 220;
  let fade;
  if (duration) { fade=s.img.animate([{opacity:1},{opacity:0.35}],{duration,fill:'forwards'}); await fade.finished; }
  s.img.src = s.pool[index]; s.index = index;
  if (duration) {
   fade.cancel();
   await s.img.animate([{opacity:0.35},{opacity:1}],{duration}).finished;
  }
 } catch (_) { /* Keep last successfully loaded photograph. */ }
 finally { s.busy=false; s.due=Date.now()+6500+Math.random()*1500; }
}
setInterval(() => {
 if(document.hidden) return;
 for(const s of states) {
  if(s.busy || !s.img._azVisible || !s.img.complete || Date.now()<s.due) continue;
  if(!s.first && (reduced.matches || s.img.matches(':hover') || s.img.closest('a,button')?.matches(':focus-within'))) continue;
  swap(s);
 }
},700);
})();
'''
js=js.replace('POOLS',json.dumps({p['key']:p['urls'] for p in mapping.values()},ensure_ascii=False))
assets[f'{folder}/portraits.js']=js.encode();(root/'portraits.js').write_text(js)
(root/'mapping.json').write_text(json.dumps({'sources':provenance,'changes':report,'counts':counts},ensure_ascii=False,indent=2))
payload={'changes':changes,'assets':{p:{'sha':sha(b),'data':base64.b64encode(b).decode()} for p,b in assets.items()}}
(root/'bundle.txt').write_text(base64.b64encode(zlib.compress(json.dumps(payload,ensure_ascii=False).encode(),9)).decode())
(root/'payload.json').write_text(json.dumps(payload,ensure_ascii=False))
print('PAGES',len(changes),'PORTRAITS',sum(counts.values()),'SOURCES',len(provenance),'ASSET_BYTES',sum(map(len,assets.values())),'COUNTS',counts)
