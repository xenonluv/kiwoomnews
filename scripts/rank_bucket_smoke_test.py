#!/usr/bin/env python3
"""정렬4 rank_bucket 최소 회귀 테스트.

네트워크 없이 radar.rank_bucket_info의 핵심 우선순위만 확인한다.
"""
from radar import rank_bucket_info, apply_rank_metadata, _rank_sort_key


CASES = [
    ({"very_good_tier": "tier1", "shakeout": True, "suspicion_score": 70}, 0),
    # 회장님 결정 2026-07-10(a안): Tier2(과낙)도 bucket 0 — 다른 강조건(급소150 등)이 겹쳐도 0 우선.
    ({"very_good_tier": "tier2", "shakeout": True, "suspicion_score": 70}, 0),
    ({"very_good_tier": "tier2", "shakeout": True, "geupso": True,
      "peak_turnover_pct": 200, "suspicion_score": 60}, 0),
    ({"geupso": True, "peak_turnover_pct": 150, "suspicion_score": 60}, 1),
    ({"low_accum": True, "peak_turnover_pct": 90, "suspicion_score": 60}, 2),
    ({"low_accum": True, "peak_turnover_pct": 20, "suspicion_score": 60}, 3),
    ({"shakeout": True, "strength_tier": 3, "suspicion_score": 75}, 4),
    ({"shakeout": True, "strength_tier": 2, "suspicion_score": 75}, 5),
    ({"shakeout": True, "strength_tier": 4, "suspicion_score": 60}, 6),
    ({"suspicion_score": 75}, 7),
    ({"shakeout": True, "suspicion_score": 60}, 8),
    ({"alert_risk_released": True, "suspicion_score": 60}, 9),
    ({"geupso": True, "peak_turnover_pct": 100, "suspicion_score": 60}, 10),
    # 후보는 별도 승격키가 없고 자기 흔들기 bucket으로 들어간다.
    ({"very_good_candidate": True, "shakeout": True, "suspicion_score": 60}, 8),
]


def main():
    for sample, expected in CASES:
        actual = rank_bucket_info(sample)["rank_bucket"]
        if actual != expected:
            raise AssertionError(f"{sample} => bucket {actual}, expected {expected}")

    # bucket 0 내부 정렬: Tier1(스윗)이 Tier2(과낙)보다 위 — 점수가 낮아도 Tier1 우선.
    t1 = apply_rank_metadata({"very_good_tier": "tier1", "shakeout": True,
                              "suspicion_score": 60, "name": "T1"})
    t2 = apply_rank_metadata({"very_good_tier": "tier2", "shakeout": True,
                              "suspicion_score": 90, "name": "T2"})
    ordered = sorted([t2, t1], key=_rank_sort_key)
    if [s["name"] for s in ordered] != ["T1", "T2"]:
        raise AssertionError(f"bucket0 내부 Tier1 우선 실패: {[s['name'] for s in ordered]}")

    print(f"rank_bucket smoke ok {len(CASES)} + tier-order")


if __name__ == "__main__":
    main()

