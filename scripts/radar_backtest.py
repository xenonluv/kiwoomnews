#!/usr/bin/env python3
"""레이더 자가 검증·개선 — "당일 종가 매수 → 다음날 올랐나" 누적 백테스트.

매일(장후 17:20 cron) 실행:
  1. data/radar_history/*.json 의 미평가 수상 종목을 익일 일봉(KIS)과 대조
       적중 = 익일 종가 > 진입가(당일 종가) / 보조: 익일 고가 ≥ +3%, 수익률
  2. 당일 마감 카드(final) 종목의 AI 익일 예측(prob_up)을 history에 기록
       → 익일 평가와 대조해 AI 적중률·확률 보정 검증 (performance.json "ai")
  3. 점수대별 보정표(bins, 표본 n>=20만 유효) 산출
  4. 누적 표본 n>=30이면 점수 항목별 성과 상관으로 가중치 자동 튜닝
       (기본값 ±30% 제한, 변경 이력 기록) → data/radar_weights.json
  5. web/data/performance.json 생성 (대시보드 /performance 데이터)
  6. --push: history/weights/performance 변경 시 git commit+push

사용:
  python3 scripts/radar_backtest.py            # 평가+산출만
  python3 scripts/radar_backtest.py --push     # cron용
"""
import os
import sys
import glob
import json
import subprocess
import urllib.request
from datetime import datetime, timezone, timedelta

# 브로커 스위치 — radar.py와 동일(기본 키움 드롭인, RADAR_BROKER=kis 로 KIS 복귀).
# 익일봉 조회(kis.daily_prices)를 실제 운영 브로커(키움)로 맞춰야 채점이 동작한다.
if os.environ.get("RADAR_BROKER", "kiwoom").lower() == "kis":
    import kis_client as kis
else:
    import kiwoom_client as kis

# 흔들기 스윗존 경계는 radar.py를 SSOT로 import(정의 드리프트 차단) — 회장님 룰 결합코호트 판정에 사용.
from radar import (SHAKEOUT_T2D_SWEET_LO, SHAKEOUT_T2D_SWEET_HI,
                   SHAKEOUT_DD_SWEET_LO, SHAKEOUT_DD_SWEET_HI)

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DIR = os.path.join(REPO, "data", "radar_history")
WEIGHTS_PATH = os.path.join(REPO, "data", "radar_weights.json")
PERF_PATH = os.path.join(REPO, "web", "data", "performance.json")
SHAKEOUT_BACKFILL_PATH = os.path.join(REPO, "data", "shakeout_backfill.json")

# 수상함 점수 항목별 기본 최대치 (radar.py suspicion_score와 정합)
DEFAULT_WEIGHTS = {"spark": 15.0, "fade": 15.0, "flow": 15.0, "event": 15.0, "ma10": 10.0}
TUNE_MIN_SAMPLES = 30   # 가중치 튜닝 활성 최소 누적 표본
TUNE_BOUND = 0.30       # 기본값 대비 ±30% 제한
CALIB_MIN_N = 20        # 보정표 구간 유효 최소 표본
SCORE_BINS = [(40, 60), (60, 75), (75, 101)]
HIGH3_X = 1.03          # 보조지표: 익일 고가 +3%

# 메가스파크×수급 가설 검증 표 (radar.py MEGA_SPARK_X와 정합)
MEGA_X = 40.0
SPARK_BUCKETS = [("<10x", 0.0, 10.0), ("10~40x", 10.0, MEGA_X), ("≥40x", MEGA_X, float("inf"))]
FEATURE_MIN_N = 10      # 피처 셀 유효 최소 표본 (탐색용 — 보정표보다 낮은 임계)

# AI(prob_up) 예측 기록 — 웹과 동일한 프로덕션 엔드포인트 호출 (로직 중복 없음).
# 방향 파생 임계(상승≥54/하락≤46)와 정합하는 확률 구간으로 보정 검증 — 프로덕션 ai.ts
# PROB_BULL_MIN=54·PROB_BEAR_MAX=46(2026-06-20 58→54 하향)과 일치(track_eval·ai_click_eval도 54).
AI_ENDPOINT = os.environ.get(
    "RADAR_AI_ENDPOINT", "https://stocknews-cyan.vercel.app/api/stock/{code}/ai")
AI_PROB_BANDS = [(0, 47), (47, 54), (54, 101)]   # 하락(≤46)/관망(47~53)/상승(≥54)
# 룰베이스 vs AI 괴리 분석: 룰 "매수 우위" 임계(/stock scoring.ts 62점)와
# AI "상승" 임계(54, 사이트 방향배지와 동일)의 일치/불일치 4분면 — 어느 쪽이 맞는지 데이터로 판별
RULE_BUY_MIN = 62
AI_UP_MIN = 54

DISCLAIMER = ("백테스트는 '당일 종가 매수 → 익일 종가 매도' 가정의 참고 지표이며 "
              "수수료·슬리피지 미반영. 매수 추천이 아닙니다.")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def load_history():
    files = sorted(glob.glob(os.path.join(HISTORY_DIR, "*.json")))
    out = []
    for f in files:
        try:
            out.append((f, json.load(open(f, encoding="utf-8"))))
        except Exception as e:
            log(f"[warn] history 로드 실패 {f}: {e}")
    return out


def next_day_bar(code, date):
    """date(YYYYMMDD)의 신호일 봉과 바로 다음 거래일 봉 → (signal_bar, next_bar).

    신호일 봉이 조회 윈도우에 없으면 (None, None) — 엉뚱한 후일봉으로
    오평가하는 것을 막는다 (평가 보류, 오래되면 호출부에서 만료 처리).
    """
    try:
        bars = kis.daily_prices(code, days=40)
    except Exception as e:
        log(f"  [warn] {code} 일봉 실패: {e}")
        return None, None
    # 거래정지(종가 0/결측) 봉 제외 — track_eval/ai_click_eval과 동일. D+1이 정지면 close=0이 되어
    # hit=False·수익률 −100%인 거짓 표본이 core 통계·가중치 튜닝을 오염시키므로, 종가 유효 봉만 본다.
    bars = [b for b in bars if b.get("close")]
    sig = next((b for b in bars if b["date"] == date), None)
    if not sig:
        return None, None
    nxt = next((b for b in bars if b["date"] > date), None)
    return sig, nxt


