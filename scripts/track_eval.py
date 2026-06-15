#!/usr/bin/env python3
"""추적 watchlist 일일 평가 — KV 목록 → 종합판정(룰)+Kimi(AI) 기록 → 익일 실측 → 누가 맞나.

흐름(장후 cron):
  1) Upstash KV(track:watchlist) SMEMBERS로 추적 종목 코드 읽기(READ_ONLY 토큰)
  2) 각 종목: /api/stock/{code}(종합판정·이름·현재가) + /api/stock/{code}/ai(상승확률) 호출 →
     data/track_history/{today}.json 에 기록(entry=당일 현재가)
  3) 미평가 과거 기록을 익일 일봉(kis.daily_prices)으로 평가(익일종가>entry=적중)
  4) web/data/track_performance.json 생성(룰 vs AI 4분면·최근표·추적목록) → --push 시 git
환경변수(.env 또는 web/.env.local): KV_REST_API_URL, KV_REST_API_READ_ONLY_TOKEN(또는 KV_REST_API_TOKEN).
radar performance.json과 분리(별도 파일) — 레이더 통계·튜닝과 독립.
"""
import os
import sys
import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kis_client as kis

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST_DIR = os.path.join(REPO, "data", "track_history")
PERF_PATH = os.path.join(REPO, "web", "data", "track_performance.json")
DEFAULT_BASE = "https://stocknews-cyan.vercel.app"
KEY = "track:watchlist"
RULE_BUY_MIN = 62   # 참고용: '매수 우위' 시작 점수 (scoring.ts). 분류는 아래 BUY_LEVELS로 한다.
# 룰 '매수' 분류는 점수가 아니라 사용자가 실제로 본 판정 level로 한다 — scoring.ts가 점수>=62여도
# 과열(RSI/52주고점/거래소 경고·위험)·관리종목이면 level을 비매수("관망·과열"/"중립")로 오버라이드하므로,
# 점수만 쓰면 사용자가 비매수로 본 종목을 '룰 매수'로 오집계한다(목적: 사용자가 본 판정 vs AI 비교).
BUY_LEVELS = ("강한 매수신호", "매수 우위")
AI_UP_MIN = 58      # Kimi '상승' 임계 (ai.ts 58/42 파생과 정합)
HIGH3_X = 1.03


def log(m):
    print(m, file=sys.stderr, flush=True)


def load_env():
    for name in (".env", os.path.join("web", ".env.local")):
        p = os.path.join(REPO, name)
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def kv_members():
    url = os.environ.get("KV_REST_API_URL")
    tok = os.environ.get("KV_REST_API_READ_ONLY_TOKEN") or os.environ.get("KV_REST_API_TOKEN")
    if not url or not tok:
        raise RuntimeError("KV_REST_API_URL/READ_ONLY_TOKEN가 .env(또는 web/.env.local)에 없습니다")
    req = urllib.request.Request(f"{url.rstrip('/')}/smembers/{KEY}",
                                 headers={"Authorization": f"Bearer {tok}"})
    r = json.load(urllib.request.urlopen(req, timeout=15))
    return sorted({str(c) for c in (r.get("result") or []) if str(c).isdigit() and len(str(c)) == 6})


