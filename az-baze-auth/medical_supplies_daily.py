"""Manual classification and dated stock ledger; imported balances are never receipts."""
import calendar
import io
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from flask import abort, g, redirect, render_template, request, send_file, url_for
from openpyxl import Workbook
import medical_supplies_import as stock

SCHEMA = '''
CREATE TABLE IF NOT EXISTS med_supply_folders (
 id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES med_supply_folders(id),
 name TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(parent_id,name)
);
CREATE TABLE IF NOT EXISTS med_supply_daily_items (
 item_id INTEGER PRIMARY KEY REFERENCES med_supply_items(id),
 import_id INTEGER NOT NULL REFERENCES med_supply_stock_imports(id),
 source_row INTEGER NOT NULL, folder_id INTEGER NOT NULL REFERENCES med_supply_folders(id),
 opening_date TEXT NOT NULL, opening_qty TEXT NOT NULL, opening_value TEXT NOT NULL,
 pieces_per_unit TEXT, version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(import_id,source_row)
);
CREATE TABLE IF NOT EXISTS med_supply_days (
 item_id INTEGER NOT NULL REFERENCES med_supply_daily_items(item_id), day TEXT NOT NULL,
 qty_in TEXT NOT NULL, value_in TEXT, qty_out TEXT NOT NULL,
 in_known INTEGER NOT NULL CHECK(in_known IN (0,1)),
 out_known INTEGER NOT NULL CHECK(out_known IN (0,1)),
 version INTEGER NOT NULL, PRIMARY KEY(item_id,day)
);
CREATE TABLE IF NOT EXISTS med_supply_folder_history (
 id INTEGER PRIMARY KEY, folder_id INTEGER NOT NULL REFERENCES med_supply_folders(id),
 actor_id INTEGER NOT NULL, changed_at TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL
);
'''
FOLDERS = [
    ('Аптека', ['Анестезия', 'Аптечки']),
    ('Дезинфекция', []), ('Инструментарий', ['Наконечники']),
    ('НЕ ИСПОЛЬЗОВАТЬ', []), ('Оборудование', ['Ленина, 85']),
    ('Расходные материалы', ['Гигиена', 'Зуботехническая лаборатория', 'Массаж',
     'Миотерапия', 'Ортодонтия', 'Ортопедия', 'Разное', 'СИЗ', 'Терапия', 'Хирургия', 'ЦСО']),
    ('ТОВАРЫ ДЛЯ ПРОДАЖИ', ['Froggy Mouth', 'Зубные пасты', 'Зубные щетки CURAPROX',
     'Контейнеры для хранения стоматоконструкций']),
    ('Хоз/Канц товары', ['Канц.Товары', 'Товары для пациентов', 'Хоз.товары']),
    ('Новое (разобрать)', []),
]


class Conflict(ValueError):
    pass


def init_schema(conn):
    conn.executescript(SCHEMA)
    # Metadata only, explicit schema setup. GET and application startup never seed data.
    with conn:
        if not conn.execute('SELECT 1 FROM med_supply_folders LIMIT 1').fetchone():
            for name, children in FOLDERS:
                fid = conn.execute('INSERT INTO med_supply_folders(name) VALUES(?)', (name,)).lastrowid
                conn.executemany('INSERT INTO med_supply_folders(parent_id,name) VALUES(?,?)',
                                 [(fid, child) for child in children])


