"""ZTL stock and production controls. All mutations are explicit, audited transactions."""
import hashlib
import io
import json
import sqlite3
import xml.etree.ElementTree as ET
from decimal import Decimal, ROUND_HALF_UP

from flask import abort, g, redirect, render_template, request, send_file, url_for
from medical_supplies import number, integer, display, text, Conflict

SCHEMA = """
CREATE TABLE IF NOT EXISTS ztl_items (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, unit TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('material','asset','other')), version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS ztl_cards (
 work_id INTEGER PRIMARY KEY REFERENCES ztl_names(id), recipe TEXT NOT NULL,
 technician_fee INTEGER NOT NULL CHECK(technician_fee>=0), version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS ztl_receipts (
 id INTEGER PRIMARY KEY, supplier TEXT NOT NULL, reference TEXT NOT NULL, received_on TEXT NOT NULL,
 lines TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0, paid INTEGER NOT NULL DEFAULT 0,
 posted INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
 document BLOB, document_name TEXT, document_type TEXT, document_hash TEXT UNIQUE,
 UNIQUE(supplier,reference,received_on)
);
CREATE TABLE IF NOT EXISTS ztl_moves (
 id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES ztl_items(id), on_date TEXT NOT NULL,
 qty INTEGER NOT NULL, value INTEGER NOT NULL, kind TEXT NOT NULL,
 ref_id INTEGER NOT NULL, reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ztl_moves_item ON ztl_moves(item_id,id);
CREATE TABLE IF NOT EXISTS ztl_production (
 order_id INTEGER PRIMARY KEY REFERENCES ztl_orders(id), pieces INTEGER NOT NULL CHECK(pieces>0),
 recipe TEXT NOT NULL, card_version INTEGER NOT NULL, technician_fee INTEGER NOT NULL,
 completed_on TEXT, technicians TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ztl_inventories (
 id INTEGER PRIMARY KEY, snapshot TEXT NOT NULL, stamp INTEGER NOT NULL,
 stage TEXT NOT NULL, counts TEXT, counts_first TEXT, attempt INTEGER NOT NULL DEFAULT 1,
 person TEXT NOT NULL DEFAULT 'Казанцева', explanation TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1, accepted_on TEXT
);
CREATE TABLE IF NOT EXISTS ztl_cost_reconciliation (
 month TEXT PRIMARY KEY, mode TEXT NOT NULL CHECK(mode IN ('included','additional')),
 stamp TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);
"""
RIGHTS = ('receipts_view','receipts_edit','inventory_count','inventory_manage')
TABS = [('orders','Учёт заказов'),('products','Реестр изделий'),('receipts','Приход'),
        ('payments','Реестр оплат'),('inventory','Инвентаризация'),('payroll','ФОТ'),('economics','Экономика')]


