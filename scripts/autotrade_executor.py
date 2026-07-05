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
    if ac.bought_today(data):
        ac.log(f"[exec:{slot}] 오늘 이미 매수함 — 중복 매수 안 함")
        return
    # 전날 이월 미청산 포지션이 남아있으면 = 14:50 강제청산 실패 → 갈아타기 불가. 신규 매수 차단(중복 보유 방지).
    stale = [p for p in ac.open_positions(data) if p.get("entry_date") != ac.today_str()]
    if stale:
        names = ", ".join(f"{p.get('name','')}({p['code']})" for p in stale)
        ac.log(f"[exec:{slot}] 🚨 전날 미청산 포지션 잔존({names}) — 강제청산 실패 의심. 신규 매수 차단(중복보유 방지)")
        ac.notify_trade(
            f"🚨 [자동매매] 전날 포지션 미청산: {names}\n"
            f"14:50 강제청산이 안 된 상태라 오늘 15:18 신규 매수를 차단했습니다. 수동 확인 필요.")
        return
    top = ac.top_suspect()
    ok, reason = ac.safety_ok(top)
    if not ok:
        ac.log(f"[exec:{slot}] 안전필터 차단: {reason}")
        return
    code, name = top["code"], top.get("name", "")
    nxt = kt.is_nxt_tradable(code)
    ac.log(f"[exec:{slot}] 레이더 1위 {name}({code}) pattern={top.get('pattern')} "
           f"score={top.get('suspicion_score')} NXT거래가능={nxt}")

    if slot == "krx":
        if nxt:
            ac.log(f"[exec:krx] {code} NXT 거래가능 → 15:18 매수 스킵(19:50 NXT 슬롯에 위임)")
            return
        res = kt.buy_market_krx(code, ac.BUY_KRW, dry=dry)
    elif slot == "nxt":
        if not nxt:
            ac.log(f"[exec:nxt] {code} NXT 거래불가 → 19:50 매수 스킵(15:18 KRX 슬롯 대상)")
            return
        res = kt.buy_limit_nxt(code, ac.BUY_KRW, dry=dry)
    else:
        raise SystemExit(f"알 수 없는 slot: {slot}")

    if res.get("dry"):
        ac.log(f"[exec:{slot}] DRY — 발주 안 함({res.get('reason')}). 포지션 미기록.")
        return
    # 실발주 성공 → 포지션 기록(진입가는 참조가 근사 — 시장가 체결가는 이후 체결조회로 정밀화 가능).
    # ⚠ 이미 실매수가 나갔으므로 기록 실패해도 크래시로 묻지 말고 크게 로깅(수동 확인). 재시도는 load/save 내장.
    try:
        data = ac.load_positions()
        data["positions"].append({
            "id": f"{code}-{ac.today_str()}-{slot}",
            "code": code, "name": name,
            "entry_date": ac.today_str(),
            "entry_price": res["ref_price"],
            "qty": res["qty"], "qty_open": res["qty"],
            "market": res["market"],
            "tp1_done": False, "status": "open",
            "opened_at": datetime.now(ac.KST).strftime("%Y-%m-%d %H:%M:%S"),
            "pattern": top.get("pattern"), "suspicion_score": top.get("suspicion_score"),
        })
        ac.save_positions(data)
        ac.log(f"[exec:{slot}] ★매수 체결·포지션 기록: {name}({code}) {res['qty']}주 @~{res['ref_price']:,.0f} ({res['market']})")
        ac.notify_trade(
            f"🟢 [자동매매] 신규 매수 {name}({code}) {res['qty']}주 @~{res['ref_price']:,.0f} ({res['market']} 시장가)\n"
            f"오늘 레이더 1위 · pattern={top.get('pattern')} score={top.get('suspicion_score')} · 익일 14:50 강제청산")
        ac.append_trade_event({
            "type": "entry", "id": f"{code}-{ac.today_str()}-{slot}", "code": code, "name": name,
            "entry_date": ac.today_str(),
            "market": res["market"], "slot": slot, "qty": res["qty"], "entry_price": res["ref_price"],
            "pattern": top.get("pattern"), "suspicion_score": top.get("suspicion_score"), "dry": False})
    except Exception as e:
        ac.log(f"[exec:{slot}] 🚨 매수는 체결됐으나 포지션 기록 실패: {e} — 수동 확인·기록 필요(중복매수·청산누락 위험)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=["krx", "nxt"], required=True)
    ap.add_argument("--dry", action="store_true", help="강제 dry(발주 안 함). 미지정 시 kiwoom_trade 기본 dry=False지만 AUTOTRADE_LIVE=1 없으면 여전히 미발주.")
    a = ap.parse_args()
    run(a.slot, dry=a.dry)
