"""ZTL orders and allocations. Independent ledger; never writes clinic finance or MIS."""
import hashlib
import io
import json
import re
import sqlite3
import ztl_control
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import abort, g, redirect, render_template, request, send_file, url_for
from itsdangerous import URLSafeTimedSerializer, BadSignature
from medical_supplies import number, integer, month, month_index, display, text, Conflict

SCHEMA = """
CREATE TABLE IF NOT EXISTS ztl_parties (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('legal','private')),
 version INTEGER NOT NULL DEFAULT 1, UNIQUE(name,kind)
);
CREATE TABLE IF NOT EXISTS ztl_names (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('doctor','work','customer','employee')),
 name TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, UNIQUE(kind,name)
);
CREATE TABLE IF NOT EXISTS ztl_orders (
 id INTEGER PRIMARY KEY, number TEXT NOT NULL UNIQUE, invoice_date TEXT NOT NULL,
 created_date TEXT NOT NULL, scope TEXT NOT NULL CHECK(scope IN ('internal','external')),
 party_id INTEGER NOT NULL REFERENCES ztl_parties(id), patient TEXT NOT NULL,
 customer_id INTEGER REFERENCES ztl_names(id), doctor_id INTEGER REFERENCES ztl_names(id),
 work_id INTEGER NOT NULL REFERENCES ztl_names(id), description TEXT NOT NULL,
 amount INTEGER NOT NULL CHECK(amount>=0), delay_days INTEGER NOT NULL CHECK(delay_days>=0),
 version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS ztl_payments (
 id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE, party_id INTEGER NOT NULL REFERENCES ztl_parties(id),
 scope TEXT NOT NULL CHECK(scope IN ('internal','external')), paid_date TEXT NOT NULL,
 amount INTEGER NOT NULL CHECK(amount>0), method TEXT NOT NULL,
 comment TEXT NOT NULL, service TEXT NOT NULL, service_total INTEGER, paid_patient TEXT NOT NULL,
 document_name TEXT, document_type TEXT, document BLOB,
 void_reason TEXT, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS ztl_allocations (
 id INTEGER PRIMARY KEY, payment_id INTEGER NOT NULL REFERENCES ztl_payments(id),
 order_id INTEGER NOT NULL REFERENCES ztl_orders(id), amount INTEGER NOT NULL CHECK(amount>0),
 UNIQUE(payment_id,order_id)
);
CREATE TABLE IF NOT EXISTS ztl_costs (
 id INTEGER PRIMARY KEY, month TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('lab_costs','clinic_costs','payroll')),
 employee_id INTEGER NOT NULL DEFAULT 0, amount INTEGER NOT NULL CHECK(amount>=0),
 taxes INTEGER NOT NULL DEFAULT 0 CHECK(taxes>=0), version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(month,kind,employee_id)
);
CREATE INDEX IF NOT EXISTS ztl_allocation_order ON ztl_allocations(order_id);
CREATE INDEX IF NOT EXISTS ztl_order_period ON ztl_orders(invoice_date);
CREATE TABLE IF NOT EXISTS ztl_history (
 id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id INTEGER NOT NULL, action TEXT NOT NULL,
 actor_id INTEGER NOT NULL, changed_at TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL
);
"""
RIGHTS = ('orders','catalog','payments','payroll','lab_costs','clinic_costs')
METHODS = {'bank':'Р/сч','card':'Карта','cash':'Наличные','qr':'QR-код'}
KINDS = {'doctor':'Врач','work':'Вид работ','customer':'Заказчик','employee':'Сотрудник ЗТЛ'}


def today():
    return datetime.now(ZoneInfo('Asia/Novosibirsk')).date().isoformat()


def init_schema(conn):
    conn.executescript(SCHEMA)
    ztl_control.init_schema(conn)


def day(value):
    try:
        value = str(value)
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        date.fromisoformat(value)
        return value
    except (ValueError, TypeError):
        raise ValueError('Укажите корректную дату.') from None


