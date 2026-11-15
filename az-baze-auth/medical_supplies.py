"""Single-stock monthly supplies ledger. No financial-report integration or seeded items."""
import io
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from flask import abort, g, redirect, render_template, request, send_file, url_for
from openpyxl import Workbook

SCHEMA = """
CREATE TABLE IF NOT EXISTS med_supply_items (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, unit TEXT NOT NULL,
 opening_month TEXT NOT NULL, opening_qty INTEGER NOT NULL CHECK(opening_qty>=0),
 pack_qty INTEGER NOT NULL CHECK(pack_qty>0), pack_price INTEGER CHECK(pack_price>=0),
 min_stock INTEGER NOT NULL CHECK(min_stock>=0), excluded INTEGER NOT NULL CHECK(excluded IN (0,1)),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0)
);
CREATE TABLE IF NOT EXISTS med_supply_periods (
 item_id INTEGER NOT NULL REFERENCES med_supply_items(id), month TEXT NOT NULL,
 qty_in INTEGER NOT NULL CHECK(qty_in>=0), qty_out INTEGER NOT NULL CHECK(qty_out>=0),
 version INTEGER NOT NULL CHECK(version>0), PRIMARY KEY(item_id,month)
);
CREATE TABLE IF NOT EXISTS med_supply_history (
 id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES med_supply_items(id),
 month TEXT, action TEXT NOT NULL, actor_id INTEGER NOT NULL,
 changed_at TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL
);
"""


class Conflict(ValueError):
    pass


def init_schema(conn):
    conn.executescript(SCHEMA)


def month(value):
    value = str(value or '')
    if not re.fullmatch(r'\d{4}-\d{2}', value):
        raise ValueError('Укажите месяц в формате ГГГГ-ММ.')
    y, m = map(int, value.split('-'))
    if not 1900 <= y <= 9998 or not 1 <= m <= 12:
        raise ValueError('Некорректный месяц.')
    return value


def number(value, scale=1000, optional=False):
    if optional and (value is None or value == ''):
        return None
    try:
        if isinstance(value, bool) or value is None or str(value).strip() == '':
            raise ValueError()
        n = Decimal(str(value).strip().replace(',', '.'))
        if not n.is_finite() or n < 0 or n > 1000000000:
            raise ValueError()
        scaled = n * scale
        if scaled != scaled.to_integral_value():
            raise ValueError()
        return int(scaled)
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError('Введите неотрицательное число: количество — до 3 знаков после запятой, цена — до 2.') from None


def integer(value):
    if not re.fullmatch(r'\d{1,12}', str(value or '')):
        raise ValueError('Некорректный номер записи или версии.')
    return int(value)


