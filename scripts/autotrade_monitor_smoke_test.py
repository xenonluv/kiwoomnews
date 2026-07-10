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


def _pos(qty_open=10, entry=10000):
    return {
        "id": "012345-20260713-krx", "code": "012345", "name": "테스트종목",
        "entry_date": "20260713", "entry_price": entry,
        "qty": qty_open, "qty_open": qty_open, "market": "KRX",
        "alloc_krw": 1_000_000, "rank": 1, "tp1_done": False, "status": "open",
        "opened_at": "2026-07-13 15:18:30",
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

    def order_status(self, code, ord_no, market="NXT", order_date=""):
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
            pos = self._run_ticks(broker, _pos(qty_open=10), n=2)
        self.assertEqual(pos["qty_open"], 6)
        self.assertEqual(pos["status"], "open")
        self.assertEqual(len(broker.cancels), 1)
        self.assertNotIn("pending_exit", pos)
        exits = [e for e in self.events if e["type"] == "exit"]
        self.assertEqual(exits[0]["sold_qty"], 4)

    def test_no_fill_cancels_and_keeps_position(self):
        """미체결(계좌 무변화) → sold 0·포지션 유지·잔여주문 취소(스택 차단)."""
        broker = FakeBroker(account_qty=60, fill=False)
        pos = self._run_ticks(broker, _pos(qty_open=10), n=2)
        self.assertEqual(pos["qty_open"], 10)
        self.assertEqual(pos["status"], "open")
        self.assertEqual(len(broker.sell_orders), 1)
        self.assertEqual(len(broker.cancels), 1)
        self.assertEqual(self.events, [])
        self.assertNotIn("pending_exit", pos)

    def test_post_order_query_failure_recovered_by_final_recheck(self):
        """주문 후 1차 조회 실패·최종 재확인에서 체결 포착 → 정상 sold 인식."""
        # 호출 순서: ①주문 전(성공 60) ②주문 후(실패) ③취소 후 최종(성공 50)
        broker = FakeBroker(account_qty=60, holdings_errors=[None, "raise", None])
        pos = self._run_ticks(broker, _pos(qty_open=10), n=2)
        self.assertEqual(pos["qty_open"], 0)
        self.assertEqual(pos["status"], "closed")
        self.assertEqual(len(broker.cancels), 1, "확인불가 시 취소를 시도해야 한다")

    def test_order_qty_capped_at_account_quantity(self):
        """봇 기록 10주 > 실계좌 7주면 주문은 7주로 캡(주문거부·과매도 방지)."""
        broker = FakeBroker(account_qty=7)
        self._run_ticks(broker, _pos(qty_open=10), n=1)
        self.assertEqual(broker.sell_orders[0][1], 7)

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