def log(conn, entity, iid, action, actor, before, after):
    # Binary proof stays in the protected document store, not the history JSON.
    def safe(v):
        if isinstance(v, dict):
            return {k: (hashlib.sha256(x).hexdigest() if k == 'document' and x else safe(x)) for k,x in v.items()}
        if isinstance(v, list):
            return [safe(x) for x in v]
        return v
    conn.execute('INSERT INTO ztl_history(entity,entity_id,action,actor_id,changed_at,before_json,after_json) VALUES(?,?,?,?,?,?,?)',
                 (entity,iid,action,actor,datetime.now(ZoneInfo('UTC')).isoformat(),
                  json.dumps(safe(before),ensure_ascii=False) if before is not None else None,
                  json.dumps(safe(after),ensure_ascii=False)))


def row_by_id(conn, table, iid):
    # Table names are internal constants only.
    row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (iid,)).fetchone()
    if row is None:
        raise ValueError('Запись не найдена.')
    return dict(row)


def version(row, data):
    if integer(data.get('version')) != row['version']:
        raise Conflict('Запись уже изменили. Обновите страницу и повторите ввод.')


def catalog_entry(conn, kind, name, actor, party_kind='private'):
    name = text(name,200)
    if kind == 'party':
        if party_kind not in ('legal','private'):
            raise ValueError('Выберите тип плательщика.')
        table, col, val = 'ztl_parties','kind',party_kind
    else:
        if kind not in KINDS:
            raise ValueError('Неизвестный справочник.')
        table, col, val = 'ztl_names','kind',kind
    existing = conn.execute(f'SELECT id FROM {table} WHERE name=? AND {col}=?',(name,val)).fetchone()
    if existing:
        return existing['id']
    if table=='ztl_names':
        # Deleted unused products remain in history; never reuse their IDs.
        iid=conn.execute("SELECT MAX(value)+1 FROM (SELECT COALESCE(MAX(id),0) AS value FROM ztl_names UNION ALL SELECT COALESCE(MAX(entity_id),0) FROM ztl_history WHERE entity='ztl_names')").fetchone()[0]
        conn.execute('INSERT INTO ztl_names(id,name,kind) VALUES(?,?,?)',(iid,name,val))
    else:
        iid = conn.execute(f'INSERT INTO {table}(name,{col}) VALUES(?,?)',(name,val)).lastrowid
    log(conn,table,iid,'Создание',actor,None,dict(name=name,kind=val))
    return iid


def save_catalog(conn,data,actor):
    kind=data.get('kind')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if not data.get('id'):
            return catalog_entry(conn,kind,data.get('name'),actor,data.get('party_kind'))
        table='ztl_parties' if kind=='party' else 'ztl_names'
        if kind!='party' and kind not in KINDS:
            raise ValueError('Неизвестный справочник.')
        old=row_by_id(conn,table,integer(data['id']));version(old,data)
        if table=='ztl_names' and old['kind']!=kind:
            raise ValueError('Нельзя менять тип записи.')
        name=text(data.get('name'),200)
        conn.execute(f'UPDATE {table} SET name=?,version=version+1 WHERE id=?',(name,old['id']))
        log(conn,table,old['id'],'Изменение наименования',actor,old,row_by_id(conn,table,old['id']))


def resolve(conn, data, key, kind, actor, can_catalog, required=True):
    if data.get(key+'_new','').strip():
        if not can_catalog:
            raise PermissionError()
        return catalog_entry(conn,kind,data[key+'_new'],actor,data.get('party_kind','private'))
    if not data.get(key):
        if required:
            raise ValueError('Выберите плательщика, работу и врача для внутреннего заказа.')
        return None
    iid = integer(data[key])
    row = row_by_id(conn,'ztl_parties' if kind=='party' else 'ztl_names',iid)
    if kind != 'party' and row['kind'] != kind:
        raise ValueError('Запись из другого справочника.')
    return iid


