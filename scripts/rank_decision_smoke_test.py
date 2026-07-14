#!/usr/bin/env python3
"""publish 순위 보존과 자동매매 decision 메타데이터 회귀 테스트."""
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from unittest import mock

os.environ["AUTOTRADE_TEST_MODE"] = "1"

import publish
import autotrade_common as ac
import autotrade_executor as executor
import radar_json_store as store


def suspect(code, *, experimental=False, bucket=4):
    return {
        "code": code,
        "name": code,
        "price": 1000,
        "suspicion_score": 80,
        "rank_bucket": bucket,
        "rank_reason": f"bucket {bucket}",
        "change_basis": "KRX",
        "visible_experimental": experimental,
    }


class PublishRankTest(unittest.TestCase):
    def test_cut_preserves_global_order_and_radar_fields(self):
        rows = [
            suspect("R1"), suspect("E1", experimental=True),
            suspect("R2"), suspect("E2", experimental=True), suspect("R3"),
        ]
        rows[1]["precut_rank"] = 22
        rows[1]["rank_model_version"] = "rank4-v1"
        selected = publish.select_published_suspects({"suspects": rows}, 4, 2)

        self.assertEqual([s["code"] for s in selected], ["R1", "E1", "R2", "E2"])
        self.assertEqual([s["published_rank"] for s in selected], [1, 2, 3, 4])
        self.assertEqual(selected[1]["precut_rank"], 22)
        self.assertEqual(selected[1]["rank_model_version"], "rank4-v1")
        self.assertNotIn("published_rank", rows[0])

    def test_history_rank_path_only_records_changes_and_atomic_write(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(publish, "HISTORY_DIR", td):
            base = {
                "generated_at": "2026-07-13 15:11:00",
                "rank_policy_name": "rank4",
                "rank_model_version": "rank4-v1",
                "rank_model_effective_from": "20260713",
                "suspects": [
                    {**suspect("A", bucket=2), "precut_rank": 1, "published_rank": 1,
                     "alert_release": False,
                     "alert_release_rule": {"parse_status": "ok", "threshold_5d_pct": 60.0},
                     "alert_release_checks": {"elapsed_days": 8,
                                              "halt_days_excluded": ["20260708"]},
                     "alert_release_error": None,
                     "alert_elapsed_days": 8},
                    {**suspect("B", bucket=4), "precut_rank": 2, "published_rank": 2},
                ],
            }
            with mock.patch.object(publish, "history_date_for", return_value="20260713"):
                path = publish.record_history(base)
                unchanged = dict(base, generated_at="2026-07-13 15:12:00")
                publish.record_history(unchanged)
                changed = dict(base, generated_at="2026-07-13 15:21:00",
                               suspects=[{**suspect("B", bucket=4),
                                          "precut_rank": 1, "published_rank": 1}])
                publish.record_history(changed)

            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            a = data["suspects"]["A"]
            b = data["suspects"]["B"]
            self.assertEqual(a["first_seen_rank"], 1)
            self.assertEqual(a["latest_published_rank"], 1)
            self.assertEqual(a["alert_release_rule"]["threshold_5d_pct"], 60.0)
            self.assertEqual(a["alert_release_checks"]["halt_days_excluded"], ["20260708"])
            self.assertEqual(a["alert_elapsed_days"], 8)
            self.assertFalse(a["final"])
            self.assertFalse(a["published"])
            self.assertEqual(len(a["rank_path"]), 2)
            self.assertEqual([event["published"] for event in a["rank_path"]], [True, False])
            self.assertEqual(b["first_seen_rank"], 2)
            self.assertEqual(b["latest_published_rank"], 1)
            self.assertEqual([event["published_rank"] for event in b["rank_path"]], [2, 1])
            self.assertEqual(b["rank_bucket_at_signal"], 4)
            self.assertEqual(b["rank_model_version"], "rank4-v1")
            self.assertEqual(b["rank_reason_at_signal"], "bucket 4")

            with open(path, "rb") as f:
                before = f.read()
            with self.assertRaises(TypeError):
                publish.atomic_write_json(path, {"not_json": {1, 2}})
            with open(path, "rb") as f:
                self.assertEqual(f.read(), before)
            self.assertFalse(any(name.endswith(".tmp") for name in os.listdir(td)))

    def test_card_rank_stats_are_attached_without_reordering(self):
        rows = [suspect("A", bucket=2), suspect("B", bucket=4)]
        payload = {
            "rank_bucket_stats_retro": {"exclusive_all": {
                "basis": "retro", "population": "all", "model_version": "rank4-v1",
                "cells": [{"bucket": 2, "n": 1, "unique_n": 1,
                           "touch7_rate": 100.0, "wilson7_lower": 20.7,
                           "avg_high": 12.0, "median_high": 12.0,
                           "min_high": 12.0, "valid": False}]}},
            "rank_bucket_stats_forward": {"eod": {
                "basis": "forward_saved_signal", "population": "eod",
                "model_version": "rank4-v1", "cells": []}},
        }
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "performance.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            publish.attach_rank_performance(rows, path)
        self.assertEqual([row["code"] for row in rows], ["A", "B"])
        self.assertEqual(rows[0]["rank_retro_stats"]["n"], 1)
        self.assertEqual(rows[0]["rank_retro_stats"]["median_high_pct"], 12.0)
        self.assertIsNone(rows[1]["rank_retro_stats"])


class RadarSnapshotTest(unittest.TestCase):
    def test_root_metadata_uses_exact_snapshot_and_marks_future_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "radar.json")
            log_path = os.path.join(td, "autotrade.log")
            payload = {
                "generated_at": "2026-07-13 15:11:00",
                "rank_model_version": "rank4-v1",
                "suspects": [suspect("A"), suspect("B")],
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            now = datetime(2026, 7, 13, 15, 18, tzinfo=ac.KST)
            with mock.patch.object(ac, "RADAR_JSON", path), mock.patch.object(ac, "LOG_PATH", log_path):
                snapshot = ac.read_radar_snapshot(now=now)
                meta = ac.radar_snapshot_meta(snapshot, now=now)
                self.assertEqual(snapshot["suspects"][0]["code"], "A")
                self.assertEqual(meta["radar_generated_at"], payload["generated_at"])
                self.assertEqual(meta["rank_model_version"], "rank4-v1")
                self.assertEqual(meta["top_codes"], ["A", "B"])
                self.assertEqual(meta["stale_seconds"], 7 * 60)
                self.assertTrue(meta["valid_for_decision"])

                payload["generated_at"] = "2026-07-13 14:47:00"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                stale = ac.read_radar_snapshot(now=now)
                self.assertFalse(ac.radar_snapshot_meta(stale, now=now)["valid_for_decision"])

                payload["generated_at"] = "2026-07-13 15:21:00"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                future = ac.read_radar_snapshot(now=now)
                self.assertFalse(ac.radar_snapshot_meta(future, now=now)["valid_for_decision"])


class LocalDecisionStoreTest(unittest.TestCase):
    def test_publish_records_actual_post_cut_order_as_immutable_run(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {"RADAR_JSON_STORE_ROOT": td}), \
                mock.patch.object(publish, "history_date_for", return_value="20260713"):
            radar = {
                "params": {"market": "UN"},
                "suspects": [
                    {**suspect("A"), "precut_rank": 1, "rank_model_version": "rank4-v1"},
                    {**suspect("B", experimental=True), "precut_rank": 2,
                     "rank_model_version": "rank4-v1"},
                    {**suspect("C"), "precut_rank": 3, "rank_model_version": "rank4-v1"},
                ],
            }
            published = publish.select_published_suspects(radar, 2, 1)
            out = {
                "generated_at": "2026-07-13 15:11:00 KST",
                "rank_policy_name": "rank4",
                "rank_model_version": "rank4-v1",
                "suspects": published,
            }
            result = publish.record_local_published_run(radar, out)
            self.assertTrue(result.ok, result.error)
            with open(result.path, encoding="utf-8") as f:
                stored = json.load(f)
            self.assertEqual(stored["record_type"], "published_run")
            self.assertEqual([row["code"] for row in stored["published_candidates"]], ["A", "B"])
            self.assertEqual([row["published_rank"] for row in stored["published_candidates"]], [1, 2])
            self.assertEqual(
                [(row["code"], row["published"]) for row in stored["precut_candidates"]],
                [("A", True), ("B", True), ("C", False)])

    def test_executor_decision_records_are_jsonl_only(self):
        """실행기는 trades/decisions.jsonl 보조 감사만 남긴다 — decisions/<slot>.json 불변
        스냅샷은 Mac publish 파생의 단독 소유(문제7 §4.5, 적대 리뷰 M2: 선점 경쟁·dry 오염 차단)."""
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {"RADAR_JSON_STORE_ROOT": td}), \
                mock.patch.object(ac, "LOG_PATH", os.path.join(td, "autotrade.log")):
            payload = {
                "trade_date": "20260713",
                "slot": "KRX_1518",
                "decision_at": "2026-07-13 15:18:00 KST",
                "radar_generated_at": "2026-07-13 15:11:00 KST",
                "rank_model_version": "rank4-v1",
                "top_codes": ["A", "B"],
                "ordered_candidates": [{"code": "A", "published_rank": 1, "rank_bucket": 2}],
            }
            self.assertTrue(ac.write_local_decision("KRX_1518", payload))
            later = dict(payload, decision_at="2026-07-13 15:21:00 KST")
            # JSONL append라 두 번째 기록도 성공한다(선점 실패 개념 없음).
            self.assertTrue(ac.write_local_decision("KRX_1518", later))

            day = os.path.join(td, "2026", "07", "13")
            # 핵심: 실행기가 publish 소유 스냅샷 파일을 만들지 않는다.
            decision_path = os.path.join(day, "decisions", "krx_1518.json")
            self.assertFalse(os.path.exists(decision_path),
                             "executor must not preempt publish-owned decision snapshot")

            decisions_path = os.path.join(day, "trades", "decisions.jsonl")
            with open(decisions_path, encoding="utf-8") as f:
                decision_events = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(decision_events), 2)
            self.assertEqual(decision_events[0]["decision_at"], payload["decision_at"])
            self.assertEqual(decision_events[1]["decision_at"], later["decision_at"])

            trade = {"type": "entry", "entry_date": "20260713", "code": "A"}
            self.assertTrue(ac.append_local_trade_event(trade))
            self.assertTrue(os.path.exists(os.path.join(day, "trades", "events.jsonl")))
            # 주문 경로 재빌드 금지(M3) + 고아 날 폴백(재검증 반박 2)의 공존 검증:
            # manifest가 없던 첫 기록 때만 1회 생성되고, 이후 기록은 재빌드하지 않는다
            # (두 번째 append 이후에도 manifest가 첫 생성본 그대로 = stale 색인 유지).
            manifest_path = os.path.join(day, "manifest.json")
            self.assertTrue(os.path.exists(manifest_path),
                            "orphan-day fallback must create manifest once")
            with open(manifest_path, encoding="utf-8") as f:
                first_manifest = f.read()
            self.assertTrue(ac.write_local_decision("KRX_1518", dict(payload)))
            with open(manifest_path, encoding="utf-8") as f:
                second_manifest = f.read()
            self.assertEqual(first_manifest, second_manifest,
                             "manifest must not be rebuilt while it already exists (M3)")

    def test_mac_decisions_use_latest_published_run_before_each_cutoff(self):
        def published_run(generated_at, code, run_id):
            payload = {
                "record_type": "published_run",
                "run": {
                    "run_id": run_id,
                    "trade_date": "20260713",
                    "generated_at": generated_at,
                    "scan_ok": True,
                    "rank_policy_name": "rank4",
                    "rank_model_version": "rank4-v1",
                },
                "published_candidates": [
                    {**suspect(code), "precut_rank": 1, "published_rank": 1,
                     "rank_model_version": "rank4-v1"}
                ],
            }
            result = store.write_scan(
                payload, trade_date="20260713", observed_at=generated_at,
                run_id=run_id, root=td)
            self.assertTrue(result.ok, result.error)

        with tempfile.TemporaryDirectory() as td:
            published_run("2026-07-13 15:11:00 KST", "A", "p1511")
            published_run("2026-07-13 15:21:00 KST", "B", "p1521")

            publish.derive_due_decision_snapshots(
                "20260713", now=datetime(2026, 7, 13, 15, 21, tzinfo=publish.KST), root=td)
            decision_dir = os.path.join(td, "2026", "07", "13", "decisions")
            with open(os.path.join(decision_dir, "krx_1518.json"), encoding="utf-8") as f:
                krx = json.load(f)
            self.assertEqual(krx["top_codes"], ["A"])
            self.assertEqual(krx["radar_generated_at"], "2026-07-13 15:11:00 KST")

            publish.derive_due_decision_snapshots(
                "20260713", now=datetime(2026, 7, 13, 15, 31, tzinfo=publish.KST), root=td)
            with open(os.path.join(decision_dir, "krx_close.json"), encoding="utf-8") as f:
                close = json.load(f)
            self.assertEqual(close["top_codes"], ["B"])

            published_run("2026-07-13 19:41:00 KST", "C", "p1941")
            published_run("2026-07-13 19:51:00 KST", "D", "p1951")
            publish.derive_due_decision_snapshots(
                "20260713", now=datetime(2026, 7, 13, 19, 51, tzinfo=publish.KST), root=td)
            with open(os.path.join(decision_dir, "nxt_1950.json"), encoding="utf-8") as f:
                nxt = json.load(f)
            self.assertEqual(nxt["top_codes"], ["C"])

            published_run("2026-07-13 20:52:00 KST", "E", "p2052")
            publish.derive_due_decision_snapshots(
                "20260713", now=datetime(2026, 7, 13, 20, 53, tzinfo=publish.KST), root=td)
            with open(os.path.join(decision_dir, "operational_eod.json"), encoding="utf-8") as f:
                eod = json.load(f)
            self.assertEqual(eod["top_codes"], ["E"])

    def test_publish_round_rebuilds_manifest_once_covering_all_writers(self):
        """적대 리뷰 M3 — 회차당 재빌드 1회(derive 말미)가 published_run·decision·
        실행기 JSONL(update_manifest=False로 쌓인 것)을 전부 색인한다."""
        with tempfile.TemporaryDirectory() as td:
            payload = {
                "record_type": "published_run",
                "run": {"run_id": "p1511", "trade_date": "20260713",
                        "generated_at": "2026-07-13 15:11:00 KST", "scan_ok": True,
                        "rank_policy_name": "rank4", "rank_model_version": "rank4-v1"},
                "published_candidates": [
                    {**suspect("A"), "precut_rank": 1, "published_rank": 1,
                     "rank_model_version": "rank4-v1"}],
            }
            # publish 경로와 동일하게 update_manifest=False로 저장
            result = store.write_scan(payload, trade_date="20260713",
                                      observed_at="2026-07-13 15:11:00 KST",
                                      run_id="p1511", root=td, update_manifest=False)
            self.assertTrue(result.ok, result.error)
            # 실행기 보조 감사(JSONL, 재빌드 없음) 시뮬레이션
            ev = store.append_trade_event(
                "decisions", {"trade_date": "20260713", "slot": "KRX_1518"},
                trade_date="20260713", root=td, update_manifest=False)
            self.assertTrue(ev.ok, ev.error)

            day_dir = os.path.join(td, "2026", "07", "13")
            self.assertFalse(os.path.exists(os.path.join(day_dir, "manifest.json")),
                             "재빌드는 derive 말미 1회여야 한다")
            publish.derive_due_decision_snapshots(
                "20260713", now=datetime(2026, 7, 13, 15, 21, tzinfo=publish.KST), root=td)
            with open(os.path.join(day_dir, "manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            indexed = {entry["path"] for entry in manifest.get("files", [])}
            self.assertIn("decisions/krx_1518.json", indexed)
            self.assertIn("trades/decisions.jsonl", indexed)
            self.assertTrue(any(p.startswith("scans/") for p in indexed),
                            f"published_run scan 미색인: {indexed}")
            verify = store.verify_manifest("20260713", td)
            self.assertTrue(verify.ok, verify.error)
            self.assertEqual((verify.value or {}).get("issues"), [])


class AutotradeInvariantTest(unittest.TestCase):
    def _run_branch(self, *, nxt=False, buy_result=None, buy_error=None,
                    account_error=None):
        radar = {
            "generated_at": datetime.now(ac.KST).strftime("%Y-%m-%d %H:%M:%S"),
            "rank_model_version": "rank4-v1",
            "suspects": [suspect("A", bucket=2)],
        }
        events = []
        buy = mock.Mock(side_effect=buy_error)
        if buy_error is None:
            buy.return_value = buy_result or {
                "dry": False, "market": "KRX", "qty": 1, "ref_price": 1000}
        account = mock.Mock(
            side_effect=account_error,
            return_value={"summary": {"deposit": 1_000_000}})
        patches = (
            mock.patch.object(ac, "autotrade_enabled", return_value=True),
            mock.patch.object(ac, "load_positions", return_value={"positions": []}),
            mock.patch.object(ac, "open_positions", return_value=[]),
            mock.patch.object(ac, "todays_positions", return_value=[]),
            mock.patch.object(ac, "read_ranks", return_value=[1]),
            mock.patch.object(ac, "read_radar_snapshot", return_value=radar),
            mock.patch.object(ac, "already_bought", return_value=False),
            mock.patch.object(ac, "read_budget", return_value=1_000_000),
            mock.patch.object(ac, "deployed_today", return_value=0),
            mock.patch.object(ac, "write_local_decision", return_value=False),
            mock.patch.object(ac, "append_local_trade_event", side_effect=lambda event: events.append(event)),
            mock.patch.object(ac, "log"),
            mock.patch.object(executor.kt, "account_holdings", account),
            mock.patch.object(executor.kt, "is_nxt_tradable", return_value=nxt),
            mock.patch.object(executor.kt, "buy_market_krx", buy),
            mock.patch.object(executor, "_record"),
        )
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            executor.run("krx", dry=False)
        return buy, events

    def test_decision_execution_label_follows_kiwoom_live_gate(self):
        """재검증 반박 1 — actual_autotrade_execution은 --dry가 아니라 kiwoom_trade와 동일한
        (dry=False AND AUTOTRADE_LIVE=1) 게이트를 따라야 한다. AUTOTRADE_LIVE 미설정 실행이
        '실발주 결정'으로 위장 기록되면 forward 감사가 오염된다."""
        for env_live, expect_actual in (({}, False), ({"AUTOTRADE_LIVE": "1"}, True)):
            radar = {
                "generated_at": datetime.now(ac.KST).strftime("%Y-%m-%d %H:%M:%S"),
                "rank_model_version": "rank4-v1",
                "suspects": [suspect("A", bucket=2)],
            }
            decisions = []
            env = {k: v for k, v in os.environ.items() if k != "AUTOTRADE_LIVE"}
            env.update(env_live)
            patches = (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(ac, "autotrade_enabled", return_value=True),
                mock.patch.object(ac, "load_positions", return_value={"positions": []}),
                mock.patch.object(ac, "open_positions", return_value=[]),
                mock.patch.object(ac, "todays_positions", return_value=[]),
                mock.patch.object(ac, "read_ranks", return_value=[1]),
                mock.patch.object(ac, "read_radar_snapshot", return_value=radar),
                mock.patch.object(ac, "already_bought", return_value=False),
                mock.patch.object(ac, "read_budget", return_value=1_000_000),
                mock.patch.object(ac, "deployed_today", return_value=0),
                mock.patch.object(ac, "write_local_decision",
                                  side_effect=lambda slot, payload: decisions.append(payload) or True),
                mock.patch.object(ac, "append_local_trade_event", return_value=True),
                mock.patch.object(ac, "log"),
                mock.patch.object(executor.market_state, "require_trading_day",
                                  return_value=(True, {"is_trading_day": True})),
                mock.patch.object(executor.kt, "account_holdings",
                                  return_value={"summary": {"deposit": 1_000_000}}),
                mock.patch.object(executor.kt, "is_nxt_tradable", return_value=False),
                mock.patch.object(executor.kt, "buy_market_krx",
                                  return_value={"dry": not expect_actual, "market": "KRX",
                                                "qty": 1, "ref_price": 1000, "reason": "test"}),
                mock.patch.object(executor, "_record"),
            )
            with ExitStack() as stack:
                for patcher in patches:
                    stack.enter_context(patcher)
                executor.run("krx", dry=False)
            self.assertEqual(len(decisions), 1, f"env={env_live}")
            self.assertEqual(decisions[0]["actual_autotrade_execution"], expect_actual,
                             f"env={env_live}")
            self.assertEqual(decisions[0]["dry"], not expect_actual, f"env={env_live}")

    def test_optional_audit_failure_keeps_selection_and_budget(self):
        radar = {
            "generated_at": datetime.now(ac.KST).strftime("%Y-%m-%d %H:%M:%S"),
            "rank_model_version": "rank4-v1",
            "suspects": [suspect("A", bucket=2), suspect("B", bucket=4), suspect("C")],
        }
        orders = []
        recorded = []

        def buy(code, budget, dry=True):
            orders.append((code, budget, dry))
            return {"dry": False, "market": "KRX", "qty": 1, "ref_price": 1000}

        patches = (
            mock.patch.object(ac, "autotrade_enabled", return_value=True),
            mock.patch.object(ac, "load_positions", return_value={"positions": []}),
            mock.patch.object(ac, "open_positions", return_value=[]),
            mock.patch.object(ac, "todays_positions", return_value=[]),
            mock.patch.object(ac, "read_ranks", return_value=[1, 2]),
            mock.patch.object(ac, "read_radar_snapshot", return_value=radar),
            mock.patch.object(ac, "already_bought", return_value=False),
            mock.patch.object(ac, "read_budget", return_value=1_000_000),
            mock.patch.object(ac, "deployed_today", return_value=0),
            mock.patch.object(ac, "write_local_decision", return_value=False),
            mock.patch.object(executor.kt, "account_holdings", return_value={"summary": {"deposit": 1_000_000}}),
            mock.patch.object(executor.kt, "is_nxt_tradable", return_value=False),
            mock.patch.object(executor.kt, "buy_market_krx", side_effect=buy),
            mock.patch.object(executor, "_record", side_effect=lambda *args: recorded.append(args)),
        )
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            executor.run("krx", dry=False)

        self.assertEqual(orders, [("A", 500_000, False), ("B", 500_000, False)])
        self.assertEqual([(args[3], args[1]["code"]) for args in recorded], [(1, "A"), (2, "B")])
        decision = recorded[0][5]
        self.assertEqual(decision["top_codes"], ["A", "B", "C"])
        self.assertEqual(decision["rank_model_version"], "rank4-v1")

    def test_invalid_decision_timestamp_is_fail_closed_before_account_or_order(self):
        radar = {
            "generated_at": "2026-07-13 15:21:00",
            "rank_model_version": "rank4-v1",
            "suspects": [suspect("A", bucket=2)],
        }
        buy = mock.Mock()
        account = mock.Mock()
        with mock.patch.object(ac, "autotrade_enabled", return_value=True), \
                mock.patch.object(ac, "load_positions", return_value={"positions": []}), \
                mock.patch.object(ac, "open_positions", return_value=[]), \
                mock.patch.object(ac, "todays_positions", return_value=[]), \
                mock.patch.object(ac, "read_ranks", return_value=[1]), \
                mock.patch.object(ac, "read_radar_snapshot", return_value=radar), \
                mock.patch.object(ac, "radar_snapshot_meta", return_value={
                    "trade_date": "20260713", "radar_generated_at": radar["generated_at"],
                    "rank_model_version": "rank4-v1", "stale_seconds": 0,
                    "valid_for_decision": False, "top_codes": ["A"]}), \
                mock.patch.object(ac, "write_local_decision", return_value=False), \
                mock.patch.object(ac, "log"), \
                mock.patch.object(executor.kt, "account_holdings", account), \
                mock.patch.object(executor.kt, "buy_market_krx", buy):
            executor.run("krx", dry=False)
        account.assert_not_called()
        buy.assert_not_called()

    def test_position_and_trade_event_keep_decision_metadata(self):
        saved = {}
        events = []
        top = {
            **suspect("A", bucket=2),
            "precut_rank": 1,
            "published_rank": 1,
            "rank_model_version": "rank4-v1",
        }
        decision = {
            "decision_at": "2026-07-13 15:18:00 KST",
            "radar_generated_at": "2026-07-13 15:11:00 KST",
            "rank_model_version": "rank4-v1",
            "top_codes": ["A", "B", "C"],
        }
        result = {"market": "KRX", "qty": 10, "ref_price": 1000}

        with mock.patch.object(ac, "today_str", return_value="20260713"), \
                mock.patch.object(ac, "load_positions", return_value={"positions": []}), \
                mock.patch.object(ac, "save_positions", side_effect=lambda data: saved.update(data)), \
                mock.patch.object(ac, "log"), mock.patch.object(ac, "notify_trade"), \
                mock.patch.object(ac, "append_trade_event", side_effect=lambda event: events.append(event)), \
                mock.patch.object(ac, "append_local_trade_event", return_value=True):
            executor._record("krx", top, result, 1, 1_000_000, decision)

        position = saved["positions"][0]
        event = events[0]
        for record in (position, event):
            self.assertEqual(record["radar_generated_at"], decision["radar_generated_at"])
            self.assertEqual(record["model_version"], "rank4-v1")
            self.assertEqual(record["rank_bucket"], 2)
            self.assertEqual(record["reason"], "bucket 2")
            self.assertEqual(record["top_codes"], ["A", "B", "C"])
            self.assertEqual(record["change_basis"], "KRX")

    def test_account_state_failure_is_fail_closed(self):
        buy, events = self._run_branch(account_error=RuntimeError("account unavailable"))
        buy.assert_not_called()
        self.assertEqual(events[-1]["order_result"], "blocked_account_state_unknown")
        self.assertFalse(events[-1]["order_attempted"])

    def test_delegation_dry_and_failure_are_audited(self):
        buy, delegated = self._run_branch(nxt=True)
        buy.assert_not_called()
        self.assertEqual((delegated[-1]["order_attempted"], delegated[-1]["order_result"]),
                         (False, "delegated_nxt"))

        buy, dry_events = self._run_branch(
            buy_result={"dry": True, "reason": "AUTOTRADE_LIVE!=1"})
        buy.assert_called_once_with("A", 1_000_000, dry=False)
        self.assertEqual((dry_events[-1]["order_attempted"], dry_events[-1]["order_result"]),
                         (False, "dry_no_order"))

        buy, failed = self._run_branch(buy_error=RuntimeError("broker rejected"))
        buy.assert_called_once_with("A", 1_000_000, dry=False)
        self.assertEqual((failed[-1]["order_attempted"], failed[-1]["order_result"]),
                         (True, "order_failed"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
