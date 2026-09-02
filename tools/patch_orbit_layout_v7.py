from pathlib import Path
import re

p = Path('interface-lab/index.html')
s = p.read_text(encoding='utf-8')

marker = '/* orbit-layout-v7 — две живые траектории + зеркальные меню по 5 */'
if marker not in s:
    css = r'''

/* orbit-layout-v7 — две живые траектории + зеркальные меню по 5 */
@media (min-width:1181px){
  .map-shell{height:790px!important;margin-top:22px!important;overflow:visible!important}
  .map{
    position:absolute!important;
    left:50%!important;
    top:0!important;
    width:1120px!important;
    height:730px!important;
    padding:0!important;
    display:block!important;
    transform:translateX(-50%)!important;
    overflow:visible!important;
  }
  .stage{
    left:610px!important;
    top:350px!important;
    width:410px!important;
    height:410px!important;
    transform:translate(-50%,-50%)!important;
  }
  .map .ring{display:none!important}
  .relation-panel{display:none!important}

  .right .specialists-badge,
  .right .side-menu-badge{display:none!important}

  .az-orbit-svg{
    position:absolute!important;
    inset:0!important;
    width:1120px!important;
    height:730px!important;
    overflow:visible!important;
    z-index:3!important;
    pointer-events:none!important;
  }
  .az-orbit-path{fill:none!important;vector-effect:non-scaling-stroke!important;stroke-linecap:round!important;stroke-linejoin:round!important}
  .az-orbit-path.outer{stroke:rgba(181,150,98,.30)!important;stroke-width:1.25!important}
  .az-orbit-path.inner{stroke:rgba(68,99,79,.22)!important;stroke-width:1.15!important}

  .map .direction.az-orbit-node{
    position:absolute!important;
    left:var(--draft-left)!important;
    top:var(--draft-top)!important;
    width:116px!important;
    height:92px!important;
    min-height:92px!important;
    margin:0!important;
    transform:translate(-50%,-50%)!important;
    padding:8px!important;
    font-size:9.6px!important;
    line-height:1.12!important;
    font-weight:600!important;
    color:#30372f!important;
    text-shadow:none!important;
    z-index:8!important;
  }

  .az-left-placeholder-menu,
  .az-right-draft-menu{
    position:absolute!important;
    z-index:17!important;
    width:350px!important;
    display:grid!important;
    gap:18px!important;
    pointer-events:none!important;
  }
  .az-left-placeholder-menu{left:4px!important;top:250px!important}
  .az-right-draft-menu{right:4px!important;top:360px!important}

  .az-left-placeholder,
  .az-right-draft-item{
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

  .az-left-placeholder:nth-child(1){margin-left:0!important}
  .az-left-placeholder:nth-child(2){margin-left:28px!important}
  .az-left-placeholder:nth-child(3){margin-left:56px!important}
  .az-left-placeholder:nth-child(4){margin-left:84px!important}
  .az-left-placeholder:nth-child(5){margin-left:112px!important}

  .az-right-draft-item{justify-self:end!important}
  .az-right-draft-item:nth-child(1){margin-right:0!important}
  .az-right-draft-item:nth-child(2){margin-right:28px!important}
  .az-right-draft-item:nth-child(3){margin-right:56px!important}
  .az-right-draft-item:nth-child(4){margin-right:84px!important}
  .az-right-draft-item:nth-child(5){margin-right:112px!important}
}
'''
    needle = '\n\n`;\n\nfunction clearDraft'
    if needle not in s:
        raise SystemExit('responsiveDraft end marker not found')
    s = s.replace(needle, css + needle, 1)

if "doc.querySelector('.az-right-draft-menu')?.remove();" not in s:
    old = "  doc.querySelector('.az-left-placeholder-menu')?.remove();\n"
    new = old + "  doc.querySelector('.az-right-draft-menu')?.remove();\n"
    if old not in s:
        raise SystemExit('clearDraft menu cleanup marker not found')
    s = s.replace(old, new, 1)

