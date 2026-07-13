# -*- coding: utf-8 -*-
"""투자경고 지정해제 예측.

종목별 KRX/KOSCOM 지정공시 본문의 해제요건을 읽고, 거래량 0인 개별 거래정지일을 제외한
실제 매매일로 경과일/T-5/T-15를 계산한다. 장중 현재가를 가상 종가로 넣어 "오늘 조건 충족 시
다음 매매일 해제 예상"을 표시할 뿐이며 KRX 최종 공시가 항상 우선한다.

공시 본문 또는 거래일을 확실히 읽지 못하면 기존 45%/75% 상수로 추정하지 않고 None을 반환한다.
이 모듈은 배지 원천값만 정확하게 만들며 rank4 순위·자동매매 정책은 변경하지 않는다.
"""
import hashlib
import html
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urljoin, urlparse

from net import get_bytes

UA_PC = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
KST = timezone(timedelta(hours=9))
RULE_LOGIC_VERSION = "krx-release-v2"
RELEASE_LOOKBACK_5 = 5
RELEASE_LOOKBACK_15 = 15

_NOTICE_CACHE = {}   # code -> latest warning event dict
_RULE_CACHE = {}     # code -> parsed rule dict

# 위험→경고 강등(투자위험 지정해제) 직후 판정 창 — 공시일부터 캘린더 3일.
# KRX는 해제 전일 공시하므로 효력 첫 거래일은 공시+1(평일) 또는 공시+3(금요 공시→월요일)에 걸림.
# 서산 원형(2026-07-09 해제공시 → 07-10 회전 245% 폭발) 실측으로 도입(회장님 승인 2026-07-10).
RISK_RELEASE_WINDOW_CDAYS = 3

_ROW_RE = re.compile(r'<a[^>]*class="tit"[^>]*>([^<]+)</a>.*?(\d{4})\.(\d{2})\.(\d{2})', re.S)
_NOTICE_ROW_RE = re.compile(
    r'<a(?=[^>]*class=["\']tit["\'])(?=[^>]*href=["\']([^"\']+)["\'])[^>]*>'
    r'(.*?)</a>.*?<td[^>]*class=["\']date["\'][^>]*>\s*'
    r'(\d{4})\.(\d{2})\.(\d{2})',
    re.S | re.I,
)
_RISK_CACHE = {}   # code -> "YYYYMMDD"(위험 해제공시일) | None — 실행당 캐시


def _clean_title(value):
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def _notice_rows(raw):
    return [
        {
            "href": urljoin("https://finance.naver.com", href),
            "title": _clean_title(title),
            "date": f"{y}{m}{d}",
        }
        for href, title, y, m, d in _NOTICE_ROW_RE.findall(raw or "")
    ]


def _latest_warning_notice(code, max_pages=8):
    """최신 투자경고 원이벤트를 찾는다. 해제/조회실패는 fail-safe 상태로 반환한다."""
    if code in _NOTICE_CACHE:
        return _NOTICE_CACHE[code]
    found = {"status": "unavailable"}
    try:
        for page in range(1, max_pages + 1):
            raw = get_bytes(
                f"https://finance.naver.com/item/news_notice.naver?code={code}&page={page}",
                UA_PC).decode("euc-kr", "ignore")
            rows = _notice_rows(raw)
            if not rows:
                # 첫 페이지부터 빈 파싱이면 차단/개편 가능성 — 오래된 지정으로 낙관하지 않는다.
                found = ({"status": "old"} if page > 1
                         else {"status": "unavailable", "error": "notice_list_empty"})
                break
            for row in rows:
                title = row["title"]
                row_code = (parse_qs(urlparse(row["href"]).query).get("code") or [None])[0]
                if row_code and row_code != str(code):
                    continue
                if "투자경고종목" not in title or "지정예고" in title:
                    continue
                # 지정해제 및 재지정 예고는 '재지정'보다 해제를 먼저 판별해야 한다.
                if "지정해제" in title:
                    found = {"status": "released", **row}
                    break
                if "재지정" in title or "지정중" in title or "매매거래" in title:
                    continue
                if "지정" in title:
                    found = {"status": "designated", **row}
                    break
            if found.get("status") in ("released", "designated"):
                break
        else:
            found = {"status": "old"}
    except Exception as exc:
        found = {"status": "unavailable", "error": type(exc).__name__}
    _NOTICE_CACHE[code] = found
    return found


def designation_notice_date(code, max_pages=8):
    """최근 지정 원공시일. 오래된 지정은 OLD, 해제/실패는 None."""
    event = _latest_warning_notice(code, max_pages=max_pages)
    if event.get("status") == "old":
        return "OLD"
    if event.get("status") == "designated":
        return event.get("date")
    return None


