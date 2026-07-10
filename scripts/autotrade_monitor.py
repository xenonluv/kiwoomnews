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


def _held_qty(code):
    """실계좌 보유수량(rmnd) 조회 — NXT 지정가 매도 체결확인용. 실패 시 None(확인 불가).
    ⚠ kt00018 필드 미검증 — 조회실패/미보유는 fail-safe(체결 미확정 → 포지션 유지)로 흡수돼 오발주 위험 없음."""
    try:
        h = kt.account_holdings()
    except Exception as e:
        ac.log(f"[monitor] {code} 잔고조회 실패(체결확인): {e}")
        return None
    for x in h.get("holdings", []) or []:
        if x.get("code") == code:
            return int(x.get("qty") or 0)
    return 0  # 목록에 없음 = 보유 0(전량 체결/미보유)


def _sell(pos, qty, reason, dry, market="KRX", cur=0):
    """qty주 매도 시도 → **실체결(확인)된 수량**을 반환(0 = 미체결/미확인/dry). 호출부는 반환 수량만큼만 포지션 갱신.

    KRX=시장가(접수=즉시 체결) / NXT=지정가(접수≠체결 → 체결 확인 필요).
    ⚠ NXT 체결 확인은 **주문 직전 계좌 스냅샷 대비 감소분(델타)**으로만 판정한다.
      (크리티컬 감사 2026-07-11: 기준선을 봇 기록 qty_open으로 잡으면 계좌에 같은 종목
      수동 보유분이 있을 때(M≥q) 완전 체결도 '미체결'로 오판 → 매분 재주문 루프가
      회장님 수동 보유분까지 청산. 계좌 전체 수량은 수동분 포함이므로 봇 기록과 비교 불가.)
    ⚠ 미체결/부분체결 잔여 주문은 즉시 취소를 시도해 다음 틱 재주문과의 스택(이중 매도) 차단.
    """
    qty = int(qty)
    if qty <= 0:
        return 0
    held_before = None
    if market == "NXT":
        # 주문 '직전' 계좌 스냅샷 — 이것이 체결 판정의 유일한 기준선.
        held_before = _held_qty(pos["code"])
        if held_before is None:
            # 기준선 없이 지정가를 내면 체결 확인이 불가능해 재주문 루프 위험 → 블라인드 발주 금지.
            ac.log(f"[monitor] {pos['name']}({pos['code']}) 주문 전 잔고조회 실패 — NXT 매도 보류(다음 틱 재시도)")
            return 0
        if held_before <= 0:
            ac.log(f"[monitor] {pos['name']}({pos['code']}) 실계좌 보유 0 — NXT 매도 불가(강제청산 로직이 종료 판단)")
            return 0
        qty = min(qty, held_before)   # 계좌 보유 초과 주문 방지
    ac.log(f"[monitor] {pos['name']}({pos['code']}) [{market}] {reason} → 매도 {qty}주 시도")
    try:
        res = kt.sell_market(pos["code"], qty, market=market, dry=dry)
    except Exception as e:
        # 호가 부재(NXT 장 밖)·주문 오류 등 — 이번 틱 실패로 두고 다음 틱 재시도(감시기 중단 방지).
        ac.log(f"[monitor] 매도 실패({market}) — 다음 틱 재시도: {e}")
        return 0
    if res.get("dry"):
        ac.log(f"[monitor] DRY — 발주 안 함({res.get('reason')})")
        return 0
    sold = qty
    if market == "NXT":
        ord_no = kt._extract_ord_no((res.get("result") or {}) if isinstance(res.get("result"), dict) else res)
        held_after = _held_qty(pos["code"])
        filled = (held_before - held_after) if held_after is not None else None
        if filled is None or filled < qty:
            # 미체결/부분체결/확인불가 — 잔여 지정가 주문을 취소해 다음 틱 신규 주문과의 스택 방지.
            # (취소 실패는 fail-safe: 이미 전량 체결됐으면 취소할 게 없어 실패하는 게 정상.)
            if ord_no:
                try:
                    kt.cancel_order(pos["code"], ord_no, market="NXT", qty=0, dry=dry)
                except Exception as e:
                    # 이미 전량 체결이면 취소 실패가 정상. 단 네트워크 타임아웃이면 미체결 주문이
                    # 살아있을 수 있어 즉시 경고(다음 틱 신규 주문과 스택 가능 — 수동 확인 필요).
                    ac.log(f"[monitor] {pos['code']} NXT 잔여주문 취소 실패(무시): {e}")
                    ac.notify_trade(
                        f"⚠️ [자동매매] {pos.get('name','')}({pos['code']}) NXT 잔여주문 취소 실패\n"
                        f"원주문 {ord_no} — 미체결 주문이 남아있을 수 있습니다. HTS에서 수동 확인 요망.")
            else:
                ac.log(f"[monitor] {pos['code']} NXT 주문번호 추출 실패 — 잔여주문 취소 불가(수동 확인 권장)")
                ac.notify_trade(
                    f"⚠️ [자동매매] {pos.get('name','')}({pos['code']}) NXT 주문번호 추출 실패\n"
                    f"미체결 잔여주문을 취소하지 못했습니다. HTS에서 미체결 주문 수동 확인 요망.")
            # 취소 후 최종 재확인 1회 — 취소 직전 체결·잔고 반영 지연을 여기서 포착.
            held_final = _held_qty(pos["code"])
            if held_final is not None:
                filled = held_before - held_final
        if filled is None or filled <= 0:
            ac.log(f"[monitor] {pos['name']}({pos['code']}) NXT 매도 접수했으나 체결 미확인"
                   f"(주문전 {held_before} → 주문후 {held_after}) — 미체결로 보고 포지션 유지"
                   f"(잔여주문 취소 시도됨 · 다음 틱/09시 KRX 시장가 정리)")
            return 0
        sold = min(qty, filled)   # 실제 체결 수량(주문 전 스냅샷 대비 감소분 — 수동 보유분과 무관)
    # 실체결 → 통계용 원장 기록(청산가·수익률은 청산판정 시 cur 기준 근사). fail-safe.
    entry = pos.get("entry_price") or 0
    ac.append_trade_event({
        "type": "exit", "id": pos.get("id"), "code": pos["code"], "name": pos.get("name"),
        "market": market, "reason": reason, "sold_qty": sold, "exit_price": cur, "entry_price": entry,
        "entry_date": pos.get("entry_date"), "opened_at": pos.get("opened_at"),
        "realized_return_pct": round((cur - entry) / entry * 100, 2) if entry else None,
        "remaining_qty": max(0, int(pos.get("qty_open", sold)) - sold), "dry": False})
    return sold


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
        # ⚠ 실체결 수량(sold)만큼만 잔량 차감 — NXT 미체결·부분체결분을 통째로 closed 처리하면
        #    미매도 잔량이 손절/청산 관리에서 영구 이탈(방치). 잔량 0일 때만 종료.
        sold = _sell(pos, sell_qty, f"강제청산·갈아타기(전날포지션, 현재 {pct:+.1f}%)", dry, market=sell_mkt, cur=cur)
        if sold:
            pos["qty_open"] = qopen - sold
            if pos["qty_open"] <= 0:
                pos["qty_open"] = 0; pos["status"] = "closed"; pos["close_reason"] = "force_exit_rotation"
            else:
                ac.log(f"[monitor] {pos['code']} 부분 강제청산 {sold}/{qopen}주 — 잔량 {pos['qty_open']}주 open 유지(다음 틱 재시도)")
            changed = True
            ac.notify_trade(
                f"🔄 [자동매매] 강제청산 {pos['name']}({pos['code']}) {sold}주 시장가\n"
                f"전날 포지션 정리 {pct:+.1f}% (진입 {entry:,.0f}→현재 {cur:,.0f}) · 15:18 새 1위 갈아타기 준비")
        return changed

    if not pos.get("tp1_done"):
        if pct <= ac.STOP_LOSS_PCT:
            sold = _sell(pos, qopen, f"손절(-5%, 현재 {pct:+.1f}%)", dry, market=sell_mkt, cur=cur)
            if sold:
                pos["qty_open"] = qopen - sold
                if pos["qty_open"] <= 0:
                    pos["status"] = "closed"; pos["close_reason"] = "stop_loss"
                changed = True  # NXT 부분체결이면 잔량 open 유지 → 다음 틱 재손절
        elif pct >= ac.TP1_PCT:
            sell_qty = int(qopen * ac.TP1_FRACTION)
            if sell_qty >= 1:
                sold = _sell(pos, sell_qty, f"1차 익절(+7%, 현재 {pct:+.1f}%) 50%", dry, market=sell_mkt, cur=cur)
                if sold:
                    pos["qty_open"] = qopen - sold; pos["tp1_done"] = True; changed = True
            else:
                sold = _sell(pos, qopen, f"1차 익절(+7%) 잔량 1주 전량", dry, market=sell_mkt, cur=cur)
                if sold:
                    pos["qty_open"] = qopen - sold
                    if pos["qty_open"] <= 0:
                        pos["status"] = "closed"; pos["close_reason"] = "tp1_all"
                    changed = True
    else:
        # 1차 익절 후 잔량 (모두 잔량 전량 청산 시도 — NXT 부분체결이면 잔량 open 유지·다음 틱 재시도)
        if pct >= ac.TP2_PCT:
            sold = _sell(pos, qopen, f"2차 익절(+11%, 현재 {pct:+.1f}%) 잔량", dry, market=sell_mkt, cur=cur)
            if sold:
                pos["qty_open"] = qopen - sold
                if pos["qty_open"] <= 0:
                    pos["status"] = "closed"; pos["close_reason"] = "tp2"
                changed = True
        elif pct <= ac.STOP_LOSS_PCT:
            sold = _sell(pos, qopen, f"손절(-5%, 현재 {pct:+.1f}%) 잔량", dry, market=sell_mkt, cur=cur)
            if sold:
                pos["qty_open"] = qopen - sold
                if pos["qty_open"] <= 0:
                    pos["status"] = "closed"; pos["close_reason"] = "stop_loss_after_tp1"
                changed = True
        elif pct <= ac.BREAKEVEN_PCT:
            sold = _sell(pos, qopen, f"본전 방어(1차 익절 후 재하락 {pct:+.1f}%≤+0.5%) 잔량", dry, market=sell_mkt, cur=cur)
            if sold:
                pos["qty_open"] = qopen - sold
                if pos["qty_open"] <= 0:
                    pos["status"] = "closed"; pos["close_reason"] = "breakeven"
                changed = True
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