old_left = "leftMenu.innerHTML='<div class=\"radial-badge az-left-placeholder\">заглушка</div><div class=\"radial-badge az-left-placeholder\">заглушка</div><div class=\"radial-badge az-left-placeholder\">заглушка</div><div class=\"radial-badge az-left-placeholder\">заглушка</div>';"
new_left = "leftMenu.innerHTML='<div class=\"radial-badge az-left-placeholder\">заглушка</div><div class=\"radial-badge az-left-placeholder\">заглушка</div><div class=\"radial-badge az-left-placeholder\">заглушка</div><div class=\"radial-badge az-left-placeholder\">заглушка</div><div class=\"radial-badge az-left-placeholder\">заглушка</div>';"
if old_left in s:
    s = s.replace(old_left, new_left, 1)
elif new_left not in s:
    raise SystemExit('left placeholder HTML marker not found')

if "const rightMenu=doc.createElement('div');" not in s:
    needle = "    page.appendChild(leftMenu);\n  }\n\n  const map=doc.querySelector('.map');"
    insert = """    page.appendChild(leftMenu);\n  }\n\n  if(page && !doc.querySelector('.az-right-draft-menu')){\n    const rightMenu=doc.createElement('div');\n    rightMenu.className='az-right-draft-menu';\n    rightMenu.setAttribute('aria-label','Разделы сайта');\n    rightMenu.innerHTML='<div class=\"radial-badge az-right-draft-item\">команда</div><div class=\"radial-badge az-right-draft-item\">о клинике</div><div class=\"radial-badge az-right-draft-item\">пациенту</div><div class=\"radial-badge az-right-draft-item\">лаборатория</div><div class=\"radial-badge az-right-draft-item\">контакты</div>';\n    page.appendChild(rightMenu);\n  }\n\n  const map=doc.querySelector('.map');"""
    if needle not in s:
        raise SystemExit('right menu insertion marker not found')
    s = s.replace(needle, insert, 1)

new_svg = "svg.innerHTML='<path class=\"az-orbit-path outer\" d=\"M470 58 C600 8 790 22 900 92 C996 154 1000 247 944 322 C895 388 931 473 868 555 C793 651 635 709 507 664 C404 628 356 553 404 486 C447 425 350 379 378 300 C407 220 354 138 470 58 Z\"/><path class=\"az-orbit-path inner\" d=\"M570 108 C696 78 823 124 855 220 C883 305 842 365 806 420 C758 494 770 555 681 604 C593 652 483 605 438 526 C394 449 437 397 423 332 C408 258 444 153 570 108 Z\"/>';"
s, n = re.subn(r"svg\.innerHTML='<path class=\\\"az-orbit-path outer\\\".*?';", new_svg, s, count=1, flags=re.S)
if n != 1:
    raise SystemExit(f'orbit SVG replacement count={n}')

new_coords = """const coords={
    'Функциональная стоматология':['570','108','inner'],
    'Ортодонтия':['855','220','inner'],
    'Остеопатия':['681','604','inner'],
    'Терапия':['423','332','inner'],
    'ИГГТ':['470','58','outer'],
    'Нутрициология':['760','58','outer'],
    'Гастроэнтерология':['944','210','outer'],
    'Превентивная медицина':['900','430','outer'],
    'Миофункциональная терапия':['820','600','outer'],
    'Детская неврология':['610','685','outer'],
    'Нейропсихология':['430','610','outer'],
    'DIERS диагностика':['382','205','outer']
  };"""
s, n = re.subn(r"const coords=\{.*?\n  \};", new_coords, s, count=1, flags=re.S)
if n != 1:
    raise SystemExit(f'coords replacement count={n}')

s = s.replace("Черновик: планшет — 2 статичные орбиты v6", "Черновик: планшет — 2 статичные орбиты v7")
s = s.replace("Черновик: ПК — 2 статичные орбиты v6", "Черновик: ПК — 2 статичные орбиты v7")

p.write_text(s, encoding='utf-8')
