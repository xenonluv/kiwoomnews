#!/usr/bin/env python3
"""자동매매 매매원장(data/autotrade_trades.jsonl) → 통계 집계 → web/data/autotrade_performance.json.

id로 entry↔exit 레그를 페어링해 승률·실현수익(수수료 차감)·사유/유형/시장별·보유기간·누적손익 산출.
표본 부족(n<MIN_N)이면 "수집 중"으로 게이트(체리피킹 방지). 표시 전용·보장 아님.

  python3 scripts/autotrade_stats.py            # 집계 후 web/data/autotrade_performance.json 기록
  python3 scripts/autotrade_stats.py --push     # + 변경 시 git commit/push (Vercel 재빌드)

⚠ 가격 근사: 진입=참조가, 청산=청산판정 시 현재가(실체결가 아님). 시장가·NXT지정가라 소폭 차이 가능.
"""
import os
import sys
import json
import subprocess
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, "data", "autotrade_trades.jsonl")
OUT = os.path.join(REPO, "web", "data", "autotrade_performance.json")
FEE_PCT = 0.3          # 왕복 수수료·세금 근사(%p) — radar strategy_sim과 동일
MIN_N = 20             # 표본 게이트


def _load_events():
    if not os.path.exists(LEDGER):
        return []
    evs = []
    for line in open(LEDGER, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            evs.append(json.loads(line))
        except Exception:
            pass  # 손상 라인 스킵
    return evs


def _classify(reason):
    r = reason or ""
    if "손절" in r:
        return "stop_loss"
    if "2차 익절" in r or "+11" in r:
        return "tp2"
    if "1차 익절" in r:
        return "tp1"
    if "강제청산" in r:
        return "force_exit"
    if "본전" in r:
        return "breakeven"
    return "other"


def _days(entry_date, exit_ts):
    try:
        d0 = datetime.strptime(entry_date, "%Y%m%d")
        d1 = datetime.strptime(exit_ts[:10], "%Y-%m-%d")
        return max(0, (d1 - d0).days)
    except Exception:
        return None


def build_trades(events):
    """id별 entry+exit 레그 → 완결 거래 리스트."""
    by_id = {}
    for e in events:
        by_id.setdefault(e.get("id"), {"entry": None, "exits": []})
        if e.get("type") == "entry":
            by_id[e["id"]]["entry"] = e
        elif e.get("type") == "exit":
            by_id[e["id"]]["exits"].append(e)
    trades = []
    for tid, t in by_id.items():
        en, exits = t["entry"] or {}, t["exits"]
        if not exits:
            continue  # 진입만 있고 청산 없음(미실현) → 통계 제외
        last = exits[-1]
        # 진입 이벤트가 유실돼도 exit 레그가 entry_price·entry_date를 실어 재구성 가능(기록 fail-safe 대비).
        entry_px = en.get("entry_price") or last.get("entry_price") or 0
        sold = sum(x.get("sold_qty") or 0 for x in exits)
        if entry_px <= 0 or sold <= 0:
            continue
        qty0 = en.get("qty") or sold
        wexit = sum((x.get("sold_qty") or 0) * (x.get("exit_price") or 0) for x in exits) / sold
        gross = (wexit - entry_px) / entry_px * 100.0
        net = gross - FEE_PCT
        pnl = sold * (wexit - entry_px) - sold * entry_px * FEE_PCT / 100.0
        edate = last.get("entry_date") or en.get("entry_date")  # exit 레그가 entry_date 보유
        rem = last.get("remaining_qty")
        closed = (rem == 0) if rem is not None else (sold >= qty0)
        trades.append({
            "id": tid, "code": en.get("code") or last.get("code"), "name": en.get("name") or last.get("name"),
            "pattern": en.get("pattern"), "market": en.get("market") or last.get("market"),
            "entry_price": round(entry_px, 1), "exit_price": round(wexit, 1),
            "qty": qty0, "sold_qty": sold, "closed": closed,
            "gross_pct": round(gross, 2), "net_pct": round(net, 2), "pnl_krw": round(pnl),
            "reason": _classify(last.get("reason")), "reason_text": last.get("reason"),
            "entry_date": edate, "exit_ts": last.get("ts"),
            "holding_days": _days(edate, last.get("ts") or ""),
            "win": net > 0,
        })
    return trades


def _grp(trades, key):
    out = {}
    for t in trades:
        k = t.get(key) or "?"
        out.setdefault(k, []).append(t)
    rows = []
    for k, ts in sorted(out.items()):
        n = len(ts)
        rows.append({key: k, "n": n,
                     "win_rate": round(sum(1 for x in ts if x["win"]) / n * 100, 1),
                     "avg_net": round(sum(x["net_pct"] for x in ts) / n, 2)})
    return rows


def build_perf(trades):
    closed = [t for t in trades if t["closed"]]
    n = len(closed)
    def avg(f, arr): return round(sum(f(x) for x in arr) / len(arr), 2) if arr else None
    hold_buckets = {"0d": 0, "1d": 0, "2d": 0, "3d+": 0, "?": 0}
    for t in closed:
        h = t["holding_days"]
        hold_buckets["?" if h is None else ("3d+" if h >= 3 else f"{h}d")] += 1
    summary = {
        "n": n, "total_trades": len(trades), "min_n": MIN_N,
        "status": "OK" if n >= MIN_N else "수집 중",
        "win_rate": round(sum(1 for x in closed if x["win"]) / n * 100, 1) if n else None,
        "avg_gross": avg(lambda x: x["gross_pct"], closed),
        "avg_net": avg(lambda x: x["net_pct"], closed),
        "total_pnl_krw": round(sum(x["pnl_krw"] for x in closed)) if n else 0,
        "best": max((x["net_pct"] for x in closed), default=None),
        "worst": min((x["net_pct"] for x in closed), default=None),
        "fee_pct": FEE_PCT,
    }
    recent = sorted(trades, key=lambda x: x.get("exit_ts") or "", reverse=True)[:20]
    return {
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "summary": summary,
        "by_reason": _grp(closed, "reason"),
        "by_pattern": _grp(closed, "pattern"),
        "by_market": _grp(closed, "market"),
        "holding": [{"bucket": k, "n": v} for k, v in hold_buckets.items() if v or k != "?"],
        "recent": recent,
        "disclaimer": "실계좌 자동매매 실측 성과. 가격은 근사(진입=참조가·청산=판정시 현재가, 실체결가 아님)·수수료 근사 차감. 표본 부족 시 '수집 중'. 보장 아님.",
    }


def write_out(perf):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    json.dump(perf, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)


def git(*a):
    return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)