def _html_to_text(raw):
    text = re.sub(r"(?is)<style.*?</style>|<script.*?</script>", " ", raw or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return "\n".join(
        re.sub(r"[ \t]+", " ", line).strip()
        for line in text.splitlines() if line.strip()
    )


def _date_with_inferred_year(month, day, designation_date):
    year = int(designation_date[:4])
    desig_month = int(designation_date[4:6])
    if int(month) < desig_month - 6:
        year += 1
    return f"{year:04d}{int(month):02d}{int(day):02d}"


def parse_release_rule_text(text, *, notice_date=None, source_url=None, raw_text=None):
    """KRX 지정공시 평문에서 해제요건만 문맥 한정 파싱한다."""
    text = text or ""
    start = text.find("해제요건")
    end = text.find("근거규정", start + 1) if start >= 0 else -1
    section = text[start:end if end > start else None] if start >= 0 else ""
    designation = re.search(
        r"(?:\|\s*)?2\.\s*지정일\s*(?:\|\s*)?(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",
        text,
    )
    elapsed = re.search(r"지정일부터\s*계산하여\s*(\d+)일째", section)
    five = re.search(
        r"판단일\(T\).*?(?<!\d)5일\s*전날\(T-5\).*?보다\s*([0-9]+(?:\.[0-9]+)?)%\s*이상\s*상승",
        section,
    )
    fifteen = re.search(
        r"판단일\(T\).*?15일\s*전날\(T-15\).*?보다\s*([0-9]+(?:\.[0-9]+)?)%\s*이상\s*상승",
        section,
    )
    high_window = re.search(r"최근\s*(\d+)일\s*종가\s*중\s*최고가", section)
    first_review = re.search(
        r"해제여부의\s*최초\s*판단일은\s*(\d{1,2})월\s*(\d{1,2})일", section)
    all_not = "모두 해당하지 않을 경우" in section
    if not all((designation, elapsed, five, fifteen, high_window, first_review, all_not)):
        return {
            "source": "KRX_KOSCOM",
            "retrieved_via": "NAVER_FINANCE_NOTICE",
            "notice_date": notice_date,
            "source_url": source_url,
            "logic_version": RULE_LOGIC_VERSION,
            "parse_status": "unavailable",
            "parse_error": "required_release_clause_missing",
        }
    designation_date = f"{int(designation.group(1)):04d}{int(designation.group(2)):02d}{int(designation.group(3)):02d}"
    source_text = raw_text if raw_text is not None else text
    return {
        "source": "KRX_KOSCOM",
        "retrieved_via": "NAVER_FINANCE_NOTICE",
        "notice_date": notice_date,
        "designation_date": designation_date,
        "first_review_date_notice": _date_with_inferred_year(
            first_review.group(1), first_review.group(2), designation_date),
        "first_review_date_adjusted": None,
        "threshold_5d_pct": float(five.group(1)),
        "threshold_15d_pct": float(fifteen.group(1)),
        "recent_high_window": int(high_window.group(1)),
        "min_elapsed_days": int(elapsed.group(1)),
        "logic_version": RULE_LOGIC_VERSION,
        "parse_status": "ok",
        "source_url": source_url,
        "raw_text_hash": hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:20],
        "fetched_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
    }


def fetch_release_rule(code, max_pages=8):
    """종목별 최신 KRX/KOSCOM 지정공시 해제규칙. 실패를 캐시해 반복 오판/과호출을 막는다."""
    if code in _RULE_CACHE:
        return _RULE_CACHE[code]
    event = _latest_warning_notice(code, max_pages=max_pages)
    if event.get("status") != "designated":
        rule = {
            "source": "KRX_KOSCOM",
            "retrieved_via": "NAVER_FINANCE_NOTICE",
            "notice_date": event.get("date"),
            "source_url": event.get("href"),
            "logic_version": RULE_LOGIC_VERSION,
            "parse_status": "unavailable",
            "parse_error": f"notice_{event.get('status', 'unknown')}",
        }
        _RULE_CACHE[code] = rule
        return rule
    try:
        query = parse_qs(urlparse(event["href"]).query)
        notice_no = (query.get("no") or [None])[0]
        if not notice_no:
            raise ValueError("notice_no_missing")
        content_url = f"https://finance.naver.com/item/news_notice_read_content.naver?no={notice_no}"
        raw = get_bytes(content_url, UA_PC).decode("euc-kr", "ignore")
        rule = parse_release_rule_text(
            _html_to_text(raw), notice_date=event.get("date"),
            source_url=event.get("href"), raw_text=raw,
        )
        rule["notice_no"] = str(notice_no)
    except Exception as exc:
        rule = {
            "source": "KRX_KOSCOM",
            "retrieved_via": "NAVER_FINANCE_NOTICE",
            "notice_date": event.get("date"),
            "source_url": event.get("href"),
            "logic_version": RULE_LOGIC_VERSION,
            "parse_status": "unavailable",
            "parse_error": type(exc).__name__,
        }
    _RULE_CACHE[code] = rule
    return rule


