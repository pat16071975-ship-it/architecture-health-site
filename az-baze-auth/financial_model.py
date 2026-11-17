"""Isolated annual planning model. Never reads or writes factual financial ledgers."""
import calendar
import copy
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from flask import abort, current_app, g, jsonify, render_template, request
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SCHEMA = '''
CREATE TABLE IF NOT EXISTS financial_model_plan (
 year INTEGER PRIMARY KEY, version INTEGER NOT NULL,
 config TEXT NOT NULL, results TEXT NOT NULL, stale TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS financial_model_history (
 id INTEGER PRIMARY KEY, year INTEGER NOT NULL, version INTEGER NOT NULL,
 actor_id INTEGER NOT NULL, changed_at TEXT NOT NULL, mode TEXT NOT NULL,
 before_json TEXT NOT NULL, after_json TEXT NOT NULL,
 UNIQUE(year,version)
);
'''
MONTHS = ['Янв','Фев','Мар','Апр','Май','Июн','Июл','Авг','Сен','Окт','Ноя','Дек']
# Label, maximum. Rates are stored as fractions; labels make this explicit in editor.
PARAMS = {
 'official_base_pct':('Официальный ФОТ без увеличения, % (пусто — неизвестно)',100),
 'official_step_pct':('Ежемесячное увеличение официального ФОТ, п.п.',100),
 'official_burden_pct':('Плановая ставка взносов с дополнительного ФОТ, % (включая травматизм; пусто — неизвестно)',100),
 'target':('Цель выручки, ₽',1e10), 'booking':('Заполнение графика записью, доля 0–1',1),
 'attendance':('Явка, доля 0–1',1), 'visit_minutes':('Средняя длительность приёма, мин',1440),
 'new_share':('Новые пациенты / визиты, доля 0–1',1), 'lead_conversion':('Лид → запись, доля 0–1',1),
 'lead_cost':('Стоимость лида, ₽',1e7), 'doctor_pay':('ФОТ врачей / выручка приёма, доля',1),
 'lab_pay':('ФОТ ЗТЛ / выручка приёма, доля',1), 'payroll_tax':('Доп. взносы сверх резерва, доля (пусто — учтены в резерве)',1),
 'medical_rate':('Медрасходники / выручка приёма, доля',1), 'lab_cost_rate':('Затраты ЗТЛ / выручка приёма, доля',1),
 'rent':('Аренда и коммунальные, ₽',1e9), 'it':('ИТ, программы и связь, ₽',1e9),
 'outsourcing':('Сторонние услуги, ₽',1e9), 'other_cost':('Прочие расходы, ₽',1e9),
 'reserve_rate':('Резерв / выручка, доля',1), 'other_revenue_rate':('Прочая выручка / выручка приёма, доля',1),
 'tax_rate':('Расчётная ставка налога модели, доля',1), 'minimum_tax':('Минимум налога / выручка, доля',1),
 'marketing_fixed':('Маркетинг фиксированный, ₽ (пусто — процент)',1e9),
 'marketing_rate':('Маркетинг / выручка при отсутствии фиксированной суммы, доля',1),
 'staff_hours':('Часы одной полной ставки в месяц',744),
 'search_months':('Срок поиска до выхода, месяцев (пусто — не задан)',24),
}
for group, label in [('admin','Администраторы'),('assist','Средний и младший персонал'),('management','Управление'),('marketing_pay','ФОТ маркетинга')]:
 for band, desc in [('low','выручка <12 млн'),('mid','12–18 млн'),('high','≥18 млн')]:
  PARAMS[f'{group}_{band}']=(f'{label}: {desc}, ₽',1e9)
ROOM_PARAMS={'daily_hours':('Часов работы в день',24),'closed_days':('Дней закрытия в месяце',31),
 'staffed_hours':('Часы действующего состава',10000),'absence_hours':('Отсутствие / отпуска, часов',10000),
 'rate':('Выручка за состоявшийся час, ₽',1e7),'weight':('Вес направления в распределении цели',1)}
NULLABLE={'payroll_tax','marketing_fixed','search_months','official_base_pct','official_burden_pct'}


def init_schema(conn):
 conn.executescript(SCHEMA)


