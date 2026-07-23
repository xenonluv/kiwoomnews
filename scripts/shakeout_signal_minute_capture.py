#!/usr/bin/env python3
"""마감 후 당일 관찰 흔들기의 KRX 신호일 1분봉을 독립 보존한다.

연구 원본 수집 전용이다. 레이더 순위·게시·자동매매 정책을 읽거나 변경하지 않는다.
"""

import argparse
import copy
import glob
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kiwoom_client as broker
import radar_json_store as store

try:
    import fcntl
except ImportError:  # pragma: no cover - production is macOS
    fcntl = None


KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DIR = os.path.join(REPO, "data", "radar_history")
ROOT = os.path.join(REPO, "data", "local", "radar_raw")
BENCHMARK_CODE = "005930"
LOCK_PATH = "/tmp/kiwoom_shakeout_signal_minute.lock"
MAX_CANDIDATES = 30
SESSION_OPEN = "090000"
SESSION_CLOSE = "153000"
FULL_FIRST_MAX = "090500"
FULL_LAST_MIN = "152000"

TERMINAL_COVERAGE = {
    "verified_full",
    "verified_with_session_gaps",
    "no_trade_confirmed",
}
MANUAL_HOLD = {"conflict", "integrity_error"}
RETRYABLE_COVERAGE = {
    "partial_daily_mismatch",
    "minute_missing",
    "stock_daily_missing",
    "trade_date_mismatch",
    "store_error",
    "api_error",
    "deferred_due_to_cap",
}


def log(message):
    print(f"[{datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}] {message}", file=sys.stderr)


def _sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def _json_hash(payload):
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _read_json_snapshot(path, *, require_integrity=False):
    raw = Path(path).read_bytes()
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    if require_integrity and not store.verify_payload_integrity(document):
        raise ValueError(f"payload checksum mismatch: {path}")
    return document, _sha256_bytes(raw)


