#!/usr/bin/env python3
"""레이더 자가 검증·개선 — "당일 종가 매수 → 다음날 올랐나" 누적 백테스트.

매일(장후 17:20 cron) 실행:
  1. data/radar_history/*.json 의 미평가 수상 종목을 익일 일봉(KIS)과 대조
       적중 = 익일 종가 > 진입가(당일 종가) / 보조: 익일 고가 ≥ +3%, 수익률
  2. 점수대별 보정표(bins, 표본 n>=20만 유효) 산출
  3. 누적 표본 n>=30이면 점수 항목별 성과 상관으로 가중치 자동 튜닝
       (기본값 ±30% 제한, 변경 이력 기록) → data/radar_weights.json
  4. web/data/performance.json 생성 (대시보드 /performance 데이터)
  5. --push: history/weights/performance 변경 시 git commit+push

사용:
  python3 scripts/radar_backtest.py            # 평가+산출만
  python3 scripts/radar_backtest.py --push     # cron용
"""
import os
import sys
import glob
import json
import subprocess
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
                                # 마감 시 게시 카드 잔존 여부(= 종가 매수 가능했던 종목).
                                # 키 없는 과거(장후 실행) 기록은 True
                                "final": s.get("final", True),
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
          f"추적 {s['tracking_days']}일 · 장중탈락 {len(dropouts)}건(참고)")
    if "--push" in sys.argv[1:]:
        push_state()


if __name__ == "__main__":
    main()
