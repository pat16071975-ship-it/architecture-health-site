from pathlib import Path
from bs4 import BeautifulSoup
import re,json,hashlib,base64,zlib,copy
B=Path(__file__).parent;L=B/'live';O=B/'out';O.mkdir(exist_ok=True)
# Reuse the prepared, approved clinical topics and doctor mapping.
ns={'__file__':str(Path('az-kids-topics-20261027/build.py').resolve())}
exec(Path('az-kids-topics-20261027/build.py').read_text().split("block='<section")[0],ns)
T=ns['T'];card=ns['card']
# Refresh cards from the current live team, preserving personal booking links.
team=BeautifulSoup((L/'team/index.html').read_text(),'html.parser')
for d in ns['D'].values():
 p=next((p for p in team.select('.person') if p.select_one('a[href="'+d['profile']+'"]')),None)
 assert p is not None,d['profile']
 d['image']=p.img['src'];a=p.select_one('.az-doctor-booking');assert a;d['booking']=a['href']
slugs=['karies-u-detej','detskij-ortodont','rebenok-dyshit-rtom','rebenok-skripit-zubami','glotanie-i-polozhenie-yazyka','korotkaya-uzdechka-yazyka','narusheniya-osanki-u-detej']
titles=['Кариес у детей: молочные и постоянные зубы','Детский ортодонт: неправильный прикус у ребёнка','Ребёнок дышит ртом или спит с открытым ртом','Ребёнок скрипит зубами во сне: что делать','Положение языка, глотание и жевание у детей','Короткая уздечка языка у ребёнка','Нарушения осанки у детей: когда нужен осмотр']
intros=[
'Пятнышко на зубе или жалоба во время еды могут тревожить родителей. Не нужно винить себя или ждать, пока появится сильная боль: начните с осмотра. В «Архитектуре здоровья» в Новосибирске администратор поможет уточнить возможность детского приёма и выбрать стоматолога.',
'Если зубы ребёнка растут неровно, хочется понять, нужно ли уже что-то делать. На консультации детского ортодонта в Новосибирске можно обсудить развитие прикуса, сроки наблюдения и возможную коррекцию. Мы поможем выбрать первый шаг без спешки.',
'Вы заметили, что ребёнок дышит ртом днём или спит с открытым ртом. Важно разобраться, свободно ли он дышит носом, есть ли храп и как чувствует себя после сна. Начать следует с педиатра или ЛОР-врача; в нашей клинике можно обсудить вопросы прикуса и оральных функций.',
'Скрежет зубами во сне часто первым замечает кто-то из родителей. Это повод спокойно обсудить наблюдения со стоматологом, особенно если ребёнок жалуется на челюсть или чувствительность зубов. В клинике «Архитектура здоровья» в Новосибирске поможем выбрать консультацию.',
'Ребёнку может быть непросто объяснить, почему неудобно жевать или глотать. Ваши наблюдения помогают специалисту: расскажите, когда это происходит и какую пищу ребёнок избегает. Начнём с оценки жалоб, чтобы подобрать помощь бережно и по делу.',
'Вопрос о короткой уздечке языка не всегда означает, что ребёнку нужна операция. Важнее понять, мешает ли ограничение движения конкретной функции. Вместе со специалистами можно разобраться в жалобах и обсудить обоснованные варианты помощи.',
'Если ребёнок сутулится, плечи выглядят несимметрично или беспокоит спина, не нужно ругать его за осанку. Расскажите, что вы заметили, врачу. В «Архитектуре здоровья» в Новосибирске можно уточнить возможность консультации отделения структуры с учётом возраста ребёнка.'
]
faqs=[
[('Нужно ли лечить молочные зубы?','Да, если врач обнаружил заболевание. Ожидать смены зуба при боли или воспалении не стоит.'),('Как узнать стоимость лечения кариеса у ребёнка?','После осмотра врач объяснит, какая помощь нужна. Стоимость зависит от состояния зуба и объёма лечения; администратор уточнит цену консультации.')],
[('Всегда ли неровные зубы требуют немедленного лечения?','Нет. Ортодонт оценивает возраст, этап смены зубов и особенности развития. Иногда достаточно наблюдения.'),('Как записаться к детскому ортодонту в Новосибирске?','Выберите специалиста ниже. При записи укажите возраст ребёнка и основную жалобу; если сомневаетесь, напишите администратору.')],
[('Можно ли просто приучать ребёнка закрывать рот?','Сначала необходимо выяснить, нет ли препятствий для носового дыхания. Не заклеивайте ребёнку рот во сне.'),('К кому обращаться, если ребёнок храпит?','К педиатру или ЛОР-врачу. При остановках дыхания во сне нужна своевременная медицинская оценка. Наши специалисты подключаются по показаниям к вопросам прикуса и мышечных функций.')],
[('Почему ребёнок скрипит зубами во сне?','По одному скрежету причину установить нельзя. Врач уточняет состояние зубов, жалобы и особенности сна; при необходимости рекомендует дополнительные консультации.'),('Нужна ли ребёнку капа?','Это решает стоматолог после осмотра. Не следует самостоятельно выбирать взрослую капу или начинать лечение по совету из интернета.')],
[('Может ли жевание на одной стороне быть связано с зубами?','Да, поэтому важно сначала исключить боль и другие стоматологические проблемы.'),('Кто проводит миофункциональные занятия?','Миофункциональные специалисты работают с оральными функциями в пределах своей компетенции. Занятия дополняют согласованный план и не заменяют медицинский осмотр.')],
[('Как определить, мешает ли уздечка языка?','Нужна очная оценка движения и функции языка с учётом жалоб и возраста. Фотографии недостаточно для решения об операции.'),('Можно ли решить вопрос только упражнениями?','Универсального решения нет. Показания к занятиям или вмешательству определяют индивидуально; при вопросах речи может потребоваться логопед.')],
[('Сутулость и сколиоз — одно и то же?','Нет. Чтобы определить характер изменений и необходимость обследования, нужен осмотр врача.'),('Подойдут ли упражнения из интернета?','При боли, выраженной асимметрии или подозрении на сколиоз сначала нужен осмотр. Подбор нагрузки зависит от состояния ребёнка.')]
]
files={}
def put(n,s):
 b=s.encode() if isinstance(s,str) else s;p=O/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b);files[n]=b
