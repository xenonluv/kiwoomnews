#!/usr/bin/env python3
"""자동매매 실행기 — 종가베팅 매수. Windows Task Scheduler에서 15:18/19:50 호출.

  python3 scripts/autotrade_executor.py --slot krx   # 15:18 — NXT 불가 종목 KRX 시장가 매수
  python3 scripts/autotrade_executor.py --slot nxt   # 19:50 — NXT 가능 종목 NXT 지정가(5호가위) 매수

흐름: KV 토글 ON? → 오늘 미매수? → 레이더 1위(suspects[0]) 안전필터 통과? →
      NXT 거래가능 여부로 슬롯 분기 → 100만원 매수 → 포지션 기록.
⚠ 실발주는 kiwoom_trade가 AUTOTRADE_LIVE=1 일 때만(아니면 dry 로그). 기본은 안전(미발주).
"""
import os
import sys
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autotrade_common as ac
import kiwoom_trade as kt


def _record(slot, top, res, rank, alloc_krw):
    """실매수 체결분 포지션 기록 + 원장 + 텔레그램. 기록 실패해도 크래시 대신 크게 로깅."""
    code, name = top["code"], top.get("name", "")
    try:
        data = ac.load_positions()
        data["positions"].append({
            "id": f"{code}-{ac.today_str()}-{slot}",
            "code": code, "name": name,
            "entry_date": ac.today_str(),
            "entry_price": res["ref_price"],
            "qty": res["qty"], "qty_open": res["qty"],
            "market": res["market"], "alloc_krw": alloc_krw, "rank": rank,
            "tp1_done": False, "status": "open",
            "opened_at": datetime.now(ac.KST).strftime("%Y-%m-%d %H:%M:%S"),
            "pattern": top.get("pattern"), "suspicion_score": top.get("suspicion_score"),
        })
        ac.save_positions(data)
        ac.log(f"[exec:{slot}] ★매수 체결·기록: {rank}위 {name}({code}) {res['qty']}주 @~{res['ref_price']:,.0f} "
               f"({res['market']}, 배정 {alloc_krw:,}원)")
        ac.notify_trade(
            f"🟢 [자동매매] 신규 매수 {rank}위 {name}({code}) {res['qty']}주 @~{res['ref_price']:,.0f} "
            f"({res['market']} · 배정 {alloc_krw:,}원)\n"
            f"pattern={top.get('pattern')} score={top.get('suspicion_score')} · 익일 14:50 강제청산")
        ac.append_trade_event({
            "type": "entry", "id": f"{code}-{ac.today_str()}-{slot}", "code": code, "name": name,
            "entry_date": ac.today_str(), "market": res["market"], "slot": slot,
            "qty": res["qty"], "entry_price": res["ref_price"], "alloc_krw": alloc_krw, "rank": rank,
            "pattern": top.get("pattern"), "suspicion_score": top.get("suspicion_score"), "dry": False})
    except Exception as e:
        ac.log(f"[exec:{slot}] 🚨 매수는 체결됐으나 포지션 기록 실패: {e} — 수동 확인 필요(중복매수·청산누락 위험)")


