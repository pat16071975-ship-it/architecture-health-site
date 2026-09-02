from pathlib import Path
import re

p = Path('interface-lab/index.html')
s = p.read_text(encoding='utf-8')
marker = '/* orbit-layout-v5 — две статичные неровные траектории */'
if marker in s:
    raise SystemExit(0)

css = r'''

/* orbit-layout-v5 — две статичные неровные траектории */
@media (min-width:721px){
  .map-shell{position:relative!important;overflow:visible!important}
  .map{
    position:absolute!important;left:50%!important;top:0!important;
    width:980px!important;height:700px!important;padding:0!important;display:block!important;
    transform:translateX(-50%)!important;transform-origin:top center!important;
  }
  .map .ring{display:none!important}
  .az-orbit-svg{
    position:absolute;inset:0;width:980px;height:700px;z-index:1;
    overflow:visible;pointer-events:none;
  }
  .az-orbit-path{fill:none;vector-effect:non-scaling-stroke;stroke-linecap:round;stroke-linejoin:round}
  .az-orbit-path-outer{stroke:rgba(181,150,98,.32);stroke-width:1.15}
  .az-orbit-path-inner{stroke:rgba(68,99,79,.20);stroke-width:1.05}
  .stage{
    left:50%!important;top:350px!important;width:400px!important;height:400px!important;
    transform:translate(-50%,-50%)!important;z-index:2!important;
  }
  .stage:before{inset:26px!important}
  .relation-panel{display:none!important}

  .map .direction{
    position:absolute!important;width:118px!important;height:96px!important;min-height:96px!important;
    margin:0!important;padding:9px 8px!important;transform:translate(-50%,-50%)!important;
    border-radius:53% 47% 51% 49% / 46% 54% 48% 52%!important;
    color:#26362d!important;font-family:'Montserrat',Inter,Segoe UI,Arial,sans-serif!important;
    font-size:10px!important;line-height:1.12!important;font-weight:600!important;
    text-shadow:none!important;overflow:hidden!important;z-index:8!important;
    transition:border-color .2s,box-shadow .2s,color .2s,background .2s!important;
  }
  .map .direction:nth-of-type(3n+1){border-radius:51% 49% 54% 46% / 47% 53% 49% 51%!important}
  .map .direction:nth-of-type(3n+2){border-radius:55% 45% 49% 51% / 50% 46% 54% 50%!important}
  .map .direction:nth-of-type(3n){border-radius:48% 52% 52% 48% / 54% 49% 51% 46%!important}
  .map .direction::before,.map .direction::after{content:none!important;display:none!important;background:none!important}

  .map .direction.az-orbit-inner{
    background:linear-gradient(180deg,rgba(239,246,240,.98),rgba(250,247,241,.98))!important;
    background-image:linear-gradient(180deg,rgba(239,246,240,.98),rgba(250,247,241,.98))!important;
    border:1.5px solid rgba(181,150,98,.68)!important;
    box-shadow:0 9px 22px rgba(71,58,38,.10),inset 0 1px rgba(255,255,255,.72)!important;
  }
  .map .direction.az-orbit-outer{
    background:linear-gradient(180deg,rgba(250,247,241,.99),rgba(245,238,226,.98))!important;
    background-image:linear-gradient(180deg,rgba(250,247,241,.99),rgba(245,238,226,.98))!important;
    border:1.5px solid rgba(181,150,98,.48)!important;
    box-shadow:0 7px 18px rgba(71,58,38,.07),inset 0 1px rgba(255,255,255,.62)!important;
  }
  .map .direction:hover,.map .direction.active{
    color:#355640!important;border-color:rgba(181,150,98,.82)!important;
    box-shadow:0 10px 24px rgba(71,58,38,.12)!important;
  }

  /* внутренняя орбита: функциональная стоматология, ортодонтия, остеопатия, терапия */
  .map .direction:nth-of-type(1){left:490px!important;top:115px!important}
  .map .direction:nth-of-type(2){left:755px!important;top:325px!important}
  .map .direction:nth-of-type(3){left:490px!important;top:575px!important}
  .map .direction:nth-of-type(8){left:230px!important;top:350px!important}

  /* внешняя орбита: миофункциональная, превентивная, гастро, нутрициология, детская неврология, нейропсихология, DIERS, ИГГТ */
  .map .direction:nth-of-type(12){left:330px!important;top:65px!important}
  .map .direction:nth-of-type(7){left:700px!important;top:70px!important}
  .map .direction:nth-of-type(6){left:865px!important;top:225px!important}
  .map .direction:nth-of-type(5){left:850px!important;top:495px!important}
  .map .direction:nth-of-type(4){left:680px!important;top:635px!important}
  .map .direction:nth-of-type(9){left:300px!important;top:635px!important}
  .map .direction:nth-of-type(10){left:120px!important;top:500px!important}
  .map .direction:nth-of-type(11){left:115px!important;top:235px!important}
}

@media (min-width:1181px){
  .map-shell{height:730px!important;margin-top:24px!important}
}

@media (min-width:721px) and (max-width:1180px){
  .map-shell{height:610px!important;margin-top:62px!important}
  .map{transform:translateX(-50%) scale(.82)!important}
  .map .direction{font-size:9.2px!important}
}
'''