style='<link rel="stylesheet" href="/assets/az-parents-seo-20261028.css">'
css=Path('az-kids-topics-20261027/out/assets/az-kids-topics-20261027.css').read_text()+'''\n.parent-topics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.parent-topic{display:flex;flex-direction:column;border:1px solid #b9d9ee;border-radius:4px;background:#f7fbfe;padding:16px}.parent-topic p{font-size:14px}.parent-topic a{margin-top:auto;font-weight:500;color:#2080c4;min-height:32px}.parent-article{max-width:960px;margin:0 auto}.parent-article section{margin-bottom:22px}.parent-article .kt-doctors{grid-template-columns:repeat(3,minmax(0,240px))}.parent-back{display:inline-block;margin:0 0 14px;color:#2080c4}.parent-note{border-left:3px solid #debc19;background:#fffdf2;padding:14px 16px}.parent-article h1{max-width:900px}.parent-article h2{font-size:23px}.parent-article .kt-copy h4{min-height:63px}.parent-article .kt-copy small{margin-top:auto}.parent-related{display:flex;flex-wrap:wrap;gap:10px}.parent-related a{padding:9px 12px;border:1px solid #dce8f2;border-radius:4px}.parent-article .kt-role{min-height:72px!important}.parent-article .kt-why{min-height:80px!important}@media(max-width:700px){.parent-topics{grid-template-columns:1fr}.parent-topic{padding:12px}.parent-article .kt-doctors{grid-template-columns:1fr}.parent-article h2{font-size:21px}.parent-article .kt-copy h4,.parent-article .kt-role,.parent-article .kt-why{min-height:0!important}}\n'''
put('assets/az-parents-seo-20261028.css',css)
# Rename only actual navigation labels targeting the existing children URL.
def rename(s):
 def anchor(m):
  a=m.group();return re.sub(r'(?<=>)Детям(?=<)', 'Родителям',a)
 return re.sub(r'<a\b[^>]*href=[\"\'](?:https://azmednsk.ru)?/detyam/[\"\'][^>]*>.*?</a>',anchor,s,flags=re.S)
import xml.etree.ElementTree as ET
allowed_pages=set()
for loc in ET.fromstring((L/'sitemap.xml').read_bytes()).iter():
 if loc.tag.endswith('loc'):
  assert loc.text.startswith('https://azmednsk.ru/'),loc.text
  path=loc.text.removeprefix('https://azmednsk.ru/').strip('/')
  allowed_pages.add((path+'/' if path else '')+'index.html')
for p in L.rglob('*.html'):
 if p.relative_to(L).as_posix() not in allowed_pages:continue
 s=p.read_text();new=rename(s);n=p.relative_to(L).as_posix()
 if n=='index.html':
  new=new.replace('<a href="/detyam/">Родителям</a>','<a class="az-kids-nav" href="/detyam/"><span>Родителям</span><span aria-hidden="true">↗</span></a>')
  new=new.replace('</head>',style+'</head>')
 if new!=s:put(n,new)
