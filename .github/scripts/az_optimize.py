from pathlib import Path
from urllib.request import Request, urlopen
from PIL import Image
import os, re

ROOT=Path('.')
font_dir=ROOT/'assets/fonts'
font_dir.mkdir(parents=True,exist_ok=True)
font_sources={
    'Montserrat-Regular.woff2':'https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/webfonts/Montserrat-Regular.woff2',
    'Montserrat-Medium.woff2':'https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/webfonts/Montserrat-Medium.woff2',
    'Montserrat-SemiBold.woff2':'https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/webfonts/Montserrat-SemiBold.woff2',
    'OFL-Montserrat.txt':'https://raw.githubusercontent.com/JulietaUla/Montserrat/master/OFL.txt',
}
for name,url in font_sources.items():
    req=Request(url,headers={'User-Agent':'architecture-health-site-build'})
    data=urlopen(req,timeout=30).read()
    if name.endswith('.woff2') and len(data)<10000:
        raise SystemExit(f'STOP: font download looks invalid: {name}, {len(data)} bytes')
    if name.endswith('.txt') and len(data)<500:
        raise SystemExit(f'STOP: license download looks invalid: {name}')
    (font_dir/name).write_bytes(data)

local_css="""@font-face{font-family:'Montserrat';src:url('../assets/fonts/Montserrat-Regular.woff2') format('woff2');font-style:normal;font-weight:400;font-display:swap}
@font-face{font-family:'Montserrat';src:url('../assets/fonts/Montserrat-Medium.woff2') format('woff2');font-style:normal;font-weight:500;font-display:swap}
@font-face{font-family:'Montserrat';src:url('../assets/fonts/Montserrat-SemiBold.woff2') format('woff2');font-style:normal;font-weight:600;font-display:swap}
"""
(ROOT/'interface-lab/local-fonts.css').write_text(local_css,encoding='utf-8')

hero_out=ROOT/'assets/direction-hero'
hero_out.mkdir(parents=True,exist_ok=True)
pages=sorted((ROOT/'interface-lab').glob('*/index.html'))
if len(pages)!=12:
    raise SystemExit(f'STOP: expected 12 direction pages, found {len(pages)}')

def ranges_for(s, pattern):
    return [(m.start(),m.end()) for m in re.finditer(pattern,s,flags=re.S|re.I)]
def in_ranges(pos,ranges):
    return any(a<=pos<b for a,b in ranges)
def strip_attr(tag,name):
    return re.sub(r'\s+'+re.escape(name)+r'=(?:"[^"]*"|\'[^\']*\'|[^\s>]+)','',tag,flags=re.I)
def add_attrs(tag, attrs):
    self_close=tag.rstrip().endswith('/>')
    base=tag.rstrip()
    base=base[:-2].rstrip() if self_close else base[:-1].rstrip()
    end=' />' if self_close else '>'
    return base+''.join(f' {k}="{v}"' for k,v in attrs.items())+end

