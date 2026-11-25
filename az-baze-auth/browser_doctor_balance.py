"""Complete management page with read-only SQLite source and offline browser."""
import json
import os
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
import report_storage
from test_paid_history_reporting import HistoricalPaidReportingTests

root = Path(__file__).resolve().parents[1]
fixture = HistoricalPaidReportingTests()
fixture.setUp()
try:
    fixture.store('2026-01-01', '2026-01-31')
    fixture.store('2026-02-01', '2026-02-17')
    # Synthetic, separately reconciled group evidence; do not alter doctor totals.
    for saved in fixture.conn.execute('SELECT as_of_date,payload_json FROM service_payment_snapshots').fetchall():
        value=json.loads(saved['payload_json'])
        value['management_lab_v1']={'version':1,'paid':60 if saved['as_of_date'].startswith('2026-01') else 80,'reason':'reconciled'}
        fixture.conn.execute('UPDATE service_payment_snapshots SET payload_json=? WHERE as_of_date=?',(json.dumps(value),saved['as_of_date']))
    fixture.add_record('2026-01-17')
    fixture.add_record('2026-01-31')
    fixture.add_record('2026-02-17')
    fixture.conn.execute('CREATE TABLE daily_uploads(data_date TEXT PRIMARY KEY, normalized_json TEXT, services_sha256 TEXT)')
    day = date(2026, 1, 1)
    while day <= date(2026, 2, 17):
        key = day.isoformat()
        if key in {'2026-01-01','2026-01-02','2026-01-03','2026-01-04','2026-02-01'}:
            day += timedelta(days=1)
            continue
        value = {'data_date': key, 'items': [
            {'date': key, 'staff': 'Dent A.', 'amount': 1, 'patient': 'DO NOT EXPOSE',
             'group':'Функциональная стоматология / Зуботехническая лаборатория (клиника)','invoice':'1'},
            {'date': key, 'staff': 'Clinic B.', 'amount': 2,'group':'Другие услуги','invoice':'2'}], 'retail_items': []}
        fixture.conn.execute('INSERT INTO daily_uploads VALUES(?,?,?)', (key, json.dumps(value), 'source'))
        day += timedelta(days=1)
    fixture.conn.commit()
    before = '\n'.join(fixture.conn.iterdump())
    fixture.conn.execute('PRAGMA query_only=ON')
    payload = fixture.client().get('/api/reports/data').get_json()
    assert len(payload['doctorServiceDays']) == 43
    assert 'DO NOT EXPOSE' not in json.dumps(payload)
    compact = fixture.client().get('/api/reports/data?view=dashboard').get_json()
    assert 'doctorServiceDays' not in compact
    with patch.object(report_storage, '_read_report_file', side_effect=lambda name: (root/'reports'/name).read_text()):
        html = report_storage._render_server_reports()
    assert before == '\n'.join(fixture.conn.iterdump())
