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
    fixture.add_record('2026-01-17')
    fixture.add_record('2026-02-17')
    fixture.conn.execute('CREATE TABLE daily_uploads(data_date TEXT PRIMARY KEY, normalized_json TEXT, services_sha256 TEXT)')
    day = date(2026, 1, 1)
    while day <= date(2026, 2, 17):
        key = day.isoformat()
        value = {'data_date': key, 'items': [
            {'date': key, 'staff': 'Dent A.', 'amount': 1, 'patient': 'DO NOT EXPOSE'},
            {'date': key, 'staff': 'Clinic B.', 'amount': 2}], 'retail_items': []}
        fixture.conn.execute('INSERT INTO daily_uploads VALUES(?,?,?)', (key, json.dumps(value), 'source'))
        day += timedelta(days=1)
    fixture.conn.commit()
    before = '\n'.join(fixture.conn.iterdump())
    fixture.conn.execute('PRAGMA query_only=ON')
    payload = fixture.client().get('/api/reports/data').get_json()
    assert len(payload['doctorServiceDays']) == 48
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
    for width in (1365, 390):
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
        page.wait_for_function('typeof DOCTOR_SERVICE_DAYS !== "undefined" && DOCTOR_SERVICE_DAYS.length === 48')
        page.locator('#reportDate').fill('2026-02-17')
        page.locator('#reportDate').dispatch_event('change')
        page.select_option('#dateViewMode', 'compare')
        row = page.locator('#dateCompareBody tr[data-management-staff-name="Dent Doctor"]')
        assert row.count() == 1
        assert row.locator('td').nth(2).inner_text().split() == ['100', '₽', '17', '₽']
        assert row.locator('td').last.inner_text().split() == ['+152', '₽']
        assert row.locator('td').first.inner_text().find('Услуги') >= 0
        # January has no exact 1–17 paid import: its original 150 must survive.
        assert row.locator('.doctor-comparison-paid').first.inner_text().split() == ['150', '₽']
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
        assert clinic.locator('td').last.inner_text().split() == ['-46', '₽']
        assert page.locator('#dateCompareHead').inner_text().count('Не закрыто') == 1
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
        assert '-125' in actual[0][0], actual
        assert clinic.locator('.doctor-comparison-paid').first.inner_text().split() == ['0', '₽']
        # Changing date must neither reuse the future payment source nor duplicate a header.
        page.locator('#reportDate').fill('2026-02-16')
        page.locator('#reportDate').dispatch_event('change')
        assert 'Нет полных данных' in row.locator('td').last.inner_text()
        assert page.locator('#dateCompareHead').inner_text().count('Не закрыто') == 1
        assert not errors, errors
        assert not writes, writes
        page.close()
        print(f'DOCTOR_BALANCE_BROWSER_{width}=PASS; DB_READ_ONLY=PASS; WRITES=NONE')
    browser.close()
