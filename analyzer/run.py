#!/usr/bin/env python3
"""최종 오케스트레이터 — 수집(팀원1)+기술(팀원2)+재료(팀원3)를 confluence로 종합.

24h 자동화 안정성을 위해 결정론 스코어(LLM 미사용). 산출물:
  - intraday_rank: 현재 시각 기준 잠정 랭킹(15분)
  - closing_bet:   내일 상승 확률 높은 종가베팅 후보(14:20 확정용)
→ web/data/predictions.json (기존 signals.json 불변) → push 시 Vercel 노출.

사용:
  python3 analyzer/run.py --dry-run        # /tmp 미리보기, push 안 함
  python3 analyzer/run.py [--push] [--top 30] [--bet 5]
"""
import os
import sys
import json
import subprocess
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import collect as collect_mod          # noqa: E402
from indicators import compute_indicators  # noqa: E402
from sentiment import analyze as analyze_news  # noqa: E402

KST = timezone(timedelta(hours=9))
REPO = os.path.join(HERE, "..")
PRED = os.path.join(REPO, "web", "data", "predictions.json")
STATE = os.path.join(HERE, "state")
HIST = os.path.join(STATE, "history")
MIN_TRADING_VALUE = 50_000_000_000  # 최근 5거래일 최대 거래대금 500억 미만 제외
DISCLAIMER = "예측·투자 참고용이며 매수 추천이 아닙니다. 갭 리스크가 있으니 손절을 지키세요."


