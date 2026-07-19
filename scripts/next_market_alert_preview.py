#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""장마감 직전 suspects의 익일 투자주의 공개 가격조건 미리보기.

읽기 전용 분석기다. 랭킹·자동매매·주문에는 연결하지 않으며, 별도 Upstash KV와
로컬 감사 이력만 갱신한다.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

import disclosure_client as disclosure  # noqa: E402
import kiwoom_client as kw  # noqa: E402
import next_session_eligibility as session_eligibility  # noqa: E402
from next_market_alert_rules import evaluate_alert_preview  # noqa: E402


KST = timezone(timedelta(hours=9))
SCHEMA_VERSION = 1
KV_KEY = "radar:alert-preview:latest"
RADAR_JSON = os.path.join(REPO, "web", "data", "radar.json")
LOCAL_ROOT = os.path.join(REPO, "data", "local", "market_alert_preview")
LOCK_PATH = "/tmp/kiwoom_next_market_alert_preview.lock"
RADAR_MAX_AGE_SECONDS = 10 * 60
SHORT_TTL_SECONDS = 90
PREVIEW_CODE_GAP_SECONDS = 0.2


def _atomic_json(path: str, payload: Dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        with open(tmp, encoding="utf-8") as handle:
            json.load(handle)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _parse_generated_at(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S KST", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=KST) if parsed.tzinfo is None else parsed.astimezone(KST)
        except ValueError:
            continue
    return None


def _iso(moment: datetime) -> str:
    return moment.astimezone(KST).isoformat(timespec="seconds")


def _load_radar(now: datetime) -> Dict[str, Any]:
    with open(RADAR_JSON, encoding="utf-8") as handle:
        radar = json.load(handle)
    generated = _parse_generated_at(radar.get("generated_at"))
    if not generated:
        raise RuntimeError("radar_generated_at_unparseable")
    age = (now - generated).total_seconds()
    if age < -120 or age > RADAR_MAX_AGE_SECONDS:
        raise RuntimeError(f"radar_stale:{int(age)}s")
    today = now.strftime("%Y%m%d")
    closed = session_eligibility.KRX_CLOSED_DATES.get(now.year)
    if (
        generated.strftime("%Y%m%d") != today
        or now.weekday() >= 5
        or closed is None
        or today in closed
    ):
        raise RuntimeError("not_current_krx_trading_day")
    return radar


def _signal_date(suspect: Dict[str, Any], now: datetime) -> str:
    for key in ("signal_date", "snapshot_as_of"):
        value = str(suspect.get(key) or "").replace("-", "")
        if len(value) == 8 and value.isdigit():
            return value
    return now.strftime("%Y%m%d")


def _cache_path(signal_date: str, code: str) -> str:
    return os.path.join(LOCAL_ROOT, "daily_cache", signal_date, f"{code}.json")


def _daily_prices(code: str, signal_date: str, *, refresh: bool) -> Iterable[Dict[str, Any]]:
    path = _cache_path(signal_date, code)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                cached = json.load(handle)
            if (
                cached.get("signal_date") == signal_date
                and cached.get("rows")
                and (not refresh or cached.get("official_close_confirmed") is True)
            ):
                return cached["rows"]
        except (OSError, ValueError, TypeError):
            pass
    rows = kw.daily_prices(code, days=35, market="J")
    _atomic_json(path, {
        "schema_version": 1,
        "signal_date": signal_date,
        "code": code,
        "cached_at": _iso(datetime.now(KST)),
        "official_close_confirmed": False,
        "rows": rows,
    })
    return rows


def _mark_official_close_confirmed(code: str, signal_date: str, now: datetime) -> None:
    path = _cache_path(signal_date, code)
    with open(path, encoding="utf-8") as handle:
        cached = json.load(handle)
    cached["official_close_confirmed"] = True
    cached["official_close_confirmed_at"] = _iso(now)
    _atomic_json(path, cached)


def _official_close(rows: Iterable[Dict[str, Any]], signal_date: str) -> Optional[float]:
    def number(value: Any) -> float:
        try:
            return abs(float(str(value or 0).replace(",", "")))
        except (TypeError, ValueError):
            return 0.0

    matches = [
        row for row in rows
        if str(row.get("date") or "").replace("-", "") == signal_date
        and number(row.get("volume")) > 0
        and number(row.get("close")) > 0
    ]
    return number(matches[-1]["close"]) if matches else None


def _quote_basis(quote: Dict[str, Any], now: datetime) -> tuple[float, str]:
    hm = now.strftime("%H%M")
    current = abs(float(quote.get("price") or 0))
    if "1520" <= hm <= "1530":
        expected = abs(float(quote.get("expected_close_price") or 0))
        expected_qty = float(quote.get("expected_close_qty") or 0)
        if (
            expected > 0
            and expected_qty > 0
            and os.environ.get("RADAR_PREVIEW_AUCTION_VERIFIED") == "1"
        ):
            return expected, "KRX_EXPECTED_CLOSE_VERIFIED"
        return expected or current, "KRX_EXPECTED_CLOSE_UNVERIFIED"
    return current, "KRX_CURRENT"


def _compact(value: Any) -> str:
    return "".join(str(value or "").split())


def _official_notice(code: str, name: str, signal_date: str) -> Optional[Dict[str, Any]]:
    """동일 코드·신호일의 투자주의 지정(예고) 공시를 보수적으로 확인한다."""
    rows = disclosure.fetch_notice_rows(code, max_pages=2)
    wanted = []
    for row in rows:
        title = _compact(row.get("title"))
        if str(row.get("date") or "") < signal_date:
            continue
        if "투자주의" in title and ("지정" in title or "예고" in title):
            wanted.append(row)
    for row in wanted:
        body = disclosure.fetch_notice_body(row)
        text = _compact(body.get("text"))
        normalized_name = _compact(name).replace("(주)", "").replace("㈜", "")
        if code not in text and normalized_name not in text:
            continue
        digest = hashlib.sha256(body["text"].encode("utf-8")).hexdigest()[:20]
        return {
            "notice_id": row.get("notice_id"),
            "title": row.get("title"),
            "published_at": row.get("date"),
            "source_url": row.get("href"),
            "content_url": body.get("content_url"),
            "source_hash": digest,
            "source_kind": "KOSCOM_VIA_NAVER",
        }
    return None


def _record_expiry(record: Dict[str, Any], now: datetime) -> datetime:
    if record.get("status") in ("CONDITION_MET_CLOSE", "OFFICIAL_CONFIRMED"):
        target = str(record.get("target_trade_date") or "")
        if len(target) == 8:
            morning = datetime.strptime(target + "090000", "%Y%m%d%H%M%S").replace(tzinfo=KST)
            if morning > now:
                return morning
    return now + timedelta(seconds=SHORT_TTL_SECONDS)


def _history_path(signal_date: str) -> str:
    return os.path.join(
        LOCAL_ROOT, signal_date[:4], signal_date[4:6], signal_date[6:8], "history.json"
    )


def _merge_history(signal_date: str, records: Dict[str, Dict[str, Any]], now: datetime) -> None:
    path = _history_path(signal_date)
    history: Dict[str, Any] = {
        "schema_version": 1, "date": signal_date, "codes": {}, "updated_at": _iso(now)
    }
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                history = json.load(handle)
        except (OSError, ValueError, TypeError):
            pass
    codes = history.setdefault("codes", {})
    for code, record in records.items():
        previous = codes.get(code) or {}
        merged = {
            "name": record.get("name"),
            "first_met": previous.get("first_met"),
            "latest": record,
            "close": previous.get("close"),
            "official": previous.get("official"),
        }
        if record.get("status") == "CONDITION_MET_INTRADAY" and not merged["first_met"]:
            merged["first_met"] = record
        if record.get("status") == "CONDITION_MET_CLOSE":
            merged["close"] = record
        if record.get("status") == "OFFICIAL_CONFIRMED":
            merged["official"] = record
        codes[code] = merged
    history["updated_at"] = _iso(now)
    _atomic_json(path, history)

    snapshot_dir = os.path.join(os.path.dirname(path), "snapshots")
    snapshot = os.path.join(snapshot_dir, now.strftime("%H%M%S") + ".json")
    _atomic_json(snapshot, {
        "schema_version": 1,
        "date": signal_date,
        "generated_at": _iso(now),
        "codes": records,
    })


def _load_env() -> None:
    kw._load_env()


def _kv_credentials() -> tuple[Optional[str], Optional[str]]:
    _load_env()
    return (
        os.environ.get("RADAR_PREVIEW_KV_REST_API_URL"),
        os.environ.get("RADAR_PREVIEW_KV_REST_API_TOKEN"),
    )


def _kv_set(payload: Dict[str, Any], ttl_seconds: int) -> bool:
    url, token = _kv_credentials()
    if not url or not token:
        sys.stderr.write("[preview] 별도 KV 쓰기 환경변수 미설정 — 로컬 이력만 저장\n")
        return False
    body = json.dumps(
        ["SET", KV_KEY, json.dumps(payload, ensure_ascii=False), "EX", max(30, ttl_seconds)]
    ).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/"),
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.load(response)
    if result.get("error"):
        raise RuntimeError("preview_kv_error:" + str(result["error"]))
    return True


def _publish(payload: Dict[str, Any], ttl_seconds: int) -> None:
    _atomic_json(os.path.join(LOCAL_ROOT, "latest.json"), payload)
    _kv_set(payload, ttl_seconds)


def _tombstone(now: datetime, reason: str) -> Dict[str, Any]:
    expires = now + timedelta(seconds=SHORT_TTL_SECONDS)
    return {
        "schema_version": SCHEMA_VERSION,
        "date": now.strftime("%Y%m%d"),
        "generated_at": _iso(now),
        "expires_at": _iso(expires),
        "verified": False,
        "reason": reason,
        "codes": {},
    }


def run(*, post_close: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    started = time.monotonic()
    now = (now or datetime.now(KST)).astimezone(KST)
    try:
        radar = _load_radar(now)
    except Exception as exc:
        payload = _tombstone(now, str(exc))
        payload["duration_ms"] = round((time.monotonic() - started) * 1_000)
        _publish(payload, SHORT_TTL_SECONDS)
        return payload

    records: Dict[str, Dict[str, Any]] = {}
    signal_dates = set()
    for index, suspect in enumerate(radar.get("suspects") or []):
        # publish/청산감시와 별도 프로세스이므로 preview 자체 버스트를 낮춰 합산 API 부하를 제한한다.
        if index:
            time.sleep(PREVIEW_CODE_GAP_SECONDS)
        code = str(suspect.get("code") or "").lstrip("A").zfill(6)
        name = str(suspect.get("name") or code)
        signal_date = _signal_date(suspect, now)
        signal_dates.add(signal_date)
        target = session_eligibility.resolve_next_trade_date(signal_date)
        refresh_daily = post_close or now.strftime("%H%M") >= "1531"
        try:
            daily = list(_daily_prices(code, signal_date, refresh=refresh_daily))
            if refresh_daily:
                close = _official_close(daily, signal_date)
                if close is None:
                    raise RuntimeError("official_close_unavailable")
                closing_quote = kw.market_alert_quote(code, market="J")
                quote_price = abs(float(closing_quote.get("price") or 0))
                if quote_price <= 0 or quote_price != close:
                    raise RuntimeError("official_close_crosscheck_failed")
                _mark_official_close_confirmed(code, signal_date, now)
                price, basis = close, "KRX_OFFICIAL_CLOSE"
            else:
                price, basis = _quote_basis(kw.market_alert_quote(code, market="J"), now)
            record = evaluate_alert_preview(
                code=code,
                name=name,
                signal_date=signal_date,
                target_trade_date=target,
                daily=daily,
                price=price,
                price_basis=basis,
                current_alert=suspect.get("alert_now"),
            )
            # top-level codes map이 식별자 SSOT이므로 값 안의 중복 코드는 제거한다.
            record.pop("code", None)
        except Exception as exc:
            record = {
                "schema_version": SCHEMA_VERSION,
                "name": name,
                "signal_date": signal_date,
                "target_trade_date": target,
                "status": "UNVERIFIED",
                "verified": False,
                "reason": f"판정 실패: {type(exc).__name__}",
                "price": None,
                "price_basis": None,
                "checks": [],
                "triggered_rule_ids": [],
            }
        if post_close:
            try:
                official = _official_notice(code, name, signal_date)
            except disclosure.DisclosureUnavailable as exc:
                record["official_check_error"] = str(exc)
            else:
                if official:
                    record.update({
                        "status": "OFFICIAL_CONFIRMED",
                        "verified": True,
                        "reason": "KOSCOM 전달 공시에서 익일 투자주의 지정(예고)을 확인했습니다.",
                        "official_evidence": official,
                    })
        record["generated_at"] = _iso(now)
        expiry = _record_expiry(record, now)
        record["expires_at"] = _iso(expiry)
        records[code] = record

    expiries = [
        datetime.fromisoformat(record["expires_at"]) for record in records.values()
    ]
    # 루트 TTL은 가장 오래 유효한 종가/공시 기록을 보존한다. API가 각 코드 expires_at을
    # 다시 검사하므로 짧은 장중 레코드가 오래 노출되지는 않는다.
    top_expiry = max(expiries) if expiries else now + timedelta(seconds=SHORT_TTL_SECONDS)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": now.strftime("%Y%m%d"),
        "generated_at": _iso(now),
        "expires_at": _iso(top_expiry),
        "verified": bool(records) and all(record.get("verified") for record in records.values()),
        "codes": records,
        "duration_ms": round((time.monotonic() - started) * 1_000),
    }
    if len(signal_dates) == 1:
        _merge_history(next(iter(signal_dates)), records, now)
    else:
        for signal_date in signal_dates:
            subset = {
                code: record for code, record in records.items()
                if record.get("signal_date") == signal_date
            }
            _merge_history(signal_date, subset, now)
    ttl_seconds = max(30, int((top_expiry - now).total_seconds()))
    _publish(payload, ttl_seconds)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="장중 1회 계산(기본값)")
    parser.add_argument("--post-close", action="store_true", help="공식 종가·공시 재확인")
    parser.add_argument("--json", action="store_true", help="결과 JSON 출력")
    args = parser.parse_args()
    lock = open(LOCK_PATH, "w", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("이미 익일 투자주의 미리보기 계산 중 — skip")
        return 0
    payload = run(post_close=args.post_close)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        statuses: Dict[str, int] = {}
        for record in payload.get("codes", {}).values():
            status = str(record.get("status"))
            statuses[status] = statuses.get(status, 0) + 1
        print(f"[preview] {payload.get('generated_at')} {statuses}")
        print(
            f"[preview] codes={len(payload.get('codes', {}))} "
            f"duration_ms={payload.get('duration_ms')} verified={payload.get('verified')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
