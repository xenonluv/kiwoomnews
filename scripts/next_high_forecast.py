#!/usr/bin/env python3
"""종목명/코드 한 줄로 현재 레이더의 익일 고가 터치 가능성을 설명한다.

읽기 전용 입력:
  web/data/radar.json, web/data/performance.json,
  data/radar_history/*.json, data/shakeout_backfill.json
"""
import argparse
import glob
import json
import math
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from rank_policy import rank_bucket_info  # noqa: E402
from next_high_metrics import derive_next_high_metrics  # noqa: E402


TOUCH_LEVELS = (7, 11, 13, 15)


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return default


def _norm(value):
    return " ".join(str(value or "").strip().casefold().split())


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _quantile(values, q):
    values = sorted(float(v) for v in values)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def _rounded(value, digits=1):
    return round(value, digits) if value is not None else None


def _krx_tick(price):
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def _price_at(close, pct):
    if close is None or pct is None:
        return None
    raw = close * (1 + pct / 100)
    tick = _krx_tick(raw)
    return int(round(raw / tick) * tick)


def _history_rows(repo):
    rows = []
    pattern = os.path.join(repo, "data", "radar_history", "*.json")
    for path in sorted(glob.glob(pattern)):
        hist = _load_json(path, {})
        day = str(hist.get("date") or os.path.basename(path).split(".")[0])
        for code, record in (hist.get("suspects") or {}).items():
            if isinstance(record, dict):
                rows.append({"date": day, "code": str(code), "record": record})
    return rows


def _current_rows(radar):
    suspects = radar.get("suspects") or []
    if isinstance(suspects, dict):
        rows = [{"code": str(code), **row} for code, row in suspects.items()
                if isinstance(row, dict)]
    else:
        rows = [row for row in suspects if isinstance(row, dict)]
    blocked = radar.get("blocked_suspects") or []
    if isinstance(blocked, dict):
        blocked_rows = [{"code": str(code), **row} for code, row in blocked.items()
                        if isinstance(row, dict)]
    else:
        blocked_rows = [row for row in blocked if isinstance(row, dict)]
    return rows + [{**row, "_current_blocked": True} for row in blocked_rows]


def _resolve(query, current, histories, backfill, allow_network=True):
    query = str(query).strip()
    if not query:
        raise ValueError("종목명 또는 6자리 종목코드가 필요합니다")
    qnorm = _norm(query)
    candidates = []
    sources = (
        ("current_radar", current),
        ("history", [{"code": item["code"], **item["record"]} for item in histories]),
        ("backfill", backfill),
    )
    for source, rows in sources:
        for row in rows:
            code = str(row.get("code") or "").strip().lstrip("A")
            name = str(row.get("name") or "").strip()
            if (query.isdigit() and code == query.zfill(6)) or (name and _norm(name) == qnorm):
                candidates.append((source, code, name or code))

    if not candidates and allow_network and not query.isdigit():
        try:
            from team1_collect import resolve_code
            code = resolve_code(query)
            if code:
                candidates.append(("name_lookup", str(code), query))
        except Exception:
            pass
    if not candidates and query.isdigit() and len(query) <= 6:
        candidates.append(("code_input", query.zfill(6), query.zfill(6)))
    if not candidates:
        raise LookupError(f"종목을 찾지 못했습니다: {query}")

    codes = {code for _, code, _ in candidates}
    if len(codes) > 1:
        raise LookupError(f"동일 종목명 후보가 여러 개입니다: {', '.join(sorted(codes))}")
    code = next(iter(codes))
    current_name = next((name for source, c, name in candidates
                         if c == code and source == "current_radar"), None)
    name = current_name or next(name for _, c, name in candidates if c == code)
    return code, name


def _result_row(item):
    record = item["record"]
    result = record.get("result")
    if not record.get("evaluated") or not isinstance(result, dict):
        return None
    row = {**record, "date": item["date"], "code": item["code"],
           "name": record.get("name") or item["code"], "backfill": False}
    for key in ("next_open_pct", "next_high_pct", "next_low_pct", "return_pct"):
        row[key] = result.get(key)
    row.update(derive_next_high_metrics(result.get("entry"), result.get("next_high")))
    return row


def _evaluated_rows(histories):
    return [row for row in (_result_row(item) for item in histories) if row is not None]