def load_json(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def apply_calibration(raw, calib):
    """백테스트 보정표가 있으면 raw 점수를 실제 적중률(검증된 확률)로 치환."""
    if not calib:
        return raw
    for b in calib.get("bins", []):
        if b["lo"] <= raw < b["hi"] and b.get("n", 0) >= 20:
            return round(b["actual_rate"])
    return raw


def record_history(closing_bet, now):
    """그날 종가베팅 후보를 이력에 기록(익일 백테스트용). raw 점수 보존."""
    os.makedirs(HIST, exist_ok=True)
    rec = {"date": now.strftime("%Y%m%d"), "as_of": now.strftime("%Y-%m-%d %H:%M KST"),
           "bets": [{"code": b["code"], "ticker": b["ticker"], "entry": b["entry"],
                     "target": b["target"], "raw": b.get("_raw"), "prob": b["tomorrow_up_prob"]}
                    for b in closing_bet]}
    json.dump(rec, open(os.path.join(HIST, f"{rec['date']}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


def score(ind, sent, persist):
    """익일 상승확률 결정론 confluence (0~100). 투명 가중치."""
    if ind.get("error"):
        return 0, [], ["지표 없음"]
    p = 40.0
    pros, cons = [], []
    cs = ind.get("close_strength")
    if cs is not None and cs >= 0.7:
        p += 8; pros.append(f"강세마감({cs})")
    elif cs is not None and cs < 0.4:
        p -= 4; cons.append("약한 마감")
    if ind.get("ma_aligned"):
        p += 6; pros.append("정배열")
    macd = ind.get("macd") or {}
    if macd.get("golden_cross") or (macd.get("above_zero") and macd.get("bullish")):
        p += 7; pros.append("MACD강세")
    rsi = ind.get("rsi") or {}
    rv = rsi.get("rsi")
    if rv is not None:
        if rv >= 80:
            p -= 12; cons.append(f"RSI{rv} 과매수")
        elif 50 <= rv < 70:
            p += 5; pros.append(f"RSI{rv} 강세")
    st = ind.get("stochastic_slow") or {}
    if st.get("golden_cross") and not st.get("overbought"):
        p += 5; pros.append("Stoch골든")
    elif st.get("overbought"):
        p -= 6; cons.append("Stoch 과매수")
    ich = ind.get("ichimoku") or {}
    if ich.get("available") and ich.get("above_cloud") and ich.get("tenkan_gt_kijun"):
        p += 7; pros.append("일목 구름위")
    if (ind.get("volume_vs_20d") or 0) >= 1.5:
        p += 4; pros.append(f"거래량{ind['volume_vs_20d']}배")
    # 재료
    s = sent or {}
    if s.get("sentiment") == "호재":
        p += 6; pros.append("호재")
    elif s.get("sentiment") == "악재":
        p -= 12; cons.append("악재")
    p += min(6.0, (s.get("importance") or 0) * 0.7)
    # 지속성(장중 반복 등장)
    ap = persist.get("appearances", 1) if persist else 1
    p += min(8.0, ap * 1.0)
    if ap >= 3:
        pros.append(f"장중 {ap}회 지속")
    return max(5, min(95, round(p))), pros[:3], cons[:2]


def build(top, bet_n, record=True):
    now = datetime.now(KST)
    uni = collect_mod.fetch_universe()
    state, _ = collect_mod.accumulate(uni, now, write=record)
    cand = uni[:top]  # API 확률 상위만 심화분석(호출 통제)
    calib = load_json(os.path.join(STATE, "calibration.json"))  # 백테스트 보정표(있으면)

    rows = []
    for u in cand:
        ind = compute_indicators(u["code"], u["name"])
        if ind.get("error"):
            continue
        if (ind.get("max_trading_value_5d") or 0) < MIN_TRADING_VALUE:
            continue
        sent = analyze_news(u["code"], u["name"])
        persist = state.get(u["code"], {})
        raw, reasons, risks = score(ind, sent, persist)
        prob = apply_calibration(raw, calib)  # 검증된 확률로 치환(데이터 누적 후)
        last = ind.get("last_close")
        row = {
            "ticker": u["name"], "code": u["code"],
            "tomorrow_up_prob": f"{prob}%", "_p": prob, "_raw": raw,
            "entry": last,
            "target": round(last * 1.05) if last else None,
            "stop": round(last * 0.97) if last else None,
            "confidence": "상" if prob >= 70 else "중" if prob >= 55 else "하",
            "reasons": reasons or ["근거 부족"],
            "risk": ", ".join(risks) if risks else "특이 위험 낮음",
            "day_change": u["day_change"],
        }
        cause_news = sent.get("cause_news") or []
        related_news = sent.get("related_news") or []
        if cause_news:
            cause_items = [
                {"title": n.get("title"), "url": n.get("url"),
                 "office": n.get("office"), "sentiment": n.get("sentiment"),
                 "cause_score": n.get("cause_score"),
                 "cause_reason": n.get("cause_reason")}
                for n in cause_news[:3] if n.get("title")
            ]
            if cause_items:
                row["cause_news"] = cause_items
                if sent.get("cause_confidence"):
                    row["cause_confidence"] = sent.get("cause_confidence")
                if sent.get("cause_summary"):
                    row["cause_summary"] = sent.get("cause_summary")
        elif related_news:
            related_items = [
                {"title": n.get("title"), "url": n.get("url"),
                 "office": n.get("office"), "sentiment": n.get("sentiment")}
                for n in related_news[:3] if n.get("title")
            ]
            if related_items:
                row["related_news"] = related_items
        rows.append(row)
    rows.sort(key=lambda r: -r["_p"])
    closing = [r for r in rows if r["_p"] >= 55][:bet_n]  # 확신 일정 이상만 종가베팅
    if record:
        record_history(closing, now)  # 익일 백테스트용 이력 기록(_raw 보존)
    for r in rows:                # 출력 전 내부 필드 제거
        r.pop("_p", None)
        r.pop("_raw", None)

    return {
        "as_of": now.strftime("%Y-%m-%d %H:%M KST"),
        "intraday_rank": rows,      # 잠정 랭킹(전체)
        "closing_bet": closing,     # 종가베팅 후보(상위·확신)
        "disclaimer": DISCLAIMER,
        "backtest": load_json(os.path.join(STATE, "backtest.json")),  # 적중률 요약(누적 후 채워짐)
    }


def git(*a):
    return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)


def acquire_git_lock():
    """전 푸셔(publish/radar_backtest/analyzer) 공용 git 직렬화 락 — autostash 교차 오염 방지."""
    try:
        import fcntl
        fh = open("/tmp/stocknews_git.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX)
        return fh
    except ImportError:
        return None


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    top = int(args[args.index("--top") + 1]) if "--top" in args else 30
    bet = int(args[args.index("--bet") + 1]) if "--bet" in args else 5

    if not dry:
        # build()가 추적 상태 파일(state/history 등)을 쓰므로 그 전에 공용 git 락 —
        # 락 밖 미커밋 변경을 타 푸셔 autostash가 스태시/충돌로 날리는 것 방지
        git_lock = acquire_git_lock()  # noqa: F841 — 프로세스 종료까지 유지
    out = build(top, bet, record=not dry)
    text = json.dumps(out, ensure_ascii=False, indent=2)

    if dry:
        open("/tmp/predictions_preview.json", "w", encoding="utf-8").write(text)
        print(f"[DRY-RUN] /tmp/predictions_preview.json  (as_of {out['as_of']})")
        print(f"종가베팅 후보 {len(out['closing_bet'])} / 잠정랭킹 {len(out['intraday_rank'])}")
        for r in out["closing_bet"]:
            print(f"  🎯 {r['ticker']} {r['tomorrow_up_prob']} 진입{r['entry']} 손절{r['stop']} [{r['confidence']}] {r['reasons']}")
        return

    open(PRED, "w", encoding="utf-8").write(text)  # git 락 보유 중 (build 전 획득)
    if "--push" in args:
        git("add", "web/data/predictions.json")
        git("commit", "-q", "-m", f"data: 내일상승 예측 갱신 (종가베팅 {len(out['closing_bet'])})")
        pl = git("pull", "--rebase", "--autostash", "origin", "main")
        if pl.returncode != 0:
            git("rebase", "--abort"); sys.stderr.write("pull 실패\n"); sys.exit(1)
        pr = git("push", "origin", "main")
        if pr.returncode != 0:  # 실패 묵살 금지 — 미push 커밋이 조용히 쌓이는 것 방지
            sys.stderr.write("push 실패:\n" + pr.stderr[-500:])
            sys.exit(1)
    print(f"predictions.json 작성 (as_of {out['as_of']}, 종가베팅 {len(out['closing_bet'])})")


if __name__ == "__main__":
    main()
