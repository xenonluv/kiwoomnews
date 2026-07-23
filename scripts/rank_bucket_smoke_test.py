#!/usr/bin/env python3
"""정렬4 rank_bucket 최소 회귀 테스트.

네트워크 없이 rank_policy SSOT의 핵심 우선순위와 버전을 확인한다.
"""
from rank_policy import (
    RANK_MODEL_EFFECTIVE_FROM,
    RANK_MODEL_VERSION,
    RANK_POLICY_NAME,
    apply_rank_metadata,
    rank_bucket_info,
    rank_sort_key,
)


CASES = [
    ({"very_good": True, "very_good_tier": "tier1", "shakeout": True,
      "suspicion_score": 70}, 0),
    # rank4-v2: 강한흔들기+조합D는 Tier1/Tier2 모두 bucket 0 관찰우선.
    ({"very_good": True, "very_good_tier": "tier1", "shakeout": True,
      "strength_tier": 3, "suspicion_score": 70}, 0),
    ({"very_good": True, "very_good_tier": "tier2", "shakeout": True,
      "strength_tier": 3, "suspicion_score": 70}, 0),
    # 불일치 저장값은 tier 문자열만 믿어 승격하지 않는다.
    ({"very_good": False, "very_good_tier": "tier1", "shakeout": True,
      "strength_tier": 3, "suspicion_score": 70}, 6),
    # Tier2 단독은 더 이상 bucket 0이 아니며 자기 일반 흔들기 조건으로 평가한다.
    ({"very_good": True, "very_good_tier": "tier2", "shakeout": True,
      "suspicion_score": 70}, 8),
    ({"very_good": True, "very_good_tier": "tier2", "shakeout": True,
      "geupso": True, "peak_turnover_pct": 200, "suspicion_score": 60}, 1),
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
    if (RANK_POLICY_NAME, RANK_MODEL_VERSION, RANK_MODEL_EFFECTIVE_FROM) != (
            "rank4", "rank4-v2", "20260724"):
        raise AssertionError("rank4-v2 정책 버전/발효일 변경 감지")
    for sample, expected in CASES:
        actual = rank_bucket_info(sample)["rank_bucket"]
        if actual != expected:
            raise AssertionError(f"{sample} => bucket {actual}, expected {expected}")

    # bucket 0 내부: Tier1+조합D → Tier2+조합D → Tier1 단독.
    t1_combo = apply_rank_metadata({
        "very_good": True, "very_good_tier": "tier1", "shakeout": True,
        "strength_tier": 3, "suspicion_score": 60, "name": "T1D",
    })
    t2_combo = apply_rank_metadata({
        "very_good": True, "very_good_tier": "tier2", "shakeout": True,
        "strength_tier": 3, "suspicion_score": 90, "name": "T2D",
    })
    t1_only = apply_rank_metadata({
        "very_good": True, "very_good_tier": "tier1", "shakeout": True,
        "strength_tier": 2, "suspicion_score": 95, "name": "T1",
    })
    ordered = sorted([t1_only, t2_combo, t1_combo], key=rank_sort_key)
    if [s["name"] for s in ordered] != ["T1D", "T2D", "T1"]:
        raise AssertionError(
            f"bucket0 내부 교집합 우선 실패: {[s['name'] for s in ordered]}"
        )
    snapshot = t1_combo["rank_bucket_stats_snapshot"]
    if (snapshot.get("n"), snapshot.get("touch7_rate")) != (34, 73.5):
        raise AssertionError(f"rank4-v2 bucket0 근거 스냅샷 불일치: {snapshot}")

    print(f"rank_bucket smoke ok {len(CASES)} + intersection-order")


if __name__ == "__main__":
    main()
