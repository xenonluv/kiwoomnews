#!/usr/bin/env python3
"""NXT 통합(UN) vs KRX(J) 거래대금 배율 실측 — 1회성 캘리브레이션 (cron 제외).

레이더를 J→UN으로 전환하면 거래대금이 종목·일자별로 1~3배 커진다. 폭발 게이트(1천억)·
재반등(30억)·flow(투신 500억)·explosion 점수 스케일을 '추측'이 아니라 이 실측 분포로 정하기 위한 도구.

흐름:
  1) 유니버스 = 코스피·코스닥 거래대금 상위(value_rank, UN 기준) 합집합
  2) 각 종목 최근 ~25거래일 일봉을 J·UN 둘 다 조회 → (code,date)별 UN/J 거래대금 배율
  3) J거래대금 규모 구간별(폭발 게이트 재산정용) 배율 분포(중앙값·분위) 집계
  4) 투신 매집액(investor_trade_daily) J·UN 배율도 측정 → flow 스케일 재산정용
  5) stdout 요약 + data/nxt_calibration.json

사용: python3 scripts/nxt_calibration.py [종목수per시장=20] [일수=25]
시크릿: KIS_APP_KEY/SECRET (.env, Mac). UN/J 비교라 KIS_MARKET 환경변수는 무시(명시 호출).
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kis_client as kis  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "data", "nxt_calibration.json")
EOK = 1e8  # 억

# J거래대금 규모 구간(원) — 폭발 게이트(1천억) 주변을 촘촘히
VALUE_BUCKETS = [(0, 100), (100, 300), (300, 700), (700, 1000), (1000, 3000), (3000, 1e9)]  # 억 단위


def pct(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    i = max(0, min(len(s) - 1, int(round((len(s) - 1) * p / 100))))
    return s[i]


def summarize(ratios):
    if not ratios:
        return {"n": 0}
    return {"n": len(ratios), "median": round(pct(ratios, 50), 3),
            "p25": round(pct(ratios, 25), 3), "p75": round(pct(ratios, 75), 3),
            "p90": round(pct(ratios, 90), 3), "max": round(max(ratios), 3)}


def universe(per_market):
    codes = {}
    for mkt in ("KOSPI", "KOSDAQ"):
        try:
            for r in kis.value_rank_union(mkt, top_n=per_market):  # J∪NX 합집합
                codes[r["code"]] = r["name"]
        except Exception as e:
            print(f"[warn] value_rank_union {mkt} 실패: {e}", file=sys.stderr)
    return codes


def main():
    per_market = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 25

    codes = universe(per_market)
    print(f"유니버스 {len(codes)}종목 (코스피·코스닥 거래대금 상위 {per_market} 합집합)", file=sys.stderr)

    all_ratios = []
    bucket_ratios = {f"{lo}~{hi}억": [] for lo, hi in VALUE_BUCKETS}
    ivtr_ratios = []
    samples = 0

    for code, name in codes.items():
        try:
            jd = {b["date"]: b for b in kis.daily_prices(code, days=days, market="J")}
            ud = {b["date"]: b for b in kis.daily_prices(code, days=days, market="UN")}
        except Exception as e:
            print(f"[skip] {code} {name} 일봉 실패: {e}", file=sys.stderr)
            continue
        for d, jb in jd.items():
            ub = ud.get(d)
            jv = jb.get("value") or 0
            uv = (ub.get("value") if ub else 0) or 0
            if jv <= 0 or uv <= 0:
                continue
            ratio = uv / jv
            all_ratios.append(ratio)
            samples += 1
            jv_eok = jv / EOK
            for lo, hi in VALUE_BUCKETS:
                if lo <= jv_eok < hi:
                    bucket_ratios[f"{lo}~{hi}억"].append(ratio)
                    break
        # 투신 매집액 배율
        try:
            jt = {r["date"]: r for r in kis.investor_trade_daily(code, market="J")}
            ut = {r["date"]: r for r in kis.investor_trade_daily(code, market="UN")}
            for d, jr in jt.items():
                jw, uw = jr.get("ivtr_won") or 0, (ut.get(d, {}).get("ivtr_won") or 0)
                if abs(jw) > 1.0 and (jw > 0) == (uw > 0):  # 같은 방향·유의미한 매집만
                    ivtr_ratios.append(abs(uw) / abs(jw))
        except Exception:
            pass

    result = {
        "universe_n": len(codes),
        "samples": samples,
        "value_ratio_overall": summarize(all_ratios),
        "value_ratio_by_J_bucket": {k: summarize(v) for k, v in bucket_ratios.items()},
        "ivtr_ratio": summarize(ivtr_ratios),
        "note": "ratio = UN거래대금 / J거래대금. 폭발 게이트 재산정은 700~1000억·1000억+ 버킷의 median 참고.",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n=== UN/J 거래대금 배율 ===")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
