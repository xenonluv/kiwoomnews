#!/usr/bin/env python3
"""autotrade_monitor NXT 매도 체결확인 회귀 테스트.

크리티컬 감사 2026-07-11 확정 결함의 영구 재현 방지:
  구현이 체결 기준선을 봇 기록(qty_open)으로 잡으면, 계좌에 같은 종목 수동 보유분이
  있을 때(M≥q) 완전 체결이 '미체결'로 오판돼 매분 재주문 루프가 수동 보유분을 청산했다.
  수정 후 = 주문 전 JSON pending 잠금 + 원주문번호 kt00007 종결 확인.
  잔고 반영 지연·수동 매도와 무관하게 미종결 주문이 있으면 후속 매도를 차단한다.
"""
import copy
import unittest
from unittest import mock

import autotrade_monitor as am


def _pos(qty_open=10, entry=10000, manual_baseline_qty=50):
    return {
        "id": "012345-20260713-krx", "code": "012345", "name": "테스트종목",
        "entry_date": am.ac.today_str(), "entry_price": entry,
        "qty": qty_open, "qty_open": qty_open, "market": "KRX",
        "alloc_krw": 1_000_000, "rank": 1, "tp1_done": False, "status": "open",
        "opened_at": "2026-07-13 15:18:30",
        "manual_baseline_qty": manual_baseline_qty,
    }


class FakeBroker:
    """즉시 체결형 가짜 계좌 — 계좌 전체 수량(봇+수동 합산)을 시뮬레이션."""

    def __init__(self, account_qty, fill=True, holdings_errors=None):
        self.account_qty = account_qty      # 계좌 전체(수동 포함) 수량
        self.fill = fill                    # sell 주문 즉시 체결 여부
        self.sell_orders = []               # (code, qty, market)
        self.cancels = []                   # (code, ord_no)
        self._holdings_errors = list(holdings_errors or [])  # 호출 순서별 예외 주입
        self.status = None

    def account_holdings(self):
        if self._holdings_errors:
            step = self._holdings_errors.pop(0)
            if step == "raise":
                raise RuntimeError("잔고조회 실패(모의)")
        return {"holdings": [{"code": "012345", "qty": self.account_qty,
                              "tradable_qty": self.account_qty}],
                "summary": {"deposit": 1_000_000}}

    def sell_market(self, code, qty, market="KRX", dry=True):
        self.sell_orders.append((code, int(qty), market))
        if self.fill:
            self.account_qty = max(0, self.account_qty - int(qty))
        self.status = {
            "ord_no": "77001", "ordered_qty": int(qty),
            "filled_qty": int(qty) if self.fill else 0,
            "remaining_qty": 0 if self.fill else int(qty),
        }
        return {"dry": False, "plan": {}, "result": {"ord_no": "77001"}}

    def cancel_order(self, code, orig_ord_no, market="KRX", qty=0, dry=True):
        self.cancels.append((code, str(orig_ord_no)))
        if self.status:
            self.status["remaining_qty"] = 0
        return {"dry": False}

    def order_status(self, code, ord_no, market="NXT", order_date="", side="sell"):
        return copy.deepcopy(self.status)


