"""Synthetic localhost fixture. No production login or data."""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'az-baze-auth'))
os.environ['AZBAZE_SECRET_KEY']='work-groups-fixture-only'
import app as core
import credentials_store as shared
import access_contacts_ext as ext
from cryptography.fernet import Fernet
from flask import session, redirect
scratch=tempfile.TemporaryDirectory()
core.DB_PATH=Path(scratch.name)/'fixture.db'
shared.KEY_PATH=Path(scratch.name)/'key'
shared.KEY_PATH.write_bytes(Fernet.generate_key())
shared.KEY_PATH.chmod(0o600)
core.init_db_file()
with sqlite3.connect(core.DB_PATH) as c:
    for uid,admin in ((1,1),(2,0),(3,0)):
        c.execute('INSERT INTO users(id,email,full_name,password_hash,is_admin,active,must_change_password,created_at,updated_at) VALUES(?,?,?,?,?,1,0,?,?)',(uid,f'{uid}@invalid','Fixture user','x',admin,'t','t'))
    c.executemany('INSERT INTO permissions(user_id,section) VALUES(?,?)',[(2,'credentials_view'),(3,'credentials_view'),(3,'credentials_edit')])
app=core.create_app()
with app.app_context():
    shared.register_credentials(app)
    ext.register(app)
    c=core.db()
    for name,phone,resp in [('Legacy contact','+7 999 111-22-33','Full phone and description retained'),('Second contact','+7 999 222-33-44','Second responsibility')]:
        c.execute("INSERT INTO work_contacts(organization,full_name,phone,responsibility,created_at,updated_at) VALUES('Org',?,?,?,'t','t')",(name,phone,resp))
    c.execute("INSERT INTO credentials(service_name,site_url,login,secret_token,note,created_at,updated_at) VALUES('Fixture service','','fixture-login',?,'Service note','t','t')",(shared._encrypt_secret('fixture-secret'),))
    c.commit()
@app.get('/fixture-login/<int:uid>')
def fixture_login(uid):
    session.clear()
    session['user_id']=uid
    return redirect('/access-contacts/')
app.run(host='127.0.0.1',port=8771,use_reloader=False)