for p in pages:
    s=p.read_text(encoding='utf-8')
    s=re.sub(r'\s*<link rel="preconnect" href="https://fonts\.googleapis\.com">','',s)
    s=re.sub(r'\s*<link rel="preconnect" href="https://fonts\.gstatic\.com" crossorigin>','',s)
    s=re.sub(r'\s*<link href="https://fonts\.googleapis\.com/css2\?[^\"]+" rel="stylesheet">','',s)
    if 'local-fonts.css?v=20260909-1' not in s:
        marker='<link rel="stylesheet" href="../direction-page.css'
        local='<link rel="stylesheet" href="../local-fonts.css?v=20260909-1">\n'
        if marker in s:
            s=s.replace(marker,local+marker,1)
        else:
            s=s.replace('</head>',local+'</head>',1)

    hero_ranges=ranges_for(s,r'<section\b[^>]*class="[^"]*\bhero\b[^"]*"[^>]*>.*?</section>')
    header_ranges=ranges_for(s,r'<header\b[^>]*>.*?</header>')
    matches=list(re.finditer(r'<img\b[^>]*>',s,flags=re.I))
    pieces=[];last=0
    for m in matches:
        pieces.append(s[last:m.start()])
        tag=m.group(0)
        srcm=re.search(r'\bsrc="([^"]+)"',tag,re.I)
        if not srcm:
            pieces.append(tag);last=m.end();continue
        src=srcm.group(1)
        path_part=src.split('?',1)[0]
        if path_part.startswith(('http://','https://','data:')):
            pieces.append(tag);last=m.end();continue
        asset=(p.parent/path_part).resolve()
        try:
            asset.relative_to(ROOT.resolve())
        except ValueError:
            raise SystemExit(f'STOP: image outside repo: {p} -> {src}')
        if not asset.exists():
            raise SystemExit(f'STOP: local image missing: {p} -> {asset}')
        is_hero=in_ranges(m.start(),hero_ranges)
        is_header=in_ranges(m.start(),header_ranges)

        effective_asset=asset
        if is_hero and asset.suffix.lower() in {'.jpg','.jpeg','.png'}:
            out=hero_out/(asset.stem+'.webp')
            with Image.open(asset) as im:
                if im.mode in ('RGBA','LA') or ('transparency' in im.info):
                    im.save(out,'WEBP',lossless=True,method=6)
                else:
                    im.convert('RGB').save(out,'WEBP',quality=84,method=6)
            effective_asset=out.resolve()
            rel=os.path.relpath(out,p.parent).replace(os.sep,'/')
            new_src=rel+'?v=20260909-1'
            tag=tag[:srcm.start(1)]+new_src+tag[srcm.end(1):]

        with Image.open(effective_asset) as im:
            width,height=im.size

        for attr in ('width','height','loading','decoding','fetchpriority'):
            tag=strip_attr(tag,attr)
        attrs={'width':str(width),'height':str(height),'decoding':'async'}
        if is_hero:
            attrs['loading']='eager';attrs['fetchpriority']='high'
        elif is_header:
            attrs['loading']='eager'
        else:
            attrs['loading']='lazy'
        tag=add_attrs(tag,attrs)
        pieces.append(tag);last=m.end()
    pieces.append(s[last:])
    s=''.join(pieces)
    p.write_text(s,encoding='utf-8')

p=ROOT/'interface-lab/index.html'
s=p.read_text(encoding='utf-8')

if 'local-fonts.css?v=20260909-1' not in s:
    s=s.replace('<title>АЗ Interface Lab</title>','<title>АЗ Interface Lab</title>\n<link rel="stylesheet" href="local-fonts.css?v=20260909-1">',1)
s=s.replace('font-family:Inter,Segoe UI,Arial,sans-serif',"font-family:'Montserrat',Inter,Segoe UI,Arial,sans-serif")

old='let orbitAnimationFrame=0;'
new='let orbitAnimationFrame=0;\nlet orbitAnimationState=null;'
if old not in s and new not in s:
    raise SystemExit('STOP: orbit animation state marker not found')
s=s.replace(old,new,1)

old_stop="function stopOrbitAnimation(){if(orbitAnimationFrame){cancelAnimationFrame(orbitAnimationFrame);orbitAnimationFrame=0}}"
new_stop="""function stopOrbitAnimation(){
  const state=orbitAnimationState;
  if(state){
    if(orbitAnimationFrame){try{state.win.cancelAnimationFrame(orbitAnimationFrame)}catch(_){cancelAnimationFrame(orbitAnimationFrame)}orbitAnimationFrame=0}
    state.observer?.disconnect();
    state.doc?.removeEventListener('visibilitychange',state.onVisibility);
    document.removeEventListener('visibilitychange',state.onVisibility);
    if(state.motionQuery&&state.onMotionChange){if(state.motionQuery.removeEventListener)state.motionQuery.removeEventListener('change',state.onMotionChange);else state.motionQuery.removeListener(state.onMotionChange)}
  }else if(orbitAnimationFrame){cancelAnimationFrame(orbitAnimationFrame);orbitAnimationFrame=0}
  orbitAnimationState=null;
}"""
if old_stop not in s and "function stopOrbitAnimation(){\n  const state=orbitAnimationState;" not in s:
    raise SystemExit('STOP: stopOrbitAnimation marker not found')
s=s.replace(old_stop,new_stop,1)

