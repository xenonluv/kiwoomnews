#!/usr/bin/env python3
"""레이더 게시 자동화 — radar.py → web/data/radar.json → 변경 시에만 push.

cron 안정성을 위해 LLM 없이 순수 Python. Vercel이 push를 받아 자동 재빌드.

사용:
  python3 scripts/publish.py --dry-run                  # /tmp 미리보기, push 안 함
  python3 scripts/publish.py --max 12 --names 한온시스템  # 실제 게시
cron(장중 15분): */15 9-15 * * 1-5  cd ~/stocknews && python3 scripts/publish.py >> /tmp/publish.log 2>&1

radar.py 인자(--min-value --high-pct --chg-min --chg-max --spark-x --spark-pct --names)는
그대로 전달된다. 빈 레이더(수상 종목 0)도 유효 상태로 게시한다.
"""
import os
import sys
import json
import subprocess
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RADAR_JSON = os.path.join(REPO, "web", "data", "radar.json")
HISTORY_DIR = os.path.join(REPO, "data", "radar_history")
DISCLAIMER = "본 정보는 투자 참고용이며 매수 추천이 아닙니다. 투자 판단과 책임은 본인에게 있습니다."
RADAR_PASSTHRU = ("--min-value", "--high-pct", "--chg-min", "--chg-max",
                  "--spark-x", "--spark-pct", "--mega-x", "--top-n",
                  "--shake-pct", "--shake-recover", "--shake-chg-max",
                  "--deep-drop-min", "--deep-drop-max", "--deep-ibs-min",
                  "--deep-recovery-min", "--deep-late-window",
                  "--kimi-mode", "--kimi-max", "--kimi-timeout",
                  "--kimi-window-start", "--kimi-window-end")
RADAR_BOOL_PASSTHRU = ("--no-deep-shake",)


def run_radar(extra_args):
    cmd = [sys.executable, os.path.join(REPO, "scripts", "radar.py")] + extra_args
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    if r.stderr:
        sys.stderr.write(r.stderr[-2000:])  # 스킵/경고 증거를 cron 로그에 남김
    if r.returncode != 0 or not r.stdout.strip():
        sys.stderr.write(f"radar 실패 (exit {r.returncode})\n")
        sys.exit(1)
    return json.loads(r.stdout)