def initial():
 return json.loads(Path(__file__).with_name('financial_model_seed.json').read_text())


def numeric(value, maximum, nullable=False):
 if value in ('',None) and nullable:return None
 if isinstance(value,bool):raise ValueError('Ожидается число.')
 try:n=float(str(value).replace(',','.'))
 except (ValueError,TypeError):raise ValueError('Введите число.') from None
 if not math.isfinite(n) or n<0 or n>maximum:raise ValueError(f'Допустимо число от 0 до {maximum:g}.')
 return n


def month_index(value):
 if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',str(value)):raise ValueError('Некорректный месяц.')
 y,m=map(int,value.split('-'));return y*12+m-1


def round_money(x):return round(x,2) if x is not None else None


def calculate(c):
 out=[];official_growth=0
 for m in range(12):
  p={k:v[m] for k,v in c['months'].items()};days=calendar.monthrange(c['year'],m+1)[1]
  warning=[];weight=sum(r['weight'][m] for r in c['rooms']);details=[];hire_details=[]
  if not weight:warning.append('Не заданы доли направлений для распределения цели.')
  productive=p['booking']*p['attendance'];required_target=p['target']/(1+p['other_revenue_rate'])
  for h in c['hires']:
   age=c['year']*12+m-month_index(h['start']);kind=h.get('kind','new')
   if kind not in ('new','expansion'):raise ValueError('Неизвестный тип изменения штата.')
   factor=(1 if kind=='expansion' else c['ramp'][min(age,len(c['ramp'])-1)]) if age>=0 else 0
   profile=h.get('schedule_profile',[1]) if kind=='expansion' else [1]
   schedule_fraction=profile[min(age,len(profile)-1)] if age>=0 else 0
   if h['count'] and not h['room']:warning.append(f"Не назначен кабинет: {h['name']}. Вклад не включён.")
   start=month_index(h['start']);search=p['search_months'];opened=None
   if search is not None:
    idx=start-int(search);opened=f'{idx//12:04d}-{idx%12+1:02d}'
   hire_details.append(dict(h,kind=kind,age=age,factor=factor,schedule_fraction=schedule_fraction,open=opened if kind=='new' else None,active=age>=0))
  for r in c['rooms']:
   if r['direction'] not in ('dentistry','structure'):raise ValueError('Неизвестное направление кабинета.')
   capacity=max(0,days-r['closed_days'][m])*r['daily_hours'][m]
   existing=max(0,r['staffed_hours'][m]-r['absence_hours'][m])
   hires=[h for h in hire_details if h['room']==r['id'] and h['active']]
   hired=sum(h['count']*p['staff_hours']*h['schedule_fraction'] for h in hires)
   effective=sum(h['count']*p['staff_hours']*h['schedule_fraction']*h['factor'] for h in hires)
   schedule=existing+hired
   if schedule>capacity+1e-6:warning.append(f"Кабинет {r['id']}: график превышает ёмкость на {schedule-capacity:.0f} ч.")
   # Full shifts occupy physical room time even while a new clinician is ramping up.
   existing_used=min(existing,capacity)
   hire_scale=min(1,max(0,capacity-existing_used)/hired) if hired else 0
   effective_schedule=existing_used+effective*hire_scale
   booked=effective_schedule*p['booking'];possible=booked*p['attendance']
   rate=r['rate'][m];target=required_target*r['weight'][m]/weight if weight else 0
   demand=target/rate if rate else None
   if rate==0 and target:warning.append(f"Кабинет {r['id']}: выручка за час равна нулю, достижимость цели не определена.")
   delivered=possible
   missing=max(0,demand-possible) if demand is not None else None
   fte=missing/(p['staff_hours']*productive) if missing is not None and p['staff_hours']*productive else None
   if demand is not None and demand>capacity*productive+1e-6:
    warning.append(f"Кабинет {r['id']}: цель выше доступной мощности при текущей записи и явке; одним наймом не закрыть.")
   details.append({'id':r['id'],'name':r['name'],'direction':r['direction'],'capacity':capacity,'staffed':min(capacity,schedule),
    'booked':booked,'possible':possible,'hours':delivered,'demand':demand,'missing':missing,'fte':fte,'revenue':delivered*rate,'load':delivered/capacity if capacity else None})
  revenue_clinic=sum(r['revenue'] for r in details);other=revenue_clinic*p['other_revenue_rate'];revenue=revenue_clinic+other
  capacity=sum(r['capacity'] for r in details);hours=sum(r['hours'] for r in details)
  wanted=sum(r['demand'] or 0 for r in details);visits=max(wanted,hours)*60/p['visit_minutes'] if p['visit_minutes'] else None
  new=visits*p['new_share'] if visits is not None else None
  leads=new/(p['lead_conversion']*p['attendance']) if new is not None and p['lead_conversion']*p['attendance'] else None
  needed_marketing=leads*p['lead_cost'] if leads is not None else None
  marketing=p['marketing_fixed'] if p['marketing_fixed'] is not None else revenue*p['marketing_rate']
  if needed_marketing is not None and marketing<needed_marketing:warning.append('Бюджет маркетинга ниже расчётной потребности в лидах.')
  band='low' if revenue<12000000 else ('mid' if revenue<18000000 else 'high')
  wage=revenue_clinic*(p['doctor_pay']+p['lab_pay'])+sum(p[f'{g}_{band}'] for g in ['admin','assist','management','marketing_pay'])
  # Owner-approved source convention: baseline contributions are budgeted inside reserve.
  payroll_tax=wage*p['payroll_tax'] if p['payroll_tax'] is not None else 0
  cost={'wage':wage,'payroll_tax':payroll_tax,'medical':revenue_clinic*p['medical_rate'],'lab':revenue_clinic*p['lab_cost_rate'],
   'rent':p['rent'],'marketing':marketing,'it':p['it'],'outsourcing':p['outsourcing'],'other_cost':p['other_cost'],'reserve':revenue*p['reserve_rate']}
  complete=all(v is not None for v in cost.values());known=sum(v for v in cost.values() if v is not None)
  profit_before=revenue-known if complete else None
  tax=max(max(profit_before,0)*p['tax_rate'],revenue*p['minimum_tax']) if complete else None
  profit=profit_before-tax if complete else None
  official_growth+=p['official_step_pct']
  base=p['official_base_pct'];burden=p['official_burden_pct']
  official_share=min(100,base+official_growth) if base is not None else None
  official_extra=wage*(official_share-base)/100 if base is not None else None
  extra_tax=official_extra*burden/100 if official_extra is not None and burden is not None else (0 if official_extra==0 else None)
  adjusted_before=profit_before-extra_tax if profit_before is not None and extra_tax is not None else None
  adjusted_tax=max(max(adjusted_before,0)*p['tax_rate'],revenue*p['minimum_tax']) if adjusted_before is not None else None
  adjusted_profit=adjusted_before-adjusted_tax if adjusted_before is not None else None
  if base is None:warning.append('Увеличение налогов: не задана исходная официальная доля ФОТ.')
  if burden is None:warning.append('Увеличение налогов: не задана плановая ставка взносов работодателя.')
  if not complete:warning.append('Не задана ставка взносов на ФОТ: полный расход и прибыль не рассчитаны.')
  out.append({'official_share':official_share/100 if official_share is not None else None,
   'official_base_wage':wage*base/100 if base is not None else None,'official_extra_wage':official_extra,
   'official_extra_tax':extra_tax,'official_adjusted_profit':adjusted_profit,
   'official_adjusted_margin':adjusted_profit/revenue if adjusted_profit is not None and revenue else None,
   'reserve_contribution_policy':1,'base_contributions_in_reserve':p['payroll_tax'] is None,
   'month':m+1,'target':p['target'],'revenue':revenue,'gap':revenue-p['target'],'dentistry':sum(r['revenue'] for r in details if r['direction']=='dentistry'),
   'structure':sum(r['revenue'] for r in details if r['direction']=='structure'),'other_revenue':other,
   'capacity':capacity,'staffed':sum(r['staffed'] for r in details),'booked':sum(r['booked'] for r in details),'hours':hours,
   'load':hours/capacity if capacity else None,'missing_fte':sum(r['fte'] or 0 for r in details),
   'starts':sum(h['count'] for h in hire_details if h['age']==0),'ramping':sum(h['count'] for h in hire_details if h['kind']=='new' and 0<=h['age']<len(c['ramp'])-1),
   'new':new,'leads':leads,'needed_marketing':needed_marketing,'marketing':marketing,**cost,'known_cost':known,'total_cost':known if complete else None,
   'profit_before':profit_before,'tax':tax,'profit':profit,'margin':profit/revenue if profit is not None and revenue else None,
   'hiring_context':{'year':c['year'],'lag':next((j for j,v in enumerate(c['ramp']) if v>=1),None),'search':p['search_months']},
   'rooms':details,'hires':hire_details,'warnings':warning})
 return out