def display(value, scale=1000):
    if value is None:
        return ''
    return format(Decimal(value) / scale, 'f').rstrip('0').rstrip('.') if value % scale else str(value // scale)


def text(value, limit):
    s = str(value or '').strip()
    if not s or len(s) > limit:
        raise ValueError(f'Заполните текстовое поле (до {limit} символов).')
    return s


def history(conn, iid, period, action, actor, before, after):
    conn.execute('INSERT INTO med_supply_history(item_id,month,action,actor_id,changed_at,before_json,after_json) VALUES(?,?,?,?,?,?,?)',
                 (iid, period, action, actor, datetime.now(ZoneInfo('UTC')).isoformat(),
                  json.dumps(before, ensure_ascii=False) if before is not None else None,
                  json.dumps(after, ensure_ascii=False)))


def save_item(conn, data, actor):
    values = dict(name=text(data.get('name'), 200), unit=text(data.get('unit'), 30),
                  pack_qty=number(data.get('pack_qty')), pack_price=number(data.get('pack_price'), 100, True),
                  min_stock=number(data.get('min_stock')), excluded=int(data.get('excluded') == '1'))
    if values['pack_qty'] == 0:
        raise ValueError('Количество в упаковке должно быть больше нуля.')
    iid = integer(data['id']) if data.get('id') else None
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        before = None
        if iid:
            row = conn.execute('SELECT * FROM med_supply_items WHERE id=?', (iid,)).fetchone()
            if row is None:
                raise ValueError('Позиция не найдена.')
            before = dict(row)
            if integer(data.get('version')) != row['version']:
                raise Conflict('Карточку уже изменили. Обновите страницу и повторите ввод.')
            if values['unit'] != row['unit']:
                raise ValueError('Единица учёта сохраняется на всю историю. Для другой единицы создайте новую позицию.')
            conn.execute('UPDATE med_supply_items SET name=:name,pack_qty=:pack_qty,pack_price=:pack_price,min_stock=:min_stock,excluded=:excluded,version=version+1 WHERE id=:id', {**values, 'id': iid})
        else:
            values.update(opening_month=month(data.get('opening_month')), opening_qty=number(data.get('opening_qty')))
            iid = conn.execute('INSERT INTO med_supply_items(name,unit,pack_qty,pack_price,min_stock,excluded,opening_month,opening_qty) VALUES(:name,:unit,:pack_qty,:pack_price,:min_stock,:excluded,:opening_month,:opening_qty)', values).lastrowid
        after = dict(conn.execute('SELECT * FROM med_supply_items WHERE id=?', (iid,)).fetchone())
        history(conn, iid, None, 'item_update' if before else 'item_create', actor, before, after)
    return iid


def save_period(conn, data, actor):
    iid, period = integer(data.get('item_id')), month(data.get('month'))
    qty_in, qty_out, version = number(data.get('qty_in')), number(data.get('qty_out')), integer(data.get('version'))
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        item = conn.execute('SELECT * FROM med_supply_items WHERE id=?', (iid,)).fetchone()
        if item is None or period < item['opening_month']:
            raise ValueError('Период раньше начала учёта или позиция не найдена.')
        old = conn.execute('SELECT * FROM med_supply_periods WHERE item_id=? AND month=?', (iid, period)).fetchone()
        if version != (old['version'] if old else 0):
            raise Conflict('Этот месяц уже изменили. Обновите страницу и повторите ввод.')
        conn.execute('INSERT INTO med_supply_periods VALUES(?,?,?,?,?) ON CONFLICT(item_id,month) DO UPDATE SET qty_in=excluded.qty_in,qty_out=excluded.qty_out,version=excluded.version',
                     (iid, period, qty_in, qty_out, version + 1))
        history(conn, iid, period, 'month_total', actor, dict(old) if old else None,
                dict(qty_in=qty_in, qty_out=qty_out, version=version + 1))


def month_index(value):
    y, m = map(int, value.split('-'))
    return y * 12 + m


def snapshot(conn, period):
    period = month(period)
    # Read one coherent snapshot even if another user saves between SELECTs.
    own_transaction = not conn.in_transaction
    if own_transaction:
        conn.execute('BEGIN')
    try:
        items = [dict(r) for r in conn.execute('SELECT * FROM med_supply_items ORDER BY name COLLATE NOCASE,id')]
        moves = [dict(r) for r in conn.execute('SELECT * FROM med_supply_periods WHERE month<=? ORDER BY month', (period,))]
    finally:
        if own_transaction:
            conn.rollback()
    grouped = {}
    for move in moves:
        grouped.setdefault(move['item_id'], []).append(move)
    result = []
    for item in items:
        if item['opening_month'] > period:
            continue
        rows = grouped.get(item['id'], [])
        current = next((r for r in rows if r['month'] == period), None)
        before = [r for r in rows if r['month'] < period]
        start = item['opening_qty'] + sum(r['qty_in'] - r['qty_out'] for r in before)
        end = start + ((current['qty_in'] - current['qty_out']) if current else 0)
        expected = month_index(period) - month_index(item['opening_month']) + 1
        complete = len(rows) == expected
        need = max(item['min_stock'] - end, 0)
        packs = (need + item['pack_qty'] - 1) // item['pack_qty'] if complete and not item['excluded'] else None
        result.append({**item, 'start': start, 'end': end, 'complete': complete,
                       'start_complete': len(before) == expected - 1,
                       'missing_months': expected - len(rows), 'current': current,
                       'packs': packs, 'order_cost': None if packs is None or item['pack_price'] is None else packs * item['pack_price']})
    return result


def workbook(rows, period):
    book = Workbook()
    sheet = book.active
    sheet.title = 'Движение'
    sheet.append(['Месяц', 'Материал', 'Единица', 'Начало (известно)', 'Приход', 'Расход', 'Конец (известно)', 'Покрытие'])
    for r in rows:
        current = r['current']
        sheet.append([period, r['name'], r['unit'], r['start']/1000,
                      current['qty_in']/1000 if current else None, current['qty_out']/1000 if current else None,
                      r['end']/1000, 'Полные данные' if r['complete'] else 'Неполные данные'])
    order = book.create_sheet('К заказу')
    order.append(['Материал', 'Единица', 'Упаковок', 'Ориентировочная сумма, ₽', 'Статус'])
    for r in rows:
        if r['excluded']:
            continue
        if not r['complete'] or r['packs']:
            order.append([r['name'], r['unit'], r['packs'], None if r['order_cost'] is None else r['order_cost']/100,
                          'Неполные остатки' if not r['complete'] else ('Цена не задана' if r['pack_price'] is None else 'Рассчитано')])
    for ws in book:
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = ws.dimensions
        for row in ws:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'  # Names starting with '=' remain text, never Excel formulas.
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = 24
    out = io.BytesIO()
    book.save(out)
    out.seek(0)
    return out


def register(app, db, permission_required, require_csrf, csrf_token, user_permissions):
    def editable():
        return 'medical_supplies_edit' in user_permissions(g.user)

    def page(error=None, status=200):
        try:
            period = month(request.values.get('month') or datetime.now(ZoneInfo('Asia/Novosibirsk')).strftime('%Y-%m'))
            tab = request.values.get('tab', 'movement')
            if tab not in ('movement', 'order', 'catalog', 'history'):
                abort(400)
            conn = db()
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='med_supply_items'").fetchone():
                return render_template('medical_supplies.html', title='Медрасходка — УЧЕТ', setup_pending=True), 503
            rows = snapshot(conn, period)
            catalog = [dict(r) for r in conn.execute('SELECT * FROM med_supply_items ORDER BY name COLLATE NOCASE,id')]
            events = [dict(r) for r in conn.execute('SELECT h.*,i.name,u.full_name AS actor_name FROM med_supply_history h JOIN med_supply_items i ON i.id=h.item_id LEFT JOIN users u ON u.id=h.actor_id ORDER BY h.id DESC LIMIT 100')] if tab == 'history' else []
            for event in events:
                before = json.loads(event['before_json']) if event['before_json'] else None
                after = json.loads(event['after_json'])
                if event['action'] == 'month_total':
                    old = f"приход {display(before['qty_in'])}, расход {display(before['qty_out'])}" if before else 'не внесены'
                    event['summary'] = f"{event['month']}: {old} → приход {display(after['qty_in'])}, расход {display(after['qty_out'])}"
                else:
                    event['summary'] = 'Создание материала' if before is None else 'Изменение карточки материала'

            edit = None
            if request.args.get('edit'):
                if not editable():
                    abort(403)
                edit = next((r for r in catalog if r['id'] == integer(request.args['edit'])), None)
                if edit is None:
                    abort(404)
            return render_template('medical_supplies.html', title='Медрасходка — УЧЕТ', setup_pending=False,
                                   rows=rows, catalog=catalog, events=events, edit=edit, month=period, tab=tab,
                                   editable=editable(), csrf=csrf_token(), fmt=display, error=error,
                                   saved=request.args.get('saved') == '1'), status
        except ValueError as exc:
            return str(exc), 400

    app.add_url_rule('/accounting/medical-supplies/', 'medical_supplies', permission_required('accounting')(page))

    @app.post('/accounting/medical-supplies/save/<action>')
    @permission_required('accounting')
    def medical_supplies_save(action):
        if not editable():
            abort(403)
        require_csrf()
        if action not in ('item', 'period'):
            abort(404)
        try:
            period = month(request.form.get('month'))
            (save_item if action == 'item' else save_period)(db(), request.form, g.user['id'])
        except Conflict as exc:
            return page(str(exc), 409)
        except ValueError as exc:
            return page(str(exc), 400)
        return redirect(url_for('medical_supplies', month=period, tab='catalog' if action == 'item' else 'movement', saved='1'), code=303)

    @app.get('/accounting/medical-supplies/export')
    @permission_required('accounting')
    def medical_supplies_export():
        try:
            period = month(request.args.get('month'))
        except ValueError as exc:
            return str(exc), 400
        return send_file(workbook(snapshot(db(), period), period), as_attachment=True,
                         download_name=f'medical-supplies-{period}.xlsx',
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
