#!/usr/bin/env python3
"""이벤트 매집 레이더 — "수상한 종목" 스캐너.

목적: 10일 이내 이벤트를 앞두고 당일 큰돈이 들어와 급등 후 식은(매집 의심) 종목 탐지.

깔때기 (목적.md 6조건):
  [유니버스] 시장별(코스피/코스닥) 거래대금 top20(KIS 공식 순위) + 등락률 top20(네이버 up 랭킹)
             합집합 → 등락률 밴드. 거래대금 700억 게이트는 정밀판정(scan_one)에서 적용.
             (KIS 장애 시 기존 네이버 전수 스캔으로 자동 폴백)
  [조건3] 당일 고가 등락률 ≥ +13% (공통 전제) 후 2트랙 분기          ← KIS 현재가
    · fade: 현재 고가 아래 + 등락률 -6~+10% (급등 후 식음, 기존)
    · shakeout: 등락률 ≤ +30% + 분봉상 고점 대비 -10%+ 눌림 후 낙폭 30%+ 회복 (눌림 후 재상승)
  [조건6] 현재 등락률 밴드 — 트랙별 상이 (위 참조)                    ← KIS 현재가
  [조건4] 현재가 ≥ 일봉 10일선                                      ← KIS 일봉
  [조건2] 당일 분봉 스파크 (거래량 중앙값 N배 + 가격 점프)            ← KIS 분봉
  [수급]  외국인/기관 순매수 매집 신호 (가점)                         ← KIS 수급
  [조건1·5] 이벤트 캘린더 × 뉴스 민감도 → event_calendar/theme_map (가점)

사용 (WSL):
  python3 scripts/radar.py                          # 기본 임계값
  python3 scripts/radar.py --min-value 70000000000 --high-pct 13 --names 한온시스템
출력: stdout JSON {generated_at, params, suspects[]}
"""
import os
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
import kimi_client

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEIGHTS_PATH = os.path.join(REPO, "data", "radar_weights.json")
PERF_PATH = os.path.join(REPO, "web", "data", "performance.json")

# ---- 기본 임계값 ----
MIN_VALUE = 70_000_000_000   # 당일 거래대금 ≥ 700억 (원)
HIGH_PCT = 13.0              # 당일 고가 등락률 하한 (%)
CHG_MIN, CHG_MAX = -6.0, 10.0  # 현재 등락률 범위 (%) — 기존 fade 밴드
SPARK_VOL_X = 8.0            # 분봉 거래량 / 당일 중앙값 배수
SPARK_PCT = 0.8              # 분봉 등락 절대값 (%)
MEGA_SPARK_X = 40.0          # 메가 스파크: 최대 클러스터 배수 임계 (실측: HPSP 136x, 스피어 44x)
MEGA_BONUS = 12              # 메가 스파크 × 당일 수급매수 동반 시 표시 점수 가점 (raw 불변)


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
        if not b["close"]:
            continue  # 거래 없는 봉(close=0)은 -100% 거짓 등락 방지 위해 스킵
        chg = ((b["close"] / prev_close - 1) * 100) if prev_close else 0.0
        prev_close = b["close"]
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


# ---------- 패턴 2: 흔들기(눌림) 후 재상승 ----------

