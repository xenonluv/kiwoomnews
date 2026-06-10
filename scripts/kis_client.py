#!/usr/bin/env python3
"""한국투자증권(KIS) Open API 클라이언트 — 표준라이브러리 전용.

.env의 KIS_APP_KEY / KIS_APP_SECRET 사용. 토큰은 .kis_token.json에 캐시
(유효 1일, 발급은 1분 1회 제한이므로 캐시 필수).

제공 함수:
  daily_prices(code, days)   일봉 (날짜·시고저종·거래량·거래대금)
  price_now(code)            현재가 스냅샷 (현재가·당일고가·등락률·거래대금·업종)
  minute_bars_today(code)    당일 1분봉 전체 (시각 역방향 페이지네이션)
  investor_daily(code)       종목별 투자자 일별 순매수 (외국인/기관/개인)

단독 실행 시 삼성전자(005930)로 4개 API를 점검한다:
  python3 scripts/kis_client.py [종목코드]
"""
import os
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


def daily_prices(code, days=30):
    """일봉 최근 days개 (오름차순). value=거래대금(원)."""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days * 2 + 10)).strftime("%Y%m%d")
    res = _call("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                "FHKST03010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                 "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": end,
                 "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
    out = []
    for row in res.get("output2", []):
        if not row.get("stck_bsop_date"):
            continue
        out.append({"date": row["stck_bsop_date"],
                    "open": _f(row.get("stck_oprc")),
                    "high": _f(row.get("stck_hgpr")),
                    "low": _f(row.get("stck_lwpr")),
                    "close": _f(row.get("stck_clpr")),
                    "volume": _f(row.get("acml_vol")),
                    "value": _f(row.get("acml_tr_pbmn"))})
    out.sort(key=lambda x: x["date"])
    return out[-days:]


def price_now(code):
    """현재가 스냅샷. 등락률·당일고가·누적거래대금·업종명 포함."""
    res = _call("/uapi/domestic-stock/v1/quotations/inquire-price",
                "FHKST01010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
    o = res["output"]
    price = _f(o.get("stck_prpr"))
    change_pct = _f(o.get("prdy_ctrt"))
    prev_close = price - _f(o.get("prdy_vrss"))  # 전일대비로 정확 역산
    return {"code": code,
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


def minute_bars_today(code, until="153000"):
    """당일 1분봉 전체 (오름차순). 1콜=30봉이라 시각을 30분씩 물려 역방향 수집.

    당일(stck_bsop_date == 오늘) 봉만 수집 — FID_PW_DATA_INCU_YN="N" +
    날짜 필터 이중 가드. 휴장일·개장 전에는 빈 리스트를 반환한다
    (전일 분봉이 당일로 혼입되면 스파크 오탐이 나므로 절대 섞지 않는다).
    """
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    if now.strftime("%H%M%S") < until:
        until = now.strftime("%H%M%S")
    bars = {}
    hour = until
    for _ in range(16):  # 09:00~15:30 = 390분 → 최대 13콜 + 여유
        res = _call("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                    "FHKST03010200",
                    {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                     "FID_INPUT_HOUR_1": hour, "FID_PW_DATA_INCU_YN": "N",
                     "FID_ETC_CLS_CODE": ""})
        rows = res.get("output2", []) or []
        got = 0
        for row in rows:
            t = row.get("stck_cntg_hour", "")
            if row.get("stck_bsop_date") != today:
                continue  # 전일/휴장일 봉 배제
            if len(t) == 6 and t not in bars:
                bars[t] = {"time": t,
                           "open": _f(row.get("stck_oprc")),
                           "high": _f(row.get("stck_hgpr")),
                           "low": _f(row.get("stck_lwpr")),
                           "close": _f(row.get("stck_prpr")),
                           "vol": _f(row.get("cntg_vol"))}
                got += 1
        if not rows or got == 0:
            break
        earliest = min(bars.keys())
        if earliest <= "090100":
            break
        prev = datetime.strptime(earliest, "%H%M%S") - timedelta(minutes=1)
        if prev.strftime("%H%M%S") < "090000":
            break
        hour = prev.strftime("%H%M%S")
    return [bars[t] for t in sorted(bars.keys())]


def investor_daily(code):
    """종목별 투자자 일별 순매수량 (최근 영업일들, 오름차순). 외국인/기관/개인."""
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


if __name__ == "__main__":
    import sys
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
