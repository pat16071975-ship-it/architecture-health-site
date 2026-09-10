from pathlib import Path

p = Path('interface-lab/index.html')
s = p.read_text(encoding='utf-8')

replacements = [
    (
        "const innerNames=['Функциональная стоматология','Ортодонтия','Остеопатия','Терапия'];",
        "const innerNames=['Функциональная стоматология','Ортодонтия','Стоматология ортопедия','Стоматология хирургия','Стоматология терапия'];"
    ),
    (
        "const outerNames=['ИГГТ','DIERS диагностика','Нейропсихология','Детская неврология','Миофункциональная терапия','Превентивная медицина','Гастроэнтерология','Нутрициология'];",
        "const outerNames=['ИГГТ','DIERS диагностика','Нейропсихология','Детская неврология','Миофункциональная терапия','Превентивная медицина','Гастроэнтерология','Нутрициология','Остеопатия'];"
    ),
    (
        "const innerAngles=[180,270,0,90];",
        "const innerAngles=[180,252,324,36,108];"
    ),
    (
        "const outerAngles=[157.5,202.5,247.5,292.5,337.5,22.5,67.5,112.5];",
        "const outerAngles=[160,200,240,280,320,0,40,80,120];"
    ),
    (
        "  'Терапия':'/architecture-health-site/assets/lab-planets/planet-12-therapy.png?v=20260904-3',",
        "  'Стоматология терапия':'/architecture-health-site/assets/lab-planets/planet-12-therapy.png?v=20260904-3',\n  'Стоматология ортопедия':'/architecture-health-site/assets/lab-planets/planet-13-dentistry-orthopedics.svg?v=20260910-1',\n  'Стоматология хирургия':'/architecture-health-site/assets/lab-planets/planet-14-dentistry-surgery.svg?v=20260910-1',"
    ),
    (
        "  doc.querySelector('.az-lab-direction-info')?.remove();doc.querySelectorAll('.az-lab-orbit-host').forEach(el=>el.classList.remove('az-lab-orbit-host'));",
        "  doc.querySelector('.az-lab-direction-info')?.remove();doc.querySelectorAll('.az-lab-generated-direction').forEach(el=>el.remove());doc.querySelectorAll('.az-lab-orbit-host').forEach(el=>el.classList.remove('az-lab-orbit-host'));"
    ),
]

for old, new in replacements:
    if old not in s:
        raise SystemExit('STOP: required marker not found: ' + old[:80])
    s = s.replace(old, new, 1)

old_prepare = """  const prepare=(names,orbitClass)=>names.map(name=>{
    const el=byName.get(norm(name))||(name==='Терапия'?byName.get(norm('Стоматология')):null);
    if(!el)return null;
    if(el.dataset.azOriginalStyle===undefined)el.dataset.azOriginalStyle=el.getAttribute('style')||'';
    if(el.dataset.azOriginalHtml===undefined)el.dataset.azOriginalHtml=el.innerHTML;
    el.classList.add('az-lab-planet',orbitClass);el.dataset.azDirectionName=name;
    const artUrl=PLANET_ARTS[name]||'';
    const labelHtml=name==='Терапия'?'Стоматология':el.dataset.azOriginalHtml;
    el.innerHTML='<span class=\"az-lab-planet-art\" aria-hidden=\"true\"></span><span class=\"az-lab-planet-label\">'+labelHtml+'</span>';
    const art=el.querySelector('.az-lab-planet-art');
    if(art)art.style.setProperty('--az-planet-mask','url(\"'+artUrl+'\")');
    el.style.setProperty('transform','translate(-50%,-50%)','important');
    return el;
  }).filter(Boolean);"""

new_prepare = """  const prepare=(names,orbitClass)=>names.map(name=>{
    let el=byName.get(norm(name));
    if(name==='Стоматология терапия')el=el||byName.get(norm('Терапия'))||byName.get(norm('Стоматология'));
    if(!el){
      const planetHost=doc.querySelector('.map')||doc.querySelector('.stage')?.parentElement;if(!planetHost)return null;
      el=doc.createElement('div');el.className='direction az-lab-generated-direction';el.textContent=name;planetHost.appendChild(el);byName.set(norm(name),el);
    }
    if(el.dataset.azOriginalStyle===undefined)el.dataset.azOriginalStyle=el.getAttribute('style')||'';
    if(el.dataset.azOriginalHtml===undefined)el.dataset.azOriginalHtml=el.innerHTML;
    el.classList.add('az-lab-planet',orbitClass);el.dataset.azDirectionName=name;
    const artUrl=PLANET_ARTS[name]||'';
    const labelHtml=name==='Стоматология терапия'?'Стоматология<br>терапия':name==='Стоматология ортопедия'?'Стоматология<br>ортопедия':name==='Стоматология хирургия'?'Стоматология<br>хирургия':el.dataset.azOriginalHtml;
    el.innerHTML='<span class=\"az-lab-planet-art\" aria-hidden=\"true\"></span><span class=\"az-lab-planet-label\">'+labelHtml+'</span>';
    const art=el.querySelector('.az-lab-planet-art');
    if(art)art.style.setProperty('--az-planet-mask','url(\"'+artUrl+'\")');
    el.style.setProperty('transform','translate(-50%,-50%)','important');
    return el;
  }).filter(Boolean);"""

if old_prepare not in s:
    raise SystemExit('STOP: collectPlanets prepare block not found')
s = s.replace(old_prepare, new_prepare, 1)

# Keep existing generic dentistry content untouched for now: only the visible planet name changes.
# New orthopedics/surgery planets intentionally do not invent clinical copy or routes.

protected = [
    'const INNER_DURATION=63000;',
    'const OUTER_DURATION=114000;',
    'innerRx:r.width*.55,innerRy:r.height*.44,outerRx:r.width*.73,outerRy:r.height*.57,innerTilt:18,outerTilt:14',
    '.az-lab-right-badge-shift{transform:translateX(-37.8px)!important}',
    ".az-lab-orbit-host .stage{transform:scale(.724137931)!important;transform-origin:center center!important}",
    ".az-lab-orbit-host .stage{transform:scale(.568965517)!important;transform-origin:center center!important}",
    "textPath.textContent='Не лечим по отдельности. Понимаем, как всё связано в вашем организме'",
    "dur','5.8s'",
]
for marker in protected:
    if marker not in s:
        raise SystemExit('STOP: protected viewer marker missing: ' + marker)

required = [
    "const innerNames=['Функциональная стоматология','Ортодонтия','Стоматология ортопедия','Стоматология хирургия','Стоматология терапия'];",
    "'Остеопатия'];",
    "const innerAngles=[180,252,324,36,108];",
    "const outerAngles=[160,200,240,280,320,0,40,80,120];",
    'planet-13-dentistry-orthopedics.svg',
    'planet-14-dentistry-surgery.svg',
    'az-lab-generated-direction',
]
for marker in required:
    if marker not in s:
        raise SystemExit('STOP: requested composition marker missing: ' + marker)

p.write_text(s, encoding='utf-8')
