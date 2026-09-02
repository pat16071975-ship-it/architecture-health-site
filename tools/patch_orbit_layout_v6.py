from pathlib import Path

p = Path('interface-lab/index.html')
s = p.read_text(encoding='utf-8')
marker = '/* orbit-layout-v6 — неровные статичные траектории + левые заглушки */'

if marker not in s:
    css = r'''

/* orbit-layout-v6 — неровные статичные траектории + левые заглушки */
@media (min-width:1181px){
  .map-shell{height:770px!important;margin-top:22px!important;overflow:visible!important}
  .map{
    left:calc(50% + 82px)!important;
    top:0!important;
    width:1100px!important;
    height:720px!important;
    transform:translateX(-50%)!important;
    overflow:visible!important;
  }
  .map .ring{display:none!important}
  .stage{
    left:550px!important;
    top:350px!important;
    width:410px!important;
    height:410px!important;
    transform:translate(-50%,-50%)!important;
  }
  .relation-panel{display:none!important}
  .az-orbit-svg{
    position:absolute;
    inset:0;
    width:1100px;
    height:720px;
    overflow:visible;
    z-index:3;
    pointer-events:none;
  }
  .az-orbit-path{
    fill:none;
    vector-effect:non-scaling-stroke;
  }
  .az-orbit-path.outer{
    stroke:rgba(181,150,98,.30);
    stroke-width:1.25;
  }
  .az-orbit-path.inner{
    stroke:rgba(68,99,79,.21);
    stroke-width:1.15;
  }
  .map .direction.az-orbit-node{
    position:absolute!important;
    left:var(--draft-left)!important;
    top:var(--draft-top)!important;
    width:116px!important;
    height:92px!important;
    min-height:92px!important;
    margin:0!important;
    transform:translate(-50%,-50%)!important;
    border-radius:53% 47% 51% 49% / 47% 54% 46% 53%!important;
    padding:8px 8px!important;
    color:#30372f!important;
    text-shadow:none!important;
    font-size:9.6px!important;
    line-height:1.12!important;
    font-weight:600!important;
    box-shadow:0 7px 18px rgba(71,58,38,.065)!important;
    z-index:8!important;
    overflow:hidden!important;
  }
  .map .direction.az-inner-orbit{
    background:linear-gradient(155deg,rgba(233,242,235,.99) 0%,rgba(247,246,238,.99) 54%,rgba(244,238,226,.99) 100%)!important;
    border:1px solid rgba(68,99,79,.31)!important;
  }
  .map .direction.az-outer-orbit{
    background:linear-gradient(155deg,rgba(251,248,241,.99) 0%,rgba(248,242,231,.99) 58%,rgba(242,234,218,.99) 100%)!important;
    border:1px solid rgba(181,150,98,.40)!important;
  }
  .map .direction.az-orbit-node::before,
  .map .direction.az-orbit-node::after{content:none!important;display:none!important;background:none!important}

  .az-left-placeholder-menu{
    position:absolute;
    left:4px;
    top:355px;
    z-index:17;
    width:330px;
    display:grid;
    gap:22px;
    pointer-events:none;
  }
  .az-left-placeholder{
    position:relative!important;
    width:230px!important;
    min-height:48px!important;
    padding:13px 18px!important;
    display:flex!important;
    align-items:center!important;
    justify-content:center!important;
    border-radius:10px!important;
    background:rgba(250,247,241,.96)!important;
    border:1px solid rgba(181,150,98,.42)!important;
    box-shadow:0 9px 24px rgba(71,58,38,.08)!important;
    color:#354039!important;
    font:600 17px/1.1 'Montserrat',Inter,Segoe UI,Arial,sans-serif!important;
    text-align:center!important;
  }
  .az-left-placeholder:nth-child(2){margin-left:34px!important}
  .az-left-placeholder:nth-child(3){margin-left:68px!important}
  .az-left-placeholder:nth-child(4){margin-left:102px!important}
}

@media (min-width:721px) and (max-width:1180px){
  .az-left-placeholder-menu{display:none!important}
  .az-orbit-svg{display:none!important}
}
'''
    needle = '\n\n`;\n\nfunction clearDraft'
    if needle not in s:
        raise SystemExit('responsiveDraft end marker not found')
    s = s.replace(needle, css + needle, 1)

