#!/usr/bin/env python3
import unittest
from datetime import datetime
from unittest import mock

import radar


class MinuteDateGuardTest(unittest.TestCase):
    def test_stale_symbol_minutes_fail_closed(self):
        stale = "19000101"
        payload = {"trade_date": stale,
                   "bars": [{"time": "150000", "close": 100}], "fetch_status": "ok"}
        with mock.patch.object(radar.kis, "MONEY_MARKET", "UN"), \
                mock.patch.object(radar.kis, "minute_bars_today_with_meta",
                                  return_value=payload):
            with self.assertRaisesRegex(RuntimeError, "STALE_TRADE_DATE"):
                radar._minute_bars_with_fallback("005930")

    def test_un_empty_falls_back_only_to_current_krx_date(self):
        today = datetime.now(radar.KST).strftime("%Y%m%d")
        responses = [
            {"trade_date": None, "bars": [], "fetch_status": "empty"},
            {"trade_date": today, "bars": [{"time": "150000", "close": 100}],
             "fetch_status": "ok"},
        ]
        with mock.patch.object(radar.kis, "MONEY_MARKET", "UN"), \
                mock.patch.object(radar.kis, "minute_bars_today_with_meta",
                                  side_effect=responses):
            bars = radar._minute_bars_with_fallback("005930")
        self.assertEqual(bars[0]["close"], 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
