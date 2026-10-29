"""Actual rendered HTML + complete JS stack, offline Chromium and synthetic API."""
import calendar
import json
import os
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
import report_storage
import cash_payments
from test_paid_history_reporting import HistoricalPaidReportingTests

root=Path(__file__).resolve().parents[1]
f=HistoricalPaidReportingTests();f.setUp()
try:
    cash_payments.init_schema(f.conn)
    def cash_evidence(day, direction, tokens):
        payload=cash_payments._blank_day()
        payload["cashTotal"]=1.0
        payload["cashUnallocated"]=1.0
        payload["payerEvidenceVersion"]=1
        payload["payerEvidenceComplete"]=True
        payload["payerEvidenceCoverageFrom"]="2026-01-01"
        payload["payerEvidenceCoverageTo"]="2026-10-27"
        payload["payerTokens"]=sorted(tokens)
        payload["payerDirectionTokens"]={"dent":[],"structure":[],"lab":[],"unallocated":[]}
        payload["payerDirectionTokens"][direction]=sorted(tokens)
        f.conn.execute(
            """INSERT INTO cash_receipts_daily(
               data_date,payload_json,source_filename,source_sha256,imported_by,imported_at
               ) VALUES(?,?,?,?,?,?)""",
            (day,json.dumps(cash_payments._round_payload(payload),ensure_ascii=False),
             "synthetic-ytd.xlsx","synthetic-sha",1,"synthetic"),
        )

    for month in range(1,11):
        prefix=f'2026-{month:02d}'
        first=prefix+('-05' if month==1 else '-02' if month in (2,3) else '-01')
        last=prefix+('-27' if month==10 else '-30' if month==5 else f'-{calendar.monthrange(2026,month)[1]:02d}')
        shared,other=f.store(first,last,confirmed=month in (1,2,3,5))
        cash_evidence(prefix+'-05','dent',{shared})
        cash_evidence(prefix+'-10','structure',{shared})
        cash_evidence(prefix+'-15','structure',{other})
        f.add_record(prefix+'-15')
        f.add_record(prefix+('-27' if month==10 else f'-{calendar.monthrange(2026,month)[1]:02d}'))
    f.conn.commit()
    f.add_record('2026-10-28',factMedicine=99999)
    before='\n'.join(f.conn.iterdump())
    f.conn.execute('PRAGMA query_only=ON')
    payload=f.client().get('/api/reports/data').get_json()
    with patch.object(report_storage,'_read_report_file',side_effect=lambda name:(root/'reports'/name).read_text()):
        html=report_storage._render_server_reports()
    assert before=='\n'.join(f.conn.iterdump())
finally:
    f.tearDown()

checks=0
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    for width in (1365,390):
        page=browser.new_page(viewport={'width':width,'height':900})
        errors=[];writes=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def route(req):
            request=req.request
            if request.method!='GET':writes.append(request.method+' '+request.url)
            path=request.url.split('az-test.invalid',1)[-1].split('?',1)[0]
            if 'az-test.invalid' not in request.url:return req.abort()
            if path=='/reports/management/':return req.fulfill(body=html,content_type='text/html')
            if path=='/api/reports/data':return req.fulfill(json=payload)
            local=root/path.lstrip('/')
            if local.is_file() and local.resolve().is_relative_to(root/'reports'):
                return req.fulfill(body=local.read_bytes(),content_type='text/javascript' if local.suffix=='.js' else 'text/css')
            return req.fulfill(status=404,body='not in isolated fixture')
        page.route('**/*',route)
        page.goto('http://az-test.invalid/reports/management/')
        page.wait_for_function("document.querySelector('#dateViewMode') && Object.keys(PAID_PATIENTS_BY_DATE).length>=10")
        assert page.locator('#dateViewMode option').all_text_contents()==['Сравнение','На дату']
        checks+=1
        for month in range(1,11):
            day=f'2026-{month:02d}-'+('27' if month==10 else f'{calendar.monthrange(2026,month)[1]:02d}')
            page.locator('#reportDate').fill(day)
            page.locator('#reportDate').dispatch_event('change')
            page.select_option('#dateViewMode','single')
            page.wait_for_function("document.querySelector('#generalGrid [data-key=paidPatients]').value==='2'")
            assert page.locator('#dentSummary [data-key=dentAvg]').input_value().replace('\xa0',' ')=='150 ₽'
            checks+=2
            page.select_option('#dateViewMode','compare')
            row=page.locator('#dateCompareBody tr').filter(has=page.locator('td:first-child',has_text='Оплатившие пациенты')).first
            assert row.locator('td').nth(month).inner_text()=='2'
            checks+=1
        for month in range(1,11):
            day=f'2026-{month:02d}-15'
            page.locator('#reportDate').fill(day)
            page.locator('#reportDate').dispatch_event('change')
            page.select_option('#dateViewMode','single')
            page.wait_for_function("document.querySelector('#generalGrid [data-key=paidPatients]').value==='2'")
            assert page.locator('#dentSummary [data-key=dentAvg]').input_value().replace('\xa0',' ')=='150 ₽'
            checks+=2
            page.select_option('#dateViewMode','compare')
            row=page.locator('#dateCompareBody tr').filter(has=page.locator('td:first-child',has_text='Оплатившие пациенты')).first
            assert row.locator('td').nth(month).inner_text()=='2'
            checks+=1
        page.locator('#reportDate').fill('2026-01-20');page.locator('#reportDate').dispatch_event('change')
        page.select_option('#dateViewMode','single')
        page.wait_for_function("document.querySelector('#generalGrid [data-key=paidPatients]').value==='2'")
        assert page.locator('#dentSummary [data-key=dentAvg]').input_value()==''
        checks+=2
        page.select_option('#mode','year')
        page.wait_for_function("document.querySelector('#periodCompareBody').children.length>10")
        def cell(label):
            return page.locator('#periodCompareBody tr').filter(has=page.locator('td:first-child',has_text=label)).first.locator('td').nth(1).inner_text().replace('\xa0',' ')
        assert cell('Оплатившие пациенты')=='20'
        assert cell('Средний чек стоматологии на оплатившего пациента')=='150 ₽'
        checks+=2
        page.select_option('#mode','date');page.select_option('#dateViewMode','compare')
        page.wait_for_function("document.querySelector('#dateSummaryNote').textContent.includes('оплатившие пациенты')")
        assert not errors,errors
        assert not writes,writes
        checks+=3
        page.close()
    browser.close()
print(f'PAID_TWO_VIEWS_BROWSER=PASS; ASSERTIONS={checks}; WIDTHS=1365,390; API_WRITES=0; SYNTHETIC_DATA_ONLY=YES')
