"""Dated stock statements. An imported closing balance is not monthly movement."""
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import PurePath
from xml.etree.ElementTree import ParseError
from zoneinfo import ZoneInfo

from flask import abort, g, redirect, render_template, request, url_for
from openpyxl import load_workbook

SCHEMA = '''
CREATE TABLE IF NOT EXISTS med_supply_stock_imports (
 id INTEGER PRIMARY KEY,
 sha256 TEXT NOT NULL UNIQUE,
 filename TEXT NOT NULL,
 snapshot_date TEXT NOT NULL,
 actor_id INTEGER NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 source_xlsx BLOB NOT NULL
);
'''
MAX_BYTES = 1024 * 1024
MAX_ROWS = 5000


def init_schema(conn):
    conn.executescript(SCHEMA)


def ready(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='med_supply_stock_imports'").fetchone())


def has_import(conn):
    return ready(conn) and bool(conn.execute('SELECT 1 FROM med_supply_stock_imports LIMIT 1').fetchone())


def decimal(value, cell):
    try:
        if value is None or isinstance(value, bool):
            raise ValueError()
        n = Decimal(str(value).strip().replace(',', '.'))
        if not n.is_finite() or abs(n) > Decimal('1000000000000'):
            raise ValueError()
        return n
    except (InvalidOperation, ValueError):
        raise ValueError(f'Ячейка {cell}: требуется числовое значение. Файл не загружен.') from None


def quantity(value):
    return format(Decimal(value), 'f').rstrip('0').rstrip('.') if '.' in format(Decimal(value), 'f') else format(Decimal(value), 'f')


def money(value):
    if value is None:
        return '—'
    return format(Decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP), ',.2f').replace(',', '\u00a0').replace('.', ',')


def parse_statement(content):
    """Keep every leaf row; never add group subtotals or merge equal names."""
    if not content or len(content) > MAX_BYTES:
        raise ValueError('Загрузите XLSX размером не больше 1 МБ.')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 200 or sum(e.file_size for e in entries) > 30 * 1024 * 1024:
                raise ValueError('Файл слишком велик после распаковки.')
        wb = load_workbook(io.BytesIO(content), data_only=False, read_only=True, keep_links=False)
    except (zipfile.BadZipFile, OSError, KeyError, ValueError, ParseError) as exc:
        raise ValueError('Не удалось прочитать XLSX: ' + str(exc)) from None
    try:
        if len(wb.worksheets) != 1:
            raise ValueError('Ожидается один лист отчёта «По обороту и остаткам».')
        ws = wb.worksheets[0]
        if not ws.max_row or ws.max_row > MAX_ROWS or ws.max_column != 17:
            raise ValueError('Структура отчёта не распознана: ожидаются 17 колонок и не более 5000 строк.')
        data = list(ws.iter_rows())
        if [data[0][i].value for i in (1,5,9,13)] != ['Начало','Приход','Расход','Конец'] or data[1][0].value != 'Итого':
            raise ValueError('Не найдены заголовки «Начало / Приход / Расход / Конец» и строка «Итого».')
        for row in data:
            if any(c.data_type == 'f' or c.data_type == 'e' for c in row):
                raise ValueError(f'Строка {row[0].row}: формула или ошибка Excel. Нужна выгрузка значений из учётной системы.')
        source_total = decimal(data[1][13].value, 'N2')
        rows, group, groups = [], '', []
        for cells in data[2:]:
            v = [c.value for c in cells]
            if all(x is None or x == '' for x in v):
                continue
            name = str(v[0] or '').strip()
            n = cells[0].row
            if not name or len(name) > 500:
                raise ValueError(f'Строка {n}: отсутствует или слишком длинное название.')
            # Group totals contain money in N, no unit and no detail costs in D/H/L/P.
            if not any(v[i] not in (None,'') for i in (2,6,10,14,3,7,11,15)):
                decimal(v[13], f'N{n}')
                group = name
                groups.append({'source_row': n, 'name': name})
                continue
            unit = str(v[14] or '').strip()
            qty, amount = decimal(v[13], f'N{n}'), decimal(v[15], f'P{n}')
            issues = []
            if not unit:
                issues.append('В «Конце» отсутствует единица учёта. Уточните её.')
            if unit not in ('шт', 'упак') and unit:
                issues.append('Уточните, как единица из файла переводится в штуки.')
            if unit == 'упак':
                issues.append('Уточните количество штук в упаковке для расчёта цены за штуку.')
            if qty == 0:
                issues.append('Нулевой остаток: цену единицы по «Концу» определить нельзя.')
            if amount == 0 and qty != 0:
                issues.append('Стоимость остатка в файле равна 0 ₽. Подтвердите нулевую стоимость или укажите цену.')
            if qty < 0 or amount < 0:
                issues.append('Отрицательный остаток или стоимость: проверьте исходную строку.')
            if 'НЕ ИСПОЛЬЗОВАТЬ' in (name + ' ' + group).upper():
                issues.append('В источнике отмечено «НЕ ИСПОЛЬЗОВАТЬ». Уточните дальнейший учёт.')
            unit_price = amount / qty if qty != 0 else None
            rows.append(dict(source_row=n, name=name, group=group, unit=unit,
                             end_qty=str(qty), end_value=str(amount),
                             unit_price=str(unit_price) if unit_price is not None else None,
                             piece_price=str(unit_price) if unit=='шт' and unit_price is not None else None,
                             issues=issues))
        if not rows:
            raise ValueError('В файле не найдено позиций.')
        names = Counter(r['name'].casefold() for r in rows)
        for row in rows:
            if names[row['name'].casefold()] > 1:
                row['issues'].append('Название повторяется в файле. Строки сохранены отдельно; уточните, одна ли это позиция.')
        total = sum((Decimal(r['end_value']) for r in rows), Decimal(0))
        if abs(total-source_total) > Decimal('.005'):
            raise ValueError(f'Сумма позиций {money(total)} ₽ не совпадает с итогом файла {money(source_total)} ₽. Файл не загружен.')
        return dict(format_version=1, sheet=ws.title, rows=rows, groups=groups,
                    source_total=str(source_total), total=str(total),
                    issue_count=sum(bool(r['issues']) for r in rows))
    finally:
        wb.close()


