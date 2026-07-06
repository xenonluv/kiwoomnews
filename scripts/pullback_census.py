#!/usr/bin/env python3
"""[전수조사 테스트 — 완전 격리·프로덕션 무접촉]
가설: 6거래일 이내 최고가(고가) 대비 −X% 빠진 날 → 익일 고가 +7% 터치(급등) 확률.

⚠ 프로덕션 무접촉·격리 (cron 제외·수동 실행 전용·표시측정용 — core 점수·게이트 무반영):
  - radar.py/radar_backtest/publish import 안 함(오직 읽기전용 kiwoom_client.daily_prices).
  - radar.json·registry·web 등 프로덕션 로직/파일 쓰기 0. 산출물은 data/pullback_census.json(연구 결과)뿐.
  - 유니버스는 공개 마스터 zip 다운로드(코드 복사, reaccum_backtest import 안 함 → kis_client 체인 회피).

사용:
  python3 pullback_census.py --codes 001260 --detail        # 스모크(특정종목 신호일 상세)
  python3 pullback_census.py --limit 40                       # 파이프라인 검증(앞 40종목)
  python3 pullback_census.py                                  # 전수(전 종목)
"""
import os, sys, json, time, zipfile, argparse, urllib.request
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kiwoom_client as kw  # 읽기전용 일봉 조회 전용

MASTER_URLS = {
    "KOSPI": "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
    "KOSDAQ": "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
}
JUNK = ("스팩", "SPAC")
OUT_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pullback_census.json")


def log(m):
    print(m, file=sys.stderr, flush=True)


def parse_master_line(line):
    if len(line) < 63:
        return None
    code = line[0:9].decode("euc-kr", "ignore").strip()
    name = line[21:61].decode("euc-kr", "ignore").strip()
    if line[61:63].decode("euc-kr", "ignore").strip() != "ST":
        return None
    if not (code and name and code.isdigit() and len(code) == 6):
        return None
    if any(j in name for j in JUNK) or name.endswith("우") or name.endswith("우B") or "우선주" in name:
        return None
    return code, name


def load_master(markets):
    uni = {}
    for mkt in markets:
        try:
            with urllib.request.urlopen(MASTER_URLS[mkt], timeout=60) as r:
                content = r.read()
            with zipfile.ZipFile(BytesIO(content)) as zf:
                raw = zf.read(zf.namelist()[0])
        except Exception as e:
            log(f"[warn] {mkt} 마스터 다운로드 실패: {e}")
            continue
        n = 0
        for line in raw.split(b"\n"):
            p = parse_master_line(line)
            if p:
                uni[p[0]] = p[1]; n += 1
        log(f"  {mkt}: {n}종목")
        time.sleep(0.3)
    return uni


# ── 버킷 정의 ────────────────────────────────────────────────────────
DD_BANDS = [("≤-45(과낙)", -1e9, -45), ("-45~-30(스윗?)", -45, -30), ("-30~-20", -30, -20),
            ("-20~-10", -20, -10), ("-10~-5", -10, -5), (">-5(무낙폭)", -5, 1e9)]
VOL_BANDS = [("<1x", 0, 1), ("1~2x", 1, 2), ("2~5x", 2, 5), ("≥5x", 5, 1e9)]


def band_of(x, bands):
    if x is None:
        return None
    for label, lo, hi in bands:
        if lo <= x < hi:
            return label
    return bands[-1][0]


def new_cell():
    return {"n": 0, "t7": 0, "t13": 0, "hit": 0, "sum_high": 0.0}


def add(cell, r):
    cell["n"] += 1
    cell["t7"] += r["touch7"]
    cell["t13"] += r["touch13"]
    cell["hit"] += r["hit"]
    cell["sum_high"] += r["next_high_pct"]


def finalize(cell):
    n = cell["n"] or 1
    return {"n": cell["n"], "touch7_rate": round(cell["t7"] / n * 100, 1),
            "touch13_rate": round(cell["t13"] / n * 100, 1),
            "hit_rate": round(cell["hit"] / n * 100, 1),
            "avg_next_high": round(cell["sum_high"] / n, 2)}


