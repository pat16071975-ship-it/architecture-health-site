(() => {
'use strict';
if (/^\/team(?:-2)?\/?$/.test(location.pathname)) return;
const pools = {"zubachev": ["/assets/portraits-20261114-v1/zubachev-1.webp", "/assets/portraits-20261114-v1/zubachev-2.webp", "/assets/portraits-20261114-v1/zubachev-3.webp", "/assets/portraits-20261114-v1/zubachev-4.webp", "/assets/portraits-20261114-v1/zubachev-5.webp", "/assets/portraits-20261114-v1/zubachev-6.webp", "/assets/portraits-20261114-v1/zubachev-7.webp", "/assets/portraits-20261114-v1/zubachev-8.webp", "/assets/portraits-20261114-v1/zubachev-9.webp", "/assets/portraits-20261114-v1/zubachev-10.webp", "/assets/portraits-20261114-v1/zubachev-11.webp"], "chirkov": ["/assets/portraits-20261114-v1/chirkov-1.webp", "/assets/portraits-20261114-v1/chirkov-2.webp", "/assets/portraits-20261114-v1/chirkov-3.webp", "/assets/portraits-20261114-v1/chirkov-4.webp", "/assets/portraits-20261114-v1/chirkov-5.webp"], "starostenko": ["/assets/portraits-20261114-v1/starostenko-1.webp", "/assets/portraits-20261114-v1/starostenko-2.webp"], "dimitrenko": ["/assets/portraits-20261114-v1/dimitrenko-1.webp", "/assets/portraits-20261114-v1/dimitrenko-2.webp"], "filatova": ["/assets/portraits-20261114-v1/filatova-1.webp", "/assets/portraits-20261114-v1/filatova-2.webp", "/assets/portraits-20261114-v1/filatova-3.webp", "/assets/portraits-20261114-v1/filatova-4.webp", "/assets/portraits-20261114-v1/filatova-5.webp"], "nazyrov": ["/assets/portraits-20261114-v1/nazyrov-1.webp", "/assets/portraits-20261114-v1/nazyrov-2.webp", "/assets/portraits-20261114-v1/nazyrov-3.webp"], "besedina": ["/assets/portraits-20261114-v1/besedina-1.webp", "/assets/portraits-20261114-v1/besedina-2.webp"], "lyubaeva": ["/assets/portraits-20261114-v1/lyubaeva-1.webp"], "pavlova": ["/assets/portraits-20261114-v1/pavlova-1.webp"], "lapina": ["/assets/portraits-20261114-v1/lapina-1.webp"], "holodtseva": ["/assets/portraits-20261114-v1/holodtseva-1.webp"]};
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const states = [];
const nextIndex = (length, current) => { const n = Math.floor(Math.random() * (length - 1)); return n >= current ? n + 1 : n; };
const observer = new IntersectionObserver(entries => entries.forEach(e => { e.target._azVisible = e.isIntersecting; }), {rootMargin:'0px'});
for (const img of document.querySelectorAll('img[data-az-person]')) {
 const pool = pools[img.dataset.azPerson];
 if (!pool || pool.length < 2) continue;
 const state = {img,pool,index:0,busy:false,first:true,due:0}; states.push(state); observer.observe(img);
}
async function swap(s) {
 s.busy = true;
 const index = s.first ? Math.floor(Math.random()*s.pool.length) : nextIndex(s.pool.length,s.index);
 s.first = false;
 try {
  if (index === s.index) return;
  const preload = new Image(); preload.src = s.pool[index]; await preload.decode();
  if (!s.img.isConnected || document.hidden || !s.img._azVisible) return;
  // Fade only after a decoded image is available; keep the original layout.
  const duration = reduced.matches ? 0 : 220;
  let fade;
  if (duration) { fade=s.img.animate([{opacity:1},{opacity:0.35}],{duration,fill:'forwards'}); await fade.finished; }
  s.img.src = s.pool[index]; s.index = index;
  if (duration) {
   fade.cancel();
   await s.img.animate([{opacity:0.35},{opacity:1}],{duration}).finished;
  }
 } catch (_) { /* Keep last successfully loaded photograph. */ }
 finally { s.busy=false; s.due=Date.now()+6500+Math.random()*1500; }
}
setInterval(() => {
 if(document.hidden) return;
 for(const s of states) {
  if(s.busy || !s.img._azVisible || !s.img.complete || Date.now()<s.due) continue;
  if(!s.first && (reduced.matches || s.img.matches(':hover') || s.img.closest('a,button')?.matches(':focus-within'))) continue;
  swap(s);
 }
},700);
})();
