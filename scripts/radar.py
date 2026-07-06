#!/usr/bin/env python3
"""이벤트 매집 레이더 — "당일 폭발 → 오늘 5분 양봉 스파크(재매집)" 스캐너 (2026-06 정의 개편).

목적: 최근 6거래일 큰 폭발(고가 +22% AND 거래량이 유통주식수의 90%+)이 난 종목이, 현재 등락률 −5~+7%에서
      14:30~장종료 5분봉 양봉(몸통%≥1.5%)으로 2회 이상 다시 분출하는(재매집) 것을 탐지.

파이프라인:
  [폭발 캐치] 매 회차 시장별 네이버 up(등락률) 랭킹을 훑어 '고가 +22% AND 당일 거래량/유통주식수 ≥90%'
              폭발을 레지스트리(.explosion_registry.json)에 기록 → 최근 6거래일 폭발만 추적.
              (거래대금 순위·등락률 합집합 유니버스는 폐지. 당일 폭발은 /forecast 페이지에 게시.)
   ▼
  [재매집(반등)] 전일 폭발 종목이 14:30~장종료 5분봉 양봉(몸통%≥1.5%) 2회 이상 스파크   ← KIS 현재가·일봉·분봉
                 AND 현재 등락률 −5~+7%(깊은 식음/이미 분출 제외, 조용한 매집 구간).
   ▼
  [조건1·5] 이벤트 캘린더 × 뉴스 민감도 → event_calendar/theme_map (표시 가점)

reaccum/explosion 트랙은 고정 표시 점수(REACCUM_SCORE base)로 게시(검증중, score_raw=0으로 통계 격리).
빈 레이더(후보 0)도 정상 상태.

사용:
  python3 scripts/radar.py                          # 기본 임계값
  python3 scripts/radar.py --names 한온시스템        # 특정 종목 강제 시드
출력: stdout JSON {generated_at, params, events[], explosions[], suspects[]}
"""
import os
import sys
import json
import argparse
from datetime import datetime, timezone, timedelta

from net import get_bytes
from team1_collect import resolve_code, fetch_news, is_individual_stock, UA
from team2_relevance import score_news, make_aliases
from event_calendar import upcoming_events
from theme_map import match_events, match_sensitivity, THEMES
if os.environ.get("RADAR_BROKER", "kiwoom").lower() == "kis":
    import kis_client as kis
else:
    import kiwoom_client as kis  # 키움 드롭인(기본). RADAR_BROKER=kis 로 KIS 복귀.
import float_ratio
import alert_release

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPLOSION_REGISTRY_PATH = os.path.join(REPO, ".explosion_registry.json")
YOUTONG_REGISTRY_PATH = os.path.join(REPO, ".youtong_registry.json")  # youtong 당일 지속 상태(종일 유지)
REACCUM_SEED_PATH = os.path.join(REPO, "data", "reaccum_seed.json")
PERFORMANCE_PATH = os.path.join(REPO, "web", "data", "performance.json")

# ---- 기본 임계값 (2026-06 폭발 정의 전면 개편) ----
# 가격(OHLC)은 항상 J(KRX 공식), 거래대금·거래량·수급만 UN(KRX+NXT 통합, kis_client.MONEY_MARKET).
# ── 폭발 정의: 당일 고가등락률 ≥22% AND 당일 거래량 / 유통주식수 ≥90%(유통주식이 통째로 손바뀜).
#    거래대금 순위·등락률 합집합 유니버스는 폐지 — 폭발 스캔 소스는 네이버 up(등락률) 랭킹뿐.
EXPLOSION_HIGH_PCT = 22.0          # 폭발 당일 고가 등락률 하한(%)
EXPLOSION_VOL_TURNOVER = 90.0      # 폭발 당일 거래량 / 유통주식수 회전율 하한(%) — 유동비율 없으면 미확정(스킵)
EXPLOSION_WINDOW = 6               # 최근 6거래일 폭발만 재매집(반등) 후보로 추적
EXPLOSION_SCAN_N = 50              # 시장별 네이버 up(등락률) 상위 N에서 폭발 감시(22%+ 누락 방지)
# ── /youtong '곧 폭발할 후보'(위로 올라오며 분출): 09:30 이후, 현재 등락률 ≥7% AND 유통주식 회전율 ≥50%
#    (상한 없음) AND 09:30 이후 5분봉 양봉(몸통%≥1.5%) 스파크 ≥1회. 이미 폭발(고가≥22% AND 회전율≥90%)은 제외
#    (forecast로 분리). 한 번 포착되면 종일 지속(registry, 현재가 실시간 갱신). 표시·알림 전용(통계 무관).
YOUTONG_CHANGE_PCT = 7.0           # /youtong 게이트: 현재 등락률 하한(%)
YOUTONG_TURNOVER_MIN = 50.0        # /youtong 유통주식 회전율 하한(%) — 상한 없음
YOUTONG_START_HHMM = "0930"        # /youtong 감지 시작 시각(그 전 무시) + 스파크 시각 하한
YOUTONG_SPARK_MIN = 1              # /youtong: 시작시각 이후 5분 양봉 스파크 최소 수(몸통%·span은 REIGNITION_* 재사용)
# ── 반등(재매집) 정의: 최근 6거래일 폭발 종목이 **14:30~장종료** 5분봉 '양봉 몸통%≥1.5%'가 2회 이상 스파크
#    (마감 직전 재분출) AND **현재 등락률 −5%~+7%**(깊은 식음/이미 분출 제외, 조용한 매집 구간). 폭발→식음→
#    재반등 흐름으로 레이더 수상종목에 노출되는 건 동일하되, 위 두 게이트로 한정한다.
REIGNITION_SPAN_MIN = 5            # 재반등 스파크 판정 분봉 합성 단위(분)
REIGNITION_BODY_PCT = 1.5          # 5분 양봉 몸통%(|종가−시가|/시가) 하한 (2026-06-28 회장님 매매신호 정합: 2.0→1.5, 다스코 14:35봉 포착)
REIGNITION_MIN_COUNT = 2           # 시작시각 이후 자격 양봉(스파크)이 이 수 이상이어야 반등 인정
REIGNITION_START_HHMM = "1430"     # 재반등 스파크 집계 시작 시각(그 전 양봉은 미집계 — 마감 직전 재분출만)
REACCUM_CHANGE_MIN = -5.0          # 재매집 현재 등락률 하한(% — 이보다 더 빠지면 깊은 식음으로 제외)
REACCUM_CHANGE_MAX = 7.0           # 재매집 현재 등락률 상한(% — 이보다 높으면 이미 분출로 제외)
GEUPSO_BODY_PCT = 2.0              # 🎯 매수급소: 14:30↑ 5분 양봉 몸통% 하한(회장님 지시 2026-07-03 —
                                   # 덕신 5.67/3.11% 2개=폭락에도 안심 vs 상지 1.51% 찔끔=받침 없음)
GEUPSO_MIN_COUNT = 2               # 🎯 매수급소: 2%+ 양봉 최소 개수. 급소는 등락률 밴드 무제한(폭락 중이든 급등 중이든)
REACCUM_TRACK_DAYS = 30            # 폭발 후 장기 추적 상한(거래일) — 6일 지나도 '20일선 위인 동안' 계속 추적(급소 감시)
LOWACCUM_CHANGE_MAX = -10.0        # 🧲 저점매집: 당일 등락 상한(이하 폭락 중일 때만 판정. 회장님 지시 2026-07-03)
LOWACCUM_BODY_PCT = 2.0            # 🧲 저점매집: 시간 무관 5분 양봉 몸통% 하한
LOWACCUM_MIN_COUNT = 3             # 🧲 저점매집: 2%+ 양봉 최소 개수 (덕신 7/3: −16% 폭락 바닥 4방 실측)
YOUTONG_TRACK_DAYS = 10            # youtong 이력 추적 일수 — 폭발 미등록(회전율 게이트 탈락) 종목도 급소/저점매집 감시
# 💥 흔들기(폭발 직후 고회전 페이드) — 회장님 지시 2026-07-03. 원형: 금호건설·동양파일 6/25(상한 터치 후
# 대폭 밀림+유통 한 바퀴 손바뀜+MA20 사수 → 익일 상한). 실측(6~7월 폭발풀 n=38): 익일 고가 +13% 터치 68.4%
# ·+7% 78.9%·평균 +18.0%·EV +4.63%/회. 기존 폭발(고가22·회전90)·youtong(+7%·50%) 게이트 사이로 빠지는 별도 그물.
SHAKEOUT_HIGH_PCT = 20.0           # 💥 당일 고가등락 하한(%) — 상한 터치권까지 갔어야
SHAKEOUT_FADE_PCT = 15.0           # 💥 페이드(고가등락 − 현재등락) 하한(%p) — 크게 밀렸어야(흔들기)
SHAKEOUT_TURNOVER_MIN = 40.0       # 💥 당일 유통회전율 하한(%) — 흔들기 속 대량 손바뀜(흡수)
SHAKEOUT_RUN6D_MAX = 100.0         # 💥 6일 누적 +100% 이상 & 당일 음수 = 과확장 붕괴 → 제외(동양파일 7/2형 실패 차단)
# 💥 흔들기 순위: **2일 합산(신호일+전일) 유통회전율** 스윗스팟이 익일 탄력 최강, 과회전은 물량 소진(회장님 20년 검증).
# 실측 2026-07-06(금요일 레이더 12종목, 오늘 등락 밴드평균): 스윗 90~140% +16.6% / 과회전>180% +4.7% / 미달 +2.9%.
#   예) 진흥 2일110%→상한가 / 남광137%·동신136%→+18/+14% / 삼기328%·삼호248%→약세·혼조.
SHAKEOUT_T2D_SWEET_LO = 90.0
SHAKEOUT_T2D_SWEET_HI = 140.0
SHAKEOUT_T2D_OVERHEAT = 180.0


def _shakeout_turnover_tier(x):
    """흔들기 **2일 합산 회전율** 순위 tier(낮을수록 우선): 0=스윗 90~140 / 1=허용(40~90·140~180) / 2=과회전>180(강등).
    turnover_2d_pct 우선, 없으면 turnover_pct(1일) 폴백."""
    t = x.get("turnover_2d_pct")
    if t is None:
        t = x.get("turnover_pct") or 0
    if SHAKEOUT_T2D_SWEET_LO <= t <= SHAKEOUT_T2D_SWEET_HI:
        return 0
    if t > SHAKEOUT_T2D_OVERHEAT:
        return 2
    return 1


# 💥 흔들기 고점 대비 낙폭(peak drawdown) 스윗존 — 회장님 관찰(2026-07-06 실측):
#   −30~−45%가 최적(승자 진흥−43·삼호−31·남광−30), −55%↓ 과낙은 재료·수급 소멸로 반등 약함(동양−55·금호−50).
SHAKEOUT_DD_SWEET_LO = -45.0   # 스윗존 하한(가장 깊음)
SHAKEOUT_DD_SWEET_HI = -30.0   # 스윗존 상한(가장 얕음)


def _shakeout_dd_tier(x):
    """흔들기 고점 대비 낙폭 tier(낮을수록 우선): 0=스윗 −45~−30% / 1=인접(−55~−45·−30~−20) / 2=그외(얕음>−20·과낙<−55)."""
    dd = x.get("peak_dd_pct")
    if dd is None:
        return 1
    if SHAKEOUT_DD_SWEET_LO <= dd <= SHAKEOUT_DD_SWEET_HI:
        return 0
    if (-55.0 <= dd < SHAKEOUT_DD_SWEET_LO) or (SHAKEOUT_DD_SWEET_HI < dd <= -20.0):
        return 1
    return 2


def _shakeout_strength(tier):
    """흔들기 강도 등급 라벨(결합 tier 0~4 → 사용자 등급). 0=회전·낙폭 둘 다 스윗=최강."""
    return {0: "Tier1(강)", 1: "Tier2(중강)", 2: "Tier3(중)"}.get(tier, "Tier4(약)")
NXT_REEVAL_START_HHMM = "1600"     # NXT 시간외가로 '현재 등락률' 재평가 시작(=15:30+신선도상한 30분). 그 전엔
                                   # 정규장 막판 양봉 텔레그램이 신선하게 나가도록 KRX 유지 + NXT 단일가도 ~16:00 체결.
REACCUM_SCORE = 62                 # 검증중 노출용 고정 표시 점수 base(raw 통계와 분리, score_raw=0)
# "예전 대장"(was_theme_leader) 판정 — regex 테마가 아니라 권위 업종(sector) 기준.
LEADER_MIN_GROUP = 3               # 같은 (폭발일, 업종) 폭발군이 이 수 이상일 때만 대장 판정
LEADER_MARGIN = 1.5               # 1위 거래대금이 2위의 이 배 이상이어야 대장 인정(근소차 가짜 대장 방지)


def log(msg):
    print(msg, file=sys.stderr, flush=True)



# ---------- 재반등 신호: 5분봉 양봉 몸통 스파크(거래대금 게이트 없음) ----------

def aggregate_minute_bars(bars, span_min):
    """당일 1분봉을 장 시작(09:00) 기준 span_min분봉으로 합성.

    open=구간 첫봉 시가, high/low=극값, close=마지막봉 종가, vol=합.
    value=Σ(close×vol) — KIS 1분봉엔 거래대금 필드가 없어 종가×거래량으로 근사(원).
    """
    buckets = {}
    order = []
    for b in bars:
        t = b.get("time", "")
        if len(t) != 6 or not b.get("close"):
            continue
        hh, mm = int(t[:2]), int(t[2:4])
        mins = hh * 60 + mm
        start = 9 * 60
        if mins < start:
            continue
        idx = (mins - start) // span_min
        key_m = start + idx * span_min
        key = f"{key_m // 60:02d}{key_m % 60:02d}00"
        val = b["close"] * b["vol"]
        row = buckets.get(key)
        if row is None:
            buckets[key] = {"time": key, "open": b["open"], "high": b["high"],
                            "low": b["low"], "close": b["close"], "vol": b["vol"],
                            "value": val}
            order.append(key)
        else:
            row["high"] = max(row["high"], b["high"])
            row["low"] = min(row["low"], b["low"])
            row["close"] = b["close"]
            row["vol"] += b["vol"]
            row["value"] += val
    return [buckets[k] for k in sorted(order)]


