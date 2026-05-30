#!/usr/bin/env python3
"""팀원3 보조 — 네이버 금융에서 실제 시세/추세 컨텍스트를 계산.
이동평균(5/20/60), 이격도, 거래량 추세, 52주 위치 → 차트 위치 후보 산출.

사용: python3 scripts/team3_price_context.py <종목코드> [종목명]
출력: stdout JSON (퀀트 판단 입력용). 데이터 부족 시 market_status_hint=분석불가.
"""
import sys
import json
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def fetch_daily(code, days=120):
    end = datetime.now(KST).strftime("%Y%m%d")
    start = (datetime.now(KST) - timedelta(days=days)).strftime("%Y%m%d")
    url = (
        "https://api.finance.naver.com/siseJson.naver?symbol="
        + code
        + "&requestType=1&startTime="
        + start
        + "&endTime="
        + end
        + "&timeframe=day"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read().decode("utf-8", "ignore")
    # 응답은 파이썬 리스트형 텍스트(작은따옴표) → JSON 정규화
    raw = raw.strip().replace("'", '"')
    rows = json.loads(raw)
    # 첫 행은 헤더
    data = []
    for row in rows[1:]:
        # [날짜, 시가, 고가, 저가, 종가, 거래량, ...]
        data.append(
            {
                "date": str(row[0]),
                "close": float(row[4]),
                "high": float(row[2]),
                "low": float(row[3]),
                "volume": float(row[5]),
            }
        )
    return data


def ma(closes, n):
    if len(closes) < n:
        return None
    return round(sum(closes[-n:]) / n, 2)


def compute_context(code, name=None):
    """종목코드로 실제 일봉을 받아 기술적 지표 컨텍스트(dict) 반환. 재사용용."""
    name = name or code
    try:
        d = fetch_daily(code)
    except Exception as e:
        return {"ticker_code": code, "ticker_name": name,
                "market_status_hint": "분석불가", "error": str(e)}
    if len(d) < 20:
        return {"ticker_code": code, "ticker_name": name,
                "market_status_hint": "분석불가", "reason": "데이터 부족"}

    closes = [x["close"] for x in d]
    vols = [x["volume"] for x in d]
    last = closes[-1]
    prev = closes[-2]
    ma5, ma20, ma60 = ma(closes, 5), ma(closes, 20), ma(closes, 60)
    hi52 = max(x["high"] for x in d)
    lo52 = min(x["low"] for x in d)
    disparity20 = round(last / ma20 * 100, 1) if ma20 else None  # 20일 이격도
    vol_avg20 = round(sum(vols[-20:]) / 20)
    vol_ratio = round(vols[-1] / vol_avg20, 2) if vol_avg20 else None
    chg = round((last / prev - 1) * 100, 2)
    pos_in_range = round((last - lo52) / (hi52 - lo52) * 100, 1) if hi52 > lo52 else None

    # 단순 규칙 기반 차트 위치 후보(최종 판단은 팀원3/Codex)
    hint = "분석불가"
    if ma20 and ma60:
        if disparity20 >= 115:
            hint = "과다상승"
        elif ma5 and ma20 < ma60 and pos_in_range is not None and pos_in_range <= 20 and chg > 0:
            hint = "저점"
        elif last < ma20 and ma20 >= ma60 and last > ma60:
            hint = "눌림목"

    return {
        "ticker_code": code,
        "ticker_name": name,
        "last_close": last,
        "change_pct_day": chg,
        "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "disparity_20d_pct": disparity20,
        "pos_in_120d_range_pct": pos_in_range,
        "volume_vs_20d_avg": vol_ratio,
        "period_high": hi52, "period_low": lo52,
        "market_status_hint": hint,
        "data_window_days": len(d),
    }


def main():
    if len(sys.argv) < 2:
        print("usage: team3_price_context.py <code> [name]", file=sys.stderr)
        sys.exit(1)
    code = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else code
    print(json.dumps(compute_context(code, name), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
