"""Order labor allocations; never post calculated compensation to paid payroll."""
import json
import re

from medical_supplies import Conflict, integer, number

SCHEMA = """CREATE TABLE IF NOT EXISTS ztl_labor_splits (
 order_id INTEGER PRIMARY KEY REFERENCES ztl_orders(id),
 total INTEGER NOT NULL CHECK(total>=0), entries TEXT NOT NULL
)"""
TECHNICIANS = (
    'Казанцев Леонид Евгеньевич',
    'Палунин Владислав Андреевич',
    'Можарова Екатерина Сергеевна',
    'Мосина Анастасия Павловна',
    'Башлыкова Анастасия Александровна',
)


def init_schema(conn):
    conn.execute(SCHEMA)


def seed_technicians(conn, actor):
    """Explicit owner-approved import, not called by GET or schema preparation."""
    import ztl
    normalize = lambda s: ' '.join(s.casefold().replace('ё', 'е').split())
    result = []
    conn.execute('SAVEPOINT ztl_technician_import')
    try:
        rows = list(conn.execute("SELECT id,name FROM ztl_names WHERE kind='employee'"))
        for name in TECHNICIANS:
            matches = [r for r in rows if normalize(r['name']) == normalize(name)]
            if len(matches) > 1:
                raise Conflict('В справочнике несколько записей: ' + name + '. Сначала разберите дубли.')
            # An abbreviated name might be this employee; do not guess or create a duplicate.
            surname, first, middle = normalize(name).split()
            for row in rows:
                parts = re.findall(r'[а-я]+', normalize(row['name']))
                if (len(parts) == 3 and parts[0] == surname and
                    parts[1] in (first, first[0]) and parts[2] in (middle, middle[0]) and
                    normalize(row['name']) != normalize(name)):
                    raise Conflict('Уточните существующую запись «' + row['name'] + '» перед добавлением «' + name + '».')
            iid = matches[0]['id'] if matches else ztl.catalog_entry(conn, 'employee', name, actor)
            result.append(dict(id=iid, name=name, created=not bool(matches)))
        conn.execute('RELEASE ztl_technician_import')
    except BaseException:
        conn.execute('ROLLBACK TO ztl_technician_import')
        conn.execute('RELEASE ztl_technician_import')
        raise
    return result


