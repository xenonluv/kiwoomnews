#!/usr/bin/env python3
"""AI 심층분석 '클릭 예측' 일일 평가 — 사용자가 'AI분석하기'를 누른 모든 종목의 익일 등락을
누적 채점해 방향 임계(현재 54/46)를 데이터로 보정한다.

흐름(장후 cron, track_eval 직후):
  1) Upstash KV: smembers aipred:dates → 날짜별 hgetall aipred:{date}
     (웹 /api/stock/{code}/ai 가 클릭 시 HSETNX로 적재 — 종목·일자당 1건)
  2) data/ai_click_history/{date}.json 에 기록(멱등 — 이미 있으면 보존, 평가결과 불변)
  3) 미평가 기록을 익일 키움 일봉으로 채점: 익일종가>신호일종가=상승. entry=신호일 종가 재정합.
  4) 확률 구간 보정표 + Brier + 최적 임계 탐색 → web/data/ai_click_performance.json → --push

설계: 표시·참고용(core 통계·가중치 튜닝과 무관). track_eval(추적목록 15종목)과 별도 표본군
      (클릭한 종목 전수 — 단 종목·일자당 1건 dedup, CDN 캐시 미스 시 1회 표집이라 클릭 횟수가 아님).
      임계 적용은 자동 X — 대시보드 권고치를 보고 ai.ts를 수동 변경(재앵커링 방지).
KV 읽기는 READ_ONLY 토큰으로 충분(쓰기는 웹). 시크릿은 .env/web/.env.local.
"""
import os
import sys
import json
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from track_eval import _signal_and_window, acquire_git_lock, load_env  # noqa: E402

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST_DIR = os.path.join(REPO, "data", "ai_click_history")
PERF_PATH = os.path.join(REPO, "web", "data", "ai_click_performance.json")
DATES_KEY = "aipred:dates"

MIN_N = 20          # 구간 보정표 valid 게이트
SWEEP_MIN_N = 30    # 최적 임계 권고 활성 최소 표본(전체)
# 임계 주변(46~60)을 촘촘히 — '몇 %부터 실제로 오르나'를 보기 위함
PROB_BANDS = [(0, 40), (40, 46), (46, 50), (50, 54), (54, 60), (60, 101)]
SWEEP_T = [46, 48, 50, 52, 54, 56, 58, 60]  # "상승 예측 = probUp ≥ T" 후보
CUR_UP_MIN = 54     # ai.ts PROB_BULL_MIN (현재값 — 권고와 대비 표시)
CUR_DOWN_MAX = 46   # ai.ts PROB_BEAR_MAX


def log(m):
    print(m, file=sys.stderr, flush=True)


def kv_get(path):
    url = os.environ.get("KV_REST_API_URL")
    tok = os.environ.get("KV_REST_API_READ_ONLY_TOKEN") or os.environ.get("KV_REST_API_TOKEN")
    if not url or not tok:
        raise RuntimeError("KV_REST_API_URL/READ_ONLY_TOKEN가 .env(또는 web/.env.local)에 없습니다")
    req = urllib.request.Request(f"{url.rstrip('/')}/{path}",
                                 headers={"Authorization": f"Bearer {tok}"})
    return json.load(urllib.request.urlopen(req, timeout=15)).get("result")


def kv_dates():
    r = kv_get(f"smembers/{DATES_KEY}")
    return sorted({str(d) for d in (r or []) if str(d).isdigit() and len(str(d)) == 8})


def kv_hash(date):
    """hgetall aipred:{date} → {code: {probUp,dir,verdictScore}}.
    Upstash REST는 [field,val,...] 평면 리스트가 표준이나, 혹시 dict로 와도 처리(형식 변동 silent 유실 방지).
    빈 키(만료)는 [] 또는 None — 정상(no-op). 예상 밖 형식만 경고."""
    r = kv_get(f"hgetall/aipred:{date}")
    pairs = []
    if isinstance(r, list):
        pairs = [(r[i], r[i + 1]) for i in range(0, len(r) - 1, 2)]
    elif isinstance(r, dict):
        pairs = list(r.items())
    elif r not in (None, []):
        log(f"  [warn] {date} hgetall 예상 밖 형식({type(r).__name__}) — 건너뜀")
    out = {}
    for f, v in pairs:
        code = str(f)
        try:
            out[code] = json.loads(v) if isinstance(v, str) else v
        except (ValueError, TypeError) as e:
            log(f"  [warn] {date} {code} 페이로드 파싱 실패(제외): {e}")
            continue
    return out