old_start="function startOrbitAnimation(doc,planets,g){const started=performance.now();const tick=now=>{if(mode!=='draft'||frame.contentDocument!==doc||!doc.documentElement.isConnected){orbitAnimationFrame=0;return}const elapsed=now-started;setPlanetPositions(planets.inner,innerAngles,g.cx,g.cy,g.innerRx,g.innerRy,g.innerTilt,(elapsed/INNER_DURATION)*360);setPlanetPositions(planets.outer,outerAngles,g.cx,g.cy,g.outerRx,g.outerRy,g.outerTilt,(elapsed/OUTER_DURATION)*360);orbitAnimationFrame=requestAnimationFrame(tick)};orbitAnimationFrame=requestAnimationFrame(tick)}"
new_start="""function startOrbitAnimation(doc,planets,g){
  const win=doc.defaultView||frame.contentWindow;if(!win)return;
  const motionQuery=win.matchMedia('(prefers-reduced-motion: reduce)');
  const state={doc,planets,g,win,motionQuery,elapsed:0,startedAt:0,running:false,inView:true,observer:null,onVisibility:null,onMotionChange:null};
  orbitAnimationState=state;
  const render=elapsed=>{
    setPlanetPositions(planets.inner,innerAngles,g.cx,g.cy,g.innerRx,g.innerRy,g.innerTilt,(elapsed/INNER_DURATION)*360);
    setPlanetPositions(planets.outer,outerAngles,g.cx,g.cy,g.outerRx,g.outerRy,g.outerTilt,(elapsed/OUTER_DURATION)*360);
  };
  const canRun=()=>orbitAnimationState===state&&mode==='draft'&&frame.contentDocument===doc&&doc.documentElement.isConnected&&!document.hidden&&!doc.hidden&&state.inView&&!motionQuery.matches;
  const pause=()=>{
    if(!state.running)return;
    state.elapsed+=Math.max(0,win.performance.now()-state.startedAt);state.running=false;
    if(orbitAnimationFrame){win.cancelAnimationFrame(orbitAnimationFrame);orbitAnimationFrame=0}
  };
  const tick=now=>{
    if(!canRun()){pause();return}
    const elapsed=state.elapsed+Math.max(0,now-state.startedAt);render(elapsed);
    orbitAnimationFrame=win.requestAnimationFrame(tick);
  };
  const resume=()=>{
    if(orbitAnimationState!==state)return;
    if(motionQuery.matches){state.elapsed=0;render(0);return}
    if(!canRun()||state.running)return;
    state.startedAt=win.performance.now();state.running=true;orbitAnimationFrame=win.requestAnimationFrame(tick);
  };
  state.onVisibility=()=>{if(canRun())resume();else pause()};
  state.onMotionChange=()=>{if(motionQuery.matches){pause();state.elapsed=0;render(0)}else resume()};
  doc.addEventListener('visibilitychange',state.onVisibility);
  document.addEventListener('visibilitychange',state.onVisibility);
  if(motionQuery.addEventListener)motionQuery.addEventListener('change',state.onMotionChange);else motionQuery.addListener(state.onMotionChange);
  const observed=doc.querySelector('.az-lab-orbit-host')||doc.querySelector('.map')||doc.querySelector('.stage');
  if(observed&&win.IntersectionObserver){
    state.observer=new win.IntersectionObserver(entries=>{const entry=entries[0];state.inView=!!entry&&entry.isIntersecting&&entry.intersectionRatio>0;if(canRun())resume();else pause()},{threshold:.01});
    state.observer.observe(observed);
  }
  render(0);
  if(!motionQuery.matches)resume();
}"""
if old_start not in s and "const motionQuery=win.matchMedia('(prefers-reduced-motion: reduce)');" not in s:
    raise SystemExit('STOP: startOrbitAnimation marker not found')
s=s.replace(old_start,new_start,1)

old_draw="if(mode!=='draft')return;const doc=frame.contentDocument;if(!doc)return;cleanupDraft(doc);const stage=doc.querySelector('.stage');if(!stage)return;const host=doc.querySelector('.map')||stage.parentElement;if(!host)return;"
new_draw="if(mode!=='draft')return;const doc=frame.contentDocument;if(!doc)return;cleanupDraft(doc);doc.querySelectorAll('link[href*=\"fonts.googleapis.com\"],link[href*=\"fonts.gstatic.com\"]').forEach(link=>link.remove());const stage=doc.querySelector('.stage');if(!stage)return;const host=doc.querySelector('.map')||stage.parentElement;if(!host)return;"
if old_draw not in s and "doc.querySelectorAll('link[href*=\"fonts.googleapis.com\"]" not in s:
    raise SystemExit('STOP: drawOrbits marker not found')
s=s.replace(old_draw,new_draw,1)