def detect_shakeout(bars, shake_pct=10.0, recover_ratio=0.30):
    """당일 분봉에서 '장중 고점 → 큰 눌림 → 재상승' 패턴 감지.

    러닝 하이 추적 중 드로다운(고점 대비 하락폭)이 shake_pct% 이상 발생했고,
    마지막 가격이 그 낙폭(고점−저점)의 recover_ratio 이상을 되돌렸으면 성립.
    예: 고점 +23% → −12% 눌림 → 낙폭 60% 회복하며 재상승 중 (원익IPS형).
    반환: {depth_pct, recovery_pct, high_time, trough_time} 또는 None.
    """
    closes = [(b["time"], b["close"]) for b in bars if b["close"]]
    if len(closes) < 30:
        return None
    run_high, run_high_t = closes[0][1], closes[0][0]
    best = None  # 가장 깊은 드로다운 (고점·저점·시각)
    trough, trough_t = run_high, run_high_t
    for t, c in closes[1:]:
        if c > run_high:
            run_high, run_high_t = c, t
            trough, trough_t = c, t  # 신고가 갱신 → 드로다운 추적 리셋
        elif c < trough:
            trough, trough_t = c, t
            depth = (run_high - trough) / run_high * 100
            if best is None or depth > best["depth"]:
                best = {"high": run_high, "high_t": run_high_t,
                        "trough": trough, "trough_t": trough_t, "depth": depth}
    if not best or best["depth"] < shake_pct:
        return None
    last = closes[-1][1]
    span = best["high"] - best["trough"]
    recovery = (last - best["trough"]) / span if span > 0 else 0.0
    if recovery < recover_ratio:
        return None
    return {"depth_pct": round(best["depth"], 1),
            "recovery_pct": round(min(recovery, 1.5) * 100),
            "high_time": f"{best['high_t'][:2]}:{best['high_t'][2:4]}",
            "trough_time": f"{best['trough_t'][:2]}:{best['trough_t'][2:4]}"}


def aggregate_15m_bars(bars):
    """1분봉을 장 시작 기준 15분봉으로 합성한다."""
    buckets = {}
    for b in bars:
        t = b.get("time", "")
        if len(t) != 6 or not b.get("close"):
            continue
        hh, mm = int(t[:2]), int(t[2:4])
        mins = hh * 60 + mm
        start = 9 * 60
        if mins < start:
            continue
        idx = (mins - start) // 15
        key_m = start + idx * 15
        key = f"{key_m // 60:02d}{key_m % 60:02d}00"
        row = buckets.get(key)
        if row is None:
            buckets[key] = {"time": key, "open": b["open"], "high": b["high"],
                            "low": b["low"], "close": b["close"], "vol": b["vol"]}
        else:
            row["high"] = max(row["high"], b["high"])
            row["low"] = min(row["low"], b["low"])
            row["close"] = b["close"]
            row["vol"] += b["vol"]
    return [buckets[k] for k in sorted(buckets)]


def _fmt_time(t):
    return f"{t[:2]}:{t[2:4]}" if isinstance(t, str) and len(t) >= 4 else ""


def detect_deep_shakeout_absorption(now, bars, drop_min=13.0, drop_max=24.0,
                                    ibs_min=0.25, recovery_min=0.20,
                                    late_window=60):
    """고점 대비 -13~-24% 급락 후 종가 흡수 흔적을 감지한다."""
    high, price = now["high"], now["price"]
    if high <= 0 or price <= 0:
        return None
    closes = [(b["time"], b["close"], b["low"], b["high"], b["vol"])
              for b in bars if b.get("close")]
    if len(closes) < 30:
        return None

    high_idx, high_bar = max(enumerate(closes), key=lambda x: x[1][3])
    if high_idx >= len(closes) - 4:
        return None
    after_high = closes[high_idx + 1:]
    rel_low_idx, low_bar = min(enumerate(after_high), key=lambda x: x[1][2])
    low_idx = high_idx + 1 + rel_low_idx
    if low_idx >= len(closes) - 3:
        return None

    high_t = high_bar[0]
    low_t = low_bar[0]
    ordered_low = low_bar[2]
    if ordered_low <= 0 or high <= ordered_low:
        return None
    drop_low = (high - ordered_low) / high * 100
    drop_close = (high - price) / high * 100
    ibs = (price - ordered_low) / (high - ordered_low)
    if not (drop_min <= drop_low <= drop_max):
        return None
    if ibs < ibs_min:
        return None

    after = closes[low_idx + 1:]
    post_low = min(x[2] for x in after)
    retest_broken = post_low < ordered_low * 0.997
    span = high - ordered_low
    recovery = (price - ordered_low) / span if span > 0 else 0.0
    if recovery < recovery_min:
        return None

    late = closes[-max(5, min(late_window, len(closes))):]
    late_lows = [x[2] for x in late]
    late_retest = min(late_lows) <= ordered_low * 1.005
    late_close_high = max(x[3] for x in late)
    late_reclaim = price >= late_close_high * 0.985
    late_vwap_num = sum(x[1] * x[4] for x in late)
    late_vwap_den = sum(x[4] for x in late)
    late_vwap = late_vwap_num / late_vwap_den if late_vwap_den else 0
    vwap_reclaim = bool(late_vwap and price >= late_vwap)
    close_hold_score = 0
    close_hold_score += 35 if ibs >= 0.45 else 20
    close_hold_score += 20 if late_reclaim else 0
    close_hold_score += 15 if vwap_reclaim else 0
    close_hold_score += 15 if not retest_broken else -20
    close_hold_score += 15 if not late_retest else 0
    bars15 = aggregate_15m_bars(bars)
    return {
        "drop_low_from_high_pct": round(drop_low, 1),
        "drop_close_from_high_pct": round(drop_close, 1),
        "ibs": round(ibs, 3),
        "recovery_pct": round(min(recovery, 1.5) * 100),
        "high_time": _fmt_time(high_t),
        "low_time": _fmt_time(low_t),
        "late_reclaim": late_reclaim,
        "vwap_reclaim": vwap_reclaim,
        "retest_broken": retest_broken,
        "close_hold_score": max(0, min(100, int(close_hold_score))),
        "bars15_count": len(bars15),
    }


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