def ready(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='med_supply_daily_items' AND type='table'").fetchone())


def today():
    return datetime.now(ZoneInfo('Asia/Novosibirsk')).date()


def num(value, optional=False):
    if optional and (value is None or str(value).strip() == ''):
        return None
    try:
        n = Decimal(str(value).strip().replace(',', '.'))
        if isinstance(value, bool) or not n.is_finite() or n < 0 or n > Decimal('1000000000'):
            raise ValueError()
        if n != n.quantize(Decimal('.001')):
            raise ValueError()
        return n
    except (ValueError, InvalidOperation):
        raise ValueError('Введите неотрицательное число, не более трёх знаков после запятой.') from None


def ident(value):
    if not str(value or '').isdigit() or len(str(value)) > 12:
        raise ValueError('Некорректный номер записи.')
    return int(value)


def folders(conn):
    return [dict(r) for r in conn.execute('SELECT * FROM med_supply_folders ORDER BY id')]


def folder_choice(conn, data):
    parent = conn.execute('SELECT * FROM med_supply_folders WHERE id=?', (ident(data.get('folder')),)).fetchone()
    if not parent or parent['parent_id'] is not None:
        raise ValueError('Выберите основную папку.')
    if not data.get('subfolder'):
        return parent['id']
    child = conn.execute('SELECT * FROM med_supply_folders WHERE id=?', (ident(data['subfolder']),)).fetchone()
    if not child or child['parent_id'] != parent['id']:
        raise ValueError('Подпапка не относится к выбранной папке.')
    return child['id']


def save_folder(conn, data, actor):
    name = str(data.get('name') or '').strip()
    if not name or len(name) > 120:
        raise ValueError('Укажите название папки, до 120 символов.')
    fid = ident(data['id']) if data.get('id') else None
    parent = ident(data['parent']) if data.get('parent') else None
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        before = None
        if fid:
            row = conn.execute('SELECT * FROM med_supply_folders WHERE id=?', (fid,)).fetchone()
            if not row:
                raise ValueError('Папка не найдена.')
            before = dict(row)
            if ident(data.get('version')) != row['version']:
                raise Conflict('Папку уже изменили. Обновите страницу.')
            parent = row['parent_id']  # Renaming never silently reparents a subtree.
        if parent:
            p = conn.execute('SELECT parent_id FROM med_supply_folders WHERE id=?', (parent,)).fetchone()
            if not p or p['parent_id'] is not None:
                raise ValueError('Разрешены два уровня: основная папка и подпапка.')
        if conn.execute('SELECT 1 FROM med_supply_folders WHERE parent_id IS ? AND name=? AND id!=?',
                        (parent, name, fid or 0)).fetchone():
            raise ValueError('Такая папка уже есть.')
        if fid:
            conn.execute('UPDATE med_supply_folders SET name=?,version=version+1 WHERE id=?', (name, fid))
        else:
            fid = conn.execute('INSERT INTO med_supply_folders(parent_id,name) VALUES(?,?)', (parent, name)).lastrowid
        after = dict(conn.execute('SELECT * FROM med_supply_folders WHERE id=?', (fid,)).fetchone())
        conn.execute('INSERT INTO med_supply_folder_history(folder_id,actor_id,changed_at,before_json,after_json) VALUES(?,?,?,?,?)',
                     (fid, actor, datetime.now(ZoneInfo('UTC')).isoformat(),
                      json.dumps(before, ensure_ascii=False) if before else None, json.dumps(after, ensure_ascii=False)))
    return fid


def import_rows(conn):
    result = []
    assigned = {(r['import_id'], r['source_row']): dict(r) for r in conn.execute('SELECT * FROM med_supply_daily_items')}
    for imp in conn.execute('SELECT id,snapshot_date,filename,payload_json FROM med_supply_stock_imports ORDER BY id'):
        for source in json.loads(imp['payload_json'])['rows']:
            result.append({**source, 'import_id': imp['id'], 'date': imp['snapshot_date'],
                           'filename': imp['filename'], 'assigned': assigned.get((imp['id'], source['source_row']))})
    return sorted(result, key=lambda r: (Decimal(r['end_qty']) == 0 or Decimal(r['end_value']) == 0,
                                        r['import_id'], r['source_row']))


def assign(conn, data, actor):
    import medical_supplies as med
    impid, source_row = ident(data.get('import_id')), ident(data.get('source_row'))
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        folder = folder_choice(conn, data)
        old = conn.execute('SELECT * FROM med_supply_daily_items WHERE import_id=? AND source_row=?', (impid, source_row)).fetchone()
        version = ident(data.get('version'))
        if version != (old['version'] if old else 0):
            raise Conflict('Позицию уже распределили или изменили. Обновите страницу.')
        if old:
            iid = old['item_id']
            conn.execute('UPDATE med_supply_daily_items SET folder_id=?,version=version+1 WHERE item_id=?', (folder, iid))
        else:
            imp = conn.execute('SELECT snapshot_date,payload_json FROM med_supply_stock_imports WHERE id=?', (impid,)).fetchone()
            if not imp:
                raise ValueError('Файл не найден.')
            row = next((r for r in json.loads(imp['payload_json'])['rows'] if r['source_row'] == source_row), None)
            if row is None:
                raise ValueError('Строка не найдена.')
            # Source values stay exact in the dated anchor; monthly opening is not fabricated.
            if not row['unit'] or len(row['unit']) > 30:
                raise ValueError('Сначала необходимо уточнить единицу учёта исходной строки.')
            if len(row['name']) > 200:
                raise ValueError('Название длиннее 200 символов. Требуется решение по названию.')
            iid = conn.execute('INSERT INTO med_supply_items(name,unit,opening_month,opening_qty,pack_qty,pack_price,min_stock,excluded) VALUES(?,?,?,0,1000,NULL,0,0)',
                               (row['name'], row['unit'], imp['snapshot_date'][:7])).lastrowid
            conn.execute('INSERT INTO med_supply_daily_items(item_id,import_id,source_row,folder_id,opening_date,opening_qty,opening_value,pieces_per_unit) VALUES(?,?,?,?,?,?,?,?)',
                         (iid, impid, source_row, folder, imp['snapshot_date'], row['end_qty'], row['end_value'],
                          '1' if row['unit'] == 'шт' else None))
        after = dict(conn.execute('SELECT * FROM med_supply_daily_items WHERE item_id=?', (iid,)).fetchone())
        med.history(conn, iid, None, 'classify', actor, dict(old) if old else None, after)
    return iid


def save_day(conn, data, actor, allowed):
    import medical_supplies as med
    iid, version = ident(data.get('item_id')), ident(data.get('version'))
    kind = data.get('kind')
    if kind not in ('in', 'out') or kind not in allowed:
        raise PermissionError('Нет права изменять этот вид движения.')
    try:
        day = date.fromisoformat(data.get('day', ''))
        if day.isoformat() != data['day'] or day > today():
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Укажите дату не позднее сегодняшней.') from None
    amount = num(data.get('qty'))
    cost = num(data.get('value'), optional=True) if kind == 'in' else None
    if kind == 'in' and amount == 0 and cost not in (None, Decimal(0)):
        raise ValueError('Стоимость прихода без количества недопустима.')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        item = conn.execute('SELECT * FROM med_supply_daily_items WHERE item_id=?', (iid,)).fetchone()
        if not item or day.isoformat() <= item['opening_date']:
            raise ValueError('Движение вводится со следующего дня после исходного остатка.')
        old = conn.execute('SELECT * FROM med_supply_days WHERE item_id=? AND day=?', (iid, day.isoformat())).fetchone()
        if version != (old['version'] if old else 0):
            raise Conflict('Данные дня уже изменились. Обновите страницу.')
        row = dict(old) if old else dict(qty_in='0', value_in=None, qty_out='0', in_known=0, out_known=0)
        row['qty_'+kind], row[kind+'_known'] = str(amount), 1
        if kind == 'in':
            row['value_in'] = str(cost) if cost is not None else ('0' if amount == 0 else None)
        conn.execute('INSERT INTO med_supply_days VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(item_id,day) DO UPDATE SET qty_in=excluded.qty_in,value_in=excluded.value_in,qty_out=excluded.qty_out,in_known=excluded.in_known,out_known=excluded.out_known,version=excluded.version',
                     (iid, day.isoformat(), row['qty_in'], row['value_in'], row['qty_out'], row['in_known'], row['out_known'], version+1))
        after = dict(conn.execute('SELECT * FROM med_supply_days WHERE item_id=? AND day=?', (iid, day.isoformat())).fetchone())
        med.history(conn, iid, day.isoformat()[:7], 'day_'+kind, actor, dict(old) if old else None, after)


def save_pieces(conn, data, actor):
    import medical_supplies as med
    iid = ident(data.get('item_id'))
    n = num(data.get('pieces'), optional=True)
    if n == 0:
        raise ValueError('Количество штук в единице должно быть больше нуля.')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT d.*,i.unit FROM med_supply_daily_items d JOIN med_supply_items i ON i.id=d.item_id WHERE item_id=?', (iid,)).fetchone()
        if not old:
            raise ValueError('Позиция не найдена.')
        if ident(data.get('version')) != old['version']:
            raise Conflict('Карточку уже изменили. Обновите страницу.')
        if old['unit'] == 'шт' and n != 1:
            raise ValueError('Для единицы «шт» коэффициент равен 1.')
        conn.execute('UPDATE med_supply_daily_items SET pieces_per_unit=?,version=version+1 WHERE item_id=?', (str(n) if n else None, iid))
        med.history(conn, iid, None, 'pieces', actor, dict(old), {'pieces_per_unit': str(n) if n else None})


def calculate(item, moves, start, end):
    """Chronological weighted cost; missing prices cannot silently become zero."""
    anchor = date.fromisoformat(item['opening_date'])
    quantity, value = Decimal(item['opening_qty']), Decimal(item['opening_value'])
    out_total, in_total, spent = Decimal(0), Decimal(0), Decimal(0)
    start_qty = quantity if anchor < start else None
    historical_gap = anchor >= start
    cells = {}
    for move in moves:
        day = date.fromisoformat(move['day'])
        if day > end:
            break
        incoming, outgoing = Decimal(move['qty_in']), Decimal(move['qty_out'])
        if start_qty is None and day >= start and anchor < start:
            start_qty = quantity
        if incoming:
            value = value + Decimal(move['value_in']) if value is not None and move['value_in'] is not None else None
            quantity += incoming
        rate = value / quantity if value is not None and quantity > 0 else None
        cost = outgoing * rate if rate is not None else (Decimal(0) if outgoing == 0 else None)
        quantity -= outgoing
        value = value - cost if value is not None and cost is not None else None
        # An overdraw does not invent a purchase price for future inventory.
        if quantity < 0:
            value = None
        if day < start:
            start_qty = quantity
        else:
            in_total += incoming
            out_total += outgoing
            spent = spent + cost if spent is not None and cost is not None else None
            cells[day.day] = move
    rate = value / quantity if value is not None and quantity > 0 else None
    pieces = Decimal(item['pieces_per_unit']) if item['pieces_per_unit'] else None
    visible = anchor <= end and start <= end
    return {**item, 'start': start_qty if visible else None, 'end': quantity if visible else None,
            'end_value': value if visible else None, 'in_total': in_total if visible else None,
            'out_total': out_total if visible else None, 'spent': spent if visible else None,
            'history_gap': historical_gap, 'unit_price': rate if visible else None,
            'piece_price': rate / pieces if visible and rate is not None and pieces else None,
            'cells': cells, 'anchor_visible': visible}


def snapshot(conn, period, until=None):
    import medical_supplies as med
    period = med.month(period)
    year, month = map(int, period.split('-'))
    start = date(year, month, 1)
    end = min(date(year, month, calendar.monthrange(year, month)[1]), until or today())
    own = not conn.in_transaction
    if own:
        conn.execute('BEGIN')
    try:
        items = [dict(r) for r in conn.execute('SELECT i.id,i.name,i.unit,i.pack_qty,i.pack_price,i.min_stock,i.excluded,d.* FROM med_supply_items i JOIN med_supply_daily_items d ON d.item_id=i.id ORDER BY i.id')]
        moves = [dict(r) for r in conn.execute('SELECT * FROM med_supply_days WHERE day<=? ORDER BY item_id,day', (end.isoformat(),))]
    finally:
        if own:
            conn.rollback()
    by_item = {}
    for move in moves:
        by_item.setdefault(move['item_id'], []).append(move)
    rows = []
    for item in items:
        moves_for_item = by_item.get(item['item_id'], [])
        r = calculate(item, moves_for_item, start, end)
        expected = max((end-date.fromisoformat(item['opening_date'])).days, 0)
        known = sum(bool(m['in_known'] and m['out_known']) for m in moves_for_item)
        r['missing_days'] = expected-known
        r['complete'] = r['anchor_visible'] and known == expected
        rows.append(r)
    return sorted(rows, key=lambda r: (r['end'] == 0 or r['end_value'] == 0, r['name'].casefold(), r['id']))


def totals(rows):
    result = {'count': len(rows), 'history_gap': any(r['history_gap'] for r in rows)}
    for field in ('spent', 'end_value'):
        result[field] = sum((r[field] for r in rows if r[field] is not None), Decimal(0)) if all(r[field] is not None for r in rows) else None
    return result


def orders(conn, period):
    from decimal import ROUND_CEILING
    result=[]
    for r in snapshot(conn, period):
        minimum=Decimal(r['min_stock'])/1000
        pack=Decimal(r['pack_qty'])/1000
        if r['excluded'] or minimum==0:
            continue
        packs = int((max(minimum-r['end'],Decimal(0))/pack).to_integral_value(rounding=ROUND_CEILING)) if r['complete'] else None
        rate=Decimal(r['pack_price'])/100 if r['pack_price'] is not None else (r['unit_price']*pack if r['unit_price'] is not None else None)
        if packs is None or packs:
            result.append({**r,'minimum':minimum,'pack':pack,'packs':packs,'order_value':rate*packs if rate is not None and packs is not None else None})
    return result


def tree(conn, rows):
    fs = folders(conn)
    result = []
    for root in (f for f in fs if f['parent_id'] is None):
        children = [f for f in fs if f['parent_id'] == root['id']]
        ids = {root['id'], *(f['id'] for f in children)}
        own = [r for r in rows if r['folder_id'] == root['id']]
        all_rows = [r for r in rows if r['folder_id'] in ids]
        result.append({**root, 'rows': own, 'total': totals(all_rows), 'children': [
            {**f, 'rows': [r for r in rows if r['folder_id'] == f['id']],
             'total': totals([r for r in rows if r['folder_id'] == f['id']])} for f in children]})
    return result


def render(conn, rights, csrf, period, error=None, status=200):
    if not ready(conn):
        return 'Дневной учёт ещё не подготовлен: требуется установка схемы.', 503
    rows = snapshot(conn, period)
    imported = import_rows(conn)
    imports=[dict(r) for r in conn.execute('SELECT id,snapshot_date,filename FROM med_supply_stock_imports ORDER BY snapshot_date DESC,id DESC')]
    selected_import=ident(request.args['import_id']) if request.args.get('import_id') else (imports[0]['id'] if imports else None)
    if selected_import is not None and selected_import not in {r['id'] for r in imports}:
        abort(404)
    imported=[r for r in imported if r['import_id']==selected_import]
    source_summaries = []
    for record in conn.execute('SELECT id,snapshot_date,payload_json FROM med_supply_stock_imports WHERE id=?',(selected_import,)):
        payload=json.loads(record['payload_json'])
        source_summaries.append({'date':date.fromisoformat(record['snapshot_date']).strftime('%d.%m.%Y'),
                                'count':len(payload['rows']),'value':payload['total'],'issues':payload['issue_count']})
    show_assigned = request.args.get('assigned') == '1'
    q = request.args.get('q', '').strip()[:200]
    pending = [r for r in imported if (show_assigned or not r['assigned']) and
               (not q or q.casefold() in (r['name']+' '+r['group']).casefold())]
    if request.args.get('issues') == '1':
        pending = [r for r in pending if r['issues']]
    fs = folders(conn)
    folder_by_id = {f['id']: f for f in fs}
    for row in pending:
        selected = folder_by_id.get(row['assigned']['folder_id']) if row['assigned'] else None
        row['root'] = selected['parent_id'] or selected['id'] if selected else None
        row['sub'] = selected['id'] if selected and selected['parent_id'] else None
    import medical_supplies as med
    legacy = med.snapshot(conn, period)
    year, m = map(int, period.split('-'))
    days = [{'day': n, 'date': date(year, m, n).isoformat(), 'weekday': ['пн','вт','ср','чт','пт','сб','вс'][date(year,m,n).weekday()]} for n in range(1,calendar.monthrange(year,m)[1]+1)]
    prev_month = date(year-1,12,1) if m == 1 else date(year,m-1,1)
    next_month = date(year+1,1,1) if m == 12 else date(year,m+1,1)
    return render_template('medical_supplies_daily.html', title='Медрасходка',
        month=period, rows=rows, groups=tree(conn,rows), total=totals(rows),
        folders=fs, pending=pending, sources=source_summaries, imports=imports, selected_import=selected_import,
        pending_count=sum(not r['assigned'] for r in imported),
        show_assigned=show_assigned, q=q, issues_only=request.args.get('issues')=='1',
        rights=rights, can_import={'catalog','purchase','in','out'} <= rights, csrf=csrf, days=days, today=today().isoformat(),
        prev_month=prev_month.strftime('%Y-%m'), next_month=next_month.strftime('%Y-%m'),
        kind='in' if request.args.get('kind')=='in' else 'out',
        error=error, saved=request.args.get('saved')=='1', already=request.args.get('saved')=='already', legacy=legacy,
        money=stock.money, qty=lambda v:'—' if v is None else stock.quantity(v), legacy_qty=med.display), status


def export(conn, period):
    rows = snapshot(conn, period)
    book = Workbook()
    sheet = book.active
    sheet.title = 'Движение'
    sheet.append(['Папка', 'Подпапка', 'Материал', 'Ед.', 'Дата исходного остатка', 'Цена за ед.', 'Цена за шт.',
                  'Остаток на 1-е', 'Приход', 'Расход', 'Потрачено, ₽', 'Остаток', 'Остаток, ₽', 'Полнота'])
    for root in tree(conn, rows):
        for sub, items in [('', root['rows'])]+[(c['name'],c['rows']) for c in root['children']]:
            for r in items:
                sheet.append([root['name'], sub, r['name'], r['unit'], r['opening_date'],
                    *[float(r[k]) if r[k] is not None else None for k in ('unit_price','piece_price','start','in_total','out_total','spent','end','end_value')],
                    'Расход до исходного остатка неизвестен' if r['history_gap'] else 'По внесённому движению'])
    daily = book.create_sheet('По дням')
    daily.append(['Материал','Дата','Приход','Стоимость прихода','Расход'])
    for r in rows:
        for cell in r['cells'].values():
            daily.append([r['name'],cell['day'],float(cell['qty_in']) if cell['in_known'] else None,
                          float(cell['value_in']) if cell['value_in'] is not None else None,
                          float(cell['qty_out']) if cell['out_known'] else None])
    import medical_supplies as med
    from openpyxl import load_workbook
    legacy=med.snapshot(conn,period)
    if legacy:
        old=load_workbook(med.workbook(legacy,period))
        for old_sheet,title in zip(old.worksheets,('Месячные итоги','К заказу — месячный учёт')):
            ws=book.create_sheet(title)
            for line in old_sheet.iter_rows(values_only=True):ws.append(list(line))
        old.close()
    order_rows=orders(conn,period)
    if order_rows:
        ws=book.create_sheet('К заказу — дневной учёт')
        ws.append(['Материал','Остаток','Минимум','В упаковке','Заказать упаковок','Сумма, ₽','Подтвердить дней'])
        for r in order_rows:
            ws.append([r['name'],float(r['end']) if r['end'] is not None else None,float(r['minimum']),float(r['pack']),r['packs'],
                       float(r['order_value']) if r['order_value'] is not None else None,r['missing_days']])
    for ws in book:
        ws.freeze_panes='C2'
        ws.auto_filter.ref=ws.dimensions
        for line in ws:
            for cell in line:
                if isinstance(cell.value,str):cell.data_type='s'
        for column in ws.columns:ws.column_dimensions[column[0].column_letter].width=22
    out=io.BytesIO();book.save(out);out.seek(0)
    return out


def register(app, db, permission_required, require_csrf, csrf_token, rights):
    @app.post('/accounting/medical-supplies/daily/<action>')
    @permission_required('accounting')
    def medical_supplies_daily_save(action):
        allowed=rights()
        needed={'assign':{'catalog'},'folder':{'catalog'},'pieces':{'purchase'},'day':{'in','out'}}
        if action not in needed:abort(404)
        if not allowed & needed[action]:abort(403)
        require_csrf()
        import medical_supplies as med
        try:
            period=med.month(request.form.get('month'))
            if not ready(db()):return 'Требуется подготовка схемы.',503
            if action=='assign':assign(db(),request.form,g.user['id'])
            elif action=='folder':save_folder(db(),request.form,g.user['id'])
            elif action=='pieces':save_pieces(db(),request.form,g.user['id'])
            else:save_day(db(),request.form,g.user['id'],allowed)
        except PermissionError:
            abort(403)
        except (ValueError, Conflict) as exc:
            # Do not silently discard a failed save or move on to another item.
            return render_template('medical_supplies_error.html', title='Медрасходка', error=str(exc),
                                   back=url_for('medical_supplies', month=request.form.get('month',''))), 409 if isinstance(exc,Conflict) else 400
        return redirect(url_for('medical_supplies',month=period,saved='1',kind=request.form.get('kind','out'),
                                **({'import_id':request.form['import_id']} if action=='assign' else {})),code=303)

    @app.get('/accounting/medical-supplies/daily/export')
    @permission_required('accounting')
    def medical_supplies_daily_export():
        import medical_supplies as med
        try:period=med.month(request.args.get('month'))
        except ValueError as exc:return str(exc),400
        if not ready(db()):abort(503)
        return send_file(export(db(),period), as_attachment=True,download_name=f'medical-supplies-daily-{period}.xlsx',
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