finally:
    fixture.tearDown()

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
    for width in (1818, 1365, 390):
        page = browser.new_page(viewport={'width': width, 'height': 900})
        errors, writes = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def route(req):
            request = req.request
            if request.method != 'GET': writes.append(request.method)
            if 'az-test.invalid' not in request.url: return req.abort()
            path = request.url.split('az-test.invalid', 1)[1].split('?', 1)[0]
            if path == '/reports/management/': return req.fulfill(body=html, content_type='text/html')
            if path == '/api/reports/data': return req.fulfill(json=payload)
            local = root/path.lstrip('/')
            if local.is_file() and local.resolve().is_relative_to(root/'reports'):
                return req.fulfill(body=local.read_bytes(), content_type='text/javascript' if local.suffix == '.js' else 'text/css')
            return req.fulfill(status=404, body='fixture')
        page.route('**/*', route)
        page.goto('http://az-test.invalid/reports/management/')
        page.wait_for_function('typeof DOCTOR_SERVICE_DAYS !== "undefined" && DOCTOR_SERVICE_DAYS.length === 43')
        page.locator('#reportDate').fill('2026-02-17')
        page.locator('#reportDate').dispatch_event('change')
        page.select_option('#dateViewMode', 'compare')
        row = page.locator('#dateCompareBody tr[data-management-staff-name="Dent Doctor"]')
        assert row.count() == 1
        assert row.locator('td').nth(2).inner_text().split() == ['100', '₽', '16', '₽']
        lab=page.locator('#dateCompareBody tr[data-management-lab="paid"]')
        assert lab.locator('td').nth(1).inner_text().split()==['—','13','₽']
        assert lab.locator('td').nth(2).inner_text().split()==['80','₽','16','₽']
        assert page.locator('#dateCompareBody tr[data-management-lab="orders"] td').nth(2).inner_text()=='16'
        # AZ-260-01: 01.02 is absent from the original service source.
        # Keep actual loaded amounts/counts, never divide full-period payments by 16.
        lab_avg=page.locator('#dateCompareBody tr[data-management-lab="avg"] td').nth(2)
        assert lab_avg.inner_text()=='—'
        assert 'Полнота количества счетов' in lab_avg.get_attribute('title')
        assert 'По загруженным датам' in page.locator('#dateCompareBody tr[data-management-lab="orders"] td').first.inner_text()
        assert 'по загруженным датам' in lab.locator('td').first.inner_text()
        page.select_option('#dateViewMode','single')
        assert page.locator('#labGrid [data-key="managementLabPaid"]').input_value().split()==['80','₽']
        assert page.locator('#labGrid [data-key="managementLabServices"]').input_value().split()==['16','₽']
        assert page.locator('#labGrid [data-key="managementLabAvg"]').input_value()=='—'
        # Independent synthetic positive control: explicit source evidence for
        # 01.02 with no ZTL orders (not a guessed zero or a production write).
        page.evaluate("""() => {
            window.savedLabServiceDays=LAB_SERVICE_DAYS;
            LAB_SERVICE_DAYS=[...LAB_SERVICE_DAYS,{date:'2026-02-01',services:0,orders:0}];
        }""")
        page.select_option('#dateViewMode','compare')
        assert lab_avg.inner_text().split()==['5','₽']
        page.select_option('#dateViewMode','single')
        assert page.locator('#labGrid [data-key="managementLabAvg"]').input_value().split()==['5','₽']
        page.evaluate('LAB_SERVICE_DAYS=window.savedLabServiceDays')
        page.select_option('#dateViewMode','compare')
        assert lab_avg.inner_text()=='—'
        assert row.locator('td').last.inner_text().split() == ['+157', '₽']
        assert row.locator('td').first.inner_text().find('Услуги') >= 0
        # January has no exact 1–17 paid import: its original 150 must survive.
        assert row.locator('.doctor-comparison-paid').first.inner_text().split() == ['150', '₽']
        assert row.locator('td').nth(1).inner_text().split() == ['150', '₽', '13', '₽']
        assert row.bounding_box()['height'] <= 56, row.bounding_box()
        assert row.locator('.doctor-comparison-spacer').count() == 0
        # The controls and doctor name share one line, even with a long name.
        row.locator('.doctor-comparison-name').evaluate('(el)=>el.textContent="Зубачев Роман Николаевич"')
        controls = row.locator('.az-management-staff-row-controls').bounding_box()
        name_box = row.locator('.doctor-comparison-name').bounding_box()
        assert controls and name_box
        assert abs(controls['y'] - name_box['y']) < 10, (controls, name_box)
        assert row.bounding_box()['height'] <= 56, row.bounding_box()
        clinic = page.locator('#dateCompareBody tr[data-management-staff-name="Clinic Doctor"]')
        assert clinic.locator('td').last.inner_text().split() == ['-36', '₽']
        assert page.locator('#dateCompareHead').inner_text().count('Не закрыто') == 1
        # Actual DOM checks for the approved short-month and December cases.
        for selected, jan, feb, apr, octo in [
            ('2026-12-30',30,28,30,30), ('2026-12-31',31,28,30,31),
            ('2026-11-30',31,28,30,31), ('2026-02-27',27,27,27,27),
            ('2026-02-28',31,28,30,31), ('2024-02-28',28,28,28,28),
            ('2024-02-29',31,29,30,31)]:
            page.locator('#reportDate').fill(selected)
            page.locator('#reportDate').dispatch_event('change')
            page.select_option('#compareRange','year')
            header=page.locator('#dateCompareHead').inner_text()
            for month, end in [(1,jan),(2,feb),(4,apr),(10,octo)]:
                assert f'{end:02d}.{month:02d}.{selected[:4]}' in header, (selected,header)
        page.locator('#reportDate').fill('2026-02-28')
        page.locator('#reportDate').dispatch_event('change')
        page.select_option('#compareRange','ytd')
        assert row.locator('td').nth(1).inner_text().split() == ['100','₽','27','₽']
        page.locator('#reportDate').fill('2026-02-17')
        page.locator('#reportDate').dispatch_event('change')
        assert row.locator('td').nth(1).inner_text().split() == ['150','₽','13','₽']
        # Compare every payment cell to the unchanged upstream renderer, not a
        # synthetic replacement formula. Includes missing, zero and negative.
        page.evaluate('''() => {
          window.originalBalanceRender = azDoctorBalance.render;
          SERVER_STORE['2026-01-17'].dentists['Dent Doctor'] = -125;
          SERVER_STORE['2026-01-17'].clinicDocs['Clinic Doctor'] = 0;
          azDoctorBalance.render = () => {};
          document.querySelector('#reportDate').dispatchEvent(new Event('change'));
        }''')
        baseline = page.locator('#dateCompareBody tr[data-management-staff-name]').evaluate_all('rows=>rows.map(r=>[...r.cells].slice(1).map(c=>c.textContent))')
        page.evaluate('''() => {
          azDoctorBalance.render = window.originalBalanceRender;
          document.querySelector('#reportDate').dispatchEvent(new Event('change'));
        }''')
        actual = page.locator('#dateCompareBody tr[data-management-staff-name]').evaluate_all('rows=>rows.map(r=>[...r.querySelectorAll(".doctor-comparison-paid")].map(c=>c.textContent))')
        assert actual == baseline, (actual, baseline)
        assert row.locator('.doctor-comparison-paid').first.inner_text().split() == ['-125', '₽']
        assert clinic.locator('.doctor-comparison-paid').first.inner_text().split() == ['0', '₽']
        # Compare payment markup AND full-cell colours with the original renderer
        # in date comparison and quarter/half/year views. Lower values deliberately oppose the payment gradient.
        page.select_option('#year', '2026')
        for mode in ('date', 'quarter', 'half', 'year'):
            page.select_option('#mode', mode)
            table_rows = ('#dateCompareBody' if mode == 'date' else '#periodCompareBody') + ' tr[data-management-staff-name]'
            page.evaluate("azDoctorBalance.render = () => {}; document.querySelector('#mode').dispatchEvent(new Event('change'))")
            originals = page.locator(table_rows).evaluate_all(
                "rows=>rows.map(r=>[...r.cells].slice(1).map(c=>({html:c.innerHTML,background:c.style.background,heat:c.classList.contains('heat-cell')})))")
            page.evaluate("azDoctorBalance.render = window.originalBalanceRender; document.querySelector('#mode').dispatchEvent(new Event('change'))")
            restored = page.locator(table_rows).evaluate_all(
                "rows=>rows.map(r=>[...r.querySelectorAll('.doctor-comparison-paid')].map(c=>({html:c.innerHTML,background:c.parentElement.style.background,heat:c.parentElement.classList.contains('heat-cell')})))")
            assert restored == originals, (mode, originals, restored)
            if mode == 'date':
                assert any(c['background'] for r in restored for c in r)
            # A single annual value has no gradient in the approved renderer.
            page.evaluate("window.savedServiceDays=DOCTOR_SERVICE_DAYS; DOCTOR_SERVICE_DAYS=DOCTOR_SERVICE_DAYS.map(d=>({...d,dentists:{'Dent Doctor':d.date.startsWith('2026-01')?999999:-999999}})); document.querySelector('#mode').dispatchEvent(new Event('change'))")
            changed_services = page.locator(table_rows).evaluate_all(
                "rows=>rows.map(r=>[...r.querySelectorAll('.doctor-comparison-paid')].map(c=>({html:c.innerHTML,background:c.parentElement.style.background,heat:c.parentElement.classList.contains('heat-cell')})))")
            assert changed_services == originals, mode
            page.evaluate('DOCTOR_SERVICE_DAYS=window.savedServiceDays')
        page.select_option('#mode', 'date')
        page.select_option('#dateViewMode', 'compare')
        # Report date does not move the independent last-uploaded balance cutoff.
        page.locator('#reportDate').fill('2026-02-16')
        page.locator('#reportDate').dispatch_event('change')
        assert '+157' in row.locator('td').last.inner_text()
        assert page.locator('#dateCompareHead').inner_text().count('Не закрыто') == 1
        # Real UI refresh with sparse archived payments: different dates must
        # change both header and amount, and missing endpoint cannot reuse a value.
        page.evaluate("""() => {
          DOCTOR_PAID_SNAPSHOTS={};
          DOCTOR_PAID_PERIODS=[
            {from:'2026-01-05',to:'2026-01-31',dentists:{'Dent Doctor':100},clinicDocs:{}},
            {from:'2026-02-02',to:'2026-02-16',dentists:{'Dent Doctor':80},clinicDocs:{}},
            {from:'2026-02-17',to:'2026-02-17',dentists:{'Dent Doctor':20},clinicDocs:{}}];
          DOCTOR_SERVICE_DAYS=[
            {date:'2026-01-05',complete:true,dentists:{'Dent Doctor':10},clinicDocs:{}},
            {date:'2026-02-02',complete:true,dentists:{'Dent Doctor':5},clinicDocs:{}},
            {date:'2026-02-17',complete:true,dentists:{'Dent Doctor':2},clinicDocs:{}}];
        }""")
        page.locator('#reportDate').fill('2026-02-17')
        page.locator('#reportDate').dispatch_event('change')
        assert page.locator('#doctorBalanceMonth option').count()==12
        page.wait_for_timeout(50)
        unchanged=page.locator('#dateCompareBody tr').evaluate_all("rows=>rows.map(r=>[...r.cells].slice(1,-1).map(c=>({text:c.textContent,background:getComputedStyle(c).backgroundColor})))")
        for month, expected in [('1','+183'),('2','+93'),('12',None),('1','+183')]:
            page.select_option('#doctorBalanceMonth',month)
            page.wait_for_timeout(50)
            header=page.locator('#dateCompareHead .doctor-balance-heading').inner_text()
            assert '17.02.2026' in header and f'01.{int(month):02d}.2026' in header,header
            value=row.locator('td').last.inner_text()
            assert (expected in value and 'По загруженным периодам' not in value) if expected else 'Нет данных с выбранного месяца' in value
            after=page.locator('#dateCompareBody tr').evaluate_all("rows=>rows.map(r=>[...r.cells].slice(1,-1).map(c=>({text:c.textContent,background:getComputedStyle(c).backgroundColor})))")
            assert after==unchanged, (month,[(i,a,b) for i,(a,b) in enumerate(zip(unchanged,after)) if a!=b])
        page.select_option('#doctorBalanceMonth','2')
        for selected in ['2026-01-10','2026-02-28','2026-02-17']:
            page.locator('#reportDate').fill(selected)
            page.locator('#reportDate').dispatch_event('change')
            page.wait_for_timeout(50)
            assert '+93' in row.locator('td').last.inner_text()
            assert '17.02.2026' in page.locator('#dateCompareHead .doctor-balance-heading').inner_text()
        # A newly loaded date extends the cutoff; incomplete money for that
        # date must not silently retain the earlier result.
        page.evaluate("SERVER_STORE['2026-02-18']={date:'2026-02-18'};document.querySelector('#doctorBalanceMonth').dispatchEvent(new Event('change'))")
        assert '18.02.2026' in page.locator('#dateCompareHead .doctor-balance-heading').inner_text()
        assert 'Нет полных данных' in row.locator('td').last.inner_text()
        page.evaluate("delete SERVER_STORE['2026-02-18'];document.querySelector('#doctorBalanceMonth').dispatchEvent(new Event('change'))")
        assert '+93' in row.locator('td').last.inner_text()
        for amount, text, css in [(100,'+93','rgb(24, 115, 60)'),(0,'-7','rgb(180, 35, 24)'),(7,'0','rgb(53, 64, 57)')]:
            page.evaluate("""amount=>{DOCTOR_PAID_PERIODS=[{from:'2026-02-01',to:'2026-02-17',dentists:{'Dent Doctor':amount},clinicDocs:{}}];document.querySelector('#doctorBalanceMonth').dispatchEvent(new Event('change'))}""",amount)
            assert row.locator('td').last.inner_text().split()==[text,'₽']
            assert row.locator('td').last.evaluate('(e)=>getComputedStyle(e).color')==css
        page.select_option('#compareRange','year')
        if width>900:
            page.evaluate("""() => {const t=document.querySelector('.date-compare-table table');window.scrollTo(document.documentElement.scrollWidth,t.getBoundingClientRect().top+window.scrollY+200)}""")
            page.wait_for_timeout(150)
            geometry=page.evaluate("""() => {
              const overlay=document.querySelector('.date-compare-sticky-head');
              const a=overlay.querySelector('.doctor-balance-heading').getBoundingClientRect();
              const b=document.querySelector('#dateCompareHead .doctor-balance-heading').getBoundingClientRect();
              const frame=overlay.getBoundingClientRect();
              return {visible:!overlay.classList.contains('hidden'),left:a.left,right:a.right,source:b.left,frameRight:frame.right};
            }""")
            assert geometry['visible'],geometry
            assert abs(geometry['left']-geometry['source'])<3,geometry
            assert geometry['right']<=geometry['frameRight']+2,geometry
        else:
            page.locator('.date-compare-table').evaluate('(e)=>e.scrollLeft=e.scrollWidth')
            assert page.locator('#dateCompareHead .doctor-balance-heading').is_visible()
        # Owner's actual complaint: 11 visible months (24 Nov) and the
        # extra label in the November payment cell widened the entire table.
        # Populate synthetic values with comparable monetary lengths, not
        # the owner's private figures. Compare full and partial geometry on
        # the exact same report and data, including the last balance column.
        page.evaluate("""() => {
          const months=Array.from({length:11},(_,i)=>'2026-'+String(i+1).padStart(2,'0'));
          LAB_PAID_PERIODS=months.map(m=>({from:m+'-01',to:m+'-24',paid:400000}));
          LAB_SERVICE_DAYS=months.map(m=>({date:m+'-24',services:1000000,orders:25}));
          for(const m of months){
            SERVER_STORE[m+'-24']={...SERVER_STORE['2026-02-17'],date:m+'-24'};
          }
        }""")
        page.select_option('#mode','date')
        page.select_option('#dateViewMode','compare')
        page.select_option('#compareRange','ytd')
        page.locator('#reportDate').fill('2026-11-24')
        page.locator('#reportDate').dispatch_event('change')
        page.locator('.date-compare-table').evaluate('(e)=>e.scrollLeft=0')
        page.evaluate("window.scrollTo(0,0)")
        assert page.locator('#dateCompareHead th').count()==13
        measure="""() => {
          const wrap=document.querySelector('.date-compare-table');
          const table=wrap.querySelector('table');
          const row=table.querySelector('tr[data-management-lab="paid"]');
          const cell=row.cells[11];
          return {
            widths:[...table.querySelectorAll('thead th')].map(e=>e.getBoundingClientRect().width),
            tableWidth:table.getBoundingClientRect().width,
            scrollWidth:wrap.scrollWidth,clientWidth:wrap.clientWidth,
            pageOverflow:document.documentElement.scrollWidth-document.documentElement.clientWidth,
            rowHeight:row.getBoundingClientRect().height,
            children:[...cell.children].map(e=>e.textContent),
            lastRight:table.querySelector('thead th:last-child').getBoundingClientRect().right,
            wrapRight:wrap.getBoundingClientRect().right
          };
        }"""
        full_geometry=page.evaluate(measure)
        page.evaluate("""() => {
          LAB_PAID_PERIODS=LAB_PAID_PERIODS.map(p=>p.to==='2026-11-24'?{...p,paid:null,knownPaid:400000}:p);
          document.querySelector('#reportDate').dispatchEvent(new Event('change'));
        }""")
        partial_geometry=page.evaluate(measure)
        assert partial_geometry['children']==full_geometry['children'],(width,partial_geometry)
        assert len(partial_geometry['children'])==2,(width,partial_geometry)
        # Chromium auto layout can redistribute a couple of CSS pixels
        # between columns when the heat style changes. Assert identical
        # total width/scroll extent and no material increase of any column,
        # and equal month widths (November cannot expand separately).
        for key in ('tableWidth','scrollWidth','clientWidth','pageOverflow','rowHeight','lastRight','wrapRight'):
            assert partial_geometry[key]==full_geometry[key],(width,key,full_geometry,partial_geometry)
        for a,b in zip(full_geometry['widths'],partial_geometry['widths']):
            assert abs(a-b)<=3,(width,full_geometry,partial_geometry)
        month_widths=partial_geometry['widths'][1:12]
        assert max(month_widths)-min(month_widths)<=1,(width,partial_geometry)
        assert partial_geometry['rowHeight']<=56,(width,partial_geometry)
        if width==1818:
            assert partial_geometry['scrollWidth']<=partial_geometry['clientWidth']+1,partial_geometry
            assert partial_geometry['lastRight']<=partial_geometry['wrapRight']+1,partial_geometry
        cell=page.locator('#dateCompareBody tr[data-management-lab="paid"] td').nth(11)
        assert cell.locator('.az-lab-comparison-label').count()==0
        assert 'полная сумма' in cell.locator('.az-lab-paid').get_attribute('title')
        # Explicit selected correction is used without adding a visible label.
        page.evaluate("""() => {
          LAB_PAID_PERIODS=[...LAB_PAID_PERIODS,{from:'2026-11-01',to:'2026-11-24',
             paid:null,knownPaid:570000,recovered:true}];
          document.querySelector('#reportDate').dispatchEvent(new Event('change'));
        }""")
        assert cell.locator('.az-lab-paid').inner_text().split()==['570','000','₽']
        assert cell.locator('.az-lab-services').inner_text().split()==['1','000','000','₽']
        assert cell.locator('.az-lab-comparison-label').count()==0
        assert page.locator('#dateCompareBody tr[data-management-lab="avg"] td').nth(11).inner_text()=='—'
        page.select_option('#dateViewMode','single')
        assert page.locator('#managementLabPaidCaption').inner_text()=='Оплаты'
        assert page.locator('#labGrid [data-key="managementLabPaid"]').input_value().split()==['570','000','₽']
        assert 'полная сумма' in page.locator('#labGrid [data-key="managementLabPaid"]').get_attribute('title')
        print(f'LAB_NO_LABEL_NO_WIDTH_GROWTH_{width}=PASS; EXACT_CORRECTION=PASS')
        assert not errors, errors
        assert not writes, writes
        page.close()
        print(f'DOCTOR_BALANCE_BROWSER_{width}=PASS; DB_READ_ONLY=PASS; WRITES=NONE')
    browser.close()
