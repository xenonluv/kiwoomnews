#!/usr/bin/env python3
"""Versioned suspects ranking policy.

The ranking rules are deliberately isolated from scanning and backtesting so a
stored model version always identifies the exact policy used at signal time.
"""

RANK_POLICY_NAME = "rank4"
RANK_MODEL_VERSION = "rank4-v2"
# 정책 변경이 시작된 승인 기준점. 배포 커밋 자체는 이 상수를 포함하므로 부모 커밋을 기록한다.
RANK_MODEL_SOURCE_COMMIT = "c70b893"
# 7/23 장후 변경이므로 당일 v1 의사결정과 섞지 않고 다음 거래일 신호부터 forward로 인정한다.
RANK_MODEL_EFFECTIVE_FROM = "20260724"
RANK_MODEL_EFFECTIVE_AT = "2026-07-24 09:00:00 KST"


RANK_BUCKET_BASELINES = {
    # rank4-v2 승인 시점의 참고 근거다. 실제 forward calibration과 섞지 않는다.
    # bucket 0 내부는 강한흔들기+조합D를 먼저, Tier1 단독을 다음으로 정렬한다.
    # 교집합은 전 시장 n=2라 100%를 기대확률로 노출하지 않고 관찰 우선순위로만 사용한다.
    0: {"label": "강한흔들기+조합D 우선 · Tier1", "n": 34, "unique_n": 30,
        "touch7_rate": 73.5, "expected_high_pct": 15.15, "avg_return": 1.45,
        "note": "전 시장 현행 bucket0 25/34=73.5%; 강한흔들기+조합D 2/2는 n<10 관찰 우선"},
    1: {"label": "급소+회전150", "n": 5, "unique_n": 3, "touch7_rate": 100.0,
        "expected_high_pct": 27.42, "avg_return": 17.74},
    2: {"label": "저점매집+회전90", "n": 15, "unique_n": 13, "touch7_rate": 86.7,
        "expected_high_pct": 17.00, "avg_return": 9.09},
    3: {"label": "저점매집 기타", "n": 21, "unique_n": 17, "touch7_rate": 81.0,
        "expected_high_pct": 15.64, "avg_return": 6.94},
    4: {"label": "흔들기+조합D+75점", "n": 9, "unique_n": 8, "touch7_rate": 88.9,
        "expected_high_pct": 17.75, "avg_return": 3.30},
    5: {"label": "흔들기+75점", "n": 12, "unique_n": 10, "touch7_rate": 83.3,
        "expected_high_pct": 17.39, "avg_return": 4.12},
    6: {"label": "흔들기+조합D", "n": 15, "unique_n": 12, "touch7_rate": 80.0,
        "expected_high_pct": 16.14, "avg_return": 2.92},
    7: {"label": "75점 기타", "n": 41, "unique_n": 29, "touch7_rate": 78.0,
        "expected_high_pct": 16.69, "avg_return": 3.09},
    8: {"label": "흔들기 기타", "n": 26, "unique_n": 21, "touch7_rate": 76.9,
        "expected_high_pct": 14.59, "avg_return": 1.69},
    9: {"label": "규제해소", "n": 0, "unique_n": 0, "touch7_rate": None,
        "expected_high_pct": None, "avg_return": None, "note": "표본 수집 중"},
    10: {"label": "급소 단독", "n": 12, "unique_n": 10, "touch7_rate": 66.7,
         "expected_high_pct": 15.35, "avg_return": 3.56},
    11: {"label": "기타 suspects", "n": 179, "unique_n": 89, "touch7_rate": 48.6,
         "expected_high_pct": 9.90, "avg_return": 0.69},
}


RANK_BUCKET_PRIORS = {
    0: {"source": "full_market_shakeout_census_202601_202605",
        "strength": "observe",
        "summary": "강한흔들기+조합D를 Tier1 단독보다 먼저 관찰; 교집합 n<10"},
    1: {"source": "chairman_40y_rule", "strength": "strong", "summary": "매수급소와 폭발일 회전율 150% 이상"},
    2: {"source": "chairman_40y_rule", "strength": "strong", "summary": "저점매집과 폭발일 회전율 90% 이상"},
    3: {"source": "chairman_40y_rule", "strength": "strong", "summary": "저점매집 지문"},
    4: {"source": "agreed_rule", "strength": "strong", "summary": "흔들기·조합D·75점 이상"},
    5: {"source": "agreed_rule", "strength": "medium", "summary": "흔들기·75점 이상"},
    6: {"source": "agreed_rule", "strength": "medium", "summary": "흔들기·조합D"},
    7: {"source": "agreed_rule", "strength": "medium", "summary": "75점 이상 기타"},
    8: {"source": "agreed_rule", "strength": "medium", "summary": "흔들기 기타"},
    9: {"source": "agreed_rule", "strength": "observe", "summary": "규제 해소 단독"},
    10: {"source": "chairman_40y_rule", "strength": "medium", "summary": "매수급소 단독"},
    11: {"source": "fallback", "strength": "base", "summary": "기타 suspects"},
}


def policy_metadata():
    return {
        "rank_policy_name": RANK_POLICY_NAME,
        "rank_model_version": RANK_MODEL_VERSION,
        "rank_model_source_commit": RANK_MODEL_SOURCE_COMMIT,
        "rank_model_effective_from": RANK_MODEL_EFFECTIVE_FROM,
        "rank_model_effective_at": RANK_MODEL_EFFECTIVE_AT,
    }


