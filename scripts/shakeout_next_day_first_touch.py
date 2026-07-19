#!/usr/bin/env python3
"""최종 흔들기 종목의 익일 +7%/-5% 최초 도달 순서를 기록한다.

연구용 독립 계측기다. 자동매매·순위·웹·기존 performance를 읽거나 쓰지 않는다.
평일 장 마감 후 실행하여 직전 KRX 거래일의 ``shakeout && final`` 종목만
대상으로 당일 KRX 1분봉 원본과 최초 터치 라벨을 로컬 raw store에 저장한다.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kiwoom_client as broker
import radar_json_store as store


KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DIR = os.path.join(REPO, "data", "radar_history")
BENCHMARK_CODE = "005930"  # KRX 거래일 달력 확인용(삼성전자)
TP_PCT = 7.0
SL_PCT = -5.0


def log(message):
    print(f"[{datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}] {message}",
          file=sys.stderr)


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _history_path(signal_date, history_dir=HISTORY_DIR):
    return os.path.join(history_dir, f"{signal_date}.json")


def load_final_shakeouts(signal_date, history_dir=HISTORY_DIR):
    """신호일 history에서 마감까지 남은 흔들기만 코드순으로 반환한다."""
    path = _history_path(signal_date, history_dir)
    if not os.path.exists(path):
        return []
    payload = _load_json(path)
    stored_date = str(payload.get("date") or "").replace("-", "")
    if stored_date and stored_date != signal_date:
        raise ValueError(f"history date mismatch: expected={signal_date} actual={stored_date}")
    rows = []
    for code, record in (payload.get("suspects") or {}).items():
        if not isinstance(record, dict):
            continue
        if record.get("shakeout") is True and record.get("final") is True:
            rows.append({"code": str(code).lstrip("A").zfill(6),
                         "name": record.get("name") or str(code)})
    return sorted(rows, key=lambda row: row["code"])


def resolve_signal_date(result_date, calendar_bars):
    """결과일 바로 앞의 실제 KRX 거래일을 반환한다."""
    dates = sorted({str(row.get("date")) for row in calendar_bars
                    if isinstance(row, dict) and row.get("date")})
    if result_date not in dates:
        raise ValueError(f"result date is not the latest available KRX session: {result_date}")
    prior = [value for value in dates if value < result_date]
    if not prior:
        raise ValueError(f"previous KRX session is unavailable: {result_date}")
    return prior[-1]


def confirmed_close(code, signal_date, result_date, daily_bars):
    """동일 KRX 일봉 배열에서 신호일 종가와 결과일 존재를 함께 검증한다."""
    by_date = {str(row.get("date")): row for row in daily_bars
               if isinstance(row, dict) and row.get("date")}
    signal = by_date.get(signal_date)
    result = by_date.get(result_date)
    close = _number((signal or {}).get("close"))
    if close is None or close <= 0:
        raise ValueError(f"confirmed KRX close missing: {code} {signal_date}")
    if result is None:
        raise ValueError(f"result KRX daily bar missing: {code} {result_date}")
    return close


def _normalized_bars(bars):
    indexed = {}
    for raw in bars or []:
        if not isinstance(raw, dict):
            continue
        digits = "".join(ch for ch in str(raw.get("time") or "") if ch.isdigit())
        if len(digits) == 4:
            digits += "00"
        if len(digits) != 6 or not ("090000" <= digits <= "153000"):
            continue
        bar = {key: _number(raw.get(key)) for key in ("open", "high", "low", "close")}
        if any(value is None or value <= 0 for value in bar.values()):
            raise ValueError(f"invalid OHLC at {digits}")
        if bar["low"] > min(bar["open"], bar["close"], bar["high"]):
            raise ValueError(f"invalid low at {digits}")
        if bar["high"] < max(bar["open"], bar["close"], bar["low"]):
            raise ValueError(f"invalid high at {digits}")
        normalized = {"time": digits, **bar, "vol": _number(raw.get("vol")) or 0.0}
        previous = indexed.get(digits)
        if previous is not None and previous != normalized:
            raise ValueError(f"conflicting minute bars at {digits}")
        indexed[digits] = normalized
    return [indexed[key] for key in sorted(indexed)]


def classify_first_touch(entry_price, bars):
    """시가를 우선 처리한 뒤 +7/-5 최초 터치를 판정한다."""
    entry = _number(entry_price)
    if entry is None or entry <= 0:
        raise ValueError("entry_price must be positive")
    ordered = _normalized_bars(bars)
    plus7_price = entry * (1 + TP_PCT / 100)
    minus5_price = entry * (1 + SL_PCT / 100)
    base = {"plus7_price": round(plus7_price, 6),
            "minus5_price": round(minus5_price, 6),
            "plus7_time": None, "minus5_time": None}
    if not ordered:
        return {**base, "first_touch": "minute_missing", "first_touch_time": None}

    for index, bar in enumerate(ordered):
        # 각 분봉의 open은 그 분봉의 high/low보다 시간상 먼저다. 장중 거래정지·VI
        # 재개 뒤 첫 체결이 임계값을 건너뛴 경우에도 same-minute unknown으로 오인하지 않는다.
        if bar["open"] >= plus7_price:
            return {**base, "plus7_time": bar["time"],
                    "first_touch": "plus7_first", "first_touch_time": bar["time"],
                    "first_touch_basis": "opening_gap" if index == 0 else "minute_open"}
        if bar["open"] <= minus5_price:
            return {**base, "minus5_time": bar["time"],
                    "first_touch": "minus5_first", "first_touch_time": bar["time"],
                    "first_touch_basis": "opening_gap" if index == 0 else "minute_open"}
        plus_hit = bar["high"] >= plus7_price
        minus_hit = bar["low"] <= minus5_price
        if plus_hit and minus_hit:
            return {**base, "plus7_time": bar["time"], "minus5_time": bar["time"],
                    "first_touch": "same_minute_unknown",
                    "first_touch_time": bar["time"], "first_touch_basis": "minute_ohlc"}
        if plus_hit:
            return {**base, "plus7_time": bar["time"],
                    "first_touch": "plus7_first", "first_touch_time": bar["time"],
                    "first_touch_basis": "minute_ohlc"}
        if minus_hit:
            return {**base, "minus5_time": bar["time"],
                    "first_touch": "minus5_first", "first_touch_time": bar["time"],
                    "first_touch_basis": "minute_ohlc"}
    return {**base, "first_touch": "neither", "first_touch_time": None,
            "first_touch_basis": "full_session"}


def _result_path(signal_date, root=None):
    return store.local_day_dir(signal_date, root) / "evaluation" / "shakeout_first_touch.json"


def run(result_date=None, *, history_dir=HISTORY_DIR, root=None,
        daily_fetch=None, minute_fetch=None, now=None):
    """한 결과일을 수집·판정한다. 후보별 실패는 격리해 나머지를 계속 처리한다."""
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    now = now.astimezone(KST)
    result_date = store.normalize_trade_date(result_date or now)
    if result_date == now.strftime("%Y%m%d") and now.strftime("%H%M%S") < "154000":
        raise ValueError("current KRX session is not finalized; run at or after 15:40 KST")
    daily_fetch = daily_fetch or broker.daily_prices
    minute_fetch = minute_fetch or broker.minute_bars_today_with_meta

    calendar = daily_fetch(BENCHMARK_CODE, days=15, market="J")
    signal_date = resolve_signal_date(result_date, calendar)
    candidates = load_final_shakeouts(signal_date, history_dir)
    results = []
    minute_saved_n = 0
    for candidate in candidates:
        code = candidate["code"]
        base = {**candidate, "signal_date": signal_date, "result_date": result_date,
                "entry_basis": "KRX_CLOSE", "source_market": "J"}
        try:
            daily = daily_fetch(code, days=15, market="J")
            entry = confirmed_close(code, signal_date, result_date, daily)
            minute = minute_fetch(code, until="153000", market="J")
            actual_date = str(minute.get("trade_date") or "")
            if actual_date != result_date:
                raise ValueError(
                    f"trade_date_mismatch: expected={result_date} actual={actual_date or 'none'}")
            bars = _normalized_bars(minute.get("bars") or [])
            if not bars:
                results.append({**base, "status": "minute_missing", "entry_price": entry,
                                "first_touch": "minute_missing", "bar_count": 0})
                continue
            saved = store.merge_minute_bars(
                result_date, code, bars, market_basis="J", source_broker="kiwoom",
                fetch_status="ok", root=root, update_manifest=False)
            if not saved.ok:
                raise RuntimeError("minute_store_error: " + str(saved.error))
            minute_saved_n += 1
            outcome = classify_first_touch(entry, bars)
            results.append({**base, "status": "evaluated", "entry_price": entry,
                            "bar_count": len(bars), "first_bar_time": bars[0]["time"],
                            "last_bar_time": bars[-1]["time"],
                            "minute_coverage": "observed_regular_session", **outcome})
        except Exception as exc:
            text = str(exc)
            if text.startswith("trade_date_mismatch:"):
                status = "trade_date_mismatch"
            elif text.startswith("confirmed KRX close missing:"):
                status = "entry_source_conflict"
            elif text.startswith("result KRX daily bar missing:"):
                status = "no_trade"
            elif text.startswith("minute_store_error:"):
                status = "minute_store_error"
            else:
                status = "error"
            results.append({**base, "status": status, "first_touch": status,
                            "error": text[:300]})
            log(f"{signal_date} {code} {candidate['name']} 제외: {text}")

    document = {
        "schema_version": 1,
        "record_type": "shakeout_next_day_first_touch",
        "signal_date": signal_date,
        "result_date": result_date,
        "population": "radar_history_shakeout_final",
        "entry_basis": "KRX_CLOSE",
        "source_market": "J",
        "generated_at": now.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "candidate_n": len(candidates),
        "evaluated_n": sum(row.get("status") == "evaluated" for row in results),
        "minute_saved_n": minute_saved_n,
        "results": results,
    }
    target = _result_path(signal_date, root)
    written = store.atomic_write_json(target, document, overwrite=True)
    if not written.ok:
        raise RuntimeError("evaluation_store_error: " + str(written.error))
    # 후보별 minute merge의 manifest 갱신은 마지막에 날짜별 한 번씩 수행한다.
    if minute_saved_n:
        manifest = store.rebuild_manifest(result_date, root)
        if not manifest.ok:
            log("결과일 manifest 갱신 실패: " + str(manifest.error))
    manifest = store.rebuild_manifest(signal_date, root)
    if not manifest.ok:
        log("신호일 manifest 갱신 실패: " + str(manifest.error))
    log(f"{signal_date}→{result_date} 최종흔들기 {len(candidates)}건 · 평가 "
        f"{document['evaluated_n']}건 · {target}")
    return str(target), document


def main():
    parser = argparse.ArgumentParser(description="최종 흔들기 익일 +7/-5 최초터치 기록")
    parser.add_argument("--date", help="결과 거래일 YYYYMMDD; 기본=오늘")
    args = parser.parse_args()
    try:
        run(args.date)
    except Exception as exc:
        log(f"회차 실패(운영 영향 없음): {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