def run(slot, dry=True):
    if not ac.autotrade_enabled():
        ac.log(f"[exec:{slot}] 자동매매 OFF(KV autotrade:enabled≠1) — 매수 안 함")
        return
    try:
        data = ac.load_positions()
    except Exception as e:
        # 포지션 상태 불명(파일 읽기 실패)이면 중복매수 여부 확인 불가 → fail-closed(매수 중단).
        ac.log(f"[exec:{slot}] 포지션 상태 확인 실패({e}) — fail-closed(매수 중단)")
        return
    # 전날 이월 미청산 포지션이 남아있으면 = 14:50 강제청산 실패 → 갈아타기 불가. 신규 매수 차단(중복 보유 방지).
    stale = [p for p in ac.open_positions(data) if p.get("entry_date") != ac.today_str()]
    if stale:
        names = ", ".join(f"{p.get('name','')}({p['code']})" for p in stale)
        ac.log(f"[exec:{slot}] 🚨 전날 미청산 포지션 잔존({names}) — 강제청산 실패 의심. 신규 매수 차단(중복보유 방지)")
        ac.notify_trade(
            f"🚨 [자동매매] 전날 포지션 미청산: {names}\n"
            f"14:50 강제청산이 안 된 상태라 오늘 신규 매수를 차단했습니다. 수동 확인 필요.")
        return

    # 하루 최대 2종목. 이미 오늘 매수한 만큼 슬롯 차감.
    slots_left = ac.MAX_AUTOTRADE_STOCKS - len(ac.todays_positions(data))
    if slots_left <= 0:
        ac.log(f"[exec:{slot}] 오늘 최대 매수 종목수({ac.MAX_AUTOTRADE_STOCKS}) 도달 — 추가 매수 안 함")
        return

    ranks = ac.read_ranks()
    top = ac.top_suspects(3)
    # 자격 종목: 선택 랭크·존재·safety 통과·오늘 그 코드 미매수. 코드 dedup. 하루 최대치로 캡.
    eligible = []
    seen = set()
    for r in ranks:
        if r - 1 >= len(top):
            continue
        s = top[r - 1]
        code = s.get("code")
        if not code or code in seen:
            continue
        ok, reason = ac.safety_ok(s)
        if not ok:
            ac.log(f"[exec:{slot}] {r}위 {code} 안전필터 차단: {reason}")
            continue
        if ac.already_bought(code, data):
            ac.log(f"[exec:{slot}] {r}위 {code} 오늘 이미 매수 — 스킵")
            continue
        seen.add(code)
        eligible.append((r, s))
    eligible = eligible[:slots_left]
    if not eligible:
        ac.log(f"[exec:{slot}] 매수 자격 종목 없음 (선택 랭크 {ranks})")
        return

    # 당일 예산 100만원을 '남은 자격종목 수'로 균등분할(초과·미달 없이 슬롯 간 안전 배분).
    remaining_budget = ac.DAILY_BUDGET - ac.deployed_today(data)
    per_stock = remaining_budget // max(1, len(eligible))
    if per_stock <= 0:
        ac.log(f"[exec:{slot}] 잔여 예산 0 — 매수 안 함")
        return
    ac.log(f"[exec:{slot}] 선택랭크={ranks} 자격={[(r, s['code']) for r, s in eligible]} "
           f"잔여예산={remaining_budget:,} 종목당={per_stock:,}")

    for rank, top_s in eligible:
        code, name = top_s["code"], top_s.get("name", "")
        nxt = kt.is_nxt_tradable(code)
        # 슬롯 라우팅: krx=비NXT 종목만 지금 / nxt=NXT 종목만 지금 (반대 슬롯은 위임)
        if slot == "krx" and nxt:
            ac.log(f"[exec:krx] {rank}위 {code} NXT 거래가능 → 19:50 NXT 슬롯 위임")
            continue
        if slot == "nxt" and not nxt:
            ac.log(f"[exec:nxt] {rank}위 {code} NXT 불가 → 15:18 KRX 슬롯 대상")
            continue
        try:
            res = (kt.buy_market_krx(code, per_stock, dry=dry) if slot == "krx"
                   else kt.buy_limit_nxt(code, per_stock, dry=dry))
        except Exception as e:
            ac.log(f"[exec:{slot}] {rank}위 {code} 매수 실패(스킵): {e}")
            continue
        if res.get("dry"):
            ac.log(f"[exec:{slot}] {rank}위 {code} DRY — 발주 안 함({res.get('reason')}). 미기록.")
            continue
        _record(slot, top_s, res, rank, per_stock)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=["krx", "nxt"], required=True)
    ap.add_argument("--dry", action="store_true", help="강제 dry(발주 안 함). 미지정 시 kiwoom_trade 기본 dry=False지만 AUTOTRADE_LIVE=1 없으면 여전히 미발주.")
    a = ap.parse_args()
    run(a.slot, dry=a.dry)
