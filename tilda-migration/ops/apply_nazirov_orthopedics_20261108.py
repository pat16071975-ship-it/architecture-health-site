#!/usr/bin/env python3
from pathlib import Path
import json,re,html,os,socket,sys,tempfile,datetime
DATA = {'topics': [['crowns-bridges', 'Отсутствующие зубы: коронки и мосты', 'Протезирование с опорой на свои зубы и имплантаты', 'При отсутствии одного или нескольких зубов стоматолог-ортопед оценивает сохранившиеся зубы, ткани вокруг них и возможные опоры для протеза. Назыров выполняет протезирование с опорой на собственные зубы и дентальные имплантаты. Выбор между коронкой, мостовидной конструкцией и другими вариантами зависит от клинической ситуации. Если требуется установка имплантатов, хирургический и ортопедический этапы планируют совместно с хирургом.'], ['edentulous', 'Полное отсутствие зубов: съёмные протезы, All-on-4 и All-on-6', 'Протезирование беззубых челюстей', 'При полном отсутствии зубов возможны съёмные протезы или несъёмные конструкции с опорой на дентальные имплантаты, в том числе по типу All-on-4 и All-on-6. Эти названия описывают конструкции с опорой соответственно на четыре или шесть имплантатов. Подходящий вариант выбирают после обследования и совместного планирования с хирургом. Сроки хирургического лечения, временного и постоянного протезирования обсуждают отдельно.'], ['veneers', 'Изменение формы и эстетики зубов: керамические виниры', 'Эстетическая реконструкция улыбки', 'Цельнокерамические виниры применяют для изменения формы и внешнего вида передних зубов по показаниям. На консультации обсуждают пожелания к улыбке, состояние эмали и существующих реставраций, смыкание зубов и нагрузку. Перед выбором виниров врач оценивает, подходит ли этот способ и нужны ли предварительное лечение или ортодонтическая подготовка.'], ['replacement', 'Дефектные коронки и мосты: перепротезирование', 'Замена ранее установленных конструкций', 'Сколы, нарушение прилегания, подвижность конструкции, застревание пищи или неудовлетворительная эстетика — поводы обратиться к ортопеду. Врач оценит старую конструкцию, опорные зубы и ткани вокруг них. После обследования обсуждают возможность ремонта, замены коронки или моста и необходимость предварительного лечения. При боли или подвижности конструкции не откладывайте осмотр.'], ['tmj', 'Боль в ВНЧС и жевательных мышцах', 'Помощь при нарушениях височно-нижнечелюстного сустава', 'Назыров принимает пациентов с жалобами на боль в области челюстного сустава и жевательных мышц, болезненные щелчки и ограничение открывания рта. Сначала нужно установить причину симптомов и определить план помощи. Лечение подбирают по диагнозу; при необходимости подключают других специалистов. Коронки и изменение прикуса не являются универсальным способом лечения боли в ВНЧС.'], ['bite', 'Восстановление формы зубов и их смыкания', 'Ортопедическая коррекция по показаниям', 'Когда зубам требуется восстановление, коронки, накладки и в отдельных случаях виниры позволяют изменить их форму и контакты при смыкании. Возможности зависят от состояния зубов, степени износа и характера нарушения. Протезирование не перемещает зубы как брекеты или элайнеры и не заменяет ортодонтическое лечение во всех случаях. План составляют после диагностики, при необходимости вместе с ортодонтом.'], ['damage', 'Сколы и восстановление зубов после лечения каналов', 'Коронки и накладки для повреждённых зубов', 'После скола или эндодонтического лечения врач оценивает объём сохранившихся тканей зуба и жевательную нагрузку. Для восстановления могут использоваться коронки или накладки. Накладка восстанавливает часть зуба, а коронка охватывает его коронковую часть. Выбор конструкции индивидуален: лечение каналов само по себе не означает, что каждому зубу обязательно нужна одинаковая коронка.'], ['splints', 'Бруксизм и мышечное напряжение: индивидуальные каппы', 'Защитные и окклюзионные каппы', 'Назыров изготавливает индивидуальные каппы при бруксизме и по показаниям при жалобах на напряжение жевательных мышц. Врач определяет назначение каппы, режим ношения и контрольные визиты. Каппа может защищать зубы и реставрации от повреждения, но не гарантирует устранения причин бруксизма или боли. Если при ношении появились боль или дискомфорт, обратитесь к врачу для проверки.']], 'faq': [['Сколько времени занимает изготовление коронки?', 'В клинике возможно срочное изготовление коронки за 1–3 дня; стандартный срок изготовления — до 14 дней. Возможность срочного изготовления и точный срок врач подтвердит после осмотра и выбора конструкции. Это срок изготовления коронки, а не всего лечения: подготовка зуба, лечение каналов или этапы имплантации могут потребовать дополнительного времени.'], ['Насколько сильно обтачивают зуб под коронку?', 'Объём подготовки зависит от материала и типа конструкции, состояния зуба и жевательной нагрузки. Врач стремится сохранить здоровые ткани, соблюдая требования к прочности и фиксации выбранной коронки. Единую глубину обточки для всех коронок назвать нельзя. До начала лечения ортопед объяснит, какой объём подготовки требуется именно в вашем случае.'], ['Какая гарантия на коронки?', 'Гарантия на коронки в клинике — один календарный год. Условия гарантии уточняются перед лечением и фиксируются в документах клиники. Гарантийный срок не равен сроку службы коронки.'], ['Какие виды коронок устанавливают в клинике?', 'В клинике устанавливают цельнокерамические коронки и коронки из диоксида циркония, реже — металлокерамические. Если зуб требует наблюдения, может использоваться временная пластмассовая коронка лабораторного изготовления. Материал и конструкцию подбирают по состоянию зуба, нагрузке и эстетическим задачам.']]}
SLUG='protezirovanie-zubov'
URL='https://azmednsk.ru/chto-my-lechim/'+SLUG+'/'
MARK='az-prosthodontics-20261108'
BOOK='https://booking.medflex.ru/?user=e0c452bf7c5bc897e8b8c0782bcb16cb&amp;employeeId=1075768&amp;source=4'
def once(s,old,new):
 assert s.count(old)==1,'STOP: changed template: '+old[:100]
 return s.replace(old,new,1)
