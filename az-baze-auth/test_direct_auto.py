import io
import json
import unittest
import urllib.error
from datetime import date
from unittest.mock import patch

import direct_auto


class DirectDailyReportTests(unittest.TestCase):
    @staticmethod
    def _campaigns(currency="RUB"):
        return json.dumps({
            "result": {
                "Campaigns": [
                    {"Id": 715444498, "Name": "Кампания врача", "Currency": currency}
                ]
            }
        }).encode()

    @staticmethod
    def _report(*, conversion="2.5", cid="715444498", duplicate=False):
        header = "Date\tCampaignId\tCampaignName\tClicks\tCost\tConversions\n"
        row = "2026-11-10\t%s\tКампания врача\t10\t123.45\t%s\n" % (cid, conversion)
        return (header + row + (row if duplicate else "")).encode()

    class FakeResponse:
        def __init__(self, status, body=b"", headers=None):
            self.status = status
            self.data = body
            self.headers = headers or {}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, limit=None):
            return self.data if limit is None else self.data[:limit]

    @staticmethod
    def http_error(status, request_id="yandex456", body=b""):
        return urllib.error.HTTPError(
            direct_auto.REPORTS_URL, status, "error",
            {"RequestId": request_id}, io.BytesIO(body),
        )

    def test_reports_auto_mode_offline_queue_eventually_returns_tsv(self):
        response = [
            self.FakeResponse(201, headers={"retryIn":"1","RequestId":"r201"}),
            self.FakeResponse(202, headers={"retryIn":"2","RequestId":"r202"}),
            self.FakeResponse(200, self._report()),
        ]
        with patch.object(direct_auto.urllib.request, "urlopen", side_effect=response) as urlopen, \
             patch.object(direct_auto.time, "sleep") as sleep:
            got = direct_auto._request(
                direct_auto.REPORTS_URL, "TEST_TOKEN", {"params":{}}, reports=True
            )
        self.assertEqual(got, self._report())
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(urlopen.call_args.args[0].get_header("Processingmode"), "auto")
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1,2])

    def test_reports_auto_mode_retries_one_server_500(self):
        payload=b'{"error":{"request_id":"abc555","error_code":1000,"error_detail":"SENSITIVE_NOT_FOR_UI"}}'
        with patch.object(direct_auto.urllib.request, "urlopen", side_effect=[
            self.http_error(500, body=payload),
            self.FakeResponse(200, self._report()),
        ]) as urlopen, patch.object(direct_auto.time, "sleep") as sleep:
            got = direct_auto._request(
                direct_auto.REPORTS_URL, "TEST_TOKEN", {"params":{}}, reports=True
            )
        self.assertEqual(got, self._report())
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_persistent_500_shows_safe_request_id_not_secret(self):
        payload=b'{"error":{"request_id":"abc555","error_code":1000,"error_detail":"SENSITIVE_NOT_FOR_UI"}}'
        with patch.object(direct_auto.urllib.request, "urlopen", side_effect=[
            self.http_error(500, body=payload), self.http_error(500, body=payload),
        ]), patch.object(direct_auto.time, "sleep"):
            with self.assertRaises(direct_auto.DirectApiError) as caught:
                direct_auto._request(
                    direct_auto.REPORTS_URL, "TEST_TOKEN", {"params":{}}, reports=True
                )
        message=str(caught.exception)
        self.assertIn("Reports", message)
        self.assertIn("HTTP 500", message)
        self.assertIn("RequestId=yandex456", message)
        self.assertIn("код=1000", message)
        self.assertNotIn("SENSITIVE_NOT_FOR_UI", message)
        self.assertNotIn("TEST_TOKEN", message)

    def test_502_retry_is_bounded_and_not_silently_marked_as_success(self):
        with patch.object(direct_auto.urllib.request, "urlopen", side_effect=[
            self.http_error(502), self.http_error(502),
        ]), patch.object(direct_auto.time, "sleep") as sleep:
            with self.assertRaisesRegex(direct_auto.DirectApiError, "HTTP 502"):
                direct_auto._request(
                    direct_auto.REPORTS_URL, "TEST_TOKEN", {"params":{}}, reports=True
                )
        sleep.assert_called_once_with(1)

    def test_long_retry_in_queues_without_hanging_a_sync_worker(self):
        with patch.object(direct_auto.urllib.request, "urlopen", return_value=
                          self.FakeResponse(201, headers={"retryIn":"90","RequestId":"r201"})), \
             patch.object(direct_auto.time, "sleep") as sleep:
            with self.assertRaisesRegex(direct_auto.DirectApiError, "90 сек"):
                direct_auto._request(
                    direct_auto.REPORTS_URL, "TEST_TOKEN", {"params":{}}, reports=True
                )
        sleep.assert_not_called()

    def test_missing_or_unsafe_request_id_is_not_echoed_to_user(self):
        payload=b'{"error":{"request_id":"secret\\nBearer OAUTH","error_code":52,"error_detail":"SECRET"}}'
        with patch.object(direct_auto.urllib.request, "urlopen", side_effect=[
            self.http_error(400, request_id="", body=payload)
        ]):
            with self.assertRaises(direct_auto.DirectApiError) as caught:
                direct_auto._request(
                    direct_auto.REPORTS_URL, "TEST_TOKEN", {"params":{}}, reports=True
                )
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertIn("код=52", str(caught.exception))

    def test_campaign_rows_money_vat_and_fractional_conversions(self):
        with patch.object(direct_auto, "_request", side_effect=[
            self._campaigns(), self._report()
        ]) as mocked:
            rows, meta = direct_auto.fetch_daily_campaigns(
                "FAKE_TEST_TOKEN", date(2026, 11, 10), date(2026, 11, 10)
            )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source_key"], "yandex_ads")
        self.assertEqual(row["campaign_id"], "715444498")
        self.assertEqual(row["spend"], 123.45)
        self.assertEqual(row["clicks"], 10)
        self.assertEqual(row["conversions"], 2.5)
        self.assertTrue(meta["include_vat"])
        self.assertEqual(meta["currency"], "RUB")
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(mocked.call_args[0][2]["params"]["IncludeVAT"], "YES")

    def test_missing_conversions_are_null_not_zero(self):
        with patch.object(direct_auto, "_request", side_effect=[
            self._campaigns(), self._report(conversion="--")
        ]):
            rows, _ = direct_auto.fetch_daily_campaigns(
                "FAKE_TEST_TOKEN", date(2026, 11, 10), date(2026, 11, 10)
            )
        self.assertIsNone(rows[0]["conversions"])

    def test_unknown_campaign_fails_closed(self):
        with patch.object(direct_auto, "_request", side_effect=[
            self._campaigns(), self._report(cid="999999999")
        ]):
            with self.assertRaises(direct_auto.DirectApiError):
                direct_auto.fetch_daily_campaigns(
                    "FAKE_TEST_TOKEN", date(2026, 11, 10), date(2026, 11, 10)
                )

    def test_non_rub_campaign_fails_closed(self):
        with patch.object(direct_auto, "_request", side_effect=[
            self._campaigns("USD"), self._report()
        ]):
            with self.assertRaises(direct_auto.DirectApiError):
                direct_auto.fetch_daily_campaigns(
                    "FAKE_TEST_TOKEN", date(2026, 11, 10), date(2026, 11, 10)
                )

    def test_duplicate_campaign_day_fails_closed(self):
        with patch.object(direct_auto, "_request", side_effect=[
            self._campaigns(), self._report(duplicate=True)
        ]):
            with self.assertRaises(direct_auto.DirectApiError):
                direct_auto.fetch_daily_campaigns(
                    "FAKE_TEST_TOKEN", date(2026, 11, 10), date(2026, 11, 10)
                )

    def test_no_live_request_before_supported_date(self):
        with patch.object(direct_auto, "_request") as mocked:
            with self.assertRaises(direct_auto.DirectApiError):
                direct_auto.fetch_daily_campaigns(
                    "FAKE_TEST_TOKEN", date(2026, 10, 26), date(2026, 11, 10)
                )
            mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
