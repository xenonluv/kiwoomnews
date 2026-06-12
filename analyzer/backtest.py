#!/usr/bin/env python3
"""Phase 3 — 예측 적중률 백테스트 & 점수 보정(calibration).

run.py가 기록한 종가베팅 이력(state/history/{YYYYMMDD}.json)을 **익일 실제 종가**와
대조 → 적중률·평균수익 + 예측점수→실제확률 보정표를 만든다.
출력: state/backtest.json(요약, run.py가 사이트에 노출), state/calibration.json(보정표, run.py가 점수 치환).

적중 정의(오버나이트): 익일 종가 > 진입(오늘 종가) = 상승 적중. 목표달성 = 익일 고가 ≥ 목표가.
"""
import os
import sys
import json
import glob
import subprocess

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from team3_price_context import fetch_daily  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")
STATE = os.path.join(HERE, "state")
HIST = os.path.join(STATE, "history")


def next_day_bar(code, pred_date):
    """예측일(D) 다음 거래일(D+1) 일봉. 아직 없으면 None(평가 보류)."""
    try:
        d = fetch_daily(code, days=120)
    except Exception:
        return None
    dates = [x["date"] for x in d]
    if pred_date not in dates:
        return None
    i = dates.index(pred_date)
    return d[i + 1] if i + 1 < len(d) else None


def evaluate():
    records = []
    for f in sorted(glob.glob(os.path.join(HIST, "*.json"))):
        day = json.load(open(f, encoding="utf-8"))
        D = day.get("date")
        for b in day.get("bets", []):
            entry = b.get("entry")
            nd = next_day_bar(b.get("code"), D) if entry else None
            if not nd:
                continue  # 익일 미도래 or 데이터 없음 → 평가 보류
            ret = (nd["close"] / entry - 1) * 100
            raw = b.get("raw")
            records.append({
                "date": D, "code": b.get("code"), "ticker": b.get("ticker"),
                "raw": raw, "ret": round(ret, 2),
                "hit_up": nd["close"] > entry,
                "hit_target": bool(b.get("target") and nd["high"] >= b["target"]),
            })

    n = len(records)
    summary = {"sample": n}
    if n:
        hits = sum(1 for r in records if r["hit_up"])
        tgts = sum(1 for r in records if r["hit_target"])
        summary["recent_hit_rate"] = f"{round(hits / n * 100)}% (n={n})"
        summary["avg_return"] = round(sum(r["ret"] for r in records) / n, 2)
        summary["target_rate"] = f"{round(tgts / n * 100)}%"

    # 보정표: raw 점수 구간별 실제 상승 적중률
    bins = []
    for lo in (50, 60, 70, 80, 90):
        grp = [r for r in records if r["raw"] is not None and lo <= r["raw"] < lo + 10]
        if grp:
            rate = round(sum(1 for r in grp if r["hit_up"]) / len(grp) * 100)
            bins.append({"lo": lo, "hi": lo + 10, "n": len(grp), "actual_rate": rate})

    os.makedirs(STATE, exist_ok=True)
    json.dump(summary, open(os.path.join(STATE, "backtest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump({"bins": bins}, open(os.path.join(STATE, "calibration.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    print("=== 백테스트 요약 ===")
    print(json.dumps(summary, ensure_ascii=False))
    print("=== 보정표(raw→실제 적중률) ===")
    for b in bins:
        print(f"  {b['lo']}~{b['hi']}: 실제 {b['actual_rate']}% (n={b['n']})")
    if n == 0:
        print("(아직 평가 가능한 이력 없음 — 예측 누적·익일 도래 후 산출됨)")
    return summary


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def acquire_git_lock():
    """전 푸셔(publish/radar_backtest/analyzer) 공용 git 직렬화 락 — autostash 교차 오염 방지."""
    try:
        import fcntl
        fh = open("/tmp/stocknews_git.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX)
        return fh
    except ImportError:
        return None


def push_state():
    """백테스트 산출물만 커밋/푸시. 변경이 없어도 이전 미푸시 커밋 회수를 위해 push 시도."""
    git_lock = acquire_git_lock()  # noqa: F841 — git 구간 동안 핸들 유지
    files = sorted(glob.glob(os.path.join(HIST, "*.json")))
    for name in ("backtest.json", "calibration.json"):
        path = os.path.join(STATE, name)
        if os.path.exists(path):
            files.append(path)

    rel_files = [os.path.relpath(path, REPO) for path in files]
    if rel_files:
        add = git("add", "--", *rel_files)
        if add.returncode != 0:
            sys.stderr.write("git add 실패:\n" + add.stderr[-1000:])
            sys.exit(1)

    diff = git("diff", "--cached", "--quiet", "--", *rel_files) if rel_files else None
    if diff and diff.returncode == 1:
        cm = git("commit", "-q", "-m", "data: update forecast backtest", "--", *rel_files)
        if cm.returncode != 0:
            sys.stderr.write("commit 실패:\n" + cm.stderr[-1000:])
            sys.exit(1)
    elif diff and diff.returncode != 0:
        sys.stderr.write("diff 확인 실패:\n" + diff.stderr[-1000:])
        sys.exit(1)
    else:
        print("백테스트 변경 없음 — commit skip, pull/push 확인")

    pl = git("pull", "--rebase", "--autostash", "origin", "main")
    if pl.returncode != 0:
        git("rebase", "--abort")
        sys.stderr.write("pull --rebase 실패 — abort 후 종료:\n" + pl.stderr[-1000:])
        sys.exit(1)

    ps = git("push", "origin", "main")
    if ps.returncode != 0:
        sys.stderr.write("push 실패:\n" + ps.stderr[-1000:])
        sys.exit(1)


if __name__ == "__main__":
    evaluate()
    if "--push" in sys.argv[1:]:
        push_state()
