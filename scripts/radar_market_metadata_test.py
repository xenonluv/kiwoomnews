# -*- coding: utf-8 -*-
import json
import unittest
from unittest.mock import patch

import radar


class RadarMarketMetadataTest(unittest.TestCase):
    def setUp(self):
        radar._ALERT_CACHE.clear()
        radar._LISTING_MARKET_CACHE.clear()

    def test_alert_lookup_reuses_exchange_market(self):
        payload = {
            "marketAlertType": {"code": "01"},
            "stockExchangeType": {"code": "KQ"},
        }
        with patch.object(
            radar, "get_bytes", return_value=json.dumps(payload).encode("utf-8")
        ) as fetch:
            self.assertEqual(radar._alert_level("413630"), "주의")
            self.assertEqual(radar._listing_market("413630"), "KOSDAQ")
            self.assertEqual(fetch.call_count, 1)

    def test_unknown_exchange_stays_unverified(self):
        payload = {"marketAlertType": None, "stockExchangeType": {"code": "UNKNOWN"}}
        with patch.object(
            radar, "get_bytes", return_value=json.dumps(payload).encode("utf-8")
        ):
            self.assertIsNone(radar._alert_level("999999"))
            self.assertIsNone(radar._listing_market("999999"))


if __name__ == "__main__":
    unittest.main()
