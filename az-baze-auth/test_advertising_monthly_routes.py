import sqlite3
import unittest
from datetime import date
from unittest.mock import patch
from test_accounting_section import AccountingSectionTests
import app
import advertising_report as core
import advertising_monthly_costs as costs

class MonthlyRoutesTests(unittest.TestCase):
    client=AccountingSectionTests.client
    def setUp(self):
        AccountingSectionTests.setUp(self)
        # Existing app factory registers these routes using the existing report DB.
        for name,value in [('DB_PATH',app.DB_PATH),('_today',lambda:date(2026,11,15)),('_direct_source_values',lambda *args:{'yandex_ads':{'spend':100}}),('_dataset_period_complete',lambda *args:True)]:
            p=patch.object(core,name,value);p.start();self.addCleanup(p.stop)
        with sqlite3.connect(app.DB_PATH) as c:
            c.executescript(core.SCHEMA)
            c.execute('CREATE TABLE report_blobs(key TEXT PRIMARY KEY,payload TEXT,updated_by INTEGER,updated_at TEXT)')
            c.execute("INSERT INTO permissions VALUES(2,'upload_services')")
        p=patch.object(app,'SECTION_KEYS',set(app.SECTION_KEYS)|{'upload_services'})
        p.start();self.addCleanup(p.stop)
        costs.register(self.web,core)
        self.url='/api/reports/advertising/costs/monthly'

    def test_permissions_and_csrf(self):
        self.assertEqual(self.client().get(self.url).status_code,302)
        self.assertEqual(self.client(3).get(self.url).status_code,403)
        self.assertEqual(self.client(3).post(self.url).status_code,403)
        client=self.client(2)
        self.assertEqual(client.get(self.url).status_code,200)
        self.assertEqual(client.post(self.url,data={'action':'confirm'}).status_code,400)
        with client.session_transaction() as s:s['csrf']='token'
        r=client.get(self.url).json
        result=client.post(self.url,data={'csrf':'token','month':r['month'],'revision':r['revision'],'action':'save','source':'other','label':'Площадка','amount':'3000'})
        self.assertEqual(result.status_code,200,result.get_data(as_text=True))
        self.assertEqual(client.get(self.url).json['fixed'],3000)

    def test_get_is_read_only_and_other_financial_blobs_unchanged(self):
        client=self.client(2)
        with sqlite3.connect(app.DB_PATH) as c:
            c.execute("INSERT INTO report_blobs VALUES('sentinel-finance','unchanged',1,'t')")
        before=app.DB_PATH.read_bytes()
        self.assertEqual(client.get(self.url).status_code,200)
        self.assertEqual(app.DB_PATH.read_bytes(),before)
        with client.session_transaction() as s:s['csrf']='token'
        r=client.get(self.url).json
        result=client.post(self.url,data={'csrf':'token','month':r['month'],'revision':r['revision'],'action':'confirm','mode':'auto'})
        self.assertEqual(result.status_code,200)
        with sqlite3.connect(app.DB_PATH) as c:
            self.assertEqual(c.execute("SELECT payload FROM report_blobs WHERE key='sentinel-finance'").fetchone()[0],'unchanged')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit_log WHERE action='advertising_monthly_confirm'").fetchone()[0],1)

if __name__=='__main__':unittest.main()
