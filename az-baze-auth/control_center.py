"""Owner-controlled oversight. Reads never prepare schema or rewrite business data."""
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from functools import wraps
from uuid import uuid4
from zoneinfo import ZoneInfo

from flask import abort, g, has_request_context, redirect, render_template, request, url_for

from medical_supplies import Conflict

LOCAL = ZoneInfo('Asia/Novosibirsk')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS control_grants (
 user_id INTEGER PRIMARY KEY REFERENCES users(id),
 can_review INTEGER NOT NULL CHECK(can_review IN (0,1))
);
CREATE TABLE IF NOT EXISTS ztl_event_context (
 history_id INTEGER PRIMARY KEY REFERENCES ztl_history(id),
 reason TEXT NOT NULL, request_token TEXT NOT NULL, labels TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS control_reviews (
 history_id INTEGER PRIMARY KEY REFERENCES ztl_history(id),
 status TEXT NOT NULL CHECK(status IN ('new','checked','question')),
 comment TEXT NOT NULL, reviewer_id INTEGER NOT NULL REFERENCES users(id),
 updated_at TEXT NOT NULL, version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS control_review_history (
 id INTEGER PRIMARY KEY, history_id INTEGER NOT NULL REFERENCES ztl_history(id),
 status TEXT NOT NULL, comment TEXT NOT NULL, reviewer_id INTEGER NOT NULL REFERENCES users(id),
 changed_at TEXT NOT NULL, version INTEGER NOT NULL,
 UNIQUE(history_id,version)
);
CREATE INDEX IF NOT EXISTS idx_ztl_history_date_actor ON ztl_history(changed_at,actor_id,id);
'''
STATUS = {'new':'Не проверено','checked':'Проверено','question':'Нужно разобраться'}
CATEGORIES = {'products':'Изделия и материалы','orders':'Заказы и исполнители',
              'receipts':'Приход','payments':'Оплаты','payroll':'ФОТ и затраты',
              'inventory':'Инвентаризация и списания'}
ENTITY_CATEGORY = {'ztl_names':'products','ztl_parties':'products','Номенклатура ЗТЛ':'products',
 'Технологическая карта':'products','Изделие ЗТЛ':'products','Заказ':'orders','Производство':'orders',
 'Распределение оплаты ЗТЛ':'orders','Приход ЗТЛ':'receipts','Оплата':'payments',
 'Затраты':'payroll','Сверка затрат ЗТЛ':'payroll','Инвентаризация':'inventory','Списание ЗТЛ':'inventory','Закупочная стоимость':'receipts'}
FIELD = {'name':'Название','ident_code':'Код IDENT','recipe':'Состав','qty':'Количество',
 'technician_fee':'Оплата работы за изделие','internal_price':'Внутренний прайс','external_price':'Внешний прайс',
 'unit':'Единица','kind':'Тип','number':'Номер заказа','invoice_date':'Дата счёта','created_date':'Дата создания',
 'scope':'Тип заказа','party_id':'Плательщик','patient':'Пациент','customer_id':'Заказчик','doctor_id':'Врач',
 'work_id':'Изделие','description':'Описание','amount':'Сумма','delay_days':'Отсрочка в днях',
 'pieces':'Количество изделий','completed_on':'Дата изготовления','technicians':'Техники',
 'entries':'Распределение','rules':'Правила распределения','total':'Общая сумма', 'value':'Стоимость',
 'supplier':'Поставщик','reference':'Номер документа','received_on':'Дата документа', 'lines':'Позиции',
 'confirmed':'Подтверждение','paid':'Оплата','posted':'Проведение','document_name':'Документ',
 'document_type':'Формат документа','document_hash':'Документ изменён','document':'Документ изменён',
 'paid_date':'Дата оплаты','method':'Способ оплаты','comment':'Комментарий','service':'Услуга',
 'service_total':'Оплата лечения','paid_patient':'Оплативший пациент','allocations':'Зачтено на заказы',
 'advance':'Аванс','reason':'Основание','void_reason':'Причина отмены','month':'Месяц',
 'employee_id':'Сотрудник','taxes':'Налоги','stage':'Состояние','counts':'Фактические остатки',
 'counts_first':'Первый пересчёт','attempt':'Номер проверки','person':'Ответственный',
 'explanation':'Объяснительная','accepted_on':'Дата принятия','positions':'Позиций',
 'mode':'Способ учёта','materials':'Материалы','other':'Прочие затраты','purchases':'Закупки',
 'deleted':'Удалено','archived':'Убрано из ассортимента','archived_on':'Дата снятия с ассортимента',
 'remaining':'Осталось распределить','assigned':'Распределено','complete':'Заполнение завершено','unit_cost':'Закупочная стоимость за единицу','receipt_id':'Документ прихода №','operation':'Действие','work':'Изделие','card':'Карта','prices':'Прайсы','voided':'Оплата отменена'}
VALUES = {'internal':'Внутренний','external':'Внешний','material':'Материал','asset':'Имущество',
 'other':'Прочее','work':'Изделие','employee':'Сотрудник','doctor':'Врач','customer':'Заказчик',
 'private':'Частный клиент','legal':'Юридическое лицо','bank':'Р/сч','card':'Карта','cash':'Наличные','qr':'QR-код',
 'count':'Ввод остатков','match':'Совпало','mismatch':'Нужен пересчёт','explain':'Нужна объяснительная',
 'review':'Объяснительная заполнена','accepted':'Принята','cancelled':'Отменена',
 'payroll':'ФОТ','lab_costs':'Затраты лаборатории','clinic_costs':'Общеклинические затраты',
 'included':'Включены в ручные затраты','additional':'Добавляются к ручным затратам','archive':'Убрать из ассортимента','restore':'Вернуть в ассортимент','delete':'Удалить'}
MONEY = {'technician_fee','internal_price','external_price','amount','value','total','taxes',
         'service_total','advance','materials','other','purchases','assigned','remaining'}
IGNORED = {'id','order_id','version','work_version','card_version','stamp','token','snapshot','needs_confirmation','explicit'}


def init_schema(conn):
    conn.executescript(SCHEMA)


def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def decoded(value):
    if isinstance(value,str) and value[:1] in ('[','{'):
        try:return json.loads(value)
        except ValueError:pass
    return value


STRUCTURED={'recipe','technicians','entries','counts','counts_first','lines','allocations','snapshot'}


def clean(value,field=''):
    if field in STRUCTURED:value=decoded(value)
    if isinstance(value,dict):return {k:clean(v,k) for k,v in value.items() if k not in IGNORED}
    if isinstance(value,list):return [clean(v) for v in value]
    return value


def normalized(entity,value):
    value=clean(value)
    if isinstance(value,dict):
        if entity=='Технологическая карта':value={k:v for k,v in value.items() if k!='work_id'}
        if entity=='Распределение оплаты ЗТЛ':value={k:value.get(k) for k in ('total','entries')}
        if entity=='Оплата' and 'void_reason' in value:value['voided']=bool(value['void_reason'])
    return value


def changed(before,after,entity=''):
    return normalized(entity,before)!=normalized(entity,after)


def needs_reason(entity,action,before,after):
    if not before or not changed(before,after,entity):return False
    if entity=='Закупочная стоимость':return False
    if entity=='Распределение оплаты ЗТЛ' and before.get('total') is None:return False
    if entity=='Производство':
        keys=('pieces','recipe','technician_fee','technicians')
        return any(clean(before.get(k),k)!=clean(after.get(k),k) for k in keys) or bool(
            before.get('completed_on') and before.get('completed_on')!=after.get('completed_on'))
    if entity=='Приход ЗТЛ':
        keys=('supplier','reference','received_on','document_hash')
        if any(before.get(k)!=after.get(k) for k in keys):return True
        old_lines=decoded(before.get('lines')) or []
        new_lines=decoded(after.get('lines')) or []
        if old_lines and all(r.get('item_id') for r in old_lines):return clean(old_lines)!=clean(new_lines)
        return bool(old_lines and ([ (r.get('qty'),r.get('value')) for r in old_lines ] != [ (r.get('qty'),r.get('value')) for r in new_lines ]))
    if entity=='Инвентаризация':
        return action=='explain' and bool(before.get('explanation')) and any(
            before.get(k)!=after.get(k) for k in ('person','explanation'))
    if entity=='Затраты' and ('подтверждения' in action or before.get('employee_id')==0 and before.get('kind')=='payroll'):
        return False
    if entity=='Сверка затрат ЗТЛ':return bool(before.get('mode') and before.get('mode')!=after.get('mode'))
    return True


def labels_for(conn,entity,iid,before,after):
    # Snapshot only labels used by this event, not an entire growing catalogue.
    names=set();items=set();parties=set()
    def collect(value,parent=''):
        if parent in STRUCTURED:value=decoded(value)
        if isinstance(value,list):
            if parent=='technicians':names.update(int(x) for x in value)
            else:
                for x in value:collect(x,parent)
        elif isinstance(value,dict):
            for key,v in value.items():
                if v is not None and key in ('work_id','doctor_id','customer_id','employee_id','technician_id'):names.add(int(v))
                elif v is not None and key=='party_id':parties.add(int(v))
                elif v is not None and (key=='item_id' or key=='id' and parent=='snapshot'):items.add(int(v))
                collect(v,key if key!='id' else parent)
    collect(before);collect(after)
    if entity in ('Технологическая карта','Изделие ЗТЛ','ztl_names'):names.add(iid)
    if entity in ('Номенклатура ЗТЛ','Закупочная стоимость','Списание ЗТЛ'):items.add(iid)
    if entity=='ztl_parties':parties.add(iid)
    order=None
    if entity in ('Заказ','Производство','Распределение оплаты ЗТЛ'):
        order=conn.execute('SELECT number,work_id FROM ztl_orders WHERE id=?',(iid,)).fetchone()
        if order:names.add(order['work_id'])
    labels={}
    for table,prefix,ids in [('ztl_names','name:',names),('ztl_items','item:',items),('ztl_parties','party:',parties)]:
        for ref in ids:
            row=conn.execute('SELECT * FROM '+table+' WHERE id=?',(ref,)).fetchone()
            if row:labels[prefix+str(ref)]=row['name']+(' ('+row['unit']+')' if table=='ztl_items' else '')
    if order:labels['title']=order['number']+' · '+labels.get('name:'+str(order['work_id']),'Изделие')
    elif entity in ('Технологическая карта','Изделие ЗТЛ','ztl_names'):
        labels['title']=labels.get('name:'+str(iid)) or (after or before or {}).get('name') or (before or {}).get('work',{}).get('name','Изделие')
    elif entity in ('Закупочная стоимость','Списание ЗТЛ'):labels['title']=labels.get('item:'+str(iid),'Материал №'+str(iid))
    elif entity=='Номенклатура ЗТЛ':labels['title']=(after or {}).get('name') or (before or {}).get('name') or 'Материал'
    elif entity=='Приход ЗТЛ':labels['title']=(after or {}).get('reference','')+' · '+(after or {}).get('supplier','')
    else:labels['title']=('Контрагент' if entity=='ztl_parties' else entity)+' №'+str(iid)
    if has_request_context() and getattr(g,'user',None):labels['actor']=g.user['full_name']
    return labels


def capture(conn,history_id,entity,iid,action,before,after):
    reason=''
    if has_request_context():
        required=needs_reason(entity,action,before,after)
        reason=(request.form.get('change_reason') or '').strip()
        # A saved explanation needs a separate edit reason, never its previous text
        # or an unrelated legacy `reason` field supplied by the caller.
        if not reason and not (required and entity=='Инвентаризация' and action=='explain'):
            reason=(request.form.get('reason') or '').strip()
        if required and not reason:
            raise ValueError('Укажите причину изменения сохранённых данных.')
        if len(reason)>4000:raise ValueError('Причина изменения: не более 4000 символов.')
        if not getattr(g,'ztl_change_token',None):g.ztl_change_token=uuid4().hex
        token=g.ztl_change_token
    else:token=uuid4().hex
    if not reason and isinstance(after,dict):reason=str(after.get('reason') or after.get('void_reason') or after.get('explanation') or '')
    conn.execute('INSERT INTO ztl_event_context VALUES(?,?,?,?)',
                 (history_id,reason,token,encode(labels_for(conn,entity,iid,before,after))))


def has_access(conn,user,is_owner,review=False):
    if not user:return False
    if is_owner(user):return True
    # Fail closed during a not-yet-prepared deployment; never create on GET.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='control_grants'").fetchone():return False
    row=conn.execute('SELECT can_review FROM control_grants WHERE user_id=?',(user['id'],)).fetchone()
    return bool(row and (not review or row['can_review']))


def protected_user(conn,user_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='control_grants'").fetchone():return False
    return bool(conn.execute('SELECT 1 FROM control_grants WHERE user_id=?',(user_id,)).fetchone())


def fmt(n,scale=100):
    if n is None:return 'Не задано'
    return format(Decimal(n)/scale,'f').rstrip('0').rstrip('.') if n%scale else str(n//scale)


def display_value(key,value,labels):
    if value is None:return 'Не задано'
    if key in ('document','document_hash'):return 'Есть документ' if value else 'Нет документа'
    if key=='unit_cost':return format(Decimal(str(value))/100,'.2f')+' ₽'
    if key in MONEY and isinstance(value,(int,float)):return fmt(value)+' ₽'
    if key=='qty' and isinstance(value,int):return fmt(value,1000)
    if key in ('work_id','doctor_id','customer_id','employee_id'):
        return labels.get('name:'+str(value),'Запись №'+str(value))
    if key=='party_id':return labels.get('party:'+str(value),'Плательщик №'+str(value))
    if key=='technicians':return ', '.join(labels.get('name:'+str(v),'Техник №'+str(v)) for v in (decoded(value) or [])) or 'Не указаны'
    if key in ('confirmed','paid','posted','deleted','archived','complete','voided') and isinstance(value,(bool,int)):
        return 'Да' if value else 'Нет'
    if isinstance(value,(dict,list)):return encode(value)
    return VALUES.get(str(value),str(value)) or 'Пусто'


def field_changes(before,after,labels,entity=''):
    before=normalized(entity,before) or {};after=normalized(entity,after) or {}
    result=[]
    for key in sorted(set(before)|set(after)):
        a,b=before.get(key),after.get(key)
        if a==b:continue
        if key=='recipe':
            if not a and not b:
                result.append(dict(field='Состав',before='Не задан' if a is None else 'Материалы не расходуются',after='Не задан' if b is None else 'Материалы не расходуются',delta=''))
            aa={r['item_id']:r['qty'] for r in (a or [])};bb={r['item_id']:r['qty'] for r in (b or [])}
            for iid in sorted(set(aa)|set(bb)):
                x,y=aa.get(iid),bb.get(iid)
                if x==y:continue
                delta=''
                if x is not None and y is not None:
                    delta=fmt(y-x,1000)
                    if x:delta+=' / '+format(Decimal(y-x)*100/Decimal(x),'.2f').rstrip('0').rstrip('.')+'%'
                    if y>x:delta='+'+delta
                result.append(dict(field='Расход на изделие · '+labels.get('item:'+str(iid),'Материал №'+str(iid)),
                                   before=fmt(x,1000) if x is not None else 'Не входит',after=fmt(y,1000) if y is not None else 'Удалён из состава',delta=delta))
        elif key in ('counts','counts_first') and isinstance(a or b,dict):
            for iid in sorted(set(a or {})|set(b or {})):
                x=(a or {}).get(iid);y=(b or {}).get(iid)
                if x!=y:result.append(dict(field=FIELD[key]+' · '+labels.get('item:'+iid,'Позиция №'+iid),before=fmt(x,1000),after=fmt(y,1000),delta=''))
        elif key in ('work','card','prices') and isinstance(a or b,dict):
            result.extend(field_changes(a,b,labels,'Технологическая карта' if key=='card' else ''))
        elif key=='lines':
            for index in range(max(len(a or []),len(b or []))):
                x=a[index] if a and index<len(a) else None;y=b[index] if b and index<len(b) else None
                if x==y:continue
                def line(r):
                    if not r:return 'Нет позиции'
                    return str(r.get('name','Позиция'))+' · '+fmt(r.get('qty'),1000)+' '+str(r.get('unit',''))+' · '+fmt(r.get('value'))+' ₽'
                result.append(dict(field='Строка прихода '+str(index+1),before=line(x),after=line(y),delta=''))
        elif key=='allocations':
            def allocations(v):return '; '.join('Заказ '+str(r.get('number',''))+': '+fmt(r['amount'])+' ₽' for r in v or []) or 'Нет зачёта'
            result.append(dict(field=FIELD[key],before=allocations(a),after=allocations(b),delta=''))
        elif key=='entries':
            aa={r['technician_id']:r for r in (a or [])};bb={r['technician_id']:r for r in (b or [])}
            def share(r):
                if not r:return 'Не задана'
                return fmt(r['value'])+('%' if r['mode']=='percent' else ' ₽')+' → '+fmt(r['amount'])+' ₽'
            for tid in sorted(set(aa)|set(bb)):
                if aa.get(tid)!=bb.get(tid):result.append(dict(field='Доля · '+labels.get('name:'+str(tid),'Техник №'+str(tid)),before=share(aa.get(tid)),after=share(bb.get(tid)),delta=''))
        else:
            delta=''
            if key in MONEY and isinstance(a,int) and isinstance(b,int):delta=('+' if b>a else '')+fmt(b-a)+' ₽'
            result.append(dict(field=FIELD.get(key,'Дополнительные данные'),before=display_value(key,a,labels),after=display_value(key,b,labels),delta=delta))
    return result


def period(args):
    today=datetime.now(LOCAL).date()
    start=date.fromisoformat(args.get('start') or today.replace(day=1).isoformat())
    end=date.fromisoformat(args.get('end') or today.isoformat())
    if end.year>=9999:raise ValueError('Недопустимая дата окончания.')
    if start>end:raise ValueError('Начало периода позже окончания.')
    return start,end


def events(conn,start,end,actor=None,entities=None,iid=None):
    low=datetime.combine(start,time.min,LOCAL).astimezone(timezone.utc).isoformat()
    high=datetime.combine(end+timedelta(days=1),time.min,LOCAL).astimezone(timezone.utc).isoformat()
    query='''SELECT h.*,u.full_name actor,c.reason,c.labels,c.request_token,r.status,r.comment,r.version review_version,
     r.updated_at,r.reviewer_id,ru.full_name reviewer FROM ztl_history h
     LEFT JOIN users u ON u.id=h.actor_id LEFT JOIN ztl_event_context c ON c.history_id=h.id
     LEFT JOIN control_reviews r ON r.history_id=h.id LEFT JOIN users ru ON ru.id=r.reviewer_id
     WHERE h.changed_at>=? AND h.changed_at<?'''
    params=[low,high]
    if actor is not None:query+=' AND h.actor_id=?';params.append(actor)
    if entities:
        query+=' AND h.entity IN ('+','.join('?' for _ in entities)+') AND h.entity_id=?';params.extend([*entities,iid])
    for row in conn.execute(query+' ORDER BY h.id DESC',params):
        r=dict(row);r['before']=json.loads(r['before_json']) if r['before_json'] else None;r['after']=json.loads(r['after_json'])
        # This historical operation stored only its cancellation reason, not a full
        # after snapshot. Reconstruct only this known patch from its own before data;
        # never consult today's record or rewrite the immutable source history.
        if (r['entity']=='Оплата' and r['action']=='Отмена ошибочной записи (не возврат денег)'
                and isinstance(r['before'],dict) and isinstance(r['after'],dict) and set(r['after'])=={'reason'}):
            r['after']={**r['before'],'void_reason':r['after']['reason']}
        r['meaningful']=changed(r['before'],r['after'],r['entity'])
        r['labels']=json.loads(r['labels']) if r['labels'] else {}
        r['title']=r['labels'].get('title') or ('Справочник' if r['entity'] in ('ztl_names','ztl_parties') else r['entity'])+' №'+str(r['entity_id'])
        r['local_time']=datetime.fromisoformat(r['changed_at']).astimezone(LOCAL).strftime('%d.%m.%Y %H:%M:%S')
        r['category']=ENTITY_CATEGORY.get(r['entity'],'products')
        r['status']=r['status'] or 'new';r['review_version']=r['review_version'] or 0
        r['diff']=field_changes(r['before'],r['after'],r['labels'],r['entity'])
        if r['entity']=='Инвентаризация' and r['action'] in ('count','accept'):
            counts=decoded((r['after'] or {}).get('counts')) or {}
            for item in decoded((r['after'] or {}).get('snapshot')) or []:
                actual=counts.get(str(item['id']))
                if actual is not None:r['diff'].append(dict(field='Сверка · '+item['name']+' ('+item['unit']+')',before='Расчёт: '+fmt(item['qty'],1000),after='Факт: '+fmt(actual,1000),delta=fmt(actual-item['qty'],1000)))
        r['reason_missing']=bool(r['request_token'] and not r['reason'] and needs_reason(r['entity'],r['action'],r['before'],r['after']))
        r['actor']=r['labels'].get('actor') or r['actor']
        r['action_label']={'count':'Сданы остатки','recheck':'Назначен пересчёт','explain':'Объяснительная','accept':'Принята','cancel':'Отменена'}.get(r['action'],r['action'])
        r['automatic']=r['entity']=='Закупочная стоимость'
        r['attention']=bool(needs_reason(r['entity'],r['action'],r['before'],r['after']) or
                            r['entity']=='Списание ЗТЛ' or r['entity']=='Инвентаризация' and (r['after'] or {}).get('stage') in ('mismatch','explain','review'))
        r['backdated']=any(str(v)[:10]<datetime.fromisoformat(r['changed_at']).astimezone(LOCAL).date().isoformat() for k,v in (r['after'] or {}).items() if k in ('invoice_date','received_on','paid_date','completed_on') and v)
        r['attention']=r['attention'] or r['backdated'] or bool(r['entity']=='Инвентаризация' and r['action']=='accept' and (r['before'] or {}).get('stage')=='review')
        r['dates']=[(FIELD[k],str(v)) for k,v in (r['after'] or {}).items() if k in ('invoice_date','received_on','paid_date','completed_on','month') and v]
        yield r


def metric(label,records):return dict(label=label,records=records,count=len(records))


def workload(conn,rows,actor):
    import ztl_control,ztl_labor
    meaningful=list(reversed([r for r in rows if r['meaningful'] and (actor is None or r['actor_id']==actor)]))
    def records(entity,action=None):
        unique={r['entity_id']:dict(id=r['entity_id'],name=r['title']) for r in meaningful if r['entity']==entity and (action is None or r['action']==action)}
        return list(unique.values())
    created=[r for r in meaningful if r['entity']=='ztl_names' and not r['before'] and (r['after'] or {}).get('kind')=='work']
    new_cards={r['entity_id']:dict(id=r['entity_id'],name=r['title']) for r in created}
    edits={r['entity_id']:dict(id=r['entity_id'],name=r['title']) for r in meaningful if r['entity']=='Технологическая карта' and r['before']}
    result={
      'created_products':metric('Создано изделий за период',list(new_cards.values())),
      'edited_products':metric('Изменено карт за период',list(edits.values())),
      'orders':metric('Создано заказов за период',records('Заказ','Создание')),
      'completed':metric('Внесена первая отметка изготовления',list({r['entity_id']:dict(id=r['entity_id'],name=r['title']) for r in meaningful if r['entity']=='Производство' and (r['after'] or {}).get('completed_on') and not (r['before'] or {}).get('completed_on')}.values())),
      'receipts':metric('Внесено документов прихода',list({r['entity_id']:dict(id=r['entity_id'],name=r['title']) for r in meaningful if r['entity']=='Приход ЗТЛ' and not r['before']}.values())),
      'posted':metric('Проведено документов прихода',records('Приход ЗТЛ','Проведение')),
      'payments':metric('Внесено оплат',records('Оплата','Создание')),
      'payroll':metric('Заполнено или изменено начислений ФОТ',list({r['entity_id']:dict(id=r['entity_id'],name='ФОТ '+str(r['after'].get('month',''))+' · '+r['labels'].get('name:'+str(r['after'].get('employee_id')),'Сотрудник')) for r in meaningful if r['entity']=='Затраты' and (r['after'] or {}).get('kind')=='payroll' and r['after'].get('employee_id')}.values())),
      'inventory':metric('Сдано пересчётов',list({(r['entity_id'],r['after'].get('attempt')):dict(id=r['entity_id'],name=r['title']+' · проверка '+str(r['after'].get('attempt'))) for r in meaningful if r['entity']=='Инвентаризация' and r['action']=='count'}.values())),
    }
    touched_products={r['entity_id'] for r in meaningful if r['entity'] in ('Технологическая карта','Изделие ЗТЛ','ztl_names')}
    touched_orders={r['entity_id'] for r in meaningful if r['entity'] in ('Заказ','Производство','Распределение оплаты ЗТЛ')}
    touched_receipts={r['entity_id'] for r in meaningful if r['entity']=='Приход ЗТЛ'}
    cards=[r for r in ztl_control.catalog(conn) if not r['archived'] and (actor is None or r['id'] in touched_products)]
    short=lambda c:dict(id=c['id'],name=c['name'])
    checks={'missing_card':('Нет технологической карты',lambda c:not c['version']),
      'no_materials':('Явно указано без расхода материалов',lambda c:bool(c['version']) and not c['parts']),
      'missing_fee':('Не задана оплата работы',lambda c:c['technician_fee'] is None),
      'zero_fee':('Явно нулевая оплата работы',lambda c:c['technician_fee']==0),
      'no_price':('Не задан ни один прайс',lambda c:c['internal_price'] is None and c['external_price'] is None),
      'missing_cost':('Неизвестна закупочная стоимость',lambda c:bool(c['version']) and c['material_cost'] is None),
      'ready':('Карты заполнены для нового заказа',lambda c:bool(c['version']) and c['technician_fee'] is not None and c['material_cost'] is not None and (c['internal_price'] is not None or c['external_price'] is not None))}
    quality={key:metric(label,[short(c) for c in cards if test(c)]) for key,(label,test) in checks.items()}
    pending=[];no_tech=[]
    for o in conn.execute('SELECT id,number FROM ztl_orders ORDER BY id'):
        if actor is not None and o['id'] not in touched_orders:continue
        prod=ztl_control.production(conn,o['id'])
        if not prod or not json.loads(prod['technicians']):no_tech.append(dict(id=o['id'],name=o['number']))
        if prod and not ztl_labor.allocation(conn,prod)['complete']:pending.append(dict(id=o['id'],name=o['number']))
    quality['no_technicians']=metric('Заказы без исполнителей',no_tech)
    quality['unfinished_labor']=metric('Распределение оплаты не завершено',pending)
    quality['draft_receipts']=metric('Непроведённые документы прихода',[dict(id=r['id'],name=r['supplier']+' · '+r['reference']) for r in conn.execute('SELECT * FROM ztl_receipts WHERE posted=0') if actor is None or r['id'] in touched_receipts])
    quality['explanations']=metric('Ожидаются объяснительные',[dict(id=r['id'],name='Инвентаризация №'+str(r['id'])) for r in conn.execute("SELECT * FROM ztl_inventories WHERE stage='explain'") if actor is None or any(e['entity']=='Инвентаризация' and e['entity_id']==r['id'] for e in meaningful)])
    touched_months={r['after'].get('month') for r in meaningful if r['entity']=='Затраты' and r['after']}
    months=sorted({r['month'] for r in conn.execute('SELECT DISTINCT month FROM ztl_costs')} |
                  {r['m'] for r in conn.execute("SELECT DISTINCT substr(completed_on,1,7) m FROM ztl_production WHERE completed_on IS NOT NULL AND completed_on!=''")})
    quality['unconfirmed_payroll']=metric('Месяцы ФОТ без подтверждения полноты',[
        dict(id=m,name=m) for m in months if (actor is None or m in touched_months) and not conn.execute(
            "SELECT 1 FROM ztl_costs WHERE month=? AND kind='payroll' AND employee_id=0",(m,)).fetchone()])
    result['confirmed_payroll']=metric('Подтверждено месяцев ФОТ',list({r['after']['month']:dict(id=r['after']['month'],name=r['after']['month']) for r in meaningful if r['entity']=='Затраты' and r['after'].get('kind')=='payroll' and r['after'].get('employee_id')==0}.values()))
    result['edited_orders']=metric('Изменено заказов за период',list({r['entity_id']:dict(id=r['entity_id'],name=r['title']) for r in meaningful if r['entity'] in ('Заказ','Производство','Распределение оплаты ЗТЛ') and r['before'] and (r['entity']!='Распределение оплаты ЗТЛ' or r['before'].get('total') is not None)}.values()))
    groups={'created_products':'product','edited_products':'product','orders':'order','edited_orders':'order','completed':'order','receipts':'receipt','posted':'receipt','payments':'payment','payroll':'cost','inventory':'inventory','no_technicians':'order','unfinished_labor':'order','draft_receipts':'receipt','explanations':'inventory','unconfirmed_payroll':'payroll','confirmed_payroll':'payroll'}
    for key,m in (result|quality).items():
        for record in m['records']:record['kind']=groups.get(key,'product')
    return result,quality


def grant_stamp(conn):
    return hashlib.sha256(encode([tuple(r) for r in conn.execute('SELECT * FROM control_grants ORDER BY user_id')]).encode()).hexdigest()


def save_grant(conn,data,actor):
    uid=int(data.get('user_id',''));mode=data.get('mode')
    if mode not in ('none','view','review'):raise ValueError('Выберите уровень доступа.')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if data.get('stamp')!=grant_stamp(conn):raise Conflict('Доступы изменились. Обновите страницу.')
        if not conn.execute('SELECT 1 FROM users WHERE id=? AND active=1',(uid,)).fetchone():raise ValueError('Выберите действующего пользователя.')
        before=conn.execute('SELECT * FROM control_grants WHERE user_id=?',(uid,)).fetchone()
        if mode=='none':conn.execute('DELETE FROM control_grants WHERE user_id=?',(uid,))
        else:conn.execute('INSERT INTO control_grants VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET can_review=excluded.can_review',(uid,int(mode=='review')))
        conn.execute('INSERT INTO audit_log(actor_user_id,action,target_user_id,details,created_at) VALUES(?,?,?,?,?)',
          (actor,'control_access',uid,encode(dict(before=dict(before) if before else None,mode=mode)),datetime.now(timezone.utc).isoformat()))


def save_review(conn,data,actor):
    hid=int(data.get('history_id',''));ver=int(data.get('version',''));status=data.get('status')
    comment=str(data.get('comment','')).strip()
    if status not in STATUS:raise ValueError('Выберите состояние проверки.')
    if len(comment)>4000 or status=='question' and not comment:raise ValueError('Укажите комментарий до 4000 символов.')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM ztl_history WHERE id=?',(hid,)).fetchone():raise ValueError('Событие не найдено.')
        old=conn.execute('SELECT * FROM control_reviews WHERE history_id=?',(hid,)).fetchone()
        if ver!=(old['version'] if old else 0):raise Conflict('Проверка уже изменена. Обновите страницу.')
        now=datetime.now(timezone.utc).isoformat()
        conn.execute('INSERT INTO control_reviews VALUES(?,?,?,?,?,?) ON CONFLICT(history_id) DO UPDATE SET status=excluded.status,comment=excluded.comment,reviewer_id=excluded.reviewer_id,updated_at=excluded.updated_at,version=excluded.version',
                     (hid,status,comment,actor,now,ver+1))
        conn.execute('INSERT INTO control_review_history(history_id,status,comment,reviewer_id,changed_at,version) VALUES(?,?,?,?,?,?)',(hid,status,comment,actor,now,ver+1))


def register(app,db,is_owner,login_required,require_csrf,csrf_token):
    def required(review=False):
        def decorator(fn):
            @wraps(fn)
            @login_required
            def wrapped(*a,**kw):
                if not has_access(db(),g.user,is_owner,review):abort(403)
                return fn(*a,**kw)
            return wrapped
        return decorator

    @app.get('/control/')
    @required()
    def control_home():
        return render_template('control_center.html',view='home',owner=is_owner(g.user))

    @app.get('/control/ztl/')
    @required()
    def control_ztl():
        try:
            start,end=period(request.args)
            actor=int(request.args['actor']) if request.args.get('actor') else None
            page=max(1,int(request.args.get('page','1')))
        except (ValueError,OverflowError):return 'Укажите корректный период и фильтры.',400
        view=request.args.get('view','events')
        if view not in ('events','work'):abort(404)
        conn=db();rows=list(events(conn,start,end,actor));category=request.args.get('category','');status=request.args.get('status','')
        if category and category not in CATEGORIES or status and status not in STATUS:abort(400)
        eid=request.args.get('event','');search=request.args.get('q','').strip().casefold()
        selected=[r for r in rows if r['meaningful'] and (not category or r['category']==category) and (not status or r['status']==status) and
          (not request.args.get('attention') or r['attention']) and (not eid or str(r['id'])==eid) and
          (not search or search in (r['title']+' '+r['entity']+' '+r['action']+' '+encode(r['diff'])).casefold())]
        work,quality=workload(conn,rows,actor) if view=='work' else ({},{})
        metric_key=request.args.get('metric','');chosen=(work|quality).get(metric_key)
        if metric_key and chosen is None:abort(400)
        shown=selected[(page-1)*50:page*50]
        for r in shown:
            r['reviews']=[dict(x) for x in conn.execute('SELECT h.*,u.full_name reviewer FROM control_review_history h LEFT JOIN users u ON u.id=h.reviewer_id WHERE history_id=? ORDER BY version',(r['id'],))]
            for review in r['reviews']:review['local_time']=datetime.fromisoformat(review['changed_at']).astimezone(LOCAL).strftime('%d.%m.%Y %H:%M:%S')
        return render_template('control_center.html',view=view,start=start.isoformat(),end=end.isoformat(),actor=actor,
          users=conn.execute('SELECT id,full_name FROM users ORDER BY full_name').fetchall(),events=shown,total=len(selected),page=page,
          categories=CATEGORIES,statuses=STATUS,category=category,status=status,query=request.args.get('q',''),attention=bool(request.args.get('attention')),
          can_review=has_access(conn,g.user,is_owner,True),owner=is_owner(g.user),csrf=csrf_token(),
          work=work,quality=quality,metric=chosen,metric_key=metric_key,records=chosen['records'][(page-1)*50:page*50] if chosen else [],
          page_url=lambda n:url_for('control_ztl',**{**request.args.to_dict(),'page':n}),
          record_url=lambda kind,iid:url_for('control_record',kind=kind,iid=iid),
          metric_url=lambda key:url_for('control_ztl',view='work',start=start,end=end,actor=actor,metric=key),
          tab_url=lambda view:url_for('control_ztl',view=view,start=start,end=end,actor=actor),
          new_count=sum(r['status']=='new' for r in rows if r['meaningful']),question_count=sum(r['status']=='question' for r in rows if r['meaningful']))

    @app.get('/control/ztl/record/<kind>/<iid>')
    @required()
    def control_record(kind,iid):
        conn=db();current=None
        if kind=='payroll':
            import re
            if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',iid):abort(404)
            costs=[dict(r) for r in conn.execute("SELECT * FROM ztl_costs WHERE month=? AND kind='payroll'",(iid,))]
            rows=[r for r in events(conn,date(1970,1,1),datetime.now(LOCAL).date()) if r['entity']=='Затраты' and (r['after'] or r['before'] or {}).get('month')==iid]
            current={'month':iid,'entries':costs}
            fields=[]
            for c in costs:
                if c['employee_id']==0:continue
                n=conn.execute('SELECT name FROM ztl_names WHERE id=?',(c['employee_id'],)).fetchone()
                fields.append(dict(field=n['name'] if n else 'Сотрудник',after=fmt(c['amount'])+' ₽ + налоги '+fmt(c['taxes'])+' ₽'))
            fields.append(dict(field='Полнота ФОТ',after='Подтверждена' if any(c['employee_id']==0 for c in costs) else 'Не подтверждена'))
            title='ФОТ · '+iid
        else:
            mapping={'product':('ztl_names',('ztl_names','Технологическая карта','Изделие ЗТЛ')),
              'order':('ztl_orders',('Заказ','Производство','Распределение оплаты ЗТЛ')),
              'receipt':('ztl_receipts',('Приход ЗТЛ',)), 'payment':('ztl_payments',('Оплата',)),
              'cost':('ztl_costs',('Затраты',)), 'inventory':('ztl_inventories',('Инвентаризация',))}
            if kind not in mapping or not iid.isdigit():abort(404)
            table,entities=mapping[kind];row=conn.execute('SELECT * FROM '+table+' WHERE id=?',(int(iid),)).fetchone()
            current=dict(row) if row else None
            if kind=='product' and current and current['kind']!='work':abort(404)
            if current and kind=='product':
                card=conn.execute('SELECT * FROM ztl_cards WHERE work_id=?',(iid,)).fetchone()
                prices=conn.execute('SELECT * FROM ztl_product_prices WHERE work_id=?',(iid,)).fetchone()
                code=conn.execute('SELECT ident_code FROM ztl_product_codes WHERE work_id=?',(iid,)).fetchone()
                current.update(dict(card) if card else {'recipe':None,'technician_fee':None})
                current.update(dict(prices) if prices else {'internal_price':None,'external_price':None})
                current['ident_code']=code['ident_code'] if code else ''
                current['archived']=bool(conn.execute('SELECT 1 FROM ztl_product_archive WHERE work_id=?',(iid,)).fetchone())
            if current and kind=='order':
                import ztl_control,ztl_labor
                prod=ztl_control.production(conn,int(iid))
                if prod:current.update(prod);current['entries']=ztl_labor.allocation(conn,prod)['entries']
            if current:current.pop('document',None)
            rows=list(events(conn,date(1970,1,1),datetime.now(LOCAL).date(),entities=entities,iid=int(iid)))
            if not current and not rows:abort(404)
            labels=labels_for(conn,entities[0],int(iid),None,current or {})
            fields=field_changes(None,current,labels,'Технологическая карта' if kind=='product' else '') if current else []
            title=labels.get('title','Запись №'+iid)
        try:page=max(1,int(request.args.get('page','1')))
        except ValueError:abort(400)
        return render_template('control_center.html',view='record',owner=is_owner(g.user),title=title,current=current,fields=fields,
            events=rows[(page-1)*50:page*50],total=len(rows),page=page,statuses=STATUS,can_review=False,
            page_url=lambda n:url_for('control_record',kind=kind,iid=iid,page=n))

    @app.post('/control/ztl/review')
    @required(True)
    def control_review():
        require_csrf()
        try:save_review(db(),request.form,g.user['id'])
        except Conflict as e:return str(e),409
        except (ValueError,OverflowError) as e:return str(e),400
        return redirect(url_for('control_ztl',start=request.form.get('start'),end=request.form.get('end'),actor=request.form.get('actor')),303)

    @app.route('/control/access/',methods=['GET','POST'])
    @login_required
    def control_access():
        if not is_owner(g.user):abort(403)
        if request.method=='POST':
            require_csrf()
            try:save_grant(db(),request.form,g.user['id'])
            except Conflict as e:return str(e),409
            except (ValueError,OverflowError) as e:return str(e),400
            return redirect(url_for('control_access'),303)
        return render_template('control_center.html',view='access',owner=True,csrf=csrf_token(),stamp=grant_stamp(db()),
          owner_id=g.user['id'],
          users=db().execute('SELECT u.id,u.full_name,u.email,c.can_review FROM users u LEFT JOIN control_grants c ON c.user_id=u.id WHERE u.active=1 ORDER BY u.full_name').fetchall())
