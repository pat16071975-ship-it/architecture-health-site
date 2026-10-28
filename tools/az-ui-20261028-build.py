"""Build the owner's two UI deltas on an isolated checkout; no live access."""
from pathlib import Path
import hashlib
import json

EXPECTED = {
    'az-baze-auth/templates/base.html': 'c7156effeb3b10b022da06d20f743196d201005e',
    'az-baze-auth/templates/uploads.html': 'c36b7393a90f72626425e92fac8b56b54c335cf6',
    'reports/period-view.js': '47b1f8d8d6b1ea55071badb57465b11cac07473a',
    'reports/index.html': '30d81dc254ad94d6def113b5f35a265d6f54949e',
    'az-baze-auth/test_report_consistency_preservation.py': 'c08eaa3648b88b7210782e63ffd8467f0b63989d',
    'tests/verify-paid-periods.cjs': 'af2373f33dcf335a3372c144b46d761472431491',
}

def blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()

sources = {}
for name, sha in EXPECTED.items():
    raw = Path(name).read_bytes()
    assert blob(raw) == sha, 'BASE_DRIFT:' + name
    sources[name] = raw.decode('utf-8')

changes = []
def replace(name, old, new, count=1):
    text = sources[name]
    assert text.count(old) == count, 'ANCHOR_MISMATCH:' + name + ':' + old[:100]
    sources[name] = text.replace(old, new)
    changes.append((name, old, new, count))

base = 'az-baze-auth/templates/base.html'
replace(base, "  const presets = {", "  let confirmedAction = null;\n  let previousFocus = null;\n  let previousOverflow = '';\n\n  const presets = {")
replace(base, "    const status = presets[options.status] ? options.status : 'imported';", "    if (modal.hidden) {\n      previousFocus = document.activeElement;\n      previousOverflow = document.body.style.overflow;\n    }\n    confirmedAction = typeof options.onConfirm === 'function' ? options.onConfirm : null;\n    const status = presets[options.status] ? options.status : 'imported';")
replace(base, "    document.body.style.overflow = '';\n  }\n\n  ok.addEventListener('click', hideUploadFeedback);", "    document.body.style.overflow = previousOverflow;\n    confirmedAction = null;\n    if (previousFocus?.isConnected) previousFocus.focus();\n  }\n\n  ok.addEventListener('click', () => {\n    if (modal.dataset.status === 'loading') return;\n    const action = confirmedAction;\n    hideUploadFeedback();\n    if (action) action();\n  });\n  modal.addEventListener('keydown', event => {\n    if (!modal.hidden && event.key === 'Tab') {\n      event.preventDefault();\n      if (modal.dataset.status !== 'loading') ok.focus();\n    }\n  });")
replace(base, 'type="button">OK</button>', 'type="button">ОК</button>')
replace(base, '[data-az-upload-form],[data-az-cash-upload-form],[data-az-metrika-form]', '[data-az-upload-form],[data-az-cash-upload-form],[data-az-paid-upload-form],[data-az-metrika-form]')
replace(base, '.az-upload-modal-message{margin:', '.az-upload-modal-message{white-space:pre-line;overflow-wrap:anywhere;margin:')
replace(base, '.az-upload-modal-card{width:min(640px,94vw);', '.az-upload-modal-card{max-height:90dvh;overflow-y:auto;width:min(640px,94vw);')

uploads = 'az-baze-auth/templates/uploads.html'
feedback = r'''  // Server-confirmed result only. Keep the acknowledgement open until OK.
  function finishUpload(type,form,out,preview={}){
    const status=out.status==='duplicate'||out.reused===true?'duplicate':out.status==='replaced'?'replaced':'imported';
    const text=out.message||(status==='duplicate'?'Данные не изменены.':'Данные загружены.');
    const names=Array.from(form.querySelectorAll('input[type="file"]')).flatMap(input=>Array.from(input.files||[],file=>file.name));
    const period=out.period||preview.period;
    const date=value=>/^\d{4}-\d{2}-\d{2}$/.test(String(value||''))?value.split('-').reverse().join('.'):String(value||'');
    const details=[text];
    if(names.length)details.push((names.length===1?'Файл: ':'Файлы: ')+names.join(', '));
    if(type==='metrika'){
      const days=Array.isArray(out.completed_days)?out.completed_days:[];
      if(days.length)details.push('Завершённые дни: '+days.map(date).join(', '));
    }else if(period?.from&&period?.to){
      details.push('Период: '+date(period.from)+' — '+date(period.to));
    }
    resultBox(type,details.join('\n'),true);
    if(window.AZUploadFeedback?.show){
      window.AZUploadFeedback.show({status,message:details.join('\n'),onConfirm:()=>location.reload()});
    }else if(typeof window.alert==='function'){
      window.alert(details.join('\n'));
      location.reload();
    }
  }

'''
replace(uploads, '  function stat(label,value){', feedback + '  function stat(label,value){')
replace(uploads, "      resultBox(context.type,out.message||'Загрузка завершена.',true);\n      overlay.classList.add('hidden');\n      context=null;\n      setTimeout(()=>location.reload(),900);", "      overlay.classList.add('hidden');\n      finishUpload(context.type,context.form,out,context.payload);\n      context=null;")
replace(uploads, "        resultBox(type,committed.message||'Загрузка завершена.',true);\n        setTimeout(()=>location.reload(),900);", "        finishUpload(type,form,committed,out);")
replace(uploads, "      const days=(out.completed_days||[]).map(value=>value.split('-').reverse().join('.')).join(', ');\n      resultBox('metrika',(out.message||'Метрика загружена.')+(days?' Завершённые дни: '+days+'.':''),true);\n      setTimeout(()=>location.reload(),900);", "      finishUpload('metrika',metrikaForm,out);")
replace(uploads, "    if(!response.ok)throw new Error(payload.message||'Сервер не принял данные.');", "    if(!response.ok||payload.status==='error')throw new Error(payload.message||'Сервер не принял данные.');")