def save_order(conn, data, actor, can_catalog=False):
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        old = row_by_id(conn,'ztl_orders',integer(data['id'])) if data.get('id') else None
        if old:
            version(old,data)
        scope = data.get('scope')
        if scope not in ('internal','external'):
            raise ValueError('Выберите внутренний или внешний заказ.')
        suffix = str(data.get('number','')).strip().removeprefix('LAB-')
        if not re.fullmatch(r'[0-9A-Za-zА-Яа-я._/-]{1,40}',suffix):
            raise ValueError('Введите номер заказа после LAB-.')
        values = dict(number='LAB-'+suffix, invoice_date=day(data.get('invoice_date')),
                      created_date=old['created_date'] if old else today(), scope=scope,
                      party_id=resolve(conn,data,'party_id','party',actor,can_catalog),
                      patient=text(data.get('patient'),200),
                      customer_id=resolve(conn,data,'customer_id','customer',actor,can_catalog,False),
                      doctor_id=resolve(conn,data,'doctor_id','doctor',actor,can_catalog) if scope=='internal' else None,
                      work_id=resolve(conn,data,'work_id','work',actor,False),
                      description=str(data.get('description','')).strip()[:4000],
                      amount=number(data.get('amount'),100),delay_days=integer(data.get('delay_days')))
        if values['delay_days'] > 3650:
            raise ValueError('Отсрочка не может превышать 3650 дней.')
        if (not old or old['work_id']!=values['work_id']) and conn.execute('SELECT 1 FROM ztl_product_archive WHERE work_id=?',(values['work_id'],)).fetchone():
            raise ValueError('Изделие убрано из ассортимента. Выберите действующее изделие.')
        if values['invoice_date'] > today():
            raise ValueError('Дата счёта не может быть в будущем.')
        if old:
            paid = conn.execute('SELECT COALESCE(SUM(a.amount),0) FROM ztl_allocations a JOIN ztl_payments p ON p.id=a.payment_id WHERE a.order_id=? AND p.void_reason IS NULL',(old['id'],)).fetchone()[0]
            if paid and (values['party_id'] != old['party_id'] or values['scope'] != old['scope']):
                raise ValueError('Сначала отмените распределённые оплаты, чтобы изменить плательщика или тип заказа.')
            if values['amount'] < paid:
                raise ValueError('Сумма счёта меньше уже зачтённых оплат.')
            conn.execute('UPDATE ztl_orders SET '+','.join(k+'=:'+k for k in values)+',version=version+1 WHERE id=:id',{**values,'id':old['id']})
            iid=old['id']
        else:
            iid=conn.execute('INSERT INTO ztl_orders('+','.join(values)+') VALUES('+','.join(':'+k for k in values)+')',values).lastrowid
        ztl_control.save_production(conn,iid,old,values,data,actor)
        log(conn,'Заказ',iid,'Изменение' if old else 'Создание',actor,old,row_by_id(conn,'ztl_orders',iid))
    return iid


def orders(conn):
    return [dict(r) for r in conn.execute('''SELECT o.*,p.name AS party_name,p.kind AS party_kind,
        w.name AS work_name,d.name AS doctor_name,c.name AS customer_name,
        COALESCE((SELECT SUM(a.amount) FROM ztl_allocations a JOIN ztl_payments pay ON pay.id=a.payment_id
          WHERE a.order_id=o.id AND pay.void_reason IS NULL),0) AS paid
        FROM ztl_orders o JOIN ztl_parties p ON p.id=o.party_id JOIN ztl_names w ON w.id=o.work_id
        LEFT JOIN ztl_names d ON d.id=o.doctor_id LEFT JOIN ztl_names c ON c.id=o.customer_id
        ORDER BY o.invoice_date,o.id''')]


def available(conn, payment_id):
    p=conn.execute('SELECT amount,void_reason FROM ztl_payments WHERE id=?',(payment_id,)).fetchone()
    if p is None:raise ValueError('Оплата не найдена.')
    used=conn.execute('SELECT COALESCE(SUM(amount),0) FROM ztl_allocations WHERE payment_id=?',(payment_id,)).fetchone()[0]
    return 0 if p['void_reason'] else p['amount']-used


def allocation_plan(conn, party_id, scope, amount, order_id=None):
    result=[]
    remaining=amount
    for o in orders(conn):
        if o['party_id'] != party_id or o['scope'] != scope or (order_id and o['id'] != order_id):
            continue
        part=min(remaining,o['amount']-o['paid'])
        if part>0:
            result.append(dict(order_id=o['id'],number=o['number'],amount=part))
            remaining-=part
    return dict(allocations=result,advance=remaining)


def ledger_stamp(conn):
    return conn.execute('SELECT COALESCE(MAX(id),0) FROM ztl_history').fetchone()[0]