def evaluate():
    """미평가 종목을 익일 일봉과 대조해 history 파일에 결과 역기록."""
    today = datetime.now(KST).strftime("%Y%m%d")
    n_eval = 0
    for path, hist in load_history():
        if hist.get("date", "") >= today:
            continue  # 당일분은 익일에 평가
        changed = False
        age_days = (datetime.now(KST).date()
                    - datetime.strptime(hist["date"], "%Y%m%d").date()).days
        for code, s in hist.get("suspects", {}).items():
            if s.get("evaluated") or not s.get("entry"):
                continue
            sig, nb = next_day_bar(code, hist["date"])
            if not sig or not nb:
                if age_days > 25:
                    # 조회 윈도우를 벗어난 오래된 미평가 — 영구 재조회 방지 위해 만료
                    s["evaluated"] = True
                    s["result"] = None
                    changed = True
                    log(f"  [expire] {hist['date']} {s.get('name')} — 평가 불가(만료)")
                continue  # 익일봉 미존재(연휴 등) — 다음 실행에서 재시도
            # entry는 신호일 일봉 종가로 재정합 (장중 마지막 회차 가격 ≠ 확정 종가 대비)
            entry = float(sig["close"]) if sig.get("close") else float(s["entry"])
            ret = (nb["close"] / entry - 1) * 100
            s["result"] = {
                "date": nb["date"],
                "next_close": nb["close"],
                "next_high": nb["high"],
                "hit": nb["close"] > entry,
                "high3": nb["high"] >= entry * HIGH3_X,
                "return_pct": round(ret, 2),
                # 익일 고가 도달폭(entry 대비 %) — 회장님 실제 익절선(+7/+13%) 검증·흔들기 밴드 튜닝 핵심 지표
                "next_high_pct": round((nb["high"] / entry - 1) * 100, 2),
            }
            s["evaluated"] = True
            changed = True
            n_eval += 1
            log(f"  [eval] {hist['date']} {s['name']} entry={entry:.0f} "
                f"→ 익일종가 {nb['close']:.0f} ({'적중' if s['result']['hit'] else '미적중'}, "
                f"{ret:+.1f}%)")
        if changed:
            json.dump(hist, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"[backtest] 신규 평가 {n_eval}건")


def ai_predict():
    """당일 마감 카드(final) 수상 종목의 AI 익일 예측(prob_up)을 history에 기록.

    웹 AI 라우트(3샘플 중앙값 합의)를 그대로 호출해 로직 중복 없이 동일 예측을 남긴다.
    익일 evaluate()의 result와 대조해 AI 적중률·확률 보정을 검증하는 루프의 입력.
    종목 단위 실패는 건너뜀(백테스트 본 작업 보호). RADAR_AI_PREDICT=0으로 비활성.

    실험(visible_experimental=재매집) 카드도 기록 대상 — 현 파이프라인의 유일 산출물이라
    AI 자료를 모아야 추후 코어 승격·괴리 분석이 가능. 단 ai_stats/divergence 표시는 여전히
    코어(write_performance에 core만 전달)만 집계하므로, 지금은 history에 '기록만' 쌓인다(#2a).
    """
    if os.environ.get("RADAR_AI_PREDICT", "").strip() == "0":
        return
    today = datetime.now(KST).strftime("%Y%m%d")
    path = os.path.join(HISTORY_DIR, f"{today}.json")
    if not os.path.exists(path):
        return
    try:
        hist = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        log(f"[warn] ai_predict history 로드 실패: {e}")
        return
    changed = False
    n_ok = 0
    for code, s in hist.get("suspects", {}).items():
        if not s.get("final") or s.get("ai_pred"):
            continue  # 마감 카드 잔존 종목만(실험 재매집 포함), 이미 기록된 건 재호출 안 함
        try:
            req = urllib.request.Request(
                AI_ENDPOINT.format(code=code), headers={"User-Agent": "radar-backtest"})
            r = json.load(urllib.request.urlopen(req, timeout=90))
            if not isinstance(r.get("probUp"), (int, float)):
                raise ValueError(str(r.get("error", {}).get("code") or "probUp 없음"))
            s["ai_pred"] = {
                "prob_up": round(float(r["probUp"])),
                # LLM 원시 확률 — 코드 산출(prob_up)과 어느 쪽 보정이 좋은지 비교 적립
                "model_prob": r.get("modelProbUp"),
                "direction": r.get("direction"),
                "model": r.get("model"),
                "as_of": r.get("asOf"),
                # 같은 시점의 /stock 룰베이스 판정 — AI와의 괴리 분석용 동시 기록
                "verdict_score": r.get("verdictScore"),
                "verdict_level": r.get("verdictLevel"),
            }
            changed = True
            n_ok += 1
            log(f"  [ai] {s.get('name')} prob_up={s['ai_pred']['prob_up']} {r.get('direction')}")
        except Exception as e:
            log(f"  [ai-skip] {s.get('name')}: {e}")
    if changed:
        json.dump(hist, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"[backtest] AI 예측 기록 {n_ok}건")


def collect_samples():
    """평가 완료 표본 전체 (날짜순)."""
    samples = []
    for _, hist in load_history():
        for code, s in hist.get("suspects", {}).items():
            if s.get("evaluated") and s.get("result"):
                r = s["result"]
                samples.append({"date": hist["date"],  # 신호일 (result의 평가일과 별개)
                                "code": code, "name": s.get("name"),
                                "score": s.get("score", 0),
                                # 표시 점수(reaccum은 score_raw=0이라 분리) — publish가 기록한 신규 history만 존재
                                "suspicion_score": s.get("suspicion_score"),
                                "breakdown": s.get("breakdown", {}),
                                "pattern": s.get("pattern", "unknown"),
                                "sector": s.get("sector") or "unknown",
                                "theme": s.get("theme") or "unknown",  # 구표본 미영속 → unknown
                                "theme_leader": s.get("theme_leader", False),  # 그날 테마 거래대금 1위
                                # 마감 시 게시 카드 잔존 여부(= 종가 매수 가능했던 종목).
                                # 키 없는 과거(장후 실행) 기록은 True
                                "final": s.get("final", True),
                                # 화면에는 노출하지만 기존 성과·튜닝 기준선에서는 제외할 실험 표본.
                                "visible_experimental": s.get("visible_experimental", False),
                                "reaccum": s.get("reaccum"),  # peak_ibs·peak_uppertail 포함(마감강도 밴드용)
                                "reignition": s.get("reignition"),  # 5분 스파크 count(스파크 횟수 밴드용)
                                # AI 익일 예측 (ai_predict가 기록 — 없으면 None/"none")
                                "ai_prob": (s.get("ai_pred") or {}).get("prob_up"),
                                "ai_dir": (s.get("ai_pred") or {}).get("direction") or "none",
                                # AI 기록 시점의 /stock 룰베이스 판정 점수 (괴리 분석용)
                                "verdict_score": (s.get("ai_pred") or {}).get("verdict_score"),
                                # 메가스파크×수급 검증용 피처. spark_max_x는 신규 기록만 존재
                                # (구버전 history는 복원 불가 → None = unknown 처리).
                                # flow_today_buy 폴백: flow = net_days*2 + today_buy*5 이므로
                                # 홀수 ⇔ 당일 순매수 (캡 15도 홀수라 안전).
                                "spark_max_x": s.get("spark_max_x"),
                                "flow_today_buy": s.get(
                                    "flow_today_buy",
                                    int(round(s.get("breakdown", {}).get("flow", 0) or 0)) % 2 == 1),
                                "mega_flow": s.get("mega_flow", False),
                                # 신호일 당일 등락률 — 등락률 구간별 익일 상승확률 분석용
                                "change_pct": s.get("change_pct"),
                                "change_basis": s.get("change_basis"),  # "NXT"면 야간가 기준 — change_band 필터로 제외(KRX hit과 기준 불일치)
                                # 폭발일 회전율(폭발일 거래량/유통주식수 %) — 구간별 익일 상승확률 검증용
                                "peak_turnover_pct": s.get("peak_turnover_pct"),
                                "turnover_basis": s.get("turnover_basis"),  # float|cap — 당일 회전율 산출 기준
                                "turnover_metric": s.get("turnover_metric"),  # "vol_float" — 밴드 필터(구 척도 분리)
                                # 흔들기 강도 튜닝용 변별 변수 (신규 history만 존재 — 구표본 None=unknown)
                                "shakeout": s.get("shakeout", False),
                                "turnover_2d_pct": s.get("turnover_2d_pct"),
                                "peak_dd_pct": s.get("peak_dd_pct"),
                                "strength_tier": s.get("strength_tier"),
                                "turnover_band": s.get("turnover_band"),
                                "dd_band": s.get("dd_band"),
                                "eval_date": r.get("date"),
                                "hit": r.get("hit", False),
                                "high3": r.get("high3", False),
                                "return_pct": r.get("return_pct", 0.0),
                                # 익일 고가 도달폭 — 흔들기 밴드별 익절터치율(+7/+13%) 집계용
                                "next_high_pct": r.get("next_high_pct")})
    samples.sort(key=lambda x: (x["date"], x["code"]))  # 동일 신호일 내 순서 안정화
    return samples


