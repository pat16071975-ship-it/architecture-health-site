const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const code=fs.readFileSync(process.argv[2],'utf8');
function run(search='',store={},blocked=false,host='azmednsk.ru'){
 const scripts=[],events={}; const ctx={location:{hostname:host,search,href:'https://'+host+'/'+search},URLSearchParams,URL,console:{warn(){}},localStorage:{setItem(k,v){if(blocked)throw Error();store[k]=v},getItem(k){if(blocked)throw Error();return store[k]},removeItem(k){if(blocked)throw Error();delete store[k]}},document:{referrer:'',scripts:[],createElement(){return {}},getElementsByTagName(){return [{parentNode:{insertBefore(k){scripts.push(k)}}}]},addEventListener(k,v){events[k]=v}}};ctx.window=ctx;vm.runInNewContext(code,ctx);return {ctx,scripts,events};
}
let store={};let x=run('?az_metrika=off',store);assert.equal(x.scripts.length,0);assert.equal(x.ctx.ym,undefined);assert.equal(Object.keys(x.events).length,0);
x=run('',store);assert.equal(x.scripts.length,0);
x=run('?az_metrika=on',store);assert.equal(x.scripts.length,1);assert.equal(x.ctx.ym.a[0][1],'init');
x=run('',store);assert.equal(x.scripts.length,1);
for(const [href,goal] of [['tel:+73832842421','phone_click'],['#booking','booking_open'],['https://booking.medflex.ru/?employeeId=203140','booking_medflex']]){x.events.click({target:{closest(){return {getAttribute(){return href}}}}});assert.equal(x.ctx.ym.a.at(-1)[2],goal)}
assert.equal(run('?az_metrika=off',{},true).scripts.length,0);
assert.equal(run('',{},true).scripts.length,1);
assert.equal(run('?az_metrika=on',{},true).scripts.length,1);
assert.equal(run('?az_metrika=unknown').scripts.length,1);
assert.equal(run('?utm_source=metrika_test').scripts.length,1);
assert.equal(run('',{},false,'example.org').scripts.length,0);
const clean=run();vm.runInNewContext(code,clean.ctx);assert.equal(clean.scripts.length,1);
console.log('QA_OFF_PERSISTENCE_ON_NORMAL_GOALS_STORAGE_FAILURE_HOST_DUPLICATE=PASS');
