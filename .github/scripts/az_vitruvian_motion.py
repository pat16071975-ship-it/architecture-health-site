from pathlib import Path

p = Path('interface-lab/index.html')
s = p.read_text(encoding='utf-8')

cleanup_old = "doc.getElementById('az-lab-vitruvian-ring-text')?.remove();doc.getElementById('az-lab-orbit-style')?.remove();"
cleanup_new = "doc.getElementById('az-lab-vitruvian-ring-text')?.remove();doc.getElementById('az-lab-vitruvian-motion')?.remove();doc.querySelector('.vit-img')?.classList.remove('az-lab-vitruvian-static');doc.getElementById('az-lab-orbit-style')?.remove();"
if cleanup_old not in s:
    raise SystemExit('STOP: cleanup marker not found')
s = s.replace(cleanup_old, cleanup_new, 1)

marker = "function addVitruvianRingText(doc,host,stageRect,hostRect){"
fn = """function addVitruvianMotion(doc){
  const stage=doc.querySelector('.stage'),source=stage?.querySelector('.vit-img'),viewportWidth=doc.defaultView?.innerWidth||0;
  if(!stage||!source||viewportWidth<721||doc.getElementById('az-lab-vitruvian-motion'))return;
  source.classList.add('az-lab-vitruvian-static');
  const root=doc.createElement('div');root.id='az-lab-vitruvian-motion';root.setAttribute('aria-hidden','true');
  const addPart=(name,clip)=>{const img=source.cloneNode(false);img.removeAttribute('id');img.className='az-lab-vitruvian-part az-lab-vitruvian-'+name;img.style.clipPath=clip;img.style.webkitClipPath=clip;img.setAttribute('draggable','false');root.appendChild(img)};
  addPart('torso','polygon(36% 5%,64% 5%,66% 74%,34% 74%)');
  addPart('arm-left','polygon(0 17%,51% 17%,51% 60%,0 60%)');
  addPart('arm-right','polygon(49% 17%,100% 17%,100% 60%,49% 60%)');
  addPart('leg-left','polygon(20% 49%,51% 49%,51% 100%,8% 100%)');
  addPart('leg-right','polygon(49% 49%,80% 49%,92% 100%,49% 100%)');
  const cloth=doc.createElement('div');cloth.className='az-lab-vitruvian-loincloth';root.appendChild(cloth);
  stage.appendChild(root);
}
"""
if marker not in s:
    raise SystemExit('STOP: ring marker not found')
s = s.replace(marker, fn + marker, 1)

call_old = "  addVitruvianRingText(doc,host,r,hostRect);\n  host.classList.add('az-lab-orbit-host');"
call_new = "  addVitruvianRingText(doc,host,r,hostRect);\n  addVitruvianMotion(doc);\n  host.classList.add('az-lab-orbit-host');"
if call_old not in s:
    raise SystemExit('STOP: draw call marker not found')
s = s.replace(call_old, call_new, 1)