def build_series(samples):
    """일별 + 누적 적중률 시계열 (대시보드 라인차트 원천).

    표본 0인 history 날짜도 포함(누적선 유지) — '수집 일수'를 정직하게 보여준다.
    """
    by_date = {}
    for s in samples:
        by_date.setdefault(s["date"], []).append(s)
    all_dates = sorted({h.get("date") for _, h in load_history() if h.get("date")})
    series = []
    cum_n = cum_hits = 0
    for d in all_dates:
        day = by_date.get(d, [])
        hits = sum(1 for x in day if x["hit"])
        cum_n += len(day)
        cum_hits += hits
        series.append({
            "date": d,
            "n": len(day),
            "hits": hits,
            "hit_rate": round(hits / len(day) * 100) if day else None,
            "cum_n": cum_n,
            "cum_hit_rate": round(cum_hits / cum_n * 100, 1) if cum_n else None,
        })
    return series


def build_bins(samples):
    bins = []
    for lo, hi in SCORE_BINS:
        grp = [s for s in samples if lo <= s["score"] < hi]
        hits = sum(1 for s in grp if s["hit"])
        bins.append({"lo": lo, "hi": hi, "n": len(grp),
                     "actual_rate": round(hits / len(grp) * 100) if grp else None,
                     "valid": len(grp) >= CALIB_MIN_N})
    return bins


def group_stats(samples, key):
    out = []
    vals = sorted({s.get(key) or "unknown" for s in samples})
    for v in vals:
        grp = [s for s in samples if (s.get(key) or "unknown") == v]
        if not grp:
            continue
        hits = sum(1 for s in grp if s["hit"])
        rets = [s["return_pct"] for s in grp]
        out.append({"key": v, "n": len(grp),
                    "hit_rate": round(hits / len(grp) * 100, 1),
                    "avg_return": round(sum(rets) / len(rets), 2),
                    "high3_rate": round(sum(1 for s in grp if s["high3"]) / len(grp) * 100, 1)})
    return out


def group_stats_gated(samples, key, min_n=FEATURE_MIN_N):
    """group_stats + 표본부족 게이트(valid). n<min_n 행은 웹이 수치 숨기고 '수집 중' 표기.
    소표본으로 테마/섹터 우선순위를 단정하지 않게 하는 안전장치(현 n 적어 대부분 valid=false 정상)."""
    rows = group_stats(samples, key)
    for r in rows:
        r["valid"] = r["n"] >= min_n
    return rows


def fill_theme_leaders(rows, samples):
    """by_theme 각 행에 leader_name/leader_count 부여 = 그 테마에서 '테마 대장'(거래대금 1위)으로
    가장 자주 뽑힌 종목(표시 전용). 동률은 이름순. 대장 표본 없으면 미부여."""
    for r in rows:
        cnt = {}
        for s in samples:
            if (s.get("theme") or "unknown") == r["key"] and s.get("theme_leader"):
                nm = s.get("name") or "?"
                cnt[nm] = cnt.get(nm, 0) + 1
        if cnt:
            top = sorted(cnt, key=lambda n: (-cnt[n], n))[0]  # 최빈 → 동률은 이름 오름차순
            r["leader_name"], r["leader_count"] = top, cnt[top]
    return rows


def ai_stats(samples):
    """AI(prob_up) 예측 검증 — ai_pred가 기록된 평가 완료 표본만.

    방향별 적중률 + 확률 구간별 실측 적중률(보정 검증) + Brier 점수(낮을수록 좋음,
    0.25 = 항상 50%라 답한 무정보 기준선).
    """
    grp = [s for s in samples if s.get("ai_prob") is not None]
    if not grp:
        return {"n": 0, "by_direction": [], "prob_bands": [], "avg_prob": None,
                "actual_rate": None, "brier": None, "divergence": divergence_stats([])}
    hits = sum(1 for s in grp if s["hit"])
    bands = []
    for lo, hi in AI_PROB_BANDS:
        b = [s for s in grp if lo <= s["ai_prob"] < hi]
        bands.append({
            "lo": lo, "hi": hi, "n": len(b),
            "avg_prob": round(sum(s["ai_prob"] for s in b) / len(b), 1) if b else None,
            "actual_rate": round(sum(1 for s in b if s["hit"]) / len(b) * 100) if b else None,
            "valid": len(b) >= CALIB_MIN_N,
        })
    brier = sum((s["ai_prob"] / 100 - (1 if s["hit"] else 0)) ** 2 for s in grp) / len(grp)
    return {
        "n": len(grp),
        "by_direction": group_stats(grp, "ai_dir"),
        "prob_bands": bands,
        "avg_prob": round(sum(s["ai_prob"] for s in grp) / len(grp), 1),
        "actual_rate": round(hits / len(grp) * 100, 1),
        "brier": round(brier, 3),
        "divergence": divergence_stats(grp),
    }


