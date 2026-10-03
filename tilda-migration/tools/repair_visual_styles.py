"""Restore static button/background/border facts omitted by migration.
Run against archived source; never modifies source HTML or page content.
"""
from pathlib import Path
from lxml import html
import re,json,hashlib
ROOT=Path(__file__).resolve().parents[1]
MARK='/* Migration visual colors and borders restored from archived source. */'
def rules(css,contexts=()):
 css=re.sub(r"/\*.*?\*/", "", css, flags=re.S)
 pos=0
 while pos<len(css):
  op=css.find('{',pos)
  if op<0:break
  head=css[pos:op].strip();depth=1;i=op+1;quote=None
  while i<len(css) and depth:
   c=css[i]
   if quote:
    if c==quote and css[i-1]!='\\':quote=None
   elif c in "\"'":quote=c
   elif c=='{':depth+=1
   elif c=='}':depth-=1
   i+=1
  body=css[op+1:i-1];pos=i
  if head.startswith('@media'):yield from rules(body,contexts+(head,))
  elif not head.startswith('@'):yield head,body,contexts

def props(body):return dict(re.findall(r'([\w-]+)\s*:\s*([^;]+)',body))
def resolve(value,variables):
 for _ in range(5):
  old=value
  value=re.sub(r'var\((--[\w-]+)(?:,([^()]*))?\)',lambda m:variables.get(m[1],m[2] or ''),value)
  if value==old:break
 return value.strip()
used={};buttons=set();pages=list((ROOT/'public').rglob('*.html'))
for p in pages:
 d=html.fromstring(p.read_bytes())
 for e in d.xpath('//*[@id and contains(@class,"visual")]'):
  used.setdefault(e.get('id'),set()).add(str(p.relative_to(ROOT/'public')))
  if 'visual-button' in e.get('class',''):buttons.add(e.get('id'))
seen=set();out=[];affected={}; button_sources=set()
for p in sorted((ROOT/'source').rglob('*.html')):
 d=html.fromstring(p.read_bytes())
 for block in d.xpath('//*[@id and starts-with(@id,"rec")]'):
  parsed=list(rules('\n'.join(block.xpath('.//style/text()'))))
  for e in block.xpath('.//*[@data-elem-id]'):
   vid='visual-'+block.get('id')[3:]+'-'+e.get('data-elem-id')
   if vid not in used or vid in seen:continue
   seen.add(vid);selector='#'+block.get('id')+' .tn-elem[data-elem-id="'+e.get('data-elem-id')+'"] .tn-atom'
   relevant=[(sel,props(body),ctx) for sel,body,ctx in parsed if sel in (selector,selector+':hover')]
   if vid in buttons:button_sources.add(vid)
   base={}
   for sel,pv,ctx in relevant:
    if not ctx and sel==selector:base.update({k:v for k,v in pv.items() if k.startswith('--')})
   context_variables={}
   for sel,pv,ctx in relevant:
    if sel==selector:
     context_variables.setdefault(ctx,{}).update({k:v for k,v in pv.items() if k.startswith('--')})
   for sel,pv,ctx in relevant:
    variables={**base,**context_variables.get(ctx,{}),**{k:v for k,v in pv.items() if k.startswith('--')}}
    decl={}
    if 'background-color' in pv and '--t396-bgcolor-color' in variables:
     value=resolve(pv['background-color'],variables)
     if value and 'var(' not in value:decl['background-color']=value
    if '--t396-bordercolor' in variables and sel==selector:
     for key,default in [('border-width','var(--t396-borderwidth,0)'),('border-style','var(--t396-borderstyle,solid)'),('border-color','var(--t396-bordercolor,transparent)')]:
      value=resolve(pv.get(key,default),variables)
      if value and 'var(' not in value:decl[key]=value
    if sel.endswith(':hover') and 'color' in pv and vid in buttons:
     value=resolve(pv['color'],variables)
     if value and 'var(' not in value:decl['color']=value
    if not decl:continue
    native='#'+vid+' > .visual-inner'+(':hover' if sel.endswith(':hover') else '')
    rule=native+'{'+ ';'.join(k+':'+v for k,v in decl.items())+'}'
    # Only preserve width media queries; hover capability wrappers unnecessary
    # for color declarations and unsupported legacy media hacks are omitted.
    for c in reversed([c for c in ctx if 'max-width' in c or 'min-width' in c and '0\\0' not in c]):rule=c+'{'+rule+'}'
    if rule not in out:out.append(rule)
    affected[vid]=sorted(used[vid])
assert button_sources==buttons,(buttons-button_sources)
css=ROOT/'public/layouts.css';original=css.read_text();baseline=original.split('\n'+MARK)[0]
patch='\n'+MARK+'\n'+'\n'.join(out)+'\n'
css.write_text(baseline+patch)
report={'pages_checked':len(pages),'visual_elements_checked':len(used),'canvas_buttons_checked':len(buttons),'restored_elements':len(affected),'affected_pages':sorted(set(p for ps in affected.values() for p in ps)),'rules':len(out),'elements':affected,'validation_scope':'Static source-to-migration style comparison; not a complete browser or mobile layout review.'}
(ROOT/'visual-style-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
Path('/tmp/visual-style-patch.txt').write_text(patch)
print(json.dumps({k:v for k,v in report.items() if k not in ['elements','affected_pages']},ensure_ascii=False));print('affected_pages',len(report['affected_pages']));print('before_sha256',hashlib.sha256(baseline.encode()).hexdigest());print('after_sha256',hashlib.sha256(css.read_bytes()).hexdigest())
