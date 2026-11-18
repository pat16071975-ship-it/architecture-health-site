import json
import sqlite3
import unittest

from doctor_service_totals import service_days


class DoctorServiceTotalsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE daily_uploads(data_date TEXT PRIMARY KEY, normalized_json TEXT, services_sha256 TEXT)')
        self.maps = {'dent': {'Врач А. Б.': 'Врач Анна Борисовна'}, 'structure': {'Специалист В. Г.': 'Специалист'}, 'lab': {}}

    def tearDown(self):
        self.db.close()

    def store(self, day='2026-01-01', items=None, retail=None, sha='source'):
        payload = {'data_date': day, 'items': items or [], 'retail_items': retail or [],
                   'visits': [{'patient': 'MUST NOT LEAK'}]}
        self.db.execute('INSERT INTO daily_uploads VALUES(?,?,?)', (day, json.dumps(payload), sha))
        self.db.commit()

    def item(self, amount=100, staff='Врач А. Б.', day='2026-01-01'):
        return {'date': day, 'staff': staff, 'amount': amount, 'paid_amount': 99999,
                'patient': 'MUST NOT LEAK', 'invoice': 'PRIVATE'}

    def test_original_amounts_retail_negative_and_read_only(self):
        self.store(items=[self.item('100.10'), self.item('-0.20'), self.item(50, 'Специалист В. Г.')],
                   retail=[self.item('10.30')])
        before = '\n'.join(self.db.iterdump())
        self.db.execute('PRAGMA query_only=ON')
        result = service_days(self.db, self.maps, set())
        self.assertEqual(result, [{'date': '2026-01-01', 'complete': True,
                                  'dentists': {'Врач Анна Борисовна': 110.2}, 'clinicDocs': {'Специалист': 50.0}}])
        self.assertEqual(before, '\n'.join(self.db.iterdump()))
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('MUST NOT LEAK', json.dumps(result))

    def test_completed_only_not_zero_services(self):
        self.store(sha='')
        self.assertEqual(service_days(self.db, self.maps, set()), [])

    def test_confirmed_empty_day_is_zero(self):
        self.store()
        self.assertTrue(service_days(self.db, self.maps, set())[0]['complete'])

    def test_missing_days_not_manufactured(self):
        self.store('2026-01-02')
        self.assertEqual([r['date'] for r in service_days(self.db, self.maps, set())], ['2026-01-02'])

    def test_spacing_alias_and_registry_direction(self):
        self.store(items=[self.item(25, 'ВрачА.Б.')])
        self.maps = {'dent': {}, 'structure': {'Врач А. Б.': 'Врач Анна Борисовна'}, 'lab': {}}
        row = service_days(self.db, self.maps, set())[0]
        self.assertEqual(row['clinicDocs'], {'Врач Анна Борисовна': 25})
        self.assertEqual(row['dentists'], {})

    def test_unknown_or_ambiguous_provider_not_confirmed(self):
        self.store(items=[self.item(100, 'Unknown')])
        self.assertFalse(service_days(self.db, self.maps, set())[0]['complete'])
        self.maps['structure']['ВрачА.Б.'] = 'Other'
        self.db.execute('DELETE FROM daily_uploads')
        self.store(items=[self.item()])
        self.assertFalse(service_days(self.db, self.maps, set())[0]['complete'])

    def test_invalid_amount_or_wrong_day_not_zero(self):
        for amount in (None, True, 'NaN', 'Infinity', 'invalid'):
            with self.subTest(amount=amount):
                self.db.execute('DELETE FROM daily_uploads')
                self.store(items=[self.item(amount)])
                self.assertFalse(service_days(self.db, self.maps, set())[0]['complete'])
        self.db.execute('DELETE FROM daily_uploads')
        self.store(items=[self.item(day='2026-02-01')])
        self.assertFalse(service_days(self.db, self.maps, set())[0]['complete'])

    def test_malformed_source_remains_visible_as_incomplete(self):
        for raw in ('bad', '{}', '{"data_date":"2026-01-01","items":null}'):
            with self.subTest(raw=raw):
                self.db.execute('DELETE FROM daily_uploads')
                self.db.execute('INSERT INTO daily_uploads VALUES(?,?,?)', ('2026-01-01', raw, 'hash'))
                self.store('2026-01-05', items=[self.item(25, day='2026-01-05')])
                rows = service_days(self.db, self.maps, set())
                self.assertEqual(len(rows), 2)
                self.assertFalse(rows[0]['complete'])
                self.assertTrue(rows[1]['complete'])

    def test_missing_table_and_invalid_json(self):
        self.db.execute("INSERT INTO daily_uploads VALUES('2026-01-01','bad','hash')")
        self.assertEqual(service_days(self.db, self.maps, set()), [
            {'date': '2026-01-01', 'complete': False, 'dentists': {}, 'clinicDocs': {}}])
        self.db.execute('DROP TABLE daily_uploads')
        self.assertEqual(service_days(self.db, self.maps, set()), [])


if __name__ == '__main__':
    unittest.main()
