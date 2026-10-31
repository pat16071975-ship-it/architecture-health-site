"""Protect the actual Oct 13/14 installed importer while fixing the journal/save UI.

All database/XLSX examples below are synthetic and isolated. No live data,
normalization or period-boundary repair is executed.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import app as app_core
import daily_upload
import paid_services_upload as uploads
import test_paid_services_periods as fixtures
import upload_history
import upload_reconcile

ROOT = Path(__file__).resolve().parent


def git_blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


class InstalledSourcePreservationTests(unittest.TestCase):
    def test_paid_sources_preserve_installed_behavior_under_advertising_hook(self):
        self.assertEqual(
            git_blob((ROOT / 'paid_services.py').read_bytes()),
            '7731d49098494276e40442d06443a0729fc7fcf3',
        )

        text = (ROOT / 'paid_services_upload.py').read_text(encoding='utf-8')
        self.assertEqual(text.count('import advertising_mis_bridge\n'), 1)
        text = text.replace('import advertising_mis_bridge\n', '', 1)

        hook = '''    advertising_sync = {"status": "skipped"}
    try:
        conn.execute("BEGIN IMMEDIATE")
        capture = advertising_mis_bridge.capture_paid_report(
            conn,
            report,
            filename,
            source_sha,
            now,
        )
        sync = advertising_mis_bridge.rebuild(conn, now)
        conn.commit()
        advertising_sync = {"status": "ok", "capture": capture, "sync": sync}
    except Exception as exc:
        conn.rollback()
        advertising_sync = {"status": "error", "error": type(exc).__name__}
        audit(
            "advertising_mis_sync_failed",
            target_user_id=g.user["id"],
            details=(
                f"source=paid_services; period={report['period_start']}..{report['period_end']}; "
                f"error={type(exc).__name__}"
            ),
        )

'''
        self.assertEqual(text.count(hook), 1)
        text = text.replace(hook, '', 1)

        response_field = '        "advertising_sync": advertising_sync,\n'
        self.assertEqual(text.count(response_field), 1)
        text = text.replace(response_field, '', 1)

        self.assertEqual(
            git_blob(text.encode()),
            'd2e682540f831d5adafd9b19e753a01d53749349',
        )

    def test_template_preserves_journal_contract_and_reconcile_close(self):
        raw = (ROOT / 'templates/uploads.html').read_bytes()
        self.assertEqual(git_blob(raw), '576803d8eb0ef60545484096060d516d182fcdb1')
        text = raw.decode('utf-8')
        changes = [
            ("{{ row['data_date'] or '—' }}{% if row['period_label'] %}<br><span class=\"muted\">{{ row['period_label'] }}</span>{% endif %}", "{{ row['data_date'] or '—' }}"),
            ("{% if row['status_text'] %}{{ row['status_text'] }}{% elif row['status']=='imported' %}", "{% if row['status']=='imported' %}"),
            ("{% if row['source_label'] %}<span class=\"muted\">{{ row['source_label'] }}</span><br>{% endif %}{{ row['completed_filename'] or '' }}", "{{ row['completed_filename'] or '' }}"),
        ]
        for before, after in changes:
            self.assertEqual(text.count(before), 1)
            text = text.replace(before, after)
        self.assertNotIn('name="period_start"', text)
        self.assertNotIn('name="period_end"', text)
        self.assertIn('expected_overlap_revision', text)
        self.assertEqual(text.count('id="reconcileClose"'), 1)
        self.assertIn('aria-label="Закрыть">×</button>', text)
        self.assertIn("closeBtn?.addEventListener('click',closePreview)", text)
        self.assertIn("event.key==='Escape'", text)
        self.assertIn('.reconcile-close{width:44px;height:44px', text)
        self.assertIn('Есть конкретные приёмы, для которых направление определить нельзя', text)
        self.assertIn('payload.unassigned_visits||[]', text)
        self.assertIn('data-visit-direction', text)
        self.assertIn('Оставить нераспределённым', text)
        self.assertIn('visit_direction_decisions', text)
        self.assertIn('Можно продолжить загрузку без выбора', text)
        self.assertIn("continueBtn.disabled=unknown.length>0&&!payload.can_replace", text)
        self.assertIn("if(select.value)result[select.dataset.visitDirection]=select.value", text)
        self.assertIn("const needs=out.requires_choice||out.requires_provider_mapping||(type!=='clinical'&&out.requires_review);", text)
        self.assertNotIn("out.requires_choice||out.requires_provider_mapping||out.requires_review", text)
        self.assertIn("type!=='clinical'&&out.requires_review", text)
        self.assertIn("5. Яндекс Метрика", text)
        self.assertIn('name="metrika_zip"', text)
        self.assertIn("/api/reports/advertising/metrika/import", text)
        self.assertIn("Загрузить данные Яндекс Метрики", text)
        self.assertIn("/api/reports/advertising/metrika/sync", text)
        self.assertIn("Ручная загрузка ZIP — резервный вариант", text)

    def test_home_logout_is_hidden_only_on_mobile(self):
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        self.assertIn('class="az-home-logout"', source)
        self.assertIn(
            '@media(max-width:700px){.az-home-logout{display:none!important}}',
            source,
        )
        self.assertIn(
            'style="position:fixed;left:18px;bottom:18px;z-index:50"',
            source,
        )
        self.assertIn('>Выйти</button></form>', source)

    def test_clinical_preview_builds_patient_level_direction_rows_and_persists_choice(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        upload_reconcile.init_schema(conn)

        period = [
            {
                "data_date": "2026-10-26",
                "normalized": {
                    "overall": {"2026-10-26": {"Повторные": 2}},
                    "visits": [
                        {"date": "2026-10-26", "patient": "Иванов И. И.", "kind": "Повторные"},
                        {"date": "2026-10-26", "patient": "Петров П. П.", "kind": "Повторные"},
                    ],
                    "items": [],
                    "doctors": {"2026-10-26": {}},
                },
            }
        ]
        rows = daily_upload._visit_direction_rows(period, conn)
        self.assertEqual([row["patient"] for row in rows], ["Иванов И. И.", "Петров П. П."])
        decisions = {
            rows[0]["visit_id"]: "structure",
            rows[1]["visit_id"]: "unassigned",
        }
        unresolved, resolved = daily_upload._apply_visit_direction_rows(
            period, rows, decisions
        )
        self.assertEqual(unresolved, [])
        self.assertEqual(resolved, decisions)
        self.assertEqual(
            period[0]["normalized"]["manual_direction_counts"]["structure"]["Повторные"],
            1,
        )
        upload_reconcile.save_visit_direction_overrides(
            conn, decisions, rows, actor_id=7
        )
        conn.commit()
        again = daily_upload._visit_direction_rows(
            [
                {
                    "data_date": "2026-10-26",
                    "normalized": {
                        "overall": {"2026-10-26": {"Повторные": 2}},
                        "visits": [
                            {"date": "2026-10-26", "patient": "Иванов И. И.", "kind": "Повторные"},
                            {"date": "2026-10-26", "patient": "Петров П. П.", "kind": "Повторные"},
                        ],
                        "items": [],
                        "doctors": {"2026-10-26": {}},
                    },
                }
            ],
            conn,
        )
        self.assertEqual(
            [row["saved_direction"] for row in again],
            ["structure", "unassigned"],
        )

    def test_visit_direction_override_rollback_is_atomic(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        upload_reconcile.init_schema(conn)
        row = {
            "visit_id": "synthetic-visit",
            "date": "2026-10-26",
            "patient_token": "token",
            "kind": "Повторные",
            "occurrence": 1,
        }
        conn.execute("BEGIN")
        upload_reconcile.save_visit_direction_overrides(
            conn,
            {"synthetic-visit": "structure"},
            [row],
            actor_id=7,
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM clinical_visit_direction_overrides"
            ).fetchone()[0],
            1,
        )
        conn.rollback()
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM clinical_visit_direction_overrides"
            ).fetchone()[0],
            0,
        )

    def test_manual_save_candidate_is_unchanged_by_importer_reconciliation(self):
        # Protect the actual manual-entry and legacy period contracts rather than
        # freezing entire files whose paid-patient read projection is authorized to change.
        html = (ROOT.parent / 'reports' / 'index.html').read_text(encoding='utf-8')
        start = html.index('function collectRecord(){')
        end = html.index('function refreshAutos(', start)
        self.assertEqual(
            hashlib.sha256(html[start:end].encode()).hexdigest(),
            'f512bdf6430e1be0f520d3bbc9178d557613271e1c197464a9c7466dea5d6396',
        )

        view = (ROOT.parent / 'reports' / 'period-view.js').read_text(encoding='utf-8')
        for start_text, end_text, expected in (
            ('  function periodData(def,year){', '  function doctorPeriodData(',
             '55d442c0ae415edfd250e9ec3587b4390274aee149f53337434e9e204fea6305'),
            ('  function addDateMetric(', '  function addDateDoctorMetric(',
             '1963e92a0001c721a7319a5eaf3c201bb65dba1fd0cd6457bd496a6e5e68f34d'),
        ):
            start = view.index(start_text)
            end = view.index(end_text, start)
            self.assertEqual(
                hashlib.sha256(view[start:end].encode()).hexdigest(),
                expected,
            )


class ActualDateImportJournalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PaidPeriodUploadTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.conn = self.fixture.conn
        self.conn.executescript('''
          CREATE TABLE users(id INTEGER PRIMARY KEY,full_name TEXT);
          INSERT INTO users VALUES(7,'Synthetic operator');
          CREATE TABLE audit_log(id INTEGER PRIMARY KEY,actor_user_id INTEGER,action TEXT,
              target_user_id INTEGER,details TEXT,created_at TEXT);
        ''')
        for obj, attr, kwargs in [(uploads, 'audit', {'new': app_core.audit}),
                                  (app_core, 'db', {'return_value': self.conn})]:
            patcher = patch.object(obj, attr, **kwargs)
            patcher.start(); self.addCleanup(patcher.stop)
        raw = fixtures.report_bytes(('01.10.2026', '11.10.2026'), period=False)
        response = self.fixture.post('commit', raw)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.previous = tuple(self.conn.execute('SELECT * FROM service_payment_snapshots').fetchone())
        self.raw = fixtures.report_bytes(('12.10.2026', '13.10.2026'), provider='Unknown U.', period=False)

    def test_incremental_slice_keeps_dates_existing_snapshot_and_journal(self):
        preview = self.fixture.post('preview', self.raw).get_json()
        self.assertEqual(preview['period'], {'from': '2026-10-12', 'to': '2026-10-13'})
        self.assertEqual(preview['overlaps'], [])
        result = self.fixture.post('commit', self.raw, expected_revision=preview['revision'],
             expected_overlap_revision=preview['overlap_revision'],
             provider_decisions=json.dumps({'Unknown U.': 'dent'}))
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()['status'], 'imported')
        self.assertEqual(self.previous, tuple(self.conn.execute('SELECT * FROM service_payment_snapshots').fetchone()))
        row = self.conn.execute('SELECT period_start,period_end,payload_json FROM service_payment_periods').fetchone()
        self.assertEqual(tuple(row[:2]), ('2026-10-12', '2026-10-13'))
        self.assertEqual(json.loads(row[2])['providers']['Unknown U.']['paid'], 260)
        periods = {row['data_date'] for row in upload_history.recent_uploads(self.conn)}
        self.assertEqual(periods, {'2026-10-01 — 2026-10-11', '2026-10-12 — 2026-10-13'})
        self.assertEqual(self.fixture.protected_state(), self.fixture.protected)

    def test_incremental_storage_failure_reverts_decisions_and_no_false_history(self):
        self.conn.execute("CREATE TRIGGER fail_slice AFTER INSERT ON service_payment_periods "
                          "BEGIN SELECT RAISE(FAIL,'synthetic slice fault'); END")
        self.conn.commit()
        before = list(self.conn.iterdump())
        history = upload_history.recent_uploads(self.conn)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'synthetic slice fault'):
            self.fixture.post('commit', self.raw, provider_decisions=json.dumps({'Unknown U.': 'dent'}))
        self.assertEqual(list(self.conn.iterdump()), before)
        self.assertEqual(upload_history.recent_uploads(self.conn), history)
        self.assertFalse(self.conn.in_transaction)


if __name__ == '__main__':
    unittest.main(verbosity=2)
