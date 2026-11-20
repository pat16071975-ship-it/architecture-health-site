"""Temporary synthetic data, localhost only. Never registered in production."""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'az-baze-auth'))
os.environ['AZBAZE_SECRET_KEY']='fixture-only-not-production-v2'
import app as auth
import ident_import
import paid_services
from flask import redirect, session

scratch=tempfile.TemporaryDirectory()
auth.DB_PATH=Path(scratch.name)/'fixture.db'
auth.init_db_file()
with sqlite3.connect(auth.DB_PATH) as conn:
    conn.executescript(paid_services.SCHEMA)
    conn.execute("INSERT INTO users(email,full_name,password_hash,is_admin,active,must_change_password,created_at,updated_at) VALUES('test@invalid','Browser test','x',1,1,0,'t','t')")
    d,s=next(iter(ident_import.DENTISTS)),next(iter(ident_import.STRUCTURE_DOCTORS))
    payload=dict(version=3,period_start='2026-10-01',period_end='2026-10-31',
                 providers={d:{'paid':100000},s:{'paid':20000}},paid_patient_evidence_complete=True,
                 paid_patients={d:['fixture-a','fixture-b'],s:['fixture-b','fixture-c']})
    conn.execute('INSERT INTO service_payment_periods(period_start,period_end,payload_json,source_filename,source_sha256,imported_at,imported_by) VALUES(?,?,?,?,?,?,?)',
                 ('2026-10-01','2026-10-31',json.dumps(payload),'fixture.xlsx','a'*64,'2026-11-01T00:00:00Z',1))
app=auth.create_app()
@app.get('/fixture-login')
def login_fixture_v2():
    session['user_id']=1
    return redirect('/reports/financial-model/')
app.run(host='127.0.0.1',port=int(os.environ.get('FINMODEL_TEST_PORT','8769')),use_reloader=False)