def _as_float(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _fmt_pct(v):
    x = _as_float(v)
    return "결측" if x is None else f"{x:.0f}%"


def _is_combo_d(x):
    return (_as_float(x.get("strength_tier"), -1) or -1) >= 3


def _is_very_good_combo_d(x):
    return (
        x.get("shakeout") is True
        and x.get("very_good") is True
        and x.get("very_good_tier") in ("tier1", "tier2")
        and _is_combo_d(x)
    )


def _very_good_sort_rank(x):
    """bucket 0 내부 우선순위.

    강한흔들기+조합D는 소표본 관찰 우선순위이며 기대확률 하드게이트가 아니다.
    Tier2 단독은 bucket 0에 들어오지 않지만 방어적으로 마지막 값으로 남긴다.
    """
    tier = x.get("very_good_tier")
    if _is_very_good_combo_d(x):
        return 0 if tier == "tier1" else 1
    if tier == "tier1":
        return 2
    return 9


def rank_shadow_buckets(x):
    score = _as_float(x.get("suspicion_score"), 0) or 0
    pt = _as_float(x.get("peak_turnover_pct"))
    peak_dd = _as_float(x.get("peak_dd_pct"))
    out = []
    if x.get("low_accum") and pt is not None and pt >= 150:
        out.append("S1")
    if x.get("alert_now") == "경고" and score >= 70:
        out.append("S2")
    if x.get("shakeout") and score >= 75 and peak_dd is not None and peak_dd <= -30:
        out.append("S3")
    if x.get("shakeout") and _is_combo_d(x) and score >= 80:
        out.append("S4")
    if x.get("geupso") and pt is not None and pt >= 90:
        out.append("S5")
    return out


def rank_bucket_info(x):
    """Return the versioned rank4-v2 bucket and evidence snapshot."""
    score = _as_float(x.get("suspicion_score"), 0) or 0
    pt = _as_float(x.get("peak_turnover_pct"))
    bucket, reason = 11, "기타 suspects → bucket 11"
    if _is_very_good_combo_d(x):
        vt = x.get("very_good_tier")
        label = "Tier1" if vt == "tier1" else "Tier2(과낙)"
        bucket, reason = 0, (
            f"강한흔들기 {label}+조합D"
            f"(dd6 {_fmt_pct(x.get('dd6_pct'))}) → bucket 0 관찰우선"
        )
    elif x.get("very_good") is True and x.get("very_good_tier") == "tier1":
        bucket, reason = 0, (
            f"매우좋음 Tier1 단독(dd6 {_fmt_pct(x.get('dd6_pct'))}) → bucket 0"
        )
    elif x.get("geupso") and pt is not None and pt >= 150:
        bucket, reason = 1, f"급소+폭발회전 {_fmt_pct(pt)} → bucket 1"
    elif x.get("low_accum") and pt is not None and pt >= 90:
        bucket, reason = 2, f"저점매집+폭발회전 {_fmt_pct(pt)} → bucket 2"
    elif x.get("low_accum"):
        bucket, reason = 3, f"저점매집+폭발회전 {_fmt_pct(pt)} → bucket 3"
    elif x.get("shakeout") and _is_combo_d(x) and score >= 75:
        bucket, reason = 4, f"흔들기+조합D+{score:.0f}점 → bucket 4"
    elif x.get("shakeout") and score >= 75:
        bucket, reason = 5, f"흔들기+{score:.0f}점 → bucket 5"
    elif x.get("shakeout") and _is_combo_d(x):
        bucket, reason = 6, "흔들기+조합D → bucket 6"
    elif score >= 75:
        bucket, reason = 7, f"{score:.0f}점 기타 → bucket 7"
    elif x.get("shakeout"):
        bucket, reason = 8, "흔들기 기타 → bucket 8"
    elif x.get("alert_release") or x.get("alert_risk_released"):
        bucket, reason = 9, "규제해소 단독 → bucket 9"
    elif x.get("geupso"):
        bucket, reason = 10, f"급소 단독(폭발회전 {_fmt_pct(pt)}) → bucket 10"

    snap = dict(RANK_BUCKET_BASELINES.get(bucket, {}))
    snap["bucket"] = bucket
    snap["basis"] = "rank4-v2 2026-07-23 승인 스냅샷"
    snap["population"] = "bucket_specific_retro_reference"
    prior = dict(RANK_BUCKET_PRIORS.get(bucket, {}))
    return {
        "rank_bucket": bucket,
        "rank_reason": reason,
        "shadow_bucket": rank_shadow_buckets(x),
        "expected_touch7_rate": snap.get("touch7_rate"),
        "expected_high_pct": snap.get("expected_high_pct"),
        "rank_bucket_stats_snapshot": snap,
        "rank_prior": prior,
        **policy_metadata(),
    }


def apply_rank_metadata(x):
    x.update(rank_bucket_info(x))
    return x


def rank_sort_key(x):
    if x.get("rank_bucket") is None:
        apply_rank_metadata(x)
    score = _as_float(x.get("suspicion_score"), 0) or 0
    fade = _as_float(x.get("fade_pct"), 0) or 0
    peak_turnover = _as_float(x.get("peak_turnover_pct"))
    if peak_turnover is None:
        peak_turnover = _as_float(x.get("turnover_pct"), 0) or 0
    turnover_2d = _as_float(x.get("turnover_2d_pct"), 0) or 0
    return (
        x.get("rank_bucket", 99),
        _very_good_sort_rank(x),
        -score,
        -fade if x.get("shakeout") else 0,
        -peak_turnover,
        -turnover_2d,
        x.get("name") or x.get("code") or "",
    )


# Compatibility name used by the existing radar code and tests.
_rank_sort_key = rank_sort_key