def reignition_bars(bars, body_pct_min=REIGNITION_BODY_PCT,
                    span_min=REIGNITION_SPAN_MIN):
    """오늘 재반등 자격 5분봉(스파크) '전체'를 시각순으로.

    자격: 당일 '상승' 5분봉(종가>시가) 중 몸통%((종가−시가)/시가×100) ≥ body_pct_min 인 봉.
    → "큰 상승 몸통" 스파크 = 폭발 종목에 돈이 다시 들어오는 재매집 신호. 거래대금 게이트는 없다(횟수만으로 판정).
    하락·도지 봉은 제외(폭락 중 오탐 방지). 각 봉 {body_pct, time("HH:MM"=버킷 시작), value_eok,
    close, open}. **전체 양봉 스파크를 반환** — scan_reaccum_candidate가 호출부에서 14:30↑로 후필터하고 ≥2회
    게이트를 적용한다(표시·대표봉·텔레그램 봉단위 알림도 그 필터된 집합 공용). value_eok는 표시용.
    """
    out = []
    for bar in aggregate_minute_bars(bars, span_min):
        if bar["open"] <= 0 or bar["close"] <= bar["open"]:
            continue  # 상승 몸통만 (하락·도지 제외)
        body = (bar["close"] - bar["open"]) / bar["open"] * 100
        if body < body_pct_min:
            continue
        out.append({"body_pct": round(body, 2),
                    "time": f"{bar['time'][:2]}:{bar['time'][2:4]}",
                    "value_eok": round(bar["value"] / 1e8),
                    "close": bar["close"], "open": bar["open"]})
    return out


def _has_live_bars(bars):
    """유효(비제로 종가) 분봉이 하나라도 있나 — UN 분봉 결측(빈 리스트/전부 0) 판별용."""
    return any((b.get("close") or 0) > 0 for b in bars)


def _minute_bars_with_fallback(code, label=""):
    """당일 분봉 — MONEY_MARKET(UN) 우선, UN이 비었거나 전부 0이면 J(KRX) 폴백. 일부 종목은 UN 일봉/
    거래대금은 정상인데 UN '분봉' 피드만 결측(전부 0)이라(NXT 분봉 미제공·키스트론류 실측) 그대로 두면
    KRX엔 명백한 분출이 있어도 0봉으로 계산돼 누락된다. UN 분봉 있으면 UN 유지(NXT 봉 반영 불변).
    reaccum·youtong 공용. 예외는 호출부로 전파(상위에서 처리)."""
    bars = kis.minute_bars_today(code, market=kis.MONEY_MARKET)
    if kis.MONEY_MARKET != "J" and not _has_live_bars(bars):
        jbars = kis.minute_bars_today(code, market="J")
        if _has_live_bars(jbars):
            log(f"  [info] {label or code}: UN 분봉 결측(전부 0) → J(KRX) 분봉 폴백")
            bars = jbars
    return bars


