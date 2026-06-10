#!/usr/bin/env python3
"""이벤트 매집 레이더 — "수상한 종목" 스캐너.

목적: 10일 이내 이벤트를 앞두고 당일 큰돈이 들어와 급등 후 식은(매집 의심) 종목 탐지.

깔때기 (목적.md 6조건):
  [유니버스] 네이버 up/down 랭킹 전수 스캔 → 등락률 밴드 + 당일 거래대금 ≥ 700억 게이트
  [조건3] 당일 고가 등락률 ≥ +13% 찍고 현재 고가 아래 (폭락/후퇴 중)   ← KIS 현재가
  [조건6] 현재 등락률 -6% ~ +10%                                   ← KIS 현재가
  [조건4] 현재가 ≥ 일봉 10일선                                      ← KIS 일봉
  [조건2] 당일 분봉 스파크 (거래량 중앙값 N배 + 가격 점프)            ← KIS 분봉
  [수급]  외국인/기관 순매수 매집 신호 (가점)                         ← KIS 수급
  [조건1·5] 이벤트 캘린더 × 뉴스 민감도 → event_calendar/theme_map (가점)

사용 (WSL):
  python3 scripts/radar.py                          # 기본 임계값
  python3 scripts/radar.py --min-value 70000000000 --high-pct 13 --names 한온시스템
출력: stdout JSON {generated_at, params, suspects[]}
"""
import sys
import json
import math
import argparse
from datetime import datetime, timezone, timedelta

from net import get_bytes
from team1_collect import resolve_code, fetch_news, is_individual_stock, UA
from team2_relevance import score_news, make_aliases
from event_calendar import upcoming_events
from theme_map import match_events
import kis_client as kis

KST = timezone(timedelta(hours=9))

# ---- 기본 임계값 ----
MIN_VALUE = 70_000_000_000   # 당일 거래대금 ≥ 700억 (원)
HIGH_PCT = 13.0              # 당일 고가 등락률 하한 (%)
CHG_MIN, CHG_MAX = -6.0, 10.0  # 현재 등락률 범위 (%)
SPARK_VOL_X = 8.0            # 분봉 거래량 / 당일 중앙값 배수
SPARK_PCT = 0.8              # 분봉 등락 절대값 (%)


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ---------- 조건 2: 분봉 스파크 ----------