# ---------- 자가 개선 입력 (radar_backtest.py 산출물) ----------

def load_tuning(use_weights=True):
    """튜닝 가중치 비율 + 유효 보정표(bins) 로드. 파일 없으면 기본값."""
    ratios = {"spark": 1.0, "fade": 1.0, "flow": 1.0, "event": 1.0, "ma10": 1.0}
    if use_weights and os.path.exists(WEIGHTS_PATH):
        try:
            w = json.load(open(WEIGHTS_PATH, encoding="utf-8"))
            for k, base in (w.get("default") or {}).items():
                if k in ratios and base:
                    ratios[k] = float(w["weights"][k]) / float(base)
            log(f"[radar] 튜닝 가중치 적용 (표본 {w.get('basis_n')}건 기반)")
        except Exception as e:
            log(f"[warn] 가중치 로드 실패(기본값 사용): {e}")
    bins = []
    if os.path.exists(PERF_PATH):
        try:
            perf = json.load(open(PERF_PATH, encoding="utf-8"))
            bins = [b for b in perf.get("bins", []) if b.get("valid")]
        except Exception:
            pass
    return ratios, bins


def calibrated_prob(score, bins):
    """점수가 표본 충분(n>=20)한 구간에 들면 실측 적중률 반환, 아니면 None."""
    for b in bins:
        if b["lo"] <= score < b["hi"]:
            return {"rate": b["actual_rate"], "n": b["n"]}
    return None


# ---------- 수상함 점수 (결정론, 투명 가중합) ----------

