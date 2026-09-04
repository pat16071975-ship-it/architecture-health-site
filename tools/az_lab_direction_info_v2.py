from pathlib import Path
p=Path('interface-lab/index.html')
s=p.read_text()
original=s
s=s.replace('Черновик: обе группы плашек на 1 см ближе к оси','Черновик: описания направлений внутри центрального круга')
s=s.replace('../?lab=orbits-20260904-9','../?lab=orbits-20260904-11')
anchor="const outerAngles=[157.5,202.5,247.5,292.5,337.5,22.5,67.5,112.5];\n"
assert anchor in s
info="""

const DIRECTION_INFO={
  'Функциональная стоматология':{title:'Функциональная стоматология',items:['щёлкает, устаёт или напрягается челюсть','после ортодонтии или протезирования остался дискомфорт','неудобно жевать, хотя зубы выглядят нормально','напряжение в области лица и челюстей стало привычным'],summary:'Здесь оценивают не только зубы, но и работу челюстей, ВНЧС и мышц как единой функциональной системы.'},
  'Ортодонтия':{title:'Ортодонтия',items:['зубы начали смещаться','вы сильно сжимаете или скрипите зубами','у ребёнка формируется неправильный прикус','ребёнок дышит ртом или часто держит рот приоткрытым'],summary:'Важно вовремя оценить развитие челюстей, дыхание, положение языка и мышечные привычки.'},
  'Остеопатия':{title:'Остеопатия',items:['шея и плечи постоянно напряжены','хочется регулярно разминать спину или шею','дискомфорт в одном месте сопровождается напряжением в другом','неприятные ощущения давно стали привычными'],summary:'Задача — понять, как тело компенсирует ограничения и распределяет нагрузку между разными зонами.'},
  'Терапия':{title:'Стоматология',items:['после лечения зубов красиво, но неудобно жевать','коронки или протезы ощущаются непривычно','зубы снова меняют положение','требуется сложное восстановление нескольких зубов'],summary:'Важно не только восстановить отдельный зуб, но и понять, как в целом должны работать зубы и челюсти.'},
  'DIERS диагностика':{title:'DIERS-диагностика',items:['есть ощущение перекоса или асимметрии тела','спина быстро устаёт','постоянно чувствуется напряжение даже без выраженной боли','важно понять, как тело работает в движении'],summary:'Диагностика помогает увидеть осанку, распределение нагрузки и возможные компенсации тела.'},
  'Миофункциональная терапия':{title:'Миофункциональная терапия',items:['язык часто находится между зубами','есть неправильное глотание','рот часто остаётся приоткрытым','после исправления прикуса старые мышечные привычки сохраняются'],summary:'Работа направлена на функцию языка и мышц, чтобы поддерживать правильную работу зубочелюстной системы.'},
  'Превентивная медицина':{title:'Превентивная медицина',items:['постоянно не хватает энергии','состояние постепенно меняется','появились привычные ощущения, которых раньше не было','хочется понимать здоровье заранее, а не только реагировать на болезнь'],summary:'Задача — определить, чему действительно стоит уделить внимание сейчас и что можно спокойно наблюдать.'},
  'Гастроэнтерология':{title:'Гастроэнтерология',items:['после еды часто возникает тяжесть или вздутие','питание регулярно вызывает дискомфорт','после еды появляется выраженная сонливость','изменения питания не дают понятного результата'],summary:'Задача — разобраться в работе пищеварительной системы и понять, какие симптомы действительно требуют внимания.'},
  'Нутрициология':{title:'Нутрициология',items:['постоянно тянет к сладкому','после еды хочется спать','энергии становится меньше','сложно понять, какое питание подходит именно вам'],summary:'Здесь питание рассматривают не как модную диету, а с учётом состояния и особенностей конкретного человека.'},
  'Детская неврология':{title:'Детская неврология',items:['ребёнок быстро устаёт или тяжело просыпается','стал хуже концентрироваться','часто жалуется на головную боль','появились тики, повторяющиеся движения или заметные изменения поведения'],summary:'Задача — понять, что является вариантом развития, за чем можно наблюдать, а чему стоит уделить внимание.'}
};
"""
s=s.replace(anchor,anchor+info,1)
old="doc.querySelectorAll('.az-lab-orbit-host').forEach(el=>el.classList.remove('az-lab-orbit-host'));"
assert old in s
s=s.replace(old,"doc.querySelector('.az-lab-direction-info')?.remove();"+old,1)
fn='function collectPlanets(doc){\n'
assert fn in s
funcs="""function closeDirectionInfo(doc){doc.querySelector('.az-lab-direction-info')?.remove()}
function showDirectionInfo(doc,name){
  const info=DIRECTION_INFO[name];if(!info)return;
  const stage=doc.querySelector('.stage');if(!stage)return;
  closeDirectionInfo(doc);
  const panel=doc.createElement('section');panel.className='az-lab-direction-info';panel.setAttribute('aria-live','polite');
  panel.innerHTML='<button class="az-lab-direction-close" type="button" aria-label="Закрыть описание">×</button><div class="az-lab-direction-info-inner"><h2>'+info.title+'</h2><p class="az-lab-direction-lead">Стоит обратить внимание, если:</p><ul>'+info.items.map(item=>'<li>'+item+'</li>').join('')+'</ul><p class="az-lab-direction-summary">'+info.summary+'</p><button class="az-lab-direction-more" type="button">Узнать подробнее</button></div>';
  panel.querySelector('.az-lab-direction-close').addEventListener('click',event=>{event.stopPropagation();closeDirectionInfo(doc)});
  stage.appendChild(panel);
}
function enableDirectionInfo(doc,planets){planets.forEach(el=>{const name=el.dataset.azDirectionName;el.onclick=event=>{event.preventDefault();event.stopPropagation();if(DIRECTION_INFO[name])showDirectionInfo(doc,name)}})}
"""
s=s.replace(fn,funcs+fn,1)
name_anchor="el.classList.add('az-lab-planet',orbitClass);"
assert name_anchor in s
s=s.replace(name_anchor,name_anchor+"el.dataset.azDirectionName=name;",1)
cleanup="el.classList.remove('az-lab-planet','az-lab-inner-planet','az-lab-outer-planet')"
assert cleanup in s
s=s.replace(cleanup,"el.onclick=null;delete el.dataset.azDirectionName;"+cleanup,1)
css_anchor=".az-lab-right-badge-shift{transform:translateX(-37.8px)!important}\n"
assert css_anchor in s
css=""".az-lab-right-badge-shift{transform:translateX(-37.8px)!important}
.az-lab-direction-info{position:absolute!important;inset:0!important;border-radius:50%!important;background:rgba(247,243,236,.97)!important;z-index:30!important;display:flex!important;align-items:center!important;justify-content:center!important;overflow:hidden!important;color:#30372f!important;font-family:'Montserrat',Inter,Segoe UI,Arial,sans-serif!important}
.az-lab-direction-info-inner{width:82%!important;max-height:88%!important;text-align:left!important;padding:34px 16px 20px!important}
.az-lab-direction-info h2{margin:0 0 14px!important;text-align:center!important;font:600 30px/1.08 'Montserrat',Inter,Segoe UI,Arial,sans-serif!important;color:#344b3d!important;letter-spacing:-.02em!important}
.az-lab-direction-lead{margin:0 0 8px!important;font-size:15px!important;line-height:1.35!important;font-weight:600!important;color:#5b655e!important}
.az-lab-direction-info ul{margin:0 0 12px 18px!important;padding:0!important;font-size:14px!important;line-height:1.4!important}
.az-lab-direction-info li{margin:0 0 5px!important;padding-left:2px!important}
.az-lab-direction-summary{margin:10px 0 12px!important;padding-top:10px!important;border-top:1px solid rgba(181,150,98,.32)!important;text-align:center!important;font-size:14px!important;line-height:1.4!important;color:#465149!important}
.az-lab-direction-more{display:block!important;margin:0 auto!important;padding:5px 12px!important;border:0!important;border-bottom:1px solid rgba(68,99,79,.55)!important;background:transparent!important;color:#44634f!important;font:600 13px/1.2 'Montserrat',Inter,Segoe UI,Arial,sans-serif!important;cursor:pointer!important}
.az-lab-direction-close{position:absolute!important;right:34px!important;top:34px!important;width:46px!important;height:46px!important;border-radius:50%!important;border:1.5px solid rgba(68,99,79,.65)!important;background:#f7f3ec!important;color:#344b3d!important;font:400 35px/40px Arial,sans-serif!important;text-align:center!important;cursor:pointer!important;z-index:31!important;box-shadow:0 5px 14px rgba(58,49,37,.12)!important;padding:0!important}
.az-lab-direction-close:hover,.az-lab-direction-close:focus-visible{background:#ece7dc!important;outline:2px solid rgba(181,150,98,.45)!important;outline-offset:2px!important}
"""
s=s.replace(css_anchor,css,1)
pointer_old="overflow:hidden!important;pointer-events:none!important;z-index:8!important;will-change:left,top!important"
assert pointer_old in s
s=s.replace(pointer_old,"overflow:hidden!important;pointer-events:auto!important;cursor:pointer!important;z-index:8!important;will-change:left,top!important",1)
planet='const planets=collectPlanets(doc);'
assert planet in s
s=s.replace(planet,planet+"enableDirectionInfo(doc,[...planets.inner,...planets.outer]);",1)
assert "innerRx:r.width*.55" in s and "innerRy:r.height*.44" in s
assert "outerRx:r.width*.73" in s and "outerRy:r.height*.57" in s
assert "innerTilt:18" in s and "outerTilt:14" in s
assert "const INNER_DURATION=63000" in s and "const OUTER_DURATION=114000" in s
assert s!=original
p.write_text(s)