def record_history(out):
    """당일 수상 종목을 검증용 이력에 누적 (radar_backtest.py가 익일 평가).

    같은 날 여러 회차가 코드별로 merge — 마지막 회차(15:45)의 price가 종가 entry로 남는다.
    수상 종목 0건인 날도 기록(표본 일수 카운트). 거래일에만 호출할 것.
    """
    os.makedirs(HISTORY_DIR, exist_ok=True)
    today = datetime.now(KST).strftime("%Y%m%d")
    path = os.path.join(HISTORY_DIR, f"{today}.json")
    hist = {"date": today, "suspects": {}}
    if os.path.exists(path):
        try:
            hist = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            # 손상 파일은 백업 후 재생성 — 조용한 전손 대신 흔적을 남긴다
            sys.stderr.write(f"[warn] history 손상 {path}: {e} — .corrupt 백업\n")
            try:
                os.replace(path, path + ".corrupt")
            except OSError:
                pass
    for s in out.get("suspects", []):
        prev = hist["suspects"].get(s["code"], {})
        hist["suspects"][s["code"]] = {
            "name": s["name"],
            "sector": s.get("sector", ""),
            "entry": s["price"],          # 당일 종가 매수 가정 (백테스트가 일봉 종가로 재정합)
            # 통계용은 raw(가중치 적용 전) — 튜닝 체제가 바뀌어도 표본 일관성 유지
            "score": s.get("score_raw", s["suspicion_score"]),
            "breakdown": s.get("score_breakdown_raw") or s.get("score_breakdown", {}),
            "change_pct": s.get("change_pct"),
            "high_pct": s.get("high_pct"),
            "fade_pct": s.get("fade_pct"),
            "pattern": s.get("pattern"),
            # 메가스파크×수급 가설 검증용 피처 (radar_backtest spark_flow 표가 사용)
            "spark_max_x": s.get("spark_max_x"),
            "spark_max_pct": s.get("spark_max_pct"),  # 부호 = 상승/하락 메가 분리 분석용
            "mega_flow": s.get("mega_flow", False),
            "flow_today_buy": bool((s.get("flow") or {}).get("today_buy")),
            "flow_net_days": (s.get("flow") or {}).get("net_days"),
            "deep_shake": s.get("deep_shake"),
            "ai_verdict": s.get("ai_verdict"),
            "matched_events": [m.get("id") for m in s.get("matched_events", [])],
            "first_seen": prev.get("first_seen") or out.get("generated_at"),
            "evaluated": prev.get("evaluated", False),
            "result": prev.get("result"),
        }
    # 최종 카드 마킹: 이번 회차 "게시 카드"(--max 컷 적용 후 = 사이트에 실제 표시된
    # 종목)에 있으면 True, 없으면 False. 매 회차 덮어쓰므로 마지막 회차(15:45)가 확정값.
    # 정의: final = 마감 시 사용자가 카드에서 보고 종가 매수할 수 있었던 종목.
    # (--max 컷에 밀린 13위 이하도 False — 사용자가 볼 수 없었으므로 의도된 동작)
    current = {s["code"] for s in out.get("suspects", [])}
    for code, rec in hist["suspects"].items():
        rec["final"] = code in current
    hist["as_of"] = out.get("generated_at")
    json.dump(hist, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return path


def market_session(now=None):
    now = now or datetime.now(KST)
    if now.weekday() >= 5:
        return "closed"
    hm = now.strftime("%H%M")
    return "open" if "0900" <= hm <= "1530" else "closed"


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def main():
    args = sys.argv[1:]
    # 동시 실행 방지: 겹친 cron 회차의 git race 차단
    lock_fh = None
    try:
        import fcntl
        lock_fh = open("/tmp/stocknews_publish.lock", "w")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("이미 실행 중(다른 publish 진행 중) — skip")
            return
    except ImportError:
        pass  # fcntl 없는 환경은 락 생략

    dry = "--dry-run" in args
    # --max 와 (구 cron 호환) --max-candidates 둘 다 허용
    max_key = "--max" if "--max" in args else ("--max-candidates" if "--max-candidates" in args else None)
    maxn = int(args[args.index(max_key) + 1]) if max_key else 12

    passthru = []
    for k in RADAR_PASSTHRU:
        if k in args:
            passthru += [k, args[args.index(k) + 1]]
    for k in RADAR_BOOL_PASSTHRU:
        if k in args:
            passthru.append(k)
    if "--names" in args:
        i = args.index("--names")
        names = []
        for nm in args[i + 1:]:
            if nm.startswith("--"):
                break
            names.append(nm)
        if names:
            passthru += ["--names"] + names

    radar = run_radar(passthru)
    out = {
        "generated_at": radar.get("generated_at"),
        "market_session": market_session(),
        "disclaimer": DISCLAIMER,
        "params": radar.get("params", {}),
        "universe_count": radar.get("universe_count", 0),
        "events": radar.get("events", []),
        "suspects": radar.get("suspects", [])[:maxn],
    }
    new = json.dumps(out, ensure_ascii=False, indent=1)

    if not dry:
        # 게시 여부와 무관하게 매 회차 검증용 이력 기록 (push는 radar_backtest가 담당)
        record_history(out)

    old = open(RADAR_JSON, encoding="utf-8").read() if os.path.exists(RADAR_JSON) else ""

    def strip_volatile(s):
        # generated_at만 제외 — market_session(open/closed)은 변경으로 취급해야
        # 마감 후 사이트가 "장중 스캔 중"으로 고착되지 않는다 (하루 최대 2회 push 추가).
        return "\n".join(l for l in s.splitlines() if '"generated_at"' not in l)

    if strip_volatile(new) == strip_volatile(old):
        print(f"변경 없음(수상종목 {len(out['suspects'])}건 동일) — push skip")
        return

    if dry:
        path = "/tmp/radar_preview.json"
        open(path, "w", encoding="utf-8").write(new)
        print(f"[DRY-RUN] 수상종목 {len(out['suspects'])}건, 이벤트 {len(out['events'])}건 → {path}")
        for s in out["suspects"]:
            print(f"  - {s['name']} score={s['suspicion_score']} "
                  f"고가{s['high_pct']}% 현재{s['change_pct']}%")
        return

    os.makedirs(os.path.dirname(RADAR_JSON), exist_ok=True)
    open(RADAR_JSON, "w", encoding="utf-8").write(new)
    git("add", "web/data/radar.json")
    git("commit", "-q", "-m", f"data: 레이더 자동 게시 (수상종목 {len(out['suspects'])}건)")
    # push 전 원격 변경 먼저 통합 (다중 머신 공존)
    pl = git("pull", "--rebase", "--autostash", "origin", "main")
    if pl.returncode != 0:
        sys.stderr.write("pull --rebase 실패(충돌 가능) — 수동 확인 필요:\n" + pl.stderr[-500:])
        git("rebase", "--abort")
        sys.exit(1)
    pr = git("push", "origin", "main")
    if pr.returncode != 0:
        sys.stderr.write("push 실패:\n" + pr.stderr[-500:])
        sys.exit(1)
    print(f"게시 완료: 수상종목 {len(out['suspects'])}건 push")
    for s in out["suspects"]:
        print(f"  - {s['name']} score={s['suspicion_score']}")


if __name__ == "__main__":
    main()