needle = '\n\n`;\n\nfunction clearDraft'
if needle not in s:
    raise SystemExit('responsiveDraft end marker not found')
s = s.replace(needle, css + needle, 1)

old_clear = "function clearDraft(doc){doc.getElementById('az-lab-responsive-style')?.remove()}"
new_clear = """function clearDraft(doc){
  doc.getElementById('az-lab-responsive-style')?.remove();
  doc.getElementById('az-lab-orbits-svg')?.remove();
  doc.querySelectorAll('.map .direction').forEach(el=>el.classList.remove('az-orbit-inner','az-orbit-outer'));
}"""
if old_clear not in s:
    raise SystemExit('clearDraft marker not found')
s = s.replace(old_clear, new_clear, 1)

old_apply = "  doc.head.appendChild(style);\n  const w=shell.getBoundingClientRect().width;"
new_apply = """  doc.head.appendChild(style);
  const dirs=[...doc.querySelectorAll('.map .direction')];
  const inner=new Set([0,1,2,7]);
  dirs.forEach((el,i)=>el.classList.add(inner.has(i)?'az-orbit-inner':'az-orbit-outer'));
  const map=doc.querySelector('.map');
  if(map){
    const svg=doc.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.id='az-lab-orbits-svg';
    svg.setAttribute('class','az-orbit-svg');
    svg.setAttribute('viewBox','0 0 980 700');
    svg.setAttribute('aria-hidden','true');
    svg.innerHTML='<path class="az-orbit-path az-orbit-path-outer" d="M330 65 C520 25 760 55 865 225 C930 340 900 515 680 635 C510 705 260 670 120 500 C40 365 55 170 330 65 Z"/><path class="az-orbit-path az-orbit-path-inner" d="M490 105 C650 95 790 195 760 350 C745 500 620 585 490 575 C340 590 205 500 225 350 C235 210 330 120 490 105 Z"/>';
    map.prepend(svg);
  }
  const w=shell.getBoundingClientRect().width;"""
if old_apply not in s:
    raise SystemExit('applyDraft marker not found')
s = s.replace(old_apply, new_apply, 1)

s = re.sub(
    r"status\.textContent=w<=720\?[^;]+;",
    "status.textContent=w<=720?'Черновик: телефон — прежняя мобильная сетка':w<=1180?'Черновик: планшет — 2 статичные орбиты':'Черновик: ПК — 2 статичные орбиты';",
    s,
    count=1
)

p.write_text(s, encoding='utf-8')