def suspicion_score(spark_clusters, fade_pct, ma10_margin_pct, acc, event_score=0.0,
                    vol_x_base=SPARK_VOL_X, ratios=None,
                    shake=None, shake_base=10.0, deep_shake=None):
    """0~100. 각 항목 근거는 breakdown으로 공개.

    ratios: 백테스트 기반 자가 튜닝 가중치 비율(항목별 0.7~1.3). None이면 기본.
    shake: shakeout 트랙이면 detect_shakeout 결과 — fade 슬롯을 눌림 깊이·회복률
    기반 점수로 대체한다 (breakdown 키를 재사용해 백테스트·UI 호환 유지).
    """
    bd = {}
    bd["base"] = 30
    # 스파크 강도: 최대 클러스터 배수. vol_x 기준치→0점, 기준치×4→15점 선형
    max_x = max((c["vol_x"] for c in spark_clusters), default=0.0)
    bd["spark"] = round(min(15.0, max(0.0, (max_x - vol_x_base) / (vol_x_base * 3) * 15)), 1)
    if deep_shake:
        hold_q = deep_shake["close_hold_score"] / 100.0
        ibs_q = min(1.0, deep_shake["ibs"] / 0.60)
        recov_q = min(1.0, deep_shake["recovery_pct"] / 100.0)
        bd["fade"] = round(15.0 * (0.45 * hold_q + 0.30 * ibs_q + 0.25 * recov_q), 1)
    elif shake:
        # 흔들기 품질: 깊이 기준치→7.5점, +8%p 더 깊으면 15점 선형 × 회복률 가중(0.6~1.0)
        depth_q = min(1.0, 0.5 + (shake["depth_pct"] - shake_base) / 8.0 * 0.5)
        recov_q = 0.6 + 0.4 * min(1.0, shake["recovery_pct"] / 100.0)
        bd["fade"] = round(15.0 * max(0.0, depth_q) * recov_q, 1)
    else:
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
    bd_raw = dict(bd)  # 가중치 적용 전 원점수 — 백테스트 통계는 이것만 사용(드리프트 방지)
    if ratios:
        for k, r in ratios.items():
            if k in bd:
                bd[k] = round(bd[k] * r, 1)
    total = max(0, min(100, int(round(sum(bd.values())))))
    total_raw = max(0, min(100, int(round(sum(bd_raw.values())))))
    return total, bd, total_raw, bd_raw


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
    # 조건 3 공통 전제: 당일 고가 +13% 이상 급등 이력
    high_pct = (now["high"] / now["prev_close"] - 1) * 100
    if high_pct < p.high_pct:
        return None
    # 트랙 분기 — fade(급등 후 식음, 기존) vs shakeout(눌림 후 재상승, 신규)
    is_fade = (now["price"] < now["high"]
               and p.chg_min <= now["change_pct"] <= p.chg_max)
    is_shake_cand = (not is_fade
                     and p.chg_min <= now["change_pct"] <= p.shake_chg_max)
    is_deep_cand = False

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
    if now["price"] < ma10 and not p.deep_shake_enabled:
        return None

    # 조건 2: 분봉 스파크
    try:
        bars = kis.minute_bars_today(code)
    except Exception as e:
        log(f"  [skip] {name}: 분봉 실패 {e}")
        return "ERR"
    deep_shake = None
    if p.deep_shake_enabled:
        deep_shake = detect_deep_shakeout_absorption(
            now, bars, p.deep_drop_min, p.deep_drop_max, p.deep_ibs_min,
            p.deep_recovery_min, p.deep_late_window)
        is_deep_cand = bool(deep_shake)
    if now["price"] < ma10 and not is_deep_cand:
        return None
    if not is_fade and not is_shake_cand and not is_deep_cand:
        return None
    sparks = detect_sparks(bars, p.spark_x, p.spark_pct)
    if not sparks and not is_deep_cand:
        return None
    # shakeout 트랙은 분봉 패턴(고점 → 큰 눌림 → 재상승) 필수
    shake = None
    if is_shake_cand and not is_deep_cand:
        shake = detect_shakeout(bars, p.shake_pct, p.shake_recover)
        if not shake:
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

    denom = now["high"] - now["prev_close"]  # 게이트상 양수지만 --high-pct 0 입력 방어
    fade_pct = (now["high"] - now["price"]) / denom * 100 if denom > 0 else 0.0
    ma10_margin = (now["price"] / ma10 - 1) * 100
    score, breakdown, score_raw, breakdown_raw = suspicion_score(
        sparks, fade_pct, ma10_margin, acc, event_score, p.spark_x, p.tuning_ratios,
        shake=shake, shake_base=p.shake_pct, deep_shake=deep_shake)
    # 보정표는 raw 점수 기준으로 누적되므로 매칭도 raw로 (가중치 체제와 무관하게 일관)
    calib = calibrated_prob(score_raw, p.calib_bins)

    # 메가스파크 × 당일 수급매수 가점 — Kimi 가점과 동일하게 표시 점수에만 반영.
    # raw(score_raw/breakdown_raw)는 불변: 백테스트 통계·보정표·가중치 튜닝 비교성 유지.
    # 가설(≥40x + 외인/기관 매수 → 강한 회복력)은 radar_backtest의 spark_flow 표로 검증.
    spark_max_x = round(max((c["vol_x"] for c in sparks), default=0.0), 1)
    mega_flow = spark_max_x >= p.mega_x and acc["today_buy"]
    if mega_flow:
        boosted = min(100, score + MEGA_BONUS)
        breakdown["mega"] = boosted - score  # 100 캡 시 실제 적용분만 기록 (해부도 과장 방지)
        score = boosted

    return {
        "code": code,
        "name": name,
        "sector": now["sector"],
        "pattern": "deep_shakeout" if deep_shake else "shakeout" if shake else "fade",
        "shake": shake,  # shakeout 트랙만 {depth_pct, recovery_pct, high_time, trough_time}
        "deep_shake": deep_shake,
        "suspicion_score": score,
        "calibrated_prob": calib,  # {rate, n} — 실측 적중률 (표본 n>=20 구간만, 없으면 None)
        "score_breakdown": breakdown,
        "score_raw": score_raw,                 # 가중치 적용 전 — 백테스트 통계용
        "score_breakdown_raw": breakdown_raw,
        "price": now["price"],
        "change_pct": round(now["change_pct"], 2),
        "high_pct": round(high_pct, 2),
        "fade_pct": round(fade_pct, 1),
        "value_eok": round(now["value"] / 1e8),
        "ma10": round(ma10, 1),
        "ma10_margin_pct": round(ma10_margin, 2),
        "spark": {"clusters": sparks},
        "spark_max_x": spark_max_x,
        "mega_flow": mega_flow,
        "flow": acc,
        "news": news_items,
        "matched_events": matched_events,
    }