def payment_values(conn, data):
    pid=integer(data.get('party_id'));row_by_id(conn,'ztl_parties',pid)
    scope=data.get('scope')
    if scope not in ('internal','external'):
        raise ValueError('Выберите тип оплаты.')
    paid_date=day(data.get('paid_date'))
    if paid_date>today():
        raise ValueError('Будущую оплату нельзя записать как полученную.')
    method=data.get('method')
    if method not in METHODS:
        raise ValueError('Выберите способ оплаты.')
    amount=number(data.get('amount'),100)
    if amount<=0:
        raise ValueError('Сумма оплаты должна быть больше нуля.')
    service=str(data.get('service','')).strip()
    service_total=number(data.get('service_total'),100) if scope=='internal' else None
    paid_patient=text(data.get('paid_patient'),200) if scope=='internal' else ''
    if scope=='internal' and (not service or service_total<amount):
        raise ValueError('Укажите оплаченную услугу и полную сумму оплаты пациента не меньше части ЗТЛ.')
    oid=integer(data['order_id']) if data.get('order_id') else None
    if oid:
        o=row_by_id(conn,'ztl_orders',oid)
        if o['party_id']!=pid or o['scope']!=scope:
            raise ValueError('Заказ принадлежит другому плательщику или типу.')
    return dict(party_id=pid,scope=scope,paid_date=paid_date,amount=amount,method=method,
                service=service[:500],service_total=service_total,paid_patient=paid_patient,comment=str(data.get('comment','')).strip()[:4000],order_id=oid)


def document(file):
    if file is None or not file.filename:
        return None,None,None
    content=file.read(1024*1024+1)
    if len(content)>1024*1024:
        raise ValueError('Документ должен быть не больше 1 МБ.')
    mime = 'application/pdf' if content.startswith(b'%PDF-') else 'image/png' if content.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if content.startswith(b'\xff\xd8\xff') else None
    if not mime:
        raise ValueError('Приложите PDF, PNG или JPEG.')
    # Never use user filename as a filesystem path or inline content.
    return content, mime, text(file.filename.replace('\\','/').split('/')[-1],200)


def save_payment(conn, values, proof, actor, stamp, token):
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT id FROM ztl_payments WHERE token=?',(token,)).fetchone():
            raise Conflict('Эта оплата уже сохранена.')
        if stamp!=ledger_stamp(conn):
            raise Conflict('Данные изменились. Заново посмотрите распределение оплаты.')
        values=dict(values)
        oid=values.pop('order_id')
        content,mime,name=proof
        if content is None and not values['comment']:
            raise ValueError('Приложите документ или заполните пояснение об оплате.')
        plan=allocation_plan(conn,values['party_id'],values['scope'],values['amount'],oid)
        values.update(document=content,document_type=mime,document_name=name,token=token)
        iid=conn.execute('INSERT INTO ztl_payments('+','.join(values)+') VALUES('+','.join(':'+k for k in values)+')',values).lastrowid
        for a in plan['allocations']:
            conn.execute('INSERT INTO ztl_allocations(payment_id,order_id,amount) VALUES(?,?,?)',(iid,a['order_id'],a['amount']))
        log(conn,'Оплата',iid,'Создание',actor,None,{**values,**plan})
    return iid


def use_advance(conn, payment_id, actor, stamp):
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if stamp!=ledger_stamp(conn):
            raise Conflict('Данные изменились. Обновите страницу перед зачётом.')
        p=row_by_id(conn,'ztl_payments',payment_id)
        plan=allocation_plan(conn,p['party_id'],p['scope'],available(conn,payment_id))
        if not plan['allocations']:
            raise ValueError('Нет доступного аванса или незакрытых заказов.')
        for a in plan['allocations']:
            conn.execute('INSERT INTO ztl_allocations(payment_id,order_id,amount) VALUES(?,?,?) ON CONFLICT(payment_id,order_id) DO UPDATE SET amount=amount+excluded.amount',(payment_id,a['order_id'],a['amount']))
        log(conn,'Оплата',payment_id,'Зачёт аванса',actor,None,plan)