def _actual_trading_bars(daily, as_of_date=None):
    """거래량 양수인 실제 매매일만 반환. volume 누락은 추정하지 않는다."""
    by_date = {}
    for bar in daily or []:
        date = str(bar.get("date") or "")
        if not date or bar.get("close") is None or (as_of_date and date > as_of_date):
            continue
        by_date[date] = bar
    bars = [by_date[d] for d in sorted(by_date)]
    if not bars:
        return [], [], "daily_missing"
    if any(bar.get("volume") is None for bar in bars):
        return [], [], "volume_missing"
    try:
        volumes = [float(bar["volume"]) for bar in bars]
    except (TypeError, ValueError, OverflowError):
        return [], [], "volume_invalid"
    if any(not math.isfinite(volume) for volume in volumes):
        return [], [], "volume_invalid"
    halted = [str(bar["date"]) for bar, volume in zip(bars, volumes) if volume <= 0]
    traded = [bar for bar, volume in zip(bars, volumes) if volume > 0]
    if not traded:
        return [], halted, "traded_daily_missing"
    if as_of_date:
        latest_raw = str(bars[-1].get("date") or "")
        if latest_raw != as_of_date:
            return [], halted, "trade_date_mismatch"
        if str(traded[-1].get("date") or "") != as_of_date:
            return [], halted, "current_day_halted"
    return traded, halted, None


