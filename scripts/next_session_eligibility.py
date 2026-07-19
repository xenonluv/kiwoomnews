# -*- coding: utf-8 -*-
"""다음 KRX 거래일의 추천/신규매수 적격성 판정.

확정 거래정지와 추천 부적격 공시만 하드 차단한다. 조회·본문·거래일을 확실히 읽지 못하면
정상으로 추정하지 않고 UNVERIFIED를 반환한다. 기존 포지션의 청산 판단에는 사용하지 않는다.
"""
import hashlib
import math
import re
from datetime import date, datetime, timedelta, timezone

import disclosure_client as disclosure


KST = timezone(timedelta(hours=9))
SCHEMA_VERSION = 1
CACHE_TTL_SECONDS = 10 * 60
CALENDAR_SOURCE = "KRX holiday rules + 2026 KASA/KASI official almanac"

# KRX는 관공서 공휴일, 근로자의 날, 연말 휴장일 등에 휴장한다. 현재 운영연도만 명시적으로
# 지원하고 다음 연도 일정이 확정되기 전에는 낙관적으로 평일을 거래일로 만들지 않는다.
KRX_CLOSED_DATES = {
    2026: {
        "20260101",                       # 신정
        "20260216", "20260217", "20260218",  # 설 연휴
        "20260302",                       # 삼일절 대체공휴일
        "20260501",                       # 근로자의 날
        "20260505",                       # 어린이날
        "20260525",                       # 부처님오신날 대체공휴일
        "20260603",                       # 제9회 전국동시지방선거
        "20260717",                       # KRX 휴장일
        "20260817",                       # 광복절 대체공휴일
        "20260924", "20260925",              # 추석 연휴(9/26 토요일)
        "20261005",                       # 개천절 대체공휴일
        "20261009",                       # 한글날
        "20261225",                       # 기독탄신일
        "20261231",                       # KRX 연말 휴장일
    },
}

_CACHE = {}
_RELEVANT_TITLE = re.compile(
    r"매매거래정지|주식병합|주식분할|감자|합병|상장폐지|정리매매"
)
_DATE = r"(\d{4})[년.\-/]\s*(\d{1,2})[월.\-/]\s*(\d{1,2})일?"


def _yyyymmdd(value):
    value = str(value or "").strip()
    dated = re.search(r"(\d{4})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})", value)
    if dated:
        raw = f"{int(dated.group(1)):04d}{int(dated.group(2)):02d}{int(dated.group(3)):02d}"
    else:
        digits = re.sub(r"\D", "", value)
        raw = digits[:8] if len(digits) >= 8 else digits
    if len(raw) != 8:
        return None
    try:
        date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None
    return raw


def _date_from_match(match):
    if not match:
        return None
    return f"{int(match.group(1)):04d}{int(match.group(2)):02d}{int(match.group(3)):02d}"


def resolve_next_trade_date(signal_date, closed_dates=None):
    """지원되는 공식 휴장 일정 안에서 실제 다음 KRX 거래일을 반환한다."""
    raw = _yyyymmdd(signal_date)
    if not raw:
        return None
    current = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    for offset in range(1, 15):
        candidate = current + timedelta(days=offset)
        holidays = closed_dates if closed_dates is not None else KRX_CLOSED_DATES.get(candidate.year)
        if holidays is None:
            return None
        key = candidate.strftime("%Y%m%d")
        if candidate.weekday() < 5 and key not in holidays:
            return key
    return None


def _first_date_after(label, text):
    match = re.search(r"(?:" + label + r").{0,80}?" + _DATE, text or "", re.S)
    return _date_from_match(match)


