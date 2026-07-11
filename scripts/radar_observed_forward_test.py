#!/usr/bin/env python3
import json
import os
import tempfile
import unittest
from unittest import mock

import radar_json_store as store
import radar_observed_forward as rof


class ObservedForwardTest(unittest.TestCase):
    def test_rejected_observation_is_evaluated(self):
        with tempfile.TemporaryDirectory() as td:
            old_root, old_stats = rof.ROOT, rof.STATS_PATH
            rof.ROOT = td
            rof.STATS_PATH = os.path.join(td, "stats.json")
            try:
                scan = {
                    "record_type": "scan_run",
                    "run": {"run_id": "r1", "trade_date": "20260710",
                            "generated_at": "2026-07-10 15:21:00 KST",
                            "scan_ok": True, "dry_run": False},
                    "observations": [{"code": "263800", "name": "데이타솔루션",
                                      "status": "REJECT_RULE",
                                      "price_snapshot": {"current": 7300},
                                      "turnover": {"turnover_pct": 400.0},
                                      "gate_decisions": []}],
                }
                result = store.write_scan(scan, trade_date="20260710",
                                          observed_at=scan["run"]["generated_at"], root=td)
                self.assertTrue(result.ok, result.error)
                self.assertTrue(rof.build_cohort("20260710"))
                bars = [{"date": "20260710", "open": 7000, "high": 8000,
                         "low": 6900, "close": 7300},
                        {"date": "20260713", "open": 7400, "high": 8200,
                         "low": 7000, "close": 7800}]
                with mock.patch.object(rof.broker, "daily_prices", return_value=bars), \
                        mock.patch.object(rof.time, "sleep"):
                    path = rof.evaluate_cohort("20260710")
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                row = payload["results"][0]
                self.assertEqual(row["status"], "evaluated")
                self.assertTrue(row["next_day"]["touch_up"]["7"])
                self.assertEqual(row["turnover_pct"], 400.0)
            finally:
                rof.ROOT, rof.STATS_PATH = old_root, old_stats

    def test_terminal_rows_are_immutable_and_only_pending_is_retried(self):
        with tempfile.TemporaryDirectory() as td:
            old_root = rof.ROOT
            rof.ROOT = td
            try:
                base = rof.day_dir("20260710")
                os.makedirs(os.path.join(base, "research"), exist_ok=True)
                os.makedirs(os.path.join(base, "evaluation"), exist_ok=True)
                cohort = {
                    "source_run_id": "r1", "population": "p",
                    "observations": [
                        {"code": "1", "name": "확정", "eligible_for_forward_eval": True},
                        {"code": "2", "name": "대기", "eligible_for_forward_eval": True},
                    ],
                }
                with open(os.path.join(base, "research", "krx_close_cohort.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(cohort, f)
                prior = {
                    "source_run_id": "r1", "results": [
                        {"code": "1", "status": "evaluated", "turnover_pct": 10,
                         "next_day": {"high_pct": 7}},
                        {"code": "2", "status": "pending"},
                    ],
                }
                with open(os.path.join(base, "evaluation", "observed_union_next_day.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(prior, f)
                with mock.patch.object(rof.broker, "daily_prices", return_value=[]) as daily, \
                        mock.patch.object(rof.time, "sleep"), \
                        mock.patch.object(rof.store, "rebuild_manifest"):
                    path = rof.evaluate_cohort("20260710")
                with open(path, encoding="utf-8") as f:
                    rows = json.load(f)["results"]
                self.assertEqual(daily.call_count, 1)
                self.assertEqual(rows[0]["status"], "evaluated")
                self.assertEqual(rows[1]["status"], "pending")
            finally:
                rof.ROOT = old_root


if __name__ == "__main__":
    unittest.main(verbosity=2)