def save_import(conn, content, filename, snapshot_date, actor):
    try:
        parsed_date = date.fromisoformat(snapshot_date)
        if parsed_date.isoformat() != snapshot_date:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('Укажите дату остатков.') from None
    filename = PurePath(str(filename).replace('\\','/')).name
    if not filename.lower().endswith('.xlsx') or len(filename) > 250:
        raise ValueError('Выберите файл XLSX.')
    # An explicit date in the export filename must agree with the chosen date.
    match = re.search(r'\((\d{4})\.(\d{2})\.(\d{2})\)', filename)
    if match and '-'.join(match.groups()) != snapshot_date:
        raise ValueError('Дата остатков не совпадает с датой в названии файла.')
    payload = parse_statement(content)
    if not ready(conn):
        raise ValueError('Загрузка остатков ещё не установлена. Требуется подготовка таблицы на сервере.')
    digest = hashlib.sha256(content).hexdigest()
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT id,snapshot_date FROM med_supply_stock_imports WHERE sha256=?', (digest,)).fetchone()
        if old:
            if old['snapshot_date'] != snapshot_date:
                raise ValueError('Этот файл уже загружен с другой датой. Повторная запись не создана.')
            return old['id'], False
        iid = conn.execute('INSERT INTO med_supply_stock_imports(sha256,filename,snapshot_date,actor_id,created_at,payload_json,source_xlsx) VALUES(?,?,?,?,?,?,?)',
                           (digest, filename, snapshot_date, actor, datetime.now(ZoneInfo('UTC')).isoformat(),
                            json.dumps(payload, ensure_ascii=False), sqlite3.Binary(content))).lastrowid
    return iid, True


def render_stock(conn, rights, csrf, error=None, status=200):
    imports = [dict(r) for r in conn.execute('SELECT id,filename,snapshot_date,created_at FROM med_supply_stock_imports ORDER BY snapshot_date DESC,id DESC')] if ready(conn) else []
    selected = None
    selected_id = request.args.get('import_id')
    if selected_id and (not selected_id.isdigit() or int(selected_id) not in [r['id'] for r in imports]):
        abort(404)
    if imports:
        selected_id = int(selected_id) if selected_id else imports[0]['id']
        selected = dict(conn.execute('SELECT id,filename,snapshot_date,created_at,payload_json FROM med_supply_stock_imports WHERE id=?', (selected_id,)).fetchone())
        selected.update(json.loads(selected.pop('payload_json')))
    rows = selected['rows'] if selected else []
    q = request.args.get('q', '').strip()[:200]
    issues_only = request.args.get('issues') == '1'
    filtered = [r for r in rows if (not q or q.casefold() in (r['name']+' '+r['group']).casefold()) and (not issues_only or r['issues'])]
    return render_template('medical_supplies_stock.html', title='Медрасходка — остатки из файла',
                           imports=imports, selected=selected, rows=filtered, q=q, issues_only=issues_only,
                           ready=ready(conn), can_import={'catalog','purchase','in','out'} <= rights,
                           csrf=csrf, error=error, money=money, qty=quantity,
                           today=datetime.now(ZoneInfo('Asia/Novosibirsk')).date().isoformat(),
                           saved=request.args.get('saved'), month=request.args.get('month','')), status


def register(app, db, permission_required, require_csrf, csrf_token, rights):
    @app.post('/accounting/medical-supplies/import')
    @permission_required('accounting')
    def medical_supplies_import():
        if not {'catalog','purchase','in','out'} <= rights():
            abort(403)
        require_csrf()
        upload = request.files.get('file')
        try:
            if upload is None:
                raise ValueError('Выберите файл XLSX.')
            iid, created = save_import(db(), upload.stream.read(MAX_BYTES+1), upload.filename,
                                       request.form.get('snapshot_date'), g.user['id'])
        except ValueError as exc:
            return render_stock(db(), rights(), csrf_token(), str(exc), 400)
        return redirect(url_for('medical_supplies', tab='stock', import_id=iid, saved='1' if created else 'already'), code=303)
