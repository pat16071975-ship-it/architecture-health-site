from pathlib import Path

p=Path('interface-lab/index.html')
s=p.read_text(encoding='utf-8')

# Этот скрипт запускается после восстановления Interface Lab из последней нормальной
# адаптивной версии до орбитальных экспериментов (commit 93057cb...).

desktop_css=r'''

/* orbit-layout-v9 — два ровных эллипса, отдельные планеты и меню */
@media (min-width:1181px){
  .page{position:relative!important}
  .map-shell{height:760px!important;margin-top:22px!important;overflow:visible!important;position:relative!important}
  .map{
    position:absolute!important;left:50%!important;top:0!important;
    width:920px!important;height:720px!important;padding:0!important;display:block!important;
    transform:translateX(-50%)!important;overflow:visible!important;
  }
  .map .ring{display:none!important}
  .relation-panel{display:none!important}
  .stage{
    left:460px!important;top:355px!important;width:410px!important;height:410px!important;
    transform:translate(-50%,-50%)!important;z-index:2!important;
  }
  .stage:before{inset:27px!important}

  /* В ПК-черновике используем собственные одинаковые планеты, чтобы базовые карточки
     сайта не вмешивались в геометрию орбит. */
  .map .direction{display:none!important}

  .az-orbit-svg{
    position:absolute!important;inset:0!important;width:920px!important;height:720px!important;
    overflow:visible!important;pointer-events:none!important;z-index:1!important;
  }
  .az-orbit-path{fill:none!important;vector-effect:non-scaling-stroke!important}
  .az-orbit-path.outer{stroke:rgba(181,150,98,.34)!important;stroke-width:1.2!important}
  .az-orbit-path.inner{stroke:rgba(68,99,79,.24)!important;stroke-width:1.1!important}

  .az-planet{
    position:absolute!important;width:116px!important;height:92px!important;
    transform:translate(-50%,-50%)!important;
    display:flex!important;align-items:center!important;justify-content:center!important;
    padding:8px 9px!important;margin:0!important;
    border-radius:53% 47% 51% 49% / 47% 54% 46% 53%!important;
    color:#30372f!important;font-family:'Montserrat',Inter,Segoe UI,Arial,sans-serif!important;
    font-size:9.6px!important;line-height:1.12!important;font-weight:600!important;text-align:center!important;
    text-shadow:none!important;z-index:8!important;box-shadow:0 7px 18px rgba(71,58,38,.07)!important;
  }
  .az-planet.inner{
    background:linear-gradient(180deg,rgba(239,246,240,.98),rgba(250,247,241,.98))!important;
    border:1.5px solid rgba(181,150,98,.68)!important;
  }
  .az-planet.outer{
    background:linear-gradient(180deg,rgba(250,247,241,.99),rgba(245,238,226,.98))!important;
    border:1.5px solid rgba(181,150,98,.48)!important;
  }

  /* Слева — прежняя утверждённая геометрия и размер. */
  .az-left-placeholder-menu{
    position:absolute!important;left:4px!important;top:355px!important;z-index:17!important;
    width:330px!important;display:grid!important;gap:22px!important;pointer-events:none!important;
  }
  .az-left-placeholder,
  .az-right-draft-item{
    position:relative!important;width:230px!important;min-height:48px!important;padding:13px 18px!important;
    display:flex!important;align-items:center!important;justify-content:center!important;
    border-radius:10px!important;background:rgba(250,247,241,.96)!important;
    border:1px solid rgba(181,150,98,.42)!important;box-shadow:0 9px 24px rgba(71,58,38,.08)!important;
    color:#354039!important;font:600 17px/1.1 'Montserrat',Inter,Segoe UI,Arial,sans-serif!important;text-align:center!important;
  }
  .az-left-placeholder:nth-child(1){margin-left:0!important}
  .az-left-placeholder:nth-child(2){margin-left:34px!important}
  .az-left-placeholder:nth-child(3){margin-left:68px!important}
  .az-left-placeholder:nth-child(4){margin-left:102px!important}
  .az-left-placeholder:nth-child(5){margin-left:136px!important}

  /* Справа — отдельная колонка в правой зоне, ниже и зеркально левой; 5 пунктов. */
  .right .specialists-badge,.right .side-menu-badge{display:none!important}
  .az-right-draft-menu{
    position:absolute!important;right:20px!important;top:430px!important;z-index:17!important;
    width:330px!important;display:grid!important;gap:22px!important;pointer-events:none!important;
  }
  .az-right-draft-item{justify-self:end!important}
  .az-right-draft-item:nth-child(1){margin-right:0!important}
  .az-right-draft-item:nth-child(2){margin-right:34px!important}
  .az-right-draft-item:nth-child(3){margin-right:68px!important}
  .az-right-draft-item:nth-child(4){margin-right:102px!important}
  .az-right-draft-item:nth-child(5){margin-right:136px!important}
}
'''

