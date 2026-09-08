(() => {
  const frame = document.getElementById('serviceFrame');
  if (!frame) return;
  let observer = null;
  let timer = null;
  const schedule = () => { clearTimeout(timer); timer = setTimeout(apply, 90); };

  frame.addEventListener('load', () => {
    const d = frame.contentDocument;
    if (!d) return;
    installStyle(d);
    if (observer) observer.disconnect();
    observer = new MutationObserver(schedule);
    observer.observe(d.body, {childList:true, subtree:true, characterData:true});
    d.addEventListener('change', schedule);
    d.addEventListener('click', schedule);
    schedule();
  });

  function installStyle(d) {
    if (d.getElementById('azAuditGuardStyle')) return;
    const s = d.createElement('style');
    s.id = 'azAuditGuardStyle';
    s.textContent = `
      .az-audit-warning{display:block;margin-top:3px;font-size:8.5px;line-height:1.25;color:#9a4b48;font-weight:700;white-space:normal}
      .az-audit-partial{background:#fff0e8!important;color:#8b493d!important;font-weight:700!important;white-space:normal!important}
      .az-audit-note{margin:8px 0;padding:9px 11px;border-left:3px solid #b59662;border-radius:8px;background:#fffaf0;color:#665b49;font-size:10px;line-height:1.4}
    `;
    d.head.appendChild(s);
  }

  function apply() {
    const d = frame.contentDocument;
    if (!d?.body) return;
    replaceText(d);
    guardIncompleteEconomics(d);
    addLabNote(d);
  }

  function replaceText(d) {
    const walker = d.createTreeWalker(d.body, NodeFilter.SHOW_TEXT);
    const nodes=[];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(n => {
      const old=n.nodeValue || '';
      let t=old.replace(/Прибыль врача/g,'Распределённая прибыль')
        .replace(/прибыль врача/g,'распределённая прибыль')
        .replace(/Потерянная выручка/g,'Оценочная потенциальная выручка')
        .replace(/потерянная выручка/g,'оценочная потенциальная выручка');
      if(t!==old)n.nodeValue=t;
    });
    d.querySelectorAll('td,th,label,div,span').forEach(el=>{
      const txt=(el.textContent||'').trim();
      if (txt === 'Распределённая прибыль' && !el.querySelector('.az-audit-warning')) {
        el.insertAdjacentHTML('beforeend','<span class="az-audit-warning">Показатель не является фактической прибылью врача</span>');
      }
      if (/\bCAC\b/i.test(txt) && !el.dataset.azAuditRenamed) {
        el.dataset.azAuditRenamed='1';
        el.textContent=txt.replace(/\bCAC\b/gi,'Стоимость первичного визита по общим расходам маркетинга');
        el.insertAdjacentHTML('beforeend','<span class="az-audit-warning">Не является CAC без сквозной атрибуции</span>');
      }
      if (/\bROMI\b/i.test(txt) && !el.dataset.azAuditRomi) {
        el.dataset.azAuditRomi='1';
        el.textContent=txt.replace(/\bROMI\b/gi,'Выручка / маркетинг (техническое отношение)');
      }
    });
  }

  function rows(d) { return [...d.querySelectorAll('#content tr')]; }
  function rowByLabel(d, re) { return rows(d).find(r => re.test((r.cells?.[0]?.textContent||'').trim())); }
  function moneyValue(text) {
    const s=String(text||'').replace(/\s/g,'').replace('₽','').replace(',','.');
    const n=Number(s.replace(/[^0-9.-]/g,''));
    return Number.isFinite(n)?n:null;
  }
  function mark(cell) {
    if (!cell || (cell.classList.contains('az-audit-partial') && cell.textContent==='Неполные данные')) return;
    cell.textContent='Неполные данные';
    cell.classList.add('az-audit-partial');
    cell.title='ФОТ отсутствует или не подтверждён. Отсутствующее значение не считается нулевым.';
  }
  function guardIncompleteEconomics(d) {
    const revenue=rowByLabel(d,/^Выручка (врача|всех врачей|лаборатории)/i);
    const salary=rowByLabel(d,/^(Основная ЗП|ФОТ лаборатории)/i);
    const profit=rowByLabel(d,/^(Доход|Финансовый результат) после (ЗП|ФОТ|выплат)/i);
    const margin=rowByLabel(d,/^(Маржинальность|Рентабельность) после выплат/i);
    if (!revenue || !salary) return;
    const n=Math.min(revenue.cells.length,salary.cells.length);
    for(let i=1;i<n;i++){
      const rv=moneyValue(revenue.cells[i].textContent);
      const st=(salary.cells[i].textContent||'').trim();
      const sv=moneyValue(st);
      if(rv>0 && (st==='—' || sv===0)){
        mark(profit?.cells[i]);
        mark(margin?.cells[i]);
      }
    }
  }
  function addLabNote(d) {
    const direction=d.getElementById('direction')?.value;
    if(direction!=='Лаборатория') return;
    const content=d.getElementById('content');
    if(!content || d.getElementById('azLabAuditNote')) return;
    const note=d.createElement('div'); note.id='azLabAuditNote'; note.className='az-audit-note';
    note.textContent='Лаборатория оценивается как отдельное направление. Персональная прибыль зубного техника не рассчитывается, если выручка формируется на уровне лаборатории.';
    content.prepend(note);
  }
})();