def void_payment(conn, data, actor):
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        p=row_by_id(conn,'ztl_payments',integer(data.get('id')))
        version(p,data)
        if p['void_reason']:
            raise Conflict('Оплата уже отменена.')
        reason=text(data.get('reason'),1000)
        conn.execute('UPDATE ztl_payments SET void_reason=?,version=version+1 WHERE id=?',(reason,p['id']))
        log(conn,'Оплата',p['id'],'Отмена ошибочной записи (не возврат денег)',actor,p,dict(reason=reason))


def save_cost(conn,data,actor,kind):
    period=month(data.get('month'))
    employee=integer(data.get('employee_id') or '0') if kind=='payroll' else 0
    employee_new=str(data.get('employee_new','')).strip() if kind=='payroll' else ''
    amount=number(data.get('amount'),100)
    taxes=number(data.get('taxes'),100) if kind=='payroll' else 0
    if kind=='payroll' and not employee and not employee_new and (amount or taxes):
        raise ValueError('Для начисления выберите сотрудника. Без сотрудника допустимо только подтверждение нулевого ФОТ.')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if employee_new:
            employee=catalog_entry(conn,'employee',employee_new,actor)
        if employee:
            if row_by_id(conn,'ztl_names',employee)['kind']!='employee':
                raise ValueError('Выберите сотрудника ЗТЛ.')
        old=conn.execute('SELECT * FROM ztl_costs WHERE month=? AND kind=? AND employee_id=?',(period,kind,employee)).fetchone()
        if integer(data.get('version'))!=(old['version'] if old else 0):
            raise Conflict('Затраты уже изменили. Обновите страницу.')
        conn.execute('INSERT INTO ztl_costs(month,kind,employee_id,amount,taxes) VALUES(?,?,?,?,?) ON CONFLICT(month,kind,employee_id) DO UPDATE SET amount=excluded.amount,taxes=excluded.taxes,version=version+1',(period,kind,employee,amount,taxes))
        if kind=='payroll' and employee:
            marker=conn.execute("SELECT * FROM ztl_costs WHERE month=? AND kind='payroll' AND employee_id=0",(period,)).fetchone()
            if marker:
                conn.execute("DELETE FROM ztl_costs WHERE id=?",(marker['id'],))
                log(conn,'Затраты',marker['id'],'Снятие подтверждения ФОТ после изменения',actor,dict(marker),{})
        new=dict(conn.execute('SELECT * FROM ztl_costs WHERE month=? AND kind=? AND employee_id=?',(period,kind,employee)).fetchone())
        log(conn,'Затраты',new['id'],'Изменение' if old else 'Создание',actor,dict(old) if old else None,new)


def report(conn, start, end):
    start,end=month(start),month(end)
    if start>end or month_index(end)-month_index(start)>119:
        raise ValueError('Выберите период от одного до 120 месяцев.')
    selected=[o for o in orders(conn) if start<=o['invoice_date'][:7]<=end]
    for o in selected:
        o['debt']=o['amount']-o['paid']
        o['due_date']=(date.fromisoformat(o['created_date'])+timedelta(days=o['delay_days'])).isoformat()
        o['overdue']=o['debt']>0 and o['due_date']<today()
    costs=[dict(r) for r in conn.execute('SELECT c.*,e.name AS employee FROM ztl_costs c LEFT JOIN ztl_names e ON e.id=c.employee_id WHERE c.month BETWEEN ? AND ? ORDER BY c.month,c.kind,e.name',(start,end))]
    periods=[];cur=start
    while cur<=end:
        periods.append(cur);y,m=map(int,cur.split('-'));cur=f'{y+1:04d}-01' if m==12 else f'{y:04d}-{m+1:02d}'
    complete=all(any(c['month']==m and c['kind']==k and (k!='payroll' or c['employee_id']==0) for c in costs) for m in periods for k in ('lab_costs','clinic_costs','payroll'))
    totals={k:sum(c['amount']+c['taxes'] for c in costs if c['kind']==k) for k in ('lab_costs','clinic_costs','payroll')}
    total_cost=sum(totals.values());billed=sum(o['amount'] for o in selected);paid=sum(o['paid'] for o in selected)
    cash=conn.execute('SELECT COALESCE(SUM(amount),0) FROM ztl_payments WHERE substr(paid_date,1,7) BETWEEN ? AND ? AND void_reason IS NULL',(start,end)).fetchone()[0]
    doctors={}
    for o in selected:
        if o['scope']=='internal':
            d=doctors.setdefault(o['doctor_id'],dict(name=o['doctor_name'],count=0,amount=0,orders=[]))
            d['count']+=1;d['amount']+=o['amount'];d['orders'].append(o)
    return dict(orders=selected,costs=costs,periods=periods,doctors=list(doctors.values()),complete=complete,
                count=len(selected),billed=billed,paid=paid,debt=billed-paid,cash=cash,total_cost=total_cost,**totals,
                theory=billed-total_cost if complete else None,actual=paid-total_cost if complete else None,
                margin=(paid-total_cost)/paid*100 if complete and paid else None)


