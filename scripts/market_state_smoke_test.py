#!/usr/bin/env python3
import unittest
from datetime import datetime

import market_state as ms


class Broker:
    def __init__(self, date): self.date = date
    def daily_prices(self, *args, **kwargs):
        return [] if self.date is None else [{"date": self.date, "close": 100}]


class PreopenBroker(Broker):
    def minute_bars_today_with_meta(self, *args, **kwargs):
        return {"trade_date": self.date, "bars": [], "fetch_status": "empty"}


class MarketStateTest(unittest.TestCase):
    def test_weekday_holiday_fails_closed(self):
        now = datetime(2026, 7, 17, 10, 0, tzinfo=ms.KST)
        state = ms.trading_day_state(Broker("20260716"), now=now)
        self.assertFalse(state["is_trading_day"])
        self.assertEqual(state["reason"], "broker_latest_trade_date_mismatch")

    def test_same_day_bar_confirms(self):
        now = datetime(2026, 7, 13, 10, 0, tzinfo=ms.KST)
        self.assertTrue(ms.trading_day_state(Broker("20260713"), now=now)["is_trading_day"])

    def test_preopen_is_unconfirmed(self):
        now = datetime(2026, 7, 13, 8, 30, tzinfo=ms.KST)
        state = ms.trading_day_state(Broker("20260713"), now=now)
        self.assertFalse(state["is_trading_day"])
        self.assertTrue(state["reason"].startswith("preopen_trade_date_error:"))

    def test_preopen_nxt_date_confirms(self):
        now = datetime(2026, 7, 13, 8, 30, tzinfo=ms.KST)
        state = ms.trading_day_state(PreopenBroker("20260713"), now=now)
        self.assertTrue(state["is_trading_day"])
        self.assertEqual(state["reason"], "confirmed_by_nxt_minute_date")


if __name__ == "__main__":
    unittest.main(verbosity=2)