def _touch_stats(rows):
    highs = [_number(row.get("next_high_pct_raw")) for row in rows]
    highs = [value for value in highs if value is not None]
    out = {"n": len(highs), "touch": {}}
    for level in TOUCH_LEVELS:
        flag = f"touch{level}"
        eligible_n = sum(row.get(flag) is not None for row in rows)
        hits = sum(row.get(flag) is True for row in rows)
        out["touch"][str(level)] = {
            "hits": hits,
            "n": eligible_n,
            "rate": _rounded(hits / eligible_n * 100) if eligible_n else None,
        }
    out.update({
        "avg_high_pct": _rounded(sum(highs) / len(highs), 2) if highs else None,
        "median_high_pct": _rounded(_quantile(highs, 0.5), 2),
        "q25_high_pct": _rounded(_quantile(highs, 0.25), 2),
        "q75_high_pct": _rounded(_quantile(highs, 0.75), 2),
        "min_high_pct": _rounded(min(highs), 2) if highs else None,
        "max_high_pct": _rounded(max(highs), 2) if highs else None,
    })
    paired = [(high, low) for high, low in (
        (_number(row.get("next_high_pct")), _number(row.get("next_low_pct"))) for row in rows)
              if high is not None and low is not None]
    low5 = sum(low <= -5 for _, low in paired)
    both = sum(high >= 7 and low <= -5 for high, low in paired)
    out["risk"] = {
        "paired_n": len(paired),
        "low_minus5_hits": low5,
        "low_minus5_rate": _rounded(low5 / len(paired) * 100) if paired else None,
        "both_plus7_minus5_hits": both,
        "both_plus7_minus5_rate": _rounded(both / len(paired) * 100) if paired else None,
    }
    return out


def _performance_cell(performance, section, bucket):
    table = performance.get(section) or {}
    table = ((table.get("eod") or {}) if section == "rank_bucket_stats_forward"
             else (table.get("exclusive_all") or table))
    return next((cell for cell in table.get("cells", []) if cell.get("bucket") == bucket), None)


def _shakeout_population(evaluated, backfill):
    live = [row for row in evaluated
            if row.get("shakeout") and row.get("visible_experimental")
            and row.get("turnover_2d_pct") is not None]
    seen = {(row.get("code"), row.get("date")) for row in live}
    retro = [dict(row) for row in backfill
             if row.get("turnover_2d_pct") is not None
             and (str(row.get("code")), str(row.get("date"))) not in seen]
    return live, retro, live + retro


def _current_signal(code, current):
    return next((row for row in current
                 if str(row.get("code") or "").strip().lstrip("A") == code), None)


def _latest_history(code, histories):
    matches = [item for item in histories if item["code"] == code]
    return matches[-1] if matches else None