def _parse_hhmmss(v):
    s = str(v or "").replace(":", "").strip()
    if len(s) == 4:
        s += "00"
    if len(s) != 6 or not s.isdigit():
        raise ValueError(f"invalid HHMMSS value: {v}")
    return s


def _kimi_in_auto_window(now=None, start="144500", end="153000"):
    now = now or datetime.now(KST)
    hm = now.strftime("%H%M%S")
    return _parse_hhmmss(start) <= hm <= _parse_hhmmss(end)


def apply_kimi_verification(suspects, mode="auto", max_items=5, timeout=60,
                            window_start="144500", window_end="153000"):
    """상위 후보에 Kimi 검증을 붙인다. 실패해도 후보 게시는 유지한다."""
    if mode == "auto" and not _kimi_in_auto_window(start=window_start, end=window_end):
        for s in suspects:
            if s.get("pattern") == "deep_shakeout":
                s["ai_verdict"] = {"status": "outside_window",
                                   "window": [window_start, window_end]}
        return
    if not kimi_client.enabled(mode):
        status = "disabled" if mode == "off" or os.environ.get("RADAR_KIMI_VERIFY") == "0" else "not_configured"
        for s in suspects:
            if s.get("pattern") == "deep_shakeout":
                s["ai_verdict"] = {"status": status}
        return
    targets = [s for s in suspects if s.get("pattern") == "deep_shakeout"]
    targets += [s for s in suspects if s.get("pattern") != "deep_shakeout"]
    seen, unique = set(), []
    for s in targets:
        if s["code"] in seen:
            continue
        seen.add(s["code"])
        unique.append(s)
        if len(unique) >= max_items:
            break
    for s in unique:
        try:
            verdict = kimi_client.verify_candidate(s, timeout=timeout)
            s["ai_verdict"] = verdict
            if verdict["verdict"] == "CONFIRM":
                s["suspicion_score"] = min(100, s["suspicion_score"] + 5)
                s["score_breakdown"]["ai"] = 5
            elif verdict["verdict"] == "REJECT":
                s["suspicion_score"] = max(0, s["suspicion_score"] - 10)
                s["score_breakdown"]["ai"] = -10
        except Exception as e:
            s["ai_verdict"] = {"status": "unavailable", "error": str(e)[:120]}


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


