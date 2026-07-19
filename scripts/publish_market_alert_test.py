# -*- coding: utf-8 -*-
import unittest
from datetime import datetime

import publish


def daily_rows():
    dates = [
        "20260622", "20260623", "20260624", "20260625", "20260626",
        "20260629", "20260630", "20260701", "20260702", "20260703",
        "20260706", "20260707", "20260708", "20260709", "20260710",
        "20260713", "20260714", "20260715",
    ]
    rows = [{"date": day, "close": 2_500, "volume": 10}
            for i, day in enumerate(dates)]
    rows[13]["close"] = 1_992
    return rows


class PublishMarketAlertBadgeTest(unittest.TestCase):
    def test_cp_system_gets_one_static_badge(self):
        suspects = [{
            "code": "413630", "name": "씨피시스템", "signal_date": "20260716",
            "price": 3_355, "change_basis": "KRX", "alert_now": None,
            "listing_market": "KOSDAQ",
        }]
        publish.attach_market_alert_badges(
            suspects,
            now=datetime(2026, 7, 16, 15, 14, tzinfo=publish.KST),
            daily_fetcher=lambda *_args, **_kwargs: daily_rows(),
        )
        badge = suspects[0]["next_market_alert_preview"]
        self.assertEqual(badge["status"], "CONDITION_MET_INTRADAY")
        self.assertEqual(badge["target_trade_date"], "20260720")
        t5 = next(c for c in badge["checks"] if c["rule_id"] == "SHORT_5D_60")
        self.assertEqual(t5["threshold_price"], 3_190)

    def test_below_threshold_has_no_badge(self):
        suspects = [{
            "code": "413630", "name": "씨피시스템", "signal_date": "20260716",
            "price": 3_185, "change_basis": "KRX", "alert_now": None,
            "listing_market": "KOSDAQ",
        }]
        publish.attach_market_alert_badges(
            suspects,
            now=datetime(2026, 7, 16, 15, 14, tzinfo=publish.KST),
            daily_fetcher=lambda *_args, **_kwargs: daily_rows(),
        )
        self.assertIsNone(suspects[0]["next_market_alert_preview"])


if __name__ == "__main__":
    unittest.main()
