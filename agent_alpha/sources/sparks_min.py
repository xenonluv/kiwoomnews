"""5분봉 양봉 스파크 — radar.py(aggregate_minute_bars/reignition_bars/_has_live_bars/_minute_bars_with_fallback)
로직을 복제(미러). ⚠ radar.py:85-159 산식 변경 시 동기화. 읽기전용(core 미수정).
스파크는 당일 분봉으로만 신뢰 포착(KIS 분봉=당일). UN→J 폴백(키스트론류 UN분봉0 누락 방지).
"""
from datetime import datetime, timedelta
import config  # noqa: F401 — 경로 부트스트랩
import kis_client as kis


def aggregate_minute_bars(bars, span_min):
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
                            "low": b["low"], "close": b["close"], "vol": b["vol"], "value": val}
            order.append(key)
        else:
            row["high"] = max(row["high"], b["high"])
            row["low"] = min(row["low"], b["low"])
            row["close"] = b["close"]
            row["vol"] += b["vol"]
            row["value"] += val
    return [buckets[k] for k in sorted(order)]


def reignition_bars(bars, body_pct_min=config.SPARK_BODY_PCT, span_min=config.SPARK_SPAN_MIN):
    out = []
    for bar in aggregate_minute_bars(bars, span_min):
        if bar["open"] <= 0 or bar["close"] <= bar["open"]:
            continue
        body = (bar["close"] - bar["open"]) / bar["open"] * 100
        if body < body_pct_min:
            continue
        out.append({"body_pct": round(body, 2),
                    "time": f"{bar['time'][:2]}:{bar['time'][2:4]}",
                    "value_eok": round(bar["value"] / 1e8),
                    "close": bar["close"], "open": bar["open"]})
    return out


def _has_live_bars(bars):
    return any((b.get("close") or 0) > 0 for b in bars)


def _session_bars(code, market, until="153000"):
    """KIS '가장 최근 거래 세션' 1분봉(오름차순). 코어 kis_client.minute_bars_today(:303)를 미러하되,
    휴장일 가드를 '벽시계 today' 대신 '응답에 담긴 최근 거래일(target)'로 바꾼다.

    ⚠ 코어와 다른 이유: 코어는 실전 라이브라 today 필터로 stale 게시(월요일 아침에 금요일 봉을 '오늘 재반등'
    으로 오게시)를 막는 게 맞다. 그러나 agent_alpha는 EOD 전진수집기 — quant의 row date(=마지막 일봉 날짜)와
    '같은 거래일' 분봉을 봐야 일관된다. 주말·휴장일·마감 한참 후 수집 시 today로 필터하면 분봉이 통째로 비어
    스파크가 '미측정'이 되는데, KIS는 그때도 '가장 최근 세션(=금요일)' 분봉을 주므로 그 날짜로 필터하면 채워진다.
    (until은 15:30 고정 — 세션 전체를 받기 위해 벽시계 now로 클램프하지 않는다.)"""
    bars, hour, target = {}, until, None
    for _ in range(16):
        res = kis._call("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200",
                        {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code,
                         "FID_INPUT_HOUR_1": hour, "FID_PW_DATA_INCU_YN": "N", "FID_ETC_CLS_CODE": ""})
        rows = res.get("output2", []) or []
        if not rows:
            break
        if target is None:
            ds = [r.get("stck_bsop_date") for r in rows if r.get("stck_bsop_date")]
            if not ds:
                break
            target = max(ds)   # 가장 최근 거래일 (평일 마감후=오늘 / 주말·휴장일=마지막 거래일)
        page_earliest = None
        for row in rows:
            t = row.get("stck_cntg_hour", "")
            if row.get("stck_bsop_date") != target or len(t) != 6:
                continue
            if page_earliest is None or t < page_earliest:
                page_earliest = t
            if not (kis.SESSION_OPEN <= t <= kis.SESSION_CLOSE):
                continue   # NXT 장전·애프터마켓 배제 — 정규장만
            bars.setdefault(t, {"time": t, "open": kis._f(row.get("stck_oprc")),
                                "high": kis._f(row.get("stck_hgpr")), "low": kis._f(row.get("stck_lwpr")),
                                "close": kis._f(row.get("stck_prpr")), "vol": kis._f(row.get("cntg_vol"))})
        if page_earliest is None or page_earliest <= "090000":
            break
        prev = datetime.strptime(min(page_earliest, hour), "%H%M%S") - timedelta(minutes=1)
        if prev.strftime("%H%M%S") < "090000":
            break
        hour = prev.strftime("%H%M%S")
    return [bars[t] for t in sorted(bars)]


def _minute_bars(code):
    """UN 우선, 결측 시 J 폴백 (radar._minute_bars_with_fallback 미러). (bars, source).
    '가장 최근 거래 세션' 기준(_session_bars) — 주말·마감후에도 마지막 거래일 분봉으로 스파크 산정."""
    bars = _session_bars(code, kis.MONEY_MARKET)
    src = "kis_un" if kis.MONEY_MARKET != "J" else "kis_j"
    if kis.MONEY_MARKET != "J" and not _has_live_bars(bars):
        jb = _session_bars(code, "J")
        if _has_live_bars(jb):
            bars, src = jb, "kis_j"
    return bars, src


def spark_1430(code):
    """(count, max_body_pct, bars[], source) — 14:30↑ 5분 양봉 몸통%≥SPARK_BODY_PCT(1.5) 스파크.
    분봉 결측(과거일·미체결)이면 (0,0,[],"none") — 날조 금지."""
    try:
        bars, src = _minute_bars(code)
    except Exception:
        return 0, 0.0, [], "none"
    if not _has_live_bars(bars):
        return 0, 0.0, [], "none"
    rb = reignition_bars(bars)
    rb1430 = [b for b in rb if b["time"] >= config.SPARK_START_HHMM]
    mx = max((b["body_pct"] for b in rb1430), default=0.0)
    return (len(rb1430), mx,
            [{"time": b["time"], "body_pct": b["body_pct"]} for b in rb1430], src)
