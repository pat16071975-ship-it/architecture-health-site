/* Yandex Metrica 113776005: public website visits and contact actions only. */
(function () {
  'use strict';
  if (!/^(www\.)?azmednsk\.ru$/i.test(location.hostname) || window.azMetrikaInstalled) return;
  window.azMetrikaInstalled = true;
  (function(m,e,t,r,i,k,a){
    m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};
    m[i].l=1*new Date();
    for(var j=0;j<document.scripts.length;j++){if(document.scripts[j].src===r)return;}
    k=e.createElement(t);a=e.getElementsByTagName(t)[0];k.async=1;k.src=r;a.parentNode.insertBefore(k,a);
  })(window,document,'script','https://mc.yandex.ru/metrika/tag.js?id=113776005','ym');
  window.ym(113776005,'init',{
    ssr:true,clickmap:true,webvisor:false,
    referrer:document.referrer,url:location.href,
    accurateTrackBounce:true,trackLinks:true
  });
  // No form values, patient identifiers, diagnosis parameters or booking completion events.
  document.addEventListener('click',function(event){
    var link=event.target && event.target.closest ? event.target.closest('a[href]') : null;
    if(!link)return;
    var href=link.getAttribute('href') || '',goal=null;
    if(/^tel:/i.test(href))goal='phone_click';
    else if(href==='#booking')goal='booking_open';
    else {
      try {
        var url=new URL(href,location.href);
        if(url.protocol==='https:' && url.hostname==='booking.medflex.ru')goal='booking_medflex';
      }catch(ignore){}
    }
    if(goal)window.ym(113776005,'reachGoal',goal);
    // Preserve native links and existing booking dialog handlers.
  },true);
})();