def _nxt_change_pct(code, prev_close):
    """정규장 마감 후 NXT 애프터마켓 '야간가'(네이버 overMarketPriceInfo)로 '현재 등락률'(전일 종가 대비)을
    재계산해 반환. KIS는 NXT 애프터마켓 분봉을 안 줘(분봉이 15:30서 끊김) 스파크는 정규장 것을 그대로 쓰되,
    종목의 '현재 위치(등락률)'만 NXT 시간외가로 보정 — 마감 후 NXT에서 회복/이탈하면 reaccum 밴드 재판정.
    정규장 중(marketStatus==OPEN)·야간가 결측·같은 거래일 아님·전일종가 없음이면 None(=정규장 KIS 등락률 유지).
    네이버 공개 API(시크릿 불필요). 실패는 None으로 흡수(fail-safe — 정규장 등락률로 폴백)."""
    if not prev_close:
        return None
    try:
        b = json.loads(get_bytes(f"https://m.stock.naver.com/api/stock/{code}/basic", UA))
    except Exception:
        return None
    if not isinstance(b, dict):
        return None  # 네이버가 200에 비-dict(JSON null/list/에러봉투)를 줘도 KRX 등락률로 폴백(fail-safe)
    if str(b.get("marketStatus") or "") == "OPEN":
        return None  # 정규장 중엔 보정 안 함(전일 시간외 혼동 방지) — KIS 실시간 등락률 사용
    om = b.get("overMarketPriceInfo") or {}
    if om.get("overMarketStatus") not in ("CLOSE", "TRADING"):
        return None
    om_day = str(om.get("localTradedAt") or "")[:10]
    if not om_day or om_day != str(b.get("localTradedAt") or "")[:10]:
        return None  # 시간외·정규장 체결이 같은 거래일일 때만(개장 전 전일 시간외 오대조 방지)
    try:
        over = float(str(om.get("overPrice")).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if over <= 0:
        return None
    return round((over / prev_close - 1) * 100, 2)


_ALERT_CACHE = {}   # 실행당 종목별 시장경보 캐시(수상종목 ≤reaccum_max이라 회당 소수 콜)


def _alert_level(code):
    """KRX 시장경보 현재 지정 — 네이버 basic marketAlertType(01/02/03) → "주의"/"경고"/"위험"/None.
    경고/위험 지정 수상종목은 게시 정렬 최후순위(회장님 지시 2026-07-03 — 경고 후 재상승=매매정지 지정 리스크).
    네이버 공개 API(시크릿 불필요)·실패 None(fail-safe=무경보 취급, 정렬만 영향이라 안전)."""
    if code in _ALERT_CACHE:
        return _ALERT_CACHE[code]
    level = None
    try:
        b = json.loads(get_bytes(f"https://m.stock.naver.com/api/stock/{code}/basic", UA))
        m = b.get("marketAlertType") if isinstance(b, dict) else None
        level = {"01": "주의", "02": "경고", "03": "위험"}.get((m or {}).get("code") if isinstance(m, dict) else None)
    except Exception:
        level = None
    _ALERT_CACHE[code] = level
    return level


# ---------- 재매집: 거래대금 폭발 레지스트리 ----------

def _today_yyyymmdd():
    return datetime.now(KST).strftime("%Y%m%d")


def _empty_registry():
    return {"trading_days": [], "records": {}, "window_scanned": {}}


def load_explosion_registry(path=EXPLOSION_REGISTRY_PATH):
    """untracked 로컬 폭발 레지스트리 로드. 손상/부재 시 빈 상태로 시작."""
    if not os.path.exists(path):
        return _empty_registry()
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        log(f"[warn] 폭발 레지스트리 로드 실패(빈 상태 사용): {e}")
        return _empty_registry()
    if not isinstance(data, dict):
        return _empty_registry()
    data.setdefault("trading_days", [])
    data.setdefault("records", {})
    data.setdefault("window_scanned", {})  # 6일 백필 비용 가드(code→YYYYMMDD)
    if not isinstance(data["trading_days"], list) or not isinstance(data["records"], dict):
        return _empty_registry()
    if not isinstance(data["window_scanned"], dict):
        data["window_scanned"] = {}
    return data


def save_explosion_registry(reg, path=EXPLOSION_REGISTRY_PATH):
    tmp = path + ".tmp"
    reg["updated_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    json.dump(reg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _merge_trading_days(reg, dates):
    days = {d for d in reg.get("trading_days", []) if isinstance(d, str)}
    days.update(d for d in dates if isinstance(d, str) and len(d) == 8)
    reg["trading_days"] = sorted(days)[-120:]


def _upsert_explosion(reg, rec):
    key = f"{rec['peak_date']}:{rec['code']}"
    old = reg["records"].get(key)
    if old:
        rec["peak_value_eok"] = max(float(old.get("peak_value_eok", 0) or 0),
                                    float(rec.get("peak_value_eok", 0) or 0))
        rec["peak_high_pct"] = max(float(old.get("peak_high_pct", 0) or 0),
                                   float(rec.get("peak_high_pct", 0) or 0))
        # 폭발일 거래량 회전율도 max 병합 — 장중 누적이라 단조증가지만, 부분 회차·소스 혼용
        # (seed 종일 vs live 장중)에서 더 큰(완결된) 값을 보존(회전율 점수 안정·게이트 재검증 일관).
        rec["vol_turnover_pct"] = max(float(old.get("vol_turnover_pct", 0) or 0),
                                      float(rec.get("vol_turnover_pct", 0) or 0)) or None
        # source는 권위 순(live>seed>telegram)으로 승격 — 텔레그램 시드가 bootstrap에서 먼저
        # 기록돼도, 같은 폭발이 이후 live 랭킹/seed로 확인되면 그쪽이 이김(가짜 '채널포착' 배지 방지).
        _SRC_RANK = {"live": 3, "seed": 2, "telegram": 1}
        rec["source"] = max(old.get("source") or "live", rec.get("source") or "live",
                            key=lambda s: _SRC_RANK.get(s, 0))
        # 원인/테마 메타는 기존 비어있을 때만 신규로 채움 — 폭발 당일 신선 catalyst가
        # 이후 회차의 stale 값으로 덮이지 않게(source와 동일 철학). cause_done이 캡처 동결 마커.
        for k in ("sector", "theme", "cause_summary", "cause_titles", "cause_done"):
            if old.get(k) and not rec.get(k):
                rec[k] = old[k]
    reg["records"][key] = rec


def _recent_active_explosions(reg, window, track_days=None):
    """폭발 레코드 → 후보 풀. 기본 = 최근 window(6) 거래일 폭발.
    track_days(예: 30)를 주면 6일 초과~track_days 이내 폭발도 `_extended_track=True` 플래그로 포함 —
    '식음 이후에도 20일선 위인 동안 계속 추적'(회장님 지시 2026-07-03, 🎯 매수급소 감시).
    MA20 생존 판정은 scan_reaccum_candidate가 이미 받는 일봉으로 수행(추가 API 0)."""
    dates = sorted(d for d in reg.get("trading_days", []) if isinstance(d, str))
    if not dates:
        dates = sorted({r.get("peak_date") for r in reg.get("records", {}).values() if r.get("peak_date")})
    active_dates = set(dates[-window:])
    ext_dates = (set(dates[-track_days:]) - active_dates) if track_days and track_days > window else set()
    latest = {}
    for rec in reg.get("records", {}).values():
        pd = rec.get("peak_date")
        if pd in active_dates:
            cand = rec
        elif pd in ext_dates:
            cand = dict(rec)
            cand["_extended_track"] = True   # 장기 추적분 — 20일선 아래로 깨지면 scan에서 탈락
        else:
            continue
        code = cand.get("code")
        if not code:
            continue
        prev = latest.get(code)
        if prev is None or _explosion_supersedes(cand, prev):
            latest[code] = cand
    return latest


def _explosion_supersedes(rec, prev):
    """code별 대표 폭발 레코드 선정 우선순위: ① 새 정의로 검증된(vol_turnover_pct 적재) 레코드가
    구 정의(None) 레코드를 항상 이긴다 — 마이그레이션 윈도(~6일) 동안 더 최근 peak_date의 legacy
    레코드가 유효한 옛 폭발을 가려(shadow) reaccum 후보를 억누르는 것 방지. ② 동급이면 최근 peak_date."""
    rec_valid = rec.get("vol_turnover_pct") is not None
    prev_valid = prev.get("vol_turnover_pct") is not None
    if rec_valid != prev_valid:
        return rec_valid
    return rec.get("peak_date", "") > prev.get("peak_date", "")


def _snapshot_trade_date(code, now):
    trade_date = (now.get("date") or "").strip()
    if trade_date:
        return trade_date
    try:
        daily = kis.daily_prices(code, days=1)
    except Exception as e:
        log(f"[warn] 스냅샷 영업일 확인 실패 {code}: {e}")
        return ""
    return (daily[-1].get("date") if daily else "") or ""


def _load_seed_items(path):
    """data/reaccum_seed.json을 유연하게 읽는다: list 또는 {names,codes,items} 허용."""
    if not path or not os.path.exists(path):
        return []
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        log(f"[warn] reaccum seed 로드 실패: {e}")
        return []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = []
        items.extend(raw.get("items") or [])
        items.extend(raw.get("names") or [])
        for c in raw.get("codes") or []:
            items.append({"code": c} if isinstance(c, str) else c)
    else:
        return []
    out = []
    for item in items:
        if isinstance(item, str):
            out.append({"name": item})
        elif isinstance(item, dict):
            out.append(item)
    return out


def _resolve_seed_items(names, seed_path):
    items = [{"name": nm} for nm in (names or [])]
    items.extend(_load_seed_items(seed_path))
    resolved = []
    seen = set()
    for item in items:
        name = (item.get("name") or item.get("title") or "").strip()
        code = (item.get("code") or item.get("symbol") or "").strip()
        if not code and name and name.isdigit() and len(name) == 6:
            code, name = name, item.get("name") or name
        if not code and name:
            try:
                code = resolve_code(name) or ""
            except Exception as e:
                log(f"[warn] seed 종목 코드 해석 실패 {name}: {e}")
                code = ""
        if not code or code in seen:
            continue
        seen.add(code)
        resolved.append({"code": code, "name": name or code, "source": item.get("source", "seed")})
    return resolved


def _telegram_seed_items(p):
    """텔레그램 채널 언급 종목을 재매집 보조 시드로 해석. 실패해도 본작업 계속(fail-safe).

    채널은 '재료가 막 언급된' 후보만 제공 — 폭발·식음·투신·재반등 게이트는 그대로 적용된다."""
    if not getattr(p, "telegram_seed", False):
        return []
    try:
        import telegram_news
        mentions = telegram_news.fetch_mentions(
            p.telegram_channel, max_age_min=p.telegram_max_age, limit=p.telegram_max)
    except Exception as e:
        log(f"[warn] 텔레그램 시드 수집 실패(건너뜀): {e}")
        return []
    out = [{"code": m["code"], "name": m.get("name") or m["code"], "source": "telegram"}
           for m in mentions if m.get("code")]
    if out:
        log(f"[radar] 텔레그램 채널({p.telegram_channel}) 언급 {len(out)}건 시드 추가: "
            + ", ".join(m["name"] for m in out[:8]))
    return out


def _close_strength(high, low, close):
    """폭발일 마감 강도 → (IBS, 윗꼬리%). IBS=(종가−저가)/(고가−저가)[1=고가마감, 0=저가마감],
    윗꼬리%=(고가−종가)/종가×100. 7일 표본 실증: 약마감(IBS↓·윗꼬리↑=장중 찍고 종가 밀림)이 익일
    연속성↑, 강마감(상한가류, IBS↑)은 익일 식음↑ 경향 — 표시·전진검증용(소표본이라 점수·게이트 미반영)."""
    hi, lo, cl = float(high or 0), float(low or 0), float(close or 0)
    # 클램프: 일봉 EOD에선 lo≤cl≤hi이지만, 혹시 모를 글리치(cl>hi 등)에도 0..1 / 윗꼬리≥0 보장.
    ibs = round(min(1.0, max(0.0, (cl - lo) / (hi - lo))), 2) if hi > lo else 1.0
    uppertail = round(max(0.0, (hi - cl) / cl * 100), 1) if cl > 0 else 0.0
    return ibs, uppertail


def _scan_code_window(reg, code, name, source, p):
    """code의 최근 윈도(explosion_window 거래일) 일봉을 훑어 새 정의(고가≥22% AND 거래량/유통주식수≥90%)
    폭발일을 찾아 registry에 upsert(vol_turnover_pct 포함). seed 부트스트랩·6일 소급 백필 공용 per-stock 스캔.

    반환: 완료된 스캔이면 등록 폭발일 수(≥0). **일봉 fetch 실패(KIS 장애)면 None** — 스캔 미완료이므로
    호출부(백필)가 'scanned' 마킹을 건너뛰고 다음 회차 재시도하게 한다(일시 장애가 그날 종일 스킵으로 굳는 것 방지).
    유동비율 스크랩은 윈도에 22% 고가 날이 있을 때만(비용 절감 — 라이브 경로처럼 22% 게이트 후 유동비율 조회)."""
    try:
        daily = kis.daily_prices_jmoney_un(code, days=max(12, p.explosion_window + 6))  # 거래량=UN 통합
    except Exception as e:
        log(f"[warn] {name} 일봉 실패: {e}")
        return None   # 스캔 미완료(일시 장애) — 재시도 대상
    if len(daily) < 2:
        return 0
    _merge_trading_days(reg, [d["date"] for d in daily[-p.explosion_window:]])
    recent_dates = {d["date"] for d in daily[-p.explosion_window:]}
    # 사전패스(cheap): 윈도 내 고가≥22% 날 후보 추출 — 없으면 유동비율 스크랩 없이 종료(비용 절감).
    cand = []
    for i, bar in enumerate(daily):
        if bar["date"] not in recent_dates or i == 0:
            continue
        prev_close = daily[i - 1].get("close") or 0
        if prev_close <= 0:
            continue
        high_pct = (bar["high"] / prev_close - 1) * 100
        if high_pct >= p.explosion_high_pct:    # 게이트 ①: 고가등락률 ≥22%
            cand.append((bar, prev_close, high_pct))
    if not cand:
        return 0   # 22% 날 없음 — 자격 없음(완료된 스캔, 유동비율 불필요)
    fratio, flisted = float_ratio.get_float_and_listed(code)  # 22% 날이 있는 코드만 유동비율 조회
    if not (fratio and fratio > 0 and flisted and flisted > 0):
        return 0  # 유통주식수 미상 → 90% 회전율 확정 불가, 폭발 미인정(fail-safe)
    qualifying = []
    for bar, prev_close, high_pct in cand:
        vol_turnover = float_ratio.vol_turnover(float(bar.get("volume") or 0), fratio, flisted)
        if vol_turnover is None or vol_turnover < p.explosion_vol_turnover:  # 게이트 ②: 거래량/유통주식수 ≥90%
            continue
        qualifying.append({
            "code": code, "name": name, "peak_date": bar["date"],
            "peak_value_eok": round(bar["value"] / 1e8),
            "peak_high_pct": round(high_pct, 2),
            "vol_turnover_pct": round(vol_turnover, 1),
            "source": source,
        })
    if not qualifying:
        return 0
    # 업종(sector) 백필: '예전 대장' 판정이 권위 업종으로 묶이려면 레코드도 sector 필요.
    # 레지스트리에 이미 있으면 재사용(중복 price_now 회피), 없을 때만 1콜. dry-run은 저장 안 하므로 생략.
    sector = _known_sector(reg, code)
    if not sector and not p.dry_run:
        try:
            sector = (kis.price_now(code) or {}).get("sector", "") or ""
        except Exception as e:
            log(f"[warn] {name} 업종 조회 실패: {e}")
            sector = ""
    for rec in qualifying:
        rec["sector"] = sector
        _upsert_explosion(reg, rec)
    return len(qualifying)


def bootstrap_seed_explosions(reg, p):
    """지정 종목 + 텔레그램 채널 언급 종목의 최근 일봉으로 과거 폭발(고가22%+거래량/유통주식수90%)을
    즉시 부트스트랩. per-stock 스캔은 _scan_code_window 공용 헬퍼."""
    count = 0
    resolved = _resolve_seed_items(p.names, p.reaccum_seed)
    seen = {r["code"] for r in resolved}
    for it in _telegram_seed_items(p):       # 채널 언급 종목 합치기(코드 중복 제외)
        if it["code"] not in seen:
            seen.add(it["code"])
            resolved.append(it)
    for item in resolved:
        count += _scan_code_window(reg, item["code"], item["name"], item.get("source", "seed"), p) or 0
    return count


def _known_sector(reg, code):
    """레지스트리에 이미 기록된 이 종목의 업종(있으면) — 중복 price_now 회피용."""
    for rec in reg.get("records", {}).values():
        if rec.get("code") == code and rec.get("sector"):
            return rec["sector"]
    return ""


def _up_ranking_rows(p):
    """시장별 네이버 up(등락률) 랭킹 상위 explosion_scan_n을 code 기준 dedup → (rows, 총행수, 실패 시장 수).
    폭발 라이브 감시·6일 소급 백필이 공용으로 쓰는 스캔 유니버스(현 등락률 정렬).
    fail_count>0 = 한 시장 랭킹이 비정상(그 시장 폭발 누락 가능) → scan_ok 판정에 반영(거짓 '깨끗' 방지)."""
    rows, seen, total, fail = [], set(), 0, 0
    for market in ("KOSPI", "KOSDAQ"):
        try:
            up_rows, _ = _rank_page("up", market, 1)
            total += len(up_rows)
            for row in up_rows[:p.explosion_scan_n]:
                c = row.get("code")
                if c and c not in seen:
                    seen.add(c)
                    rows.append(row)
        except Exception as e:
            fail += 1
            log(f"[warn] {market} 등락률 랭킹 수집 실패: {e}")
    return rows, total, fail


def backfill_window_explosions(reg, p):
    """6일 소급 폭발 백필 — 오늘 등락률 상위 ∪ 기존 레지스트리 활성 코드(재검증)의 지난 윈도 일봉을 스캔해
    새 정의(22%/90%) 폭발일을 registry에 채운다(vol_turnover_pct). 라이브 스캔(오늘)으로만 쌓이던 후보 풀을
    소급 보강 → 전일 폭발 종목이 오늘 14:30↑ 5분 양봉 2회+ AND 현재 등락률 −5~+7%면 수상종목으로 노출. 등록한 폭발일 수 반환.

    비용 가드: ① 이미 검증된(활성 vol_turnover_pct 있는) code 스킵 ② reg['window_scanned'][code]==오늘이면
    재스캔 안 함(10분 cron 매 회차 전체 재스캔 방지 — 첫 회차만 풀, 이후 신규 진입분만)."""
    today = _today_yyyymmdd()
    scanned = reg.setdefault("window_scanned", {})
    if not isinstance(scanned, dict):
        scanned = reg["window_scanned"] = {}
    active = _recent_active_explosions(reg, p.explosion_window)
    validated = {c for c, r in active.items() if r.get("vol_turnover_pct") is not None}
    # 유니버스: 오늘 등락률 상위 + 활성 레지스트리 코드(이름은 레코드/랭킹에서). 시드는 bootstrap이 이미 처리.
    universe = {}  # code -> name
    try:
        for row in _up_ranking_rows(p)[0]:
            if row.get("code"):
                universe[row["code"]] = row.get("name") or row["code"]
    except Exception as e:
        log(f"[warn] 백필 등락률 유니버스 수집 실패: {e}")
    for c, r in active.items():
        universe.setdefault(c, r.get("name") or c)
    count = scanned_now = 0
    for code, name in universe.items():
        if code in validated or scanned.get(code) == today:
            continue  # 이미 검증됨 / 오늘 이미 스캔함 → 재스캔 비용 절약
        n = _scan_code_window(reg, code, name, "backfill", p)
        if n is None:
            continue  # 일봉 fetch 실패(일시 장애) — scanned 마킹 안 함, 다음 회차 재시도
        scanned[code] = today
        scanned_now += 1
        count += n
    # window_scanned는 오늘 것만 유지(과거 자동 정리 — 다음 거래일 재스캔 허용)
    reg["window_scanned"] = {c: d for c, d in scanned.items() if d == today}
    log(f"[radar] 6일 소급 백필: 신규 폭발 {count}건(스캔 {scanned_now}코드, 검증완료 {len(validated)} 스킵)")
    return count


def update_live_explosions(reg, p):
    """당일 폭발(고가등락률 ≥22% AND 거래량/유통주식수 ≥90%)을 감시해 registry에 적재.

    유니버스(스캔 소스) = 시장별 네이버 up(등락률) 랭킹뿐(거래대금 순위·합집합 유니버스는 폐지).
    ⚠ up 랭킹은 '현재 등락률' 정렬이라, 장중 고가 +22%를 찍고 종가로 크게 밀린 종목은 순위 하위로
    내려가 상위 explosion_scan_n(기본 50) 밖이면 누락될 수 있다(기본값으로 대부분 커버되나 한계 존재).
    게이트는 KIS price_now(가격=J 공식,
    거래량=UN 통합)와 float_ratio(유통비율·발행주식수)로 판정한다. 유동비율이 없으면 90% 회전율을
    확정할 수 없어 폭발 미확정으로 스킵한다(22% 단독으로는 폭발로 보지 않음).

    반환 (count, scan_ok, today_explosions, youtong_candidates) — today_explosions=/forecast 당일 폭발,
    youtong_candidates=/youtong 싼 게이트(현재 등락률≥7 AND 회전율≥50 AND 미폭발) 통과분.
    5분봉 스파크 확정·종일 지속은 prepare_youtong이 처리(분봉 조회 비용 가드).
    """
    rows, up_rank_total, rank_fail = _up_ranking_rows(p)  # 네이버 up(등락률) 상위 dedup + 도달성·실패시장 카운트
    count = 0
    attempted = price_errors = 0  # price_now 도달성 — 전수 실패면 KIS 부분장애 신호
    high_pass = float_missing = 0  # 22% 고가 게이트 통과 수 / 그중 유동비율 결측으로 탈락한 수
    live_dates = set()
    today_explosions = []
    youtong_candidates = []   # /youtong 싼 게이트 통과분(스파크·지속은 prepare_youtong에서)
    today = _today_yyyymmdd()
    for row in rows:
        attempted += 1
        try:
            now = kis.price_now_jmoney_un(row["code"])  # 가격=J 공식 / 거래량=UN 통합(회전율 게이트)
        except Exception as e:
            price_errors += 1
            log(f"[warn] 폭발 현재가 조회 실패 {row.get('name')}: {e}")
            continue
        trade_date = _snapshot_trade_date(row["code"], now)
        if trade_date != today:
            continue
        if not now.get("high") or not now.get("prev_close"):
            continue
        high_pct = (now["high"] / now["prev_close"] - 1) * 100
        change_pct = round(float(now.get("change_pct") or 0), 2)  # 현재 등락률(전일 종가 대비)
        want_explosion = high_pct >= p.explosion_high_pct          # 폭발 후보(고가 게이트 ①: ≥22%)
        want_youtong = change_pct >= p.youtong_change_pct          # /youtong 후보(현재 등락률 ≥7%)
        if not (want_explosion or want_youtong):
            continue
        value_won = float(now.get("value") or 0)   # 당일 거래대금(UN, 표시용)
        # 게이트 ②(회전율)용 유동비율 — 폭발·youtong 어느 한쪽이라도 후보면 1회 조회(공유). 유통주식수 =
        # 발행주식수 × 유동비율, 없으면 회전율 확정 불가(fail-safe). 조회 대상이 high≥22 → 'high≥22 OR
        # change≥youtong_change_pct(7)'으로 넓어지나 float_ratio 7일 캐시로 완충(price_now는 이미 전 행 조회 — 추가 KIS 콜 없음).
        volume = float(now.get("volume") or 0)
        fr, flisted = float_ratio.get_float_and_listed(row["code"])
        vt = float_ratio.vol_turnover(volume, fr, flisted)  # 공유 산식(거래량/유통주식수 %), 결측 시 None
        float_ok = bool(fr and fr > 0 and flisted and flisted > 0)
        is_explosion = want_explosion and vt is not None and vt >= p.explosion_vol_turnover
        # /youtong 싼 게이트: 현재 등락률≥7 AND 회전율≥50(상한 없음) AND 아직 폭발 아님. 5분봉 스파크
        # 확정·종일 지속은 prepare_youtong이 처리(분봉은 신규 후보만 1회 조회 — 비용 가드).
        # ⚠ 폭발 승격(is_explosion) 종목도 youtong 후보로 수집 — 한 번 youtong에 뜬 종목은 폭발해도 삭제하지
        # 않고 유지(회장님 지시 2026-06-29 "삭제말고 냅둬"). /forecast에도 병행 노출되며 youtong엔 '🔥 폭발' 배지.
        if (want_youtong and vt is not None and vt >= p.youtong_turnover_min):
            youtong_candidates.append({
                "code": row["code"],
                "name": row["name"],
                "sector": now.get("sector", ""),
                "change_pct": change_pct,            # 현재 등락률(실시간)
                "high_pct": round(high_pct, 2),      # 당일 고가 등락률(참고)
                "vol_turnover_pct": round(vt, 1),    # 유통주식 회전율(≥50, 상한 없음)
                "value_eok": round(value_won / 1e8),
                # KIS _f()가 결측가를 0.0으로 강제 → 0/음수는 미상으로 보고 null(타입 계약: number|null=미상 null).
                "price": (now.get("price") if (now.get("price") or 0) > 0 else None),
                "exploded": bool(is_explosion),      # 폭발 승격 여부(배지용)
            })
        # ── 폭발(explosion) 경로 — 고가 게이트 통과분만(high_pass·float_missing 회계·게이트 동작 불변) ──
        if not want_explosion:
            continue
        high_pass += 1
        if vt is None:
            # 거래량 0/결측은 게이트 미충족일 뿐 — 유동비율(wisereport) 결측만 '소스 장애' 카운트.
            if not float_ok:
                float_missing += 1
            continue
        if vt < p.explosion_vol_turnover:
            continue
        vol_turnover = vt
        # 마감강도(peak_ibs)는 장중 현재가로 산출하면 종가와 달라 오염되므로 여기서 저장하지 않는다.
        # 수상종목(전일 폭발)으로 노출될 때 scan_reaccum_candidate가 '완결된 폭발일 일봉'에서 산출(EOD 정확).
        rec_new = {
            "code": row["code"],
            "name": row["name"],
            "peak_date": trade_date,
            "peak_value_eok": round(value_won / 1e8),
            "peak_high_pct": round(high_pct, 2),
            "vol_turnover_pct": round(vol_turnover, 1),     # 폭발일 거래량 회전율(유통주식수 대비 %)
            "source": "live",
            "sector": now.get("sector", ""),  # 0 API
        }
        # 신규 폭발만 catalyst 1회 캡처(폭발 당일=신선). 시도하면 cause_done=True로 동결 →
        # 무뉴스 종목(cause_summary="")도 매 회차 재fetch 안 함. cause_titles는 재매집 시점
        # match_events 0 API 재계산용. dry-run은 생략(저장 안 하므로 무의미한 fetch 방지).
        key = f"{trade_date}:{row['code']}"
        if not p.dry_run and not (reg["records"].get(key) or {}).get("cause_done"):
            _, theme, cause_summary, raw_titles = _explain_cause(row["code"], row["name"], now.get("sector", ""))
            rec_new["theme"] = theme  # 순수 테마 카테고리(없으면 "") — fade와 동일, sector 폴백 안 함
            rec_new["cause_summary"] = cause_summary
            # 원본 제목 전체(필터 전) — 재매집 match_events가 scan_one(raw_titles)과 동일 매칭하도록
            rec_new["cause_titles"] = raw_titles
            rec_new["cause_done"] = True
        _upsert_explosion(reg, rec_new)
        live_dates.add(trade_date)
        today_explosions.append({
            "code": row["code"],
            "name": row["name"],
            "sector": now.get("sector", ""),
            "high_pct": round(high_pct, 2),
            "vol_turnover_pct": round(vol_turnover, 1),
            "value_eok": round(value_won / 1e8),
            "price": now.get("price"),
            "change_pct": round(float(now.get("change_pct") or 0), 2),
            "backfill": False,   # 랭킹 잔류(라이브) 행 — 현재가/등락률 실시간
        })
        count += 1
    _merge_trading_days(reg, live_dates)
    # 유동비율 소스(wisereport) 광역 장애 가드: 22% 고가를 통과한 종목이 다수인데 전부 유동비율
    # 결측으로 폭발 0건이면, '조용한 깨끗한 레이더'가 실은 float 소스 장애일 수 있다 → 로그로 surface
    # (캐시 7일 보호로 흔치 않아 exit은 하지 않고 경고만 — 운영자가 cron 로그에서 인지).
    if count == 0 and high_pass >= 3 and float_missing == high_pass:
        log(f"[warn] 22% 고가 통과 {high_pass}종목 전부 유동비율 결측 → 폭발 0건. "
            f"wisereport(float) 소스 장애 의심(캐시 만료/차단). data/float_ratio.json 확인 권장.")
    # KIS/네이버 도달성: 한 시장이라도 랭킹 수집이 실패(rank_fail>0)했거나, 양 시장 모두 빈손이거나,
    # price_now가 절반 이상 실패하면 '부분장애'로 본다 — 한 시장만 죽고 다른 시장 행이 있어 거짓 '깨끗'
    # 게시(그 시장 폭발 누락)되는 것을 방지(개편 전 단일시장 실패 전파 동작 복원).
    # ⚠ rank_fail==0이면 네이버 랭킹 수집은 정상 — up_rank_total==0(200+빈 stocks: 휴장일/장전/빈 랭킹)도
    # '정상 빈 상태'로 통과시켜 거짓 수집장애 exit(3)·stale 미게시를 막는다. 실제 장애는 랭킹 예외(rank_fail>0)
    # 또는 price_now 과반 실패만으로 본다(휴장일은 분봉 날짜필터로 어차피 0건 안전 게시).
    scan_ok = rank_fail == 0 and not (
        attempted > 0 and price_errors >= max(2, (attempted + 1) // 2))
    return count, scan_ok, today_explosions, youtong_candidates


def _forecast_rank_key(e):
    """/forecast 폭발 종목 순위(폭발순위기준.md): ① 유통주식 회전율 90~130 밴드 종목이 최상위
    (그 안에선 당일 거래대금 내림차순) ② 130 초과는 그 아래, 회전율 오름차순(클수록 뒤로 — 흔히 저유동
    품절주 펌프)·거래대금 보조. 폭발 게이트가 회전율≥90 보장이라 90~130=정상 밴드. 라이브/백필 구분 없이
    순수 기준만(백필은 '랭킹 밀림' 배지·실시간 현재가로 구분). 균일 길이 3-튜플(tier가 먼저라 밴드 안/밖 불혼합)."""
    t = float(e.get("vol_turnover_pct") or 0)   # 유통주식 회전율(%)
    v = float(e.get("value_eok") or 0)          # 당일 거래대금(억)
    if 90 <= t <= 130:
        return (0, -v, 0.0)   # 밴드[90~130]: 거래대금 내림차순
    return (1, t, -v)         # 130 초과: 회전율 오름차순(높을수록 뒤로) + 거래대금 내림차순 보조


def _backfill_today_explosions(today_explosions, reg, today):
    """/forecast '당일 폭발' 리스트 안정화: 오전에 폭발해 registry에 든 종목이 오후 네이버 up 랭킹
    상위에서 밀려 이번 회차 라이브 스캔에 안 잡혀도(또는 스캔이 예외로 실패해도), registry의
    오늘(peak_date==today) 레코드로 백필해 리스트가 장중에 깜빡이지 않게 한다. 고가·회전율은 폭발
    시점(stored)값이지만 **현재가·현재 등락률은 종목별 price_now로 실시간 조회**해 카드에 채운다
    (registry의 peak_change_pct는 장중 스냅샷 max 병합이라 '현재 등락률'이 아니므로 쓰지 않는다 —
    조회 실패 시에만 None). 폭발순위기준(_forecast_rank_key)으로 정렬. 라이브 스캔 try 밖에서 호출."""
    seen_today = {e["code"] for e in today_explosions}
    for r in reg.get("records", {}).values():
        if r.get("peak_date") != today or r.get("code") in seen_today:
            continue
        if r.get("vol_turnover_pct") is None:  # 새 정의로 적재된 레코드만(구 게이트 레코드 제외)
            continue
        price = change_pct = None   # 현재가/현재 등락률 — 유효 조회 시에만 채움(실패·결측·0이면 None=미표시)
        try:
            now = kis.price_now(r["code"])  # 가격·등락률은 J(KRX 공식) 1콜이면 충분(거래대금 미사용)
            pr = now.get("price")
            # KIS _f()가 결측 필드를 0.0으로 강제하므로 'price is None'으론 글리치를 못 거른다 →
            # 0/음수 현재가(거래정지·응답 글리치)는 유효가로 보지 않고 미표시(거짓 '0.00%' 방지).
            if pr and pr > 0:
                price = pr
                change_pct = round(float(now.get("change_pct") or 0), 2)
        except Exception as e:
            log(f"  [warn] 백필 현재가 조회 실패 {r.get('name') or r['code']}: {e}")
        today_explosions.append({
            "code": r["code"],
            "name": r.get("name") or r["code"],
            "sector": r.get("sector", ""),
            "high_pct": round(float(r.get("peak_high_pct") or 0), 2),
            "vol_turnover_pct": r.get("vol_turnover_pct"),
            "value_eok": int(float(r.get("peak_value_eok") or 0)),
            "price": price,            # 현재가(실시간 조회) — 고가·회전율은 폭발 시점값
            "change_pct": change_pct,  # 현재 등락률(실시간 조회) — 조회 실패 시에만 None
            "backfill": True,          # 랭킹에서 밀린 종목(폭발은 오늘, 현재가는 실시간)
        })
        seen_today.add(r["code"])
    # 폭발순위기준(폭발순위기준.md): 회전율 90~130 밴드 + 거래대금 최다 우선, 130 초과는 회전율 높을수록 뒤로.
    # 라이브/백필 구분 없이 순수 기준만(백필은 '랭킹 밀림' 배지·실시간 현재가로 이미 구분 — 거래대금 큰 폭발이면 #1 가능).
    today_explosions.sort(key=_forecast_rank_key)
    return today_explosions


def _load_youtong_registry(path=YOUTONG_REGISTRY_PATH, today=None):
    """youtong 당일 지속 상태 {date, codes:{code:{first_seen,name,sector,high_pct,vol_turnover_pct,value_eok}}}.
    date가 오늘이 아니면(전일자) 리셋 — 매일 새로 시작. 손상 파일도 안전하게 빈 상태."""
    today = today or _today_yyyymmdd()
    try:
        d = json.load(open(path, encoding="utf-8"))
        if isinstance(d, dict) and d.get("date") == today and isinstance(d.get("codes"), dict):
            d.setdefault("history", {})
            return d
        if isinstance(d, dict) and isinstance(d.get("codes"), dict):
            # 날짜 교체 — 지난 날 youtong 코드를 이력으로 롤링(폭발 미등록 종목의 급소/저점매집 추적 풀,
            # 회장님 지시 2026-07-03: 덕신·가온처럼 고가 폭발인데 회전율 게이트 탈락한 종목 커버).
            hist = d.get("history") if isinstance(d.get("history"), dict) else {}
            if d.get("date") and d["codes"]:
                hist[d["date"]] = {c: (v.get("name") or c) for c, v in d["codes"].items()}
            hist = {k: hist[k] for k in sorted(hist)[-YOUTONG_TRACK_DAYS:]}   # 최근 N일만 보관
            return {"date": today, "codes": {}, "history": hist}
    except Exception:
        pass
    return {"date": today, "codes": {}, "history": {}}


def _save_youtong_registry(reg, path=YOUTONG_REGISTRY_PATH):
    try:
        tmp = path + ".tmp"
        json.dump(reg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception as e:
        log(f"[warn] youtong registry 저장 실패: {e}")


def prepare_youtong(candidates, p, explosion_codes=None, now=None, registry_path=None):
    """/youtong '곧 폭발할 후보'(위로 올라오며 분출) — 싼 게이트(등락률≥7·회전율≥50·미폭발) 통과 후보 중
    '시작시각(09:30) 이후 5분봉 양봉(몸통%≥1.5%) 스파크 ≥1회'를 만족하면 당일 registry에 적재 → **종일 지속**.
    한 번 들면 장 마감까지 유지(현재가/등락률은 매 회차 갱신, first_seen='처음 포착 HH:MM' 보존). 단 그 사이
    **폭발(고가≥22 AND 회전율≥90)로 승격한 종목은 /forecast로 분류되므로 youtong에서 제거**(explosion_codes, 역할 분리).
    분봉은 신규 후보만 조회(이미 적재면 스킵). 단 스파크 미발생 후보는 매 회차 재조회된다 — 스파크는 누적이라
    '나중에 떴나' 확인이 필요(미발생 풀=change≥7 AND 회전율≥50 교집합이라 작음, 지연만 영향·정합성 무관).
    시작시각 전엔 빈 목록. now/registry_path/explosion_codes는 주입용(기본 실시각·기본 경로·빈 집합)."""
    now = now or datetime.now(KST)
    if now.strftime("%H%M") < p.youtong_start:   # 감지 시작 시각(예 0930) 전 — 아무것도 포착 안 함
        return []
    explosion_codes = explosion_codes or set()
    start_colon = p.youtong_start[:2] + ":" + p.youtong_start[2:]  # "0930" → "09:30"(스파크 time 비교용)
    path = registry_path or YOUTONG_REGISTRY_PATH
    reg = _load_youtong_registry(path, now.strftime("%Y%m%d"))
    codes = reg["codes"]
    cand_by_code = {c["code"]: c for c in candidates}
    # 1) 신규 후보만 5분봉 스파크 확정 → registry 적재(이미 있으면 분봉 재조회 스킵).
    # ⚠ 폭발 승격 종목도 적재(회장님 지시: 폭발해도 youtong에서 삭제 말고 유지). 스파크는 충족해야 적재.
    for code, c in cand_by_code.items():
        if code in codes:
            continue
        try:
            bars = _minute_bars_with_fallback(code, c.get("name"))
        except Exception as e:
            log(f"  [skip] youtong 분봉 실패 {c.get('name') or code}: {e}")
            continue
        sparks = [b for b in reignition_bars(bars, p.reignition_body_pct, p.reignition_span_min)
                  if b["time"] >= start_colon]   # 시작시각 이후 양봉 스파크만(위로 올라오는 신호)
        if len(sparks) >= p.youtong_spark_min:
            codes[code] = {
                "first_seen": now.strftime("%H:%M"),
                "name": c.get("name") or code, "sector": c.get("sector", ""),
                "high_pct": c.get("high_pct"), "vol_turnover_pct": c.get("vol_turnover_pct"),
                "value_eok": c.get("value_eok"),
            }
    # 2) registry(오늘 적재분) 전체를 youtong[]로 렌더 — 종일 지속. 현재가는 실시간 갱신.
    out = []
    for code, rec in list(codes.items()):
        # (폭발 승격해도 youtong 유지 — 회장님 지시. /forecast와 병행 노출, '🔥 폭발' 배지로 구분)
        # 적재값 회전율·고가·거래대금은 항상 숫자(후보 게이트가 보장) — 손상 레코드(수동편집 등)면 스킵·제거
        # (웹 카드가 toLocaleString을 호출해 None이면 크래시 → 방어).
        rvt, rhigh, rval = rec.get("vol_turnover_pct"), rec.get("high_pct"), rec.get("value_eok")
        if not all(isinstance(v, (int, float)) for v in (rvt, rhigh, rval)):
            codes.pop(code, None)
            continue
        c = cand_by_code.get(code)
        if c:   # 이번 회차 랭킹에 다시 잡힘 — fresh 값
            price, change_pct = c.get("price"), c.get("change_pct")
            vt, high_pct, value_eok = c.get("vol_turnover_pct"), c.get("high_pct"), c.get("value_eok")
        else:   # 랭킹서 밀린 지속 종목 — 현재가만 재조회(백필식), 회전율·고가는 적재값 유지
            price = change_pct = None
            try:
                nowq = kis.price_now(code)
                pr = nowq.get("price")
                if pr and pr > 0:   # 유효 현재가일 때만 — KIS _f()가 결측을 0.0으로 강제하므로 0/음수는 미상 처리
                    price = pr
                    change_pct = round(float(nowq.get("change_pct") or 0), 2)
            except Exception as e:
                log(f"  [warn] youtong 지속 현재가 조회 실패 {rec.get('name') or code}: {e}")
            vt, high_pct, value_eok = rvt, rhigh, rval
        out.append({
            "code": code, "name": rec.get("name") or code, "sector": rec.get("sector", ""),
            "change_pct": change_pct, "high_pct": high_pct, "vol_turnover_pct": vt,
            "value_eok": value_eok, "price": price, "first_seen": rec.get("first_seen"),
            "exploded": code in explosion_codes,   # 폭발 승격 여부(youtong 유지·🔥 배지·/forecast 병행)
        })
    if not p.dry_run:
        _save_youtong_registry(reg, path)
    return out


def prepare_reaccum_registry(p):
    """(active_explosions, live_scan_ok, today_explosions, today_youtong) 반환. live_scan_ok=False면
    폭발감시 자체가 전면 실패 = KIS/네이버 장애 신호 → 호출부의 수집장애 가드가 사용한다.
    today_explosions=/forecast 당일 폭발, today_youtong=/youtong '곧 폭발할 후보'(종일 지속)."""
    if not p.reaccum_enabled:
        return {}, True, [], []
    reg = load_explosion_registry()
    seed_count = bootstrap_seed_explosions(reg, p)
    live_scan_ok = True
    today_explosions = []
    youtong_candidates = []
    try:
        live_count, live_scan_ok, today_explosions, youtong_candidates = update_live_explosions(reg, p)
    except Exception as e:
        live_count = 0
        live_scan_ok = False  # 폭발 스캔 전면 실패(raise) — KIS/네이버 장애 의심
        log(f"[warn] 라이브 폭발 감시 실패(기존 registry/seed만 사용): {e}")
    # 6일 소급 폭발 백필 — 등락률 상위∪레지스트리 재검증으로 prior-day 폭발 후보 풀을 채운다(오늘 수상종목용).
    # 실패해도 본작업 안 깨지게 격리(표시 전용 후보 풀 보강).
    try:
        backfill_window_explosions(reg, p)
    except Exception as e:
        log(f"[warn] 6일 소급 백필 실패(라이브/시드만 사용): {e}")
    # 라이브 스캔 성공/예외 무관하게 registry 기반 백필 — 예외 경로에서도 /forecast가 비지 않게(try 밖).
    today_explosions = _backfill_today_explosions(today_explosions, reg, _today_yyyymmdd())
    # youtong: 싼 게이트 통과분(candidates)을 5분봉 스파크로 확정 + 종일 지속(별도 registry). try 밖(격리).
    # 폭발(/forecast)로 승격한 종목은 youtong서 제거하도록 today_explosions 코드를 넘긴다(역할 분리).
    try:
        explosion_codes = {e.get("code") for e in today_explosions}
        # 폭발(라이브+백필) 종목을 youtong 후보로 보강 — scan(update_live_explosions)이 간헐/구조적으로
        # 종목을 누락해도(예: price 조회 실패) 폭발 종목은 today_explosions(registry 백필)에 남으므로,
        # 그걸 youtong 후보로 흡수해 '한 번 폭발=적중이면 youtong 유지'(회장님 지시)를 보장. 스파크는 prepare가 확정.
        _yt_codes = {c["code"] for c in youtong_candidates}
        for e in today_explosions:
            if e.get("code") in _yt_codes:
                continue
            youtong_candidates.append({
                "code": e["code"], "name": e.get("name"), "sector": e.get("sector", ""),
                "change_pct": e.get("change_pct"), "high_pct": e.get("high_pct"),
                "vol_turnover_pct": e.get("vol_turnover_pct"), "value_eok": e.get("value_eok"),
                "price": e.get("price"), "exploded": True,
            })
        today_youtong = prepare_youtong(youtong_candidates, p, explosion_codes)
    except Exception as e:
        today_youtong = []
        log(f"[warn] youtong 처리 실패(빈 목록): {e}")
    if not p.dry_run:
        save_explosion_registry(reg)
    elif seed_count or live_count:
        log("[radar] dry-run: 폭발 레지스트리 저장 생략")
    active = _recent_active_explosions(reg, p.explosion_window, p.reaccum_track_days)
    # youtong 이력(폭발 미등록·회전율 게이트 탈락 종목) 병합 — 급소/저점매집 전용 추적(_yt_track).
    # 덕신(고가 4일 연속 +22%인데 회전 미달)·가온전선 사각지대 커버(회장님 지시 2026-07-03).
    try:
        yreg = _load_youtong_registry(YOUTONG_REGISTRY_PATH, _today_yyyymmdd())
        for ydate in sorted(yreg.get("history", {})):
            for ycode, yname in yreg["history"][ydate].items():
                if ycode not in active:
                    active[ycode] = {"code": ycode, "name": yname, "peak_date": ydate, "_yt_track": True}
    except Exception as e:
        log(f"[warn] youtong 이력 풀 병합 실패(무시): {e}")
    n_ext = sum(1 for r in active.values() if r.get("_extended_track") or r.get("_yt_track"))
    log(f"[radar] reaccum registry active={len(active)}(장기추적 {n_ext}) seed={seed_count} live={live_count} "
        f"당일폭발={len(today_explosions)} youtong={len(today_youtong)}")
    return active, live_scan_ok, today_explosions, today_youtong


# ── 익일~3일 상승확률 예측(동결 모델) — 전종목 6개월 백테스트로 보정, holdout 검증치 ──
# 정직: "보장"이 아니라 과거 실측 확률. 강 모멘텀 상위군(z≥경계)만 holdout서 유의(53% TEST),
# 중·하는 분리 안 돼 base로 둠. 표시 전용(score_raw 미반영). 라이브로 calibration 지속 보정 예정.
FORECAST_MEAN = {"mom3": 2.1624, "mom5": 5.2731, "ma20_gap": 11.9678, "vol_surge": 1.0054}
FORECAST_STD = {"mom3": 11.5152, "mom5": 17.6902, "ma20_gap": 17.5081, "vol_surge": 1.4395}
FORECAST_STRONG_Z = 0.3617   # 상위 tercile 경계(train)
FORECAST_BASE_3D7 = 46       # 재매집 후보 전체 3일내+7% 터치(과거 실측, 시장 19% 대비)
FORECAST_STRONG_3D7 = 52     # 강 모멘텀 상위군(holdout TEST ~53, 보수적 52)
FORECAST_NEXT7 = 25          # 내일(1일) +7% 터치 — 정직 공개(낮음)


def forecast_prob(mom3, mom5, ma20_gap, vol_surge):
    """동결 모델로 '3일 내 +7% 터치' 과거 실측 확률 라벨 산출(표시 전용·보장 아님)."""
    feats = {"mom3": mom3, "mom5": mom5, "ma20_gap": ma20_gap, "vol_surge": vol_surge}
    z = sum((feats[k] - FORECAST_MEAN[k]) / FORECAST_STD[k] for k in FORECAST_MEAN)
    strong = z >= FORECAST_STRONG_Z
    return {
        "horizon": "3일 내 +7%",
        "prob_pct": FORECAST_STRONG_3D7 if strong else FORECAST_BASE_3D7,
        "base_pct": FORECAST_BASE_3D7,
        "strong": strong,            # 강 모멘텀 상위군(holdout 검증)
        "next_day_7_pct": FORECAST_NEXT7,  # 내일 1일 기준은 낮음 — 정직 표기
        "note": "과거 실측 확률·보장 아님",
    }


def _reaccum_eligible(rec, p):
    """이 레코드가 reaccum(반등조짐) 후보 대상인가 — 새 폭발 정의로 검증(vol_turnover_pct 적재)되고
    고가등락률 ≥ 임계인 레코드만. 구 게이트(거래대금 1,500억/13%) legacy 레코드는 vol_turnover_pct가
    없어 제외. scan_reaccum_candidate의 KIS 호출 직전 게이트와 high_fail 가드 분모가 공유한다."""
    return (rec.get("vol_turnover_pct") is not None
            and float(rec.get("peak_high_pct") or 0) >= p.explosion_high_pct)


def scan_reaccum_candidate(rec, p, events):
    """최근 6거래일 폭발(고가≥22% AND 거래량/유통주식수≥90%) 종목이 **14:30~장종료** 5분봉 양봉(몸통%≥1.5%)이
    2회 이상 스파크(마감 직전 재분출) AND **현재 등락률 −5%~+7%**(깊은 식음/이미 분출 제외)인 '재매집' 후보를
    만든다. 폭발→식음→재반등 흐름으로 노출되는 건 동일하되 두 게이트로 한정(MA20·투신·거래원 게이트는 미사용)."""
    code, name = rec["code"], rec.get("name") or rec["code"]
    peak_date = rec.get("peak_date")
    if not peak_date:
        return None
    # 새 폭발 정의(고가≥22% AND 거래량/유통주식수≥90%)로 적재된 레코드만 후보로 본다. 개편 전(거래대금
    # 1,500억/13% 게이트) 레코드는 vol_turnover_pct가 없어 — 재검증 없이 식음·반등 후보로 새는 걸 차단
    # (마이그레이션 윈도 ~6일간 정의 불일치 후보 방지. 구 레코드는 윈도 만료로 자가 소거).
    if not _reaccum_eligible(rec, p) and not rec.get("_yt_track"):
        return None   # youtong 이력 추적분은 폭발 검증 필드가 없어도 급소/저점매집 감시 대상
    try:
        now = kis.price_now_jmoney_un(code)  # 가격=J 공식 / 거래대금·거래량=UN 통합(표시·vsurge)
    except Exception as e:
        log(f"  [skip] {name}: reaccum 현재가 조회 실패 {e}")
        return "ERR"
    if not now.get("price") or not now.get("prev_close"):
        return None
    change_pct = round(float(now.get("change_pct") or 0), 2)  # 현재 등락률(전일 종가 대비)
    change_basis = "KRX"
    # NXT 애프터마켓 야간가로 '현재 등락률' 재평가 — NXT 시간외 회복(정규장 +8%→NXT −5%, −9%→−5%)이면 밴드
    # 진입, 이탈이면 빠짐(스파크는 정규장 것 유지 — NXT 분봉 미제공). **재평가 시작은 16:00(=15:30+신선도상한
    # 30분)부터** — 그 전(15:31~15:51)엔 정규장 막판 5분 양봉이 아직 신선해 텔레그램으로 나가야 하므로 KRX 유지.
    # 16:00이면 막판 봉은 이미 상한 초과 + NXT 애프터마켓 단일가가 실제 체결되는 시점이라 야간가도 그때 존재.
    if datetime.now(KST).strftime("%H%M") >= NXT_REEVAL_START_HHMM:
        nxt_chg = _nxt_change_pct(code, now.get("prev_close"))
        if nxt_chg is not None:
            change_pct, change_basis = nxt_chg, "NXT"
    # 현재 등락률 게이트: −5%~+7% 밖이면 원칙 제외(깊은 식음/이미 분출) — 단 🎯 매수급소(14:30↑ 2%+ 양봉
    # ≥2회)는 밴드 무제한(회장님 지시 2026-07-03: 폭락 중이든 급등 중이든 급소가 뜨는 날이 매수 시점).
    # 급소는 14:30 이후에만 발동 가능 → 그 전엔 밴드 밖 후보를 기존처럼 조기 컷(비용 절감 유지).
    in_band = (p.reaccum_change_min <= change_pct <= p.reaccum_change_max)
    _hhmm = datetime.now(KST).strftime("%H%M")
    if not in_band and _hhmm < p.reignition_start and change_pct > p.lowaccum_change_max:
        return None   # 예외: ≤−10% 폭락 중이면 🧲 저점매집 후보라 오전에도 판정 진행
    # 장기 추적분(폭발 후 6일 초과, _extended_track)은 '🎯 매수급소 전용' — 급소는 14:30 이후에만 발동
    # 가능하므로 그 전엔 스킵(풀 ~100종목 × 일봉·분봉 조회를 오전 내내 하지 않는 비용 가드).
    if ((rec.get("_extended_track") or rec.get("_yt_track")) and _hhmm < p.reignition_start
            and change_pct > p.lowaccum_change_max):
        return None
    high = now.get("high") or now["price"]
    high_pct = (high / now["prev_close"] - 1) * 100
    try:
        daily = kis.daily_prices_jmoney_un(code, days=25)  # MA20·vsurge·forecast 피처용(가격=J / 거래대금=UN)
    except Exception as e:
        log(f"  [skip] {name}: reaccum 일봉 실패 {e}")
        return "ERR"
    closes = [d["close"] for d in daily if d.get("close")]
    if len(closes) < 20:
        return None
    signal_date = (now.get("date") or (daily[-1].get("date") if daily else "") or "").strip()
    if signal_date != _today_yyyymmdd() or signal_date <= peak_date:
        return None
    ma20 = sum(closes[-20:]) / 20
    ma10 = sum(closes[-10:]) / 10
    # 장기 추적(폭발 후 6일 초과) 후보는 '20일선 위인 동안만' 유지 — 깨지면 추적 종료(추세 사망, 회장님 정의).
    if (rec.get("_extended_track") or rec.get("_yt_track")) and now["price"] < ma20:
        return None
    # ── 반등 게이트: **14:30~장종료** 5분봉 양봉 몸통%≥1.5% 스파크가 REIGNITION_MIN_COUNT(2)회 이상(마감 직전
    #    재분출). 시작시각(reignition_start) 이전 양봉은 미집계. 스파크는 **그 봉의 절대 등락률과 무관하게** 센다 —
    #    −9%에서 양봉으로 회복해 −5% 마감한 깊은 식음 반등도 잡아야 하므로(현재 등락률 게이트[−5~+7]가 최종 위치만 판정).
    # 분봉도 거래대금·수급과 동일하게 MONEY_MARKET(기본 UN). 정규장 시간창 가드(kis_client)로 NXT 장 밖 봉 배제.
    try:
        bars = _minute_bars_with_fallback(code, name)  # UN 우선, 결측 시 J 폴백(공용 헬퍼)
    except Exception as e:
        log(f"  [skip] {name}: reaccum 분봉 실패 {e}")
        return "ERR"
    reign_start_colon = p.reignition_start[:2] + ":" + p.reignition_start[2:]  # "1430" → "14:30"
    all_bars = reignition_bars(bars, p.reignition_body_pct, p.reignition_span_min)  # 전 시간대 1.5%+ 양봉
    rbars = [b for b in all_bars if b["time"] >= reign_start_colon]  # 14:30↑(기존 재매집·급소 판정용)
    # 🧲 저점매집 판정 — 폭락(≤−10%) 중인데 20일선 사수 + '시간 무관' 몸통 2%+ 양봉 ≥3회(회장님 지시 2026-07-03:
    # 덕신 7/3 −16% 폭락 바닥에서 11시부터 2%+ 스파크 4방 = 주포가 눌러놓고 밑에서 받는 지문).
    labars = [b for b in all_bars if b["body_pct"] >= p.lowaccum_body_pct]
    low_accum = (change_pct <= p.lowaccum_change_max and now["price"] >= ma20
                 and len(labars) >= p.lowaccum_min_count)
    # 🎯 매수급소 판정 — 14:30↑ 몸통 2%+ 양봉 ≥2회(회장님 15년 신호: '큰손이 아직 받치고 있다'는 지문.
    # 덕신 7/2 5.67/3.11% 2개=익일 폭락에도 안심 vs 상지 1.51% 1개=받침 없음).
    gbars = [b for b in rbars if b["body_pct"] >= p.geupso_body_pct]
    geupso = len(gbars) >= p.geupso_min_count
    # 통과 조건: ①기존 재매집(14:30↑ 1.5% ≥2회) ②급소 ③저점매집 중 하나. 밴드 밖·장기추적·youtong이력분은
    # 급소/저점매집일 때만(기존 6일 폭발 재매집 의미론 불변 — 순수 확장).
    reignition_ok = len(rbars) >= p.reignition_min_count
    if not (reignition_ok or geupso or low_accum):
        return None
    if not in_band and not (geupso or low_accum):
        return None
    if (rec.get("_extended_track") or rec.get("_yt_track")) and not (geupso or low_accum):
        return None
    # 대표(최대 몸통) 봉 — 표시용. 저점매집 전용(14:30 봉 없음)이면 저점매집 봉에서 선정.
    reignition = max(rbars or labars, key=lambda b: b["body_pct"])

    ma10_margin = (now["price"] / ma10 - 1) * 100 if ma10 else 0.0
    ma20_margin = (now["price"] / ma20 - 1) * 100 if ma20 else 0.0
    # ── 변별 점수(표시 전용 '강도') — 검증된 적중확률이 아니라 셋업을 얼마나 강하게 충족했나의
    #    순위. raw(score_raw)는 0 유지 = 실험 격리라 코어 튜닝에 미반영(표본 쌓이면 데이터로 검증).
    re_body_max = max((b["body_pct"] for b in rbars), default=0.0)
    # 회전율(유통주식수 대비, 거래량 기준) — 폭발일 회전율은 registry 저장값(vol_turnover_pct, 위 게이트로
    # 항상 존재). 당일 회전율 = 당일 거래량 / 유통주식수(float_ratio.vol_turnover 공유 산식).
    fr, flisted = float_ratio.get_float_and_listed(code)  # 유동비율·발행주식수(보통주만, 캐시)
    turnover_pct = float_ratio.vol_turnover(float(now.get("volume") or 0), fr, flisted)
    # basis는 '실제 산출된 값'에 맞춘다 — 거래량 0 글리치(turnover_pct=None)면 "cap"(값 없음과 일관).
    turnover_basis = "float" if turnover_pct is not None else "cap"
    peak_turnover_pct = rec.get("vol_turnover_pct")  # 새 게이트 통과 레코드라 항상 non-None
    # 폭발일 마감강도 — 항상 '완결된 폭발일 일봉'(EOD)에서 산출. peak_date < signal_date(가드)라 그 일봉은
    # 완성된 종가다 → 장중 proxy로 인한 오염 없음(전진검증·표시 전용, 점수 미반영).
    peak_ibs = peak_uppertail = None
    pbar = next((d for d in daily if d.get("date") == peak_date), None)
    if pbar:
        peak_ibs, peak_uppertail = _close_strength(pbar.get("high"), pbar.get("low"), pbar.get("close"))
    breakdown = {
        "base": REACCUM_SCORE,
        # 게이트 최소 횟수(min_count)를 0점 기준으로 — 최소통과=baseline, 초과분만 가점(임계 바꿔도 정합).
        "re_count": min(10, max(0, (len(rbars) - p.reignition_min_count + 1) * 2)),  # min→2·+1→4·+2→6·+3→8·+4↑→10
        "re_body": round(min(6, max(0, (re_body_max - p.reignition_body_pct) / max(0.5, 6 - p.reignition_body_pct) * 6))),  # 최대 몸통% 게이트~6%→0~6(게이트 임계 정합)
        "peak_turnover": round(min(10, max(0, ((peak_turnover_pct or 0) - 90) / 110 * 10))),  # 폭발일 회전율 90~200%→0~10
        "re_turnover": round(min(6, max(0, ((turnover_pct or 0) - 30) / 70 * 6))),  # 당일 회전율 30~100%→0~6
    }
    score = min(95, REACCUM_SCORE + breakdown["re_count"]
                + breakdown["re_body"] + breakdown["peak_turnover"] + breakdown["re_turnover"])
    raw_breakdown = {k: 0 for k in breakdown}
    # 익일~3일 상승확률 라벨(표시 전용·보장 아님) — 동결 모델. daily/now에서 0 API로 피처 산출.
    vals_d = [d.get("value") or 0 for d in daily]
    nz = [x for x in vals_d[-21:-1] if x > 0]   # 0/결측 거래일 제외 — 희박 거래 시 vsurge 폭증→오라벨 방지
    av20v = sum(nz) / len(nz) if len(nz) >= 10 else 0.0   # 유효 거래일 10일+ 일 때만
    vsurge = (now.get("value") or 0) / av20v if av20v > 0 else 1.0
    mom3 = (closes[-1] / closes[-4] - 1) * 100 if len(closes) >= 4 and closes[-4] else 0.0
    mom5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 and closes[-6] else 0.0
    forecast = forecast_prob(mom3, mom5, ma20_margin, vsurge)
    # 원인/테마("왜 올랐나"): 폭발시점 캡처본(registry) 우선 — 폭발 당일 catalyst라 신선(0 API).
    # 없으면(seed/과거폭발) 재매집 시점 보강 fetch. 표시 전용 — 점수 미반영.
    theme = rec.get("theme") or ""
    cause_summary = rec.get("cause_summary") or ""
    if rec.get("cause_done"):  # 폭발시점 캡처됨 — 0 API(캐시된 원본 제목으로 이벤트만 재계산)
        news_items, titles = [], (rec.get("cause_titles") or [])
    else:                      # seed/과거폭발 — 재매집 시점 보강 fetch (titles=원본, scan_one과 동일 매칭)
        news_items, theme, cause_summary, titles = _explain_cause(code, name, now.get("sector", ""))
    try:  # 이벤트 민감도 — 캐시/보강 경로 무관하게 항상 계산(표시 일관). 실패해도 빈값.
        matched_events, _ = match_events(events, titles, now.get("sector", ""))
    except Exception:
        matched_events = []
    # theme는 순수 카테고리(없으면 "") — fade(scan_one)와 동일, sector 폴백 안 함(그룹핑 대칭)
    return {
        "code": code,
        "name": name,
        "sector": now.get("sector", ""),
        "pattern": "reaccum",
        "geupso": geupso,               # 🎯 매수급소 — 14:30↑ 몸통 2%+ 양봉 ≥2회(밴드 무제한·장기추적 포함)
        "geupso_bars": [{"time": b["time"], "body_pct": b["body_pct"]} for b in gbars],
        "shake": None,
        "deep_shake": None,
        "suspicion_score": score,
        "calibrated_prob": None,
        "score_breakdown": breakdown,
        "score_raw": 0,
        "score_breakdown_raw": raw_breakdown,
        "forecast": forecast,   # 3일내 +7% 과거 실측 확률 라벨(표시 전용·보장 아님)
        "price": now["price"],
        "change_pct": change_pct,
        "change_basis": change_basis,   # "KRX"(정규장) / "NXT"(마감 후 시간외 야간가로 등락률 재평가)
        "high_pct": round(high_pct, 2),
        "value_eok": round(float(now.get("value") or 0) / 1e8),
        "turnover_pct": turnover_pct,   # 당일 회전율(거래량/유통주식수 %) — 손바뀜 강도
        "peak_turnover_pct": peak_turnover_pct,  # 폭발일 회전율(거래량/유통주식수 %) — 폭발의 자명함
        "float_ratio": fr,              # 유동비율(0~1) — None이면 회전율 미산출
        "turnover_basis": turnover_basis,  # "float"(유통 기준) | "cap"(미상)
        "ma10": round(ma10, 1),
        "ma10_margin_pct": round(ma10_margin, 2),
        "spark": {"clusters": []},
        "spark_max_x": None,   # reaccum은 분봉 스파크 클러스터 없음(None=미산출) — 레거시 spark_flow 통계가 제외
        "spark_max_pct": None,
        "mega_flow": False,
        # 재반등(오늘) 신호 — 대표(최대 몸통) 5분 스파크
        "reignition": {
            "body_pct": reignition["body_pct"],
            "time": reignition["time"],
            "value_eok": reignition["value_eok"],
            "count": len(rbars),   # 14:30 이후 자격 양봉 스파크 수(게이트 ≥2)
        },
        # 당일 자격 5분 스파크 전체 — 텔레그램 봉단위 알림용(표시는 reignition 대표봉만 사용).
        # 저점매집 전용(14:30 봉 없음)이면 저점매집 2%+ 봉을 실어 신선봉 텔레그램이 그대로 작동.
        "reignition_bars": [{"time": b["time"], "body_pct": b["body_pct"], "value_eok": b["value_eok"]}
                            for b in (rbars or labars)],
        "low_accum": low_accum,          # 🧲 저점매집 — 폭락(≤−10%)+20일선 사수+시간무관 2%+ 스파크 ≥3회
        "low_accum_bars": [{"time": b["time"], "body_pct": b["body_pct"]} for b in labars] if low_accum else [],
        "news": news_items,
        "matched_events": matched_events,
        "theme": theme,
        "visible_experimental": True,
        "reaccum": {
            "peak_date": peak_date,
            "peak_value_eok": int(float(rec.get("peak_value_eok") or 0)),
            "peak_high_pct": round(float(rec.get("peak_high_pct") or 0), 2),
            "peak_turnover_pct": peak_turnover_pct,  # 폭발일 거래량 회전율(유통주식수 대비 %)
            "peak_ibs": peak_ibs,         # 폭발일 마감강도(0=저가마감·1=고가마감) — 표시·전진검증용
            "peak_uppertail": peak_uppertail,  # 폭발일 윗꼬리%((고가−종가)/종가) — 약마감일수록 큼
            "ma20": round(ma20, 1),
            "ma20_margin_pct": round(ma20_margin, 2),
            "cause_summary": cause_summary,  # 폭발 catalyst 한 줄("왜 올랐나")
        },
    }


def _explosion_day_value_eok(code, peak_date, cache):
    """폭발일 일봉 종일 거래대금(억) — 대장 판정용 단위 통일(seed=종일/live=장중 혼용 편향 제거).
    실패하거나 그날 봉이 없으면 None(호출부가 저장값으로 폴백)."""
    key = (code, peak_date)
    if key in cache:
        return cache[key]
    val = None
    try:
        for bar in kis.daily_prices_jmoney_un(code, days=12):  # 대장 판정=폭발일 UN 거래대금
            if bar.get("date") == peak_date:
                eok = round(float(bar.get("value") or 0) / 1e8)
                val = eok if eok > 0 else None  # 0/결측 봉은 글리치 → None으로 폴백 유도
                break
    except Exception as e:
        log(f"  [warn] {code} 폭발일 거래대금 재조회 실패: {e}")
    cache[key] = val
    return val


def _theme_leader_codes(active_explosions):
    """'예전 대장'(was_theme_leader) 코드 집합. 권위 업종(sector) 기준:
    같은 (폭발일, 업종) 폭발군이 LEADER_MIN_GROUP개+ 일 때, 폭발일 일봉 종일 거래대금 1위가
    2위의 LEADER_MARGIN배 이상이면 그 1종목만 대장. regex 테마 오분류·seed/live 단위 혼용·
    근소차 가짜 대장을 모두 차단. sector 없는 폭발은 그룹에서 제외(보수적).
    거래대금은 폭발일 일봉을 재조회해 그룹(=같은 폭발일) 내 완결도를 통일한다(seed 종일값 vs
    live 장중 캡처값의 편향 제거). 표시 전용 뱃지라 호출은 그룹 ≥3 종목에 한정돼 비용은 제한적."""
    groups = {}
    for c, rec in active_explosions.items():
        sec, pdate = rec.get("sector"), rec.get("peak_date")
        if sec and pdate:
            groups.setdefault((pdate, sec), []).append((c, rec))
    leader_codes = set()
    val_cache = {}
    for grp in groups.values():
        if len(grp) < LEADER_MIN_GROUP:
            continue
        ranked = []
        for c, rec in grp:
            v = _explosion_day_value_eok(c, rec.get("peak_date"), val_cache)
            if v is None:
                v = float(rec.get("peak_value_eok") or 0)  # 폴백: 저장값
            ranked.append((c, v))
        ranked.sort(key=lambda cv: cv[1], reverse=True)
        top_code, top_v = ranked[0]
        second_v = ranked[1][1] if len(ranked) > 1 else 0.0
        # second_v>0 필수: 2위 값이 0/결측이면 마진을 가늠할 수 없어(0*1.5=0 무력화) 보수적으로 미인정.
        if second_v > 0 and top_v >= second_v * LEADER_MARGIN:
            leader_codes.add(top_code)
    return leader_codes


def _leader_cohort_prob(path=PERFORMANCE_PATH):
    """performance.json에서 '예전 대장' 코호트 실측 익일 상승률 {rate,n} — 표본 충분(valid)할
    때만, 아니면 None. 표시 전용(수상카드 한 줄). 파일 부재·파싱 실패는 None(격리)."""
    try:
        perf = json.load(open(path, encoding="utf-8"))
        lr = ((perf.get("experimental") or {}).get("leader_reaccum") or {}).get("leader") or {}
        if lr.get("valid") and lr.get("hit_rate") is not None:
            return {"rate": lr["hit_rate"], "n": lr["n"]}
    except Exception:
        pass
    return None


def attach_reaccum_candidates(suspects, active_explosions, p, events):
    """반등조짐 후보를 suspects에 추가한다(반환: (추가 수, 조회실패 수)). reaccum이 유일 산출물이라
    호출 시 suspects는 비어 있어 항상 신규 append — 구 fade 트랙과의 뱃지 병합 경로는 폐지."""
    if not p.reaccum_enabled or not p.reaccum_visible or not active_explosions:
        return 0, 0
    try:  # 대장 판정 실패가 게시 자체를 막지 않게 격리(표시 전용 뱃지) — 모듈 설계 계약
        leader_codes = _theme_leader_codes(active_explosions)
    except Exception as e:
        log(f"  [warn] 예전 대장 판정 실패(뱃지 생략): {e}")
        leader_codes = set()
    cohort_prob = _leader_cohort_prob()  # 대장 코호트 실측률(표본 충분할 때만, 표시 전용)
    added = err_count = 0
    for code, rec in active_explosions.items():
        try:
            r = scan_reaccum_candidate(rec, p, events)
        except Exception as e:  # reaccum의 어떤 실패도 게시 자체를 막지 않게 격리
            log(f"  [skip] {rec.get('name', code)}: reaccum 예외 {e}")
            continue
        if r == "ERR":
            err_count += 1
            continue
        if not r:
            continue
        is_leader = code in leader_codes
        r["reaccum"]["was_theme_leader"] = is_leader  # 폭발일 업종 거래대금 1위(예전 대장)였나
        r["reaccum"]["source"] = rec.get("source", "live")  # live|seed|telegram(채널 언급發)
        # 예전 대장이면 코호트 실측률 한 줄 표시(표본 충분할 때만, 표시 전용·점수 무관)
        r["leader_cohort_prob"] = cohort_prob if is_leader else None
        suspects.append(r)
        added += 1
    return added, err_count


# ---------- 메인 깔때기 ----------

def derive_theme(titles, sector=""):
    """뉴스 제목들+업종 → 상위 테마 1개(없으면 ""). match_sensitivity 단일 소스.
    결정론: hit 최다 → 동률이면 THEMES 선언 순서(우선순위) 앞선 테마. 예: 업종 '전기·전자'가
    SECTOR_HINTS상 반도체·환율 둘 다 매칭(각 hit=1)이면 먼저 선언된 반도체를 택한다(환율 오선택 방지).
    뉴스가 테마에 기여하면 그 count가 더 커 자연히 우선됨. fade·reaccum·폭발 세 경로 공용."""
    hits = match_sensitivity(titles, sector)
    if not hits:
        return ""
    order = list(THEMES)  # 선언 순서 = 우선순위
    return max(hits, key=lambda c: (hits[c], -order.index(c)))


def _explain_cause(code, name, sector=""):
    """'왜 올랐나' — 종목뉴스 fetch → 재료뉴스(score_news, 표시용) + 테마 + 원인요약 + raw_titles.
    (news_items, theme, cause_summary, raw_titles) 반환. **어떤 예외든 빈값(graceful)** — 표시 전용
    메타가 코어 파이프라인을 중단시키지 않게 전체 try(scan_one의 try/except:pass와 동일 철학).
    raw_titles = fetch 원본 제목 전체(score_news 필터·6컷 전) — match_events/derive_theme가 scan_one과
    동일하게 원본으로 매칭하도록(재매집 이벤트/테마 과소매칭 방지). 추가 API = fetch_news 1콜."""
    try:
        raw = [n for n in fetch_news(code, 10) if n.get("title")]
        raw_titles = [n["title"] for n in raw]
        news_items = score_news(raw, make_aliases(name)).get("relevant", [])[:6]
        theme = derive_theme(raw_titles, sector)
        cause_summary = news_items[0].get("title", "") if news_items else ""
        return news_items, theme, cause_summary, raw_titles
    except Exception:
        return [], "", "", []


_RANK_CACHE = {}  # per-run 캐시: (direction,market,page)→(rows,total). radar는 1회성 프로세스라 stale 무관.
                  # update_live_explosions가 같은 (up,market,1)을 여러 번 보므로 중복 네이버 호출 제거.


def _rank_page(direction, market, page):
    ckey = (direction, market, page)
    if ckey in _RANK_CACHE:
        return _RANK_CACHE[ckey]
    url = (f"https://m.stock.naver.com/api/stocks/{direction}/{market}"
           f"?page={page}&pageSize=100")
    d = json.loads(get_bytes(url, UA))
    if "stocks" not in d:  # 응답 스키마 변화 — 조용한 빈 결과 대신 명시 실패 (실패는 캐시 안 함)
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
    result = (rows, int(d.get("totalCount") or 0))
    _RANK_CACHE[ckey] = result
    return result


def scan_shakeout(p, extra_codes=None):
    """💥 흔들기(폭발 직후 고회전 페이드) 스캔 — 회장님 지시 2026-07-03.

    셀: 당일 고가등락 ≥+20% AND 페이드(고가등락−현재등락) ≥15%p AND 당일 유통회전율 ≥40% AND MA20 위
        AND 경고/위험 미지정 AND 과확장 붕괴 아님. 원형 = 금호건설·동양파일 6/25(익일 상한 + 연상).
    소스 = up ∪ down 랭킹(음봉 마감 흔들기는 up에 없음 — 동양파일 6/25 −9.1%가 사각지대였음).
    기존 폭발·youtong 게이트는 불변 — 둘 사이로 빠지는 종목(금호 6/25 회전 66%<90·종가 +5.3%<7%)의 전용 그물.
    표시·전진검증 전용(score_raw=0 통계 격리). 실패는 종목 단위 skip(fail-safe)."""
    cand, seen = [], set()
    for direction in ("up", "down"):
        for market in ("KOSPI", "KOSDAQ"):
            try:
                rows, _ = _rank_page(direction, market, 1)
            except Exception as e:
                log(f"[warn] 흔들기 {direction}/{market} 랭킹 실패: {e}")
                continue
            for row in rows[:p.explosion_scan_n]:
                c, r = row.get("code"), row.get("change_pct")
                if not c or c in seen or r is None:
                    continue
                # 산술 프리필터(KIS 콜 절감): fade≥15 & 고가≤+30(가격제한) → 현재등락 ≤ 30−15+0.5=15.5.
                # ⚠ 상한은 '가격제한 30'이 기준(고가 게이트 20이 아님) — 20을 쓰면 상한이 5.5로 잘못 좁아져
                #    종가 +5.5~+15.5%로 마감한 흔들기(고가 상한권 터치 후 소폭 상승 마감, 예: 남화토건 6/19
                #    고가+28→종가+13→익일고가+12) 승자가 KIS 조회 전 전부 드롭됨(리뷰 2026-07-04 확정 버그).
                if r > 30.0 - SHAKEOUT_FADE_PCT + 0.5 or r < -26:
                    continue
                seen.add(c)
                cand.append(row)
    # 랭킹 사각지대 보완: 이미 추적 중인 youtong/폭발 레지스트리 코드도 후보에 합집합
    #   — 삼기처럼 급등 후 페이드로 등락률 top50에서 밀린 강한 흔들기를 포착(게이트는 그대로 적용).
    for c in (extra_codes or []):
        if c and c not in seen:
            seen.add(c)
            cand.append({"code": c, "name": None, "change_pct": 0})
    out = []
    for row in cand:
        code = row["code"]
        try:
            now = kis.price_now_jmoney_un(code)   # 가격=J 공식 / 거래량=UN 통합
        except Exception:
            continue
        if not (now.get("high") and now.get("prev_close") and now.get("price")):
            continue
        high_pct = (now["high"] / now["prev_close"] - 1) * 100
        change_pct = round(float(now.get("change_pct") or 0), 2)
        fade = high_pct - change_pct
        if high_pct < SHAKEOUT_HIGH_PCT or fade < SHAKEOUT_FADE_PCT:
            continue
        fr, listed = float_ratio.get_float_and_listed(code)
        fs = (listed * fr) if (fr and listed) else None
        if not fs:
            continue                                  # 유동비율 미상 → 회전율 확정 불가(미인정, fail-safe)
        turnover = float(now.get("volume") or 0) / fs * 100
        if turnover < SHAKEOUT_TURNOVER_MIN:
            continue
        try:
            daily = kis.daily_prices_jmoney_un(code, days=60)   # 가격=J / 거래량=UN(통합) — 2일 회전율·고점낙폭 산출에 사용
        except Exception:
            continue
        closes = [b.get("close") for b in daily if b.get("close")]
        if len(closes) < 20:
            continue                                  # 신규상장 등 MA20 판정 불가 — 미인정
        ma20 = sum(closes[-20:]) / 20
        # 2일 합산(신호일+전일) 유통회전율 — 흔들기 순위 스윗스팟 판정용(회장님 20년 룰)
        vols = [b.get("volume") or 0 for b in daily]
        turnover_2d = (vols[-1] + vols[-2]) / fs * 100 if len(vols) >= 2 else turnover
        # 고점 대비 낙폭(최근 60일 최고가 대비 현재가) — 흔들기 순위 낙폭 스윗존(−30~−45%) 판정용
        highs = [b.get("high") or 0 for b in daily]
        peak = max(highs) if highs else 0
        peak_dd = (now["price"] / peak - 1) * 100 if peak else 0
        if now["price"] < ma20:
            continue                                  # 추세 사수 실패
        run6 = (closes[-1] / closes[-7] - 1) * 100 if (len(closes) >= 7 and closes[-7]) else None
        if run6 is not None and run6 >= SHAKEOUT_RUN6D_MAX and change_pct < 0:
            continue                                  # 과확장 붕괴(5연상 붕괴류) — 실측 실패 유형 차단
        # ⚠ 경고/위험 지정도 제외하지 않음(회장님 지시 2026-07-06): 재료 강하면 경고받고도 급등(삼기 122350 실증).
        #   배지(alert_now)만 달고 순위·채점·자동매수는 그대로 — 매매 종목 선택은 회장님이 개별로.
        ma10 = sum(closes[-10:]) / 10
        # 밴드별 익절 힌트 — 회전율 밴드별 익일 고가 천장 실측(2026-07-04 전수 60건, 회장님 관찰:
        # 90~120%는 +12%가 천장이라 +13 익절 걸면 EV 역전). 표시 전용.
        if turnover >= 120:
            tp_hint = "+13%"
        elif turnover >= 90:
            tp_hint = "+7~10% (12% 천장 실측)"
        elif turnover >= 70:
            tp_hint = "⚠ 약체 밴드(70~90%, 표본소)"
        else:
            tp_hint = "+15% (양극단·표본소)"
        stier = (_shakeout_turnover_tier({"turnover_2d_pct": turnover_2d})
                 + _shakeout_dd_tier({"peak_dd_pct": peak_dd}))  # 결합 강도 tier(0~4, 낮을수록 강)
        out.append({
            "code": code, "name": row.get("name") or now.get("sector") or code, "sector": now.get("sector", ""),
            "pattern": "shakeout",
            "shakeout": True,
            "tp_hint": tp_hint,                       # 익절선 힌트(회전 밴드 실측) — 표시 전용
            "fade_pct": round(fade, 1),               # 고가 대비 밀림 폭(%p)
            "suspicion_score": int(min(95, 55 + fade * 0.6 + min(turnover, 200) * 0.08)),  # 표시 전용
            "score_raw": 0, "score_breakdown": {}, "score_breakdown_raw": {},  # 통계 격리
            "calibrated_prob": None, "forecast": None, "shake": None, "deep_shake": None,
            "price": now["price"], "change_pct": change_pct, "change_basis": "KRX",
            "high_pct": round(high_pct, 2),
            "value_eok": round(float(now.get("value") or 0) / 1e8),
            "turnover_pct": round(turnover, 1), "float_ratio": fr, "turnover_basis": "float",
            "turnover_2d_pct": round(turnover_2d, 1),   # 💥 2일 합산(신호일+전일) — 스윗스팟 순위 판정 기준
            "turnover_band": _shakeout_turnover_tier({"turnover_2d_pct": turnover_2d}),  # 0=스윗90~140 1=허용 2=과회전>180
            "peak_dd_pct": round(peak_dd, 1),           # 💥 고점(60일) 대비 낙폭 — 스윗존 −30~−45%가 최적
            "dd_band": _shakeout_dd_tier({"peak_dd_pct": peak_dd}),  # 0=스윗 1=인접 2=그외
            "strength_tier": stier, "strength": _shakeout_strength(stier),  # 강도 등급(종베 선택 보조)
            "run_6d_pct": round(run6, 1) if run6 is not None else None,
            "ma20_gap_pct": round((now["price"] / ma20 - 1) * 100, 1),
            "ma10": round(ma10, 1), "ma10_margin_pct": round((now["price"] / ma10 - 1) * 100, 2),
            "spark": {"clusters": []}, "spark_max_x": None, "spark_max_pct": None, "mega_flow": False,
            "reignition": None, "reignition_bars": [],
            "geupso": False, "geupso_bars": [], "low_accum": False, "low_accum_bars": [],
            "news": [], "matched_events": [], "theme": "",
            "visible_experimental": True,
        })
    if out:
        log("[radar] 💥 흔들기 " + ", ".join(
            f"{r['name']}(고가{r['high_pct']:+.0f}→{r['change_pct']:+.0f}%·회전{r['turnover_pct']:.0f}%)" for r in out))
    return out


def main():
    ap = argparse.ArgumentParser(
        description="이벤트 매집 레이더 — 과거 폭발 → 오늘 5분 양봉 스파크(재매집) 탐지")
    # 반등(재매집) 게이트 = 전일 폭발 종목 + 당일 5분 양봉 스파크 횟수 — 식음·등락률·MA20·투신 게이트 폐지
    ap.add_argument("--reignition-body-pct", type=float, default=REIGNITION_BODY_PCT,
                    help="5분 양봉 몸통%% 하한(기본 1.5)")
    ap.add_argument("--reignition-span-min", type=int, default=REIGNITION_SPAN_MIN,
                    help="재반등 스파크 판정 분봉 합성 단위(분, 기본 5)")
    ap.add_argument("--reignition-min-count", type=int, default=REIGNITION_MIN_COUNT,
                    help="시작시각 이후 자격 양봉 스파크 최소 횟수(기본 2)")
    ap.add_argument("--reignition-start", default=REIGNITION_START_HHMM,
                    help="재반등 스파크 집계 시작 시각 HHMM(그 전 양봉 미집계, 기본 1430)")
    ap.add_argument("--reaccum-change-min", type=float, default=REACCUM_CHANGE_MIN,
                    help="재매집 현재 등락률 하한(%%, 기본 -5)")
    ap.add_argument("--reaccum-change-max", type=float, default=REACCUM_CHANGE_MAX,
                    help="재매집 현재 등락률 상한(%%, 기본 7)")
    ap.add_argument("--geupso-body-pct", type=float, default=GEUPSO_BODY_PCT,
                    help="🎯 매수급소 5분 양봉 몸통%% 하한(기본 2.0 — 밴드 무제한 급소 판정)")
    ap.add_argument("--geupso-min-count", type=int, default=GEUPSO_MIN_COUNT,
                    help="🎯 매수급소 최소 양봉 수(기본 2)")
    ap.add_argument("--reaccum-track-days", type=int, default=REACCUM_TRACK_DAYS,
                    help="폭발 후 장기 추적 상한 거래일(기본 30 — 6일 초과분은 20일선 위인 동안만 유지)")
    ap.add_argument("--lowaccum-change-max", type=float, default=LOWACCUM_CHANGE_MAX,
                    help="🧲 저점매집: 당일 등락 상한(기본 -10 — 이하 폭락 중일 때만 판정)")
    ap.add_argument("--lowaccum-body-pct", type=float, default=LOWACCUM_BODY_PCT,
                    help="🧲 저점매집: 시간 무관 5분 양봉 몸통%% 하한(기본 2.0)")
    ap.add_argument("--lowaccum-min-count", type=int, default=LOWACCUM_MIN_COUNT,
                    help="🧲 저점매집: 최소 양봉 수(기본 3)")
    ap.add_argument("--names", nargs="*", default=[], help="watchlist 강제 포함")
    ap.add_argument("--no-reaccum", dest="reaccum_enabled", action="store_false",
                    help="재매집(reaccum) registry 감시와 후보 생성을 비활성화")
    ap.set_defaults(reaccum_enabled=True)
    ap.add_argument("--no-reaccum-visible", dest="reaccum_visible", action="store_false",
                    help="재매집 후보를 registry에는 기록하되 suspects 화면 노출은 비활성화")
    ap.set_defaults(reaccum_visible=True)
    ap.add_argument("--reaccum-max", type=int, default=12,
                    help="게시 단계에서 예약할 재매집 후보 슬롯 수(파라미터 기록용)")
    ap.add_argument("--explosion-vol-turnover", type=float, default=EXPLOSION_VOL_TURNOVER,
                    help="폭발 게이트: 당일 거래량/유통주식수 회전율 하한(%%, 기본 90)")
    ap.add_argument("--explosion-high-pct", type=float, default=EXPLOSION_HIGH_PCT,
                    help="폭발 당일 고가 등락률 하한(%%, 기본 22)")
    ap.add_argument("--explosion-window", type=int, default=EXPLOSION_WINDOW,
                    help="폭발 유효 거래일 수(식음·반등 추적 윈도)")
    ap.add_argument("--explosion-scan-n", type=int, default=EXPLOSION_SCAN_N,
                    help="시장별 네이버 up(등락률) 상위 N종목에서 폭발 감시")
    ap.add_argument("--youtong-change-pct", type=float, default=YOUTONG_CHANGE_PCT,
                    help="/youtong 게이트: 현재 등락률 하한(%%, 기본 7)")
    ap.add_argument("--youtong-turnover-min", type=float, default=YOUTONG_TURNOVER_MIN,
                    help="/youtong 유통주식 회전율 하한(%%, 기본 50, 상한 없음)")
    ap.add_argument("--youtong-start", default=YOUTONG_START_HHMM,
                    help="/youtong 감지 시작 시각 HHMM(그 전 무시·스파크 시각 하한, 기본 0930)")
    ap.add_argument("--youtong-spark-min", type=int, default=YOUTONG_SPARK_MIN,
                    help="/youtong: 시작시각 이후 5분 양봉 스파크 최소 수(기본 1)")
    ap.add_argument("--reaccum-seed", default=REACCUM_SEED_PATH,
                    help="즉시 부트스트랩용 재매집 seed JSON 경로")
    ap.add_argument("--no-telegram-seed", dest="telegram_seed", action="store_false",
                    help="텔레그램 채널 언급 종목을 재매집 보조 시드로 쓰지 않음")
    ap.set_defaults(telegram_seed=True)
    ap.add_argument("--telegram-channel", default="FastStockNews",
                    help="보조 시드용 공개 텔레그램 채널 username(@제외)")
    ap.add_argument("--telegram-max-age", type=float, default=360.0,
                    help="이 분(min) 이내 채널 글만 시드로 사용(기본 360=6h)")
    ap.add_argument("--telegram-max", type=int, default=25,
                    help="채널 시드 최대 종목 수")
    ap.add_argument("--dry-run", action="store_true",
                    help="폭발 레지스트리를 저장하지 않고 stdout만 생성")
    p = ap.parse_args()
    p.explosion_window = max(1, int(p.explosion_window))
    p.explosion_scan_n = max(1, int(p.explosion_scan_n))
    p.reignition_span_min = max(1, int(p.reignition_span_min))
    p.reignition_min_count = max(1, int(p.reignition_min_count))
    _rs = str(p.reignition_start).strip()  # "HHMM" 4자리 숫자만 — 오입력은 기본값(시각 비교 깨짐 방지)
    p.reignition_start = _rs if (len(_rs) == 4 and _rs.isdigit()) else REIGNITION_START_HHMM  # 정확히 4자리 HHMM만(짧은/긴 숫자 오변환 방지)
    p.youtong_spark_min = max(1, int(p.youtong_spark_min))
    _ys = str(p.youtong_start).strip()  # "HHMM" 4자리 숫자만 — 비숫자/콜론 등 오입력은 기본값으로(시각 비교 깨짐 방지)
    p.youtong_start = _ys if (len(_ys) == 4 and _ys.isdigit()) else YOUTONG_START_HHMM  # 정확히 4자리 HHMM만
    p.reaccum_max = max(0, int(p.reaccum_max))
    p.geupso_body_pct = max(0.5, float(p.geupso_body_pct))
    p.geupso_min_count = max(1, int(p.geupso_min_count))
    p.reaccum_track_days = max(p.explosion_window, int(p.reaccum_track_days))  # 최소 기존 윈도(6) 이상
    p.lowaccum_body_pct = max(0.5, float(p.lowaccum_body_pct))
    p.lowaccum_min_count = max(1, int(p.lowaccum_min_count))
    p.lowaccum_change_max = float(p.lowaccum_change_max)
    active_explosions, live_scan_ok, today_explosions, today_youtong = prepare_reaccum_registry(p)

    # 조건 1: D-10 이벤트 캘린더 (재반등 후보의 이벤트 민감도 표시용)
    events = upcoming_events(10)
    log(f"[radar] D-10 이벤트 {len(events)}건")

    # 유일 산출물 = 재매집 후보 (과거 폭등 → 식음 → 오늘 재반등)
    suspects = []
    reaccum_added, err_count = attach_reaccum_candidates(
        suspects, active_explosions, p, events)
    total = len(active_explosions)
    # high_fail 분모는 '실제 KIS 조회를 시도하는' 적격(새 정의 검증) 레코드 수만 — legacy(vol_turnover_pct
    # None) 레코드는 scan_reaccum_candidate가 KIS 호출 전에 None 반환이라 ERR로 안 잡힌다. 마이그레이션
    # 윈도 동안 legacy가 다수면 total이 부풀어 '절반 실패' 비율이 희석돼 부분장애를 놓치는 걸 방지.
    eligible = sum(1 for r in active_explosions.values() if _reaccum_eligible(r, p))
    log(f"[radar] reaccum 후보 {reaccum_added}건 (폭발감시 {total}종목·적격 {eligible}, 조회실패 {err_count}, live_ok={live_scan_ok})")
    # 데이터 수집 장애 가드 — KIS 토큰/키 장애 시 거짓 '빈 레이더' 게시 방지(구 유니버스 exit(2) 대체).
    #  (a) 폭발감시 전면 실패 + 수상종목 0 + 당일 폭발(/forecast)도 0 → 시장 도달 확인 불가(빈 레지스트리 포함).
    #  (b) 적격 후보가 있는데 절반 이상 조회 실패 → KIS 장애로 반쪽 게시 방지(소형 레지스트리도 floor=2로 포착).
    # 빈 레지스트리·조건 미달로 인한 0건은 정상('레이더 깨끗') — live_scan_ok=True면 종료하지 않는다.
    # ⚠ today_explosions(/forecast)가 registry 백필로 채워졌으면 보여줄 실데이터가 있으므로 중단하지 않는다.
    collection_dead = (not live_scan_ok) and reaccum_added == 0 and len(today_explosions) == 0
    high_fail = eligible > 0 and err_count >= max(2, (eligible + 1) // 2)
    if collection_dead or high_fail:
        log(f"[error] 데이터 수집 장애 의심(live_ok={live_scan_ok}, 실패 {err_count}/{eligible}적격, 후보 {reaccum_added}) — 게시 중단")
        sys.exit(3)
    # 💥 흔들기 스캔(별도 그물) — 기존 suspects와 코드 중복 시 기존 레코드에 플래그만 병합(이중 카드 방지)
    try:
        # 랭킹 사각지대 보완: 이미 추적 중인 youtong·폭발 레지스트리 코드도 흔들기 후보로 재검사
        #   (삼기처럼 페이드로 등락률 top50서 밀린 강한 흔들기 포착).
        extra = list(active_explosions.keys()) + [y.get("code") for y in (today_youtong or [])]
        shakeouts = scan_shakeout(p, extra_codes=extra)
    except Exception as e:
        log(f"[warn] 흔들기 스캔 실패(무시): {e}")
        shakeouts = []
    existing = {s["code"]: s for s in suspects}
    for r in shakeouts:
        if r["code"] in existing:
            # ⚠ 순위 필드(turnover_2d_pct·peak_dd_pct)까지 병합해야 흔들기 tier가 올바로 계산됨.
            #   누락 시 tier가 1일 회전율 폴백/None으로 오작동 → 병합된(더 강한) 흔들기가 강등돼 잘못된 1위(자동매매 오매수).
            existing[r["code"]].update({
                "shakeout": True, "fade_pct": r["fade_pct"],
                "turnover_2d_pct": r.get("turnover_2d_pct"), "peak_dd_pct": r.get("peak_dd_pct"),
                "turnover_band": r.get("turnover_band"), "dd_band": r.get("dd_band"),
                "strength_tier": r.get("strength_tier"), "strength": r.get("strength"),
                "tp_hint": r.get("tp_hint"),
            })
        else:
            suspects.append(r)
    # KRX 시장경보 지정 조회(최종 수상종목만 ≤reaccum_max·회당 1콜) — 경고/위험 지정은 무조건 후순위 강등
    # (회장님 지시 2026-07-03: 투자경고 종목이 상위 추천됨. 경고 후 재상승=매매정지 지정 리스크). fail-safe(None=무경보 취급).
    # 단 경고 종목이 '내일 해제 예정'(KRX 해제공식 충족 예측, alert_release.py)이면 반대로 최상단 승격(해제=재료).
    for s in suspects:
        s["alert_now"] = _alert_level(s["code"])
        if s["alert_now"] == "경고":
            try:
                s["alert_release"] = alert_release.forecast_release_for(
                    s["code"], kis.daily_prices(s["code"], days=25), s.get("price"))
            except Exception:
                s["alert_release"] = None
    suspects.sort(key=lambda x: (
        # ⚠ 경고/위험 최후순위 강등 폐지(회장님 지시 2026-07-06): 배지(alert_now)만 달고 순위엔 무영향.
        #   재료 강하면 경고받고도 급등 — 매매 선택은 회장님이 개별로.
        not x.get("shakeout"),                                                  # 💥 흔들기 최상단(검증됨 — 7/2제외 익일고가+13% 75%)
        not x.get("alert_release"),                                             # 🔓 경고 해제 예정(긍정 신호 — 최상단 승격 유지)
        not x.get("geupso"),                                                    # 🎯 급소(14:30↑ 스파크 — 전진검증 중)
        ((_shakeout_turnover_tier(x) + _shakeout_dd_tier(x)) if x.get("shakeout") else 0),  # 💥 흔들기: 회전 스윗(90~140) + 고점낙폭 스윗(−30~−45) 결합 우선
        (-(x.get("fade_pct") or 0) if x.get("shakeout") else 0),                # 💥 흔들기: 동급이면 더 강한 흔들기(fade) 우선(진흥형)
        -x["suspicion_score"]))
        # ⚠ 🧲 저점매집 '우선순위 승격'·'저회전 오름차순' 정렬 철회(2026-07-04 회장님 결정): 익일 기준 미검증
        #   (저회전<15% 폭락 표본 n=2·둘다 익일 +13% 실패, 5분봉 지문 소급 불가). 이번 주 손실의 뿌리 =
        #   미검증 신호를 1위로 올린 것. low_accum은 배지·by_low_accum 관찰축으로만 유지(표본 성숙 후 재도입 판단).

    out = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "params": {"market": kis.MONEY_MARKET,  # 거래대금/수급 시장구분: UN=KRX+NXT 통합 / J=KRX 단독(가격은 항상 J)
                   "reignition_body_pct": p.reignition_body_pct,
                   "reignition_span_min": p.reignition_span_min,
                   "reignition_min_count": p.reignition_min_count,
                   "reignition_start": p.reignition_start,            # 재반등 스파크 집계 시작 시각 HHMM
                   "reaccum_change_min": p.reaccum_change_min,        # 재매집 현재 등락률 하한(%)
                   "reaccum_change_max": p.reaccum_change_max,        # 재매집 현재 등락률 상한(%)
                   "reaccum_enabled": p.reaccum_enabled,
                   "reaccum_visible": p.reaccum_visible,
                   "reaccum_max": p.reaccum_max,
                   "explosion_vol_turnover": p.explosion_vol_turnover,  # 폭발 게이트: 거래량/유통주식수 회전율 하한(%)
                   "explosion_high_pct": p.explosion_high_pct,
                   "explosion_window": p.explosion_window,
                   "explosion_scan_n": p.explosion_scan_n,
                   "youtong_change_pct": p.youtong_change_pct,        # /youtong: 현재 등락률 하한(%)
                   "youtong_turnover_min": p.youtong_turnover_min,    # /youtong: 유통 회전율 하한(%, 상한 없음)
                   "youtong_start": p.youtong_start,                  # /youtong: 감지 시작 시각 HHMM
                   "youtong_spark_min": p.youtong_spark_min,          # /youtong: 시작시각 이후 5분 스파크 최소 수
                   "geupso_body_pct": p.geupso_body_pct,              # 🎯 매수급소: 5분 양봉 몸통% 하한(2.0)
                   "geupso_min_count": p.geupso_min_count,            # 🎯 매수급소: 최소 양봉 수(2)
                   "reaccum_track_days": p.reaccum_track_days,        # 폭발 후 장기 추적 상한(거래일)
                   "lowaccum_change_max": p.lowaccum_change_max,      # 🧲 저점매집: 당일 등락 상한(-10)
                   "lowaccum_body_pct": p.lowaccum_body_pct,          # 🧲 저점매집: 양봉 몸통% 하한(2.0)
                   "lowaccum_min_count": p.lowaccum_min_count,        # 🧲 저점매집: 최소 양봉 수(3)
                   "telegram_seed": p.telegram_seed,
                   "telegram_channel": p.telegram_channel if p.telegram_seed else None},
        "universe_count": len(active_explosions),
        "events": events,
        "explosions": today_explosions,   # 당일 폭발 종목(/forecast 게시용)
        # /youtong '곧 폭발할 후보' — 회전율 내림차순(폭발 임박순). 종일 지속(registry)·표시용(통계 무관).
        "youtong": sorted(today_youtong, key=lambda e: -(e.get("vol_turnover_pct") or 0)),
        "suspects": suspects,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
