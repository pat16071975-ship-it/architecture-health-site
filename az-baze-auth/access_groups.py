"""Shared work groups. Private credentials never use these tables or routes."""
from flask import abort, g, redirect, render_template, request

SCHEMA = '''
CREATE TABLE IF NOT EXISTS work_groups (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL, name_key TEXT NOT NULL,
 parent_id INTEGER REFERENCES work_groups(id) ON DELETE RESTRICT,
 version INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS work_groups_unique_name
 ON work_groups(COALESCE(parent_id,0),name_key);
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
TABLES = {'contacts': 'work_contact_groups', 'credentials': 'work_credential_groups'}


def init_schema(conn):
    conn.executescript(SCHEMA)
    # Seed only an empty catalogue; a renamed group must never reappear on restart.
    if not conn.execute('SELECT 1 FROM work_groups LIMIT 1').fetchone():
        conn.executemany('INSERT INTO work_groups(name,name_key) VALUES(?,?)',
                         [(n, n.casefold()) for n in DEFAULTS])


def ready(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'work_groups', *TABLES.values()} <= names:
        abort(503, 'Группы ещё не подготовлены.')


def log(conn, action, details):
    import app as core
    conn.execute('INSERT INTO audit_log(actor_user_id,action,details,created_at) VALUES(?,?,?,?)',
                 (g.user['id'], action, details, core.iso_now()))


def catalogue(conn):
    ready(conn)
    rows = [dict(r) for r in conn.execute('SELECT * FROM work_groups ORDER BY id')]
    roots = sorted((r for r in rows if r['parent_id'] is None), key=lambda r: r['name'].casefold())
    result = []
    for root in roots:
        result.append(dict(root, label=root['name']))
        for child in sorted((r for r in rows if r['parent_id'] == root['id']), key=lambda r: r['name'].casefold()):
            result.append(dict(child, label=root['name']+' / '+child['name']))
    return result


def group_id(conn, raw, required=True):
    ready(conn)
    if raw in ('', '0', None):
        if required:
            raise ValueError('Выберите группу: куда отнести запись.')
        return 0
    if not str(raw).isascii() or not str(raw).isdigit():
        raise ValueError('Выберите существующую группу.')
    value = int(raw)
    if value > 9223372036854775807 or not conn.execute('SELECT 1 FROM work_groups WHERE id=?', (value,)).fetchone():
        raise ValueError('Группа не найдена. Обновите список.')
    return value


def create(conn, name, parent=''):
    ready(conn)
    name = ' '.join(name.split())
    if not name or len(name) > 100 or name.casefold() == 'без группы':
        raise ValueError('Укажите название группы до 100 символов; «Без группы» — служебный раздел.')
    parent_id = group_id(conn, parent, required=False) or None
    if parent_id and conn.execute('SELECT parent_id FROM work_groups WHERE id=?', (parent_id,)).fetchone()[0] is not None:
        raise ValueError('Подгруппу можно добавить только в основную группу.')
    if conn.execute('SELECT 1 FROM work_groups WHERE COALESCE(parent_id,0)=? AND name_key=?', (parent_id or 0, name.casefold())).fetchone():
        raise ValueError('Такая группа уже существует.')
    ident = conn.execute('INSERT INTO work_groups(name,name_key,parent_id) VALUES(?,?,?)',
                         (name, name.casefold(), parent_id)).lastrowid
    log(conn, 'work_group_created', f'group_id={ident}; parent_id={parent_id}')
    return ident


def from_form(conn, required=True):
    raw = request.form.get('group_id', '')
    if raw == 'new':
        return create(conn, request.form.get('new_group_name', ''), request.form.get('new_group_parent', ''))
    return group_id(conn, raw, required)


def assigned(conn, kind, ident):
    ready(conn)
    row = conn.execute('SELECT group_id FROM '+TABLES[kind]+' WHERE record_id=?', (ident,)).fetchone()
    return row[0] if row else 0


def check_expected(conn, kind, ident, expected):
    if str(assigned(conn, kind, ident)) != expected:
        abort(409, 'Группа записи уже изменена. Обновите страницу.')


def assign(conn, kind, ident, target):
    table = TABLES[kind]
    if target:
        conn.execute('INSERT INTO '+table+'(record_id,group_id) VALUES(?,?) ON CONFLICT(record_id) DO UPDATE SET group_id=excluded.group_id', (ident, target))
    else:
        conn.execute('DELETE FROM '+table+' WHERE record_id=?', (ident,))


def listing(conn, kind, rows):
    groups = catalogue(conn)
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
    def work_groups_manage():
        conn = core.db()
        ready(conn)
        error = None
        if request.method == 'POST':
            core.require_csrf()
            try:
                with conn:
                    conn.execute('BEGIN IMMEDIATE')
                    if request.form.get('action') == 'rename':
                        ident = group_id(conn, request.form.get('id'))
                        old = conn.execute('SELECT * FROM work_groups WHERE id=?', (ident,)).fetchone()
                        if request.form.get('version') != str(old['version']):
                            abort(409, 'Группа уже изменена. Обновите страницу.')
                        name = ' '.join(request.form.get('name', '').split())
                        if not name or len(name) > 100 or name.casefold() == 'без группы':
                            raise ValueError('Укажите название группы до 100 символов.')
                        if conn.execute('SELECT 1 FROM work_groups WHERE COALESCE(parent_id,0)=? AND name_key=? AND id<>?', (old['parent_id'] or 0, name.casefold(), ident)).fetchone():
                            raise ValueError('Такая группа уже существует.')
                        conn.execute('UPDATE work_groups SET name=?,name_key=?,version=version+1 WHERE id=?', (name, name.casefold(), ident))
                        log(conn, 'work_group_renamed', f'group_id={ident}')
                    else:
                        create(conn, request.form.get('name', ''), request.form.get('parent_id', ''))
                return redirect('/access-contacts/groups/')
            except ValueError as exc:
                error = str(exc)
        return render_template('work_groups.html', groups=catalogue(conn), error=error, csrf=core.csrf_token()), 400 if error else 200

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
                target = group_id(conn, request.form.get('group_id'))
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
