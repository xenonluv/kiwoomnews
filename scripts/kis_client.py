#!/usr/bin/env python3
"""RETIRED: 운영 및 cron에서 사용하지 않는 과거 연구 재현용 KIS 클라이언트.

운영 데이터 생성에 사용하면 안 된다. 현재 레이더·백테스트·예약 평가는 키움 API를 사용한다.

.env의 KIS_APP_KEY / KIS_APP_SECRET 사용. 토큰은 .kis_token.json에 캐시
(유효 1일, 발급은 1분 1회 제한이므로 캐시 필수).

제공 함수:
  daily_prices(code, days)   일봉 (날짜·시고저종·거래량·거래대금)
  price_now(code)            현재가 스냅샷 (현재가·당일고가·등락률·거래대금·업종)
  minute_bars_today(code)    당일 1분봉 전체 (시각 역방향 페이지네이션)
  investor_daily(code)       종목별 투자자 일별 순매수 (외국인/기관/개인)
  value_rank(market, top_n)  당일 거래대금(거래금액순) 상위 종목 (코스피/코스닥)

단독 실행 시 삼성전자(005930)로 4개 API를 점검한다:
  python3 scripts/kis_client.py [종목코드]
  python3 scripts/kis_client.py rank [KOSPI|KOSDAQ]   # 거래대금 순위 점검
"""
import os
import sys
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_CACHE = os.path.join(ROOT, ".kis_token.json")
BASE = "https://openapi.koreainvestment.com:9443"
MIN_GAP = 0.06  # 실전계좌 초당 20건 제한 → 호출 간 최소 간격
_last_call = [0.0]

# 시장 구분(FID_COND_MRKT_DIV_CODE): J=KRX 정규장 단독 / NX=NXT / UN=KRX+NXT 통합.
# ⚠ 가격(OHLC)은 **항상 J(KRX 공식)** — MA·등락률·고가게이트·익일평가의 권위 기준이다.
#   UN 종가는 NXT 시간외 체결이 섞여 공식 종가와 1~6% 어긋나(실측) 가격에 쓰면 지표·평가가 왜곡된다.
# 거래대금·거래량·수급(money)만 통합(UN) — NXT 거래분(종목별 과반) 누락을 막는다.
# KIS_MARKET=J 로 money까지 KRX 단독 환원 가능.
MONEY_MARKET = os.environ.get("KIS_MARKET", "UN")


def _load_env():
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _keys():
    _load_env()
    app_key = os.environ.get("KIS_APP_KEY", "")
    app_secret = os.environ.get("KIS_APP_SECRET", "")
    if not app_key or not app_secret:
        raise RuntimeError("KIS_APP_KEY/KIS_APP_SECRET가 .env에 없습니다")
    return app_key, app_secret


_token_fail_at = [0.0]  # 발급 실패 시각 — 60초 쿨다운(발급 1분 1회 제한 보호)


def _invalidate_token():
    try:
        os.remove(TOKEN_CACHE)
    except OSError:
        pass


