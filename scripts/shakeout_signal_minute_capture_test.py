#!/usr/bin/env python3
"""신호일 흔들기 분봉 수집기의 네트워크 없는 회귀 테스트."""

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import radar_json_store as store
import shakeout_signal_minute_capture as capture


DAY = "20260717"
NEXT_DAY = "20260720"
NOW = datetime(2026, 7, 17, 15, 45, tzinfo=capture.KST)


def full_bars():
    return [
        {"time": "090000", "open": 100, "high": 105, "low": 95, "close": 101, "vol": 300},
        {"time": "120000", "open": 101, "high": 110, "low": 100, "close": 108, "vol": 300},
        {"time": "153000", "open": 108, "high": 109, "low": 90, "close": 105, "vol": 400},
    ]


def daily_row(day=DAY, volume=1000):
    return {
        "date": day,
        "open": 100,
        "high": 110,
        "low": 90,
        "close": 105,
        "volume": volume,
    }


def material(evidence_datetime="20260717150000"):
    return {
        "grade": "A",
        "directness": "direct",
        "freshness": "today",
        "evidence": [{"datetime": evidence_datetime, "title": "원문 그대로"}],
    }


def suspect(
    name,
    *,
    shakeout=True,
    final=True,
    published=True,
    tier=1,
    very_good=False,
    evidence_datetime="20260717150000",
):
    return {
        "name": name,
        "shakeout": shakeout,
        "pattern": "shakeout" if shakeout else "reaccum",
        "final": final,
        "published": published,
        "first_seen": "2026-07-17 12:00:00 KST",
        "last_seen": "2026-07-17 15:41:00 KST",
        "strength_tier": tier,
        "very_good": very_good,
        "very_good_candidate": not very_good,
        "dd6_pct": -31.0 if very_good else -15.0,
        "material": material(evidence_datetime),
    }


class FakeApi:
    def __init__(self, *, latest=DAY, minute=None, stock_daily=None):
        self.latest = latest
        self.minute_response = minute or {"trade_date": DAY, "bars": full_bars()}
        self.stock_daily = stock_daily
        self.daily_calls = []
        self.minute_calls = []

    def daily(self, code, days=15, market="J"):
        self.daily_calls.append((code, days, market))
        if code == capture.BENCHMARK_CODE:
            return [daily_row(self.latest)]
        if self.stock_daily is not None:
            return self.stock_daily
        return [daily_row(DAY)]

    def minute(self, code, until="153000", market="J"):
        self.minute_calls.append((code, until, market))
        return self.minute_response


class ShakeoutSignalMinuteCaptureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "raw"
        self.history_dir = base / "history"
        self.history_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_history(self, suspects=None, blocked=None, *, as_of="2026-07-17 15:41:00 KST"):
        payload = {
            "date": DAY,
            "as_of": as_of,
            "suspects": suspects or {},
            "blocked_suspects": blocked or {},
        }
        path = self.history_dir / f"{DAY}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def write_scan(self, run_id, observations, *, generated="2026-07-17 15:40:00 KST"):
        result = store.write_scan(
            {
                "record_type": "scan_run",
                "run": {
                    "run_id": run_id,
                    "trade_date": DAY,
                    "scan_ok": True,
                    "dry_run": False,
                    "generated_at": generated,
                },
                "observations": observations,
            },
            trade_date=DAY,
            observed_at=generated,
            run_id=run_id,
            root=self.root,
            update_manifest=False,
        )
        self.assertTrue(result.ok, result.error)
        return Path(result.path)

    @staticmethod
    def gate_observation(code, *, gate=True, legacy=False):
        row = {
            "code": code,
            "name": "스캔" + code,
            "observed_at": "2026-07-17 15:39:00 KST",
        }
        if legacy:
            row["pattern"] = "shakeout"
        else:
            row["gate_decisions"] = [
                {
                    "track": "shakeout",
                    "gate": "final",
                    "status": "PASS" if gate else "REJECT_RULE",
                }
            ]
        return row

    def read_index(self):
        path = capture._index_path(DAY, self.root)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(store.verify_payload_integrity(payload))
        return payload

    def run_capture(self, api, **kwargs):
        return capture.run(
            now=kwargs.pop("now", NOW),
            requested_date=kwargs.pop("requested_date", None),
            history_dir=self.history_dir,
            root=self.root,
            daily_fetch=api.daily,
            minute_fetch=api.minute,
            **kwargs,
        )

    def test_population_union_groups_and_provenance(self):
        history_path = self.write_history(
            {
                "111111": suspect("VG", tier=4, very_good=True),
                "222222": suspect("강", tier=3),
                "333333": suspect("약", tier=1, final=False, published=False),
                "999999": suspect("제외", shakeout=False),
            },
            {
                "444444": {
                    "name": "차단",
                    "pattern": "shakeout",
                    "final": False,
                    "published": False,
                    "first_blocked": "2026-07-17 14:00:00 KST",
                    "last_blocked": "2026-07-17 15:00:00 KST",
                }
            },
        )
        self.write_scan(
            "run-a",
            [
                self.gate_observation("333333"),
                self.gate_observation("555555"),
                self.gate_observation("666666", legacy=True),
                self.gate_observation("777777", gate=False),
            ],
        )
        future = self.write_scan(
            "future",
            [self.gate_observation("888888")],
            generated="2026-07-17 15:46:00 KST",
        )
        damaged = self.write_scan("damaged", [self.gate_observation("121212")])
        document = json.loads(damaged.read_text(encoding="utf-8"))
        document["run"]["scan_ok"] = False
        damaged.write_text(json.dumps(document), encoding="utf-8")

        rows, meta = capture.build_candidates(
            DAY, NOW, history_dir=self.history_dir, root=self.root
        )
        self.assertEqual(
            set(rows), {"111111", "222222", "333333", "444444", "555555", "666666"}
        )
        self.assertEqual(rows["111111"]["shakeout_group"], "very_good")
        self.assertEqual(rows["222222"]["shakeout_group"], "조합D 단독")
        self.assertEqual(rows["333333"]["shakeout_group"], "약한흔들기")
        self.assertFalse(rows["333333"]["final_as_of_capture"])
        self.assertTrue(rows["444444"]["history_present"])
        self.assertTrue(rows["444444"]["blocked"])
        self.assertEqual(rows["444444"]["shakeout_group"], "강도 미확인")
        self.assertTrue(rows["555555"]["scan_only"])
        self.assertIn("scan_only", rows["555555"]["sources"])
        self.assertTrue(rows["666666"]["legacy_pattern_fallback"])
        self.assertEqual(rows["555555"]["material_snapshot"], None)
        self.assertEqual(meta["history_source_file_sha256"], capture._sha256_bytes(history_path.read_bytes()))
        self.assertTrue(future.exists())

    def test_material_time_classes_are_strictly_separated(self):
        cutoff = datetime(2026, 7, 17, 15, 30, tzinfo=capture.KST)
        common = (Path("history.json"), "hash", "15:41", NOW)
        prior = capture._material_snapshot(
            {"material": material("20260717153000")}, cutoff, *common
        )
        post = capture._material_snapshot(
            {"material": material("20260717153100")}, cutoff, *common
        )
        bad = capture._material_snapshot(
            {"material": material("알수없음")}, cutoff, *common
        )
        missing = capture._material_snapshot({}, cutoff, *common)
        self.assertEqual(prior["material_time_class"], "evidence_time_proxy_pre_cutoff")
        self.assertNotEqual(prior["material_time_class"], "signal_time_prior")
        self.assertEqual(post["material_time_class"], "post_close_material")
        self.assertEqual(bad["material_time_class"], "unverifiable_capture")
        self.assertEqual(missing["material_time_class"], "missing")
        self.assertEqual(prior["material_snapshot"]["evidence"][0]["title"], "원문 그대로")

    def test_canonicalization_and_coverage_boundaries(self):
        same = [
            {"time": DAY + "090000", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10},
            {"time": "090000", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "vol": 10.0},
        ]
        bars = capture._canonical_bars(same, signal_date=DAY)
        self.assertEqual(len(bars), 1)
        self.assertIn("vol", bars[0])
        self.assertNotIn("volume", bars[0])
        with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
            capture._canonical_bars(
                same + [{"time": "090000", "open": 100, "high": 101, "low": 99, "close": 99, "vol": 10}],
                signal_date=DAY,
            )
        with self.assertRaisesRegex(ValueError, "bar date mismatch"):
            capture._canonical_bars(
                [{"time": NEXT_DAY + "090000", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}],
                signal_date=DAY,
            )
        with self.assertRaises((TypeError, ValueError)):
            capture._canonical_bars(
                [{"time": "090000", "open": 1, "high": 1, "low": 1, "close": 1}],
                signal_date=DAY,
            )

        verified = capture.assess_coverage(daily_row(), full_bars())
        self.assertEqual(verified["coverage_status"], "verified_full")
        volume_warning = capture.assess_coverage(
            daily_row(volume=2000), full_bars()
        )
        self.assertEqual(volume_warning["coverage_status"], "verified_full")
        self.assertTrue(volume_warning["coverage_warnings"])
        gap = capture.assess_coverage(daily_row(), [
            {**full_bars()[0], "time": "091000"},
            {**full_bars()[1], "time": "120000"},
            {**full_bars()[2], "time": "151000"},
        ])
        self.assertEqual(gap["coverage_status"], "verified_with_session_gaps")
        partial = capture.assess_coverage(daily_row(), full_bars()[:2])
        self.assertEqual(partial["coverage_status"], "partial_daily_mismatch")
        self.assertEqual(
            capture.assess_coverage(daily_row(volume=0), [])["coverage_status"],
            "no_trade_confirmed",
        )
        self.assertEqual(
            capture.assess_coverage(daily_row(volume=1), [])["coverage_status"],
            "minute_missing",
        )
        with self.assertRaises((TypeError, ValueError)):
            capture.assess_coverage({**daily_row(), "volume": None}, [])

    def test_current_session_guard_and_historical_backfill_block(self):
        self.write_history({"111111": suspect("한종목")})
        api = FakeApi()
        with self.assertRaisesRegex(ValueError, "at or after 15:40"):
            self.run_capture(
                api, now=datetime(2026, 7, 17, 15, 39, tzinfo=capture.KST)
            )
        later = FakeApi(latest=NEXT_DAY)
        with self.assertRaisesRegex(ValueError, "historical backfill is forbidden"):
            self.run_capture(later, requested_date=DAY, now=datetime(2026, 7, 20, 16, 0, tzinfo=capture.KST))
        self.assertEqual(later.minute_calls, [])

    def test_holiday_latest_trade_date_is_safe_noop(self):
        api = FakeApi()
        result = self.run_capture(
            api, now=datetime(2026, 7, 18, 15, 45, tzinfo=capture.KST)
        )
        self.assertEqual(result["candidate_n"], 0)
        self.assertEqual(result["logical_api_call_n"], 1)
        self.assertTrue(result["capture_complete"])

    def test_one_candidate_exception_does_not_block_the_next(self):
        self.write_history(
            {"111111": suspect("실패"), "222222": suspect("성공")}
        )
        api = FakeApi()

        def mixed_minute(code, until="153000", market="J"):
            api.minute_calls.append((code, until, market))
            return None if code == "111111" else {"trade_date": DAY, "bars": full_bars()}

        api.minute = mixed_minute
        result = self.run_capture(api)
        self.assertEqual(result["processed_n"], 2)
        statuses = {
            row["code"]: row["coverage_status"]
            for row in self.read_index()["results"]
        }
        self.assertEqual(statuses["111111"], "api_error")
        self.assertEqual(statuses["222222"], "verified_full")

    def test_invalid_cap_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "max_candidates"):
            self.run_capture(FakeApi(), max_candidates=0)

    def test_existing_full_j_is_reused_without_minute_call(self):
        self.write_history({"111111": suspect("재사용")})
        saved = store.merge_minute_bars(
            DAY,
            "111111",
            full_bars(),
            market_basis="J",
            source_broker="first_touch",
            fetched_at=NOW,
            root=self.root,
            update_manifest=False,
        )
        self.assertTrue(saved.ok, saved.error)
        api = FakeApi()
        result = self.run_capture(api)
        self.assertEqual(result["logical_api_call_n"], 2)
        self.assertEqual(api.minute_calls, [])
        row = self.read_index()["results"][0]
        self.assertEqual(row["capture_status"], "reused")
        self.assertEqual(row["minute_source"], "reused_existing_J")
        self.assertEqual(row["coverage_status"], "verified_full")
        self.assertTrue(row["minute_fetched_first_at"])

    def test_fetched_j_is_canonical_and_manifest_rebuilt_once(self):
        self.write_history({"111111": suspect("신규")})
        api = FakeApi(
            minute={
                "trade_date": DAY,
                "bars": [{**bar, "volume": bar["vol"]} for bar in full_bars()],
            }
        )
        for bar in api.minute_response["bars"]:
            bar.pop("vol")
        original_rebuild = store.rebuild_manifest
        with mock.patch.object(
            capture.store, "rebuild_manifest", wraps=original_rebuild
        ) as rebuild:
            result = self.run_capture(api)
        self.assertEqual(rebuild.call_count, 1)
        self.assertEqual(result["logical_api_call_n"], 3)
        minute_path = capture._minute_path(DAY, "111111", self.root)
        minute = json.loads(minute_path.read_text(encoding="utf-8"))
        self.assertEqual(minute["market_basis"], "J")
        self.assertTrue(all("vol" in bar and "volume" not in bar for bar in minute["bars"]))
        self.assertTrue(store.verify_payload_integrity(minute))
        index = self.read_index()
        self.assertEqual(index["api_call_n"], 3)
        self.assertEqual(index["results"][0]["actual_trade_date"], DAY)
        manifest = json.loads(
            (store.local_day_dir(DAY, self.root) / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertTrue(store.verify_payload_integrity(manifest))

    def test_trade_date_mismatch_does_not_write_minute(self):
        self.write_history({"111111": suspect("날짜")})
        api = FakeApi(minute={"trade_date": NEXT_DAY, "bars": full_bars()})
        self.run_capture(api)
        self.assertFalse(capture._minute_path(DAY, "111111", self.root).exists())
        row = self.read_index()["results"][0]
        self.assertEqual(row["coverage_status"], "trade_date_mismatch")
        self.assertEqual(row["actual_trade_date"], NEXT_DAY)
        self.assertEqual(row["attempts"][-1]["actual_trade_date"], NEXT_DAY)

    def test_conflict_quarantine_preserves_j_and_un(self):
        self.write_history({"111111": suspect("충돌")})
        partial = full_bars()[:2]
        saved = store.merge_minute_bars(
            DAY, "111111", partial, market_basis="J", root=self.root, update_manifest=False
        )
        self.assertTrue(saved.ok, saved.error)
        un = store.merge_minute_bars(
            DAY, "111111", partial, market_basis="UN", root=self.root, update_manifest=False
        )
        self.assertTrue(un.ok, un.error)
        before_j = Path(saved.path).read_bytes()
        before_un = Path(un.path).read_bytes()
        incoming = full_bars()
        incoming[0] = {**incoming[0], "high": 106}
        api = FakeApi(minute={"trade_date": DAY, "bars": incoming})
        self.run_capture(api)
        self.assertEqual(Path(saved.path).read_bytes(), before_j)
        self.assertEqual(Path(un.path).read_bytes(), before_un)
        row = self.read_index()["results"][0]
        self.assertEqual(row["coverage_status"], "conflict")
        self.assertEqual(row["attempts"][-1]["conflicts"][0]["time"], "090000")
        conflict_path = store.local_day_dir(DAY, self.root) / row["attempts"][-1]["conflict_path"]
        conflict = json.loads(conflict_path.read_text(encoding="utf-8"))
        self.assertTrue(store.verify_payload_integrity(conflict))
        self.assertEqual(conflict["existing_file_sha256"], capture._sha256_bytes(before_j))
        self.assertEqual(conflict["existing_bar_count"], 2)

    def test_partial_retries_then_becomes_terminal(self):
        self.write_history({"111111": suspect("재시도")})
        first = FakeApi(minute={"trade_date": DAY, "bars": full_bars()[:2]})
        self.run_capture(first)
        row = self.read_index()["results"][0]
        self.assertEqual(row["coverage_status"], "partial_daily_mismatch")
        second = FakeApi()
        self.run_capture(second)
        row = self.read_index()["results"][0]
        self.assertEqual(row["coverage_status"], "verified_full")
        self.assertEqual(len(row["attempts"]), 2)
        third = FakeApi()
        self.run_capture(third)
        self.assertEqual(third.minute_calls, [])
        self.assertEqual(len(self.read_index()["results"][0]["attempts"]), 2)

    def test_partial_raw_coverage_survives_a_temporary_retry_error(self):
        self.write_history({"111111": suspect("일시오류")})
        self.run_capture(FakeApi(minute={"trade_date": DAY, "bars": full_bars()[:2]}))

        failing = FakeApi()

        def fail_minute(*_args, **_kwargs):
            raise RuntimeError("temporary broker error")

        failing.minute = fail_minute
        self.run_capture(failing)
        row = self.read_index()["results"][0]
        self.assertEqual(row["coverage_status"], "partial_daily_mismatch")
        self.assertEqual(row["capture_status"], "api_error")
        self.assertEqual(row["last_retry_failure_status"], "api_error")
        self.assertTrue(row["minute_file_sha256"])

    def test_cap_records_all_and_deferred_runs_first_next_time(self):
        self.write_history(
            {"111111": suspect("첫째"), "222222": suspect("둘째")}
        )
        first = FakeApi()
        result = self.run_capture(first, max_candidates=1)
        self.assertFalse(result["capture_complete"])
        index = self.read_index()
        self.assertEqual(index["candidate_n"], 2)
        self.assertEqual(index["deferred_codes"], ["222222"])
        self.assertEqual(
            {row["code"]: row["coverage_status"] for row in index["results"]}["222222"],
            "deferred_due_to_cap",
        )
        second = FakeApi()
        result2 = self.run_capture(second, max_candidates=1)
        self.assertTrue(result2["capture_complete"])
        self.assertEqual(second.minute_calls[0][0], "222222")

    def test_old_partial_transitions_without_historical_minute_merge(self):
        self.write_history({"111111": suspect("과거")})
        self.run_capture(FakeApi(minute={"trade_date": DAY, "bars": full_bars()[:2]}))
        later = FakeApi(latest=NEXT_DAY)
        result = self.run_capture(
            later,
            requested_date=DAY,
            now=datetime(2026, 7, 20, 16, 0, tzinfo=capture.KST),
        )
        self.assertEqual(result["processed_n"], 0)
        self.assertEqual(later.minute_calls, [])
        row = self.read_index()["results"][0]
        self.assertEqual(row["coverage_status"], "historical_partial")
        self.assertEqual(row["capture_status"], "stored")
        self.assertEqual(row["attempts"][-1]["capture_status"], "historical_partial")

    def test_first_cohort_and_material_remain_immutable(self):
        self.write_history({"111111": suspect("최초", final=False)})
        self.run_capture(FakeApi(minute={"trade_date": DAY, "bars": full_bars()[:2]}))
        self.write_history(
            {"111111": suspect("나중", final=True, evidence_datetime="20260717153100")},
            as_of="2026-07-17 16:01:00 KST",
        )
        self.run_capture(FakeApi())
        row = self.read_index()["results"][0]
        self.assertFalse(row["final_as_of_capture"])
        self.assertEqual(row["material_time_class"], "evidence_time_proxy_pre_cutoff")
        self.assertGreaterEqual(len(row["cohort_observations"]), 2)
        newest = row["cohort_observations"][-1]["snapshot"]
        self.assertTrue(newest["final_as_of_capture"])
        self.assertEqual(newest["material_time_class"], "post_close_material")

    def test_capture_lock_skips_second_holder(self):
        lock_path = str(Path(self.temp.name) / "capture.lock")
        with capture.capture_lock(lock_path) as first:
            self.assertIsNotNone(first)
            with capture.capture_lock(lock_path) as second:
                self.assertIsNone(second)

    def test_manifest_failure_is_not_reported_as_success(self):
        self.write_history({"111111": suspect("매니페스트")})
        with mock.patch.object(
            capture.store,
            "rebuild_manifest",
            return_value=store.StoreResult(ok=False, error="manifest boom"),
        ):
            with self.assertRaisesRegex(RuntimeError, "manifest rebuild failed"):
                self.run_capture(FakeApi())
        self.assertTrue(capture._index_path(DAY, self.root).exists())

    def test_cron_contains_exactly_one_signal_capture_line(self):
        cron = Path(__file__).with_name("install_cron_kiwoom.sh").read_text(encoding="utf-8")
        expected = (
            "45 15 * * 1-5 cd $REPO && PYTHONUTF8=1 ${PY} "
            "scripts/shakeout_signal_minute_capture.py "
            ">> /tmp/kiwoom_shakeout_signal_minute.log 2>&1"
        )
        self.assertEqual(cron.count(expected), 1)
        self.assertNotIn("autotrade_common", capture.__dict__)


if __name__ == "__main__":
    unittest.main()
