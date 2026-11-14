const vm=require('vm'),fs=require('fs'),assert=require('assert');
const code=fs.readFileSync('/tmp/portrait-update/portraits.js','utf8');
async function run(path,reduced=false){
 let tick,time=0,fail=false,animated=0;
 const imgs=['zubachev','filatova','lyubaeva'].map(key=>({dataset:{azPerson:key},src:'/assets/portraits-20261114-v1/'+key+'-1.webp',isConnected:true,complete:true,matches:()=>false,closest:()=>null,animate:()=>{animated++;return{finished:Promise.resolve(),cancel(){}}}}));
 const context={location:{pathname:path},matchMedia:()=>({matches:reduced}),document:{hidden:false,querySelectorAll:()=>imgs},IntersectionObserver:class{constructor(cb){this.cb=cb}observe(img){this.cb([{target:img,isIntersecting:true}])}},Image:class{async decode(){if(fail)throw Error('network')}},setInterval:fn=>tick=fn,Date:{now:()=>time},Math};
 vm.runInNewContext(code,context);
 if(path==='/team/'||path==='/team-2/'){assert(!tick);return}
 for(let j=0;j<100;j++){time+=10000;const before=imgs.map(i=>i.src);tick();await new Promise(r=>setImmediate(r));
  assert(imgs[2].src.endsWith('lyubaeva-1.webp'));for(let k=0;k<2;k++){assert(imgs[k].src.includes(imgs[k].dataset.azPerson+'-'));if(j>0&&!reduced)assert.notEqual(imgs[k].src,before[k]);}}
 let before=imgs.map(i=>i.src);context.document.hidden=true;time+=10000;tick();await new Promise(r=>setImmediate(r));assert.deepEqual(imgs.map(i=>i.src),before);
 context.document.hidden=false;fail=true;time+=10000;tick();await new Promise(r=>setImmediate(r));assert.deepEqual(imgs.map(i=>i.src),before);
 if(reduced)assert.equal(animated,0);
}
(async()=>{for(const p of ['/team/','/team-2/','/team/zubachev-roman/'])await run(p);await run('/',true);console.log('JS_TEST=PASS: identities isolated; no consecutive repeat; single static; team excluded; hidden paused; failed image retained; reduced motion respected')})();