def init_schema(conn):
    conn.executescript(SCHEMA)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def rounded(value):
    return int(Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def stock(conn):
    result = []
    for r in conn.execute('SELECT * FROM ztl_items ORDER BY name,id'):
        item = dict(r)
        qty, value = conn.execute('SELECT COALESCE(SUM(qty),0),COALESCE(SUM(value),0) FROM ztl_moves WHERE item_id=?', (r['id'],)).fetchone()
        item.update(qty=qty, value=value)
        last = conn.execute("SELECT qty,value FROM ztl_moves WHERE item_id=? AND kind='receipt' ORDER BY id DESC LIMIT 1", (r['id'],)).fetchone()
        item['unit_cost'] = Decimal(value) * 1000 / qty if qty else (Decimal(last['value']) * 1000 / last['qty'] if last else None)
        item['received'] = conn.execute("SELECT COALESCE(SUM(qty),0) FROM ztl_moves WHERE item_id=? AND kind='receipt'", (r['id'],)).fetchone()[0]
        item['used'] = -conn.execute("SELECT COALESCE(SUM(qty),0) FROM ztl_moves WHERE item_id=? AND kind IN ('order','order_reverse')", (r['id'],)).fetchone()[0]
        result.append(item)
    return result


def stock_stamp(conn):
    return conn.execute('SELECT COALESCE(MAX(id),0) FROM ztl_moves').fetchone()[0]


def move(conn, item_id, qty, value, kind, ref_id, reason, on_date):
    # Posting time controls moving average. Document dates are separate metadata.
    conn.execute('INSERT INTO ztl_moves(item_id,on_date,qty,value,kind,ref_id,reason) VALUES(?,?,?,?,?,?,?)',
                 (item_id,on_date,qty,value,kind,ref_id,reason))


def consume(conn, item_id, qty, kind, ref_id, reason, on_date):
    item = next((r for r in stock(conn) if r['id'] == item_id), None)
    if not item or qty > item['qty']:
        raise ValueError('Недостаточно остатка: ' + (item['name'] if item else str(item_id)) + '. Сначала внесите приход.')
    value = item['value'] if qty == item['qty'] else rounded(Decimal(item['value']) * qty / item['qty'])
    move(conn,item_id,-qty,-value,kind,ref_id,reason,on_date)
    return value


def catalog(conn):
    items = {r['id']:r for r in stock(conn)}
    result = []
    for row in conn.execute("SELECT n.id,n.name,c.recipe,c.technician_fee,c.version FROM ztl_names n LEFT JOIN ztl_cards c ON c.work_id=n.id WHERE n.kind='work' ORDER BY n.name,n.id"):
        r = dict(row); r['parts'] = []; cost = Decimal(0)
        if r['recipe'] is None:
            r.update(material_cost=None,total_cost=None);result.append(r);continue
        for part in json.loads(r['recipe']):
            item = items[part['item_id']]
            value = item['unit_cost'] * part['qty'] / 1000 if item['unit_cost'] is not None else None
            r['parts'].append({**part, 'name':item['name'],'unit':item['unit'],'cost':rounded(value) if value is not None else None})
            cost = cost + value if cost is not None and value is not None else None
        r['material_cost'] = rounded(cost) if cost is not None else None
        r['total_cost'] = r['material_cost'] + r['technician_fee'] if cost is not None else None
        result.append(r)
    return result


def save_item(conn,data,actor):
    import ztl
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        kind=data.get('kind');unit=text(data.get('unit'),30)
        if kind not in ('material','asset','other'):raise ValueError('Выберите тип позиции.')
        iid=conn.execute('INSERT INTO ztl_items(name,unit,kind) VALUES(?,?,?)',(text(data.get('name'),200),unit,kind)).lastrowid
        ztl.log(conn,'Номенклатура ЗТЛ',iid,'Создание',actor,None,ztl.row_by_id(conn,'ztl_items',iid))
    return iid


def save_card(conn,data,actor):
    import ztl
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        wid=integer(data['work_id']) if data.get('work_id') else ztl.catalog_entry(conn,'work',data.get('name'),actor)
        work=ztl.row_by_id(conn,'ztl_names',wid)
        if work['kind']!='work':raise ValueError('Выберите изделие.')
        old=conn.execute('SELECT * FROM ztl_cards WHERE work_id=?',(wid,)).fetchone()
        if integer(data.get('version','0')) != (old['version'] if old else 0):raise Conflict('Карта изменена. Откройте её заново.')
        parts=[]
        for item in conn.execute("SELECT * FROM ztl_items WHERE kind='material'"):
            value=data.get('qty_'+str(item['id']),'')
            if str(value).strip():
                qty=number(value)
                if qty:parts.append(dict(item_id=item['id'],qty=qty))
        if not parts and data.get('no_materials')!='1':raise ValueError('Заполните нормы материалов или явно подтвердите отсутствие расхода.')
        fee=number(data.get('technician_fee'),100)
        conn.execute('INSERT INTO ztl_cards(work_id,recipe,technician_fee) VALUES(?,?,?) ON CONFLICT(work_id) DO UPDATE SET recipe=excluded.recipe,technician_fee=excluded.technician_fee,version=ztl_cards.version+1',(wid,encode(parts),fee))
        ztl.log(conn,'Технологическая карта',wid,'Сохранение',actor,dict(old) if old else None,dict(recipe=parts,technician_fee=fee))


def production(conn,order_id):
    row=conn.execute('SELECT * FROM ztl_production WHERE order_id=?',(order_id,)).fetchone()
    return dict(row) if row else None


def save_production(conn,order_id,old_order,values,data,actor):
    """Called inside save_order's transaction. Old orders are never backfilled on GET."""
    import ztl
    old=production(conn,order_id)
    raw=data.getlist('technician_id') if hasattr(data,'getlist') else data.get('technician_id',[])
    if isinstance(raw,str):raw=[raw]
    technicians=sorted(set(integer(v) for v in raw if str(v).strip()))
    if old and 'technician_id' not in data:technicians=json.loads(old['technicians'])
    if not technicians:
        if old_order and not old and 'technician_id' not in data:return
        raise ValueError('Выберите хотя бы одного техника.')
    for tid in technicians:
        if ztl.row_by_id(conn,'ztl_names',tid)['kind']!='employee':raise ValueError('Исполнитель должен быть сотрудником ЗТЛ.')
    pieces=integer(data.get('pieces',old['pieces'] if old else '1'))
    if not 1<=pieces<=10000:raise ValueError('Количество изделий: от 1 до 10000.')
    legacy=bool(old_order and not old)
    same=old and old_order['work_id']==values['work_id']
    if same:
        recipe=json.loads(old['recipe']);fee=old['technician_fee'];cv=old['card_version']
    elif legacy:
        recipe=[];fee=0;cv=0
    else:
        card=conn.execute('SELECT * FROM ztl_cards WHERE work_id=?',(values['work_id'],)).fetchone()
        if not card:raise ValueError('Сначала заполните технологическую карту изделия в реестре.')
        recipe=json.loads(card['recipe']);fee=card['technician_fee'];cv=card['version']
    completed=ztl.day(data['completed_on']) if data.get('completed_on') else (old['completed_on'] if old and 'completed_on' not in data else None)
    if completed and (completed>ztl.today() or completed<values['invoice_date']):raise ValueError('Дата изготовления должна быть между датой счёта и сегодняшним днём.')
    if old and old['completed_on'] and (not same or pieces!=old['pieces']):raise ValueError('У изготовленного заказа нельзя менять изделие или количество. Исправление согласуйте отдельно.')
    needs_issue=not legacy and (not old or not same or pieces!=old['pieces'])
    if needs_issue:
        if old:
            balances=conn.execute("SELECT item_id,SUM(qty) qty,SUM(value) value FROM ztl_moves WHERE kind IN ('order','order_reverse') AND ref_id=? GROUP BY item_id",(order_id,)).fetchall()
            for b in balances:
                if b['qty']:move(conn,b['item_id'],-b['qty'],-b['value'],'order_reverse',order_id,'Корректировка заказа',ztl.today())
        for part in recipe:
            consume(conn,part['item_id'],part['qty']*pieces,'order',order_id,'Создание / изменение заказа',ztl.today())
    conn.execute('INSERT INTO ztl_production(order_id,pieces,recipe,card_version,technician_fee,completed_on,technicians) VALUES(?,?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET pieces=excluded.pieces,recipe=excluded.recipe,card_version=excluded.card_version,technician_fee=excluded.technician_fee,completed_on=excluded.completed_on,technicians=excluded.technicians',
                 (order_id,pieces,encode(recipe),cv,fee,completed,encode(technicians)))
    ztl.log(conn,'Производство',order_id,'Сохранение',actor,old,production(conn,order_id))


def upload(file):
    """Recognize supported structured UPD XML; unstructured documents require line entry."""
    if not file or not file.filename:return (None,None,None,None),[]
    raw=file.read(5*1024*1024+1)
    if not raw or len(raw)>5*1024*1024:raise ValueError('Документ должен быть не больше 5 МБ.')
    name=file.filename.replace('\\','/').split('/')[-1][:180];lines=[]
    if raw.startswith(b'%PDF-'):mime='application/pdf'
    elif raw.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
    elif raw.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
    elif name.lower().endswith('.xml'):
        mime='application/xml'
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('XML с DTD/ENTITY не поддерживается.')
        try:
            root=ET.fromstring(raw)
            for el in root.iter():
                if el.tag.split('}')[-1]!='СведТов':continue
                a=el.attrib
                if not all(k in a for k in ('НаимТов','КолТов','СтТовУчНал')):continue
                qty=number(a['КолТов']);value=number(a['СтТовУчНал'],100)
                if qty:lines.append(dict(name=a['НаимТов'][:200],unit=a.get('ОКЕИ_Тов',''),qty=qty,value=value,item_id=None))
        except (ET.ParseError,ValueError):raise ValueError('Не удалось прочитать строки УПД XML. Проверьте документ.') from None
    else:raise ValueError('Загрузите PDF, PNG, JPEG или XML УПД.')
    if len(lines)>200:raise ValueError('В документе больше 200 позиций. Разделите его.')
    return (name,mime,raw,hashlib.sha256(raw).hexdigest()),lines


def save_receipt(conn,data,actor,file=None):
    import ztl
    proof,parsed=upload(file)
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        old=ztl.row_by_id(conn,'ztl_receipts',integer(data['id'])) if data.get('id') else None
        if old:
            ztl.version(old,data)
            if old['posted']:raise Conflict('Приход уже проведён. Повторное проведение запрещено.')
        lines=[]
        for i in range(200):
            key='item_'+str(i)
            if not data.get(key):
                if str(data.get('qty_'+str(i),'')).strip() or str(data.get('value_'+str(i),'')).strip():raise ValueError('Выберите позицию для каждой заполненной строки.')
                continue
            item=ztl.row_by_id(conn,'ztl_items',integer(data[key]))
            qty=number(data.get('qty_'+str(i)));value=number(data.get('value_'+str(i)),100)
            if not qty:raise ValueError('Количество прихода должно быть больше нуля.')
            lines.append(dict(item_id=item['id'],name=item['name'],unit=item['unit'],qty=qty,value=value))
        source_lines=parsed or (json.loads(old['lines']) if old else [])
        if lines and source_lines and len(lines)<len(source_lines):raise ValueError('Сопоставьте все строки документа: частичный приход не проводится.')
        if not lines:lines=source_lines
        confirmed=data.get('confirmed')=='1';paid=data.get('paid')=='1'
        if confirmed and (not lines or any(not r.get('item_id') for r in lines)):raise ValueError('Для подтверждения сопоставьте все позиции документа с номенклатурой и проверьте единицы.')
        if old and not proof[0]:proof=(old['document_name'],old['document_type'],old['document'],old['document_hash'])
        received=ztl.day(data.get('received_on'))
        if received>ztl.today():raise ValueError('Дата прихода не может быть в будущем.')
        values=dict(supplier=text(data.get('supplier'),200),reference=text(data.get('reference'),200),received_on=received,
                    lines=encode(lines),confirmed=int(confirmed),paid=int(paid),document_name=proof[0],document_type=proof[1],document=proof[2],document_hash=proof[3])
        if old:
            iid=old['id'];conn.execute('UPDATE ztl_receipts SET '+','.join(k+'=:'+k for k in values)+',version=version+1 WHERE id=:id',{**values,'id':iid})
        else:iid=conn.execute('INSERT INTO ztl_receipts('+','.join(values)+') VALUES('+','.join(':'+k for k in values)+')',values).lastrowid
        if confirmed and paid:
            # Source receipt is kept at its original date. Stock is posted now, never repricing old issues.
            for line in lines:move(conn,line['item_id'],line['qty'],line['value'],'receipt',iid,'Подтверждённый и оплаченный приход',ztl.today())
            conn.execute('UPDATE ztl_receipts SET posted=1 WHERE id=?',(iid,))
        ztl.log(conn,'Приход ЗТЛ',iid,'Проведение' if confirmed and paid else 'Черновик',actor,old,ztl.row_by_id(conn,'ztl_receipts',iid))
    return iid


def inventory_action(conn,action,data,actor):
    import ztl
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if action=='start':
            if conn.execute("SELECT 1 FROM ztl_inventories WHERE stage NOT IN ('accepted','cancelled')").fetchone():raise Conflict('Сначала завершите или отмените текущую инвентаризацию.')
            rows=[{k:r[k] for k in ('id','name','unit','qty','value','used','received')} for r in stock(conn)]
            if not rows:raise ValueError('Нет номенклатуры для инвентаризации.')
            iid=conn.execute("INSERT INTO ztl_inventories(snapshot,stamp,stage) VALUES(?,?,'count')",(encode(rows),stock_stamp(conn))).lastrowid
            ztl.log(conn,'Инвентаризация',iid,'Начало',actor,None,{'positions':len(rows)})
            return iid
        old=ztl.row_by_id(conn,'ztl_inventories',integer(data.get('id')));ztl.version(old,data);iid=old['id']
        if old['stage'] in ('accepted','cancelled'):raise Conflict('Инвентаризация уже закрыта.')
        if action!='cancel' and old['stamp']!=stock_stamp(conn):raise Conflict('После начала сверки изменился склад. Отмените эту сверку и начните новую.')
        snapshot=json.loads(old['snapshot']);counts=json.loads(old['counts']) if old['counts'] else {}
        changes={}
        if action=='count':
            if old['stage']!='count':raise Conflict('Сейчас ввод остатков закрыт.')
            counts={str(r['id']):number(data.get('count_'+str(r['id']))) for r in snapshot}
            match=all(counts[str(r['id'])]==r['qty'] for r in snapshot)
            changes=dict(counts=encode(counts),stage='match' if match else 'mismatch' if old['attempt']==1 else 'explain')
            if old['attempt']==1:changes['counts_first']=encode(counts)
        elif action=='recheck':
            if old['stage']!='mismatch' or old['attempt']!=1:raise Conflict('Повторная проверка недоступна.')
            changes=dict(stage='count',attempt=2,counts=None)
        elif action=='explain':
            if old['stage'] not in ('explain','review') or old['attempt']!=2:raise Conflict('Объяснительная сейчас не требуется.')
            changes=dict(person=text(data.get('person'),200),explanation=text(data.get('explanation'),10000),stage='review')
        elif action=='accept':
            if old['stage'] not in ('match','review'):raise Conflict('Нельзя принять: нужна повторная проверка или объяснительная.')
            for r in snapshot:
                delta=counts[str(r['id'])]-r['qty']
                if delta<0:consume(conn,r['id'],-delta,'inventory',iid,old['explanation'] or 'Сверка',ztl.today())
                elif delta>0:
                    item=next(s for s in stock(conn) if s['id']==r['id'])
                    if item['unit_cost'] is None:raise ValueError('Неизвестна стоимость излишка '+r['name']+'. Внесите исходный приход и проведите новую сверку.')
                    move(conn,r['id'],delta,rounded(item['unit_cost']*delta/1000),'inventory',iid,old['explanation'],ztl.today())
            changes=dict(stage='accepted',accepted_on=ztl.today())
        elif action=='cancel':changes=dict(stage='cancelled',explanation=text(data.get('reason'),4000))
        else:raise ValueError('Неизвестное действие.')
        conn.execute('UPDATE ztl_inventories SET '+','.join(k+'=:'+k for k in changes)+',version=version+1 WHERE id=:id',{**changes,'id':iid})
        ztl.log(conn,'Инвентаризация',iid,action,actor,old,ztl.row_by_id(conn,'ztl_inventories',iid))
    return iid


def write_off(conn,data,actor):
    import ztl
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if integer(data.get('stamp'))!=stock_stamp(conn):raise Conflict('Склад уже изменился. Обновите страницу перед списанием.')
        item=ztl.row_by_id(conn,'ztl_items',integer(data.get('item_id')))
        qty=number(data.get('qty'))
        if not qty:raise ValueError('Укажите количество списания.')
        reason=text(data.get('reason'),4000)
        value=consume(conn,item['id'],qty,'writeoff',actor,reason,ztl.today())
        ztl.log(conn,'Списание ЗТЛ',item['id'],'Списание',actor,None,dict(qty=qty,value=value,reason=reason))


def month_costs(conn,period):
    rows=[dict(r) for r in conn.execute("""SELECT m.*,i.kind AS item_kind FROM ztl_moves m
        JOIN ztl_items i ON i.id=m.item_id LEFT JOIN ztl_receipts r ON m.kind='receipt' AND r.id=m.ref_id
        WHERE substr(CASE WHEN m.kind='receipt' THEN r.received_on ELSE m.on_date END,1,7)=? ORDER BY m.id""",(period,))]
    # Materials expense once on use; nonmaterials once on receipt (no depreciation).
    materials=-sum(r['value'] for r in rows if r['item_kind']=='material' and r['kind']!='receipt')
    other=sum(r['value'] for r in rows if r['item_kind']!='material' and r['kind']=='receipt')
    purchases=sum(r['value'] for r in rows if r['kind']=='receipt')
    legacy=conn.execute("SELECT id,amount,taxes,version FROM ztl_costs WHERE month=? AND kind='lab_costs'",(period,)).fetchone()
    stamp=hashlib.sha256(encode([rows,dict(legacy) if legacy else None]).encode()).hexdigest()
    old=conn.execute('SELECT * FROM ztl_cost_reconciliation WHERE month=?',(period,)).fetchone()
    valid=old and old['stamp']==stamp
    return dict(month=period,materials=materials,other=other,purchases=purchases,total=materials+other,
                stamp=stamp,mode=old['mode'] if valid else None,version=old['version'] if old else 0,
                needs_confirmation=bool(rows))


def economic_summary(conn,r):
    costs=[month_costs(conn,p) for p in r['periods']]
    ready=all(not c['needs_confirmation'] or c['mode'] for c in costs)
    extra=sum(c['total'] for c in costs if c['mode']=='additional')
    total=r['total_cost']+extra
    complete=r['complete'] and ready
    return dict(months=costs,additional=extra,total_cost=total,complete=complete,
                theory=r['billed']-total if complete else None,actual=r['paid']-total if complete else None,
                margin=(r['paid']-total)/r['paid']*100 if complete and r['paid'] else None)


def payroll_orders(conn,start,end,technician=None):
    import ztl
    names={r['id']:r['name'] for r in conn.execute("SELECT id,name FROM ztl_names WHERE kind='employee'")}
    result=[]
    for o in ztl.orders(conn):
        p=production(conn,o['id'])
        if not p or not p['completed_on'] or not start<=p['completed_on'][:7]<=end:continue
        ids=json.loads(p['technicians'])
        if technician and technician not in ids:continue
        result.append({**o,**p,'technician_names':[names[i] for i in ids], 'shared':len(ids)>1,
                       'fee_total':p['pieces']*p['technician_fee']})
    return result


def register(app,db,permission_required,require_csrf,csrf_token,user_permissions):
    import ztl
    def rights():return {r.removeprefix('ztl_') for r in user_permissions(g.user) if r.startswith('ztl_')}
    def require(right):
        if right not in rights():abort(403)

    @app.post('/accounting/ztl/control/<action>')
    @permission_required('accounting')
    def ztl_control_save(action):
        needed={'item':'catalog','card':'catalog','receipt':'receipts_edit','inventory-start':'inventory_manage',
                'inventory-count':'inventory_count','inventory-recheck':'inventory_manage','inventory-explain':'inventory_count',
                'inventory-accept':'inventory_manage','inventory-cancel':'inventory_manage','writeoff':'inventory_manage','reconcile':'lab_costs'}.get(action)
        if not needed:abort(404)
        if needed=='inventory_count' and 'inventory_manage' in rights():pass
        else:require(needed)
        require_csrf();conn=db();data=request.form;tab='products'
        try:
            if action=='item':save_item(conn,data,g.user['id'])
            elif action=='card':save_card(conn,data,g.user['id'])
            elif action=='receipt':save_receipt(conn,data,g.user['id'],request.files.get('document'));tab='receipts'
            elif action.startswith('inventory-'):inventory_action(conn,action[10:],data,g.user['id']);tab='inventory'
            elif action=='writeoff':write_off(conn,data,g.user['id']);tab='inventory'
            elif action=='reconcile':
                period=ztl.month(data.get('month'));mode=data.get('mode')
                if mode not in ('included','additional'):raise ValueError('Укажите, включены ли эти затраты в ручную сумму.')
                with conn:
                    conn.execute('BEGIN IMMEDIATE');current=month_costs(conn,period)
                    if current['stamp']!=data.get('stamp') or current['version']!=integer(data.get('version')):raise Conflict('Состав затрат изменился. Обновите страницу.')
                    conn.execute('INSERT INTO ztl_cost_reconciliation(month,mode,stamp) VALUES(?,?,?) ON CONFLICT(month) DO UPDATE SET mode=excluded.mode,stamp=excluded.stamp,version=ztl_cost_reconciliation.version+1',(period,mode,current['stamp']))
                    ztl.log(conn,'Сверка затрат ЗТЛ',0,'Подтверждение',g.user['id'],current,dict(month=period,mode=mode))
                tab='economics'
        except Conflict as exc:return str(exc),409
        except ValueError as exc:return 'Данные не сохранены: '+str(exc),400
        except sqlite3.IntegrityError:return 'Такая запись или документ уже существует. Дубликат не проведён.',409
        return redirect(url_for('ztl_page',tab=tab,start=data.get('start') or ztl.today()[:7],end=data.get('end') or ztl.today()[:7],saved='1'),303)

    @app.get('/accounting/ztl/receipt-document/<int:iid>')
    @permission_required('accounting')
    def ztl_receipt_document(iid):
        if not rights() & {'receipts_view','receipts_edit'}:abort(403)
        try:r=ztl.row_by_id(db(),'ztl_receipts',iid)
        except ValueError:abort(404)
        if not r['document']:abort(404)
        res=send_file(io.BytesIO(r['document']),mimetype=r['document_type'],download_name=r['document_name'],as_attachment=True,max_age=0)
        res.headers['Cache-Control']='private, no-store';res.headers['X-Content-Type-Options']='nosniff'
        return res

    @app.get('/accounting/ztl/inventory/<int:iid>/print')
    @permission_required('accounting')
    def ztl_inventory_print(iid):
        require('inventory_manage')
        try:r=ztl.row_by_id(db(),'ztl_inventories',iid)
        except ValueError:abort(404)
        if r['attempt']!=2 or not r['explanation']:abort(409)
        return render_template('ztl_explanation.html',inv=r,rows=json.loads(r['snapshot']),counts=json.loads(r['counts']),qty=display)