def _parse_kst(value):
    if not value:
        return None
    raw = str(value).strip().replace(" KST", "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _evidence_datetime(value):
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    formats = {14: "%Y%m%d%H%M%S", 12: "%Y%m%d%H%M"}
    fmt = formats.get(len(digits))
    if not fmt:
        return None
    try:
        return datetime.strptime(digits, fmt).replace(tzinfo=KST)
    except ValueError:
        return None


def _material_snapshot(record, cutoff, source_path, source_hash, history_as_of, captured_at):
    material = copy.deepcopy(record.get("material"))
    base = {
        "material_snapshot": material,
        "material_captured_at": captured_at.strftime("%Y-%m-%d %H:%M:%S KST"),
        "material_source_path": source_path,
        "material_source_history_as_of": history_as_of,
        "material_source_file_sha256": source_hash,
        "material_decision_cutoff": cutoff.strftime("%Y-%m-%d %H:%M:%S KST"),
        "evidence_max_datetime": None,
        "evidence_parse_status": "missing",
        "evidence_all_pre_cutoff": False,
        "material_time_class": "missing",
    }
    if not isinstance(material, dict) or not material:
        return base
    evidence = material.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return {
            **base,
            "evidence_parse_status": "missing_or_empty",
            "material_time_class": "unverifiable_capture",
        }
    parsed = [_evidence_datetime(item.get("datetime")) if isinstance(item, dict) else None
              for item in evidence]
    known = [value for value in parsed if value is not None]
    maximum = max(known) if known else None
    if maximum:
        base["evidence_max_datetime"] = maximum.strftime("%Y-%m-%d %H:%M:%S KST")
    if any(value is not None and value > cutoff for value in parsed):
        return {
            **base,
            "evidence_parse_status": "complete" if len(known) == len(parsed) else "partial",
            "material_time_class": "post_close_material",
        }
    if len(known) == len(parsed):
        return {
            **base,
            "evidence_parse_status": "complete",
            "evidence_all_pre_cutoff": True,
            "material_time_class": "evidence_time_proxy_pre_cutoff",
        }
    return {
        **base,
        "evidence_parse_status": "partial" if known else "unparseable",
        "material_time_class": "unverifiable_capture",
    }


def _shakeout_group(record, *, history_present):
    if not history_present or record.get("strength_tier") is None:
        return "강도 미확인"
    if record.get("very_good") is True:
        return "very_good"
    try:
        return "조합D 단독" if float(record["strength_tier"]) >= 3 else "약한흔들기"
    except (TypeError, ValueError):
        return "강도 미확인"


def _candidate_from_history(
    code,
    record,
    *,
    blocked,
    source_path,
    source_hash,
    history_as_of,
    captured_at,
    cutoff,
):
    history_present = True
    material = _material_snapshot(
        record, cutoff, source_path, source_hash, history_as_of, captured_at
    )
    technical_keys = (
        "very_good",
        "very_good_tier",
        "very_good_candidate",
        "strength_tier",
        "strength",
        "dd6_pct",
        "fade_pct",
        "turnover_2d_pct",
        "peak_dd_pct",
        "turnover_band",
        "dd_band",
    )
    row = {
        "code": str(code),
        "name": record.get("name") or str(code),
        "sources": ["blocked_history" if blocked else "history"],
        "history_present": history_present,
        "scan_only": False,
        "blocked": bool(blocked),
        "blocked_reason": record.get("blocked_reason"),
        "final_as_of_capture": record.get("final"),
        "published_as_of_capture": record.get("published"),
        "cohort_as_of": history_as_of,
        "first_observed_at": record.get("first_seen") or record.get("first_blocked"),
        "last_observed_at": record.get("last_seen") or record.get("last_blocked"),
        "scan_run_ids": [],
        "gate_evidence": [],
        "legacy_pattern_fallback": False,
    }
    for key in technical_keys:
        row[key] = record.get(key)
    row["shakeout_group"] = _shakeout_group(
        record, history_present=history_present and not blocked
    )
    row.update(material)
    return row


def _merge_candidate(candidates, incoming):
    code = incoming["code"]
    existing = candidates.get(code)
    if existing is None:
        candidates[code] = incoming
        return
    existing["sources"] = sorted(set(existing.get("sources") or [])
                                 | set(incoming.get("sources") or []))
    existing["scan_run_ids"] = sorted(set(existing.get("scan_run_ids") or [])
                                      | set(incoming.get("scan_run_ids") or []))
    existing["gate_evidence"] = (existing.get("gate_evidence") or []) + (
        incoming.get("gate_evidence") or []
    )
    existing["legacy_pattern_fallback"] = bool(
        existing.get("legacy_pattern_fallback") or incoming.get("legacy_pattern_fallback")
    )
    for key in ("first_observed_at", "last_observed_at"):
        values = [value for value in (existing.get(key), incoming.get(key)) if value]
        if values:
            existing[key] = min(values) if key.startswith("first") else max(values)
    if existing.get("name") in (None, "", code) and incoming.get("name"):
        existing["name"] = incoming["name"]


def _scan_gate_pass(observation):
    decisions = observation.get("gate_decisions")
    if isinstance(decisions, list):
        passed = [
            decision
            for decision in decisions
            if isinstance(decision, dict)
            and decision.get("track") == "shakeout"
            and decision.get("gate") == "final"
            and decision.get("status") == "PASS"
        ]
        return bool(passed), passed, False
    legacy = observation.get("pattern") == "shakeout"
    return legacy, [], legacy


def build_candidates(signal_date, captured_at, *, history_dir=HISTORY_DIR, root=ROOT):
    history_path = os.path.join(history_dir, signal_date + ".json")
    history = {"date": signal_date, "suspects": {}, "blocked_suspects": {}}
    history_hash = None
    history_mtime = None
    if os.path.exists(history_path):
        history, history_hash = _read_json_snapshot(history_path)
        if str(history.get("date") or "") != signal_date:
            raise ValueError(
                f"history date mismatch expected={signal_date} "
                f"actual={history.get('date')}"
            )
        history_mtime = datetime.fromtimestamp(
            os.path.getmtime(history_path), tz=KST
        ).strftime("%Y-%m-%d %H:%M:%S KST")
    history_as_of = history.get("as_of")
    cutoff = datetime.strptime(signal_date + "153000", "%Y%m%d%H%M%S").replace(tzinfo=KST)
    source_path = os.path.relpath(history_path, REPO)
    candidates = {}
    for code, record in (history.get("suspects") or {}).items():
        if isinstance(record, dict) and record.get("shakeout") is True:
            _merge_candidate(
                candidates,
                _candidate_from_history(
                    code,
                    record,
                    blocked=False,
                    source_path=source_path,
                    source_hash=history_hash,
                    history_as_of=history_as_of,
                    captured_at=captured_at,
                    cutoff=cutoff,
                ),
            )
    for code, record in (history.get("blocked_suspects") or {}).items():
        if isinstance(record, dict) and record.get("pattern") == "shakeout":
            _merge_candidate(
                candidates,
                _candidate_from_history(
                    code,
                    record,
                    blocked=True,
                    source_path=source_path,
                    source_hash=history_hash,
                    history_as_of=history_as_of,
                    captured_at=captured_at,
                    cutoff=cutoff,
                ),
            )

    scan_dir = store.local_day_dir(signal_date, root) / "scans"
    for path in sorted(glob.glob(os.path.join(str(scan_dir), "*.json"))):
        try:
            payload, _ = _read_json_snapshot(path, require_integrity=True)
            if payload.get("record_type") != "scan_run":
                continue
            run = payload.get("run") or {}
            if (
                run.get("scan_ok") is not True
                or str(run.get("trade_date") or "") != signal_date
                or run.get("dry_run") is True
            ):
                continue
            generated = _parse_kst(run.get("generated_at") or payload.get("created_at"))
            if generated is None or generated > captured_at:
                continue
            run_id = str(run.get("run_id") or "")
            for observation in payload.get("observations") or []:
                if not isinstance(observation, dict) or not observation.get("code"):
                    continue
                passed, evidence, legacy = _scan_gate_pass(observation)
                if not passed:
                    continue
                code = str(observation["code"])
                incoming = {
                    "code": code,
                    "name": observation.get("name") or code,
                    "sources": ["scan_gate"],
                    "history_present": False,
                    "scan_only": True,
                    "blocked": False,
                    "blocked_reason": None,
                    "final_as_of_capture": None,
                    "published_as_of_capture": None,
                    "cohort_as_of": captured_at.strftime("%Y-%m-%d %H:%M:%S KST"),
                    "first_observed_at": observation.get("observed_first_at")
                    or observation.get("observed_at"),
                    "last_observed_at": observation.get("observed_last_at")
                    or observation.get("observed_at"),
                    "scan_run_ids": [run_id] if run_id else [],
                    "gate_evidence": copy.deepcopy(evidence),
                    "legacy_pattern_fallback": legacy,
                    "shakeout_group": "강도 미확인",
                    "very_good": None,
                    "very_good_tier": None,
                    "very_good_candidate": None,
                    "strength_tier": None,
                    "strength": None,
                    "dd6_pct": None,
                    "fade_pct": None,
                    "turnover_2d_pct": None,
                    "peak_dd_pct": None,
                    "turnover_band": None,
                    "dd_band": None,
                    "scan_technical": copy.deepcopy(observation.get("technical") or {}),
                    "material_snapshot": None,
                    "material_captured_at": captured_at.strftime("%Y-%m-%d %H:%M:%S KST"),
                    "material_source_path": None,
                    "material_source_history_as_of": None,
                    "material_source_file_sha256": None,
                    "material_decision_cutoff": cutoff.strftime("%Y-%m-%d %H:%M:%S KST"),
                    "evidence_max_datetime": None,
                    "evidence_parse_status": "missing",
                    "evidence_all_pre_cutoff": False,
                    "material_time_class": "missing",
                }
                _merge_candidate(candidates, incoming)
                if code in candidates and candidates[code].get("history_present"):
                    candidates[code]["scan_only"] = False
        except Exception as exc:
            log(f"scan 제외 {path}: {exc}")

    metadata = {
        "history_path": source_path,
        "history_as_of": history_as_of,
        "history_read_at": captured_at.strftime("%Y-%m-%d %H:%M:%S KST"),
        "history_file_mtime": history_mtime,
        "history_source_file_sha256": history_hash,
    }
    for row in candidates.values():
        if row.get("scan_only"):
            row["sources"] = sorted(set(row.get("sources") or []) | {"scan_only"})
    return candidates, metadata


def _number(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric OHLCV value")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite OHLCV value")
    return number


def _canonical_bars(bars, *, signal_date=None):
    indexed = {}
    for raw in bars or []:
        if not isinstance(raw, dict):
            raise TypeError("minute bar must be an object")
        text = re.sub(r"[^0-9]", "", str(raw.get("time") or raw.get("datetime") or ""))
        if len(text) >= 14:
            if signal_date and text[:8] != signal_date:
                raise ValueError(f"bar date mismatch expected={signal_date} actual={text[:8]}")
            text = text[-6:]
        elif len(text) == 4:
            text += "00"
        elif len(text) >= 6:
            text = text[:6]
        else:
            raise ValueError("minute bar requires HHMMSS time")
        if not (SESSION_OPEN <= text <= SESSION_CLOSE):
            raise ValueError(f"minute time outside KRX session: {text}")
        volume = raw.get("vol")
        if volume is None:
            volume = raw.get("volume")
        bar = {
            "time": text,
            "open": _number(raw.get("open")),
            "high": _number(raw.get("high")),
            "low": _number(raw.get("low")),
            "close": _number(raw.get("close")),
            "vol": _number(volume),
        }
        if min(bar[key] for key in ("open", "high", "low", "close")) <= 0:
            raise ValueError(f"non-positive price at {text}")
        if bar["vol"] < 0:
            raise ValueError(f"negative volume at {text}")
        if not (
            bar["low"] <= bar["open"] <= bar["high"]
            and bar["low"] <= bar["close"] <= bar["high"]
        ):
            raise ValueError(f"invalid OHLC at {text}")
        previous = indexed.get(text)
        if previous is not None and previous != bar:
            raise ValueError(f"conflicting duplicate minute at {text}")
        indexed[text] = bar
    return [indexed[key] for key in sorted(indexed)]


def _daily_row(daily, signal_date):
    return next(
        (row for row in daily or [] if str(row.get("date") or "") == signal_date),
        None,
    )


def assess_coverage(daily, bars):
    if not daily:
        return {
            "coverage_status": "stock_daily_missing",
            "price_ohlc_match": False,
            "coverage_warnings": ["signal_date daily bar missing"],
            "bar_count": len(bars or []),
        }
    base = {
        "actual_trade_date": str(daily.get("date") or "") or None,
        "daily_open": _number(daily.get("open")),
        "daily_high": _number(daily.get("high")),
        "daily_low": _number(daily.get("low")),
        "daily_close": _number(daily.get("close")),
        "daily_volume": _number(daily.get("volume")),
        "bar_count": len(bars or []),
        "requested_until": SESSION_CLOSE,
        "coverage_warnings": [],
    }
    if not bars:
        status = "no_trade_confirmed" if base["daily_volume"] == 0 else "minute_missing"
        return {**base, "coverage_status": status, "price_ohlc_match": False}
    first, last = bars[0], bars[-1]
    minute_volume = sum(bar["vol"] for bar in bars)
    ratio = minute_volume / base["daily_volume"] if base["daily_volume"] > 0 else None
    match = (
        first["open"] == base["daily_open"]
        and max(bar["high"] for bar in bars) == base["daily_high"]
        and min(bar["low"] for bar in bars) == base["daily_low"]
        and last["close"] == base["daily_close"]
    )
    warnings = []
    if ratio is not None and not 0.98 <= ratio <= 1.02:
        warnings.append(f"minute/daily volume ratio={ratio:.4f}")
    if match and first["time"] <= FULL_FIRST_MAX and last["time"] >= FULL_LAST_MIN:
        status = "verified_full"
    elif match:
        status = "verified_with_session_gaps"
        warnings.append("price OHLC matches but session edge coverage is incomplete")
    else:
        status = "partial_daily_mismatch"
        warnings.append("minute price OHLC does not reproduce daily OHLC")
    return {
        **base,
        "minute_first_open": first["open"],
        "minute_high": max(bar["high"] for bar in bars),
        "minute_low": min(bar["low"] for bar in bars),
        "minute_last_close": last["close"],
        "minute_volume_sum": minute_volume,
        "minute_daily_volume_ratio": round(ratio, 6) if ratio is not None else None,
        "first_bar_time": first["time"],
        "last_bar_time": last["time"],
        "price_ohlc_match": match,
        "coverage_status": status,
        "coverage_warnings": warnings,
    }


def _minute_path(signal_date, code, root):
    return store.local_day_dir(signal_date, root) / "minute" / f"{code}_J.json"


def _load_existing_minute(path, signal_date, code):
    if not path.exists():
        return None, None
    document, digest = _read_json_snapshot(path, require_integrity=True)
    if (
        str(document.get("trade_date") or "") != signal_date
        or str(document.get("code") or "") != code
        or str(document.get("market_basis") or "").upper() != "J"
    ):
        raise ValueError("existing J minute metadata mismatch")
    bars = _canonical_bars(document.get("bars") or [], signal_date=signal_date)
    return (document, bars), digest


def _bar_conflicts(existing_bars, incoming_bars):
    existing = {bar["time"]: bar for bar in existing_bars or []}
    conflicts = []
    for incoming in incoming_bars or []:
        previous = existing.get(incoming["time"])
        if previous is None:
            continue
        fields = [
            key
            for key in ("open", "high", "low", "close", "vol")
            if previous.get(key) != incoming.get(key)
        ]
        if fields:
            conflicts.append({"time": incoming["time"], "fields": fields})
    return conflicts


def _quarantine_conflict(
    signal_date,
    code,
    run_id,
    bars,
    error,
    *,
    root,
    existing_file_sha256=None,
    existing_payload_sha256=None,
    existing_bar_count=None,
    conflicts=None,
    fetched_at=None,
):
    target = (
        store.local_day_dir(signal_date, root)
        / "research"
        / "minute_conflicts"
        / f"{code}_J_{run_id}.json"
    )
    payload = {
        "schema_version": 1,
        "record_type": "minute_bar_conflict",
        "signal_date": signal_date,
        "code": code,
        "market_basis": "J",
        "created_at": (fetched_at or datetime.now(KST)).strftime(
            "%Y-%m-%d %H:%M:%S KST"
        ),
        "existing_file_sha256": existing_file_sha256,
        "existing_payload_sha256": existing_payload_sha256,
        "existing_bar_count": existing_bar_count,
        "incoming_bar_count": len(bars),
        "incoming_payload_sha256": _json_hash(bars),
        "conflicts": conflicts or [],
        "error": str(error),
        "bars": bars,
    }
    return store.atomic_write_json(target, payload, overwrite=False)


class ApiCounter:
    def __init__(self, daily_fetch, minute_fetch):
        self.daily_fetch = daily_fetch
        self.minute_fetch = minute_fetch
        self.logical_calls = 0

    def daily(self, *args, **kwargs):
        self.logical_calls += 1
        return self.daily_fetch(*args, **kwargs)

    def minute(self, *args, **kwargs):
        self.logical_calls += 1
        return self.minute_fetch(*args, **kwargs)


def _is_terminal(row):
    return row.get("coverage_status") in TERMINAL_COVERAGE


def _is_manual_hold(row):
    return row.get("coverage_status") in MANUAL_HOLD or row.get("capture_status") in MANUAL_HOLD


def _cohort_fingerprint(row):
    keys = (
        "sources",
        "final_as_of_capture",
        "published_as_of_capture",
        "blocked",
        "blocked_reason",
        "cohort_as_of",
        "shakeout_group",
        "very_good",
        "very_good_candidate",
        "strength_tier",
        "dd6_pct",
        "material_source_file_sha256",
        "material_time_class",
    )
    return _json_hash({key: row.get(key) for key in keys})


def _merge_index_candidate(existing, current):
    if existing is None:
        row = copy.deepcopy(current)
        row["cohort_observations"] = [{
            "captured_at": current.get("material_captured_at"),
            "fingerprint": _cohort_fingerprint(current),
        }]
        row.setdefault("attempts", [])
        return row
    merged = copy.deepcopy(existing)
    observations = list(merged.get("cohort_observations") or [])
    fingerprint = _cohort_fingerprint(current)
    if not any(item.get("fingerprint") == fingerprint for item in observations):
        observations.append({
            "captured_at": current.get("material_captured_at"),
            "fingerprint": fingerprint,
            "snapshot": copy.deepcopy(current),
        })
    merged["cohort_observations"] = observations
    merged["sources"] = sorted(set(merged.get("sources") or [])
                               | set(current.get("sources") or []))
    merged["scan_run_ids"] = sorted(set(merged.get("scan_run_ids") or [])
                                    | set(current.get("scan_run_ids") or []))
    evidence = (merged.get("gate_evidence") or []) + (
        current.get("gate_evidence") or []
    )
    deduplicated = {}
    for item in evidence:
        if isinstance(item, dict):
            deduplicated[_json_hash(item)] = copy.deepcopy(item)
    merged["gate_evidence"] = list(deduplicated.values())
    merged["legacy_pattern_fallback"] = bool(
        merged.get("legacy_pattern_fallback")
        or current.get("legacy_pattern_fallback")
    )
    for key in ("first_observed_at", "last_observed_at"):
        values = [value for value in (merged.get(key), current.get(key)) if value]
        if values:
            merged[key] = min(values) if key.startswith("first") else max(values)
    return merged


def _index_path(signal_date, root):
    return store.local_day_dir(signal_date, root) / "research" / "shakeout_signal_minutes.json"


def _load_index(signal_date, root):
    path = _index_path(signal_date, root)
    if not path.exists():
        return None
    document, _ = _read_json_snapshot(path, require_integrity=True)
    if document.get("record_type") != "shakeout_signal_minute_capture":
        raise ValueError("existing capture index record_type mismatch")
    return document


def _apply_attempt(row, attempt, coverage=None):
    row.setdefault("attempts", []).append(attempt)
    if coverage:
        row.update(coverage)
    row["capture_status"] = attempt["capture_status"]
    if attempt.get("minute_source"):
        row["minute_source"] = attempt["minute_source"]
    if attempt.get("minute_path"):
        row["minute_path"] = attempt["minute_path"]
    if attempt.get("minute_file_sha256"):
        row["minute_file_sha256"] = attempt["minute_file_sha256"]


def _apply_retry_failure(row, attempt, coverage_status, extra=None):
    """Record a failed refresh without discarding a prior partial raw coverage."""

    prior_partial = (
        row.get("coverage_status") == "partial_daily_mismatch"
        and row.get("minute_path")
    )
    if prior_partial:
        row["last_retry_failure_status"] = coverage_status
        if extra:
            row.update(extra)
        _apply_attempt(row, attempt)
        return
    coverage = {"coverage_status": coverage_status}
    if extra:
        coverage.update(extra)
    _apply_attempt(row, attempt, coverage)


def _capture_one(signal_date, row, api, run_id, captured_at, *, root):
    code = row["code"]
    attempted_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    attempt = {
        "run_id": run_id,
        "attempted_at": attempted_at,
        "capture_status": "api_error",
        "minute_source": None,
        "minute_path": None,
        "minute_file_sha256": None,
        "error": None,
    }
    try:
        daily = _daily_row(api.daily(code, days=15, market="J"), signal_date)
    except Exception as exc:
        attempt["error"] = str(exc)[:500]
        _apply_retry_failure(row, attempt, "api_error")
        return
    if daily is None:
        attempt["capture_status"] = "stock_daily_missing"
        _apply_retry_failure(row, attempt, "stock_daily_missing")
        return

    minute_path = _minute_path(signal_date, code, root)
    try:
        existing, digest = _load_existing_minute(minute_path, signal_date, code)
    except Exception as exc:
        attempt["capture_status"] = "integrity_error"
        attempt["error"] = str(exc)[:500]
        _apply_attempt(row, attempt, {"coverage_status": "integrity_error"})
        return
    existing_document = None
    existing_bars = []
    if existing is not None:
        existing_document, existing_bars = existing
        try:
            existing_coverage = assess_coverage(daily, existing_bars)
        except Exception as exc:
            attempt["capture_status"] = "api_error"
            attempt["error"] = f"invalid official daily row: {exc}"[:500]
            _apply_retry_failure(row, attempt, "api_error")
            return
        if existing_coverage["coverage_status"] in {
            "verified_full",
            "verified_with_session_gaps",
        }:
            attempt.update({
                "capture_status": "reused",
                "minute_source": "reused_existing_J",
                "minute_path": os.path.relpath(minute_path, store.local_day_dir(signal_date, root)),
                "minute_file_sha256": digest,
            })
            events = existing_document.get("fetch_events") or []
            row["minute_fetched_first_at"] = existing_document.get("fetched_first_at")
            row["minute_fetched_last_at"] = existing_document.get("fetched_last_at")
            row["minute_fetch_event_first"] = copy.deepcopy(events[0]) if events else None
            row["minute_fetch_event_last"] = copy.deepcopy(events[-1]) if events else None
            _apply_attempt(row, attempt, existing_coverage)
            return

    try:
        response = api.minute(code, until=SESSION_CLOSE, market="J")
    except Exception as exc:
        attempt["error"] = str(exc)[:500]
        _apply_retry_failure(row, attempt, "api_error")
        return
    actual_date = str(response.get("trade_date") or "")
    attempt["actual_trade_date"] = actual_date or None
    if actual_date != signal_date:
        attempt["capture_status"] = "trade_date_mismatch"
        attempt["error"] = f"expected={signal_date} actual={actual_date or 'none'}"
        _apply_retry_failure(
            row,
            attempt,
            "trade_date_mismatch",
            {
                "actual_trade_date": actual_date or None,
            },
        )
        return
    try:
        bars = _canonical_bars(response.get("bars") or [], signal_date=signal_date)
    except Exception as exc:
        attempt["capture_status"] = "api_error"
        attempt["error"] = str(exc)[:500]
        _apply_retry_failure(row, attempt, "api_error")
        return
    if not bars:
        try:
            coverage = assess_coverage(daily, bars)
        except Exception as exc:
            attempt["capture_status"] = "api_error"
            attempt["error"] = f"invalid official daily row: {exc}"[:500]
            _apply_retry_failure(row, attempt, "api_error")
            return
        attempt["capture_status"] = coverage["coverage_status"]
        if existing_bars:
            _apply_retry_failure(
                row,
                attempt,
                coverage["coverage_status"],
                {"last_empty_fetch_coverage": coverage},
            )
        else:
            _apply_attempt(row, attempt, coverage)
        return

    conflicts = _bar_conflicts(existing_bars, bars)
    if conflicts:
        error = "minute bar conflict at " + ",".join(item["time"] for item in conflicts)
        quarantine = _quarantine_conflict(
            signal_date,
            code,
            run_id,
            bars,
            error,
            root=root,
            existing_file_sha256=digest,
            existing_payload_sha256=(existing_document.get("integrity") or {}).get(
                "payload_sha256"
            ),
            existing_bar_count=len(existing_bars),
            conflicts=conflicts,
            fetched_at=captured_at,
        )
        attempt["capture_status"] = "conflict"
        attempt["error"] = error
        attempt["conflict_path"] = (
            os.path.relpath(quarantine.path, store.local_day_dir(signal_date, root))
            if quarantine.path
            else None
        )
        attempt["conflict_quarantine_ok"] = quarantine.ok
        attempt["conflict_quarantine_error"] = quarantine.error
        attempt["existing_file_sha256"] = digest
        attempt["incoming_payload_sha256"] = _json_hash(bars)
        attempt["conflicts"] = conflicts
        _apply_attempt(row, attempt, {"coverage_status": "conflict"})
        return

    saved = store.merge_minute_bars(
        signal_date,
        code,
        bars,
        market_basis="J",
        source_broker="kiwoom",
        fetched_at=captured_at,
        fetch_status="ok",
        root=root,
        update_manifest=False,
        conflict_policy="error",
    )
    if not saved.ok:
        if "minute bar conflict" in str(saved.error):
            conflict_document = existing_document
            conflict_bars = existing_bars
            conflict_digest = digest
            try:
                reloaded, conflict_digest = _load_existing_minute(
                    minute_path, signal_date, code
                )
                if reloaded is not None:
                    conflict_document, conflict_bars = reloaded
            except Exception as reload_exc:
                attempt["conflict_reload_error"] = str(reload_exc)[:500]
            quarantine = _quarantine_conflict(
                signal_date,
                code,
                run_id,
                bars,
                saved.error,
                root=root,
                existing_file_sha256=conflict_digest,
                existing_payload_sha256=(
                    (conflict_document or {}).get("integrity") or {}
                ).get("payload_sha256"),
                existing_bar_count=len(conflict_bars),
                conflicts=_bar_conflicts(conflict_bars, bars),
                fetched_at=captured_at,
            )
            attempt["capture_status"] = "conflict"
            attempt["error"] = str(saved.error)[:500]
            attempt["conflict_path"] = (
                os.path.relpath(quarantine.path, store.local_day_dir(signal_date, root))
                if quarantine.path
                else None
            )
            attempt["conflict_quarantine_ok"] = quarantine.ok
            attempt["conflict_quarantine_error"] = quarantine.error
            attempt["existing_file_sha256"] = conflict_digest
            attempt["incoming_payload_sha256"] = _json_hash(bars)
            attempt["conflicts"] = _bar_conflicts(conflict_bars, bars)
            _apply_attempt(row, attempt, {"coverage_status": "conflict"})
        else:
            attempt["capture_status"] = "store_error"
            attempt["error"] = str(saved.error)[:500]
            _apply_retry_failure(row, attempt, "store_error")
        return
    try:
        stored, digest = _load_existing_minute(Path(saved.path), signal_date, code)
        stored_document, stored_bars = stored
        coverage = assess_coverage(daily, stored_bars)
    except Exception as exc:
        attempt["capture_status"] = "integrity_error"
        attempt["error"] = str(exc)[:500]
        _apply_attempt(row, attempt, {"coverage_status": "integrity_error"})
        return
    attempt.update({
        "capture_status": "stored",
        "minute_source": "fetched_J",
        "minute_path": os.path.relpath(saved.path, store.local_day_dir(signal_date, root)),
        "minute_file_sha256": digest,
    })
    events = stored_document.get("fetch_events") or []
    row["minute_fetched_first_at"] = stored_document.get("fetched_first_at")
    row["minute_fetched_last_at"] = stored_document.get("fetched_last_at")
    row["minute_fetch_event_first"] = copy.deepcopy(events[0]) if events else None
    row["minute_fetch_event_last"] = copy.deepcopy(events[-1]) if events else None
    _apply_attempt(row, attempt, coverage)


def run(
    requested_date=None,
    *,
    now=None,
    history_dir=HISTORY_DIR,
    root=ROOT,
    daily_fetch=None,
    minute_fetch=None,
    max_candidates=MAX_CANDIDATES,
    lock_result="not_enforced_library_call",
):
    now = (now or datetime.now(KST)).astimezone(KST)
    if max_candidates <= 0:
        raise ValueError("max_candidates must be positive")
    daily_fetch = daily_fetch or broker.daily_prices
    minute_fetch = minute_fetch or broker.minute_bars_today_with_meta
    api = ApiCounter(daily_fetch, minute_fetch)
    calendar = api.daily(BENCHMARK_CODE, days=15, market="J")
    dates = sorted(str(row.get("date") or "") for row in calendar if row.get("date"))
    if not dates:
        raise RuntimeError("KRX benchmark calendar is empty")
    latest_date = dates[-1]
    signal_date = store.normalize_trade_date(requested_date or latest_date)
    historical_only = signal_date != latest_date
    if signal_date > latest_date:
        raise ValueError(f"requested future trade date: {signal_date} > {latest_date}")
    if (
        not historical_only
        and signal_date == now.strftime("%Y%m%d")
        and now.strftime("%H%M%S") < "154000"
    ):
        raise ValueError("current KRX session is not finalized; run at or after 15:40 KST")

    run_id = uuid.uuid4().hex
    started = time.monotonic()
    existing_index = _load_index(signal_date, root)
    if historical_only and existing_index is None:
        raise ValueError(
            f"requested date {signal_date} is not latest KRX date {latest_date}; "
            "historical backfill is forbidden"
        )
    if historical_only:
        candidates = {}
        history_meta = {
            "history_path": None,
            "history_as_of": existing_index.get("history_as_of"),
            "history_read_at": existing_index.get("history_read_at"),
            "history_file_mtime": None,
            "history_source_file_sha256": existing_index.get(
                "history_source_file_sha256"
            ),
        }
    else:
        candidates, history_meta = build_candidates(
            signal_date, now, history_dir=history_dir, root=root
        )
    existing_by_code = {
        str(row.get("code")): row
        for row in (existing_index or {}).get("results") or []
        if row.get("code")
    }
    rows = {}
    for code in sorted(set(existing_by_code) | set(candidates)):
        rows[code] = _merge_index_candidate(existing_by_code.get(code), candidates.get(code) or {
            **existing_by_code[code],
            "material_captured_at": now.strftime("%Y-%m-%d %H:%M:%S KST"),
        })

    processed = []
    deferred = []
    if historical_only:
        for row in rows.values():
            if row.get("coverage_status") == "partial_daily_mismatch":
                row["coverage_status"] = "historical_partial"
                row.setdefault("attempts", []).append({
                    "run_id": run_id,
                    "attempted_at": now.strftime("%Y-%m-%d %H:%M:%S KST"),
                    "capture_status": "historical_partial",
                    "error": f"latest API trade_date is {latest_date}; no historical merge attempted",
                })
    else:
        retry_rows = [
            row for row in rows.values()
            if not _is_terminal(row) and not _is_manual_hold(row)
        ]
        retry_rows.sort(
            key=lambda row: (
                row.get("coverage_status") != "deferred_due_to_cap",
                row["code"],
            )
        )
        allowed = retry_rows[:max_candidates]
        deferred = retry_rows[max_candidates:]
        for row in deferred:
            attempt = {
                "run_id": run_id,
                "attempted_at": now.strftime("%Y-%m-%d %H:%M:%S KST"),
                "capture_status": "deferred_due_to_cap",
                "error": f"candidate cap {max_candidates} exceeded",
            }
            _apply_attempt(row, attempt, {"coverage_status": "deferred_due_to_cap"})
        for row in allowed:
            try:
                _capture_one(signal_date, row, api, run_id, now, root=root)
            except Exception as exc:
                attempt = {
                    "run_id": run_id,
                    "attempted_at": datetime.now(KST).strftime(
                        "%Y-%m-%d %H:%M:%S KST"
                    ),
                    "capture_status": "api_error",
                    "error": f"unexpected candidate failure: {exc}"[:500],
                }
                _apply_retry_failure(row, attempt, "api_error")
                log(
                    f"후보 {row['code']} 수집 실패 격리: "
                    f"{store.redact_secrets(str(exc))}"
                )
            processed.append(row["code"])

    incomplete = [
        row["code"]
        for row in rows.values()
        if not _is_terminal(row)
    ]
    error_summary = []
    for row in rows.values():
        attempts = row.get("attempts") or []
        latest_attempt = attempts[-1] if attempts else {}
        if (
            latest_attempt.get("run_id") == run_id
            and latest_attempt.get("error")
        ):
            error_summary.append({
                "code": row["code"],
                "capture_status": latest_attempt.get("capture_status"),
                "coverage_status": row.get("coverage_status"),
                "error": latest_attempt.get("error"),
            })
    capture_runs = list((existing_index or {}).get("capture_runs") or [])
    capture_runs.append({
        "run_id": run_id,
        "started_at": now.strftime("%Y-%m-%d %H:%M:%S KST"),
        "completed_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "wall_clock_date": now.strftime("%Y%m%d"),
        "requested_date": requested_date,
        "actual_signal_date": signal_date,
        "historical_only": historical_only,
        "lock_result": lock_result,
        "candidate_n": len(rows),
        "processed_n": len(processed),
        "deferred_n": len(deferred),
        "logical_api_call_n": api.logical_calls,
        "api_call_n": api.logical_calls,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "manifest_rebuild_planned": True,
        "manifest_result_at_index_write": "pending",
        "error_n": len(error_summary),
        "error_summary": error_summary,
    })
    history_snapshots = list((existing_index or {}).get("history_snapshots") or [])
    snapshot = {
        **history_meta,
        "captured_at": now.strftime("%Y-%m-%d %H:%M:%S KST"),
    }
    if not any(item.get("history_source_file_sha256")
               == snapshot.get("history_source_file_sha256")
               for item in history_snapshots):
        history_snapshots.append(snapshot)
    index = {
        "schema_version": 1,
        "record_type": "shakeout_signal_minute_capture",
        "signal_date": signal_date,
        "wall_clock_date": now.strftime("%Y%m%d"),
        "entry_basis": "KRX_CLOSE",
        "source_market": "J",
        "material_decision_cutoff": datetime.strptime(
            signal_date + "153000", "%Y%m%d%H%M%S"
        ).replace(tzinfo=KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "capture_complete": not incomplete,
        "candidate_n": len(rows),
        "processed_n": len(processed),
        "deferred_n": len(deferred),
        "deferred_codes": [row["code"] for row in deferred],
        "incomplete_codes": incomplete,
        "logical_api_call_n": api.logical_calls,
        "api_call_n": api.logical_calls,
        "history_as_of": history_meta.get("history_as_of"),
        "history_read_at": history_meta.get("history_read_at"),
        "history_source_file_sha256": history_meta.get("history_source_file_sha256"),
        "history_snapshots": history_snapshots,
        "capture_runs": capture_runs,
        "results": [rows[code] for code in sorted(rows)],
    }
    target = _index_path(signal_date, root)
    written = store.atomic_write_json(target, index, overwrite=True)
    if not written.ok:
        raise RuntimeError("capture index write failed: " + str(written.error))
    manifest = store.rebuild_manifest(signal_date, root)
    if not manifest.ok:
        raise RuntimeError("manifest rebuild failed: " + str(manifest.error))
    return {
        "signal_date": signal_date,
        "index_path": str(target),
        "candidate_n": len(rows),
        "processed_n": len(processed),
        "deferred_n": len(deferred),
        "capture_complete": not incomplete,
        "logical_api_call_n": api.logical_calls,
        "manifest_ok": True,
    }


@contextmanager
def capture_lock(path=LOCK_PATH):
    handle = open(path, "a+b")
    if fcntl is None:  # pragma: no cover
        handle.close()
        raise RuntimeError("fcntl is required for the production capture lock")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        yield None
        return
    try:
        yield handle
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYYMMDD; 최신 거래일과 다르면 과거 원본 병합 금지")
    parser.add_argument("--max-candidates", type=int, default=MAX_CANDIDATES)
    args = parser.parse_args()
    if args.max_candidates <= 0:
        parser.error("--max-candidates must be positive")
    with capture_lock() as locked:
        if locked is None:
            log("이미 신호일 분봉 수집기가 실행 중 — skip")
            return
        result = run(
            args.date,
            max_candidates=args.max_candidates,
            lock_result="acquired",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
