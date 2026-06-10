#!/usr/bin/env python3
"""팀원1 — forecast 후보군 수집 + 장중 시계열 누적.

레이더(/api/radar)의 수상 종목을 우선 사용하되, forecast가 누락 종목을 놓치지 않도록
상승률 상위 종목을 보강한다. 실행마다 종목별 장중 이력
(등장횟수·확률추이·생존)을 analyzer/state/intraday_YYYYMMDD.json 에 누적한다.
"""
import os
import sys
import json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from net import get_bytes  # noqa: E402  (요청간격+백오프)
from team1_collect import top_ranking  # noqa: E402

KST = timezone(timedelta(hours=9))
API = os.environ.get("RADAR_API", "https://stocknews-cyan.vercel.app/api/radar")
STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
SUPPLEMENT_N = int(os.environ.get("FORECAST_SUPPLEMENT_N", "20"))


def fetch_universe():
    """레이더 API(수상 종목) + 랭킹 보강 → 종목군 [{code, name, tier, prob, day_change}]."""
    out = []
    seen = set()
    try:  # 레이더 API 장애 시에도 랭킹 보강만으로 진행
        data = json.loads(get_bytes(API))
        for s in data.get("suspects", []):
            code = s.get("code")
            if not code or code in seen:
                continue
            seen.add(code)
            out.append({
                "code": code,
                "name": s.get("name"),
                "tier": "radar",
                "position": s.get("sector"),
                "prob": s.get("suspicion_score"),
                "day_change": s.get("change_pct"),
                "source": "radar",
            })
    except Exception as e:
        print(f"[warn] radar API 조회 실패({e}) — 랭킹 보강만 사용", file=sys.stderr)

    for sort_key in ("상승률",):
        for market in ("KOSPI", "KOSDAQ"):
            try:
                ranked = top_ranking(sort_key, market, SUPPLEMENT_N)
            except Exception:
                continue
            for r in ranked:
                code = r.get("code")
                if not code or code in seen:
                    continue
                seen.add(code)
                out.append({
                    "code": code,
                    "name": r.get("name"),
                    "tier": "supplement",
                    "position": sort_key,
                    "prob": None,
                    "day_change": None,
                    "source": f"ranking:{sort_key}:{market}",
                })
    return [x for x in out if x["code"] and x["name"]]


def accumulate(universe, now=None, write=True):
    """장중 시계열 누적 → state 파일. 종목별 등장횟수·확률추이·최근tier 갱신."""
    now = now or datetime.now(KST)
    path = os.path.join(STATE_DIR, f"intraday_{now.strftime('%Y%m%d')}.json")
    state = {}
    if os.path.exists(path):
        state = json.load(open(path, encoding="utf-8"))
    if not write:
        return state, path

    os.makedirs(STATE_DIR, exist_ok=True)
    hhmm = now.strftime("%H:%M")
    for u in universe:
        c = u["code"]
        rec = state.get(c, {"name": u["name"], "appearances": 0,
                            "first_seen": hhmm, "prob_series": [], "tier_series": []})
        rec["name"] = u["name"]
        rec["appearances"] += 1
        rec["last_seen"] = hhmm
        rec["last_tier"] = u["tier"]
        rec["last_position"] = u["position"]
        rec["last_prob"] = u["prob"]
        rec["last_day_change"] = u["day_change"]
        rec["prob_series"].append({"t": hhmm, "p": u["prob"]})
        rec["tier_series"].append(u["tier"])
        state[c] = rec

    json.dump(state, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return state, path


def main():
    uni = fetch_universe()
    state, path = accumulate(uni)
    print(f"수집 {len(uni)}종목 · 누적 {len(state)}종목 → {path}")
    for u in uni:
        print(f"  - {u['name']}({u['code']}) {u['tier']} {u['prob']}% {u['position']} 등락 {u['day_change']}")


if __name__ == "__main__":
    main()
