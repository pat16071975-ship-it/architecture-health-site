"""Private vault. Every object query is scoped to the authenticated owner."""
from flask import abort, g, jsonify, redirect, render_template, request

PREFIX = '/personal-credentials/'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS personal_credentials (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 service_name TEXT NOT NULL DEFAULT '', login TEXT NOT NULL DEFAULT '',
 secret_token TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 deleted_at TEXT, version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS personal_credentials_owner ON personal_credentials(owner_id, deleted_at);
'''


def init_schema(conn):
    conn.executescript(SCHEMA)


def register(app):
    # Imports deferred: app creates its default instance during module import.
    import app as core

    def connection():
        conn = core.db()
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='personal_credentials'").fetchone():
            abort(503, 'Личный раздел пока не подготовлен.')
        return conn

    def item(item_id, trashed=False):
        row = connection().execute(
            'SELECT * FROM personal_credentials WHERE id=? AND owner_id=? AND (deleted_at IS NOT NULL)=?',
            (item_id, g.user['id'], int(trashed))).fetchone()
        if row is None:
            abort(404)
        return row

    def version(row):
        if request.form.get('version') != str(row['version']):
            abort(409, 'Запись уже изменена. Обновите страницу.')

    def render(template, **values):
        return render_template(template, credential_prefix=PREFIX,
                               credential_label='Личные доступы', csrf=core.csrf_token(), **values)

    def change(row, sql, params):
        cursor = connection().execute(sql + ' WHERE id=? AND owner_id=? AND version=?',
                                     (*params, row['id'], g.user['id'], row['version']))
        if cursor.rowcount != 1:
            connection().rollback()
            abort(409)
        connection().commit()

    @app.get(PREFIX)
    @core.login_required
    def personal_credentials_index():
        conn = connection()
        rows = conn.execute('SELECT * FROM personal_credentials WHERE owner_id=? AND deleted_at IS NULL ORDER BY service_name,id', (g.user['id'],)).fetchall()
        count = conn.execute('SELECT COUNT(*) FROM personal_credentials WHERE owner_id=? AND deleted_at IS NOT NULL', (g.user['id'],)).fetchone()[0]
        return render('credentials.html', rows=rows, trash_count=count, can_edit=True, can_delete=True, can_reveal=True)

    @app.route(PREFIX+'new', methods=['GET', 'POST'])
    @app.route(PREFIX+'<int:item_id>/edit', methods=['GET', 'POST'])
    @core.login_required
    def personal_credentials_form(item_id=None):
        import credentials_store as crypto
        row = item(item_id) if item_id is not None else None
        connection()
        if request.method == 'POST':
            core.require_csrf()
            if row is not None:
                version(row)
            fields = {}
            for name, limit in [('service_name',160), ('login',320), ('password',1024), ('note',2000)]:
                fields[name] = request.form.get(name, '')
                if len(fields[name]) > limit:
                    abort(400)
            token = row['secret_token'] if row is not None else ''
            if request.form.get('clear_password') == '1':
                token = ''
            elif fields['password']:
                token = crypto._encrypt_secret(fields['password'])
            values = (fields['service_name'], fields['login'], token, fields['note'], core.iso_now())
            if row is None:
                connection().execute('INSERT INTO personal_credentials(service_name,login,secret_token,note,updated_at,created_at,owner_id) VALUES(?,?,?,?,?,?,?)', (*values, values[-1], g.user['id']))
                connection().commit()
            else:
                change(row, 'UPDATE personal_credentials SET service_name=?,login=?,secret_token=?,note=?,updated_at=?,version=version+1', values)
            return redirect(PREFIX)
        return render('credential_form.html', title='Изменить личный доступ' if row else 'Новый личный доступ',
                      values=dict(row) if row else {}, editing=row is not None,
                      has_password=bool(row and row['secret_token']), version=row['version'] if row else 0)

    @app.post(PREFIX+'<int:item_id>/secret')
    @core.login_required
    def personal_credentials_secret(item_id):
        import credentials_store as crypto
        core.require_csrf()
        row = item(item_id)
        if request.form.get('action') not in {'show', 'copy_password', 'copy_all'}:
            abort(400)
        secret = crypto._decrypt_secret(row['secret_token']) if row['secret_token'] else ''
        response = jsonify(password=secret, service=row['service_name'], site='', login=row['login'], note=row['note'])
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.route(PREFIX+'<int:item_id>/delete', methods=['GET', 'POST'])
    @core.login_required
    def personal_credentials_delete(item_id):
        import credentials_store as crypto
        row = item(item_id)
        error = None
        if request.method == 'POST':
            core.require_csrf()
            version(row)
            if not crypto._current_password_ok(request.form.get('current_password', '')):
                error = 'Пароль AZ-BAZE неверен.'
            elif row['service_name'] and request.form.get('service_confirmation') != row['service_name']:
                error = 'Название сервиса введено не точно.'
            else:
                change(row, 'UPDATE personal_credentials SET deleted_at=?,updated_at=?,version=version+1', (core.iso_now(), core.iso_now()))
                return redirect(PREFIX)
        return render('credential_delete_confirm.html', row=row, version=row['version'], error=error, display_title=row['service_name'] or 'Доступ без названия')

    @app.get(PREFIX+'trash')
    @core.login_required
    def personal_credentials_trash():
        rows = connection().execute('SELECT *, NULL AS deleted_by_name FROM personal_credentials WHERE owner_id=? AND deleted_at IS NOT NULL ORDER BY deleted_at DESC,id', (g.user['id'],)).fetchall()
        return render('credentials_trash.html', rows=rows, can_purge=False)

    @app.post(PREFIX+'<int:item_id>/restore')
    @core.login_required
    def personal_credentials_restore(item_id):
        core.require_csrf()
        row = item(item_id, trashed=True)
        version(row)
        change(row, 'UPDATE personal_credentials SET deleted_at=NULL,updated_at=?,version=version+1', (core.iso_now(),))
        return redirect(PREFIX)
