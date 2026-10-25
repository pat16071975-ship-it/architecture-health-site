const {chromium}=require('/opt/codex/runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});const page=await browser.newPage();
 const root=path.resolve('tilda-migration/public');const requests=[];
 await page.route('**/*',async route=>{
  const u=new URL(route.request().url());
  if(u.hostname==='mc.yandex.ru'){requests.push(u.href);return route.fulfill({contentType:'application/javascript',body:''});}
  if(u.hostname!=='azmednsk.ru')return route.abort();
  let p=path.join(root,decodeURIComponent(u.pathname));if(u.pathname.endsWith('/'))p=path.join(p,'index.html');
  if(!fs.existsSync(p)||!fs.statSync(p).isFile())return route.abort();
  const ext=path.extname(p);return route.fulfill({body:fs.readFileSync(p),contentType:({'.html':'text/html','.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml','.jpg':'image/jpeg','.webp':'image/webp','.png':'image/png'})[ext]||'application/octet-stream'});
 });
 let count=0;
 for(const viewport of [{width:1440,height:1000},{width:390,height:844}]){
 await page.setViewportSize(viewport);
 for(const url of ['/','/team/','/chto-my-lechim/','/team/filatova-anna/']){
  await page.goto('https://azmednsk.ru'+url,{waitUntil:'load'});
  assert.equal(await page.evaluate(()=>ym.a.filter(a=>a[1]==='init').length),1);
  const booking=page.locator('a[href="#booking"]').first();
  if(await booking.count()){
   await booking.click();assert.equal(await page.evaluate(()=>ym.a.filter(a=>a[2]==='booking_open').length),1);
   assert.equal(await page.locator('#booking').evaluate(el=>el.open),true);
   await page.locator('#booking .dialog-close').first().click();
  }
  for(const [selector,goal] of [['a[href^="tel:"]','phone_click'],['a[href^="https://booking.medflex.ru/"]','booking_medflex']]){
   await page.locator(selector).first().evaluate(el=>{el.addEventListener('click',e=>e.preventDefault(),{once:true});el.click();});
   assert.equal(await page.evaluate(g=>ym.a.filter(a=>a[2]===g).length,goal),1);
  }
  count++;
 }
 }
 console.log(JSON.stringify({browser:'chromium',pages_and_viewports:count,init_once:true,booking_dialog:true,goals:true,real_analytics_sent:false,tag_requests:requests.length}));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