class NxtSellConfirmTest(unittest.TestCase):
    def _patched(self, broker, price=9400):
        return (
            mock.patch.object(am.kt, "account_holdings", broker.account_holdings),
            mock.patch.object(am.kt, "sell_market", broker.sell_market),
            mock.patch.object(am.kt, "cancel_order", broker.cancel_order),
            mock.patch.object(am.kt, "order_status", broker.order_status),
            mock.patch.object(am.kw, "current_price", return_value=price),
            mock.patch.object(am.ac, "log"),
            mock.patch.object(am.ac, "notify_trade"),   # 실텔레그램 송신 차단
            mock.patch.object(am.ac, "past_force_exit", return_value=False),
            mock.patch.dict(am.os.environ, {"AUTOTRADE_LIVE": "1"}),
            mock.patch.object(am.ac, "append_trade_event",
                              side_effect=lambda ev: self.events.append(ev)),
        )

    def setUp(self):
        self.events = []
        self.persisted = []

    def _run_ticks(self, broker, pos, n=6, price=9400):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._patched(broker, price=price):
                stack.enter_context(p)
            for _ in range(n):
                if pos.get("status") != "open":
                    break
                am.check_position(pos, dry=False, acct_by_code=None,
                                  session="nxt_premarket",
                                  persist=lambda: self.persisted.append(copy.deepcopy(pos)))
        return pos

    def test_original_defect_manual_shares_not_liquidated(self):
        """원 시나리오: 봇 10주 + 수동 50주, 프리마켓 -6% 손절.
        수정 전: 6틱 → 매도 주문 6건(60주) = 수동분 청산 / 수정 후: 정확히 1건(10주)."""
        broker = FakeBroker(account_qty=60)   # 봇 10 + 수동 50
        pos = self._run_ticks(broker, _pos(qty_open=10), n=6)
        self.assertEqual(len(broker.sell_orders), 1, broker.sell_orders)
        self.assertEqual(broker.sell_orders[0], ("012345", 10, "NXT"))
        self.assertEqual(broker.account_qty, 50, "수동 보유 50주는 그대로여야 한다")
        self.assertEqual(pos["qty_open"], 0)
        self.assertEqual(pos["status"], "closed")
        exits = [e for e in self.events if e["type"] == "exit"]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0]["sold_qty"], 10)

    def test_pre_order_holdings_failure_blocks_blind_order(self):
        """주문 전 잔고조회 실패 → 체결확인 불가 주문은 아예 내지 않는다."""
        broker = FakeBroker(account_qty=60, holdings_errors=["raise"])
        self._run_ticks(broker, _pos(), n=1)
        self.assertEqual(broker.sell_orders, [])

    def test_zero_held_blocks_order(self):
        """실계좌 보유 0(수동 전량매도 등) → 매도 주문 없음(정리는 강제청산 로직 몫)."""
        broker = FakeBroker(account_qty=0)
        pos = self._run_ticks(broker, _pos(), n=1)
        self.assertEqual(broker.sell_orders, [])
        self.assertEqual(pos["status"], "open")

    def test_partial_fill_counts_only_delta_and_cancels_rest(self):
        """부분체결(10주 주문, 4주만 체결) → sold=4·잔량 6 유지·잔여주문 취소."""
        broker = FakeBroker(account_qty=60, fill=False)

        def partial_sell(code, qty, market="KRX", dry=True):
            broker.sell_orders.append((code, int(qty), market))
            broker.account_qty -= 4
            broker.status = {
                "ord_no": "77002", "ordered_qty": int(qty),
                "filled_qty": 4, "remaining_qty": int(qty) - 4,
            }
            return {"dry": False, "result": {"ord_no": "77002"}}

        with mock.patch.object(broker, "sell_market", partial_sell):
            pos = self._run_ticks(broker, _pos(qty_open=10), n=3)
        self.assertEqual(pos["qty_open"], 6)
        self.assertEqual(pos["status"], "open")
        self.assertEqual(len(broker.cancels), 1)
        self.assertNotIn("pending_exit", pos)
        exits = [e for e in self.events if e["type"] == "exit"]
        self.assertEqual(exits[0]["sold_qty"], 4)

    def test_no_fill_cancels_and_keeps_position(self):
        """미체결(계좌 무변화) → sold 0·포지션 유지·잔여주문 취소(스택 차단)."""
        broker = FakeBroker(account_qty=60, fill=False)
        pos = self._run_ticks(broker, _pos(qty_open=10), n=3)
        self.assertEqual(pos["qty_open"], 10)
        self.assertEqual(pos["status"], "open")
        self.assertEqual(len(broker.sell_orders), 1)
        self.assertEqual(len(broker.cancels), 1)
        self.assertEqual(self.events, [])
        self.assertNotIn("pending_exit", pos)

    def test_holdings_lag_does_not_override_order_status_fill(self):
        """접수 뒤 잔고 재조회 대신 원주문 체결조회만으로 정상 반영한다."""
        broker = FakeBroker(account_qty=60, holdings_errors=[None, "raise", None])
        pos = self._run_ticks(broker, _pos(qty_open=10), n=2)
        self.assertEqual(pos["qty_open"], 0)
        self.assertEqual(pos["status"], "closed")
        self.assertEqual(len(broker.cancels), 0, "전량 체결 원주문은 취소하지 않는다")

    def test_account_shortfall_blocks_order(self):
        """기준선+봇 기록보다 실계좌가 적으면 수동매도 가능성이 있어 발주를 차단한다."""
        broker = FakeBroker(account_qty=7)
        self._run_ticks(broker, _pos(qty_open=10), n=1)
        self.assertEqual(broker.sell_orders, [])

    def test_delayed_holdings_update_never_reorders_or_sells_manual_shares(self):
        """체결 후 잔고가 두 번 stale이어도 다음 틱은 재주문 없이 pending만 재조정한다."""
        broker = FakeBroker(account_qty=60)
        reported = iter([60, 60, 60, 50])

        def lagged_holdings():
            qty = next(reported, broker.account_qty)
            return {"holdings": [{"code": "012345", "qty": qty,
                                   "tradable_qty": qty}], "summary": {}}

        with mock.patch.object(broker, "account_holdings", lagged_holdings):
            pos = self._run_ticks(broker, _pos(qty_open=10), n=2)
        self.assertEqual(broker.sell_orders, [("012345", 10, "NXT")])
        self.assertEqual(broker.account_qty, 50, "수동 보유 50주는 보존되어야 한다")
        self.assertEqual(pos["qty_open"], 0)
        self.assertEqual(pos["status"], "closed")
        self.assertNotIn("pending_exit", pos)
        self.assertTrue(any("pending_exit" in snap for snap in self.persisted),
                        "주문 전에 pending 잠금이 원장에 저장되어야 한다")

    def test_cancel_failure_keeps_durable_latch_and_blocks_next_tick(self):
        """잔여주문 취소 실패 시 경고만 하지 않고 다음 틱 신규 주문을 차단한다."""
        broker = FakeBroker(account_qty=60, fill=False)

        def cancel_failure(*args, **kwargs):
            raise RuntimeError("취소 응답 불명")

        with mock.patch.object(broker, "cancel_order", cancel_failure):
            pos = self._run_ticks(broker, _pos(qty_open=10), n=2)
        self.assertEqual(len(broker.sell_orders), 1)
        self.assertIn("pending_exit", pos)
        self.assertEqual(pos["pending_exit"]["state"], "cancel_retry_failed")
        self.assertFalse(pos["pending_exit"]["cancel_confirmed"])

    def test_manual_sale_delta_is_not_counted_as_bot_fill(self):
        """동일 종목 수동매도로 잔고가 줄어도 주문 TR이 미체결이면 봇 원장은 차감하지 않는다."""
        broker = FakeBroker(account_qty=60, fill=False)
        reported = iter([60, 50])

        def holdings_after_manual_sale():
            qty = next(reported, 50)
            return {"holdings": [{"code": "012345", "qty": qty,
                                   "tradable_qty": qty}], "summary": {}}

        with mock.patch.object(broker, "account_holdings", holdings_after_manual_sale):
            pos = self._run_ticks(broker, _pos(qty_open=10), n=2)
        self.assertEqual(len(broker.sell_orders), 1)
        self.assertEqual(pos["qty_open"], 10)
        self.assertEqual(self.events, [])
        self.assertIn("pending_exit", pos)

    def test_restart_recovers_fill_from_persisted_order_number(self):
        """주문 뒤 프로세스가 종료돼도 JSON pending을 다시 읽어 중복주문 없이 체결을 반영한다."""
        broker = FakeBroker(account_qty=60)
        reported = iter([60, 60, 60])

        def stale_holdings():
            qty = next(reported, broker.account_qty)
            return {"holdings": [{"code": "012345", "qty": qty,
                                   "tradable_qty": qty}], "summary": {}}

        with mock.patch.object(broker, "account_holdings", stale_holdings):
            first_process = self._run_ticks(broker, _pos(qty_open=10), n=1)
        self.assertIn("pending_exit", first_process)
        restarted = copy.deepcopy(self.persisted[-1])
        self._run_ticks(broker, restarted, n=1)
        self.assertEqual(len(broker.sell_orders), 1)
        self.assertEqual(restarted["qty_open"], 0)
        self.assertEqual(restarted["status"], "closed")

    def test_order_status_regression_keeps_latch(self):
        """이미 반영한 체결량보다 주문조회가 작아지면 잠금을 풀거나 재주문하지 않는다."""
        broker = FakeBroker(account_qty=56, fill=False)
        broker.status = {
            "ord_no": "77001", "ordered_qty": 10,
            "filled_qty": 0, "remaining_qty": 0,
        }
        pos = _pos(qty_open=6)
        pos["pending_exit"] = {
            "ord_no": "77001", "order_date": "20260711",
            "requested_qty": 10, "accounted_filled": 4,
            "reason": "손절", "price": 9400, "cancel_confirmed": True,
        }
        self._run_ticks(broker, pos, n=1)
        self.assertEqual(pos["qty_open"], 6)
        self.assertIn("pending_exit", pos)
        self.assertEqual(broker.sell_orders, [])

    def test_pending_persist_failure_blocks_order_before_broker(self):
        """주문 전 핵심 원장에 잠금을 못 쓰면 브로커 주문을 호출하지 않는다."""
        broker = FakeBroker(account_qty=60)
        pos = _pos(qty_open=10)
        from contextlib import ExitStack
        with ExitStack() as stack:
            for patcher in self._patched(broker, price=9400):
                stack.enter_context(patcher)
            changed = am.check_position(
                pos, dry=False, session="nxt_premarket",
                persist=lambda: (_ for _ in ()).throw(OSError("disk full")))
        self.assertFalse(changed)
        self.assertEqual(broker.sell_orders, [])

    def test_krx_receipt_is_not_recorded_as_fill(self):
        """KRX 시장가도 접수 직후에는 원장을 줄이지 않고 원주문 체결조회까지 기다린다."""
        broker = FakeBroker(account_qty=60)
        pos = _pos(qty_open=10)
        from contextlib import ExitStack
        with ExitStack() as stack:
            for patcher in self._patched(broker, price=9400):
                stack.enter_context(patcher)
            am.check_position(pos, dry=False, session="krx",
                              persist=lambda: self.persisted.append(copy.deepcopy(pos)))
            self.assertEqual(pos["qty_open"], 10)
            self.assertIn("pending_exit", pos)
            self.assertEqual(pos["pending_exit"]["market"], "KRX")
            am.check_position(pos, dry=False, session="krx",
                              persist=lambda: self.persisted.append(copy.deepcopy(pos)))
        self.assertEqual(pos["qty_open"], 0)
        self.assertEqual(pos["status"], "closed")

    def test_dry_pending_reconcile_does_not_live_cancel(self):
        broker = FakeBroker(account_qty=60, fill=False)
        broker.status = {"ord_no": "77001", "ordered_qty": 10,
                         "filled_qty": 0, "remaining_qty": 10}
        pos = _pos()
        pos["pending_exit"] = {
            "market": "NXT", "ord_no": "77001", "order_date": "20260711",
            "requested_qty": 10, "accounted_filled": 0,
        }
        with mock.patch.object(am.kt, "order_status", broker.order_status), \
                mock.patch.object(am.kt, "cancel_order", return_value={"dry": True}) as cancel:
            am.check_position(pos, dry=True, persist=lambda: None)
        self.assertTrue(cancel.call_args.kwargs["dry"])

    def test_unrelated_pending_entry_does_not_block_other_position(self):
        a = _pos()
        a["entry_intent_id"] = "A-intent"
        pending_b = [{"intent_id": "B-intent", "code": "999999"}]
        self.assertFalse(am._blocked_by_pending_entry(a, pending_b))
        self.assertTrue(am._blocked_by_pending_entry(
            a, [{"intent_id": "A-intent", "code": "012345"}]))

    def test_run_keeps_monitoring_unrelated_open_when_another_entry_is_unknown(self):
        a = _pos()
        a["entry_intent_id"] = "A-intent"
        data = {"positions": [a], "pending_entries": [
            {"intent_id": "B-intent", "code": "999999", "state": "SUBMIT_UNKNOWN"}
        ]}
        with mock.patch.object(am.ac, "load_positions", return_value=data), \
                mock.patch.object(am.ac, "market_session", return_value="krx"), \
                mock.patch.object(am.ac, "open_positions", return_value=[a]), \
                mock.patch.object(am.ac, "past_force_exit", return_value=False), \
                mock.patch.object(am.autotrade_orders, "reconcile_pending_entries",
                                  return_value=True), \
                mock.patch.object(am.autotrade_orders, "review_pending_attention"), \
                mock.patch.object(am, "check_position", return_value=False) as check, \
                mock.patch.object(am.ac, "log"):
            am._run_unlocked(dry=True)
        check.assert_called_once()

    def test_linked_pending_reaches_force_exit_instead_of_being_skipped(self):
        pos = _pos()
        pos.update({"entry_intent_id": "A-intent", "entry_date": "20260710"})
        data = {"positions": [pos], "pending_entries": [
            {"intent_id": "A-intent", "code": pos["code"],
             "state": "CANCEL_UNKNOWN", "order_date": "20260710"}
        ]}
        with mock.patch.object(am.ac, "load_positions", return_value=data), \
                mock.patch.object(am.ac, "market_session", return_value="krx"), \
                mock.patch.object(am.ac, "open_positions", return_value=[pos]), \
                mock.patch.object(am.ac, "past_force_exit", return_value=True), \
                mock.patch.object(am.kt, "account_holdings", return_value={"holdings": []}), \
                mock.patch.object(am.autotrade_orders, "review_pending_attention"), \
                mock.patch.object(am.autotrade_orders, "reconcile_pending_entries",
                                  return_value=True), \
                mock.patch.object(am, "check_position", return_value=False) as check, \
                mock.patch.object(am.ac, "log"):
            am._run_unlocked(dry=True)
        self.assertTrue(check.called)
        self.assertTrue(check.call_args.kwargs["entry_pending"])

    def test_outer_reconcile_failure_does_not_stop_unrelated_position(self):
        pos = _pos()
        data = {"positions": [pos], "pending_entries": [
            {"intent_id": "bad", "code": "999999", "state": "STATUS_INCONSISTENT"}
        ]}
        with mock.patch.object(am.ac, "load_positions", return_value=data), \
                mock.patch.object(am.ac, "market_session", return_value="krx"), \
                mock.patch.object(am.ac, "open_positions", return_value=[pos]), \
                mock.patch.object(am.ac, "past_force_exit", return_value=False), \
                mock.patch.object(am.autotrade_orders, "review_pending_attention"), \
                mock.patch.object(am.autotrade_orders, "reconcile_pending_entries",
                                  side_effect=ValueError("bad row")), \
                mock.patch.object(am, "check_position", return_value=False) as check, \
                mock.patch.object(am.ac, "notify_trade"), \
                mock.patch.object(am.ac, "log"):
            am._run_unlocked(dry=True)
        check.assert_called_once()

    def test_non_dict_pending_does_not_kill_unrelated_position_check(self):
        pos = _pos()
        data = {"positions": [pos], "pending_entries": [None]}
        with mock.patch.object(am.ac, "load_positions", return_value=data), \
                mock.patch.object(am.ac, "market_session", return_value="krx"), \
                mock.patch.object(am.ac, "open_positions", return_value=[pos]), \
                mock.patch.object(am.ac, "past_force_exit", return_value=False), \
                mock.patch.object(am.autotrade_orders, "review_pending_attention"), \
                mock.patch.object(am.autotrade_orders, "reconcile_pending_entries",
                                  return_value=True), \
                mock.patch.object(am, "check_position", return_value=False) as check, \
                mock.patch.object(am.ac, "log"):
            am._run_unlocked(dry=True)
        check.assert_called_once()

    def test_closed_session_does_not_alert_unverified_local_pending(self):
        data = {"positions": [], "pending_entries": [
            {"intent_id": "A", "code": "012345", "state": "SUBMIT_UNKNOWN"}
        ]}
        with mock.patch.object(am.ac, "load_positions", return_value=data), \
                mock.patch.object(am.ac, "market_session", return_value="closed"), \
                mock.patch.object(am.autotrade_orders, "review_pending_attention") as review, \
                mock.patch.object(am.autotrade_orders, "reconcile_pending_entries") as reconcile, \
                mock.patch.object(am.kt, "cancel_order") as cancel, \
                mock.patch.object(am.ac, "log"):
            am._run_unlocked(dry=False)
        review.assert_not_called()
        reconcile.assert_not_called()
        cancel.assert_not_called()

    def test_web_off_reconciles_pending_without_generic_buy_alert(self):
        data = {"positions": [], "pending_entries": [
            {"intent_id": "A", "code": "012345", "state": "ACCEPTED"}
        ]}
        with mock.patch.object(am.ac, "load_positions", return_value=data), \
                mock.patch.object(am.ac, "market_session", return_value="krx"), \
                mock.patch.object(am.ac, "autotrade_enabled", return_value=False), \
                mock.patch.object(am.autotrade_orders, "reconcile_pending_entries",
                                  return_value=False) as reconcile, \
                mock.patch.object(am.ac, "open_positions", return_value=[]), \
                mock.patch.object(am.ac, "log"):
            am._run_unlocked(dry=True)
        reconcile.assert_called_once_with(data, dry=True, alert_pending=False)

    def test_linked_pending_allows_stop_loss_but_defers_profit_exit(self):
        pos = _pos()
        with mock.patch.object(am.kw, "current_price", return_value=9400), \
                mock.patch.object(am, "_sell", return_value=0) as sell:
            am.check_position(pos, dry=True, session="krx", entry_pending=True)
        sell.assert_called_once()
        with mock.patch.object(am.kw, "current_price", return_value=10800), \
                mock.patch.object(am, "_sell", return_value=0) as sell:
            am.check_position(pos, dry=True, session="krx", entry_pending=True)
        sell.assert_not_called()

    def test_time_force_exit_continues_when_current_price_is_unavailable(self):
        pos = _pos(qty_open=4)
        pos["entry_date"] = "20260710"
        holdings = {pos["code"]: {"qty": 4, "tradable_qty": 4}}
        with mock.patch.object(am.kw, "current_price", side_effect=RuntimeError("quote down")), \
                mock.patch.object(am.ac, "past_force_exit", return_value=True), \
                mock.patch.object(am, "_sell", return_value=0) as sell:
            am.check_position(pos, dry=True, acct_by_code=holdings, session="krx")
        sell.assert_called_once()
        self.assertEqual(sell.call_args.args[1], 4)
        self.assertEqual(sell.call_args.kwargs["cur"], 0)

        normal = _pos(qty_open=4)
        with mock.patch.object(am.kw, "current_price", return_value=0), \
                mock.patch.object(am.ac, "past_force_exit", return_value=False), \
                mock.patch.object(am, "_sell", return_value=0) as normal_sell:
            am.check_position(normal, dry=True, session="krx")
        normal_sell.assert_not_called()

    def test_force_exit_to_zero_waits_for_entry_terminal_and_late_fill_is_managed(self):
        pos = _pos(qty_open=4)
        pos["entry_intent_id"] = "A-intent"
        pos["entry_date"] = "20260710"
        pos["entry_pending_unresolved"] = True
        pos["pending_exit"] = {
            "market": "KRX", "ord_no": "9001", "order_date": "20260711",
            "requested_qty": 4, "accounted_filled": 0,
            "reason": "강제청산", "close_reason": "force_exit_rotation",
        }
        exit_status = {"ordered_qty": 4, "filled_qty": 4, "remaining_qty": 0,
                       "ord_no": "9001"}
        with mock.patch.object(am.kt, "order_status", return_value=exit_status), \
                mock.patch.object(am.ac, "append_trade_event"):
            am._reconcile_pending_exit(pos, persist=lambda: None)
        self.assertEqual(pos["qty_open"], 0)
        self.assertEqual(pos["status"], "open")
        self.assertTrue(pos["awaiting_entry_terminal"])

        with mock.patch.object(am.kw, "current_price", return_value=9000), \
                mock.patch.object(am.ac, "past_force_exit", return_value=True):
            am.check_position(pos, dry=True, acct_by_code={}, session="krx",
                              entry_pending=True)
        self.assertEqual(pos["status"], "open", "보유 0이어도 pending 종결 전 조기 close 금지")

        entry = {"intent_id": "A-intent", "state": "PARTIAL", "code": pos["code"],
                 "name": pos["name"], "entry_date": "20260710", "order_date": "20260710",
                 "market": "KRX", "requested_qty": 10, "accounted_filled": 4,
                 "ref_price": 10000, "ord_no": "77", "audit": {}}
        entry_status = {"ordered_qty": 10, "filled_qty": 6, "remaining_qty": 4,
                        "avg_fill_price": 10000, "ord_no": "77"}
        data = {"positions": [pos], "pending_entries": [entry]}
        with mock.patch.object(am.autotrade_orders.ac, "append_trade_event"), \
                mock.patch.object(am.autotrade_orders.ac, "notify_trade"):
            am.autotrade_orders._apply_fill(data, entry, entry_status, newly_filled=2)
        self.assertEqual(pos["qty_open"], 2)
        self.assertEqual(pos["status"], "open")
        with mock.patch.object(am.kw, "current_price", return_value=9000), \
                mock.patch.object(am.ac, "past_force_exit", return_value=True), \
                mock.patch.object(am, "_sell", return_value=0) as sell:
            am.check_position(
                pos, dry=True,
                acct_by_code={pos["code"]: {"qty": 2, "tradable_qty": 2}},
                session="krx", entry_pending=True)
        self.assertEqual(sell.call_args.args[1], 2, "늦은 체결 2주를 다시 강제청산해야 한다")

    def test_tp1_partial_fill_enables_breakeven_and_zero_fill_does_not(self):
        pos = _pos(qty_open=10)
        pos["pending_exit"] = {
            "market": "NXT", "ord_no": "tp1", "order_date": "20260711",
            "requested_qty": 5, "accounted_filled": 0, "mark_tp1": True,
        }
        partial = {"ordered_qty": 5, "filled_qty": 2, "remaining_qty": 0,
                   "ord_no": "tp1", "cancel_confirmed": True}
        with mock.patch.object(am.kt, "order_status", return_value=partial), \
                mock.patch.object(am.ac, "append_trade_event"):
            am._reconcile_pending_exit(pos, persist=lambda: None)
        self.assertTrue(pos["tp1_done"])
        self.assertNotIn("tp1_remaining_qty", pos)

        with mock.patch.object(am.kw, "current_price", return_value=10050), \
                mock.patch.object(am, "_sell", return_value=0) as sell:
            am.check_position(pos, dry=True, session="krx")
        self.assertIn("본전 방어", sell.call_args.args[2])

        zero = _pos(qty_open=10)
        zero["pending_exit"] = {
            "market": "NXT", "ord_no": "tp0", "order_date": "20260711",
            "requested_qty": 5, "accounted_filled": 0, "mark_tp1": True,
        }
        no_fill = {"ordered_qty": 5, "filled_qty": 0, "remaining_qty": 0,
                   "ord_no": "tp0", "cancel_confirmed": True}
        with mock.patch.object(am.kt, "order_status", return_value=no_fill):
            am._reconcile_pending_exit(zero, persist=lambda: None)
        self.assertFalse(zero["tp1_done"])