def analyze(query, repo=REPO, allow_network=True):
    radar = _load_json(os.path.join(repo, "web", "data", "radar.json"), {})
    performance = _load_json(os.path.join(repo, "web", "data", "performance.json"), {})
    backfill_doc = _load_json(os.path.join(repo, "data", "shakeout_backfill.json"), {})
    backfill = backfill_doc.get("samples", []) if isinstance(backfill_doc, dict) else []
    histories = _history_rows(repo)
    current = _current_rows(radar)
    code, name = _resolve(query, current, histories, backfill, allow_network=allow_network)
    signal = _current_signal(code, current)
    latest = _latest_history(code, histories)

    if signal is None:
        latest_record = latest["record"] if latest else None
        return {
            "status": "no_current_signal", "forecast_valid": False,
            "query": query, "code": code,
            "name": (latest_record or {}).get("name") or name,
            "data_as_of": radar.get("generated_at") or performance.get("as_of"),
            "latest_history": ({"signal_date": latest["date"],
                                "evaluated": bool(latest_record.get("evaluated")),
                                "result": latest_record.get("result")} if latest else None),
            "message": "현재 radar.json에 유효한 신호가 없어 익일 예측을 만들지 않습니다.",
        }

    eligibility = signal.get("next_session_eligibility") or {}
    if signal.get("_current_blocked") or eligibility.get("recommendable") is False:
        return {
            "status": "next_session_ineligible",
            "forecast_valid": False,
            "query": query,
            "code": code,
            "name": signal.get("name") or name,
            "data_as_of": radar.get("generated_at") or performance.get("as_of"),
            "target_trade_date": eligibility.get("target_trade_date"),
            "next_session_eligibility": eligibility,
            "message": (eligibility.get("reason")
                        or signal.get("blocked_reason")
                        or "현재 신호는 다음 거래일 추천 부적격으로 제외됐습니다."),
        }

    signal_date = str(signal.get("signal_date") or radar.get("date") or "").replace("-", "")
    known_result = None
    if latest and latest["date"] == signal_date and latest["record"].get("evaluated"):
        known_result = latest["record"].get("result")
    forecast_valid = known_result is None

    bucket_meta = rank_bucket_info(signal)
    signal_bucket = signal.get("rank_bucket")
    # 현재 소급 비교는 현재 정책 bucket끼리만 묶는다. 구버전 신호의 저장 bucket을
    # 현재 정책으로 재분류한 과거행에 그대로 조인하면 모델 의미가 섞인다.
    bucket = bucket_meta.get("rank_bucket")
    evaluated = _evaluated_rows(histories)
    bucket_rows = [row for row in evaluated
                   if rank_bucket_info(row).get("rank_bucket") == bucket]
    bucket_stats = _touch_stats(bucket_rows)
    live, retro, shakeout_all = _shakeout_population(evaluated, backfill)
    same_stock = [row for row in shakeout_all if str(row.get("code")) == code]

    retro_cell = _performance_cell(performance, "rank_bucket_stats_retro", bucket)
    forward_cell = _performance_cell(performance, "rank_bucket_stats_forward", bucket)
    close = _number(signal.get("signal_close") or signal.get("price") or signal.get("snapshot_close"))
    point = bucket_stats.get("median_high_pct")
    if point is None:
        point = _number(signal.get("expected_high_pct"))
    low, high = bucket_stats.get("q25_high_pct"), bucket_stats.get("q75_high_pct")

    forward_n = int((forward_cell or {}).get("n") or 0)
    evidence_n = bucket_stats["n"]
    confidence = "medium" if forward_n >= 20 else "low"
    warnings = []
    if forward_n < 20:
        warnings.append(f"현재 모델 forward 동일 버킷 표본 {forward_n}건")
    if evidence_n < 10:
        warnings.append(f"현재 정책 소급 동일 버킷 표본 {evidence_n}건")
    if len(retro) > len(live):
        warnings.append(f"흔들기 전체는 라이브 {len(live)}건보다 소급 {len(retro)}건이 많음")
    if not signal.get("rank_model_version"):
        warnings.append("신호 당시 rank_model_version 미기록 또는 혼합 배포")
    if signal_bucket is not None and signal_bucket != bucket:
        warnings.append(
            f"신호 당시 bucket {signal_bucket}와 현재 소급 bucket {bucket}을 분리해 계산"
        )
    if (_number(signal.get("turnover_2d_pct")) or 0) >= 180:
        warnings.append("2일 회전율 극과열 구간")
    if (signal.get("material") or {}).get("grade") in (None, "N"):
        warnings.append("확인된 직접 재료 없음")
    if known_result is not None:
        warnings.append("해당 신호의 결과가 이미 평가되어 미래예측으로 사용할 수 없음")

    return {
        "status": "ok" if forecast_valid else "outcome_already_known",
        "forecast_valid": forecast_valid, "query": query, "code": code,
        "name": signal.get("name") or name,
        "data_as_of": radar.get("generated_at") or performance.get("as_of"),
        "performance_as_of": performance.get("as_of"),
        "signal": {
            "date": signal_date, "close": close, "pattern": signal.get("pattern"),
            "shakeout": bool(signal.get("shakeout")),
            "suspicion_score": signal.get("suspicion_score"),
            "rank_bucket": signal_bucket if signal_bucket is not None else bucket,
            "current_retro_bucket": bucket,
            "rank_reason": (signal.get("rank_reason") if signal_bucket is not None
                            else bucket_meta.get("rank_reason")),
            "current_retro_reason": bucket_meta.get("rank_reason"),
            "rank_model_version": signal.get("rank_model_version"),
            "change_pct": signal.get("change_pct"), "high_pct": signal.get("high_pct"),
            "fade_pct": signal.get("fade_pct"),
            "turnover_2d_pct": signal.get("turnover_2d_pct"),
            "peak_dd_pct": signal.get("peak_dd_pct"), "strength": signal.get("strength"),
            "material_grade": (signal.get("material") or {}).get("grade"),
        },
        "forecast": {
            "point_high_pct": point, "point_high_price": _price_at(close, point),
            "q25_high_pct": low, "q75_high_pct": high,
            "q25_high_price": _price_at(close, low), "q75_high_price": _price_at(close, high),
            "confidence": confidence,
        },
        "bucket_evidence": bucket_stats,
        "signal_time_prior": {
            "expected_touch7_rate": signal.get("expected_touch7_rate"),
            "expected_high_pct": signal.get("expected_high_pct"),
            "snapshot": signal.get("rank_bucket_stats_snapshot"),
        },
        "current_retro_cell": retro_cell, "current_forward_cell": forward_cell,
        "shakeout_population": {
            "all": _touch_stats(shakeout_all), "live": _touch_stats(live),
            "backfill": _touch_stats(retro),
        },
        "same_stock_history": {
            "n": len(same_stock),
            "rows": [{"signal_date": row.get("date"),
                      "next_high_pct": row.get("next_high_pct"),
                      "next_low_pct": row.get("next_low_pct"),
                      "return_pct": row.get("return_pct"),
                      "backfill": bool(row.get("backfill"))}
                     for row in sorted(same_stock, key=lambda row: row.get("date") or "")],
        },
        "known_result": known_result, "warnings": warnings,
    }


