(() => {
  let E=null, installed=false;
  const norm=v=>String(v||'').toLowerCase().replace(/ё/g,'е').replace(/\s+/g,' ').trim();
  const ym=(y,i)=>`${y}-${String(i+1).padStart(2,'0')}`;
  const vals=(y,src)=>Array.from({length:12},(_,i)=>{const m=ym(y,i);return (E?.period||[]).includes(m)?(Number(src?.[m])||0):null});
  const allAssignments=()=>E?.payroll?.assignments||[];
  const sumAssignments=(y,list)=>{const src={};(E?.period||[]).forEach(m=>src[m]=list.reduce((s,x)=>s+(Number(x.months?.[m])||0),0));return vals(y,src)};
  const direction=x=>{
    if(x?.group!=='Врачи')return null;
    const t=norm([x.department,x.role,x.function].join(' '));
    if(/остеопат|нутрициолог|подиатр|гастроэнтеролог|нейропсихолог|массаж|миофункцион|логопед/.test(t))return 'structure';
    if(/ортодонт|стоматолог|ортопед|хирург|гигиенист|терапевт|терапия/.test(t))return 'dent';
    return null;
  };
  const people=(y,list,prefix)=>list.slice().sort((a,b)=>String(a.name||'').localeCompare(String(b.name||''),'ru')).map((x,i)=>node(`${prefix}-${i}`,`${x.name} — ${x.role}`,4,vals(y,x.months||{}),[],'detail'));
  const ordinaryGroup=(y,g,i)=>{const gd=E?.payroll?.groups?.[g];if(!gd)return null;const list=allAssignments().filter(x=>x.group===g);return node(`audit-fot-group-${i}`,g,3,vals(y,gd.months||{}),people(y,list,`audit-fot-group-${i}`),'subcategory')};

  function fotChildren(y,accrued){
    const rev=vals(y,E?.opu?.revenue_net||{});
    const ratio=accrued.map((v,i)=>v===null||rev[i]===null||!rev[i]?null:v/rev[i]);
    const dent=allAssignments().filter(x=>x.group==='Врачи'&&direction(x)==='dent');
    const structure=allAssignments().filter(x=>x.group==='Врачи'&&direction(x)==='structure');
    const unassigned=allAssignments().filter(x=>x.group==='Врачи'&&!direction(x));
    const aux=vals(y,E?.payroll?.groups?.['Вспомогательный персонал']?.months||{});
    const assistants=vals(y,E?.opu?.assistant_salary||{});
    const restAux=aux.map((v,i)=>v===null?null:v-(Number(assistants[i])||0));
    return [
      node('audit-fot-ratio','ФОТ / выручка',3,ratio,[],'detail','percent'),
      dent.length?node('audit-fot-dent','Стоматология — врачи',3,sumAssignments(y,dent),people(y,dent,'audit-fot-dent'),'subcategory'):null,
      structure.length?node('audit-fot-structure','Отделение структуры — врачи',3,sumAssignments(y,structure),people(y,structure,'audit-fot-structure'),'subcategory'):null,
      unassigned.length?node('audit-fot-unassigned','Врачи — не распределено по направлению',3,sumAssignments(y,unassigned),people(y,unassigned,'audit-fot-unassigned'),'subcategory'):null,
      node('audit-fot-assistants','Ассистенты стоматологов',3,assistants,[],'subcategory'),
      node('audit-fot-aux-rest','Остальной вспомогательный персонал',3,restAux,[],'subcategory'),
      ordinaryGroup(y,'АУП',2),
      ordinaryGroup(y,'Администраторы',3),
      ordinaryGroup(y,'Лаборатория',4),
      ordinaryGroup(y,'Штатный маркетинг',5)
    ].filter(Boolean);
  }

  const add=(a,b)=>Array.from({length:12},(_,i)=>a?.[i]===null&&b?.[i]===null?null:(Number(a?.[i])||0)+(Number(b?.[i])||0));
  const sub=(a,b)=>Array.from({length:12},(_,i)=>a?.[i]===null?null:(Number(a?.[i])||0)-(Number(b?.[i])||0));
  const any=v=>(v||[]).some(x=>x!==null&&Math.abs(Number(x)||0)>.01);
  const bucket=name=>{
    const n=norm(name);
    if(n.includes('услуги лаборатории'))return 'Услуги лаборатории';
    if(n.includes('материалы для зтл'))return 'Материалы для ЗТЛ';
    if(n.includes('стоматологические материалы'))return 'Стоматологические материалы';
    if(n.includes('расходные материалы')||n.includes('стоматологические принадлежности'))return 'Расходные медицинские материалы';
    if(n.includes('компьютерная томография')||n.includes('диагност'))return 'Диагностика / КТ';
    if(n.includes('утилизац')||n.includes('спецодеж')||n.includes('санитар'))return 'Санитарные и медицинские расходы';
    if(n.includes('материал'))return 'Прочие материалы';
    return 'Прочие медицинские расходы';
  };

  function medicalNodes(y){
    const lines=(E?.opu?.expense_lines||[]).filter(x=>x.category==='Медицинские расходы');
    const order=['Стоматологические материалы','Материалы для ЗТЛ','Расходные медицинские материалы','Услуги лаборатории','Диагностика / КТ','Санитарные и медицинские расходы','Прочие материалы','Прочие медицинские расходы'];
    const grouped=new Map();
    lines.forEach((x,i)=>{const b=bucket(x.source_name);if(!grouped.has(b))grouped.set(b,[]);grouped.get(b).push({x,i})});
    const detail=order.map((b,bi)=>{const arr=grouped.get(b)||[];if(!arr.length)return null;const v=arr.reduce((s,it)=>add(s,vals(y,it.x.months||{})),Array(12).fill(null));return node(`audit-med-bucket-${bi}`,b,3,v,arr.map(it=>node(`audit-med-source-${bi}-${it.i}`,it.x.source_name,4,vals(y,it.x.months||{}),[],'detail')),'subcategory')}).filter(Boolean);
    const materialNames=new Set(['Стоматологические материалы','Материалы для ЗТЛ','Расходные медицинские материалы','Услуги лаборатории','Прочие материалы']);
    const materials=vals(y,E?.opu?.materials||{});
    const materialChildren=detail.filter(x=>materialNames.has(x.label));
    const materialKnown=materialChildren.reduce((s,x)=>add(s,x.values),Array(12).fill(null));
    const residual=sub(materials,materialKnown);
    if(any(residual))materialChildren.push(node('audit-materials-residual','Прочие материалы',3,residual,[],'detail'));
    const result=[];
    if(materials.some(x=>x!==null))result.push(node('audit-materials-accrued','Материалы — начислено',2,materials,materialChildren,'subcategory'));
    const other=detail.filter(x=>!materialNames.has(x.label));
    if(other.length)result.push(node('audit-medical-other-accrued','Прочие медицинские расходы — начислено',2,other.reduce((s,x)=>add(s,x.values),Array(12).fill(null)),other,'subcategory'));
    return result;
  }

  function install(){
    if(installed||!E?.available||E?.control?.status!=='OK'||typeof expenseCategoryNode!=='function'||typeof node!=='function'||typeof render!=='function')return;
    installed=true;const original=expenseCategoryNode;
    expenseCategoryNode=function(y,name,i){
      const base=original(y,name,i);if(y!==2026)return base;
      if(name==='ФОТ'){
        const accrued=vals(y,E.payroll?.total_by_month||{});
        base.children=[
          node('audit-fot-accrued','Начисленный ФОТ по зарплатному реестру',2,accrued,fotChildren(y,accrued),'subcategory'),
          node('fot-paid-detail','ФОТ по фактическим выплатам — детализация',2,base.values,base.children||[],'subcategory')
        ];
      }
      if(name==='Медицинские расходы')base.children=[...medicalNodes(y),node('medical-paid-detail','Медицинские расходы по фактическим оплатам — детализация',2,base.values,base.children||[],'subcategory')];
      return base;
    };
    if(typeof DATA!=='undefined'&&DATA)render();
  }

  fetch('/api/reports/economics-control',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(p=>{E=p?.data;install()}).catch(e=>console.error('AZ finrez economics integration failed',e));
})();
