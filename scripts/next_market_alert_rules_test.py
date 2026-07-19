# -*- coding: utf-8 -*-
import unittest

from next_market_alert_rules import (
    evaluate_alert_preview,
    krx_tick,
    minimum_valid_price,
    trading_references,
)


def bars(signal_date="20260716"):
    dates = [
        "20260622", "20260623", "20260624", "20260625", "20260626",
        "20260629", "20260630", "20260701", "20260702", "20260703",
        "20260706", "20260707", "20260708", "20260709", "20260710",
        "20260713", "20260714", "20260715",
    ]
    rows = [{"date": day, "close": 1_000 + i, "volume": 10}
            for i, day in enumerate(dates)]
    rows[13]["close"] = 1_992  # 2026-07-09 = T-5
    rows.append({"date": signal_date, "close": 3_355, "volume": 10})
    return rows


class NextMarketAlertRulesTest(unittest.TestCase):
    def test_tick_boundaries_and_ceil(self):
        self.assertEqual(krx_tick(999, "KOSPI"), 1)
        self.assertEqual(krx_tick(1_000, "KOSPI"), 5)
        self.assertEqual(krx_tick(10_000, "KOSPI"), 50)
        self.assertEqual(krx_tick(100_000, "KOSPI"), 500)
        self.assertEqual(krx_tick(100_000, "KOSDAQ"), 100)
        self.assertEqual(krx_tick(500_000, "KOSPI"), 1_000)
        self.assertEqual(krx_tick(500_000, "KOSDAQ"), 100)
        self.assertEqual(minimum_valid_price(3_187.2, "KOSDAQ"), 3_190)
        self.assertEqual(minimum_valid_price(1_999.2, "KOSDAQ"), 2_000)

    def test_trading_references_skip_signal_and_zero_volume(self):
        rows = bars()
        rows.append({"date": "20260712", "close": 9_999, "volume": 0})
        refs = trading_references(rows, "20260716")
        self.assertEqual(refs[3]["date"], "20260713")
        self.assertEqual(refs[5]["date"], "20260709")
        self.assertEqual(refs[5]["close"], 1_992)

    def test_cp_system_t5_boundary(self):
        common = dict(
            code="413630", name="씨피시스템", signal_date="20260716",
            target_trade_date="20260720", listing_market="KOSDAQ", daily=bars(),
            price_basis="KRX_CURRENT",
        )
        below = evaluate_alert_preview(price=3_185, **common)
        met = evaluate_alert_preview(price=3_190, **common)
        t5_below = next(c for c in below["checks"] if c["rule_id"] == "SHORT_5D_60")
        t5_met = next(c for c in met["checks"] if c["rule_id"] == "SHORT_5D_60")
        self.assertEqual(t5_below["theoretical_price"], 3_187.2)
        self.assertEqual(t5_below["threshold_price"], 3_190)
        self.assertFalse(t5_below["met"])
        self.assertTrue(t5_met["met"])
        self.assertEqual(met["status"], "CONDITION_MET_INTRADAY")

    def test_cp_system_observed_price_margin(self):
        result = evaluate_alert_preview(
            code="413630", name="씨피시스템", signal_date="20260716",
            target_trade_date="20260720", listing_market="KOSDAQ",
            daily=bars(), price=3_355,
            price_basis="KRX_CURRENT",
        )
        t5 = next(c for c in result["checks"] if c["rule_id"] == "SHORT_5D_60")
        self.assertAlmostEqual(t5["current_rate_pct"], 68.4237)
        self.assertEqual(t5["margin_price"], 165)
        self.assertAlmostEqual(t5["margin_pct"], 5.1724)
        self.assertEqual(result["status"], "CONDITION_MET_INTRADAY")

    def test_unverified_auction_never_confirms(self):
        result = evaluate_alert_preview(
            code="413630", name="씨피시스템", signal_date="20260716",
            target_trade_date="20260720", listing_market="KOSDAQ",
            daily=bars(), price=3_355,
            price_basis="KRX_EXPECTED_CLOSE_UNVERIFIED",
        )
        self.assertEqual(result["status"], "AUCTION_PRICE_UNVERIFIED")
        self.assertFalse(result["verified"])

    def test_official_close_status(self):
        result = evaluate_alert_preview(
            code="413630", name="씨피시스템", signal_date="20260716",
            target_trade_date="20260720", listing_market="KOSDAQ",
            daily=bars(), price=3_355,
            price_basis="KRX_OFFICIAL_CLOSE",
        )
        self.assertEqual(result["status"], "CONDITION_MET_CLOSE")
        self.assertTrue(result["verified"])

    def test_higher_alert_uses_separate_track(self):
        result = evaluate_alert_preview(
            code="000001", name="테스트", signal_date="20260716",
            target_trade_date="20260720", listing_market="KOSPI",
            daily=[], price=3_355,
            price_basis="KRX_CURRENT", current_alert="경고",
        )
        self.assertEqual(result["status"], "NOT_APPLICABLE")
        self.assertEqual(result["separate_track"], "alert_release_or_redesignation")

    def test_high_price_kosdaq_uses_100_won_tick(self):
        rows = bars()
        rows[13]["close"] = 156_300  # T-5, 이론 임계가 250,080원
        result = evaluate_alert_preview(
            code="999999", name="고가코스닥", signal_date="20260716",
            target_trade_date="20260720", listing_market="KOSDAQ",
            daily=rows, price=250_100, price_basis="KRX_CURRENT",
        )
        t5 = next(c for c in result["checks"] if c["rule_id"] == "SHORT_5D_60")
        self.assertEqual(t5["threshold_price"], 250_100)
        self.assertTrue(t5["met"])

    def test_missing_listing_market_is_unverified(self):
        result = evaluate_alert_preview(
            code="999999", name="시장미확인", signal_date="20260716",
            target_trade_date="20260720", listing_market=None,
            daily=bars(), price=3_355, price_basis="KRX_CURRENT",
        )
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertFalse(result["verified"])


if __name__ == "__main__":
    unittest.main()
