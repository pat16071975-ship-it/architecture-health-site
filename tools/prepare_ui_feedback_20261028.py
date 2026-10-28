"""Exact-base source transformation only. No server, DB, deployment or merge."""
import hashlib
from pathlib import Path
import re
import subprocess

BASE = '2a0301e28d01a7ebaeae81e1e96b9ce4362930b7'
PINS = {
    'az-baze-auth/templates/uploads.html': 'c36b7393a90f72626425e92fac8b56b54c335cf6',
    'reports/period-view.js': '47b1f8d8d6b1ea55071badb57465b11cac07473a',
    'reports/index.html': '30d81dc254ad94d6def113b5f35a265d6f54949e',
    'az-baze-auth/test_report_consistency_preservation.py': 'c08eaa3648b88b7210782e63ffd8467f0b63989d',
    'tests/verify-paid-upload-periods.cjs': '851775d15ee67a152314cbc5ed3840f70f0d6f61',
}

def blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()

def once(text, old, new):
    assert text.count(old) == 1, 'ANCHOR_COUNT:' + old[:70]
    return text.replace(old, new, 1)

UPLOAD_CSS = '''
.upload-result-dialog{box-sizing:border-box;width:min(560px,calc(100vw - 32px));max-height:calc(100dvh - 32px);overflow:auto;padding:0;border:1px solid #d8cdbb;border-radius:16px;background:#fffdf8;color:#2d342f;box-shadow:0 24px 80px rgba(37,42,39,.28)}
.upload-result-dialog::backdrop{background:rgba(38,45,40,.42);backdrop-filter:blur(3px)}
.upload-result-head{padding:16px 18px;border-bottom:1px solid #e3dacd;background:#faf6ef}
.upload-result-head h2{margin:0;font-size:21px;font-weight:600;line-height:1.3}
.upload-result-body{padding:12px 18px;font-size:13px;line-height:1.5}
.upload-result-body p{margin:8px 0;white-space:pre-wrap;overflow-wrap:anywhere}
.upload-result-foot{display:flex;justify-content:flex-end;padding:12px 18px;border-top:1px solid #e3dacd;background:#faf7f1}
.upload-result-foot .btn{min-width:96px;min-height:44px}
@media(max-width:430px){.upload-result-head h2{font-size:19px}.upload-result-head,.upload-result-body,.upload-result-foot{padding-left:14px;padding-right:14px}}
'''
UPLOAD_HTML = '''<dialog id="uploadResultDialog" class="upload-result-dialog" role="alertdialog" aria-modal="true" aria-labelledby="uploadResultTitle" aria-describedby="uploadResultMessage uploadResultFiles uploadResultPeriod">
  <div class="upload-result-head"><h2 id="uploadResultTitle">Результат загрузки</h2></div>
  <div class="upload-result-body">
    <p id="uploadResultMessage"></p>
    <p id="uploadResultFiles"></p>
    <p id="uploadResultPeriod"></p>
  </div>
  <div class="upload-result-foot"><button type="button" class="btn primary" id="uploadResultOk">ОК</button></div>
</dialog>

'''
UPLOAD_JS = r'''  // Show only a server-confirmed result. No import or financial logic here.
  function uploadResultDetails(payload, fileNames, fallbackPeriod){
    if(!payload||typeof payload!=='object'||Array.isArray(payload)||payload.status==='error'||payload.ok===false||(!['ok','success','imported','replaced','duplicate','identical'].includes(payload.status)&&payload.ok!==true)){
      throw new Error('Сервер не подтвердил результат загрузки. Проверьте журнал загрузок.');
    }
    const unchanged=payload.status==='duplicate'||payload.status==='identical'||payload.reused===true;
    const period=payload.period||fallbackPeriod||{};
    const date=value=>/^\d{4}-\d{2}-\d{2}$/.test(String(value||''))?value.split('-').reverse().join('.'):String(value||'');
    const completed=Array.isArray(payload.completed_days)?payload.completed_days:[];
    const periodText=completed.length
      ?'Загруженные дни: '+completed.map(date).join(', ')
      :period.from&&period.to
        ?'Период: '+date(period.from)+' — '+date(period.to)
        :'Период не указан в ответе сервера.';
    let resultMessage=String(payload.message||(unchanged?'Сохранённые данные не изменены.':'Сервер подтвердил загрузку данных.'));
    if(payload.advertising_sync?.status==='error'){
      resultMessage+='\nОтчёт сохранён, но обновление рекламного отчёта завершилось с ошибкой.';
    }
    return {title:unchanged?'Данные не изменены':'Данные загружены',message:resultMessage,files:(fileNames||[]).map(String).join('\n'),period:periodText};
  }

  function uploadFileNames(form){
    return [...form.querySelectorAll('input[type="file"]')].flatMap(input=>[...(input.files||[])].map(file=>file.name));
  }

  function showUploadResult(payload, fileNames, fallbackPeriod){
    const details=uploadResultDetails(payload,fileNames,fallbackPeriod);
    const dialog=document.getElementById('uploadResultDialog');
    document.getElementById('uploadResultTitle').textContent=details.title;
    document.getElementById('uploadResultMessage').textContent=details.message;
    document.getElementById('uploadResultFiles').textContent=details.files;
    document.getElementById('uploadResultPeriod').textContent=details.period;
    const button=document.getElementById('uploadResultOk');
    button.disabled=false;
    if(!dialog.open)dialog.showModal();
    button.focus();
  }

  document.getElementById('uploadResultDialog')?.addEventListener('cancel',event=>event.preventDefault());
  document.getElementById('uploadResultOk')?.addEventListener('click',()=>{
    const button=document.getElementById('uploadResultOk');
    if(button.disabled)return;
    button.disabled=true;
    document.getElementById('uploadResultDialog').close();
    location.reload();
  });

'''
GROUP_JS = '''  // Presentation only. Move whole rows below active colleagues; retain every
  // historical value. Use the last two displayed months, excluding future ones.
  function groupInactiveDoctorRows(tbody, months, selectedDate){
    const recent=months.filter(month=>month.target<=selectedDate).slice(-2);
    if(recent.length!==2)return;
    for(const [rowClass,type] of [['row-team-dent','dentists'],['row-team-clinic','clinicDocs']]){
      const rows=[...tbody.querySelectorAll('tr.'+rowClass)];
      if(!rows.length)continue;
      const inactive=rows.filter(row=>{
        const name=row.cells[0].textContent;
        return recent.every(month=>{
          const value=rawOrNull(doctorPaidValue(month.target,type,name,(month.r?.[type]||{})[name]));
          return value===null||value===0;
        });
      });
      if(!inactive.length)continue;
      const afterGroup=rows[rows.length-1].nextSibling;
      const toggleRow=document.createElement('tr');
      toggleRow.className='az-inactive-doctors-toggle '+(type==='dentists'?'row-team-dent-toggle':'row-team-clinic-toggle');
      const cell=document.createElement('td');
      cell.colSpan=months.length+1;
      const button=document.createElement('button');
      button.type='button';
      button.className='az-inactive-doctors-button';
      button.setAttribute('aria-expanded','false');
      const label='Нули / нет данных за 2 месяца ('+inactive.length+')';
      button.textContent='+ '+label;
      cell.appendChild(button);
      toggleRow.appendChild(cell);
      tbody.insertBefore(toggleRow,afterGroup);
      inactive.forEach((row,index)=>{
        row.classList.add('az-inactive-doctor');
        row.id='az-inactive-'+type+'-'+index;
        row.hidden=true;
        tbody.insertBefore(row,afterGroup);
      });
      button.setAttribute('aria-controls',inactive.map(row=>row.id).join(' '));
      button.addEventListener('click',()=>{
        const expanded=button.getAttribute('aria-expanded')!=='true';
        button.setAttribute('aria-expanded',String(expanded));
        button.textContent=(expanded?'− ':'+ ')+label;
        inactive.forEach(row=>{row.hidden=!expanded});
        queueDateStickyClone();
      });
    }
  }

'''
GROUP_CSS = '''    .date-compare-table tr.az-inactive-doctor[hidden]{display:none!important}
    .date-compare-table .az-inactive-doctors-toggle td{padding:4px 12px;text-align:left;background:#faf6ef}
    .az-inactive-doctors-button{display:block;max-width:100%;min-height:44px;padding:6px 0;border:0;background:transparent;color:#44634f;font:600 12px/1.4 Inter,Arial,sans-serif;text-align:left;white-space:normal;cursor:pointer}
    .az-inactive-doctors-button:focus-visible{outline:2px solid #44634f;outline-offset:2px;border-radius:4px}
'''

