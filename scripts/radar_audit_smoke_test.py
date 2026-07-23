#!/usr/bin/env python3
"""Regression test for rejected-candidate raw audit retention."""

import json
import os
import tempfile
from datetime import datetime as _real_datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import radar
from radar_audit import AuditCollector
from rank_policy import RANK_MODEL_VERSION, policy_metadata


CODE = "263800"

# 벽시계 고정 — scan_reaccum_candidate의 14:30 조기 컷이 실행 시각에 좌우돼
# 자정~14:29 실행 시 sparks 미관찰로 실패하던 결함 제거(적대 재검증 신규 결함 1).
_KST = timezone(timedelta(hours=9))
_FIXED_NOW = _real_datetime(2026, 7, 10, 15, 0, tzinfo=_KST)


class _FixedDateTime(_real_datetime):
    @classmethod
    def now(cls, tz=None):
        return _FIXED_NOW.astimezone(tz) if tz else _FIXED_NOW.replace(tzinfo=None)


def _params():
    return SimpleNamespace(
        explosion_high_pct=22.0,
        explosion_vol_turnover=90.0,
        explosion_scan_n=50,
        youtong_change_pct=7.0,
        youtong_turnover_min=50.0,
        reignition_body_pct=1.5,
        reignition_span_min=5,
        reignition_min_count=2,
        reignition_start="1430",
        reaccum_change_min=-5.0,
        reaccum_change_max=7.0,
        lowaccum_change_max=-10.0,
        lowaccum_body_pct=2.0,
        lowaccum_min_count=3,
        geupso_body_pct=2.0,
        geupso_min_count=2,
        geupso_change_max=10.0,
        dry_run=True,
    )


def _now():
    prev = 8660.0
    return {
        "date": "20260710", "price": round(prev * (1 - 0.0544)),
        "open": 8410.0, "high": round(prev * 1.1308), "low": 8050.0,
        "prev_close": prev, "change_pct": -5.44,
        "volume": 22_020_375, "value": 173_500_000_000,
        "sector": "소프트웨어",
    }


def _daily():
    rows = []
    for i in range(23):
        rows.append({
            "date": f"202606{i + 1:02d}", "open": 7900, "high": 8500,
            "low": 7800, "close": 8000 + i * 5, "volume": 1_000_000,
            "value": 8_000_000_000,
        })
    rows.append({"date": "20260709", "open": 8100, "high": 10_600,
                 "low": 8000, "close": 8660, "volume": 25_000_000,
                 "value": 190_000_000_000})
    rows.append({"date": "20260710", "open": 8410, "high": _now()["high"],
                 "low": 8050, "close": _now()["price"],
                 "volume": _now()["volume"], "value": _now()["value"]})
    return rows


def _minute():
    # Three intraday sparks, all before 14:30. There is deliberately no
    # qualifying bar in the reaccumulation decision window.
    return [
        {"time": "100000", "open": 8000, "high": 8170, "low": 7990,
         "close": 8160, "vol": 100_000},
        {"time": "110000", "open": 8100, "high": 8290, "low": 8090,
         "close": 8270, "vol": 120_000},
        {"time": "130000", "open": 8050, "high": 8230, "low": 8040,
         "close": 8215, "vol": 130_000},
        {"time": "143000", "open": 8200, "high": 8220, "low": 8160,
         "close": 8190, "vol": 80_000},
    ]


def _gates(row):
    return {(g["track"], g["gate"]): g for g in row["gate_decisions"]}


