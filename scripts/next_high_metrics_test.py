#!/usr/bin/env python3
import unittest

from next_high_metrics import derive_next_high_metrics


class NextHighMetricsTest(unittest.TestCase):
    def test_rounded_seven_is_not_touch(self):
        value = derive_next_high_metrics(100000, 106995)
        self.assertEqual(value["next_high_pct"], 7.0)
        self.assertFalse(value["touch7"])

    def test_exact_boundaries(self):
        self.assertTrue(derive_next_high_metrics(100, 107)["touch7"])
        self.assertFalse(derive_next_high_metrics(100000, 112999)["touch13"])
        self.assertTrue(derive_next_high_metrics(100, 113)["touch13"])

    def test_missing_raw_does_not_infer_from_pct(self):
        value = derive_next_high_metrics(None, None)
        self.assertEqual(value["metrics_status"], "raw_price_missing")
        self.assertNotIn("touch7", value)


if __name__ == "__main__":
    unittest.main()
