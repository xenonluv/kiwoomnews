#!/usr/bin/env python3
"""키움증권 REST API 클라이언트 — 표준라이브러리 전용.

⚠ `kis_client.py`의 **드롭인 대체**를 목표로 한다: public 함수명·인자·반환 dict 구조를
그대로 맞춰 `radar.py`/`agent_alpha` 등이 `import kiwoom_client as kis` 한 줄만 바꿔도 돌게 한다.

.env의 KIWOOM_APP_KEY / KIWOOM_SECRET_KEY 사용. 토큰은 .kiwoom_token.json에 캐시.

시장 구분(KIS의 J/UN/NX ↔ 키움 종목코드 접미사):
  "J"(KRX 정규장, 접미사 없음)  ·  "UN"(통합 SOR=KRX+NXT, 접미사 _AL)  ·  "NX"(NXT, 접미사 _NX)
  가격(OHLC)=항상 J(KRX 공식), 거래대금·거래량·수급=UN(통합) — kis_client와 동일 정책.

TR 매핑(실측 확정 2026-07-05):
  daily_prices          ka10081 주식일봉차트  /api/dostk/chart   (stk_dt_pole_chart_qry)
  price_now             ka10001 주식기본정보  /api/dostk/stkinfo  (+당일 거래대금은 일봉행에서 조달)
  minute_bars_today     ka10080 주식분봉차트  /api/dostk/chart   (stk_min_pole_chart_qry, 1콜 다일자)
  investor_daily/_trade ka10059 종목별투자자기관별요청 /api/dostk/stkinfo (stk_invsr_orgn)

단독 실행: python3 scripts/kiwoom_client.py [종목코드]
"""
import os
import sys
import json
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_CACHE = os.path.join(ROOT, ".kiwoom_token.json")
BASE = os.environ.get("KIWOOM_API_BASE_URL", "https://api.kiwoom.com")
MIN_GAP = 0.1  # 호출 간 최소 간격(초). 키움 레이트 제한 보수적 설정 — 필요 시 조정.
_last_call = [0.0]

# 가격은 항상 J(KRX 공식), 거래대금·거래량·수급만 통합(UN). KIWOOM_MARKET=J로 KRX 단독 환원.
# (드롭인: 기존 KIS_MARKET env도 폴백 인식)
MONEY_MARKET = os.environ.get("KIWOOM_MARKET", os.environ.get("KIS_MARKET", "UN"))

# 시장 구분 → 종목코드 접미사. KRX=접미사없음 / 통합(SOR)=_AL / NXT=_NX.
_MKT_SUFFIX = {"J": "", "": "", "KRX": "", "UN": "_AL", "통합": "_AL", "NX": "_NX", "NXT": "_NX"}


def _mkt(code, market):
    """종목코드에 시장 접미사를 붙인다(J/KRX=그대로, UN=_AL, NX=_NX)."""
    return code + _MKT_SUFFIX.get(market, "")


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
    app_key = os.environ.get("KIWOOM_APP_KEY", "")
    app_secret = os.environ.get("KIWOOM_SECRET_KEY", "")
    if not app_key or not app_secret:
        raise RuntimeError("KIWOOM_APP_KEY/KIWOOM_SECRET_KEY가 .env에 없습니다")
    return app_key, app_secret


_token_fail_at = [0.0]  # 발급 실패 시각 — 60초 쿨다운


def _invalidate_token():
    try:
        os.remove(TOKEN_CACHE)
    except OSError:
        pass