def calculate(total, rules):
    """Integer kopecks / hundredths of a percent; largest remainder for percentage cents."""
    entries = []
    for rule in rules:
        tid, mode, value = rule['technician_id'], rule['mode'], rule['value']
        if mode not in ('rub', 'percent') or not isinstance(value, int) or value < 0:
            raise ValueError('Укажите оплату в рублях или процентах.')
        if mode == 'percent' and value > 10000:
            raise ValueError('Доля техника не может превышать 100%.')
        entries.append(dict(technician_id=tid, mode=mode, value=value,
                            amount=value if mode == 'rub' else total * value // 10000))
    if len({e['technician_id'] for e in entries}) != len(entries):
        raise ValueError('Один техник не может быть указан дважды.')
    percent = [e for e in entries if e['mode'] == 'percent']
    percent_sum = sum(e['value'] for e in percent)
    fixed = sum(e['amount'] for e in entries if e['mode'] == 'rub')
    # Check the exact, unrounded demand as well as the displayed sum.
    if percent_sum > 10000 or fixed * 10000 + total * percent_sum > total * 10000:
        raise ValueError('Распределение превышает общую оплату работы по заказу.')
    target = (total * percent_sum + 5000) // 10000
    extra = target - sum(e['amount'] for e in percent)
    for entry in sorted(percent, key=lambda e: (-(total * e['value'] % 10000), e['technician_id']))[:extra]:
        entry['amount'] += 1
    return sorted(entries, key=lambda e: e['technician_id'])


def allocation(conn, production):
    if not production:
        return dict(total=None, entries=[], assigned=0, remaining=None, complete=False, explicit=False)
    ids = json.loads(production['technicians'])
    total = production['pieces'] * production['technician_fee'] if production['card_version'] else None
    saved = conn.execute('SELECT * FROM ztl_labor_splits WHERE order_id=?', (production['order_id'],)).fetchone()
    entries = json.loads(saved['entries']) if saved else []
    if not saved and total is not None and len(ids) == 1:
        entries = calculate(total, [dict(technician_id=ids[0], mode='percent', value=10000)])
    assigned = sum(e['amount'] for e in entries)
    return dict(total=total, entries=entries, assigned=assigned,
                remaining=total-assigned if total is not None else None,
                complete=total is not None and assigned == total and {e['technician_id'] for e in entries} == set(ids),
                explicit=saved is not None)


def save_for_order(conn, old_production, production, data, actor, can_payroll):
    """Inside the order transaction; protects existing allocations from stale/legacy forms."""
    import ztl
    submitted = 'labor_present' in data
    fields = [k for k in data if k.startswith(('labor_mode_', 'labor_value_'))]
    if (submitted or fields) and not can_payroll:
        raise PermissionError()
    if fields and not submitted:
        raise ValueError('Откройте форму распределения оплаты заново.')
    old = allocation(conn, old_production)
    ids = json.loads(production['technicians']) if production else []
    total = production['pieces'] * production['technician_fee'] if production and production['card_version'] else None
    old_ids = json.loads(old_production['technicians']) if old_production else []
    changed = old['total'] != total or set(old_ids) != set(ids)
    if not submitted:
        if old['explicit'] and changed:
            raise ValueError('Изменились исполнители или общая оплата. Проверьте распределение с правом редактирования ФОТ.')
        return
    if total is None:
        if any(str(data.get(k, '')).strip() for k in fields if k.startswith('labor_value_')):
            raise ValueError('У исторического заказа нет сохранённой расценки. Распределение не рассчитано.')
        return
    for key in fields:
        suffix = key.removeprefix('labor_mode_').removeprefix('labor_value_')
        if not suffix.isdigit() or int(suffix) not in ids:
            raise ValueError('Распределение содержит техника, не выбранного в заказе.')
        if hasattr(data, 'getlist') and len(data.getlist(key)) != 1:
            raise ValueError('Повторяющееся поле распределения.')
    rules = []
    for tid in ids:
        raw = str(data.get('labor_value_' + str(tid), '')).strip()
        if not raw:
            continue  # Pending decision; distinct from an explicit zero allocation.
        mode = data.get('labor_mode_' + str(tid))
        if mode not in ('rub', 'percent'):
            raise ValueError('Выберите рубли или проценты для каждого заполненного значения.')
        rules.append(dict(technician_id=tid, mode=mode, value=number(raw, 100)))
    entries = calculate(total, rules)
    current = dict(total=total, entries=entries)
    if old['explicit'] and old['total'] == total and old['entries'] == entries:
        return
    conn.execute('INSERT INTO ztl_labor_splits(order_id,total,entries) VALUES(?,?,?) '
                 'ON CONFLICT(order_id) DO UPDATE SET total=excluded.total,entries=excluded.entries',
                 (production['order_id'], total, json.dumps(entries, ensure_ascii=False, sort_keys=True)))
    ztl.log(conn, 'Распределение оплаты ЗТЛ', production['order_id'], 'Сохранение', actor, old, current)


def save_allocation(conn, data, actor):
    import ztl
    import ztl_control
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        oid = integer(data.get('order_id'))
        order = ztl.row_by_id(conn, 'ztl_orders', oid)
        ztl.version(order, data)
        p = ztl_control.production(conn, oid)
        if not p or not p['card_version']:
            raise ValueError('У заказа нет сохранённой расценки для распределения.')
        if 'labor_present' not in data:
            raise ValueError('Откройте форму распределения оплаты заново.')
        save_for_order(conn, p, p, data, actor, True)
        conn.execute('UPDATE ztl_orders SET version=version+1 WHERE id=?', (oid,))
