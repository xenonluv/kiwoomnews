# -*- coding: utf-8 -*-
"""익일 투자주의 예고의 공개 가격조건을 계산하는 순수 규칙 모듈.

이 모듈은 주문·랭킹을 변경하지 않는다. KRX 공식 종가/현재가와 실제 거래일 일봉을
입력받아 공개 정보만으로 확정 가능한 단기·중기 급등 조건을 계산한다.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Optional


SCHEMA_VERSION = 1

PUBLIC_PRICE_RULES = (
    ("SHORT_3D_100", 3, 100.0, "최근 3매매일 종가 대비 100% 이상 상승"),
    ("SHORT_5D_60", 5, 60.0, "최근 5매매일 종가 대비 60% 이상 상승"),
    ("MID_15D_100", 15, 100.0, "최근 15매매일 종가 대비 100% 이상 상승"),
)

ACTIVE_HIGHER_ALERTS = {
    "경고", "위험", "투자경고", "투자위험", "WARNING", "RISK", "INVESTMENT_WARNING",
    "INVESTMENT_RISK",
}


def krx_tick(price: float) -> int:
    """현재 KRX 호가단위. 시장 구분 없이 적용되는 2023년 이후 단위."""
    value = max(0.0, float(price or 0))
    if value < 2_000:
        return 1
    if value < 5_000:
        return 5
    if value < 20_000:
        return 10
    if value < 50_000:
        return 50
    if value < 200_000:
        return 100
    if value < 500_000:
        return 500
    return 1_000


def minimum_valid_price(raw_price: float) -> int:
    """이론 임계가격 이상인 가장 작은 유효 KRX 호가를 반환한다."""
    raw = max(0.0, float(raw_price or 0))
    candidate = int(math.floor(raw))
    if candidate < raw:
        candidate += 1
    # 경계(2천/5천 등)를 넘을 때 호가단위가 달라지므로 후보 가격에서 반복 보정한다.
    for _ in range(4):
        tick = krx_tick(candidate)
        rounded = int(math.ceil(raw / tick) * tick)
        if rounded == candidate:
            return rounded
        candidate = rounded
    return candidate


def _number(value: Any) -> float:
    try:
        return abs(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0.0


def trading_references(
    daily: Iterable[Dict[str, Any]], signal_date: str
) -> Dict[int, Dict[str, Any]]:
    """신호일을 제외한 거래량 있는 KRX 일봉에서 T-3/T-5/T-15를 뽑는다."""
    usable: Dict[str, Dict[str, Any]] = {}
    for row in daily or []:
        day = str(row.get("date") or "").replace("-", "")
        close = _number(row.get("close"))
        volume = _number(row.get("volume"))
        if len(day) != 8 or day >= signal_date or close <= 0 or volume <= 0:
            continue
        usable[day] = {
            "date": day,
            "close": close,
            "volume": volume,
        }
    ordered = sorted(usable.values(), key=lambda item: item["date"], reverse=True)
    return {
        offset: ordered[offset - 1]
        for offset in (3, 5, 15)
        if len(ordered) >= offset
    }


def _check(
    rule_id: str,
    offset: int,
    required_pct: float,
    label: str,
    reference: Optional[Dict[str, Any]],
    price: float,
) -> Dict[str, Any]:
    if not reference:
        return {
            "rule_id": rule_id,
            "label": label,
            "offset": offset,
            "available": False,
            "met": None,
        }
    base = float(reference["close"])
    theoretical = base * (1.0 + required_pct / 100.0)
    threshold = minimum_valid_price(theoretical)
    rate = (price / base - 1.0) * 100.0 if price > 0 else None
    margin_price = price - threshold if price > 0 else None
    margin_pct = (
        margin_price / threshold * 100.0
        if margin_price is not None and threshold > 0
        else None
    )
    distance_pct = (
        (threshold - price) / threshold * 100.0
        if price > 0 and threshold > price
        else 0.0
    )
    return {
        "rule_id": rule_id,
        "label": label,
        "offset": offset,
        "available": True,
        "base_date": reference["date"],
        "base_close": base,
        "required_pct": required_pct,
        "theoretical_price": round(theoretical, 4),
        "threshold_price": threshold,
        "current_rate_pct": round(rate, 4) if rate is not None else None,
        "margin_price": round(margin_price, 4) if margin_price is not None else None,
        "margin_pct": round(margin_pct, 4) if margin_pct is not None else None,
        "distance_to_threshold_pct": round(distance_pct, 4),
        "met": bool(price >= threshold) if price > 0 else None,
    }


def evaluate_alert_preview(
    *,
    code: str,
    name: str,
    signal_date: str,
    target_trade_date: Optional[str],
    daily: Iterable[Dict[str, Any]],
    price: float,
    price_basis: str,
    current_alert: Optional[str] = None,
    watch_margin_pct: float = 3.0,
) -> Dict[str, Any]:
    """공개 가격조건을 평가한다.

    ``price_basis``는 KRX_CURRENT, KRX_EXPECTED_CLOSE_VERIFIED,
    KRX_EXPECTED_CLOSE_UNVERIFIED, KRX_OFFICIAL_CLOSE 중 하나다.
    """
    result: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "code": str(code).zfill(6),
        "name": name,
        "signal_date": signal_date,
        "target_trade_date": target_trade_date,
        "status": "UNVERIFIED",
        "verified": False,
        "price": round(_number(price), 4) or None,
        "price_basis": price_basis,
        "reason": "판정 자료가 충분하지 않습니다.",
        "checks": [],
        "triggered_rule_ids": [],
        "nearest_margin_pct": None,
        "private_account_condition_available": False,
        "repeated_attention_history_available": False,
        "rule_metadata": {
            "version": "KRX_PUBLIC_ATTENTION_20260720",
            "source": "KRX KIND 시장경보제도 > 투자주의종목",
            "verified_on": "2026-07-20",
            "scope": "공개 가격조건만 판정",
        },
    }
    if not target_trade_date:
        result["reason"] = "다음 KRX 거래일을 확정하지 못했습니다."
        return result
    if str(current_alert or "").strip() in ACTIVE_HIGHER_ALERTS:
        result.update({
            "status": "NOT_APPLICABLE",
            "verified": True,
            "reason": "현재 투자경고·위험 종목은 표준 익일 투자주의 예고 트랙 대상이 아닙니다.",
            "separate_track": "alert_release_or_redesignation",
        })
        return result
    if result["price"] is None:
        result["reason"] = "KRX 판정 가격을 확인하지 못했습니다."
        return result

    references = trading_references(daily, signal_date)
    checks = [
        _check(rule_id, offset, pct, label, references.get(offset), result["price"])
        for rule_id, offset, pct, label in PUBLIC_PRICE_RULES
    ]
    result["checks"] = checks
    if not all(check.get("available") for check in checks):
        result["reason"] = "T-3/T-5/T-15 실제 거래일 종가를 모두 확인하지 못했습니다."
        return result

    triggered = [check["rule_id"] for check in checks if check.get("met")]
    positive_margins = [
        check["distance_to_threshold_pct"] for check in checks
        if check.get("distance_to_threshold_pct") is not None
        and check["distance_to_threshold_pct"] > 0
    ]
    nearest = min(positive_margins) if positive_margins else 0.0
    result["triggered_rule_ids"] = triggered
    result["nearest_margin_pct"] = round(nearest, 4)

    if price_basis == "KRX_EXPECTED_CLOSE_UNVERIFIED":
        result.update({
            "status": "AUCTION_PRICE_UNVERIFIED",
            "verified": False,
            "reason": "동시호가 예상체결가의 실시간 신뢰성이 확인되지 않아 확정 배지를 내지 않습니다.",
        })
        return result

    result["verified"] = True
    if triggered:
        if price_basis == "KRX_OFFICIAL_CLOSE":
            status = "CONDITION_MET_CLOSE"
            reason = "KRX 공식 종가가 공개 가격조건을 충족했습니다. 최종 공시 대기 상태입니다."
        else:
            status = "CONDITION_MET_INTRADAY"
            reason = "현재 KRX 가격이 공개 가격조건을 충족했습니다. 종가 변동 전 잠정 계산입니다."
        result.update(status=status, reason=reason)
        return result

    t5 = next(check for check in checks if check["rule_id"] == "SHORT_5D_60")
    t5_partial_threshold = minimum_valid_price(t5["base_close"] * 1.45)
    if result["price"] >= t5_partial_threshold:
        result.update({
            "status": "PARTIAL_PUBLIC_CONDITION",
            "reason": "5매매일 45% 공개 가격조건은 충족했으나 불건전계좌 조건은 외부에서 확정할 수 없습니다.",
            "partial_threshold_price": t5_partial_threshold,
        })
    elif 0 < nearest <= watch_margin_pct:
        result.update({
            "status": "WATCH",
            "reason": f"가장 가까운 공개 가격조건까지 {nearest:.2f}% 남았습니다.",
        })
    else:
        result.update({
            "status": "NOT_MET",
            "reason": "확인 가능한 공개 가격조건을 충족하지 않았습니다.",
        })
    return result