def hiring_needs(results,config=None):
 # Recommendations must use the same saved calculation snapshot as the monthly table.
 rows=[]
 for room in results[0]['rooms']:
  prior=0
  for i,m in enumerate(results):
   r=next(x for x in m['rooms'] if x['id']==room['id'])
   n=math.ceil((r['fte'] or 0)-1e-9)
   if n>prior:
    context=m.get('hiring_context')
    if context is None:continue  # Never reconstruct a saved result with unsaved/new assumptions.
    lag=context['lag']
    idx=context['year']*12+i-(lag or 0)
    search=context['search']
    start=f'{idx//12:04d}-{idx%12+1:02d}' if lag is not None else 'Профиль не достигает полной загрузки'
    opening=None if search is None or lag is None else f'{(idx-int(search))//12:04d}-{(idx-int(search))%12+1:02d}'
    rows.append({'room':room['id'],'name':r['name'],'needed':m['month'],'count':n-prior,'start':start,'opening':opening})
    prior=n
 return rows


def read_state(conn):
 row=conn.execute('SELECT * FROM financial_model_plan WHERE year=2027').fetchone()
 if row:
  state={'version':row['version'],'config':json.loads(row['config']),'results':json.loads(row['results']),'stale':json.loads(row['stale'])}
  # Preserve persisted results and settings; GET only signals old calculation policy.
  outdated=[i+1 for i,m in enumerate(state['results']) if m.get('reserve_contribution_policy')!=1]
  state['stale']=sorted(set(state['stale'])|set(outdated));state['outdated_policy']=outdated
  return state
 c=initial();return {'version':0,'config':c,'results':calculate(c),'stale':[]}


