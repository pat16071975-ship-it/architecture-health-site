"""Cash-dated paid-patient timeline regressions; synthetic identities only."""
import json
import unittest

import cash_payments
import report_storage
from test_paid_history_reporting import HistoricalPaidReportingTests


class CashPaidTimelineTests(unittest.TestCase):
    def setUp(self):
        self.f = HistoricalPaidReportingTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        cash_payments.init_schema(self.f.conn)

    def _payload(self, tokens_by_direction, start="2026-01-01", end="2026-01-31"):
        payload = cash_payments._blank_day()
        all_tokens = set()
        direct = {"dent": set(), "structure": set(), "lab": set(), "unallocated": set()}
        for direction, tokens in tokens_by_direction.items():
            direct[direction].update(tokens)
            all_tokens.update(tokens)
        payload["cashTotal"] = 100.0
        payload["cashUnallocated"] = 100.0
        payload["payerEvidenceComplete"] = True
        payload["payerEvidenceCoverageFrom"] = start
        payload["payerEvidenceCoverageTo"] = end
        payload["payerTokens"] = all_tokens
        payload["payerDirectionTokens"] = direct
        return cash_payments._round_payload(payload)

    def _put(self, day, payload):
        self.f.conn.execute(
            """INSERT INTO cash_receipts_daily(
               data_date,payload_json,source_filename,source_sha256,imported_by,imported_at
               ) VALUES(?,?,?,?,?,?)""",
            (day, json.dumps(payload, ensure_ascii=False), "year.xlsx", "sha", 1, "now"),
        )
        self.f.conn.commit()

    def test_arbitrary_days_come_from_cash_dates_after_month_end_reconciliation(self):
        shared, other = self.f.store("2026-01-05", "2026-01-31", confirmed=True)
        self._put("2026-01-05", self._payload({"dent": {shared}}))
        self._put("2026-01-10", self._payload({"structure": {shared}}))
        self._put("2026-01-15", self._payload({"structure": {other}}))

        intervals = report_storage._paid_patient_intervals(self.f.conn)
        rows = report_storage._management_paid_patients_by_date(self.f.conn, intervals)

        self.assertEqual(rows["2026-01-09"]["medicine"], 1)
        self.assertEqual(rows["2026-01-09"]["dent"], 1)
        self.assertEqual(rows["2026-01-09"]["structure"], 0)
        self.assertEqual(rows["2026-01-15"]["medicine"], 2)
        self.assertEqual(rows["2026-01-15"]["dent"], 1)
        self.assertEqual(rows["2026-01-15"]["structure"], 2)
        self.assertEqual(rows["2026-01-17"]["medicine"], 2)
        self.assertEqual(rows["2026-01-31"]["medicine"], 2)

    def test_unallocated_receipt_is_resolved_only_for_single_medical_direction(self):
        shared, other = self.f.store("2026-01-05", "2026-01-31", confirmed=True)
        self._put("2026-01-05", self._payload({"dent": {shared}}))
        self._put("2026-01-10", self._payload({"structure": {shared}}))
        self._put("2026-01-15", self._payload({"unallocated": {other}}))

        rows = report_storage._management_paid_patients_by_date(
            self.f.conn, report_storage._paid_patient_intervals(self.f.conn)
        )
        self.assertEqual(rows["2026-01-15"]["structure"], 2)

    def test_incomplete_direction_reconciliation_does_not_publish_fake_daily_timeline(self):
        shared, other = self.f.store("2026-01-05", "2026-01-31", confirmed=True)
        # shared belongs to both dent+structure; unallocated cannot prove either direction.
        self._put("2026-01-05", self._payload({"unallocated": {shared}}))
        self._put("2026-01-15", self._payload({"structure": {other}}))

        rows = report_storage._management_paid_patients_by_date(
            self.f.conn, report_storage._paid_patient_intervals(self.f.conn)
        )
        self.assertNotIn("2026-01-15", rows)
        # Existing confirmed MIS month-end is retained.
        self.assertEqual(rows["2026-01-31"]["medicine"], 2)

    def test_positive_cash_day_without_patient_evidence_blocks_daily_timeline(self):
        shared, other = self.f.store("2026-01-05", "2026-01-31", confirmed=True)
        self._put("2026-01-05", self._payload({"dent": {shared}}))
        bad = self._payload({"structure": {shared, other}})
        bad["payerEvidenceComplete"] = False
        self._put("2026-01-15", bad)

        rows = report_storage._management_paid_patients_by_date(
            self.f.conn, report_storage._paid_patient_intervals(self.f.conn)
        )
        self.assertNotIn("2026-01-15", rows)

    def test_api_populates_arbitrary_saved_date_without_exposing_tokens(self):
        shared, other = self.f.store("2026-01-05", "2026-01-31", confirmed=True)
        self._put("2026-01-05", self._payload({"dent": {shared}}))
        self._put("2026-01-10", self._payload({"structure": {shared}}))
        self._put("2026-01-15", self._payload({"structure": {other}}))
        self.f.add_record("2026-01-15")

        response = self.f.client().get("/api/reports/data")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        row = payload["data"]["2026-01-15"]
        self.assertEqual(row["paidPatients"], 2)
        self.assertEqual(row["paidDentPatients"], 1)
        self.assertEqual(row["paidClinicPatients"], 2)
        raw = response.get_data(as_text=True)
        self.assertNotIn(shared, raw)
        self.assertNotIn(other, raw)


if __name__ == "__main__":
    unittest.main()