needle='\n`;\n\nfunction clearDraft'
if needle not in s:
    raise SystemExit('responsiveDraft end marker not found')
s=s.replace(needle,desktop_css+needle,1)

runtime=r'''function normalizedName(el){return el.textContent.replace(/\s+/g,' ').trim()}
function clearDraft(doc){
  doc.getElementById('az-lab-responsive-style')?.remove();
  doc.querySelectorAll('.az-orbit-svg,.az-planet,.az-left-placeholder-menu,.az-right-draft-menu').forEach(el=>el.remove());
}
function addMenus(doc){
  const page=doc.querySelector('.page');
  if(!page)return;
  const left=doc.createElement('div');
  left.className='az-left-placeholder-menu';
  left.innerHTML='<div class="az-left-placeholder">заглушка</div>'.repeat(5);
  page.appendChild(left);

  const right=doc.createElement('div');
  right.className='az-right-draft-menu';
  right.innerHTML='<div class="az-right-draft-item">команда</div><div class="az-right-draft-item">о клинике</div><div class="az-right-draft-item">пациенту</div><div class="az-right-draft-item">лаборатория</div><div class="az-right-draft-item">контакты</div>';
  page.appendChild(right);
}
function addOrbits(doc){
  const map=doc.querySelector('.map');
  if(!map)return;

  const svg=doc.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('class','az-orbit-svg');
  svg.setAttribute('viewBox','0 0 920 720');
  svg.setAttribute('aria-hidden','true');
  svg.innerHTML='<ellipse class="az-orbit-path outer" cx="460" cy="355" rx="390" ry="305"/><ellipse class="az-orbit-path inner" cx="460" cy="355" rx="300" ry="255"/>';
  map.prepend(svg);

  const planets=[
    ['Функциональная стоматология','inner',-90],
    ['Ортодонтия','inner',0],
    ['Остеопатия','inner',90],
    ['Терапия','inner',180],
    ['ИГГТ','outer',-112.5],
    ['Нутрициология','outer',-67.5],
    ['Гастроэнтерология','outer',-22.5],
    ['Превентивная медицина','outer',22.5],
    ['Миофункциональная терапия','outer',67.5],
    ['Детская неврология','outer',112.5],
    ['Нейропсихология','outer',157.5],
    ['DIERS диагностика','outer',202.5]
  ];
  const geom={inner:[300,255],outer:[390,305]};
  planets.forEach(([name,kind,deg])=>{
    const rad=deg*Math.PI/180;
    const [rx,ry]=geom[kind];
    const el=doc.createElement('div');
    el.className='az-planet '+kind;
    el.textContent=name;
    el.style.left=(460+rx*Math.cos(rad))+'px';
    el.style.top=(355+ry*Math.sin(rad))+'px';
    map.appendChild(el);
  });
}
function applyDraft(){
  const doc=frame.contentDocument;
  if(!doc)return;
  clearDraft(doc);
  if(mode!=='draft'){
    status.textContent='Оригинал сайта';
    return;
  }
  const style=doc.createElement('style');
  style.id='az-lab-responsive-style';
  style.textContent=responsiveDraft;
  doc.head.appendChild(style);
  const w=shell.getBoundingClientRect().width;
  if(w>1180){
    addMenus(doc);
    addOrbits(doc);
    status.textContent='Черновик: ПК — 2 ровных эллипса, меню 5+5';
  }else{
    status.textContent=w<=720?'Черновик: телефон — блок контактов и всё ниже опущены':'Черновик: планшет — оффер и разделы разведены';
  }
}
'''
start=s.find('function clearDraft(doc)')
end=s.find("frame.addEventListener('load',applyDraft);")
if start<0 or end<0 or end<=start:
    raise SystemExit('runtime markers not found')
s=s[:start]+runtime+s[end:]

# По умолчанию открываем ПК для проверки текущего эксперимента.
s=s.replace('<button class="btn" data-width="1440" type="button">ПК</button>','<button class="btn active" data-width="1440" type="button">ПК</button>',1)
s=s.replace('<button class="btn active" data-width="900" type="button">Планшет</button>','<button class="btn" data-width="900" type="button">Планшет</button>',1)
s=s.replace('width:min(100%,900px)','width:1440px;max-width:100%',1)
s=s.replace('Черновик: планшет и телефон','Черновик: ПК — 2 ровных эллипса, меню 5+5',1)
s=s.replace('src="../"','src="../?lab=v9"',1)

p.write_text(s,encoding='utf-8')
