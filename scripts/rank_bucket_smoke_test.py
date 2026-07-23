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
    # rank4-v3 최상단: 명시적인 조합D 단독 + 75점 이상만 승격한다.
    ({"very_good": False, "shakeout": True, "strength_tier": 3,
      "suspicion_score": 80}, 0),
    ({"very_good": False, "shakeout": True, "strength_tier": 4,
      "suspicion_score": 75}, 0),
    ({"very_good": False, "shakeout": True, "strength_tier": 3,
      "suspicion_score": 74.99}, 6),
    # 강한흔들기 교집합·Tier1 단독은 bucket 4 표본 관찰군이다.
    ({"very_good": True, "very_good_tier": "tier1", "shakeout": True,
      "suspicion_score": 70}, 4),
    ({"very_good": True, "very_good_tier": "tier1", "shakeout": True,
      "strength_tier": 3, "suspicion_score": 70}, 4),
    ({"very_good": True, "very_good_tier": "tier2", "shakeout": True,
      "strength_tier": 3, "suspicion_score": 70}, 4),
    # 불일치·결측 저장값은 최상단으로 추정하지 않는다.
    ({"very_good": False, "very_good_tier": "tier1", "shakeout": True,
      "strength_tier": 3, "suspicion_score": 70}, 6),
    ({"shakeout": True, "strength_tier": 3, "suspicion_score": 90}, 5),
    ({"very_good": False, "shakeout": True, "suspicion_score": 90}, 5),
    # Tier2 단독은 자기 일반 흔들기 조건으로 평가한다.
    ({"very_good": True, "very_good_tier": "tier2", "shakeout": True,
      "suspicion_score": 70}, 8),
    # 기존 branch 위치를 유지하므로 bucket 1~3 중복 조건은 그대로 선행한다.
    ({"very_good": False, "shakeout": True, "strength_tier": 3,
      "geupso": True, "peak_turnover_pct": 200, "suspicion_score": 80}, 1),
    ({"very_good": False, "shakeout": True, "strength_tier": 3,
      "low_accum": True, "peak_turnover_pct": 90, "suspicion_score": 80}, 2),
    ({"geupso": True, "peak_turnover_pct": 150, "suspicion_score": 60}, 1),
    ({"low_accum": True, "peak_turnover_pct": 90, "suspicion_score": 60}, 2),
    ({"low_accum": True, "peak_turnover_pct": 20, "suspicion_score": 60}, 3),
    ({"very_good": False, "shakeout": True, "strength_tier": 2,
      "suspicion_score": 75}, 5),
    ({"very_good": False, "shakeout": True, "strength_tier": 4,
      "suspicion_score": 60}, 6),
    ({"suspicion_score": 75}, 7),
    ({"shakeout": True, "suspicion_score": 60}, 8),
    ({"alert_risk_released": True, "suspicion_score": 60}, 9),
    ({"geupso": True, "peak_turnover_pct": 100, "suspicion_score": 60}, 10),
    # 후보는 별도 승격키가 없고 자기 흔들기 bucket으로 들어간다.
    ({"very_good_candidate": True, "shakeout": True, "suspicion_score": 60}, 8),
]


def main():
    if (RANK_POLICY_NAME, RANK_MODEL_VERSION, RANK_MODEL_EFFECTIVE_FROM) != (
            "rank4", "rank4-v3", "20260724"):
        raise AssertionError("rank4-v3 정책 버전/발효일 변경 감지")
    for sample, expected in CASES:
        actual = rank_bucket_info(sample)["rank_bucket"]
        if actual != expected:
            raise AssertionError(f"{sample} => bucket {actual}, expected {expected}")

    # 전체 정렬: 조합D 단독+75점(점수 내림차순) → 기존 bucket 1~3
    # → 강한흔들기 관찰군 → 75점 미만 조합D.
    combo83 = apply_rank_metadata({
        "very_good": False, "shakeout": True,
        "strength_tier": 3, "suspicion_score": 83, "name": "D83",
    })
    combo76 = apply_rank_metadata({
        "very_good": False, "shakeout": True,
        "strength_tier": 4, "suspicion_score": 76, "name": "D76",
    })
    geupso = apply_rank_metadata({
        "geupso": True, "peak_turnover_pct": 170,
        "suspicion_score": 90, "name": "G",
    })
    low = apply_rank_metadata({
        "low_accum": True, "peak_turnover_pct": 100,
        "suspicion_score": 90, "name": "L",
    })
    strong = apply_rank_metadata({
        "very_good": True, "very_good_tier": "tier1", "shakeout": True,
        "strength_tier": 3, "suspicion_score": 95, "name": "VG",
    })
    combo74 = apply_rank_metadata({
        "very_good": False, "shakeout": True,
        "strength_tier": 3, "suspicion_score": 74, "name": "D74",
    })
    ordered = sorted(
        [strong, combo74, low, combo76, geupso, combo83],
        key=rank_sort_key,
    )
    expected_order = ["D83", "D76", "G", "L", "VG", "D74"]
    if [s["name"] for s in ordered] != expected_order:
        raise AssertionError(
            f"rank4-v3 우선순위 실패: {[s['name'] for s in ordered]}"
        )
    snapshot = combo83["rank_bucket_stats_snapshot"]
    if (snapshot.get("n"), snapshot.get("touch7_rate")) != (15, 86.7):
        raise AssertionError(f"rank4-v3 bucket0 근거 스냅샷 불일치: {snapshot}")
    if snapshot.get("population") != "signal_time_prior_reference":
        raise AssertionError(f"prior/retro/forward 구분값 불일치: {snapshot}")
    if strong["rank_bucket_stats_snapshot"].get("touch7_rate") is not None:
        raise AssertionError("소표본 bucket4가 2/2 적중률을 기대확률로 노출함")

    print(f"rank_bucket smoke ok {len(CASES)} + v3-order")


if __name__ == "__main__":
    main()