def register(app,db,permission_required,require_csrf,csrf_token,user_permissions):
    def rights():
        return {r for r in (*RIGHTS,*ztl_control.RIGHTS) if 'ztl_'+r in user_permissions(g.user)}

    def context():
        conn=db()
        return dict(rights=rights(),csrf=csrf_token(),fmt=lambda v: display(v,100) if v is not None else 'Неполные данные',
                    today=today(),methods=METHODS,kinds=KINDS,ztl_tabs=ztl_control.TABS,qty_fmt=display,
                    parties=[dict(r) for r in conn.execute('SELECT * FROM ztl_parties ORDER BY name,id')],
                    names=[dict(r) for r in conn.execute('SELECT n.*,a.work_id IS NOT NULL AS archived FROM ztl_names n LEFT JOIN ztl_product_archive a ON a.work_id=n.id ORDER BY n.name,n.id')])

    def render(error=None,status=200,**extra):
        labels=dict(ztl_control.TABS,doctors='Заказы по врачам',catalog='Справочники',history='История',
                    order_form='Учёт заказов',payment_form='Реестр оплат',payment_preview='Реестр оплат')
        section_title=labels[extra['tab']]
        return render_template('ztl.html',title=section_title+' — ЗТЛ',section_title=section_title,error=error,**context(),**extra),status

    @app.get('/accounting/ztl/')
    @app.get('/accounting/ztl/<tab>/')
    @permission_required('accounting')
    def ztl_page(tab=None):
        conn=db()
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='ztl_orders'").fetchone():
            return 'Учёт ЗТЛ ещё не подготовлен к работе.',503
        start=request.args.get('start') or today()[:7];end=request.args.get('end') or start
        # The entrance is a menu, not a ledger. Old explicit ?tab= links still work.
        tab=tab if tab is not None else request.args.get('tab')
        if tab is None:
            return render_template('ztl_menu.html',rights=rights(),ztl_tabs=ztl_control.TABS,start=start,end=end)
        if tab not in ('orders','doctors','payments','costs','catalog','history','products','receipts','inventory','payroll','economics'):
            abort(400)
        if tab=='costs':tab='economics'
        if tab=='receipts' and not rights() & {'receipts_view','receipts_edit'}:abort(403)
        if tab=='inventory' and not rights() & {'inventory_count','inventory_manage'}:abort(403)
        try:
            conn.execute('BEGIN')
            r=report(conn,start,end) if tab in ('orders','doctors','payroll','economics') else None
            mode=request.args.get('mode','all');unpaid=request.args.get('unpaid')=='1'
            if mode not in ('all','group','internal','external','legal','private'):
                abort(400)
            visible=[o for o in r['orders'] if (mode not in ('internal','external') or o['scope']==mode) and (mode not in ('legal','private') or o['party_kind']==mode) and (not unpaid or o['debt']>0)] if tab=='orders' else []
            technician=integer(request.args['technician']) if request.args.get('technician') else None
            employees={n['id']:n['name'] for n in conn.execute("SELECT id,name FROM ztl_names WHERE kind='employee'")}
            for o in visible:
                p=ztl_control.production(conn,o['id'])
                o['production']=p
                o['technician_ids']=json.loads(p['technicians']) if p else []
                o['technician_names']=[employees[i] for i in o['technician_ids']]
            if technician:visible=[o for o in visible if technician in o['technician_ids']]
            if mode=='group':
                visible.sort(key=lambda o:(o['scope'],o['invoice_date'],o['id']))
            payments=[dict(p) for p in conn.execute('SELECT p.id,p.party_id,p.scope,p.paid_date,p.amount,p.method,p.comment,p.service,p.service_total,p.paid_patient,p.document_name,p.void_reason,p.version,q.name AS party_name FROM ztl_payments p JOIN ztl_parties q ON q.id=p.party_id ORDER BY p.paid_date DESC,p.id DESC')] if tab=='payments' else []
            balances=[]
            if tab=='payments':
                all_orders=orders(conn)
                for p in payments:
                    p['advance']=available(conn,p['id'])
                    p['allocations']=[dict(a) for a in conn.execute('SELECT a.amount,o.number FROM ztl_allocations a JOIN ztl_orders o ON o.id=a.order_id WHERE a.payment_id=?',(p['id'],))]
                for party in conn.execute('SELECT * FROM ztl_parties ORDER BY name'):
                    for scope in ('internal','external'):
                        debt=sum(o['amount']-o['paid'] for o in all_orders if o['party_id']==party['id'] and o['scope']==scope)
                        advance=sum(p['advance'] for p in payments if p['party_id']==party['id'] and p['scope']==scope)
                        if debt or advance:
                            balances.append(dict(name=party['name'],party_id=party['id'],scope=scope,debt=debt,advance=advance,balance=advance-debt))
            events=[dict(e) for e in conn.execute('SELECT h.*,u.full_name AS actor FROM ztl_history h LEFT JOIN users u ON u.id=h.actor_id ORDER BY h.id DESC LIMIT 200')] if tab=='history' else []
            if 'inventory_manage' not in rights():events=[e for e in events if e['entity'] not in ('Инвентаризация','Списание ЗТЛ','Производство')]
            if not rights() & {'receipts_view','receipts_edit'}:events=[e for e in events if e['entity']!='Приход ЗТЛ']
            extra=dict(technician=technician)
            if tab=='products':extra.update(items=ztl_control.stock(conn),cards=ztl_control.catalog(conn),units=ztl_control.units(conn))
            if tab=='receipts':
                receipts=[dict(row) for row in conn.execute('SELECT id,supplier,reference,received_on,lines,confirmed,paid,posted,version,document_name FROM ztl_receipts ORDER BY id DESC')]
                for row in receipts:row['parts']=json.loads(row['lines'])
                extra.update(items=ztl_control.stock(conn),receipts=receipts,units=ztl_control.units(conn))
            if tab=='inventory':
                inventories=[dict(row) for row in conn.execute('SELECT * FROM ztl_inventories ORDER BY id DESC')]
                full='inventory_manage' in rights()
                for inv in inventories:
                    snap=json.loads(inv['snapshot']);counts=json.loads(inv['counts']) if inv['counts'] else {}
                    inv['rows']=[{**({k:v for k,v in row.items() if full or k in ('id','name','unit','received')}), 'count':counts.get(str(row['id']))} for row in snap]
                    if not full:
                        for key in ('snapshot','stamp','counts','counts_first'):inv.pop(key,None)
                extra.update(inventories=inventories,items=ztl_control.stock(conn) if full else [],full_inventory=full,stock_stamp=ztl_control.stock_stamp(conn))
            if tab=='payroll':
                extra.update(payroll_orders=ztl_control.payroll_orders(conn,start,end,technician))
            if tab=='economics':extra.update(economics=ztl_control.economic_summary(conn,r))
            result=render(**extra,tab=tab,start=start,end=end,r=r,visible=visible,mode=mode,unpaid=unpaid,payments=payments,balances=balances,events=events,stamp=ledger_stamp(conn) if tab=='payments' else None,saved=request.args.get('saved')=='1')
            return result
        except ValueError as exc:
            return str(exc),400
        finally:
            conn.rollback()

    @app.get('/accounting/ztl/order')
    @permission_required('accounting')
    def ztl_order_form():
        if 'orders' not in rights():abort(403)
        try:
            edit=row_by_id(db(),'ztl_orders',integer(request.args['id'])) if request.args.get('id') else None
            production=ztl_control.production(db(),edit['id']) if edit else None
            if production:production['technician_ids']=json.loads(production['technicians'])
            return render(tab='order_form',edit=edit,production=production)
        except ValueError as exc:return str(exc),400

    @app.get('/accounting/ztl/payment')
    @permission_required('accounting')
    def ztl_payment_form():
        if 'payments' not in rights():abort(403)
        conn=db()
        try:
            pid=integer(request.args['party_id']) if request.args.get('party_id') else None
            if pid:row_by_id(conn,'ztl_parties',pid)
        except ValueError as exc:return str(exc),400
        scope=request.args.get('scope','external')
        if scope not in ('internal','external'):abort(400)
        due=sum(o['amount']-o['paid'] for o in orders(conn) if o['party_id']==pid and o['scope']==scope)
        return render(tab='payment_form',all_orders=orders(conn),chosen_party=pid,chosen_scope=scope,due=due)

    @app.post('/accounting/ztl/payment-preview')
    @permission_required('accounting')
    def ztl_payment_preview():
        if 'payments' not in rights():abort(403)
        require_csrf()
        try:
            conn=db();conn.execute('BEGIN')
            values=payment_values(conn,request.form)
            plan=allocation_plan(conn,values['party_id'],values['scope'],values['amount'],values['order_id'])
            signed=URLSafeTimedSerializer(app.secret_key,salt='ztl-payment').dumps(dict(values=values,stamp=ledger_stamp(conn),actor=g.user['id']))
            return render(tab='payment_preview',values=values,plan=plan,signed=signed)
        except ValueError as exc:return str(exc),400
        finally:db().rollback()

    @app.post('/accounting/ztl/save/<action>')
    @permission_required('accounting')
    def ztl_save(action):
        needed={'order':'orders','catalog':'catalog','payment':'payments','advance':'payments','void':'payments',
                'payroll':'payroll','lab_costs':'lab_costs','clinic_costs':'clinic_costs'}.get(action)
        if not needed:abort(404)
        if needed not in rights():abort(403)
        require_csrf()
        conn=db()
        try:
            if action=='order':save_order(conn,request.form,g.user['id'],'catalog' in rights())
            elif action=='catalog':save_catalog(conn,request.form,g.user['id'])
            elif action=='payment':
                signed=text(request.form.get('signed'),20000)
                payload=URLSafeTimedSerializer(app.secret_key,salt='ztl-payment').loads(signed,max_age=1800)
                if payload['actor']!=g.user['id']:abort(403)
                values=payload['values']
                # Proof/comment are supplied on the confirmation screen; immutable amounts are signed.
                values['comment']=str(request.form.get('comment','')).strip()[:4000]
                save_payment(conn,values,document(request.files.get('document')),g.user['id'],payload['stamp'],hashlib.sha256(signed.encode()).hexdigest())
            elif action=='advance':use_advance(conn,integer(request.form.get('id')),g.user['id'],integer(request.form.get('stamp')))
            elif action=='void':void_payment(conn,request.form,g.user['id'])
            else:save_cost(conn,request.form,g.user['id'],action)
        except PermissionError:abort(403)
        except Conflict as exc:return str(exc),409,{'Content-Type':'text/plain; charset=utf-8'}
        except (ValueError,BadSignature) as exc:return 'Данные не сохранены: '+str(exc),400,{'Content-Type':'text/plain; charset=utf-8'}
        except sqlite3.IntegrityError:return 'Данные не сохранены: номер заказа уже существует или ссылка некорректна.',409
        return redirect(url_for('ztl_page',tab='payments' if needed=='payments' else 'payroll' if action=='payroll' else 'economics' if action in ('lab_costs','clinic_costs') else 'catalog' if action=='catalog' else 'orders',start=request.form.get('month') or today()[:7],end=request.form.get('month') or today()[:7],saved='1'),303)

    @app.get('/accounting/ztl/document/<int:payment_id>')
    @permission_required('accounting')
    def ztl_document(payment_id):
        try:p=row_by_id(db(),'ztl_payments',payment_id)
        except ValueError:abort(404)
        if not p['document']:abort(404)
        response=send_file(io.BytesIO(p['document']),mimetype=p['document_type'],as_attachment=True,download_name=p['document_name'],max_age=0)
        response.headers['Cache-Control']='private, no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response
