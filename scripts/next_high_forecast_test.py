#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import next_high_forecast as nhf


def write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def record(name, high, low, *, evaluated=True):
    return {
        "name": name, "entry": 1000, "final": True, "visible_experimental": True,
        "pattern": "shakeout", "shakeout": True, "suspicion_score": 80,
        "strength_tier": 3, "turnover_2d_pct": 200, "peak_dd_pct": -35,
        "evaluated": evaluated,
        "result": ({"entry": 1000, "next_high": 1000 + high * 10,
                    "next_high_pct": high, "next_low_pct": low,
                    "return_pct": 1, "date": "20260102"} if evaluated else None),
    }


class ForecastTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        radar_row = {
            "code": "123456", "name": "테스트", "signal_date": "20260101",
            "signal_close": 1000, "pattern": "shakeout", "shakeout": True,
            "suspicion_score": 80, "strength_tier": 3,
            "turnover_2d_pct": 200, "peak_dd_pct": -35,
            "rank_bucket": 4, "material": {"grade": "N"},
        }
        write(os.path.join(self.repo, "web/data/radar.json"),
              {"generated_at": "2026-01-01 20:00 KST", "suspects": [radar_row]})
        write(os.path.join(self.repo, "web/data/performance.json"), {
            "as_of": "2026-01-01 22:00 KST",
            "rank_bucket_stats_retro": {"exclusive_all": {"cells": []}},
            "rank_bucket_stats_forward": {"eod": {"cells": []}},
        })
        for index, (high, low) in enumerate(((5, -2), (10, -6), (15, -4), (20, -7)), 1):
            write(os.path.join(self.repo, f"data/radar_history/202512{index:02d}.json"), {
                "date": f"202512{index:02d}",
                "suspects": {f"00000{index}": record(f"과거{index}", high, low)},
            })
        write(os.path.join(self.repo, "data/radar_history/20260101.json"), {
            "date": "20260101", "suspects": {"123456": record("테스트", 0, 0, evaluated=False)}})
        write(os.path.join(self.repo, "data/shakeout_backfill.json"), {"samples": []})

    def tearDown(self):
        self.tmp.cleanup()

    def test_current_signal_generates_deterministic_touch_table(self):
        report = nhf.analyze("테스트", repo=self.repo, allow_network=False)
        self.assertTrue(report["forecast_valid"])
        self.assertEqual(report["bucket_evidence"]["n"], 4)
        self.assertEqual(report["bucket_evidence"]["touch"]["7"]["rate"], 75.0)
        self.assertEqual(report["bucket_evidence"]["touch"]["15"]["rate"], 50.0)
        self.assertEqual(report["forecast"]["point_high_pct"], 12.5)
        self.assertEqual(report["forecast"]["point_high_price"], 1125)
        self.assertEqual(report["signal"]["rank_bucket"], 4)
        self.assertEqual(report["signal"]["current_retro_bucket"], 5)
        self.assertIsNone(report["signal"]["rank_reason"])
        self.assertIn("bucket 5", report["signal"]["current_retro_reason"])
        self.assertTrue(any("bucket 4" in w and "bucket 5" in w
                            for w in report["warnings"]))

    def test_historical_name_is_not_presented_as_current_forecast(self):
        report = nhf.analyze("과거1", repo=self.repo, allow_network=False)
        self.assertFalse(report["forecast_valid"])
        self.assertEqual(report["status"], "no_current_signal")

    def test_forecast_price_uses_valid_krx_tick(self):
        self.assertEqual(nhf._price_at(5580, 16.21), 6480)

    def test_current_blocked_signal_is_not_replaced_by_old_forecast(self):
        path = os.path.join(self.repo, "web/data/radar.json")
        write(path, {
            "generated_at": "2026-01-01 20:00 KST",
            "suspects": [],
            "blocked_suspects": [{
                "code": "123456", "name": "테스트", "signal_date": "20260101",
                "blocked_reason": "다음 거래일 거래정지",
                "next_session_eligibility": {
                    "status": "HALT_CONFIRMED", "target_trade_date": "20260102",
                    "tradable_next_session": False, "recommendable": False,
                    "auto_buy_allowed": False, "reason": "다음 거래일 거래정지",
                },
            }],
        })
        report = nhf.analyze("테스트", repo=self.repo, allow_network=False)
        self.assertFalse(report["forecast_valid"])
        self.assertEqual(report["status"], "next_session_ineligible")
        self.assertEqual(report["target_trade_date"], "20260102")
        self.assertIn("거래정지", report["message"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