def get_token(force=False):
    """캐시된 토큰 반환, 만료 임박(10분)·force 시 재발급.

    발급 실패 시 60초 쿨다운 — 같은 프로세스의 후속 호출이 tokenP를
    재차 두드리는 폭주(1분 1회 제한 위반)를 막는다.
    """
    cached = None  # (token, 남은시간) — 발급 실패 시 미만료 토큰 폴백용
    if os.path.exists(TOKEN_CACHE):
        try:
            tk = json.load(open(TOKEN_CACHE, encoding="utf-8"))
            exp = datetime.strptime(tk["expired"], "%Y-%m-%d %H:%M:%S")
            remain = exp - datetime.now()
            if remain > timedelta(0):
                cached = tk["token"]
            if not force and remain > timedelta(minutes=10):
                return tk["token"]
        except Exception:
            pass
    if time.time() - _token_fail_at[0] < 60:
        if cached and not force:
            return cached  # 쿨다운 중이라도 아직 유효한 토큰이 있으면 사용
        raise RuntimeError("KIS 토큰 발급 쿨다운 중(직전 발급 실패, 1분 1회 제한)")
    app_key, app_secret = _keys()
    body = json.dumps({"grant_type": "client_credentials",
                       "appkey": app_key, "appsecret": app_secret}).encode()
    req = urllib.request.Request(
        BASE + "/oauth2/tokenP", data=body,
        headers={"content-type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            res = json.load(r)
    except Exception:
        _token_fail_at[0] = time.time()
        if cached and not force:
            return cached  # 일시 발급 장애 — 미만료 토큰으로 계속
        raise
    token = res.get("access_token")
    if not token:
        _token_fail_at[0] = time.time()
        if cached and not force:
            return cached
        raise RuntimeError(f"KIS 토큰 발급 실패: {res.get('error_code')} "
                           f"{res.get('error_description')}")
    expired = res.get("access_token_token_expired",
                      (datetime.now() + timedelta(hours=23)).strftime("%Y-%m-%d %H:%M:%S"))
    tmp = TOKEN_CACHE + ".tmp"
    json.dump({"token": token, "expired": expired}, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, TOKEN_CACHE)  # 원자적 쓰기 — 중도 kill에도 캐시 파일 안 깨짐
    return token


def revoke_token(token):
    """KIS 접근토큰 폐기(/oauth2/revokeP). KIS는 유효 토큰이 있으면 tokenP가 '기존 토큰'을 반환하므로,
    07:00 고정 재발급을 위해 먼저 기존 토큰을 폐기해야 새 토큰(=새 만료시각)이 나온다. 실패는 dict로 흡수."""
    app_key, app_secret = _keys()
    body = json.dumps({"appkey": app_key, "appsecret": app_secret, "token": token}).encode()
    req = urllib.request.Request(BASE + "/oauth2/revokeP", data=body,
                                 headers={"content-type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception as e:
        return {"_error": str(e)}


TOKEN_ERR_CODES = ("EGW00121", "EGW00123")  # 토큰 무효 / 토큰 만료


def _call(path, tr_id, params, retries=3):
    """GET 호출 공통.

    재시도 대상: 레이트리밋(EGW00201, 429)·일시 서버 오류(5xx)·
    토큰 무효(401, EGW00121/EGW00123 — 캐시 무효화 후 강제 재발급).
    """
    app_key, app_secret = _keys()
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    last_err = None
    force_token = False
    for attempt in range(retries):
        gap = MIN_GAP - (time.time() - _last_call[0])
        if gap > 0:
            time.sleep(gap)
        _last_call[0] = time.time()
        try:
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": "Bearer " + get_token(force=force_token),
                "appkey": app_key,
                "appsecret": app_secret,
                "tr_id": tr_id,
                "custtype": "P",
            }
            force_token = False
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                res = json.load(r)
            if res.get("rt_cd") == "0":
                return res
            msg_cd = res.get("msg_cd")
            last_err = RuntimeError(f"KIS {msg_cd}: {res.get('msg1','').strip()}")
            if msg_cd in TOKEN_ERR_CODES:
                _invalidate_token()
                force_token = True
            elif msg_cd != "EGW00201":  # 토큰·초당건수 외에는 재시도 무의미
                raise last_err
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 401:
                _invalidate_token()
                force_token = True
            elif e.code not in (429, 500, 502, 503, 504):
                raise
        except urllib.error.URLError as e:
            last_err = e
        time.sleep(0.5 * (attempt + 1))
    raise last_err


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def daily_prices(code, days=30, market="J"):
    """일봉 최근 days개 (오름차순). value=거래대금(원). 기본 market="J"(KRX 공식 — 가격·평가 권위).

    거래대금/거래량을 통합(UN)으로 보고 싶으면 daily_prices_jmoney_un()을 쓴다(가격은 J 유지).
    ⚠ FHKST03010100은 1콜 최대 ~100봉(KIS 한도)만 반환 → days>100이면 조회종료일(end)을
    가장 오래된 봉 직전으로 당겨가며 페이징해 합친다. days<=100이면 1콜(기존과 동일).
    """
    mkt = market
    start = (datetime.now() - timedelta(days=days * 2 + 10)).strftime("%Y%m%d")
    acc = {}                                   # date -> bar (중복 방지)
    cur_end = datetime.now().strftime("%Y%m%d")
    max_pages = (days + 99) // 100 + 2 if days > 100 else 1  # 여유 페이지
    for _ in range(max_pages):
        res = _call("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                    "FHKST03010100",
                    {"FID_COND_MRKT_DIV_CODE": mkt, "FID_INPUT_ISCD": code,
                     "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": cur_end,
                     "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
        added = 0
        for row in res.get("output2", []):
            d = row.get("stck_bsop_date")
            if not d or d in acc:
                continue
            acc[d] = {"date": d,
                      "open": _f(row.get("stck_oprc")),
                      "high": _f(row.get("stck_hgpr")),
                      "low": _f(row.get("stck_lwpr")),
                      "close": _f(row.get("stck_clpr")),
                      "volume": _f(row.get("acml_vol")),
                      "value": _f(row.get("acml_tr_pbmn"))}
            added += 1
        if added == 0:                         # 더 줄 게 없음
            break
        oldest = min(acc)
        if oldest <= start or len(acc) >= days:  # 범위 끝 도달 / 충분
            break
        cur_end = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        time.sleep(MIN_GAP)
    out = sorted(acc.values(), key=lambda x: x["date"])
    return out[-days:]


def price_now(code, market="J"):
    """현재가 스냅샷. 등락률·당일고가·누적거래대금·업종명 포함. 기본 market="J"(KRX 공식).

    거래대금/거래량 통합(UN)이 필요하면 price_now_jmoney_un()을 쓴다(가격은 J 유지)."""
    mkt = market
    res = _call("/uapi/domestic-stock/v1/quotations/inquire-price",
                "FHKST01010100",
                {"FID_COND_MRKT_DIV_CODE": mkt, "FID_INPUT_ISCD": code})
    o = res["output"]
    price = _f(o.get("stck_prpr"))
    change_pct = _f(o.get("prdy_ctrt"))
    prev_close = price - _f(o.get("prdy_vrss"))  # 전일대비로 정확 역산
    return {"code": code,
            "date": (o.get("stck_bsop_date") or "").strip(),
            "price": price,
            "high": _f(o.get("stck_hgpr")),
            "low": _f(o.get("stck_lwpr")),
            "open": _f(o.get("stck_oprc")),
            "change_pct": change_pct,
            "prev_close": round(prev_close, 2),
            "value": _f(o.get("acml_tr_pbmn")),
            "volume": _f(o.get("acml_vol")),
            "sector": (o.get("bstp_kor_isnm") or "").strip(),
            "market_cap_eok": _f(o.get("hts_avls")),
            "per": _f(o.get("per")),
            "w52_high": _f(o.get("w52_hgpr"))}


def _overlay_money(bar, un_bar):
    """가격은 그대로 두고 거래대금/거래량만 UN 값으로 덮어쓴다(un_bar 없거나 0이면 J 유지).

    ⚠ _f()가 결측 필드를 0.0으로 강제하므로 `is not None`은 항상 참이라 무용지물 → 0으로 검사.
    UN ⊇ KRX(통합=KRX+NXT)라 정상 데이터의 UN value/volume은 J 이상 → max(J, UN)로 덮어쓴다.
    0은 곧 결측·글리치이므로 J 보존(0으로 덮으면 게이트가 진짜 거래대금을 0으로 봄). UN이 0은 아니나
    J보다 작은 부분집계(NXT만 반영 등)를 줄 때도 max로 더 큰 J를 지켜 과소탐지를 막는다."""
    if un_bar:
        if (un_bar.get("value") or 0) > 0:
            bar["value"] = max(bar.get("value") or 0, un_bar["value"])
        if (un_bar.get("volume") or 0) > 0:
            bar["volume"] = max(bar.get("volume") or 0, un_bar["volume"])


def daily_prices_jmoney_un(code, days=30):
    """일봉: 가격(OHLC)=J(KRX 공식, MA·평가 권위), 거래대금/거래량=UN(통합) 덮어쓰기.

    MONEY_MARKET=="J"면 추가 호출 없이 J 그대로. UN 조회 실패 시 J로 graceful degrade.
    레이더의 거래대금 게이트·표시는 이걸 쓰고, 익일평가(track_eval·backtest)는 plain daily_prices(J)를 쓴다."""
    jb = daily_prices(code, days=days, market="J")
    if MONEY_MARKET == "J":
        return jb
    try:
        un = {b["date"]: b for b in daily_prices(code, days=days, market="UN")}
    except Exception as e:
        # UN 실패 시 J로 degrade — 단 게이트는 UN 기준(1,500억)이라 J값(~2/3)이면 과소탐지 가능 → 경고.
        sys.stderr.write(f"[kis] {code} 일봉 UN 조회 실패 → J 거래대금으로 degrade: {e}\n")
        return jb
    for b in jb:
        _overlay_money(b, un.get(b["date"]))
    return jb


def price_now_jmoney_un(code):
    """현재가: 가격(현재가·고가·등락률)=J(KRX 공식), 거래대금/거래량=UN(통합) 덮어쓰기.

    MONEY_MARKET=="J"면 J 그대로. UN 조회 실패 시 J로 graceful degrade."""
    now = price_now(code, market="J")
    if MONEY_MARKET == "J":
        return now
    try:
        un = price_now(code, market="UN")
        _overlay_money(now, un)
    except Exception as e:
        # UN 실패 시 J로 degrade — 게이트는 UN 기준이라 과소탐지 가능 → 경고(조용히 묻지 않음).
        sys.stderr.write(f"[kis] {code} 현재가 UN 조회 실패 → J 거래대금으로 degrade: {e}\n")
    return now


SESSION_OPEN = "090000"   # 정규장 시작
SESSION_CLOSE = "153000"  # 정규장 종료(동시호가 포함)


def minute_bars_today(code, until="153000", market="J"):
    """당일 1분봉 전체 (오름차순). 1콜=30봉이라 시각을 30분씩 물려 역방향 수집.

    당일(stck_bsop_date == 오늘) 봉만 수집 — FID_PW_DATA_INCU_YN="N" +
    날짜 필터 이중 가드. 휴장일·개장 전에는 빈 리스트를 반환한다
    (전일 분봉이 당일로 혼입되면 스파크 오탐이 나므로 절대 섞지 않는다).

    market 기본 "J"(KRX). radar는 "UN"(KRX+NXT 통합)으로 호출 — 거래대금·수급과 정합.
    UN엔 프리마켓(08:30~09:00) 봉이 섞이지만 SESSION_OPEN~CLOSE 시간창 가드로 정규장 봉만 채택한다.
    (until 기본 15:30이라 앵커가 15:30을 안 넘어 애프터마켓 봉은 애초에 fetch되지 않는다 — 2026-06-22 실측.)
    """
    mkt = market
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    if now.strftime("%H%M%S") < until:
        until = now.strftime("%H%M%S")
    bars = {}
    hour = until
    for _ in range(16):  # 09:00~15:30 = 390분 → 최대 13콜 + 여유
        res = _call("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                    "FHKST03010200",
                    {"FID_COND_MRKT_DIV_CODE": mkt, "FID_INPUT_ISCD": code,
                     "FID_INPUT_HOUR_1": hour, "FID_PW_DATA_INCU_YN": "N",
                     "FID_ETC_CLS_CODE": ""})
        rows = res.get("output2", []) or []
        if not rows:
            break  # 더 이상 데이터 없음
        page_earliest = None  # 이 페이지의 당일 봉 최소시각(가드 전 — 페이징 커서). None=당일 봉 없음
        for row in rows:
            t = row.get("stck_cntg_hour", "")
            if row.get("stck_bsop_date") != today:
                continue  # 전일/휴장일 봉 배제
            if len(t) != 6:
                continue
            if page_earliest is None or t < page_earliest:
                page_earliest = t
            if not (SESSION_OPEN <= t <= SESSION_CLOSE):
                continue  # NXT 장전(~09:00)·애프터마켓(15:30~) 봉 배제 — 정규장만 채택
            if t not in bars:
                bars[t] = {"time": t,
                           "open": _f(row.get("stck_oprc")),
                           "high": _f(row.get("stck_hgpr")),
                           "low": _f(row.get("stck_lwpr")),
                           "close": _f(row.get("stck_prpr")),
                           "vol": _f(row.get("cntg_vol"))}
        # 종료·다음커서는 가드 통과수(got)가 아니라 **페이지의 당일 최소시각(page_earliest)** 으로 구동.
        # UN은 장 밖 봉이 섞여 한 페이지가 전부 정규장 밖일 수 있는데(got==0), 정규장 봉이 더 과거(위)에
        # 남아있으면 누락하면 안 된다. page_earliest로 페이징하면 그런 페이지를 건너 정규장까지 도달한다.
        # ※ radar 경로는 until≤15:30이라 앵커가 15:30을 안 넘어 애프터마켓 페이지는 fetch되지 않는다
        #   (실재하는 장 밖 봉은 프리마켓 08:30~09:00). until을 키워 호출하는 경우까지 일반 대비한 안전판.
        # 당일 봉이 아예 없으면(전일 진입) 종료.
        if page_earliest is None:
            break
        if page_earliest <= "090000":  # 09:00봉까지 도달(09:00봉은 위에서 이미 수집) → 종료
            break
        # 다음 앵커는 반드시 현재 앵커 미만으로 — KIS가 앵커 이상 봉을 반환하는 이변에도 진행 보장(정지 방지).
        next_anchor = min(page_earliest, hour)
        prev = datetime.strptime(next_anchor, "%H%M%S") - timedelta(minutes=1)
        if prev.strftime("%H%M%S") < "090000":
            break
        hour = prev.strftime("%H%M%S")
    return [bars[t] for t in sorted(bars.keys())]


def minute_bars_today_with_meta(code, until="153000", market="J"):
    """KIS 당일 필터 결과에 기준일 메타를 붙인다."""
    bars = minute_bars_today(code, until=until, market=market)
    today = datetime.now().strftime("%Y%m%d")
    return {"trade_date": today if bars else None, "bars": bars,
            "fetch_status": "ok" if bars else "empty"}


def investor_daily(code):
    """종목별 투자자 일별 순매수량 (최근 영업일들, 오름차순). 외국인/기관/개인.

    ⚠ FHKST01010900은 UN(통합) 미지원(J/NX만) — "J" 고정. radar는 이 함수를 쓰지 않고
    투신(ivtr)까지 세분되는 investor_trade_daily(FHPTJ04160001, UN 지원)를 쓴다. 셀프테스트 전용.
    """
    res = _call("/uapi/domestic-stock/v1/quotations/inquire-investor",
                "FHKST01010900",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
    out = []
    for row in res.get("output", []):
        if not row.get("stck_bsop_date"):
            continue
        out.append({"date": row["stck_bsop_date"],
                    "frgn": _f(row.get("frgn_ntby_qty")),
                    "orgn": _f(row.get("orgn_ntby_qty")),
                    "prsn": _f(row.get("prsn_ntby_qty")),
                    "close": _f(row.get("stck_clpr"))})
    out.sort(key=lambda x: x["date"])
    return out


def investor_trade_daily(code, end_date="", market=None):
    """종목별 투자자매매동향(일별) — 투신(ivtr) 포함. 최근 ~30거래일, 오름차순.

    FHPTJ04160001. inquire-investor(외인/기관/개인)와 달리 투신·금액까지 세분.
    ivtr=투신 순매수 수량, ivtr_won=투신 순매수 금액(백만원). reaccum 투신 매집 판정용.
    market=None이면 MONEY_MARKET(UN) — 거래대금과 일관되게 KRX+NXT 통합 수급(수급은 가격 아님).
    """
    mkt = market or MONEY_MARKET
    res = _call("/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily",
                "FHPTJ04160001",
                {"FID_COND_MRKT_DIV_CODE": mkt, "FID_INPUT_ISCD": code,
                 "FID_INPUT_DATE_1": end_date, "FID_ORG_ADJ_PRC": "", "FID_ETC_CLS_CODE": ""})
    out = []
    for row in res.get("output2", []):
        d = (row.get("stck_bsop_date") or "").strip()
        if not d:
            continue
        out.append({"date": d,
                    "frgn": _f(row.get("frgn_ntby_qty")),
                    "orgn": _f(row.get("orgn_ntby_qty")),
                    "ivtr": _f(row.get("ivtr_ntby_qty")),
                    "ivtr_won": _f(row.get("ivtr_ntby_tr_pbmn"))})
    out.sort(key=lambda x: x["date"])
    return out


# ── 순위 API (유니버스 구성용) ──────────────────────────────────────────
# 시장 구분은 FID_INPUT_ISCD 업종코드로: 0001=코스피 종합, 1001=코스닥 종합.
# 응답은 최대 ~30행 — top20 용도로 충분 (전수 스캔 불가 제약은 그대로).
_RANK_SECTOR = {"KOSPI": "0001", "KOSDAQ": "1001"}
# 제외 비트(10자리): 투자위험/경고/주의·관리·정리매매·불성실·우선주·거래정지·ETF·ETN·신용불가·SPAC
# 시장경보(첫 비트)는 레이더 관심 대상이라 포함(0), 그 외 비종목성은 제외(1).
_RANK_EXLS = "0110111101"


def value_rank(market="KOSPI", top_n=20, mrkt="J"):
    """당일 거래대금(거래금액순) 상위 종목. → [{name, code, change_pct, value_mn}]

    market=KOSPI/KOSDAQ (업종 구분, FID_INPUT_ISCD). mrkt=시장 구분 — ⚠ 이 순위 API(FHPST01710000)는
    J(KRX)·NX(NXT)만 지원하고 **UN은 거부(INVALID FID_COND_MRKT_DIV_CODE, 실측 2026-06)**.
    UN을 그대로 쓰면 깨지므로 기본 "J". NXT-헤비 종목 누락을 막으려면 호출부가
    value_rank_union()처럼 J·NX 결과를 합집합한다.
    """
    res = _call("/uapi/domestic-stock/v1/quotations/volume-rank",
                "FHPST01710000",
                {"FID_COND_MRKT_DIV_CODE": mrkt,
                 "FID_COND_SCR_DIV_CODE": "20171",
                 "FID_INPUT_ISCD": _RANK_SECTOR[market],
                 "FID_DIV_CLS_CODE": "1",        # 보통주만
                 "FID_BLNG_CLS_CODE": "3",       # 거래금액순
                 "FID_TRGT_CLS_CODE": "111111111",
                 "FID_TRGT_EXLS_CLS_CODE": _RANK_EXLS,
                 "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
                 "FID_VOL_CNT": "", "FID_INPUT_DATE_1": ""})
    out = []
    for r in (res.get("output") or [])[:top_n]:
        code = (r.get("mksc_shrn_iscd") or "").strip()
        name = (r.get("hts_kor_isnm") or "").strip()
        if not code or not name:
            continue
        out.append({"name": name, "code": code,
                    "change_pct": _f(r.get("prdy_ctrt")),
                    "value_mn": _f(r.get("acml_tr_pbmn")) / 1e6})  # 원→백만원
    return out


def value_rank_union(market="KOSPI", top_n=20):
    """거래대금 상위 종목을 J(KRX)·NX(NXT) **양 시장에서 뽑아 합집합** → [{name, code, change_pct, value_mn}].

    순위 API가 UN을 거부하므로, NXT에서만 거래대금이 큰 종목(예: NXT 비중 과반)이 KRX-only
    순위에서 탈락하는 누락을 막는다. 같은 종목이 양쪽에 있으면 value_mn은 J+NX 합산(≈통합 거래대금
    근사), change_pct는 J 우선. 결과는 value_mn 내림차순 상위 top_n."""
    merged = {}
    # 시장별로 미리 top_n까지만 자르면 'J 21위·NX 21위지만 합산 상위'인 종목이 누락된다 →
    # 가용 행을 넉넉히(min 40, API가 ~30 상한이라 사실상 전수) 받아 합집합 후 마지막에만 절단.
    fetch_n = max(top_n, 40)
    for mrkt in ("J", "NX"):
        try:
            rows = value_rank(market, top_n=fetch_n, mrkt=mrkt)
        except Exception as e:
            # 한쪽 시장 실패는 다른 쪽으로 계속(부분 유니버스가 abort보다 안전) — 단 조용히 묻지 않게 경고.
            sys.stderr.write(f"[kis] value_rank {market}/{mrkt} 실패(스킵): {e}\n")
            rows = []
        for r in rows:
            m = merged.get(r["code"])
            if m:
                m["value_mn"] += r["value_mn"]  # J+NX 합산
            else:
                merged[r["code"]] = dict(r)
    out = sorted(merged.values(), key=lambda x: x["value_mn"], reverse=True)
    return out[:top_n]


# 참고: 등락률 순위 FHPST01700000은 정렬 코드 0~4 전부 등락률순으로 동작하지
# 않음을 실측 확인(2026-06) — 등락률 TOP-N은 radar.py가 네이버 up 랭킹으로 대체.


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--issue-token":
        # 매일 아침 07:00 cron 고정 발행 — KIS는 유효 토큰 있으면 tokenP가 기존을 반환(force 무시)하므로,
        # 기존 토큰을 revoke(폐기)한 뒤 새로 발급해야 만료시각이 07:00 기준으로 고정된다.
        rv = None
        if os.path.exists(TOKEN_CACHE):
            try:
                _old = json.load(open(TOKEN_CACHE, encoding="utf-8")).get("token")
                if _old:
                    rv = revoke_token(_old)
                    print("[kis] 기존 토큰 폐기:", rv.get("code") or rv.get("message") or rv.get("_error") or rv)
            except Exception as e:
                # 캐시 읽기 실패(손상 JSON 등) = 'revoke 미시도'일 뿐 드리프트 신호 아님 — rv는 None 유지.
                # (get_token(force=True)가 손상 캐시를 흡수하고 fresh 토큰을 정상 발급하므로 07:00 고정은 달성됨)
                print("[kis] 캐시 읽기 실패 — revoke 생략(신규 발급 진행):", e)
        # ⚠ 캐시를 미리 지우지 않는다 — force=True가 어차피 tokenP를 호출하고, 선삭제 시 발급 실패면
        # 07:00~09시 토큰 공백이 생긴다. 기존 캐시 보존이 안전(폐기된 토큰이면 09시 publish가 401→자동 재발급).
        try:
            get_token(force=True)
        except Exception as e:
            print(f"[kis] ⚠ 토큰 발급 실패 — 기존 캐시 유지, 09시 publish가 자가복구: {e}")
            sys.exit(1)
        try:
            exp = json.load(open(TOKEN_CACHE, encoding="utf-8")).get("expired")
        except Exception:
            exp = None
        # revoke 실패면 KIS가 기존(드리프트) 토큰을 반환했을 수 있어 '07:00 고정' 미달성 → 경고+비0(cron 로그 식별).
        if rv is not None and rv.get("_error"):
            print(f"[kis] ⚠ revoke 실패로 07:00 고정 미달성 가능(만료: {exp}) — 다음 회차 재시도")
            sys.exit(1)
        print(f"[kis] 토큰 발급 완료(07:00 고정) — 만료: {exp}")
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "rank":
        markets = [sys.argv[2]] if len(sys.argv) > 2 else ["KOSPI", "KOSDAQ"]
        for mkt in markets:
            v = value_rank(mkt)
            print(f"== value_rank {mkt} ({len(v)}건) ==")
            print(json.dumps(v[:3] + v[-1:], ensure_ascii=False, indent=1))
        sys.exit(0)
    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    print("== price_now ==")
    print(json.dumps(price_now(code), ensure_ascii=False, indent=1))
    d = daily_prices(code, days=12)
    print(f"== daily_prices ({len(d)}건, 최근 3) ==")
    print(json.dumps(d[-3:], ensure_ascii=False, indent=1))
    m = minute_bars_today(code)
    print(f"== minute_bars_today ({len(m)}건) ==")
    print(json.dumps(m[:2] + m[-2:], ensure_ascii=False, indent=1))
    inv = investor_daily(code)
    print(f"== investor_daily ({len(inv)}건, 최근 3) ==")
    print(json.dumps(inv[-3:], ensure_ascii=False, indent=1))
