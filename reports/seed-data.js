(() => {
  const SEED_MARKER='az-management-seed-2026-07';
  const SEED_VERSION='2026-07';
  const SEED_ITERATIONS=310000;
  const SEED_AAD='az-management-seed-v1';
  const SEED_SALT='vIznhWhYBLkdrqe0KT5Z1A==';
  const SEED_IV='0rizftVZ2JzEjNeg';
  const SEED_CIPHER='mT5F+T4rl5yUzEyEIt/I2CwalnR4tmgMzWt3rtJfl4LSiBZyGas3BH9xTNRN04RdGe+UGpfmW027eWNbCmF+aeahfXcbqABjyo9RYlwxCTY8RfyYoHuRbAxAZTKG0IcHfwT9L0hpUx7tAXoHw37AR2SjLUL3k7uOjqNDvZvnioVK6FSFC2qhZorF/cTSqfYSNK0hhkA3W5MYk5O2PE9zimeShciryyDoQCepUE7JSxJCuSQhYeBcC2MnzoPt1Al9238Z+1hqHq/DAY7VCY7Z8DjL6isz33MVZl3BhMafi5ZY/dIDF2KdhbTHkhSeJpUPYb96FeqWjAXvItzMhcy62o6XcMsoMAzcAurMTlGACJkIpn2Acw4pGr7UHu8tKWdzhWRNY4tvCD+TaVZ1N/x8M2U9YDP9b1w8600gWaKIkoGsBuHiww9TrGpMGO8hwIsLU0AoSNj8+NNbwdAeDaFWfz20eM54dSwUohz7JZR7LgqjXOdI4myzQwLGln4/7LFFr6rcjoof4kkZWKYEPWtn8IpFJys/5qVe8kMiNJyAJl9lSLSTT/V2iZOsBDBoCekBmqnCHDuTupzB6LAgirEoWyqm0syIsab0sgonFqvoe6OEUkJz7Z/TMSYSXks5KZEYjQVhwLUWjPunwwYE6djPAuauHaPMG++UMcLVVw505ptljw3cXcMIrwU5a07zfk9FFhb4JFP/zs41d6dlUeB1QoNM/JX6p9MfG5lp6yOZiI3z1zb8bL02TpTM7yvHsf0PARC6JdZGratsou30thskYrBQoyiu++WmIYrEf/2XyLUlI3hp2Ycpo6SOtbkXiABLjTv+9B/CxHYVD+yjSFb4uERWF0Uek1Ue6FwL0ygyBD6IUEZgs+OBZZpkjllfhRLzditxZmPgVBwgFEHyUNB4SraYUOipMSorIkpS8pHE4+41CnAMLUukCBT3MIEXz1WCyC6IK6ChrRGa3qP+yQ1lscFr1vfb6rP0hH5IPzZF7wvpI3Rd8k4BU3VUIFhqm7poHDVYjAiVu3UT7TEbvRXim+WJ0w9HQRxDwhoNXAq1Kgd70BCi63ttBx5fMjcMZUjpvK6h4SpEZCfoZekMM6+4OMrJ+f1WvnAjHhJ0Se6hd2oZ2a+2GlLU9nQt4ski0sxY6vZfU1lWj0Uqcf82emqzjqeR8hvCR0oK3xQ62sTc6wCX1SQShna0CmbH0EMi7R6kTOtp8f8f3os3XzI/P2OYReGJFyZqNyzX29ag9BXGDKTD+X65t2QtT+te7lwqSalaWNGelmHabo1MItHccvSF7ovR2sxOsyfVpaoAqUjuOd4dsrsBQ6YTvm64Qvo+WScpnYe6yw0mORDR9YccqCvXMl56YBrbsnSNHSX2rc2lQViKRLFsJTwdRGIEjmUxJ2ZBL1YUuTtddERNb+fATUmxx/MaoflbaayrFM7oOT7Eue5fo/AU1ecz4ryt5bOdwv0D0hzBg3k/ne4EhW25hNP/C0q/22My0kI9Ah/Y5ty4w27Qa0zx7sMltkT7ZgeLqqQkwubjPHzyXvnB3533FFEy';

  const bytes = value => Uint8Array.from(atob(value), c => c.charCodeAt(0));
  const isBlank = value => value === '' || value === null || value === undefined;

  async function decryptSeed(password){
    const material=await crypto.subtle.importKey('raw',new TextEncoder().encode(password),'PBKDF2',false,['deriveKey']);
    const key=await crypto.subtle.deriveKey({name:'PBKDF2',salt:bytes(SEED_SALT),iterations:SEED_ITERATIONS,hash:'SHA-256'},material,{name:'AES-GCM',length:256},false,['decrypt']);
    const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:bytes(SEED_IV),additionalData:new TextEncoder().encode(SEED_AAD)},key,bytes(SEED_CIPHER));
    return JSON.parse(new TextDecoder().decode(plain));
  }

  function buildRecord(data,index){
    const record=blankRecord(data.dates[index]);
    record.plan=data.plan[index];
    record.factMedicine=data.fm[index];
    record.factLab=data.fl[index];
    record.primary=data.p[index];
    record.repeat=data.r[index];
    record.dentPrimary=data.dp[index];
    record.dentRepeat=data.dr[index];
    record.clinicPrimary=data.cp[index];
    record.clinicRepeat=data.cr[index];
    record.labRevenue=data.fl[index];
    dentists.forEach((name,doctorIndex)=>record.dentists[name]=data.dent[doctorIndex][index]);
    clinicDocs.forEach((name,doctorIndex)=>record.clinicDocs[name]=data.clinic[doctorIndex][index]);
    record._source='audit_2026_confirmed';
    return record;
  }

  function mergeRecord(seed,existing){
    if(!existing)return seed;
    const merged={...seed};
    Object.keys(seed).forEach(key=>{
      if(key==='dentists'||key==='clinicDocs')return;
      if(!isBlank(existing[key]))merged[key]=existing[key];
    });
    merged.dentists={...seed.dentists};
    dentists.forEach(name=>{if(existing.dentists&&!isBlank(existing.dentists[name]))merged.dentists[name]=existing.dentists[name]});
    merged.clinicDocs={...seed.clinicDocs};
    clinicDocs.forEach(name=>{if(existing.clinicDocs&&!isBlank(existing.clinicDocs[name]))merged.clinicDocs[name]=existing.clinicDocs[name]});
    merged._source=existing._source||seed._source;
    return merged;
  }

  async function applySeed(password){
    if(localStorage.getItem(SEED_MARKER)===SEED_VERSION)return {changed:0,latest:null,already:true};
    const data=await decryptSeed(password);
    const store=loadStore();
    let changed=0;
    data.dates.forEach((date,index)=>{
      const seed=buildRecord(data,index);
      const before=store[date];
      const merged=mergeRecord(seed,before);
      if(JSON.stringify(before)!==JSON.stringify(merged)){store[date]=merged;changed++}
    });
    saveStore(store);
    localStorage.setItem(SEED_MARKER,SEED_VERSION);
    return {changed,latest:data.dates[data.dates.length-1],already:false};
  }

  const form=document.querySelector('#loginForm');
  if(form){
    form.addEventListener('submit',async event=>{
      event.preventDefault();
      const user=document.querySelector('#loginName').value.trim();
      const password=document.querySelector('#loginPassword').value;
      try{
        const hash=await sha256(user+'|'+password);
        if(user!==AUTH_USER||hash!==AUTH_HASH)return;
        const result=await applySeed(password);
        if(!result.already&&result.latest){
          document.querySelector('#reportDate').value=result.latest;
          loadDate();
          document.querySelector('#status').textContent=`Загружены подтверждённые исторические данные за январь–июль 2026. Добавлено/дополнено записей: ${result.changed}. Ручные значения сохранены.`;
        }
      }catch(error){
        console.error('Historical seed failed',error);
        document.querySelector('#status').textContent='Исторические данные не удалось загрузить. Ручной ввод продолжает работать.';
      }
    });
  }



  // az-import-merge-v1: импорт добавляет/обновляет только даты из файла,
  // сохраняя остальные записи текущего браузера.
  window.importBackup = function(file){
    const reader = new FileReader();
    reader.onload = () => {
      try{
        const payload = JSON.parse(reader.result);
        if(!payload || !payload.data || typeof payload.data !== 'object' || Array.isArray(payload.data)) throw new Error('invalid payload');
        const entries = Object.entries(payload.data).filter(([date, record]) => /^\d{4}-\d{2}-\d{2}$/.test(date) && record && typeof record === 'object' && !Array.isArray(record));
        if(!entries.length) throw new Error('no dated records');
        if(!confirm(`Добавить или обновить записей: ${entries.length}? Остальные сохранённые даты останутся без изменений.`)) return;

        const store = loadStore();
        entries.forEach(([date, record]) => {
          const base = blankRecord(date);
          store[date] = {
            ...base,
            ...record,
            date,
            dentists: {...base.dentists, ...(record.dentists || {})},
            clinicDocs: {...base.clinicDocs, ...(record.clinicDocs || {})}
          };
        });
        saveStore(store);

        const dates = entries.map(([date]) => date).sort();
        const latest = dates[dates.length - 1];
        const dateInput = document.querySelector('#reportDate');
        if(dateInput) dateInput.value = latest;
        loadDate();
        const status = document.querySelector('#status');
        if(status) status.textContent = `Импортировано записей: ${entries.length}. Открыта дата ${new Date(latest + 'T12:00:00').toLocaleDateString('ru-RU')}.`;
        alert(`Данные добавлены. Импортировано записей: ${entries.length}.`);
      }catch(error){
        console.error('Backup merge import failed', error);
        alert('Не удалось прочитать файл импорта.');
      }
    };
    reader.readAsText(file);
  };

  if(sessionStorage.getItem(SESSION_KEY)==='1'&&!localStorage.getItem(SEED_MARKER)){
    sessionStorage.removeItem(SESSION_KEY);
    location.reload();
  }
})();