def prepare(root):
 catalog=(root/'chto-my-lechim/index.html').read_text()
 faq=(root/'otvety-na-voprosy/index.html').read_text()
 template=(root/'chto-my-lechim/karies/index.html').read_text()
 doctor=(root/'team/nazirov-uktamjon/index.html').read_text()
 assert 'Назыров' in doctor and '1075768' in doctor,'STOP: wrong doctor'
 assert MARK not in catalog and 'id="prosthodontics"' not in faq,'STOP: already installed'
 assert not (root/('chto-my-lechim/'+SLUG+'/index.html')).exists(),'STOP: destination exists'
 entries=[];sections=[]
 for key,label,title,text in DATA['topics']:
  href='/chto-my-lechim/'+SLUG+'/#'+key
  entries.append('<a data-roles=\'["Стоматолог"]\' data-search="'+html.escape(label+' стоматолог ортопед Назыров протезирование виниры коронки')+'" href="'+href+'">'+html.escape(label)+'<span aria-hidden="true">↗</span></a>')
  extra=''
  if key=='tmj':extra='<p><a href="/chto-my-lechim/vnchs/">Подробнее о боли и щелчках в челюсти →</a></p>'
  if key=='splints':extra='<p><a href="/chto-my-lechim/bruksizm/">Подробнее о бруксизме →</a></p>'
  if key=='bite':extra='<p><a href="/chto-my-lechim/prikus/">Об ортодонтическом лечении →</a></p>'
  sections.append('<section id="'+key+'"><h2>'+html.escape(title)+'</h2><p>'+html.escape(text)+'</p>'+extra+'</section>')
 group='<details class="topic" id="'+MARK+'" open><summary>Ортопедическая стоматология<b aria-hidden="true">+</b></summary><div class="items">'+''.join(entries)+'</div></details>'
 anchor='<details class="topic" id="group-dental-therapy"'
 catalog=once(catalog,anchor,group+anchor)
 nav='<a aria-controls="prosthodontics" aria-expanded="false" class="faq-direction" href="#prosthodontics"><span>Ортопедическая стоматология — коронки и протезирование</span><span class="count">4 вопроса</span></a>'
 faq=re.sub(r'(<nav\b[^>]*class="faq-directions"[^>]*>)(.*?)(</nav>)',lambda m:m[1]+m[2]+nav+m[3],faq,count=1,flags=re.S)
 assert nav in faq,'STOP: FAQ navigation not found'
 qhtml=''.join('<details class="faq-item" id="prosthodontics-question-'+str(i)+'"><summary>'+html.escape(q)+'</summary><p class="faq-answer">'+html.escape(a)+'</p></details>' for i,(q,a) in enumerate(DATA['faq'],1))
 panel='<details class="faq-panel" id="prosthodontics"><summary><h2>Ортопедическая стоматология</h2><span class="faq-section-count">4 вопроса</span></summary><div class="faq-question-list">'+qhtml+'</div><div class="faq-next"><p>Приём ведёт Назыров Уктамжон Жумавоевич, стоматолог-ортопед.</p><a href="/team/nazirov-uktamjon/">О враче и запись на приём ↗</a> · <a href="/chto-my-lechim/'+SLUG+'/">Подробнее о протезировании ↗</a></div></details>'
 faq=once(faq,'</section></main>',panel+'</section></main>')
 qa=[{'@type':'Question','name':q,'acceptedAnswer':{'@type':'Answer','text':a}} for q,a in DATA['faq']]
 found=[0]
 def update_schema(m):
  obj=json.loads(m[2])
  def visit(x):
   if isinstance(x,dict):
    if x.get('@type')=='FAQPage':x.setdefault('mainEntity',[]).extend(qa);found[0]+=1
    else:
     for v in x.values():visit(v)
   elif isinstance(x,list):
    for v in x:visit(v)
  visit(obj)
  return m[1]+json.dumps(obj,ensure_ascii=False)+m[3]
 faq=re.sub(r'(<script\b[^>]*type="application/ld\+json"[^>]*>)(.*?)(</script>)',update_schema,faq,flags=re.S)
 if not found[0]:faq=once(faq,'</head>','<script type="application/ld+json">'+json.dumps({'@context':'https://schema.org','@type':'FAQPage','mainEntity':qa},ensure_ascii=False)+'</script></head>')
 assert found[0]<=1,'STOP: duplicate FAQ schemas'
 portrait=re.search(r'<img\b[^>]*alt="Назыров Уктамжон Жумавоевич"[^>]*>',doctor)
 assert portrait,'STOP: doctor portrait missing'
 image=re.search(r'\bsrc="([^"]+)"',portrait[0])[1]
 card='<aside class="sidebar" id="dentists"><article class="doctor"><div class="az-dental-head"><img class="portrait" alt="Назыров Уктамжон Жумавоевич" loading="lazy" src="'+image+'"><h3>Назыров<span>Уктамжон Жумавоевич</span></h3></div><p class="role">Врач-стоматолог-ортопед</p><a class="primary" data-az-doctor-booking="1075768" href="'+BOOK+'" target="_blank" rel="noopener">Записаться на приём ↗</a><a class="secondary" href="/team/nazirov-uktamjon/">Подробнее о специалисте</a></article></aside>'
 pagefaq='<section class="questions" id="faq"><h2>Вопросы о коронках</h2>'+''.join('<details><summary>'+html.escape(q)+'</summary><p>'+html.escape(a)+'</p></details>' for q,a in DATA['faq'])+'<p><a href="/otvety-na-voprosy/#prosthodontics">Все ответы по направлению →</a></p></section>'
 intro='<div class="intro"><div class="kicker">ОРТОПЕДИЧЕСКАЯ СТОМАТОЛОГИЯ · НОВОСИБИРСК</div><h1>Протезирование зубов: коронки, накладки и виниры</h1><p class="lead">Восстановление повреждённых и отсутствующих зубов, эстетики улыбки и жевательной функции. Приём ведёт стоматолог-ортопед Назыров Уктамжон Жумавоевич.</p></div>'
 guide='<nav class="guide" aria-label="Содержание статьи">'+''.join('<a href="#'+k+'">'+html.escape(label)+'</a>' for k,label,_,_ in DATA['topics'])+'<a href="#faq">Вопросы о коронках</a><a href="#dentists">Запись к врачу</a></nav>'
 content=intro+guide+'<div class="layout"><article class="reading">'+''.join(sections)+pagefaq+'<section><h2>Как проходит консультация</h2><p>Врач уточнит жалобы и пожелания, осмотрит зубы и имеющиеся конструкции. По показаниям назначит исследования, обсудит варианты протезирования, этапы, сроки и стоимость. Если требуется предварительное лечение или имплантация, согласует последовательность помощи со смежными специалистами.</p></section><p><a href="/chto-my-lechim/">Все темы раздела «Что мы лечим» →</a></p></article>'+card+'</div>'
 page,n=re.subn(r'(<main>).*?(?=<section class="az-chat")',lambda m:m[1]+content,template,flags=re.S)
 assert n==1,'STOP: article template changed'
 title='Протезирование зубов в Новосибирске — стоматолог-ортопед Назыров'
 page=re.sub(r'<title>.*?</title>','<title>'+title+'</title>',page,flags=re.S)
 page=re.sub(r'<meta\b[^>]*(?:name="description"|property="og:[^"]+")[^>]*>','',page)
 page=re.sub(r'<link\b[^>]*rel="canonical"[^>]*>','',page)
 page=re.sub(r'<script\b[^>]*type="application/ld\+json"[^>]*>.*?</script>','',page,flags=re.S)
 meta='<link rel="canonical" href="'+URL+'"><meta name="description" content="Коронки, накладки, виниры, протезы с опорой на зубы и имплантаты. Направления работы и ответы стоматолога-ортопеда Назырова в клинике Архитектура здоровья."><meta property="og:title" content="'+title+'"><meta property="og:url" content="'+URL+'">'
 schema={'@context':'https://schema.org','@graph':[{'@type':'WebPage','@id':URL+'#webpage','url':URL,'name':title,'inLanguage':'ru-RU'},{'@type':'FAQPage','@id':URL+'#faq','mainEntity':qa}]}
 page=once(page,'</head>',meta+'<script type="application/ld+json">'+json.dumps(schema,ensure_ascii=False)+'</script></head>')
 # Make the header booking dialog target this specialist too.
 page=page.replace('href="https://booking.medflex.ru/?user=e0c452bf7c5bc897e8b8c0782bcb16cb"','href="'+BOOK+'" data-az-doctor-booking="1075768"')
 sitemap=(root/'sitemap.xml').read_text();assert URL not in sitemap
 sitemap=once(sitemap,'</urlset>','<url><loc>'+URL+'</loc></url></urlset>')
 return {'chto-my-lechim/index.html':catalog,'otvety-na-voprosy/index.html':faq,'chto-my-lechim/'+SLUG+'/index.html':page,'sitemap.xml':sitemap}
