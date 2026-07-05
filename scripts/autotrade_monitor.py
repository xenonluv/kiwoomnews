#!/usr/bin/env python3
"""자동매매 청산 모니터 — 오픈 포지션 감시. Task Scheduler가 장중 수분마다 호출.

청산 규칙(회장님 지시):
  · -5%      : 전량 시장가 손절
  · +7%      : 보유 50% 시장가 익절(1차) → tp1_done
  · +11%     : 1차 익절 후 잔량 시장가 익절
  · 본전 방어 : 1차 익절 후 잔량이 진입가 근처(≤+0.5%)로 재하락하면 시장가 매도

⚠ 실발주는 kiwoom_trade가 AUTOTRADE_LIVE=1 일 때만. 기본 dry(미발주 로그).
현재가는 KRX(J) 기준. 매도 실패해도 다음 회차 재시도(포지션 상태는 실체결 시에만 갱신).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autotrade_common as ac
import kiwoom_client as kw
import kiwoom_trade as kt


def _pct(cur, entry):
    if not entry or entry <= 0:
        return 0.0
    return (cur - entry) / entry * 100.0


def _sell(pos, qty, reason, dry):
    """qty주 시장가 매도. 실체결(live) 시 True 반환(호출부가 포지션 갱신)."""
    qty = int(qty)
    if qty <= 0:
        return False
    ac.log(f"[monitor] {pos['name']}({pos['code']}) {reason} → 매도 {qty}주 시도")
    res = kt.sell_market(pos["code"], qty, market="KRX", dry=dry)
    if res.get("dry"):
        ac.log(f"[monitor] DRY — 발주 안 함({res.get('reason')})")
        return False
    return True


def check_position(pos, dry=True):
    """단일 포지션 청산 판정·실행. 상태 변경 여부 반환."""
    try:
        cur = kw.price_now(pos["code"], market="J").get("price") or 0
    except Exception as e:
        ac.log(f"[monitor] {pos['code']} 현재가 조회 실패: {e}")
        return False
    entry = pos["entry_price"]
    pct = _pct(cur, entry)
    qopen = pos["qty_open"]
    changed = False

    if not pos.get("tp1_done"):
        if pct <= ac.STOP_LOSS_PCT:
            if _sell(pos, qopen, f"손절(-5%, 현재 {pct:+.1f}%)", dry):
                pos["qty_open"] = 0; pos["status"] = "closed"; pos["close_reason"] = "stop_loss"; changed = True
        elif pct >= ac.TP1_PCT:
            sell_qty = int(qopen * ac.TP1_FRACTION)
            if sell_qty >= 1 and _sell(pos, sell_qty, f"1차 익절(+7%, 현재 {pct:+.1f}%) 50%", dry):
                pos["qty_open"] = qopen - sell_qty; pos["tp1_done"] = True; changed = True
            elif sell_qty < 1 and _sell(pos, qopen, f"1차 익절(+7%) 잔량 1주 전량", dry):
                pos["qty_open"] = 0; pos["status"] = "closed"; pos["close_reason"] = "tp1_all"; changed = True
    else:
        # 1차 익절 후 잔량
        if pct >= ac.TP2_PCT:
            if _sell(pos, qopen, f"2차 익절(+11%, 현재 {pct:+.1f}%) 잔량", dry):
                pos["qty_open"] = 0; pos["status"] = "closed"; pos["close_reason"] = "tp2"; changed = True
        elif pct <= ac.STOP_LOSS_PCT:
            if _sell(pos, qopen, f"손절(-5%, 현재 {pct:+.1f}%) 잔량", dry):
                pos["qty_open"] = 0; pos["status"] = "closed"; pos["close_reason"] = "stop_loss_after_tp1"; changed = True
        elif pct <= ac.BREAKEVEN_PCT:
            if _sell(pos, qopen, f"본전 방어(1차 익절 후 재하락 {pct:+.1f}%≤+0.5%) 잔량", dry):
                pos["qty_open"] = 0; pos["status"] = "closed"; pos["close_reason"] = "breakeven"; changed = True
    if not changed:
        ac.log(f"[monitor] {pos['name']}({pos['code']}) 보유 현재 {pct:+.1f}% "
               f"(entry {entry:,.0f} → {cur:,.0f}, {qopen}주, tp1={pos.get('tp1_done')})")
    return changed


def run(dry=True):
    try:
        data = ac.load_positions()
    except Exception as e:
        # 상태 불명이면 잘못된 empty로 청산 규칙이 무력화되므로 이번 회차 중단(fail-closed).
        ac.log(f"[monitor] 포지션 로드 실패 — 청산 판정 중단(fail-closed): {e}")
        return
    opens = ac.open_positions(data)
    if not opens:
        ac.log("[monitor] 오픈 포지션 없음")
        return
    for pos in data["positions"]:
        if pos.get("status") != "open":
            continue
        if check_position(pos, dry=dry):
            # 발주(실체결) 직후 즉시 개별 저장 — 배치 말미 단일 save의 유실 창을 없애 이중 매도 방지.
            # 저장 실패 시 상태가 디스크에 안 남았으므로 후속 포지션 발주를 즉시 중단(다음 회차가 재판정).
            try:
                ac.save_positions(data)
            except Exception as e:
                ac.log(f"[monitor] 🚨 save 실패 — 상태 미갱신, 후속 청산 발주 중단: {e}")
                return


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    run(dry=dry)