class OrderStatusTest(unittest.TestCase):
    def test_kt00007_original_order_is_normalized(self):
        response = {"acnt_ord_cntr_prps_dtl": [{
            "ord_no": "0017196", "stk_cd": "A012345",
            "ord_qty": "0000000010", "cntr_qty": "0000000004",
            "ord_remnq": "0000000006", "acpt_tp": "접수", "mdfy_cncl": "",
        }]}
        with mock.patch.object(am.kt.kw, "_call", return_value=response) as call:
            status = am.kt.order_status("012345", "17196", order_date="20260711")
        self.assertEqual(status["filled_qty"], 4)
        self.assertEqual(status["remaining_qty"], 6)
        body = call.call_args.args[2]
        self.assertEqual(body["dmst_stex_tp"], "NXT")
        self.assertEqual(body["ord_dt"], "20260711")

    def test_missing_order_balance_fails_closed(self):
        response = {"acnt_ord_cntr_prps_dtl": [{
            "ord_no": "0017196", "stk_cd": "012345",
            "ord_qty": "0000000010", "cntr_qty": "0000000010",
            "ord_remnq": "",
        }]}
        with mock.patch.object(am.kt.kw, "_call", return_value=response):
            with self.assertRaises(RuntimeError):
                am.kt.order_status("012345", "0017196")

    def test_cancel_confirmation_child_is_joined_to_original_order(self):
        response = {"acnt_ord_cntr_prps_dtl": [{
            "ord_no": "0017196", "stk_cd": "A012345",
            "ord_qty": "0000000010", "cntr_qty": "0000000000",
            "ord_remnq": "0000000000", "acpt_tp": "접수", "mdfy_cncl": "",
            "ori_ord": "0000000",
        }, {
            "ord_no": "0017201", "stk_cd": "A012345",
            "ord_qty": "0000000010", "cntr_qty": "0000000000",
            "ord_remnq": "0000000000", "acpt_tp": "접수",
            "mdfy_cncl": "취소확인", "ori_ord": "0017196",
        }]}
        with mock.patch.object(am.kt.kw, "_call", return_value=response):
            status = am.kt.order_status("012345", "0017196")
        self.assertTrue(status["cancel_confirmed"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
