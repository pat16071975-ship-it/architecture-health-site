"""Disposable localhost-only browser-test fixture. Not imported by production."""
import os,sys,sqlite3,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'az-baze-auth'))
os.environ['AZBAZE_SECRET_KEY']='fixture-only-not-production'
import app as auth
from flask import session,redirect
scratch=tempfile.TemporaryDirectory();auth.DB_PATH=Path(scratch.name)/'fixture.db';auth.init_db_file()
with sqlite3.connect(auth.DB_PATH) as c:
 c.execute("INSERT INTO users(email,full_name,password_hash,is_admin,active,must_change_password,created_at,updated_at) VALUES('test@invalid','Browser test','x',1,1,0,'t','t')")
app=auth.create_app()
@app.get('/fixture-login')
def login_fixture():
 session['user_id']=1;return redirect('/reports/financial-model/')
app.run(host='127.0.0.1',port=8768,use_reloader=False)