def divergence_stats(samples):
    """룰베이스 판정 vs AI 예측의 일치/불일치 4분면 적중률 — 괴리 시 어느 쪽이 맞는지 검증.

    예: 룰 79점 "강한 매수신호" vs AI 46% 관망 (스피어 2026-06-12) → "룰만 강세" 셀.
    이 셀의 실측 적중률이 높으면 룰이, 낮으면 AI가 옳았던 것 — 표본 누적으로 판별해
    가중치·프롬프트 튜닝 근거로 쓴다. verdict_score 없는 구표본은 제외(unknown_n).
    """
    known = [s for s in samples if s.get("verdict_score") is not None]
    cells = []
    for label, rule_buy, ai_up in (
        ("동행 강세 (룰 매수 + AI 상승)", True, True),
        ("룰만 강세 (룰 매수 + AI 비상승)", True, False),
        ("AI만 강세 (룰 비매수 + AI 상승)", False, True),
        ("동반 약세 (룰 비매수 + AI 비상승)", False, False),
    ):
        grp = [s for s in known
               if (s["verdict_score"] >= RULE_BUY_MIN) == rule_buy
               and (s["ai_prob"] >= AI_UP_MIN) == ai_up]
        hits = sum(1 for s in grp if s["hit"])
        rets = [s["return_pct"] for s in grp]
        cells.append({
            "key": label, "rule_buy": rule_buy, "ai_up": ai_up, "n": len(grp),
            "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
            "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
            "valid": len(grp) >= FEATURE_MIN_N,
        })
    return {"rule_buy_min": RULE_BUY_MIN, "ai_up_min": AI_UP_MIN,
            "min_n": FEATURE_MIN_N, "unknown_n": len(samples) - len(known),
            "cells": cells}


def spark_flow_matrix(samples):
    """스파크 배율 구간 × 당일 수급매수 적중률 표 — 메가스파크 가설 검증.

    가설(2026-06-12 관찰): 스파크 ≥40배 + 외인/기관 매수 동반 종목은 회복력이 강함
    (HPSP 136x→상한가, 스피어 44x→반등). 표본 충분 시 MEGA_BONUS의 raw 승격 근거가 된다.
    spark_max_x 미기록 구표본은 unknown_n으로 분리 (셀 통계에서 제외).
    """
    known = [s for s in samples if s.get("spark_max_x") is not None]
    cells = []
    for label, lo, hi in SPARK_BUCKETS:
        for flow_buy in (True, False):
            grp = [s for s in known
                   if lo <= s["spark_max_x"] < hi and s["flow_today_buy"] == flow_buy]
            hits = sum(1 for s in grp if s["hit"])
            rets = [s["return_pct"] for s in grp]
            cells.append({
                "spark_bucket": label, "flow_buy": flow_buy, "n": len(grp),
                "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
                "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
                "high3_rate": (round(sum(1 for s in grp if s["high3"]) / len(grp) * 100, 1)
                               if grp else None),
                "valid": len(grp) >= FEATURE_MIN_N,
            })
    return {"mega_x": MEGA_X, "min_n": FEATURE_MIN_N,
            "unknown_n": len(samples) - len(known), "cells": cells}


# 신호일 당일 등락률 구간 — 반등 게이트에 등락률 제한이 없어졌으므로(개편) 음수~상한가 전 구간을 커버.
# '식음 후 반등 신호일에 몇 % 구간 종가매수가 익일 더 오르나'를 구간별로 검증(표시 전용).
CHANGE_BANDS = [("≤−5%", -100.0, -5.0), ("−5~0%", -5.0, 0.0), ("0~+5%", 0.0, 5.0),
                ("+5~+15%", 5.0, 15.0), ("+15%+", 15.0, 100.0)]


def change_band_stats(samples):
    """등락률 구간별 익일 상승확률(적중률)·평균수익 — '몇 % 구간 종가베팅이 익일 더 오르나'.

    hit_rate = 익일 종가 > 신호일 종가 비율 = 실측 상승확률. change_pct 미기록 구표본은 제외.
    ⚠ change_basis=="NXT"(마감 후 NXT 야간가로 재평가된 등락률) 표본도 제외 — hit은 KRX 정규장 종가 기준이라
       야간가 등락률을 같은 구간에 넣으면 x축(등락률)·결과축(KRX hit)의 가격 기준이 어긋난다. 구표본은
       change_basis 미기록(None)=KRX로 간주해 유지(turnover_metric=="vol_float" 필터와 동일한 분리 원칙).
    valid 게이트(n>=FEATURE_MIN_N)로 소표본 단정 방지.
    """
    known = [s for s in samples
             if s.get("change_pct") is not None and s.get("change_basis") in (None, "KRX")]
    cells = []
    for label, lo, hi in CHANGE_BANDS:
        grp = [s for s in known if lo <= s["change_pct"] < hi]
        hits = sum(1 for s in grp if s["hit"])
        rets = [s["return_pct"] for s in grp]
        cells.append({
            "band": label, "n": len(grp),
            "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
            "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
            "valid": len(grp) >= FEATURE_MIN_N,
        })
    return {"min_n": FEATURE_MIN_N, "unknown_n": len(samples) - len(known), "cells": cells}


# 폭발일 회전율 = 폭발일 거래량/유통주식수(%). 폭발 게이트가 ≥90%를 강제하므로 분포는 90%+에서 시작 →
# 구간을 90% 이상에서 변별. (구 메트릭=거래대금/유통시총 ~40~200% 표본은 90% 미만이면 자연 배제, 90%+면
# 섞이나 25일 만료로 자가 소거 — 표시 전용·score_raw=0 격리 풀이라 코어 통계·튜닝엔 무영향.)
TURNOVER_BANDS = [("90~120%", 90.0, 120.0), ("120~160%", 120.0, 160.0), ("160~220%", 160.0, 220.0),
                  ("220~300%", 220.0, 300.0), ("300%+", 300.0, 1e9)]


def peak_turnover_band_stats(samples):
    """폭발일 회전율(폭발일 거래량/유통주식수 %) 구간별 익일 상승확률·평균수익 — '유통주식이 더 크게
    손바뀐 폭발일수록 익일 더 오르나'를 데이터로 검증(재매집 실험 풀, 코어 통계·튜닝과 격리). peak_turnover_pct
    미기록 구표본은 제외. change_band_stats와 동일 셀 구조(웹 ChangeBandStats 타입 재사용).
    ⚠ 메트릭 버전 표본(turnover_metric=="vol_float")만 — 개편 전 거래대금/유통시총 척도(태그 없음)와
    섞지 않는다. turnover_basis(당일 회전율의 float/cap)가 라이브 스크랩 실패로 흔들려도 영향 없음."""
    known = [s for s in samples
             if s.get("peak_turnover_pct") is not None and s.get("turnover_metric") == "vol_float"]
    cells = []
    for label, lo, hi in TURNOVER_BANDS:
        grp = [s for s in known if lo <= s["peak_turnover_pct"] < hi]
        hits = sum(1 for s in grp if s["hit"])
        rets = [s["return_pct"] for s in grp]
        cells.append({
            "band": label, "n": len(grp),
            "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
            "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
            "valid": len(grp) >= FEATURE_MIN_N,
        })
    return {"min_n": FEATURE_MIN_N, "unknown_n": len(samples) - len(known), "cells": cells}