def parse_notice_event(row, text):
    """공시 한 건을 판정 가능한 사건으로 정규화한다."""
    title = str((row or {}).get("title") or "")
    compact_title = re.sub(r"\s+", "", title)
    text = text or ""
    event = {
        "notice_id": str((row or {}).get("notice_id") or ""),
        "notice_date": _yyyymmdd((row or {}).get("date")),
        "title": title,
        "source_url": (row or {}).get("href"),
        "source_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:20],
        "kind": "OTHER",
        "start": None,
        "end": None,
        "relisting_expected": None,
        "reason_code": None,
    }
    if "상장폐지" in compact_title or "정리매매" in compact_title:
        event.update(kind="RECOMMENDATION_BLOCK", reason_code="DELISTING_OR_LIQUIDATION")
        return event
    if "매매거래정지예고" in compact_title or ("매매거래정지" in compact_title and "예고" in compact_title):
        event.update(kind="NOTICE_ONLY", reason_code="HALT_NOTICE")
        return event

    halt_title = "매매거래정지" in compact_title
    schedule_text = "매매거래정지기간" in text
    if not halt_title and not schedule_text:
        return event

    schedule_at = text.find("매매거래정지기간")
    schedule = text[schedule_at:schedule_at + 300] if schedule_at >= 0 else ""
    start = (_first_date_after(r"매매거래정지(?:일|일시)", text)
             or _first_date_after(r"시작일", schedule))
    end = (_first_date_after(r"종료일", schedule)
           or _first_date_after(r"매매거래정지해제일", text))
    relisting = _first_date_after(r"신주권?상장예정일|변경상장예정일", text)
    if end and start and end < start:
        end = None
    reason = "TRADING_HALT"
    if "병합" in text or "병합" in compact_title:
        reason = "SHARE_CONSOLIDATION_HALT"
    elif "분할" in text or "분할" in compact_title:
        reason = "SHARE_SPLIT_HALT"
    elif "감자" in text or "감자" in compact_title:
        reason = "CAPITAL_REDUCTION_HALT"
    elif "합병" in text or "합병" in compact_title:
        reason = "MERGER_HALT"
    event.update(
        kind="HALT_CONFIRMED" if halt_title else "HALT_SCHEDULE",
        start=start,
        end=end,
        relisting_expected=relisting,
        reason_code=reason,
    )
    return event


def _event_order(event):
    notice_id = str(event.get("notice_id") or "")
    return (str(event.get("notice_date") or ""), int(notice_id) if notice_id.isdigit() else -1)


def _base_result(signal_date, target_trade_date, now):
    checked = now.astimezone(KST)
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of_date": _yyyymmdd(signal_date),
        "target_trade_date": target_trade_date,
        "status": "UNVERIFIED",
        "tradable_next_session": None,
        "recommendable": False,
        "auto_buy_allowed": False,
        "reason_code": "UNVERIFIED",
        "reason": "공시 또는 다음 거래일을 확인하지 못했습니다.",
        "restriction_start": None,
        "restriction_end": None,
        "relisting_expected": None,
        "checked_at": checked.isoformat(timespec="seconds"),
        "expires_at": (checked + timedelta(seconds=CACHE_TTL_SECONDS)).isoformat(timespec="seconds"),
        "evidence": None,
        "query_scope": {"source": "KRX_KOSCOM", "pages_checked": 0,
                        "calendar_source": CALENDAR_SOURCE},
    }


def _apply_event(result, event, *, status, tradable, recommendable, reason):
    result.update({
        "status": status,
        "tradable_next_session": tradable,
        "recommendable": recommendable,
        "auto_buy_allowed": bool(tradable is True and recommendable),
        "reason_code": event.get("reason_code") or status,
        "reason": reason,
        "restriction_start": event.get("start"),
        "restriction_end": event.get("end"),
        "relisting_expected": event.get("relisting_expected"),
        "evidence": {
            "notice_id": event.get("notice_id"),
            "title": event.get("title"),
            "published_at": event.get("notice_date"),
            "source_url": event.get("source_url"),
            "source_hash": event.get("source_hash"),
        },
    })
    return result


