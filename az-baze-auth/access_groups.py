"""Independent contact and service credential groups. Private credentials never use these tables or routes."""
from flask import abort, g, redirect, render_template, request

SCHEMA = '''
CREATE TABLE IF NOT EXISTS work_groups (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL, name_key TEXT NOT NULL,
 parent_id INTEGER REFERENCES work_groups(id) ON DELETE RESTRICT,
 version INTEGER NOT NULL DEFAULT 1,
 kind TEXT NOT NULL DEFAULT 'contacts' CHECK(kind IN ('contacts','credentials'))
);
CREATE UNIQUE INDEX IF NOT EXISTS work_groups_unique_name
 ON work_groups(kind,COALESCE(parent_id,0),name_key);
CREATE TABLE IF NOT EXISTS work_contact_groups (
 record_id INTEGER PRIMARY KEY REFERENCES work_contacts(id) ON DELETE CASCADE,
 group_id INTEGER NOT NULL REFERENCES work_groups(id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS work_credential_groups (
 record_id INTEGER PRIMARY KEY REFERENCES credentials(id) ON DELETE CASCADE,
 group_id INTEGER NOT NULL REFERENCES work_groups(id) ON DELETE RESTRICT
);
'''
DEFAULTS = ('Программы и IT', 'Реклама и продвижение', 'Почта и связь',
            'Финансы и документы', 'Оборудование', 'Помещения и обслуживание',
            'Безопасность', 'Поставщики')
CREDENTIAL_DEFAULTS = ('Медицинские системы', 'Сайты, домены и серверы',
    'Реклама и аналитика', 'Карты, справочники и отзывы',
    'Телефония, CRM и мессенджеры', 'Почта и облачные аккаунты',
    'Управление работой', 'Финансы и документооборот', 'Оборудование и безопасность')
LABELS = {'contacts': 'Группы рабочих контактов', 'credentials': 'Группы служебных доступов'}
TABLES = {'contacts': 'work_contact_groups', 'credentials': 'work_credential_groups'}


def _copy_credential_groups(conn):
    # Clone only paths actually used by service credentials (including trash).
    # The original catalogue and contact memberships retain their IDs and values.
    rows = {r[0]: tuple(r) for r in conn.execute(
        'SELECT id,name,name_key,parent_id,version FROM work_groups')}
    used = {r[0] for r in conn.execute('SELECT DISTINCT group_id FROM work_credential_groups')}
    for ident in tuple(used):
        if ident not in rows:
            raise ValueError('Не найдена прежняя группа доступа; разделение остановлено.')
        parent = rows[ident][3]
        if parent is not None:
            if parent not in rows or rows[parent][3] is not None:
                raise ValueError('Повреждена иерархия групп; разделение остановлено.')
            used.add(parent)
    mapping = {}
    for ident in sorted(used, key=lambda i: (rows[i][3] is not None, i)):
        _, name, key, parent, version = rows[ident]
        mapping[ident] = conn.execute(
            "INSERT INTO work_groups(name,name_key,parent_id,version,kind) VALUES(?,?,?,?,'credentials')",
            (name, key, mapping[parent] if parent is not None else None, version)).lastrowid
    for old, new in mapping.items():
        conn.execute('UPDATE work_credential_groups SET group_id=? WHERE group_id=?', (new, old))


def init_schema(conn):
    # Explicit preparation only, never called by a GET. SAVEPOINT keeps the
    # whole separation atomic and also supports a caller-owned transaction.
    conn.execute('SAVEPOINT separate_work_groups')
    try:
        columns = {r[1] for r in conn.execute('PRAGMA table_info(work_groups)')}
        legacy = bool(columns) and 'kind' not in columns
        if legacy:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not set(TABLES.values()) <= names:
                raise ValueError('Не найдены прежние связи групп; разделение остановлено.')
            conn.execute("ALTER TABLE work_groups ADD COLUMN kind TEXT NOT NULL DEFAULT 'contacts' CHECK(kind IN ('contacts','credentials'))")
            conn.execute('DROP INDEX work_groups_unique_name')
        for statement in SCHEMA.split(';'):
            if statement.strip():
                conn.execute(statement)
        if [r[2] for r in conn.execute('PRAGMA index_info(work_groups_unique_name)')] != ['kind', None, 'name_key']:
            raise ValueError('Неверная структура индекса групп.')
        if legacy:
            _copy_credential_groups(conn)
        # Seed once per catalogue. In a legacy migration, copied groups are
        # preserved and the nine approved service groups are added if missing.
        for kind, defaults in (('contacts', DEFAULTS), ('credentials', CREDENTIAL_DEFAULTS)):
            empty = not conn.execute('SELECT 1 FROM work_groups WHERE kind=? LIMIT 1', (kind,)).fetchone()
            if empty or (legacy and kind == 'credentials'):
                for name in defaults:
                    conn.execute('INSERT OR IGNORE INTO work_groups(name,name_key,kind) VALUES(?,?,?)',
                                 (name, name.casefold(), kind))
        conn.execute('RELEASE SAVEPOINT separate_work_groups')
    except BaseException:
        conn.execute('ROLLBACK TO SAVEPOINT separate_work_groups')
        conn.execute('RELEASE SAVEPOINT separate_work_groups')
        raise


