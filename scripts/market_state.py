#!/usr/bin/env python3
"""라이브 거래일 확인. 불확실하면 열림으로 추정하지 않는다."""
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
SENTINEL_CODE = "005930"


def trading_day_state(broker, now=None, code=SENTINEL_CODE):
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    state = {
        "kst_date": today,
        "is_weekday": now.weekday() < 5,
        "broker_trade_date": None,
        "is_trading_day": False,
        "market_open_confirmed": False,
        "reason": None,
    }
    if now.weekday() >= 5:
        state["reason"] = "weekend"
        return state
    # 08시 NXT 프리마켓은 당일 일봉이 아직 없으므로 NXT 원응답의 최신 분봉 날짜로 확인한다.
    # 확인 불가 시 전일 가격으로 청산 판단하지 않는다.
    if now.strftime("%H%M") < "0900":
        try:
            meta = broker.minute_bars_today_with_meta(
                code, until=now.strftime("%H%M%S"), market="NX")
            latest = meta.get("trade_date") if isinstance(meta, dict) else None
            state["broker_trade_date"] = latest
            if latest == today:
                state["is_trading_day"] = True
                state["market_open_confirmed"] = True
                state["reason"] = "confirmed_by_nxt_minute_date"
                return state
        except Exception as exc:
            state["reason"] = "preopen_trade_date_error:" + str(exc)[:160]
            return state
        state["reason"] = "preopen_trade_date_unconfirmed"
        return state
    try:
        bars = broker.daily_prices(code, days=5, market="J")
    except TypeError:
        bars = broker.daily_prices(code, days=5)
    except Exception as exc:
        state["reason"] = "broker_daily_error:" + str(exc)[:160]
        return state
    valid = [str(b.get("date") or "") for b in bars or [] if b.get("close")]
    latest = max(valid) if valid else None
    state["broker_trade_date"] = latest
    if latest != today:
        state["reason"] = "broker_latest_trade_date_mismatch"
        return state
    state["is_trading_day"] = True
    state["market_open_confirmed"] = now.strftime("%H%M") <= "1530"
    state["reason"] = "confirmed_by_daily_bar"
    return state


def require_trading_day(broker, now=None, code=SENTINEL_CODE):
    state = trading_day_state(broker, now=now, code=code)
    return bool(state.get("is_trading_day")), state