def evaluate_release(daily, current_price, rule, *, as_of_date=None):
    """종목별 규칙과 실제 매매일로 해제예정을 평가하고 근거를 함께 반환한다."""
    base = {"value": None, "reason": "rule_unavailable", "rule": rule, "checks": None}
    if not isinstance(rule, dict) or rule.get("parse_status") != "ok":
        return base
    try:
        price = float(current_price)
        designation_date = str(rule["designation_date"])
        min_elapsed = int(rule["min_elapsed_days"])
        window = int(rule["recent_high_window"])
        threshold_5 = float(rule["threshold_5d_pct"])
        threshold_15 = float(rule["threshold_15d_pct"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return {**base, "reason": "rule_invalid"}
    if (not re.fullmatch(r"\d{8}", designation_date)
            or min_elapsed <= 0 or window <= 0
            or not all(math.isfinite(value) and value >= 0
                       for value in (threshold_5, threshold_15))):
        return {**base, "reason": "rule_invalid"}
    if not math.isfinite(price) or price <= 0:
        return {**base, "reason": "current_price_missing"}
    traded, halted, error = _actual_trading_bars(daily, as_of_date=as_of_date)
    if error:
        return {**base, "reason": error, "checks": {"halt_days_excluded": halted}}
    elapsed_dates = [str(b["date"]) for b in traded if str(b["date"]) >= designation_date]
    adjusted = elapsed_dates[min_elapsed - 1] if min_elapsed > 0 and len(elapsed_dates) >= min_elapsed else None
    rule = dict(rule)
    rule["first_review_date_adjusted"] = adjusted
    elapsed_days = len(elapsed_dates)
    elapsed_ok = min_elapsed > 0 and elapsed_days >= min_elapsed
    checks = {
        "as_of_date": as_of_date or str(traded[-1].get("date") or ""),
        "elapsed_days": elapsed_days,
        "elapsed_ok": elapsed_ok,
        "five_day_ok": None,
        "fifteen_day_ok": None,
        "not_recent_high_ok": None,
        "halt_days_excluded": [d for d in halted if not designation_date or d >= designation_date],
    }
    if not elapsed_ok:
        return {"value": False, "reason": "elapsed_not_met", "rule": rule, "checks": checks}
    required_history = max(window, RELEASE_LOOKBACK_15) + 1
    if len(traded) < required_history:
        return {"value": None, "reason": "daily_history_short", "rule": rule, "checks": checks}
    try:
        closes = [float(b["close"]) for b in traded]
    except (TypeError, ValueError, OverflowError):
        return {"value": None, "reason": "close_invalid", "rule": rule, "checks": checks}
    if any(not math.isfinite(close) or close <= 0 for close in closes):
        return {"value": None, "reason": "close_invalid", "rule": rule, "checks": checks}
    closes[-1] = price
    c_t = closes[-1]
    c_5 = closes[-(RELEASE_LOOKBACK_5 + 1)]
    c_15 = closes[-(RELEASE_LOOKBACK_15 + 1)]
    five_ok = c_t < c_5 * (1.0 + threshold_5 / 100.0)
    fifteen_ok = c_t < c_15 * (1.0 + threshold_15 / 100.0)
    not_high_ok = c_t < max(closes[-window:])
    checks.update({
        "five_day_ok": five_ok,
        "fifteen_day_ok": fifteen_ok,
        "not_recent_high_ok": not_high_ok,
        "current_price": c_t,
        "t_minus_5_close": c_5,
        "t_minus_15_close": c_15,
    })
    value = bool(five_ok and fifteen_ok and not_high_ok)
    return {
        "value": value,
        "reason": "all_release_conditions_met" if value else "conditions_not_met",
        "rule": rule,
        "checks": checks,
    }


def evaluate_release_for(code, daily, current_price, *, as_of_date=None):
    """조회+판정 상세 진입점. 어떤 실패도 True로 승격시키지 않는다."""
    try:
        return evaluate_release(
            daily, current_price, fetch_release_rule(code), as_of_date=as_of_date)
    except Exception as exc:
        return {
            "value": None,
            "reason": type(exc).__name__,
            "rule": None,
            "checks": None,
        }


def forecast_release(daily, current_price, rule, *, as_of_date=None):
    """하위호환 Boolean 진입점. 규칙 dict가 아니면 추정하지 않는다."""
    return evaluate_release(daily, current_price, rule, as_of_date=as_of_date).get("value")


def forecast_release_for(code, daily, current_price, *, as_of_date=None):
    return evaluate_release_for(
        code, daily, current_price, as_of_date=as_of_date).get("value")


def risk_release_date(code, max_pages=2):
    """최근 '투자위험종목 지정해제' 공시일 "YYYYMMDD" | None(해제 아님/재지정/실패).

    designation_notice_date와 같은 공시목록(최신순)에서 '투자위험종목' 이벤트만 본다.
    최신 위험 이벤트가 '지정해제'면 그 공시일, '지정/재지정'이면 None(현재 위험이거나 재지정됨).
    예고·매매거래(정지/재개) 파생 공시는 제외. 해제는 최근 며칠 내만 의미 있어 2페이지면 충분."""
    if code in _RISK_CACHE:
        return _RISK_CACHE[code]
    found = None
    try:
        for page in range(1, max_pages + 1):
            raw = get_bytes(
                f"https://finance.naver.com/item/news_notice.naver?code={code}&page={page}",
                UA_PC).decode("euc-kr", "ignore")
            rows = _ROW_RE.findall(raw)
            if not rows:
                break                               # 파싱 실패/공시 소진 — None 유지(fail-safe)
            hit = False
            for title, y, m, d in rows:
                if ("투자위험종목" not in title or "지정예고" in title or "매매거래" in title):
                    continue
                if "지정해제" in title:
                    found, hit = f"{y}{m}{d}", True
                else:                               # 지정/재지정이 더 최신 — 해제 상태 아님
                    found, hit = None, True
                break
            if hit:
                break
    except Exception:
        found = None                                # 네트워크/파싱 실패 — 판정 포기(fail-safe)
    _RISK_CACHE[code] = found
    return found


def recent_risk_release(code, today_yyyymmdd, window_days=RISK_RELEASE_WINDOW_CDAYS):
    """위험→경고 강등(투자위험 지정해제) 직후인지 — 해제공시일부터 캘린더 window_days 내면 True.

    True = '최고 단계 규제가 방금 풀린 종목'(억눌림 해소 재료) — rank4에서 alert_release와
    같은 규제해소 관찰 bucket. 판정불가·실패는 False(오분류 방지)."""
    d = risk_release_date(code)
    if not d or not today_yyyymmdd:
        return False
    try:
        from datetime import date
        t = date(int(today_yyyymmdd[:4]), int(today_yyyymmdd[4:6]), int(today_yyyymmdd[6:8]))
        r = date(int(d[:4]), int(d[4:6]), int(d[6:8]))
        return 0 <= (t - r).days <= window_days
    except Exception:
        return False


def elapsed_trading_days(daily, notice_date):
    """경고 지정일(공시 다음 매매거래일) 기산 경과 매매일수 — history 전진검증용(정렬 미사용).

    반환: 1=지정 첫날, 2=이튿날 … / 0=지정일이 아직 미래(공시 당일 저녁) /
    999='오래된 지정'(OLD — 공시 스캔 밖) / None=판정불가(공시 조회 실패·일봉 없음).
    daily 창(기본 25일)보다 오래된 지정은 창 길이로 하한 집계됨(≥25면 충분 경과로 해석)."""
    if notice_date is None:
        return None
    if notice_date == "OLD":
        return 999
    traded, _, error = _actual_trading_bars(daily)
    if error:
        return None
    dates = [str(b.get("date")) for b in traded]
    desig_idx = next((i for i, d in enumerate(dates) if d > notice_date), None)
    if desig_idx is None:
        return 0
    return len(dates) - desig_idx
