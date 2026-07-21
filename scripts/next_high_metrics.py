#!/usr/bin/env python3
"""익일 고가 KPI를 원시 가격으로 판정하는 공용 순수 함수."""
from decimal import Decimal, InvalidOperation

METRICS_VERSION = "next-high-raw-v1"


def derive_next_high_metrics(entry, next_high):
    try:
        entry_d = Decimal(str(entry))
        high_d = Decimal(str(next_high))
    except (InvalidOperation, TypeError, ValueError):
        return {"metrics_status": "raw_price_missing", "metrics_version": METRICS_VERSION}
    if not entry_d.is_finite() or not high_d.is_finite() or entry_d <= 0 or high_d < 0:
        return {"metrics_status": "raw_price_missing", "metrics_version": METRICS_VERSION}
    raw = (high_d / entry_d - Decimal("1")) * Decimal("100")
    out = {
        "metrics_status": "ok",
        "next_high_pct_raw": float(raw),
        "next_high_pct": round(float(raw), 2),
        "distance_to_7_pct": round(float(raw - Decimal("7")), 6),
        "distance_to_13_pct": round(float(raw - Decimal("13")), 6),
        "metrics_version": METRICS_VERSION,
    }
    for level in (7, 11, 13, 15):
        out[f"touch{level}"] = high_d * 100 >= entry_d * (100 + level)
    return out


def ensure_next_high_metrics(result):
    out = dict(result or {})
    out.update(derive_next_high_metrics(out.get("entry"), out.get("next_high")))
    return out