def build_universe_naver(chg_min, chg_max, min_value_mn):
    """[폴백] 등락률 밴드 내 + 거래대금 게이트 통과 종목 전수 수집 (네이버 up/down 풀스캔).

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
    return list(seen.values())


def build_universe_rank(chg_min, chg_max, top_n):
    """[기본] 시장별 거래대금 top_n + 등락률 top_n 합집합 → 등락률 밴드.

    거래대금 순위 = KIS 공식 volume-rank(거래금액순, 실측 검증).
    등락률 순위 = 네이버 up 랭킹 1페이지 상위 top_n (등락률 내림차순 보장,
    KIS fluctuation API는 정렬이 등락률순으로 동작하지 않음을 실측 확인).
    700억 게이트는 여기서 적용하지 않는다 — 정밀판정(scan_one)의 KIS
    price_now 재검증이 거래대금 하한을 책임진다 (순위권+큰돈 이중 필터).
    어느 한 콜이라도 실패하면 예외 전파 → 호출부가 네이버 전수 스캔 폴백
    (반쪽 유니버스로 조용히 왜곡 게시하는 것 방지).
    """
    seen = {}

    def keep(r):
        if r["change_pct"] is None or not (chg_min <= r["change_pct"] <= chg_max):
            return
        seen.setdefault(r["code"], r)

    for market in ("KOSPI", "KOSDAQ"):
        for r in kis.value_rank(market, top_n):           # 거래대금 top_n
            keep(r)
        rows, _ = _rank_page("up", market, 1)             # 등락률 top_n
        for r in rows[:top_n]:
            keep(r)
    return list(seen.values())


def build_universe(chg_min, chg_max, min_value_mn, names, top_n):
    """유니버스 구성: KIS 순위 방식 기본, 실패 시 네이버 전수 스캔 폴백."""
    try:
        rows = build_universe_rank(chg_min, chg_max, top_n)
        method = "kis_rank"
    except Exception as e:
        log(f"[warn] KIS 랭킹 유니버스 실패: {e} → 네이버 전수 스캔 폴백")
        rows = build_universe_naver(chg_min, chg_max, min_value_mn)
        method = "naver_scan"

    seen = {r["code"]: r for r in rows}
    for nm in names or []:
        code = resolve_code(nm)
        if code and code not in seen:
            seen[code] = {"name": nm, "code": code, "change_pct": None, "value_mn": None}
    return list(seen.values()), method


def main():
    ap = argparse.ArgumentParser(description="이벤트 매집 레이더")
    ap.add_argument("--min-value", type=float, default=MIN_VALUE, help="당일 거래대금 하한(원)")
    ap.add_argument("--high-pct", type=float, default=HIGH_PCT, help="당일 고가 등락률 하한(%%)")
    ap.add_argument("--chg-min", type=float, default=CHG_MIN)
    ap.add_argument("--chg-max", type=float, default=CHG_MAX)
    ap.add_argument("--spark-x", type=float, default=SPARK_VOL_X, help="분봉 거래량 중앙값 배수")
    ap.add_argument("--spark-pct", type=float, default=SPARK_PCT, help="분봉 등락 하한(%%)")
    ap.add_argument("--mega-x", type=float, default=MEGA_SPARK_X,
                    help="메가 스파크 임계(중앙값 배수) — 당일 수급매수 동반 시 강한 가점")
    ap.add_argument("--top-n", type=int, default=20,
                    help="유니버스: 시장×지표(거래대금/등락률)별 상위 N종목")
    ap.add_argument("--shake-pct", type=float, default=10.0,
                    help="흔들기 트랙: 장중 고점 대비 눌림 깊이 하한(%%)")
    ap.add_argument("--shake-recover", type=float, default=0.30,
                    help="흔들기 트랙: 낙폭 대비 회복률 하한(0~1)")
    ap.add_argument("--shake-chg-max", type=float, default=30.0,
                    help="흔들기 트랙: 현재 등락률 상한(%%) — fade 밴드와 별도")
    ap.add_argument("--no-deep-shake", dest="deep_shake_enabled", action="store_false",
                    help="급락 흡수(deep_shakeout) 트랙 비활성화")
    ap.set_defaults(deep_shake_enabled=True)
    ap.add_argument("--deep-drop-min", type=float, default=13.0,
                    help="급락 흡수: 고점 대비 저가 하락률 하한(%%)")
    ap.add_argument("--deep-drop-max", type=float, default=24.0,
                    help="급락 흡수: 고점 대비 저가 하락률 상한(%%)")
    ap.add_argument("--deep-ibs-min", type=float, default=0.25,
                    help="급락 흡수: 종가/현재가 저가 방어 IBS 하한(0~1)")
    ap.add_argument("--deep-recovery-min", type=float, default=0.20,
                    help="급락 흡수: 고저폭 대비 회복률 하한(0~1)")
    ap.add_argument("--deep-late-window", type=int, default=60,
                    help="급락 흡수: 막판 확인 구간(분)")
    ap.add_argument("--kimi-mode", choices=("auto", "on", "off"), default="auto",
                    help="Kimi 상위 후보 검증 모드(auto=키 있으면 자동)")
    ap.add_argument("--kimi-max", type=int, default=5,
                    help="Kimi 검증 최대 후보 수")
    ap.add_argument("--kimi-timeout", type=int, default=60,
                    help="Kimi 후보별 타임아웃(초)")
    ap.add_argument("--kimi-window-start", default="144500",
                    help="Kimi auto 검증 시작 시각(HHMMSS, 기본 14:45)")
    ap.add_argument("--kimi-window-end", default="153000",
                    help="Kimi auto 검증 종료 시각(HHMMSS, 기본 15:30)")
    ap.add_argument("--names", nargs="*", default=[], help="watchlist 강제 포함")
    ap.add_argument("--no-tuned-weights", action="store_true",
                    help="백테스트 튜닝 가중치 무시 (기본 가중치 사용)")
    p = ap.parse_args()
    p.tuning_ratios, p.calib_bins = load_tuning(use_weights=not p.no_tuned_weights)

    # 조건 1: D-10 이벤트 캘린더
    events = upcoming_events(10)
    log(f"[radar] D-10 이벤트 {len(events)}건")

    # 1차 게이트: 시장별 거래대금·등락률 순위권 합집합 + 등락률 밴드
    universe_chg_min = p.chg_min
    if p.deep_shake_enabled:
        # deep_shakeout 후보를 수집하기 위한 유니버스 확장.
        # scan_one의 fade/shakeout 판정은 p.chg_min(-6 기본)을 그대로 써서
        # 급락 종목이 deep 조건 실패 후 fade로 오분류되지 않게 한다.
        universe_chg_min = min(universe_chg_min, -abs(p.deep_drop_max))
    candidates, universe_method = build_universe(
        universe_chg_min, p.chg_max, p.min_value / 1e6, p.names, p.top_n)
    if not candidates:
        # 정상 장에선 순위권 종목이 밴드 안에 항상 여러 개 — 0이면 수집 장애로 본다
        log("[error] 1차 게이트 통과 0종목 — 랭킹 수집 장애 의심(KIS·네이버 모두), 중단")
        sys.exit(2)
    log(f"[radar] 1차 게이트 통과 {len(candidates)}종목 ({universe_method}) → KIS 정밀 판정")

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
    apply_kimi_verification(suspects, p.kimi_mode, p.kimi_max, p.kimi_timeout,
                            p.kimi_window_start, p.kimi_window_end)
    suspects.sort(key=lambda x: -x["suspicion_score"])

    out = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "params": {"min_value_eok": round(p.min_value / 1e8), "high_pct": p.high_pct,
                   "chg_range": [p.chg_min, p.chg_max], "spark_x": p.spark_x,
                   "spark_pct": p.spark_pct, "mega_x": p.mega_x,
                   "universe": universe_method, "top_n": p.top_n,
                   "universe_chg_range": [universe_chg_min, p.chg_max],
                   "shake_pct": p.shake_pct, "shake_chg_max": p.shake_chg_max,
                   "deep_shake_enabled": p.deep_shake_enabled,
                   "deep_drop_range": [p.deep_drop_min, p.deep_drop_max],
                   "deep_ibs_min": p.deep_ibs_min,
                   "kimi_mode": p.kimi_mode,
                   "kimi_max": p.kimi_max,
                   "kimi_window": [p.kimi_window_start, p.kimi_window_end]},
        "universe_count": len(candidates),
        "events": events,
        "suspects": suspects,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