def main():
    assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()==BASE
    assert not subprocess.check_output(['git','status','--porcelain'],text=True).strip()
    before={}
    for path,pin in PINS.items():
        raw=Path(path).read_bytes()
        assert blob(raw)==pin, 'BASE_BLOB:'+path
        before[path]=raw.decode('utf-8')
    changes={}
    path='az-baze-auth/templates/uploads.html'
    text=before[path]
    edits=[
        ('</style>',UPLOAD_CSS+'</style>'),
        ('<div id="reconcileOverlay"',UPLOAD_HTML+'<div id="reconcileOverlay"'),
        ('  async function send(url,form,extra={}){',UPLOAD_JS+'  async function send(url,form,extra={}){'),
        ("    let payload={};\n    try{payload=await response.json()}catch{}\n    if(!response.ok)throw new Error(payload.message||'Сервер не принял данные.');\n    return payload;", "    let payload;\n    try{payload=await response.json()}catch{throw new Error('Не удалось прочитать подтверждение сервера. Проверьте журнал загрузок перед повторной отправкой.')}\n    if(!response.ok||!payload||typeof payload!=='object'||Array.isArray(payload)||payload.status==='error'||payload.ok===false)throw new Error(payload?.message||'Сервер не подтвердил результат. Проверьте журнал загрузок.');\n    return payload;"),
        ("      const out=await send(url,context.form,extra);\n      resultBox(context.type,out.message||'Загрузка завершена.',true);\n      overlay.classList.add('hidden');\n      context=null;\n      setTimeout(()=>location.reload(),900);", "      const submittedFiles=uploadFileNames(context.form),submittedPeriod=context.payload.period,submittedType=context.type;\n      const out=await send(url,context.form,extra);\n      showUploadResult(out,submittedFiles,submittedPeriod);\n      resultBox(submittedType,out.message||'Загрузка завершена.',true);\n      overlay.classList.add('hidden');\n      context=null;"),
        ("      const out=await send(url,form);\n      const needs=out.requires_choice", "      const submittedFiles=uploadFileNames(form);\n      const out=await send(url,form);\n      const needs=out.requires_choice"),
        ("        resultBox(type,committed.message||'Загрузка завершена.',true);\n        setTimeout(()=>location.reload(),900);", "        showUploadResult(committed,submittedFiles,out.period);\n        resultBox(type,committed.message||'Загрузка завершена.',true);"),
        ("      const out=await send('/api/reports/advertising/metrika/import',metrikaForm);", "      const submittedFiles=uploadFileNames(metrikaForm);\n      const out=await send('/api/reports/advertising/metrika/import',metrikaForm);"),
        ("      resultBox('metrika',(out.message||'Метрика загружена.')+(days?' Завершённые дни: '+days+'.':''),true);\n      setTimeout(()=>location.reload(),900);", "      showUploadResult(out,submittedFiles);\n      resultBox('metrika',(out.message||'Метрика загружена.')+(days?' Завершённые дни: '+days+'.':''),true);"),
    ]
    for old,new in edits:text=once(text,old,new)
    reverse=text
    for old,new in reversed(edits):reverse=once(reverse,new,old)
    assert reverse==before[path], 'UPLOAD_SCOPE'
    assert 'setTimeout(()=>location.reload(),900)' not in text
    changes[path]=text
    path='reports/period-view.js'
    text=before[path]
    edits=[
        ('    .cash-split{',GROUP_CSS+'    .cash-split{'),
        ('  function renderDateComparison(){',GROUP_JS+'  function renderDateComparison(){'),
        ('    queueDateStickyClone();\n  }\n\n  let dateStickyRaf=0;', '    groupInactiveDoctorRows(tbody,months,date);\n    queueDateStickyClone();\n  }\n\n  let dateStickyRaf=0;'),
    ]
    for old,new in edits:text=once(text,old,new)
    reverse=text
    for old,new in reversed(edits):reverse=once(reverse,new,old)
    assert reverse==before[path], 'FINANCIAL_LOGIC_SCOPE'
    changes[path]=text
    path='reports/index.html'
    text,n=re.subn(r'(period-view\.js\?v=)[^"\'<> ]+',r'\g<1>20261028-upload-doctor-groups-1',before[path])
    assert n==1, 'CACHE_TOKEN_ANCHOR'
    changes[path]=text
    path='az-baze-auth/test_report_consistency_preservation.py'
    text=before[path]
    for runtime in ('az-baze-auth/templates/uploads.html','reports/period-view.js','reports/index.html'):
        text=once(text,PINS[runtime],blob(changes[runtime].encode()))
    changes[path]=text
    path='tests/verify-paid-upload-periods.cjs'
    text=before[path]
    text=once(text,"    id, disabled: false, value: '', dataset: {}, textContent: '', providerSelects: [], providerReads: 0,", "    id, disabled: false, value: '', dataset: {}, textContent: '', providerSelects: [], providerReads: 0,\n    open: false, showModal() { this.open = true; }, close() { this.open = false; }, focus() {},")
    text=once(text,"    querySelectorAll(selector) {\n      assert.equal(selector, '[data-provider]');", "    querySelectorAll(selector) {\n      if (selector === 'input[type=\"file\"]') return (this.inputs || []).filter(input => input.type === 'file').map(input => ({files: [input.value]}));\n      assert.equal(selector, '[data-provider]');")
    text=once(text,"    'reconcileKeep', 'reconcileNew', 'reconcileContinue', 'reconcileCancel', 'paidClientResult',", "    'reconcileKeep', 'reconcileNew', 'reconcileContinue', 'reconcileCancel', 'paidClientResult',\n    'uploadResultDialog', 'uploadResultTitle', 'uploadResultMessage', 'uploadResultFiles', 'uploadResultPeriod', 'uploadResultOk',")
    text=once(text,"  const timers = [];", "  const timers = [];\n  const navigation = {reloads: 0};")
    text=once(text,"    location: { reload() {}, replace() {} },", "    location: { reload() { navigation.reloads += 1; }, replace() {} },")
    text=once(text,"  return { form, ids, calls, file, timers };", "  return { form, ids, calls, file, timers, navigation };")
    text=once(text,"  assert.equal(test.timers.length, 1);", "  assert.equal(test.timers.length, 0, 'No automatic reload before acknowledgement.');\n  assert.equal(test.ids.uploadResultDialog.open, true);\n  assert.equal(test.ids.uploadResultTitle.textContent, 'Данные загружены');\n  assert.equal(test.ids.uploadResultFiles.textContent, 'selected-period.xlsx');\n  assert.equal(test.navigation.reloads, 0);\n  await test.ids.uploadResultOk.dispatch('click');\n  assert.equal(test.ids.uploadResultDialog.open, false);\n  assert.equal(test.navigation.reloads, 1);")
    text=once(text,"  assert.equal(test.ids.paidClientResult.textContent, 'Сохранены старые данные.');", "  assert.equal(test.ids.paidClientResult.textContent, 'Сохранены старые данные.');\n  assert.equal(test.ids.uploadResultTitle.textContent, 'Данные не изменены');\n  assert.equal(test.timers.length, 0);")
    changes[path]=text
    for path,text in changes.items():
        Path(path).write_text(text,encoding='utf-8')
        print('SOURCE_BLOB',path,blob(text.encode()))
    print('SOURCE_ONLY=PASS; RUNTIME_FILES=3; DATABASE_ACCESS=NONE; MERGE=NONE; DEPLOY=NONE')

if __name__=='__main__':main()