def main():
    p = _params()
    radar._AUDIT = AuditCollector(model_meta=policy_metadata(), dry_run=True)
    rec = {
        "code": CODE, "name": "데이타솔루션", "peak_date": "20260709",
        "peak_high_pct": 29.9, "vol_turnover_pct": 110.0,
        "peak_value_eok": 1900, "source": "live", "cause_done": True,
    }
    rank_row = {"code": CODE, "name": "데이타솔루션", "change_pct": -5.44,
                "value_mn": 173_500}
    reg = {"records": {}, "trading_days": [], "window_scanned": {}}

    with patch.object(radar, "datetime", _FixedDateTime), \
            patch.object(radar, "_up_ranking_rows", return_value=([rank_row], 1, 0)), \
            patch.object(radar.kis, "price_now_jmoney_un", return_value=_now()), \
            patch.object(radar.kis, "daily_prices_jmoney_un", return_value=_daily()), \
            patch.object(radar, "_minute_bars_with_fallback", side_effect=lambda code, label="": (
                radar._AUDIT.minute_bars(code, _minute(), market_basis="KRX") or _minute())), \
            patch.object(radar, "_nxt_change_pct", return_value=None), \
            patch.object(radar.float_ratio, "get_float_and_listed", return_value=(0.25, 22_000_000)), \
            patch.object(radar, "_rank_page", side_effect=lambda direction, market, page: (
                ([rank_row], 1) if direction == "up" and market == "KOSPI" else ([], 0))):
        radar.update_live_explosions(reg, p)
        result = radar.scan_reaccum_candidate(rec, p, [])
        shakeouts = radar.scan_shakeout(p, events=[], extra_codes=[])

    assert result is None
    assert shakeouts == []
    radar._AUDIT.mark_ranked([])
    payload = radar._AUDIT.payload(generated_at="2026-07-10 15:30:00 KST")
    row = next(x for x in payload["observations"] if x["code"] == CODE)
    gates = _gates(row)

    assert round(row["price_snapshot"]["high_pct"], 2) == 13.08
    assert row["price_snapshot"]["change_pct"] == -5.44
    assert 399.0 < row["turnover"]["turnover_pct"] < 401.0
    assert len(row["sparks"]["all"]) == 3
    assert row["sparks"]["after_reignition_start_count"] == 0
    assert gates[("explosion", "high_pct")]["reason_code"] == "EXPLOSION_HIGH_BELOW_MIN"
    assert gates[("reaccum", "change_band")]["reason_code"] == "REACCUM_CHANGE_BELOW_MIN"
    assert gates[("reaccum", "spark_count")]["reason_code"] == "REACCUM_SPARK_BELOW_MIN"
    assert gates[("shakeout", "high_pct")]["reason_code"] == "SHAKEOUT_HIGH_BELOW_MIN"
    assert row["rank"]["published"] is False
    assert row["status"] == "REJECT_RULE"

    # End-to-end local persistence creates schema/model, immutable scan,
    # normalized minute volume, and a verifiable manifest without Git files.
    with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ, {"RADAR_JSON_STORE_ROOT": td}):
        collector = AuditCollector(model_meta=policy_metadata(), dry_run=False)
        collector.trade_date = "20260713"
        collector.observe(CODE, name="데이타솔루션", source="naver_up")
        collector.gate(CODE, "reaccum", "final", "REJECT_RULE",
                       reason_code="NO_REACCUM_VARIANT_PASSED")
        collector.minute_bars(
            CODE, [{"time": "090000", "open": 100, "high": 110,
                    "low": 99, "close": 108, "vol": 1234}],
            market_basis="KRX")
        persisted = collector.persist(generated_at="2026-07-13 09:03:12 KST")
        assert persisted.ok, persisted.error
        root = Path(td)
        assert (root / "schema.json").is_file()
        assert (root / "models" / f"{RANK_MODEL_VERSION}.json").is_file()
        day = root / "2026" / "07" / "13"
        minute = json.loads((day / "minute" / f"{CODE}_KRX.json").read_text(encoding="utf-8"))
        assert minute["bars"][0]["volume"] == 1234
        assert "vol" not in minute["bars"][0]
        import radar_json_store as store
        assert store.verify_manifest("20260713", root).ok
    print("radar audit smoke ok: rejected values + four gate reasons retained")


if __name__ == "__main__":
    main()
