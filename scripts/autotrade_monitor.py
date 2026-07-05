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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autotrade_common as ac
import kiwoom_client as kw
import kiwoom_trade as kt


def _pct(cur, entry):
    if not entry or entry <= 0:
        return 0.0
    return (cur - entry) / entry * 100.0


def _sell(pos, qty, reason, dry, market="KRX"):
    """qty주 매도(KRX=시장가 / NXT=매수5호가 아래 지정가, sell_market이 자동 분기).
    실체결(live) 시 True 반환(호출부가 포지션 갱신)."""
    qty = int(qty)
    if qty <= 0:
        return False
    ac.log(f"[monitor] {pos['name']}({pos['code']}) [{market}] {reason} → 매도 {qty}주 시도")
    try:
        res = kt.sell_market(pos["code"], qty, market=market, dry=dry)
    except Exception as e:
        # 호가 부재(NXT 장 밖)·주문 오류 등 — 이번 틱 실패로 두고 다음 틱 재시도(감시기 중단 방지).
        ac.log(f"[monitor] 매도 실패({market}) — 다음 틱 재시도: {e}")
        return False
    if res.get("dry"):
        ac.log(f"[monitor] DRY — 발주 안 함({res.get('reason')})")
        return False
    return True


def check_position(pos, dry=True, acct_by_code=None, session="krx"):
    """단일 포지션 청산 판정·실행. 상태 변경 여부 반환.

    acct_by_code: 강제청산 시 실계좌 대조용 {code: holding}. "ERROR"=조회실패(강제청산 보류).
    session: "krx"(정규장 — J가격·KRX시장가) / "nxt_premarket"(08:00~ — NXT가격·NXT지정가, NXT거래가능만).
    """
    # 세션별 가격 기준·매도 경로
    if session == "nxt_premarket":
        price_market, sell_mkt = "NX", "NXT"
    else:
        price_market, sell_mkt = "J", "KRX"
    try:
        cur = kw.current_price(pos["code"], market=price_market) or 0
    except Exception as e:
        ac.log(f"[monitor] {pos['code']} 현재가 조회 실패({price_market}): {e}")
        return False
    # 현재가 0/결측은 실제 가격이 아니라 데이터 오류(거래정지·API 결측·NXT 미개장) → 스킵(다음 틱 재시도).
    # ⚠ 이 가드가 없으면 cur=0 → _pct=-100% → 건강한 포지션을 -5% 손절로 시장가 매도(치명적 오발주).
    if cur <= 0:
        if session == "nxt_premarket":
            ac.log(f"[monitor] {pos['code']} NXT 프리마켓 현재가 없음 — 스킵(09:00 KRX 세션에 위임)")
        else:
            ac.log(f"[monitor] {pos['code']} 현재가 0/결측({price_market}) — 데이터 오류로 스킵(다음 틱 재시도)")
        return False
    entry = pos["entry_price"]
    pct = _pct(cur, entry)
    qopen = pos["qty_open"]
    changed = False

    # ── 강제 청산(갈아타기): 전날 이월 포지션은 14:50 이후 손익무관 전량 시장가 정리 ──
    #    → 15:18 오늘의 새 1위로 갈아타기 전에 계좌를 비운다. 최우선(손절/익절 룰보다 앞).
    #    ⚠ 실계좌 대조로 봇 기록 초과/유령 매도 방지(수동 보유분 보호).
    if pos.get("entry_date") != ac.today_str() and ac.past_force_exit():
        if acct_by_code == "ERROR":
            ac.log(f"[monitor] {pos['code']} 강제청산 보류 — 실계좌 대조 불가(다음 틱 재시도)")
            return False
        avail = qopen  # 대조 정보 없으면(이론상 없음) 봇 기록대로
        if isinstance(acct_by_code, dict):
            h = acct_by_code.get(pos["code"])
            held_qty = h["qty"] if h else 0          # rmnd_qty 실보유 수량
            avail = h["tradable_qty"] if h else 0    # trde_able_qty 매도가능 수량
            if held_qty <= 0:
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
        if _sell(pos, sell_qty, f"강제청산·갈아타기(전날포지션, 현재 {pct:+.1f}%)", dry, market=sell_mkt):
            pos["qty_open"] = 0; pos["status"] = "closed"; pos["close_reason"] = "force_exit_rotation"; changed = True
            ac.notify_trade(
                f"🔄 [자동매매] 강제청산 {pos['name']}({pos['code']}) {sell_qty}주 시장가\n"
                f"전날 포지션 정리 {pct:+.1f}% (진입 {entry:,.0f}→현재 {cur:,.0f}) · 15:18 새 1위 갈아타기 준비")
        return changed

    if not pos.get("tp1_done"):
        if pct <= ac.STOP_LOSS_PCT:
            if _sell(pos, qopen, f"손절(-5%, 현재 {pct:+.1f}%)", dry, market=sell_mkt):
                pos["qty_open"] = 0; pos["status"] = "closed"; pos["close_reason"] = "stop_loss"; changed = True
        elif pct >= ac.TP1_PCT:
            sell_qty = int(qopen * ac.TP1_FRACTION)
            if sell_qty >= 1 and _sell(pos, sell_qty, f"1차 익절(+7%, 현재 {pct:+.1f}%) 50%", dry, market=sell_mkt):
                pos["qty_open"] = qopen - sell_qty; pos["tp1_done"] = True; changed = True
            elif sell_qty < 1 and _sell(pos, qopen, f"1차 익절(+7%) 잔량 1주 전량", dry, market=sell_mkt):
                pos["qty_open"] = 0; pos["status"] = "closed"; pos["close_reason"] = "tp1_all"; changed = True
    else:
        # 1차 익절 후 잔량
        if pct >= ac.TP2_PCT:
            if _sell(pos, qopen, f"2차 익절(+11%, 현재 {pct:+.1f}%) 잔량", dry, market=sell_mkt):
                pos["qty_open"] = 0; pos["status"] = "closed"; pos["close_reason"] = "tp2"; changed = True
        elif pct <= ac.STOP_LOSS_PCT:
            if _sell(pos, qopen, f"손절(-5%, 현재 {pct:+.1f}%) 잔량", dry, market=sell_mkt):
                pos["qty_open"] = 0; pos["status"] = "closed"; pos["close_reason"] = "stop_loss_after_tp1"; changed = True
        elif pct <= ac.BREAKEVEN_PCT:
            if _sell(pos, qopen, f"본전 방어(1차 익절 후 재하락 {pct:+.1f}%≤+0.5%) 잔량", dry, market=sell_mkt):
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
    session = ac.market_session()
    if session == "closed":
        ac.log("[monitor] 장 마감 세션(closed) — 감시 무동작")
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
    for pos in data["positions"]:
        if pos.get("status") != "open":
            continue
        if check_position(pos, dry=dry, acct_by_code=acct_by_code, session=session):
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
