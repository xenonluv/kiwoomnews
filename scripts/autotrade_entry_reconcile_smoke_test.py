#!/usr/bin/env python3
import unittest
from datetime import datetime, timedelta
from unittest import mock

import autotrade_orders as orders


def pending():
    return {"intent_id": "A-20260713-nxt-1", "state": "ACCEPTED", "code": "A",
            "name": "A", "entry_date": "20260713", "order_date": "20260713",
            "market": "NXT", "requested_qty": 10, "accounted_filled": 0,
            "ref_price": 1000, "alloc_krw": 10000, "rank": 1, "ord_no": "77",
            "audit": {}}


class EntryReconcileTest(unittest.TestCase):
    def test_partial_fill_records_only_confirmed_quantity(self):
        data = {"positions": [], "pending_entries": [pending()]}
        states = [
            {"ordered_qty": 10, "filled_qty": 4, "remaining_qty": 6,
             "avg_fill_price": 990, "ord_no": "77"},
            {"ordered_qty": 10, "filled_qty": 4, "remaining_qty": 0,
             "avg_fill_price": 990, "ord_no": "77"},
        ]
        with mock.patch.object(orders.kt, "order_status", side_effect=states), \
                mock.patch.object(orders.kt, "cancel_order", return_value={"dry": False}), \
                mock.patch.object(orders.ac, "append_trade_event"), \
                mock.patch.object(orders.ac, "notify_trade"):
            self.assertTrue(orders.reconcile_pending_entries(data, persist=lambda: None))
            self.assertEqual(data["positions"][0]["qty_open"], 4)
            self.assertEqual(data["positions"][0]["entry_price"], 990)
            self.assertFalse(orders.reconcile_pending_entries(data, persist=lambda: None))
        self.assertEqual(data["positions"][0]["qty_open"], 4)
        self.assertEqual(data["pending_entries"], [])

    def test_unverified_fill_freezes_entry_price(self):
        data = {"positions": [], "pending_entries": [pending()]}
        status = {"ordered_qty": 10, "filled_qty": 10, "remaining_qty": 0,
                  "avg_fill_price": None, "ord_no": "77"}
        with mock.patch.object(orders.kt, "order_status", return_value=status), \
                mock.patch.object(orders.ac, "append_trade_event"), \
                mock.patch.object(orders.ac, "notify_trade"):
            orders.reconcile_pending_entries(data, persist=lambda: None)
        self.assertEqual(data["positions"][0]["qty_open"], 10)
        self.assertFalse(data["positions"][0]["entry_price_verified"])

    def test_missing_order_number_remains_latched(self):
        row = pending(); row["ord_no"] = None; row["state"] = "SUBMIT_UNKNOWN"
        data = {"positions": [], "pending_entries": [row]}
        with mock.patch.object(orders.ac, "notify_trade", return_value=True):
            self.assertTrue(orders.reconcile_pending_entries(data, persist=lambda: None))
        self.assertEqual(len(data["pending_entries"]), 1)

    def test_web_off_keeps_unknown_pending_without_generic_notification(self):
        row = pending(); row["ord_no"] = None; row["state"] = "SUBMIT_UNKNOWN"
        data = {"positions": [], "pending_entries": [row]}
        with mock.patch.object(orders.ac, "notify_trade") as notify:
            self.assertTrue(orders.reconcile_pending_entries(
                data, persist=lambda: None, alert_pending=False))
        notify.assert_not_called()
        self.assertEqual(data["pending_entries"], [row])

    def test_broker_terminal_fill_is_resolved_before_attention(self):
        row = pending()
        data = {"positions": [], "pending_entries": [row]}
        status = {"ordered_qty": 10, "filled_qty": 10, "remaining_qty": 0,
                  "avg_fill_price": 1000, "ord_no": "77"}
        with mock.patch.object(orders.kt, "order_status", return_value=status), \
                mock.patch.object(orders.ac, "append_trade_event"), \
                mock.patch.object(orders.ac, "notify_trade") as notify:
            self.assertFalse(orders.reconcile_pending_entries(
                data, persist=lambda: None))
        self.assertEqual(data["pending_entries"], [])
        self.assertEqual(data["positions"][0]["qty_open"], 10)
        notify.assert_not_called()

    def test_explicit_dry_never_sends_live_cancel(self):
        data = {"positions": [], "pending_entries": [pending()]}
        status = {"ordered_qty": 10, "filled_qty": 0, "remaining_qty": 10,
                  "avg_fill_price": None, "ord_no": "77"}
        with mock.patch.object(orders.kt, "order_status", return_value=status), \
                mock.patch.object(orders.kt, "cancel_order", return_value={"dry": True}) as cancel:
            self.assertTrue(orders.reconcile_pending_entries(
                data, persist=lambda: None, dry=True))
        self.assertTrue(cancel.call_args.kwargs["dry"])
        self.assertEqual(data["pending_entries"][0]["state"], "CANCEL_DRY_BLOCKED")

    def test_fill_cannot_resurrect_closed_position(self):
        row = pending()
        data = {"positions": [{"entry_intent_id": row["intent_id"], "status": "closed",
                               "qty_open": 0}], "pending_entries": [row]}
        status = {"ordered_qty": 10, "filled_qty": 1, "remaining_qty": 9,
                  "avg_fill_price": 1000, "ord_no": "77"}
        with mock.patch.object(orders.kt, "order_status", return_value=status), \
                mock.patch.object(orders.ac, "notify_trade", return_value=True):
            self.assertTrue(orders.reconcile_pending_entries(data, persist=lambda: None))
        self.assertEqual(data["positions"][0]["qty_open"], 0)
        self.assertGreaterEqual(data["pending_entries"][0]["reconcile_error_count"], 1)

    def test_bad_row_is_isolated_and_next_pending_still_reconciles(self):
        bad = pending()
        bad["intent_id"] = "bad"
        bad["requested_qty"] = "not-an-int"
        bad["reconcile_error_count"] = "also-not-an-int"
        good = pending()
        good["intent_id"] = "good"
        good["code"] = "B"
        good["ord_no"] = "88"
        data = {"positions": [], "pending_entries": [bad, good]}
        statuses = [
            {"ordered_qty": 10, "filled_qty": 0, "remaining_qty": 10,
             "avg_fill_price": None, "ord_no": "77"},
            {"ordered_qty": 10, "filled_qty": 10, "remaining_qty": 0,
             "avg_fill_price": 1010, "ord_no": "88"},
        ]
        with mock.patch.object(orders.kt, "order_status", side_effect=statuses), \
                mock.patch.object(orders.kt, "cancel_order", return_value={"dry": False}), \
                mock.patch.object(orders.ac, "append_trade_event"), \
                mock.patch.object(orders.ac, "notify_trade", return_value=True):
            self.assertTrue(orders.reconcile_pending_entries(data, persist=lambda: None))
        self.assertEqual(data["positions"][0]["entry_intent_id"], "good")
        self.assertEqual(data["positions"][0]["qty_open"], 10)
        self.assertEqual(data["pending_entries"][0]["intent_id"], "bad")
        self.assertGreaterEqual(data["pending_entries"][0]["reconcile_error_count"], 1)

    def test_failed_persist_rolls_back_row_transition_without_double_count(self):
        row = pending()
        data = {"positions": [], "pending_entries": [row]}
        status = {"ordered_qty": 10, "filled_qty": 4, "remaining_qty": 6,
                  "avg_fill_price": 990, "ord_no": "77"}
        calls = 0

        def fail_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("disk full")

        with mock.patch.object(orders.kt, "order_status", return_value=status), \
                mock.patch.object(orders.kt, "cancel_order") as cancel, \
                mock.patch.object(orders.ac, "append_trade_event"), \
                mock.patch.object(orders.ac, "notify_trade", return_value=True):
            orders.reconcile_pending_entries(data, persist=fail_once)
        self.assertEqual(data["pending_entries"][0]["accounted_filled"], 0)
        self.assertEqual(data["positions"], [])
        cancel.assert_not_called()

    def test_stale_unknown_pending_escalates_and_realerts_once_per_day(self):
        row = pending()
        row.update({"ord_no": None, "state": "SUBMIT_UNKNOWN",
                    "created_at": "2026-07-10 15:18:00 KST"})
        data = {"positions": [], "pending_entries": [row]}
        sent = []
        now = datetime(2026, 7, 11, 8, 0, tzinfo=orders.ac.KST)
        with mock.patch.object(orders.ac, "notify_trade",
                               side_effect=lambda text: sent.append(text) or True):
            orders.review_pending_attention(data, persist=lambda: None, now=now)
            orders.review_pending_attention(data, persist=lambda: None,
                                             now=now + timedelta(hours=1))
            orders.review_pending_attention(data, persist=lambda: None,
                                             now=now + timedelta(days=1))
        self.assertEqual(len(sent), 2)
        self.assertEqual(row["manual_review_status"], "EXPIRED_SUSPECTED")
        self.assertEqual(row["escalated_from"], "SUBMIT_UNKNOWN")
        self.assertEqual(len(data["pending_entries"]), 1, "자동 만료/삭제하면 안 된다")

    def test_failed_alert_retries_after_one_hour(self):
        row = pending()
        row.update({"ord_no": None, "state": "SUBMIT_UNKNOWN"})
        data = {"positions": [], "pending_entries": [row]}
        now = datetime(2026, 7, 11, 8, 0, tzinfo=orders.ac.KST)
        with mock.patch.object(orders.ac, "notify_trade", side_effect=[False, True]) as notify:
            orders.review_pending_attention(data, persist=lambda: None, now=now)
            orders.review_pending_attention(data, persist=lambda: None,
                                             now=now + timedelta(minutes=30))
            orders.review_pending_attention(data, persist=lambda: None,
                                             now=now + timedelta(minutes=61))
        self.assertEqual(notify.call_count, 2)
        self.assertEqual(row["last_alert_success_date"], "20260711")

    def test_future_alert_timestamp_does_not_suppress_forever(self):
        row = pending()
        row.update({"ord_no": None, "state": "SUBMIT_UNKNOWN",
                    "last_alert_attempt_at": "2099-01-01 00:00:00 KST"})
        data = {"positions": [], "pending_entries": [row]}
        now = datetime(2026, 7, 11, 8, 0, tzinfo=orders.ac.KST)
        with mock.patch.object(orders.ac, "notify_trade", return_value=True) as notify:
            orders.review_pending_attention(data, persist=lambda: None, now=now)
        notify.assert_called_once()

    def test_non_dict_pending_is_quarantined_without_unlocking_buy(self):
        good = pending()
        good.update({"intent_id": "good-after-invalid", "code": "B", "ord_no": "88"})
        data = {"positions": [], "pending_entries": [None, good]}
        status = {"ordered_qty": 10, "filled_qty": 10, "remaining_qty": 0,
                  "avg_fill_price": 1000, "ord_no": "88"}
        with mock.patch.object(orders.kt, "order_status", return_value=status), \
                mock.patch.object(orders.ac, "append_trade_event"), \
                mock.patch.object(orders.ac, "notify_trade", return_value=True):
            self.assertTrue(orders.reconcile_pending_entries(data, persist=lambda: None))
        row = data["pending_entries"][0]
        self.assertEqual(row["state"], "INVALID_PENDING_ROW")
        self.assertEqual(row["manual_review_status"], "MANUAL_REVIEW_REQUIRED")
        self.assertEqual(data["positions"][0]["entry_intent_id"], "good-after-invalid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