old_clear = "function clearDraft(doc){doc.getElementById('az-lab-responsive-style')?.remove()}"
new_clear = """function clearDraft(doc){
  doc.getElementById('az-lab-responsive-style')?.remove();
  doc.querySelector('.az-orbit-svg')?.remove();
  doc.querySelector('.az-left-placeholder-menu')?.remove();
  doc.querySelectorAll('.direction').forEach(el=>{
    el.classList.remove('az-orbit-node','az-inner-orbit','az-outer-orbit');
    el.style.removeProperty('--draft-left');
    el.style.removeProperty('--draft-top');
  });
}"""
if old_clear in s:
    s = s.replace(old_clear, new_clear, 1)
elif 'az-left-placeholder-menu' not in s.split('function applyDraft',1)[0]:
    raise SystemExit('clearDraft target not found')

apply_needle = "  doc.head.appendChild(style);\n"
apply_insert = r'''  doc.head.appendChild(style);

  const page=doc.querySelector('.page');
  if(page && !doc.querySelector('.az-left-placeholder-menu')){
    const leftMenu=doc.createElement('div');
    leftMenu.className='az-left-placeholder-menu';
    leftMenu.setAttribute('aria-label','Будущие разделы');
    leftMenu.innerHTML='<div class="radial-badge az-left-placeholder">заглушка</div><div class="radial-badge az-left-placeholder">заглушка</div><div class="radial-badge az-left-placeholder">заглушка</div><div class="radial-badge az-left-placeholder">заглушка</div>';
    page.appendChild(leftMenu);
  }

  const map=doc.querySelector('.map');
  if(map && !map.querySelector('.az-orbit-svg')){
    const svg=doc.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('class','az-orbit-svg');
    svg.setAttribute('viewBox','0 0 1100 720');
    svg.setAttribute('aria-hidden','true');
    svg.innerHTML='<path class="az-orbit-path outer" d="M285 67 C455 8 790 20 965 165 C1060 245 1082 424 997 545 C900 679 617 716 405 646 C237 590 160 458 155 337 C150 209 192 122 285 67 Z"/><path class="az-orbit-path inner" d="M432 137 C572 80 781 109 866 226 C939 326 912 472 798 557 C672 650 471 617 360 516 C266 430 260 310 316 230 C346 185 386 154 432 137 Z"/>';
    map.prepend(svg);
  }

  const coords={
    'Функциональная стоматология':['550','142','inner'],
    'Ортодонтия':['842','302','inner'],
    'Остеопатия':['610','568','inner'],
    'Терапия':['330','338','inner'],
    'ИГГТ':['455','72','outer'],
    'Нутрициология':['785','92','outer'],
    'Гастроэнтерология':['982','225','outer'],
    'Превентивная медицина':['1010','430','outer'],
    'Миофункциональная терапия':['805','628','outer'],
    'Детская неврология':['442','646','outer'],
    'Нейропсихология':['220','475','outer'],
    'DIERS диагностика':['225','208','outer']
  };
  doc.querySelectorAll('.direction').forEach(el=>{
    const name=el.textContent.replace(/\s+/g,' ').trim();
    const c=coords[name];
    if(!c)return;
    el.classList.add('az-orbit-node',c[2]==='inner'?'az-inner-orbit':'az-outer-orbit');
    el.style.setProperty('--draft-left',c[0]+'px');
    el.style.setProperty('--draft-top',c[1]+'px');
  });
'''
if apply_insert not in s:
    if apply_needle not in s:
        raise SystemExit('applyDraft insertion target not found')
    s = s.replace(apply_needle, apply_insert, 1)

s = s.replace("'Черновик: планшет — 2 статичные орбиты'", "'Черновик: планшет — 2 статичные орбиты v6'")
s = s.replace("'Черновик: ПК — 2 статичные орбиты'", "'Черновик: ПК — 2 статичные орбиты v6'")
s = s.replace("'Черновик: планшет — две статичные орбиты'", "'Черновик: планшет — 2 статичные орбиты v6'")
s = s.replace("'Черновик: ПК — две статичные орбиты'", "'Черновик: ПК — 2 статичные орбиты v6'")

p.write_text(s, encoding='utf-8')
