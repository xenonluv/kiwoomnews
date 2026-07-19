#!/usr/bin/env python3
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime

import shakeout_next_day_first_touch as ft


def bar(time, open_, high, low, close=None):
    return {"time": time, "open": open_, "high": high, "low": low,
            "close": open_ if close is None else close, "vol": 1}


class FirstTouchUnitTest(unittest.TestCase):
    def test_opening_gap_stop_precedes_later_rally(self):
        out = ft.classify_first_touch(100, [bar("090000", 94, 110, 93, 105)])
        self.assertEqual(out["first_touch"], "minus5_first")
        self.assertEqual(out["first_touch_basis"], "opening_gap")

    def test_opening_gap_target_precedes_later_drop(self):
        out = ft.classify_first_touch(100, [bar("090000", 108, 109, 92, 96)])
        self.assertEqual(out["first_touch"], "plus7_first")

    def test_target_then_stop(self):
        bars = [bar("090000", 100, 103, 99), bar("091000", 103, 108, 101),
                bar("100000", 100, 101, 94)]
        self.assertEqual(ft.classify_first_touch(100, bars)["first_touch"], "plus7_first")

    def test_intraday_reopen_price_is_known_before_same_bar_range(self):
        bars = [bar("090000", 100, 103, 99), bar("100000", 108, 109, 94, 96)]
        out = ft.classify_first_touch(100, bars)
        self.assertEqual(out["first_touch"], "plus7_first")
        self.assertEqual(out["first_touch_basis"], "minute_open")

    def test_stop_then_target(self):
        bars = [bar("090000", 100, 103, 99), bar("091000", 98, 99, 94),
                bar("100000", 100, 108, 99)]
        self.assertEqual(ft.classify_first_touch(100, bars)["first_touch"], "minus5_first")

    def test_same_minute_is_unknown(self):
        out = ft.classify_first_touch(100, [bar("091000", 100, 108, 94)])
        self.assertEqual(out["first_touch"], "same_minute_unknown")

    def test_neither_and_missing(self):
        self.assertEqual(ft.classify_first_touch(100, [bar("091000", 100, 106, 96)])
                         ["first_touch"], "neither")
        self.assertEqual(ft.classify_first_touch(100, [])["first_touch"], "minute_missing")

    def test_conflicting_duplicate_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicting minute"):
            ft.classify_first_touch(100, [bar("091000", 100, 101, 99),
                                          bar("091000", 100, 102, 99)])

    def test_previous_session_not_calendar_yesterday(self):
        bars = [{"date": "20260710"}, {"date": "20260713"}]
        self.assertEqual(ft.resolve_signal_date("20260713", bars), "20260710")

    def test_real_saved_minute_regressions(self):
        cases = [("002990_UN.json", 17310, "plus7_first"),
                 ("214330_UN.json", 8930, "minus5_first")]
        minute_dir = os.path.join(ft.REPO, "data", "local", "radar_raw",
                                  "2026", "07", "10", "minute")
        for filename, entry, expected in cases:
            path = os.path.join(minute_dir, filename)
            if not os.path.exists(path):
                self.skipTest(f"saved regression fixture missing: {path}")
            with open(path, encoding="utf-8") as handle:
                bars = json.load(handle)["bars"]
            with self.subTest(filename=filename):
                self.assertEqual(ft.classify_first_touch(entry, bars)["first_touch"], expected)


class FirstTouchIntegrationTest(unittest.TestCase):
    def test_cron_is_independent_and_has_no_live_or_push_flag(self):
        installer = os.path.join(ft.REPO, "scripts", "install_cron_kiwoom.sh")
        proc = subprocess.run(["bash", installer, "--dry-run"], cwd=ft.REPO,
                              text=True, capture_output=True, check=True)
        lines = [line for line in proc.stdout.splitlines()
                 if "scripts/shakeout_next_day_first_touch.py" in line]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("40 15 * * 1-5 "))
        self.assertNotIn("AUTOTRADE_LIVE", lines[0])
        self.assertNotIn("--push", lines[0])

    def test_current_day_before_close_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not finalized"):
            ft.run("20260713", history_dir="/does/not/matter", root="/does/not/matter",
                   daily_fetch=lambda *args, **kwargs: [],
                   minute_fetch=lambda *args, **kwargs: {},
                   now=datetime.fromisoformat("2026-07-13T15:39:59+09:00"))

    def test_run_selects_only_final_shakeout_and_uses_confirmed_close(self):
        with tempfile.TemporaryDirectory() as td:
            history_dir = os.path.join(td, "history")
            raw_root = os.path.join(td, "raw")
            os.makedirs(history_dir)
            history = {
                "date": "20260710",
                "suspects": {
                    "000001": {"name": "대상", "shakeout": True, "final": True,
                               "signal_close": 50},
                    "000002": {"name": "탈락", "shakeout": True, "final": False},
                    "000003": {"name": "재매집", "shakeout": False, "final": True},
                },
            }
            with open(os.path.join(history_dir, "20260710.json"), "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False)

            def daily(code, days=15, market="J"):
                self.assertEqual(market, "J")
                if code == ft.BENCHMARK_CODE:
                    return [{"date": "20260710", "close": 1},
                            {"date": "20260713", "close": 1}]
                return [{"date": "20260710", "close": 100},
                        {"date": "20260713", "close": 104}]

            def minute(code, until="153000", market="J"):
                self.assertEqual((code, market), ("000001", "J"))
                return {"trade_date": "20260713",
                        "bars": [bar("090000", 100, 104, 99),
                                 bar("091000", 103, 108, 101)]}

            path, payload = ft.run(
                "20260713", history_dir=history_dir, root=raw_root,
                daily_fetch=daily, minute_fetch=minute,
                now=datetime.fromisoformat("2026-07-13T15:40:00+09:00"))
            self.assertEqual(payload["candidate_n"], 1)
            self.assertEqual(payload["evaluated_n"], 1)
            row = payload["results"][0]
            self.assertEqual(row["entry_price"], 100)
            self.assertEqual(row["first_touch"], "plus7_first")
            self.assertTrue(os.path.exists(path))
            self.assertTrue(os.path.exists(
                os.path.join(raw_root, "2026", "07", "13", "minute", "000001_J.json")))

    def test_trade_date_mismatch_is_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            history_dir = os.path.join(td, "history")
            os.makedirs(history_dir)
            with open(os.path.join(history_dir, "20260710.json"), "w", encoding="utf-8") as f:
                json.dump({"date": "20260710", "suspects": {
                    "000001": {"shakeout": True, "final": True}}}, f)

            def daily(code, days=15, market="J"):
                return [{"date": "20260710", "close": 100},
                        {"date": "20260713", "close": 101}]

            def minute(code, until="153000", market="J"):
                return {"trade_date": "20260710", "bars": [bar("090000", 100, 101, 99)]}

            _, payload = ft.run("20260713", history_dir=history_dir, root=os.path.join(td, "raw"),
                                daily_fetch=daily, minute_fetch=minute,
                                now=datetime.fromisoformat("2026-07-13T15:40:00+09:00"))
            self.assertEqual(payload["evaluated_n"], 0)
            self.assertEqual(payload["results"][0]["status"], "trade_date_mismatch")


if __name__ == "__main__":
    unittest.main(verbosity=2)
