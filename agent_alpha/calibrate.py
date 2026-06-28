"""전진검증 채점 — 라벨된 forward 표본으로 (a)정량 가설밴드 (b)LLM confidence Brier 산출 → calibration.json.
정량밴드 1순위축 = 2일 유통회전율. min_n 게이트(셀 표본<CALIB_MIN_N 이면 '관찰중'). 전 셀 보고(체리피킹 금지).
"""
import json
import os
import config


def _load_labeled():
    rows = []
    try:
        with open(config.FORWARD_JSONL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r.get("labeled") and r.get("hit") is not None and r.get("next_return_pct") is not None:
                    rows.append(r)
    except Exception:
        pass
    return rows


def _stat(rows):
    n = len(rows)
    if n == 0:
        return {"n": 0, "hit_rate": None, "avg_return": None, "avg_high": None, "touch7_rate": None,
                "valid": False, "status": "관찰중"}
    hit = sum(1 for r in rows if r.get("hit"))
    avg = sum(r["next_return_pct"] for r in rows) / n
    # 종가 베팅 가정 시 익일 '고가' 도달폭 — avg_high=평균 익일고가 등락(종가 대비), touch7=익일 +7% 고가 터치율(익절 도달).
    highs = [r["next_high_pct"] for r in rows if r.get("next_high_pct") is not None]
    avg_high = round(sum(highs) / len(highs), 2) if highs else None
    touch7 = round(sum(1 for h in highs if h >= 7) / len(highs) * 100, 1) if highs else None
    valid = n >= config.CALIB_MIN_N
    return {"n": n, "hit_rate": round(hit / n * 100, 1), "avg_return": round(avg, 2),
            "avg_high": avg_high, "touch7_rate": touch7,
            "valid": valid, "status": "입증가능" if valid else "관찰중"}


def _band(v, bands):
    if v is None:
        return None
    for lo, hi in bands:
        if lo <= v < hi:
            return f"{int(lo)}~{'inf' if hi > 1e8 else int(hi)}"
    return None


def _spark_band(c):
    if c is None:
        return None
    if c == 0:
        return "0"
    if c >= config.SPARK_MIN:
        return f">={config.SPARK_MIN}"
    return f"1~{config.SPARK_MIN - 1}"


def run():
    config.ensure_dirs()
    rows = _load_labeled()
    eumbong = [r for r in rows if r.get("is_eumbong")]
    out = {
        "generated_at": config.now_iso(),
        "total_labeled": len(rows),
        "overall": _stat(rows),
        "eumbong_overall": _stat(eumbong),
        "by_turnover2d_eumbong": {},     # 1순위축: 2일회전율(음봉)
        "by_spark_eumbong_hi_turnover": {},  # 음봉 + 고회전(200%+) 중 스파크별
        "by_close_strength_eumbong": {},
        "by_spark_count": {},            # 14:30 스파크 횟수 단독(측정행 전체·음봉/회전 무관) — 회장님 핵심신호 직접검증
        "by_hidden_foreign": {},         # 키움 속 숨은 외국인 매집(해당/미해당) — frgn+>0·외국계≈0·키움≥30%
        "by_combined_score": {},         # 합산 종합점수(스파크 횟수 + 외인매집 강도) 밴드 — /alpha 정렬 순위 자체의 적중률
        "cells": [],                     # turnover2d × spark × close_strength × 음봉 (min_n 게이트)
        "llm": None,
        "min_n": config.CALIB_MIN_N,
        "note": "전진검증 — 표본 부족 셀은 관찰중. 스파크는 거래일 수집분만(과거 불가). 매수추천 아님.",
    }
    for lo, hi in config.TURNOVER_2D_BANDS:
        key = f"{int(lo)}~{'inf' if hi > 1e8 else int(hi)}"
        out["by_turnover2d_eumbong"][key] = _stat([r for r in eumbong if _band(r.get("turnover_2d_pct"), config.TURNOVER_2D_BANDS) == key])
    # ⚠ 스파크축은 '측정된'(spark_source != none) 행만 — 미측정(분봉 결측·백필)을 '0'에 섞으면 0버킷 오염.
    def _measured(r):
        return r.get("spark_source") not in (None, "none")
    hi_turn = [r for r in eumbong if (r.get("turnover_2d_pct") or 0) >= 200 and _measured(r)]
    for sb in ("0", f"1~{config.SPARK_MIN - 1}", f">={config.SPARK_MIN}"):
        out["by_spark_eumbong_hi_turnover"][sb] = _stat([r for r in hi_turn if _spark_band(r.get("spark_1430_count")) == sb])

    def _cs_band(cs):  # 단일축·셀 동일 라벨(원시 float 경계) — 라벨 불일치 제거
        if cs is None:
            return None
        for lo, hi in config.CLOSE_STRENGTH_BANDS:
            if lo <= cs < hi:
                return f"{lo}~{hi}"
        return None
    for lo, hi in config.CLOSE_STRENGTH_BANDS:
        key = f"{lo}~{hi}"
        out["by_close_strength_eumbong"][key] = _stat([r for r in eumbong if _cs_band(r.get("close_strength")) == key])
    # 결합 셀(전 셀 보고). 미측정 스파크는 'none' 버킷으로 분리(0과 섞지 않음).
    seen = {}
    for r in eumbong:
        tb = _band(r.get("turnover_2d_pct"), config.TURNOVER_2D_BANDS)
        sb = _spark_band(r.get("spark_1430_count")) if _measured(r) else "none(미측정)"
        cb = _cs_band(r.get("close_strength"))
        if tb is None:
            continue
        seen.setdefault((tb, sb, cb), []).append(r)
    for (tb, sb, cb), grp in sorted(seen.items()):
        st = _stat(grp)
        st.update({"turnover2d": tb, "spark": sb, "close_strength": cb})
        out["cells"].append(st)

    # 14:30 스파크 횟수 단독(측정행 전체 — 음봉/회전 조건 없이. 회장님 핵심신호 직접 검증)
    measured = [r for r in rows if _measured(r)]
    for sb in ("0", f"1~{config.SPARK_MIN - 1}", f">={config.SPARK_MIN}"):
        out["by_spark_count"][sb] = _stat([r for r in measured if _spark_band(r.get("spark_1430_count")) == sb])

    # 키움 속 숨은 외국인 매집 강도 — SSOT: quant 저장 hidden_foreign_level 우선, 옛 행(필드 없음)이면 동일식 재계산.
    # 반환 None=결측(분류 제외·날조방지) / 0=미해당 / 1~3=해당.
    def _hf_level(r):
        if "hidden_foreign_level" in r:
            return r["hidden_foreign_level"]
        fn, gq, kc = r.get("frgn_net"), r.get("glob_net_qty"), r.get("kiwoom_buy_concentration")
        if fn is None or gq is None or kc is None:
            return None
        if fn <= 0 or abs(gq) >= abs(fn) * 0.1 or kc < 0.3:
            return 0
        return 3 if fn >= 100000 else 2 if fn >= 30000 else 1
    out["by_hidden_foreign"]["해당"] = _stat([r for r in rows if (_hf_level(r) or 0) > 0])
    out["by_hidden_foreign"]["미해당"] = _stat([r for r in rows if _hf_level(r) == 0])

    # 합산 종합점수(스파크 횟수 + 외인매집 강도) 밴드 — /alpha 정렬 순위 자체가 익일상승을 맞췄나 검증.
    # SSOT: quant 저장 combined_score 우선, 옛 행이면 재구성.
    def _combined(r):
        c = r.get("combined_score")
        if c is not None:
            return c
        sr = -1 if r.get("spark_source") == "none" else (r.get("spark_1430_count") or 0)
        return sr + (_hf_level(r) or 0)

    def _combined_band(c):
        if c is None:
            return None
        return "<=1" if c <= 1 else "2~3" if c <= 3 else "4~5" if c <= 5 else ">=6"
    for k in ("<=1", "2~3", "4~5", ">=6"):
        out["by_combined_score"][k] = _stat([r for r in rows if _combined_band(_combined(r)) == k])

    # LLM Brier(있으면)
    llm_rows = [r for r in rows if isinstance(r.get("prob_up"), (int, float))]
    if llm_rows:
        brier = sum((r["prob_up"] - (1 if r["hit"] else 0)) ** 2 for r in llm_rows) / len(llm_rows)
        bands = {}
        for lo, hi, lab in [(0, 0.46, "관망(<46)"), (0.46, 0.54, "중립"), (0.54, 1.01, "상승(>=54)")]:
            g = [r for r in llm_rows if lo <= r["prob_up"] < hi]
            bands[lab] = _stat(g)
        out["llm"] = {"n": len(llm_rows), "brier": round(brier, 4), "by_prob_band": bands}

    tmp = config.CALIBRATION + ".tmp"
    json.dump(out, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, config.CALIBRATION)
    print(f"[alpha-calibrate] 라벨표본 {len(rows)} · 음봉 {len(eumbong)} → {config.CALIBRATION}")
    print(f"  음봉 2일회전율별: " + " · ".join(f"{k}:{v['hit_rate']}%({v['n']},{v['status']})" for k, v in out["by_turnover2d_eumbong"].items()))
    return out


if __name__ == "__main__":
    run()
