#!/usr/bin/env python3
"""비게시 포함 일별 관찰 cohort와 익일 결과를 독립적으로 생성한다.

운영 rank/performance 파일을 읽거나 쓰지 않는다. 실패해도 publish/backtest/autotrade와 무관하다.
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kiwoom_client as broker
import radar_json_store as store

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(REPO, "data", "local", "radar_raw")
STATS_PATH = os.path.join(ROOT, "research_turnover_performance.json")

COVERAGE_BANDS = [(0, 5, "0~5"), (5, 10, "5~10"), (10, 20, "10~20"),
                  (20, 40, "20~40"), (40, 80, "40~80"), (80, 150, "80~150"),
                  (150, 300, "150~300"), (300, float("inf"), "300+")]
HYPOTHESIS_BANDS = [(0, 90, "<90"), (90, 110, "90~110"),
                    (110, 150, "110~150"), (150, 200, "150~200"),
                    (200, 300, "200~300"), (300, float("inf"), "300+")]


def log(message):
    print(f"[{datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}] {message}", file=sys.stderr)


def day_dir(date):
    return os.path.join(ROOT, date[:4], date[4:6], date[6:8])


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _run_time(payload):
    value = str((payload.get("run") or {}).get("generated_at") or "").replace(" KST", "")
    try:
        return datetime.fromisoformat(value).replace(tzinfo=KST)
    except Exception:
        return None


def select_krx_close_scan(date):
    cutoff = datetime.strptime(date + "153000", "%Y%m%d%H%M%S").replace(tzinfo=KST)
    eligible = []
    for path in glob.glob(os.path.join(day_dir(date), "scans", "*.json")):
        try:
            payload = _load(path)
            run = payload.get("run") or {}
            when = _run_time(payload)
            if (payload.get("record_type") == "scan_run" and run.get("scan_ok") is True
                    and not run.get("dry_run") and run.get("trade_date") == date
                    and when is not None and when <= cutoff):
                eligible.append((when, path, payload))
        except Exception as exc:
            log(f"scan skip {path}: {exc}")
    return max(eligible, key=lambda item: item[0]) if eligible else None


def build_cohort(date):
    selected = select_krx_close_scan(date)
    if selected is None:
        return None
    when, source_path, scan = selected
    rows = []
    for obs in scan.get("observations") or []:
        if not isinstance(obs, dict) or not obs.get("code"):
            continue
        status = obs.get("status")
        price = obs.get("price_snapshot") or {}
        eligible = status not in ("API_ERROR", "MISSING_DATA") and bool(price.get("current"))
        reasons = []
        if status in ("API_ERROR", "MISSING_DATA"):
            reasons.append(status)
        if not price.get("current"):
            reasons.append("MISSING_SIGNAL_PRICE")
        rows.append({
            "code": str(obs["code"]), "name": obs.get("name"), "status": status,
            "eligible_for_forward_eval": eligible, "exclusion_reasons": reasons,
            "source_universes": obs.get("source_universes") or [],
            "gate_decisions": obs.get("gate_decisions") or [],
            "price_snapshot": price, "turnover": obs.get("turnover") or {},
            "technical": obs.get("technical") or {}, "sparks": obs.get("sparks") or {},
            "explosion_history": obs.get("explosion_history") or {},
            "rank": obs.get("rank") or {},
        })
    document = {
        "schema_version": 1, "record_type": "observed_research_cohort",
        "trade_date": date, "population": "research_krx_close",
        "source_run_id": (scan.get("run") or {}).get("run_id"),
        "source_generated_at": (scan.get("run") or {}).get("generated_at"),
        "source_scan": os.path.relpath(source_path, day_dir(date)),
        "rank_model_version": (scan.get("run") or {}).get("rank_model_version"),
        "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "observations": rows,
    }
    target = os.path.join(day_dir(date), "research", "krx_close_cohort.json")
    result = store.atomic_write_json(target, document, overwrite=False)
    if not result.ok and result.error == "immutable target already exists":
        existing = _load(target)
        if existing.get("source_run_id") == document["source_run_id"]:
            return target
    if not result.ok:
        raise RuntimeError(result.error)
    store.rebuild_manifest(date, ROOT)
    log(f"cohort {date}: {len(rows)} observations from {when}")
    return target


def _next_bars(code, date, cache):
    if code not in cache:
        cache[code] = broker.daily_prices(code, days=40, market="J")
        time.sleep(0.11)
    bars = [b for b in cache[code] if b.get("date") and b.get("close")]
    signal = next((b for b in bars if b["date"] == date), None)
    nxt = next((b for b in bars if b["date"] > date), None)
    return signal, nxt


def evaluate_cohort(date, cache=None):
    path = os.path.join(day_dir(date), "research", "krx_close_cohort.json")
    if not os.path.exists(path):
        return None
    existing_path = os.path.join(day_dir(date), "evaluation", "observed_union_next_day.json")
    existing = None
    if os.path.exists(existing_path):
        try:
            existing = _load(existing_path)
            if (existing.get("source_run_id") == _load(path).get("source_run_id")
                    and all(r.get("status") in ("evaluated", "excluded")
                            for r in existing.get("results") or [])):
                return existing_path
        except Exception:
            existing = None
    cache = cache if cache is not None else {}
    cohort = _load(path)
    terminal_by_code = {}
    if existing and existing.get("source_run_id") == cohort.get("source_run_id"):
        terminal_by_code = {
            r.get("code"): r for r in existing.get("results") or []
            if r.get("code") and r.get("status") in ("evaluated", "excluded")
        }
    results = []
    calls_before = len(cache)
    for row in cohort.get("observations") or []:
        if row["code"] in terminal_by_code:
            # Forward 확정치는 불변이다. API 조회창 밖으로 밀렸거나 일시 장애가 나도
            # evaluated/excluded를 pending으로 강등하거나 통계에서 소실시키지 않는다.
            results.append(terminal_by_code[row["code"]])
            continue
        base = {"code": row["code"], "name": row.get("name"), "source_status": row.get("status")}
        if not row.get("eligible_for_forward_eval"):
            results.append({**base, "status": "excluded", "reasons": row.get("exclusion_reasons") or []})
            continue
        try:
            signal, nxt = _next_bars(row["code"], date, cache)
        except Exception as exc:
            results.append({**base, "status": "pending", "reason": "daily_api_error", "error": str(exc)[:300]})
            continue
        if not signal or not nxt:
            results.append({**base, "status": "pending", "reason": "signal_or_next_bar_missing"})
            continue
        entry = float(signal["close"])
        pct = lambda value: round((float(value) / entry - 1) * 100, 2)
        high_pct, low_pct = pct(nxt["high"]), pct(nxt["low"])
        results.append({
            **base, "status": "evaluated", "entry_basis": "KRX_CLOSE", "entry_price": entry,
            "turnover_pct": (row.get("turnover") or {}).get("turnover_pct"),
            "next_day": {"date": nxt["date"], "open": nxt["open"], "high": nxt["high"],
                         "low": nxt["low"], "close": nxt["close"],
                         "open_pct": pct(nxt["open"]), "high_pct": high_pct,
                         "low_pct": low_pct, "close_pct": pct(nxt["close"]),
                         "touch_up": {str(x): high_pct >= x for x in (3, 7, 11, 13, 15)},
                         "touch_down": {str(x): low_pct <= -x for x in (3, 5, 7, 10)},
                         "high_low_order": "unknown"},
        })
    payload = {
        "schema_version": 1, "record_type": "observed_union_next_day",
        "signal_date": date, "population": cohort.get("population"),
        "source_run_id": cohort.get("source_run_id"),
        "evaluated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "api_unique_calls": len(cache) - calls_before, "results": results,
    }
    target = existing_path
    result = store.atomic_write_json(target, payload, overwrite=True)
    if not result.ok:
        raise RuntimeError(result.error)
    store.rebuild_manifest(date, ROOT)
    log(f"evaluation {date}: {sum(r.get('status') == 'evaluated' for r in results)} evaluated, calls={payload['api_unique_calls']}")
    return target


def _median(values):
    values = sorted(values)
    if not values:
        return None
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def _band_stats(rows, bands):
    cells = []
    for lo, hi, label in bands:
        grp = [r for r in rows if lo <= r["turnover_pct"] < hi]
        highs = [r["next_day"]["high_pct"] for r in grp]
        lows = [r["next_day"]["low_pct"] for r in grp]
        cells.append({
            "band": label, "n": len(grp), "unique_n": len({r["code"] for r in grp}),
            "median_high_pct": round(_median(highs), 2) if highs else None,
            "touch": {str(x): {"hits": sum(v >= x for v in highs),
                                "rate": round(sum(v >= x for v in highs) / len(highs) * 100, 1) if highs else None}
                      for x in (7, 11, 13, 15)},
            "low_minus5": {"hits": sum(v <= -5 for v in lows),
                           "rate": round(sum(v <= -5 for v in lows) / len(lows) * 100, 1) if lows else None},
            "both_plus7_minus5": {"hits": sum(h >= 7 and l <= -5 for h, l in zip(highs, lows)),
                                  "rate": round(sum(h >= 7 and l <= -5 for h, l in zip(highs, lows)) / len(grp) * 100, 1) if grp else None},
        })
    return cells


def write_stats():
    rows = []
    for path in glob.glob(os.path.join(ROOT, "????", "??", "??", "evaluation", "observed_union_next_day.json")):
        for row in _load(path).get("results") or []:
            if row.get("status") == "evaluated" and isinstance(row.get("turnover_pct"), (int, float)):
                rows.append(row)
    payload = {
        "schema_version": 1, "record_type": "research_turnover_performance",
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "source": "observed_union_forward_only", "n": len(rows),
        "coverage_bands": _band_stats(rows, COVERAGE_BANDS),
        "hypothesis_v1": {
            "registered_at": "2026-07-11", "turnover_metric": "same_day_float_turnover_pct",
            "population": "research_krx_close", "live_only": True,
            "dedup": "one code per signal date", "target": "next_day_high_touch_7pct",
            "note": "110~150 observation cell; no automatic rank application",
            "bands": _band_stats(rows, HYPOTHESIS_BANDS),
        },
    }
    result = store.atomic_write_json(STATS_PATH, payload, overwrite=True)
    if not result.ok:
        raise RuntimeError(result.error)
    return STATS_PATH


def available_dates():
    out = []
    for path in glob.glob(os.path.join(ROOT, "????", "??", "??")):
        parts = path.rstrip(os.sep).split(os.sep)[-3:]
        value = "".join(parts)
        if len(value) == 8 and value.isdigit():
            out.append(value)
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYYMMDD; 기본 오늘 cohort + 과거 미평가")
    args = ap.parse_args()
    today = datetime.now(KST).strftime("%Y%m%d")
    target = args.date or today
    build_cohort(target)
    cache = {}
    for date in available_dates():
        if date < today:
            evaluate_cohort(date, cache=cache)
    write_stats()


if __name__ == "__main__":
    main()