def candidate(old, change, validate_ramp=True):
 if not isinstance(change,dict):raise ValueError('Некорректная правка.')
 if change.get('group')=='batch':
  edits=change.get('changes')
  if not isinstance(edits,list) or not 1<=len(edits)<=100:raise ValueError('Выберите от 1 до 100 изменений.')
  working=copy.deepcopy(old);affected=set();first=12
  for edit in edits:
   if not isinstance(edit,dict) or edit.get('group') not in ('months','room','hire','ramp'):raise ValueError('Некорректная группа изменения.')
   config,start,indices=candidate(working,edit,False)
   working['config']=config;affected.update(indices);first=min(first,start)
  if working['config']['ramp']!=sorted(working['config']['ramp']):raise ValueError('Загрузка адаптации не должна уменьшаться.')
  return working['config'],first,sorted(affected)
 c=copy.deepcopy(old['config']);start=int(change.get('from',0))
 if not 1<=start<=12:raise ValueError('Выберите месяц применения.')
 scope=change.get('scope')
 if scope not in ('month','future'):raise ValueError('Выберите период действия.')
 indices=range(start-1,12) if scope=='future' else [start-1]
 group=change.get('group');key=str(change.get('key',''));value=change.get('value')
 if group=='months':
  if key not in PARAMS:raise ValueError('Неизвестный параметр.')
  n=numeric(value,PARAMS[key][1],key in NULLABLE)
  if key=='search_months' and n is not None and n!=int(n):raise ValueError('Срок поиска — целое число месяцев.')
  for m in indices:c['months'][key][m]=n
  # A one-month step changes every later cumulative share, even if later step inputs stay unchanged.
  if key=='official_step_pct':indices=range(start-1,12)
 elif group=='room':
  if key not in ROOM_PARAMS:raise ValueError('Неизвестный параметр кабинета.')
  room=next((r for r in c['rooms'] if r['id']==str(change.get('id'))),None)
  if not room:raise ValueError('Кабинет не найден.')
  n=numeric(value,ROOM_PARAMS[key][1])
  for m in indices:
   if key=='closed_days' and (n!=int(n) or n>calendar.monthrange(c['year'],m+1)[1]):raise ValueError('Некорректное число закрытых дней.')
   room[key][m]=n
 elif group=='hire':
  h=next((r for r in c['hires'] if r['id']==str(change.get('id'))),None)
  if not h:raise ValueError('Строка найма не найдена.')
  if key=='start':
   idx=month_index(value)
   if not 2026*12<=idx<2029*12:raise ValueError('Дата выхода вне горизонта модели.')
   h[key]=value
  elif key=='room':
   if str(value) not in [r['id'] for r in c['rooms']]:raise ValueError('Выберите кабинет.')
   h[key]=str(value)
  elif key=='count':h[key]=numeric(value,20)
  else:raise ValueError('Неизвестный параметр найма.')
  # Annual hire entry: prohibit partial historical recomputation masquerading as full update.
  first=min(month_index(h['start']),month_index(next(r for r in old['config']['hires'] if r['id']==h['id'])['start']))-2027*12+1
  start=max(1,min(start,first));indices=range(start-1,12)
 elif group=='ramp':
  i=int(key)
  if i not in range(5):raise ValueError('Некорректный месяц адаптации.')
  c['ramp'][i]=numeric(value,1)
  if validate_ramp and c['ramp']!=sorted(c['ramp']):raise ValueError('Загрузка по месяцам адаптации не должна уменьшаться.')
  start=1;indices=range(12)
 else:raise ValueError('Неизвестная группа параметров.')
 return c,start,list(indices)