def detect_sparks(bars, vol_x=SPARK_VOL_X, pct=SPARK_PCT):
    """당일 1분봉에서 거래량 스파크 클러스터 추출.

    스파크 = 거래량 ≥ 당일 중앙값×vol_x AND |봉 등락| ≥ pct%.
    개장 직후(~09:10)는 원래 거래량이 크므로 임계 1.5배 가중.
    연속 분봉은 1개 클러스터로 묶는다.
    """
    if len(bars) < 30:
        return []
    vols = sorted(b["vol"] for b in bars if b["vol"] > 0)
    if not vols:
        return []
    median = vols[len(vols) // 2]
    if median <= 0:
        return []
    clusters = []
    cur = None
    prev_close = None
    for b in bars:
        chg = ((b["close"] / prev_close - 1) * 100) if prev_close else 0.0
        prev_close = b["close"] or prev_close
        x = b["vol"] / median
        need = vol_x * (1.5 if b["time"] <= "091000" else 1.0)
        if x >= need and abs(chg) >= pct:
            if cur is None:
                cur = {"time": b["time"][:4], "vol_x": x, "pct": chg, "n": 1}
            else:
                cur["n"] += 1
                cur["vol_x"] = max(cur["vol_x"], x)
                cur["pct"] += chg
        elif cur is not None:
            clusters.append(cur)
            cur = None
    if cur is not None:
        clusters.append(cur)
    return [{"time": f"{c['time'][:2]}:{c['time'][2:]}",
             "vol_x": round(c["vol_x"], 1),
             "pct": round(c["pct"], 2),
             "minutes": c["n"]} for c in clusters]


# ---------- 수급: 외국인/기관 매집 신호 ----------

def accumulation_signal(inv, days=5):
    """최근 N일 외국인+기관 순매수 → {days_net_buy, today, streak}."""
    if not inv:
        return {"net_days": 0, "today_buy": False, "streak": 0, "detail": []}
    recent = inv[-days:]
    detail = [{"date": r["date"][4:], "frgn": int(r["frgn"]), "orgn": int(r["orgn"])}
              for r in recent]
    net_days = sum(1 for r in recent if r["frgn"] + r["orgn"] > 0)
    streak = 0
    for r in reversed(recent):
        if r["frgn"] + r["orgn"] > 0:
            streak += 1
        else:
            break
    today_buy = bool(recent) and (recent[-1]["frgn"] + recent[-1]["orgn"] > 0)
    return {"net_days": net_days, "today_buy": today_buy, "streak": streak,
            "detail": detail}


# ---------- 수상함 점수 (결정론, 투명 가중합) ----------

def suspicion_score(spark_clusters, fade_pct, ma10_margin_pct, acc, event_score=0.0,
                    vol_x_base=SPARK_VOL_X):
    """0~100. 각 항목 근거는 breakdown으로 공개."""
    bd = {}
    bd["base"] = 30
    # 스파크 강도: 최대 클러스터 배수. vol_x 기준치→0점, 기준치×4→15점 선형
    max_x = max((c["vol_x"] for c in spark_clusters), default=0.0)
    bd["spark"] = round(min(15.0, max(0.0, (max_x - vol_x_base) / (vol_x_base * 3) * 15)), 1)
    # 페이드 품질: 고가 상승분 대비 후퇴율 30~70%가 매집형 정점(15점), 0%/100%로 갈수록 감소
    f = fade_pct
    if f <= 0:
        bd["fade"] = 0.0
    elif f >= 95:
        bd["fade"] = 2.0  # 사실상 전량 반납 — 매집보다 털기에 가까움
    else:
        bd["fade"] = round(15.0 * max(0.0, 1.0 - abs(f - 50.0) / 50.0), 1)
    # 10일선 여유: 0~8% 마진 → 0~10점
    bd["ma10"] = round(min(10.0, max(0.0, ma10_margin_pct / 8.0 * 10)), 1)
    # 수급: 최근 5일 중 순매수일 수(×2) + 당일 순매수(+5)
    bd["flow"] = round(min(15.0, acc["net_days"] * 2 + (5 if acc["today_buy"] else 0)), 1)
    # 이벤트 근접 × 민감도 (theme_map.match_events 가점)
    bd["event"] = round(min(15.0, event_score), 1)
    total = int(round(sum(bd.values())))
    return max(0, min(100, total)), bd


# ---------- 메인 깔때기 ----------

def scan_one(name, code, p, events):
    """단일 종목 전체 판정. 조건 미달 None / 데이터 오류 "ERR" / 통과 suspect dict."""
    try:
        now = kis.price_now(code)
    except Exception as e:
        log(f"  [skip] {name}: 현재가 조회 실패 {e}")
        return "ERR"
    if not now["price"] or not now["prev_close"]:
        return None

    # 거래대금 게이트 (KIS 값으로 정확 재확인)
    if now["value"] < p.min_value:
        return None
    # 조건 3: 고가 +13% 이상 찍고 현재 고가 아래
    high_pct = (now["high"] / now["prev_close"] - 1) * 100
    if high_pct < p.high_pct or now["price"] >= now["high"]:
        return None
    # 조건 6: 현재 등락률 범위
    if not (p.chg_min <= now["change_pct"] <= p.chg_max):
        return None

    # 조건 4: 일봉 10일선
    try:
        daily = kis.daily_prices(code, days=15)
    except Exception as e:
        log(f"  [skip] {name}: 일봉 실패 {e}")
        return "ERR"
    closes = [d["close"] for d in daily]
    if len(closes) < 10:
        return None
    ma10 = sum(closes[-10:]) / 10
    if now["price"] < ma10:
        return None

    # 조건 2: 분봉 스파크
    try:
        bars = kis.minute_bars_today(code)
    except Exception as e:
        log(f"  [skip] {name}: 분봉 실패 {e}")
        return "ERR"
    sparks = detect_sparks(bars, p.spark_x, p.spark_pct)
    if not sparks:
        return None

    # 수급 (실패해도 진행 — 가점 항목)
    try:
        acc = accumulation_signal(kis.investor_daily(code))
    except Exception:
        acc = {"net_days": 0, "today_buy": False, "streak": 0, "detail": []}

    # 재료 뉴스 + 이벤트 민감도 매칭 (조건 5)
    news_items, raw_titles = [], []
    try:
        raw = [n for n in fetch_news(code, 10) if n.get("title")]
        raw_titles = [n["title"] for n in raw]
        scored = score_news(raw, make_aliases(name))
        news_items = scored.get("relevant", [])[:6]
    except Exception:
        pass
    matched_events, event_score = match_events(events, raw_titles, now["sector"])

    fade_pct = (now["high"] - now["price"]) / (now["high"] - now["prev_close"]) * 100
    ma10_margin = (now["price"] / ma10 - 1) * 100
    score, breakdown = suspicion_score(sparks, fade_pct, ma10_margin, acc,
                                       event_score, p.spark_x)

    return {
        "code": code,
        "name": name,
        "sector": now["sector"],
        "suspicion_score": score,
        "score_breakdown": breakdown,
        "price": now["price"],
        "change_pct": round(now["change_pct"], 2),
        "high_pct": round(high_pct, 2),
        "fade_pct": round(fade_pct, 1),
        "value_eok": round(now["value"] / 1e8),
        "ma10": round(ma10, 1),
        "ma10_margin_pct": round(ma10_margin, 2),
        "spark": {"clusters": sparks},
        "flow": acc,
        "news": news_items,
        "matched_events": matched_events,
    }


def _rank_page(direction, market, page):
    url = (f"https://m.stock.naver.com/api/stocks/{direction}/{market}"
           f"?page={page}&pageSize=100")
    d = json.loads(get_bytes(url, UA))
    if "stocks" not in d:  # 응답 스키마 변화 — 조용한 빈 결과 대신 명시 실패
        raise RuntimeError(f"네이버 랭킹 응답에 stocks 없음: {direction}/{market}")
    rows = []
    for s in d.get("stocks", []):
        name, code = s.get("stockName"), s.get("itemCode") or s.get("reutersCode")
        if not is_individual_stock(name, code, s.get("stockEndType")):
            continue
        try:
            rate = float(str(s.get("fluctuationsRatio", "0")).replace(",", ""))
            value_mn = float(str(s.get("accumulatedTradingValue", "0")).replace(",", ""))
        except ValueError:
            continue
        rows.append({"name": name, "code": code, "change_pct": rate, "value_mn": value_mn})
    return rows, int(d.get("totalCount") or 0)


def build_universe(chg_min, chg_max, min_value_mn, names):
    """등락률 밴드 내 + 거래대금 게이트 통과 종목 전수 수집 (네이버 up/down 풀스캔).

    up: 상승 종목 전 페이지. down: 마지막 페이지부터 역방향(완만한 하락 → 큰 하락),
    페이지 전체가 chg_min 미만이 되면 중단.
    """
    seen = {}

    def keep(r):
        if not (chg_min <= r["change_pct"] <= chg_max):
            return
        if r["value_mn"] < min_value_mn:
            return
        seen.setdefault(r["code"], r)

    for market in ("KOSPI", "KOSDAQ"):
        try:
            rows, total = _rank_page("up", market, 1)
            for r in rows:
                keep(r)
            for page in range(2, math.ceil(total / 100) + 1):
                rows, _ = _rank_page("up", market, page)
                for r in rows:
                    keep(r)
        except Exception as e:
            log(f"[warn] {market} up 랭킹 실패: {e}")
        try:
            rows, total = _rank_page("down", market, 1)
            for r in rows:
                keep(r)
            for page in range(math.ceil(total / 100), 1, -1):  # 역방향
                rows, _ = _rank_page("down", market, page)
                for r in rows:
                    keep(r)
                if rows and all(r["change_pct"] < chg_min for r in rows):
                    break
        except Exception as e:
            log(f"[warn] {market} down 랭킹 실패: {e}")

    for nm in names or []:
        code = resolve_code(nm)
        if code and code not in seen:
            seen[code] = {"name": nm, "code": code, "change_pct": None, "value_mn": None}
    return list(seen.values())


def main():
    ap = argparse.ArgumentParser(description="이벤트 매집 레이더")
    ap.add_argument("--min-value", type=float, default=MIN_VALUE, help="당일 거래대금 하한(원)")
    ap.add_argument("--high-pct", type=float, default=HIGH_PCT, help="당일 고가 등락률 하한(%%)")
    ap.add_argument("--chg-min", type=float, default=CHG_MIN)
    ap.add_argument("--chg-max", type=float, default=CHG_MAX)
    ap.add_argument("--spark-x", type=float, default=SPARK_VOL_X, help="분봉 거래량 중앙값 배수")
    ap.add_argument("--spark-pct", type=float, default=SPARK_PCT, help="분봉 등락 하한(%%)")
    ap.add_argument("--names", nargs="*", default=[], help="watchlist 강제 포함")
    p = ap.parse_args()

    # 조건 1: D-10 이벤트 캘린더
    events = upcoming_events(10)
    log(f"[radar] D-10 이벤트 {len(events)}건")

    # 1차 게이트: 네이버 전수 스캔에서 등락률 밴드 + 거래대금 게이트 (정확값)
    candidates = build_universe(p.chg_min, p.chg_max, p.min_value / 1e6, p.names)
    if not candidates:
        # 정상 장에선 700억+ 종목이 밴드 안에 항상 수십 개 — 0이면 수집 장애로 본다
        log("[error] 1차 게이트 통과 0종목 — 네이버 랭킹 수집 장애 의심, 중단")
        sys.exit(2)
    log(f"[radar] 1차 게이트 통과 {len(candidates)}종목 → KIS 정밀 판정")

    suspects = []
    err_count = 0
    for s in candidates:
        r = scan_one(s["name"], s["code"], p, events)
        if r == "ERR":
            err_count += 1
        elif r:
            log(f"  [HIT] {s['name']} score={r['suspicion_score']} "
                f"고가{r['high_pct']}% 현재{r['change_pct']}% 페이드{r['fade_pct']}%")
            suspects.append(r)
    # 데이터 오류 비율 가드 — KIS 키 부재/토큰 장애/부분 장애 시 거짓 '빈 레이더' 게시 방지
    # (정상 장에서도 거래정지 등으로 1~2건 ERR는 가능하므로 3건 미만은 허용)
    if err_count >= max(3, int(len(candidates) * 0.3)):
        log(f"[error] 조회 실패 {err_count}/{len(candidates)}종목 — KIS 장애 의심, 게시 중단")
        sys.exit(3)
    suspects.sort(key=lambda x: -x["suspicion_score"])

    out = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "params": {"min_value_eok": round(p.min_value / 1e8), "high_pct": p.high_pct,
                   "chg_range": [p.chg_min, p.chg_max], "spark_x": p.spark_x,
                   "spark_pct": p.spark_pct},
        "universe_count": len(candidates),
        "events": events,
        "suspects": suspects,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
