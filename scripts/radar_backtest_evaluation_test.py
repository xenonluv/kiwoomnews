#!/usr/bin/env python3
import unittest

import radar_backtest as rb


def bar(date, price=100, volume=1000, **overrides):
    out = {"date": date, "open": price, "high": price, "low": price,
           "close": price, "volume": volume}
    out.update(overrides)
    return out


class NextSessionEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.market = [bar("20260716"), bar("20260720")]
        self.signal = bar("20260716", 90)

    def test_actual_market_session_skips_holiday(self):
        self.assertEqual(rb.actual_next_market_session("20260716", self.market), "20260720")

    def test_zero_volume_flat_bar_is_excluded(self):
        result = rb.classify_next_session_bar(
            "20260716", [self.signal, bar("20260720", 90, 0)], self.market)
        self.assertEqual(result["status"], rb.EXCLUDED_UNTRADABLE)
        self.assertEqual(result["reason_code"], "HALT_PLACEHOLDER")

    def test_positive_volume_flat_bar_is_valid(self):
        result = rb.classify_next_session_bar(
            "20260716", [self.signal, bar("20260720", 90, 10)], self.market)
        self.assertEqual(result["status"], rb.EVALUATED)

    def test_missing_volume_and_inconsistent_zero_are_pending(self):
        missing = bar("20260720", 90)
        missing.pop("volume")
        self.assertEqual(rb.classify_next_session_bar(
            "20260716", [self.signal, missing], self.market)["status"], rb.PENDING_DATA_QUALITY)
        inconsistent = bar("20260720", 90, 0, high=91)
        self.assertEqual(rb.classify_next_session_bar(
            "20260716", [self.signal, inconsistent], self.market)["status"], rb.PENDING_DATA_QUALITY)

    def test_late_resume_is_not_next_day(self):
        result = rb.classify_next_session_bar(
            "20260716", [self.signal, bar("20260721", 110, 100)], self.market)
        self.assertEqual(result["status"], rb.EXCLUDED_UNTRADABLE)
        self.assertEqual(result["reason_code"], "LATE_RESUME_BAR")
        self.assertIsNone(result["next"])

    def test_market_session_missing_is_pending(self):
        result = rb.classify_next_session_bar("20260716", [self.signal], [bar("20260716")])
        self.assertEqual(result["status"], rb.PENDING_MARKET_SESSION)

    def test_legacy_result_stays_included_but_explicit_exclusion_does_not(self):
        legacy = {"evaluated": True, "result": {"next_high": 110}}
        excluded = {**legacy, "evaluation_status": rb.EXCLUDED_UNTRADABLE}
        self.assertTrue(rb.is_evaluated_result(legacy))
        self.assertFalse(rb.is_evaluated_result(excluded))


if __name__ == "__main__":
    unittest.main()
