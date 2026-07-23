#!/usr/bin/env python3
"""Versioned suspects ranking policy.

The ranking rules are deliberately isolated from scanning and backtesting so a
stored model version always identifies the exact policy used at signal time.
"""

RANK_POLICY_NAME = "rank4"
RANK_MODEL_VERSION = "rank4-v3"
# 정책 변경이 시작된 승인 기준점. 배포 커밋 자체는 이 상수를 포함하므로 부모 커밋을 기록한다.
RANK_MODEL_SOURCE_COMMIT = "60ecdf5"
# 7/23 장후 v2 발효 전에 교체하므로 당일 v1 기록은 그대로 두고 다음 거래일부터 v3로 인정한다.
RANK_MODEL_EFFECTIVE_FROM = "20260724"
RANK_MODEL_EFFECTIVE_AT = "2026-07-24 09:00:00 KST"


RANK_BUCKET_BASELINES = {
    # rank4-v3 승인 시점의 최근 live final 참고 근거다. 장기 소급·새 forward와 섞지 않는다.
    # 1~5월 current-retro는 같은 조건의 +7이 84/169(49.7%)여서 최근 국면 prior로만 사용한다.
    0: {"label": "조합D 단독+75점 최우선", "n": 15, "unique_n": 14,
        "touch7_rate": 86.7, "expected_high_pct": 17.59, "avg_return": 4.15,
        "note": "최근 live final +7 13/15·+13 11/15; rank4-v1 EOD +7 6/7. 장기 고정확률 아님"},
    1: {"label": "급소+회전150", "n": 5, "unique_n": 3, "touch7_rate": 100.0,
        "expected_high_pct": 27.42, "avg_return": 17.74},
    2: {"label": "저점매집+회전90", "n": 15, "unique_n": 13, "touch7_rate": 86.7,
        "expected_high_pct": 17.00, "avg_return": 9.09},
    3: {"label": "저점매집 기타", "n": 21, "unique_n": 17, "touch7_rate": 81.0,
        "expected_high_pct": 15.64, "avg_return": 6.94},
    4: {"label": "강한흔들기 교집합·Tier1 관찰", "n": 2, "unique_n": 2,
        "touch7_rate": None, "expected_high_pct": None, "avg_return": None,
        "note": "live final 2건(CS 교집합·금호전기 Tier1 단독). 2/2를 기대확률로 노출하지 않음"},
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
    0: {"source": "live_final_and_rank4_v1_eod_20260723",
        "strength": "medium",
        "summary": "최근 국면 조합D 단독+75점 final 13/15·EOD 6/7; 장기 소급은 별도"},
    1: {"source": "chairman_40y_rule", "strength": "strong", "summary": "매수급소와 폭발일 회전율 150% 이상"},
    2: {"source": "chairman_40y_rule", "strength": "strong", "summary": "저점매집과 폭발일 회전율 90% 이상"},
    3: {"source": "chairman_40y_rule", "strength": "strong", "summary": "저점매집 지문"},
    4: {"source": "live_final_20260723", "strength": "observe",
        "summary": "강한흔들기 교집합·Tier1은 live n=2 소표본 관찰"},
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


def _is_combo_d_only(x):
    """명시적으로 강한흔들기가 아닌 조합D.

    very_good 결측을 False로 추정해 최상단에 올리지 않는다.
    """
    return (
        x.get("shakeout") is True
        and x.get("very_good") is False
        and _is_combo_d(x)
    )


def _very_good_sort_rank(x):
    """강한흔들기 관찰 bucket 내부 우선순위.

    강한흔들기+조합D는 소표본 관찰 우선순위이며 기대확률 하드게이트가 아니다.
    Tier2 단독은 관찰 bucket에 들어오지 않지만 방어적으로 마지막 값으로 남긴다.
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
    """Return the versioned rank4-v3 bucket and evidence snapshot."""
    score = _as_float(x.get("suspicion_score"), 0) or 0
    pt = _as_float(x.get("peak_turnover_pct"))
    bucket, reason = 11, "기타 suspects → bucket 11"
    if _is_very_good_combo_d(x):
        vt = x.get("very_good_tier")
        label = "Tier1" if vt == "tier1" else "Tier2(과낙)"
        bucket, reason = 4, (
            f"강한흔들기 {label}+조합D"
            f"(dd6 {_fmt_pct(x.get('dd6_pct'))}) → bucket 4 표본관찰"
        )
    elif x.get("very_good") is True and x.get("very_good_tier") == "tier1":
        bucket, reason = 4, (
            f"매우좋음 Tier1 단독(dd6 {_fmt_pct(x.get('dd6_pct'))}) → bucket 4 표본관찰"
        )
    elif x.get("geupso") and pt is not None and pt >= 150:
        bucket, reason = 1, f"급소+폭발회전 {_fmt_pct(pt)} → bucket 1"
    elif x.get("low_accum") and pt is not None and pt >= 90:
        bucket, reason = 2, f"저점매집+폭발회전 {_fmt_pct(pt)} → bucket 2"
    elif x.get("low_accum"):
        bucket, reason = 3, f"저점매집+폭발회전 {_fmt_pct(pt)} → bucket 3"
    elif _is_combo_d_only(x) and score >= 75:
        bucket, reason = 0, f"조합D 단독+{score:.0f}점(최근 국면 고가강) → bucket 0"
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
    snap["basis"] = "rank4-v3 2026-07-23 최근 국면 승인 스냅샷"
    # 신호 당시 함께 고정되는 prior 스냅샷이다. 이후 소급/forward 실측과 섞지 않는다.
    snap["population"] = "signal_time_prior_reference"
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