view = 'reports/period-view.js'
fold = r'''  // Visibility only: never alter stored values, totals, or financial formulas.
  const doctorFoldExpanded=new Set();
  function doctorFoldParts(anchor){
    if(!paidDate(anchor))return null;
    const currentStart=anchor.slice(0,7)+'-01';
    const previous=new Date(currentStart+'T12:00:00Z');
    previous.setUTCDate(0);
    const previousEnd=previous.toISOString().slice(0,10);
    const previousStart=previousEnd.slice(0,7)+'-01';
    // Require complete source coverage; a missing month is not a zero.
    const previousParts=completeDoctorPaidParts(previousStart,previousEnd,doctorPaidIntervals(previousStart,previousEnd));
    const currentParts=completeDoctorPaidParts(currentStart,anchor,doctorPaidIntervals(currentStart,anchor));
    if(!previousParts?.length||!currentParts?.length)return null;
    return {previousParts,currentParts,previousStart,anchor};
  }
  function foldInactiveDoctors(tbody,months,date){
    if(typeof tbody.querySelectorAll!=='function')return;
    const available=months.filter(month=>month.target<=date&&(month.r||month.paidParts?.length));
    const anchor=available.length?available[available.length-1].target:null;
    const evidence=doctorFoldParts(anchor);
    if(!evidence)return;
    for(const [type,cls] of [['dentists','row-team-dent'],['clinicDocs','row-team-clinic']]){
      const rows=Array.from(tbody.querySelectorAll('tr.'+cls));
      const inactive=rows.filter(row=>{
        const name=row.cells[0]?.textContent.trim();
        return name&&doctorPartsPaid(evidence.previousParts,type,name)===0&&doctorPartsPaid(evidence.currentParts,type,name)===0;
      });
      if(!inactive.length)continue;
      const key=type+'|'+evidence.previousStart+'|'+anchor;
      const toggle=document.createElement('tr');
      toggle.className='az-doctor-fold-toggle '+cls;
      const cell=document.createElement('td');
      cell.colSpan=months.length+1;
      const button=document.createElement('button');
      button.type='button';button.className='btn';
      button.title='Предыдущий месяц полностью и текущий с начала месяца по '+anchor.split('-').reverse().join('.');
      const ids=inactive.map((row,index)=>{
        const id='az-doctor-fold-'+type+'-'+index;
        row.id=id;row.dataset.azDoctorFold='1';return id;
      });
      button.setAttribute('aria-controls',ids.join(' '));
      const apply=()=>{
        const expanded=doctorFoldExpanded.has(key);
        inactive.forEach(row=>{row.hidden=!expanded});
        button.setAttribute('aria-expanded',String(expanded));
        button.textContent=(expanded?'−':'+')+' Без выручки за 2 месяца ('+inactive.length+')';
      };
      button.addEventListener('click',()=>{
        if(doctorFoldExpanded.has(key))doctorFoldExpanded.delete(key);else doctorFoldExpanded.add(key);
        apply();queueDateStickyClone();
      });
      cell.appendChild(button);toggle.appendChild(cell);
      const after=rows[rows.length-1].nextSibling;
      tbody.insertBefore(toggle,after);
      inactive.forEach(row=>tbody.insertBefore(row,after));
      apply();
    }
  }

'''
replace(view, '  const escapeHtml=s=>', fold + '  const escapeHtml=s=>')
replace(view, "    queueDateStickyClone();\n  }\n\n  let dateStickyRaf=0;", "    foldInactiveDoctors(tbody,months,date);\n    queueDateStickyClone();\n  }\n\n  let dateStickyRaf=0;")
replace(view, '    #dateCompareView{margin-top:16px}', '    #dateCompareBody tr[data-az-doctor-fold][hidden]{display:none!important}\n    .az-doctor-fold-toggle button{min-height:44px;white-space:normal;text-align:left}\n    #dateCompareView{margin-top:16px}')
replace('reports/index.html', './period-view.js?v=20261028-paid-patients-1', './period-view.js?v=20261028-doctor-fold-1')
replace('tests/verify-paid-periods.cjs', "assert(html.includes('./period-view.js?v=20261028-paid-patients-1'), 'Paid-patient script cache key');", "assert(html.includes('./period-view.js?v=20261028-doctor-fold-1'), 'Paid-patient and doctor-fold script cache key');")
replace('az-baze-auth/test_report_consistency_preservation.py', "self.assertEqual(git_blob(raw), 'c36b7393a90f72626425e92fac8b56b54c335cf6')", "self.assertEqual(git_blob(raw), '" + blob(sources[uploads].encode()) + "')")
for name, expected in EXPECTED.items():
    restored=sources[name]
    for path,old,new,count in reversed(changes):
        if path==name:
            assert restored.count(new)==count, 'REVERSE_ANCHOR:' + path
            restored=restored.replace(new,old)
    assert blob(restored.encode())==expected, 'UNRELATED_DELTA:' + name
    Path(name).write_text(sources[name],encoding='utf-8')
print('EXACT_UI_DELTA_AND_REVERSE_BASE_GUARDS=PASS')
print(json.dumps({name:blob(Path(name).read_bytes()) for name in EXPECTED},sort_keys=True))
