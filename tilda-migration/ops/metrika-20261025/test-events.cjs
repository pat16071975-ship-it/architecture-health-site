const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const code=fs.readFileSync('tilda-migration/public/assets/az-metrika-20261025.js','utf8');
function fixture(host='azmednsk.ru'){
 const callbacks={},loads=[];const c={location:{hostname:host,href:'https://'+host+'/?utm_source=test'},URL,Date};c.window=c;
 c.document={scripts:[],referrer:'https://2gis.ru/',createElement:()=>({}),getElementsByTagName:()=>[{parentNode:{insertBefore:x=>loads.push(x)}}],addEventListener:(n,cb)=>callbacks[n]=cb};
 vm.runInNewContext(code,c);return {c,callbacks,loads};
}
const f=fixture();assert.equal(f.loads.length,1);assert.equal(f.c.ym.a[0][1],'init');assert.equal(f.c.ym.a[0][2].webvisor,false);
for(const [href,goal] of [['#booking','booking_open'],['https://booking.medflex.ru/?user=example','booking_medflex'],['tel:+73832842421','phone_click']]){
 let prevented=false;f.callbacks.click({target:{closest:()=>({getAttribute:()=>href})},preventDefault:()=>prevented=true});
 assert.equal(f.c.ym.a.at(-1)[2],goal);assert.equal(f.c.ym.a.at(-1).length,3);assert.equal(prevented,false);
}
const len=f.c.ym.a.length;
for(const href of ['/team/','mailto:info@expertstom.ru','https://booking.medflex.ru.evil.example/','https://example.com/?next=booking.medflex.ru','#mission'])f.callbacks.click({target:{closest:()=>({getAttribute:()=>href})}});
assert.equal(f.c.ym.a.length,len);vm.runInNewContext(code,f.c);assert.equal(f.c.ym.a.length,len);assert.equal(f.loads.length,1);
assert.equal(fixture('localhost').loads.length,0);assert.equal(fixture('preview.azmednsk.ru').loads.length,0);assert.equal(fixture('www.azmednsk.ru').loads.length,1);
console.log('EVENTS=PASS; DEDUPLICATION=PASS; HOST_GUARD=PASS; NATIVE_NAVIGATION=UNCHANGED');
