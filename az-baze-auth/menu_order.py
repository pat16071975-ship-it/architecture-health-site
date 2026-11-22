"""Shared navigation order. Reorders existing tiles; never grants access or creates links.

Schema preparation is explicit: init_schema(conn), outside web requests.
"""
import hashlib
import json
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from flask import abort, g, redirect, render_template, request, url_for

SCHEMA = """
CREATE TABLE IF NOT EXISTS navigation_order (
    menu TEXT PRIMARY KEY,
    item_order TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version > 0),
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);
"""

# Stable IDs are independent of labels, query periods and staff permissions.
CATALOG = {
    'home': ('Главное меню', [
        ('reports', 'Отчёты', '/reports/'),
        ('knowledge', 'База знаний', '/knowledge/'),
        ('settings', 'Настройка клиник', '/structure/clinics/'),
        ('access', 'Доступы и контакты', '/access-contacts/'),
        ('admin', 'Управление доступом', '/admin'),
        ('surveys', 'Опросы', '/surveys/'),
        ('birthdays', 'Дни рождения сотрудников', '#birthdays'),
        ('uploads', 'Загрузка данных', '/uploads/'),
        ('accounting', 'УЧЕТ', '/accounting/'),
        ('nn', 'Варикоза нет - KZ', '/nn/'),
        ('control', 'Контроль', '/control/'),
    ]),
    'reports': ('Отчёты', [
        ('attention', 'Требует внимания', '/reports/attention/'),
        ('dashboard', 'Дашборд', '/reports/dashboard.html'),
        ('management', 'Управленческий учёт', '/reports/management/'),
        ('finrez', 'Финрез', '/reports/finrez/'),
        ('model', 'Финмодель 2027', '/reports/financial-model/'),
        ('forecast', 'Прогноз роста', '/reports/forecast/'),
        ('services', 'Аналитика услуг', '/reports/services.html'),
        ('transitions', 'Перенаправление', '/reports/transitions.html'),
        ('advertising', 'Рекламный отчёт', '/reports/advertising/'),
    ]),
    'accounting': ('УЧЕТ', [
        ('ztl', 'ЗТЛ', '/accounting/ztl/'),
        ('medical-supplies', 'Медрасходка', '/accounting/medical-supplies/'),
        ('bookkeeping', 'Бухгалтерия', '/accounting/bookkeeping/'),
    ]),
    'ztl': ('УЧЕТ → ЗТЛ', [
        ('orders', 'Учёт заказов', '/accounting/ztl/orders/'),
        ('products', 'Реестр изделий (технологические карты)', '/accounting/ztl/products/'),
        ('receipts', 'Приход', '/accounting/ztl/receipts/'),
        ('payments', 'Реестр оплат', '/accounting/ztl/payments/'),
        ('inventory', 'Инвентаризация', '/accounting/ztl/inventory/'),
        ('payroll', 'ФОТ', '/accounting/ztl/payroll/'),
        ('economics', 'Экономика', '/accounting/ztl/economics/'),
    ]),
    'medical': ('УЧЕТ → Медрасходка', [
        ('movement', 'Движение', '/accounting/medical-supplies/?tab=movement'),
        ('order', 'К заказу', '/accounting/medical-supplies/?tab=order'),
        ('catalog', 'Справочник', '/accounting/medical-supplies/?tab=catalog'),
        ('history', 'История изменений', '/accounting/medical-supplies/?tab=history'),
    ]),
    'access': ('Доступы и контакты', [
        ('personal', 'Личные доступы', '/personal-credentials/'),
        ('service', 'Служебные доступы', '/credentials/'),
        ('contacts', 'Рабочие контакты', '/contacts/'),
    ]),
    'control': ('Контроль', [('ztl', 'ЗТЛ', '/control/ztl/')]),
    'control_ztl': ('Контроль → ЗТЛ', [
        ('events', 'Изменения и события', '/control/ztl/?view=events'),
        ('work', 'Работа и заполненность', '/control/ztl/?view=work'),
    ]),
    'nn': ('Варикоза нет - KZ', [
        ('management', 'Управленческий отчёт', '/nn/management/'),
        ('forecast', 'Прогноз роста', '/nn/forecast/'),
        ('bi', 'BI-аналитика', '/nn/bi/'),
        ('graphics', 'Графические отчеты', '/nn/graphics/'),
        ('reports', 'Отчёты для НН', '/nn/reports/'),
        ('uploads', 'Загрузка данных', '/nn/uploads/'),
        ('personal', 'Личные доступы', '/personal-credentials/'),
    ]),
}