def main():
 check=len(sys.argv)==3 and sys.argv[1]=='--check-root'
 root=Path(sys.argv[2]) if check else Path('/var/www/azmednsk-preview')
 if not check:
  assert len(sys.argv)==1 and os.geteuid()==0 and socket.gethostname()=='vdska','STOP: run as root on vdska'
  c=Path('/etc/nginx/sites-available/azmednsk-preview').read_text()
  assert re.search(r'server_name\s+[^;]*\bazmednsk\.ru\b',c)
  assert set(re.findall(r'^\s*root\s+([^;]+);',c,re.M))=={str(root)}
 changes=prepare(root)
 for rel in changes:
  p=root/rel;assert not any(x.is_symlink() for x in [p,*p.parents]),'STOP: symlink'
 print('PREFLIGHT=PASS; TOPICS=8; FAQ=4; FILES=4')
 if check:
  dest=Path(__file__).parent/'naz-preview'
  for rel,s in changes.items():p=dest/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
  print('CHECK_ONLY=PASS; LIVE_WRITES=NONE');return
 backup=Path('/var/backups/azmednsk')/('nazirov-orthopedics-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True)
 old={k:(root/k).read_bytes() if (root/k).exists() else None for k in changes}
 for rel,blob in old.items():
  if blob is not None:p=backup/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(blob)
 (backup/'new-files.json').write_text(json.dumps([k for k,v in old.items() if v is None]))
 written=[]
 try:
  for rel in sorted(changes,key=lambda k:old[k] is not None):
   p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix='.az-naz-',dir=p.parent)
   try:
    with os.fdopen(fd,'w') as f:f.write(changes[rel])
    os.chmod(tmp,0o644);os.replace(tmp,p);written.append(rel)
   finally:
    if os.path.exists(tmp):os.unlink(tmp)
  for rel,s in changes.items():assert (root/rel).read_text()==s
 except BaseException:
  for rel in reversed(written):
   if old[rel] is None:(root/rel).unlink()
   else:(root/rel).write_bytes(old[rel])
  raise
 print('NAZIROV_ORTHOPEDICS=PASS; TOPICS=8; FAQ=4; NEW_PAGES=1')
 print('BACKUP='+str(backup));print('URL='+URL)
if __name__=='__main__':main()
