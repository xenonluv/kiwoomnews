# -*- coding: utf-8 -*-
import json
import unittest
from unittest.mock import patch

import radar


class RadarMarketMetadataTest(unittest.TestCase):
    def setUp(self):
        radar._ALERT_CACHE.clear()
        radar._LISTING_MARKET_CACHE.clear()
        radar._ALERT_META_CACHE.clear()

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
            self.assertEqual(radar._alert_snapshot("413630")["level"], "ATTENTION")
            self.assertEqual(radar._alert_snapshot("413630")["lookup_status"], "VERIFIED")
            self.assertEqual(fetch.call_count, 1)

    def test_unknown_exchange_stays_unverified(self):
        payload = {"marketAlertType": None, "stockExchangeType": {"code": "UNKNOWN"}}
        with patch.object(
            radar, "get_bytes", return_value=json.dumps(payload).encode("utf-8")
        ):
            self.assertIsNone(radar._alert_level("999999"))
            self.assertIsNone(radar._listing_market("999999"))
            self.assertEqual(radar._alert_snapshot("999999")["lookup_status"], "SCHEMA_UNKNOWN")

    def test_verified_none_and_errors_are_distinct(self):
        payload = {"itemCode": "005930", "stockName": "삼성전자",
                   "marketAlertType": None, "stockExchangeType": {"code": "KS"}}
        with patch.object(radar, "get_bytes", return_value=json.dumps(payload).encode()):
            self.assertIsNone(radar._alert_level("005930"))
            self.assertEqual(radar._alert_snapshot("005930")["level"], "NONE")
        radar._ALERT_CACHE.clear(); radar._ALERT_META_CACHE.clear()
        with patch.object(radar, "get_bytes", side_effect=ValueError("bad")):
            self.assertIsNone(radar._alert_level("005930"))
            self.assertEqual(radar._alert_snapshot("005930")["lookup_status"], "ERROR")

    def test_unknown_raw_code_is_schema_unknown(self):
        payload = {"itemCode": "005930", "marketAlertType": {"code": "99"}}
        with patch.object(radar, "get_bytes", return_value=json.dumps(payload).encode()):
            self.assertIsNone(radar._alert_level("005930"))
            self.assertEqual(radar._alert_snapshot("005930")["level"], "UNKNOWN")
            self.assertEqual(radar._alert_snapshot("005930")["lookup_status"], "SCHEMA_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