font_faces="""@font-face{font-family:'Montserrat';src:url('/architecture-health-site/assets/fonts/Montserrat-Regular.woff2') format('woff2');font-style:normal;font-weight:400;font-display:swap}
@font-face{font-family:'Montserrat';src:url('/architecture-health-site/assets/fonts/Montserrat-Medium.woff2') format('woff2');font-style:normal;font-weight:500;font-display:swap}
@font-face{font-family:'Montserrat';src:url('/architecture-health-site/assets/fonts/Montserrat-SemiBold.woff2') format('woff2');font-style:normal;font-weight:600;font-display:swap}
"""
marker="host.classList.add('az-lab-orbit-host');const style=doc.createElement('style');style.id='az-lab-orbit-style';style.textContent=`\n"
if font_faces.splitlines()[0] not in s:
    if marker not in s:
        raise SystemExit('STOP: injected style marker not found')
    s=s.replace(marker,marker+font_faces,1)

reduced_css="@media (prefers-reduced-motion:reduce){.az-lab-orbit-host .direction{will-change:auto!important}}\n"
if reduced_css.strip() not in s:
    css_marker='@media (min-width:1181px){\n'
    if css_marker not in s:
        raise SystemExit('STOP: orbit CSS media marker missing')
    s=s.replace(css_marker,reduced_css+css_marker,1)

p.write_text(s,encoding='utf-8')

index=p.read_text(encoding='utf-8')
required=[
    "matchMedia('(prefers-reduced-motion: reduce)')",
    'IntersectionObserver',
    "visibilitychange",
    'const INNER_DURATION=63000;',
    'const OUTER_DURATION=114000;',
    "const innerAngles=[180,270,0,90];",
    "const outerAngles=[157.5,202.5,247.5,292.5,337.5,22.5,67.5,112.5];",
    'innerRx:r.width*.55,innerRy:r.height*.44,outerRx:r.width*.73,outerRy:r.height*.57,innerTilt:18,outerTilt:14',
    '.az-lab-right-badge-shift{transform:translateX(-37.8px)!important}',
    ".az-lab-orbit-host .stage{transform:scale(.724137931)!important;transform-origin:center center!important}",
]
for marker in required:
    if marker not in index:
        raise SystemExit(f'STOP: required/protected marker missing: {marker}')
for font in ['Montserrat-Regular.woff2','Montserrat-Medium.woff2','Montserrat-SemiBold.woff2']:
    fp=ROOT/'assets/fonts'/font
    if not fp.exists() or fp.stat().st_size<10000:
        raise SystemExit(f'STOP: invalid local font {font}')

for p in pages:
    s=p.read_text(encoding='utf-8')
    if 'fonts.googleapis.com' in s or 'fonts.gstatic.com' in s:
        raise SystemExit(f'STOP: remote Google Font remains: {p}')
    if '../local-fonts.css?v=20260909-1' not in s:
        raise SystemExit(f'STOP: local font CSS missing: {p}')
    hero=re.search(r'<section\b[^>]*class="[^"]*\bhero\b[^"]*"[^>]*>(.*?)</section>',s,re.S|re.I)
    if not hero:
        raise SystemExit(f'STOP: hero section missing: {p}')
    hero_imgs=re.findall(r'<img\b[^>]*>',hero.group(1),re.I)
    if not hero_imgs:
        raise SystemExit(f'STOP: hero image missing: {p}')
    for tag in hero_imgs:
        src=re.search(r'\bsrc="([^"]+)"',tag,re.I)
        if not src:
            continue
        ext=src.group(1).split('?',1)[0].lower()
        if not (ext.endswith('.webp') or ext.endswith('.avif')):
            raise SystemExit(f'STOP: hero is not modern image format: {p} -> {src.group(1)}')
        for attr in ('width=','height=','decoding="async"','loading="eager"','fetchpriority="high"'):
            if attr not in tag:
                raise SystemExit(f'STOP: hero attr {attr} missing: {p}')
    hero_end=hero.end()
    for m in re.finditer(r'<img\b[^>]*>',s,re.I):
        tag=m.group(0)
        for attr in ('width=','height=','decoding="async"','loading='):
            if attr not in tag:
                raise SystemExit(f'STOP: image attr {attr} missing: {p}')
        if m.start()>hero_end and 'loading="lazy"' not in tag:
            raise SystemExit(f'STOP: below-fold image is not lazy: {p} -> {tag}')
