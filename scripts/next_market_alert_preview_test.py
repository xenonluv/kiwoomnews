# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import next_market_alert_preview as preview


def daily_rows():
    dates = [
        "20260622", "20260623", "20260624", "20260625", "20260626",
        "20260629", "20260630", "20260701", "20260702", "20260703",
        "20260706", "20260707", "20260708", "20260709", "20260710",
        "20260713", "20260714", "20260715",
    ]
    rows = [{"date": day, "close": 1_000 + i, "volume": 10}
            for i, day in enumerate(dates)]
    rows[13]["close"] = 1_992
    return rows


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class NextMarketAlertPreviewTest(unittest.TestCase):
    def test_intraday_runner_cp_system_regression(self):
        now = datetime(2026, 7, 16, 15, 14, tzinfo=preview.KST)
        radar = {
            "generated_at": "2026-07-16 15:11:00 KST",
            "suspects": [{
                "code": "413630", "name": "씨피시스템",
                "signal_date": "20260716", "alert_now": None,
            }],
        }
        captured = {}
        with (
            mock.patch.object(preview, "_load_radar", return_value=radar),
            mock.patch.object(preview, "_daily_prices", return_value=daily_rows()),
            mock.patch.object(
                preview.kw, "market_alert_quote",
                return_value={"price": 3_355, "expected_close_price": 0,
                              "expected_close_qty": 0},
            ),
            mock.patch.object(preview, "_merge_history"),
            mock.patch.object(
                preview, "_publish",
                side_effect=lambda payload, ttl: captured.update(payload=payload, ttl=ttl),
            ),
        ):
            result = preview.run(now=now)
        record = result["codes"]["413630"]
        self.assertNotIn("code", record)
        self.assertEqual(record["target_trade_date"], "20260720")
        self.assertEqual(record["status"], "CONDITION_MET_INTRADAY")
        t5 = next(c for c in record["checks"] if c["rule_id"] == "SHORT_5D_60")
        self.assertEqual(t5["threshold_price"], 3_190)
        self.assertEqual(t5["margin_price"], 165)
        self.assertEqual(captured["payload"], result)

    def test_stale_radar_writes_short_tombstone(self):
        now = datetime(2026, 7, 16, 15, 14, tzinfo=preview.KST)
        captured = {}
        with (
            mock.patch.object(preview, "_load_radar", side_effect=RuntimeError("radar_stale")),
            mock.patch.object(
                preview, "_publish",
                side_effect=lambda payload, ttl: captured.update(payload=payload, ttl=ttl),
            ),
        ):
            result = preview.run(now=now)
        self.assertFalse(result["verified"])
        self.assertEqual(result["codes"], {})
        self.assertEqual(captured["ttl"], preview.SHORT_TTL_SECONDS)

    def test_auction_unverified_without_observation_flag(self):
        now = datetime(2026, 7, 16, 15, 25, tzinfo=preview.KST)
        with mock.patch.dict("os.environ", {}, clear=True):
            price, basis = preview._quote_basis({
                "price": 3_350,
                "expected_close_price": 3_360,
                "expected_close_qty": 10,
            }, now)
        self.assertEqual(price, 3_360)
        self.assertEqual(basis, "KRX_EXPECTED_CLOSE_UNVERIFIED")

    def test_preview_kv_uses_separate_key_and_expiry(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["body"] = json.loads(request.data)
            captured["authorization"] = request.headers["Authorization"]
            captured["timeout"] = timeout
            return _Response({"result": "OK"})

        with (
            mock.patch.object(preview, "_kv_credentials", return_value=("https://preview", "secret")),
            mock.patch.object(preview.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            self.assertTrue(preview._kv_set({"schema_version": 1}, 90))
        self.assertEqual(captured["body"][0:2], ["SET", preview.KV_KEY])
        self.assertEqual(captured["body"][-2:], ["EX", 90])
        self.assertEqual(captured["authorization"], "Bearer secret")

    def test_history_preserves_first_met_close_and_official(self):
        signal_date = "20260716"
        times = [
            datetime(2026, 7, 16, 15, 14, tzinfo=preview.KST),
            datetime(2026, 7, 16, 15, 15, tzinfo=preview.KST),
            datetime(2026, 7, 16, 15, 31, tzinfo=preview.KST),
            datetime(2026, 7, 16, 16, 40, tzinfo=preview.KST),
        ]
        with tempfile.TemporaryDirectory() as root, mock.patch.object(preview, "LOCAL_ROOT", root):
            preview._merge_history(
                signal_date, {"413630": {"name": "씨피시스템",
                                         "status": "CONDITION_MET_INTRADAY",
                                         "generated_at": times[0].isoformat()}}, times[0])
            preview._merge_history(
                signal_date, {"413630": {"name": "씨피시스템",
                                         "status": "NOT_MET",
                                         "generated_at": times[1].isoformat()}}, times[1])
            preview._merge_history(
                signal_date, {"413630": {"name": "씨피시스템",
                                         "status": "CONDITION_MET_CLOSE",
                                         "generated_at": times[2].isoformat()}}, times[2])
            preview._merge_history(
                signal_date, {"413630": {"name": "씨피시스템",
                                         "status": "OFFICIAL_CONFIRMED",
                                         "generated_at": times[3].isoformat()}}, times[3])
            path = preview._history_path(signal_date)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)["codes"]["413630"]
        self.assertEqual(record["first_met"]["generated_at"], times[0].isoformat())
        self.assertEqual(record["latest"]["status"], "OFFICIAL_CONFIRMED")
        self.assertEqual(record["close"]["status"], "CONDITION_MET_CLOSE")
        self.assertEqual(record["official"]["status"], "OFFICIAL_CONFIRMED")

    def test_official_close_cache_is_reused_only_after_crosscheck_mark(self):
        now = datetime(2026, 7, 16, 15, 31, tzinfo=preview.KST)
        rows = daily_rows() + [{"date": "20260716", "close": 3_355, "volume": 100}]
        with (
            tempfile.TemporaryDirectory() as root,
            mock.patch.object(preview, "LOCAL_ROOT", root),
            mock.patch.object(preview.kw, "daily_prices", return_value=rows) as fetch,
        ):
            preview._daily_prices("413630", "20260716", refresh=True)
            preview._daily_prices("413630", "20260716", refresh=True)
            self.assertEqual(fetch.call_count, 2)
            preview._mark_official_close_confirmed("413630", "20260716", now)
            preview._daily_prices("413630", "20260716", refresh=True)
            self.assertEqual(fetch.call_count, 2)

    def test_post_close_official_notice_can_confirm_when_price_api_fails(self):
        now = datetime(2026, 7, 16, 16, 40, tzinfo=preview.KST)
        radar = {
            "generated_at": "2026-07-16 16:36:00 KST",
            "suspects": [{
                "code": "413630", "name": "씨피시스템", "signal_date": "20260716",
            }],
        }
        with (
            mock.patch.object(preview, "_load_radar", return_value=radar),
            mock.patch.object(preview, "_daily_prices", side_effect=RuntimeError("quote down")),
            mock.patch.object(preview, "_official_notice", return_value={"notice_id": "1"}),
            mock.patch.object(preview, "_merge_history"),
            mock.patch.object(preview, "_publish"),
        ):
            result = preview.run(post_close=True, now=now)
        record = result["codes"]["413630"]
        self.assertEqual(record["status"], "OFFICIAL_CONFIRMED")
        self.assertTrue(record["verified"])
        self.assertEqual(record["official_evidence"]["notice_id"], "1")

    def test_installer_has_preview_jobs_without_live_or_push(self):
        with open(os.path.join(preview.REPO, "scripts", "install_cron_kiwoom.sh"),
                  encoding="utf-8") as handle:
            lines = [
                line for line in handle
                if "next_market_alert_preview.py" in line and not line.lstrip().startswith("#")
            ]
        self.assertEqual(len(lines), 3)
        self.assertTrue(any("55-59 14" in line and "--once" in line for line in lines))
        self.assertTrue(any("0-35 15" in line and "--once" in line for line in lines))
        self.assertTrue(any("40 15-20" in line and "--post-close" in line for line in lines))
        self.assertTrue(all("AUTOTRADE_LIVE" not in line and "--push" not in line for line in lines))


if __name__ == "__main__":
    unittest.main()