def record_dates(dates):
    """KV의 각 날짜를 로컬 history로 동기화. 이미 있는 종목·평가결과는 보존(멱등)."""
    os.makedirs(HIST_DIR, exist_ok=True)
    today = datetime.now(KST).strftime("%Y%m%d")
    added = 0
    for date in dates:
        if date >= today:
            continue  # 당일분은 다음 거래일에 평가 — 미리 받아도 봉이 없음
        path = os.path.join(HIST_DIR, f"{date}.json")
        hist = {"date": date, "tracks": {}}
        if os.path.exists(path):
            try:
                hist = json.load(open(path, encoding="utf-8"))
            except Exception as e:
                log(f"  [warn] history 손상 {path}: {e} — .corrupt 백업 후 재생성")
                try:
                    os.replace(path, path + ".corrupt")
                except OSError:
                    pass
                hist = {"date": date, "tracks": {}}
        hist["date"] = date
        hist.setdefault("tracks", {})
        try:
            members = kv_hash(date)
        except Exception as e:
            log(f"  [skip] {date} KV 조회 실패: {e}")
            continue
        for code, rec in members.items():
            if not (code.isdigit() and len(code) == 6) or code in hist["tracks"]:
                continue
            prob = rec.get("probUp") if isinstance(rec, dict) else None
            hist["tracks"][code] = {
                "ai_prob": round(float(prob)) if isinstance(prob, (int, float)) else None,
                "ai_dir": rec.get("dir") if isinstance(rec, dict) else None,
                "verdict_score": rec.get("verdictScore") if isinstance(rec, dict) else None,
                "evaluated": False, "result": None,
            }
            added += 1
        json.dump(hist, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return added


def evaluate():
    """미평가 기록을 익일 일봉으로 채점(D+1 적중). entry=신호일 종가 재정합. 25일 초과 미평가는 만료."""
    if not os.path.isdir(HIST_DIR):
        return 0
    today = datetime.now(KST).strftime("%Y%m%d")
    done = 0
    for fn in sorted(os.listdir(HIST_DIR)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(HIST_DIR, fn)
        try:
            hist = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        date = hist.get("date") or fn[:8]
        if date >= today:
            continue
        age = (datetime.strptime(today, "%Y%m%d") - datetime.strptime(date, "%Y%m%d")).days
        changed = False
        for code, t in hist.get("tracks", {}).items():
            if t.get("evaluated"):
                continue
            sig, after = _signal_and_window(code, date, span=1)
            if not sig or not after:
                if age > 25:  # 신호일 봉이 영영 없거나 익일봉 미존재 → 영구 재조회 방지(만료)
                    t["evaluated"] = True
                    t.setdefault("result", None)
                    changed = True
                continue
            entry = float(sig["close"])  # close>0 봉만 반환 → entry>0 보장
            nb = after[0]
            t["evaluated"] = True
            t["result"] = {
                "date": nb["date"], "next_close": nb["close"], "entry_close": entry,
                "hit": nb["close"] > entry,
                "return_pct": round((nb["close"] / entry - 1) * 100, 2),
            }
            done += 1
            changed = True
        if changed:
            json.dump(hist, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return done


def _collect():
    """평가 완료 + AI 확률이 있는 표본(날짜순). 만료(result=None)는 제외."""
    out = []
    if not os.path.isdir(HIST_DIR):
        return out
    for fn in sorted(os.listdir(HIST_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            hist = json.load(open(os.path.join(HIST_DIR, fn), encoding="utf-8"))
        except Exception:
            continue
        for code, t in hist.get("tracks", {}).items():
            r = t.get("result")
            if not t.get("evaluated") or not r or t.get("ai_prob") is None:
                continue
            out.append({"date": hist.get("date") or fn[:8], "code": code,
                        "ai_prob": t["ai_prob"], "hit": bool(r.get("hit")),
                        "return_pct": r.get("return_pct", 0.0)})
    out.sort(key=lambda x: (x["date"], x["code"]))
    return out


def _bands(samples):
    cells = []
    for lo, hi in PROB_BANDS:
        grp = [s for s in samples if lo <= s["ai_prob"] < hi]
        hits = sum(1 for s in grp if s["hit"])
        cells.append({
            "lo": lo, "hi": hi, "n": len(grp),
            "avg_prob": round(sum(s["ai_prob"] for s in grp) / len(grp), 1) if grp else None,
            "actual_rate": round(hits / len(grp) * 100, 1) if grp else None,
            "valid": len(grp) >= MIN_N,
        })
    return cells


def _sweep(samples):
    """후보 임계 T별 '상승 예측=probUp≥T'의 정밀도/재현율/정확도/균형정확도. 권고=균형정확도 최대."""
    n = len(samples)
    pos = sum(1 for s in samples if s["hit"])      # 실제 상승
    neg = n - pos                                  # 실제 하락/보합
    rows = []
    best = None
    for t in SWEEP_T:
        tp = sum(1 for s in samples if s["ai_prob"] >= t and s["hit"])
        fp = sum(1 for s in samples if s["ai_prob"] >= t and not s["hit"])
        fn_ = pos - tp
        tn = neg - fp
        pred_up = tp + fp
        precision = round(tp / pred_up * 100, 1) if pred_up else None      # 상승예측 적중률
        recall = round(tp / pos * 100, 1) if pos else None                 # 실제상승 포착률
        tnr = (tn / neg) if neg else None
        bal = round((tp / pos + tn / neg) / 2 * 100, 1) if pos and neg else None  # 균형정확도
        accuracy = round((tp + tn) / n * 100, 1) if n else None
        row = {"t": t, "n_pred_up": pred_up, "precision": precision, "recall": recall,
               "tnr": round(tnr * 100, 1) if tnr is not None else None,
               "balanced_acc": bal, "accuracy": accuracy}
        rows.append(row)
        if bal is not None and (best is None or bal > best["balanced_acc"]):
            best = row
    recommended = best["t"] if (best is not None and n >= SWEEP_MIN_N) else None
    return {"rows": rows, "recommended_up_min": recommended,
            "current_up_min": CUR_UP_MIN, "current_down_max": CUR_DOWN_MAX,
            "min_n": SWEEP_MIN_N, "pos": pos, "neg": neg}


def write_perf():
    samples = _collect()
    n = len(samples)
    hits = sum(1 for s in samples if s["hit"])
    brier = round(sum((s["ai_prob"] / 100 - (1 if s["hit"] else 0)) ** 2 for s in samples) / n, 4) \
        if n else None
    out = {
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "n": n,
        "hit_rate": round(hits / n * 100, 1) if n else None,
        "avg_prob": round(sum(s["ai_prob"] for s in samples) / n, 1) if n else None,
        "brier": brier,
        "min_n": MIN_N,
        "prob_bands": _bands(samples),
        "threshold_sweep": _sweep(samples),
        "recent": [{"date": s["date"], "code": s["code"], "ai_prob": s["ai_prob"],
                    "hit": s["hit"], "return_pct": s["return_pct"]} for s in samples[-30:]][::-1],
        "disclaimer": "사용자가 'AI분석하기'를 누른 모든 종목의 AI 상승확률을 익일 등락으로 채점한 "
                      "기록입니다. 확률 구간별 실측 상승률·최적 임계는 표시·참고용이며 자동 적용되지 "
                      "않습니다(권고치 확인 후 수동 조정). 약세 단일 레짐 표본 한계.",
    }
    os.makedirs(os.path.dirname(PERF_PATH), exist_ok=True)
    json.dump(out, open(PERF_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out


def _git(*a):
    import subprocess
    return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)


def push_state():
    os.makedirs(HIST_DIR, exist_ok=True)
    a = _git("add", "--", "data/ai_click_history", "web/data/ai_click_performance.json")
    if a.returncode:
        sys.stderr.write("[ai-click] git add 실패:\n" + a.stderr[-300:])
        sys.exit(1)
    if _git("diff", "--cached", "--quiet").returncode == 0:
        log("[ai-click] 변경 없음 — push skip")
        return
    c = _git("commit", "-q", "-m", "data: AI 클릭 예측 평가 갱신")
    if c.returncode:
        sys.stderr.write("[ai-click] commit 실패:\n" + c.stderr[-300:])
        sys.exit(1)
    for _ in range(2):
        pl = _git("pull", "--rebase", "--autostash", "origin", "main")
        if pl.returncode:
            sys.stderr.write("[ai-click] pull --rebase 실패 — abort 후 종료:\n" + pl.stderr[-300:])
            _git("rebase", "--abort")
            sys.exit(1)
        pr = _git("push", "origin", "main")
        if pr.returncode == 0:
            log("[ai-click] push 완료")
            return
    sys.stderr.write("[ai-click] push 실패:\n" + pr.stderr[-300:])
    sys.exit(1)


def main():
    load_env()
    git_lock = acquire_git_lock()  # noqa: F841 — 첫 파일 쓰기 전 공용 git 락(타 푸셔와 직렬화)
    done = evaluate()  # 익일 평가 먼저(로컬 history만 사용 — KV 실패해도 진행)
    try:
        dates = kv_dates()
        added = record_dates(dates)
        log(f"[ai-click] KV 날짜 {len(dates)}개 · 신규 기록 {added}")
    except Exception as e:
        log(f"[ai-click] KV 읽기 실패(평가만 진행): {e}")
    perf = write_perf()
    log(f"[ai-click] 평가완료 {done} · 누적표본 {perf['n']}")
    if "--push" in sys.argv[1:]:
        push_state()


if __name__ == "__main__":
    main()