def evaluate_events(events, signal_date, target_trade_date, *, now=None, observed_trading=True):
    """정규화 공시 사건을 목표 거래일 적격성으로 축약한다."""
    now = now or datetime.now(KST)
    result = _base_result(signal_date, target_trade_date, now)
    if not target_trade_date:
        result["reason_code"] = "TARGET_TRADE_DATE_UNAVAILABLE"
        result["reason"] = "실제 다음 KRX 거래일을 확정하지 못했습니다."
        return result

    relevant = [event for event in events or [] if event.get("kind") != "OTHER"]
    blocked = sorted(
        (event for event in relevant if event.get("kind") == "RECOMMENDATION_BLOCK"),
        key=_event_order, reverse=True)
    if blocked:
        event = blocked[0]
        return _apply_event(
            result, event, status="RECOMMENDATION_BLOCKED", tradable=None,
            recommendable=False, reason="상장폐지·정리매매 관련 확정 공시로 추천에서 제외합니다.")

    unparsed_confirmed = sorted(
        (event for event in relevant
         if event.get("kind") == "HALT_CONFIRMED" and not event.get("start")),
        key=_event_order, reverse=True)
    if unparsed_confirmed:
        event = unparsed_confirmed[0]
        result.update(
            reason_code="CONFIRMED_HALT_DATE_UNPARSED",
            reason="확정 매매거래정지 공시는 확인했지만 정지 시작일을 읽지 못했습니다.",
            evidence={
                "notice_id": event.get("notice_id"), "title": event.get("title"),
                "published_at": event.get("notice_date"),
                "source_url": event.get("source_url"), "source_hash": event.get("source_hash"),
            },
        )
        return result

    # 최신 유효 일정이 우선한다. 확정 정지 공시와 일정 공시를 함께 보면 종료일/상장일이 더
    # 풍부한 일정 공시를 보조 증거로 이용하되, 확정 정지 자체는 제목이 명시된 공시가 결정한다.
    halt_events = sorted(
        (event for event in relevant if event.get("kind") in ("HALT_CONFIRMED", "HALT_SCHEDULE")
         and event.get("start")), key=_event_order, reverse=True)
    confirmed_starts = {event.get("start") for event in halt_events
                        if event.get("kind") == "HALT_CONFIRMED"}
    schedules = [event for event in halt_events if event.get("kind") == "HALT_SCHEDULE"]
    confirmations = [event for event in halt_events if event.get("kind") == "HALT_CONFIRMED"]
    if schedules and confirmations:
        latest_schedule = schedules[0]
        latest_confirmation = confirmations[0]
        if (_event_order(latest_schedule) > _event_order(latest_confirmation)
                and latest_schedule.get("start") != latest_confirmation.get("start")):
            result.update(
                reason_code="HALT_SCHEDULE_CONFLICT",
                reason="최신 정정 일정과 확정 정지 공시의 시작일이 달라 재확인이 필요합니다.",
                evidence={
                    "notice_id": latest_schedule.get("notice_id"),
                    "title": latest_schedule.get("title"),
                    "published_at": latest_schedule.get("notice_date"),
                    "source_url": latest_schedule.get("source_url"),
                    "source_hash": latest_schedule.get("source_hash"),
                },
            )
            return result
    for event in halt_events:
        start, end = event.get("start"), event.get("end")
        if event.get("kind") == "HALT_SCHEDULE" and start not in confirmed_starts:
            continue
        # 신호일 실제 거래가 관찰됐으면 과거의 종료일 없는 정지를 현재까지 연장하지 않는다.
        # 단, 정지 시작일과 신호일이 같으면 데이터가 서로 모순이므로 정상으로 추정하지 않는다.
        if start < str(signal_date) and end is None:
            if observed_trading is True:
                continue
            if observed_trading is None:
                result.update(reason_code="CURRENT_HALT_STATE_UNVERIFIED",
                              reason="과거 정지의 해제 여부와 신호일 실제 거래를 확인하지 못했습니다.")
                return result
        if start <= target_trade_date and (end is None or target_trade_date <= end):
            same_start = [other for other in halt_events if other.get("start") == start]
            enriched = dict(event)
            for other in same_start:
                enriched["end"] = enriched.get("end") or other.get("end")
                enriched["relisting_expected"] = (enriched.get("relisting_expected")
                                                     or other.get("relisting_expected"))
            period = start
            if enriched.get("end"):
                period += f"~{enriched['end']}"
            current_halt = start <= str(signal_date)
            return _apply_event(
                result, enriched,
                status="CURRENTLY_HALTED" if current_halt else "HALT_CONFIRMED",
                tradable=False,
                recommendable=False, reason=f"다음 거래일이 확정 매매정지기간({period})에 포함됩니다.")

    notices = sorted((event for event in relevant if event.get("kind") == "NOTICE_ONLY"),
                     key=_event_order, reverse=True)
    if notices:
        return _apply_event(
            result, notices[0], status="NOTICE_ONLY", tradable=None,
            recommendable=True, reason="매매거래정지 예고 공시가 있으나 확정 정지는 아닙니다.")

    result.update({
        "status": "CLEAR_AS_CHECKED",
        "tradable_next_session": True,
        "recommendable": True,
        "auto_buy_allowed": True,
        "reason_code": "NO_BLOCKING_DISCLOSURE_FOUND",
        "reason": "확인한 공시 범위에서 다음 거래일 차단 근거를 찾지 못했습니다.",
    })
    return result


