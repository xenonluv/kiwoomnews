#!/usr/bin/env python3
"""게시 자동화 (Tier A) — 스크리너 → 결정론 스코어 → signals.json → 변경 시에만 push.

자동 cron 안정성을 위해 LLM(Codex) 없이 순수 Python으로 동작.
(Codex 팀원3 심층분석은 수동/별도 잡으로 운용)

사용:
  python3 scripts/publish.py --dry-run                     # /tmp에 미리보기, push 안 함
  python3 scripts/publish.py --vol-x 1.5 --gain 3 --max 6  # 실제 게시
cron(장중 15분): */15 9-15 * * 1-5  cd ~/stocknews && python3 scripts/publish.py >> /tmp/publish.log 2>&1
"""
import os
import sys
import json
import subprocess
from datetime import datetime, timezone, timedelta

from team3_price_context import compute_context

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNALS = os.path.join(REPO, "web", "data", "signals.json")
DISCLAIMER = "본 정보는 투자 참고용이며 매수 추천이 아닙니다. 투자 판단과 책임은 본인에게 있습니다."

# 결정론 스코어 가중치 (투명)
PHASE_ADJ = {"저점": 12, "눌림목": 10, "박스": 0, "과다상승": -12, "분석불가": -5}


def run_screener(extra_args):
    cmd = [sys.executable, os.path.join(REPO, "scripts", "screener.py")] + extra_args
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    if not r.stdout.strip():
        sys.stderr.write("스크리너 출력 없음:\n" + r.stderr[-500:])
        sys.exit(1)
    return json.loads(r.stdout)


def score(importance, phase, gc_recent, disp, sentiment):
    """상승확률 결정론 스코어(0~100). 재료강도+일봉국면+3분봉타이밍+감성."""
    p = 45 + min(15.0, (importance or 5) * 1.5) + PHASE_ADJ.get(phase, 0)
    if gc_recent and disp is not None and abs(disp) <= 1.0:
        p += 8  # 단기 진입 타이밍(갓 골든크로스)
    p += 5 if sentiment == "호재" else (-10 if sentiment == "악재" else 0)
    return max(10, min(95, round(p)))


def _post(r, tier, stamp_full, today):
    code, name = r["code"], r["name"]
    ctx = compute_context(code, name)
    phase = ctx.get("market_status_hint", "분석불가")
    c = r.get("C", {})
    disp = c.get("disparity_pct")
    prob = score(r.get("importance"), phase, c.get("gc_recent"), disp, r.get("sentiment"))
    news = r.get("news", [])
    headline = (news[0]["title"][:60] if news else f"{name} 스크리너 포착")
    if tier == "signal":
        gc_txt = "갓 골든크로스" if c.get("gc_recent") else "정배열"
        summary = (f"[시그널] 일봉 {phase} · 3분봉 {gc_txt}(이격도 {disp}%) · "
                   f"재료 {r.get('sentiment')}(중요도 {r.get('importance')}). 일봉 국면도 함께 확인.")
    else:
        summary = (f"[후보] 재료+거래대금 포착(중요도 {r.get('importance')}, {r.get('sentiment')}) · "
                   f"일봉 {phase}. 3분봉 골든크로스 진입신호는 아직 대기 중.")
    return {
        "post_id": f"POST_{today}_{code}",
        "status": "PUBLISHED",
        "tier": tier,
        "target_stock": name,
        "signal_probability": f"{prob}%",
        "position_type": phase,
        "headline": headline,
        "summary": summary,
        "disclaimer": DISCLAIMER,
        "published_at": stamp_full,
        "news": [
            {"title": n.get("title"), "url": n.get("url"),
             "office": n.get("office"), "sentiment": n.get("sentiment")}
            for n in news[:6] if n.get("title")
        ],
    }


def build_posts(scr, maxn, max_cand, news_min):
    stamp_full = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    today = datetime.now(KST).strftime("%Y%m%d")
    posts = []
    seen = set()
    # Tier 1: 시그널 (A+B+C 통과)
    for r in scr.get("passed", [])[:maxn]:
        posts.append(_post(r, "signal", stamp_full, today))
        seen.add(r["code"])
    # Tier 2: 후보 (A+B 통과, C 대기) — by_sector에서 재료(B) 통과분, 중요도순
    cands = []
    for recs in scr.get("by_sector", {}).values():
        for r in recs:
            if r.get("A_hit") and r["code"] not in seen and (r.get("B_news") or 0) >= news_min:
                cands.append(r)
                seen.add(r["code"])
    cands.sort(key=lambda r: -(r.get("importance") or 0))
    for r in cands[:max_cand]:
        posts.append(_post(r, "candidate", stamp_full, today))
    return posts


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    maxn = int(args[args.index("--max") + 1]) if "--max" in args else 6
    max_cand = int(args[args.index("--max-candidates") + 1]) if "--max-candidates" in args else 8
    news_min = int(args[args.index("--news-min") + 1]) if "--news-min" in args else 2
    # 스크리너에 전달할 임계값(기본: 느슨)
    passthru = []
    for k in ("--vol-x", "--gain", "--news-min", "--gc-window", "--disp-max", "--topn", "--min-value"):
        if k in args:
            passthru += [k, args[args.index(k) + 1]]
    if not passthru:
        passthru = ["--vol-x", "1.5", "--gain", "3.0", "--news-min", "2",
                    "--gc-window", "40", "--disp-max", "2.0", "--topn", "20"]
    # 관심종목(watchlist)을 스크리너 유니버스에 포함 (랭킹에 안 잡혀도 평가)
    if "--names" in args:
        i = args.index("--names")
        names = []
        for nm in args[i + 1:]:
            if nm.startswith("--"):
                break
            names.append(nm)
        if names:
            passthru += ["--names"] + names

    scr = run_screener(passthru)
    posts = build_posts(scr, maxn, max_cand, news_min)
    new = json.dumps(posts, ensure_ascii=False, indent=2)

    if not posts:
        print("게시할 통과 종목 없음 — signals.json 유지, skip")
        return

    old = open(SIGNALS, encoding="utf-8").read() if os.path.exists(SIGNALS) else ""
    # post_id/내용만 비교(시각은 매번 바뀌므로 제외)
    def strip_time(s):
        return "\n".join(l for l in s.splitlines() if "published_at" not in l)
    if strip_time(new) == strip_time(old):
        print(f"변경 없음({len(posts)}건 동일) — push skip")
        return

    if dry:
        out = "/tmp/publish_preview.json"
        open(out, "w", encoding="utf-8").write(new)
        print(f"[DRY-RUN] {len(posts)}건 → {out} (push 안 함)")
        for p in posts:
            print(f"  - {p['target_stock']} {p['signal_probability']} {p['position_type']}")
        return

    open(SIGNALS, "w", encoding="utf-8").write(new)
    git("add", "web/data/signals.json")
    git("commit", "-q", "-m", f"data: 시그널 자동 게시 ({len(posts)}건)")
    pr = git("push", "-q", "origin", "main")
    print(f"게시 완료: {len(posts)}건 push (exit {pr.returncode})")
    for p in posts:
        print(f"  - {p['target_stock']} {p['signal_probability']} {p['position_type']}")


if __name__ == "__main__":
    main()