# 5분 스파크 횟수·폭발일 마감강도(IBS) 구간 — 주식분석.md ③·7일 표본 가설의 전진 검증용.
REIGNITION_COUNT_BANDS = [("2회", 2, 3), ("3~4회", 3, 5), ("5회+", 5, 1e9)]  # 게이트가 14:30↑ ≥2회로 변경
PEAK_IBS_BANDS = [("약마감 <0.4", 0.0, 0.4), ("중간 0.4~0.7", 0.4, 0.7), ("강마감 ≥0.7", 0.7, 2.0)]


def _hit_band_cells(known, keyfn, bands):
    """구간별 익일 적중률·평균수익 셀 — change_band/peak_turnover_band과 동일 셀 구조 공용."""
    cells = []
    for label, lo, hi in bands:
        grp = [s for s in known if lo <= keyfn(s) < hi]
        hits = sum(1 for s in grp if s["hit"])
        rets = [s["return_pct"] for s in grp]
        cells.append({
            "band": label, "n": len(grp),
            "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
            "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
            "valid": len(grp) >= FEATURE_MIN_N,
        })
    return cells


def reignition_count_band_stats(samples):
    """14:30~장종료 5분봉 양봉 스파크 횟수 구간별 익일 상승확률 — '마감 직전 재분출이 많을수록 익일 더 오르나'
    전진 검증. 게이트가 14:30↑ ≥2회라 분포는 2 이상(구표본=당일 전체 count는 정의가 달라 만료까지 혼재).
    ChangeBandStats 구조 재사용(웹 패널 공용)."""
    known = [s for s in samples if (s.get("reignition") or {}).get("count") is not None]
    return {"min_n": FEATURE_MIN_N, "unknown_n": len(samples) - len(known),
            "cells": _hit_band_cells(known, lambda s: s["reignition"]["count"], REIGNITION_COUNT_BANDS)}


def peak_ibs_band_stats(samples):
    """폭발일 마감강도(IBS=(종가−저가)/(고가−저가)) 구간별 익일 상승확률 — 7일 표본 반직관 가설('약마감
    [윗꼬리 큰]이 익일 연속성↑·상한가류 강마감은 식음↑')의 전진 검증. peak_ibs는 신규 history만 존재(구표본 제외)."""
    known = [s for s in samples if (s.get("reaccum") or {}).get("peak_ibs") is not None]
    return {"min_n": FEATURE_MIN_N, "unknown_n": len(samples) - len(known),
            "cells": _hit_band_cells(known, lambda s: s["reaccum"]["peak_ibs"], PEAK_IBS_BANDS)}


# 💥 흔들기 강도 튜닝 밴드 — 회장님 20년룰(회전 스윗90~140 + 깊은눌림 -30~-45 = 급등) 전진 검증축.
SHAKEOUT_T2D_BANDS = [("부족 <90", 0, 90), ("스윗 90~140", 90, 140),
                      ("과열 140~180", 140, 180), ("극과열 ≥180", 180, 1e12)]
SHAKEOUT_DD_BANDS = [("깊음 ≤-45", -1e12, -45), ("스윗 -45~-30", -45, -30), ("얕음 >-30", -30, 1e12)]
SHAKEOUT_TIER_BANDS = [("Tier1(강)", 0, 1), ("Tier2(중강)", 1, 2),
                       ("Tier3(중)", 2, 3), ("Tier4(약)", 3, 5)]


def _shakeout_stat(grp):
    """흔들기 표본군의 익일 성적 — 적중률·평균수익·평균고가 + 익절터치율(+7 부분·+11 전량·+13 참고).
    터치선(+7/+11)은 회장님 실제 매도 기준(+7% 50%익절/+11% 잔량익절)에 정렬."""
    hits = sum(1 for s in grp if s["hit"])
    rets = [s["return_pct"] for s in grp]
    highs = [s["next_high_pct"] for s in grp if s.get("next_high_pct") is not None]
    def touch(x):
        return round(sum(1 for h in highs if h >= x) / len(highs) * 100, 1) if highs else None
    return {
        "n": len(grp),
        "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
        "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
        "avg_high": round(sum(highs) / len(highs), 2) if highs else None,   # 익일 평균 고가 도달폭
        "touch7_rate": touch(7), "touch11_rate": touch(11), "touch13_rate": touch(13),
        "valid": len(grp) >= FEATURE_MIN_N,
    }


def _shakeout_cells(known, keyfn, bands):
    """흔들기 밴드 셀(단일 축)."""
    return [{"band": label, **_shakeout_stat([s for s in known
                                              if keyfn(s) is not None and lo <= keyfn(s) < hi])}
            for label, lo, hi in bands]


def _rule_flags(s):
    """(적정회전 여부, 깊은눌림 여부) — radar 스윗존 상수 SSOT. 결측이면 None."""
    t, dd = s.get("turnover_2d_pct"), s.get("peak_dd_pct")
    if t is None or dd is None:
        return None
    return (SHAKEOUT_T2D_SWEET_LO <= t <= SHAKEOUT_T2D_SWEET_HI,
            SHAKEOUT_DD_SWEET_LO <= dd <= SHAKEOUT_DD_SWEET_HI)


def shakeout_rule_cohorts(known):
    """💥 회장님 20년룰 결합(AND) 코호트 — 축을 쪼개지 않고 '적정회전 AND 깊은눌림'을 있는 그대로 검정.
    룰충족(둘 다) vs 적정회전만 vs 깊은눌림만 vs 그외 — 4코호트 익일 성적. 매도 기준(+7/+11) 터치율로 판정."""
    defs = [("룰충족(적정회전+깊은눌림)", (True, True)), ("적정회전만", (True, False)),
            ("깊은눌림만", (False, True)), ("그외", (False, False))]
    return [{"cohort": label, **_shakeout_stat([s for s in known if _rule_flags(s) == flags])}
            for label, flags in defs]


def load_shakeout_backfill():
    """💥 과거 흔들기 소급 재구성 표본(shakeout_backfill.py 산출) 로드 — 튜닝 표본 조기 확보.
    일봉 기반 재현(정의는 라이브와 1:1)·익일결과는 실제 일봉. 유니버스 한계(생존편향)는 파일 note에 명시."""
    try:
        d = json.load(open(SHAKEOUT_BACKFILL_PATH, encoding="utf-8"))
        return d.get("samples", [])
    except Exception:
        return []   # 파일 없으면 라이브 표본만