def transition(old,change,mode):
 if mode not in ('recalculate','keep'):raise ValueError('Выберите действие с зависимыми показателями.')
 c,start,indices=candidate(old,change);fresh=calculate(c);results=copy.deepcopy(old['results']);stale=set(old['stale'])
 # Candidate settings changed only selected periods (except explicit annual hire/ramp edits).
 if mode=='keep':stale.update(i+1 for i in indices)
 else:
  for i in indices:results[i]=fresh[i];stale.discard(i+1)
 return {'version':old['version']+1,'config':c,'results':results,'stale':sorted(stale)},fresh,start


def commit_change(conn,version,change,mode,actor):
 conn.execute('BEGIN IMMEDIATE')
 try:
  old=read_state(conn)
  if old['version']!=version:raise RuntimeError('Модель уже изменена. Обновите страницу и повторите просмотр.')
  if change.get('group')=='refresh':
   new=copy.deepcopy(old);new['results']=calculate(old['config']);new['stale']=[];new['version']+=1
  else:new,_,_=transition(old,change,mode)
  conn.execute('INSERT INTO financial_model_plan VALUES(2027,?,?,?,?) ON CONFLICT(year) DO UPDATE SET version=excluded.version,config=excluded.config,results=excluded.results,stale=excluded.stale',
   (new['version'],json.dumps(new['config'],ensure_ascii=False),json.dumps(new['results'],ensure_ascii=False),json.dumps(new['stale'])))
  conn.execute('INSERT INTO financial_model_history(year,version,actor_id,changed_at,mode,before_json,after_json) VALUES(2027,?,?,?,?,?,?)',
   (new['version'],actor,datetime.now(timezone.utc).isoformat(),mode,json.dumps(old,ensure_ascii=False),json.dumps(new,ensure_ascii=False)))
  conn.commit();return new
 except Exception:conn.rollback();raise


