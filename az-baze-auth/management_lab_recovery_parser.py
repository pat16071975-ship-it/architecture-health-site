"""Strict standard-layout IDENT pair parser. No DB writes or patient output."""
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
import re
from openpyxl import load_workbook

MAX_ROWS = 30000
INVOICE = re.compile(r'^Сч[её]т\s*№\s*(\S+)\s+от\s+(\d{2}\.\d{2}\.\d{4})(?:\s+\d{1,2}:\d{2}:\d{2})?$', re.I)


def text(value):
    return ' '.join(str(value if value is not None else '').split())


def cents(value):
    if value is None or isinstance(value, bool) or text(value) in ('', '-', '—'):
        raise ValueError('Missing monetary value in detail or summary')
    try:
        n = Decimal(text(value).replace(' ', '').replace(',', '.')) * 100
        r = n.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        if not n.is_finite() or abs(n-r) > Decimal('0.00001') or abs(r)>10**14:
            raise ValueError('Invalid monetary value')
        return int(r)
    except InvalidOperation as exc:
        raise ValueError('Invalid monetary value') from exc


def quantity(value):
    try:
        n = Decimal(text(value).replace(' ', '').replace(',', '.'))
        if not n.is_finite() or abs(n)>10**9 or n != n.quantize(Decimal('.0001')):
            raise ValueError('Invalid quantity')
        return n
    except InvalidOperation as exc:
        raise ValueError('Invalid quantity') from exc


def day(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s=text(value)
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',s):
        return date.fromisoformat(s).isoformat()
    if not re.fullmatch(r'\d{2}\.\d{2}\.\d{4}(?: \d{1,2}:\d{2}(?::\d{2})?)?',s):
        raise ValueError('Invalid detail date')
    if ' ' in s:
        date_text,time_text=s.split(' ',1)
        datetime.strptime(time_text,'%H:%M:%S' if time_text.count(':')==2 else '%H:%M')
    return datetime.strptime(s.split(' ')[0], '%d.%m.%Y').date().isoformat()


def parse(raw, paid):
    width=8 if paid else 7
    wb=load_workbook(BytesIO(raw),read_only=True,data_only=True)
    try:
        if len(wb.worksheets)!=1:
            raise ValueError('Expected one standard IDENT worksheet')
        ws=wb.worksheets[0]
        if ws.max_column!=width or not 1<=ws.max_row<=MAX_ROWS:
            raise ValueError('Unexpected IDENT layout or row limit')
        rows=iter(ws.iter_rows(values_only=True))
        h=[text(x) for x in next(rows)]
        if h[:4] not in (['Группа услуг','Услуги','',''],['Группа услуг','Дата','Услуги','Количество']):
            raise ValueError('Unexpected IDENT grouping')
        expected=['Задолженность на нач. периода','Сумма со скидкой','Оплачено','Задолженность на конец периода'] if paid else ['Сумма по прейскуранту','Скидка','Сумма со скидкой']
        if h[4:]!=expected:
            raise ValueError('Unexpected IDENT financial columns')
        total=None
        details=[]
        invoice=invoice_day=group=None
        for row in rows:
            a,b,c,d=map(text,row[:4])
            if not any(text(v) for v in row):
                continue
            if a==('Услуги' if paid else 'Итого') and not b and not c and not d:
                summary=cents(row[5] if paid else row[6]),cents(row[6]) if paid else None
                if details or (total is not None and total!=summary):
                    raise ValueError('Ambiguous service total')
                total=summary
                invoice=invoice_day=group=None
                continue
            if total is None:
                if c or d:
                    raise ValueError('Service detail before summary')
                continue
            if a=='Авансы' and not c and not d:
                # The audited source format can contain advances outside
                # the services section. Never attribute them to a group.
                break
            match=INVOICE.fullmatch(a)
            if match:
                if b or c or d:
                    raise ValueError('Unexpected invoice row')
                invoice,invoice_day=match.group(1),day(match.group(2))
                group=None
                continue
            if not c and not d:
                if a or b:
                    invoice=invoice_day=group=None
                continue
            if not (c and d and invoice and invoice_day):
                raise ValueError('Incomplete IDENT detail')
            actual_day=day(row[1]) if b else invoice_day
            if actual_day!=invoice_day:
                raise ValueError('Invoice/detail dates differ')
            if a:
                group=a
            if not group:
                raise ValueError('Missing service group')
            key=(actual_day,invoice,group,c,quantity(row[3]),cents(row[5] if paid else row[6]))
            details.append((key,cents(row[6]) if paid else None))
        if total is None or not details or sum(k[-1] for k,_ in details)!=total[0]:
            raise ValueError('Service detail does not reconcile to workbook total')
        return details,total
    finally:
        wb.close()


def pair(service_raw, paid_raw, is_lab_group):
    services,service_total=parse(service_raw,False)
    payments,paid_total=parse(paid_raw,True)
    if service_total[0]!=paid_total[0] or Counter(k for k,_ in services)!=Counter(k for k,_ in payments):
        raise ValueError('Service/payment workbooks do not match')
    days=defaultdict(lambda:[0,set()])
    seen={}
    for (d,invoice,group,_,qty,amount),_ in services:
        prior=seen.setdefault(invoice,d)
        if prior!=d:
            raise ValueError('Invoice reused on multiple dates')
        entry=days[d]
        if is_lab_group(group):
            entry[0]+=amount
            entry[1].add(invoice)
    direct=sum(amount for k,amount in payments if is_lab_group(k[2]))
    all_detail=sum(amount for _,amount in payments)
    return {'from':min(days),'to':max(days),'knownPaid':direct/100,'paid':None,
            'clinicPaid':paid_total[1]/100,'clinicDetailPaid':all_detail/100,
            'clinicUnallocatedPaid':(paid_total[1]-all_detail)/100,'rowCount':len(services),
            'days':[{'date':d,'services':amt/100,'orders':len(inv)} for d,(amt,inv) in sorted(days.items())]}