def evaluate_for_code(code, signal_date, *, now=None, max_pages=2, force_refresh=False,
                      fetch_rows=None, fetch_body=None, closed_dates=None,
                      observed_trading=True):
    """공시 조회부터 최종 적격성까지 수행한다. 예외는 UNVERIFIED로 봉합한다."""
    now = now or datetime.now(KST)
    normalized_code = str(code or "").strip().lstrip("A").zfill(6)
    signal_date = _yyyymmdd(signal_date)
    target = resolve_next_trade_date(signal_date, closed_dates=closed_dates) if signal_date else None
    key = (normalized_code, signal_date, target)
    cached = _CACHE.get(key)
    if not force_refresh and cached and cached[0] > now.timestamp():
        return dict(cached[1])

    result = _base_result(signal_date, target, now)
    if not normalized_code.isdigit() or len(normalized_code) != 6:
        result.update(reason_code="CODE_INVALID", reason="6자리 종목코드를 확인하지 못했습니다.")
        return result
    if not target:
        result.update(reason_code="TARGET_TRADE_DATE_UNAVAILABLE",
                      reason="실제 다음 KRX 거래일을 확정하지 못했습니다.")
        return result
    fetch_rows = fetch_rows or disclosure.fetch_notice_rows
    fetch_body = fetch_body or disclosure.fetch_notice_body
    try:
        rows = fetch_rows(normalized_code, max_pages=max_pages)
        relevant_rows = [row for row in rows if _RELEVANT_TITLE.search(str(row.get("title") or ""))]
        events = []
        for row in relevant_rows:
            body = fetch_body(row)
            events.append(parse_notice_event(row, body["text"]))
        result = evaluate_events(
            events, signal_date, target, now=now, observed_trading=observed_trading)
        result["query_scope"]["pages_checked"] = max_pages
    except Exception as exc:
        result.update(
            status="UNVERIFIED", tradable_next_session=None, recommendable=False,
            auto_buy_allowed=False, reason_code="DISCLOSURE_CHECK_FAILED",
            reason=f"공시 확인 실패: {type(exc).__name__}",
        )
    _CACHE[key] = (now.timestamp() + CACHE_TTL_SECONDS, dict(result))
    return result


def signal_date_for(suspect, radar_generated_at=None):
    for value in (suspect.get("signal_date"), suspect.get("snapshot_as_of"), radar_generated_at):
        parsed = _yyyymmdd(value)
        if parsed:
            return parsed
    return None


def evaluate_for_suspect(suspect, *, radar_generated_at=None, now=None, force_refresh=False):
    signal_date = signal_date_for(suspect or {}, radar_generated_at)
    volume = (suspect or {}).get("signal_volume")
    observed_trading = None
    if volume is not None:
        try:
            observed_trading = math.isfinite(float(volume)) and float(volume) > 0
        except (TypeError, ValueError, OverflowError):
            observed_trading = None
    return evaluate_for_code(
        (suspect or {}).get("code"), signal_date, now=now,
        force_refresh=force_refresh, observed_trading=observed_trading)


def is_fresh(eligibility, now=None):
    if not isinstance(eligibility, dict):
        return False
    try:
        expires = datetime.fromisoformat(str(eligibility.get("expires_at")))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=KST)
        return expires > (now or datetime.now(KST))
    except (TypeError, ValueError):
        return False


def safety_allowed(eligibility):
    if not isinstance(eligibility, dict):
        return False, "next_session_eligibility 없음"
    if eligibility.get("status") == "UNVERIFIED":
        return False, eligibility.get("reason") or "공시 확인 실패"
    if eligibility.get("recommendable") is not True:
        return False, eligibility.get("reason") or "추천 부적격"
    if eligibility.get("tradable_next_session") is False:
        return False, eligibility.get("reason") or "다음 거래일 거래정지"
    if eligibility.get("auto_buy_allowed") is not True:
        return False, eligibility.get("reason") or "신규 자동매수 불가"
    return True, "ok"
