"""전진검증 채점 — 라벨된 forward 표본으로 (a)정량 가설밴드 (b)LLM confidence Brier 산출 → calibration.json.
정량밴드 1순위축 = 2일 유통회전율. min_n 게이트(셀 표본<CALIB_MIN_N 이면 '관찰중'). 전 셀 보고(체리피킹 금지).
"""
import json
import os
import config
import fitness


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


def _load_all():
    """라벨 무관 전체 행 — 종베 '순위' 축은 그날 전체 movers로 순위를 매겨야 정확(라벨된 것만으로 순위 왜곡 방지)."""
    rows = []
    try:
        with open(config.FORWARD_JSONL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
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
        "by_combined_score": {},         # (레거시) 합산 종합점수(스파크 횟수 + 외인매집 강도) 밴드
        "by_change_pct": {},             # 당일 등락률 밴드별 — 종베 핵심(0~+8% 최적 vs 이미 오른 종목)
        "by_mover_type": {},             # reaccum/youtong/explosion별 익일 성과
        "by_close_bet_band": {},         # 종베 적합도 점수대별(적합/중간/약/부적합) — /alpha 현행 정렬축 검증
        "by_close_bet_rank": {},         # 종베 정렬 순위별(1위/2위/… — '1·2위만 종베' 실전 검증)
        "by_value_band": {},             # 거래대금 밴드별(채점축 대칭 검증 — v4 ≥1000억 +10의 전진검증)
        "by_spark_strength": {},         # 스파크 세기(무/약<3%/강≥3%) — '무>강' 관측의 서열 판정용
        "by_liquidity_deficit": {},      # 유동성결핍(대금<50억 or 회전2d<40%) 해당/미해당 — v4 −15 검증
        "by_crash_state": {},            # 폭락제외(과확장붕괴/연속하락4일+/정상) — 회장님 지시 벌점 전진검증
        "by_ma20": {},                   # 20일선 위/아래 — 역배열 벌점(−20) 전진검증(회장님 지시 2026-07-03)
        "by_low_accum": {},              # 🧲 저점매집(폭락+MA20사수+전일 2%+스파크≥3) 해당/미해당 — 익일 성과 전진검증
        "by_alert": {},                  # 시장경보별(무경보/주의/경고지정예측/경고·위험, 서로소) — 벌점(주의−10/예측−25/경고−30/위험−60) 전진검증
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
        lv = _hf_level(r)
        if lv is None:
            return None   # 외인매집 결측 → 종합점수 미정(밴드 제외, 결측을 0으로 섞지 않음)
        sr = -1 if r.get("spark_source") == "none" else (r.get("spark_1430_count") or 0)
        return sr + lv

    def _combined_band(c):
        if c is None:
            return None
        return "<=1" if c <= 1 else "2~3" if c <= 3 else "4~5" if c <= 5 else ">=6"
    for k in ("<=1", "2~3", "4~5", ">=6"):
        out["by_combined_score"][k] = _stat([r for r in rows if _combined_band(_combined(r)) == k])

    # ── 종가베팅 적합도(SSOT=fitness.close_bet_fitness) 검증 축 ──
    # ⚠ 저장된 close_bet_fitness는 신뢰하지 않고 '항상 재계산' — 산식(fitness.py) 변경 시 옛 forward 행의
    #    저장값이 stale해져 검증축이 옛 산식으로 채점되는 것을 방지(웹 AlphaList도 매번 재계산하므로 기준 일치).
    def _cbf(r):
        return fitness.close_bet_fitness(r)

    # 당일 등락률 밴드별(전체) — 종베 핵심축(0~+8% 최적, +8%↑는 단조 강등)
    def _change_band(c):
        if c is None:
            return None
        return "≤-10" if c <= -10 else "-10~0" if c < 0 else "0~8" if c < 8 else "8~15" if c < 15 else "15~22" if c < 22 else "22+"
    for b in ("≤-10", "-10~0", "0~8", "8~15", "15~22", "22+"):
        out["by_change_pct"][b] = _stat([r for r in rows if _change_band(r.get("change_pct")) == b])

    # mover 유형별
    for mt in ("reaccum", "youtong", "explosion"):
        out["by_mover_type"][mt] = _stat([r for r in rows if r.get("mover_type") == mt])

    # 종베 점수대별
    def _cbf_band(s):
        return "적합(75+)" if s >= 75 else "중간(60~74)" if s >= 60 else "약(45~59)" if s >= 45 else "부적합(<45)"
    for b in ("적합(75+)", "중간(60~74)", "약(45~59)", "부적합(<45)"):
        out["by_close_bet_band"][b] = _stat([r for r in rows if _cbf_band(_cbf(r)) == b])

    # 종베 순위별 — 그날 전체 movers(라벨 무관)로 순위 산정 후 라벨 행만 채점.
    # ⚠ 정렬키는 웹 AlphaList.tsx sort와 **1:1 동기화**해야 순위축이 화면 순위를 정확히 검증한다:
    #    점수 desc · 거래대금(value_eok) desc · code asc (완전 결정·잔여순서 의존 제거).
    #    (구 |회전2d-115| 타이브레이크는 폐기된 80~150 스윗스팟의 유산이라 v4에서 제거 — 감사 판결.
    #     value_eok desc는 39표본에서 유일한 양(+0.161)의 날짜내 순위상관 신호.)
    # 같은 날 같은 코드가 여러 행이면(정지종목 stale date 등) 첫 행만 — 순위 유니버스를 code 단위로 유일화(중복 카운트/덮어쓰기 방지).
    by_date = {}
    for r in _load_all():
        if r.get("data_ok") is False or not r.get("date"):
            continue
        by_date.setdefault(r["date"], {}).setdefault(r.get("code"), r)
    rank_of = {}
    for dt, bycode in by_date.items():
        ordered = sorted(bycode.values(),
                         key=lambda r: (-_cbf(r), -(r.get("value_eok") or 0), r.get("code") or ""))
        for i, r in enumerate(ordered, 1):
            rank_of[(r.get("code"), dt)] = i
    rank_groups = {"1위": [], "2위": [], "3위": [], "4~5위": [], "6위+": []}
    for r in rows:
        rk = rank_of.get((r.get("code"), r.get("date")))
        if rk is None:
            continue
        b = "1위" if rk == 1 else "2위" if rk == 2 else "3위" if rk == 3 else "4~5위" if rk <= 5 else "6위+"
        rank_groups[b].append(r)
    for b in ("1위", "2위", "3위", "4~5위", "6위+"):
        out["by_close_bet_rank"][b] = _stat(rank_groups[b])

    # ── v4 채점축 대칭 관찰축 (감사 판결: "채점하는 축은 검증축 대칭") ──
    # 거래대금 밴드별 — ≥1000억 +10의 전진검증
    def _val_band(v):
        if v is None:
            return None
        return "<50억" if v < 50 else "50~150억" if v < 150 else "150~1000억" if v < 1000 else "1000억+"
    for b in ("<50억", "50~150억", "150~1000억", "1000억+"):
        out["by_value_band"][b] = _stat([r for r in rows if _val_band(r.get("value_eok")) == b])

    # 스파크 세기(무/약/강1/강2+) — '무>강' 서열 판정 + 강2회+ 가점(+8, 회장님 지시) 전진검증
    def _strong_cnt(r):
        c = r.get("spark_strong_count")
        if c is not None:
            return c
        if r.get("spark_bars") is not None:
            return sum(1 for b in r["spark_bars"] if (b.get("body_pct") or 0) >= 3.0)
        return None
    def _spark_strength(r):
        if r.get("spark_source") in (None, "none"):
            return None                       # 미측정 — 분류 제외
        mx = r.get("spark_max_body_pct")
        if not mx or mx <= 0:
            return "무스파크"
        if mx < 3.0:
            return "약(<3%)"
        sc = _strong_cnt(r)
        return "강2회+" if (sc is not None and sc >= 2) else "강1회"
    for b in ("무스파크", "약(<3%)", "강1회", "강2회+"):
        out["by_spark_strength"][b] = _stat([r for r in rows if _spark_strength(r) == b])

    # 유동성결핍(대금<50억 or 회전2d<40%) — v4 통합 −15 검증
    def _liq_deficit(r):
        v, t = r.get("value_eok"), r.get("turnover_2d_pct")
        if v is None and t is None:
            return None
        return "결핍" if ((v is not None and v < 50) or (t is not None and t < 40)) else "정상"
    for b in ("결핍", "정상"):
        out["by_liquidity_deficit"][b] = _stat([r for r in rows if _liq_deficit(r) == b])

    # 폭락 제외 벌점(과확장붕괴·연속하락 — 2026-07-02 회장님 지시) 전진검증. 필드 없는 옛 행은 분류 제외.
    def _crash_state(r):
        r6, ds, c = r.get("run_6d_pct"), r.get("down_streak"), r.get("change_pct")
        if r6 is None and ds is None:
            return None
        if r6 is not None and r6 >= 100 and c is not None and c < 0:
            return "과확장붕괴"
        if ds is not None and ds >= 4:
            return "연속하락4일+"
        return "정상"
    for b in ("과확장붕괴", "연속하락4일+", "정상"):
        out["by_crash_state"][b] = _stat([r for r in rows if _crash_state(r) == b])

    # 20일선 위/아래 — 역배열 벌점 전진검증 (필드 없는 옛 행은 분류 제외)
    out["by_ma20"]["20일선 위"] = _stat([r for r in rows if (r.get("ma20_gap_pct") or -1) >= 0 and r.get("ma20_gap_pct") is not None])
    out["by_ma20"]["20일선 아래"] = _stat([r for r in rows if r.get("ma20_gap_pct") is not None and r["ma20_gap_pct"] < 0])

    # 🧲 저점매집 전진검증 — "저점매집 뜬 날이 진짜 매수 급소였나"(익일 고가로 채점). 필드 없는 옛 행 제외.
    out["by_low_accum"]["해당"] = _stat([r for r in rows if r.get("low_accum") is True])
    out["by_low_accum"]["미해당"] = _stat([r for r in rows if r.get("low_accum") is False])

    # 시장경보 지정 전진검증 — 필드 없는 옛 행 제외("alert_now" in r 게이트). None=무경보.
    # 서로소 분할 — 예측(forecast) 발동 행은 무경보/주의에서 빼서 '경고지정예측' 버킷으로만(대조군 오염 방지).
    out["by_alert"]["무경보"] = _stat([r for r in rows if "alert_now" in r and not r.get("alert_now") and not r.get("alert_forecast")])
    out["by_alert"]["주의"] = _stat([r for r in rows if r.get("alert_now") == "주의" and not r.get("alert_forecast")])
    # 경고지정예측(벌점 −25 대상: forecast 있고 경고/위험 미지정) — 위에서 무경보/주의 제외해 서로소
    out["by_alert"]["경고지정예측"] = _stat([r for r in rows if r.get("alert_forecast") and r.get("alert_now") not in ("경고", "위험")])
    out["by_alert"]["경고해제예정"] = _stat([r for r in rows if r.get("alert_now") == "경고" and r.get("alert_release")])
    out["by_alert"]["경고·위험"] = _stat([r for r in rows if r.get("alert_now") in ("경고", "위험") and not r.get("alert_release")])

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
    print(f"  종베 순위별 익일고가: " + " · ".join(f"{k}:{v['avg_high']}%({v['n']})" for k, v in out["by_close_bet_rank"].items()))
    return out


if __name__ == "__main__":
    run()
