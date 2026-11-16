const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('az-baze-auth/templates/uploads.html','utf8');
const code = html.slice(html.indexOf('  const automaticStatus='),html.indexOf("  const overlay=",html.indexOf("  const automaticStatus=")));
async function scenario(response, failure=false) {
  const element={textContent:''}, calls=[], timers=[];
  const context={document:{getElementById:()=>element},
    fetch:async(url,options)=>{calls.push({url,options});if(failure)throw Error('offline');return response;},
    AbortSignal:{timeout:()=> 'timeout'},window:{setTimeout:(fn,delay)=>timers.push({fn,delay})}};
  vm.runInNewContext(code,context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/reports/advertising/metrika/sync-status');
  assert.equal(calls[0].options.cache,'no-store');assert.equal(calls[0].options.credentials,'same-origin');
  assert.equal(timers.length,1);assert.equal(timers[0].delay,60000);
  return element.textContent;
}
(async()=>{
 const message='Автоматическая проверка успешно завершена — 16.11.2026 в 16:00.';
 assert.equal(await scenario({ok:true,json:async()=>({message})}),message);
 for(const response of [{ok:false},{ok:true,json:async()=>({})}])
   assert.match(await scenario(response),/Не удалось получить статус/);
 assert.match(await scenario(null,true),/через минуту/);
 assert(html.indexOf('id="metrikaAutomaticStatus"') > html.indexOf('id="metrikaAutoLoad"'));
 console.log('AUTO_SYNC_STATUS_UI=PASS; success, HTTP error, malformed response, offline, polling');
})().catch(e=>{console.error(e);process.exitCode=1;});
