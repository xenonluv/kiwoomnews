#!/usr/bin/env python3
import os
import unittest
from datetime import datetime
from contextlib import ExitStack
from unittest import mock

import autotrade_executor as executor


class PostSubmitPersistenceTest(unittest.TestCase):
    def _pending(self, ord_no=None, state="SUBMIT_UNKNOWN"):
        return {
            "intent_id": "A-20260711-krx-1", "code": "A", "name": "테스트",
            "market": "KRX", "requested_qty": 10, "ord_no": ord_no, "state": state,
        }

    def test_uncertain_submit_warns_before_failed_save_and_does_not_raise(self):
        order = []
        pending = self._pending()
        with mock.patch.object(executor.ac, "notify_trade",
                               side_effect=lambda text: order.append(("notify", text)) or True), \
                mock.patch.object(executor.ac, "save_positions",
                                  side_effect=lambda data: order.append(("save", data)) or
                                  (_ for _ in ()).throw(OSError("disk full"))), \
                mock.patch.object(executor.ac, "log"):
            ok = executor._persist_post_submit(
                {"positions": [], "pending_entries": [pending]}, pending,
                warning="매수 제출 결과 불명")
        self.assertFalse(ok)
        self.assertEqual(order[0][0], "notify")
        self.assertTrue(any(kind == "save" for kind, _ in order))

    def test_known_order_number_save_failure_sends_emergency_with_order_number(self):
        pending = self._pending(ord_no="9001", state="ACCEPTED")
        messages = []
        with mock.patch.object(executor.ac, "save_positions", side_effect=OSError("disk full")), \
                mock.patch.object(executor.ac, "notify_trade",
                                  side_effect=lambda text: messages.append(text) or True), \
                mock.patch.object(executor.ac, "log"):
            ok = executor._persist_post_submit(
                {"positions": [], "pending_entries": [pending]}, pending)
        self.assertFalse(ok)
        self.assertTrue(any("9001" in message and "저장 실패" in message for message in messages))

    def test_successful_save_records_initial_alert_metadata(self):
        pending = self._pending()
        with mock.patch.object(executor.ac, "save_positions"), \
                mock.patch.object(executor.ac, "notify_trade", return_value=True), \
                mock.patch.object(executor.ac, "today_str", return_value="20260711"):
            self.assertTrue(executor._persist_post_submit(
                {"positions": [], "pending_entries": [pending]}, pending,
                warning="매수 주문번호 없음"))
        self.assertEqual(pending["last_alert_success_date"], "20260711")

    def test_web_off_skips_pending_notification_and_reconcile(self):
        payload = {"positions": [], "pending_entries": [self._pending()]}
        with mock.patch.object(executor.ac, "load_positions", return_value=payload), \
                mock.patch.object(executor.ac, "autotrade_enabled", return_value=False), \
                mock.patch.object(executor.autotrade_orders, "review_pending_attention") as review, \
                mock.patch.object(executor.autotrade_orders, "reconcile_pending_entries") as reconcile, \
                mock.patch.object(executor.ac, "log"):
            executor._run_unlocked("krx", dry=True)
        review.assert_not_called()
        reconcile.assert_not_called()

    def test_post_submit_save_failure_stops_second_candidate_end_to_end(self):
        radar = {
            "generated_at": datetime.now(executor.ac.KST).strftime("%Y-%m-%d %H:%M:%S"),
            "rank_model_version": "rank4-v1",
            "suspects": [
                {"code": "A", "name": "A", "price": 1000, "change_basis": "KRX"},
                {"code": "B", "name": "B", "price": 1000, "change_basis": "KRX"},
            ],
        }
        scenarios = (
            (RuntimeError("submit timeout"), None),
            (None, {"dry": False, "result": {}}),
            (None, {"dry": False, "result": {"ord_no": "9001"}}),
        )
        for submit_error, response in scenarios:
            with self.subTest(submit_error=submit_error, response=response):
                payload = {"positions": [], "pending_entries": []}
                submit = mock.Mock(side_effect=submit_error)
                if submit_error is None:
                    submit.return_value = response
                prepare = mock.Mock(side_effect=lambda code, budget: {
                    "market": "KRX", "code": code, "qty": 10, "ref_price": 1000,
                    "api_id": "kt10000", "body": {}, "label": code,
                })
                env = dict(os.environ, AUTOTRADE_LIVE="1",
                           AUTOTRADE_ORDER_FIELDS_VERIFIED="1")
                patches = (
                    mock.patch.dict(os.environ, env, clear=True),
                    mock.patch.object(executor.ac, "load_positions", return_value=payload),
                    mock.patch.object(executor.ac, "autotrade_enabled", return_value=True),
                    mock.patch.object(executor.ac, "open_positions", return_value=[]),
                    mock.patch.object(executor.ac, "todays_positions", return_value=[]),
                    mock.patch.object(executor.ac, "read_ranks", return_value=[1, 2]),
                    mock.patch.object(executor.ac, "read_radar_snapshot", return_value=radar),
                    mock.patch.object(executor.ac, "radar_snapshot_meta", return_value={
                        "trade_date": executor.ac.today_str(), "valid_for_decision": True,
                        "rank_model_version": "rank4-v1", "top_codes": ["A", "B"],
                    }),
                    mock.patch.object(executor.ac, "already_bought", return_value=False),
                    mock.patch.object(executor.ac, "write_local_decision", return_value=True),
                    mock.patch.object(executor.ac, "append_local_trade_event", return_value=True),
                    mock.patch.object(executor.ac, "read_budget", return_value=1_000_000),
                    mock.patch.object(executor.ac, "deployed_today", return_value=0),
                    mock.patch.object(executor.ac, "save_positions",
                                      side_effect=[None, OSError("disk full")]),
                    mock.patch.object(executor.ac, "notify_trade", return_value=True),
                    mock.patch.object(executor.ac, "log"),
                    mock.patch.object(executor.market_state, "require_trading_day",
                                      return_value=(True, {})),
                    mock.patch.object(executor.kt, "account_holdings", return_value={
                        "summary": {"deposit": 1_000_000}, "holdings": []}),
                    mock.patch.object(executor.kt, "is_nxt_tradable", return_value=False),
                    mock.patch.object(executor.kt, "prepare_buy_market_krx", prepare),
                    mock.patch.object(executor.kt, "submit_prepared_buy", submit),
                )
                with ExitStack() as stack:
                    for patcher in patches:
                        stack.enter_context(patcher)
                    executor._run_unlocked("krx", dry=False)
                self.assertEqual(submit.call_count, 1)
                self.assertEqual(prepare.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
