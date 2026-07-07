#!/usr/bin/env python3
"""💥 흔들기 과거 표본 소급 재구성 (backfill) — 튜닝 표본을 앞당긴다.

라이브 흔들기 스캔(radar.scan_shakeout)은 당일 스냅샷(now)만 보므로 과거 흔들기 신호는 표본이 없다.
그러나 산식이 전부 '그날의 일봉'으로 환원되므로(now.high→daily[D].high, now.prev_close→daily[D-1].close,
now.price→daily[D].close, now.volume→daily[D].volume(UN)), 일봉만 있으면 과거 어느 날이든 흔들기 게이트를
재현하고 익일봉으로 결과를 채점할 수 있다.

⚠️ 정의는 radar.scan_shakeout과 1:1 (헬퍼·상수를 그대로 import). 익일결과(hit/next_high_pct)는 실제 일봉.
⚠️ 유니버스 한계(정직): 과거 up/down 랭킹을 API로 못 얻어, 후보 = 폭발 레지스트리 ∪ youtong ∪ radar_history
   코드로 한정(추적 대상은 다 포함되나, 그 밖의 종목은 누락 가능 — 생존편향 소폭). 결과에 universe 명시.

출력: data/shakeout_backfill.json {generated_at, window_days, universe_n, date_min, date_max, samples[]}
      radar_backtest.write_performance가 이 파일을 흔들기 표본에 (code,date) 디둡 병합 → shakeout_bands 튜닝표.

사용:
  RADAR_BROKER=kiwoom python3 scripts/shakeout_backfill.py            # 재구성 + 저장
  RADAR_BROKER=kiwoom python3 scripts/shakeout_backfill.py --days 70  # 조회 창(일봉 수)
"""
import os
import sys
import json
import glob
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 브로커 스위치 — radar.py와 동일(기본 키움). 익일봉·거래량을 실제 운영 브로커로 맞춘다.
if os.environ.get("RADAR_BROKER", "kiwoom").lower() == "kis":
    import kis_client as kis
else:
    import kiwoom_client as kis

import float_ratio
# 흔들기 산식·상수를 radar에서 그대로 가져와 정의 드리프트 차단(SSOT).
from radar import (
    SHAKEOUT_HIGH_PCT, SHAKEOUT_FADE_PCT, SHAKEOUT_TURNOVER_MIN, SHAKEOUT_RUN6D_MAX,
    _shakeout_turnover_tier, _shakeout_dd_tier, _shakeout_strength, _very_good_tier,
)

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(REPO, "data", "shakeout_backfill.json")


def log(msg):
    sys.stderr.write(msg + "\n")


def gather_universe():
    """후보 코드 = 폭발 레지스트리 ∪ youtong ∪ radar_history."""
    codes = set()
    # 폭발 레지스트리: records 키가 "YYYYMMDD:code"
    p = os.path.join(REPO, ".explosion_registry.json")
    if os.path.exists(p):
        try:
            rec = json.load(open(p, encoding="utf-8")).get("records", {})
            for k in rec:
                if ":" in k:
                    codes.add(k.split(":", 1)[1])
        except Exception as e:
            log(f"[warn] explosion_registry: {e}")
    # youtong 레지스트리: codes = {code: {...}}
    p = os.path.join(REPO, ".youtong_registry.json")
    if os.path.exists(p):
        try:
            codes.update(json.load(open(p, encoding="utf-8")).get("codes", {}).keys())
        except Exception as e:
            log(f"[warn] youtong_registry: {e}")
    # radar_history 전체 코드
    for f in glob.glob(os.path.join(REPO, "data", "radar_history", "*.json")):
        try:
            codes.update(json.load(open(f, encoding="utf-8")).get("suspects", {}).keys())
        except Exception:
            pass
    return sorted(c for c in codes if c and c.isdigit() and len(c) == 6)