def fetch_json(path, timeout=90):
    # BASE는 load_env() 이후에 읽어야 .env/web/.env.local의 TRACK_BASE가 반영됨(매 호출 평가)
    base = (os.environ.get("TRACK_BASE") or DEFAULT_BASE).rstrip("/")
    req = urllib.request.Request(f"{base}{path}", headers={"User-Agent": "track-eval"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def record_today(codes):
    """오늘 추적 종목의 종합판정+Kimi 예측 기록. 이미 기록된 종목은 재호출 안 함(재실행 안전)."""
    today = datetime.now(KST).strftime("%Y%m%d")
    os.makedirs(HIST_DIR, exist_ok=True)
    path = os.path.join(HIST_DIR, f"{today}.json")
    hist = {"date": today, "tracks": {}}
    if os.path.exists(path):
        try:
            hist = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            # 손상 파일은 백업 후 재생성 — 조용한 전손/덮어쓰기 대신 흔적(publish.record_history 동일)
            log(f"  [warn] today history 손상 {path}: {e} — .corrupt 백업 후 재생성")
            try:
                os.replace(path, path + ".corrupt")
            except OSError:
                pass
            hist = {"date": today, "tracks": {}}
    hist["date"] = today          # 파일명({today}.json)과 inner date 불변식 — 평가가 신호일 오인 방지
    hist.setdefault("tracks", {})  # 유효하나 tracks 없는 파일에서도 KeyError 방지

    def fetch_ai(code):
        """Kimi 상승확률 → (prob:int|None, dir|None). 실패(503/타임아웃/쿼터)는 None으로.
        thinking 모드면 /ai가 ~280s(maxDuration=300)까지 → 클라 타임아웃을 서버 천장에 맞춰
        느린(주로 가장 흥미로운) 종목의 AI 표본 유실·편향 방지. 기본(thinking off)은 5~20s라
        90s로 충분(radar_backtest와 동일)."""
        ai_to = 300 if os.environ.get("MOONSHOT_THINKING") == "enabled" else 90
        try:
            ai = fetch_json(f"/api/stock/{code}/ai", timeout=ai_to)
        except Exception as e:
            log(f"  [ai-skip] {code}: AI 조회 실패 {e}")
            return None, None
        p = ai.get("probUp")
        return (round(float(p)) if isinstance(p, (int, float)) else None), ai.get("direction")

    added = 0
    for code in codes:
        existing = hist["tracks"].get(code)
        if existing:
            # 룰은 있고 AI만 누락된 같은 날 기록 → AI만 재시도(이미 평가된 건 건드리지 않음)
            if existing.get("ai_prob") is None and not existing.get("evaluated"):
                prob, ai_dir = fetch_ai(code)
                if prob is not None:
                    existing["ai_prob"], existing["ai_dir"] = prob, ai_dir
                    added += 1
                    time.sleep(0.1)
            continue
        try:
            rep = fetch_json(f"/api/stock/{code}")
        except Exception as e:
            log(f"  [skip] {code}: 종합판정 조회 실패 {e}")
            continue
        price = (rep.get("price") or {}).get("close")  # PriceSection.close = 현재/종가
        if not price:   # None/0 모두 채점 불가(거래정지 0종가 등) → entry=0 영구 미평가/거짓적중 방지
            log(f"  [skip] {code}: price 없음/0")
            continue
        verdict = rep.get("verdict") or {}
        # AI는 실패해도 룰 표본은 남긴다(ai_prob=null) — 통계가 null 제외 설계라 룰 검증은 유지
        prob, ai_dir = fetch_ai(code)
        hist["tracks"][code] = {
            "name": rep.get("name") or code,
            "entry": price,                       # 당일 현재가(장후=종가) — 익일 평가 기준
            "verdict_score": verdict.get("score"),
            "verdict_level": verdict.get("level"),
            "ai_prob": prob,
            "ai_dir": ai_dir,
            "evaluated": False, "result": None,
        }
        added += 1
        time.sleep(0.1)
    json.dump(hist, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return len(hist["tracks"]), added


def _signal_and_next(code, date):
    """신호일(date) 봉과 바로 다음 거래일 봉 → (sig, next). 신호일 봉이 조회 윈도우에
    없으면 (None, None) — 휴장일에 stale(전 거래일) 가격으로 만든 표본을 엉뚱한 후일봉에
    오평가/중복 누적하는 것을 막는다(radar_backtest.next_day_bar와 동일 철학). days=40으로
    25일 만료창 + 거래정지 공백에 여유."""
    try:
        bars = kis.daily_prices(code, days=40)
    except Exception:
        return None, None
    # sig/nxt 모두 close가 truthy인 봉만 — 신호일/익일이 거래정지(close=0/누락)면 채점 불가로
    # 보류, entry=0 → hit(nb>0) 거짓양성·ZeroDivision 방지.
    sig = next((b for b in bars if b.get("date") == date and b.get("close")), None)
    if not sig:
        return None, None
    nxt = next((b for b in bars if b.get("date", "") > date and b.get("close")), None)
    return sig, nxt


def evaluate():
    """미평가 추적 기록을 신호일·익일 일봉으로 평가. 신호일 봉이 실제로 있을 때만 채점하고
    entry를 신호일 종가로 재정합(장후 기록가 ≠ 확정 종가 drift 제거). 25일 초과는 만료."""
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
            continue  # 당일분은 익일에 평가(다음 거래일 봉이 아직 없음)
        age = (datetime.strptime(today, "%Y%m%d") - datetime.strptime(date, "%Y%m%d")).days
        changed = False
        for code, t in hist.get("tracks", {}).items():
            if t.get("evaluated") or not t.get("entry"):
                continue
            sig, nb = _signal_and_next(code, date)
            if not sig or not nb:
                if age > 25:   # 신호일 봉이 영영 없음(그날 휴장 등) → 영구 재조회 방지 위해 만료
                    t["evaluated"] = True
                    t["result"] = None
                    changed = True
                continue       # 익일봉 미존재(연휴 등) — 다음 실행에서 재시도
            entry = float(sig["close"])  # _signal_and_next가 close>0 봉만 반환 → entry>0 보장
            ret = (nb["close"] / entry - 1) * 100
            t["evaluated"] = True
            t["result"] = {"date": nb["date"], "next_close": nb["close"], "entry_close": entry,
                           "hit": nb["close"] > entry, "high3": nb["high"] >= entry * HIGH3_X,
                           "return_pct": round(ret, 2)}
            changed = True
            done += 1
        if changed:
            json.dump(hist, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return done


def _collect():
    """평가 완료 추적 표본(날짜순). 만료(expired)는 제외."""
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
            if not t.get("evaluated") or not r or r.get("expired"):
                continue
            out.append({"date": hist.get("date") or fn[:8], "code": code, "name": t.get("name"),
                        "verdict_score": t.get("verdict_score"), "verdict_level": t.get("verdict_level"),
                        "ai_prob": t.get("ai_prob"),
                        "hit": bool(r.get("hit")), "return_pct": r.get("return_pct", 0.0)})
    out.sort(key=lambda x: (x["date"] or "", x["code"]))
    return out


def _prev_tracking():
    """기존 track_performance.json의 추적목록 — KV 실패 시 0 오염 방지용으로 보존."""
    try:
        return (json.load(open(PERF_PATH, encoding="utf-8")) or {}).get("tracking") or []
    except Exception:
        return []


def write_perf(tracked_codes):
    samples = _collect()
    n = len(samples)

    def rate(grp):
        return round(sum(1 for s in grp if s["hit"]) / len(grp) * 100, 1) if grp else None

    # 룰 '매수'는 사용자가 본 판정 level 기준(점수 아님 — 과열/관리 오버라이드 반영). AI는 표시 방향 임계.
    # 판정/예측 누락(거래정지·수집실패 등 None)은 분모 왜곡 방지 위해 제외.
    def is_buy(s):
        return s.get("verdict_level") in BUY_LEVELS
    rule_buy = [s for s in samples if is_buy(s)]
    ai_up = [s for s in samples if s["ai_prob"] is not None and s["ai_prob"] >= AI_UP_MIN]
    # 룰 vs AI 4분면 — 룰 판정(level)과 AI 확률이 둘 다 있는 표본만(None 한쪽이면 4분면서 제외)
    quad_samples = [s for s in samples if s.get("verdict_level") is not None and s["ai_prob"] is not None]
    unknown_n = n - len(quad_samples)  # 룰/AI 한쪽이라도 없어 4분면에 못 넣은 표본(정직한 분모 고지)
    def quad(rb, au):
        g = [s for s in quad_samples
             if is_buy(s) == rb
             and (s["ai_prob"] >= AI_UP_MIN) == au]
        return {"n": len(g), "hit_rate": rate(g), "avg_return":
                round(sum(s["return_pct"] for s in g) / len(g), 2) if g else None}
    out = {
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "n": n,
        "rule_buy": {"n": len(rule_buy), "hit_rate": rate(rule_buy)},   # 종합판정 ≥62일 때 익일 적중률
        "ai_up": {"n": len(ai_up), "hit_rate": rate(ai_up)},            # Kimi ≥58%일 때 익일 적중률
        "rule_buy_min": RULE_BUY_MIN, "ai_up_min": AI_UP_MIN, "min_n": 10,
        "quad_n": len(quad_samples), "unknown_n": unknown_n,  # 4분면 분모·제외 표본 고지
        "divergence": {
            "both": quad(True, True),       # 둘 다 강세 → 가장 신뢰
            "rule_only": quad(True, False), # 룰만 매수(케이뱅크형: 82점인데 AI 관망)
            "ai_only": quad(False, True),
            "neither": quad(False, False),
        },
        "recent": [{"date": s["date"], "name": s["name"], "verdict_score": s["verdict_score"],
                    "ai_prob": s["ai_prob"], "hit": s["hit"], "return_pct": s["return_pct"]}
                   for s in samples[-30:]][::-1],
        "tracking": tracked_codes,
        "disclaimer": "추적 종목의 종합판정(룰)·Kimi(AI) 예측을 익일 종가로 검증한 기록입니다. 투자 참고용.",
    }
    os.makedirs(os.path.dirname(PERF_PATH), exist_ok=True)
    json.dump(out, open(PERF_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out


def _git(*a):
    import subprocess
    return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)


def acquire_git_lock():
    """전 푸셔(publish/radar_backtest/analyzer/track) 공용 git 직렬화 락.
    pull --rebase --autostash가 겹치면 타 프로세스가 쓰는 중인 파일까지 스태시하는 교차
    오염이 가능 — 첫 파일 쓰기 전에 블로킹 획득하고 프로세스 종료까지 핸들을 유지한다."""
    try:
        import fcntl
        fh = open("/tmp/stocknews_git.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX)
        return fh
    except ImportError:
        return None  # fcntl 없는 환경(Windows 등)은 락 생략


def push_state():
    # 공용 git 락은 main()이 첫 파일 쓰기 전에 이미 보유 (이중 flock = 자기 데드락) → 재획득 금지.
    # history 디렉터리가 없으면 git add가 pathspec 오류(rc 128) → 빈 디렉터리라도 만들어 add 성공.
    os.makedirs(HIST_DIR, exist_ok=True)
    a = _git("add", "--", "data/track_history", "web/data/track_performance.json")
    if a.returncode:
        sys.stderr.write("[track] git add 실패:\n" + a.stderr[-300:])
        sys.exit(1)
    if _git("diff", "--cached", "--quiet").returncode == 0:
        log("[track] 변경 없음 — push skip")
        return
    c = _git("commit", "-q", "-m", "data: 추적 종목 평가 갱신")
    if c.returncode:
        sys.stderr.write("[track] commit 실패:\n" + c.stderr[-300:])
        sys.exit(1)
    for _ in range(2):  # 타 푸셔와 경합 시 1회 재시도
        pl = _git("pull", "--rebase", "--autostash", "origin", "main")
        if pl.returncode:
            sys.stderr.write("[track] pull --rebase 실패 — rebase abort 후 종료:\n" + pl.stderr[-300:])
            _git("rebase", "--abort")
            sys.exit(1)
        pr = _git("push", "origin", "main")
        if pr.returncode == 0:
            log("[track] push 완료")
            return
    sys.stderr.write("[track] push 실패:\n" + pr.stderr[-300:])
    sys.exit(1)


def main():
    load_env()
    # 추적 파일(history·performance) 쓰기 전 공용 git 락을 먼저 잡는다 — 락 밖 미커밋 변경을
    # 타 푸셔의 autostash가 스태시/충돌로 날리는 것 방지(radar_backtest와 동일 패턴).
    # 17:30 단독 회차라 대기 비용 미미. 핸들은 프로세스 종료까지 유지(닫히면 락 해제).
    git_lock = acquire_git_lock()  # noqa: F841
    kv_ok = True
    try:
        codes = kv_members()
    except Exception as e:
        log(f"[track] KV 목록 읽기 실패: {e}")
        codes, kv_ok = None, False
    done = evaluate()                       # 익일 평가 먼저(로컬 history만 사용)
    if not kv_ok:
        # KV 일시 실패: 추적목록은 기존 값 보존(0 오염 방지), 평가 통계만 갱신해 게시
        prev = _prev_tracking()
        perf = write_perf(prev)
        log(f"[track] KV 실패 → 추적목록 {len(prev)}개 보존, 평가완료 {done} · 누적표본 {perf['n']}")
    else:
        log(f"[track] 추적 종목 {len(codes)}개")
        total, added = (record_today(codes) if codes else (0, 0))
        perf = write_perf(codes)
        log(f"[track] 오늘 기록 {total}(신규 {added}) · 평가완료 {done} · 누적표본 {perf['n']}")
    if "--push" in sys.argv[1:]:
        push_state()


if __name__ == "__main__":
    main()