def shakeout_band_stats(shakeout_samples):
    """💥 흔들기 강도 튜닝표 — 2일회전율·고점낙폭·결합티어 밴드별 익일 상승확률·평균수익·고가터치율.
    회장님 20년룰(회전 적정 + 깊은눌림 = 급등, 과회전 = 물량소진) 전진 검증. 흔들기 변수는 신규 history만
    영속(2026-07-06~) → 표본 성숙 전엔 valid=False('관찰중')."""
    known = [s for s in shakeout_samples if s.get("turnover_2d_pct") is not None]
    backfill_n = sum(1 for s in known if s.get("backfill"))
    return {
        "min_n": FEATURE_MIN_N, "n": len(known),
        # 표본 출처 구분(정직) — live=오늘부터 라이브 게시분, backfill=일봉 소급 재구성분(생존편향 주의).
        "live_n": len(known) - backfill_n, "backfill_n": backfill_n,
        # 🎯 회장님 룰 = 결합(AND) 코호트 — 주 판정축(축 분리 밴드는 참고).
        "by_rule_cohort": shakeout_rule_cohorts(known),
        "by_turnover_2d": _shakeout_cells(known, lambda s: s.get("turnover_2d_pct"), SHAKEOUT_T2D_BANDS),
        "by_peak_dd": _shakeout_cells(known, lambda s: s.get("peak_dd_pct"), SHAKEOUT_DD_BANDS),
        "by_strength_tier": _shakeout_cells(known, lambda s: s.get("strength_tier"), SHAKEOUT_TIER_BANDS),
    }


def leader_reaccum_stats(reaccum_experimental):
    """'예전 대장' 재매집 엣지 검증 — was_theme_leader 코호트 A/B.

    가설: 폭발일에 업종 거래대금 1위(예전 대장)였던 종목이 재매집 시 익일 더 잘 오른다.
    leader(was_theme_leader=true) vs nonleader(false) vs all(전체 reaccum baseline)의
    익일 적중률·평균수익·고가3% 비교 + lift(=leader.hit_rate − nonleader.hit_rate).
    reaccum 실험 풀만 입력(코어 통계·가중치 튜닝과 격리). reaccum 블록 없거나 플래그가
    None인 표본은 unknown_n. min_n 게이트로 소표본 단정 방지.

    데이터 주의: sector 기반 대장 로직이 최근 개선돼 그 전 표본은 was_theme_leader가
    거짓일 수 있다 → 신뢰 표본은 배포 이후 forward로만 누적(valid 게이트가 자연 처리)."""
    def _flag(s):
        return (s.get("reaccum") or {}).get("was_theme_leader")
    leader = [s for s in reaccum_experimental if _flag(s) is True]
    nonleader = [s for s in reaccum_experimental if _flag(s) is False]
    unknown_n = sum(1 for s in reaccum_experimental if _flag(s) not in (True, False))

    def _cohort(subset):
        st = sample_stats(subset)
        st["valid"] = st["n"] >= FEATURE_MIN_N
        return st

    lc, nlc = _cohort(leader), _cohort(nonleader)
    lift = (round(lc["hit_rate"] - nlc["hit_rate"], 1)
            if lc["valid"] and nlc["valid"]
            and lc["hit_rate"] is not None and nlc["hit_rate"] is not None else None)
    return {
        "min_n": FEATURE_MIN_N,
        "unknown_n": unknown_n,
        "leader": lc,
        "nonleader": nlc,
        "all": _cohort(reaccum_experimental),
        "lift": lift,
    }


def tune_weights(samples):
    """항목별 정규화 기여도의 적중군-미적중군 평균 차(lift)로 가중치 조정.

    표본 < TUNE_MIN_SAMPLES 이면 None (기본값 유지). 결과는 ±TUNE_BOUND 제한.
    """
    if len(samples) < TUNE_MIN_SAMPLES:
        return None
    hits = [s for s in samples if s["hit"]]
    misses = [s for s in samples if not s["hit"]]
    if not hits or not misses:
        return None  # 전승/전패 표본으론 상관 계산 무의미

    def avg_norm(grp, comp):
        vals = [min(1.0, (s["breakdown"].get(comp, 0) or 0) / DEFAULT_WEIGHTS[comp])
                for s in grp]
        return sum(vals) / len(vals)

    weights = {}
    lifts = {}
    for comp, base in DEFAULT_WEIGHTS.items():
        lift = avg_norm(hits, comp) - avg_norm(misses, comp)  # -1 ~ +1
        factor = max(-TUNE_BOUND, min(TUNE_BOUND, lift))
        weights[comp] = round(base * (1 + factor), 1)
        lifts[comp] = round(lift, 3)
    return {"weights": weights, "lifts": lifts, "basis_n": len(samples)}


def sample_stats(samples):
    n = len(samples)
    hits = sum(1 for s in samples if s["hit"])
    rets = [s["return_pct"] for s in samples]
    return {
        "n": n,
        "hit_rate": round(hits / n * 100, 1) if n else None,
        "avg_return": round(sum(rets) / n, 2) if n else None,
        "high3_rate": round(sum(1 for s in samples if s["high3"]) / n * 100, 1) if n else None,
    }


