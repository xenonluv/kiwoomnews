#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-01~05 흔들기 소급 전수 재스캔 (연구용·읽기전용, 운영 파일 불변).

방법론 = shakeout_backfill.scan_code 1:1 재사용(정의·게이트·티어 동일).
차이 = 유니버스만 '레지스트리∪history'가 아니라 '네이버 전 상장종목'으로 확장.

API 안전:
  - 키움 하드캡 KIWOOM_CALL_CAP(기본 7000). 초과 즉시 중단·체크포인트 보존.
  - 연속 오류 12회 → 중단(차단/장애 의심 시 폭주 방지).
  - 클라이언트 자체 MIN_GAP=0.1 위에 EXTRA_SLEEP 추가.
  - 체크포인트(jsonl)로 재실행 시 이미 조회한 종목 재호출 0.
"""
import json, os, sys, time
sys.path.insert(0, "/Users/jinjin/kiwoomnews/scripts")
import kiwoom_client as kw
import float_ratio
import shakeout_backfill as sb
from net import get_bytes

OUT = os.path.dirname(os.path.abspath(__file__)) + "/janmay"
os.makedirs(OUT, exist_ok=True)
UNIVERSE_F = OUT + "/universe.json"
PASS1_F = OUT + "/pass1.jsonl"
RESULT_F = OUT + "/result.json"
SAMPLES_F = OUT + "/samples.json"

KIWOOM_CALL_CAP = 7000
EXTRA_SLEEP = 0.10
CONSEC_ERR_LIMIT = 12
DAYS = 240                      # 2025-08~현재 — 60일 선행창+1~5월+검증분
WIN_LO, WIN_HI = "20260102", "20260531"   # 연구 창
HIGH_PCT_MIN = 20.0             # 프리필터(가격 게이트만, scan_code가 최종 판정)
FADE_MIN = 15.0

calls = {"kiwoom": 0}
_orig_call = kw._call
def _counted(*a, **k):
    calls["kiwoom"] += 1
    if calls["kiwoom"] > KIWOOM_CALL_CAP:
        raise RuntimeError("KIWOOM_CALL_CAP exceeded — aborting for API safety")
    time.sleep(EXTRA_SLEEP)
    return _orig_call(*a, **k)
kw._call = _counted

def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

def fetch_universe():
    if os.path.exists(UNIVERSE_F):
        u = json.load(open(UNIVERSE_F))
        log(f"universe 캐시 재사용: {len(u)}종목")
        return u
    codes = {}
    for mkt in ("KOSPI", "KOSDAQ"):
        page = 1
        while True:
            raw = get_bytes(
                f"https://m.stock.naver.com/api/stocks/marketValue/{mkt}?page={page}&pageSize=100",
                {"User-Agent": "Mozilla/5.0"})
            d = json.loads(raw)
            st = d.get("stocks") or []
            for s in st:
                c = str(s.get("itemCode") or "")
                nm = s.get("stockName") or ""
                if len(c) == 6 and c.isdigit() and "스팩" not in nm:
                    codes[c] = nm
            if len(st) < 100:
                break
            page += 1
        log(f"{mkt} 완료 — 누적 {len(codes)}")
    u = sorted(codes)
    json.dump(u, open(UNIVERSE_F, "w"))
    return u

def pass1(universe):
    done = set()
    if os.path.exists(PASS1_F):
        for line in open(PASS1_F):
            try: done.add(json.loads(line)["code"])
            except Exception: pass
        log(f"pass1 체크포인트: {len(done)}종목 완료분 스킵")
    fh = open(PASS1_F, "a")
    consec = 0
    t0 = time.time()
    for i, code in enumerate(universe):
        if code in done:
            continue
        row = {"code": code, "cand": 0}
        try:
            bars = kw.daily_prices(code, days=DAYS, market="J")
            consec = 0
            bars = [b for b in bars if b.get("close") and b.get("high")]
            closes = [b["close"] for b in bars]
            for j in range(1, len(bars)):
                d = bars[j]["date"]
                if not (WIN_LO <= d <= WIN_HI):
                    continue
                pc = closes[j-1]
                if not pc: continue
                hp = (bars[j]["high"]/pc - 1) * 100
                cp = (closes[j]/pc - 1) * 100
                if hp >= HIGH_PCT_MIN and (hp - cp) >= FADE_MIN:
                    row["cand"] += 1
        except RuntimeError:
            fh.close(); raise
        except Exception as e:
            row["err"] = str(e)[:120]
            consec += 1
            if consec >= CONSEC_ERR_LIMIT:
                fh.write(json.dumps(row) + "\n"); fh.close()
                raise RuntimeError(f"연속 오류 {consec}회 — 중단(마지막 {code}: {e})")
        fh.write(json.dumps(row) + "\n")
        if (i+1) % 250 == 0:
            fh.flush()
            el = time.time()-t0
            log(f"pass1 {i+1}/{len(universe)} — kiwoom콜 {calls['kiwoom']} — {el/60:.1f}분")
    fh.close()

def candidates_from_pass1():
    out = []
    for line in open(PASS1_F):
        try:
            r = json.loads(line)
            if r.get("cand", 0) > 0:
                out.append(r["code"])
        except Exception: pass
    return sorted(set(out))

def main():
    log("=== 1~5월 흔들기 소급 전수 재스캔 시작 ===")
    universe = fetch_universe()
    log(f"유니버스 {len(universe)}종목 — pass1(J일봉 1콜/종목) 시작")
    pass1(universe)
    cands = candidates_from_pass1()
    log(f"pass1 완료 — 가격게이트 후보 {len(cands)}종목, kiwoom콜 {calls['kiwoom']}")

    # pass2: 기존 scan_code 1:1 재사용 (jmoney_un 2콜 + float)
    today = time.strftime("%Y%m%d")
    float_cache = {}
    samples = []
    for i, code in enumerate(cands):
        try:
            samples.extend(sb.scan_code(code, DAYS, float_cache, today))
        except RuntimeError:
            raise
        except Exception as e:
            log(f"pass2 {code} 실패(격리): {e}")
        if (i+1) % 50 == 0:
            log(f"pass2 {i+1}/{len(cands)} — kiwoom콜 {calls['kiwoom']}")
    json.dump(samples, open(SAMPLES_F, "w"), ensure_ascii=False)
    log(f"pass2 완료 — 표본 {len(samples)}건, kiwoom콜 {calls['kiwoom']}")

    # 집계
    def strongf(s):
        if s.get("very_good") is True: return True
        t = s.get("strength_tier")
        try: return t is not None and float(t) >= 3
        except Exception: return False
    from collections import defaultdict
    mon = defaultdict(lambda: {"all":0,"strong":0,"days":set(),"sdays":set()})
    for s in samples:
        d = str(s.get("signal_date") or s.get("date") or "")
        if len(d) != 8: continue
        m = d[:6]
        mon[m]["all"] += 1; mon[m]["days"].add(d)
        if strongf(s):
            mon[m]["strong"] += 1; mon[m]["sdays"].add(d)
    result = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "universe_n": len(universe), "pass1_candidates": len(cands),
        "kiwoom_calls": calls["kiwoom"], "sample_n": len(samples),
        "monthly": {m: {"all": v["all"], "strong": v["strong"],
                        "days": len(v["days"]), "sdays": len(v["sdays"]),
                        "day_list": sorted(v["days"])}
                    for m, v in sorted(mon.items())},
        "note": "정의=shakeout_backfill.scan_code 1:1(가격J/거래량UN·유통비율 현재값). "
                "시장경보 소급 불가로 소폭 과대집계 가능. 상폐종목 누락(생존편향).",
    }
    json.dump(result, open(RESULT_F, "w"), ensure_ascii=False, indent=1)
    log("월별 집계:")
    for m, v in sorted(result["monthly"].items()):
        log(f"  {m[:4]}.{m[4:]}: 전체 {v['all']}건/{v['days']}일, 강한 {v['strong']}건/{v['sdays']}일")
    log(f"=== 완료 — 총 kiwoom콜 {calls['kiwoom']} ===")

if __name__ == "__main__":
    main()
