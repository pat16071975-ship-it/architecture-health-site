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


# Supported IDENT paid layout: styled provider/patient headings in the
# standard export, or an unstyled flat provider -> invoice hierarchy.
# Never infer an unknown heading from names or from a convenient subtotal.
_DEBT_LABELS = {"Неоплаченные услуги на начало периода", "Незакрытые долги без счетов"}


def _money_vector(values, role):
    allowed_dash = {
        "invoice": {0}, "detail": {0}, "old_debt": {1}, "advances": {1},
    }.get(role, set())
    amounts = tuple(
        0 if i in allowed_dash and text(v) in ("-", "—") else cents(v)
        for i, v in enumerate(values)
    )
    opening, billed, paid, closing = amounts
    if opening + billed - paid != closing:
        raise ValueError("IDENT monetary balance mismatch: " + role)
    if role in ("invoice", "detail") and opening != 0:
        raise ValueError("Current service row includes opening debt")
    if role in ("old_debt", "advances") and billed != 0:
        raise ValueError("Non-service row includes billed services")
    return amounts


def _sum_money(rows):
    return tuple(sum(row[i] for row in rows) for i in range(4))


def _check_subtotal(role, subtotal, children):
    if not children or subtotal != _sum_money(children):
        raise ValueError("IDENT monetary subtotal mismatch: " + role)


def _check_paid_hierarchy(ws, expected_detail_count, expected_total):
    """Reconcile all four monetary columns at every service hierarchy level.

    A signed adjustment is allowed only as an actual balanced source row.
    Opening debt is separate from current service items. Repeated opening-debt
    headings are one parent with reconciled children, never extra payments.
    No unexplained residual (positive or negative) is accepted.
    """
    rows = iter(ws.iter_rows())
    header = next(rows)
    signature = tuple(getattr(c, "_style_id", 0) for c in header)
    flat = signature == (0,) * 8
    if not flat and signature != (15, 14, 13, 13, 15, 15, 15, 15):
        raise ValueError("Unsupported IDENT paid hierarchy layout")
    total = grand = advances = None
    provider = patient = invoice = debt = None
    provider_totals = []
    detail_totals = []
    old_totals = []
    started = False
    ended = False

    def finish_invoice():
        nonlocal invoice
        if invoice is not None:
            _check_subtotal("invoice", *invoice)
            target = provider if flat else patient
            target[1].append(invoice[0])
            invoice = None

    def finish_debt():
        nonlocal debt
        if debt is not None:
            if debt[1]:
                _check_subtotal("opening debt", *debt)
            target = provider if flat else patient
            target[1].append(debt[0])
            old_totals.append(debt[0])
            debt = None

    def finish_patient():
        nonlocal patient
        finish_invoice()
        finish_debt()
        if patient is not None:
            _check_subtotal("patient", *patient)
            provider[1].append(patient[0])
            patient = None

    def finish_provider():
        nonlocal provider
        finish_patient()
        if provider is not None:
            _check_subtotal("provider", *provider)
            provider_totals.append(provider[0])
            provider = None

    for cells in rows:
        row = tuple(c.value for c in cells)
        a, b, c, d = map(text, row[:4])
        if not any(text(v) for v in row):
            continue
        heading = bool(a and not b and not c and not d)
        if heading and a == "Итого":
            if started or grand is not None:
                raise ValueError("Unexpected IDENT grand total")
            grand = _money_vector(row[4:8], "grand")
            continue
        if heading and a == "Авансы":
            if advances is not None:
                raise ValueError("Duplicate IDENT advances summary")
            advances = _money_vector(row[4:8], "advances")
            if started:
                finish_provider()
                ended = True
            continue
        if heading and a == "Услуги":
            summary = _money_vector(row[4:8], "services")
            if ended or provider is not None or provider_totals or detail_totals or (total is not None and total != summary):
                raise ValueError("Conflicting IDENT services summary")
            total = summary
            started = True
            continue
        if not started or ended:
            # The advances section is not evidence of services or ZTL.
            # Do not allow an unexpected detail row to escape reconciliation.
            if advances is None or c or d:
                raise ValueError("Unexpected row outside IDENT services hierarchy")
            continue
        if c and d:
            if invoice is None or debt is not None:
                raise ValueError("IDENT payment detail without current invoice")
            value = _money_vector(row[4:8], "detail")
            invoice[1].append(value)
            detail_totals.append(value)
            continue
        if not heading:
            raise ValueError("Unexpected IDENT paid hierarchy row")
        if a in _DEBT_LABELS:
            if provider is None or (not flat and patient is None):
                raise ValueError("Opening debt outside provider/patient")
            finish_invoice()
            value = _money_vector(row[4:8], "old_debt")
            if debt is None:
                debt = [value, []]
            else:
                debt[1].append(value)
            continue
        if INVOICE.fullmatch(a):
            if provider is None or (not flat and patient is None):
                raise ValueError("IDENT invoice outside provider/patient")
            finish_invoice()
            finish_debt()
            invoice = [_money_vector(row[4:8], "invoice"), []]
            continue
        style = getattr(cells[0], "_style_id", 0)
        if (flat and style == 0) or (not flat and style == 6):
            finish_provider()
            provider = [_money_vector(row[4:8], "provider"), []]
        elif not flat and style == 3 and provider is not None:
            finish_patient()
            patient = [_money_vector(row[4:8], "patient"), []]
        else:
            raise ValueError("Unrecognized IDENT paid subtotal level")

    finish_provider()
    if total is None:
        raise ValueError("Missing IDENT services summary")
    _check_subtotal("services", total, provider_totals)
    if len(detail_totals) != expected_detail_count or (total[1], total[2]) != expected_total:
        raise ValueError("IDENT payment hierarchy differs from parsed details")
    if total != _sum_money(detail_totals + old_totals):
        raise ValueError("Unexplained IDENT payment residual")
    if grand is not None or advances is not None:
        if grand is None or advances is None or grand != _sum_money([total, advances]):
            raise ValueError("IDENT grand total/advances mismatch")
    # Negative unallocated payments are legitimate ONLY when the signed
    # amount was explicitly present and reconciled in named old-debt rows.


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
        if paid:
            _check_paid_hierarchy(ws, len(details), total)
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