def scan_code(code, days, float_cache, today):
    """한 코드의 일봉에서 모든 과거 흔들기일을 재현 → 표본 리스트."""
    try:
        daily = kis.daily_prices_jmoney_un(code, days=days)   # 가격=J / 거래량=UN (라이브와 동일)
    except Exception as e:
        log(f"  [warn] {code} 일봉 실패: {e}")
        return []
    bars = sorted((b for b in daily if b.get("close") and b.get("high")),
                  key=lambda b: b["date"])
    if len(bars) < 21:
        return []
    fr, listed = float_ratio.get_float_and_listed(code, cache=float_cache)
    fs = (listed * fr) if (fr and listed) else None
    if not fs:
        return []   # 유동비율 미상 → 회전율 확정 불가(라이브와 동일 fail-safe)

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    vols = [b.get("volume") or 0 for b in bars]
    name = None
    out = []
    # D = index i, 신호일. 필요: prev(i-1), MA20(i>=19), run6(i>=6), 익일봉(i+1 존재 & 종가확정)
    for i in range(19, len(bars) - 1):
        nb = bars[i + 1]
        if nb["date"] >= today:      # 익일 종가 미확정(당일/미래) → 채점 보류
            continue
        prev_close = closes[i - 1]
        if not prev_close:
            continue
        high_pct = (highs[i] / prev_close - 1) * 100
        change_pct = round((closes[i] / prev_close - 1) * 100, 2)
        fade = high_pct - change_pct
        if high_pct < SHAKEOUT_HIGH_PCT or fade < SHAKEOUT_FADE_PCT:
            continue
        turnover = vols[i] / fs * 100
        if turnover < SHAKEOUT_TURNOVER_MIN:
            continue
        ma20 = sum(closes[i - 19:i + 1]) / 20
        if closes[i] < ma20:
            continue                                 # 추세 사수 실패(라이브와 동일)
        turnover_2d = (vols[i] + vols[i - 1]) / fs * 100
        win = highs[max(0, i - 59):i + 1]            # 최근 60봉(신호일 포함) 고점 — 라이브 days=60 대응
        peak = max(win) if win else 0
        peak_dd = (closes[i] / peak - 1) * 100 if peak else 0
        peak6 = max(highs[max(0, i - 5):i + 1])      # 오늘+직전5일 고가 — 라이브 dd6와 동일
        dd6 = (closes[i] / peak6 - 1) * 100 if peak6 else 0
        run6 = (closes[i] / closes[i - 6] - 1) * 100 if (i >= 6 and closes[i - 6]) else None
        if run6 is not None and run6 >= SHAKEOUT_RUN6D_MAX and change_pct < 0:
            continue                                 # 과확장 붕괴(라이브와 동일)
        stier = (_shakeout_turnover_tier({"turnover_2d_pct": turnover_2d})
                 + _shakeout_dd_tier({"peak_dd_pct": peak_dd}))
        vg_tier = _very_good_tier(dd6)
        ma10 = sum(closes[i - 9:i + 1]) / 10
        entry = closes[i]
        out.append({
            "date": bars[i]["date"], "code": code, "name": name or code,
            "pattern": "shakeout", "shakeout": True, "backfill": True,
            "high_pct": round(high_pct, 2), "change_pct": change_pct, "fade_pct": round(fade, 1),
            # 신호일 원천 스냅샷 — 라이브 history와 같은 키로 저장해 조건 재튜닝을 가능하게 한다.
            "entry": entry,
            "signal_date": bars[i]["date"],
            "signal_open": bars[i].get("open"),
            "signal_high": bars[i].get("high"),
            "signal_low": bars[i].get("low"),
            "signal_close": entry,
            "signal_prev_close": prev_close,
            "signal_volume": vols[i],
            "signal_value": bars[i].get("value"),
            "signal_value_eok": round((bars[i].get("value") or 0) / 1e8, 1),
            "signal_peak6_price": peak6,
            "signal_peak60_price": peak,
            "signal_ma20": round(ma20, 1),
            "signal_ma10": round(ma10, 1),
            "ma20_gap_pct": round((entry / ma20 - 1) * 100, 1) if ma20 else None,
            "ma10_margin_pct": round((entry / ma10 - 1) * 100, 2) if ma10 else None,
            "float_ratio": fr,
            "turnover_pct": round(turnover, 1),
            "turnover_2d_pct": round(turnover_2d, 1),
            "turnover_band": _shakeout_turnover_tier({"turnover_2d_pct": turnover_2d}),
            "peak_dd_pct": round(peak_dd, 1),
            "dd_band": _shakeout_dd_tier({"peak_dd_pct": peak_dd}),
            "dd6_pct": round(dd6, 1),
            "very_good": vg_tier in ("tier1", "tier2"),
            "very_good_tier": vg_tier,
            "very_good_candidate": vg_tier == "candidate",
            "strength_tier": stier, "strength": _shakeout_strength(stier),
            "run_6d_pct": round(run6, 1) if run6 is not None else None,
            # 익일 결과(실제 일봉) — radar_backtest evaluate()와 동일 정의
            "eval_date": nb["date"],
            "next_open": nb["open"],
            "next_high": nb["high"],
            "next_low": nb["low"],
            "next_close": nb["close"],
            "hit": nb["close"] > entry,
            "high3": nb["high"] >= entry * 1.03,
            "return_pct": round((nb["close"] / entry - 1) * 100, 2),
            "next_open_pct": round((nb["open"] / entry - 1) * 100, 2),
            "next_high_pct": round((nb["high"] / entry - 1) * 100, 2),
            "next_low_pct": round((nb["low"] / entry - 1) * 100, 2),
        })
    return out


def main():
    days = 70
    if "--days" in sys.argv:
        try:
            days = int(sys.argv[sys.argv.index("--days") + 1])
        except (ValueError, IndexError):
            pass
    if hasattr(kis, "enable_run_cache"):
        kis.enable_run_cache()
    today = datetime.now(KST).strftime("%Y%m%d")
    universe = gather_universe()
    log(f"[backfill] 유니버스 {len(universe)}종목 · 창 {days}봉 · today={today}")
    float_cache = {}
    samples = []
    for n, code in enumerate(universe, 1):
        samples.extend(scan_code(code, days, float_cache, today))
        if n % 20 == 0:
            log(f"  …{n}/{len(universe)} 스캔, 누적 표본 {len(samples)}")
    samples.sort(key=lambda s: (s["date"], s["code"]))
    dates = [s["date"] for s in samples]
    out = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "window_days": days,
        "universe_n": len(universe),
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "n": len(samples),
        "note": ("과거 흔들기 소급 재구성(일봉 기반). 정의는 radar.scan_shakeout과 1:1, 익일결과는 실제 일봉. "
                 "유니버스=폭발레지스트리∪youtong∪radar_history(과거 랭킹 부재로 그 밖 종목은 누락 가능)."),
        "samples": samples,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    json.dump(out, open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 요약: 적중률·밴드 분포
    hits = sum(1 for s in samples if s["hit"])
    log(f"[backfill] 완료 — 표본 {len(samples)}건 "
        f"({out['date_min']}~{out['date_max']}) 익일적중 {hits}/{len(samples)}"
        f"{f' ({round(hits/len(samples)*100,1)}%)' if samples else ''} → {OUT_PATH}")


if __name__ == "__main__":
    main()