def init_schema(conn):
    # Caller owns transaction/commit. No startup or GET migration.
    conn.execute(SCHEMA)


def ready(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='navigation_order'").fetchone())


def ids(menu):
    return [item[0] for item in CATALOG[menu][1]]


def catalog_version(menu):
    return hashlib.sha256(json.dumps(CATALOG[menu], ensure_ascii=False).encode()).hexdigest()


def state(conn, menu):
    row = conn.execute('SELECT item_order,version FROM navigation_order WHERE menu=?', (menu,)).fetchone() if ready(conn) else None
    order, damaged = None, False
    if row:
        try:
            order = json.loads(row[0])
            if order is not None and (not isinstance(order, list) or any(not isinstance(k, str) for k in order) or len(order) != len(set(order))):
                raise ValueError('invalid order')
        except (TypeError, ValueError):
            order, damaged = None, True
    return dict(order=order, version=row[1] if row else 0, damaged=damaged)


def effective(menu, order):
    base = ids(menu)
    # Retired keys disappear; newly added tiles follow the saved order.
    return [key for key in order or [] if key in base] + [key for key in base if key not in (order or [])]


def write_audit(conn, actor, menu, before, after, action, now):
    conn.execute('INSERT INTO audit_log(actor_user_id,action,details,created_at) VALUES(?,?,?,?)',
                 (actor, ('nn_' if menu in ('home', 'nn') else '') + 'navigation_order_' + action,
                  json.dumps(dict(menu=menu, before=before, after=after), ensure_ascii=False), now))


class OrderError(ValueError):
    status = 400


class Conflict(OrderError):
    status = 409


def save(conn, menu, form, actor, now):
    if not ready(conn):
        raise Conflict('Настройка порядка ещё не подготовлена к работе.')
    if form.get('catalog') != catalog_version(menu):
        raise Conflict('Список разделов изменился. Обновите страницу и повторите настройку.')
    try:
        expected = int(form.get('version', ''))
    except (TypeError, ValueError):
        raise OrderError('Обновите страницу: не указана версия порядка.')
    action = form.get('action')
    if action not in ('save', 'reset'):
        raise OrderError('Неизвестное действие.')
    order = form.getlist('item') if action == 'save' else None
    if order is not None and (len(order) != len(ids(menu)) or set(order) != set(ids(menu))):
        raise OrderError('В списке должны быть все плашки выбранного меню, без повторов.')
    try:
        conn.execute('BEGIN IMMEDIATE')
        previous = state(conn, menu)
        if expected != previous['version']:
            raise Conflict('Порядок уже изменён в другом окне. Обновите страницу.')
        if previous['order'] == order and not previous['damaged']:
            conn.rollback()
            return
        conn.execute('''INSERT INTO navigation_order(menu,item_order,version,updated_by,updated_at)
                        VALUES(?,?,?,?,?) ON CONFLICT(menu) DO UPDATE SET item_order=excluded.item_order,
                        version=excluded.version,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                     (menu, json.dumps(order), expected + 1, actor, now))
        write_audit(conn, actor, menu, previous['order'], order, action, now)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def key_for(menu, href):
    url = urlsplit(href)
    if url.scheme or url.netloc or not href.startswith('/'):
        return None
    path = url.path.rstrip('/')
    query = parse_qs(url.query)
    if menu == 'medical':
        return query.get('tab', ['movement'])[0] if path == '/accounting/medical-supplies' else None
    if menu == 'control_ztl':
        return query.get('view', ['events'])[0] if path == '/control/ztl' else None
    if menu == 'ztl' and path == '/accounting/ztl':
        return query.get('tab', [None])[0]
    return next((key for key, _label, target in CATALOG[menu][1] if urlsplit(target).path.rstrip('/') == path), None)


def container_menu(path, tag, attrs):
    classes = set(attrs.get('class', '').split())
    label = attrs.get('aria-label')
    if path == '/' and tag == 'nav' and 'menu-column' in classes:
        return 'home'
    if path in ('/', '/nn/') and tag == 'nav' and 'nn-menu' in classes:
        return 'nn'
    if path == '/reports/' and tag == 'nav' and label == 'Разделы отчётов':
        return 'reports'
    if path == '/accounting/' and tag == 'nav' and label == 'Разделы учёта':
        return 'accounting'
    if path == '/accounting/ztl/' and tag == 'nav' and label == 'Разделы ЗТЛ':
        return 'ztl'
    if path == '/accounting/medical-supplies/' and tag == 'nav' and label == 'Разделы медрасходки':
        return 'medical'
    if path == '/access-contacts/' and tag == 'div' and 'access-hub-grid' in classes:
        return 'access'
    if path == '/control/' and tag == 'nav' and label == 'Разделы контроля':
        return 'control'
    if path == '/control/ztl/' and tag == 'nav' and label == 'Контроль ЗТЛ':
        return 'control_ztl'
    return None


class TileParser(HTMLParser):
    """Locate raw, direct-child anchor spans. Never serialize user content or alter attrs."""
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, text, path):
        super().__init__(convert_charrefs=False)
        self.text, self.path, self.stack, self.groups = text, path, [], []
        self.lines = [0]
        for line in text.splitlines(keepends=True):
            self.lines.append(self.lines[-1] + len(line))

    def position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        scope = container_menu(self.path, tag, attrs)
        group = dict(menu=scope, tiles=[]) if scope else None
        if group:
            self.groups.append(group)
        tile = None
        if self.stack and self.stack[-1]['group']:
            parent = self.stack[-1]['group']
            key = key_for(parent['menu'], attrs.get('href', '')) if tag == 'a' else None
            if parent['menu'] == 'home' and tag == 'div' and attrs.get('data-section') == 'section4' and 'menu-btn' in attrs.get('class', '').split():
                key = 'birthdays'
            if key in ids(parent['menu']):
                tile = dict(key=key, start=self.position(), end=None)
                parent['tiles'].append(tile)
        if tag not in self.VOID:
            self.stack.append(dict(tag=tag, group=group, tile=tile))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1]['tag'] != tag:
            return
        node = self.stack.pop()
        if node['tile']:
            node['tile']['end'] = self.position() + len('</' + tag + '>')


def reorder(text, path, read_order):
    parser = TileParser(text, path)
    parser.feed(text)
    edits = []
    for group in parser.groups:
        tiles = group['tiles']
        if len(tiles) < 2 or any(t['end'] is None for t in tiles) or len({t['key'] for t in tiles}) != len(tiles):
            continue
        order = read_order(group['menu'])
        if order is None:
            continue
        rank = {key: i for i, key in enumerate(effective(group['menu'], order))}
        sorted_tiles = sorted(tiles, key=lambda t: rank[t['key']])
        for target, source in zip(tiles, sorted_tiles):
            edits.append((target['start'], target['end'], text[source['start']:source['end']]))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def register(app, core):
    @app.route('/structure/clinics/menu-order/', methods=['GET', 'POST'])
    @core.permission_required('structure_manage')
    def clinic_menu_order():
        # The owner controls shared navigation, including the private NN hub.
        if not core.is_owner(g.user):
            abort(403)
        menu = request.args.get('menu', 'home')
        if menu not in CATALOG:
            abort(404)
        conn = core.db()
        error, status = None, 200
        if request.method == 'POST':
            core.require_csrf()
            try:
                save(conn, menu, request.form, g.user['id'], core.iso_now())
                return redirect(url_for('clinic_menu_order', menu=menu, saved='1'), code=303)
            except OrderError as exc:
                error, status = str(exc), exc.status
        current = state(conn, menu)
        entries = {key: dict(key=key, label=label) for key, label, _href in CATALOG[menu][1]}
        return render_template('menu_order.html', menu=menu, menus=CATALOG,
                               items=[entries[key] for key in effective(menu, current['order'])],
                               version=current['version'], catalog=catalog_version(menu),
                               prepared=ready(conn), damaged=current['damaged'],
                               saved=request.args.get('saved') == '1' and request.method == 'GET',
                               error=error, csrf=core.csrf_token()), status

    def apply_order(response):
        if not getattr(g, 'user', None) or response.status_code != 200 or response.mimetype != 'text/html':
            return response
        if request.path not in ('/', '/reports/', '/accounting/', '/accounting/ztl/',
                                '/accounting/medical-supplies/', '/access-contacts/', '/control/', '/control/ztl/', '/nn/'):
            return response
        conn = core.db()
        if not ready(conn):
            return response
        response.direct_passthrough = False
        original = response.get_data(as_text=True)
        rendered = reorder(original, request.path, lambda menu: state(conn, menu)['order'])
        if rendered != original:
            response.set_data(rendered)
            response.headers.pop('ETag', None)
            response.headers['Cache-Control'] = 'no-store'
        return response

    # Flask runs after_request in reverse registration order. Run after all existing
    # permission filtering, link injection and report decoration (also future hooks).
    app.after_request_funcs.setdefault(None, []).insert(0, apply_order)