def save_weights(tuned):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    prev = {}
    if os.path.exists(WEIGHTS_PATH):
        try:
            prev = json.load(open(WEIGHTS_PATH, encoding="utf-8"))
        except Exception:
            pass
    if tuned is None:
        return prev or None
    hist = prev.get("history", [])
    if not hist or hist[-1].get("weights") != tuned["weights"]:
        hist.append({"date": today, "weights": tuned["weights"], "basis_n": tuned["basis_n"]})
    out = {"weights": tuned["weights"], "default": DEFAULT_WEIGHTS,
           "lifts": tuned["lifts"], "basis_n": tuned["basis_n"],
           "updated": today, "history": hist[-30:]}
    json.dump(out, open(WEIGHTS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out


# ── 분할 전략 실측 트래커 — 레이더 신호를 20/30/50 분할 + 7%익절/-5%손절로 매매 가정,
#    forward 일봉으로 실현 net 수익 누적(표시 전용·라이브 보정). 손절=종가기준(저가 데이터 없음).
STRAT = {"tranches": [0.2, 0.3, 0.5], "tp": 7.0, "sl": 5.0, "fee": 0.3, "hold": 10, "addwin": 4}
STRATEGY_MIN_N = 30  # 분할전략 패널 유효 최소 표본(미만은 "수집 중")


def _strategy_outcome(closes, highs, i):
    """20/30/50 분할(하락일 추가)+7%익절/-5%손절(종가)+수수료 차감 → (outcome, ret_net)|None(forward 부족)."""
    if i < 0 or i + STRAT["hold"] >= len(closes):
        return None
    w = STRAT["tranches"]
    bought = [(closes[i], w[0])]

    def avg():
        return sum(p * x for p, x in bought) / sum(x for _, x in bought)

    out = None
    ret = 0.0
    for t in range(i + 1, i + STRAT["hold"] + 1):
        a = avg()
        if a <= 0:
            return None
        if highs[t] >= a * (1 + STRAT["tp"] / 100):
            out, ret = "win", STRAT["tp"]; break
        if closes[t] <= a * (1 - STRAT["sl"] / 100):
            out, ret = "stop", (closes[t] / a - 1) * 100; break
        if (t - i) <= STRAT["addwin"] and len(bought) < len(w) and closes[t] < closes[t - 1]:
            bought.append((closes[t], w[len(bought)]))
    if out is None:
        out, ret = "time", (closes[i + STRAT["hold"]] / avg() - 1) * 100
    return out, round(ret - STRAT["fee"], 2)


def strategy_eval():
    """미시뮬 reaccum 신호를 forward 일봉으로 분할전략 시뮬 → history에 s['strategy'] 기록.
    10거래일 보유라 신호 후 ~16일(달력) 지난 것만 처리(그 전엔 보류). 40일 초과·신호일봉 부재는 None 만료."""
    today = datetime.now(KST).strftime("%Y%m%d")
    n_done = 0
    for path, hist in load_history():
        if hist.get("date", "") >= today:
            continue
        try:
            age = (datetime.now(KST).date()
                   - datetime.strptime(hist["date"], "%Y%m%d").date()).days
        except Exception:
            continue
        if age < 16:
            continue  # forward 10거래일 미확보 — 다음 실행에서 재시도
        changed = False
        for code, s in hist.get("suspects", {}).items():
            if s.get("pattern") != "reaccum" or "strategy" in s:
                continue
            try:
                bars = kis.daily_prices(code, days=40)
            except Exception:
                continue
            idx = next((k for k, b in enumerate(bars) if b.get("date") == hist["date"]), None)
            if idx is None:
                if age > 40:
                    s["strategy"] = None; changed = True  # 더는 못 받음 → 만료
                continue
            closes = [b.get("close") for b in bars]
            highs = [b.get("high") for b in bars]
            win = closes[idx:idx + STRAT["hold"] + 1] + highs[idx:idx + STRAT["hold"] + 1]
            if any(x is None for x in win):   # close·high 어느 쪽이라도 결측이면 채점 보류/만료
                if age > 40:
                    s["strategy"] = None; changed = True
                continue
            oc = _strategy_outcome(closes, highs, idx)
            if oc is None:
                if age > 40:
                    s["strategy"] = None; changed = True
                continue
            s["strategy"] = {"outcome": oc[0], "ret_net": oc[1]}
            changed = True; n_done += 1
        if changed:
            json.dump(hist, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"[backtest] 분할전략 시뮬 신규 {n_done}건")


def strategy_sim_stats():
    """history에 누적된 분할전략 실현 결과 집계(표시 전용)."""
    rows = [s["strategy"] for _, hist in load_history()
            for s in hist.get("suspects", {}).values()
            if isinstance(s.get("strategy"), dict) and "ret_net" in s["strategy"]]
    n = len(rows)
    base = {"n": n, "min_n": STRATEGY_MIN_N, "tp": STRAT["tp"], "sl": STRAT["sl"],
            "fee": STRAT["fee"], "tranches": STRAT["tranches"]}
    if not n:
        return {**base, "win_rate": None, "stop_rate": None, "avg_net": None,
                "profit_rate": None, "worst": None}
    rets = sorted(r["ret_net"] for r in rows)
    return {**base,
            "win_rate": round(sum(1 for r in rows if r["outcome"] == "win") / n * 100, 1),
            "stop_rate": round(sum(1 for r in rows if r["outcome"] == "stop") / n * 100, 1),
            "avg_net": round(sum(rets) / n, 3),
            "profit_rate": round(sum(1 for x in rets if x > 0) / n * 100, 1),
            "worst": rets[0]}


def write_performance(samples, series, bins, weights, dropouts=None,
                      experimental=None, experimental_dropouts=None):
    n = len(samples)
    hits = sum(1 for s in samples if s["hit"])
    rets = [s["return_pct"] for s in samples]
    dropouts = dropouts or []
    experimental = experimental or []
    experimental_dropouts = experimental_dropouts or []
    reaccum_experimental = [s for s in experimental + experimental_dropouts
                            if s.get("pattern") == "reaccum"]
    reaccum_sorted = sorted(reaccum_experimental, key=lambda s: (s.get("date", ""), s.get("code", "")))
    # 💥 흔들기 표본 — 마감카드 잔존/탈락 무관 전량(shakeout 플래그 기준). 강도 밴드 튜닝축 전진검증.
    # + 과거 소급 재구성분(shakeout_backfill.json)을 (code,date) 디둡 병합 — 라이브 표본 우선(중복 시 라이브 유지).
    shakeout_live = [s for s in experimental + experimental_dropouts if s.get("shakeout")]
    _seen = {(s.get("code"), s.get("date")) for s in shakeout_live}
    shakeout_backfill = [b for b in load_shakeout_backfill()
                         if (b.get("code"), b.get("date")) not in _seen]
    shakeout_experimental = shakeout_live + shakeout_backfill
    dn = len(dropouts)
    out = {
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "summary": {
            "n": n,
            "hit_rate": round(hits / n * 100, 1) if n else None,
            "avg_return": round(sum(rets) / n, 2) if n else None,
            "high3_rate": round(sum(1 for s in samples if s["high3"]) / n * 100) if n else None,
            "tracking_days": len(series),
            # 장중 탈락군(마감 카드 미잔존) 참고 성적 — 탈락 필터의 효용 검증용.
            # 주 통계·튜닝에는 포함되지 않는다.
            "dropout": ({"n": dn,
                         "hit_rate": round(sum(1 for s in dropouts if s["hit"]) / dn * 100, 1)}
                        if dn else None),
        },
        "series": series,
        "bins": bins,
        "by_pattern": group_stats(samples, "pattern"),
        # 테마/섹터별 성과 — "어느 테마가 강한 반등인가" 데이터 근거. valid 게이트로 소표본 단정 방지.
        "by_sector": group_stats_gated(samples, "sector"),
        "by_theme": fill_theme_leaders(group_stats_gated(samples, "theme"), samples),
        # AI 익일 예측(prob_up) 검증 루프 — ai_predict()가 기록한 표본의 적중·보정 통계
        "ai": ai_stats(samples),
        # 메가스파크×수급 가설 검증 표 (스파크 배율 구간 × 당일 수급매수)
        "spark_flow": spark_flow_matrix(samples),
        # 등락률 구간별 익일 상승확률 — '몇 % 구간 종가매수가 익일 더 오르나'(재매집 실험 풀)
        "change_bands": change_band_stats(reaccum_experimental),
        # 폭발일 회전율 구간별 익일 상승확률 — '시총 대비 폭발이 클수록 더 오르나'(재매집 실험 풀, peak_turnover 비중 검증)
        "peak_turnover_bands": peak_turnover_band_stats(reaccum_experimental),
        # 5분 스파크 횟수 구간별 익일 상승확률 — 주식분석.md ③ '스파크 많을수록 오르나' 전진 검증(재매집 실험 풀)
        "reignition_count_bands": reignition_count_band_stats(reaccum_experimental),
        # 폭발일 마감강도(IBS) 구간별 익일 상승확률 — 7일 표본 반직관 가설('약마감↑') 전진 검증(재매집 실험 풀)
        "peak_ibs_bands": peak_ibs_band_stats(reaccum_experimental),
        # 💥 흔들기 강도 튜닝표 — 2일회전율·고점낙폭·결합티어 밴드별 익일 상승확률·고가터치율(회장님 20년룰 검증)
        "shakeout_bands": shakeout_band_stats(shakeout_experimental),
        # 분할 전략 실측 — 20/30/50 분할+7%익절/-5%손절 실현 net 누적(라이브 보정)
        "strategy_sim": strategy_sim_stats(),
        "experimental": {
            # 재매집(reaccum) = 현 파이프라인 주력 산출물. core(fade/shakeout)와 격리(score_raw=0)돼
            # 메인 통계엔 미반영이나, 실제 매일 쌓이는 트랙이라 자체 적중률 추세·최근표를 노출한다.
            "reaccum": {
                **sample_stats(reaccum_experimental),
                "tracking_days": len(series),
                "series": build_series(reaccum_experimental),
                "recent": [{"date": s["date"], "name": s["name"],
                            "score": s.get("suspicion_score") or 0,  # 표시점수(구표본 미기록=0)
                            "hit": s["hit"], "return_pct": s["return_pct"]}
                           for s in reaccum_sorted[-20:]][::-1],
            },
            # '예전 대장' 재매집 엣지 검증(대장 vs 비대장 익일 적중률 A/B·lift) — 코어 격리
            "leader_reaccum": leader_reaccum_stats(reaccum_experimental),
        },
        "weights": {
            "current": (weights or {}).get("weights") or DEFAULT_WEIGHTS,
            "default": DEFAULT_WEIGHTS,
            "tuned": bool(weights and weights.get("basis_n", 0) >= TUNE_MIN_SAMPLES),
            "basis_n": (weights or {}).get("basis_n", 0),
            "tune_min_samples": TUNE_MIN_SAMPLES,
            "history": (weights or {}).get("history", []),
        },
        "recent": [{"date": s["date"], "name": s["name"], "score": s["score"],
                    "hit": s["hit"], "return_pct": s["return_pct"]}
                   for s in samples[-20:]][::-1],
        "disclaimer": DISCLAIMER,
    }
    os.makedirs(os.path.dirname(PERF_PATH), exist_ok=True)
    json.dump(out, open(PERF_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def acquire_git_lock():
    """전 푸셔 공용 git 직렬화 락 (publish.py와 동일 패턴) — autostash 교차 오염 방지."""
    try:
        import fcntl
        fh = open("/tmp/stocknews_git.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX)
        return fh
    except ImportError:
        return None


def push_state():
    # 공용 git 락은 main()이 첫 추적 파일 쓰기 전에 이미 보유 (이중 획득 = flock 자기 데드락)
    files = glob.glob(os.path.join(HISTORY_DIR, "*.json")) + [PERF_PATH]
    if os.path.exists(WEIGHTS_PATH):
        files.append(WEIGHTS_PATH)
    git("add", "--", *files)
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("변경 없음 — push skip")
        return
    r = git("commit", "-q", "-m", "data: 레이더 성과 검증 갱신")
    if r.returncode != 0:
        sys.stderr.write("commit 실패:\n" + r.stderr[-300:])
        sys.exit(1)
    for attempt in range(2):  # 다른 푸셔(publish 등)와 경합 시 1회 재시도
        pl = git("pull", "--rebase", "--autostash", "origin", "main")
        if pl.returncode != 0:
            sys.stderr.write("pull --rebase 실패 — 수동 확인 필요:\n" + pl.stderr[-300:])
            git("rebase", "--abort")
            sys.exit(1)
        pr = git("push", "origin", "main")
        if pr.returncode == 0:
            print("push 완료")
            return
    sys.stderr.write("push 실패:\n" + pr.stderr[-300:])
    sys.exit(1)


def main():
    # 추적 파일(history·weights·performance) 쓰기 전 공용 git 락 — 락 밖 미커밋 변경을
    # 타 푸셔 autostash가 스태시/충돌로 날리는 것 방지. publish가 9~20시로 확장돼 17:2x publish와
    # 락이 겹칠 수 있으나 공용 블로킹 락이라 정합성 보존(겹치면 publish가 잠깐 대기 후 다음 회차 자가복구).
    git_lock = acquire_git_lock()  # noqa: F841 — 프로세스 종료까지 유지
    evaluate()
    ai_predict()  # 당일 마감 카드의 AI 예측 기록 (익일 evaluate가 채점)
    strategy_eval()  # 분할 전략 실측 시뮬(forward 10일 충족분) — history에 누적
    samples = collect_samples()
    # 주 통계·튜닝 = 마감 카드 잔존(final) 표본만 — 정석 사용법(종가 매수)과 모집단 일치.
    core = [s for s in samples if s["final"] and not s.get("visible_experimental")]
    experimental = [s for s in samples if s["final"] and s.get("visible_experimental")]
    dropouts = [s for s in samples if (not s["final"]) and not s.get("visible_experimental")]
    experimental_dropouts = [s for s in samples
                             if (not s["final"]) and s.get("visible_experimental")]
    series = build_series(core)
    bins = build_bins(core)
    weights = save_weights(tune_weights(core))
    perf = write_performance(core, series, bins, weights, dropouts,
                             experimental, experimental_dropouts)
    s = perf["summary"]
    print(f"[backtest] 최종카드 표본 {s['n']}건 · 적중률 {s['hit_rate']}% · "
          f"평균수익 {s['avg_return']}% · 고가+3% {s['high3_rate']}% · "
          f"추적 {s['tracking_days']}일 · 장중탈락 {len(dropouts)}건(참고) · "
          f"실험표본 {len(experimental)}건 · AI평가표본 {perf['ai']['n']}건")
    if "--push" in sys.argv[1:]:
        push_state()


if __name__ == "__main__":
    main()
