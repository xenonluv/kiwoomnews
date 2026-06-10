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
DISCLAIMER = "본 정보는 투자 참고용이며 매수 추천이 아닙니다. 투자 판단과 책임은 본인에게 있습니다."
RADAR_PASSTHRU = ("--min-value", "--high-pct", "--chg-min", "--chg-max",
                  "--spark-x", "--spark-pct")


def run_radar(extra_args):
    cmd = [sys.executable, os.path.join(REPO, "scripts", "radar.py")] + extra_args
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    if r.stderr:
        sys.stderr.write(r.stderr[-2000:])  # 스킵/경고 증거를 cron 로그에 남김
    if r.returncode != 0 or not r.stdout.strip():
        sys.stderr.write(f"radar 실패 (exit {r.returncode})\n")
        sys.exit(1)
    return json.loads(r.stdout)


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
    maxn = int(args[args.index("--max") + 1]) if "--max" in args else 12

    passthru = []
    for k in RADAR_PASSTHRU:
        if k in args:
            passthru += [k, args[args.index(k) + 1]]
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
