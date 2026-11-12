import json
import unittest
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