def _fmt_pct(value):
    return "자료 없음" if value is None else f"{value:+.1f}%"


def _fmt_price(value):
    return "자료 없음" if value is None else f"{value:,.0f}원"


def format_text(report):
    if not report.get("forecast_valid"):
        lines = [f"{report['name']} ({report['code']}) 익일 고가 분석",
                 f"기준: {report.get('data_as_of') or '알 수 없음'}",
                 report.get("message") or "현재 미래예측으로 사용할 수 있는 신호가 없습니다."]
        latest = report.get("latest_history")
        if latest:
            lines.append(f"최근 기록: {latest.get('signal_date')} · 평가={latest.get('evaluated')}")
        return "\n".join(lines)

    signal, forecast = report["signal"], report["forecast"]
    touch = report["bucket_evidence"]["touch"]
    lines = [
        f"{report['name']} ({report['code']}) 익일 고가 분석",
        f"기준: {report.get('data_as_of') or '알 수 없음'} · 신호일 {signal.get('date')}",
        (f"신호: {signal.get('pattern')} · {signal.get('suspicion_score')}점 · "
         f"bucket {signal.get('rank_bucket')} · 종가 {_fmt_price(signal.get('close'))}"),
        "터치 확률(현재 정책 동일 버킷): " + " · ".join(
            f"+{level}% {touch[str(level)]['rate']}% ({touch[str(level)]['hits']}/{report['bucket_evidence']['n']})"
            for level in TOUCH_LEVELS),
        (f"중심 고가: {_fmt_pct(forecast.get('point_high_pct'))} "
         f"({_fmt_price(forecast.get('point_high_price'))})"),
        (f"경험적 중간 50% 범위: {_fmt_pct(forecast.get('q25_high_pct'))}~"
         f"{_fmt_pct(forecast.get('q75_high_pct'))} "
         f"({_fmt_price(forecast.get('q25_high_price'))}~{_fmt_price(forecast.get('q75_high_price'))})"),
        f"근거 표본: bucket n={report['bucket_evidence']['n']} · 신뢰도 {forecast.get('confidence')}",
    ]
    same = report["same_stock_history"]
    if same["n"]:
        rows = ", ".join(f"{row['signal_date']} 고가 {_fmt_pct(row['next_high_pct'])}"
                         for row in same["rows"][-3:])
        lines.append(f"동일 종목 과거 {same['n']}건: {rows}")
    if report.get("warnings"):
        lines.append("주의: " + " · ".join(report["warnings"]))
    lines.append("통계적 고가 범위이며 실제 체결가격이나 수익을 보장하지 않습니다.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="종목별 익일 고가 터치 가능성 분석")
    parser.add_argument("query", nargs="+", help="종목명 또는 6자리 코드")
    parser.add_argument("--repo", default=REPO, help="kiwoomnews 저장소 경로")
    parser.add_argument("--json", action="store_true", help="기계 판독용 JSON 출력")
    parser.add_argument("--no-network", action="store_true", help="종목명 온라인 해석 비활성")
    args = parser.parse_args(argv)
    query = " ".join(args.query)
    try:
        report = analyze(query, repo=os.path.abspath(args.repo), allow_network=not args.no_network)
    except (ValueError, LookupError) as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)
              if args.json else f"분석 실패: {e}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
