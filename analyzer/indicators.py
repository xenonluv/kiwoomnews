#!/usr/bin/env python3
"""팀원2 보조 — 기술적 지표를 '코드로' 계산 (LLM은 수치 해석만).

일봉(OHLCV)에서 마감강도·거래량·MA정배열·MACD·RSI·Stochastic Slow·일목균형표를
순수 파이썬으로 산출. LLM이 직접 계산하면 환각이 나므로 반드시 여기서 수치를 만든다.

데이터: 기존 scripts/team3_price_context.fetch_daily 재사용(+ scripts/net 레이트리밋).
"""
import os
import sys

# 기존 scripts/ 재사용 (중복 구현·중복 호출 방지)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from team3_price_context import fetch_daily  # noqa: E402


def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def ema_series(xs, n):
    if len(xs) < n:
        return []
    k = 2 / (n + 1)
    out = [sum(xs[:n]) / n]  # 시드 = 첫 n개 SMA
    for x in xs[n:]:
        out.append(x * k + out[-1] * (1 - k))
    return out  # 길이 = len(xs)-n+1


def macd(closes, fast=12, slow=26, sig=9):
    if len(closes) < slow + sig:
        return None
    ef, es = ema_series(closes, fast), ema_series(closes, slow)
    tail = min(len(ef), len(es))
    macd_line = [ef[-tail + i] - es[-tail + i] for i in range(tail)]
    sigs = ema_series(macd_line, sig)
    if not sigs:
        return None
    line, signal = macd_line[-1], sigs[-1]
    prev_line = macd_line[-2] if len(macd_line) >= 2 else line
    prev_sig = sigs[-2] if len(sigs) >= 2 else signal
    golden = prev_line <= prev_sig and line > signal  # 시그널 상향 돌파
    return {"macd": round(line, 2), "signal": round(signal, 2),
            "hist": round(line - signal, 2), "above_zero": line > 0,
            "golden_cross": golden, "bullish": line > signal}


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(gains)):  # Wilder 평활
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return {"rsi": 100.0, "zone": "과매수"}
    r = 100 - 100 / (1 + ag / al)
    zone = "과매수" if r >= 80 else "과매도" if r <= 30 else "강세" if r >= 50 else "약세"
    return {"rsi": round(r, 1), "zone": zone}


def stochastic_slow(highs, lows, closes, n=14, k=3, d=3):
    if len(closes) < n + k + d:
        return None
    fast_k = []
    for i in range(n - 1, len(closes)):
        hh = max(highs[i - n + 1:i + 1])
        ll = min(lows[i - n + 1:i + 1])
        fast_k.append(100 * (closes[i] - ll) / (hh - ll) if hh > ll else 50.0)
    slow_k = [sma(fast_k[:i + 1], k) for i in range(k - 1, len(fast_k))]
    slow_d = [sma(slow_k[:i + 1], d) for i in range(d - 1, len(slow_k))]
    if not slow_d:
        return None
    K, D = slow_k[-1], slow_d[-1]
    pk = slow_k[-2] if len(slow_k) >= 2 else K
    pd = slow_d[-2] if len(slow_d) >= 2 else D
    golden = pk <= pd and K > D
    return {"k": round(K, 1), "d": round(D, 1), "golden_cross": golden,
            "overbought": K >= 80, "bullish": K > D}


def ichimoku(highs, lows, closes):
    def midpoint(h, l, n, idx):
        if idx - n + 1 < 0:
            return None
        return (max(h[idx - n + 1:idx + 1]) + min(l[idx - n + 1:idx + 1])) / 2

    i = len(closes) - 1
    tenkan = midpoint(highs, lows, 9, i)
    kijun = midpoint(highs, lows, 26, i)
    # 오늘 위치에 그려지는 구름 = 26봉 전에 계산된 선행스팬
    j = i - 26
    if j < 0 or tenkan is None or kijun is None:
        return {"available": False}
    t26, k26 = midpoint(highs, lows, 9, j), midpoint(highs, lows, 26, j)
    spanA = (t26 + k26) / 2 if t26 and k26 else None
    spanB = midpoint(highs, lows, 52, j)
    if spanA is None or spanB is None:
        return {"available": False}
    cloud_top, cloud_bot = max(spanA, spanB), min(spanA, spanB)
    close = closes[-1]
    return {"available": True,
            "above_cloud": close > cloud_top,
            "in_cloud": cloud_bot <= close <= cloud_top,
            "tenkan_gt_kijun": tenkan > kijun,
            "tenkan": round(tenkan, 1), "kijun": round(kijun, 1),
            "cloud_top": round(cloud_top, 1), "cloud_bot": round(cloud_bot, 1)}


def compute_indicators(code, name=None, days=200):
    """종목코드 → 기술적 지표 묶음(수치 + 강세 플래그)."""
    name = name or code
    try:
        d = fetch_daily(code, days=days)
    except Exception as e:
        return {"code": code, "name": name, "error": str(e)}
    if len(d) < 35:
        return {"code": code, "name": name, "error": f"데이터부족({len(d)})"}

    closes = [x["close"] for x in d]
    highs = [x["high"] for x in d]
    lows = [x["low"] for x in d]
    vols = [x["volume"] for x in d]
    last, hi, lo = closes[-1], highs[-1], lows[-1]

    ma5, ma20, ma60 = sma(closes, 5), sma(closes, 20), sma(closes, 60)
    aligned = bool(ma5 and ma20 and ma60 and ma5 > ma20 > ma60)  # 정배열
    close_strength = round((last - lo) / (hi - lo), 2) if hi > lo else None  # 마감강도(1=고가마감)
    vol_avg20 = sma(vols, 20)
    vol_ratio = round(vols[-1] / vol_avg20, 2) if vol_avg20 else None
    max_value5 = max((closes[i] * vols[i] for i in range(len(d) - 5, len(d))), default=None)

    return {
        "code": code, "name": name, "last_close": last,
        "ma_aligned": aligned, "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "close_strength": close_strength,  # 0~1, 0.8+ = 강세 마감
        "volume_vs_20d": vol_ratio,
        "max_trading_value_5d": max_value5,
        "macd": macd(closes),
        "rsi": rsi(closes),
        "stochastic_slow": stochastic_slow(highs, lows, closes),
        "ichimoku": ichimoku(highs, lows, closes),
    }


if __name__ == "__main__":
    import json
    code = sys.argv[1] if len(sys.argv) > 1 else "018880"
    name = sys.argv[2] if len(sys.argv) > 2 else code
    print(json.dumps(compute_indicators(code, name), ensure_ascii=False, indent=2))
