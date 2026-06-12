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

import kis_client as kis

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DIR = os.path.join(REPO, "data", "radar_history")
WEIGHTS_PATH = os.path.join(REPO, "data", "radar_weights.json")
PERF_PATH = os.path.join(REPO, "web", "data", "performance.json")

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
# 방향 파생 임계(58/42)와 정합하는 확률 구간으로 보정 검증.
AI_ENDPOINT = os.environ.get(
    "RADAR_AI_ENDPOINT", "https://stocknews-cyan.vercel.app/api/stock/{code}/ai")
AI_PROB_BANDS = [(0, 43), (43, 58), (58, 101)]
# 룰베이스 vs AI 괴리 분석: 룰 "매수 우위" 임계(/stock scoring.ts 62점)와
# AI "상승" 임계(58)의 일치/불일치 4분면 — 어느 쪽이 맞는지 데이터로 판별
RULE_BUY_MIN = 62
AI_UP_MIN = 58

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
            continue  # 마감 카드 잔존 종목만, 이미 기록된 건 재호출 안 함
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
                                "breakdown": s.get("breakdown", {}),
                                "pattern": s.get("pattern", "unknown"),
                                "ai_verdict": ((s.get("ai_verdict") or {}).get("verdict")
                                               or (s.get("ai_verdict") or {}).get("status")
                                               or "none"),
                                # 마감 시 게시 카드 잔존 여부(= 종가 매수 가능했던 종목).
                                # 키 없는 과거(장후 실행) 기록은 True
                                "final": s.get("final", True),
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
                                "eval_date": r.get("date"),
                                "hit": r.get("hit", False),
                                "high3": r.get("high3", False),
                                "return_pct": r.get("return_pct", 0.0)})
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


def write_performance(samples, series, bins, weights, dropouts=None):
    n = len(samples)
    hits = sum(1 for s in samples if s["hit"])
    rets = [s["return_pct"] for s in samples]
    dropouts = dropouts or []
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
        "by_ai_verdict": group_stats(samples, "ai_verdict"),
        # AI 익일 예측(prob_up) 검증 루프 — ai_predict()가 기록한 표본의 적중·보정 통계
        "ai": ai_stats(samples),
        # 메가스파크×수급 가설 검증 표 (스파크 배율 구간 × 당일 수급매수)
        "spark_flow": spark_flow_matrix(samples),
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


def push_state():
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
    evaluate()
    ai_predict()  # 당일 마감 카드의 AI 예측 기록 (익일 evaluate가 채점)
    samples = collect_samples()
    # 주 통계·튜닝 = 마감 카드 잔존(final) 표본만 — 정석 사용법(종가 매수)과 모집단 일치.
    finals = [s for s in samples if s["final"]]
    dropouts = [s for s in samples if not s["final"]]
    series = build_series(finals)
    bins = build_bins(finals)
    weights = save_weights(tune_weights(finals))
    perf = write_performance(finals, series, bins, weights, dropouts)
    s = perf["summary"]
    print(f"[backtest] 최종카드 표본 {s['n']}건 · 적중률 {s['hit_rate']}% · "
          f"평균수익 {s['avg_return']}% · 고가+3% {s['high3_rate']}% · "
          f"추적 {s['tracking_days']}일 · 장중탈락 {len(dropouts)}건(참고) · "
          f"AI평가표본 {perf['ai']['n']}건")
    if "--push" in sys.argv[1:]:
        push_state()


if __name__ == "__main__":
    main()
