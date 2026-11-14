(() => {
'use strict';
const pools = {"zubachev": ["/assets/portraits-20261114-v1/zubachev-1.webp", "/assets/portraits-20261114-v1/zubachev-2.webp", "/assets/portraits-20261114-v1/zubachev-3.webp", "/assets/portraits-20261114-v1/zubachev-4.webp", "/assets/portraits-20261114-v1/zubachev-5.webp", "/assets/portraits-20261114-v1/zubachev-6.webp", "/assets/portraits-20261114-v1/zubachev-7.webp", "/assets/portraits-20261114-v1/zubachev-8.webp", "/assets/portraits-20261114-v1/zubachev-9.webp", "/assets/portraits-20261114-v1/zubachev-10.webp", "/assets/portraits-20261114-v1/zubachev-11.webp"], "chirkov": ["/assets/portraits-20261114-v1/chirkov-1.webp", "/assets/portraits-20261114-v1/chirkov-2.webp", "/assets/portraits-20261114-v1/chirkov-3.webp", "/assets/portraits-20261114-v1/chirkov-4.webp", "/assets/portraits-20261114-v1/chirkov-5.webp"], "starostenko": ["/assets/portraits-20261114-v1/starostenko-1.webp", "/assets/portraits-20261114-v1/starostenko-2.webp"], "dimitrenko": ["/assets/portraits-20261114-v1/dimitrenko-1.webp", "/assets/portraits-20261114-v1/dimitrenko-2.webp"], "filatova": ["/assets/portraits-20261114-v1/filatova-1.webp", "/assets/portraits-20261114-v1/filatova-2.webp", "/assets/portraits-20261114-v1/filatova-3.webp", "/assets/portraits-20261114-v1/filatova-4.webp", "/assets/portraits-20261114-v1/filatova-5.webp"], "nazyrov": ["/assets/portraits-20261114-v1/nazyrov-1.webp", "/assets/portraits-20261114-v1/nazyrov-2.webp", "/assets/portraits-20261114-v1/nazyrov-3.webp"], "besedina": ["/assets/portraits-20261114-v1/besedina-1.webp", "/assets/portraits-20261114-v1/besedina-2.webp"], "lyubaeva": ["/assets/portraits-20261114-v1/lyubaeva-1.webp"], "pavlova": ["/assets/portraits-20261114-v1/pavlova-1.webp"], "lapina": ["/assets/portraits-20261114-v1/lapina-1.webp"], "holodtseva": ["/assets/portraits-20261114-v1/holodtseva-1.webp"]};
const choices = new Map();
window.azRenderPortraitV3 = function(script) {
 const template=script.previousElementSibling;
 if(!template || !template.matches('template[data-az-portrait-template]')) return;
 const img=template.content.firstElementChild.cloneNode(true);
 const person=img.dataset.azPerson, pool=pools[person];
 if(!pool || !pool.length) return;
 let url=choices.get(person);
 if(!url) {
  const key='az-portrait-v2:'+location.pathname+':'+person;
  let last=null;
  try {last=sessionStorage.getItem(key);} catch(_) {}
  const options=pool.length>1 ? pool.filter(u=>u!==last) : pool;
  url=options[Math.floor(Math.random()*options.length)];
  choices.set(person,url);
  try {sessionStorage.setItem(key,url);} catch(_) {}
 }
 img.removeAttribute('data-az-src');
 // The chosen src is assigned while detached: no default image ever paints.
 img.src=url;
 template.replaceWith(img);
};
})();