base=rename((L/'detyam/index.html').read_text())
# Keep approved hero, illustration, care and contact sections.
soup=BeautifulSoup(base,'html.parser')
section='<section id="kids-questions"><h2>Что беспокоит вас в здоровье ребёнка?</h2><p>Выберите знакомую ситуацию. В каждом материале — на что обратить внимание, с чего начать и к кому обратиться.</p><div class="parent-topics">'
for i,t in enumerate(T):section+='<article class="parent-topic"><h3>'+titles[i]+'</h3><p>'+t[1]+'</p><a href="/detyam/'+slugs[i]+'/">Разобраться и выбрать специалиста ↗</a></article>'
section+='</div><div class="kt-future"><h3>Забота сегодня — больше возможностей завтра</h3><p>Своевременный осмотр помогает понять, когда достаточно наблюдения, а когда важно действовать. Если заболевание прогрессирует, лечение может стать сложнее и дороже. Мы поможем разобраться без спешки и лишней тревоги.</p></div></section>'
soup.select_one('#kids-questions').replace_with(BeautifulSoup(section,'html.parser'))
soup.head.append(BeautifulSoup(style,'html.parser'));put('detyam/index.html',str(soup))
# Reuse the live page shell including real booking, cookies, messengers and tracking.
for i,t in enumerate(T):
 title,signs,risk,help_,related,docs=t;url='https://azmednsk.ru/detyam/'+slugs[i]+'/'
 s=BeautifulSoup(base,'html.parser');s.title.string=(('Детский ортодонт в Новосибирске' if i==1 else titles[i]+' — Новосибирск')+' | Архитектура здоровья')
 description=intros[i]
 for q in s.select('link[rel="canonical"]'):q['href']=url
 for q in s.select('meta[property="og:url"]'):q['content']=url
 for q in s.select('meta[property="og:title"]'):q['content']=s.title.string
 for q in s.select('meta[name="description"],meta[property="og:description"]'):q['content']=description
 s.head.append(BeautifulSoup(style,'html.parser'))
 content='<div class="az-kids parent-article"><a class="parent-back" href="/detyam/">← Все вопросы о здоровье детей</a><h1>'+titles[i]+'</h1><p>'+intros[i]+'</p>'
 for heading,text in [('Что можно заметить',signs),('Почему важно разобраться вовремя',risk),('С чего начать и как мы можем помочь',help_)]:content+='<section><h2>'+heading+'</h2><p>'+text+'</p></section>'
 content+='<section class="parent-note"><h2>Подготовимся к приёму вместе</h2><p>Запишите, когда вы заметили изменения и что беспокоит ребёнка. Возьмите имеющиеся заключения и результаты обследований. Покупать новые исследования заранее не нужно — их необходимость обсудите с врачом.</p><p>Расскажите администратору о возрасте ребёнка и особенностях, которые помогут сделать знакомство со специалистом спокойнее.</p></section>'
 content+='<section><h2>К кому обратиться</h2><p>Выберите специалиста или попросите администратора помочь с записью. Возрастные ограничения уточняются перед приёмом.</p><div class="kt-doctors">'+''.join(card(*d) for d in docs)+'</div></section><section><h2>Вопросы родителей</h2>'
 for q,a in faqs[i]:content+='<details><summary>'+q+'</summary><div><p>'+a+'</p></div></details>'
 content+='</section><section><h2>Другие вопросы о здоровье детей</h2><div class="parent-related">'
 for j in [(i+1)%7,(i+2)%7]:content+='<a href="/detyam/'+slugs[j]+'/">'+titles[j]+'</a>'
 content+='</div></section><p>Материал помогает подготовиться к консультации и не заменяет индивидуальные рекомендации после осмотра.</p></div>'
 s.select_one('.az-kids').replace_with(BeautifulSoup(content,'html.parser'))
 put('detyam/'+slugs[i]+'/index.html',str(s))
# Discoverability in sitemap; retain existing URLs including /detyam/.
s=(L/'sitemap.xml').read_text();s=s.replace('</urlset>',''.join('<url><loc>https://azmednsk.ru/detyam/'+x+'/</loc></url>' for x in slugs)+'</urlset>');put('sitemap.xml',s)
h=lambda b:hashlib.sha256(b).hexdigest()
M={'files':{n:{'before':h((L/n).read_bytes()) if (L/n).exists() else None,'after':h(b),'data':base64.b64encode(zlib.compress(b,9)).decode()} for n,b in files.items()},'protected':{}}
for n,r in M['files'].items():
 if n.endswith('.html') and n!='index.html' and not n.startswith('detyam/'):
  r.pop('data');r['rename']=True
t=Path('az-team-align-20261027/apply_team_align_20261027.py').read_text();t=re.sub(r'^M=.*$',lambda m:'M='+repr(M),t,flags=re.M).replace('team-align-','parents-seo-').replace('TEAM_ALIGNMENT=PASS; HTTPS=PASS; HTML_UPDATED=1',f'PARENTS_SEO=PASS; HTTPS=PASS; FILES_UPDATED={len(files)}; NEW_PAGES=7').replace('PHOTOS_TEXTS_PRICES_AND_BOOKING_LINKS=UNCHANGED','EXISTING_PHOTOS_PRICES_AND_BOOKING_LINKS=UNCHANGED')
t=t.replace("new=zlib.decompress(base64.b64decode(r['data']))", "new=(old if sha(old)==r['after'] else rename(old.decode()).encode()) if r.get('rename') else zlib.decompress(base64.b64decode(r['data']))")
rename_source=__import__('inspect').getsource(rename)
t=t.replace('def prepare(root):',rename_source+'\ndef prepare(root):')
t=t.replace('import argparse,', 'import re,argparse,') if 'import argparse,' in t else t.replace('from pathlib import Path','import re\nfrom pathlib import Path')
(B/'apply_parents_seo_20261028.py').write_text(t)
print('FILES',len(files),'SHA256',h(t.encode()))