def process_stock(code, days, min_value, detail_rows=None):
    """한 종목의 일봉을 훑어 신호일(모든 유동 거래일) 레코드 리스트 반환. look-ahead 안전."""
    try:
        bars = kw.daily_prices(code, days=days, market="J")  # 읽기전용
    except Exception as e:
        return None  # 조회 실패
    bars = [b for b in bars if b.get("close") and b.get("high")]
    if len(bars) < 25:
        return []
    highs = [b["high"] for b in bars]
    closes = [b["close"] for b in bars]
    vols = [b.get("volume") or 0 for b in bars]
    vals = [b.get("value") or 0 for b in bars]
    rows = []
    # d: 신호일. d+1 존재(look-ahead), d>=20(MA20·거래량20), d>=5(6일창)
    for d in range(20, len(bars) - 1):
        val_eok = vals[d] / 1e8
        if val_eok < min_value:
            continue
        peak6 = max(highs[d - 5:d + 1])
        dd6 = (closes[d] / peak6 - 1) * 100 if peak6 else 0
        peak60 = max(highs[max(0, d - 59):d + 1])
        dd60 = (closes[d] / peak60 - 1) * 100 if peak60 else 0
        vol20 = sum(vols[d - 19:d + 1]) / 20 or 1
        vol_surge = vols[d] / vol20
        ma20 = sum(closes[d - 19:d + 1]) / 20
        above_ma20 = closes[d] >= ma20
        # 직전 창(d-9..d)에 고가등락 ≥+20% 폭발일 있었나 (흔들기형 vs 그냥 흘러내림)
        prior_spike = any((highs[j] / closes[j - 1] - 1) * 100 >= 20
                          for j in range(max(1, d - 9), d + 1) if closes[j - 1])
        nb = bars[d + 1]
        next_high_pct = (nb["high"] / closes[d] - 1) * 100
        rows.append({
            "code": code, "date": bars[d]["date"], "dd6": round(dd6, 1), "dd60": round(dd60, 1),
            "val_eok": round(val_eok, 1), "vol_surge": round(vol_surge, 2),
            "above_ma20": above_ma20, "prior_spike": prior_spike,
            "next_high_pct": round(next_high_pct, 2),
            "touch7": int(next_high_pct >= 7), "touch13": int(next_high_pct >= 13),
            "hit": int(nb["close"] > closes[d]),
        })
    if detail_rows is not None:
        detail_rows.extend(rows)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="", help="스모크: 특정 6자리 코드 CSV")
    ap.add_argument("--limit", type=int, default=0, help="유니버스 앞 N종목만(0=전체)")
    ap.add_argument("--min-value-eok", type=float, default=10.0, help="신호일 거래대금 하한(억)")
    ap.add_argument("--days", type=int, default=150)
    ap.add_argument("--detail", action="store_true", help="--codes 신호일 상세 출력")
    ap.add_argument("--out", default=OUT_DEFAULT)
    a = ap.parse_args()

    if a.codes:
        uni = {c.strip(): c.strip() for c in a.codes.split(",") if c.strip()}
    else:
        log("마스터 로딩…")
        uni = load_master(["KOSPI", "KOSDAQ"])
        if a.limit:
            uni = dict(list(uni.items())[:a.limit])
    codes = list(uni)
    log(f"대상 {len(codes)}종목 · min_value={a.min_value_eok}억 · days={a.days}")

    # 집계 누산기
    tot = new_cell()
    by_dd6, by_dd60, by_vol, by_spike, by_ma20 = {}, {}, {}, {}, {}
    combos = {}   # (dd6밴드, prior_spike, above_ma20) → cell
    deep_detail = []   # dd6 ≤ -20 상세(검증·보고용)
    smoke_rows = []
    errors = 0

    t0 = time.time()
    for i, code in enumerate(codes):
        rows = process_stock(code, a.days, a.min_value_eok,
                             detail_rows=(smoke_rows if a.codes else None))
        if rows is None:
            errors += 1; continue
        for r in rows:
            add(tot, r)
            b6 = band_of(r["dd6"], DD_BANDS)
            add(by_dd6.setdefault(b6, new_cell()), r)
            add(by_dd60.setdefault(band_of(r["dd60"], DD_BANDS), new_cell()), r)
            add(by_vol.setdefault(band_of(r["vol_surge"], VOL_BANDS), new_cell()), r)
            add(by_spike.setdefault(f"prior_spike={r['prior_spike']}", new_cell()), r)
            add(by_ma20.setdefault(f"above_ma20={r['above_ma20']}", new_cell()), r)
            ck = f"{b6} | spike={int(r['prior_spike'])} | ma20up={int(r['above_ma20'])}"
            add(combos.setdefault(ck, new_cell()), r)
            if r["dd6"] <= -20:
                deep_detail.append(r)
        if not a.codes and (i + 1) % 200 == 0:
            log(f"  …{i+1}/{len(codes)} (신호 {tot['n']:,}·조회실패 {errors}·{time.time()-t0:.0f}s)")

    def order(dd_dict):
        return [{"band": lbl, **finalize(dd_dict[lbl])} for lbl, _, _ in DD_BANDS if lbl in dd_dict]

    result = {
        "test_note": "전수조사 테스트 — 프로덕션 무접촉·표시측정 전용(score 무반영)",
        "universe_n": len(codes), "signal_n": tot["n"], "query_errors": errors,
        "min_value_eok": a.min_value_eok, "days": a.days, "outcome": "익일 고가 +7% 터치(touch7)",
        "base_rate": finalize(tot),
        "by_dd6": order(by_dd6),
        "by_dd60": order(by_dd60),
        "by_vol_surge": [{"band": lbl, **finalize(by_vol[lbl])} for lbl, _, _ in VOL_BANDS if lbl in by_vol],
        "by_prior_spike": {k: finalize(v) for k, v in sorted(by_spike.items())},
        "by_ma20": {k: finalize(v) for k, v in sorted(by_ma20.items())},
        "top_combos_deep": sorted(
            [{"combo": k, **finalize(v)} for k, v in combos.items()
             if v["n"] >= 30 and k.startswith(("≤-45", "-45~-30", "-30~-20"))],
            key=lambda x: x["touch7_rate"], reverse=True),
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    # ── stdout 요약 ──
    print("\n" + "=" * 70)
    print(f"전수조사 테스트 결과 (유니버스 {len(codes)} · 신호 {tot['n']:,} · 조회실패 {errors} · {time.time()-t0:.0f}s)")
    print(f"헤드라인: 익일 고가 +7% 터치율 | base rate(전 유동일) = {result['base_rate']['touch7_rate']}%  (평균 익일고가 {result['base_rate']['avg_next_high']}%)")
    print("\n[6일 고점 대비 낙폭(dd6)별 급등률]")
    print(f"  {'낙폭밴드':<16}{'n':>8}{'+7%터치':>9}{'+13%터치':>9}{'평균익일고가':>12}")
    for c in result["by_dd6"]:
        print(f"  {c['band']:<16}{c['n']:>8,}{c['touch7_rate']:>8}%{c['touch13_rate']:>8}%{c['avg_next_high']:>11}%")
    print("\n[거래량 급증(vol_surge)별]")
    for c in result["by_vol_surge"]:
        print(f"  {c['band']:<8}{c['n']:>8,}  +7%터치 {c['touch7_rate']}%")
    print("\n[직전 스파이크 유무]")
    for k, v in result["by_prior_spike"].items():
        print(f"  {k:<20}{v['n']:>8,}  +7%터치 {v['touch7_rate']}%")
    print("[MA20 위/아래]")
    for k, v in result["by_ma20"].items():
        print(f"  {k:<20}{v['n']:>8,}  +7%터치 {v['touch7_rate']}%")
    print("\n[깊은낙폭 조합 TOP (n≥30, +7%터치 내림차순)]")
    for c in result["top_combos_deep"][:12]:
        print(f"  {c['combo']:<40}{c['n']:>6,}  +7% {c['touch7_rate']}% · +13% {c['touch13_rate']}% · 익일고가 {c['avg_next_high']}%")

    if a.detail and smoke_rows:
        print(f"\n[스모크 상세 — {a.codes} 신호일(최근 15, look-ahead 안전분)]")
        for r in smoke_rows[-15:]:
            print(f"  {r['date']} dd6={r['dd6']}% dd60={r['dd60']}% val={r['val_eok']}억 "
                  f"volx{r['vol_surge']} ma20up={r['above_ma20']} spike={r['prior_spike']} "
                  f"→ 익일고가 {r['next_high_pct']}% (t7={r['touch7']})")
    print(f"\n출력: {a.out}")


if __name__ == "__main__":
    main()
