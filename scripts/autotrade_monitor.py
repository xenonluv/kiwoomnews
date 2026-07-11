#!/usr/bin/env python3
"""자동매매 청산 모니터 — 오픈 포지션 감시. Task Scheduler가 장중 수분마다 호출.

청산 규칙(회장님 지시):
  · -5%      : 전량 시장가 손절
  · +7%      : 보유 50% 시장가 익절(1차) → tp1_done
  · +11%     : 1차 익절 후 잔량 시장가 익절
  · 본전 방어 : 1차 익절 후 잔량이 진입가 근처(≤+0.5%)로 재하락하면 시장가 매도

세션(ac.market_session):
  · NXT 프리마켓(08:00~08:59) — NXT 거래가능 포지션만, NXT 현재가로 판정·NXT 지정가 매도(다음날 08시 급등락 대응).
  · KRX 정규장(09:00~15:30) — KRX(J) 현재가로 판정·KRX 시장가 매도(현행). 14:50 이후 전날 이월분 강제청산.
  · 그 외 — 무동작.

⚠ 실발주는 kiwoom_trade가 AUTOTRADE_LIVE=1 일 때만. 기본 dry(미발주 로그).
매도 실패해도 다음 회차 재시도(포지션 상태는 실체결 시에만 갱신).
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autotrade_common as ac
import kiwoom_client as kw
import kiwoom_trade as kt
import market_state
import autotrade_orders


def _pct(cur, entry):
    if not entry or entry <= 0:
        return 0.0
    return (cur - entry) / entry * 100.0


def _persist_pending(pos, persist, context):
    """매도 주문 잠금을 핵심 포지션 원장에 저장한다. 실패하면 신규 주문을 금지한다."""
    if persist is None:
        ac.log(f"[monitor] {pos['code']} {context} 저장 콜백 없음 — 발주 차단")
        return False
    try:
        persist()
        return True
    except Exception as e:
        ac.log(f"[monitor] 🚨 {pos['code']} {context} 저장 실패 — 발주 차단: {e}")
        ac.notify_trade(
            f"🚨 [자동매매] {pos.get('name','')}({pos['code']}) 매도 주문 잠금 저장 실패\n"
            "중복 매도 방지를 위해 발주하지 않았습니다. 포지션 원장을 확인해야 합니다.")
        return False


def _append_exit_event(pos, sold, market, reason, cur):
    entry = pos.get("entry_price") or 0
    verified_exit = cur if cur and cur > 0 else None
    ac.append_trade_event({
        "type": "exit", "id": pos.get("id"), "code": pos["code"], "name": pos.get("name"),
        "market": market, "reason": reason, "sold_qty": sold, "exit_price": verified_exit,
        "entry_price": entry, "entry_date": pos.get("entry_date"),
        "opened_at": pos.get("opened_at"),
        "realized_return_pct": (round((cur - entry) / entry * 100, 2)
                                if entry and verified_exit is not None else None),
        "remaining_qty": max(0, int(pos.get("qty_open", sold)) - sold), "dry": False})


def _refresh_entry_price(pos):
    """주문조회에 평균 체결가가 뒤늦게 나타나면 진입가 검증을 완료한다."""
    if pos.get("entry_price_verified") is not False or not pos.get("entry_ord_no"):
        return False
    try:
        status = kt.order_status(
            pos["code"], pos["entry_ord_no"], market=pos.get("market") or "KRX",
            order_date=pos.get("entry_order_date") or "", side="buy")
        avg = float((status or {}).get("avg_fill_price") or 0)
        if avg > 0:
            pos["entry_price"] = avg
            pos["entry_price_verified"] = True
            ac.log(f"[monitor] {pos['code']} 평균 매수체결가 재검증 완료: {avg:,.0f}")
            return True
    except Exception as exc:
        ac.log(f"[monitor] {pos['code']} 평균 매수체결가 재검증 보류: {exc}")
    return False


def _linked_pending_entry(pos, pending_entries):
    """추가 체결 가능성이 있는 동일 intent/종목 pending을 반환한다."""
    intent = pos.get("entry_intent_id")
    code = pos.get("code")
    return next((row for row in pending_entries or []
                 if (isinstance(row, dict)
                     and ((intent and row.get("intent_id") == intent)
                          or (code and row.get("code") == code)))), None)


def _blocked_by_pending_entry(pos, pending_entries):
    """하위호환 검사. 실제 모니터는 차단 대신 risk-only 관리로 진입한다."""
    return _linked_pending_entry(pos, pending_entries) is not None


def _reconcile_pending_exit(pos, persist, dry=False):
    """기존 KRX/NXT 매도를 주문번호로 재조정한다. 미종결이면 새 주문을 내지 않는다."""
    pending = pos.get("pending_exit")
    if not isinstance(pending, dict):
        return False
    market = pending.get("market") or "KRX"
    ord_no = pending.get("ord_no")
    if not ord_no:
        ac.log(f"[monitor] {pos['code']} {market} pending 주문번호 없음 — 신규 청산 발주 차단 유지(수동 확인 필요)")
        return False

    try:
        status = kt.order_status(
            pos["code"], ord_no, market=market,
            order_date=pending.get("order_date") or "", side="sell")
    except Exception as e:
        ac.log(f"[monitor] {pos['code']} {market} 주문상태 조회 실패 — 신규 청산 발주 차단 유지: {e}")
        return False
    if status is None:
        ac.log(f"[monitor] {pos['code']} {market} 주문 {ord_no} 조회 미반영 — 신규 청산 발주 차단 유지")
        return False

    requested = max(0, int(pending.get("requested_qty") or 0))
    ordered = max(0, int(status.get("ordered_qty") or 0))
    if requested <= 0 or ordered != requested:
        ac.log(f"[monitor] {pos['code']} {market} 주문 {ord_no} 수량 불일치 "
               f"(pending {requested}, 주문조회 {ordered}) — 신규 청산 발주 차단 유지")
        return False
    accounted = max(0, int(pending.get("accounted_filled") or 0))
    total_filled = max(0, int(status.get("filled_qty") or 0))
    remaining = max(0, int(status.get("remaining_qty") or 0))
    if (total_filled > ordered or remaining > ordered or total_filled < accounted
            or total_filled + remaining > ordered):
        ac.log(f"[monitor] {pos['code']} {market} 주문 {ord_no} 상태 불일치 "
               f"(주문 {ordered}, 체결 {total_filled}, 잔량 {remaining}, 반영 {accounted}) "
               "— 신규 청산 발주 차단 유지")
        return False
    newly_filled = max(0, total_filled - accounted)
    changed = False
    if newly_filled:
        qopen = max(0, int(pos.get("qty_open") or 0) - newly_filled)
        _append_exit_event(
            pos, newly_filled, market, pending.get("reason") or f"{market} pending 체결",
            pending.get("price") or 0)
        pos["qty_open"] = qopen
        if qopen <= 0:
            if pos.get("entry_pending_unresolved"):
                # 매수 pending이 종결되기 전에는 늦은 추가체결을 받을 open placeholder로 둔다.
                pos["status"] = "open"
                pos["awaiting_entry_terminal"] = True
            else:
                pos["status"] = "closed"
                pos["close_reason"] = pending.get("close_reason") or "pending_exit_filled"
        pending["accounted_filled"] = total_filled
        changed = True

    pending["last_status"] = status
    if status.get("cancel_confirmed"):
        pending["cancel_confirmed"] = True
    pending["last_checked_at"] = datetime.now(ac.KST).strftime("%Y-%m-%d %H:%M:%S KST")
    # kt00007 원주문 행의 주문잔량 0은 체결·취소·거부를 모두 포함하는 종결 조건이다.
    terminal = remaining == 0
    if terminal:
        if pending.get("mark_tp1"):
            if total_filled > 0:
                pos["tp1_done"] = True
                pos.pop("tp1_remaining_qty", None)
        pos.pop("pending_exit", None)
        changed = True
        ac.log(f"[monitor] {pos['code']} {market} 주문 {ord_no} 종결 확정 "
               f"(체결 {total_filled}/{requested}, 잔량 0) — 주문 잠금 해제")
    else:
        if not pending.get("cancel_requested") and not pending.get("cancel_confirmed"):
            try:
                cancel_result = kt.cancel_order(
                    pos["code"], ord_no, market=market, qty=0, dry=dry)
                pending["cancel_requested"] = not cancel_result.get("dry")
                pending["state"] = "cancel_requested" if not dry else "cancel_dry_blocked"
            except Exception as e:
                pending["state"] = "cancel_retry_failed"
                pending["cancel_error"] = str(e)[:500]
            changed = True
        pos["pending_exit"] = pending
        ac.log(f"[monitor] {pos['code']} {market} 주문 {ord_no} 미종결 "
               f"(체결 {total_filled}/{requested}, 주문잔량 {remaining}) — 신규 주문 차단 유지")

    if changed and not _persist_pending(pos, persist, "pending 재조정"):
        return False
    return changed


def _sell(pos, qty, reason, dry, market="KRX", cur=0, persist=None,
          close_reason=None, mark_tp1=False, full_position_exit=False):
    """매도 접수 전 durable pending을 저장하고 체결분은 다음 조회에서만 반영한다."""
    qty = int(qty)
    if qty <= 0:
        return 0
    live_execution = not dry and os.environ.get("AUTOTRADE_LIVE") == "1"
    if live_execution:
        try:
            holding = next((h for h in kt.account_holdings().get("holdings", [])
                            if h.get("code") == pos["code"]), None)
        except Exception as exc:
            ac.log(f"[monitor] {pos['code']} 주문 전 잔고조회 실패 — 매도 보류: {exc}")
            return 0
        held = int((holding or {}).get("qty") or 0)
        tradable = int((holding or {}).get("tradable_qty") or 0)
        qopen = int(pos.get("qty_open") or 0)
        baseline = pos.get("manual_baseline_qty")
        if baseline is None:
            ac.log(f"[monitor] {pos['code']} 매수 전 수동보유 기준선 없음 — 수동분 보호를 위해 자동매도 차단")
            ac.notify_trade(
                f"🚨 [자동매매] {pos.get('name','')}({pos['code']}) 소유수량 기준선 없음\n"
                "자동매도를 차단했습니다. HTS와 포지션 원장을 수동 대조하세요.")
            return 0
        baseline = int(baseline)
        if held < baseline + qopen:
            ac.log(f"[monitor] {pos['code']} 수량 불일치(보유 {held} < 수동기준 {baseline}+봇잔량 {qopen}) — 매도 차단")
            return 0
        qty = min(qty, qopen, max(0, tradable - baseline))
        if qty <= 0:
            ac.log(f"[monitor] {pos['code']} 봇 귀속 매도가능수량 0 — 매도 보류")
            return 0
        pos["pending_exit"] = {
            "state": "prepared", "market": market,
            "held_before": held, "manual_baseline_qty": baseline,
            "requested_qty": qty, "accounted_filled": 0,
            "qopen_before": qopen, "reason": reason,
            "close_reason": close_reason, "mark_tp1": bool(mark_tp1),
            "full_position_exit": bool(full_position_exit), "price": cur,
            "order_date": ac.today_str(),
            "created_at": datetime.now(ac.KST).strftime("%Y-%m-%d %H:%M:%S KST"),
            "ord_no": None, "cancel_confirmed": False,
        }
        if not _persist_pending(pos, persist, "주문 전 pending_exit"):
            pos.pop("pending_exit", None)
            return 0
    ac.log(f"[monitor] {pos['name']}({pos['code']}) [{market}] {reason} → 매도 {qty}주 시도")
    try:
        res = kt.sell_market(pos["code"], qty, market=market, dry=dry)
    except Exception as e:
        if live_execution:
            pending = pos.get("pending_exit") or {}
            pending.update({"state": "submit_unknown", "submit_error": str(e)[:500]})
            pos["pending_exit"] = pending
            _persist_pending(pos, persist, f"{market} 주문결과 불명")
            ac.notify_trade(
                f"🚨 [자동매매] {pos.get('name','')}({pos['code']}) {market} 주문결과 불명\n"
                "pending 잠금으로 후속 매도를 차단했습니다. HTS 주문내역을 수동 확인해야 합니다.")
        else:
            ac.log(f"[monitor] 매도 실패({market}) — 다음 틱 재시도: {e}")
        return 0
    if res.get("dry"):
        if live_execution:
            pos.pop("pending_exit", None)
            _persist_pending(pos, persist, "DRY 응답 pending 해제")
        ac.log(f"[monitor] DRY — 발주 안 함({res.get('reason')})")
        return 0
    if not live_execution:
        return 0
    ord_no = kt._extract_ord_no(
        (res.get("result") or {}) if isinstance(res.get("result"), dict) else res)
    pending = pos.get("pending_exit") or {}
    pending.update({"state": "accepted" if ord_no else "submit_unknown", "ord_no": ord_no})
    pos["pending_exit"] = pending
    _persist_pending(pos, persist, f"{market} 주문번호")
    if not ord_no:
        ac.notify_trade(
            f"🚨 [자동매매] {pos.get('name','')}({pos['code']}) {market} 매도 주문번호 없음\n"
            "접수 여부가 불명확해 후속 매도를 잠갔습니다. HTS 주문내역을 확인하세요.")
    return 0


def check_position(pos, dry=True, acct_by_code=None, session="krx", persist=None,
                   entry_pending=False):
    """단일 포지션 청산 판정·실행. 상태 변경 여부 반환.

    acct_by_code: 강제청산 시 실계좌 대조용 {code: holding}. "ERROR"=조회실패(강제청산 보류).
    session: "krx"(정규장 — J가격·KRX시장가) / "nxt_premarket"(08:00~ — NXT가격·NXT지정가, NXT거래가능만).
    """
    # 이전 매도 주문이 미확정이면 신규 NXT/KRX 주문보다 재조정이 항상 우선한다.
    if pos.get("pending_exit"):
        return _reconcile_pending_exit(pos, persist, dry=dry)
    entry_price_refreshed = _refresh_entry_price(pos)

    # 세션별 가격 기준·매도 경로
    if session == "nxt_premarket":
        price_market, sell_mkt = "NX", "NXT"
    else:
        price_market, sell_mkt = "J", "KRX"
    price_error = None
    try:
        cur = kw.current_price(pos["code"], market=price_market) or 0
    except Exception as e:
        cur = 0
        price_error = e
    entry = pos["entry_price"]
    qopen = pos["qty_open"]
    changed = entry_price_refreshed

    # ── 강제 청산(갈아타기): 전날 이월 포지션은 14:50 이후 손익무관 전량 시장가 정리 ──
    #    → 15:18 오늘의 새 1위로 갈아타기 전에 계좌를 비운다. 최우선(손절/익절 룰보다 앞).
    #    ⚠ 실계좌 대조로 봇 기록 초과/유령 매도 방지(수동 보유분 보호).
    if pos.get("entry_date") != ac.today_str() and ac.past_force_exit():
        pct = _pct(cur, entry) if cur > 0 else None
        pct_text = f"{pct:+.1f}%" if pct is not None else "현재가 미확인"
        if price_error or cur <= 0:
            ac.log(f"[monitor] {pos['code']} 현재가 미확인({price_market})이지만 시간 강제청산은 계속: "
                   f"{price_error or '0/결측'}")
        if acct_by_code == "ERROR":
            ac.log(f"[monitor] {pos['code']} 강제청산 보류 — 실계좌 대조 불가(다음 틱 재시도)")
            return False
        avail = qopen  # 대조 정보 없으면(이론상 없음) 봇 기록대로
        if isinstance(acct_by_code, dict):
            h = acct_by_code.get(pos["code"])
            held_qty = h["qty"] if h else 0          # rmnd_qty 실보유 수량
            avail = h["tradable_qty"] if h else 0    # trde_able_qty 매도가능 수량
            if held_qty <= 0:
                if entry_pending or pos.get("entry_pending_unresolved"):
                    ac.log(
                        f"[monitor] {pos['code']} 실계좌 보유 0이지만 매수 pending 미종결 — "
                        "늦은 추가체결 관리를 위해 open placeholder 유지")
                    return changed
                # 실계좌에 실제로 없음(수동매도/미체결) → 종료 처리(팔 게 없음)
                ac.log(f"[monitor] {pos['name']}({pos['code']}) 강제청산 스킵 — 실계좌 보유 0(수동매도/미체결). 종료 처리")
                pos["qty_open"] = 0; pos["status"] = "closed"; pos["close_reason"] = "force_exit_not_held"
                ac.notify_trade(f"⚠️ [자동매매] {pos['name']}({pos['code']}) 강제청산 대상이나 실계좌 잔고 없음 — 종료 처리(수동 확인)")
                return True
            if avail <= 0:
                # ⚠ 보유(rmnd>0)하나 지금 매도불가(결제락 등) → 종료하지 말고 다음 틱 재시도(실보유 포지션 방치 방지)
                ac.log(f"[monitor] {pos['name']}({pos['code']}) 보유 {held_qty}주 있으나 매도가능 0(락 등) — 강제청산 보류(다음 틱 재시도)")
                return False
        sell_qty = min(qopen, avail)
        if sell_qty < qopen:
            ac.log(f"[monitor] {pos['code']} 실계좌 매도가능 {avail} < 봇기록 {qopen} — 매도가능분만 청산")
        # ⚠ 실체결 수량(sold)만큼만 잔량 차감 — NXT 미체결·부분체결분을 통째로 closed 처리하면
        #    미매도 잔량이 손절/청산 관리에서 영구 이탈(방치). 잔량 0일 때만 종료.
        sold = _sell(pos, sell_qty, f"강제청산·갈아타기(전날포지션, {pct_text})", dry,
                     market=sell_mkt, cur=cur, persist=persist,
                     close_reason="force_exit_rotation", full_position_exit=True)
        if sold:
            pos["qty_open"] = qopen - sold
            if pos["qty_open"] <= 0:
                pos["qty_open"] = 0; pos["status"] = "closed"; pos["close_reason"] = "force_exit_rotation"
            else:
                ac.log(f"[monitor] {pos['code']} 부분 강제청산 {sold}/{qopen}주 — 잔량 {pos['qty_open']}주 open 유지(다음 틱 재시도)")
            changed = True
            ac.notify_trade(
                f"🔄 [자동매매] 강제청산 {pos['name']}({pos['code']}) {sold}주 시장가\n"
                f"전날 포지션 정리 {pct_text} (진입 {entry:,.0f}→현재 {cur or 0:,.0f}) · 15:18 새 1위 갈아타기 준비")
        return changed

    # 일반 손절·익절은 현재가가 필수다. 0을 -100%로 오인해 오발주하지 않는다.
    if cur <= 0:
        if session == "nxt_premarket":
            ac.log(f"[monitor] {pos['code']} NXT 프리마켓 현재가 없음 — 스킵(09:00 KRX 세션에 위임)")
        else:
            ac.log(f"[monitor] {pos['code']} 현재가 조회 실패/결측({price_market}): "
                   f"{price_error or '0'} — 다음 틱 재시도")
        return False
    pct = _pct(cur, entry)

    if pos.get("entry_price_verified") is False:
        ac.log(f"[monitor] {pos['code']} 진입 체결가 미검증 — 손절/익절 보류; 익일 강제청산은 유지")
        return False

    # 미종결 매수주문과 연결된 동안에는 손절만 허용한다. 확인된 보유분의 위험은
    # 줄이되 TP/본전매도로 상태를 복잡하게 만들지 않고, 늦은 추가체결은 open
    # placeholder가 다음 틱에 다시 관리한다. 익일 강제청산은 위 분기에서 이미 처리된다.
    if entry_pending and pct > ac.STOP_LOSS_PCT:
        ac.log(f"[monitor] {pos['code']} 매수 pending 미종결 — TP/본전매도 보류, 손절·강제청산은 유지")
        return changed

    if not pos.get("tp1_done"):
        if pct <= ac.STOP_LOSS_PCT:
            sold = _sell(pos, qopen, f"손절(-5%, 현재 {pct:+.1f}%)", dry, market=sell_mkt,
                         cur=cur, persist=persist, close_reason="stop_loss",
                         full_position_exit=True)
            if sold:
                pos["qty_open"] = qopen - sold
                if pos["qty_open"] <= 0:
                    pos["status"] = "closed"; pos["close_reason"] = "stop_loss"
                changed = True  # NXT 부분체결이면 잔량 open 유지 → 다음 틱 재손절
        elif pct >= ac.TP1_PCT:
            sell_qty = int(pos.get("tp1_remaining_qty") or (qopen * ac.TP1_FRACTION))
            if sell_qty >= 1:
                sold = _sell(pos, sell_qty, f"1차 익절(+7%, 현재 {pct:+.1f}%) 50%", dry,
                             market=sell_mkt, cur=cur, persist=persist, mark_tp1=True)
                if sold:
                    pos["qty_open"] = qopen - sold; pos["tp1_done"] = True; changed = True
            else:
                sold = _sell(pos, qopen, f"1차 익절(+7%) 잔량 1주 전량", dry, market=sell_mkt,
                             cur=cur, persist=persist, close_reason="tp1_all",
                             full_position_exit=True)
                if sold:
                    pos["qty_open"] = qopen - sold
                    if pos["qty_open"] <= 0:
                        pos["status"] = "closed"; pos["close_reason"] = "tp1_all"
                    changed = True
    else:
        # 1차 익절 후 잔량 (모두 잔량 전량 청산 시도 — NXT 부분체결이면 잔량 open 유지·다음 틱 재시도)
        if pct >= ac.TP2_PCT:
            sold = _sell(pos, qopen, f"2차 익절(+11%, 현재 {pct:+.1f}%) 잔량", dry,
                         market=sell_mkt, cur=cur, persist=persist, close_reason="tp2",
                         full_position_exit=True)
            if sold:
                pos["qty_open"] = qopen - sold
                if pos["qty_open"] <= 0:
                    pos["status"] = "closed"; pos["close_reason"] = "tp2"
                changed = True
        elif pct <= ac.STOP_LOSS_PCT:
            sold = _sell(pos, qopen, f"손절(-5%, 현재 {pct:+.1f}%) 잔량", dry,
                         market=sell_mkt, cur=cur, persist=persist,
                         close_reason="stop_loss_after_tp1", full_position_exit=True)
            if sold:
                pos["qty_open"] = qopen - sold
                if pos["qty_open"] <= 0:
                    pos["status"] = "closed"; pos["close_reason"] = "stop_loss_after_tp1"
                changed = True
        elif pct <= ac.BREAKEVEN_PCT:
            sold = _sell(pos, qopen, f"본전 방어(1차 익절 후 재하락 {pct:+.1f}%≤+0.5%) 잔량", dry,
                         market=sell_mkt, cur=cur, persist=persist,
                         close_reason="breakeven", full_position_exit=True)
            if sold:
                pos["qty_open"] = qopen - sold
                if pos["qty_open"] <= 0:
                    pos["status"] = "closed"; pos["close_reason"] = "breakeven"
                changed = True
    if not changed:
        ac.log(f"[monitor] {pos['name']}({pos['code']}) 보유 현재 {pct:+.1f}% "
               f"(entry {entry:,.0f} → {cur:,.0f}, {qopen}주, tp1={pos.get('tp1_done')})")
    return changed


def _run_unlocked(dry=True):
    try:
        data = ac.load_positions()
    except Exception as e:
        # 상태 불명이면 잘못된 empty로 청산 규칙이 무력화되므로 이번 회차 중단(fail-closed).
        ac.log(f"[monitor] 포지션 로드 실패 — 청산 판정 중단(fail-closed): {e}")
        return
    if data.get("pending_entries"):
        # 가시성 검사는 주문/취소를 호출하지 않으므로 장 세션·거래일과 무관하게 수행한다.
        try:
            autotrade_orders.review_pending_attention(
                data, persist=lambda: ac.save_positions(data))
        except Exception as e:
            ac.log(f"[monitor] pending 가시성 검사 오류 격리 — 청산 계속: {e}")
    session = ac.market_session()
    if session == "closed":
        ac.log("[monitor] 장 마감 세션(closed) — 감시 무동작")
        return
    if not dry:
        trading_ok, trading_state = market_state.require_trading_day(kt.kw)
        if not trading_ok:
            ac.log(f"[monitor] 거래일 확인 실패 — 자동청산 보류: {trading_state}")
            return
    if data.get("pending_entries"):
        try:
            unresolved = autotrade_orders.reconcile_pending_entries(data, dry=dry)
        except Exception as e:
            # 방어 최종선: 행별 격리를 빠져나온 예외가 있어도 무관 포지션 위험청산은 진행한다.
            ac.log(f"[monitor] 매수 pending 재조정 전체 오류 — 해당 pending 격리 후 청산 계속: {e}")
            ac.notify_trade(
                f"🚨 [자동매매] 매수 pending 재조정 오류: {str(e)[:300]}\n"
                "해당 종목은 수동 확인이 필요하지만 다른 포지션 청산은 계속합니다.")
            unresolved = True
        if unresolved:
            ac.log("[monitor] 미확정 매수 주문 존재 — 동일 intent/종목 포지션만 청산 격리")
    opens = ac.open_positions(data)
    if not opens:
        ac.log("[monitor] 오픈 포지션 없음")
        return
    ac.log(f"[monitor] 세션={session} (오픈 {len(opens)}건)")
    # 강제청산 대상(전날 이월)이 있고 시각이 지났으면 실계좌 잔고 1회 조회(대조용). 실패 시 "ERROR"→강제청산 보류.
    acct_by_code = None
    if ac.past_force_exit() and any(p.get("entry_date") != ac.today_str() for p in opens):
        try:
            acct_by_code = {h["code"]: h for h in kt.account_holdings()["holdings"]}
        except Exception as e:
            ac.log(f"[monitor] 실계좌 잔고 조회 실패 — 강제청산 정합성 체크 불가, 이번 회차 보류: {e}")
            acct_by_code = "ERROR"
    def persist_positions():
        ac.save_positions(data)

    for pos in data["positions"]:
        if pos.get("status") != "open":
            continue
        linked_pending = _linked_pending_entry(pos, data.get("pending_entries"))
        was_pending = bool(pos.get("entry_pending_unresolved"))
        pos["entry_pending_unresolved"] = bool(linked_pending)
        if not linked_pending and was_pending and int(pos.get("qty_open") or 0) <= 0:
            pos["status"] = "closed"
            pos["awaiting_entry_terminal"] = False
            pos["close_reason"] = "entry_terminal_after_risk_exit"
            try:
                ac.save_positions(data)
            except Exception as e:
                ac.log(f"[monitor] {pos.get('code')} pending 종결 상태 저장 실패 — 다음 틱 재시도: {e}")
            continue
        if check_position(pos, dry=dry, acct_by_code=acct_by_code, session=session,
                          persist=persist_positions, entry_pending=bool(linked_pending)):
            # 발주(실체결) 직후 즉시 개별 저장 — 배치 말미 단일 save의 유실 창을 없애 이중 매도 방지.
            # 저장 실패 시 상태가 디스크에 안 남았으므로 후속 포지션 발주를 즉시 중단(다음 회차가 재판정).
            try:
                ac.save_positions(data)
            except Exception as e:
                ac.log(f"[monitor] 🚨 save 실패 — 상태 미갱신, 후속 청산 발주 중단: {e}")
                return


def run(dry=True):
    with ac.acquire_execution_lock("monitor") as acquired:
        if not acquired:
            return
        return _run_unlocked(dry=dry)


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    run(dry=dry)