PERF_REL = "web/data/autotrade_performance.json"


def push():
    """autotrade_performance.json만 커밋/푸시. 공용 락으로 타 푸셔와 직렬화·rebase 충돌 시 안전 복구."""
    import fcntl
    fh = open("/tmp/stocknews_git.lock", "w")
    fcntl.flock(fh, fcntl.LOCK_EX)
    try:
        git("add", PERF_REL)
        if git("diff", "--cached", "--quiet", "--", PERF_REL).returncode == 0:
            print("[stats] 변경 없음 — push 스킵")
            return
        git("commit", "-m", "data: 자동매매 성과 갱신", "--", PERF_REL)  # pathspec: 다른 스테이징 안 딸려감
        pl = git("pull", "--rebase", "--autostash", "origin", "main")
        if pl.returncode != 0:
            git("rebase", "--abort")  # 충돌 시 작업트리를 되돌려 타 푸셔(publish 등) 안 깨짐
            print(f"[stats] pull 충돌 — rebase abort, push 취소: {pl.stderr.strip()}")
            return
        r = git("push", "origin", "main")
        print("[stats] push 완료" if r.returncode == 0 else f"[stats] push 실패: {r.stderr.strip()}")
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)


def _stable(d):
    """as_of(매 회차 변하는 타임스탬프) 제외 — 실질 변경 여부 판정용."""
    return {k: v for k, v in d.items() if k != "as_of"}


def main():
    trades = build_trades(_load_events())
    perf = build_perf(trades)
    # 실질 내용(as_of 제외)이 바뀐 경우에만 기록/푸시 — 매 회차 무의미 커밋·Vercel 재빌드 방지.
    changed = True
    if os.path.exists(OUT):
        try:
            changed = _stable(json.load(open(OUT, encoding="utf-8"))) != _stable(perf)
        except Exception:
            changed = True
    if changed:
        write_out(perf)
    s = perf["summary"]
    print(f"[stats] 완결 {s['n']}건 (총 {s['total_trades']}) 상태={s['status']} "
          f"승률={s['win_rate']} 평균net={s['avg_net']} 누적손익={s['total_pnl_krw']:,}원 "
          f"{'(변경)' if changed else '(변경없음)'} → {OUT}")
    if "--push" in sys.argv:
        if changed:
            push()
        else:
            print("[stats] 실질 변경 없음 — push 스킵")


if __name__ == "__main__":
    main()