motion_old = "@media (prefers-reduced-motion:reduce){.az-lab-orbit-host .direction{will-change:auto!important}}"
motion_new = """#az-lab-vitruvian-motion{position:absolute!important;inset:0!important;z-index:3!important;pointer-events:none!important;overflow:hidden!important;border-radius:50%!important}
.az-lab-vitruvian-part{position:absolute!important;left:50%!important;top:54%!important;width:102%!important;height:102%!important;object-fit:cover!important;object-position:center 52%!important;transform:translate(-50%,-50%)!important;filter:sepia(.08) saturate(.9) contrast(.97) brightness(1.04)!important;mix-blend-mode:multiply!important;opacity:.66!important;will-change:transform,opacity!important}
.az-lab-vitruvian-static{opacity:.58!important}
.az-lab-vitruvian-torso{z-index:5!important;transform-origin:50% 54%!important;animation:azLabVitruvianTorso 18s ease-in-out infinite!important}
.az-lab-vitruvian-arm-left{z-index:4!important;transform-origin:42% 38%!important;animation:azLabVitruvianArmLeft 18s ease-in-out infinite!important}
.az-lab-vitruvian-arm-right{z-index:4!important;transform-origin:58% 38%!important;animation:azLabVitruvianArmRight 18s ease-in-out infinite!important}
.az-lab-vitruvian-leg-left{z-index:4!important;transform-origin:46% 58%!important;animation:azLabVitruvianLegLeft 18s ease-in-out infinite!important}
.az-lab-vitruvian-leg-right{z-index:4!important;transform-origin:54% 58%!important;animation:azLabVitruvianLegRight 18s ease-in-out infinite!important}
.az-lab-vitruvian-loincloth{position:absolute!important;left:50%!important;top:57.4%!important;width:20.5%!important;height:11.4%!important;z-index:8!important;transform:translate(-50%,-50%)!important;clip-path:polygon(12% 9%,88% 9%,78% 92%,22% 92%)!important;background:linear-gradient(180deg,rgba(239,232,219,.96),rgba(218,204,180,.94) 72%,rgba(181,150,98,.78))!important;border-top:1px solid rgba(123,96,60,.48)!important;border-radius:38% 38% 18% 18%/22% 22% 34% 34%!important;box-shadow:inset 0 4px 8px rgba(255,255,255,.28),0 2px 5px rgba(87,66,39,.10)!important;filter:sepia(.08)!important;animation:azLabVitruvianCloth 18s ease-in-out infinite!important;will-change:transform!important}
@keyframes azLabVitruvianTorso{0%,56%,100%{transform:translate(-50%,-50%) rotate(0deg) translateY(0)}64%{transform:translate(-50%,-50%) rotate(-1.1deg) translateY(-.3%)}72%{transform:translate(-50%,-50%) rotate(1.25deg) translateY(.45%)}80%{transform:translate(-50%,-50%) rotate(0deg) translateY(1.15%) scaleY(.992)}88%{transform:translate(-50%,-50%) rotate(0deg) translateY(0)}}
@keyframes azLabVitruvianArmLeft{0%,56%,100%{transform:translate(-50%,-50%) rotate(0deg)}66%{transform:translate(-50%,-50%) rotate(-4.8deg)}74%{transform:translate(-50%,-50%) rotate(-7deg)}82%{transform:translate(-50%,-50%) rotate(-2.2deg) translateY(.8%)}90%{transform:translate(-50%,-50%) rotate(0deg)}}
@keyframes azLabVitruvianArmRight{0%,56%,100%{transform:translate(-50%,-50%) rotate(0deg)}66%{transform:translate(-50%,-50%) rotate(4.8deg)}74%{transform:translate(-50%,-50%) rotate(7deg)}82%{transform:translate(-50%,-50%) rotate(2.2deg) translateY(.8%)}90%{transform:translate(-50%,-50%) rotate(0deg)}}
@keyframes azLabVitruvianLegLeft{0%,62%,100%{transform:translate(-50%,-50%) rotate(0deg) scaleY(1)}74%{transform:translate(-50%,-50%) rotate(.7deg) scaleY(.997)}82%{transform:translate(-50%,-49.1%) rotate(1.35deg) scaleY(.985)}90%{transform:translate(-50%,-50%) rotate(0deg) scaleY(1)}}
@keyframes azLabVitruvianLegRight{0%,62%,100%{transform:translate(-50%,-50%) rotate(0deg) scaleY(1)}74%{transform:translate(-50%,-50%) rotate(-.7deg) scaleY(.997)}82%{transform:translate(-50%,-49.1%) rotate(-1.35deg) scaleY(.985)}90%{transform:translate(-50%,-50%) rotate(0deg) scaleY(1)}}
@keyframes azLabVitruvianCloth{0%,56%,100%{transform:translate(-50%,-50%) rotate(0deg)}64%{transform:translate(-50%,-50%) rotate(-1deg)}72%{transform:translate(-50%,-50%) rotate(1.1deg)}80%{transform:translate(-50%,-48.9%) scaleY(.992)}90%{transform:translate(-50%,-50%) rotate(0deg)}}
@media (prefers-reduced-motion:reduce){.az-lab-orbit-host .direction{will-change:auto!important}.az-lab-vitruvian-static{opacity:1!important}#az-lab-vitruvian-motion .az-lab-vitruvian-part{display:none!important}.az-lab-vitruvian-loincloth{animation:none!important;will-change:auto!important}}"""
if motion_old not in s:
    raise SystemExit('STOP: reduced-motion marker not found')
s = s.replace(motion_old, motion_new, 1)

protected = [
    'const INNER_DURATION=63000;',
    'const OUTER_DURATION=114000;',
    "const innerAngles=[180,270,0,90];",
    "const outerAngles=[157.5,202.5,247.5,292.5,337.5,22.5,67.5,112.5];",
    'innerRx:r.width*.55,innerRy:r.height*.44,outerRx:r.width*.73,outerRy:r.height*.57,innerTilt:18,outerTilt:14',
    '.az-lab-right-badge-shift{transform:translateX(-37.8px)!important}',
    ".az-lab-orbit-host .stage{transform:scale(.724137931)!important;transform-origin:center center!important}",
    "font-size',viewportWidth>=1181?'15':'12'",
    "dur','5.8s",
    "textPath.textContent='Не лечим по отдельности. Понимаем, как всё связано в вашем организме'",
]
for item in protected:
    if item not in s:
        raise SystemExit('STOP: protected marker missing: ' + item)

for item in ['addVitruvianMotion(doc);','azLabVitruvianArmLeft','azLabVitruvianArmRight','azLabVitruvianLegLeft','azLabVitruvianLegRight','az-lab-vitruvian-loincloth']:
    if item not in s:
        raise SystemExit('STOP: motion marker missing: ' + item)

p.write_text(s, encoding='utf-8')
