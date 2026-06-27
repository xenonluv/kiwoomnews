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
        return {"n": 0, "hit_rate": None, "avg_return": None, "valid": False, "status": "관찰중"}
    hit = sum(1 for r in rows if r.get("hit"))
    avg = sum(r["next_return_pct"] for r in rows) / n
    valid = n >= config.CALIB_MIN_N
    return {"n": n, "hit_rate": round(hit / n * 100, 1), "avg_return": round(avg, 2),
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
        "cells": [],                     # turnover2d × spark × close_strength × 음봉 (min_n 게이트)
        "llm": None,
        "min_n": config.CALIB_MIN_N,
        "note": "전진검증 — 표본 부족 셀은 관찰중. 스파크는 거래일 수집분만(과거 불가). 매수추천 아님.",
    }
    for lo, hi in config.TURNOVER_2D_BANDS:
        key = f"{int(lo)}~{'inf' if hi > 1e8 else int(hi)}"
        out["by_turnover2d_eumbong"][key] = _stat([r for r in eumbong if _band(r.get("turnover_2d_pct"), config.TURNOVER_2D_BANDS) == key])
    hi_turn = [r for r in eumbong if (r.get("turnover_2d_pct") or 0) >= 200]
    for sb in ("0", f"1~{config.SPARK_MIN - 1}", f">={config.SPARK_MIN}"):
        out["by_spark_eumbong_hi_turnover"][sb] = _stat([r for r in hi_turn if _spark_band(r.get("spark_1430_count")) == sb])
    for lo, hi in config.CLOSE_STRENGTH_BANDS:
        key = f"{lo}~{hi}"
        out["by_close_strength_eumbong"][key] = _stat([r for r in eumbong if (r.get("close_strength") is not None and lo <= r["close_strength"] < hi)])
    # 결합 셀(전 셀 보고)
    seen = {}
    for r in eumbong:
        tb = _band(r.get("turnover_2d_pct"), config.TURNOVER_2D_BANDS)
        sb = _spark_band(r.get("spark_1430_count"))
        cb = _band((r.get("close_strength") or 0) * 100, [(lo * 100, hi * 100) for lo, hi in config.CLOSE_STRENGTH_BANDS])
        if tb is None:
            continue
        seen.setdefault((tb, sb, cb), []).append(r)
    for (tb, sb, cb), grp in sorted(seen.items()):
        st = _stat(grp)
        st.update({"turnover2d": tb, "spark": sb, "close_strength": cb})
        out["cells"].append(st)

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