def register(app,db,permission_required,require_csrf,csrf_token,user_permissions):
 def edit_guard():
  if 'financial_model_edit' not in user_permissions(g.user):abort(403)
 def serializer():return URLSafeTimedSerializer(current_app.secret_key,salt='financial-model-preview-v1')
 @app.get('/reports/financial-model/')
 @permission_required('reports')
 def financial_model_page():
  state=read_state(db())
  return render_template('financial_model.html',needs=hiring_needs(state['results']),state=state,months=MONTHS,params=PARAMS,room_params=ROOM_PARAMS,
   can_edit='financial_model_edit' in user_permissions(g.user),csrf=csrf_token())
 @app.post('/reports/financial-model/preview')
 @permission_required('reports')
 def financial_model_preview():
  edit_guard();require_csrf()
  try:
   old=read_state(db());change=json.loads(request.form.get('change','{}'));mode=request.form.get('mode')
   if not isinstance(change,dict):raise ValueError('Некорректная правка.')
   if int(request.form.get('version',-1))!=old['version']:return jsonify(error='Модель изменена. Обновите страницу.'),409
   if change.get('group')=='refresh':
    if mode!='recalculate':raise ValueError('Обновление требует пересчёта.')
    new=copy.deepcopy(old);new['results']=calculate(old['config']);new['stale']=[];fresh=new['results'];start=1
   else:new,fresh,start=transition(old,change,mode)
   token=serializer().dumps({'version':old['version'],'change':change,'mode':mode,'actor':g.user['id']})
   return jsonify(token=token,from_month=start,stale=new['stale'],mode=mode,
    before=[{k:r[k] for k in ['revenue','total_cost','profit','official_extra_tax','official_adjusted_profit']} for r in old['results']],
    after=[{k:r[k] for k in ['revenue','total_cost','profit','official_extra_tax','official_adjusted_profit']} for r in new['results']],
    potential=[{k:r[k] for k in ['revenue','total_cost','profit','official_extra_tax','official_adjusted_profit']} for r in fresh])
  except (ValueError,TypeError,KeyError,IndexError):return jsonify(error='Проверьте значения и период изменения.'),400
 @app.post('/reports/financial-model/save')
 @permission_required('reports')
 def financial_model_save():
  edit_guard();require_csrf()
  try:
   payload=serializer().loads(request.form.get('token',''),max_age=1800)
   if payload['actor']!=g.user['id']:abort(403)
   state=commit_change(db(),payload['version'],payload['change'],payload['mode'],g.user['id'])
   return jsonify(version=state['version'])
  except (BadSignature,SignatureExpired,ValueError,TypeError,KeyError):return jsonify(error='Подтверждение недействительно. Повторите предварительный просмотр.'),400
  except RuntimeError as e:return jsonify(error=str(e)),409
 @app.get('/reports/financial-model/history')
 @permission_required('reports')
 def financial_model_history():
  rows=db().execute('SELECT version,actor_id,changed_at,mode,before_json,after_json FROM financial_model_history WHERE year=2027 ORDER BY version DESC LIMIT 100').fetchall()
  items=[]
  for row in rows:
   item={k:row[k] for k in ('version','actor_id','changed_at','mode')}
   before=json.loads(row['before_json']);after=json.loads(row['after_json']);changes=[]
   def walk(a,b,path=''):
    if isinstance(a,dict) and isinstance(b,dict):
     for k in b:walk(a.get(k),b[k],path+'/'+str(k))
    elif isinstance(a,list) and isinstance(b,list) and len(a)==len(b):
     for i,(x,y) in enumerate(zip(a,b)):walk(x,y,path+'/'+str(i+1))
    elif a!=b:changes.append({'field':path,'before':a,'after':b})
   walk(before['config'],after['config'])
   for ch in changes:
    parts=ch['field'].strip('/').split('/')
    if parts[0]=='months' and len(parts)==3:ch['field']=PARAMS.get(parts[1],(parts[1],))[0]+' · '+MONTHS[int(parts[2])-1]
    elif parts[0]=='rooms' and len(parts)==4:
     room=after['config']['rooms'][int(parts[1])-1]
     ch['field']='Каб. '+room['id']+' · '+ROOM_PARAMS.get(parts[2],(parts[2],))[0]+' · '+MONTHS[int(parts[3])-1]
    elif parts[0]=='hires' and len(parts)==3:
     h=after['config']['hires'][int(parts[1])-1]
     ch['field']=h['name']+' · '+{'start':'Выход','count':'Ставки','room':'Кабинет'}.get(parts[2],parts[2])
    elif parts[0]=='ramp':ch['field']='Адаптация · месяц '+parts[1]
   item['changes']=changes;items.append(item)
  return jsonify(history=items)