def _parse_expiry(expires_dt):
    """키움 expires_dt('YYYYMMDDHHMMSS') → 'YYYY-MM-DD HH:MM:SS'. 파싱 실패 시 now+23h."""
    try:
        dt = datetime.strptime(expires_dt, "%Y%m%d%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return (datetime.now() + timedelta(hours=23)).strftime("%Y-%m-%d %H:%M:%S")


def get_token(force=False):
    """캐시된 토큰 반환, 만료 임박(10분)·force 시 재발급. 발급 실패 시 60초 쿨다운."""
    cached = None
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
            return cached
        raise RuntimeError("키움 토큰 발급 쿨다운 중(직전 발급 실패)")
    app_key, app_secret = _keys()
    body = json.dumps({"grant_type": "client_credentials",
                       "appkey": app_key, "secretkey": app_secret}).encode()
    req = urllib.request.Request(
        BASE + "/oauth2/token", data=body,
        headers={"Content-Type": "application/json;charset=UTF-8"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            res = json.load(r)
    except Exception:
        _token_fail_at[0] = time.time()
        if cached and not force:
            return cached
        raise
    token = res.get("token")
    if not token or res.get("return_code") not in (0, None):
        _token_fail_at[0] = time.time()
        if cached and not force:
            return cached
        raise RuntimeError(f"키움 토큰 발급 실패: {res.get('return_code')} {res.get('return_msg')}")
    expired = _parse_expiry(res.get("expires_dt", ""))
    tmp = TOKEN_CACHE + ".tmp"
    json.dump({"token": token, "expired": expired}, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, TOKEN_CACHE)
    return token


def revoke_token(token):
    """키움 접근토큰 폐기(/oauth2/revoke). 실패는 dict로 흡수(드롭인 호환)."""
    app_key, app_secret = _keys()
    body = json.dumps({"appkey": app_key, "secretkey": app_secret, "token": token}).encode()
    req = urllib.request.Request(BASE + "/oauth2/revoke", data=body,
                                 headers={"Content-Type": "application/json;charset=UTF-8"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception as e:
        return {"_error": str(e)}


def _call(api_id, path, body, retries=3):
    """POST 호출 공통(키움은 POST + api-id 헤더).

    재시도: return_code!=0 중 토큰류·레이트류, HTTP 401(토큰 무효화+강제 재발급)·5xx·429.
    """
    app_key, app_secret = _keys()
    url = BASE + path
    data = json.dumps(body).encode()
    last_err = None
    force_token = False
    for attempt in range(retries):
        gap = MIN_GAP - (time.time() - _last_call[0])
        if gap > 0:
            time.sleep(gap)
        _last_call[0] = time.time()
        try:
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": "Bearer " + get_token(force=force_token),
                "appkey": app_key,
                "secretkey": app_secret,
                "api-id": api_id,
            }
            force_token = False
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                res = json.load(r)
            rc = res.get("return_code")
            if rc == 0 or rc is None:
                return res
            msg = (res.get("return_msg") or "").strip()
            last_err = RuntimeError(f"키움 {rc}: {msg}")
            # 토큰 관련(메시지에 토큰/token/만료 포함) → 무효화 후 강제 재발급
            if "token" in msg.lower() or "토큰" in msg or "만료" in msg:
                _invalidate_token()
                force_token = True
            else:
                raise last_err  # 그 외 비즈니스 오류는 재시도 무의미
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


def _abs(v):
    """가격 필드용: 키움은 부호(+/-)로 등락방향을 표기하므로 절대값 크기를 취한다."""
    try:
        return abs(float(str(v).replace("+", "").strip()))
    except (TypeError, ValueError):
        return 0.0


def _f(v):
    """부호 유지(순매수 수량·전일대비·등락률 등)."""
    try:
        return float(str(v).replace("+", "").strip())
    except (TypeError, ValueError):
        return 0.0


def daily_prices(code, days=30, market="J"):
    """일봉 최근 days개(오름차순). value=거래대금(원). 기본 market="J"(KRX 공식).

    ka10081. 1콜에 다수 봉(≥100)이 오므로 days<=~600은 단일 콜로 충분(cont-yn 페이징 미사용).
    ⚠ 키움 trde_prica는 백만원 단위 → ×1e6으로 원 단위(KIS 호환)로 변환.
    """
    res = _call("ka10081", "/api/dostk/chart",
                {"stk_cd": _mkt(code, market), "base_dt": datetime.now().strftime("%Y%m%d"),
                 "upd_stkpc_tp": "1"})
    acc = {}
    for row in res.get("stk_dt_pole_chart_qry", []) or []:
        d = (row.get("dt") or "").strip()
        if not d or d in acc:
            continue
        acc[d] = {"date": d,
                  "open": _abs(row.get("open_pric")),
                  "high": _abs(row.get("high_pric")),
                  "low": _abs(row.get("low_pric")),
                  "close": _abs(row.get("cur_prc")),
                  "volume": _f(row.get("trde_qty")),
                  "value": _f(row.get("trde_prica")) * 1e6}
    out = sorted(acc.values(), key=lambda x: x["date"])
    return out[-days:]


def _today_value_volume(code, market="J"):
    """당일(가장 최근 거래일) 거래대금(원)·거래량. price_now의 거래대금 보강용
    (ka10001엔 당일 거래대금 필드가 없어 일봉 최신행에서 조달)."""
    d = daily_prices(code, days=1, market=market)
    if d:
        return d[-1]["value"], d[-1]["volume"]
    return 0.0, 0.0


def price_now(code, market="J"):
    """현재가 스냅샷(등락률·당일고가·거래대금·업종/시총 등). 기본 market="J".

    ka10001(기본정보) + 당일 거래대금은 일봉 최신행에서 보강.
    """
    o = _call("ka10001", "/api/dostk/stkinfo", {"stk_cd": _mkt(code, market)})
    price = _abs(o.get("cur_prc"))
    change_pct = _f(o.get("flu_rt"))            # ka10001 flu_rt는 % 단위(예 +8.22)
    prev_close = price - _f(o.get("pred_pre"))  # 전일대비로 역산(= base_pric)
    value, volume = _today_value_volume(code, market)
    return {"code": code,
            "date": "",  # ka10001엔 기준일자 없음 — 필요 시 일봉 date 사용
            "price": price,
            "high": _abs(o.get("high_pric")),
            "low": _abs(o.get("low_pric")),
            "open": _abs(o.get("open_pric")),
            "change_pct": change_pct,
            "prev_close": round(prev_close, 2),
            "value": value,
            "volume": volume or _f(o.get("trde_qty")),
            "sector": (o.get("stk_nm") or "").strip(),  # ka10001엔 업종명 없음 → 종목명 대체(주의)
            "market_cap_eok": _f(o.get("mac")),
            "per": _f(o.get("per")),
            "w52_high": _abs(o.get("250hgst"))}


def _overlay_money(bar, un_bar):
    """가격은 그대로 두고 거래대금/거래량만 UN 값으로 덮어쓴다(0/결측이면 J 유지, max로 과소 방지)."""
    if un_bar:
        if (un_bar.get("value") or 0) > 0:
            bar["value"] = max(bar.get("value") or 0, un_bar["value"])
        if (un_bar.get("volume") or 0) > 0:
            bar["volume"] = max(bar.get("volume") or 0, un_bar["volume"])


def daily_prices_jmoney_un(code, days=30):
    """일봉: 가격(OHLC)=J(KRX 공식), 거래대금/거래량=UN(통합 _AL) 덮어쓰기. 실패 시 J로 degrade."""
    jb = daily_prices(code, days=days, market="J")
    if MONEY_MARKET == "J":
        return jb
    try:
        un = {b["date"]: b for b in daily_prices(code, days=days, market="UN")}
    except Exception as e:
        sys.stderr.write(f"[kiwoom] {code} 일봉 UN 조회 실패 → J 거래대금으로 degrade: {e}\n")
        return jb
    for b in jb:
        _overlay_money(b, un.get(b["date"]))
    return jb


def price_now_jmoney_un(code):
    """현재가: 가격=J(KRX 공식), 거래대금/거래량=UN(통합) 덮어쓰기. 실패 시 J로 degrade."""
    now = price_now(code, market="J")
    if MONEY_MARKET == "J":
        return now
    try:
        un = price_now(code, market="UN")
        _overlay_money(now, un)
    except Exception as e:
        sys.stderr.write(f"[kiwoom] {code} 현재가 UN 조회 실패 → J 거래대금으로 degrade: {e}\n")
    return now


SESSION_OPEN = "090000"
SESSION_CLOSE = "153000"


def minute_bars_today(code, until="153000", market="J"):
    """당일 1분봉 전체(오름차순). ka10080은 1콜에 다일자 봉(~900)을 주므로,
    가장 최근 거래일 봉만 골라 정규장(09:00~15:30) 창으로 거른다.

    ⚠ '오늘' 벽시계 대신 응답의 최신 거래일 기준으로 필터 — 휴장 직후·마감 후에도
    직전 거래일 분봉을 일관되게 본다(kis_client는 벽시계 today였으나 키움은 다일자 응답이라
    최신 거래일 기준이 더 견고. 라이브 장중엔 최신=오늘로 동일).
    """
    res = _call("ka10080", "/api/dostk/chart",
                {"stk_cd": _mkt(code, market), "tic_scope": "1", "upd_stkpc_tp": "1"})
    rows = res.get("stk_min_pole_chart_qry", []) or []
    if not rows:
        return []
    # 최신 거래일 판정
    days_seen = [(r.get("cntr_tm") or "")[:8] for r in rows if len(r.get("cntr_tm") or "") >= 14]
    if not days_seen:
        return []
    latest_day = max(days_seen)
    bars = {}
    for row in rows:
        ct = row.get("cntr_tm") or ""
        if len(ct) < 14 or ct[:8] != latest_day:
            continue
        t = ct[8:14]  # HHMMSS
        if not (SESSION_OPEN <= t <= SESSION_CLOSE) or t > until:
            continue
        if t not in bars:
            bars[t] = {"time": t,
                       "open": _abs(row.get("open_pric")),
                       "high": _abs(row.get("high_pric")),
                       "low": _abs(row.get("low_pric")),
                       "close": _abs(row.get("cur_prc")),
                       "vol": _f(row.get("trde_qty"))}
    return [bars[t] for t in sorted(bars.keys())]


def investor_daily(code):
    """종목별 투자자 일별 순매수량(오름차순). 외국인/기관/개인. ka10059(수량).

    반환 키는 kis_client와 동일: {date, frgn, orgn, prsn, close}."""
    res = _call("ka10059", "/api/dostk/stkinfo",
                {"dt": datetime.now().strftime("%Y%m%d"), "stk_cd": code,
                 "amt_qty_tp": "2", "trde_tp": "0", "unit_tp": "1"})
    out = []
    for row in res.get("stk_invsr_orgn", []) or []:
        d = (row.get("dt") or "").strip()
        if not d:
            continue
        out.append({"date": d,
                    "frgn": _f(row.get("frgnr_invsr")),
                    "orgn": _f(row.get("orgn")),
                    "prsn": _f(row.get("ind_invsr")),
                    "close": _abs(row.get("cur_prc"))})
    out.sort(key=lambda x: x["date"])
    return out


def investor_trade_daily(code, end_date="", market=None):
    """종목별 투자자매매동향(일별) — 투신(ivtr) 포함. ka10059.

    반환 키는 kis_client와 동일: {date, frgn, orgn, ivtr, ivtr_won}.
    수량은 amt_qty_tp=2, 금액(ivtr_won, 백만원)은 amt_qty_tp=1 별도 콜로 조달.
    """
    dt = end_date or datetime.now().strftime("%Y%m%d")
    qty = _call("ka10059", "/api/dostk/stkinfo",
                {"dt": dt, "stk_cd": code, "amt_qty_tp": "2", "trde_tp": "0", "unit_tp": "1"})
    won_by_date = {}
    try:
        amt = _call("ka10059", "/api/dostk/stkinfo",
                    {"dt": dt, "stk_cd": code, "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1"})
        for row in amt.get("stk_invsr_orgn", []) or []:
            d = (row.get("dt") or "").strip()
            if d:
                won_by_date[d] = _f(row.get("invtrt"))  # 투신 순매수 금액(백만원)
    except Exception as e:
        sys.stderr.write(f"[kiwoom] {code} 투신 금액 조회 실패(수량만 사용): {e}\n")
    out = []
    for row in qty.get("stk_invsr_orgn", []) or []:
        d = (row.get("dt") or "").strip()
        if not d:
            continue
        out.append({"date": d,
                    "frgn": _f(row.get("frgnr_invsr")),
                    "orgn": _f(row.get("orgn")),
                    "ivtr": _f(row.get("invtrt")),
                    "ivtr_won": won_by_date.get(d, 0.0)})
    out.sort(key=lambda x: x["date"])
    return out


def value_rank(market="KOSPI", top_n=20, mrkt="J"):
    """[미구현] 레이더 유니버스는 네이버 up 랭킹을 쓰므로 키움 거래대금순위 TR은 이식하지 않았다.
    (float_gate_calibration 등 연구 스크립트가 필요로 하면 ka10032 계열로 추후 구현.)"""
    raise NotImplementedError("kiwoom_client.value_rank 미구현 — 레이더는 네이버 랭킹 사용")


def value_rank_union(market="KOSPI", top_n=20):
    raise NotImplementedError("kiwoom_client.value_rank_union 미구현 — 레이더는 네이버 랭킹 사용")


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    print("== price_now ==")
    print(json.dumps(price_now(code), ensure_ascii=False, indent=1))
    print("== price_now_jmoney_un ==")
    print(json.dumps(price_now_jmoney_un(code), ensure_ascii=False, indent=1))
    d = daily_prices(code, days=12)
    print(f"== daily_prices ({len(d)}건, 최근 3) ==")
    print(json.dumps(d[-3:], ensure_ascii=False, indent=1))
    dj = daily_prices_jmoney_un(code, days=3)
    print(f"== daily_prices_jmoney_un ({len(dj)}건) ==")
    print(json.dumps(dj[-2:], ensure_ascii=False, indent=1))
    m = minute_bars_today(code)
    print(f"== minute_bars_today ({len(m)}건) ==")
    print(json.dumps(m[:2] + m[-2:], ensure_ascii=False, indent=1))
    inv = investor_daily(code)
    print(f"== investor_daily ({len(inv)}건, 최근 3) ==")
    print(json.dumps(inv[-3:], ensure_ascii=False, indent=1))