def require_kind(kind):
    if kind not in TABLES:
        abort(404)
    return kind


def ready(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if (not {'work_groups', *TABLES.values()} <= names
        or 'kind' not in {r[1] for r in conn.execute('PRAGMA table_info(work_groups)')}
        or [r[2] for r in conn.execute('PRAGMA index_info(work_groups_unique_name)')] != ['kind', None, 'name_key']):
        abort(503, 'Группы ещё не подготовлены.')


def log(conn, action, details):
    import app as core
    conn.execute('INSERT INTO audit_log(actor_user_id,action,details,created_at) VALUES(?,?,?,?)',
                 (g.user['id'], action, details, core.iso_now()))


def catalogue(conn, kind='contacts'):
    require_kind(kind)
    ready(conn)
    rows = [dict(r) for r in conn.execute('SELECT * FROM work_groups WHERE kind=? ORDER BY id', (kind,))]
    roots = sorted((r for r in rows if r['parent_id'] is None), key=lambda r: r['name'].casefold())
    result = []
    for root in roots:
        result.append(dict(root, label=root['name']))
        for child in sorted((r for r in rows if r['parent_id'] == root['id']), key=lambda r: r['name'].casefold()):
            result.append(dict(child, label=root['name']+' / '+child['name']))
    return result


def group_id(conn, raw, required=True, kind='contacts'):
    require_kind(kind)
    ready(conn)
    if raw in ('', '0', None, 0):
        if required:
            raise ValueError('Выберите группу: куда отнести запись.')
        return 0
    if not str(raw).isascii() or not str(raw).isdigit():
        raise ValueError('Выберите существующую группу.')
    value = int(raw)
    if value > 9223372036854775807 or not conn.execute('SELECT 1 FROM work_groups WHERE id=? AND kind=?', (value, kind)).fetchone():
        raise ValueError('Группа не найдена в этом разделе. Обновите список.')
    return value


def create(conn, name, parent='', kind='contacts'):
    require_kind(kind)
    ready(conn)
    name = ' '.join(name.split())
    if not name or len(name) > 100 or name.casefold() == 'без группы':
        raise ValueError('Укажите название группы до 100 символов; «Без группы» — служебный раздел.')
    parent_id = group_id(conn, parent, required=False, kind=kind) or None
    if parent_id and conn.execute('SELECT parent_id FROM work_groups WHERE id=?', (parent_id,)).fetchone()[0] is not None:
        raise ValueError('Подгруппу можно добавить только в основную группу.')
    if conn.execute('SELECT 1 FROM work_groups WHERE kind=? AND COALESCE(parent_id,0)=? AND name_key=?', (kind, parent_id or 0, name.casefold())).fetchone():
        raise ValueError('Такая группа уже существует.')
    ident = conn.execute('INSERT INTO work_groups(name,name_key,parent_id,kind) VALUES(?,?,?,?)',
                         (name, name.casefold(), parent_id, kind)).lastrowid
    log(conn, 'work_group_created', f'kind={kind}; group_id={ident}; parent_id={parent_id}')
    return ident


def from_form(conn, required=True, kind='contacts'):
    raw = request.form.get('group_id', '')
    if raw == 'new':
        return create(conn, request.form.get('new_group_name', ''), request.form.get('new_group_parent', ''), kind=kind)
    return group_id(conn, raw, required, kind=kind)


def assigned(conn, kind, ident):
    ready(conn)
    row = conn.execute('SELECT group_id FROM '+TABLES[kind]+' WHERE record_id=?', (ident,)).fetchone()
    return row[0] if row else 0


def check_expected(conn, kind, ident, expected):
    if str(assigned(conn, kind, ident)) != expected:
        abort(409, 'Группа записи уже изменена. Обновите страницу.')


def assign(conn, kind, ident, target):
    target = group_id(conn, target, required=False, kind=kind)
    table = TABLES[kind]
    if target:
        conn.execute('INSERT INTO '+table+'(record_id,group_id) VALUES(?,?) ON CONFLICT(record_id) DO UPDATE SET group_id=excluded.group_id', (ident, target))
    else:
        conn.execute('DELETE FROM '+table+' WHERE record_id=?', (ident,))


def listing(conn, kind, rows):
    groups = catalogue(conn, kind)
    assignments = dict(conn.execute('SELECT record_id,group_id FROM '+TABLES[kind]))
    nodes = {r['id']: dict(r, rows=[], children=[], count=0) for r in groups}
    ungrouped = dict(id=0, name='Без группы', rows=[], children=[], count=0)
    for row in rows:
        node = nodes.get(assignments.get(row['id']), ungrouped)
        node['rows'].append(row)
        node['count'] += 1
    roots = []
    for node in nodes.values():
        if node['parent_id'] is None:
            roots.append(node)
        else:
            parent = nodes[node['parent_id']]
            parent['children'].append(node)
            parent['count'] += node['count']
    return dict(groups=groups, tree=[r for r in roots if r['count']] + ([ungrouped] if ungrouped['count'] else []),
                assignments=assignments, kind=kind)


def register(app):
    import app as core

    @app.route('/access-contacts/groups/', methods=['GET', 'POST'])
    @core.permission_required('credentials_view')
    @core.permission_required('credentials_edit')
    def work_groups_legacy():
        if request.method == 'POST':
            core.require_csrf()
            abort(409, 'Группы разделены. Откройте управление группами нужного раздела заново.')
        return redirect('/access-contacts/groups/contacts/')

    @app.route('/access-contacts/groups/<kind>/', methods=['GET', 'POST'])
    @core.permission_required('credentials_view')
    @core.permission_required('credentials_edit')
    def work_groups_manage(kind):
        require_kind(kind)
        conn = core.db()
        ready(conn)
        error = None
        if request.method == 'POST':
            core.require_csrf()
            try:
                with conn:
                    conn.execute('BEGIN IMMEDIATE')
                    if request.form.get('action') == 'rename':
                        ident = group_id(conn, request.form.get('id'), kind=kind)
                        old = conn.execute('SELECT * FROM work_groups WHERE id=?', (ident,)).fetchone()
                        if request.form.get('version') != str(old['version']):
                            abort(409, 'Группа уже изменена. Обновите страницу.')
                        name = ' '.join(request.form.get('name', '').split())
                        if not name or len(name) > 100 or name.casefold() == 'без группы':
                            raise ValueError('Укажите название группы до 100 символов.')
                        if conn.execute('SELECT 1 FROM work_groups WHERE kind=? AND COALESCE(parent_id,0)=? AND name_key=? AND id<>?', (kind, old['parent_id'] or 0, name.casefold(), ident)).fetchone():
                            raise ValueError('Такая группа уже существует.')
                        conn.execute('UPDATE work_groups SET name=?,name_key=?,version=version+1 WHERE id=?', (name, name.casefold(), ident))
                        log(conn, 'work_group_renamed', f'kind={kind}; group_id={ident}')
                    else:
                        create(conn, request.form.get('name', ''), request.form.get('parent_id', ''), kind=kind)
                return redirect('/access-contacts/groups/'+kind+'/')
            except ValueError as exc:
                error = str(exc)
        return render_template('work_groups.html', groups=catalogue(conn, kind), kind=kind, heading=LABELS[kind], error=error, csrf=core.csrf_token()), 400 if error else 200

    @app.post('/access-contacts/groups/<kind>/move')
    @core.permission_required('credentials_view')
    @core.permission_required('credentials_edit')
    def work_groups_move(kind):
        if kind not in TABLES:
            abort(404)
        core.require_csrf()
        conn = core.db()
        ready(conn)
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                target = group_id(conn, request.form.get('group_id'), kind=kind)
                choices = request.form.getlist('record')
                if not choices or len(choices) > 1000 or len(set(choices)) != len(choices):
                    raise ValueError('Выберите от 1 до 1000 разных записей.')
                checked = []
                for choice in choices:
                    parts = choice.split(':')
                    if len(parts) != 2 or any(not p.isascii() or not p.isdigit() or len(p)>18 for p in parts):
                        raise ValueError('Некорректный выбор записи.')
                    ident, expected = parts
                    sql = ('SELECT 1 FROM work_contacts WHERE id=?' if kind == 'contacts' else
                           'SELECT 1 FROM credentials WHERE id=? AND id NOT IN (SELECT credential_id FROM credential_trash)')
                    if not conn.execute(sql, (int(ident),)).fetchone():
                        abort(409, 'Запись удалена или перемещена в корзину. Обновите страницу.')
                    check_expected(conn, kind, int(ident), expected)
                    checked.append(int(ident))
                if len(set(checked)) != len(checked):
                    raise ValueError('Запись выбрана несколько раз.')
                for ident in checked:
                    assign(conn, kind, ident, target)
                log(conn, 'work_group_records_moved', f'kind={kind}; group_id={target}; ids={checked}')
            return redirect('/'+kind+'/')
        except ValueError as exc:
            abort(400, str(exc))
