from pathlib import Path

p = Path('interface-lab/index.html')
s = p.read_text(encoding='utf-8')

old_clear = """function clearDraft(doc){
  doc.getElementById('az-lab-responsive-style')?.remove();
  doc.getElementById('az-lab-orbits-svg')?.remove();
  doc.querySelectorAll('.map .direction').forEach(el=>el.classList.remove('az-orbit-inner','az-orbit-outer'));
}"""
new_clear = """function clearDraft(doc){
  doc.getElementById('az-lab-responsive-style')?.remove();
  doc.querySelectorAll('.az-orbit-svg').forEach(el=>el.remove());
  doc.querySelector('.az-left-placeholder-menu')?.remove();
  doc.querySelectorAll('.map .direction').forEach(el=>{
    el.classList.remove('az-orbit-inner','az-orbit-outer','az-orbit-node','az-inner-orbit','az-outer-orbit');
    el.style.removeProperty('--draft-left');
    el.style.removeProperty('--draft-top');
  });
}"""
if old_clear not in s:
    raise SystemExit('clearDraft source block not found')
s = s.replace(old_clear, new_clear, 1)

old_dup = """  const dirs=[...doc.querySelectorAll('.map .direction')];
  const inner=new Set([0,1,2,7]);
  dirs.forEach((el,i)=>el.classList.add(inner.has(i)?'az-orbit-inner':'az-orbit-outer'));
  const map=doc.querySelector('.map');
  if(map){
    const svg=doc.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.id='az-lab-orbits-svg';
    svg.setAttribute('class','az-orbit-svg');
    svg.setAttribute('viewBox','0 0 980 700');
    svg.setAttribute('aria-hidden','true');
    svg.innerHTML='<path class=\"az-orbit-path az-orbit-path-outer\" d=\"M330 65 C520 25 760 55 865 225 C930 340 900 515 680 635 C510 705 260 670 120 500 C40 365 55 170 330 65 Z\"/><path class=\"az-orbit-path az-orbit-path-inner\" d=\"M490 105 C650 95 790 195 760 350 C745 500 620 585 490 575 C340 590 205 500 225 350 C235 210 330 120 490 105 Z\"/>';
    map.prepend(svg);
  }
"""
if old_dup not in s:
    raise SystemExit('obsolete duplicate orbit block not found')
s = s.replace(old_dup, '', 1)

p.write_text(s, encoding='utf-8')
