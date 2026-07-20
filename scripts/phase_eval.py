#!/usr/bin/env python3
"""AI 국면 판정 '클릭 판정' 일일 검증 — 사용자가 종목상세에서 '식음 vs 고점'(국면 판정)을 호출한
모든 종목의 익일 등락을 누적 채점해, "재매집이라 하면 실제로 올랐나? 신뢰도 높을수록 맞나?"를 검증한다.

흐름(장후 cron, ai_click_eval 직후):
  1) Upstash KV: smembers phase:dates → 날짜별 hgetall phase:{date}
     (웹 /api/stock/{code}/phase 가 호출 시 HSETNX로 적재 — 종목·일자당 1건 {phase, confidence})
  2) data/phase_history/{date}.json 에 기록(멱등 — 이미 있으면 보존, 평가결과 불변)
  3) 미평가 기록을 익일 키움 일봉으로 채점. entry=신호일 종가. rose=익일종가>신호일종가.
     방향 적중(hit): 재매집→rose=True면 적중 / 분산→rose=False면 적중 / 중립→None(방향 무판단, 제외).
  4) 국면별 적중률 + 신뢰도 구간별 적중률(calibration) → web/data/phase_performance.json → --push

설계: 표시·검증 전용(core 통계·가중치 튜닝과 무관, 자동 적용 X). ai_click_eval(상승확률)과 별도 표본군
      (국면 판정 클릭 전수 — 종목·일자당 1건 dedup). 국면은 3분류라 확률밴드 대신 방향 적중으로 채점.
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
HIST_DIR = os.path.join(REPO, "data", "phase_history")
PERF_PATH = os.path.join(REPO, "web", "data", "phase_performance.json")
DATES_KEY = "phase:dates"

PHASES = ("재매집", "분산", "중립")
MIN_N = 10  # 국면별·신뢰도 구간 valid 게이트(소표본 단정 방지)
CONF_BANDS = [(0, 60), (60, 70), (70, 80), (80, 101)]  # 신뢰도 구간(높을수록 맞나 검증)
DISCLAIMER = ("사용자가 종목상세에서 'AI 국면 판정'을 호출한 종목의 판정을 익일 등락으로 채점한 "
              "기록입니다. 재매집→상승·분산→하락을 적중으로 보며(중립은 방향 무판단이라 적중 집계 제외), "
              "표시·검증 전용이라 자동 튜닝에 쓰지 않습니다. 약세 단일 레짐·소표본 한계 — 보장이 아닌 참고.")


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
    """hgetall phase:{date} → {code: {phase, confidence}}.
    Upstash REST는 [field,val,...] 평면 리스트가 표준이나 dict로 와도 처리. 빈 키(만료)는 []/None(정상)."""
    r = kv_get(f"hgetall/phase:{date}")
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
            continue  # 당일분은 다음 거래일에 평가
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
            phase = rec.get("phase") if isinstance(rec, dict) else None
            if phase not in PHASES:
                continue  # 알 수 없는 판정값은 기록 안 함(스키마 방어)
            conf = rec.get("confidence") if isinstance(rec, dict) else None
            hist["tracks"][code] = {
                "phase": phase,
                "confidence": round(float(conf)) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None,
                "evaluated": False, "result": None,
            }
            added += 1
        json.dump(hist, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return added


def evaluate():
    """미평가 기록을 익일 일봉으로 채점. entry=신호일 종가. 25일 초과 미평가는 만료."""
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
            rose = nb["close"] > entry
            fell = nb["close"] < entry  # 보합(==)은 rose·fell 모두 False → 양 방향 모두 미적중(대칭)
            phase = t.get("phase")
            # 방향 적중: 재매집=상승 예측(rose)·분산=하락 예측(fell). 보합일을 'not rose'로 잡으면 분산만
            # 적중을 챙겨 편향되므로 fell(strict <)로 — 보합은 양쪽 다 미적중. 중립은 방향 무판단(hit None).
            if phase == "재매집":
                hit = rose
            elif phase == "분산":
                hit = fell
            else:
                hit = None
            t["evaluated"] = True
            t["result"] = {
                "date": nb["date"], "next_close": nb["close"], "entry_close": entry,
                "rose": rose, "hit": hit,
                "return_pct": round((nb["close"] / entry - 1) * 100, 2),
            }
            done += 1
            changed = True
        if changed:
            json.dump(hist, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return done


def _collect():
    """평가 완료 + 판정·신뢰도 있는 표본(날짜순). 만료(result=None)는 제외."""
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
            if not t.get("evaluated") or not r or t.get("phase") not in PHASES:
                continue
            out.append({"date": hist.get("date") or fn[:8], "code": code,
                        "phase": t["phase"], "confidence": t.get("confidence"),
                        "rose": bool(r.get("rose")), "hit": r.get("hit"),
                        "return_pct": r.get("return_pct", 0.0)})
    out.sort(key=lambda x: (x["date"], x["code"]))
    return out


def _by_phase(samples):
    cells = []
    for ph in PHASES:
        grp = [s for s in samples if s["phase"] == ph]
        directional = [s for s in grp if s["hit"] is not None]  # 중립은 hit None
        hits = sum(1 for s in directional if s["hit"])
        rose = sum(1 for s in grp if s["rose"])
        cells.append({
            "phase": ph, "n": len(grp),
            # 방향 적중률(재매집·분산만). 중립은 None.
            "hit_rate": round(hits / len(directional) * 100, 1) if directional else None,
            "rose_rate": round(rose / len(grp) * 100, 1) if grp else None,  # 참고: 익일 상승 비율
            "avg_return": round(sum(s["return_pct"] for s in grp) / len(grp), 2) if grp else None,
            "valid": len(grp) >= MIN_N,
        })
    return cells


def _conf_bands(samples):
    """신뢰도 구간별 방향 적중률 — '신뢰도 높을수록 더 맞나' 검증(재매집·분산만, 중립 제외)."""
    directional = [s for s in samples if s["hit"] is not None and s["confidence"] is not None]
    cells = []
    for lo, hi in CONF_BANDS:
        grp = [s for s in directional if lo <= s["confidence"] < hi]
        hits = sum(1 for s in grp if s["hit"])
        cells.append({
            "lo": lo, "hi": hi, "n": len(grp),
            "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
            "valid": len(grp) >= MIN_N,
        })
    return cells


def write_perf():
    samples = _collect()
    directional = [s for s in samples if s["hit"] is not None]  # 재매집+분산
    n = len(directional)
    hits = sum(1 for s in directional if s["hit"])
    out = {
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "n": n,                    # 방향 채점 표본(중립 제외)
        "total_n": len(samples),   # 중립 포함 전체 채점 표본
        "accuracy": round(hits / n * 100, 1) if n else None,
        "min_n": MIN_N,
        "by_phase": _by_phase(samples),
        "confidence_bands": _conf_bands(samples),
        "recent": [{"date": s["date"], "code": s["code"], "phase": s["phase"],
                    "confidence": s["confidence"], "hit": s["hit"], "rose": s["rose"],
                    "return_pct": s["return_pct"]} for s in samples[-30:]][::-1],
        "disclaimer": DISCLAIMER,
    }
    os.makedirs(os.path.dirname(PERF_PATH), exist_ok=True)
    json.dump(out, open(PERF_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out


def _git(*a):
    import subprocess
    return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)


def push_state():
    os.makedirs(HIST_DIR, exist_ok=True)
    a = _git("add", "--", "data/phase_history", "web/data/phase_performance.json")
    if a.returncode:
        sys.stderr.write("[phase] git add 실패:\n" + a.stderr[-300:])
        sys.exit(1)
    if _git("diff", "--cached", "--quiet").returncode == 0:
        log("[phase] 변경 없음 — push skip")
        return
    c = _git("commit", "-q", "-m", "data: AI 국면 판정 평가 갱신")
    if c.returncode:
        sys.stderr.write("[phase] commit 실패:\n" + c.stderr[-300:])
        sys.exit(1)
    for _ in range(2):
        pl = _git("pull", "--rebase", "--autostash", "origin", "main")
        if pl.returncode:
            sys.stderr.write("[phase] pull --rebase 실패 — abort 후 종료:\n" + pl.stderr[-300:])
            _git("rebase", "--abort")
            sys.exit(1)
        pr = _git("push", "origin", "main")
        if pr.returncode == 0:
            log("[phase] push 완료")
            return
    sys.stderr.write("[phase] push 실패:\n" + pr.stderr[-300:])
    sys.exit(1)


def main():
    load_env()
    git_lock = acquire_git_lock()  # noqa: F841 — 첫 파일 쓰기 전 공용 git 락(타 푸셔와 직렬화)
    done = evaluate()  # 익일 평가 먼저(로컬 history만 사용 — KV 실패해도 진행)
    try:
        dates = kv_dates()
        added = record_dates(dates)
        log(f"[phase] KV 날짜 {len(dates)}개 · 신규 기록 {added}")
    except Exception as e:
        log(f"[phase] KV 읽기 실패(평가만 진행): {e}")
    perf = write_perf()
    log(f"[phase] 평가완료 {done} · 방향표본 {perf['n']}(중립 포함 {perf['total_n']})")
    if "--push" in sys.argv[1:]:
        push_state()


if __name__ == "__main__":
    main()
